from __future__ import annotations

from app_logging import log_swallowed
from datetime import date, datetime
from typing import Any, Dict, List

from app_db import get_connection_sqlserver_database, get_storehub_database_name


TABLE_NAME = "DISTINTA_CASSA_CONVALIDE"


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def _ensure_table() -> None:
    conn = _conn(read_only=False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            IF OBJECT_ID('dbo.{TABLE_NAME}', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.{TABLE_NAME} (
                    id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
                    site NVARCHAR(50) NOT NULL,
                    dal DATE NOT NULL,
                    al DATE NOT NULL,
                    total_diff DECIMAL(18,2) NOT NULL CONSTRAINT DF_{TABLE_NAME}_TOTAL_DIFF DEFAULT 0,
                    created_by NVARCHAR(255) NULL,
                    created_name NVARCHAR(255) NULL,
                    created_role NVARCHAR(50) NULL,
                    created_at DATETIME2(0) NOT NULL DEFAULT SYSDATETIME()
                );
                CREATE INDEX IX_{TABLE_NAME}_SITE_DATES ON dbo.{TABLE_NAME}(site, dal, al);
            END
            """
        )
        cur.execute(
            f"""
            IF COL_LENGTH('dbo.{TABLE_NAME}', 'total_diff') IS NULL
                ALTER TABLE dbo.{TABLE_NAME} ADD total_diff DECIMAL(18,2) NOT NULL CONSTRAINT DF_{TABLE_NAME}_TOTAL_DIFF_ALTER DEFAULT 0;
            IF COL_LENGTH('dbo.{TABLE_NAME}', 'created_name') IS NULL
                ALTER TABLE dbo.{TABLE_NAME} ADD created_name NVARCHAR(255) NULL;
            """
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('rendiconto_convalide_repository:53')


def _parse_iso(v: str) -> date:
    return datetime.strptime((v or "").strip(), "%Y-%m-%d").date()


def _row_to_dict(row) -> Dict[str, Any]:
    dal = row[2]
    al = row[3]
    if isinstance(dal, datetime):
        dal = dal.date()
    if isinstance(al, datetime):
        al = al.date()
    return {
        "id": int(row[0]),
        "site": str(row[1] or "").strip(),
        "dal_iso": dal.isoformat() if isinstance(dal, date) else str(dal or "").strip(),
        "al_iso": al.isoformat() if isinstance(al, date) else str(al or "").strip(),
        "total_diff": float(row[4] or 0.0),
        "created_by": str(row[5] or "").strip(),
        "created_name": str(row[6] or "").strip(),
        "created_role": str(row[7] or "").strip(),
        "created_at": row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8] or "").strip(),
    }


def list_convalide_overlapping(*, store_code: str, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
    _ensure_table()
    d1 = _parse_iso(start_iso)
    d2 = _parse_iso(end_iso)
    if d2 < d1:
        d1, d2 = d2, d1

    conn = _conn(read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, site, dal, al, total_diff, created_by, created_name, created_role, created_at
            FROM dbo.{TABLE_NAME}
            WHERE LTRIM(RTRIM(site)) = ?
              AND dal <= ?
              AND al >= ?
            ORDER BY dal, al
            """,
            [str(store_code).strip(), d2, d1],
        )
        return [_row_to_dict(r) for r in (cur.fetchall() or [])]
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('rendiconto_convalide_repository:106')


def list_convalida_days_month(*, store_code: str, year: int, month: int) -> Dict[str, Dict[str, Any]]:
    if year < 2000 or not (1 <= int(month) <= 12):
        return {}

    month_start = date(int(year), int(month), 1)
    month_end = date(int(year) + 1, 1, 1) if int(month) == 12 else date(int(year), int(month) + 1, 1)
    month_end = month_end.fromordinal(month_end.toordinal() - 1)

    rows = list_convalide_overlapping(
        store_code=str(store_code),
        start_iso=month_start.isoformat(),
        end_iso=month_end.isoformat(),
    )

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d1 = _parse_iso(str(r.get("dal_iso") or ""))
        d2 = _parse_iso(str(r.get("al_iso") or ""))
        if d2 < d1:
            d1, d2 = d2, d1
        cur = max(d1, month_start)
        last = min(d2, month_end)
        while cur <= last:
            out[cur.isoformat()] = dict(r)
            cur = date.fromordinal(cur.toordinal() + 1)
    return out


def insert_convalida_periodo(
    *,
    store_code: str,
    dal_iso: str,
    al_iso: str,
    total_diff: float,
    created_by: str,
    created_name: str,
    created_role: str,
) -> Dict[str, Any]:
    _ensure_table()
    d1 = _parse_iso(dal_iso)
    d2 = _parse_iso(al_iso)
    if d2 < d1:
        d1, d2 = d2, d1

    existing = list_convalide_overlapping(store_code=str(store_code), start_iso=d1.isoformat(), end_iso=d2.isoformat())
    if existing:
        raise RuntimeError("Esiste già una convalida che si sovrappone al periodo selezionato.")

    conn = _conn(read_only=False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            INSERT INTO dbo.{TABLE_NAME}(site, dal, al, total_diff, created_by, created_name, created_role)
            OUTPUT INSERTED.id, INSERTED.site, INSERTED.dal, INSERTED.al, INSERTED.total_diff, INSERTED.created_by, INSERTED.created_name, INSERTED.created_role, INSERTED.created_at
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(store_code).strip(),
                d1,
                d2,
                float(total_diff or 0.0),
                str(created_by or "").strip(),
                str(created_name or "").strip(),
                str(created_role or "").strip().lower(),
            ],
        )
        row = cur.fetchone()
        conn.commit()
        return _row_to_dict(row) if row else {}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('rendiconto_convalide_repository:183')


def list_convalide_store(*, store_code: str) -> List[Dict[str, Any]]:
    _ensure_table()
    conn = _conn(read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, site, dal, al, total_diff, created_by, created_name, created_role, created_at
            FROM dbo.{TABLE_NAME}
            WHERE LTRIM(RTRIM(site)) = ?
            ORDER BY dal DESC, al DESC, created_at DESC
            """,
            [str(store_code).strip()],
        )
        return [_row_to_dict(r) for r in (cur.fetchall() or [])]
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('rendiconto_convalide_repository:205')


def delete_convalida_by_id(*, convalida_id: int, store_code: str) -> bool:
    _ensure_table()
    conn = _conn(read_only=False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            DELETE FROM dbo.{TABLE_NAME}
            WHERE id = ?
              AND LTRIM(RTRIM(site)) = ?
            """,
            [int(convalida_id), str(store_code).strip()],
        )
        affected = int(cur.rowcount or 0)
        conn.commit()
        return affected > 0
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('rendiconto_convalide_repository:228')
