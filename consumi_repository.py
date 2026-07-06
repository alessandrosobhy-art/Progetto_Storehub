from __future__ import annotations

from app_logging import log_swallowed
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app_db import get_connection

import delivery_repository
import inventory_repository

# Riutilizziamo alcune utilities (parsing date e bucket) già validate nella pagina Analisi
from analysis_repository import (
    get_revenues_net,
    _parse_any_date_to_iso,
    _iter_month_starts,
    _bucket_from_group,
    _detect_group_col,
)


def _safe_float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except Exception:
        try:
            return float(str(v).strip().replace(".", "").replace(",", "."))
        except Exception:
            return 0.0


def _parse_iso_date(s: str) -> date:
    ss = (s or "").strip()[:10]
    return datetime.strptime(ss, "%Y-%m-%d").date()


def _detect_code_col(cols: List[str]) -> str:
    cols_map = {str(c).strip().lower(): c for c in cols or []}
    for key in ("codice", "code", "articolo", "sku"):
        if key in cols_map:
            return cols_map[key]
    # fallback: try contains
    for c in cols or []:
        k = str(c).strip().lower()
        if "cod" in k and "codice" in k:
            return c
    return ""


def _detect_unit_col(cols: List[str]) -> str:
    cols_map = {str(c).strip().lower().replace("_", "").replace(" ", ""): c for c in cols or []}
    for key in (
        "qtacar",
        "pezzi_per_collo",
        "pezzi per collo",
        "pezxcollo",
        "unit",
        "units",
    ):
        kk = str(key).strip().lower().replace("_", "").replace(" ", "")
        if kk in cols_map:
            return cols_map[kk]
    return ""


def _sum_delivery_car_by_code(
    store_code: str,
    start: date,
    end_inclusive: date,
    bucket: str,
    supplier_name: Optional[str],
    codes: set[str],
) -> Tuple[Dict[str, float], List[str]]:
    """Somma i colli (CAR) in DatiDelivery per codice prodotto."""
    warnings: List[str] = []
    bucket = bucket if bucket in ("FoodPaper", "Operating") else "FoodPaper"

    table = delivery_repository.get_delivery_table_name()
    conn = get_connection(store_code)
    try:
        layout = delivery_repository._detect_delivery_layout(conn, table)
        if layout.get("error"):
            return {}, [layout.get("error")]

        site_col = layout["site_col"]
        qta_col = layout["qta_col"]
        supplier_col = layout.get("supplier_col")
        date_col = layout.get("deliv_date_col") or layout.get("doc_date_col")
        cols = layout.get("columns") or []
        group_col = _detect_group_col(cols) or ""
        code_col = layout.get("code_col") or _detect_code_col(cols) or ""
        unit_col = _detect_unit_col(cols) or ""

        if not date_col:
            return {}, ["Colonna data non trovata in DatiDelivery"]
        if not code_col:
            return {}, ["Colonna codice prodotto non trovata in DatiDelivery"]
        if supplier_name and not supplier_col:
            warnings.append("DatiDelivery: colonna fornitore non trovata, filtro fornitore non applicabile.")

        out: Dict[str, float] = {}

        cur = conn.cursor()

        for m_start in _iter_month_starts(start, end_inclusive):
            mm_yyyy = f"{m_start.month:02d}/{m_start.year}"

            select_cols = [date_col, qta_col, code_col]
            if supplier_col:
                select_cols.append(supplier_col)
            if group_col:
                select_cols.append(group_col)
            if unit_col:
                select_cols.append(unit_col)

            rows = []
            # Prova 1: Month/Year su date native
            try:
                where = [f"[{site_col}] = ?", f"Month([{date_col}]) = ?", f"Year([{date_col}]) = ?"]
                params: List[Any] = [str(store_code), m_start.month, m_start.year]
                if supplier_name and supplier_col:
                    where.append(f"[{supplier_col}] = ?")
                    params.append(supplier_name)
                sql = (
                    f"SELECT {', '.join('['+c+']' for c in select_cols)} FROM [{table}] "
                    f"WHERE " + " AND ".join(where)
                )
                cur.execute(sql, params)
                rows = cur.fetchall()
            except Exception:
                # Prova 2: Right(date,7) = 'MM/YYYY' (date salvata come testo)
                where = [f"[{site_col}] = ?", f"Right([{date_col}], 7) = ?"]
                params2: List[Any] = [str(store_code), mm_yyyy]
                if supplier_name and supplier_col:
                    where.append(f"[{supplier_col}] = ?")
                    params2.append(supplier_name)
                sql = (
                    f"SELECT {', '.join('['+c+']' for c in select_cols)} FROM [{table}] "
                    f"WHERE " + " AND ".join(where)
                )
                cur.execute(sql, params2)
                rows = cur.fetchall()

            for r in rows:
                idx = 0
                raw_date = r[idx]; idx += 1
                raw_qta = r[idx]; idx += 1
                raw_code = r[idx]; idx += 1
                raw_supplier = None
                if supplier_col:
                    raw_supplier = r[idx]; idx += 1
                raw_group = None
                if group_col:
                    raw_group = r[idx]; idx += 1
                # unit col present but unused here

                if supplier_name and supplier_col:
                    if str(raw_supplier or "").strip() != str(supplier_name).strip():
                        continue

                d_iso = _parse_any_date_to_iso(raw_date)
                if not d_iso:
                    continue
                d = datetime.strptime(d_iso, "%Y-%m-%d").date()
                if d < start or d > end_inclusive:
                    continue

                code = str(raw_code or "").strip()
                if not code or code not in codes:
                    continue

                if group_col:
                    b = _bucket_from_group(raw_group)
                    if b != bucket:
                        continue

                out[code] = out.get(code, 0.0) + _safe_float(raw_qta)

        return out, warnings
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('consumi_repository:185')


def _sum_inventory_totpz_by_code(
    store_code: str,
    table_name: str,
    mov_type: str,
    start: date,
    end_inclusive: date,
    bucket: str,
    supplier_name: Optional[str],
    codes: set[str],
) -> Tuple[Dict[str, float], List[str]]:
    """Somma TOTPZ per codice prodotto su una tabella tipo inventario (DatiInventario o DatiTX)."""
    warnings: List[str] = []
    bucket = bucket if bucket in ("FoodPaper", "Operating") else "FoodPaper"

    conn = get_connection(store_code)
    try:
        try:
            table = inventory_repository._resolve_table_name(
                conn,
                table_name,
                extra_candidates=[table_name, table_name.upper(), table_name.lower()],
            )
        except Exception as ex:
            return {}, [f"Tabella '{table_name}' non trovata: {ex}"]

        cols = inventory_repository._get_table_columns(conn, table)
        require_site2 = table_name.lower() in ("datitx", "tx", "dati tx")
        layout = inventory_repository._detect_inventory_layout(cols, require_site2=require_site2)

        group_col = _detect_group_col(cols) or ""
        supplier_col = inventory_repository._detect_supplier_col(cols) or ""
        code_col = _detect_code_col(cols) or ""

        if not code_col:
            return {}, [f"Colonna codice prodotto non trovata in tabella {table}."]
        if supplier_name and not supplier_col:
            warnings.append(f"{table}: colonna fornitore non trovata, filtro fornitore non applicabile.")

        start_dt = datetime(start.year, start.month, start.day)
        end_excl = datetime(end_inclusive.year, end_inclusive.month, end_inclusive.day) + timedelta(days=1)

        select_cols = [layout["date_col"], layout["totpz_col"], code_col]
        if group_col:
            select_cols.append(group_col)
        if supplier_col:
            select_cols.append(supplier_col)
        mov_type_u = (mov_type or "").strip().upper()
        site_filter_col = layout["site_col"]
        if require_site2 and mov_type_u == "TXIN":
            site_filter_col = layout.get("site2_col") or layout["site_col"]
            mov_like = "TXOUT%"
        elif require_site2 and mov_type_u == "TXOUT":
            mov_like = "TXOUT%"
        else:
            mov_like = mov_type_u + "%"

        where = [
            f"[{site_filter_col}] = ?",
            f"UPPER(LTRIM(RTRIM([{layout['mov_col']}]))) LIKE ?",
            f"[{layout['date_col']}] >= ?",
            f"[{layout['date_col']}] < ?",
        ]
        params: List[Any] = [str(store_code), mov_like, start_dt, end_excl]
        if supplier_name and supplier_col:
            where.append(f"[{supplier_col}] = ?")
            params.append(supplier_name)

        sql = f"SELECT {', '.join('['+c+']' for c in select_cols)} FROM [{table}] WHERE " + " AND ".join(where)

        cur = conn.cursor()
        out: Dict[str, float] = {}
        try:
            cur.execute(sql, params)
            rows = cur.fetchall()
        except Exception:
            # Fallback: la data potrebbe essere testo (dd/mm/yyyy). Provo a leggere per mesi e filtrare in Python.
            rows = []
            for m_start in _iter_month_starts(start, end_inclusive):
                mm_yyyy = f"{m_start.month:02d}/{m_start.year}"
                try:
                    where2 = [
                        f"[{site_filter_col}] = ?",
                        f"UPPER(LTRIM(RTRIM([{layout['mov_col']}]))) LIKE ?",
                        f"Right([{layout['date_col']}], 7) = ?",
                    ]
                    params2: List[Any] = [str(store_code), mov_like, mm_yyyy]
                    if supplier_name and supplier_col:
                        where2.append(f"[{supplier_col}] = ?")
                        params2.append(supplier_name)
                    sql2 = (
                        f"SELECT {', '.join('['+c+']' for c in select_cols)} FROM [{table}] "
                        f"WHERE " + " AND ".join(where2)
                    )
                    cur.execute(sql2, params2)
                    rows.extend(cur.fetchall())
                except Exception:
                    continue

        for r in rows:
            idx = 0
            raw_date = r[idx]; idx += 1
            raw_pz = r[idx]; idx += 1
            raw_code = r[idx]; idx += 1
            raw_group = None
            if group_col:
                raw_group = r[idx]; idx += 1
            raw_supplier = None
            if supplier_col:
                raw_supplier = r[idx]

            if supplier_name and supplier_col:
                if str(raw_supplier or "").strip() != str(supplier_name).strip():
                    continue

            d_iso = _parse_any_date_to_iso(raw_date)
            if not d_iso:
                continue
            d = datetime.strptime(d_iso, "%Y-%m-%d").date()
            if d < start or d > end_inclusive:
                continue

            code = str(raw_code or "").strip()
            if not code or code not in codes:
                continue

            if group_col:
                b = _bucket_from_group(raw_group)
                if b != bucket:
                    continue

            out[code] = out.get(code, 0.0) + _safe_float(raw_pz)

        return out, warnings
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('consumi_repository:325')


def get_product_consumption_table(
    store_code: str,
    start_iso: str,
    end_iso: str,
    supplier_code: str,
    listino: str,
) -> Dict[str, Any]:
    """Calcola il consumo in pezzi per tutti i prodotti di un fornitore/listino."""
    start = _parse_iso_date(start_iso)
    end = _parse_iso_date(end_iso)
    if end < start:
        start, end = end, start

    inv_init_date = start - timedelta(days=1)
    listino = (listino or "").strip() or "FoodPaper"
    if listino not in ("FoodPaper", "Operating"):
        listino = "FoodPaper"

    revenues_net, rev_warn = get_revenues_net(store_code, start, end)
    warnings: List[str] = []
    warnings.extend(rev_warn)

    price = delivery_repository.get_price_list_for_supplier(store_code, supplier_code)
    if price.get("error"):
        return {"error": price.get("error"), "warnings": warnings}

    code_col = price.get("code_column")
    desc_col = price.get("desc_column")
    unit_col = price.get("unit_column")
    cols = price.get("columns") or []
    group_col = _detect_group_col(cols) or ""

    rows_all = price.get("rows") or []
    # seleziona solo il listino richiesto
    rows = [r for r in rows_all if str(r.get("_TipoListino") or "").strip() == listino]

    if not rows:
        return {
            "error": None,
            "warnings": warnings + [f"Nessun prodotto trovato nel listino {listino} per il fornitore selezionato."],
            "period": {"start": start.isoformat(), "end": end.isoformat(), "inv_initial_date": inv_init_date.isoformat()},
            "supplier": supplier_code,
            "listino": listino,
            "revenues_net": float(revenues_net),
            "rows": [],
        }

    if not code_col or not desc_col:
        return {"error": "Listino: colonne Codice/Descrizione non riconosciute.", "warnings": warnings}

    def _sort_key(r: Dict[str, Any]):
        return str(r.get(desc_col) or "").strip().lower()

    rows_sorted = sorted(rows, key=_sort_key)

    products: List[Dict[str, Any]] = []
    codes: set[str] = set()
    for r in rows_sorted:
        code = str(r.get(code_col) or "").strip()
        if not code:
            continue
        codes.add(code)
        products.append(
            {
                "code": code,
                "desc": str(r.get(desc_col) or "").strip(),
                "group": str(r.get(group_col) or "").strip() if group_col else "",
                "qtacar": _safe_float(r.get(unit_col)) if unit_col else 0.0,
            }
        )

    if not codes:
        return {"error": "Listino: nessun codice prodotto valido trovato.", "warnings": warnings}

    # componenti del calcolo
    delivery_car, w1 = _sum_delivery_car_by_code(store_code, start, end, listino, supplier_code, codes)
    inv_init, w2 = _sum_inventory_totpz_by_code(store_code, "DatiInventario", "INV", inv_init_date, inv_init_date, listino, supplier_code, codes)
    tx_in, w3 = _sum_inventory_totpz_by_code(store_code, "DatiTX", "TXIN", start, end, listino, supplier_code, codes)
    tx_out, w4 = _sum_inventory_totpz_by_code(store_code, "DatiTX", "TXOUT", start, end, listino, supplier_code, codes)
    inv_fin, w5 = _sum_inventory_totpz_by_code(store_code, "DatiInventario", "INV", end, end, listino, supplier_code, codes)

    warnings.extend(w1 + w2 + w3 + w4 + w5)

    out_rows: List[Dict[str, Any]] = []
    for p in products:
        code = p["code"]
        qta_car = float(p.get("qtacar") or 0.0)
        inv_i = float(inv_init.get(code, 0.0))
        car = float(delivery_car.get(code, 0.0))
        del_pz = float(car * qta_car) if qta_car > 0 else 0.0
        txi = float(tx_in.get(code, 0.0))
        txo = float(tx_out.get(code, 0.0))
        inv_f = float(inv_fin.get(code, 0.0))
        consumo = float(inv_i + del_pz + txi - txo - inv_f)
        per1000 = float((consumo / revenues_net) * 1000.0) if revenues_net > 0 else 0.0

        out_rows.append(
            {
                "code": code,
                "desc": p.get("desc") or "",
                "group": p.get("group") or "",
                "qtacar": qta_car,
                "inv_initial_pz": inv_i,
                "delivery_car": car,
                "delivery_pz": del_pz,
                "tx_in_pz": txi,
                "tx_out_pz": txo,
                "inv_final_pz": inv_f,
                "consumption_pz": consumo,
                "consumption_per_1000": per1000,
            }
        )

    return {
        "error": None,
        "warnings": warnings,
        "period": {"start": start.isoformat(), "end": end.isoformat(), "inv_initial_date": inv_init_date.isoformat()},
        "supplier": supplier_code,
        "listino": listino,
        "revenues_net": float(revenues_net),
        "rows": out_rows,
    }