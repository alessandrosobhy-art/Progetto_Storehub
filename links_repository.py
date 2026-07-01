import os
import json
import mimetypes
from uuid import uuid4
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""

# Default bucket/table names (create them in Supabase)
LINKS_BUCKET = os.getenv("SUPABASE_LINKS_BUCKET") or "app-links"
LINKS_TABLE = os.getenv("SUPABASE_LINKS_TABLE") or "app_links"
CATEGORIES_TABLE = os.getenv("SUPABASE_LINKS_CATEGORIES_TABLE") or "app_link_categories"

_session = requests.Session()
_TENANT_COLUMN_CACHE: Dict[str, bool] = {}


class SupabaseConfigError(RuntimeError):
    pass


def _require_config() -> None:
    if not SUPABASE_URL:
        raise SupabaseConfigError("SUPABASE_URL non configurato")
    if not SUPABASE_SERVICE_ROLE_KEY:
        raise SupabaseConfigError("SUPABASE_SERVICE_ROLE_KEY non configurato")


def _sb_headers_json() -> Dict[str, str]:
    _require_config()
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    }


def _sb_headers_plain() -> Dict[str, str]:
    _require_config()
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }


def public_image_url(image_path: Optional[str]) -> str:
    if not image_path:
        return ""
    # Works only if the bucket is PUBLIC
    return f"{SUPABASE_URL}/storage/v1/object/public/{LINKS_BUCKET}/{image_path.lstrip('/')}"


def _extract_category_name(val: Any) -> str:
    """Supabase REST join can return an object or a list. Extract category name safely."""
    try:
        if not val:
            return ""
        if isinstance(val, dict):
            return (val.get("name") or "").strip()
        if isinstance(val, list) and val:
            if isinstance(val[0], dict):
                return (val[0].get("name") or "").strip()
        return ""
    except Exception:
        return ""


def _table_supports_tenant_key(table_name: str) -> bool:
    cached = _TENANT_COLUMN_CACHE.get(table_name)
    if cached is not None:
        return bool(cached)
    try:
        r = _session.get(
            f"{SUPABASE_URL}/rest/v1/{table_name}",
            headers=_sb_headers_plain(),
            params={"select": "id,tenant_key", "limit": "1"},
            timeout=20,
        )
        r.raise_for_status()
        _TENANT_COLUMN_CACHE[table_name] = True
    except Exception:
        _TENANT_COLUMN_CACHE[table_name] = False
    return bool(_TENANT_COLUMN_CACHE[table_name])


def _apply_tenant_filter(params: Dict[str, Any], table_name: str, tenant_key: Optional[str]) -> bool:
    tenant = str(tenant_key or "").strip()
    if not tenant:
        return True
    if not _table_supports_tenant_key(table_name):
        return tenant == "default"
    if tenant == "default":
        params["or"] = "(tenant_key.eq.default,tenant_key.is.null)"
    else:
        params["tenant_key"] = f"eq.{tenant}"
    return True


def _apply_tenant_payload(payload: Dict[str, Any], table_name: str, tenant_key: Optional[str]) -> None:
    tenant = str(tenant_key or "").strip()
    if not tenant:
        return
    if not _table_supports_tenant_key(table_name):
        if tenant != "default":
            raise RuntimeError(f"La tabella {table_name} non ha tenant_key: aggiungila prima di creare link tenant-specific.")
        return
    payload["tenant_key"] = tenant


# -----------------------
# Categories
# -----------------------
def list_categories(include_inactive: bool = False, tenant_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns categories ordered by sort_order asc, then name asc."""
    _require_config()
    params = {
        "select": "id,name,sort_order,is_active,created_at,updated_at",
        "order": "sort_order.asc,name.asc",
    }
    if not include_inactive:
        params["is_active"] = "eq.true"
    if not _apply_tenant_filter(params, CATEGORIES_TABLE, tenant_key):
        return []

    r = _session.get(
        f"{SUPABASE_URL}/rest/v1/{CATEGORIES_TABLE}",
        headers=_sb_headers_plain(),
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    return r.json() or []


def get_category(category_id: str, tenant_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    _require_config()
    if not category_id:
        return None
    params = {
        "select": "id,name,sort_order,is_active,created_at,updated_at",
        "id": f"eq.{category_id}",
        "limit": "1",
    }
    if not _apply_tenant_filter(params, CATEGORIES_TABLE, tenant_key):
        return None
    r = _session.get(
        f"{SUPABASE_URL}/rest/v1/{CATEGORIES_TABLE}",
        headers=_sb_headers_plain(),
        params=params,
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    rows = r.json() or []
    return rows[0] if rows else None


def create_category(*, name: str, sort_order: int = 0, is_active: bool = True, tenant_key: Optional[str] = None) -> Dict[str, Any]:
    _require_config()
    cid = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    payload: Dict[str, Any] = {
        "id": cid,
        "name": (name or "").strip(),
        "sort_order": int(sort_order or 0),
        "is_active": bool(is_active),
        "updated_at": now,
    }
    _apply_tenant_payload(payload, CATEGORIES_TABLE, tenant_key)

    r = _session.post(
        f"{SUPABASE_URL}/rest/v1/{CATEGORIES_TABLE}",
        headers=_sb_headers_json(),
        data=json.dumps([payload]),
        timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"Supabase insert category failed: {r.status_code} {r.text}")

    try:
        rows = r.json() if r.text else []
    except Exception:
        rows = []
    return rows[0] if isinstance(rows, list) and rows else (get_category(cid, tenant_key=tenant_key) or payload)


def update_category(*, category_id: str, name: str, sort_order: int = 0, is_active: bool = True, tenant_key: Optional[str] = None) -> None:
    _require_config()
    if not category_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "name": (name or "").strip(),
        "sort_order": int(sort_order or 0),
        "is_active": bool(is_active),
        "updated_at": now,
    }
    _apply_tenant_payload(payload, CATEGORIES_TABLE, tenant_key)
    params = {"id": f"eq.{category_id}"}
    if not _apply_tenant_filter(params, CATEGORIES_TABLE, tenant_key):
        return
    r = _session.patch(
        f"{SUPABASE_URL}/rest/v1/{CATEGORIES_TABLE}",
        headers=_sb_headers_json(),
        params=params,
        data=json.dumps(payload),
        timeout=30,
    )
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Supabase update category failed: {r.status_code} {r.text}")


def delete_category(category_id: str, tenant_key: Optional[str] = None) -> None:
    _require_config()
    if not category_id:
        return
    params = {"id": f"eq.{category_id}"}
    if not _apply_tenant_filter(params, CATEGORIES_TABLE, tenant_key):
        return
    r = _session.delete(
        f"{SUPABASE_URL}/rest/v1/{CATEGORIES_TABLE}",
        headers=_sb_headers_plain(),
        params=params,
        timeout=30,
    )
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Supabase delete category failed: {r.status_code} {r.text}")


# -----------------------
# Links
# -----------------------
def list_links(active_only: bool = True, tenant_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns links ordered by sort_order asc, then created_at asc."""
    _require_config()
    params = {
        # join category name (can be object or array)
        "select": "id,title,url,image_path,category_id,sort_order,is_active,created_at,updated_at,app_link_categories(name)",
        "order": "sort_order.asc,created_at.asc",
    }
    if active_only:
        params["is_active"] = "eq.true"
    if not _apply_tenant_filter(params, LINKS_TABLE, tenant_key):
        return []

    r = _session.get(
        f"{SUPABASE_URL}/rest/v1/{LINKS_TABLE}",
        headers=_sb_headers_plain(),
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json() or []
    for row in rows:
        row["image_url"] = public_image_url(row.get("image_path"))
        row["category_name"] = _extract_category_name(row.get("app_link_categories"))
    return rows


def list_links_grouped(tenant_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns a list of groups: [{id,name,sort_order,links:[...]}], ready for the /link page."""
    cats = list_categories(include_inactive=False, tenant_key=tenant_key)
    links = list_links(active_only=True, tenant_key=tenant_key)

    cat_map: Dict[str, Dict[str, Any]] = {}
    for c in cats:
        cid = c.get("id")
        if not cid:
            continue
        cat_map[cid] = {
            "id": cid,
            "name": c.get("name") or "",
            "sort_order": int(c.get("sort_order") or 0),
            "links": [],
        }

    uncategorized = {"id": None, "name": "Senza categoria", "sort_order": 10_000, "links": []}

    for l in links:
        cid = l.get("category_id")
        if cid and cid in cat_map:
            cat_map[cid]["links"].append(l)
        else:
            uncategorized["links"].append(l)

    groups = sorted(cat_map.values(), key=lambda x: (int(x.get("sort_order") or 0), (x.get("name") or "").lower()))
    if uncategorized["links"]:
        groups.append(uncategorized)
    return groups


def get_link(link_id: str, tenant_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    _require_config()
    if not link_id:
        return None
    params = {
        "select": "id,title,url,image_path,category_id,sort_order,is_active,created_at,updated_at",
        "id": f"eq.{link_id}",
        "limit": "1",
    }
    if not _apply_tenant_filter(params, LINKS_TABLE, tenant_key):
        return None
    r = _session.get(
        f"{SUPABASE_URL}/rest/v1/{LINKS_TABLE}",
        headers=_sb_headers_plain(),
        params=params,
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    rows = r.json() or []
    if not rows:
        return None
    row = rows[0]
    row["image_url"] = public_image_url(row.get("image_path"))
    return row


def upsert_link(
    *,
    link_id: Optional[str],
    title: str,
    url: str,
    sort_order: int = 0,
    is_active: bool = True,
    image_path: Optional[str] = None,
    category_id: Optional[str] = None,
    tenant_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates or updates a link (upsert by id). Returns the stored record."""
    _require_config()

    lid = (link_id or "").strip() or str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    payload: Dict[str, Any] = {
        "id": lid,
        "title": (title or "").strip() or None,
        "url": (url or "").strip(),
        "sort_order": int(sort_order or 0),
        "is_active": bool(is_active),
        "image_path": (image_path or "").strip() or None,
        "category_id": (category_id or "").strip() or None,
        "updated_at": now,
    }
    _apply_tenant_payload(payload, LINKS_TABLE, tenant_key)

    r = _session.post(
        f"{SUPABASE_URL}/rest/v1/{LINKS_TABLE}",
        headers=_sb_headers_json(),
        params={"on_conflict": "id"},
        data=json.dumps([payload]),
        timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"Supabase upsert failed: {r.status_code} {r.text}")

    try:
        rows = r.json() if r.text else []
    except Exception:
        rows = []
    row = rows[0] if isinstance(rows, list) and rows else get_link(lid, tenant_key=tenant_key) or payload
    row["image_url"] = public_image_url(row.get("image_path"))
    return row


def delete_link(link_id: str, tenant_key: Optional[str] = None) -> None:
    _require_config()
    if not link_id:
        return
    params = {"id": f"eq.{link_id}"}
    if not _apply_tenant_filter(params, LINKS_TABLE, tenant_key):
        return
    r = _session.delete(
        f"{SUPABASE_URL}/rest/v1/{LINKS_TABLE}",
        headers=_sb_headers_plain(),
        params=params,
        timeout=30,
    )
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Supabase delete failed: {r.status_code} {r.text}")


# -----------------------
# Supabase Storage
# -----------------------
def _guess_ext(filename: str, content_type: str) -> str:
    ext = ""
    if filename:
        _, ext = os.path.splitext(filename)
    ext = (ext or "").lower().strip(".")
    if ext and len(ext) <= 8:
        return ext
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed.strip(".")
    return "bin"


def storage_upload_image(
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    link_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Uploads image to Supabase Storage. Returns (image_path, public_url).

    NOTE: bucket must be PUBLIC for public_url to work.
    """
    _require_config()
    if not file_bytes:
        raise ValueError("file_bytes vuoto")

    lid = (link_id or "").strip() or str(uuid4())
    ext = _guess_ext(filename or "", content_type or "")
    object_path = f"{lid}.{ext}"  # flat path to keep it simple

    url = f"{SUPABASE_URL}/storage/v1/object/{LINKS_BUCKET}/{object_path}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "true",
    }

    # Try POST then PUT (Supabase Storage supports one of them depending on setup/version)
    r = _session.post(url, headers=headers, data=file_bytes, timeout=60)
    if r.status_code not in (200, 201):
        r = _session.put(url, headers=headers, data=file_bytes, timeout=60)

    if r.status_code not in (200, 201):
        raise RuntimeError(f"Supabase storage upload failed: {r.status_code} {r.text}")

    pub = public_image_url(object_path)
    return object_path, pub


def storage_delete_image(image_path: Optional[str]) -> None:
    _require_config()
    if not image_path:
        return
    obj = image_path.lstrip("/")
    url = f"{SUPABASE_URL}/storage/v1/object/{LINKS_BUCKET}/{obj}"
    r = _session.delete(url, headers=_sb_headers_plain(), timeout=30)
    # 200 or 204 ok; 404 ok
    if r.status_code not in (200, 204, 404):
        raise RuntimeError(f"Supabase storage delete failed: {r.status_code} {r.text}")
