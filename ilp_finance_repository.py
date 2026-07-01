from __future__ import annotations

import re
from typing import Any, Dict, List

from app_db import get_connection_sqlserver_database


TABLES: Dict[str, Dict[str, Any]] = {
    "transazioni": {
        "db_table": "dbo.[Transazioni]",
        "title": "Transazioni",
        "store_column": "Store",
        "type_column": "Tipo",
        "type_label": "Tipo",
        "search_columns": ["Banca", "Societa", "Tipo", "Categoria", "Store", "Descrizione", "Note"],
        "columns": [
            ("Id", "ID"),
            ("DataContabile", "Data contabile"),
            ("DataValuta", "Data valuta"),
            ("Societa", "Società"),
            ("Banca", "Banca"),
            ("Importo", "Importo"),
            ("Tipo", "Tipo"),
            ("Categoria", "Categoria"),
            ("Store", "Store"),
            ("Descrizione", "Descrizione"),
            ("Note", "Note"),
            ("DataInserimento", "Inserito il"),
        ],
    },
    "cashin": {
        "db_table": "dbo.[CashIn]",
        "title": "Cash In",
        "store_column": "Store",
        "type_column": "Categoria",
        "type_label": "Categoria",
        "search_columns": ["Banca", "Societa", "Categoria", "Store", "Descrizione", "Note"],
        "columns": [
            ("Id", "ID"),
            ("DataContabile", "Data contabile"),
            ("DataValuta", "Data valuta"),
            ("Societa", "Società"),
            ("Banca", "Banca"),
            ("Importo", "Importo"),
            ("Categoria", "Categoria"),
            ("Store", "Store"),
            ("Descrizione", "Descrizione"),
            ("Note", "Note"),
            ("DataInserimento", "Inserito il"),
        ],
    },
    "cashout": {
        "db_table": "dbo.[CashOut]",
        "title": "Cash Out",
        "store_column": "Store",
        "type_column": "Categoria",
        "type_label": "Categoria",
        "search_columns": ["Banca", "Societa", "Categoria", "Store", "Descrizione", "Note"],
        "columns": [
            ("Id", "ID"),
            ("DataContabile", "Data contabile"),
            ("DataValuta", "Data valuta"),
            ("Societa", "Società"),
            ("Banca", "Banca"),
            ("Importo", "Importo"),
            ("Categoria", "Categoria"),
            ("Store", "Store"),
            ("Descrizione", "Descrizione"),
            ("Note", "Note"),
            ("DataInserimento", "Inserito il"),
        ],
    },
    "versamenti": {
        "db_table": "dbo.[Versamenti]",
        "store_column": "",
        "type_column": "TipoVersamento",
        "type_label": "Tipo versamento",
        "title": "Versamenti",
        "search_columns": ["Banca", "Societa", "TipoVersamento", "NotaVers", "NumeroTessera", "NumeroATM", "Orario"],
        "columns": [
            ("Id", "ID"),
            ("DataContabile", "Data contabile"),
            ("DataValuta", "Data valuta"),
            ("Societa", "Società"),
            ("Banca", "Banca"),
            ("Importo", "Importo"),
            ("TipoVersamento", "Tipo versamento"),
            ("NotaVers", "Nota"),
            ("NumeroTessera", "Numero tessera"),
            ("NumeroATM", "Numero ATM"),
            ("Orario", "Orario"),
            ("DataInserimento", "Inserito il"),
        ],
    },
}

DATE_FIELDS: Dict[str, str] = {
    "DataContabile": "TRY_CONVERT(date, NULLIF([DataContabile], ''), 3)",
    "DataValuta": "TRY_CONVERT(date, NULLIF([DataValuta], ''), 3)",
    "DataInserimento": "TRY_CONVERT(date, [DataInserimento])",
}


def _cfg(table_key: str) -> Dict[str, Any]:
    key = str(table_key or "").strip().lower()
    cfg = TABLES.get(key)
    if not cfg:
        raise ValueError("Tabella finance non valida.")
    return cfg


def _sanitize_like(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _build_where(
    *,
    cfg: Dict[str, Any],
    date_field: str,
    start_iso: str,
    end_iso: str,
    store_values: List[str] | None,
    include_blank_store: bool,
    banca: str,
    societa: str,
    type_value: str,
    search: str,
    tessera: str,
    atm: str,
) -> tuple[str, List[Any]]:
    where_parts: List[str] = ["1=1"]
    params: List[Any] = []

    date_expr = DATE_FIELDS.get(date_field) or DATE_FIELDS["DataContabile"]
    if start_iso:
        where_parts.append(f"{date_expr} >= ?")
        params.append(start_iso)
    if end_iso:
        where_parts.append(f"{date_expr} <= ?")
        params.append(end_iso)

    store_col = str(cfg.get("store_column") or "").strip()
    selected_stores = [str(x or "").strip() for x in (store_values or []) if str(x or "").strip()]
    if store_col and selected_stores:
        placeholders = ",".join("?" for _ in selected_stores)
        store_expr = f"LTRIM(RTRIM([{store_col}]))"
        clause = f"{store_expr} IN ({placeholders})"
        if include_blank_store:
            clause = f"({clause} OR NULLIF({store_expr}, '') IS NULL)"
        where_parts.append(clause)
        params.extend(selected_stores)
    elif store_col and not include_blank_store and store_values == []:
        where_parts.append("1=0")

    if banca := _sanitize_like(banca):
        where_parts.append("UPPER(ISNULL([Banca], '')) LIKE ?")
        params.append(f"%{banca.upper()}%")

    if societa := _sanitize_like(societa):
        where_parts.append("UPPER(ISNULL([Societa], '')) LIKE ?")
        params.append(f"%{societa.upper()}%")

    type_col = str(cfg.get("type_column") or "").strip()
    if type_col and (type_value := _sanitize_like(type_value)):
        where_parts.append(f"UPPER(ISNULL([{type_col}], '')) LIKE ?")
        params.append(f"%{type_value.upper()}%")

    if "NumeroTessera" in [c for c, _ in cfg.get("columns") or []] and (tessera := _sanitize_like(tessera)):
        where_parts.append("REPLACE(ISNULL([NumeroTessera], ''), ' ', '') LIKE ?")
        params.append(f"%{tessera.replace(' ', '')}%")

    if "NumeroATM" in [c for c, _ in cfg.get("columns") or []] and (atm := _sanitize_like(atm)):
        where_parts.append("UPPER(ISNULL([NumeroATM], '')) LIKE ?")
        params.append(f"%{atm.upper()}%")

    if search := _sanitize_like(search):
        search_parts: List[str] = []
        for col in cfg.get("search_columns") or []:
            search_parts.append(f"UPPER(ISNULL([{col}], '')) LIKE ?")
            params.append(f"%{search.upper()}%")
        if search_parts:
            where_parts.append("(" + " OR ".join(search_parts) + ")")

    return " WHERE " + " AND ".join(where_parts), params


def list_finance_rows(
    *,
    table_key: str,
    date_field: str = "DataContabile",
    start_iso: str = "",
    end_iso: str = "",
    store_values: List[str] | None = None,
    include_blank_store: bool = False,
    banca: str = "",
    societa: str = "",
    type_value: str = "",
    search: str = "",
    tessera: str = "",
    atm: str = "",
    page: int = 1,
    page_size: int = 200,
    export_all: bool = False,
) -> Dict[str, Any]:
    cfg = _cfg(table_key)
    page = max(1, int(page or 1))
    export_all = bool(export_all)
    page_size = max(20, min(500, int(page_size or 200)))

    where_sql, params = _build_where(
        cfg=cfg,
        date_field=date_field,
        start_iso=start_iso,
        end_iso=end_iso,
        store_values=store_values,
        include_blank_store=include_blank_store,
        banca=banca,
        societa=societa,
        type_value=type_value,
        search=search,
        tessera=tessera,
        atm=atm,
    )

    selected_cols = ", ".join(f"[{c}]" for c, _ in cfg.get("columns") or [])
    table_name = str(cfg["db_table"])
    order_sql = " ORDER BY TRY_CONVERT(datetime, [DataInserimento]) DESC, [Id] DESC"

    conn = get_connection_sqlserver_database("ILP_FINANCE", read_only=True)
    try:
        cur = conn.cursor()

        count_sql = f"SELECT COUNT(*) AS total_rows, COALESCE(SUM([Importo]), 0) AS total_importo FROM {table_name}{where_sql}"
        cur.execute(count_sql, params)
        count_row = cur.fetchone()
        total_rows = int((count_row[0] if count_row else 0) or 0)
        total_importo = float((count_row[1] if count_row else 0) or 0.0)

        if export_all:
            data_sql = f"SELECT {selected_cols} FROM {table_name}{where_sql}{order_sql}"
            cur.execute(data_sql, params)
        else:
            offset = (page - 1) * page_size
            data_sql = (
                f"SELECT {selected_cols} "
                f"FROM {table_name}{where_sql}{order_sql} "
                f"OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
            )
            cur.execute(data_sql, [*params, offset, page_size])
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()

    return {
        "table": str(table_key or "").strip().lower(),
        "title": cfg.get("title") or str(table_key or "").strip().title(),
        "columns": [{"key": c, "label": lbl} for c, lbl in (cfg.get("columns") or [])],
        "type_label": cfg.get("type_label") or "Tipo",
        "has_store": bool(cfg.get("store_column")),
        "has_tessera": any(c == "NumeroTessera" for c, _ in (cfg.get("columns") or [])),
        "has_atm": any(c == "NumeroATM" for c, _ in (cfg.get("columns") or [])),
        "rows": rows,
        "page": page,
        "page_size": page_size,
        "total_rows": total_rows,
        "total_importo": total_importo,
        "pages": 1 if export_all else max(1, (total_rows + page_size - 1) // page_size),
    }


def list_distinct_store_values(table_key: str) -> List[str]:
    cfg = _cfg(table_key)
    store_col = str(cfg.get("store_column") or "").strip()
    if not store_col:
        return []

    conn = get_connection_sqlserver_database("ILP_FINANCE", read_only=True)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT DISTINCT LTRIM(RTRIM([{store_col}])) AS store_name
            FROM {cfg['db_table']}
            WHERE NULLIF(LTRIM(RTRIM([{store_col}])), '') IS NOT NULL
            ORDER BY LTRIM(RTRIM([{store_col}]))
            """
        )
        return [str((row[0] or "")).strip() for row in cur.fetchall() if str((row[0] or "")).strip()]
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()
