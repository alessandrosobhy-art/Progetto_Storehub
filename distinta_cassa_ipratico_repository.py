from __future__ import annotations

from app_logging import log_swallowed
import json
from typing import Any, Optional

from app_db import get_backend, get_connection, get_connection_sqlserver_database, get_storehub_database_name


TABLE_NAME = "DISTINTA_CASSA_IPRATICO"


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def _ensure_sql_backend() -> None:
    if get_backend() != "sqlserver":
        raise RuntimeError("Lo snapshot iPratico Distinta cassa richiede SQL Server.")


def _loads_json(raw: Any) -> Any:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def ensure_distinta_cassa_ipratico_schema() -> None:
    # Idempotent DDL/schema checks should run in autocommit mode; otherwise
    # pooled pyodbc connections can stay sleeping with an open transaction.
    conn = _conn(read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
IF OBJECT_ID('dbo.{TABLE_NAME}', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.{TABLE_NAME} (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    site NVARCHAR(50) NOT NULL,
    data_iso DATE NOT NULL,
    vendite_lorde DECIMAL(18,4) NOT NULL DEFAULT 0,
    annullati DECIMAL(18,4) NOT NULL DEFAULT 0,
    scontrini INT NOT NULL DEFAULT 0,
    pos DECIMAL(18,4) NOT NULL DEFAULT 0,
    contanti DECIMAL(18,4) NOT NULL DEFAULT 0,
    ticket DECIMAL(18,4) NOT NULL DEFAULT 0,
    satispay DECIMAL(18,4) NOT NULL DEFAULT 0,
    imported_json NVARCHAR(MAX) NULL,
    mapped_json NVARCHAR(MAX) NULL,
    save_origin NVARCHAR(30) NULL,
    imported_manually BIT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_{TABLE_NAME}_site_day ON dbo.{TABLE_NAME}(site, data_iso);
END
"""
        )
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('distinta_cassa_ipratico_repository:67')


def get_distinta_cassa_ipratico_snapshot(*, store_code: str, data_iso: str) -> Optional[dict[str, Any]]:
    ensure_distinta_cassa_ipratico_schema()
    conn = _conn(read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT TOP 1
                row_uuid,
                site,
                data_iso,
                vendite_lorde,
                annullati,
                scontrini,
                pos,
                contanti,
                ticket,
                satispay,
                imported_json,
                mapped_json,
                save_origin,
                imported_manually,
                created_at,
                updated_at
            FROM dbo.{TABLE_NAME}
            WHERE LTRIM(RTRIM(CAST(site AS NVARCHAR(50)))) = ?
              AND data_iso = ?
            ORDER BY updated_at DESC, created_at DESC
            """,
            (str(store_code).strip(), str(data_iso).strip()),
        )
        row = cur.fetchone()
        if not row:
            return None

        imported = _loads_json(row[10])
        mapped = _loads_json(row[11])
        chiusura = {
            "vendite_lorde": float(row[3] or 0.0),
            "annullati": float(row[4] or 0.0),
            "scontrini": int(row[5] or 0),
            "pos": float(row[6] or 0.0),
            "contanti": float(row[7] or 0.0),
            "ticket": float(row[8] or 0.0),
            "satispay": float(row[9] or 0.0),
        }
        return {
            "row_uuid": str(row[0] or "").strip(),
            "site": str(row[1] or "").strip(),
            "data_iso": str(row[2] or "").strip(),
            "chiusura": chiusura,
            "imported": imported if isinstance(imported, dict) else {},
            "mapped": mapped if isinstance(mapped, dict) else {},
            "save_origin": str(row[12] or "").strip(),
            "imported_manually": bool(row[13]),
            "created_at": row[14],
            "updated_at": row[15],
        }
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('distinta_cassa_ipratico_repository:132')


def upsert_distinta_cassa_ipratico_snapshot(
    *,
    store_code: str,
    data_iso: str,
    imported_payload: dict[str, Any],
    mapped_payload: dict[str, Any],
    save_origin: str,
    imported_manually: bool,
) -> None:
    ensure_distinta_cassa_ipratico_schema()
    site = str(store_code).strip()
    day = str(data_iso).strip()
    if not site or not day:
        raise RuntimeError("store_code e data_iso sono obbligatori.")

    chiusura = dict((mapped_payload or {}).get("chiusura") or {})
    vendite_lorde = float(chiusura.get("vendite_lorde") or 0.0)
    annullati = float(chiusura.get("annullati") or 0.0)
    scontrini = int(chiusura.get("scontrini") or 0)
    pos = float(chiusura.get("pos") or 0.0)
    contanti = float(chiusura.get("contanti") or 0.0)
    ticket = float(chiusura.get("ticket") or 0.0)
    satispay = float((mapped_payload or {}).get("satispay") or 0.0)

    imported_json = json.dumps(imported_payload or {}, ensure_ascii=False)
    mapped_json = json.dumps(mapped_payload or {}, ensure_ascii=False)

    conn = _conn(read_only=False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            UPDATE dbo.{TABLE_NAME}
               SET vendite_lorde = ?,
                   annullati = ?,
                   scontrini = ?,
                   pos = ?,
                   contanti = ?,
                   ticket = ?,
                   satispay = ?,
                   imported_json = ?,
                   mapped_json = ?,
                   save_origin = ?,
                   imported_manually = ?,
                   updated_at = SYSUTCDATETIME()
             WHERE LTRIM(RTRIM(CAST(site AS NVARCHAR(50)))) = ?
               AND data_iso = ?
            """,
            (
                vendite_lorde,
                annullati,
                scontrini,
                pos,
                contanti,
                ticket,
                satispay,
                imported_json,
                mapped_json,
                str(save_origin or "").strip()[:30] or "auto_on_save",
                1 if imported_manually else 0,
                site,
                day,
            ),
        )
        if int(cur.rowcount or 0) == 0:
            cur.execute(
                f"""
                INSERT INTO dbo.{TABLE_NAME} (
                    site,
                    data_iso,
                    vendite_lorde,
                    annullati,
                    scontrini,
                    pos,
                    contanti,
                    ticket,
                    satispay,
                    imported_json,
                    mapped_json,
                    save_origin,
                    imported_manually,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME(), SYSUTCDATETIME())
                """,
                (
                    site,
                    day,
                    vendite_lorde,
                    annullati,
                    scontrini,
                    pos,
                    contanti,
                    ticket,
                    satispay,
                    imported_json,
                    mapped_json,
                    str(save_origin or "").strip()[:30] or "auto_on_save",
                    1 if imported_manually else 0,
                ),
            )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('distinta_cassa_ipratico_repository:242')


def sum_distinta_cassa_ipratico_contanti_month_multi(store_codes: list[str], *, year: int, month: int) -> dict[str, float]:
    ensure_distinta_cassa_ipratico_schema()
    codes = [str(c or "").strip() for c in (store_codes or []) if str(c or "").strip()]
    if not codes:
        return {}

    if year < 2000 or not (1 <= int(month) <= 12):
        return {c: 0.0 for c in codes}

    start = f"{int(year):04d}-{int(month):02d}-01"
    if int(month) == 12:
        end = f"{int(year) + 1:04d}-01-01"
    else:
        end = f"{int(year):04d}-{int(month) + 1:02d}-01"

    out = {c: 0.0 for c in codes}
    conn = _conn(read_only=True)
    try:
        cur = conn.cursor()
        ph = ",".join(["?"] * len(codes))
        cur.execute(
            f"""
            SELECT
                LTRIM(RTRIM(CAST(site AS NVARCHAR(50)))) AS site_code,
                SUM(COALESCE(contanti, 0)) AS s
            FROM dbo.{TABLE_NAME}
            WHERE LTRIM(RTRIM(CAST(site AS NVARCHAR(50)))) IN ({ph})
              AND data_iso >= ?
              AND data_iso < ?
            GROUP BY LTRIM(RTRIM(CAST(site AS NVARCHAR(50))))
            """,
            (*codes, start, end),
        )
        for site_code, total in (cur.fetchall() or []):
            code = str(site_code or "").strip()
            if not code:
                continue
            out[code] = float(total or 0.0)
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('distinta_cassa_ipratico_repository:288')
