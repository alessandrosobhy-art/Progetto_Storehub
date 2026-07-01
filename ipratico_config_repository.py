from __future__ import annotations

import os
from typing import Any, Dict, List

from app_db import get_connection_sqlserver_database, get_storehub_database_name


DEFAULT_TENANT_KEY = os.getenv("STOREHUB_TENANT_KEY") or "default"


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def _tenant(tenant_key: str | None = None) -> str:
    if tenant_key:
        return str(tenant_key or "").strip() or "default"
    try:
        from flask import has_request_context, session

        if has_request_context():
            key = str(session.get("tenant_key") or "").strip()
            if key:
                return key
    except Exception:
        pass
    return (DEFAULT_TENANT_KEY or "default").strip() or "default"


def ensure_ipratico_config_schema() -> None:
    sql = """
IF OBJECT_ID('dbo.StoreHubIpraticoIntegrationConfig','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubIpraticoIntegrationConfig (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    enabled BIT NOT NULL DEFAULT 1,
    test_days INT NOT NULL DEFAULT 4,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubIpraticoIntegrationConfig_tenant
    ON dbo.StoreHubIpraticoIntegrationConfig(tenant_key);
END

IF OBJECT_ID('dbo.StoreHubIpraticoPaymentMappings','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubIpraticoPaymentMappings (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    method_key NVARCHAR(255) NOT NULL,
    method_label NVARCHAR(500) NOT NULL,
    target_section_key NVARCHAR(120) NULL,
    target_field_key NVARCHAR(160) NULL,
    target_label NVARCHAR(255) NULL,
    target_tipo NVARCHAR(30) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    notes NVARCHAR(1000) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubIpraticoPaymentMappings_tenant_method
    ON dbo.StoreHubIpraticoPaymentMappings(tenant_key, method_key);
END
"""
    # Idempotent DDL/schema checks should run in autocommit mode; otherwise
    # pooled pyodbc connections can stay sleeping with an open transaction.
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(sql)


def get_ipratico_config(tenant_key: str | None = None) -> Dict[str, Any]:
    ensure_ipratico_config_schema()
    tenant = _tenant(tenant_key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT TOP 1 tenant_key, enabled, test_days
  FROM dbo.StoreHubIpraticoIntegrationConfig
 WHERE tenant_key = ?
""",
            tenant,
        )
        row = cur.fetchone()
    if not row:
        enabled_default = tenant == "default"
        return {"tenant_key": tenant, "enabled": enabled_default, "test_days": 4}
    return {"tenant_key": str(row[0] or tenant), "enabled": bool(row[1]), "test_days": int(row[2] or 4)}


def save_ipratico_config(*, enabled: bool, test_days: int = 4, tenant_key: str | None = None) -> Dict[str, Any]:
    ensure_ipratico_config_schema()
    tenant = _tenant(tenant_key)
    days = max(1, min(int(test_days or 4), 14))
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubIpraticoIntegrationConfig
   SET enabled = ?, test_days = ?, updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ?
""",
            1 if enabled else 0,
            days,
            tenant,
        )
        if not cur.rowcount:
            cur.execute(
                """
INSERT INTO dbo.StoreHubIpraticoIntegrationConfig (tenant_key, enabled, test_days)
VALUES (?, ?, ?)
""",
                tenant,
                1 if enabled else 0,
                days,
            )
        conn.commit()
    return get_ipratico_config(tenant)


def list_ipratico_payment_mappings(tenant_key: str | None = None) -> List[Dict[str, Any]]:
    ensure_ipratico_config_schema()
    tenant = _tenant(tenant_key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT method_key, method_label, target_section_key, target_field_key, target_label,
       target_tipo, is_active, notes
  FROM dbo.StoreHubIpraticoPaymentMappings
 WHERE tenant_key = ?
 ORDER BY method_label
""",
            tenant,
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def upsert_ipratico_payment_mappings(rows: List[Dict[str, Any]], tenant_key: str | None = None) -> int:
    ensure_ipratico_config_schema()
    tenant = _tenant(tenant_key)
    count = 0
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        for raw in rows or []:
            method_key = str((raw or {}).get("method_key") or "").strip()
            method_label = str((raw or {}).get("method_label") or "").strip()
            if not method_key or not method_label:
                continue
            target_section_key = str((raw or {}).get("target_section_key") or "").strip() or None
            target_field_key = str((raw or {}).get("target_field_key") or "").strip() or None
            target_label = str((raw or {}).get("target_label") or "").strip() or None
            target_tipo = str((raw or {}).get("target_tipo") or "").strip().upper() or None
            is_active = bool(target_section_key and target_field_key and (raw or {}).get("is_active", True))
            cur.execute(
                """
UPDATE dbo.StoreHubIpraticoPaymentMappings
   SET method_label = ?,
       target_section_key = ?,
       target_field_key = ?,
       target_label = ?,
       target_tipo = ?,
       is_active = ?,
       notes = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ? AND method_key = ?
""",
                method_label,
                target_section_key,
                target_field_key,
                target_label,
                target_tipo,
                1 if is_active else 0,
                str((raw or {}).get("notes") or "").strip() or None,
                tenant,
                method_key,
            )
            if not cur.rowcount:
                cur.execute(
                    """
INSERT INTO dbo.StoreHubIpraticoPaymentMappings (
    tenant_key, method_key, method_label, target_section_key, target_field_key,
    target_label, target_tipo, is_active, notes
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
                    tenant,
                    method_key,
                    method_label,
                    target_section_key,
                    target_field_key,
                    target_label,
                    target_tipo,
                    1 if is_active else 0,
                    str((raw or {}).get("notes") or "").strip() or None,
                )
            count += 1
        conn.commit()
    return count


def set_ipratico_payment_mapping_active(method_key: str, *, active: bool, tenant_key: str | None = None) -> bool:
    ensure_ipratico_config_schema()
    tenant = _tenant(tenant_key)
    key = str(method_key or "").strip()
    if not key:
        return False
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubIpraticoPaymentMappings
   SET is_active = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ? AND method_key = ?
""",
            1 if active else 0,
            tenant,
            key,
        )
        changed = bool(cur.rowcount)
        conn.commit()
    return changed


def delete_ipratico_payment_mapping(method_key: str, tenant_key: str | None = None) -> bool:
    ensure_ipratico_config_schema()
    tenant = _tenant(tenant_key)
    key = str(method_key or "").strip()
    if not key:
        return False
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
DELETE FROM dbo.StoreHubIpraticoPaymentMappings
 WHERE tenant_key = ? AND method_key = ?
""",
            tenant,
            key,
        )
        changed = bool(cur.rowcount)
        conn.commit()
    return changed
