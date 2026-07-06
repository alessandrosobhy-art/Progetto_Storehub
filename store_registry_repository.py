from __future__ import annotations

from app_logging import log_swallowed
from datetime import date, datetime
from typing import Any, Dict, List

from app_db import get_connection_ilp, get_connection_sqlserver_database, get_storehub_database_name


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def _norm(v: Any) -> str:
    return str(v or "").strip().lower()


def _norm_key(v: Any) -> str:
    return "".join(ch for ch in _norm(v) if ch.isalnum())


def _clean(v: Any) -> str:
    return str(v or "").strip()


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(raw).date()
    except Exception:
        return None


def _find_column(columns: List[str], *, exact: List[str] | None = None, contains: List[str] | None = None, exclude: List[str] | None = None) -> str:
    exact_norm = [_norm_key(x) for x in (exact or [])]
    contains_norm = [_norm_key(x) for x in (contains or [])]
    exclude_norm = {_norm_key(x) for x in (exclude or []) if x}
    for col in columns:
        key = _norm_key(col)
        if key in exclude_norm:
            continue
        if key in exact_norm:
            return col
    for col in columns:
        key = _norm_key(col)
        if key in exclude_norm:
            continue
        if any(token and token in key for token in contains_norm):
            return col
    return ""


def ensure_store_registry_schema() -> None:
    sql = """
IF OBJECT_ID('dbo.StoreHubStoreRegistry','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubStoreRegistry (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    store_code NVARCHAR(50) NOT NULL,
    store_name NVARCHAR(255) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    area_manager NVARCHAR(255) NULL,
    yoobic_address NVARCHAR(500) NULL,
    closure_date DATE NULL,
    opening_date DATE NULL,
    address_line1 NVARCHAR(255) NULL,
    address_line2 NVARCHAR(255) NULL,
    postal_code NVARCHAR(30) NULL,
    city NVARCHAR(120) NULL,
    province NVARCHAR(120) NULL,
    country NVARCHAR(120) NULL,
    phone NVARCHAR(80) NULL,
    email NVARCHAR(320) NULL,
    google_location_id NVARCHAR(255) NULL,
    glovo_store_id NVARCHAR(255) NULL,
    deliveroo_store_id NVARCHAR(255) NULL,
    ipratico_api_key NVARCHAR(500) NULL,
    notes NVARCHAR(MAX) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubStoreRegistry_store_code
    ON dbo.StoreHubStoreRegistry(store_code);
END
IF COL_LENGTH('dbo.StoreHubStoreRegistry','store_name') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD store_name NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','is_active') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD is_active BIT NOT NULL DEFAULT 1;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','sort_order') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD sort_order INT NOT NULL DEFAULT 0;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','area_manager') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD area_manager NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','yoobic_address') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD yoobic_address NVARCHAR(500) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','closure_date') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD closure_date DATE NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','opening_date') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD opening_date DATE NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','address_line1') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD address_line1 NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','address_line2') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD address_line2 NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','postal_code') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD postal_code NVARCHAR(30) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','city') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD city NVARCHAR(120) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','province') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD province NVARCHAR(120) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','country') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD country NVARCHAR(120) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','phone') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD phone NVARCHAR(80) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','email') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD email NVARCHAR(320) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','google_location_id') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD google_location_id NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','glovo_store_id') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD glovo_store_id NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','deliveroo_store_id') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD deliveroo_store_id NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','ipratico_api_key') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD ipratico_api_key NVARCHAR(500) NULL;
IF COL_LENGTH('dbo.StoreHubStoreRegistry','notes') IS NULL
  ALTER TABLE dbo.StoreHubStoreRegistry ADD notes NVARCHAR(MAX) NULL;

IF OBJECT_ID('dbo.StoreHubAreaManagers','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubAreaManagers (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    name NVARCHAR(255) NOT NULL,
    code NVARCHAR(120) NULL,
    email NVARCHAR(320) NULL,
    phone NVARCHAR(80) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubAreaManagers_name
    ON dbo.StoreHubAreaManagers(name);
END
IF COL_LENGTH('dbo.StoreHubAreaManagers','code') IS NULL
  ALTER TABLE dbo.StoreHubAreaManagers ADD code NVARCHAR(120) NULL;
IF COL_LENGTH('dbo.StoreHubAreaManagers','email') IS NULL
  ALTER TABLE dbo.StoreHubAreaManagers ADD email NVARCHAR(320) NULL;
IF COL_LENGTH('dbo.StoreHubAreaManagers','phone') IS NULL
  ALTER TABLE dbo.StoreHubAreaManagers ADD phone NVARCHAR(80) NULL;
IF COL_LENGTH('dbo.StoreHubAreaManagers','is_active') IS NULL
  ALTER TABLE dbo.StoreHubAreaManagers ADD is_active BIT NOT NULL DEFAULT 1;
"""
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()


def _row_to_dict(row: Any, columns: List[str]) -> Dict[str, Any]:
    item = dict(zip(columns, row))
    return {
        "store_code": _clean(item.get("store_code")),
        "store_name": _clean(item.get("store_name")),
        "is_active": bool(item.get("is_active")),
        "sort_order": int(item.get("sort_order") or 0),
        "area_manager": _clean(item.get("area_manager")),
        "yoobic_address": _clean(item.get("yoobic_address")),
        "yoobic": _clean(item.get("yoobic_address")),
        "closure_date": item.get("closure_date"),
        "opening_date": item.get("opening_date"),
        "address_line1": _clean(item.get("address_line1")),
        "address_line2": _clean(item.get("address_line2")),
        "postal_code": _clean(item.get("postal_code")),
        "city": _clean(item.get("city")),
        "province": _clean(item.get("province")),
        "country": _clean(item.get("country")),
        "phone": _clean(item.get("phone")),
        "email": _clean(item.get("email")),
        "google_location_id": _clean(item.get("google_location_id")),
        "google": _clean(item.get("google_location_id")),
        "glovo_store_id": _clean(item.get("glovo_store_id")),
        "glovo": _clean(item.get("glovo_store_id")),
        "deliveroo_store_id": _clean(item.get("deliveroo_store_id")),
        "deliveroo": _clean(item.get("deliveroo_store_id")),
        "ipratico_api_key": _clean(item.get("ipratico_api_key")),
        "zucchetti": _clean(item.get("ipratico_api_key")),
        "notes": _clean(item.get("notes")),
    }


def list_store_registry(include_inactive: bool = False) -> List[Dict[str, Any]]:
    ensure_store_registry_schema()
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        sql = """
SELECT store_code, store_name, is_active, sort_order, area_manager, yoobic_address,
       closure_date, opening_date, address_line1, address_line2, postal_code,
       city, province, country, phone, email,
       google_location_id, glovo_store_id, deliveroo_store_id,
       ipratico_api_key, notes
  FROM dbo.StoreHubStoreRegistry
"""
        if not include_inactive:
            sql += " WHERE is_active = 1\n"
        sql += " ORDER BY sort_order, store_code"
        cur.execute(sql)
        columns = [c[0] for c in cur.description]
        return [_row_to_dict(row, columns) for row in cur.fetchall()]


def get_store_registry(store_code: str) -> Dict[str, Any] | None:
    ensure_store_registry_schema()
    code = _clean(store_code)
    if not code:
        return None
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT TOP 1 store_code, store_name, is_active, sort_order, area_manager, yoobic_address,
       closure_date, opening_date, address_line1, address_line2, postal_code,
       city, province, country, phone, email,
       google_location_id, glovo_store_id, deliveroo_store_id,
       ipratico_api_key, notes
  FROM dbo.StoreHubStoreRegistry
 WHERE store_code = ?
""",
            code,
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [c[0] for c in cur.description]
        return _row_to_dict(row, columns)


def upsert_store_registry(
    *,
    store_code: str,
    store_name: str | None = None,
    is_active: bool = True,
    sort_order: int = 0,
    area_manager: str | None = None,
    yoobic_address: str | None = None,
    closure_date: Any = None,
    opening_date: Any = None,
    address_line1: str | None = None,
    address_line2: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    province: str | None = None,
    country: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    google_location_id: str | None = None,
    glovo_store_id: str | None = None,
    deliveroo_store_id: str | None = None,
    ipratico_api_key: str | None = None,
    notes: str | None = None,
) -> Dict[str, Any]:
    ensure_store_registry_schema()
    code = _clean(store_code)
    if not code:
        raise ValueError("Codice store obbligatorio")
    closure = _parse_date(closure_date)
    opening = _parse_date(opening_date)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
IF EXISTS (SELECT 1 FROM dbo.StoreHubStoreRegistry WHERE store_code = ?)
BEGIN
  UPDATE dbo.StoreHubStoreRegistry
     SET store_name = ?,
         is_active = ?,
         sort_order = ?,
         area_manager = ?,
         yoobic_address = ?,
         closure_date = ?,
         opening_date = ?,
         address_line1 = ?,
         address_line2 = ?,
         postal_code = ?,
         city = ?,
         province = ?,
         country = ?,
         phone = ?,
         email = ?,
         google_location_id = ?,
         glovo_store_id = ?,
         deliveroo_store_id = ?,
         ipratico_api_key = ?,
         notes = ?,
         updated_at = SYSUTCDATETIME()
   WHERE store_code = ?;
END
ELSE
BEGIN
  INSERT INTO dbo.StoreHubStoreRegistry
    (store_code, store_name, is_active, sort_order, area_manager, yoobic_address,
     closure_date, opening_date, address_line1, address_line2, postal_code,
     city, province, country, phone, email,
     google_location_id, glovo_store_id, deliveroo_store_id, ipratico_api_key, notes)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
END
""",
            code,
            _clean(store_name) or None,
            1 if is_active else 0,
            int(sort_order or 0),
            _clean(area_manager) or None,
            _clean(yoobic_address) or None,
            closure,
            opening,
            _clean(address_line1) or None,
            _clean(address_line2) or None,
            _clean(postal_code) or None,
            _clean(city) or None,
            _clean(province) or None,
            _clean(country) or None,
            _clean(phone) or None,
            _clean(email) or None,
            _clean(google_location_id) or None,
            _clean(glovo_store_id) or None,
            _clean(deliveroo_store_id) or None,
            _clean(ipratico_api_key) or None,
            _clean(notes) or None,
            code,
            code,
            _clean(store_name) or None,
            1 if is_active else 0,
            int(sort_order or 0),
            _clean(area_manager) or None,
            _clean(yoobic_address) or None,
            closure,
            opening,
            _clean(address_line1) or None,
            _clean(address_line2) or None,
            _clean(postal_code) or None,
            _clean(city) or None,
            _clean(province) or None,
            _clean(country) or None,
            _clean(phone) or None,
            _clean(email) or None,
            _clean(google_location_id) or None,
            _clean(glovo_store_id) or None,
            _clean(deliveroo_store_id) or None,
            _clean(ipratico_api_key) or None,
            _clean(notes) or None,
        )
        conn.commit()
    return get_store_registry(code) or {"store_code": code}


def list_area_managers(include_inactive: bool = False) -> List[Dict[str, Any]]:
    ensure_store_registry_schema()
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        sql = """
SELECT row_uuid, name, code, email, phone, is_active, created_at, updated_at
  FROM dbo.StoreHubAreaManagers
"""
        if not include_inactive:
            sql += " WHERE is_active = 1\n"
        sql += " ORDER BY name"
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def upsert_area_manager(
    *,
    name: str,
    code: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    is_active: bool = True,
) -> Dict[str, Any]:
    ensure_store_registry_schema()
    am_name = _clean(name)
    if not am_name:
        raise ValueError("Nome area manager obbligatorio")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
IF EXISTS (SELECT 1 FROM dbo.StoreHubAreaManagers WHERE name = ?)
BEGIN
  UPDATE dbo.StoreHubAreaManagers
     SET code = ?,
         email = ?,
         phone = ?,
         is_active = ?,
         updated_at = SYSUTCDATETIME()
   WHERE name = ?;
END
ELSE
BEGIN
  INSERT INTO dbo.StoreHubAreaManagers (name, code, email, phone, is_active)
  VALUES (?, ?, ?, ?, ?);
END
""",
            am_name,
            _clean(code) or None,
            _clean(email) or None,
            _clean(phone) or None,
            1 if is_active else 0,
            am_name,
            am_name,
            _clean(code) or None,
            _clean(email) or None,
            _clean(phone) or None,
            1 if is_active else 0,
        )
        conn.commit()
    return next((r for r in list_area_managers(include_inactive=True) if _clean(r.get("name")) == am_name), {"name": am_name})


def set_area_manager_active(name: str, active: bool) -> bool:
    ensure_store_registry_schema()
    am_name = _clean(name)
    if not am_name:
        raise ValueError("Nome area manager obbligatorio")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubAreaManagers
   SET is_active = ?,
       updated_at = SYSUTCDATETIME()
 WHERE name = ?
""",
            1 if active else 0,
            am_name,
        )
        ok = bool(cur.rowcount)
        conn.commit()
        return ok


def assign_area_manager_to_stores(area_manager_name: str, store_codes: List[str]) -> int:
    ensure_store_registry_schema()
    am_name = _clean(area_manager_name)
    if not am_name:
        raise ValueError("Area manager obbligatorio")
    codes = [_clean(code) for code in (store_codes or []) if _clean(code)]
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE dbo.StoreHubStoreRegistry SET area_manager = NULL, updated_at = SYSUTCDATETIME() WHERE area_manager = ?", am_name)
        count = 0
        for code in codes:
            cur.execute(
                """
UPDATE dbo.StoreHubStoreRegistry
   SET area_manager = ?,
       updated_at = SYSUTCDATETIME()
 WHERE store_code = ?
""",
                am_name,
                code,
            )
            if not cur.rowcount:
                cur.execute(
                    """
INSERT INTO dbo.StoreHubStoreRegistry (store_code, store_name, area_manager)
VALUES (?, ?, ?)
""",
                    code,
                    code,
                    am_name,
                )
            count += 1
        conn.commit()
        return count


def sync_area_managers_from_store_registry() -> int:
    ensure_store_registry_schema()
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT DISTINCT LTRIM(RTRIM(area_manager)) AS area_manager
  FROM dbo.StoreHubStoreRegistry
 WHERE area_manager IS NOT NULL
   AND LTRIM(RTRIM(area_manager)) <> ''
"""
        )
        names = [_clean(row[0]) for row in cur.fetchall() if _clean(row[0])]
        count = 0
        for name in names:
            cur.execute(
                """
IF NOT EXISTS (SELECT 1 FROM dbo.StoreHubAreaManagers WHERE name = ?)
BEGIN
  INSERT INTO dbo.StoreHubAreaManagers (name, is_active)
  VALUES (?, 1);
END
ELSE
BEGIN
  UPDATE dbo.StoreHubAreaManagers
     SET is_active = 1,
         updated_at = SYSUTCDATETIME()
   WHERE name = ?;
END
""",
                name,
                name,
                name,
            )
            count += 1
        conn.commit()
        return count


def delete_store_registry(store_code: str) -> bool:
    ensure_store_registry_schema()
    code = _clean(store_code)
    if not code:
        raise ValueError("Codice store obbligatorio")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.StoreHubStoreRegistry WHERE store_code = ?", code)
        ok = bool(cur.rowcount)
        conn.commit()
        return ok


def get_ipratico_api_key_for_store(store_code: str) -> str:
    row = get_store_registry(store_code)
    api_key = _clean((row or {}).get("ipratico_api_key"))
    db_name = _clean(get_storehub_database_name()).upper()
    if not api_key and db_name in {"APP_STOREHUB", "APP_STOREHUB_DEFAULT"}:
        try:
            from db_integration import get_warehouse_stores

            seed_store_registry_from_ilp(get_warehouse_stores(include_inactive=True) or [], only_missing=True)
            row = get_store_registry(store_code)
            api_key = _clean((row or {}).get("ipratico_api_key"))
        except Exception:
            api_key = ""
    if not api_key:
        raise RuntimeError(f"Chiave iPratico non configurata per lo store {store_code}.")
    return api_key


def _load_ilp_store_rows() -> List[Dict[str, Any]]:
    conn = get_connection_ilp(read_only=True)
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT TOP 5000 * FROM dbo.[STORE]")
        except Exception:
            cur.execute("SELECT TOP 5000 * FROM [STORE]")
        columns = [d[0] for d in (cur.description or [])]
        code_col = _find_column(
            columns,
            exact=["codice", "storecode", "site", "pdv", "idpdv"],
            contains=["storecode", "codice", "site", "pdv"],
            exclude=["am"],
        )
        name_col = _find_column(
            columns,
            exact=["store", "storename", "name", "descrizione", "negozio", "puntovendita"],
            contains=["store", "descr", "negozio", "puntovendita", "ragione"],
            exclude=["am"],
        )
        out: List[Dict[str, Any]] = []
        field_map = {
            "area_manager": _find_column(columns, exact=["am"], contains=["area manager", "am"]),
            "yoobic_address": _find_column(columns, exact=["yoobic"], contains=["yoobic"]),
            "ipratico_api_key": _find_column(columns, exact=["zucchetti"], contains=["zucchetti"]),
            "opening_date": _find_column(columns, exact=["apertura", "dataapertura", "openingdate"], contains=["apertura", "opening"]),
            "address_line1": _find_column(columns, exact=["indirizzo", "address"], contains=["indirizzo", "address"]),
            "postal_code": _find_column(columns, exact=["cap", "zipcode", "postalcode"], contains=["cap", "zip", "postal"]),
            "city": _find_column(columns, exact=["citta", "city", "comune"], contains=["citta", "city", "comune"]),
            "province": _find_column(columns, exact=["provincia", "province", "prov"], contains=["provincia", "province"]),
            "country": _find_column(columns, exact=["nazione", "country"], contains=["nazione", "country"]),
            "phone": _find_column(columns, exact=["telefono", "phone", "tel"], contains=["telefono", "phone"]),
            "email": _find_column(columns, exact=["email", "mail"], contains=["email", "mail"]),
            "google_location_id": _find_column(columns, exact=["google"], contains=["google"]),
            "glovo_store_id": _find_column(columns, exact=["glovo"], contains=["glovo"]),
            "deliveroo_store_id": _find_column(columns, exact=["deliveroo"], contains=["deliveroo"]),
            "closure_date": _find_column(columns, exact=["chiusura"], contains=["chiusura", "closing"]),
        }
        for raw in cur.fetchall() or []:
            row = dict(zip(columns, raw))
            code = _clean(row.get(code_col)) if code_col else ""
            if not code:
                continue
            item = {
                "store_code": code,
                "store_name": _clean(row.get(name_col)) if name_col else code,
            }
            for target, source in field_map.items():
                item[target] = row.get(source) if source else None
            out.append(item)
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('store_registry_repository:613')


def seed_store_registry_from_ilp(stores: List[Dict[str, Any]] | None = None, *, only_missing: bool = True) -> int:
    ensure_store_registry_schema()
    ilp_rows = _load_ilp_store_rows()
    by_code = {_norm_key(row.get("store_code")): row for row in ilp_rows if _norm_key(row.get("store_code"))}
    by_name = {_norm_key(row.get("store_name")): row for row in ilp_rows if _norm_key(row.get("store_name"))}
    source_stores = stores or [
        {"code": row.get("store_code"), "name": row.get("store_name"), "is_active": True, "sort_order": 0}
        for row in ilp_rows
    ]
    count = 0
    for store in source_stores:
        code = _clean((store or {}).get("code") or (store or {}).get("store_code"))
        if not code:
            continue
        if only_missing and get_store_registry(code):
            continue
        name = _clean((store or {}).get("name") or (store or {}).get("store_name"))
        ilp = by_code.get(_norm_key(code)) or by_name.get(_norm_key(name)) or {}
        upsert_store_registry(
            store_code=code,
            store_name=name or _clean(ilp.get("store_name")) or code,
            is_active=bool((store or {}).get("is_active", True)),
            sort_order=int((store or {}).get("sort_order") or 0),
            area_manager=_clean(ilp.get("area_manager")),
            yoobic_address=_clean(ilp.get("yoobic_address")),
            closure_date=ilp.get("closure_date"),
            opening_date=ilp.get("opening_date"),
            address_line1=_clean(ilp.get("address_line1")),
            postal_code=_clean(ilp.get("postal_code")),
            city=_clean(ilp.get("city")),
            province=_clean(ilp.get("province")),
            country=_clean(ilp.get("country")),
            phone=_clean(ilp.get("phone")),
            email=_clean(ilp.get("email")),
            google_location_id=_clean(ilp.get("google_location_id")),
            glovo_store_id=_clean(ilp.get("glovo_store_id")),
            deliveroo_store_id=_clean(ilp.get("deliveroo_store_id")),
            ipratico_api_key=_clean(ilp.get("ipratico_api_key")),
        )
        count += 1
    sync_area_managers_from_store_registry()
    return count
