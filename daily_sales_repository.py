from __future__ import annotations

from app_logging import log_swallowed
import json
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from app_db import get_connection_sqlserver_database, get_storehub_database_name
from cash_statement_config_repository import evaluate_cash_statement_calculations, list_cash_statement_config
from dati_database_repository import list_legacy_daily_sales_range


DEFAULT_TENANT_KEY = os.getenv("STOREHUB_TENANT_KEY") or "default"
_STANDARD_DISTINTA_KEYS = {
    "vendite_lorde",
    "annullati",
    "scontrini",
    "pos",
    "contanti",
    "ticket",
    "fatture",
    "numero_fatture",
    "omaggi",
    "vendite_iva_4",
    "vendite_iva_22",
    "cash_deposit_line",
    "ticket_line",
    "delivery_line",
    "coupon_line",
}
_REPORT_METRIC_LABELS = {
    "gross_revenue": "Giro d'affari lordo al netto annullati",
    "net_revenue": "Giro d'affari netto stimato",
    "cancelled_amount": "Annullati",
    "receipts_count": "Numero scontrini",
    "pos_amount": "POS dichiarato",
    "cash_amount": "Contanti dichiarati",
    "ticket_total": "Totale ticket",
    "ticket_cash_effect": "Ticket che riducono la differenza cassa",
    "delivery_total": "Totale delivery",
    "delivery_online_amount": "Delivery online",
    "delivery_cash_amount": "Delivery contanti",
    "delivery_cash_effect": "Delivery che riducono la differenza cassa",
    "coupon_total": "Totale coupon",
    "coupon_cash_effect": "Coupon che riducono la differenza cassa",
    "expenses_net": "Spese nette",
    "cash_deposits_total": "Totale distinte contanti",
    "cash_difference": "Differenza cassa",
}
_HISTORICAL_IMPORT_FIELDS = [
    "store_code",
    "business_date",
    "gross_revenue",
    "net_revenue",
    "cancelled_amount",
    "receipts_count",
    "pos_amount",
    "cash_amount",
    "ticket_total",
    "ticket_cash_effect",
    "delivery_total",
    "delivery_online_amount",
    "delivery_cash_amount",
    "delivery_cash_effect",
    "coupon_total",
    "coupon_cash_effect",
    "expenses_net",
    "cash_deposits_total",
    "cash_difference",
]


def historical_daily_sales_import_targets() -> List[Dict[str, str]]:
    labels = {
        "store_code": "Store / codice negozio",
        "business_date": "Data",
        **_REPORT_METRIC_LABELS,
    }
    return [{"key": key, "label": labels.get(key, key)} for key in _HISTORICAL_IMPORT_FIELDS]


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def _date_only(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except Exception:
            log_swallowed('daily_sales_repository:96')
    return datetime.strptime(raw[:10], "%Y-%m-%d").date()


def _num(value: Any) -> float:
    if isinstance(value, str):
        s = value.strip().replace("€", "").replace(" ", "")
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        value = s
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def ensure_daily_sales_schema() -> None:
    sql = """
IF OBJECT_ID('dbo.StoreHubDailySales','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubDailySales (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    tenant_key NVARCHAR(120) NOT NULL,
    store_code NVARCHAR(50) NOT NULL,
    business_date DATE NOT NULL,
    gross_revenue DECIMAL(18,4) NOT NULL DEFAULT 0,
    net_revenue DECIMAL(18,4) NOT NULL DEFAULT 0,
    cancelled_amount DECIMAL(18,4) NOT NULL DEFAULT 0,
    receipts_count INT NOT NULL DEFAULT 0,
    pos_amount DECIMAL(18,4) NOT NULL DEFAULT 0,
    cash_amount DECIMAL(18,4) NOT NULL DEFAULT 0,
    ticket_total DECIMAL(18,4) NOT NULL DEFAULT 0,
    ticket_cash_effect DECIMAL(18,4) NOT NULL DEFAULT 0,
    delivery_total DECIMAL(18,4) NOT NULL DEFAULT 0,
    delivery_online_amount DECIMAL(18,4) NOT NULL DEFAULT 0,
    delivery_cash_amount DECIMAL(18,4) NOT NULL DEFAULT 0,
    delivery_cash_effect DECIMAL(18,4) NOT NULL DEFAULT 0,
    coupon_total DECIMAL(18,4) NOT NULL DEFAULT 0,
    coupon_cash_effect DECIMAL(18,4) NOT NULL DEFAULT 0,
    expenses_net DECIMAL(18,4) NOT NULL DEFAULT 0,
    cash_deposits_total DECIMAL(18,4) NOT NULL DEFAULT 0,
    cash_difference DECIMAL(18,4) NOT NULL DEFAULT 0,
    source NVARCHAR(80) NOT NULL DEFAULT 'distinta_cassa',
    source_payload NVARCHAR(MAX) NULL,
    legacy_datidatabase_synced BIT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubDailySales_tenant_store_date
    ON dbo.StoreHubDailySales(tenant_key, store_code, business_date);
END
"""
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()


def _entry_type(entry: Dict[str, Any]) -> str:
    raw = str(entry.get("tipo") or "SI").strip().upper()
    if raw.startswith("N") or raw in {"0", "FALSE", "F"}:
        return "NO"
    return "SI"


def _sum_entries(entries: Iterable[Dict[str, Any]], *, categoria: str, only_tipo: Optional[str] = None) -> float:
    cat_norm = str(categoria or "").strip().lower()
    tipo_norm = str(only_tipo or "").strip().upper()
    total = 0.0
    for entry in entries or []:
        if str(entry.get("categoria") or "").strip().lower() != cat_norm:
            continue
        if tipo_norm and _entry_type(entry) != tipo_norm:
            continue
        total += _num(entry.get("valore"))
    return total


def _apply_custom_field_behaviors(values: Dict[str, Any], *, tenant_key: str) -> Dict[str, float]:
    out = {
        "gross_revenue": 0.0,
        "cash_difference": 0.0,
        "receipts_count": 0.0,
        "pos_amount": 0.0,
        "cash_amount": 0.0,
        "cash_deposits_total": 0.0,
    }
    try:
        cfg = list_cash_statement_config(tenant_key=tenant_key)
    except Exception:
        return out
    for field in cfg.get("fields") or []:
        if not field.get("is_active") or not field.get("is_visible"):
            continue
        key = str(field.get("field_key") or "").strip()
        if not key or key in _STANDARD_DISTINTA_KEYS or key not in values:
            continue
        val = _num(values.get(key))
        if field.get("affects_gross_revenue"):
            out["gross_revenue"] += val * _num(field.get("gross_revenue_sign"))
        if field.get("affects_cash_difference"):
            out["cash_difference"] += val * _num(field.get("cash_difference_sign"))
        if field.get("affects_receipts"):
            out["receipts_count"] += val * _num(field.get("receipts_sign"))
        if field.get("affects_pos_amount"):
            out["pos_amount"] += val * _num(field.get("pos_amount_sign"))
        if field.get("affects_cash_amount"):
            out["cash_amount"] += val * _num(field.get("cash_amount_sign"))
        if field.get("affects_cash_deposit"):
            out["cash_deposits_total"] += val * _num(field.get("cash_deposit_sign"))
    return out


def _tipo_matches(entry: Dict[str, Any], tipo_filter: Any) -> bool:
    filtro = str(tipo_filter or "").strip().upper()
    if not filtro:
        return True
    return _entry_type(entry) == filtro


def _apply_custom_entry_behaviors(entries: Iterable[Dict[str, Any]], *, tenant_key: str) -> Dict[str, Any]:
    out = {
        "adjustments": {
            "gross_revenue": 0.0,
            "cash_difference": 0.0,
            "receipts_count": 0.0,
            "pos_amount": 0.0,
            "cash_amount": 0.0,
            "ticket_total": 0.0,
            "ticket_cash_effect": 0.0,
            "delivery_total": 0.0,
            "delivery_online_amount": 0.0,
            "delivery_cash_amount": 0.0,
            "delivery_cash_effect": 0.0,
            "coupon_total": 0.0,
            "coupon_cash_effect": 0.0,
            "cash_deposits_total": 0.0,
        },
        "values": {},
    }
    rows = list(entries or [])
    try:
        cfg = list_cash_statement_config(tenant_key=tenant_key)
    except Exception:
        return out

    sections_by_key = {str(s.get("section_key") or ""): s for s in cfg.get("sections") or []}
    for field in cfg.get("fields") or []:
        if not field.get("is_active") or not field.get("is_visible"):
            continue
        section_key = str(field.get("section_key") or "").strip()
        field_key = str(field.get("field_key") or "").strip()
        if not section_key or not field_key or field_key in _STANDARD_DISTINTA_KEYS:
            continue
        section = sections_by_key.get(section_key) or {}
        if str(section.get("section_kind") or "").strip().lower() != "option_list":
            continue
        if section_key in {"ticket", "delivery", "coupon"}:
            continue

        category = str(field.get("legacy_category") or section.get("legacy_category") or section.get("label") or "").strip().lower()
        voce = str(field.get("legacy_voce") or field.get("label") or "").strip().lower()
        if not category or not voce:
            continue

        total = 0.0
        for entry in rows:
            if str(entry.get("categoria") or "").strip().lower() != category:
                continue
            if str(entry.get("voce") or "").strip().lower() != voce:
                continue
            total += _num(entry.get("valore"))
        if abs(total) <= 1e-9:
            continue

        out["values"][field_key] = total
        adj = out["adjustments"]
        if field.get("affects_gross_revenue"):
            adj["gross_revenue"] += total * _num(field.get("gross_revenue_sign"))
        if field.get("affects_cash_difference"):
            matching = [
                entry for entry in rows
                if str(entry.get("categoria") or "").strip().lower() == category
                and str(entry.get("voce") or "").strip().lower() == voce
                and _tipo_matches(entry, field.get("cash_difference_tipo_filter"))
            ]
            adj["cash_difference"] += sum(_num(entry.get("valore")) for entry in matching) * _num(field.get("cash_difference_sign"))
        if field.get("affects_receipts"):
            adj["receipts_count"] += total * _num(field.get("receipts_sign"))
        if field.get("affects_pos_amount"):
            adj["pos_amount"] += total * _num(field.get("pos_amount_sign"))
        if field.get("affects_cash_amount"):
            adj["cash_amount"] += total * _num(field.get("cash_amount_sign"))
        if field.get("affects_ticket_total"):
            adj["ticket_total"] += total * _num(field.get("ticket_total_sign"))
        if field.get("affects_ticket_cash_effect"):
            matching = [
                entry for entry in rows
                if str(entry.get("categoria") or "").strip().lower() == category
                and str(entry.get("voce") or "").strip().lower() == voce
                and _tipo_matches(entry, field.get("ticket_cash_effect_tipo_filter"))
            ]
            adj["ticket_cash_effect"] += sum(_num(entry.get("valore")) for entry in matching) * _num(field.get("ticket_cash_effect_sign"))
        if field.get("affects_delivery_total"):
            adj["delivery_total"] += total * _num(field.get("delivery_total_sign"))
        if field.get("affects_delivery_online"):
            matching = [
                entry for entry in rows
                if str(entry.get("categoria") or "").strip().lower() == category
                and str(entry.get("voce") or "").strip().lower() == voce
                and _tipo_matches(entry, field.get("delivery_online_tipo_filter"))
            ]
            adj["delivery_online_amount"] += sum(_num(entry.get("valore")) for entry in matching) * _num(field.get("delivery_online_sign"))
        if field.get("affects_delivery_cash"):
            matching = [
                entry for entry in rows
                if str(entry.get("categoria") or "").strip().lower() == category
                and str(entry.get("voce") or "").strip().lower() == voce
                and _tipo_matches(entry, field.get("delivery_cash_tipo_filter"))
            ]
            adj["delivery_cash_amount"] += sum(_num(entry.get("valore")) for entry in matching) * _num(field.get("delivery_cash_sign"))
        if field.get("affects_delivery_cash_effect"):
            matching = [
                entry for entry in rows
                if str(entry.get("categoria") or "").strip().lower() == category
                and str(entry.get("voce") or "").strip().lower() == voce
                and _tipo_matches(entry, field.get("delivery_cash_effect_tipo_filter"))
            ]
            adj["delivery_cash_effect"] += sum(_num(entry.get("valore")) for entry in matching) * _num(field.get("delivery_cash_effect_sign"))
        if field.get("affects_coupon_total"):
            adj["coupon_total"] += total * _num(field.get("coupon_total_sign"))
        if field.get("affects_coupon_cash_effect"):
            matching = [
                entry for entry in rows
                if str(entry.get("categoria") or "").strip().lower() == category
                and str(entry.get("voce") or "").strip().lower() == voce
                and _tipo_matches(entry, field.get("coupon_cash_effect_tipo_filter"))
            ]
            adj["coupon_cash_effect"] += sum(_num(entry.get("valore")) for entry in matching) * _num(field.get("coupon_cash_effect_sign"))
        if field.get("affects_cash_deposit"):
            adj["cash_deposits_total"] += total * _num(field.get("cash_deposit_sign"))
    return out


def build_daily_sales_from_distinta(
    *,
    store_code: str,
    data_iso: str,
    chiusura_vals: Dict[str, Any],
    entries: List[Dict[str, Any]],
    expenses_net: float = 0.0,
    tenant_key: str | None = None,
) -> Dict[str, Any]:
    tenant = str(tenant_key or DEFAULT_TENANT_KEY).strip() or "default"
    day = _date_only(data_iso)

    vendite_lorde = _num(chiusura_vals.get("vendite_lorde"))
    annullati = _num(chiusura_vals.get("annullati"))
    gross_revenue = vendite_lorde - annullati
    net_revenue = gross_revenue / 1.1 if gross_revenue else 0.0

    ticket_total = _sum_entries(entries, categoria="Ticket")
    ticket_cash_effect = _sum_entries(entries, categoria="Ticket", only_tipo="SI")
    delivery_total = _sum_entries(entries, categoria="Delivery")
    delivery_online_amount = _sum_entries(entries, categoria="Delivery", only_tipo="SI")
    delivery_cash_amount = _sum_entries(entries, categoria="Delivery", only_tipo="NO")
    delivery_cash_effect = delivery_online_amount
    coupon_total = _sum_entries(entries, categoria="Coupon")
    coupon_cash_effect = _sum_entries(entries, categoria="Coupon", only_tipo="SI")
    cash_deposits_total = _sum_entries(entries, categoria="Distinte")
    pos_amount = _num(chiusura_vals.get("pos"))
    cash_amount = _num(chiusura_vals.get("contanti"))
    expenses = _num(expenses_net)
    cash_difference = (
        cash_deposits_total
        + ticket_cash_effect
        + delivery_cash_effect
        + coupon_cash_effect
        + pos_amount
        + expenses
        - gross_revenue
    )

    try:
        receipts_count = int(round(_num(chiusura_vals.get("scontrini"))))
    except Exception:
        receipts_count = 0
    custom_adjustments = _apply_custom_field_behaviors(chiusura_vals, tenant_key=tenant)
    gross_revenue += custom_adjustments["gross_revenue"]
    cash_difference += custom_adjustments["cash_difference"]
    pos_amount += custom_adjustments["pos_amount"]
    cash_amount += custom_adjustments["cash_amount"]
    cash_deposits_total += custom_adjustments["cash_deposits_total"]
    receipts_count = int(round(receipts_count + custom_adjustments["receipts_count"]))

    custom_entry_info = _apply_custom_entry_behaviors(entries, tenant_key=tenant)
    custom_entry_adjustments = custom_entry_info.get("adjustments") or {}
    custom_entry_values = custom_entry_info.get("values") or {}
    gross_revenue += _num(custom_entry_adjustments.get("gross_revenue"))
    cash_difference += _num(custom_entry_adjustments.get("cash_difference"))
    pos_amount += _num(custom_entry_adjustments.get("pos_amount"))
    cash_amount += _num(custom_entry_adjustments.get("cash_amount"))
    ticket_total += _num(custom_entry_adjustments.get("ticket_total"))
    ticket_cash_effect += _num(custom_entry_adjustments.get("ticket_cash_effect"))
    delivery_total += _num(custom_entry_adjustments.get("delivery_total"))
    delivery_online_amount += _num(custom_entry_adjustments.get("delivery_online_amount"))
    delivery_cash_amount += _num(custom_entry_adjustments.get("delivery_cash_amount"))
    delivery_cash_effect += _num(custom_entry_adjustments.get("delivery_cash_effect"))
    coupon_total += _num(custom_entry_adjustments.get("coupon_total"))
    coupon_cash_effect += _num(custom_entry_adjustments.get("coupon_cash_effect"))
    cash_deposits_total += _num(custom_entry_adjustments.get("cash_deposits_total"))
    receipts_count = int(round(receipts_count + _num(custom_entry_adjustments.get("receipts_count"))))

    base_context = {
        **{str(k): _num(v) for k, v in (chiusura_vals or {}).items()},
        **{str(k): _num(v) for k, v in custom_entry_values.items()},
        "gross_revenue": gross_revenue,
        "business_volume": gross_revenue,
        "net_revenue": net_revenue,
        "cancelled_amount": annullati,
        "receipts_count": receipts_count,
        "pos_amount": pos_amount,
        "cash_amount": cash_amount,
        "ticket_total": ticket_total,
        "ticket_cash_effect": ticket_cash_effect,
        "delivery_total": delivery_total,
        "delivery_online_amount": delivery_online_amount,
        "delivery_cash_amount": delivery_cash_amount,
        "delivery_cash_effect": delivery_cash_effect,
        "coupon_total": coupon_total,
        "coupon_cash_effect": coupon_cash_effect,
        "expenses_net": expenses,
        "cash_deposits_total": cash_deposits_total,
        "cash_difference": cash_difference,
    }
    calc_info = {"calculated_fields": {}, "canonical_metrics": {}, "errors": []}
    try:
        calc_info = evaluate_cash_statement_calculations(base_values=base_context, tenant_key=tenant)
        canonical = calc_info.get("canonical_metrics") or {}
        gross_revenue = _num(canonical.get("gross_revenue", canonical.get("business_volume", gross_revenue)))
        net_revenue = _num(canonical.get("net_revenue", net_revenue))
        cash_difference = _num(canonical.get("cash_difference", cash_difference))
        pos_amount = _num(canonical.get("pos_amount", pos_amount))
        cash_amount = _num(canonical.get("cash_amount", cash_amount))
        ticket_total = _num(canonical.get("ticket_total", ticket_total))
        delivery_total = _num(canonical.get("delivery_total", delivery_total))
        coupon_total = _num(canonical.get("coupon_total", coupon_total))
        receipts_count = int(round(_num(canonical.get("receipts_count", receipts_count))))
    except Exception as exc:
        calc_info = {"calculated_fields": {}, "canonical_metrics": {}, "errors": [str(exc)]}

    return {
        "tenant_key": tenant,
        "store_code": str(store_code).strip(),
        "business_date": day,
        "gross_revenue": gross_revenue,
        "net_revenue": net_revenue,
        "cancelled_amount": annullati,
        "receipts_count": receipts_count,
        "pos_amount": pos_amount,
        "cash_amount": cash_amount,
        "ticket_total": ticket_total,
        "ticket_cash_effect": ticket_cash_effect,
        "delivery_total": delivery_total,
        "delivery_online_amount": delivery_online_amount,
        "delivery_cash_amount": delivery_cash_amount,
        "delivery_cash_effect": delivery_cash_effect,
        "coupon_total": coupon_total,
        "coupon_cash_effect": coupon_cash_effect,
        "expenses_net": expenses,
        "cash_deposits_total": cash_deposits_total,
        "cash_difference": cash_difference,
        "source": "distinta_cassa",
        "source_payload": json.dumps(
            {
                "chiusura": chiusura_vals,
                "entries": entries,
                "report_metrics": {
                    "gross_revenue": gross_revenue,
                    "net_revenue": net_revenue,
                    "cancelled_amount": annullati,
                    "receipts_count": receipts_count,
                    "pos_amount": pos_amount,
                    "cash_amount": cash_amount,
                    "ticket_total": ticket_total,
                    "ticket_cash_effect": ticket_cash_effect,
                    "delivery_total": delivery_total,
                    "delivery_online_amount": delivery_online_amount,
                    "delivery_cash_amount": delivery_cash_amount,
                    "delivery_cash_effect": delivery_cash_effect,
                    "coupon_total": coupon_total,
                    "coupon_cash_effect": coupon_cash_effect,
                    "expenses_net": expenses,
                    "cash_deposits_total": cash_deposits_total,
                    "cash_difference": cash_difference,
                },
                "report_metric_labels": _REPORT_METRIC_LABELS,
                "calculated_fields": calc_info.get("calculated_fields") or {},
                "canonical_metrics": calc_info.get("canonical_metrics") or {},
                "formula_errors": calc_info.get("errors") or [],
                "custom_behavior_adjustments": custom_adjustments,
                "custom_entry_values": custom_entry_values,
                "custom_entry_behavior_adjustments": custom_entry_adjustments,
            },
            ensure_ascii=False,
            default=str,
        ),
    }


def upsert_daily_sales(row: Dict[str, Any]) -> Dict[str, Any]:
    ensure_daily_sales_schema()
    fields = [
        "gross_revenue",
        "net_revenue",
        "cancelled_amount",
        "receipts_count",
        "pos_amount",
        "cash_amount",
        "ticket_total",
        "ticket_cash_effect",
        "delivery_total",
        "delivery_online_amount",
        "delivery_cash_amount",
        "delivery_cash_effect",
        "coupon_total",
        "coupon_cash_effect",
        "expenses_net",
        "cash_deposits_total",
        "cash_difference",
        "source",
        "source_payload",
    ]
    tenant = str(row.get("tenant_key") or DEFAULT_TENANT_KEY).strip() or "default"
    store = str(row.get("store_code") or "").strip()
    day = _date_only(row.get("business_date"))
    if not store:
        raise ValueError("store_code mancante")

    update_set = ", ".join([f"{field} = ?" for field in fields] + ["updated_at = SYSUTCDATETIME()"])
    update_params = [row.get(field) for field in fields] + [tenant, store, day]

    insert_cols = ["tenant_key", "store_code", "business_date"] + fields
    insert_sql_cols = ", ".join(insert_cols)
    insert_ph = ", ".join(["?"] * len(insert_cols))
    insert_params = [tenant, store, day] + [row.get(field) for field in fields]

    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
UPDATE dbo.StoreHubDailySales
   SET {update_set}
 WHERE tenant_key = ? AND store_code = ? AND business_date = ?
""",
            update_params,
        )
        cur.execute(
            """
IF NOT EXISTS (
  SELECT 1 FROM dbo.StoreHubDailySales
   WHERE tenant_key = ? AND store_code = ? AND business_date = ?
)
BEGIN
"""
            + f"  INSERT INTO dbo.StoreHubDailySales ({insert_sql_cols}) VALUES ({insert_ph})\n"
            + "END",
            [tenant, store, day] + insert_params,
        )
        conn.commit()
    return {"ok": True, "tenant_key": tenant, "store_code": store, "business_date": day.isoformat()}


def upsert_daily_sales_from_distinta(
    *,
    store_code: str,
    data_iso: str,
    chiusura_vals: Dict[str, Any],
    entries: List[Dict[str, Any]],
    expenses_net: float = 0.0,
    tenant_key: str | None = None,
) -> Dict[str, Any]:
    row = build_daily_sales_from_distinta(
        store_code=store_code,
        data_iso=data_iso,
        chiusura_vals=chiusura_vals,
        entries=entries,
        expenses_net=expenses_net,
        tenant_key=tenant_key,
    )
    return upsert_daily_sales(row)


def upsert_historical_daily_sales_from_csv_row(
    *,
    csv_row: Dict[str, Any],
    mapping: Dict[str, str],
    tenant_key: str | None = None,
) -> Dict[str, Any]:
    tenant = str(tenant_key or DEFAULT_TENANT_KEY).strip() or "default"
    reverse = {target: source for source, target in (mapping or {}).items() if source and target}
    store_source = reverse.get("store_code")
    date_source = reverse.get("business_date")
    if not store_source or not date_source:
        raise ValueError("Mappatura store e data obbligatoria.")
    store = str((csv_row or {}).get(store_source) or "").strip()
    if not store:
        raise ValueError("Store mancante.")
    day = _date_only((csv_row or {}).get(date_source))

    row: Dict[str, Any] = {
        "tenant_key": tenant,
        "store_code": store,
        "business_date": day,
        "source": "historical_csv_import",
    }
    for field in _HISTORICAL_IMPORT_FIELDS:
        if field in {"store_code", "business_date"}:
            continue
        source_col = reverse.get(field)
        row[field] = _num((csv_row or {}).get(source_col)) if source_col else 0.0

    if not row.get("net_revenue") and row.get("gross_revenue"):
        row["net_revenue"] = _num(row.get("gross_revenue")) / 1.1

    row["receipts_count"] = int(round(_num(row.get("receipts_count"))))
    row["source_payload"] = json.dumps(
        {
            "import_type": "historical_csv",
            "mapping": mapping,
            "original_row": csv_row,
            "report_metrics": {field: row.get(field) for field in _HISTORICAL_IMPORT_FIELDS if field not in {"store_code", "business_date"}},
            "report_metric_labels": _REPORT_METRIC_LABELS,
        },
        ensure_ascii=False,
        default=str,
    )
    return upsert_daily_sales(row)


def delete_daily_sales_day(*, store_code: str, data_iso: str, tenant_key: str | None = None) -> Dict[str, Any]:
    ensure_daily_sales_schema()
    tenant = str(tenant_key or DEFAULT_TENANT_KEY).strip() or "default"
    day = _date_only(data_iso)
    with _conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
DELETE FROM dbo.StoreHubDailySales
 WHERE tenant_key = ? AND store_code = ? AND business_date = ?
""",
            tenant,
            str(store_code).strip(),
            day,
        )
        deleted = int(cur.rowcount or 0)
        conn.commit()
    return {"ok": True, "deleted": deleted}


def get_daily_sales_day(*, store_code: str, data_iso: str, tenant_key: str | None = None) -> Dict[str, Any] | None:
    ensure_daily_sales_schema()
    tenant = str(tenant_key or DEFAULT_TENANT_KEY).strip() or "default"
    day = _date_only(data_iso)
    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT tenant_key, store_code, business_date, gross_revenue, net_revenue, cancelled_amount,
       receipts_count, pos_amount, cash_amount, ticket_total, ticket_cash_effect,
       delivery_total, delivery_online_amount, delivery_cash_amount, delivery_cash_effect,
       coupon_total, coupon_cash_effect, expenses_net, cash_deposits_total, cash_difference,
       source, updated_at
  FROM dbo.StoreHubDailySales
 WHERE tenant_key = ? AND store_code = ? AND business_date = ?
""",
            tenant,
            str(store_code).strip(),
            day,
        )
        r = cur.fetchone()
        if not r:
            return None
        cols = [d[0] for d in cur.description]
        return {cols[i]: r[i] for i in range(len(cols))}


def list_daily_sales_range(
    *,
    store_code: str,
    start_day: date,
    end_day: date,
    tenant_key: str | None = None,
    include_legacy_fallback: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Ritorna vendite giornaliere StoreHub-native, con fallback legacy DatiDatabase.

    Chiavi output: ISO date.

    Il fallback legacy Ã¨ intenzionalmente minimale: ricostruisce gross/net revenue
    da DatiDatabase, ma non inventa campi che la tabella legacy non espone in modo
    affidabile. I nuovi tenant potranno chiamare questa funzione con
    include_legacy_fallback=False.
    """
    if end_day < start_day:
        start_day, end_day = end_day, start_day

    ensure_daily_sales_schema()
    tenant = str(tenant_key or DEFAULT_TENANT_KEY).strip() or "default"
    out: Dict[str, Dict[str, Any]] = {}

    with _conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(
            """
SELECT tenant_key, store_code, business_date, gross_revenue, net_revenue, cancelled_amount,
       receipts_count, pos_amount, cash_amount, ticket_total, ticket_cash_effect,
       delivery_total, delivery_online_amount, delivery_cash_amount, delivery_cash_effect,
       coupon_total, coupon_cash_effect, expenses_net, cash_deposits_total, cash_difference,
       source, updated_at
  FROM dbo.StoreHubDailySales
 WHERE tenant_key = ? AND store_code = ? AND business_date >= ? AND business_date <= ?
 ORDER BY business_date
""",
            tenant,
            str(store_code).strip(),
            start_day,
            end_day,
        )
        cols = [d[0] for d in cur.description]
        for r in cur.fetchall() or []:
            row = {cols[i]: r[i] for i in range(len(cols))}
            d_val = row.get("business_date")
            if isinstance(d_val, datetime):
                d_iso = d_val.date().isoformat()
            elif isinstance(d_val, date):
                d_iso = d_val.isoformat()
            else:
                d_iso = str(d_val or "")[:10]
            if d_iso:
                row["source_family"] = "storehub"
                out[d_iso] = row

    if not include_legacy_fallback:
        return out

    missing: list[date] = []
    cur_day = start_day
    while cur_day <= end_day:
        if cur_day.isoformat() not in out:
            missing.append(cur_day)
        cur_day += timedelta(days=1)

    if not missing:
        return out

    try:
        legacy = list_legacy_daily_sales_range(store_code=str(store_code), start_day=start_day, end_day=end_day) or {}
    except Exception:
        legacy = {}

    for d in missing:
        d_iso = d.isoformat()
        legacy_row = legacy.get(d_iso) or {}
        gross = _num(legacy_row.get("gross_revenue"))
        if not gross:
            continue
        out[d_iso] = {
            "tenant_key": tenant,
            "store_code": str(store_code).strip(),
            "business_date": d,
            "gross_revenue": gross,
            "net_revenue": gross / 1.1,
            "cancelled_amount": 0.0,
            "receipts_count": int(round(_num(legacy_row.get("receipts_count")))),
            "pos_amount": 0.0,
            "cash_amount": 0.0,
            "ticket_total": 0.0,
            "ticket_cash_effect": 0.0,
            "delivery_total": _num(legacy_row.get("delivery_total")),
            "delivery_online_amount": 0.0,
            "delivery_cash_amount": 0.0,
            "delivery_cash_effect": 0.0,
            "coupon_total": 0.0,
            "coupon_cash_effect": 0.0,
            "expenses_net": 0.0,
            "cash_deposits_total": 0.0,
            "cash_difference": 0.0,
            "source": "legacy_datidatabase",
            "source_family": "legacy",
            "updated_at": None,
        }

    return out


def get_revenues_net_range(
    *,
    store_code: str,
    start_day: date,
    end_day: date,
    tenant_key: str | None = None,
    include_legacy_fallback: bool = True,
) -> float:
    rows = list_daily_sales_range(
        store_code=store_code,
        start_day=start_day,
        end_day=end_day,
        tenant_key=tenant_key,
        include_legacy_fallback=include_legacy_fallback,
    )
    return sum(_num(row.get("net_revenue")) for row in rows.values())


def get_revenues_gross_range(
    *,
    store_code: str,
    start_day: date,
    end_day: date,
    tenant_key: str | None = None,
    include_legacy_fallback: bool = True,
) -> float:
    rows = list_daily_sales_range(
        store_code=store_code,
        start_day=start_day,
        end_day=end_day,
        tenant_key=tenant_key,
        include_legacy_fallback=include_legacy_fallback,
    )
    return sum(_num(row.get("gross_revenue")) for row in rows.values())
