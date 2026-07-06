from __future__ import annotations

from app_logging import log_swallowed
import datetime as _dt
from typing import Any, Dict, Iterable, List, Set

from app_db import get_connection_sqlserver_database, get_storehub_database_name


MONTH_NAMES = [
    "Gennaio",
    "Febbraio",
    "Marzo",
    "Aprile",
    "Maggio",
    "Giugno",
    "Luglio",
    "Agosto",
    "Settembre",
    "Ottobre",
    "Novembre",
    "Dicembre",
]


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def ensure_pnl_store_visibility_schema() -> None:
    sql = """
IF OBJECT_ID('dbo.CruscottoPnlStoreVisibleMonths','U') IS NULL
BEGIN
  CREATE TABLE dbo.CruscottoPnlStoreVisibleMonths (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    year_num INT NOT NULL,
    month_num INT NOT NULL,
    is_visible BIT NOT NULL CONSTRAINT DF_CruscottoPnlStoreVisibleMonths_visible DEFAULT 1,
    updated_at DATETIME2 NOT NULL CONSTRAINT DF_CruscottoPnlStoreVisibleMonths_updated DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_CruscottoPnlStoreVisibleMonths_year_month
    ON dbo.CruscottoPnlStoreVisibleMonths(year_num, month_num);
END
"""
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()


def list_visible_months(year_from: int | None = None, year_to: int | None = None) -> List[Dict[str, Any]]:
    ensure_pnl_store_visibility_schema()
    params: list[Any] = []
    where = ["is_visible = 1"]
    if year_from is not None:
        where.append("year_num >= ?")
        params.append(int(year_from))
    if year_to is not None:
        where.append("year_num <= ?")
        params.append(int(year_to))

    sql = f"""
SELECT year_num, month_num
FROM dbo.CruscottoPnlStoreVisibleMonths
WHERE {' AND '.join(where)}
ORDER BY year_num DESC, month_num ASC
    """
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(sql, *params)
        rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        y = int(getattr(r, "year_num", r[0]))
        m = int(getattr(r, "month_num", r[1]))
        out.append(
            {
                "year": y,
                "month": m,
                "label": f"{MONTH_NAMES[m - 1]} {y}" if 1 <= m <= 12 else f"{m}/{y}",
            }
        )
    return out


def visible_month_set() -> Set[tuple[int, int]]:
    return {(int(r["year"]), int(r["month"])) for r in list_visible_months()}


def period_is_visible(year: int, month_from: int, month_to: int) -> bool:
    year = int(year)
    month_from = max(1, min(12, int(month_from)))
    month_to = max(1, min(12, int(month_to)))
    if month_from > month_to:
        month_from, month_to = month_to, month_from

    allowed = visible_month_set()
    return all((year, m) in allowed for m in range(month_from, month_to + 1))


def save_visible_months_for_year(year: int, visible_months: Iterable[int]) -> None:
    ensure_pnl_store_visibility_schema()
    year = int(year)
    wanted = {int(m) for m in visible_months if 1 <= int(m) <= 12}

    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        for month in range(1, 13):
            is_visible = 1 if month in wanted else 0
            cur.execute(
                """
UPDATE dbo.CruscottoPnlStoreVisibleMonths
   SET is_visible = ?, updated_at = SYSUTCDATETIME()
 WHERE year_num = ? AND month_num = ?
""",
                is_visible,
                year,
                month,
            )
            cur.execute(
                """
IF NOT EXISTS (
  SELECT 1 FROM dbo.CruscottoPnlStoreVisibleMonths WHERE year_num = ? AND month_num = ?
)
BEGIN
  INSERT INTO dbo.CruscottoPnlStoreVisibleMonths (year_num, month_num, is_visible)
  VALUES (?, ?, ?)
END
""",
                year,
                month,
                year,
                month,
                is_visible,
            )
        conn.commit()


def maintenance_years(extra_year: int | None = None) -> List[int]:
    current = _dt.date.today().year
    years = {current - 1, current, current + 1}
    if extra_year:
        years.add(int(extra_year))
    try:
        for row in list_visible_months(current - 5, current + 5):
            years.add(int(row["year"]))
    except Exception:
        log_swallowed('cruscotto_pnl_store_repository:148')
    return sorted(years, reverse=True)
