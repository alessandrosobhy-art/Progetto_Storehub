from __future__ import annotations

from app_logging import log_swallowed
import os
import re
import ast
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
        log_swallowed('cash_statement_config_repository:29')
    return (DEFAULT_TENANT_KEY or "default").strip() or "default"


def _current_ui_language() -> str:
    try:
        from flask import has_request_context, session

        if has_request_context():
            lang = str(session.get("ui_language") or "it").strip().lower()
            if lang in {"it", "en", "fr", "es"}:
                return lang
    except Exception:
        log_swallowed('cash_statement_config_repository:42')
    return "it"


def _apply_label_translation(row: Dict[str, Any], namespace: str, translation_key: str, language_code: str) -> None:
    base_label = str(row.get("label") or "").strip()
    if not base_label:
        return
    translated = ""
    try:
        from translation_repository import translate_text

        translated = translate_text(namespace, translation_key, base_label, language_code)
    except Exception:
        translated = ""
    if translated and translated != base_label:
        row["label"] = translated
        return
    legacy = str(row.get(f"label_{language_code}") or "").strip()
    if language_code != "it" and legacy:
        row["label"] = legacy


def ensure_cash_statement_config_schema() -> None:
    sql = """
IF OBJECT_ID('dbo.StoreHubCashStatementSections','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubCashStatementSections (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    section_key NVARCHAR(120) NOT NULL,
    label NVARCHAR(255) NOT NULL,
    label_en NVARCHAR(255) NULL,
    label_fr NVARCHAR(255) NULL,
    label_es NVARCHAR(255) NULL,
    section_kind NVARCHAR(80) NOT NULL DEFAULT 'fields',
    legacy_category NVARCHAR(120) NULL,
    base_section BIT NOT NULL DEFAULT 0,
    protected_section BIT NOT NULL DEFAULT 0,
    is_visible BIT NOT NULL DEFAULT 1,
    is_active BIT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubCashStatementSections_tenant_key
    ON dbo.StoreHubCashStatementSections(tenant_key, section_key);
END

IF OBJECT_ID('dbo.StoreHubCashStatementFields','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubCashStatementFields (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    section_key NVARCHAR(120) NOT NULL,
    field_key NVARCHAR(160) NOT NULL,
    label NVARCHAR(255) NOT NULL,
    label_en NVARCHAR(255) NULL,
    label_fr NVARCHAR(255) NULL,
    label_es NVARCHAR(255) NULL,
    value_type NVARCHAR(60) NOT NULL DEFAULT 'money',
    option_group NVARCHAR(120) NULL,
    legacy_category NVARCHAR(120) NULL,
    legacy_voce NVARCHAR(255) NULL,
    legacy_tipo NVARCHAR(30) NULL,
    legacy_datidatabase_field NVARCHAR(120) NULL,
    ipratico_key NVARCHAR(160) NULL,
    required_field BIT NOT NULL DEFAULT 0,
    readonly_field BIT NOT NULL DEFAULT 0,
    imported_field BIT NOT NULL DEFAULT 0,
    protected_field BIT NOT NULL DEFAULT 0,
    is_visible BIT NOT NULL DEFAULT 1,
    is_active BIT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    affects_gross_revenue BIT NOT NULL DEFAULT 0,
    gross_revenue_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    affects_cash_difference BIT NOT NULL DEFAULT 0,
    cash_difference_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    cash_difference_tipo_filter NVARCHAR(30) NULL,
    affects_receipts BIT NOT NULL DEFAULT 0,
    receipts_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    affects_pos_amount BIT NOT NULL DEFAULT 0,
    pos_amount_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    affects_cash_amount BIT NOT NULL DEFAULT 0,
    cash_amount_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    affects_ticket_total BIT NOT NULL DEFAULT 0,
    ticket_total_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    affects_ticket_cash_effect BIT NOT NULL DEFAULT 0,
    ticket_cash_effect_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    ticket_cash_effect_tipo_filter NVARCHAR(30) NULL,
    affects_delivery_total BIT NOT NULL DEFAULT 0,
    delivery_total_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    affects_delivery_online BIT NOT NULL DEFAULT 0,
    delivery_online_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    delivery_online_tipo_filter NVARCHAR(30) NULL,
    affects_delivery_cash BIT NOT NULL DEFAULT 0,
    delivery_cash_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    delivery_cash_tipo_filter NVARCHAR(30) NULL,
    affects_delivery_cash_effect BIT NOT NULL DEFAULT 0,
    delivery_cash_effect_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    delivery_cash_effect_tipo_filter NVARCHAR(30) NULL,
    affects_coupon_total BIT NOT NULL DEFAULT 0,
    coupon_total_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    affects_coupon_cash_effect BIT NOT NULL DEFAULT 0,
    coupon_cash_effect_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    coupon_cash_effect_tipo_filter NVARCHAR(30) NULL,
    affects_cash_deposit BIT NOT NULL DEFAULT 0,
    cash_deposit_sign DECIMAL(9,4) NOT NULL DEFAULT 0,
    formula_expression NVARCHAR(1000) NULL,
    canonical_metric NVARCHAR(120) NULL,
    behavior_json NVARCHAR(MAX) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubCashStatementFields_tenant_section_field
    ON dbo.StoreHubCashStatementFields(tenant_key, section_key, field_key);
END

IF COL_LENGTH('dbo.StoreHubCashStatementFields', 'formula_expression') IS NULL
BEGIN
  ALTER TABLE dbo.StoreHubCashStatementFields ADD formula_expression NVARCHAR(1000) NULL;
END
IF COL_LENGTH('dbo.StoreHubCashStatementFields', 'canonical_metric') IS NULL
BEGIN
  ALTER TABLE dbo.StoreHubCashStatementFields ADD canonical_metric NVARCHAR(120) NULL;
END
IF COL_LENGTH('dbo.StoreHubCashStatementSections', 'label_en') IS NULL
BEGIN
  ALTER TABLE dbo.StoreHubCashStatementSections ADD label_en NVARCHAR(255) NULL;
END
IF COL_LENGTH('dbo.StoreHubCashStatementSections', 'label_fr') IS NULL
BEGIN
  ALTER TABLE dbo.StoreHubCashStatementSections ADD label_fr NVARCHAR(255) NULL;
END
IF COL_LENGTH('dbo.StoreHubCashStatementSections', 'label_es') IS NULL
BEGIN
  ALTER TABLE dbo.StoreHubCashStatementSections ADD label_es NVARCHAR(255) NULL;
END
IF COL_LENGTH('dbo.StoreHubCashStatementFields', 'label_en') IS NULL
BEGIN
  ALTER TABLE dbo.StoreHubCashStatementFields ADD label_en NVARCHAR(255) NULL;
END
IF COL_LENGTH('dbo.StoreHubCashStatementFields', 'label_fr') IS NULL
BEGIN
  ALTER TABLE dbo.StoreHubCashStatementFields ADD label_fr NVARCHAR(255) NULL;
END
IF COL_LENGTH('dbo.StoreHubCashStatementFields', 'label_es') IS NULL
BEGIN
  ALTER TABLE dbo.StoreHubCashStatementFields ADD label_es NVARCHAR(255) NULL;
END
"""
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()


def _upsert_section(cur, row: Dict[str, Any]) -> None:
    preserve_runtime_flags = bool(row.get("_preserve_runtime_flags"))
    if preserve_runtime_flags:
        set_sql = """
   SET label = ?,
       label_en = ?,
       label_fr = ?,
       label_es = ?,
       section_kind = ?,
       legacy_category = ?,
       base_section = ?,
       protected_section = ?,
       sort_order = ?,
       updated_at = SYSUTCDATETIME()
"""
        params = [
            row["label"],
            _clean_translation(row.get("label_en")),
            _clean_translation(row.get("label_fr")),
            _clean_translation(row.get("label_es")),
            row["section_kind"],
            row.get("legacy_category"),
            int(row.get("base_section") or 0),
            int(row.get("protected_section") or 0),
            int(row.get("sort_order") or 0),
            row["tenant_key"],
            row["section_key"],
        ]
    else:
        set_sql = """
   SET label = ?,
       label_en = ?,
       label_fr = ?,
       label_es = ?,
       section_kind = ?,
       legacy_category = ?,
       base_section = ?,
       protected_section = ?,
       is_visible = ?,
       is_active = ?,
       sort_order = ?,
       updated_at = SYSUTCDATETIME()
"""
        params = [
            row["label"],
            _clean_translation(row.get("label_en")),
            _clean_translation(row.get("label_fr")),
            _clean_translation(row.get("label_es")),
            row["section_kind"],
            row.get("legacy_category"),
            int(row.get("base_section") or 0),
            int(row.get("protected_section") or 0),
            int(row.get("is_visible", 1)),
            int(row.get("is_active", 1)),
            int(row.get("sort_order") or 0),
            row["tenant_key"],
            row["section_key"],
        ]
    cur.execute(
        f"""
UPDATE dbo.StoreHubCashStatementSections
{set_sql}
 WHERE tenant_key = ? AND section_key = ?
""",
        *params,
    )
    if cur.rowcount:
        return
    cur.execute(
        """
INSERT INTO dbo.StoreHubCashStatementSections
  (tenant_key, section_key, label, label_en, label_fr, label_es, section_kind, legacy_category, base_section, protected_section, is_visible, is_active, sort_order)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
        row["tenant_key"],
        row["section_key"],
        row["label"],
        _clean_translation(row.get("label_en")),
        _clean_translation(row.get("label_fr")),
        _clean_translation(row.get("label_es")),
        row["section_kind"],
        row.get("legacy_category"),
        int(row.get("base_section") or 0),
        int(row.get("protected_section") or 0),
        int(row.get("is_visible", 1)),
        int(row.get("is_active", 1)),
        int(row.get("sort_order") or 0),
    )


_FIELD_COLUMNS = [
    "tenant_key",
    "section_key",
    "field_key",
    "label",
    "label_en",
    "label_fr",
    "label_es",
    "value_type",
    "option_group",
    "legacy_category",
    "legacy_voce",
    "legacy_tipo",
    "legacy_datidatabase_field",
    "ipratico_key",
    "required_field",
    "readonly_field",
    "imported_field",
    "protected_field",
    "is_visible",
    "is_active",
    "sort_order",
    "affects_gross_revenue",
    "gross_revenue_sign",
    "affects_cash_difference",
    "cash_difference_sign",
    "cash_difference_tipo_filter",
    "affects_receipts",
    "receipts_sign",
    "affects_pos_amount",
    "pos_amount_sign",
    "affects_cash_amount",
    "cash_amount_sign",
    "affects_ticket_total",
    "ticket_total_sign",
    "affects_ticket_cash_effect",
    "ticket_cash_effect_sign",
    "ticket_cash_effect_tipo_filter",
    "affects_delivery_total",
    "delivery_total_sign",
    "affects_delivery_online",
    "delivery_online_sign",
    "delivery_online_tipo_filter",
    "affects_delivery_cash",
    "delivery_cash_sign",
    "delivery_cash_tipo_filter",
    "affects_delivery_cash_effect",
    "delivery_cash_effect_sign",
    "delivery_cash_effect_tipo_filter",
    "affects_coupon_total",
    "coupon_total_sign",
    "affects_coupon_cash_effect",
    "coupon_cash_effect_sign",
    "coupon_cash_effect_tipo_filter",
    "affects_cash_deposit",
    "cash_deposit_sign",
    "formula_expression",
    "canonical_metric",
    "behavior_json",
]


def _clean_translation(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _field_value(row: Dict[str, Any], key: str) -> Any:
    if key in {"required_field", "readonly_field", "imported_field", "protected_field", "is_visible", "is_active"}:
        return int(row.get(key, 1 if key in {"is_visible", "is_active"} else 0) or 0)
    if key.startswith("affects_"):
        return int(row.get(key) or 0)
    if key.endswith("_sign"):
        return float(row.get(key) or 0)
    if key == "sort_order":
        return int(row.get(key) or 0)
    return row.get(key)


def _upsert_field(cur, row: Dict[str, Any]) -> None:
    update_columns = _FIELD_COLUMNS[3:]
    if row.get("_preserve_runtime_flags"):
        update_columns = [col for col in update_columns if col not in {"is_visible", "is_active"}]
    assignments = ", ".join([f"{col} = ?" for col in update_columns] + ["updated_at = SYSUTCDATETIME()"])
    values = [_field_value(row, col) for col in update_columns]
    values.extend([row["tenant_key"], row["section_key"], row["field_key"]])
    cur.execute(
        f"""
UPDATE dbo.StoreHubCashStatementFields
   SET {assignments}
 WHERE tenant_key = ? AND section_key = ? AND field_key = ?
""",
        *values,
    )
    if cur.rowcount:
        return
    cols_sql = ", ".join(_FIELD_COLUMNS)
    placeholders = ", ".join(["?"] * len(_FIELD_COLUMNS))
    cur.execute(
        f"INSERT INTO dbo.StoreHubCashStatementFields ({cols_sql}) VALUES ({placeholders})",
        *[_field_value(row, col) for col in _FIELD_COLUMNS],
    )


def seed_default_cash_statement_config(tenant_key: str | None = None) -> None:
    ensure_cash_statement_config_schema()
    tenant = _tenant(tenant_key)
    sections = [
        {"tenant_key": tenant, "section_key": "dati_chiusura", "label": "Dati chiusura", "section_kind": "fields", "legacy_category": "Dati chiusura", "base_section": 1, "protected_section": 1, "sort_order": 10},
        {"tenant_key": tenant, "section_key": "foto", "label": "Foto distinta", "section_kind": "attachment", "legacy_category": None, "base_section": 1, "protected_section": 1, "sort_order": 20},
        {"tenant_key": tenant, "section_key": "distinte", "label": "Distinte", "section_kind": "cash_denominations", "legacy_category": "Distinte", "base_section": 1, "protected_section": 1, "sort_order": 30},
        {"tenant_key": tenant, "section_key": "ticket", "label": "Ticket", "section_kind": "option_list", "legacy_category": "Ticket", "base_section": 0, "protected_section": 1, "sort_order": 40},
        {"tenant_key": tenant, "section_key": "delivery", "label": "Delivery", "section_kind": "option_list", "legacy_category": "Delivery", "base_section": 0, "protected_section": 1, "sort_order": 50},
        {"tenant_key": tenant, "section_key": "coupon", "label": "Coupon", "section_kind": "option_list", "legacy_category": "Coupon", "base_section": 0, "protected_section": 1, "sort_order": 60},
    ]
    fields = [
        _field(tenant, "dati_chiusura", "vendite_lorde", "VENDITE LORDE", 10, required=1, gross=1, gross_sign=1, legacy_field="gross_revenue", ipratico="vendite_lorde"),
        _field(tenant, "dati_chiusura", "annullati", "ANNULLATI", 20, gross=1, gross_sign=-1, legacy_field="cancelled_amount", ipratico="annullati"),
        _field(tenant, "dati_chiusura", "scontrini", "SCONTRINI", 30, value_type="int", required=1, receipts=1, receipts_sign=1, legacy_field="receipts_count", ipratico="scontrini"),
        _field(tenant, "dati_chiusura", "pos", "POS", 40, required=1, cash_diff=1, cash_diff_sign=1, pos=1, pos_sign=1, ipratico="pos"),
        _field(tenant, "dati_chiusura", "contanti", "CONTANTI", 50, required=1, cash=1, cash_sign=1, ipratico="contanti"),
        _field(tenant, "dati_chiusura", "ticket", "TICKET", 60, readonly=1, imported=1, ticket_total=1, ticket_total_sign=1, ipratico="ticket"),
        _field(tenant, "dati_chiusura", "fatture", "FATTURE", 70, ipratico="fatture"),
        _field(tenant, "dati_chiusura", "numero_fatture", "NUMERO FATTURE", 80, value_type="int", ipratico="numero_fatture"),
        _field(tenant, "dati_chiusura", "omaggi", "OMAGGI", 90, ipratico="omaggi"),
        _field(tenant, "dati_chiusura", "vendite_iva_4", "VENDITE IVA 4%", 100, ipratico="vendite_iva_4"),
        _field(tenant, "dati_chiusura", "vendite_iva_22", "VENDITE IVA 22%", 110, ipratico="vendite_iva_22"),
        _field(tenant, "distinte", "cash_deposit_line", "Taglio contanti", 10, value_type="cash_denominations", cash_diff=1, cash_diff_sign=1, cash_deposit=1, cash_deposit_sign=1),
        _field(tenant, "ticket", "ticket_line", "Voce ticket", 10, value_type="option_money", option_group="Ticket", legacy_category="Ticket", ticket_total=1, ticket_total_sign=1, ticket_effect=1, ticket_effect_sign=1, ticket_effect_filter="SI", cash_diff=1, cash_diff_sign=1, cash_diff_filter="SI"),
        _field(tenant, "delivery", "delivery_line", "Voce delivery", 10, value_type="option_money", option_group="Delivery", legacy_category="Delivery", delivery_total=1, delivery_total_sign=1, delivery_online=1, delivery_online_sign=1, delivery_online_filter="NO", delivery_cash=1, delivery_cash_sign=1, delivery_cash_filter="SI", delivery_effect=1, delivery_effect_sign=1, delivery_effect_filter="SI", cash_diff=1, cash_diff_sign=1, cash_diff_filter="SI", legacy_field="delivery_total"),
        _field(tenant, "coupon", "coupon_line", "Voce coupon", 10, value_type="option_money", option_group="Coupon", legacy_category="Coupon", coupon_total=1, coupon_total_sign=1, coupon_effect=1, coupon_effect_sign=1, coupon_effect_filter="SI", cash_diff=1, cash_diff_sign=1, cash_diff_filter="SI"),
    ]
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        for section in sections:
            section["_preserve_runtime_flags"] = True
            _upsert_section(cur, section)
        for field in fields:
            field["_preserve_runtime_flags"] = True
            _upsert_field(cur, field)
        conn.commit()


def align_cash_statement_config_to_tenant(tenant_key: str | None = None) -> None:
    """Ensure the current tenant DB has its base cash statement config under the real tenant key."""
    seed_default_cash_statement_config(tenant_key=tenant_key)


def _field(
    tenant: str,
    section: str,
    key: str,
    label: str,
    sort_order: int,
    *,
    value_type: str = "money",
    option_group: str | None = None,
    legacy_category: str | None = "Dati chiusura",
    legacy_field: str | None = None,
    ipratico: str | None = None,
    required: int = 0,
    readonly: int = 0,
    imported: int = 0,
    gross: int = 0,
    gross_sign: float = 0,
    cash_diff: int = 0,
    cash_diff_sign: float = 0,
    cash_diff_filter: str | None = None,
    receipts: int = 0,
    receipts_sign: float = 0,
    pos: int = 0,
    pos_sign: float = 0,
    cash: int = 0,
    cash_sign: float = 0,
    ticket_total: int = 0,
    ticket_total_sign: float = 0,
    ticket_effect: int = 0,
    ticket_effect_sign: float = 0,
    ticket_effect_filter: str | None = None,
    delivery_total: int = 0,
    delivery_total_sign: float = 0,
    delivery_online: int = 0,
    delivery_online_sign: float = 0,
    delivery_online_filter: str | None = None,
    delivery_cash: int = 0,
    delivery_cash_sign: float = 0,
    delivery_cash_filter: str | None = None,
    delivery_effect: int = 0,
    delivery_effect_sign: float = 0,
    delivery_effect_filter: str | None = None,
    coupon_total: int = 0,
    coupon_total_sign: float = 0,
    coupon_effect: int = 0,
    coupon_effect_sign: float = 0,
    coupon_effect_filter: str | None = None,
    cash_deposit: int = 0,
    cash_deposit_sign: float = 0,
) -> Dict[str, Any]:
    return {
        "tenant_key": tenant,
        "section_key": section,
        "field_key": key,
        "label": label,
        "label_en": None,
        "label_fr": None,
        "label_es": None,
        "value_type": value_type,
        "option_group": option_group,
        "legacy_category": legacy_category,
        "legacy_voce": label if legacy_category == "Dati chiusura" else None,
        "legacy_tipo": None,
        "legacy_datidatabase_field": legacy_field,
        "ipratico_key": ipratico,
        "required_field": required,
        "readonly_field": readonly,
        "imported_field": imported,
        "protected_field": 1,
        "is_visible": 1,
        "is_active": 1,
        "sort_order": sort_order,
        "affects_gross_revenue": gross,
        "gross_revenue_sign": gross_sign,
        "affects_cash_difference": cash_diff,
        "cash_difference_sign": cash_diff_sign,
        "cash_difference_tipo_filter": cash_diff_filter,
        "affects_receipts": receipts,
        "receipts_sign": receipts_sign,
        "affects_pos_amount": pos,
        "pos_amount_sign": pos_sign,
        "affects_cash_amount": cash,
        "cash_amount_sign": cash_sign,
        "affects_ticket_total": ticket_total,
        "ticket_total_sign": ticket_total_sign,
        "affects_ticket_cash_effect": ticket_effect,
        "ticket_cash_effect_sign": ticket_effect_sign,
        "ticket_cash_effect_tipo_filter": ticket_effect_filter,
        "affects_delivery_total": delivery_total,
        "delivery_total_sign": delivery_total_sign,
        "affects_delivery_online": delivery_online,
        "delivery_online_sign": delivery_online_sign,
        "delivery_online_tipo_filter": delivery_online_filter,
        "affects_delivery_cash": delivery_cash,
        "delivery_cash_sign": delivery_cash_sign,
        "delivery_cash_tipo_filter": delivery_cash_filter,
        "affects_delivery_cash_effect": delivery_effect,
        "delivery_cash_effect_sign": delivery_effect_sign,
        "delivery_cash_effect_tipo_filter": delivery_effect_filter,
        "affects_coupon_total": coupon_total,
        "coupon_total_sign": coupon_total_sign,
        "affects_coupon_cash_effect": coupon_effect,
        "coupon_cash_effect_sign": coupon_effect_sign,
        "coupon_cash_effect_tipo_filter": coupon_effect_filter,
        "affects_cash_deposit": cash_deposit,
        "cash_deposit_sign": cash_deposit_sign,
        "behavior_json": None,
    }


def list_cash_statement_config(tenant_key: str | None = None) -> Dict[str, List[Dict[str, Any]]]:
    ensure_cash_statement_config_schema()
    tenant = _tenant(tenant_key)
    def fetch_config_for(cur, key: str) -> tuple[list[dict], list[dict]]:
        cur.execute(
            """
SELECT tenant_key, section_key, label, label_en, label_fr, label_es, section_kind, legacy_category, base_section,
       protected_section, is_visible, is_active, sort_order
  FROM dbo.StoreHubCashStatementSections
 WHERE tenant_key = ?
 ORDER BY sort_order, label
""",
            key,
        )
        section_cols = [c[0] for c in cur.description]
        sections = [dict(zip(section_cols, row)) for row in cur.fetchall()]
        cur.execute(
            """
SELECT tenant_key, section_key, field_key, label, label_en, label_fr, label_es, value_type, option_group, legacy_category,
       legacy_voce, legacy_tipo, legacy_datidatabase_field, ipratico_key, required_field,
       readonly_field, imported_field, protected_field, is_visible, is_active, sort_order,
       affects_gross_revenue, gross_revenue_sign, affects_cash_difference, cash_difference_sign,
       cash_difference_tipo_filter, affects_receipts, receipts_sign, affects_pos_amount,
       pos_amount_sign, affects_cash_amount, cash_amount_sign, affects_ticket_total,
       ticket_total_sign, affects_ticket_cash_effect, ticket_cash_effect_sign,
       ticket_cash_effect_tipo_filter, affects_delivery_total, delivery_total_sign,
       affects_delivery_online, delivery_online_sign, delivery_online_tipo_filter,
       affects_delivery_cash, delivery_cash_sign, delivery_cash_tipo_filter,
       affects_delivery_cash_effect, delivery_cash_effect_sign, delivery_cash_effect_tipo_filter,
       affects_coupon_total, coupon_total_sign, affects_coupon_cash_effect,
       coupon_cash_effect_sign, coupon_cash_effect_tipo_filter, affects_cash_deposit,
       cash_deposit_sign, formula_expression, canonical_metric
  FROM dbo.StoreHubCashStatementFields
 WHERE tenant_key = ?
 ORDER BY section_key, sort_order, label
""",
            key,
        )
        field_cols = [c[0] for c in cur.description]
        fields = [dict(zip(field_cols, row)) for row in cur.fetchall()]
        return sections, fields

    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        sections, fields = fetch_config_for(cur, tenant)
        if not sections and tenant != "default":
            sections, fields = fetch_config_for(cur, "default")
    lang = _current_ui_language()
    if lang:
        for section in sections:
            section_key = str(section.get("section_key") or "").strip()
            _apply_label_translation(section, "cash_statement", f"section.{section_key}", lang)
        for field in fields:
            section_key = str(field.get("section_key") or "").strip()
            field_key = str(field.get("field_key") or "").strip()
            _apply_label_translation(field, "cash_statement", f"field.{section_key}.{field_key}", lang)
    return {"sections": sections, "fields": fields}


def seed_cash_statement_translations(tenant_key: str | None = None) -> int:
    ensure_cash_statement_config_schema()
    tenant = _tenant(tenant_key)
    def fetch_rows_for(cur, key: str) -> tuple[list[dict], list[dict]]:
        cur.execute(
            """
SELECT section_key, label, label_en, label_fr, label_es
  FROM dbo.StoreHubCashStatementSections
 WHERE tenant_key = ?
""",
            key,
        )
        sections = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
        cur.execute(
            """
SELECT section_key, field_key, label, label_en, label_fr, label_es
  FROM dbo.StoreHubCashStatementFields
 WHERE tenant_key = ?
""",
            key,
        )
        fields = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
        return sections, fields

    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        sections, fields = fetch_rows_for(cur, tenant)
        if not sections and tenant != "default":
            sections, fields = fetch_rows_for(cur, "default")
    count = 0
    try:
        from translation_repository import upsert_tenant_custom_translation

        for section in sections:
            label = str(section.get("label") or "").strip()
            section_key = str(section.get("section_key") or "").strip()
            if not label or not section_key:
                continue
            upsert_tenant_custom_translation(
                "cash_statement",
                f"section.{section_key}",
                label,
                {
                    "it": label,
                    "en": section.get("label_en") or "",
                    "fr": section.get("label_fr") or "",
                    "es": section.get("label_es") or "",
                },
            )
            count += 1
        for field in fields:
            label = str(field.get("label") or "").strip()
            section_key = str(field.get("section_key") or "").strip()
            field_key = str(field.get("field_key") or "").strip()
            if not label or not section_key or not field_key:
                continue
            upsert_tenant_custom_translation(
                "cash_statement",
                f"field.{section_key}.{field_key}",
                label,
                {
                    "it": label,
                    "en": field.get("label_en") or "",
                    "fr": field.get("label_fr") or "",
                    "es": field.get("label_es") or "",
                },
            )
            count += 1
    except Exception:
        return count
    return count


def list_cash_statement_dashboard_customizations(tenant_key: str | None = None) -> List[Dict[str, Any]]:
    """Visible tenant customizations to expose in dashboard daily popups."""
    config = list_cash_statement_config(tenant_key=tenant_key)
    fields_by_section: Dict[str, List[Dict[str, Any]]] = {}
    for field in config.get("fields") or []:
        if not field.get("is_active") or not field.get("is_visible"):
            continue
        fields_by_section.setdefault(str(field.get("section_key") or ""), []).append(field)

    out: List[Dict[str, Any]] = []
    for section in config.get("sections") or []:
        if not section.get("is_active") or not section.get("is_visible"):
            continue
        section_key = str(section.get("section_key") or "")
        fields = fields_by_section.get(section_key) or []
        custom_fields = [f for f in fields if not f.get("protected_field")]
        is_custom_section = not bool(section.get("base_section")) and not bool(section.get("protected_section"))
        if not is_custom_section and not custom_fields:
            continue
        visible_fields = fields if is_custom_section else custom_fields
        if not visible_fields:
            continue
        out.append(
            {
                "section_key": section_key,
                "label": section.get("label"),
                "label_en": section.get("label_en") or section.get("label"),
                "label_fr": section.get("label_fr") or section.get("label"),
                "label_es": section.get("label_es") or section.get("label"),
                "section_kind": section.get("section_kind"),
                "legacy_category": section.get("legacy_category") or section.get("label"),
                "fields": [
                    {
                        "field_key": f.get("field_key"),
                        "label": f.get("label"),
                        "label_en": f.get("label_en") or f.get("label"),
                        "label_fr": f.get("label_fr") or f.get("label"),
                        "label_es": f.get("label_es") or f.get("label"),
                        "value_type": f.get("value_type"),
                        "legacy_category": f.get("legacy_category") or section.get("legacy_category") or section.get("label"),
                        "legacy_voce": f.get("legacy_voce") or f.get("label"),
                        "legacy_tipo": f.get("legacy_tipo"),
                    }
                    for f in visible_fields
                ],
            }
        )
    return out


def _slug(value: str, fallback: str = "voce") -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw or fallback


def create_cash_statement_section(
    *,
    label: str,
    label_en: str = "",
    label_fr: str = "",
    label_es: str = "",
    section_kind: str = "fields",
    sort_order: int = 100,
    tenant_key: str | None = None,
) -> str:
    ensure_cash_statement_config_schema()
    tenant = _tenant(tenant_key)
    name = str(label or "").strip()
    if not name:
        raise ValueError("Nome sezione obbligatorio.")
    kind = str(section_kind or "fields").strip() or "fields"
    base_key = _slug(name, "sezione")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        section_key = base_key
        suffix = 2
        while True:
            cur.execute(
                "SELECT 1 FROM dbo.StoreHubCashStatementSections WHERE tenant_key = ? AND section_key = ?",
                tenant,
                section_key,
            )
            if not cur.fetchone():
                break
            section_key = f"{base_key}_{suffix}"
            suffix += 1
        _upsert_section(
            cur,
            {
                "tenant_key": tenant,
                "section_key": section_key,
                "label": name,
                "label_en": label_en,
                "label_fr": label_fr,
                "label_es": label_es,
                "section_kind": kind,
                "legacy_category": name,
                "base_section": 0,
                "protected_section": 0,
                "is_visible": 1,
                "is_active": 1,
                "sort_order": sort_order,
            },
        )
        conn.commit()
    try:
        from translation_repository import upsert_tenant_custom_translation

        upsert_tenant_custom_translation(
            "cash_statement",
            f"section.{section_key}",
            name,
            {"it": name, "en": label_en, "fr": label_fr, "es": label_es},
        )
    except Exception:
        log_swallowed('cash_statement_config_repository:796')
    return section_key


def create_cash_statement_field(
    *,
    section_key: str,
    label: str,
    label_en: str = "",
    label_fr: str = "",
    label_es: str = "",
    value_type: str = "money",
    behavior: str = "none",
    formula_expression: str = "",
    canonical_metric: str = "",
    legacy_tipo: str = "",
    sort_order: int = 100,
    required: bool = False,
    tenant_key: str | None = None,
) -> str:
    ensure_cash_statement_config_schema()
    tenant = _tenant(tenant_key)
    section = str(section_key or "").strip()
    name = str(label or "").strip()
    if not section:
        raise ValueError("Sezione obbligatoria.")
    if not name:
        raise ValueError("Nome voce obbligatorio.")
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT TOP 1 label, section_kind FROM dbo.StoreHubCashStatementSections WHERE tenant_key = ? AND section_key = ?",
            tenant,
            section,
        )
        section_row = cur.fetchone()
        if not section_row:
            raise ValueError("Sezione non trovata.")
        section_kind = str(section_row[1] or "").strip().lower()
        if section_kind == "option_list" and str(value_type or "").strip().lower() == "calculated":
            raise ValueError("Una sezione elenco voci non puo contenere campi calcolati.")
        existing_keys = _list_field_keys(cur, tenant)
        _validate_formula_expression(str(formula_expression or ""), existing_keys)
        base_key = _slug(name, "voce")
        field_key = base_key
        suffix = 2
        while True:
            cur.execute(
                "SELECT 1 FROM dbo.StoreHubCashStatementFields WHERE tenant_key = ? AND section_key = ? AND field_key = ?",
                tenant,
                section,
                field_key,
            )
            if not cur.fetchone():
                break
            field_key = f"{base_key}_{suffix}"
            suffix += 1
        row = _custom_field_row(
            tenant=tenant,
            section=section,
            key=field_key,
            label=name,
            label_en=label_en,
            label_fr=label_fr,
            label_es=label_es,
            value_type=value_type,
            behavior=behavior,
            formula_expression=formula_expression,
            canonical_metric=canonical_metric,
            legacy_tipo=legacy_tipo,
            sort_order=sort_order,
            required=required,
            legacy_category=str(section_row[0] or section),
        )
        _upsert_field(cur, row)
        conn.commit()
    try:
        from translation_repository import upsert_tenant_custom_translation

        upsert_tenant_custom_translation(
            "cash_statement",
            f"field.{section}.{field_key}",
            name,
            {"it": name, "en": label_en, "fr": label_fr, "es": label_es},
        )
    except Exception:
        log_swallowed('cash_statement_config_repository:882')
    return field_key


def _list_field_keys(cur, tenant: str) -> set[str]:
    cur.execute(
        "SELECT field_key FROM dbo.StoreHubCashStatementFields WHERE tenant_key = ?",
        tenant,
    )
    return {str(row[0] or "").strip() for row in cur.fetchall() if str(row[0] or "").strip()}


def _validate_formula_expression(formula: str, allowed_names: set[str]) -> None:
    expr = str(formula or "").strip()
    if not expr:
        return
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Formula non valida.") from exc
    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.USub,
        ast.UAdd,
        ast.Load,
        ast.Name,
        ast.Constant,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise ValueError("Formula non valida: usa solo campi, numeri e operatori + - * /.")
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise ValueError(f"Campo formula non riconosciuto: {node.id}.")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("Formula non valida: le costanti devono essere numeriche.")


def _to_number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _eval_formula_expression(formula: str, context: Dict[str, float]) -> float:
    tree = ast.parse(str(formula or "").strip(), mode="eval")

    def eval_node(node) -> float:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError("Costante formula non numerica.")
        if isinstance(node, ast.Name):
            if node.id not in context:
                raise KeyError(node.id)
            return _to_number(context[node.id])
        if isinstance(node, ast.UnaryOp):
            val = eval_node(node.operand)
            if isinstance(node.op, ast.USub):
                return -val
            if isinstance(node.op, ast.UAdd):
                return val
            raise ValueError("Operatore formula non consentito.")
        if isinstance(node, ast.BinOp):
            left = eval_node(node.left)
            right = eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    return 0.0
                return left / right
        raise ValueError("Formula non valida.")

    return eval_node(tree)


def evaluate_cash_statement_calculations(
    *,
    base_values: Dict[str, Any],
    tenant_key: str | None = None,
) -> Dict[str, Any]:
    config = list_cash_statement_config(tenant_key=tenant_key)
    fields = [
        f for f in (config.get("fields") or [])
        if f.get("is_active") and f.get("is_visible")
    ]
    context: Dict[str, float] = {str(k): _to_number(v) for k, v in (base_values or {}).items()}
    calculated_fields: Dict[str, float] = {}
    canonical_metrics: Dict[str, float] = {}
    errors: List[str] = []

    for field in sorted(fields, key=lambda f: int(f.get("sort_order") or 0)):
        field_key = str(field.get("field_key") or "").strip()
        if not field_key:
            continue
        formula = str(field.get("formula_expression") or "").strip()
        if formula:
            try:
                value = _eval_formula_expression(formula, context)
            except KeyError as exc:
                errors.append(f"{field_key}: campo mancante {exc.args[0]}")
                continue
            except Exception as exc:
                errors.append(f"{field_key}: {exc}")
                continue
            context[field_key] = value
            calculated_fields[field_key] = value
        metric = str(field.get("canonical_metric") or "").strip()
        if metric and field_key in context:
            canonical_metrics[metric] = _to_number(context[field_key])

    return {
        "context": context,
        "calculated_fields": calculated_fields,
        "canonical_metrics": canonical_metrics,
        "errors": errors,
    }


def _custom_field_row(
    *,
    tenant: str,
    section: str,
    key: str,
    label: str,
    label_en: str,
    label_fr: str,
    label_es: str,
    value_type: str,
    behavior: str,
    formula_expression: str,
    canonical_metric: str,
    legacy_tipo: str,
    sort_order: int,
    required: bool,
    legacy_category: str,
) -> Dict[str, Any]:
    row = _field(
        tenant,
        section,
        key,
        label,
        sort_order,
        value_type=str(value_type or "money"),
        legacy_category=legacy_category,
        required=1 if required else 0,
    )
    row["label_en"] = _clean_translation(label_en)
    row["label_fr"] = _clean_translation(label_fr)
    row["label_es"] = _clean_translation(label_es)
    row["protected_field"] = 0
    row["legacy_voce"] = label
    row["legacy_tipo"] = str(legacy_tipo or "").strip().upper() or None
    row["formula_expression"] = str(formula_expression or "").strip() or None
    row["canonical_metric"] = str(canonical_metric or "").strip() or None
    row.update(_behavior_flags(str(behavior or "none").strip().lower()))
    return row


def set_cash_statement_section_visible(section_key: str, *, visible: bool, tenant_key: str | None = None) -> bool:
    ensure_cash_statement_config_schema()
    tenant = _tenant(tenant_key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubCashStatementSections
   SET is_visible = ?, updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ? AND section_key = ?
""",
            1 if visible else 0,
            tenant,
            str(section_key or "").strip(),
        )
        changed = bool(cur.rowcount)
        conn.commit()
    return changed


def set_cash_statement_field_visible(
    *,
    section_key: str,
    field_key: str,
    visible: bool,
    tenant_key: str | None = None,
) -> bool:
    ensure_cash_statement_config_schema()
    tenant = _tenant(tenant_key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubCashStatementFields
   SET is_visible = ?, updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ? AND section_key = ? AND field_key = ?
""",
            1 if visible else 0,
            tenant,
            str(section_key or "").strip(),
            str(field_key or "").strip(),
        )
        changed = bool(cur.rowcount)
        conn.commit()
    return changed


def delete_cash_statement_field(
    *,
    section_key: str,
    field_key: str,
    tenant_key: str | None = None,
    include_protected: bool = False,
) -> bool:
    ensure_cash_statement_config_schema()
    tenant = _tenant(tenant_key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        where = "tenant_key = ? AND section_key = ? AND field_key = ?"
        params: list[Any] = [tenant, str(section_key or "").strip(), str(field_key or "").strip()]
        if not include_protected:
            where += " AND protected_field = 0"
        cur.execute(f"DELETE FROM dbo.StoreHubCashStatementFields WHERE {where}", *params)
        changed = bool(cur.rowcount)
        conn.commit()
    return changed


def delete_cash_statement_section(
    *,
    section_key: str,
    tenant_key: str | None = None,
    include_protected: bool = False,
) -> bool:
    ensure_cash_statement_config_schema()
    tenant = _tenant(tenant_key)
    section = str(section_key or "").strip()
    if not section:
        return False
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        where = "tenant_key = ? AND section_key = ?"
        params: list[Any] = [tenant, section]
        if not include_protected:
            where += " AND protected_section = 0 AND base_section = 0"
        cur.execute(f"SELECT 1 FROM dbo.StoreHubCashStatementSections WHERE {where}", *params)
        if not cur.fetchone():
            conn.commit()
            return False
        cur.execute(
            "DELETE FROM dbo.StoreHubCashStatementFields WHERE tenant_key = ? AND section_key = ?",
            tenant,
            section,
        )
        cur.execute(
            "DELETE FROM dbo.StoreHubCashStatementSections WHERE tenant_key = ? AND section_key = ?",
            tenant,
            section,
        )
        changed = bool(cur.rowcount)
        conn.commit()
    return changed


def update_cash_statement_field_behavior(
    *,
    section_key: str,
    field_key: str,
    behavior: str,
    tenant_key: str | None = None,
) -> bool:
    ensure_cash_statement_config_schema()
    tenant = _tenant(tenant_key)
    behavior_key = str(behavior or "none").strip().lower()
    flags = _behavior_flags(behavior_key)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
UPDATE dbo.StoreHubCashStatementFields
   SET affects_gross_revenue = ?,
       gross_revenue_sign = ?,
       affects_cash_difference = ?,
       cash_difference_sign = ?,
       cash_difference_tipo_filter = ?,
       affects_receipts = ?,
       receipts_sign = ?,
       affects_pos_amount = ?,
       pos_amount_sign = ?,
       affects_cash_amount = ?,
       cash_amount_sign = ?,
       affects_ticket_total = ?,
       ticket_total_sign = ?,
       affects_ticket_cash_effect = ?,
       ticket_cash_effect_sign = ?,
       ticket_cash_effect_tipo_filter = ?,
       affects_delivery_total = ?,
       delivery_total_sign = ?,
       affects_delivery_online = ?,
       delivery_online_sign = ?,
       delivery_online_tipo_filter = ?,
       affects_delivery_cash = ?,
       delivery_cash_sign = ?,
       delivery_cash_tipo_filter = ?,
       affects_delivery_cash_effect = ?,
       delivery_cash_effect_sign = ?,
       delivery_cash_effect_tipo_filter = ?,
       affects_coupon_total = ?,
       coupon_total_sign = ?,
       affects_coupon_cash_effect = ?,
       coupon_cash_effect_sign = ?,
       coupon_cash_effect_tipo_filter = ?,
       affects_cash_deposit = ?,
       cash_deposit_sign = ?,
       updated_at = SYSUTCDATETIME()
 WHERE tenant_key = ? AND section_key = ? AND field_key = ?
""",
            flags["affects_gross_revenue"],
            flags["gross_revenue_sign"],
            flags["affects_cash_difference"],
            flags["cash_difference_sign"],
            flags["cash_difference_tipo_filter"],
            flags["affects_receipts"],
            flags["receipts_sign"],
            flags["affects_pos_amount"],
            flags["pos_amount_sign"],
            flags["affects_cash_amount"],
            flags["cash_amount_sign"],
            flags["affects_ticket_total"],
            flags["ticket_total_sign"],
            flags["affects_ticket_cash_effect"],
            flags["ticket_cash_effect_sign"],
            flags["ticket_cash_effect_tipo_filter"],
            flags["affects_delivery_total"],
            flags["delivery_total_sign"],
            flags["affects_delivery_online"],
            flags["delivery_online_sign"],
            flags["delivery_online_tipo_filter"],
            flags["affects_delivery_cash"],
            flags["delivery_cash_sign"],
            flags["delivery_cash_tipo_filter"],
            flags["affects_delivery_cash_effect"],
            flags["delivery_cash_effect_sign"],
            flags["delivery_cash_effect_tipo_filter"],
            flags["affects_coupon_total"],
            flags["coupon_total_sign"],
            flags["affects_coupon_cash_effect"],
            flags["coupon_cash_effect_sign"],
            flags["coupon_cash_effect_tipo_filter"],
            flags["affects_cash_deposit"],
            flags["cash_deposit_sign"],
            tenant,
            str(section_key or "").strip(),
            str(field_key or "").strip(),
        )
        changed = bool(cur.rowcount)
        conn.commit()
    return changed


def _behavior_flags(behavior_key: str) -> Dict[str, Any]:
    flags = {
        "affects_gross_revenue": 0,
        "gross_revenue_sign": 0,
        "affects_cash_difference": 0,
        "cash_difference_sign": 0,
        "cash_difference_tipo_filter": None,
        "affects_receipts": 0,
        "receipts_sign": 0,
        "affects_pos_amount": 0,
        "pos_amount_sign": 0,
        "affects_cash_amount": 0,
        "cash_amount_sign": 0,
        "affects_ticket_total": 0,
        "ticket_total_sign": 0,
        "affects_ticket_cash_effect": 0,
        "ticket_cash_effect_sign": 0,
        "ticket_cash_effect_tipo_filter": None,
        "affects_delivery_total": 0,
        "delivery_total_sign": 0,
        "affects_delivery_online": 0,
        "delivery_online_sign": 0,
        "delivery_online_tipo_filter": None,
        "affects_delivery_cash": 0,
        "delivery_cash_sign": 0,
        "delivery_cash_tipo_filter": None,
        "affects_delivery_cash_effect": 0,
        "delivery_cash_effect_sign": 0,
        "delivery_cash_effect_tipo_filter": None,
        "affects_coupon_total": 0,
        "coupon_total_sign": 0,
        "affects_coupon_cash_effect": 0,
        "coupon_cash_effect_sign": 0,
        "coupon_cash_effect_tipo_filter": None,
        "affects_cash_deposit": 0,
        "cash_deposit_sign": 0,
    }
    if behavior_key == "gross_plus":
        flags["affects_gross_revenue"] = 1
        flags["gross_revenue_sign"] = 1
    elif behavior_key == "gross_minus":
        flags["affects_gross_revenue"] = 1
        flags["gross_revenue_sign"] = -1
    elif behavior_key == "cash_difference_plus":
        flags["affects_cash_difference"] = 1
        flags["cash_difference_sign"] = 1
    elif behavior_key == "cash_difference_minus":
        flags["affects_cash_difference"] = 1
        flags["cash_difference_sign"] = -1
    elif behavior_key == "pos":
        flags["affects_pos_amount"] = 1
        flags["pos_amount_sign"] = 1
        flags["affects_cash_difference"] = 1
        flags["cash_difference_sign"] = 1
    elif behavior_key == "cash":
        flags["affects_cash_amount"] = 1
        flags["cash_amount_sign"] = 1
    elif behavior_key == "receipts":
        flags["affects_receipts"] = 1
        flags["receipts_sign"] = 1
    elif behavior_key == "cash_deposit":
        flags["affects_cash_deposit"] = 1
        flags["cash_deposit_sign"] = 1
        flags["affects_cash_difference"] = 1
        flags["cash_difference_sign"] = 1
    elif behavior_key == "ticket_si":
        flags["affects_ticket_total"] = 1
        flags["ticket_total_sign"] = 1
        flags["affects_ticket_cash_effect"] = 1
        flags["ticket_cash_effect_sign"] = 1
        flags["ticket_cash_effect_tipo_filter"] = "SI"
        flags["affects_cash_difference"] = 1
        flags["cash_difference_sign"] = 1
        flags["cash_difference_tipo_filter"] = "SI"
    elif behavior_key == "delivery_online_si":
        flags["affects_delivery_total"] = 1
        flags["delivery_total_sign"] = 1
        flags["affects_delivery_online"] = 1
        flags["delivery_online_sign"] = 1
        flags["delivery_online_tipo_filter"] = "SI"
        flags["affects_delivery_cash_effect"] = 1
        flags["delivery_cash_effect_sign"] = 1
        flags["delivery_cash_effect_tipo_filter"] = "SI"
        flags["affects_cash_difference"] = 1
        flags["cash_difference_sign"] = 1
        flags["cash_difference_tipo_filter"] = "SI"
    elif behavior_key == "delivery_cash_no":
        flags["affects_delivery_total"] = 1
        flags["delivery_total_sign"] = 1
        flags["affects_delivery_cash"] = 1
        flags["delivery_cash_sign"] = 1
        flags["delivery_cash_tipo_filter"] = "NO"
    elif behavior_key == "coupon_si":
        flags["affects_coupon_total"] = 1
        flags["coupon_total_sign"] = 1
        flags["affects_coupon_cash_effect"] = 1
        flags["coupon_cash_effect_sign"] = 1
        flags["coupon_cash_effect_tipo_filter"] = "SI"
        flags["affects_cash_difference"] = 1
        flags["cash_difference_sign"] = 1
        flags["cash_difference_tipo_filter"] = "SI"
    return flags
