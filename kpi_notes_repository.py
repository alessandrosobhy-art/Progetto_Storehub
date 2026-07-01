from __future__ import annotations

import os
from datetime import date as _date, datetime as _dt

try:
    import pyodbc  # type: ignore
except Exception:  # pragma: no cover
    pyodbc = None  # type: ignore


def _sql_candidate_drivers(preferred: str | None) -> list[str]:
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
        # In questo caso app_db.get_connection() è già configurata per APP_STOREHUB
        from app_db import get_connection  # type: ignore

        return get_connection(None)

    if pyodbc is None:
        raise RuntimeError("pyodbc non disponibile: impossibile connettersi a SQL Server per KPI notes.")

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


def _as_iso(d: _dt | None) -> str | None:
    if not d:
        return None
    try:
        return d.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(d)


def ensure_kpi_period_notes_schema() -> None:
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            IF OBJECT_ID('dbo.KPI_PERIOD_NOTES', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.KPI_PERIOD_NOTES (
                    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
                    store_code NVARCHAR(50) NOT NULL,
                    period_type NVARCHAR(1) NOT NULL,
                    period_start DATE NOT NULL,
                    note_text NVARCHAR(MAX) NULL,
                    created_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
                    updated_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME()
                );
                CREATE UNIQUE INDEX UX_KPI_PERIOD_NOTES_store_period
                    ON dbo.KPI_PERIOD_NOTES(store_code, period_type, period_start);
            END
            """
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_note(store_code: str, period_type: str, period_start: _date) -> dict[str, object] | None:
    """Ritorna la nota per un periodo.

    period_type: 'W' (weekly) o 'M' (monthly)
    period_start: data inizio periodo (lunedì per weekly, primo del mese per monthly)
    """

    sc = (store_code or "").strip()
    pt = (period_type or "W").strip().upper()[:1]
    if pt not in ("W", "M"):
        pt = "W"

    ensure_kpi_period_notes_schema()
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT store_code, period_type, period_start, note_text, created_at, updated_at
            FROM dbo.KPI_PERIOD_NOTES
            WHERE store_code = ? AND period_type = ? AND period_start = ?
            """,
            (sc, pt, period_start),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "store_code": str(row.store_code),
            "period_type": str(row.period_type),
            "period_start": str(row.period_start),
            "text": str(row.note_text or ""),
            "created_at": _as_iso(getattr(row, "created_at", None)),
            "updated_at": _as_iso(getattr(row, "updated_at", None)),
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def upsert_note(store_code: str, period_type: str, period_start: _date, text: str) -> dict[str, object]:
    sc = (store_code or "").strip()
    pt = (period_type or "W").strip().upper()[:1]
    if pt not in ("W", "M"):
        pt = "W"

    note_text = (text or "").strip()

    ensure_kpi_period_notes_schema()
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            MERGE dbo.KPI_PERIOD_NOTES WITH (HOLDLOCK) AS tgt
            USING (SELECT ? AS store_code, ? AS period_type, ? AS period_start) AS src
              ON tgt.store_code = src.store_code
             AND tgt.period_type = src.period_type
             AND tgt.period_start = src.period_start
            WHEN MATCHED THEN
              UPDATE SET note_text = ?, updated_at = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
              INSERT (store_code, period_type, period_start, note_text)
              VALUES (src.store_code, src.period_type, src.period_start, ?);
            """,
            (sc, pt, period_start, note_text, note_text),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # return current row
    return get_note(sc, pt, period_start) or {
        "store_code": sc,
        "period_type": pt,
        "period_start": str(period_start),
        "text": note_text,
        "created_at": None,
        "updated_at": None,
    }
