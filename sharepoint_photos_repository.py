"""sharepoint_photos_repository.py

Gestione upload/download/delete foto per l'app (SharePoint) tramite Microsoft Graph.

La cartella di destinazione è una folder SharePoint condivisa tramite link.
Sotto a quella folder viene creata (se non esiste) una sottocartella per store (SITE).

Dipendenze:
- Token Microsoft salvati in Supabase (tabella ms_tokens) come già usato per il DB cloud.
"""

from __future__ import annotations

import io
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import uuid4

import requests
from PIL import Image, ImageOps

from sharepoint_test_repository import (
    MS_GRAPH_BASE,
    ResolvedShare,
    SharePointTestError,
    SUPABASE_SERVICE_ROLE_KEY,
    get_graph_access_token,
    resolve_sharing_url,
    upload_item_by_path,
    download_item_by_path,
)


_PHOTO_CATEGORY_FOLDER = {
    "spese": "SPESE",
    "versamenti": "VERSAMENTI",
    "azzeramenti": "AZZERAMENTI",
}
_PHOTO_RULE_KEY = {
    "spese": "rendiconto_spese",
    "versamenti": "rendiconto_versamenti",
    "azzeramenti": "rendiconto_distinta_cassa",
}


# Sharing URL della folder: FOTO_APP/SPESE
_DEFAULT_SP_SPESE_PHOTO_SHARING_URL = (
    "https://fbinvestmentholding.sharepoint.com/:f:/r/sites/FB/F%20%20B/I%20LOVE%20POKE/OPS/CDG/"
    "FOTO_APP/SPESE?e=5%3adb1ef131bac942278393e9dc95e12f03&sharingv2=true&fromShare=true&at=9"
)


def get_spese_photo_sharing_url() -> str:
    return (
        (os.getenv("SP_SPESE_PHOTO_SHARING_URL") or "").strip()
        or (os.getenv("SP_SPESE_FOTO_SHARING_URL") or "").strip()
        or _DEFAULT_SP_SPESE_PHOTO_SHARING_URL
    )


# Sharing URL della folder: FOTO_APP/VERSAMENTI
_DEFAULT_SP_VERSAMENTI_PHOTO_SHARING_URL = (
    "https://fbinvestmentholding.sharepoint.com/:f:/r/sites/FB/F%20%20B/I%20LOVE%20POKE/OPS/CDG/"
    "FOTO_APP/VERSAMENTI?e=5%3adb1ef131bac942278393e9dc95e12f03&sharingv2=true&fromShare=true&at=9"
)


def get_versamenti_photo_sharing_url() -> str:
    return (
        (os.getenv("SP_VERSAMENTI_PHOTO_SHARING_URL") or "").strip()
        or (os.getenv("SP_VERSAMENTI_FOTO_SHARING_URL") or "").strip()
        or _DEFAULT_SP_VERSAMENTI_PHOTO_SHARING_URL
    )


# Sharing URL della folder: FOTO_APP/AZZERAMENTI
_DEFAULT_SP_AZZERAMENTI_PHOTO_SHARING_URL = (
    "https://fbinvestmentholding.sharepoint.com/:f:/r/sites/FB/F%20%20B/I%20LOVE%20POKE/OPS/CDG/"
    "FOTO_APP/AZZERAMENTI?e=5%3adb1ef131bac942278393e9dc95e12f03&sharingv2=true&fromShare=true&at=9"
)


def get_azzeramenti_photo_sharing_url() -> str:
    return (
        (os.getenv("SP_AZZERAMENTI_PHOTO_SHARING_URL") or "").strip()
        or (os.getenv("SP_AZZERAMENTI_FOTO_SHARING_URL") or "").strip()
        or _DEFAULT_SP_AZZERAMENTI_PHOTO_SHARING_URL
    )



def _graph_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _safe_segment(name: str) -> str:
    # Folder name: limitiamo a caratteri sicuri.
    s = (name or "").strip()
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", s)
    s = s.strip("._-")
    return s or "UNKNOWN"


@dataclass(frozen=True)
class _PhotoStorageContext:
    provider: str
    local_base_path: str = ""
    sharepoint_sharing_url: str = ""
    sharepoint_base_path: str = ""


def _join_local_storage_path(root_path: str, rule_path: str, category: str) -> str:
    root = str(root_path or "").strip().strip("\"'")
    rule = str(rule_path or "").strip().strip("\"'").strip("/\\")
    category_folder = _PHOTO_CATEGORY_FOLDER.get(category, category).strip("/\\")
    if not root:
        raise SharePointTestError("Cartella storage tenant non configurata")

    parts = [p for p in re.split(r"[\\/]+", rule) if p]
    root_tail = os.path.basename(os.path.normpath(root)).lower()
    if parts and parts[0].lower() == root_tail:
        parts = parts[1:]
    if not parts or parts[-1].lower() != category_folder.lower():
        parts.append(category_folder)
    return os.path.join(root, *parts)


def _looks_like_local_path(value: str) -> bool:
    path = str(value or "").strip().strip("\"'")
    return bool(re.match(r"^[A-Za-z]:[\\/]", path) or path.startswith("\\\\") or path.startswith("/"))


def _tenant_photo_storage_context(category: str) -> _PhotoStorageContext:
    try:
        from flask import has_request_context, session
        from tenant_config_repository import get_current_tenant, get_storage_rule, get_tenant

        session_tenant_key = ""
        try:
            if has_request_context():
                session_tenant_key = str(session.get("tenant_key") or session.get("master_admin_tenant_key") or "").strip()
        except Exception:
            session_tenant_key = ""
        tenant = get_tenant(session_tenant_key) if session_tenant_key else (get_current_tenant() or {})
        tenant_key = str(tenant.get("tenant_key") or session_tenant_key or "default")
        rule = get_storage_rule(tenant_key, _PHOTO_RULE_KEY.get(category, category)) or {}
    except Exception:
        tenant = {}
        rule = {}

    rule_active = bool(rule.get("is_active", True))
    rule_provider = str(rule.get("provider") or "").strip().lower()
    rule_path = str(rule.get("base_path") or "").strip()
    rule_sharepoint_url = str(rule.get("sharepoint_sharing_url") or "").strip()
    tenant_storage_type = str(tenant.get("storage_type") or "").strip().lower()
    tenant_local_root = str(tenant.get("storage_base_path") or "").strip()

    if rule_active and _looks_like_local_path(rule_path):
        return _PhotoStorageContext(
            provider="local_vm",
            local_base_path=_join_local_storage_path(rule_path, "", category),
        )

    if rule_active and rule_provider == "local_vm":
        local_root = tenant_local_root or rule_path
        return _PhotoStorageContext(
            provider="local_vm",
            local_base_path=_join_local_storage_path(local_root, rule_path, category),
        )

    if rule_active and rule_provider == "sharepoint" and rule_sharepoint_url:
        return _PhotoStorageContext(
            provider="sharepoint",
            sharepoint_sharing_url=rule_sharepoint_url,
            sharepoint_base_path=rule_path.strip("/\\"),
        )

    if tenant_storage_type == "local_vm":
        local_root = tenant_local_root or rule_path
        return _PhotoStorageContext(
            provider="local_vm",
            local_base_path=_join_local_storage_path(local_root, rule_path, category),
        )

    if tenant_local_root and not rule_sharepoint_url:
        return _PhotoStorageContext(
            provider="local_vm",
            local_base_path=_join_local_storage_path(tenant_local_root, rule_path, category),
        )

    if tenant_storage_type == "sharepoint":
        sharing_url = str(tenant.get("sharepoint_sharing_url") or "").strip()
        base_path = str(rule_path or tenant.get("sharepoint_base_path") or "").strip().strip("/\\")
        if sharing_url:
            return _PhotoStorageContext(
                provider="sharepoint",
                sharepoint_sharing_url=sharing_url,
                sharepoint_base_path=base_path,
            )

    return _PhotoStorageContext(provider="sharepoint")


def _local_photo_path(*, category: str, store_code: str, filename: str, ensure_folder: bool) -> str | None:
    ctx = _tenant_photo_storage_context(category)
    if ctx.provider != "local_vm":
        return None
    safe_name = os.path.basename(str(filename or "").strip())
    if not safe_name:
        raise SharePointTestError("Filename mancante")
    folder = os.path.join(ctx.local_base_path, _safe_segment(store_code))
    if ensure_folder:
        os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, safe_name)


def _write_local_photo(*, category: str, store_code: str, filename: str, content: bytes) -> bool:
    target = _local_photo_path(category=category, store_code=store_code, filename=filename, ensure_folder=True)
    if not target:
        return False
    with open(target, "wb") as f:
        f.write(content)
    return True


def _read_local_photo(*, category: str, store_code: str, filename: str) -> bytes | None:
    target = _local_photo_path(category=category, store_code=store_code, filename=filename, ensure_folder=False)
    if not target:
        return None
    if not os.path.exists(target):
        raise SharePointTestError("Foto non trovata")
    with open(target, "rb") as f:
        return f.read()


def _delete_local_photo(*, category: str, store_code: str, filename: str) -> bool | None:
    target = _local_photo_path(category=category, store_code=store_code, filename=filename, ensure_folder=False)
    if not target:
        return None
    if not os.path.exists(target):
        return False
    os.remove(target)
    parent = os.path.dirname(target)
    try:
        if parent and not os.listdir(parent):
            shutil.rmtree(parent)
    except Exception:
        pass
    return True


def _ensure_child_folder(access_token: str, *, drive_id: str, parent_item_id: str, name: str) -> str:
    """Ritorna l'item_id della folder `name` sotto `parent_item_id` (creandola se serve)."""
    name = _safe_segment(name)

    # 1) prova a risolvere per path
    get_url = f"{MS_GRAPH_BASE}/drives/{drive_id}/items/{parent_item_id}:/{name}"
    r = requests.get(get_url, headers=_graph_headers(access_token), timeout=30)
    if r.status_code == 200:
        js = r.json() or {}
        item_id = js.get("id")
        if item_id:
            return str(item_id)
    elif r.status_code not in (404,):
        raise SharePointTestError(f"Graph GET folder failed: {r.status_code} {r.text}")

    # 2) crea folder
    post_url = f"{MS_GRAPH_BASE}/drives/{drive_id}/items/{parent_item_id}/children"
    body = {
        "name": name,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail",
    }
    rr = requests.post(post_url, headers=_graph_headers(access_token), json=body, timeout=60)
    if rr.status_code in (200, 201):
        js = rr.json() or {}
        item_id = js.get("id")
        if not item_id:
            raise SharePointTestError("Creazione folder: item id mancante")
        return str(item_id)

    # 3) se esiste già (race), recupera
    if rr.status_code in (409,):
        r2 = requests.get(get_url, headers=_graph_headers(access_token), timeout=30)
        if r2.status_code == 200:
            js = r2.json() or {}
            item_id = js.get("id")
            if item_id:
                return str(item_id)

    raise SharePointTestError(f"Creazione folder fallita: {rr.status_code} {rr.text}")


def _ensure_folder_path(access_token: str, resolved: ResolvedShare, relative_path: str) -> str:
    parent_id = str(resolved.folder_item_id)
    for part in [p for p in str(relative_path or "").replace("\\", "/").split("/") if p.strip()]:
        parent_id = _ensure_child_folder(
            access_token,
            drive_id=str(resolved.drive_id),
            parent_item_id=parent_id,
            name=part,
        )
    return parent_id


def ensure_store_folder(access_token: str, resolved: ResolvedShare, store_code: str) -> str:
    """Assicura l'esistenza della sottocartella dello store (SITE) e ritorna l'item_id."""
    return _ensure_child_folder(
        access_token,
        drive_id=str(resolved.drive_id),
        parent_item_id=str(resolved.folder_item_id),
        name=str(store_code),
    )


def _photo_target(
    *,
    access_token: str,
    category: str,
    fallback_sharing_url: str,
    store_code: str,
    filename: str,
    ensure_folders: bool,
) -> tuple[ResolvedShare, str]:
    category_folder = _PHOTO_CATEGORY_FOLDER.get(category, category).strip("/\\")
    storage_ctx = _tenant_photo_storage_context(category)
    root_url = storage_ctx.sharepoint_sharing_url
    base_path = storage_ctx.sharepoint_base_path
    if root_url:
        resolved = resolve_sharing_url(access_token, root_url)
        store_segment = _safe_segment(store_code)
        base_tail = base_path.replace("\\", "/").rstrip("/").split("/")[-1].upper() if base_path else ""
        parts = [base_path] if base_path else []
        if base_tail != category_folder.upper():
            parts.append(category_folder)
        parts.append(store_segment)
        folder_path = "/".join(parts)
        if ensure_folders:
            _ensure_folder_path(access_token, resolved, folder_path)
        return resolved, f"{folder_path}/{os.path.basename(str(filename or '').strip())}"

    resolved = resolve_sharing_url(access_token, fallback_sharing_url)
    if ensure_folders:
        ensure_store_folder(access_token, resolved, store_code)
    return resolved, f"{_safe_segment(store_code)}/{os.path.basename(str(filename or '').strip())}"


def _image_to_jpeg_bytes(file_bytes: bytes) -> bytes:
    """Normalizza e converte un'immagine in JPEG (RGB), gestendo EXIF rotation."""
    with Image.open(io.BytesIO(file_bytes)) as im:
        im = ImageOps.exif_transpose(im)
        # Gestione alpha -> compositing su bianco
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")

        out = io.BytesIO()
        im.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue()


def generate_spesa_photo_filename(*, store_code: str, data_iso: str | None = None) -> str:
    dt_part = ""
    if data_iso:
        dt_part = (str(data_iso).strip().replace("-", "") or "")[:8]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = uuid4().hex[:10]
    store = _safe_segment(store_code)
    if dt_part:
        return f"spesa_{store}_{dt_part}_{ts}_{rand}.jpg"
    return f"spesa_{store}_{ts}_{rand}.jpg"


def upload_spesa_photo(
    *,
    sb_jwt: str,
    user_id: str,
    store_code: str,
    file_storage,
    data_iso: Optional[str] = None,
) -> str:
    """Carica la foto in SharePoint e ritorna il filename salvato."""
    # Su alcuni browser (soprattutto mobile/tablet) il cookie di sessione può
    # non contenere sempre l'access_token Supabase (dimensione cookie / policy).
    # In quei casi possiamo comunque lavorare usando la SERVICE_ROLE_KEY per
    # leggere/refreshare i token Microsoft salvati in ms_tokens.
    if not user_id:
        raise SharePointTestError("Sessione non valida: utente non autenticato")

    raw = file_storage.read() if hasattr(file_storage, "read") else None
    if not raw:
        raise SharePointTestError("File vuoto")

    # Limite semplice: 15MB
    if len(raw) > 15 * 1024 * 1024:
        raise SharePointTestError("File troppo grande (max 15MB)")

    # Converte a JPEG per compatibilità browser
    try:
        content = _image_to_jpeg_bytes(raw)
    except Exception as e:
        raise SharePointTestError(f"Formato immagine non supportato: {e}")

    filename = generate_spesa_photo_filename(store_code=store_code, data_iso=data_iso)
    if _write_local_photo(category="spese", store_code=store_code, filename=filename, content=content):
        return filename

    if not sb_jwt and not SUPABASE_SERVICE_ROLE_KEY:
        raise SharePointTestError("Sessione non valida: token Supabase mancante (e SERVICE_ROLE_KEY non configurata)")
    access_token, _ = get_graph_access_token(sb_jwt, user_id)
    sharing_url = get_spese_photo_sharing_url()
    resolved, rel_path = _photo_target(
        access_token=access_token,
        category="spese",
        fallback_sharing_url=sharing_url,
        store_code=store_code,
        filename=filename,
        ensure_folders=True,
    )
    upload_item_by_path(access_token, resolved, rel_path, content)
    return filename


def download_spesa_photo(*, sb_jwt: str, user_id: str, store_code: str, filename: str) -> bytes:
    if not user_id:
        raise SharePointTestError("Sessione non valida: utente non autenticato")
    filename = os.path.basename(str(filename or "").strip())
    if not filename:
        raise SharePointTestError("Filename mancante")

    local_content = _read_local_photo(category="spese", store_code=store_code, filename=filename)
    if local_content is not None:
        return local_content

    if not sb_jwt and not SUPABASE_SERVICE_ROLE_KEY:
        raise SharePointTestError("Sessione non valida: token Supabase mancante (e SERVICE_ROLE_KEY non configurata)")
    access_token, _ = get_graph_access_token(sb_jwt, user_id)
    sharing_url = get_spese_photo_sharing_url()
    resolved, rel_path = _photo_target(
        access_token=access_token,
        category="spese",
        fallback_sharing_url=sharing_url,
        store_code=store_code,
        filename=filename,
        ensure_folders=False,
    )
    return download_item_by_path(access_token, resolved, rel_path)


def delete_spesa_photo(*, sb_jwt: str, user_id: str, store_code: str, filename: str) -> bool:
    """Prova a cancellare la foto. Ritorna True se cancellata, False se non trovata."""
    if not user_id:
        raise SharePointTestError("Sessione non valida: utente non autenticato")
    filename = os.path.basename(str(filename or "").strip())
    if not filename:
        return False

    local_deleted = _delete_local_photo(category="spese", store_code=store_code, filename=filename)
    if local_deleted is not None:
        return local_deleted

    if not sb_jwt and not SUPABASE_SERVICE_ROLE_KEY:
        raise SharePointTestError("Sessione non valida: token Supabase mancante (e SERVICE_ROLE_KEY non configurata)")
    access_token, _ = get_graph_access_token(sb_jwt, user_id)
    sharing_url = get_spese_photo_sharing_url()
    resolved, rel_path = _photo_target(
        access_token=access_token,
        category="spese",
        fallback_sharing_url=sharing_url,
        store_code=store_code,
        filename=filename,
        ensure_folders=False,
    )

    # Risolvi item-id per path
    get_url = f"{MS_GRAPH_BASE}/drives/{resolved.drive_id}/items/{resolved.folder_item_id}:/{rel_path}"
    r = requests.get(get_url, headers=_graph_headers(access_token), timeout=30)
    if r.status_code == 404:
        return False
    if r.status_code != 200:
        raise SharePointTestError(f"Graph GET foto failed: {r.status_code} {r.text}")
    item_id = (r.json() or {}).get("id")
    if not item_id:
        return False

    del_url = f"{MS_GRAPH_BASE}/drives/{resolved.drive_id}/items/{item_id}"
    rr = requests.delete(del_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=60)
    if rr.status_code in (204, 200):
        return True
    if rr.status_code == 404:
        return False
    raise SharePointTestError(f"Delete foto failed: {rr.status_code} {rr.text}")

def generate_versamento_photo_filename(*, store_code: str, data_iso: str | None = None) -> str:
    dt_part = ""
    if data_iso:
        dt_part = (str(data_iso).strip().replace("-", "") or "")[:8]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = uuid4().hex[:10]
    store = _safe_segment(store_code)
    if dt_part:
        return f"versamento_{store}_{dt_part}_{ts}_{rand}.jpg"
    return f"versamento_{store}_{ts}_{rand}.jpg"


def upload_versamento_photo(
    *,
    sb_jwt: str,
    user_id: str,
    store_code: str,
    file_storage,
    data_iso: Optional[str] = None,
) -> str:
    """Carica la foto in SharePoint (VERSAMENTI) e ritorna il filename salvato."""
    if not user_id:
        raise SharePointTestError("Sessione non valida: utente non autenticato")

    raw = file_storage.read() if hasattr(file_storage, "read") else None
    if not raw:
        raise SharePointTestError("File vuoto")

    # Limite semplice: 15MB
    if len(raw) > 15 * 1024 * 1024:
        raise SharePointTestError("File troppo grande (max 15MB)")

    # Converte a JPEG per compatibilità browser
    try:
        content = _image_to_jpeg_bytes(raw)
    except Exception as e:
        raise SharePointTestError(f"Formato immagine non supportato: {e}")

    filename = generate_versamento_photo_filename(store_code=store_code, data_iso=data_iso)
    if _write_local_photo(category="versamenti", store_code=store_code, filename=filename, content=content):
        return filename

    if not sb_jwt and not SUPABASE_SERVICE_ROLE_KEY:
        raise SharePointTestError("Sessione non valida: token Supabase mancante (e SERVICE_ROLE_KEY non configurata)")
    access_token, _ = get_graph_access_token(sb_jwt, user_id)
    sharing_url = get_versamenti_photo_sharing_url()
    resolved, rel_path = _photo_target(
        access_token=access_token,
        category="versamenti",
        fallback_sharing_url=sharing_url,
        store_code=store_code,
        filename=filename,
        ensure_folders=True,
    )
    upload_item_by_path(access_token, resolved, rel_path, content)
    return filename


def download_versamento_photo(*, sb_jwt: str, user_id: str, store_code: str, filename: str) -> bytes:
    if not user_id:
        raise SharePointTestError("Sessione non valida: utente non autenticato")
    filename = os.path.basename(str(filename or "").strip())
    if not filename:
        raise SharePointTestError("Filename mancante")

    local_content = _read_local_photo(category="versamenti", store_code=store_code, filename=filename)
    if local_content is not None:
        return local_content

    if not sb_jwt and not SUPABASE_SERVICE_ROLE_KEY:
        raise SharePointTestError("Sessione non valida: token Supabase mancante (e SERVICE_ROLE_KEY non configurata)")
    access_token, _ = get_graph_access_token(sb_jwt, user_id)
    sharing_url = get_versamenti_photo_sharing_url()
    resolved, rel_path = _photo_target(
        access_token=access_token,
        category="versamenti",
        fallback_sharing_url=sharing_url,
        store_code=store_code,
        filename=filename,
        ensure_folders=False,
    )
    return download_item_by_path(access_token, resolved, rel_path)


def delete_versamento_photo(*, sb_jwt: str, user_id: str, store_code: str, filename: str) -> bool:
    """Prova a cancellare la foto VERSAMENTI. Ritorna True se cancellata, False se non trovata."""
    if not user_id:
        raise SharePointTestError("Sessione non valida: utente non autenticato")
    filename = os.path.basename(str(filename or "").strip())
    if not filename:
        return False

    local_deleted = _delete_local_photo(category="versamenti", store_code=store_code, filename=filename)
    if local_deleted is not None:
        return local_deleted

    if not sb_jwt and not SUPABASE_SERVICE_ROLE_KEY:
        raise SharePointTestError("Sessione non valida: token Supabase mancante (e SERVICE_ROLE_KEY non configurata)")
    access_token, _ = get_graph_access_token(sb_jwt, user_id)
    sharing_url = get_versamenti_photo_sharing_url()
    resolved, rel_path = _photo_target(
        access_token=access_token,
        category="versamenti",
        fallback_sharing_url=sharing_url,
        store_code=store_code,
        filename=filename,
        ensure_folders=False,
    )

    # Risolvi item-id per path
    get_url = f"{MS_GRAPH_BASE}/drives/{resolved.drive_id}/items/{resolved.folder_item_id}:/{rel_path}"
    r = requests.get(get_url, headers=_graph_headers(access_token), timeout=30)
    if r.status_code == 404:
        return False
    if r.status_code != 200:
        raise SharePointTestError(f"Graph GET foto failed: {r.status_code} {r.text}")
    item_id = (r.json() or {}).get("id")
    if not item_id:
        return False

    del_url = f"{MS_GRAPH_BASE}/drives/{resolved.drive_id}/items/{item_id}"
    rr = requests.delete(del_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=60)
    if rr.status_code in (204, 200):
        return True
    if rr.status_code == 404:
        return False
    raise SharePointTestError(f"Delete foto failed: {rr.status_code} {rr.text}")


def generate_azzeramento_photo_filename(*, store_code: str, data_iso: str | None = None) -> str:
    dt_part = ""
    if data_iso:
        dt_part = (str(data_iso).strip().replace("-", "") or "")[:8]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = uuid4().hex[:10]
    store = _safe_segment(store_code)
    if dt_part:
        return f"azzeramento_{store}_{dt_part}_{ts}_{rand}.jpg"
    return f"azzeramento_{store}_{ts}_{rand}.jpg"


def upload_azzeramento_photo(*, sb_jwt: str, user_id: str, store_code: str, file_storage, data_iso: Optional[str] = None) -> str:
    if not user_id:
        raise SharePointTestError("Sessione non valida: utente non autenticato")

    raw = file_storage.read() if hasattr(file_storage, "read") else None
    if not raw:
        raise SharePointTestError("File vuoto")
    if len(raw) > 15 * 1024 * 1024:
        raise SharePointTestError("File troppo grande (max 15MB)")

    try:
        content = _image_to_jpeg_bytes(raw)
    except Exception as e:
        raise SharePointTestError(f"Formato immagine non supportato: {e}")

    filename = generate_azzeramento_photo_filename(store_code=store_code, data_iso=data_iso)
    if _write_local_photo(category="azzeramenti", store_code=store_code, filename=filename, content=content):
        return filename

    if not sb_jwt and not SUPABASE_SERVICE_ROLE_KEY:
        raise SharePointTestError("Sessione non valida: token Supabase mancante (e SERVICE_ROLE_KEY non configurata)")
    access_token, _ = get_graph_access_token(sb_jwt, user_id)
    sharing_url = get_azzeramenti_photo_sharing_url()
    resolved, rel_path = _photo_target(
        access_token=access_token,
        category="azzeramenti",
        fallback_sharing_url=sharing_url,
        store_code=store_code,
        filename=filename,
        ensure_folders=True,
    )
    upload_item_by_path(access_token, resolved, rel_path, content)
    return filename


def download_azzeramento_photo(*, sb_jwt: str, user_id: str, store_code: str, filename: str) -> bytes:
    if not user_id:
        raise SharePointTestError("Sessione non valida: utente non autenticato")
    filename = os.path.basename(str(filename or "").strip())
    if not filename:
        raise SharePointTestError("Filename mancante")

    local_content = _read_local_photo(category="azzeramenti", store_code=store_code, filename=filename)
    if local_content is not None:
        return local_content

    if not sb_jwt and not SUPABASE_SERVICE_ROLE_KEY:
        raise SharePointTestError("Sessione non valida: token Supabase mancante (e SERVICE_ROLE_KEY non configurata)")
    access_token, _ = get_graph_access_token(sb_jwt, user_id)
    sharing_url = get_azzeramenti_photo_sharing_url()
    resolved, rel_path = _photo_target(
        access_token=access_token,
        category="azzeramenti",
        fallback_sharing_url=sharing_url,
        store_code=store_code,
        filename=filename,
        ensure_folders=False,
    )
    return download_item_by_path(access_token, resolved, rel_path)


def delete_azzeramento_photo(*, sb_jwt: str, user_id: str, store_code: str, filename: str) -> bool:
    if not user_id:
        raise SharePointTestError("Sessione non valida: utente non autenticato")
    filename = os.path.basename(str(filename or "").strip())
    if not filename:
        return False

    local_deleted = _delete_local_photo(category="azzeramenti", store_code=store_code, filename=filename)
    if local_deleted is not None:
        return local_deleted

    if not sb_jwt and not SUPABASE_SERVICE_ROLE_KEY:
        raise SharePointTestError("Sessione non valida: token Supabase mancante (e SERVICE_ROLE_KEY non configurata)")
    access_token, _ = get_graph_access_token(sb_jwt, user_id)
    sharing_url = get_azzeramenti_photo_sharing_url()
    resolved, rel_path = _photo_target(
        access_token=access_token,
        category="azzeramenti",
        fallback_sharing_url=sharing_url,
        store_code=store_code,
        filename=filename,
        ensure_folders=False,
    )

    get_url = f"{MS_GRAPH_BASE}/drives/{resolved.drive_id}/items/{resolved.folder_item_id}:/{rel_path}"
    r = requests.get(get_url, headers=_graph_headers(access_token), timeout=30)
    if r.status_code == 404:
        return False
    if r.status_code != 200:
        raise SharePointTestError(f"Graph GET foto failed: {r.status_code} {r.text}")
    item_id = (r.json() or {}).get("id")
    if not item_id:
        return False

    del_url = f"{MS_GRAPH_BASE}/drives/{resolved.drive_id}/items/{item_id}"
    rr = requests.delete(del_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=60)
    if rr.status_code in (204, 200):
        return True
    if rr.status_code == 404:
        return False
    raise SharePointTestError(f"Delete foto failed: {rr.status_code} {rr.text}")
