from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from app_db import get_connection_sqlserver_database
from tenant_config_repository import current_tenant_key

try:
    from flask import has_request_context, session
except Exception:  # pragma: no cover
    has_request_context = None  # type: ignore
    session = None  # type: ignore


DB_NAME = os.getenv("STOREHUB_TENANT_DATABASE") or os.getenv("SQLSERVER_DATABASE") or os.getenv("SQLSERVER_DB") or "APP_STOREHUB"


DEFAULT_INQUADRAMENTI = [
    ("Store Manager", 10, True),
    ("Assistant", 20, True),
    ("Banconista", 30, True),
    ("Apprendista", 40, True),
    ("Stage", 50, False),
    ("Intermittente", 60, False),
]


DEFAULT_CAUSALI = [
    ("Ferie", 10, True, False, False, False, False, False, True),
    ("Permesso", 20, True, False, False, False, False, False, True),
    ("Allattamento", 30, True, False, False, True, False, False, True),
    ("Malattia", 40, True, False, False, False, False, False, True),
    ("Riposo Festivo", 50, False, False, False, True, False, True, False),
    ("Off", 60, False, False, False, False, False, False, False),
    ("Prestito", 70, False, False, False, False, True, False, True),
    ("Training", 80, False, False, True, True, False, False, True),
    ("Extra", 90, False, True, False, True, False, False, False),
]


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(DB_NAME, read_only=read_only)


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _tenant_key(tenant_key: str | None = None) -> str:
    if tenant_key:
        return str(tenant_key).strip() or current_tenant_key()
    try:
        if has_request_context and has_request_context() and session is not None:
            k = str(session.get("tenant_key") or session.get("master_tenant_key") or "").strip()
            if k:
                return k
    except Exception:
        pass
    return current_tenant_key()


def ensure_orari_config_schema() -> None:
    sql = """
IF OBJECT_ID('dbo.StoreHubOrariInquadramenti','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubOrariInquadramenti (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    name NVARCHAR(120) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    requires_contract_match BIT NOT NULL DEFAULT 1,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubOrariInquadramenti_tenant_name
    ON dbo.StoreHubOrariInquadramenti(tenant_key, name);
END
IF COL_LENGTH('dbo.StoreHubOrariInquadramenti','requires_contract_match') IS NULL
  ALTER TABLE dbo.StoreHubOrariInquadramenti ADD requires_contract_match BIT NOT NULL DEFAULT 1;

IF OBJECT_ID('dbo.StoreHubOrariCausali','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubOrariCausali (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    name NVARCHAR(120) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    justifies_contract_hours BIT NOT NULL DEFAULT 0,
    counts_productivity BIT NOT NULL DEFAULT 1,
    counts_training BIT NOT NULL DEFAULT 0,
    counts_labor_cost BIT NOT NULL DEFAULT 1,
    requires_loan_store BIT NOT NULL DEFAULT 0,
    requires_time_range BIT NOT NULL DEFAULT 0,
    auto_extra_eligible BIT NOT NULL DEFAULT 0,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubOrariCausali_tenant_name
    ON dbo.StoreHubOrariCausali(tenant_key, name);
END
"""
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()


def seed_default_orari_config(tenant_key: str | None = None) -> None:
    ensure_orari_config_schema()
    key = _tenant_key(tenant_key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        for name, sort_order, requires_contract_match in DEFAULT_INQUADRAMENTI:
            cur.execute(
                """
MERGE dbo.StoreHubOrariInquadramenti AS tgt
USING (SELECT ? AS tenant_key, ? AS name) AS src
ON tgt.tenant_key = src.tenant_key AND tgt.name = src.name
WHEN NOT MATCHED THEN
  INSERT (tenant_key, name, sort_order, requires_contract_match, is_active)
  VALUES (?, ?, ?, ?, 1);
""",
                key,
                name,
                key,
                name,
                int(sort_order),
                1 if requires_contract_match else 0,
            )
        for row in DEFAULT_CAUSALI:
            name, sort_order, justifies, productivity, training, labor, loan, requires_time, auto_extra = row
            cur.execute(
                """
MERGE dbo.StoreHubOrariCausali AS tgt
USING (SELECT ? AS tenant_key, ? AS name) AS src
ON tgt.tenant_key = src.tenant_key AND tgt.name = src.name
WHEN NOT MATCHED THEN
  INSERT (
    tenant_key, name, sort_order, justifies_contract_hours, counts_productivity,
    counts_training, counts_labor_cost, requires_loan_store, requires_time_range,
    auto_extra_eligible, is_active
  )
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
""",
                key,
                name,
                key,
                name,
                int(sort_order),
                1 if justifies else 0,
                1 if productivity else 0,
                1 if training else 0,
                1 if labor else 0,
                1 if loan else 0,
                1 if requires_time else 0,
                1 if auto_extra else 0,
            )
        conn.commit()


def list_orari_inquadramenti(tenant_key: str | None = None, *, active_only: bool = True) -> List[Dict[str, Any]]:
    seed_default_orari_config(tenant_key)
    key = _tenant_key(tenant_key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        sql = """
SELECT tenant_key, name, sort_order, requires_contract_match, is_active
  FROM dbo.StoreHubOrariInquadramenti
 WHERE tenant_key = ?
"""
        if active_only:
            sql += "   AND is_active = 1\n"
        sql += " ORDER BY sort_order, name"
        cur.execute(sql, key)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def list_orari_causali(tenant_key: str | None = None, *, active_only: bool = True) -> List[Dict[str, Any]]:
    seed_default_orari_config(tenant_key)
    key = _tenant_key(tenant_key)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        sql = """
SELECT tenant_key, name, sort_order, justifies_contract_hours, counts_productivity,
       counts_training, counts_labor_cost, requires_loan_store, requires_time_range,
       auto_extra_eligible, is_active
  FROM dbo.StoreHubOrariCausali
 WHERE tenant_key = ?
"""
        if active_only:
            sql += "   AND is_active = 1\n"
        sql += " ORDER BY sort_order, name"
        cur.execute(sql, key)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def upsert_orari_inquadramento(payload: Dict[str, Any], tenant_key: str | None = None) -> None:
    ensure_orari_config_schema()
    key = _tenant_key(tenant_key)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Nome inquadramento obbligatorio")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
MERGE dbo.StoreHubOrariInquadramenti AS tgt
USING (SELECT ? AS tenant_key, ? AS name) AS src
ON tgt.tenant_key = src.tenant_key AND tgt.name = src.name
WHEN MATCHED THEN UPDATE SET
  sort_order = ?, requires_contract_match = ?, is_active = ?, updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN
  INSERT (tenant_key, name, sort_order, requires_contract_match, is_active)
  VALUES (?, ?, ?, ?, ?);
""",
            key,
            name,
            int(payload.get("sort_order") or 0),
            1 if payload.get("requires_contract_match") else 0,
            1 if payload.get("is_active") else 0,
            key,
            name,
            int(payload.get("sort_order") or 0),
            1 if payload.get("requires_contract_match") else 0,
            1 if payload.get("is_active") else 0,
        )
        conn.commit()


def upsert_orari_causale(payload: Dict[str, Any], tenant_key: str | None = None) -> None:
    ensure_orari_config_schema()
    key = _tenant_key(tenant_key)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Nome causale obbligatorio")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
MERGE dbo.StoreHubOrariCausali AS tgt
USING (SELECT ? AS tenant_key, ? AS name) AS src
ON tgt.tenant_key = src.tenant_key AND tgt.name = src.name
WHEN MATCHED THEN UPDATE SET
  sort_order = ?, justifies_contract_hours = ?, counts_productivity = ?,
  counts_training = ?, counts_labor_cost = ?, requires_loan_store = ?,
  requires_time_range = ?, auto_extra_eligible = ?, is_active = ?, updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN
  INSERT (
    tenant_key, name, sort_order, justifies_contract_hours, counts_productivity,
    counts_training, counts_labor_cost, requires_loan_store, requires_time_range,
    auto_extra_eligible, is_active
  )
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
""",
            key,
            name,
            int(payload.get("sort_order") or 0),
            1 if payload.get("justifies_contract_hours") else 0,
            1 if payload.get("counts_productivity") else 0,
            1 if payload.get("counts_training") else 0,
            1 if payload.get("counts_labor_cost") else 0,
            1 if payload.get("requires_loan_store") else 0,
            1 if payload.get("requires_time_range") else 0,
            1 if payload.get("auto_extra_eligible") else 0,
            1 if payload.get("is_active") else 0,
            key,
            name,
            int(payload.get("sort_order") or 0),
            1 if payload.get("justifies_contract_hours") else 0,
            1 if payload.get("counts_productivity") else 0,
            1 if payload.get("counts_training") else 0,
            1 if payload.get("counts_labor_cost") else 0,
            1 if payload.get("requires_loan_store") else 0,
            1 if payload.get("requires_time_range") else 0,
            1 if payload.get("auto_extra_eligible") else 0,
            1 if payload.get("is_active") else 0,
        )
        conn.commit()


def delete_orari_inquadramento(name: str, tenant_key: str | None = None) -> bool:
    ensure_orari_config_schema()
    key = _tenant_key(tenant_key)
    n = str(name or "").strip()
    if not n:
        raise ValueError("Nome inquadramento obbligatorio")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
DELETE FROM dbo.StoreHubOrariInquadramenti
 WHERE tenant_key = ?
   AND name = ?
""",
            key,
            n,
        )
        ok = bool(cur.rowcount)
        conn.commit()
        return ok


def delete_orari_causale(name: str, tenant_key: str | None = None) -> bool:
    ensure_orari_config_schema()
    key = _tenant_key(tenant_key)
    n = str(name or "").strip()
    if not n:
        raise ValueError("Nome causale obbligatorio")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
DELETE FROM dbo.StoreHubOrariCausali
 WHERE tenant_key = ?
   AND name = ?
""",
            key,
            n,
        )
        ok = bool(cur.rowcount)
        conn.commit()
        return ok


def orari_config_for_frontend(tenant_key: str | None = None) -> Dict[str, Any]:
    inquadramenti = list_orari_inquadramenti(tenant_key, active_only=True)
    causali = list_orari_causali(tenant_key, active_only=True)
    return {
        "inquadramenti": inquadramenti,
        "causali": causali,
        "inquadramenti_by_key": {_norm_key(r.get("name")): r for r in inquadramenti},
        "causali_by_key": {_norm_key(r.get("name")): r for r in causali},
    }
