from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict

from app_db import get_backend, get_connection, sql_date, sql_cast_str, supports_schema_alter

try:
    from flask import has_request_context, session
except Exception:  # pragma: no cover
    has_request_context = None  # type: ignore
    session = None  # type: ignore


DEFAULT_TENANT_KEY = "default"


def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def _access_has_table(cur, table_name: str) -> bool:
    t = str(table_name).strip().lower()
    for row in cur.tables(tableType="TABLE"):
        n = getattr(row, "table_name", None)
        if not n:
            try:
                n = row[2]
            except Exception:
                n = None
        if n and str(n).strip().lower() == t:
            return True
    return False


def _has_table(cur, table_name: str) -> bool:
    if _access_has_table(cur, table_name):
        return True
    try:
        cur.execute("SELECT OBJECT_ID(?)", f"dbo.{table_name}")
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def _access_column_types(cur, table_name: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for row in cur.columns(table=table_name):
            n = getattr(row, "column_name", None)
            if not n:
                try:
                    n = row[3]
                except Exception:
                    n = None
            t = getattr(row, "type_name", None)
            if not t:
                try:
                    t = row[5]
                except Exception:
                    t = None
            if n:
                out[_norm(str(n))] = str(t or "").upper()
    except Exception:
        pass
    return out


def _is_text_type(type_name: str) -> bool:
    t = (type_name or "").upper()
    return any(x in t for x in ("CHAR", "TEXT", "VARCHAR", "LONGCHAR", "MEMO"))


def _ensure_sales_table(conn) -> None:
    cur = conn.cursor()
    if _has_table(cur, "Sales"):
        return
    if not supports_schema_alter():
        return
    cur.execute(
        """
        CREATE TABLE Sales (
            Site TEXT(20),
            Data DATETIME,
            Sales CURRENCY
        )
        """
    )
    try:
        cur.execute("CREATE INDEX idx_sales_site_data ON Sales (Site, Data)")
    except Exception:
        pass
    conn.commit()


def _coerce_date_for_insert(d: date, data_type: str) -> Any:
    if _is_text_type(data_type):
        return d.isoformat()
    return datetime(d.year, d.month, d.day)


def _tenant_key() -> str:
    try:
        if has_request_context and has_request_context() and session is not None:
            key = str(session.get("tenant_key") or "").strip()
            if key:
                return key
    except Exception:
        pass
    return DEFAULT_TENANT_KEY


def ensure_sales_forecast_schema() -> None:
    if get_backend() != "sqlserver":
        return
    with get_connection(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
IF OBJECT_ID('dbo.StoreHubSalesForecast','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubSalesForecast (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    store_code NVARCHAR(50) NOT NULL,
    business_date DATE NOT NULL,
    forecast_net DECIMAL(18,4) NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubSalesForecast_tenant_store_date
    ON dbo.StoreHubSalesForecast (tenant_key, store_code, business_date);
END
"""
        )
        conn.commit()


def _list_storehub_sales_forecast(*, store_code: str, start_day: date, end_day: date) -> Dict[str, float]:
    ensure_sales_forecast_schema()
    tenant = _tenant_key()
    with get_connection(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT business_date, forecast_net
  FROM dbo.StoreHubSalesForecast
 WHERE tenant_key = ?
   AND store_code = ?
   AND business_date >= ?
   AND business_date <= ?
 ORDER BY business_date
""",
            tenant,
            str(store_code).strip(),
            start_day,
            end_day,
        )
        out: Dict[str, float] = {}
        for row in cur.fetchall() or []:
            d = row[0]
            if isinstance(d, datetime):
                d_iso = d.date().isoformat()
            elif isinstance(d, date):
                d_iso = d.isoformat()
            else:
                d_iso = str(d or "").strip()[:10]
            if not d_iso:
                continue
            try:
                out[d_iso] = float(row[1] or 0.0)
            except Exception:
                out[d_iso] = 0.0
        return out


def _save_storehub_sales_forecast(*, store_code: str, sales_by_day: Dict[str, float]) -> Dict[str, Any]:
    ensure_sales_forecast_schema()
    tenant = _tenant_key()
    saved = 0
    with get_connection(read_only=False) as conn:
        cur = conn.cursor()
        for d_iso, val in (sales_by_day or {}).items():
            try:
                d = date.fromisoformat(str(d_iso))
            except Exception:
                continue
            cur.execute(
                """
UPDATE dbo.StoreHubSalesForecast
   SET forecast_net = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
   AND store_code = ?
   AND business_date = ?
""",
                float(val or 0.0),
                tenant,
                str(store_code).strip(),
                d,
            )
            if not cur.rowcount:
                cur.execute(
                    """
INSERT INTO dbo.StoreHubSalesForecast (tenant_key, store_code, business_date, forecast_net)
VALUES (?, ?, ?, ?)
""",
                    tenant,
                    str(store_code).strip(),
                    d,
                    float(val or 0.0),
                )
            saved += 1
        conn.commit()
    return {"ok": True, "saved": saved, "source": "StoreHubSalesForecast"}


def list_sales_week(*, store_code: str, start_day: date, end_day: date) -> Dict[str, float]:
    if get_backend() == "sqlserver":
        return _list_storehub_sales_forecast(store_code=store_code, start_day=start_day, end_day=end_day)
    conn = get_connection(store_code)
    try:
        _ensure_sales_table(conn)
        cur = conn.cursor()
        if not _has_table(cur, "Sales"):
            return {}

        types = _access_column_types(cur, "Sales")
        site_t = types.get(_norm("Site"), "")
        data_t = types.get(_norm("Data"), "")

        params = []
        where = []

        # Site
        if _is_text_type(site_t) or not site_t:
            where.append(f"{_qname('Site')}=?")
            params.append(str(store_code))
        else:
            # numeric site
            try:
                params.append(int(str(store_code).strip()))
            except Exception:
                params.append(str(store_code))
            where.append(f"{_qname('Site')}=?")

        # Date range
        if "DATE" in data_t or "TIME" in data_t or not _is_text_type(data_t):
            start_dt = datetime(start_day.year, start_day.month, start_day.day)
            end_excl = datetime(end_day.year, end_day.month, end_day.day) + timedelta(days=1)
            where.append(f"{_qname('Data')}>=? AND {_qname('Data')}<?")
            params.extend([start_dt, end_excl])
        else:
            where.append(f"{sql_date(_qname('Data'))}>=? AND {sql_date(_qname('Data'))}<=?")
            params.extend([start_day.isoformat(), end_day.isoformat()])

        sql = f"SELECT {_qname('Data')}, {_qname('Sales')} FROM {_qname('Sales')} WHERE " + " AND ".join(where)
        cur.execute(sql, params)

        out: Dict[str, float] = {}
        for row in cur.fetchall() or []:
            d = row[0]
            v = row[1]
            if isinstance(d, datetime):
                di = d.date().isoformat()
            elif isinstance(d, date):
                di = d.isoformat()
            else:
                try:
                    di = datetime.fromisoformat(str(d)).date().isoformat()
                except Exception:
                    di = str(d)
            try:
                out[di] = float(v) if v is not None else 0.0
            except Exception:
                try:
                    out[di] = float(str(v).replace(',', '.'))
                except Exception:
                    out[di] = 0.0
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_sales_week(*, store_code: str, sales_by_day: Dict[str, float]) -> Dict[str, Any]:
    if get_backend() == "sqlserver":
        return _save_storehub_sales_forecast(store_code=store_code, sales_by_day=sales_by_day)
    conn = get_connection(store_code)
    try:
        _ensure_sales_table(conn)
        cur = conn.cursor()
        if not _has_table(cur, "Sales"):
            raise RuntimeError("Tabella Sales non inizializzata per questo tenant.")

        types = _access_column_types(cur, "Sales")
        site_t = types.get(_norm("Site"), "")
        data_t = types.get(_norm("Data"), "")
        sales_t = types.get(_norm("Sales"), "")

        def _coerce_site(v: str) -> Any:
            if _is_text_type(site_t) or not site_t:
                return str(v)
            try:
                return int(str(v).strip())
            except Exception:
                return str(v)

        def _coerce_sales(v: float) -> Any:
            if _is_text_type(sales_t):
                return str(v)
            return float(v)

        saved = 0
        for d_iso, val in (sales_by_day or {}).items():
            try:
                d = date.fromisoformat(str(d_iso))
            except Exception:
                continue

            # Delete existing row for that day/site (robust across types)
            cur.execute(
                f"DELETE FROM {_qname('Sales')} WHERE {sql_cast_str(_qname('Site'))}=? AND {sql_date(_qname('Data'))}=?",
                [str(store_code), d.isoformat()],
            )

            # Insert new
            cur.execute(
                f"INSERT INTO {_qname('Sales')} ({_qname('Site')}, {_qname('Data')}, {_qname('Sales')}) VALUES (?,?,?)",
                [_coerce_site(str(store_code)), _coerce_date_for_insert(d, data_t), _coerce_sales(float(val))],
            )
            saved += 1

        conn.commit()
        return {"ok": True, "saved": saved}
    finally:
        try:
            conn.close()
        except Exception:
            pass
