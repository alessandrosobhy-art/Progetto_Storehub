from __future__ import annotations

import math
import statistics
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app_db import get_connection, get_backend, sql_date, sql_trim
from delivery_repository import get_price_list_for_supplier, get_suppliers_for_store
from inventory_repository import get_conversions_for_supplier
from sales_repository import list_sales_week

SCRIPT_VERSION = "orders_repository_v1.5.2_cover_from_order_to_delivery2"

_Z = 1.64
_SIGMA_DAYS_DEFAULT = 42
_SIGMA_POOL_DAYS = 120
_SIGMA_MIN_DAYS = 7


@dataclass
class _InvRow:
    inv_date: date
    inv_qty: float


def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _norm_key(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("_", "")


def _get_cols(cur) -> List[str]:
    if not getattr(cur, "description", None):
        return []
    return [c[0] for c in cur.description if c and c[0]]


def _stddev(vals: List[float]) -> float:
    vals = [float(v) for v in vals if v is not None]
    if len(vals) < 2:
        return 0.0
    try:
        return float(statistics.pstdev(vals))
    except Exception:
        try:
            return float(statistics.stdev(vals))
        except Exception:
            return 0.0


def _pick_continuous_sigma_window(rev_daily: Dict[date, float], sigma_end: date, max_days: int) -> Tuple[int, Optional[date], Optional[date]]:
    streak = 0
    for i in range(max_days):
        d = sigma_end - timedelta(days=i)
        if d in rev_daily:
            streak += 1
        else:
            break
    if streak < _SIGMA_MIN_DAYS:
        return 0, None, None
    used = min(max_days, streak)
    start = sigma_end - timedelta(days=used - 1)
    return used, start, sigma_end


def _sum_daily_map(m: Dict[date, float], start_d: date, end_d: date) -> float:
    if end_d < start_d:
        return 0.0
    tot = 0.0
    d = start_d
    while d <= end_d:
        tot += float(m.get(d, 0.0))
        d += timedelta(days=1)
    return float(tot)


def _parse_day(x: str) -> date:
    return datetime.fromisoformat(x).date()


def _ensure_sql_backend():
    if get_backend() != "sqlserver":
        raise RuntimeError("La funzionalità Ordini richiede backend SQL Server (tabella condivisa).")


def _sql_date_any(expr: str) -> str:
    # expr può essere un campo (es. [Data]) o un placeholder '?'
    if get_backend() == "sqlserver":
        return f"COALESCE(TRY_CONVERT(date, {expr}), TRY_CONVERT(date, {expr}, 103), TRY_CONVERT(date, {expr}, 105))"
    return f"DateValue({expr})"



def _norm_spaces(s: str) -> str:
    return " ".join((s or "").strip().split())


def _normalize_listino(x: str) -> str:
    return (x or "").strip().lower().replace(" ", "")


def _row_get_any(row: Dict[str, Any], candidates: List[str]) -> Any:
    if not isinstance(row, dict):
        return None
    for c in candidates:
        if not c:
            continue
        if c in row and row.get(c) not in (None, ""):
            return row.get(c)
    nmap = {_norm_key(k): k for k in row.keys()}
    for c in candidates:
        if not c:
            continue
        kk = nmap.get(_norm_key(c))
        if kk is not None and row.get(kk) not in (None, ""):
            return row.get(kk)
    return None


def _filter_rows_by_listino(rows: List[Dict[str, Any]], listino: str) -> List[Dict[str, Any]]:
    l = _normalize_listino(listino)
    if not l or l in ("all", "tutti", "both"):
        return rows
    tipokey = "_TipoListino"
    f = [r for r in rows if _normalize_listino(str(r.get(tipokey) or r.get(tipokey.lower()) or "")) == l]
    if f:
        return f
    if l in ("foodpaper", "fp", "food"):
        f = [r for r in rows if _normalize_listino(str(r.get(tipokey) or r.get(tipokey.lower()) or "")) == "foodpaper"]
        return f or rows
    if l in ("operating", "op", "oper"):
        f = [r for r in rows if _normalize_listino(str(r.get(tipokey) or r.get(tipokey.lower()) or "")) == "operating"]
        return f or rows
    return rows


def _get_price_list(store_code: str, supplier_name: str, listino: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return (rows, info) for the selected supplier/listino.

    delivery_repository.get_price_list_for_supplier returns a dict with key 'rows' (merged FoodPaper+Operating).
    We normalize to list[dict] and apply listino filtering, with fallbacks if the supplier key differs (code vs name).
    """
    max_rows = int(os.getenv("ORDINI_PRICELIST_MAX_ROWS") or "5000")
    supplier_key = _norm_spaces(str(supplier_name))

    raw = get_price_list_for_supplier(str(store_code), supplier_key, max_rows=max_rows)
    info: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    rows = []
    if isinstance(raw, dict):
        rows = raw.get("rows") or []
    elif isinstance(raw, list):
        rows = raw
    rows = [r for r in rows if isinstance(r, dict)]
    rows = _filter_rows_by_listino(rows, listino)

    # Fallback: se il valore passato è il "name" ma il listino è filtrato per "code" (o viceversa)
    if not rows:
        try:
            sup = get_suppliers_for_store(str(store_code))
            sups = (sup.get("suppliers") or []) if isinstance(sup, dict) else []
            target = _norm_spaces(supplier_key).lower()

            def _n(x: Any) -> str:
                return _norm_spaces(str(x or "")).lower()

            match = None
            for s in sups:
                if not isinstance(s, dict):
                    continue
                if _n(s.get("name")) == target or _n(s.get("code")) == target:
                    match = s
                    break
            if match:
                alt = _norm_spaces(str(match.get("code") or match.get("name") or ""))
                if alt and alt != supplier_key:
                    raw2 = get_price_list_for_supplier(str(store_code), alt, max_rows=max_rows)
                    info2: Dict[str, Any] = raw2 if isinstance(raw2, dict) else {}
                    rows2 = (raw2.get("rows") or []) if isinstance(raw2, dict) else (raw2 if isinstance(raw2, list) else [])
                    rows2 = [r for r in rows2 if isinstance(r, dict)]
                    rows2 = _filter_rows_by_listino(rows2, listino)
                    if rows2:
                        return rows2, info2
        except Exception:
            pass

    return rows, info


def _get_last_inv_by_code(store_code: str, codes: List[str], up_to_day: date) -> Tuple[Dict[str, _InvRow], List[str]]:
    warnings: List[str] = []
    if not codes:
        return {}, warnings

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        table = "DatiInventario"

        cur.execute(f"SELECT TOP 1 * FROM {_qname(table)}")
        cols = _get_cols(cur)
        n = {_norm_key(c): c for c in cols}

        site_col = n.get("site", "Site")
        code_col = n.get("codice", "Codice")
        data_col = n.get("data", "Data")
        qta_col = n.get("totpz", None) or n.get("totpez", None) or n.get("qta", None) or n.get("pezzi", None) or n.get("qtapz", None) or "TOTPZ"
        mov_col = n.get("causale", None) or n.get("tipotrans", None) or n.get("movtype", None) or n.get("tipo", None) or "Causale"

        ph = ",".join(["?"] * len(codes))
        sql = (
            f"SELECT {_qname(code_col)} AS code, {_qname(data_col)} AS d, {_qname(qta_col)} AS q "
            f"FROM {_qname(table)} "
            f"WHERE {sql_trim(_qname(site_col))}=? AND {sql_trim(_qname(mov_col))} LIKE ? "
            f"AND {_sql_date_any(_qname(data_col))} <= ? "
            f"AND {sql_trim(_qname(code_col))} IN ({ph})"
        )
        params: List[Any] = [str(store_code), "INV%", up_to_day] + [str(c) for c in codes]
        cur.execute(sql, params)

        out: Dict[str, _InvRow] = {}
        for code, d, q in cur.fetchall():
            cd = str(code).strip()
            try:
                dd = d.date() if hasattr(d, "date") else _parse_day(str(d))
            except Exception:
                continue
            try:
                qq = float(q) if q is not None else 0.0
            except Exception:
                qq = 0.0

            prev = out.get(cd)
            if (prev is None) or (dd > prev.inv_date):
                out[cd] = _InvRow(inv_date=dd, inv_qty=qq)

        return out, warnings
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_agg_pz_by_code(store_code: str, table: str, codes: List[str], start_d: date, end_d: date, *, kind: str) -> Dict[str, float]:
    # kind: "delivery" -> DatiDelivery sum (pezzi oppure colli*qtacar)
    #       "txin" / "txout" -> DatiTX sum qta
    if not codes or end_d < start_d:
        return {}

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT TOP 1 * FROM {_qname(table)}")
        cols = _get_cols(cur)
        n = {_norm_key(c): c for c in cols}

        site_col = n.get("site", "Site")
        code_col = n.get("codice", "Codice")
        data_col = n.get("data", "Data")
        site2_col = n.get("site2", None)
        site_filter_col = site_col

        if kind == "delivery":
            car_col = n.get("car", None) or n.get("colli", None) or n.get("ncolli", None)
            qtacar_col = n.get("qtacar", None) or n.get("qtacollo", None) or n.get("qtapercollo", None)
            pz_col = n.get("totpz", None) or n.get("totpez", None) or n.get("pezzi", None) or n.get("pz", None) or n.get("qta", None)
            if pz_col:
                expr = f"SUM(COALESCE({_qname(pz_col)},0))"
            elif car_col and qtacar_col:
                expr = f"SUM(COALESCE({_qname(car_col)},0)*COALESCE({_qname(qtacar_col)},0))"
            else:
                expr = "SUM(0)"
            extra_where = ""
            extra_params: List[Any] = []
        else:
            qta_col = n.get("totpz", None) or n.get("totpez", None) or n.get("qta", None) or n.get("pezzi", None) or n.get("qtapz", None) or "TOTPZ"
            expr = f"SUM(COALESCE({_qname(qta_col)},0))"
            mov_col = n.get("causale", None) or n.get("tipotrans", None) or n.get("movtype", None) or n.get("tipo", None) or "Causale"
            extra_where = f" AND {sql_trim(_qname(mov_col))}=?"
            mov_value = "TXOUT"
            if kind == "txin":
                if site2_col:
                    # DatiTX: salva solo TXOUT con SITE2 = destinazione. TXIN = TXOUT dove SITE2 = store.
                    site_filter_col = site2_col
                    mov_value = "TXOUT"
                else:
                    mov_value = "TXIN"  # legacy
            extra_params = [mov_value]

        ph = ",".join(["?"] * len(codes))
        sql = (
            f"SELECT {sql_trim(_qname(code_col))} AS code, {expr} AS pz "
            f"FROM {_qname(table)} "
            f"WHERE {sql_trim(_qname(site_filter_col))}=? "
            f"AND {_sql_date_any(_qname(data_col))}>=? AND {_sql_date_any(_qname(data_col))}<=? "
            f"AND {sql_trim(_qname(code_col))} IN ({ph}) "
            f"{extra_where} "
            f"GROUP BY {sql_trim(_qname(code_col))}"
        )
        params: List[Any] = [str(store_code), start_d, end_d] + [str(c) for c in codes] + extra_params
        cur.execute(sql, params)

        out: Dict[str, float] = {}
        for code, pz in cur.fetchall():
            cd = str(code).strip()
            try:
                out[cd] = float(pz) if pz is not None else 0.0
            except Exception:
                out[cd] = 0.0
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_revenues_net_daily(store_code: str, start_d: date, end_d: date) -> Dict[date, float]:
    if end_d < start_d:
        return {}

    try:
        from daily_sales_repository import list_daily_sales_range

        rows = list_daily_sales_range(store_code=str(store_code), start_day=start_d, end_day=end_d)
        out: Dict[date, float] = {}
        for key, row in (rows or {}).items():
            try:
                d = key if isinstance(key, date) else datetime.strptime(str(key)[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            try:
                out[d] = float(row.get("net_revenue") or 0.0)
            except Exception:
                out[d] = 0.0
        return out
    except Exception:
        pass

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        table = "DatiDatabase"

        cur.execute(f"SELECT TOP 1 * FROM {_qname(table)}")
        cols = _get_cols(cur)
        n = {_norm_key(c): c for c in cols}

        site_col = n.get("site", "Site")
        data_col = n.get("data", "Data")

        # In questo progetto i ricavi vengono letti da DatiDatabase come *lordo* e convertiti a *netto* (lordo / 1.1),
        # coerente con gli altri moduli (cruscotto/analisi).
        # Evitiamo fallback a colonne non esistenti (es. "RevenuesNet") che causano 42S22.

        def _pick_rev_col_and_mode() -> Tuple[Optional[str], bool]:
            # returns (col_name, is_gross)
            # 1) preferisci colonne già nette
            net_candidates = [
                "revenuesnet",
                "revenuenet",
                "revenues_net",
                "netrevenues",
                "fatturatonetto",
                "fatturanetto",
                "ricavinetti",
            ]
            for k in net_candidates:
                kk = _norm_key(k)
                if kk in n:
                    return n[kk], False

            # 2) colonne lorde note
            gross_candidates = [
                "fatturatolordo",
                "giroaffarilordo",
                "giro affari lordo",
                "fatt lordo",
                "fatturato",
                "giroaffari",
                "giro affari",
                "sales",
                "revenue",
                "revenues",
                "ricavi",
            ]
            for k in gross_candidates:
                kk = _norm_key(k)
                if kk in n:
                    return n[kk], True

            # 3) heuristic: prova una colonna che “assomiglia” a fatturato/ricavi/revenue
            for nk, orig in n.items():
                if any(t in nk for t in ("fatt", "ricav", "revenue", "sales", "giroaffari")):
                    # se contiene "net" la consideriamo già netta, altrimenti lorda
                    is_gross = ("net" not in nk and "netto" not in nk)
                    return orig, is_gross

            return None, True

        rev_col, is_gross = _pick_rev_col_and_mode()

        if not rev_col:
            # Nessuna colonna ricavi individuata: ritorna mappa vuota (evita crash della API)
            return {}

        # Conversione robusta: se il campo è testo, TRY_CONVERT evita errori in SUM
        base_sum = f"SUM(COALESCE(TRY_CONVERT(float, {_qname(rev_col)}), 0))"
        rev_expr = f"({base_sum})/1.1" if is_gross else base_sum

        sql = (
            f"SELECT {_sql_date_any(_qname(data_col))} AS d, {rev_expr} AS rev "
            f"FROM {_qname(table)} "
            f"WHERE {sql_trim(_qname(site_col))}=? AND {_sql_date_any(_qname(data_col))}>=? AND {_sql_date_any(_qname(data_col))}<=? "
            f"GROUP BY {_sql_date_any(_qname(data_col))}"
        )
        cur.execute(sql, [str(store_code), start_d, end_d])

        out: Dict[date, float] = {}
        for d, rev in cur.fetchall():
            dd = d if isinstance(d, date) else (d.date() if hasattr(d, "date") else _parse_day(str(d)))
            try:
                out[dd] = float(rev) if rev is not None else 0.0
            except Exception:
                out[dd] = 0.0
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _sum_sales_forecast(store_code: str, start_d: date, end_d: date) -> Tuple[float, Dict[str, float]]:
    if end_d < start_d:
        return 0.0, {}
    out = list_sales_week(store_code=store_code, start_day=start_d, end_day=end_d)
    tot = 0.0
    for _, v in out.items():
        try:
            tot += float(v or 0.0)
        except Exception:
            pass
    return float(tot), out


def _month_range_previous(order_day: date) -> Tuple[date, date]:
    first_this = date(order_day.year, order_day.month, 1)
    prev_end = first_this - timedelta(days=1)
    prev_start = date(prev_end.year, prev_end.month, 1)
    return prev_start, prev_end


def build_orders_suggestion(*, store_code: str, supplier_name: str, listino: str, order_day: date, next_delivery_day: date) -> Dict[str, Any]:
    _ensure_sql_backend()

    warnings: List[str] = []
    if next_delivery_day <= order_day:
        raise ValueError("La data consegna successiva deve essere maggiore della data ordine.")

    price_list, pl_info = _get_price_list(store_code, supplier_name, listino)
    if isinstance(pl_info, dict) and pl_info.get("error"):
        warnings.append(f"[LISTINO] {pl_info.get('error')}")

    code_key = (pl_info.get("code_column") if isinstance(pl_info, dict) else None) or ""
    codes: List[str] = []
    for r in price_list:
        v = _row_get_any(r, [code_key, "Codice", "codice", "CODE", "Code", "ItemCode", "Articolo"])
        c = str(v or "").strip()
        if c:
            codes.append(c)

    codes = sorted(set(codes))
    if not codes:
        w = "Nessun prodotto trovato per il fornitore/listino."
        if isinstance(pl_info, dict) and pl_info.get("tables"):
            w += f" Tabelle: {pl_info.get('tables')}"
        return {"meta": {"total_rows": 0}, "rows": [], "warnings": [w]}

    # Conversioni KG per descrizione
    conv_by_descr = get_conversions_for_supplier(str(store_code), str(supplier_name))

    # Periodi
    cover_start = order_day
    # Copertura: dal giorno dell'ordine fino a Consegna 2 (inclusa).
    cover_end = next_delivery_day
    L = (cover_end - cover_start).days + 1

    pm_start, pm_end = _month_range_previous(order_day)

    # Forecast vendite periodo copertura
    forecast_rev_total, _forecast_map = _sum_sales_forecast(str(store_code), cover_start, cover_end)

    # Prev month revenues total (trasparenza)
    rev_pm_daily = _get_revenues_net_daily(str(store_code), pm_start, pm_end)
    prev_month_revenues_total = _sum_daily_map(rev_pm_daily, pm_start, pm_end)

    # Sigma revenues con finestra dinamica (continua)
    sigma_end = order_day - timedelta(days=1)
    pool_start = sigma_end - timedelta(days=_SIGMA_POOL_DAYS - 1)
    rev_pool_daily = _get_revenues_net_daily(str(store_code), pool_start, sigma_end)

    used_sigma_days, sigma_start, sigma_end_ok = _pick_continuous_sigma_window(rev_pool_daily, sigma_end, _SIGMA_DAYS_DEFAULT)
    if used_sigma_days <= 0:
        sigma_rev = 0.0
        warnings.append(f"[SIGMA] Dati insufficienti o non continui per calcolare sigma: servono almeno {_SIGMA_MIN_DAYS} giorni consecutivi.")
    else:
        sigma_vals = [float(rev_pool_daily.get(sigma_start + timedelta(days=i), 0.0)) for i in range(used_sigma_days)]
        sigma_rev = _stddev(sigma_vals)
        if used_sigma_days != _SIGMA_DAYS_DEFAULT:
            warnings.append(f"[SIGMA] Calcolata su {used_sigma_days} giorni (store recente o buchi dati).")

    sigma_revenues_total = _sum_daily_map(rev_pool_daily, sigma_start, sigma_end_ok) if (used_sigma_days and sigma_start and sigma_end_ok) else 0.0

    # Last inventory up to order day (incluso)
    inv_up_to = order_day
    last_inv_map, w_inv = _get_last_inv_by_code(str(store_code), codes, inv_up_to)
    warnings.extend(w_inv)

    out_rows: List[Dict[str, Any]] = []

    pl_by_code: Dict[str, Dict[str, Any]] = {}
    for r in price_list:
        cc = str(_row_get_any(r, [code_key, "Codice", "codice", "CODE", "Code", "ItemCode", "Articolo"]) or "").strip()
        if cc and cc not in pl_by_code:
            pl_by_code[cc] = r

    desc_key = (pl_info.get("desc_column") if isinstance(pl_info, dict) else None) or ""
    unit_key = (pl_info.get("unit_column") if isinstance(pl_info, dict) else None) or ""

    for code in codes:
        pr = pl_by_code.get(code, {})
        desc = str(_row_get_any(pr, [desc_key, "Descrizione", "descrizione", "Desc", "Descr"]) or "").strip()
        qtacar = _row_get_any(pr, [unit_key, "QtaCar", "qtacar", "Qtacar", "Pezzi_per_collo", "Pezzi per collo", "PezziPerCollo", "QtaCarico"]) or 0
        try:
            qtacar = float(qtacar or 0)
        except Exception:
            qtacar = 0.0

        inv = last_inv_map.get(code)
        if not inv:
            inv_date = None
            inv_qty = 0.0
            stock_period_start = None
            stock_period_end = inv_up_to
            warnings.append(f"[INV] Nessun inventario INV trovato per codice {code} fino al {inv_up_to.isoformat()}.")
        else:
            inv_date = inv.inv_date
            inv_qty = float(inv.inv_qty)
            stock_period_start = inv_date + timedelta(days=1)
            stock_period_end = inv_up_to

        # Aggregates since last inventory (NO WASTE)
        if stock_period_start and stock_period_end and stock_period_end >= stock_period_start:
            deliv_pz = _get_agg_pz_by_code(str(store_code), "DatiDelivery", [code], stock_period_start, stock_period_end, kind="delivery").get(code, 0.0)
            txin_pz = _get_agg_pz_by_code(str(store_code), "DatiTX", [code], stock_period_start, stock_period_end, kind="txin").get(code, 0.0)
            txout_pz = _get_agg_pz_by_code(str(store_code), "DatiTX", [code], stock_period_start, stock_period_end, kind="txout").get(code, 0.0)
            rev_real_daily = _get_revenues_net_daily(str(store_code), stock_period_start, stock_period_end)
            rev_real = _sum_daily_map(rev_real_daily, stock_period_start, stock_period_end)
        else:
            deliv_pz = txin_pz = txout_pz = 0.0
            rev_real = 0.0

        # Units per 1000: mese precedente basato su inventari (NO WASTE)
        u1000 = 0.0
        try:
            inv_start_map, _ = _get_last_inv_by_code(str(store_code), [code], pm_start - timedelta(days=1))
            inv_end_map, _ = _get_last_inv_by_code(str(store_code), [code], pm_end)
            inv_start = inv_start_map.get(code)
            inv_end = inv_end_map.get(code)

            if inv_start and inv_end and inv_end.inv_date > inv_start.inv_date:
                span_start = inv_start.inv_date + timedelta(days=1)
                span_end = inv_end.inv_date
                del_span = _get_agg_pz_by_code(str(store_code), "DatiDelivery", [code], span_start, span_end, kind="delivery").get(code, 0.0)
                txin_span = _get_agg_pz_by_code(str(store_code), "DatiTX", [code], span_start, span_end, kind="txin").get(code, 0.0)
                txout_span = _get_agg_pz_by_code(str(store_code), "DatiTX", [code], span_start, span_end, kind="txout").get(code, 0.0)

                cons_inv = float(inv_start.inv_qty) + del_span + txin_span - txout_span - float(inv_end.inv_qty)

                rev_span_daily = _get_revenues_net_daily(str(store_code), span_start, span_end)
                rev_span = _sum_daily_map(rev_span_daily, span_start, span_end)
                if rev_span > 0:
                    u1000 = (cons_inv / rev_span) * 1000.0
                else:
                    u1000 = 0.0
            else:
                u1000 = 0.0
        except Exception:
            u1000 = 0.0

        if u1000 <= 0:
            warnings.append(f"[U/1000] u/1000 non calcolabile per {code} (mancano inventari o revenues nel mese precedente).")

        # consumption estimated for past period (since last inv)
        consumo_est = (rev_real / 1000.0) * u1000 if u1000 > 0 else 0.0

        # Stock teorico (NO WASTE)
        stock_theoretical = inv_qty + deliv_pz + txin_pz - txout_pz - consumo_est

        # forecast consumption
        demand_forecast = (forecast_rev_total / 1000.0) * u1000 if u1000 > 0 else 0.0

        # safety stock in units
        sigma_units = (sigma_rev / 1000.0) * u1000 if u1000 > 0 else 0.0
        safety_stock = _Z * sigma_units * math.sqrt(max(L, 1))
        target_stock = demand_forecast + safety_stock

        needed = max(0.0, target_stock - stock_theoretical)
        if qtacar and qtacar > 0:
            order_car = int(math.ceil(needed / qtacar))
            order_pz = float(order_car) * float(qtacar)
        else:
            order_car = 0
            order_pz = float(needed)

        conv = 0.0
        try:
            conv = float(conv_by_descr.get((desc or "").strip().lower(), 0.0))
        except Exception:
            conv = 0.0
        stock_kg = stock_theoretical * conv if conv else None
        order_kg = order_pz * conv if conv else None

        out_rows.append({
            "code": code,
            "desc": desc,
            "qtacar": float(qtacar) if qtacar else 0.0,
            "unit_per_1000": float(u1000),

            "stock_theoretical": float(stock_theoretical),
            "forecast_revenues_total": float(forecast_rev_total),
            "forecast_consumption": float(demand_forecast),

            "sigma_revenues_std": float(sigma_rev),
            "sigma_days_used": int(used_sigma_days or 0),
            "safety_stock": float(safety_stock),
            "target_stock": float(target_stock),

            "order_pz": float(order_pz),
            "order_car": int(order_car),

            "last_inv_date": inv_date.isoformat() if inv_date else None,

            # dettagli per popup
            "inv_date": inv_date.isoformat() if inv_date else None,
            "inv_qty": float(inv_qty),
            "stock_period_start": stock_period_start.isoformat() if stock_period_start else None,
            "stock_period_end": stock_period_end.isoformat() if stock_period_end else None,
            "delivery_pz_since_inv": float(deliv_pz),
            "txin_pz_since_inv": float(txin_pz),
            "txout_pz_since_inv": float(txout_pz),
            "past_revenues_used": float(rev_real),
            "past_consumption_est": float(consumo_est),

            "conv_kg_per_pz": float(conv) if conv else None,
            "stock_kg": float(stock_kg) if stock_kg is not None else None,
            "order_kg": float(order_kg) if order_kg is not None else None,
        })

    meta = {
        "script_version": SCRIPT_VERSION,
        "supplier": supplier_name,
        "listino": listino,
        "order_date": order_day.isoformat(),
        "next_delivery": next_delivery_day.isoformat(),
        "coverage_start": cover_start.isoformat(),
        "coverage_end": cover_end.isoformat(),
        "coverage_days": int(L),

        "forecast_revenues_total": float(forecast_rev_total),

        "prev_month_start": pm_start.isoformat(),
        "prev_month_end": pm_end.isoformat(),
        "prev_month_revenues_total": float(prev_month_revenues_total),

        "sigma_days_default": int(_SIGMA_DAYS_DEFAULT),
        "sigma_days_used": int(used_sigma_days or 0),
        "sigma_start": sigma_start.isoformat() if sigma_start else None,
        "sigma_end": sigma_end_ok.isoformat() if sigma_end_ok else None,
        "sigma_revenues_std": float(sigma_rev),
        "sigma_revenues_total": float(sigma_revenues_total),

        "z": float(_Z),
        "total_rows": int(len(out_rows)),
    }

    return {"meta": meta, "rows": out_rows, "warnings": warnings}
