import os
import sys
import tempfile
from typing import Callable, Optional

try:
    import pyodbc
except ImportError:  # pragma: no cover
    pyodbc = None

# In alcuni scenari (ripetute connessioni ravvicinate a file Access) il pooling di pyodbc
# può lasciare handle in uno stato incoerente e generare HY000 "The driver did not supply an error".
# Disattiviamo il pooling per rendere le connessioni più deterministiche.
try:  # pragma: no cover
    if pyodbc is not None:
        pyodbc.pooling = False
except Exception:
    pass


def _in_flask_request() -> bool:
    """True se siamo in un contesto request Flask."""
    try:
        from flask import has_request_context

        return bool(has_request_context())
    except Exception:  # pragma: no cover
        return False


def _get_session_value(key: str, default=None):
    if not _in_flask_request():
        return default
    try:
        from flask import session

        return session.get(key, default)
    except Exception:  # pragma: no cover
        return default


def _db_mode() -> str:
    mode = str(_get_session_value("db_mode", "") or "").strip().lower()
    if mode in ("cloud", "locale", "local"):
        return "cloud" if mode == "cloud" else "local"
    # Se non scelto, lasciamo local come fallback tecnico.
    return "local"


class AccessConnectionWrapper:
    """Wrapper per connessione pyodbc che supporta upload su SharePoint in close()."""

    def __init__(
        self,
        conn,
        *,
        on_close_upload: Optional[Callable[[], None]] = None,
        cleanup_path: Optional[str] = None,
    ):
        self._conn = conn
        self._dirty = False
        self._on_close_upload = on_close_upload
        self._cleanup_path = cleanup_path

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        self._dirty = True
        return self._conn.commit()

    def rollback(self):
        try:
            return self._conn.rollback()
        finally:
            # rollback non marca dirty
            pass

    def close(self):
        close_err = None
        upload_err = None
        try:
            try:
                self._conn.close()
            except Exception as e:  # pragma: no cover
                close_err = e

            # Upload solo se commit() è stato chiamato.
            if self._dirty and self._on_close_upload:
                try:
                    self._on_close_upload()
                except Exception as e:
                    upload_err = e
        finally:
            if self._cleanup_path:
                try:
                    os.remove(self._cleanup_path)
                except Exception:
                    pass

        # Evitiamo di sovrascrivere eccezioni già in corso (es. in un finally).
        # Se non ci sono eccezioni attive, propaghiamo l'errore di upload/close.
        exc_type, _, _ = sys.exc_info()
        if exc_type is None:
            if upload_err:
                raise upload_err
            if close_err:
                raise close_err

    def __getattr__(self, item):
        # delegate qualunque altro metodo/attributo alla connessione reale
        return getattr(self._conn, item)


def get_access_base_path() -> str:
    """Percorso base 'primario' (locale) in cui si trovano le cartelle degli store."""
    base = os.getenv("ACCESS_BASE_PATH")
    if not base:
        base = r"C:\FILE\F & B INVESTMENT HOLDING srl\F & B - OPS\CONDIVISA\DatiFile"
    return os.path.normpath(base)


def get_access_base_path_candidates() -> list[str]:
    """Restituisce i percorsi base locali da provare in sequenza (primario + fallback).

    Il fallback viene usato automaticamente senza avvisi. Se entrambi falliscono,
    la connessione locale verrà considerata non disponibile.
    """
    primary = get_access_base_path()
    fallback = os.getenv("ACCESS_BASE_PATH_FALLBACK")
    if not fallback:
        fallback = r"C:\File\F & B INVESTMENT HOLDING srl\F & B - CONDIVISA\DatiFile"
    fallback = os.path.normpath(fallback)

    out = []
    for p in (primary, fallback):
        if p and p not in out:
            out.append(p)
    return out


def get_access_password() -> str:
    # Restituisce la password del database Access.
    # Prima prova a leggere la variabile d'ambiente ACCESS_DB_PASSWORD,
    # altrimenti usa la password di default attuale.
    pwd = os.getenv("ACCESS_DB_PASSWORD")
    if not pwd:
        pwd = "1BTeam1"
    return pwd


def _build_db_path(base: str, store_code: str, filename: str = "Datifile.mdb") -> str:
    base = os.path.normpath(base or "")
    store_code = str(store_code).strip()
    filename = str(filename).strip()
    return os.path.normpath(os.path.join(base, store_code, filename))


def build_db_path(store_code: str) -> str:
    """Percorso completo del file Datifile.mdb usando il base path primario."""
    return _build_db_path(get_access_base_path(), store_code, "Datifile.mdb")


def _local_db_candidates(store_code: str) -> list[str]:
    """Elenco percorsi locali da provare (primario + fallback)."""
    bases = get_access_base_path_candidates()
    # Su filesystem case-sensitive (es. Linux) il nome file potrebbe differire.
    filenames = ["Datifile.mdb", "datifile.mdb"]
    out: list[str] = []
    for b in bases:
        for fn in filenames:
            p = _build_db_path(b, store_code, fn)
            if p not in out:
                out.append(p)
    return out


def _resolve_local_db_path(store_code: str) -> str:
    for p in _local_db_candidates(store_code):
        if os.path.exists(p):
            return p
    raise FileNotFoundError("Non è stato possibile connettersi al link locale.")



def _cloud_relative_path(store_code: str) -> str:
    """Percorso relativo del DB dentro la cartella SharePoint condivisa."""
    base_subpath = str(_get_session_value("sp_base_subpath", "") or "").strip() or os.getenv(
        "SP_BASE_SUBPATH", "DatiFile"
    )
    filename = str(_get_session_value("sp_db_filename", "") or "").strip() or os.getenv(
        "SP_DB_FILENAME", "Datifile.mdb"
    )
    base_subpath = base_subpath.strip("/")
    filename = filename.strip("/")
    return f"{base_subpath}/{str(store_code).strip()}/{filename}".lstrip("/")


def _cloud_sharing_url() -> str:
    return (
        str(_get_session_value("sp_sharing_url", "") or "").strip()
        or os.getenv("SP_SHARING_URL")
        or os.getenv("SP_TEST_SHARING_URL")
        or ""
    )


def _effective_db_label(store_code: str) -> str:
    if _db_mode() == "cloud":
        rel = _cloud_relative_path(store_code)
        return f"SharePoint::{rel}"
    try:
        return _resolve_local_db_path(store_code)
    except Exception:
        # fallback: mostra comunque il path primario
        return build_db_path(store_code)


def _cloud_get_graph_token() -> str:
    if not _in_flask_request():
        raise RuntimeError("Connessione cloud richiede un contesto Flask (sessione).")
    from flask import session

    sb_jwt = session.get("sb_token")
    uid = session.get("uid")
    if not uid:
        raise RuntimeError("Sessione Supabase non valida: esegui login e riprova.")

    # Su alcuni browser (soprattutto mobile/tablet) il cookie di sessione può
    # perdere l'access_token Supabase (dimensione cookie / policy). In questo
    # caso possiamo comunque recuperare i token Microsoft salvati su Supabase
    # usando la SERVICE_ROLE_KEY (se configurata).
    if not sb_jwt and not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        raise RuntimeError("Sessione Supabase non valida: token mancante (e SERVICE_ROLE_KEY non configurata).")
    # Import lazy per evitare dipendenze non necessarie in modalità locale.
    from sharepoint_test_repository import get_graph_access_token

    token, _refreshed = get_graph_access_token(sb_jwt, uid)
    return token


def _cloud_get_resolved_share(access_token: str, sharing_url: str):
    """Risolve (e cache-a in sessione) il link di condivisione."""
    from sharepoint_test_repository import ResolvedShare, resolve_sharing_url

    if _in_flask_request():
        from flask import session

        cached_for = session.get("sp_resolved_for")
        drive_id = session.get("sp_resolved_drive_id")
        folder_item_id = session.get("sp_resolved_folder_item_id")
        folder_name = session.get("sp_resolved_folder_name")
        web_url = session.get("sp_resolved_web_url")

        if cached_for == sharing_url and drive_id and folder_item_id:
            return ResolvedShare(
                drive_id=str(drive_id),
                folder_item_id=str(folder_item_id),
                folder_name=str(folder_name or ""),
                web_url=str(web_url or ""),
            )

        resolved = resolve_sharing_url(access_token, sharing_url)
        session["sp_resolved_for"] = sharing_url
        session["sp_resolved_drive_id"] = resolved.drive_id
        session["sp_resolved_folder_item_id"] = resolved.folder_item_id
        session["sp_resolved_folder_name"] = resolved.folder_name
        session["sp_resolved_web_url"] = resolved.web_url
        return resolved

    return resolve_sharing_url(access_token, sharing_url)


def _cloud_download_db_bytes(access_token: str, store_code: str) -> bytes:
    sharing_url = _cloud_sharing_url()
    if not sharing_url:
        raise RuntimeError(
            "SP_SHARING_URL mancante: imposta la variabile d'ambiente oppure seleziona la connessione cloud."
        )
    relative_path = _cloud_relative_path(store_code)
    from sharepoint_test_repository import download_item_by_path

    resolved = _cloud_get_resolved_share(access_token, sharing_url)
    return download_item_by_path(access_token, resolved, relative_path)


# --------------------
# Cloud download cache (read-only)
# --------------------

_CLOUD_DB_CACHE: dict[str, tuple[float, bytes]] = {}


def _cloud_download_db_bytes_cached(access_token: str, store_code: str) -> bytes:
    """Scarica l'.mdb da SharePoint con una cache TTL.

    Usata SOLO in modalità read_only, per evitare download ripetuti nello stesso
    request (e in generale ravvicinati) durante aggregazioni/dashboards.
    """
    import time

    key = str(store_code or "").strip()
    if not key:
        return _cloud_download_db_bytes(access_token, store_code)

    try:
        ttl = int(os.getenv("ACCESS_CLOUD_CACHE_TTL_SEC") or "90")
    except Exception:
        ttl = 90

    now = time.time()
    hit = _CLOUD_DB_CACHE.get(key)
    if hit:
        ts, data = hit
        if (now - ts) <= ttl:
            return data

    data = _cloud_download_db_bytes(access_token, store_code)
    _CLOUD_DB_CACHE[key] = (now, data)

    # evita crescita illimitata
    try:
        if len(_CLOUD_DB_CACHE) > 250:
            # drop 50 più vecchi
            oldest = sorted(_CLOUD_DB_CACHE.items(), key=lambda kv: kv[1][0])[:50]
            for k, _v in oldest:
                _CLOUD_DB_CACHE.pop(k, None)
    except Exception:
        pass

    return data


def _cloud_upload_db_bytes(access_token: str, store_code: str, data: bytes) -> None:
    sharing_url = _cloud_sharing_url()
    if not sharing_url:
        raise RuntimeError("SP_SHARING_URL mancante: impossibile effettuare upload.")
    relative_path = _cloud_relative_path(store_code)
    from sharepoint_test_repository import upload_item_by_path

    resolved = _cloud_get_resolved_share(access_token, sharing_url)
    upload_item_by_path(access_token, resolved, relative_path, data)


def _pick_access_driver() -> str:
    """Ritorna il nome driver ODBC migliore disponibile per Access."""
    if pyodbc is None:
        return "Microsoft Access Driver (*.mdb, *.accdb)"
    try:
        drivers = [d for d in pyodbc.drivers() if d]
    except Exception:
        drivers = []

    preferred = []
    for d in drivers:
        dl = d.lower()
        if "access" in dl and "driver" in dl:
            preferred.append(d)

    # di solito questo è quello giusto su Windows
    for d in preferred:
        if "*.mdb" in d.lower() and "*.accdb" in d.lower():
            return d
    return preferred[0] if preferred else "Microsoft Access Driver (*.mdb, *.accdb)"


def _connect_with_retry(conn_str_variants, attempts: int = 4, connect_timeout: int = 5, query_timeout: int = 10):
    """Tenta la connessione provando varianti (es. con/senza PWD) e retry/backoff."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        for conn_str in conn_str_variants:
            try:
                conn = pyodbc.connect(conn_str, timeout=connect_timeout)
                try:
                    conn.timeout = query_timeout
                except Exception:
                    pass
                return conn
            except Exception as e:
                last_exc = e
        # backoff leggero
        try:
            import time

            time.sleep(min(0.25 * attempt, 1.0))
        except Exception:
            pass
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Impossibile aprire la connessione Access")


def get_connection(store_code: str, read_only: bool = False):
    """Apre una connessione ODBC verso il database Access dello store indicato.

    Note:
    - read_only è usato per chiamate di sola lettura; non cambia il file, ma ci permette
      di mantenere la firma compatibile e di applicare strategie meno invasive.
    """
    if pyodbc is None:
        raise RuntimeError(
            "pyodbc non è installato. Esegui 'pip install pyodbc' nell'ambiente "
            "in cui fai girare l'app per utilizzare la parte Magazzino."
        )

    password = get_access_password()
    driver = _pick_access_driver()

    # -------------------- CLOUD (SharePoint) --------------------
    if _db_mode() == "cloud":
        access_token = _cloud_get_graph_token()

        # In cloud mode each connection requires downloading the whole .mdb.
        # For read-only operations (dashboards/aggregations) this could happen
        # many times in the same request and create timeouts.
        # We cache downloads for a short TTL only when read_only=True.
        mdb_bytes = _cloud_download_db_bytes_cached(access_token, store_code) if read_only else _cloud_download_db_bytes(access_token, store_code)

        fd, tmp_path = tempfile.mkstemp(suffix=".mdb")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(mdb_bytes)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            raise

        base = f"Driver={{{driver}}};DBQ={tmp_path};"
        conn_str_variants = [
            base + f"PWD={password};" if password else base,
            base,  # fallback senza password
        ]

        conn = _connect_with_retry(conn_str_variants)

        def _upload_after_close():
            # viene chiamata solo se la connessione è stata marcata dirty (commit)
            with open(tmp_path, "rb") as rf:
                out_bytes = rf.read()
            _cloud_upload_db_bytes(access_token, store_code, out_bytes)

        return AccessConnectionWrapper(conn, on_close_upload=_upload_after_close, cleanup_path=tmp_path)

    # -------------------- LOCALE (file system) --------------------
    db_path = _resolve_local_db_path(store_code)
    base = f"Driver={{{driver}}};DBQ={db_path};"
    conn_str_variants = [
        base + f"PWD={password};" if password else base,
        base,
    ]
    return _connect_with_retry(conn_str_variants)


def test_connection(store_code: str, max_tables: int = 10) -> dict:
    # Tenta una connessione al database Access dello store e restituisce
    # alcune informazioni utili per il debug.
    # Ritorna un dizionario con chiavi:
    # - ok: True/False
    # - db_path: percorso usato
    # - error: messaggio errore (se ok=False)
    # - tables: elenco di alcune tabelle trovate (se ok=True)
    info = {
        "ok": False,
        "db_path": _effective_db_label(store_code),
        "error": None,
        "tables": [],
    }

    try:
        conn = get_connection(store_code)
    except Exception as e:  # pragma: no cover
        info["error"] = str(e)
        return info

    try:
        cursor = conn.cursor()
        tables = []
        # Elenca le tabelle "normali", escludendo le MSys*
        for row in cursor.tables(tableType='TABLE'):
            name = getattr(row, "table_name", None) or (len(row) > 2 and row[2]) or None
            if not name:
                continue
            if str(name).startswith("MSys"):
                continue
            tables.append(str(name))
            if len(tables) >= max_tables:
                break
        info["tables"] = tables
        info["ok"] = True
        return info
    finally:
        try:
            conn.close()
        except Exception:
            pass
