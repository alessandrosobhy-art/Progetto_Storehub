"""sharepoint_test_repository.py

Utility per testare lettura/scrittura di un file Access (.mdb) su SharePoint
tramite Microsoft Graph, usando i token salvati in Supabase (tabella ms_tokens).

Note operative:
- Modifica del DB: per scrivere sul DB remoto scarichiamo il file .mdb, lo
  modifichiamo in locale (file temporaneo) e lo ricarichiamo su SharePoint.
- Concorrenza: se più utenti scrivono insieme, l'ultimo upload vince.
"""

from __future__ import annotations

import base64
import io
import os
import tempfile
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


try:
    import pyodbc
except Exception:  # pragma: no cover
    pyodbc = None


# ---------------------------- Config ---------------------------------------

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY") or ""
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""

MS_TENANT = os.getenv("MS_TENANT", "common")
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")
MS_REDIRECT_URI = os.getenv("MS_REDIRECT_URI", "http://localhost:5000/ms/callback")

MS_TOKEN_URL = f"https://login.microsoftonline.com/{MS_TENANT}/oauth2/v2.0/token"
MS_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ---------------------------- Helpers --------------------------------------

class SharePointTestError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_dt(s: str | None) -> datetime:
    if not s:
        return _utcnow() - timedelta(days=365)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return _utcnow() - timedelta(days=365)


def _sb_headers(token: str | None, use_service_role: bool = False) -> Dict[str, str]:
    # Preferiamo usare il JWT dell'utente (RLS), ma possiamo fare fallback
    # con service role se presente.
    h = {
        "apikey": SUPABASE_ANON_KEY or SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "application/json",
    }
    if use_service_role and SUPABASE_SERVICE_ROLE_KEY:
        h["Authorization"] = f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
    elif token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _sb_get_ms_tokens(sb_jwt: str, user_id: str) -> Optional[Dict[str, Any]]:
    if not SUPABASE_URL:
        raise SharePointTestError("SUPABASE_URL mancante")
    url = f"{SUPABASE_URL}/rest/v1/ms_tokens"
    params = {
        "select": "access_token,refresh_token,expires_at,provider",
        "user_id": f"eq.{user_id}",
        "provider": "eq.microsoft",
        "limit": "1",
    }

    # 1) prova con JWT utente (RLS)
    r = requests.get(url, headers=_sb_headers(sb_jwt, use_service_role=False), params=params, timeout=20)

    if r.status_code == 200:
        rows = r.json() or []
        if rows:
            return rows[0]
        # Se la policy RLS filtra tutto (200 ma lista vuota), prova anche con Service Role
        if SUPABASE_SERVICE_ROLE_KEY:
            r2 = requests.get(url, headers=_sb_headers(sb_jwt, use_service_role=True), params=params, timeout=20)
            if r2.status_code != 200:
                raise SharePointTestError(f"Supabase ms_tokens read failed: {r2.status_code} {r2.text}")
            rows2 = r2.json() or []
            return rows2[0] if rows2 else None
        return None

    if r.status_code in (401, 403) and SUPABASE_SERVICE_ROLE_KEY:
        # 2) fallback service role (es. JWT mancante / policy blocca)
        r = requests.get(url, headers=_sb_headers(sb_jwt, use_service_role=True), params=params, timeout=20)
        if r.status_code != 200:
            raise SharePointTestError(f"Supabase ms_tokens read failed: {r.status_code} {r.text}")
        rows = r.json() or []
        return rows[0] if rows else None

    raise SharePointTestError(f"Supabase ms_tokens read failed: {r.status_code} {r.text}")


def _sb_upsert_ms_tokens(sb_jwt: str, row: Dict[str, Any]) -> None:
    if not SUPABASE_URL:
        raise SharePointTestError("SUPABASE_URL mancante")
    url = f"{SUPABASE_URL}/rest/v1/ms_tokens?on_conflict=user_id,provider"
    payload = [row]
    headers = _sb_headers(sb_jwt, use_service_role=False)
    headers["Prefer"] = "resolution=merge-duplicates"
    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    if r.status_code in (401, 403) and SUPABASE_SERVICE_ROLE_KEY:
        headers = _sb_headers(sb_jwt, use_service_role=True)
        headers["Prefer"] = "resolution=merge-duplicates"
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    if r.status_code not in (200, 201, 204):
        raise SharePointTestError(f"Supabase ms_tokens upsert failed: {r.status_code} {r.text}")


def get_graph_access_token(sb_jwt: str, user_id: str) -> Tuple[str, bool]:
    """Ritorna (access_token, refreshed).

    Se il token è scaduto prova a fare refresh e aggiorna ms_tokens.
    """
    row = _sb_get_ms_tokens(sb_jwt, user_id)
    if not row:
        raise SharePointTestError("Nessun token Microsoft trovato in ms_tokens per questo utente")

    access_token = row.get("access_token")
    refresh_token = row.get("refresh_token")
    expires_at = _parse_iso_dt(row.get("expires_at"))

    if access_token and expires_at > (_utcnow() + timedelta(seconds=30)):
        return access_token, False

    if not refresh_token:
        raise SharePointTestError("Token scaduto e refresh_token mancante in ms_tokens")
    if not (MS_CLIENT_ID and MS_CLIENT_SECRET):
        raise SharePointTestError("MS_CLIENT_ID/MS_CLIENT_SECRET mancanti in .env")

    # Importante: in OAuth v2 la scope influenza l'access token ottenuto.
    # Usiamo scopes SharePoint se presenti, altrimenti fallback a MS_SCOPES.
    scopes = (os.getenv("MS_SCOPES_SHAREPOINT") or "").strip()
    if not scopes:
        cand = (os.getenv("MS_SCOPES") or "").strip()
        lc = cand.lower()
        # Se MS_SCOPES non include permessi Files/Sites, non usarlo per evitare token "monchi"
        if cand and ("files." in lc or "sites." in lc):
            scopes = cand
    if not scopes:
        scopes = "offline_access User.Read Files.ReadWrite.All Sites.ReadWrite.All"
    data = {
        "client_id": MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        # redirect_uri è opzionale nel refresh; lo includiamo perché spesso
        # è configurato così nelle app esistenti.
        "redirect_uri": MS_REDIRECT_URI,
        "scope": scopes,
    }
    tok = requests.post(MS_TOKEN_URL, data=data, timeout=30)
    if not tok.ok:
        raise SharePointTestError(f"Refresh token Microsoft fallito: {tok.status_code} {tok.text}")
    js = tok.json() or {}
    new_access = js.get("access_token")
    new_refresh = js.get("refresh_token") or refresh_token
    expires_in = int(js.get("expires_in", 3600) or 3600)
    new_exp = _utcnow() + timedelta(seconds=max(60, expires_in - 60))

    if not new_access:
        raise SharePointTestError("Refresh token Microsoft: access_token mancante nella risposta")

    _sb_upsert_ms_tokens(
        sb_jwt,
        {
            "user_id": user_id,
            "provider": "microsoft",
            "access_token": new_access,
            "refresh_token": new_refresh,
            "expires_at": new_exp.isoformat(),
        },
    )
    return new_access, True


def graph_share_id_from_url(sharing_url: str) -> str:
    """Converte un sharing URL in shareId per /shares/{shareId}/driveItem."""
    raw = sharing_url.encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    return f"u!{b64}"


def _graph_headers(access_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def graph_get_json(access_token: str, url: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    r = requests.get(url, headers=_graph_headers(access_token), params=params or {}, timeout=60)
    if not r.ok:
        raise SharePointTestError(f"Graph GET failed: {r.status_code} {r.text}")
    return r.json() if r.text else {}


@dataclass
class ResolvedShare:
    drive_id: str
    folder_item_id: str
    folder_name: str
    web_url: str


def resolve_sharing_url(access_token: str, sharing_url: str) -> ResolvedShare:
    share_id = graph_share_id_from_url(sharing_url)
    url = f"{MS_GRAPH_BASE}/shares/{share_id}/driveItem"
    js = graph_get_json(access_token, url)
    pr = js.get("parentReference") or {}
    drive_id = pr.get("driveId")
    folder_item_id = js.get("id")
    folder_name = js.get("name") or ""
    web_url = js.get("webUrl") or ""
    if not (drive_id and folder_item_id):
        raise SharePointTestError("Impossibile risolvere il link di condivisione: driveId/itemId mancanti")
    return ResolvedShare(drive_id=drive_id, folder_item_id=folder_item_id, folder_name=folder_name, web_url=web_url)


def get_item_metadata_by_path(access_token: str, resolved: ResolvedShare, relative_path: str) -> Dict[str, Any]:
    rel = (relative_path or "").lstrip("/")
    url = f"{MS_GRAPH_BASE}/drives/{resolved.drive_id}/items/{resolved.folder_item_id}:/{rel}"
    return graph_get_json(access_token, url)


def download_item_by_path(access_token: str, resolved: ResolvedShare, relative_path: str) -> bytes:
    rel = (relative_path or "").lstrip("/")
    url = f"{MS_GRAPH_BASE}/drives/{resolved.drive_id}/items/{resolved.folder_item_id}:/{rel}:/content"
    r = requests.get(url, headers=_graph_headers(access_token), timeout=180, allow_redirects=True)
    if not r.ok:
        raise SharePointTestError(f"Download file fallito: {r.status_code} {r.text}")
    return r.content


def _upload_small_put(access_token: str, resolved: ResolvedShare, relative_path: str, content: bytes) -> Dict[str, Any]:
    rel = (relative_path or "").lstrip("/")
    url = f"{MS_GRAPH_BASE}/drives/{resolved.drive_id}/items/{resolved.folder_item_id}:/{rel}:/content"
    headers = _graph_headers(access_token)
    headers.pop("Accept", None)
    r = requests.put(url, headers=headers, data=content, timeout=180)
    if not r.ok:
        raise SharePointTestError(f"Upload file (PUT) fallito: {r.status_code} {r.text}")
    return r.json() if r.text else {}


def _upload_large_session(access_token: str, resolved: ResolvedShare, relative_path: str, content: bytes) -> Dict[str, Any]:
    rel = (relative_path or "").lstrip("/")
    create_url = f"{MS_GRAPH_BASE}/drives/{resolved.drive_id}/items/{resolved.folder_item_id}:/{rel}:/createUploadSession"
    body = {"item": {"@microsoft.graph.conflictBehavior": "replace"}}
    r = requests.post(create_url, headers={**_graph_headers(access_token), "Content-Type": "application/json"}, json=body, timeout=60)
    if not r.ok:
        raise SharePointTestError(f"createUploadSession fallito: {r.status_code} {r.text}")
    upload_url = (r.json() or {}).get("uploadUrl")
    if not upload_url:
        raise SharePointTestError("createUploadSession: uploadUrl mancante")

    total = len(content)
    chunk_size = 10 * 1024 * 1024  # 10MB
    start = 0
    last_resp: Dict[str, Any] = {}
    while start < total:
        end = min(start + chunk_size, total) - 1
        chunk = content[start : end + 1]
        headers = {
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {start}-{end}/{total}",
        }
        rr = requests.put(upload_url, headers=headers, data=chunk, timeout=180)
        if rr.status_code in (200, 201):
            last_resp = rr.json() if rr.text else {}
            break
        if rr.status_code != 202:
            raise SharePointTestError(f"Upload chunk fallito: {rr.status_code} {rr.text}")
        # 202 -> continua
        start = end + 1
    return last_resp


def upload_item_by_path(access_token: str, resolved: ResolvedShare, relative_path: str, content: bytes) -> Dict[str, Any]:
    # Soglia semplice: se > 4MB usiamo upload session.
    if len(content) <= 4 * 1024 * 1024:
        return _upload_small_put(access_token, resolved, relative_path, content)
    return _upload_large_session(access_token, resolved, relative_path, content)


# ---------------------------- Access (.mdb) ops ----------------------------

def _access_connect(db_path: str, password: str | None) -> "pyodbc.Connection":
    if pyodbc is None:
        raise SharePointTestError(
            "pyodbc non è installato. Installa il driver ODBC di Access e 'pip install pyodbc' "
            "nell'ambiente dove gira l'app."
        )
    if not os.path.exists(db_path):
        raise SharePointTestError(f"File .mdb non trovato (temp): {db_path}")
    pwd = password or ""
    conn_str = (
        r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};"
        f"DBQ={db_path};"
        + (f"PWD={pwd};" if pwd else "")
    )
    return pyodbc.connect(conn_str)


def _access_list_tables(conn) -> List[str]:
    cur = conn.cursor()
    out: List[str] = []
    for row in cur.tables(tableType="TABLE"):
        name = getattr(row, "table_name", None) or (len(row) > 2 and row[2]) or None
        if not name:
            continue
        if str(name).startswith("MSys"):
            continue
        out.append(str(name))
    return sorted(set(out))


def _access_has_table(conn, table_name: str) -> bool:
    t = table_name.lower().strip()
    for n in _access_list_tables(conn):
        if n.lower() == t:
            return True
    return False


def _access_ensure_test_table(conn, table_name: str = "SP_DDT_TEST") -> None:
    if _access_has_table(conn, table_name):
        return
    cur = conn.cursor()
    # Access SQL: COUNTER = autonumber
    sql = f"""
    CREATE TABLE {table_name} (
        id COUNTER PRIMARY KEY,
        site TEXT(20),
        ddt_number LONG,
        ddt_date DATETIME,
        note MEMO,
        inserted_at DATETIME,
        inserted_by TEXT(255)
    )
    """.strip()
    cur.execute(sql)
    conn.commit()


def access_read_test_info(mdb_bytes: bytes, password: str | None) -> Dict[str, Any]:
    """Apre il DB e ritorna info utili (tabelle + ultime righe della tabella test, se esiste)."""
    with tempfile.TemporaryDirectory(prefix="sp_mdb_") as td:
        db_path = os.path.join(td, "Datifile.mdb")
        with open(db_path, "wb") as f:
            f.write(mdb_bytes)

        conn = _access_connect(db_path, password)
        try:
            tables = _access_list_tables(conn)
            last_rows: List[Dict[str, Any]] = []
            if _access_has_table(conn, "SP_DDT_TEST"):
                cur = conn.cursor()
                cur.execute(
                    "SELECT TOP 10 id, site, ddt_number, ddt_date, note, inserted_at, inserted_by FROM SP_DDT_TEST ORDER BY id DESC"
                )
                cols = [c[0] for c in cur.description]
                for row in cur.fetchall() or []:
                    last_rows.append({cols[i]: row[i] for i in range(len(cols))})
            return {"tables": tables, "last_test_rows": last_rows}
        finally:
            try:
                conn.close()
            except Exception:
                pass


def access_insert_test_ddt(
    mdb_bytes: bytes,
    password: str | None,
    site: str,
    ddt_number: int | None,
    ddt_date: datetime | None,
    note: str | None,
    inserted_by: str | None,
) -> Tuple[bytes, Dict[str, Any]]:
    """Inserisce una riga nella tabella SP_DDT_TEST (creandola se serve) e ritorna (new_mdb_bytes, info)."""
    with tempfile.TemporaryDirectory(prefix="sp_mdb_") as td:
        db_path = os.path.join(td, "Datifile.mdb")
        with open(db_path, "wb") as f:
            f.write(mdb_bytes)

        conn = _access_connect(db_path, password)
        try:
            _access_ensure_test_table(conn, "SP_DDT_TEST")
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO SP_DDT_TEST (site, ddt_number, ddt_date, note, inserted_at, inserted_by) VALUES (?,?,?,?,?,?)",
                (
                    str(site or "").strip(),
                    int(ddt_number) if ddt_number is not None else None,
                    ddt_date if ddt_date is not None else _utcnow(),
                    (note or ""),
                    _utcnow(),
                    (inserted_by or ""),
                ),
            )
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # Leggiamo bytes aggiornati dopo aver chiuso la connessione (evita lock su Windows)
        with open(db_path, "rb") as rf:
            out_bytes = rf.read()

        # Rileggiamo le ultime righe (su una nuova connessione)
        info = access_read_test_info(out_bytes, password)
        return out_bytes, info
