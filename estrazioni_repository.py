from __future__ import annotations

import csv
import io
import os
from typing import Any, Dict, List, Optional, Tuple

try:
    import pyodbc  # type: ignore
except Exception:  # pragma: no cover
    pyodbc = None  # type: ignore

from app_db import get_connection


TABLE_FULL = "dbo.SCHEDULING_SITE_FABBISOGNO"


def _sql_candidate_drivers(preferred: Optional[str]) -> list[str]:
    out: list[str] = []
    if preferred:
        out.append(preferred)
    out.extend(
        [
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 17 for SQL Server",
            "ODBC Driver 13 for SQL Server",
            "ODBC Driver 11 for SQL Server",
            "SQL Server",
        ]
    )
    seen = set()
    uniq: list[str] = []
    for d in out:
        k = (d or "").strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(d)
    return uniq


def _get_sql_conn_app_storehub():
    """Connessione SQL Server su APP_STOREHUB.

    - Se DB_BACKEND=sqlserver usa app_db.get_connection()
    - Altrimenti prova una connessione diretta via pyodbc con env SQLSERVER_*.
    """
    try:
        from app_db import get_backend as _get_backend  # type: ignore
    except Exception:  # pragma: no cover
        _get_backend = lambda: "access"  # type: ignore

    if str(_get_backend() or "").lower() == "sqlserver":
        return get_connection(None)

    if pyodbc is None:
        raise RuntimeError("pyodbc non disponibile: impossibile connettersi a SQL Server per Estrazioni.")

    server = os.getenv("SQLSERVER_SERVER") or os.getenv("SQLSERVER_HOST") or r"10.24.1.1\\SQLEXPRESS"
    database = os.getenv("SQLSERVER_DATABASE") or os.getenv("SQLSERVER_DB") or "APP_STOREHUB"
    user = os.getenv("SQLSERVER_USER") or "file"
    password = os.getenv("SQLSERVER_PASSWORD") or ""
    preferred_driver = os.getenv("SQLSERVER_DRIVER")
    encrypt = (os.getenv("SQLSERVER_ENCRYPT") or "no").strip().lower()
    trust_cert = (os.getenv("SQLSERVER_TRUST_CERT") or os.getenv("SQLSERVER_TRUST_SERVER_CERT") or "yes").strip().lower()
    timeout = int(os.getenv("SQLSERVER_TIMEOUT") or "30")

    enc_val = "yes" if encrypt in ("1", "true", "yes", "y") else "no"
    tsc_val = "yes" if trust_cert in ("1", "true", "yes", "y") else "no"

    last_im002 = None
    for driver in _sql_candidate_drivers(preferred_driver):
        conn_str = ";".join(
            [
                f"DRIVER={{{driver}}}",
                f"SERVER={server}",
                f"DATABASE={database}",
                f"UID={user}",
                f"PWD={password}",
                f"Encrypt={enc_val}",
                f"TrustServerCertificate={tsc_val}",
            ]
        ) + ";"
        try:
            return pyodbc.connect(conn_str, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            if getattr(e, "args", None) and len(e.args) > 0 and str(e.args[0]) == "IM002":
                last_im002 = e
                continue
            raise

    raise RuntimeError(
        "ODBC driver per SQL Server non trovato (IM002). "
        "Installa 'ODBC Driver 18 for SQL Server' o 'ODBC Driver 17 for SQL Server', "
        "oppure imposta SQLSERVER_DRIVER nel .env."
    ) from last_im002


def ensure_fabbisogno_table() -> None:
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            IF OBJECT_ID('{TABLE_FULL}', 'U') IS NULL
            BEGIN
                CREATE TABLE {TABLE_FULL} (
                    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWSEQUENTIALID() PRIMARY KEY,
                    site VARCHAR(16) NOT NULL,
                    codice_fabbisogno VARCHAR(20) NOT NULL,
                    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    CONSTRAINT UQ_SCHED_SITE UNIQUE (site)
                );
            END
            """
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_fabbisogno_sites() -> List[Dict[str, Any]]:
    ensure_fabbisogno_table()
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT site, codice_fabbisogno, updated_at FROM {TABLE_FULL} ORDER BY site")
        rows = []
        for r in cur.fetchall() or []:
            rows.append(
                {
                    "site": str(getattr(r, "site", None) or r[0] or "").strip(),
                    "codice_fabbisogno": str(getattr(r, "codice_fabbisogno", None) or r[1] or "").strip(),
                    "updated_at": getattr(r, "updated_at", None) or r[2],
                }
            )
        return rows
    finally:
        try:
            conn.close()
        except Exception:
            pass


def upsert_fabbisogno_site(site: str, codice_fabbisogno: str) -> None:
    ensure_fabbisogno_table()
    s = (site or "").strip()
    c = (codice_fabbisogno or "").strip()
    if not s or not c:
        raise ValueError("Site e codice fabbisogno sono obbligatori")
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            MERGE {TABLE_FULL} AS tgt
            USING (SELECT ? AS site, ? AS codice_fabbisogno) AS src
            ON (tgt.site = src.site)
            WHEN MATCHED THEN
                UPDATE SET codice_fabbisogno = src.codice_fabbisogno, updated_at = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (site, codice_fabbisogno) VALUES (src.site, src.codice_fabbisogno);
            """,
            (s, c),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_fabbisogno_site(site: str) -> None:
    ensure_fabbisogno_table()
    s = (site or "").strip()
    if not s:
        return
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {TABLE_FULL} WHERE site=?", (s,))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def import_fabbisogno_csv(text: str) -> Tuple[int, List[str]]:
    """Import massivo: ritorna (righe_ok, warnings)."""
    ensure_fabbisogno_table()
    raw = text or ""
    warnings: List[str] = []
    if not raw.strip():
        return 0, ["CSV vuoto"]

    try:
        dialect = csv.Sniffer().sniff(raw[:2000])
    except Exception:
        class _D:
            delimiter = ";"
        dialect = _D()

    reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
    if not reader.fieldnames:
        return 0, ["Header CSV non trovato"]

    def norm(h: str) -> str:
        return (h or "").strip().lower().replace(" ", "").replace("_", "")

    hm = {norm(h): h for h in reader.fieldnames}

    def pick(row: dict, *keys: str) -> str:
        for k in keys:
            kk = hm.get(norm(k))
            if kk and kk in row:
                return str(row.get(kk) or "").strip()
        return ""

    ok = 0
    line = 1
    for row in reader:
        line += 1
        site = pick(row, "site", "store", "store_code", "storecode")
        code = pick(row, "codicefabbisogno", "codice_fabbisogno", "fabbisogno", "codice")
        if not site or not code:
            warnings.append(f"Riga {line}: site/codice mancanti")
            continue
        try:
            upsert_fabbisogno_site(site, code)
            ok += 1
        except Exception as e:
            warnings.append(f"Riga {line}: errore upsert ({e})")
    return ok, warnings


def get_fabbisogno_code_for_site(site: str) -> Optional[str]:
    """Ritorna il codice fabbisogno per site, se presente."""
    ensure_fabbisogno_table()
    s = (site or "").strip()
    if not s:
        return None
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT codice_fabbisogno FROM {TABLE_FULL} WHERE site=?", (s,))
        r = cur.fetchone()
        if not r:
            return None
        return str(getattr(r, "codice_fabbisogno", None) or r[0] or "").strip() or None
    finally:
        try:
            conn.close()
        except Exception:
            pass
