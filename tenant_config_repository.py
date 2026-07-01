from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from app_db import get_connection_sqlserver_database


DB_NAME = os.getenv("STOREHUB_TENANT_DATABASE") or os.getenv("SQLSERVER_DATABASE") or os.getenv("SQLSERVER_DB") or "APP_STOREHUB"
DEFAULT_TENANT_KEY = os.getenv("STOREHUB_TENANT_KEY") or "default"
DEFAULT_TENANT_NAME = os.getenv("STOREHUB_TENANT_NAME") or "StoreHub default"
STORAGE_PROVIDERS = {"local_vm", "sharepoint", "supabase_storage", "s3", "azure_blob"}
DEFAULT_STORAGE_RULES = [
    ("rendiconto_spese", "Rendiconto - spese", "sharepoint", "FOTO_APP/SPESE", "", "Foto spese rendiconto"),
    ("rendiconto_versamenti", "Rendiconto - versamenti", "sharepoint", "FOTO_APP/VERSAMENTI", "", "Foto versamenti rendiconto"),
    ("rendiconto_distinta_cassa", "Rendiconto - distinta cassa", "sharepoint", "FOTO_APP/AZZERAMENTI", "", "Foto distinta cassa"),
    ("links", "Link", "supabase_storage", "", "app-links", "Immagini pagina Link"),
]
STORE_REGISTRY_FIELDS = [
    ("opening_date", "Data apertura", "Anagrafica"),
    ("phone", "Telefono", "Contatti"),
    ("email", "Email", "Contatti"),
    ("address_line1", "Indirizzo", "Indirizzo"),
    ("address_line2", "Indirizzo 2", "Indirizzo"),
    ("postal_code", "CAP", "Indirizzo"),
    ("city", "Citta", "Indirizzo"),
    ("province", "Provincia", "Indirizzo"),
    ("country", "Nazione", "Indirizzo"),
    ("yoobic_address", "Yoobic address", "MBO Audit"),
    ("closure_date", "Data chiusura", "Anagrafica"),
    ("ipratico_api_key", "API key iPratico", "Integrazione"),
    ("google_location_id", "Google location", "MBO Google"),
    ("glovo_store_id", "Glovo store id", "MBO Glovo"),
    ("deliveroo_store_id", "Deliveroo store id", "MBO Deliveroo"),
    ("notes", "Note", "Anagrafica"),
]
TENANT_MODULE_DEFAULTS = [
    ("dashboard", "Dashboard", True),
    ("magazzino", "Magazzino", True),
    ("supplier_orders", "Ordini al fornitore", True),
    ("rendiconto", "Rendiconto", True),
    ("estrazioni", "Estrazioni", True),
    ("orari", "Gestione Orari", True),
    ("cruscotto", "Cruscotto", True),
    ("links", "Link", True),
    ("estrazioni_hq", "Estrazioni HQ", False),
    ("controlli", "Controlli di Gestione", False),
    ("cruscotto_pnl_store", "Cruscotto - P&L store", False),
    ("mbo", "MBO", False),
]
SUPPORTED_TENANT_LANGUAGES = {"it", "en", "fr", "es", "pt"}


def _normalize_language_codes(value: str | list[str] | tuple[str, ...] | None, *, multilanguage_enabled: bool = False) -> str:
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = re.split(r"[,;\s]+", str(value or ""))
    codes: list[str] = []
    for item in raw_items:
        code = str(item or "").strip().lower()
        if code in SUPPORTED_TENANT_LANGUAGES and code not in codes:
            codes.append(code)
    if "it" not in codes:
        codes.insert(0, "it")
    if not multilanguage_enabled:
        return "it"
    return ",".join(codes or ["it"])


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(DB_NAME, read_only=read_only)


def current_tenant_key() -> str:
    return (DEFAULT_TENANT_KEY or "default").strip() or "default"


def ensure_tenant_schema() -> None:
    sql = """
IF OBJECT_ID('dbo.StoreHubTenants','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubTenants (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    display_name NVARCHAR(255) NOT NULL,
    database_name NVARCHAR(255) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubTenants_tenant_key
    ON dbo.StoreHubTenants(tenant_key);
END
IF COL_LENGTH('dbo.StoreHubTenants','sql_server') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD sql_server NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubTenants','sql_user') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD sql_user NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubTenants','storage_type') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD storage_type NVARCHAR(50) NULL;
IF COL_LENGTH('dbo.StoreHubTenants','storage_base_path') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD storage_base_path NVARCHAR(1024) NULL;
IF COL_LENGTH('dbo.StoreHubTenants','sharepoint_sharing_url') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD sharepoint_sharing_url NVARCHAR(MAX) NULL;
IF COL_LENGTH('dbo.StoreHubTenants','sharepoint_base_path') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD sharepoint_base_path NVARCHAR(1024) NULL;
IF COL_LENGTH('dbo.StoreHubTenants','notes') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD notes NVARCHAR(MAX) NULL;
IF COL_LENGTH('dbo.StoreHubTenants','master_can_admin') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD master_can_admin BIT NOT NULL DEFAULT 0;
IF COL_LENGTH('dbo.StoreHubTenants','tenant_admin_enabled') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD tenant_admin_enabled BIT NOT NULL DEFAULT 1;
IF COL_LENGTH('dbo.StoreHubTenants','max_users') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD max_users INT NULL;
IF COL_LENGTH('dbo.StoreHubTenants','max_stores') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD max_stores INT NULL;
IF COL_LENGTH('dbo.StoreHubTenants','scheduling_enabled') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD scheduling_enabled BIT NOT NULL DEFAULT 0;
IF COL_LENGTH('dbo.StoreHubTenants','ai_enabled') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD ai_enabled BIT NOT NULL DEFAULT 1;
IF COL_LENGTH('dbo.StoreHubTenants','multilanguage_enabled') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD multilanguage_enabled BIT NOT NULL DEFAULT 0;
IF COL_LENGTH('dbo.StoreHubTenants','enabled_language_codes') IS NULL
  ALTER TABLE dbo.StoreHubTenants ADD enabled_language_codes NVARCHAR(100) NOT NULL DEFAULT 'it';

IF OBJECT_ID('dbo.StoreHubTenantUsers','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubTenantUsers (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    user_id NVARCHAR(120) NULL,
    email NVARCHAR(320) NOT NULL,
    tenant_role NVARCHAR(50) NOT NULL DEFAULT 'user',
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubTenantUsers_tenant_email
    ON dbo.StoreHubTenantUsers(tenant_key, email);
END
IF COL_LENGTH('dbo.StoreHubTenantUsers','tenant_role') IS NULL
  ALTER TABLE dbo.StoreHubTenantUsers ADD tenant_role NVARCHAR(50) NOT NULL DEFAULT 'user';
IF COL_LENGTH('dbo.StoreHubTenantUsers','is_active') IS NULL
  ALTER TABLE dbo.StoreHubTenantUsers ADD is_active BIT NOT NULL DEFAULT 1;

IF OBJECT_ID('dbo.StoreHubTenantStores','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubTenantStores (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    store_code NVARCHAR(50) NOT NULL,
    store_name NVARCHAR(255) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubTenantStores_tenant_store
    ON dbo.StoreHubTenantStores(tenant_key, store_code);
END
IF COL_LENGTH('dbo.StoreHubTenantStores','store_name') IS NULL
  ALTER TABLE dbo.StoreHubTenantStores ADD store_name NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubTenantStores','is_active') IS NULL
  ALTER TABLE dbo.StoreHubTenantStores ADD is_active BIT NOT NULL DEFAULT 1;

IF OBJECT_ID('dbo.StoreHubTenantStorageRules','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubTenantStorageRules (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    category_key NVARCHAR(120) NOT NULL,
    display_name NVARCHAR(255) NOT NULL,
    provider NVARCHAR(50) NOT NULL,
    base_path NVARCHAR(1024) NULL,
    sharepoint_sharing_url NVARCHAR(MAX) NULL,
    bucket_name NVARCHAR(255) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    notes NVARCHAR(MAX) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubTenantStorageRules_tenant_category
    ON dbo.StoreHubTenantStorageRules(tenant_key, category_key);
END
IF COL_LENGTH('dbo.StoreHubTenantStorageRules','sharepoint_sharing_url') IS NULL
  ALTER TABLE dbo.StoreHubTenantStorageRules ADD sharepoint_sharing_url NVARCHAR(MAX) NULL;
IF COL_LENGTH('dbo.StoreHubTenantStorageRules','bucket_name') IS NULL
  ALTER TABLE dbo.StoreHubTenantStorageRules ADD bucket_name NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.StoreHubTenantStorageRules','notes') IS NULL
  ALTER TABLE dbo.StoreHubTenantStorageRules ADD notes NVARCHAR(MAX) NULL;

IF OBJECT_ID('dbo.StoreHubTenantStoreFieldConfig','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubTenantStoreFieldConfig (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    field_key NVARCHAR(120) NOT NULL,
    display_name NVARCHAR(255) NOT NULL,
    field_group NVARCHAR(120) NULL,
    is_visible BIT NOT NULL DEFAULT 1,
    is_required BIT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubTenantStoreFieldConfig_tenant_field
    ON dbo.StoreHubTenantStoreFieldConfig(tenant_key, field_key);
END
IF COL_LENGTH('dbo.StoreHubTenantStoreFieldConfig','field_group') IS NULL
  ALTER TABLE dbo.StoreHubTenantStoreFieldConfig ADD field_group NVARCHAR(120) NULL;
IF COL_LENGTH('dbo.StoreHubTenantStoreFieldConfig','is_required') IS NULL
  ALTER TABLE dbo.StoreHubTenantStoreFieldConfig ADD is_required BIT NOT NULL DEFAULT 0;

IF OBJECT_ID('dbo.StoreHubTenantModules','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubTenantModules (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    module_key NVARCHAR(120) NOT NULL,
    display_name NVARCHAR(255) NOT NULL,
    is_enabled BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubTenantModules_tenant_module
    ON dbo.StoreHubTenantModules(tenant_key, module_key);
END
"""
    # Schema checks are idempotent DDL/metadata operations. Run them in
    # autocommit mode so pooled pyodbc connections do not remain sleeping
    # with an open transaction after frequent tenant lookups.
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(sql)


def ensure_current_tenant() -> Dict[str, Any]:
    ensure_tenant_schema()
    key = current_tenant_key()
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenants
   SET database_name = COALESCE(database_name, ?),
       scheduling_enabled = CASE WHEN tenant_key = 'default' THEN 1 ELSE scheduling_enabled END,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
""",
            DB_NAME,
            key,
        )
        if not cur.rowcount:
            cur.execute(
                """
INSERT INTO dbo.StoreHubTenants (tenant_key, display_name, database_name)
VALUES (?, ?, ?)
""",
                key,
                DEFAULT_TENANT_NAME,
                DB_NAME,
            )
        conn.commit()
    seed_default_storage_rules(key)
    seed_default_tenant_modules(key)
    return get_current_tenant()


def get_current_tenant() -> Dict[str, Any]:
    ensure_tenant_schema()
    key = current_tenant_key()
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT TOP 1 tenant_key, display_name, database_name, is_active,
       sql_server, sql_user, storage_type, storage_base_path,
       sharepoint_sharing_url, sharepoint_base_path, notes,
       master_can_admin, tenant_admin_enabled, max_users, max_stores, scheduling_enabled, ai_enabled,
       multilanguage_enabled, enabled_language_codes
  FROM dbo.StoreHubTenants
 WHERE tenant_key = ?
""",
            key,
        )
        row = cur.fetchone()
    if not row:
        return {"tenant_key": key, "display_name": DEFAULT_TENANT_NAME, "database_name": DB_NAME, "is_active": True, "ai_enabled": True, "multilanguage_enabled": False, "enabled_language_codes": "it"}
    return {
        "tenant_key": str(row[0] or key),
        "display_name": str(row[1] or DEFAULT_TENANT_NAME),
        "database_name": str(row[2] or DB_NAME),
        "is_active": bool(row[3]),
        "sql_server": str(row[4] or ""),
        "sql_user": str(row[5] or ""),
        "storage_type": str(row[6] or "local_vm"),
        "storage_base_path": str(row[7] or ""),
        "sharepoint_sharing_url": str(row[8] or ""),
        "sharepoint_base_path": str(row[9] or ""),
        "notes": str(row[10] or ""),
        "master_can_admin": bool(row[11]),
        "tenant_admin_enabled": bool(row[12]),
        "max_users": int(row[13]) if row[13] is not None else None,
        "max_stores": int(row[14]) if row[14] is not None else None,
        "scheduling_enabled": bool(row[15]),
        "ai_enabled": bool(row[16]),
        "multilanguage_enabled": bool(row[17]),
        "enabled_language_codes": str(row[18] or "it"),
    }


def get_tenant(tenant_key: str | None = None) -> Dict[str, Any]:
    ensure_tenant_schema()
    key = str(tenant_key or current_tenant_key()).strip() or current_tenant_key()
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT TOP 1 tenant_key, display_name, database_name, is_active,
       sql_server, sql_user, storage_type, storage_base_path,
       sharepoint_sharing_url, sharepoint_base_path, notes,
       master_can_admin, tenant_admin_enabled, max_users, max_stores, scheduling_enabled, ai_enabled,
       multilanguage_enabled, enabled_language_codes
  FROM dbo.StoreHubTenants
 WHERE tenant_key = ?
""",
            key,
        )
        row = cur.fetchone()
    if not row and key == current_tenant_key():
        return get_current_tenant()
    if not row:
        return {
            "tenant_key": key,
            "display_name": key,
            "database_name": DB_NAME,
            "is_active": True,
            "sql_server": "",
            "sql_user": "",
            "storage_type": "local_vm",
            "storage_base_path": "",
            "sharepoint_sharing_url": "",
            "sharepoint_base_path": "",
            "notes": "",
            "master_can_admin": False,
            "tenant_admin_enabled": True,
            "max_users": None,
            "max_stores": None,
            "scheduling_enabled": False,
            "ai_enabled": True,
            "multilanguage_enabled": False,
            "enabled_language_codes": "it",
        }
    return {
        "tenant_key": str(row[0] or key),
        "display_name": str(row[1] or key),
        "database_name": str(row[2] or DB_NAME),
        "is_active": bool(row[3]),
        "sql_server": str(row[4] or ""),
        "sql_user": str(row[5] or ""),
        "storage_type": str(row[6] or "local_vm"),
        "storage_base_path": str(row[7] or ""),
        "sharepoint_sharing_url": str(row[8] or ""),
        "sharepoint_base_path": str(row[9] or ""),
        "notes": str(row[10] or ""),
        "master_can_admin": bool(row[11]),
        "tenant_admin_enabled": bool(row[12]),
        "max_users": int(row[13]) if row[13] is not None else None,
        "max_stores": int(row[14]) if row[14] is not None else None,
        "scheduling_enabled": bool(row[15]),
        "ai_enabled": bool(row[16]),
        "multilanguage_enabled": bool(row[17]),
        "enabled_language_codes": str(row[18] or "it"),
    }


def list_tenants() -> List[Dict[str, Any]]:
    ensure_current_tenant()
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT tenant_key, display_name, database_name, is_active,
       sql_server, sql_user, storage_type, storage_base_path,
       sharepoint_sharing_url, sharepoint_base_path, notes,
       master_can_admin, tenant_admin_enabled, max_users, max_stores, scheduling_enabled, ai_enabled,
       multilanguage_enabled, enabled_language_codes
  FROM dbo.StoreHubTenants
 ORDER BY display_name, tenant_key
"""
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def save_current_tenant_name(display_name: str) -> Dict[str, Any]:
    ensure_tenant_schema()
    key = current_tenant_key()
    name = str(display_name or "").strip() or DEFAULT_TENANT_NAME
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenants
   SET display_name = ?,
       database_name = COALESCE(database_name, ?),
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
""",
            name,
            DB_NAME,
            key,
        )
        if not cur.rowcount:
            cur.execute(
                """
INSERT INTO dbo.StoreHubTenants (tenant_key, display_name, database_name)
VALUES (?, ?, ?)
""",
                key,
                name,
                DB_NAME,
            )
        conn.commit()
    return get_current_tenant()


def save_tenant_name(tenant_key: str, display_name: str) -> Dict[str, Any]:
    ensure_tenant_schema()
    key = str(tenant_key or current_tenant_key()).strip() or current_tenant_key()
    name = str(display_name or "").strip() or key
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenants
   SET display_name = ?,
       database_name = COALESCE(database_name, ?),
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
""",
            name,
            DB_NAME,
            key,
        )
        if not cur.rowcount:
            cur.execute(
                """
INSERT INTO dbo.StoreHubTenants (tenant_key, display_name, database_name)
VALUES (?, ?, ?)
""",
                key,
                name,
                DB_NAME,
            )
        conn.commit()
    return get_tenant(key)


def _normalize_tenant_key(value: str) -> str:
    key = str(value or "").strip().lower()
    key = re.sub(r"[^a-z0-9_-]+", "_", key)
    key = re.sub(r"_+", "_", key).strip("_")
    if not key:
        raise ValueError("Chiave tenant obbligatoria")
    if len(key) > 120:
        raise ValueError("Chiave tenant troppo lunga")
    return key


def save_tenant(
    *,
    tenant_key: str,
    display_name: str,
    database_name: str | None = None,
    is_active: bool = True,
    sql_server: str | None = None,
    sql_user: str | None = None,
    storage_type: str | None = None,
    storage_base_path: str | None = None,
    sharepoint_sharing_url: str | None = None,
    sharepoint_base_path: str | None = None,
    notes: str | None = None,
    tenant_admin_enabled: bool = True,
    master_can_admin: bool = False,
    max_users: int | None = None,
    max_stores: int | None = None,
    scheduling_enabled: bool = False,
    ai_enabled: bool = False,
    multilanguage_enabled: bool = False,
    enabled_language_codes: str | list[str] | tuple[str, ...] | None = None,
) -> Dict[str, Any]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    name = str(display_name or "").strip() or key
    db_name = str(database_name or "").strip() or DB_NAME
    storage = str(storage_type or "local_vm").strip().lower() or "local_vm"
    if storage not in STORAGE_PROVIDERS:
        storage = "local_vm"
    max_users_val = int(max_users) if max_users not in (None, "") else None
    max_stores_val = int(max_stores) if max_stores not in (None, "") else None
    tenant_admin = bool(tenant_admin_enabled)
    master_admin = bool(master_can_admin) or not tenant_admin
    multilanguage = bool(multilanguage_enabled)
    language_codes = _normalize_language_codes(enabled_language_codes, multilanguage_enabled=multilanguage)

    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenants
   SET display_name = ?,
       database_name = ?,
       is_active = ?,
       sql_server = ?,
       sql_user = ?,
       storage_type = ?,
       storage_base_path = ?,
       sharepoint_sharing_url = ?,
       sharepoint_base_path = ?,
       notes = ?,
       tenant_admin_enabled = ?,
       master_can_admin = ?,
       max_users = ?,
       max_stores = ?,
       scheduling_enabled = ?,
       ai_enabled = ?,
       multilanguage_enabled = ?,
       enabled_language_codes = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
""",
            name,
            db_name,
            1 if is_active else 0,
            str(sql_server or "").strip() or None,
            str(sql_user or "").strip() or None,
            storage,
            str(storage_base_path or "").strip() or None,
            str(sharepoint_sharing_url or "").strip() or None,
            str(sharepoint_base_path or "").strip() or None,
            str(notes or "").strip() or None,
            1 if tenant_admin else 0,
            1 if master_admin else 0,
            max_users_val,
            max_stores_val,
            1 if scheduling_enabled else 0,
            1 if ai_enabled else 0,
            1 if multilanguage else 0,
            language_codes,
            key,
        )
        if not cur.rowcount:
            cur.execute(
                """
INSERT INTO dbo.StoreHubTenants
  (tenant_key, display_name, database_name, is_active, sql_server, sql_user, storage_type, storage_base_path,
   sharepoint_sharing_url, sharepoint_base_path, notes, tenant_admin_enabled, master_can_admin, max_users, max_stores, scheduling_enabled, ai_enabled,
   multilanguage_enabled, enabled_language_codes)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
                key,
                name,
                db_name,
                1 if is_active else 0,
                str(sql_server or "").strip() or None,
                str(sql_user or "").strip() or None,
                storage,
                str(storage_base_path or "").strip() or None,
                str(sharepoint_sharing_url or "").strip() or None,
                str(sharepoint_base_path or "").strip() or None,
                str(notes or "").strip() or None,
                1 if tenant_admin else 0,
                1 if master_admin else 0,
                max_users_val,
                max_stores_val,
                1 if scheduling_enabled else 0,
                1 if ai_enabled else 0,
                1 if multilanguage else 0,
                language_codes,
            )
        conn.commit()
    return get_tenant(key)


def set_tenant_active(tenant_key: str, active: bool) -> bool:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenants
   SET is_active = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
""",
            1 if active else 0,
            key,
        )
        ok = bool(cur.rowcount)
        conn.commit()
    return ok


def list_tenant_store_codes(tenant_key: str, *, active_only: bool = True) -> set[str]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        if active_only:
            cur.execute(
                """
SELECT store_code
  FROM dbo.StoreHubTenantStores
 WHERE tenant_key = ?
   AND is_active = 1
""",
                key,
            )
        else:
            cur.execute(
                """
SELECT store_code
  FROM dbo.StoreHubTenantStores
 WHERE tenant_key = ?
""",
                key,
            )
        return {str(row[0] or "").strip() for row in cur.fetchall() if str(row[0] or "").strip()}


def list_tenant_stores(tenant_key: str, *, active_only: bool = True) -> List[Dict[str, Any]]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        sql = """
SELECT tenant_key, store_code, store_name, is_active, created_at, updated_at
  FROM dbo.StoreHubTenantStores
 WHERE tenant_key = ?
"""
        params: list[Any] = [key]
        if active_only:
            sql += "   AND is_active = 1\n"
        sql += " ORDER BY store_name, store_code"
        cur.execute(sql, *params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def upsert_tenant_store(
    *,
    tenant_key: str,
    store_code: str,
    store_name: str | None = None,
    is_active: bool = True,
) -> Dict[str, Any]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    code = str(store_code or "").strip()
    if not code:
        raise ValueError("Codice store obbligatorio")
    name = str(store_name or "").strip() or None
    tenant = get_tenant(key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT is_active FROM dbo.StoreHubTenantStores WHERE tenant_key = ? AND store_code = ?",
            key,
            code,
        )
        existing = cur.fetchone()
        currently_active = bool(existing[0]) if existing else False
        max_stores = tenant.get("max_stores")
        if is_active and not currently_active and max_stores is not None:
            cur.execute(
                "SELECT COUNT(1) FROM dbo.StoreHubTenantStores WHERE tenant_key = ? AND is_active = 1",
                key,
            )
            active_count = int(cur.fetchone()[0] or 0)
            if active_count >= int(max_stores):
                raise ValueError(f"Limite store tenant raggiunto ({max_stores}).")
        cur.execute(
            """
UPDATE dbo.StoreHubTenantStores
   SET store_name = COALESCE(?, store_name),
       is_active = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
   AND store_code = ?
""",
            name,
            1 if is_active else 0,
            key,
            code,
        )
        if not cur.rowcount:
            cur.execute(
                """
INSERT INTO dbo.StoreHubTenantStores (tenant_key, store_code, store_name, is_active)
VALUES (?, ?, ?, ?)
""",
                key,
                code,
                name,
                1 if is_active else 0,
            )
        conn.commit()
    return {"tenant_key": key, "store_code": code, "store_name": name, "is_active": bool(is_active)}


def delete_tenant_store(tenant_key: str, store_code: str) -> bool:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    code = str(store_code or "").strip()
    if not code:
        raise ValueError("Codice store obbligatorio")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
DELETE FROM dbo.StoreHubTenantStores
 WHERE tenant_key = ?
   AND store_code = ?
""",
            key,
            code,
        )
        ok = bool(cur.rowcount)
        conn.commit()
    return ok


def cleanup_shadow_default_tenant_stores(tenant_key: str, valid_store_codes: set[str]) -> int:
    """Rimuove dal default gli store duplicati creati dal vecchio flusso globale.

    Cancella solo codici presenti anche in altri tenant e non presenti
    nell'anagrafica store del database default.
    """
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    valid_codes = {str(code or "").strip() for code in (valid_store_codes or set()) if str(code or "").strip()}
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT DISTINCT d.store_code
  FROM dbo.StoreHubTenantStores d
 WHERE d.tenant_key = ?
   AND EXISTS (
       SELECT 1
         FROM dbo.StoreHubTenantStores x
        WHERE x.store_code = d.store_code
          AND x.tenant_key <> d.tenant_key
   )
""",
            key,
        )
        shadow_codes = [
            str(row[0] or "").strip()
            for row in cur.fetchall()
            if str(row[0] or "").strip() and str(row[0] or "").strip() not in valid_codes
        ]
        for code in shadow_codes:
            cur.execute(
                """
DELETE FROM dbo.StoreHubTenantStores
 WHERE tenant_key = ?
   AND store_code = ?
""",
                key,
                code,
            )
        conn.commit()
        return len(shadow_codes)


def seed_tenant_stores(tenant_key: str, stores: List[Dict[str, Any]], *, only_if_empty: bool = True) -> int:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    rows = stores or []
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        if only_if_empty:
            cur.execute("SELECT COUNT(1) FROM dbo.StoreHubTenantStores WHERE tenant_key = ?", key)
            if int(cur.fetchone()[0] or 0) > 0:
                return 0
        count = 0
        for row in rows:
            code = str((row or {}).get("code") or (row or {}).get("store_code") or "").strip()
            if not code:
                continue
            name = str((row or {}).get("name") or (row or {}).get("store_name") or "").strip() or None
            active = bool((row or {}).get("is_active", True))
            cur.execute(
                """
IF EXISTS (SELECT 1 FROM dbo.StoreHubTenantStores WHERE tenant_key = ? AND store_code = ?)
BEGIN
  UPDATE dbo.StoreHubTenantStores
     SET store_name = COALESCE(?, store_name),
         is_active = ?,
         updated_at = SYSUTCDATETIME()
   WHERE tenant_key = ?
     AND store_code = ?
END
ELSE
BEGIN
  INSERT INTO dbo.StoreHubTenantStores (tenant_key, store_code, store_name, is_active)
  VALUES (?, ?, ?, ?)
END
""",
                key,
                code,
                name,
                1 if active else 0,
                key,
                code,
                key,
                code,
                name,
                1 if active else 0,
            )
            count += 1
        conn.commit()
        return count


def _normalize_category_key(value: str) -> str:
    key = str(value or "").strip().lower()
    key = re.sub(r"[^a-z0-9_-]+", "_", key)
    key = re.sub(r"_+", "_", key).strip("_")
    if not key:
        raise ValueError("Categoria storage obbligatoria")
    if len(key) > 120:
        raise ValueError("Categoria storage troppo lunga")
    return key


def _normalize_storage_provider(value: str) -> str:
    provider = str(value or "local_vm").strip().lower() or "local_vm"
    if provider not in STORAGE_PROVIDERS:
        provider = "local_vm"
    return provider


def seed_default_storage_rules(tenant_key: str | None = None) -> None:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key or current_tenant_key())
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        for category_key, display_name, provider, base_path, bucket_name, notes in DEFAULT_STORAGE_RULES:
            cur.execute(
                """
IF NOT EXISTS (
  SELECT 1
    FROM dbo.StoreHubTenantStorageRules
   WHERE tenant_key = ?
     AND category_key = ?
)
BEGIN
  INSERT INTO dbo.StoreHubTenantStorageRules
    (tenant_key, category_key, display_name, provider, base_path, bucket_name, is_active, notes)
  VALUES (?, ?, ?, ?, ?, ?, 1, ?)
END
""",
                key,
                category_key,
                key,
                category_key,
                display_name,
                provider,
                base_path or None,
                bucket_name or None,
                notes or None,
            )
        conn.commit()


def seed_default_tenant_modules(tenant_key: str | None = None) -> None:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key or current_tenant_key())
    is_default = key == current_tenant_key() or key == "default"
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        for module_key, display_name, default_enabled in TENANT_MODULE_DEFAULTS:
            enabled = True if is_default else bool(default_enabled)
            cur.execute(
                """
MERGE dbo.StoreHubTenantModules AS tgt
USING (SELECT ? AS tenant_key, ? AS module_key) AS src
ON tgt.tenant_key = src.tenant_key AND tgt.module_key = src.module_key
WHEN NOT MATCHED THEN
  INSERT (tenant_key, module_key, display_name, is_enabled)
  VALUES (?, ?, ?, ?);
""",
                key,
                module_key,
                key,
                module_key,
                display_name,
                1 if enabled else 0,
            )
        conn.commit()


def is_tenant_module_enabled(module_key: str, tenant_key: str | None = None) -> bool:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key or current_tenant_key())
    seed_default_tenant_modules(key)
    mkey = str(module_key or "").strip()
    if not mkey:
        return True
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT TOP 1 is_enabled
  FROM dbo.StoreHubTenantModules
 WHERE tenant_key = ?
   AND module_key = ?
""",
            key,
            mkey,
        )
        row = cur.fetchone()
    if row is None:
        return True
    return bool(row[0])


def list_tenant_modules(tenant_key: str) -> List[Dict[str, Any]]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    seed_default_tenant_modules(key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT tenant_key, module_key, display_name, is_enabled, created_at, updated_at
  FROM dbo.StoreHubTenantModules
 WHERE tenant_key = ?
 ORDER BY display_name, module_key
""",
            key,
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def set_tenant_module_enabled(tenant_key: str, module_key: str, enabled: bool) -> bool:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    seed_default_tenant_modules(key)
    mkey = str(module_key or "").strip()
    if not mkey:
        raise ValueError("Modulo obbligatorio")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenantModules
   SET is_enabled = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
   AND module_key = ?
""",
            1 if enabled else 0,
            key,
            mkey,
        )
        ok = bool(cur.rowcount)
        conn.commit()
    return ok


def list_storage_rules(tenant_key: str) -> List[Dict[str, Any]]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    seed_default_storage_rules(key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT row_uuid, tenant_key, category_key, display_name, provider, base_path,
       sharepoint_sharing_url, bucket_name, is_active, notes, created_at, updated_at
  FROM dbo.StoreHubTenantStorageRules
 WHERE tenant_key = ?
 ORDER BY display_name, category_key
""",
            key,
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_storage_rule(tenant_key: str, category_key: str) -> Dict[str, Any]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    category = _normalize_category_key(category_key)
    seed_default_storage_rules(key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT TOP 1 row_uuid, tenant_key, category_key, display_name, provider, base_path,
       sharepoint_sharing_url, bucket_name, is_active, notes, created_at, updated_at
  FROM dbo.StoreHubTenantStorageRules
 WHERE tenant_key = ?
   AND category_key = ?
""",
            key,
            category,
        )
        row = cur.fetchone()
        if not row:
            return {}
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, row))


def save_storage_rule(
    *,
    tenant_key: str,
    category_key: str,
    display_name: str,
    provider: str,
    base_path: str | None = None,
    sharepoint_sharing_url: str | None = None,
    bucket_name: str | None = None,
    is_active: bool = True,
    notes: str | None = None,
) -> Dict[str, Any]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    category = _normalize_category_key(category_key)
    name = str(display_name or "").strip() or category
    normalized_provider = _normalize_storage_provider(provider)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenantStorageRules
   SET display_name = ?,
       provider = ?,
       base_path = ?,
       sharepoint_sharing_url = ?,
       bucket_name = ?,
       is_active = ?,
       notes = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
   AND category_key = ?
""",
            name,
            normalized_provider,
            str(base_path or "").strip() or None,
            str(sharepoint_sharing_url or "").strip() or None,
            str(bucket_name or "").strip() or None,
            1 if is_active else 0,
            str(notes or "").strip() or None,
            key,
            category,
        )
        if not cur.rowcount:
            cur.execute(
                """
INSERT INTO dbo.StoreHubTenantStorageRules
  (tenant_key, category_key, display_name, provider, base_path, sharepoint_sharing_url, bucket_name, is_active, notes)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
                key,
                category,
                name,
                normalized_provider,
                str(base_path or "").strip() or None,
                str(sharepoint_sharing_url or "").strip() or None,
                str(bucket_name or "").strip() or None,
                1 if is_active else 0,
                str(notes or "").strip() or None,
            )
        conn.commit()
    return get_storage_rule(key, category)


def set_storage_rule_active(tenant_key: str, category_key: str, active: bool) -> bool:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    category = _normalize_category_key(category_key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenantStorageRules
   SET is_active = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
   AND category_key = ?
""",
            1 if active else 0,
            key,
            category,
        )
        ok = bool(cur.rowcount)
        conn.commit()
    return ok


def seed_default_store_field_config(tenant_key: str | None = None) -> None:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key or current_tenant_key())
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        for field_key, display_name, field_group in STORE_REGISTRY_FIELDS:
            cur.execute(
                """
IF NOT EXISTS (
  SELECT 1
    FROM dbo.StoreHubTenantStoreFieldConfig
   WHERE tenant_key = ?
     AND field_key = ?
)
BEGIN
  INSERT INTO dbo.StoreHubTenantStoreFieldConfig
    (tenant_key, field_key, display_name, field_group, is_visible, is_required)
  VALUES (?, ?, ?, ?, 1, 0)
END
""",
                key,
                field_key,
                key,
                field_key,
                display_name,
                field_group,
            )
        conn.commit()


def list_store_field_configs(tenant_key: str) -> List[Dict[str, Any]]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    seed_default_store_field_config(key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT field_key, display_name, field_group, is_visible, is_required
  FROM dbo.StoreHubTenantStoreFieldConfig
 WHERE tenant_key = ?
 ORDER BY field_group, display_name
""",
            key,
        )
        cols = [c[0] for c in cur.description]
        allowed = {f[0] for f in STORE_REGISTRY_FIELDS}
        return [dict(zip(cols, row)) for row in cur.fetchall() if str(row[0] or "").strip() in allowed]


def set_store_field_visible(tenant_key: str, field_key: str, visible: bool) -> bool:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    field = str(field_key or "").strip()
    allowed = {f[0] for f in STORE_REGISTRY_FIELDS}
    if field not in allowed:
        raise ValueError("Campo store non configurabile.")
    seed_default_store_field_config(key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenantStoreFieldConfig
   SET is_visible = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
   AND field_key = ?
   AND is_required = 0
""",
            1 if visible else 0,
            key,
            field,
        )
        ok = bool(cur.rowcount)
        conn.commit()
        return ok


def set_store_field_group_visible(tenant_key: str, field_group: str, visible: bool) -> int:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    group = str(field_group or "").strip()
    allowed_groups = {f[2] for f in STORE_REGISTRY_FIELDS}
    if group not in allowed_groups:
        raise ValueError("Gruppo campi store non configurabile.")
    seed_default_store_field_config(key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenantStoreFieldConfig
   SET is_visible = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
   AND field_group = ?
   AND is_required = 0
""",
            1 if visible else 0,
            key,
            group,
        )
        count = int(cur.rowcount or 0)
        conn.commit()
        return count


def tenant_status(tenant_key: str) -> Dict[str, Any]:
    tenant = get_tenant(tenant_key)
    out = {
        "tenant": tenant,
        "db_ok": False,
        "db_error": "",
        "storage_ok": False,
        "storage_error": "",
        "ready_ok": False,
        "checks": [],
    }
    db_name = str(tenant.get("database_name") or DB_NAME).strip() or DB_NAME
    schema_tables = [
        "StoreHubStoreRegistry",
        "DatiDelivery",
        "datiinventario",
        "DatiTX",
        "StoreHubDailySales",
        "DELIVERY_WEEKLY",
        "DeliveryProviders",
        "StoreHubSalesForecast",
        "KPI_PERIOD_NOTES",
        "STAFF",
        "STAFF_P",
        "ELENCHI",
        "DATIPRIMANOTA",
        "SPESE",
        "StoreHubCashStatementSections",
        "StoreHubCashStatementFields",
        "StoreHubIpraticoIntegrationConfig",
        "StoreHubIpraticoPaymentMappings",
        "DISTINTA_CASSA_IPRATICO",
        "DISTINTA_CASSA_FOTO",
        "DISTINTA_CASSA_CONVALIDE",
        "CruscottoPnlStoreVisibleMonths",
        "FINANCE_STORE_CODE_CATALOG",
        "FINANCE_STORE_CODE_ASSIGNMENTS",
        "Fornitori",
        "FornitoriContatti",
        "FornitoriContattiStores",
        "ListiniPrezzi",
        "ListiniElenchi",
        "ListiniStore",
        "ListinoTipi",
        "ListinoGruppi",
        "OrdiniFornitori",
        "OrdiniFornitoriRighe",
        "OrdiniFornitoriLog",
        "FornitoriUtenti",
        "MboAreaManagers",
        "StoreHubTranslations",
    ]
    existing_tables: set[str] = set()
    try:
        with get_connection_sqlserver_database(db_name, read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            for table_name in schema_tables:
                cur.execute("SELECT OBJECT_ID(?)", f"dbo.{table_name}")
                if cur.fetchone()[0]:
                    existing_tables.add(table_name)
            listino_default_ok = False
            if "ListiniElenchi" in existing_tables:
                cur.execute("SELECT COUNT(1) FROM dbo.ListiniElenchi WHERE nome = 'Listino DOS' AND is_default = 1")
                listino_default_ok = int(cur.fetchone()[0] or 0) > 0
        out["db_ok"] = True
    except Exception as e:
        out["db_error"] = str(e)
        listino_default_ok = False

    storage_type = str(tenant.get("storage_type") or "local_vm").strip().lower()
    storage_path = str(tenant.get("storage_base_path") or "").strip()
    if storage_type == "local_vm":
        if not storage_path:
            out["storage_ok"] = True
        else:
            try:
                os.makedirs(storage_path, exist_ok=True)
                out["storage_ok"] = os.path.isdir(storage_path)
                if not out["storage_ok"]:
                    out["storage_error"] = "Cartella non disponibile"
            except Exception as e:
                out["storage_error"] = str(e)
    elif storage_type == "sharepoint":
        sharing_url = str(tenant.get("sharepoint_sharing_url") or "").strip()
        if sharing_url:
            out["storage_ok"] = True
        else:
            # Compatibilità con il tenant attuale: le foto possono essere ancora
            # configurate tramite le cartelle SharePoint specifiche per categoria.
            out["storage_ok"] = True
    else:
        out["storage_ok"] = True
    try:
        users = list_tenant_users(str(tenant.get("tenant_key") or tenant_key))
    except Exception:
        users = []
    try:
        stores = list_tenant_stores(str(tenant.get("tenant_key") or tenant_key), active_only=True)
    except Exception:
        stores = []
    try:
        rules = list_storage_rules(str(tenant.get("tenant_key") or tenant_key))
    except Exception:
        rules = []

    tenant_admin_enabled = bool(tenant.get("tenant_admin_enabled", True))
    active_admins = [
        row for row in users
        if bool(row.get("is_active")) and str(row.get("tenant_role") or "").strip().lower() == "admin"
    ]
    effective_active_admins = active_admins if tenant_admin_enabled else []
    active_users = [row for row in users if bool(row.get("is_active"))]
    max_users = tenant.get("max_users")
    max_stores = tenant.get("max_stores")
    active_storage_rules = [row for row in rules if bool(row.get("is_active"))]
    required_storage = {"rendiconto_spese", "rendiconto_versamenti", "rendiconto_distinta_cassa", "links"}
    active_storage_keys = {str(row.get("category_key") or "").strip() for row in active_storage_rules}
    schema_ok = out["db_ok"] and all(table_name in existing_tables for table_name in schema_tables)

    checks = [
        {
            "key": "database",
            "label": "Database configurato e raggiungibile",
            "ok": bool(out["db_ok"]),
            "detail": out["db_error"],
        },
        {
            "key": "schema",
            "label": "Schema base inizializzato",
            "ok": bool(schema_ok),
            "detail": "" if schema_ok else "Tabelle mancanti: " + ", ".join(t for t in schema_tables if t not in existing_tables),
        },
        {
            "key": "storage",
            "label": "Storage tenant valido",
            "ok": bool(out["storage_ok"]),
            "detail": out["storage_error"],
        },
        {
            "key": "storage_rules",
            "label": "Categorie storage base attive",
            "ok": required_storage.issubset(active_storage_keys),
            "detail": "" if required_storage.issubset(active_storage_keys) else "Categorie mancanti: " + ", ".join(sorted(required_storage - active_storage_keys)),
        },
        {
            "key": "admin",
            "label": "Admin tenant o Master abilitato",
            "ok": bool(effective_active_admins) or bool(tenant.get("master_can_admin")),
            "detail": (
                "Master abilitato come admin"
                if bool(tenant.get("master_can_admin"))
                else f"{len(effective_active_admins)} admin attivi"
            ),
        },
        {
            "key": "tenant_admin_mode",
            "label": "Modalita amministrazione coerente",
            "ok": bool(tenant_admin_enabled) or bool(tenant.get("master_can_admin")),
            "detail": (
                "Admin tenant abilitati"
                if tenant_admin_enabled
                else "Admin tenant disattivati: gli admin esistenti entrano come user"
            ),
        },
        {
            "key": "user_limit",
            "label": "Limite utenti rispettato",
            "ok": max_users is None or len(active_users) <= int(max_users),
            "detail": (
                f"{len(active_users)} utenti attivi"
                if max_users is None
                else f"{len(active_users)} / {int(max_users)} utenti attivi"
            ),
        },
        {
            "key": "stores",
            "label": "Almeno uno store attivo associato",
            "ok": bool(stores),
            "detail": f"{len(stores)} store attivi",
        },
        {
            "key": "store_limit",
            "label": "Limite store rispettato",
            "ok": max_stores is None or len(stores) <= int(max_stores),
            "detail": (
                f"{len(stores)} store attivi"
                if max_stores is None
                else f"{len(stores)} / {int(max_stores)} store attivi"
            ),
        },
        {
            "key": "default_price_list",
            "label": "Listino default presente",
            "ok": bool(listino_default_ok),
            "detail": "" if listino_default_ok else "Listino DOS non trovato come default",
        },
        {
            "key": "ai_support",
            "label": "Support AI tenant",
            "ok": True,
            "detail": "Attivo" if bool(tenant.get("ai_enabled")) else "Spento",
        },
        {
            "key": "multilanguage",
            "label": "Supporto multi lingua",
            "ok": True,
            "detail": (
                str(tenant.get("enabled_language_codes") or "it")
                if bool(tenant.get("multilanguage_enabled"))
                else "Solo italiano"
            ),
        },
    ]
    out["checks"] = checks
    out["ready_ok"] = all(bool(row.get("ok")) for row in checks)
    return out


def _normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("Email utente obbligatoria")
    return email


def _normalize_tenant_role(value: str) -> str:
    role = str(value or "user").strip().lower() or "user"
    if role not in {"master", "admin", "supervisor", "user", "fornitore"}:
        role = "user"
    return role


def list_tenant_users(tenant_key: str) -> List[Dict[str, Any]]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT row_uuid, tenant_key, user_id, email, tenant_role, is_active, created_at, updated_at
  FROM dbo.StoreHubTenantUsers
 WHERE tenant_key = ?
 ORDER BY email
""",
            key,
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def upsert_tenant_user(
    *,
    tenant_key: str,
    email: str,
    user_id: str | None = None,
    tenant_role: str = "user",
    is_active: bool = True,
) -> Dict[str, Any]:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    mail = _normalize_email(email)
    role = _normalize_tenant_role(tenant_role)
    uid = str(user_id or "").strip() or None
    tenant = get_tenant(key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT is_active FROM dbo.StoreHubTenantUsers WHERE tenant_key = ? AND email = ?",
            key,
            mail,
        )
        existing = cur.fetchone()
        currently_active = bool(existing[0]) if existing else False
        max_users = tenant.get("max_users")
        if is_active and not currently_active and max_users is not None:
            cur.execute(
                "SELECT COUNT(1) FROM dbo.StoreHubTenantUsers WHERE tenant_key = ? AND is_active = 1",
                key,
            )
            active_count = int(cur.fetchone()[0] or 0)
            if active_count >= int(max_users):
                raise ValueError(f"Limite utenti tenant raggiunto ({max_users}).")
        cur.execute(
            """
UPDATE dbo.StoreHubTenantUsers
   SET user_id = COALESCE(?, user_id),
       tenant_role = ?,
       is_active = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
   AND email = ?
""",
            uid,
            role,
            1 if is_active else 0,
            key,
            mail,
        )
        if not cur.rowcount:
            cur.execute(
                """
INSERT INTO dbo.StoreHubTenantUsers (tenant_key, user_id, email, tenant_role, is_active)
VALUES (?, ?, ?, ?, ?)
""",
                key,
                uid,
                mail,
                role,
                1 if is_active else 0,
            )
        conn.commit()
    rows = list_tenant_users(key)
    return next((r for r in rows if str(r.get("email") or "").lower() == mail), {})


def set_tenant_user_active(tenant_key: str, email: str, active: bool) -> bool:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    mail = _normalize_email(email)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubTenantUsers
   SET is_active = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
   AND email = ?
""",
            1 if active else 0,
            key,
            mail,
        )
        ok = bool(cur.rowcount)
        conn.commit()
    return ok


def delete_tenant_user(tenant_key: str, email: str) -> bool:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    mail = _normalize_email(email)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
DELETE FROM dbo.StoreHubTenantUsers
 WHERE tenant_key = ?
   AND email = ?
""",
            key,
            mail,
        )
        ok = bool(cur.rowcount)
        conn.commit()
    return ok


def remove_platform_masters_from_tenant(tenant_key: str) -> int:
    ensure_tenant_schema()
    key = _normalize_tenant_key(tenant_key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
DELETE FROM dbo.StoreHubTenantUsers
 WHERE tenant_key = ?
   AND LOWER(tenant_role) = 'master'
""",
            key,
        )
        count = int(cur.rowcount or 0)
        conn.commit()
    return count


def get_user_tenants(*, user_id: str | None = None, email: str | None = None) -> List[Dict[str, Any]]:
    ensure_tenant_schema()
    uid = str(user_id or "").strip()
    mail = str(email or "").strip().lower()
    where = []
    params: list[Any] = []
    if uid:
        where.append("tu.user_id = ?")
        params.append(uid)
    if mail:
        where.append("LOWER(tu.email) = ?")
        params.append(mail)
    if not where:
        return []
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
SELECT tu.tenant_key, t.display_name, t.database_name, t.is_active AS tenant_active,
       t.tenant_admin_enabled, tu.tenant_role, tu.is_active AS user_active
  FROM dbo.StoreHubTenantUsers tu
  LEFT JOIN dbo.StoreHubTenants t
    ON t.tenant_key = tu.tenant_key
 WHERE ({" OR ".join(where)})
 ORDER BY t.display_name, tu.tenant_key
""",
            *params,
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_active_user_tenants(*, user_id: str | None = None, email: str | None = None) -> List[Dict[str, Any]]:
    rows = get_user_tenants(user_id=user_id, email=email)
    return [
        row for row in rows
        if bool(row.get("tenant_active", True)) and bool(row.get("user_active", True))
    ]


def resolve_user_tenant(*, user_id: str | None = None, email: str | None = None, preferred_tenant_key: str | None = None) -> Dict[str, Any] | None:
    rows = get_active_user_tenants(user_id=user_id, email=email)
    if not rows:
        return None
    preferred = str(preferred_tenant_key or "").strip().lower()
    if preferred:
        for row in rows:
            if str(row.get("tenant_key") or "").strip().lower() == preferred:
                return row
    return rows[0]
