"""collect_repository.py

Repository per la pagina "raccolta dati" (dashboard mensile).

Obiettivo:
  - Calcolare totali € per giorno del mese, in base a:
      movement: INV | DELIVERY | TXIN | TXOUT | WASTE
      category: FoodPaper | Operating
  - Restituire un breakdown per giorno (fornitore + bucket FoodPaper/Operating).

La logica è robusta rispetto a:
  - colonne con maiuscole/minuscole diverse
  - date in DatiDelivery salvate come testo (dd/mm/yyyy)
"""

from __future__ import annotations

from app_logging import log_swallowed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app_db import get_connection, sql_mid, sql_date
import delivery_repository
import inventory_repository


# ------------------------------
#  Helpers
# ------------------------------


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace("_", " ")


def _bucket_from_group(group_val: Any) -> str:
    """Ritorna FoodPaper / Operating a partire da una colonna gruppo/categoria."""
    g = _norm(str(group_val or ""))
    if not g:
        return "Operating"

    # Varianti possibili
    if g in ("food", "paper", "foodpaper", "food paper", "food&paper", "food & paper"):
        return "FoodPaper"

    # Se contiene "food" o "paper" lo consideriamo FoodPaper
    if "food" in g or "paper" in g:
        return "FoodPaper"

    return "Operating"


def _parse_any_date_to_iso(d: Any) -> Optional[str]:
    """Converte date/datetime o stringhe comuni in YYYY-MM-DD.

    Supporta:
    - date/datetime
    - ISO (YYYY-MM-DD) anche se seguito da orario
    - dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy
    - mm/dd/yyyy, mm-dd-yyyy, mm.dd.yyyy

    Per stringhe ambigue (es. 06/12/2025) questa funzione prova prima DMY e poi MDY.
    Quando si conosce già il range target, preferire _parse_any_date_to_iso_in_range().
    """
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()

    s = str(d).strip()
    if not s:
        return None

    s10 = s[:10].strip()

    # ISO date
    try:
        if len(s10) == 10 and s10[4] == "-" and s10[7] == "-":
            return datetime.strptime(s10, "%Y-%m-%d").date().isoformat()
    except Exception:
        log_swallowed('collect_repository:83')

    # Numeric dates
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y"):
        try:
            return datetime.strptime(s10, fmt).date().isoformat()
        except Exception:
            continue

    return None



    # yyyy-mm-dd
    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return datetime.strptime(s[:10], "%Y-%m-%d").date().isoformat()
    except Exception:
        log_swallowed('collect_repository:101')

    # dd/mm/yyyy
    try:
        if "/" in s:
            return datetime.strptime(s[:10], "%d/%m/%Y").date().isoformat()
    except Exception:
        log_swallowed('collect_repository:108')

    return None



def _swap_day_month(d: date) -> Optional[date]:
    try:
        if d.day <= 12 and d.month <= 12 and d.day != d.month:
            return date(d.year, d.day, d.month)
    except Exception:
        return None
    return None


def _parse_any_date_to_iso_in_range(raw_date: Any, start: date, end: date) -> Optional[str]:
    """Parsa una data e, se possibile, sceglie l'interpretazione che cade nel range [start, end).

    Gestisce ambiguità dd/mm vs mm/dd (tipiche su Windows/Access in locale EN).
    """
    if raw_date is None:
        return None

    # Date/Datetime
    try:
        if isinstance(raw_date, datetime):
            d0 = raw_date.date()
        elif isinstance(raw_date, date):
            d0 = raw_date
        else:
            d0 = None
    except Exception:
        d0 = None

    if d0 is not None:
        if start <= d0 < end:
            return d0.isoformat()
        d1 = _swap_day_month(d0)
        if d1 is not None and start <= d1 < end:
            return d1.isoformat()
        return None

    s = str(raw_date).strip()
    if not s:
        return None

    candidates: List[date] = []

    # ISO yyyy-mm-dd...
    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            candidates.append(datetime.strptime(s[:10], "%Y-%m-%d").date())
    except Exception:
        log_swallowed('collect_repository:161')

    # dd/mm/yyyy and mm/dd/yyyy (and with '-')
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"):
        try:
            candidates.append(datetime.strptime(s[:10], fmt).date())
        except Exception:
            continue

    in_range = [d for d in candidates if start <= d < end]
    if len(in_range) == 1:
        return in_range[0].isoformat()
    if len(in_range) > 1:
        return in_range[0].isoformat()  # stabile: prima candidata

    return None

def _month_range(year: int, month: int) -> Tuple[date, date]:
    start = date(year, month, 1)
    # next month
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end


def _detect_group_or_category_col(cols: List[str]) -> str:
    """Prova a trovare una colonna gruppo/categoria in modo flessibile."""
    cols_map = { _norm(c): c for c in cols }
    for key in ("gruppo", "group", "grp", "categoria", "category"):
        if key in cols_map:
            return cols_map[key]
    # fallback: cerca contiene
    for c in cols:
        n = _norm(c)
        if "grupp" in n:
            return c
    for c in cols:
        n = _norm(c)
        if "categ" in n:
            return c
    return ""


def _safe_float(v: Any) -> float:
    try:
        if v is None:
            return 0.0
        return float(v)
    except Exception:
        try:
            return float(str(v).replace(",", "."))
        except Exception:
            return 0.0


@dataclass
class MonthlyResult:
    year: int
    month: int
    movement: str
    category: str
    days: Dict[str, float]  # YYYY-MM-DD -> total
    total: float


# ------------------------------
#  Public API
# ------------------------------


def get_collect_month_data(
    store_code: str,
    year: int,
    month: int,
    movement: str,
    category: str,
) -> Dict[str, Any]:
    """Dati mese: totali giornalieri (filtrati per bucket category)."""
    movement = (movement or "").strip().upper()
    category = (category or "FoodPaper").strip()
    if category not in ("FoodPaper", "Operating"):
        category = "FoodPaper"

    start, end = _month_range(year, month)
    days = {}
    # inizializza tutte le date del mese a 0
    d = start
    while d < end:
        days[d.isoformat()] = 0.0
        d += timedelta(days=1)

    if movement == "DELIVERY":
        _fill_delivery_month(store_code, start, end, category, days)
    elif movement in ("INV", "WASTE", "TXIN", "TXOUT"):
        _fill_inventory_like_month(store_code, start, end, movement, category, days)
    else:
        raise ValueError("movement non valido")

    total = float(sum(days.values()))
    return {
        "year": year,
        "month": month,
        "movement": movement,
        "category": category,
        "days": days,
        "total": total,
    }



def _month_total_for_movement(
    store_code: str,
    year: int,
    month: int,
    movement: str,
    category: str,
) -> float:
    data = get_collect_month_data(
        store_code=store_code,
        year=year,
        month=month,
        movement=movement,
        category=category,
    )
    try:
        return float(data.get("total") or 0.0)
    except Exception:
        return 0.0


def _giro_affari_month_from_primanota(store_code: str, year: int, month: int) -> float:
    """Giro affari mese = vendite lorde - annullati (coerente con rendiconto/api/dashboard/month)."""
    try:
        # import locale per evitare dipendenze circolari in fase di import
        from primanota_repository import load_primanota_month_agg
    except Exception:
        return 0.0

    try:
        rows = load_primanota_month_agg(str(store_code), year=year, month=month) or []
    except Exception:
        return 0.0

    vendite_lorde = 0.0
    annullati = 0.0

    for r in rows:
        cat = str(r.get("categoria") or "").strip().lower()
        if cat != "dati chiusura":
            continue
        voce = str(r.get("voce") or "").strip().upper()
        s = float(r.get("sum") or 0.0)
        if voce == "VENDITE LORDE":
            vendite_lorde += s
        elif voce == "ANNULLATI":
            annullati += s

    return float(vendite_lorde - annullati)


def get_magazzino_month_summary(
    store_code: str,
    year: int,
    month: int,
    category: str,
    current_movement: str = "",
    current_total: float = 0.0,
) -> Dict[str, Any]:
    """Riepilogo mese (Magazzino) per la dashboard.

    Valori:
    - ddt (DELIVERY)
    - txin (TXIN)
    - txout (TXOUT)
    - waste (WASTE)
    - revenues_net = lettura StoreHubDailySales con fallback legacy DatiDatabase
    - waste_pct = waste / revenues_net * 100
    """
    category = (category or "FoodPaper").strip()
    if category not in ("FoodPaper", "Operating"):
        category = "FoodPaper"

    current_movement = (current_movement or "").strip().upper()
    try:
        current_total_f = float(current_total or 0.0)
    except Exception:
        current_total_f = 0.0

    def total_for(mov: str) -> float:
        mov = (mov or "").strip().upper()
        if mov == current_movement:
            return current_total_f
        try:
            return _month_total_for_movement(store_code, year, month, mov, category)
        except Exception:
            return 0.0

    ddt = total_for("DELIVERY")
    txin = total_for("TXIN")
    txout = total_for("TXOUT")
    waste = total_for("WASTE")

    # Revenues (net): StoreHub-native con fallback legacy DatiDatabase.
    revenues_net = 0.0
    try:
        from daily_sales_repository import get_revenues_net_range

        start, end = _month_range(year, month)
        end_incl = end - timedelta(days=1)
        revenues_net = get_revenues_net_range(store_code=str(store_code), start_day=start, end_day=end_incl)
        try:
            revenues_net = float(revenues_net or 0.0)
        except Exception:
            revenues_net = 0.0
    except Exception:
        revenues_net = 0.0

    if revenues_net and revenues_net > 0:
        waste_pct = float(waste) / float(revenues_net) * 100.0
    else:
        waste_pct = None

    return {
        "ddt": float(ddt),
        "txin": float(txin),
        "txout": float(txout),
        "waste": float(waste),
        "revenues_net": float(revenues_net),
        "waste_pct": (float(waste_pct) if waste_pct is not None else None),
    }



def get_collect_day_breakdown(
    store_code: str,
    day_iso: str,
    movement: str,
) -> Dict[str, Any]:
    """Breakdown per giorno.

    - Per INV/DELIVERY/WASTE: aggregazione per fornitore.
    - Per TXIN/TXOUT: aggregazione per destinazione/origine (SITE2) e, dentro ogni SITE2, per fornitore.

    Restituisce sempre i totali complessivi FoodPaper/Operating e una lista 'rows' (per compatibilità).
    Per i trasferimenti include anche 'site2_groups'.
    """
    movement = (movement or "").strip().upper()
    if not day_iso:
        raise ValueError("date mancante")
    day = datetime.strptime(day_iso, "%Y-%m-%d").date()
    day_start = datetime(day.year, day.month, day.day)
    day_end = day_start + timedelta(days=1)

    if movement == "DELIVERY":
        raw_rows = _delivery_breakdown_day(store_code, day, day_iso)  # (supplier, group, amount)
    elif movement in ("INV", "WASTE", "TXIN", "TXOUT"):
        raw_rows = _inventory_like_breakdown_day(store_code, day_start, day_end, movement)
    else:
        raise ValueError("movement non valido")

    is_tx = movement in ("TXIN", "TXOUT")

    # --- aggregazione ---
    def _add(bucket_map: Dict[str, Dict[str, float]], supplier: str, group_val: Any, amount: Any) -> None:
        s = (supplier or "(Senza fornitore)").strip()
        b = _bucket_from_group(group_val)
        bucket_map.setdefault(s, {"FoodPaper": 0.0, "Operating": 0.0})
        bucket_map[s][b] += _safe_float(amount)

    # complessivo (per compatibilità e per i totali in testata)
    by_supplier_all: Dict[str, Dict[str, float]] = {}

    site2_groups: List[Dict[str, Any]] = []
    if is_tx:
        by_site2: Dict[str, Dict[str, Dict[str, float]]] = {}

        for r in raw_rows or []:
            # r può essere (supplier, group, amount, site2)
            supplier = r[0] if len(r) > 0 else ""
            group_val = r[1] if len(r) > 1 else None
            amount = r[2] if len(r) > 2 else 0
            site2 = r[3] if len(r) > 3 else ""

            site2_key = (str(site2 or "").strip() or "(SITE2 non indicato)")
            by_site2.setdefault(site2_key, {})
            _add(by_site2[site2_key], supplier, group_val, amount)
            _add(by_supplier_all, supplier, group_val, amount)

        # format per ogni SITE2
        total_fp_all = 0.0
        total_op_all = 0.0
        for site2 in sorted(by_site2.keys()):
            sup_map = by_site2[site2]
            out_rows = []
            sub_fp = 0.0
            sub_op = 0.0
            for supplier in sorted(sup_map.keys()):
                fp = float(sup_map[supplier]["FoodPaper"])
                op = float(sup_map[supplier]["Operating"])
                sub_fp += fp
                sub_op += op
                out_rows.append({
                    "supplier": supplier,
                    "foodpaper": fp,
                    "operating": op,
                    "total": float(fp + op),
                })

            total_fp_all += sub_fp
            total_op_all += sub_op

            site2_groups.append({
                "site2": site2,
                "totals": {
                    "foodpaper": float(sub_fp),
                    "operating": float(sub_op),
                    "all": float(sub_fp + sub_op),
                },
                "rows": out_rows,
            })

        totals = {
            "foodpaper": float(total_fp_all),
            "operating": float(total_op_all),
            "all": float(total_fp_all + total_op_all),
        }

    else:
        for supplier, group_val, amount in (raw_rows or []):
            _add(by_supplier_all, supplier, group_val, amount)

        totals = {
            "foodpaper": 0.0,
            "operating": 0.0,
            "all": 0.0,
        }

    # Righe complessive (supplier x bucket) - usate anche per INV/DELIVERY/WASTE
    out_rows_all = []
    total_fp = 0.0
    total_op = 0.0
    for supplier in sorted(by_supplier_all.keys()):
        fp = float(by_supplier_all[supplier]["FoodPaper"])
        op = float(by_supplier_all[supplier]["Operating"])
        total_fp += fp
        total_op += op
        out_rows_all.append({
            "supplier": supplier,
            "foodpaper": fp,
            "operating": op,
            "total": float(fp + op),
        })

    # Se non TX, i totali sono quelli appena calcolati
    if not is_tx:
        totals = {
            "foodpaper": float(total_fp),
            "operating": float(total_op),
            "all": float(total_fp + total_op),
        }

    out = {
        "date": day_iso,
        "movement": movement,
        "totals": totals,
        "rows": out_rows_all,
    }
    if is_tx:
        out["site2_groups"] = site2_groups

    return out
# ------------------------------
#  Delivery
# ------------------------------


def _fill_delivery_month(store_code: str, start: date, end: date, category: str, days: Dict[str, float]) -> None:
    table = delivery_repository.get_delivery_table_name()
    conn = get_connection(store_code)
    try:
        layout = delivery_repository._detect_delivery_layout(conn, table)
        if layout.get("error"):
            raise ValueError(layout["error"])

        site_col = layout["site_col"]
        val_col = layout["val_col"]
        supplier_col = layout.get("supplier_col")
        date_col = layout.get("deliv_date_col") or layout.get("doc_date_col")
        if not date_col:
            raise ValueError("Colonna data non trovata in DatiDelivery")

        cols = layout.get("columns") or []
        group_col = _detect_group_or_category_col(cols)
        if not group_col:
            # se manca, trattiamo tutto come Operating
            group_col = None

        cur = conn.cursor()

        # DatiDelivery può salvare la data come Date/Time oppure come TESTO.
        # 1) Se la colonna è Date/Time, Month/Year è affidabile.
        # 2) Se la colonna è TESTO, su Windows EN può comparire mm/dd/yyyy: quindi filtriamo
        #    per mese/anno provando entrambe le posizioni (dd/mm e mm/dd) e poi validiamo in Python.
        rows = []

        select_cols = [date_col, val_col]
        if supplier_col:
            select_cols.append(supplier_col)
        if group_col:
            select_cols.append(group_col)

        # 1) Month/Year su date native
        try:
            sql = (
                f"SELECT {', '.join('['+c+']' for c in select_cols)} "
                f"FROM [{table}] WHERE [{site_col}] = ? AND Month([{date_col}]) = ? AND Year([{date_col}]) = ?"
            )
            cur.execute(sql, (store_code, start.month, start.year))
            rows = cur.fetchall()
        except Exception:
            rows = []

        # 2) fallback TESTO (dd/mm o mm/dd)
        if not rows:
            month_str = f"{start.month:02d}"
            year_str = f"{start.year:04d}"
            date10 = f"Left([{date_col}], 10)"
            sql = (
                f"SELECT {', '.join('['+c+']' for c in select_cols)} "
                f"FROM [{table}] WHERE [{site_col}] = ? "
                f"AND Right({date10}, 4) = ? "
                f"AND ({sql_mid(date10, 4, 2)} = ? OR Left({date10}, 2) = ?)"
            )
            cur.execute(sql, (store_code, year_str, month_str, month_str))
            rows = cur.fetchall()

        for r in rows:
            # ordine: date, value, supplier?, group?
            idx = 0
            raw_date = r[idx]; idx += 1
            raw_val = r[idx]; idx += 1
            raw_supplier = r[idx] if supplier_col else None
            if supplier_col:
                idx += 1
            raw_group = r[idx] if group_col else None

            day_iso = _parse_any_date_to_iso_in_range(raw_date, start, end)
            if not day_iso:
                continue
            # safety: tieni solo date nel mese richiesto
            try:
                d = datetime.strptime(day_iso, "%Y-%m-%d").date()
                if d < start or d >= end:
                    continue
            except Exception:
                continue

            b = _bucket_from_group(raw_group)
            if b != category:
                continue
            days[day_iso] = float(days.get(day_iso, 0.0) + _safe_float(raw_val))

    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('collect_repository:629')


def _delivery_breakdown_day(store_code: str, day: date, day_iso: str) -> List[Tuple[str, Any, Any]]:
    table = delivery_repository.get_delivery_table_name()
    conn = get_connection(store_code)
    try:
        layout = delivery_repository._detect_delivery_layout(conn, table)
        if layout.get("error"):
            raise ValueError(layout["error"])

        site_col = layout["site_col"]
        val_col = layout["val_col"]
        supplier_col = layout.get("supplier_col")
        date_col = layout.get("deliv_date_col") or layout.get("doc_date_col")
        if not date_col:
            raise ValueError("Colonna data non trovata in DatiDelivery")

        cols = layout.get("columns") or []
        group_col = _detect_group_or_category_col(cols)

        if not supplier_col:
            supplier_col = None
        if not group_col:
            group_col = None

        cur = conn.cursor()
        alt_day = _swap_day_month(day)

        select_cols = [date_col, val_col]
        if supplier_col:
            select_cols.append(supplier_col)
        if group_col:
            select_cols.append(group_col)

        # per date testuali
        ddmmyyyy = day.strftime("%d/%m/%Y")

        sql_eq = (
            f"SELECT {', '.join('['+c+']' for c in select_cols)} "
            f"FROM [{table}] WHERE [{site_col}] = ? AND [{date_col}] = ?"
        )

        date_expr = sql_date(f"[{date_col}]")
        param_date_expr = sql_date("?")
        sql_dv = (
            f"SELECT {', '.join('['+c+']' for c in select_cols)} "
            f"FROM [{table}] WHERE [{site_col}] = ? AND {date_expr} = {param_date_expr}"
        )

        rows = []
        # 1) prova robusta (DateValue + parametro date): funziona bene con colonne Date/Time
        try:
            cur.execute(sql_dv, (store_code, day))
            rows = cur.fetchall() or []
        except Exception:
            rows = []

        # 2) fallback su equality: utile quando la colonna data è TESTO.
        #    Proviamo sia dd/mm (IT) sia mm/dd (EN) e validiamo confrontando la data col giorno richiesto.
        if not rows:
            def _row_matches_day(raw_date: Any) -> bool:
                iso = _parse_any_date_to_iso(raw_date)
                if not iso:
                    return False
                try:
                    d0 = datetime.strptime(iso, "%Y-%m-%d").date()
                    return d0 == day or (alt_day is not None and d0 == alt_day)
                except Exception:
                    return False

            for candidate in (ddmmyyyy, day.strftime("%m/%d/%Y"), day_iso):
                try:
                    cur.execute(sql_eq, (store_code, candidate))
                    tmp = cur.fetchall() or []
                except Exception:
                    tmp = []

                if tmp:
                    try:
                        tmp = [r for r in tmp if _row_matches_day(r[0])]
                    except Exception:
                        tmp = []
                if tmp:
                    rows = tmp
                    break

        out: List[Tuple[str, Any, Any]] = []
        for r in rows:
            idx = 0
            _raw_date = r[idx]; idx += 1  # non usato
            raw_val = r[idx]; idx += 1
            raw_supplier = r[idx] if supplier_col else None
            if supplier_col:
                idx += 1
            raw_group = r[idx] if group_col else None
            out.append((str(raw_supplier or ""), raw_group, raw_val))
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('collect_repository:731')

# ------------------------------
#  Inventory / TX / Waste
# ------------------------------


def _fill_inventory_like_month(
    store_code: str,
    start: date,
    end: date,
    movement: str,
    category: str,
    days: Dict[str, float],
) -> None:
    conn = get_connection(store_code)
    try:
        is_tx = movement in ("TXIN", "TXOUT")
        table_name = inventory_repository.get_tx_table_name() if is_tx else inventory_repository.get_inventory_table_name()
        table = inventory_repository._resolve_table_name(
            conn,
            table_name,
            extra_candidates=(
                ["DatiTX", "datitx", "DATITX", "tx", "DatiTx"] if is_tx else ["DatiInventario", "DATIINVENTARIO", "dati inventario", "inventario", "Inventario"]
            ),
        )

        cols = inventory_repository._get_table_columns(conn, table)
        layout = inventory_repository._detect_inventory_layout(cols, require_site2=is_tx)

        supplier_col = inventory_repository._detect_supplier_col(cols) or None
        group_col = _detect_group_or_category_col(cols) or None

        site_col = layout["site_col"]
        date_col = layout["date_col"]
        mov_col = layout["mov_col"]
        euro_col = layout["toteuro_col"]
        site2_col = layout.get("site2_col") if is_tx else None
        filter_site_col = site_col
        mov_val = movement if movement != "WASTE" else "WASTE CRUDO"
        if is_tx and site2_col:
            # DatiTX: trasferimenti salvati solo come TXOUT con SITE2 = destinazione.
            # TXIN = TXOUT dove SITE2 = store.
            mov_val = "TXOUT"
            if movement == "TXIN":
                filter_site_col = site2_col


        cur = conn.cursor()
        select_cols = [date_col, euro_col]
        if supplier_col:
            select_cols.append(supplier_col)
        if group_col:
            select_cols.append(group_col)

        sql = (
            f"SELECT {', '.join('['+c+']' for c in select_cols)} "
            f"FROM [{table}] "
            f"WHERE [{filter_site_col}] = ? AND [{mov_col}] = ? AND [{date_col}] >= ? AND [{date_col}] < ?"
        )

        cur.execute(sql, (store_code, mov_val, datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())))
        rows = cur.fetchall()

        for r in rows:
            idx = 0
            raw_date = r[idx]; idx += 1
            raw_val = r[idx]; idx += 1
            raw_supplier = r[idx] if supplier_col else None
            if supplier_col:
                idx += 1
            raw_group = r[idx] if group_col else None

            day_iso = _parse_any_date_to_iso_in_range(raw_date, start, end)
            if not day_iso:
                continue
            b = _bucket_from_group(raw_group)
            if b != category:
                continue
            days[day_iso] = float(days.get(day_iso, 0.0) + _safe_float(raw_val))
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('collect_repository:815')


def _inventory_like_breakdown_day(
    store_code: str,
    day_start: datetime,
    day_end: datetime,
    movement: str,
) -> List[Tuple[str, Any, Any]]:
    """Righe per breakdown giornaliero per INV/TX/WASTE.

    Per TXIN/TXOUT include anche SITE2 come 4° elemento della tupla: (supplier, group, amount, site2).
    """
    conn = get_connection(store_code)
    try:
        is_tx = movement in ("TXIN", "TXOUT")
        table_name = inventory_repository.get_tx_table_name() if is_tx else inventory_repository.get_inventory_table_name()
        table = inventory_repository._resolve_table_name(
            conn,
            table_name,
            extra_candidates=(
                ["DatiTX", "datitx", "DATITX", "tx", "DatiTx"] if is_tx else ["DatiInventario", "DATIINVENTARIO", "dati inventario", "inventario", "Inventario"]
            ),
        )

        cols = inventory_repository._get_table_columns(conn, table)
        layout = inventory_repository._detect_inventory_layout(cols, require_site2=is_tx)
        supplier_col = inventory_repository._detect_supplier_col(cols) or None
        group_col = _detect_group_or_category_col(cols) or None

        site_col = layout["site_col"]
        date_col = layout["date_col"]
        mov_col = layout["mov_col"]
        euro_col = layout["toteuro_col"]
        site2_col = layout.get("site2_col") if is_tx else None
        mov_val = movement if movement != "WASTE" else "WASTE CRUDO"
        filter_site_col = site_col
        output_site2_col = site2_col
        if is_tx and output_site2_col:
            # DatiTX: trasferimenti salvati solo come TXOUT con SITE2 = destinazione.
            # TXIN = TXOUT dove SITE2 = store (per il breakdown mostriamo come 'site2' l'origine).
            mov_val = "TXOUT"
            if movement == "TXIN":
                filter_site_col = site2_col
                output_site2_col = site_col
            else:
                filter_site_col = site_col
                output_site2_col = site2_col

        cur = conn.cursor()

        # Ordine colonne selezionate (per parsing)
        select_cols = [euro_col]
        col_order = ["euro"]

        if supplier_col:
            select_cols.append(supplier_col)
            col_order.append("supplier")
        if group_col:
            select_cols.append(group_col)
            col_order.append("group")
        if is_tx and output_site2_col:
            select_cols.append(output_site2_col)
            col_order.append("site2")

        sql = (
            f"SELECT {', '.join('['+c+']' for c in select_cols)} "
            f"FROM [{table}] "
            f"WHERE [{filter_site_col}] = ? AND [{mov_col}] = ? AND [{date_col}] >= ? AND [{date_col}] < ?"
        )

        cur.execute(sql, (store_code, mov_val, day_start, day_end))
        fetched = cur.fetchall()

        out = []
        for r in fetched:
            idx = 0
            raw_val = r[idx]; idx += 1
            raw_supplier = None
            raw_group = None
            raw_site2 = ""

            if "supplier" in col_order:
                raw_supplier = r[idx]; idx += 1
            if "group" in col_order:
                raw_group = r[idx]; idx += 1
            if "site2" in col_order:
                raw_site2 = r[idx] if idx < len(r) else ""

            if is_tx:
                out.append((str(raw_supplier or ""), raw_group, raw_val, str(raw_site2 or "")))
            else:
                out.append((str(raw_supplier or ""), raw_group, raw_val))
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('collect_repository:913')
