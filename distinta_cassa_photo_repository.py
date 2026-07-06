from __future__ import annotations

from app_logging import log_swallowed
import os
from datetime import datetime
from typing import Optional
from uuid import uuid4

from app_db import get_connection_sqlserver_database, get_storehub_database_name
from tenant_config_repository import current_tenant_key


TABLE_NAME = "DISTINTA_CASSA_FOTO"


def _tenant(tenant_key: str | None = None) -> str:
    return str(tenant_key or current_tenant_key() or "default").strip() or "default"


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def ensure_distinta_cassa_photo_schema() -> None:
    conn = _conn(read_only=False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
IF OBJECT_ID('dbo.{TABLE_NAME}', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.{TABLE_NAME} (
        id INT IDENTITY(1,1) NOT NULL,
        row_uuid UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_{TABLE_NAME}_row_uuid DEFAULT NEWID(),
        tenant_key NVARCHAR(120) NOT NULL CONSTRAINT DF_{TABLE_NAME}_tenant_key DEFAULT 'default',
        site NVARCHAR(50) NOT NULL,
        data_iso DATE NOT NULL,
        foto_file NVARCHAR(255) NOT NULL,
        created_at DATETIME2(0) NOT NULL CONSTRAINT DF_{TABLE_NAME}_created_at DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(0) NOT NULL CONSTRAINT DF_{TABLE_NAME}_updated_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_{TABLE_NAME} PRIMARY KEY CLUSTERED (id),
        CONSTRAINT UQ_{TABLE_NAME}_row_uuid UNIQUE (row_uuid)
    );
END
IF COL_LENGTH('dbo.{TABLE_NAME}', 'tenant_key') IS NULL
BEGIN
    ALTER TABLE dbo.{TABLE_NAME} ADD tenant_key NVARCHAR(120) NULL;
    EXEC('UPDATE dbo.{TABLE_NAME} SET tenant_key = ''default'' WHERE tenant_key IS NULL OR LTRIM(RTRIM(tenant_key)) = ''''');
    EXEC('ALTER TABLE dbo.{TABLE_NAME} ALTER COLUMN tenant_key NVARCHAR(120) NOT NULL');
END
DECLARE @constraintName NVARCHAR(255);
SELECT TOP 1 @constraintName = kc.name
  FROM sys.key_constraints kc
 WHERE kc.parent_object_id = OBJECT_ID('dbo.{TABLE_NAME}')
   AND kc.type = 'UQ'
   AND kc.name = 'UQ_{TABLE_NAME}_site_data';
IF @constraintName IS NOT NULL
BEGIN
    DECLARE @dropSql NVARCHAR(MAX);
    SET @dropSql = 'ALTER TABLE dbo.{TABLE_NAME} DROP CONSTRAINT ' + QUOTENAME(@constraintName);
    EXEC(@dropSql);
END
IF NOT EXISTS (
    SELECT 1
      FROM sys.indexes
     WHERE name = 'UX_{TABLE_NAME}_tenant_site_data'
       AND object_id = OBJECT_ID('dbo.{TABLE_NAME}')
)
BEGIN
    EXEC('CREATE UNIQUE INDEX UX_{TABLE_NAME}_tenant_site_data ON dbo.{TABLE_NAME}(tenant_key, site, data_iso)');
END
"""
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('distinta_cassa_photo_repository:78')


def get_distinta_cassa_photo_file(*, store_code: str, data_iso: str, tenant_key: str | None = None) -> Optional[str]:
    ensure_distinta_cassa_photo_schema()
    tenant = _tenant(tenant_key)
    conn = _conn(read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT TOP 1 foto_file
            FROM dbo.{TABLE_NAME}
            WHERE tenant_key = ?
              AND LTRIM(RTRIM(CAST(site AS NVARCHAR(50)))) = ?
              AND data_iso = ?
            ORDER BY updated_at DESC, created_at DESC
            """,
            (tenant, str(store_code).strip(), str(data_iso).strip()),
        )
        row = cur.fetchone()
        return str(row[0]).strip() if row and row[0] else None
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('distinta_cassa_photo_repository:105')


def upsert_distinta_cassa_photo_file(
    *,
    store_code: str,
    data_iso: str,
    foto_file: str,
    tenant_key: str | None = None,
) -> None:
    ensure_distinta_cassa_photo_schema()
    tenant = _tenant(tenant_key)
    site = str(store_code).strip()
    day = str(data_iso).strip()
    file_name = str(foto_file).strip()
    if not (site and day and file_name):
        raise RuntimeError("store_code, data_iso e foto_file sono obbligatori.")

    conn = _conn(read_only=False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            UPDATE dbo.{TABLE_NAME}
               SET foto_file = ?,
                   updated_at = SYSUTCDATETIME()
             WHERE tenant_key = ?
               AND LTRIM(RTRIM(CAST(site AS NVARCHAR(50)))) = ?
               AND data_iso = ?
            """,
            (file_name, tenant, site, day),
        )
        updated = int(cur.rowcount or 0)
        if updated == 0:
            cur.execute(
                f"""
                INSERT INTO dbo.{TABLE_NAME} (row_uuid, tenant_key, site, data_iso, foto_file, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, SYSUTCDATETIME(), SYSUTCDATETIME())
                """,
                (str(uuid4()), tenant, site, day, file_name),
            )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('distinta_cassa_photo_repository:151')


def delete_distinta_cassa_photo_assoc(*, store_code: str, data_iso: str, tenant_key: str | None = None) -> None:
    ensure_distinta_cassa_photo_schema()
    tenant = _tenant(tenant_key)
    conn = _conn(read_only=False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"DELETE FROM dbo.{TABLE_NAME} WHERE tenant_key = ? AND LTRIM(RTRIM(CAST(site AS NVARCHAR(50)))) = ? AND data_iso = ?",
            (tenant, str(store_code).strip(), str(data_iso).strip()),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('distinta_cassa_photo_repository:169')



def list_distinta_cassa_photo_days(
    *,
    store_code: str,
    year: int,
    month: int,
    tenant_key: str | None = None,
) -> dict[str, str]:
    ensure_distinta_cassa_photo_schema()
    tenant = _tenant(tenant_key)
    conn = _conn(read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT CONVERT(char(10), data_iso, 23) AS d_iso, foto_file
            FROM dbo.{TABLE_NAME}
            WHERE tenant_key = ?
              AND LTRIM(RTRIM(CAST(site AS NVARCHAR(50)))) = ?
              AND YEAR(data_iso) = ?
              AND MONTH(data_iso) = ?
              AND NULLIF(LTRIM(RTRIM(CAST(foto_file AS NVARCHAR(255)))), '') IS NOT NULL
            """,
            (tenant, str(store_code).strip(), int(year), int(month)),
        )
        rows = cur.fetchall() or []
        out: dict[str, str] = {}
        for row in rows:
            d_iso = str(row[0] or '').strip()
            foto_file = str(row[1] or '').strip()
            if d_iso and foto_file:
                out[d_iso] = foto_file
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('distinta_cassa_photo_repository:209')
