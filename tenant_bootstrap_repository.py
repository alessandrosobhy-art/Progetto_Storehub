from __future__ import annotations

from typing import Any, Callable, Dict, List

from app_db import get_connection_sqlserver_database, storehub_database_context


def _check_db(database_name: str) -> None:
    with get_connection_sqlserver_database(database_name, read_only=False) as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()


def initialize_tenant_database(database_name: str, tenant_key: str | None = None) -> Dict[str, Any]:
    db_name = str(database_name or "").strip()
    if not db_name:
        raise ValueError("Database tenant obbligatorio.")
    tenant = str(tenant_key or "default").strip() or "default"

    steps: List[Dict[str, Any]] = []

    def run_step(label: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
            steps.append({"label": label, "ok": True, "error": ""})
        except Exception as exc:
            steps.append({"label": label, "ok": False, "error": str(exc)})

    _check_db(db_name)
    with storehub_database_context(db_name):
        from cash_statement_config_repository import seed_default_cash_statement_config
        from cruscotto_pnl_store_repository import ensure_pnl_store_visibility_schema
        from daily_sales_repository import ensure_daily_sales_schema
        from delivery_repository import ensure_delivery_providers_schema, ensure_delivery_weekly_schema
        from distinta_cassa_ipratico_repository import ensure_distinta_cassa_ipratico_schema
        from distinta_cassa_photo_repository import ensure_distinta_cassa_photo_schema
        from finance_store_mapping_repository import ensure_finance_store_mapping_tables
        from ipratico_config_repository import ensure_ipratico_config_schema
        from kpi_notes_repository import ensure_kpi_period_notes_schema
        from mbo_repository import ensure_mbo_schema
        from orari_repository import ensure_turni_schema
        from orari_visibility_repository import ensure_orari_visibility_table
        from performance_repository import ensure_tenant_performance_indexes
        from rendiconto_legacy_schema_repository import ensure_rendiconto_legacy_schema
        from rendiconto_convalide_repository import _ensure_table as ensure_rendiconto_convalide_table
        from sales_repository import ensure_sales_forecast_schema
        from staff_repository import ensure_staff_schema
        from store_registry_repository import ensure_store_registry_schema, seed_store_registry_from_ilp
        from supplier_order_flow_repository import ensure_supplier_order_flow_schema
        from supplier_orders_repository import ensure_price_lists_schema, ensure_supplier_orders_schema
        from translation_repository import ensure_translations_schema, seed_pilot_translations
        from warehouse_schema_repository import ensure_warehouse_operational_schema

        run_step("Anagrafica store tenant", ensure_store_registry_schema)
        run_step("Magazzino operativo", ensure_warehouse_operational_schema)
        run_step("Daily sales", ensure_daily_sales_schema)
        run_step("Delivery weekly", ensure_delivery_weekly_schema)
        run_step("Delivery providers", ensure_delivery_providers_schema)
        run_step("Sales forecast", ensure_sales_forecast_schema)
        run_step("Note KPI", ensure_kpi_period_notes_schema)
        run_step("Rendiconto legacy operativo", ensure_rendiconto_legacy_schema)
        run_step("Distinta cassa - configurazione", lambda: seed_default_cash_statement_config(tenant_key=tenant))
        run_step("Distinta cassa - foto", ensure_distinta_cassa_photo_schema)
        run_step("Distinta cassa - convalide", ensure_rendiconto_convalide_table)
        run_step("iPratico", ensure_ipratico_config_schema)
        run_step("iPratico snapshot", ensure_distinta_cassa_ipratico_schema)
        run_step("Listini e fornitori", ensure_supplier_orders_schema)
        run_step("Listini multipli", ensure_price_lists_schema)
        run_step("Ordini fornitori", ensure_supplier_order_flow_schema)
        run_step("MBO", ensure_mbo_schema)
        run_step("P&L store visibilita", ensure_pnl_store_visibility_schema)
        run_step("Staff", ensure_staff_schema)
        run_step("Turni staff", ensure_turni_schema)
        run_step("Orari visibilita", ensure_orari_visibility_table)
        run_step("Finance store mapping", ensure_finance_store_mapping_tables)
        run_step("Traduzioni", ensure_translations_schema)
        run_step("Traduzioni base", seed_pilot_translations)
        run_step("Ottimizzazione indici tenant", ensure_tenant_performance_indexes)
        if db_name.upper() in {"APP_STOREHUB", "APP_STOREHUB_DEFAULT"}:
            def seed_default_store_registry() -> None:
                try:
                    from db_integration import get_warehouse_stores

                    stores = get_warehouse_stores(include_inactive=True) or []
                except Exception:
                    stores = []
                seed_store_registry_from_ilp(stores, only_missing=True)

            run_step("Seed anagrafica store default", seed_default_store_registry)

    ok = all(step["ok"] for step in steps)
    return {"ok": ok, "database_name": db_name, "tenant_key": tenant, "steps": steps}
