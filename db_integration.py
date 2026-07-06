
# db_integration.py — FINAL SINGLE-FILE (FK patch)
# - Aggiunta chiamata _ensure_location_exists(location_id) PRIMA degli upserts su reviews e media
# - Così evitiamo errori 409/23503 quando la location non è ancora presente

from app_logging import log_swallowed
import os, time, threading, json, requests
from datetime import datetime, timezone
from typing import Callable, List, Dict, Any, Optional

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SERVICE_ROLE = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
READ_FROM_DB_FIRST = (os.getenv("READ_FROM_DB_FIRST","true").lower() == "true")
DB_TTL_SECONDS = int(os.getenv("DB_TTL_SECONDS","120") or "120")

_session = requests.Session()
_refresh_locks: Dict[tuple, float] = {}

def _sb_headers(write: bool = False) -> Dict[str,str]:
    key = SERVICE_ROLE
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }

def _sb_get(path: str, params: Dict[str,str]):
    return _session.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=_sb_headers(False), params=params, timeout=30)

def _sb_post(path: str, payload: Dict[str,Any]):
    return _session.post(f"{SUPABASE_URL}/rest/v1/{path}", headers=_sb_headers(True), data=json.dumps(payload), timeout=60)

def _sb_upsert(table: str, rows: List[Dict[str,Any]], on_conflict: Optional[str] = None):
    if not rows:
        return
    params = {}
    if on_conflict:
        params["on_conflict"] = on_conflict
    r = _session.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=_sb_headers(True), params=params, data=json.dumps(rows), timeout=60)
    if r.status_code not in (200,201,204):
        raise RuntimeError(f"Supabase upsert {table} failed: {r.status_code} {r.text}")

def _sb_delete(table: str, params: Dict[str,str]):
    """Esegue una DELETE su una tabella Supabase usando la SERVICE_ROLE key."""
    return _session.delete(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(False),
        params=params or {},
        timeout=30,
    )

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _rating_to_int(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        v = int(val)
        return v if 1 <= v <= 5 else None
    mapping = {"ONE":1,"TWO":2,"THREE":3,"FOUR":4,"FIVE":5,"one":1,"two":2,"three":3,"four":4,"five":5,"1":1,"2":2,"3":3,"4":4,"5":5}
    return mapping.get(str(val), None)

def _ensure_location_exists(location_id: str):
    try:
        _sb_upsert("locations", [{
            "location_id": location_id,
            "google_name": None,
            "title": None,
            "store_code": None,
            "google_update_time": None
        }], on_conflict="location_id")
    except Exception as e:
        print("ensure_location_exists failed (ignored):", e)

# ---------- SELECT HELPERS ----------
def _rpc_reviews_with_replies(location_id: str, limit: int) -> Optional[List[Dict[str,Any]]]:
    r = _sb_post("rpc/reviews_with_replies", {"p_location_id": location_id, "p_limit": limit})
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        return None
    return r.json() or []

def _fetch_reviews_rows(location_id: str, limit: int) -> List[Dict[str,Any]]:
    r = _sb_get("reviews", {
        "select": "review_id,star_rating,comment,reviewer_language,create_time,update_time,reviewer_display_name",
        "location_id": f"eq.{location_id}",
        "order": "update_time.desc",
        "limit": str(limit)
    })
    r.raise_for_status()
    return r.json() or []

def _fetch_replies_child_join(location_id: str) -> Dict[str, Dict[str,Any]]:
    r = _sb_get("review_replies", {
        "select": "review_id,reply_text,reply_update_time,reviews!inner(review_id)",
        "reviews.location_id": f"eq.{location_id}",
        "limit": "2000"
    })
    r.raise_for_status()
    out = {}
    for row in (r.json() or []):
        rid = row.get("review_id")
        if rid:
            out[rid] = {"reply_text": row.get("reply_text") or "", "reply_update_time": row.get("reply_update_time")}
    return out

def _select_reviews_google_shape(location_id: str, limit: int = 100) -> List[Dict[str,Any]]:
    rpc_rows = _rpc_reviews_with_replies(location_id, limit)
    if rpc_rows is not None:
        out = []
        for row in rpc_rows:
            g = {
                "name": row.get("review_id"),
                "starRating": row.get("star_rating"),
                "comment": row.get("comment") or "",
                "reviewerLanguage": row.get("reviewer_language"),
                "createTime": row.get("create_time"),
                "updateTime": row.get("update_time"),
                "reviewer": {"displayName": row.get("reviewer_display_name")},
            }
            rt = row.get("reply_text") or ""
            ru = row.get("reply_update_time")
            if rt != "" or ru is not None:
                g["reviewReply"] = {"comment": rt, "updateTime": ru}
            out.append(g)
        return out

    reviews = _fetch_reviews_rows(location_id, limit)
    if not reviews:
        return []
    replies = _fetch_replies_child_join(location_id)

    out = []
    for r in reviews:
        g = {
            "name": r["review_id"],
            "starRating": r.get("star_rating"),
            "comment": r.get("comment") or "",
            "reviewerLanguage": r.get("reviewer_language"),
            "createTime": r.get("create_time"),
            "updateTime": r.get("update_time"),
            "reviewer": {"displayName": r.get("reviewer_display_name")},
        }
        rep = replies.get(r["review_id"])
        if rep:
            g["reviewReply"] = {"comment": rep.get("reply_text") or "", "updateTime": rep.get("reply_update_time")}
        out.append(g)
    return out

def _select_media_google_shape(location_id: str, limit: int = 200) -> List[Dict[str,Any]]:
    r = _sb_get("media", {
        "select": "media_id,category,source_url,media_format,create_time,deleted_at",
        "location_id": f"eq.{location_id}",
        "deleted_at": "is.null",
        "order": "create_time.desc",
        "limit": str(limit)
    })
    r.raise_for_status()
    items = r.json() or []
    out = []
    for m in items:
        out.append({
            "name": m.get("media_id"),
            "googleUrl": m.get("source_url"),
            "mediaFormat": m.get("media_format"),
            "createTime": m.get("create_time"),
            "locationAssociation": {"category": m.get("category")}
        })
    return out

# ---------- ENRICHMENT per il template ----------
def _normalize_stars_ui(val):
    if val is None:
        return 0
    try:
        v = int(val)
        return v if 1 <= v <= 5 else 0
    except Exception:
        mapping = {
            "ONE":1,"TWO":2,"THREE":3,"FOUR":4,"FIVE":5,
            "one":1,"two":2,"three":3,"four":4,"five":5,
            "1":1,"2":2,"3":3,"4":4,"5":5
        }
        return mapping.get(str(val), 0)

def _enrich_reviews_for_template(items: List[Dict[str,Any]]):
    for rev in items or []:
        rev["_review_name"] = rev.get("name") or rev.get("_review_name") or ""
        rev["_stars"] = _normalize_stars_ui(rev.get("starRating"))
        rep = (rev.get("reviewReply") or rev.get("reply") or rev.get("ownerReply") or rev.get("review_reply"))
        reply_text = ""
        if isinstance(rep, dict):
            reply_text = rep.get("comment") or rep.get("text") or ""
        elif isinstance(rev.get("replyText"), str):
            reply_text = rev["replyText"] or ""
        rev["_replyText"] = reply_text
        if rev.get("comment") is None:
            rev["comment"] = ""
    return items

# ---------- TTL + SWR ----------
def _update_sync_state(entity: str, location_id: str):
    _sb_upsert("sync_state", [{
        "entity_type": entity,
        "location_id": location_id,
        "last_sync_time": _now_iso()
    }], on_conflict="entity_type,location_id")

def _is_fresh(entity: str, location_id: str) -> bool:
    try:
        r = _sb_get("sync_state", {
            "select": "last_sync_time",
            "entity_type": f"eq.{entity}",
            "location_id": f"eq.{location_id}"
        })
        if r.status_code == 200 and r.json():
            last = r.json()[0].get("last_sync_time")
            if last:
                ts = datetime.fromisoformat(last.replace("Z","+00:00")).timestamp()
                return (time.time() - ts) <= DB_TTL_SECONDS
    except Exception:
        log_swallowed('db_integration:224')
    return False

def _kickoff_once(entity: str, location_id: str, target):
    key = (entity, location_id)
    now = time.time()
    last = _refresh_locks.get(key, 0.0)
    if (now - last) < 60:
        return
    _refresh_locks[key] = now
    threading.Thread(target=target, daemon=True).start()

# ---------- Public API ----------

def get_warehouse_stores(include_inactive: bool = False) -> List[Dict[str,Any]]:
    """
    Restituisce l'elenco degli store di magazzino da Supabase.

    Si aspetta una tabella "warehouse_stores" con almeno le colonne:
      - id (uuid)
      - code (text)
      - name (text)
      - is_active (boolean, opzionale)
      - sort_order (integer, opzionale)
    """
    tenant_key = ""
    try:
        from flask import has_request_context, session
        from tenant_config_repository import current_tenant_key, list_tenant_stores

        default_tenant_key = current_tenant_key()
        if has_request_context():
            role_l = str(session.get("role") or "").strip().lower()
            if bool(session.get("is_master")) or role_l == "master":
                tenant_key = str(session.get("master_admin_tenant_key") or "").strip()
            else:
                tenant_key = str(session.get("tenant_key") or "").strip()
        if not tenant_key:
            tenant_key = default_tenant_key

        if tenant_key and tenant_key != default_tenant_key:
            tenant_rows = list_tenant_stores(tenant_key, active_only=not include_inactive) or []
            return [
                {
                    "id": str(row.get("store_code") or "").strip(),
                    "code": str(row.get("store_code") or "").strip(),
                    "name": row.get("store_name") or str(row.get("store_code") or "").strip(),
                    "is_active": bool(row.get("is_active", True)),
                    "sort_order": 0,
                }
                for row in tenant_rows
                if str(row.get("store_code") or "").strip()
            ]
    except Exception:
        tenant_key = ""

    params = {
        "select": "id,code,name,is_active,sort_order",
        "order": "sort_order.asc,code.asc"
    }
    r = _sb_get("warehouse_stores", params)
    r.raise_for_status()
    rows = r.json() or []
    out: List[Dict[str,Any]] = []
    for row in rows:
        # Se c'è il campo is_active e vale False, lo saltiamo
        if not include_inactive and "is_active" in row and not row.get("is_active"):
            continue
        out.append(row)
    try:
        from flask import has_request_context, session
        from tenant_config_repository import (
            current_tenant_key,
            list_tenant_store_codes,
            seed_tenant_stores,
        )

        tenant_key = tenant_key or ""
        if has_request_context():
            role_l = str(session.get("role") or "").strip().lower()
            if bool(session.get("is_master")) or role_l == "master":
                tenant_key = str(session.get("master_admin_tenant_key") or "").strip()
            else:
                tenant_key = str(session.get("tenant_key") or "").strip()
        if not tenant_key:
            tenant_key = current_tenant_key()
        if tenant_key:
            seed_tenant_stores(tenant_key, out, only_if_empty=True)
            allowed = list_tenant_store_codes(tenant_key, active_only=not include_inactive)
            if allowed:
                out = [row for row in out if str((row or {}).get("code") or "").strip() in allowed]
    except Exception:
        log_swallowed('db_integration:316')
    return out


def upsert_warehouse_store(code: str, name: str, is_active: bool = True, sort_order: Optional[int] = None) -> Dict[str, Any]:
    """Crea/aggiorna uno store nella tabella Supabase warehouse_stores."""
    code = str(code or "").strip()
    name = str(name or "").strip()
    if not code:
        raise ValueError("Codice store obbligatorio")
    if not name:
        raise ValueError("Nome store obbligatorio")
    row: Dict[str, Any] = {
        "code": code,
        "name": name,
        "is_active": bool(is_active),
    }
    if sort_order is not None:
        row["sort_order"] = int(sort_order or 0)
    _sb_upsert("warehouse_stores", [row], on_conflict="code")
    try:
        from flask import has_request_context, session
        from tenant_config_repository import current_tenant_key, upsert_tenant_store

        tenant_key = ""
        if has_request_context():
            role_l = str(session.get("role") or "").strip().lower()
            if bool(session.get("is_master")) or role_l == "master":
                tenant_key = str(session.get("master_admin_tenant_key") or "").strip()
            else:
                tenant_key = str(session.get("tenant_key") or "").strip()
        if not tenant_key:
            tenant_key = current_tenant_key()
        if tenant_key:
            upsert_tenant_store(
                tenant_key=tenant_key,
                store_code=code,
                store_name=name,
                is_active=bool(is_active),
            )
    except Exception:
        log_swallowed('db_integration:357')
    return row


def delete_warehouse_store(code: str) -> bool:
    """Elimina uno store da Supabase warehouse_stores. Usato solo in configurazione/test."""
    code = str(code or "").strip()
    if not code:
        raise ValueError("Codice store obbligatorio")
    r = _sb_delete("warehouse_stores", {"code": f"eq.{code}"})
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Supabase delete warehouse_stores failed: {r.status_code} {r.text}")
    return True


def delete_warehouse_user_store_assignments_for_store(store_code: str) -> bool:
    """Rimuove tutte le assegnazioni utente per uno store eliminato."""
    code = str(store_code or "").strip()
    if not code:
        raise ValueError("Codice store obbligatorio")
    r = _sb_delete("warehouse_user_stores", {"store_code": f"eq.{code}"})
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Supabase delete warehouse_user_stores by store failed: {r.status_code} {r.text}")
    return True


def get_user_warehouse_stores(user_id: str) -> List[Dict[str,Any]]:
    """Restituisce gli store di magazzino assegnati a uno specifico utente.

    Legge dalla tabella "warehouse_user_stores" usando la SERVICE_ROLE key,
    ignorando eventuali policy RLS sul client.
    """
    if not user_id:
        return []
    params = {
        "select": "id,user_id,store_code,store_name",
        "user_id": f"eq.{user_id}",
        "order": "store_code.asc"
    }
    r = _sb_get("warehouse_user_stores", params)
    r.raise_for_status()
    return r.json() or []


def get_all_warehouse_user_stores() -> List[Dict[str,Any]]:
    """Restituisce tutte le righe di warehouse_user_stores (per pagina Admin)."""
    params = {
        "select": "id,user_id,store_code,store_name",
        "order": "user_id.asc,store_code.asc"
    }
    r = _sb_get("warehouse_user_stores", params)
    r.raise_for_status()
    return r.json() or []


def upsert_user_store_assignment(user_id: str, store_code: str, store_name: Optional[str] = None) -> None:
    """Crea/aggiorna una riga di warehouse_user_stores per (user_id, store_code)."""
    if not user_id or not store_code:
        raise ValueError("user_id e store_code sono obbligatori")
    row: Dict[str,Any] = {
        "user_id": user_id,
        "store_code": store_code,
    }
    if store_name is not None and store_name != "":
        row["store_name"] = store_name
    _sb_upsert("warehouse_user_stores", [row], on_conflict="user_id,store_code")


def delete_user_store_assignment(row_id) -> None:
    """Elimina una riga da warehouse_user_stores per id.

    Nota: in Supabase la colonna `id` può essere BIGINT oppure UUID (stringa),
    quindi non forziamo conversioni numeriche.
    """
    if row_id is None:
        return
    row_id_str = str(row_id).strip()
    if not row_id_str:
        return
    _sb_delete("warehouse_user_stores", {"id": f"eq.{row_id_str}"})
def delete_user_store_assignments_for_user(user_id: str) -> None:
    """Elimina tutte le righe di warehouse_user_stores per user_id (best-effort)."""
    if not user_id:
        return
    r = _sb_delete("warehouse_user_stores", {"user_id": f"eq.{user_id}"})
    # PostgREST può rispondere 204 (No Content) o 200
    if getattr(r, "status_code", 0) not in (200, 204):
        raise RuntimeError(f"Supabase delete warehouse_user_stores by user failed: {getattr(r,'status_code',None)} {getattr(r,'text','')}")

def set_user_warehouse_stores(user_id: str, store_codes: List[str], code_to_name: Optional[Dict[str,str]] = None) -> None:
    """Sostituisce le assegnazioni store per un utente (delete + upsert).

    - store_codes: lista codici store selezionati
    - code_to_name: mappa opzionale code->name per salvare anche store_name
    """
    if not user_id:
        raise ValueError("user_id obbligatorio")
    # normalizza
    codes = []
    seen = set()
    for c in store_codes or []:
        c = str(c).strip()
        if not c or c in seen:
            continue
        seen.add(c)
        codes.append(c)

    delete_user_store_assignments_for_user(user_id)

    if not codes:
        return

    rows = []
    for code in codes:
        row: Dict[str,Any] = {"user_id": user_id, "store_code": code}
        if code_to_name:
            nm = code_to_name.get(code)
            if nm:
                row["store_name"] = nm
        rows.append(row)

    _sb_upsert("warehouse_user_stores", rows, on_conflict="user_id,store_code")
def reviews_for_ui(location_id: str, fetch_google: Callable[[], List[Dict[str,Any]]], limit: int = 100):
    if READ_FROM_DB_FIRST and _is_fresh("reviews", location_id):
        rows = _select_reviews_google_shape(location_id, limit=limit)
        if rows: 
            return _enrich_reviews_for_template(rows)

    def do_refresh():
        try: 
            items = fetch_google() or []
            rows, replies = _normalize_reviews_rows(location_id, items)
            _ensure_location_exists(location_id)  # FK parent
            if rows: _sb_upsert("reviews", rows, on_conflict="review_id")
            if replies: _sb_upsert("review_replies", replies, on_conflict="review_id")
            _update_sync_state("reviews", location_id)
        except Exception as e:
            print("refresh reviews failed:", e)

    rows = _select_reviews_google_shape(location_id, limit=limit)
    if rows:
        _kickoff_once("reviews", location_id, do_refresh)
        return _enrich_reviews_for_template(rows)

    # write-through
    try:
        items = fetch_google() or []
        rows, replies = _normalize_reviews_rows(location_id, items)
        _ensure_location_exists(location_id)  # FK parent
        if rows: _sb_upsert("reviews", rows, on_conflict="review_id")
        if replies: _sb_upsert("review_replies", replies, on_conflict="review_id")
        _update_sync_state("reviews", location_id)
        return _enrich_reviews_for_template(items)
    except Exception as e:
        print("write-through reviews failed:", e)
        items = fetch_google() or []
        return _enrich_reviews_for_template(items)

def media_for_ui(location_id: str, fetch_google: Callable[[], List[Dict[str,Any]]], limit: int = 200):
    if READ_FROM_DB_FIRST and _is_fresh("media", location_id):
        rows = _select_media_google_shape(location_id, limit=limit)
        if rows: 
            return rows

    def do_refresh():
        try:
            items = fetch_google() or []
            rows = _normalize_media_rows(location_id, items)
            _ensure_location_exists(location_id)  # FK parent
            if rows: _sb_upsert("media", rows, on_conflict="media_id")
            _update_sync_state("media", location_id)
        except Exception as e:
            print("refresh media failed:", e)

    rows = _select_media_google_shape(location_id, limit=limit)
    if rows:
        _kickoff_once("media", location_id, do_refresh)
        return rows

    try:
        items = fetch_google() or []
        rows = _normalize_media_rows(location_id, items)
        _ensure_location_exists(location_id)  # FK parent
        if rows: _sb_upsert("media", rows, on_conflict="media_id")
        _update_sync_state("media", location_id)
    except Exception as e:
        print("write-through media failed:", e)
        items = fetch_google() or []
    return items

# ---------- Normalizers (Google -> DB rows) ----------
def _normalize_reviews_rows(location_id: str, items: List[Dict[str,Any]]):
    reviews_rows, reply_rows = [], []
    for r in items or []:
        rid = r.get("name")
        reviews_rows.append({
            "review_id": rid,
            "location_id": location_id,
            "star_rating": _rating_to_int(r.get("starRating")),
            "comment": (r.get("comment") or ""),
            "reviewer_language": r.get("reviewerLanguage"),
            "create_time": r.get("createTime"),
            "update_time": r.get("updateTime"),
            "reviewer_display_name": (r.get("reviewer") or {}).get("displayName"),
            "is_deleted": False,
            "raw": r
        })
        rep = r.get("reviewReply")
        if rep and rid:
            reply_rows.append({
                "review_id": rid,
                "reply_text": rep.get("comment") or "",
                "reply_update_time": rep.get("updateTime"),
                "replied_by": None
            })
    return reviews_rows, reply_rows

def _normalize_media_rows(location_id: str, items: List[Dict[str,Any]]):
    out = []
    for m in items or []:
        mid = m.get("name") or m.get("mediaKey") or m.get("googleUrl")
        out.append({
            "media_id": mid,
            "location_id": location_id,
            "category": (m.get("locationAssociation") or {}).get("category"),
            "source_url": m.get("sourceUrl") or m.get("googleUrl"),
            "media_format": m.get("mediaFormat"),
            "create_time": m.get("createTime"),
            "deleted_at": None,
            "raw": m
        })
    return out

# ---------- Hooks after Google writes ----------
def on_review_reply_saved(review_id: str, reply_text: str, reply_update_time: str, actor_uid: str = None):
    loc_num = review_id.split("/locations/")[1].split("/")[0] if "/locations/" in review_id else None
    if loc_num:
        _ensure_location_exists(f"locations/{loc_num}")
    try:
        _sb_upsert("reviews", [{
            "review_id": review_id,
            "location_id": f"locations/{loc_num}" if loc_num else None,
            "is_deleted": False,
            "update_time": reply_update_time,
            "create_time": reply_update_time,
            "raw": {"_stub": True}
        }], on_conflict="review_id")
    except Exception as e:
        print("ensure_review_for_reply failed (ignored):", e)

    _sb_upsert("review_replies", [{
        "review_id": review_id,
        "reply_text": reply_text or "",
        "reply_update_time": reply_update_time,
        "replied_by": actor_uid
    }], on_conflict="review_id")
    _sb_upsert("audit_log", [{
        "action": "review.reply.update",
        "actor_uid": actor_uid,
        "gbp_resource": review_id,
        "payload": {"reply_text": reply_text or ""},
        "ts": _now_iso()
    }])

def on_media_deleted(media_id: str, actor_uid: str = None):
    try:
        _session.patch(f"{SUPABASE_URL}/rest/v1/media",
                       headers=_sb_headers(True),
                       params={"media_id": f"eq.{media_id}"},
                       data=json.dumps({"deleted_at": _now_iso()}),
                       timeout=30)
    finally:
        _sb_upsert("audit_log", [{
            "action": "media.delete",
            "actor_uid": actor_uid,
            "gbp_resource": media_id,
            "payload": {},
            "ts": _now_iso()
        }])

def on_media_uploaded(location_id: str, google_media_object: Dict[str,Any], actor_uid: str = None):
    _ensure_location_exists(location_id)  # FK parent
    rows = _normalize_media_rows(location_id, [google_media_object])
    if rows:
        _sb_upsert("media", rows, on_conflict="media_id")
    _sb_upsert("audit_log", [{
        "action": "media.upload",
        "actor_uid": actor_uid,
        "gbp_resource": rows[0]["media_id"] if rows else None,
        "payload": google_media_object,
        "ts": _now_iso()
    }])

def get_profile_role_by_id(user_id: str) -> str | None:
    """Restituisce profiles.role per un utente usando la SERVICE_ROLE key.
    Utile per verificare il ruolo lato server (non fidarsi della sessione/cookie).
    """
    if not user_id:
        return None
    params = {"select": "role", "id": f"eq.{user_id}", "limit": "1"}
    r = _sb_get("profiles", params)
    r.raise_for_status()
    arr = r.json() or []
    if not arr:
        return None
    return arr[0].get("role")
