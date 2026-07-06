from __future__ import annotations

from app_logging import log_swallowed
import json
from datetime import date
from typing import List

from app_db import get_connection_sqlserver_database, get_storehub_database_name


TABLE_NAME = "dbo.ORARI_VISIBLE_PEOPLE"


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def ensure_orari_visibility_table() -> None:
    conn = _conn(read_only=False)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            IF OBJECT_ID('{TABLE_NAME}', 'U') IS NULL
            BEGIN
              CREATE TABLE {TABLE_NAME} (
                VisibilityId INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                StoreCode NVARCHAR(20) NOT NULL,
                WeekStart DATE NOT NULL,
                VisibleNamesJson NVARCHAR(MAX) NOT NULL,
                CreatedAt DATETIME2(0) NOT NULL CONSTRAINT DF_ORARI_VISIBLE_PEOPLE_CreatedAt DEFAULT SYSUTCDATETIME(),
                UpdatedAt DATETIME2(0) NOT NULL CONSTRAINT DF_ORARI_VISIBLE_PEOPLE_UpdatedAt DEFAULT SYSUTCDATETIME()
              );
            END
            """
        )
        cur.execute(
            f"""
            IF NOT EXISTS (
              SELECT 1
              FROM sys.indexes
              WHERE name = 'UX_ORARI_VISIBLE_PEOPLE_StoreCode_WeekStart'
                AND object_id = OBJECT_ID('{TABLE_NAME}')
            )
            BEGIN
              CREATE UNIQUE INDEX UX_ORARI_VISIBLE_PEOPLE_StoreCode_WeekStart
              ON {TABLE_NAME}(StoreCode, WeekStart);
            END
            """
        )
        conn.commit()
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('orari_visibility_repository:55')
        conn.close()


def _normalize_names(names: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in names or []:
        name = str(raw or "").strip()
        if not name:
            continue
        low = name.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(name)
    out.sort(key=lambda x: x.lower())
    return out


def get_visible_people_week(*, store_code: str, week_start: date) -> List[str]:
    ensure_orari_visibility_table()
    conn = _conn(read_only=True)
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT TOP 1 VisibleNamesJson FROM {TABLE_NAME} WHERE StoreCode=? AND WeekStart=?",
            [str(store_code or "").strip(), week_start],
        )
        row = cur.fetchone()
        if not row:
            return []
        try:
            data = json.loads(str(row[0] or "[]"))
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return _normalize_names([str(x or "").strip() for x in data])
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('orari_visibility_repository:98')
        conn.close()


def save_visible_people_week(*, store_code: str, week_start: date, names: list[str] | tuple[str, ...]) -> None:
    ensure_orari_visibility_table()
    payload = json.dumps(_normalize_names(list(names or [])), ensure_ascii=False)
    conn = _conn(read_only=False)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            MERGE {TABLE_NAME} AS tgt
            USING (SELECT ? AS StoreCode, ? AS WeekStart) AS src
              ON tgt.StoreCode = src.StoreCode AND tgt.WeekStart = src.WeekStart
            WHEN MATCHED THEN
              UPDATE SET VisibleNamesJson = ?, UpdatedAt = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
              INSERT (StoreCode, WeekStart, VisibleNamesJson)
              VALUES (?, ?, ?);
            """,
            [
                str(store_code or "").strip(),
                week_start,
                payload,
                str(store_code or "").strip(),
                week_start,
                payload,
            ],
        )
        conn.commit()
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('orari_visibility_repository:133')
        conn.close()
