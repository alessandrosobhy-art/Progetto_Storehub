from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from decimal import Decimal
import unicodedata

from app_db import get_connection_ilp, get_connection_database_new



# Etichette mesi (IT) per grafici/periodi
MONTH_LABELS = {
    1: "Gen", 2: "Feb", 3: "Mar", 4: "Apr", 5: "Mag", 6: "Giu",
    7: "Lug", 8: "Ago", 9: "Set", 10: "Ott", 11: "Nov", 12: "Dic",
}

def _norm(s: str) -> str:
    base = unicodedata.normalize("NFKD", str(s or ""))
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return " ".join(base.strip().lower().split())


def _to_float(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, Decimal):
        try:
            return float(v)
        except Exception:
            return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


@dataclass
class PlLayout:
    table_ref: str
    site_col: str
    month_col: str
    year_col: str
    voice_col: str
    value_col: str


_LAYOUT_CACHE: Dict[str, PlLayout] = {}


def _resolve_table_ref(conn, table: str) -> str:
    """Return a safe, schema-qualified table/view reference for SQL Server.

    Prefers dbo schema for unqualified names, but falls back to unqualified
    reference if dbo doesn't resolve (e.g. different default schema).
    """
    t = (table or "").strip()
    if "." in t:
        schema, name = t.split(".", 1)
        return f"[{schema}].[{name}]"

    candidates = [f"[dbo].[{t}]", f"[{t}]"]
    cur = conn.cursor()
    last_err = None
    for ref in candidates:
        try:
            cur.execute(f"SELECT TOP 0 * FROM {ref}")
            # force metadata
            _ = cur.description
            return ref
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    # re-raise the last error for easier troubleshooting
    raise last_err if last_err else RuntimeError(f"Oggetto non trovato: {table}")



def _detect_pl_layout(conn, table: str) -> PlLayout:
    key = _norm(table)
    if key in _LAYOUT_CACHE:
        return _LAYOUT_CACHE[key]

    cur = conn.cursor()
    table_ref = _resolve_table_ref(conn, table)
    cur.execute(f"SELECT TOP 0 * FROM {table_ref}")
    cols = [str(d[0]) for d in (cur.description or [])]
    cols_norm = {_norm(c): c for c in cols}

    def pick(candidates: List[str]) -> str:
        for cand in candidates:
            c = cols_norm.get(_norm(cand))
            if c:
                return c
        raise RuntimeError(f"Colonna non trovata in {table}: {candidates}")

    layout = PlLayout(
        table_ref=table_ref,
        site_col=pick(["Site", "site", "Colonna1", "colonna1"]),
        month_col=pick(["Mese", "mese", "Month", "month", "Colonna2", "colonna2"]),
        year_col=pick(["Anno", "anno", "Year", "year", "Colonna3", "colonna3"]),
        voice_col=pick(["Voce", "voce", "Voice", "voice", "Colonna4", "colonna4"]),
        value_col=pick(["Valore", "valore", "Value", "value", "Colonna7", "colonna7", "TotEuro", "toteuro", "TOTEURO", "Importo", "importo", "Amount", "amount"]),
    )
    _LAYOUT_CACHE[key] = layout
    return layout


def _fetch_voice_sum_range(
    conn,
    table: str,
    site: Any,
    year: int,
    month_from: int,
    month_to: int,
    voices: Optional[List[str]] = None,
) -> Dict[str, float]:
    layout = _detect_pl_layout(conn, table)
    voice_expr = f"LTRIM(RTRIM(CAST([{layout.voice_col}] AS NVARCHAR(255))))"
    month_expr = f"TRY_CONVERT(int, [{layout.month_col}])"
    year_expr = f"TRY_CONVERT(int, [{layout.year_col}])"
    site_expr = f"LTRIM(RTRIM(CAST([{layout.site_col}] AS NVARCHAR(255))))"

    site_values = site if isinstance(site, (list, tuple, set)) else [site]
    site_values = [str(s).strip() for s in site_values if str(s).strip()]
    if not site_values:
        return {}

    params: List[Any] = []
    where: List[str] = []
    if len(site_values) == 1:
        where.append(f"{site_expr} = ?")
        params.append(site_values[0])
    else:
        placeholders_site = ",".join(["?"] * len(site_values))
        where.append(f"{site_expr} IN ({placeholders_site})")
        params.extend(site_values)

    where.extend([f"{year_expr} = ?", f"{month_expr} >= ?", f"{month_expr} <= ?"])
    params.extend([int(year), int(month_from), int(month_to)])
    if voices:
        placeholders = ",".join(["?"] * len(voices))
        where.append(f"{voice_expr} IN ({placeholders})")
        params.extend(voices)

    sql = (
        f"SELECT {voice_expr} AS voice, SUM(TRY_CONVERT(decimal(18,4), [{layout.value_col}])) AS value "
        f"FROM {layout.table_ref} "
        f"WHERE {' AND '.join(where)} "
        f"GROUP BY {voice_expr}"
    )
    cur = conn.cursor()
    cur.execute(sql, params)
    out: Dict[str, float] = {}
    for voice, value in cur.fetchall() or []:
        out[str(voice).strip()] = _to_float(value)
    return out


def _fetch_voice_sum_month(
    conn,
    table: str,
    site: Any,
    year: int,
    month: int,
    voices: Optional[List[str]] = None,
) -> Dict[str, float]:
    layout = _detect_pl_layout(conn, table)
    voice_expr = f"LTRIM(RTRIM(CAST([{layout.voice_col}] AS NVARCHAR(255))))"
    month_expr = f"TRY_CONVERT(int, [{layout.month_col}])"
    year_expr = f"TRY_CONVERT(int, [{layout.year_col}])"
    site_expr = f"LTRIM(RTRIM(CAST([{layout.site_col}] AS NVARCHAR(255))))"

    site_values = site if isinstance(site, (list, tuple, set)) else [site]
    site_values = [str(s).strip() for s in site_values if str(s).strip()]
    if not site_values:
        return {}

    params: List[Any] = []
    where: List[str] = []
    if len(site_values) == 1:
        where.append(f"{site_expr} = ?")
        params.append(site_values[0])
    else:
        placeholders_site = ",".join(["?"] * len(site_values))
        where.append(f"{site_expr} IN ({placeholders_site})")
        params.extend(site_values)

    where.extend([f"{year_expr} = ?", f"{month_expr} = ?"])
    params.extend([int(year), int(month)])
    if voices:
        placeholders = ",".join(["?"] * len(voices))
        where.append(f"{voice_expr} IN ({placeholders})")
        params.extend(voices)

    sql = (
        f"SELECT {voice_expr} AS voice, SUM(TRY_CONVERT(decimal(18,4), [{layout.value_col}])) AS value "
        f"FROM {layout.table_ref} "
        f"WHERE {' AND '.join(where)} "
        f"GROUP BY {voice_expr}"
    )
    cur = conn.cursor()
    cur.execute(sql, params)
    out: Dict[str, float] = {}
    for voice, value in cur.fetchall() or []:
        out[str(voice).strip()] = _to_float(value)
    return out


def _voice_get(d: Dict[str, float], voice: str) -> float:
    # match case-insensitive, ignoring extra spaces
    vnorm = _norm(voice)
    for k, val in (d or {}).items():
        if _norm(k) == vnorm:
            return float(val)
    return 0.0


INVENTORY_OPENING_ALIASES = [
    "Magazzino Iniziale",
    "Inventario Iniziale",
    "Initial Inventory",
    "Opening Inventory",
]
INVENTORY_CLOSING_ALIASES = [
    "Magazzino Finale",
    "Inventario Finale",
    "Final Inventory",
    "Closing Inventory",
]


def _voice_get_any(d: Dict[str, float], voices: List[str]) -> float:
    return sum(_voice_get(d, voice) for voice in voices)


def _pct(value: float, revenues: float) -> float:
    if revenues == 0:
        return 0.0
    return value / revenues


def _diff_pct(diff: float, base: float) -> Optional[float]:
    if base == 0:
        return None
    return diff / base


MONTHS_IT = {
    1: "Gen",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "Mag",
    6: "Giu",
    7: "Lug",
    8: "Ago",
    9: "Set",
    10: "Ott",
    11: "Nov",
    12: "Dic",
}


PANEL_VOICES_ORDER: List[str] = [
    "REVENUES",
    "Magazzino Iniziale",
    "Acquistato",
    "Trasferimenti",
    "Magazzino Finale",
    "Waste",
    "COGS",
    "MARGINE DI CONTRIBUZIONE",
    "Labour fixed",
    "Stage",
    "External Labour",
    "Trasferimento",
    "Costo formazione",
    "Other cost",
    "LABOUR COST",
    "Variable fees",
    "Other delivery fees",
    "DELIVERY FEES",
    "Rent",
    "Spese Condominiali",
    "Utilities",
    "Cleaning+Security",
    "Marketing",
    "Maintenance",
    "Spese Trasporto",
    "Other G&A",
    "Casse e HiTec",
    "Altri servizi esterni",
    "Commissioni Ticket",
    "Piccole attrezzaure - Cancelleria",
    "Costi assicurativi",
    "Affitto attrezzature",
    "Altro",
    "G&A STORE",
    "TOTALE COSTI CONTROLLABILI",
    "STORE EBITDA",
    "Other personnel cost",
    "Bank commissions",
    "Consultancies",
    "Other taxes",
    "Other revenues",
    "EBITDA",
]



OTHER_GA_SUBVOICES = {
    "Casse e HiTec",
    "Altri servizi esterni",
    "Commissioni Ticket",
    "Piccole attrezzaure - Cancelleria",
    "Costi assicurativi",
    "Affitto attrezzature",
    "Altro",
}


COMPUTED_TOTAL_ROWS = {
    "REVENUES",
    "COGS",
    "MARGINE DI CONTRIBUZIONE",
    "LABOUR COST",
    "DELIVERY FEES",
    "G&A STORE",
    "TOTALE COSTI CONTROLLABILI",
    "STORE EBITDA",
    "EBITDA",
}


def get_pnl(
    store_code: str,
    year: int,
    month_from: int,
    month_to: int,
) -> Dict[str, Any]:
    """Restituisce il P&L (budget/actual/last year) per store e periodo.

    - Budget: DB ILP, tabella BudgetPL
    - Actual & Last year: DB 'DATABASE NEW', vista vw_DATIPL
    """
    store_raw = (store_code or "").strip()
    store_codes = [s.strip() for s in store_raw.split(",") if s.strip()]
    if not store_codes:
        raise ValueError("store_code mancante")

    # Normalizzazione: rimuovi duplicati preservando ordine
    _seen = set()
    store_codes = [s for s in store_codes if not (s in _seen or _seen.add(s))]
    if month_from < 1 or month_from > 12 or month_to < 1 or month_to > 12 or month_from > month_to:
        raise ValueError("Intervallo mesi non valido")

    conn_budget = get_connection_ilp(read_only=True)
    conn_actual = get_connection_database_new(read_only=True)
    try:
        # fetch range sums
        budget_r = _fetch_voice_sum_range(conn_budget, "BudgetPL", store_codes, year, month_from, month_to)

        # ACTUAL & LAST YEAR: vista su DATABASE NEW
        actual_view = "vw_DATIPL"
        actual_r = _fetch_voice_sum_range(conn_actual, actual_view, store_codes, year, month_from, month_to)
        last_r = _fetch_voice_sum_range(conn_actual, actual_view, store_codes, year - 1, month_from, month_to)

        # Saldi di magazzino: non si sommano sul periodo.
        # Apertura = mese iniziale selezionato, chiusura = mese finale selezionato.
        # Fetchiamo tutte le voci del mese per gestire anche alias/varianti di nomenclatura.
        init_voice = "Magazzino Iniziale"
        fin_voice = "Magazzino Finale"
        budget_init = _fetch_voice_sum_month(conn_budget, "BudgetPL", store_codes, year, month_from)
        budget_fin = _fetch_voice_sum_month(conn_budget, "BudgetPL", store_codes, year, month_to)

        actual_init = _fetch_voice_sum_month(conn_actual, actual_view, store_codes, year, month_from)
        actual_fin = _fetch_voice_sum_month(conn_actual, actual_view, store_codes, year, month_to)
        last_init = _fetch_voice_sum_month(conn_actual, actual_view, store_codes, year - 1, month_from)
        last_fin = _fetch_voice_sum_month(conn_actual, actual_view, store_codes, year - 1, month_to)

        def build_source_dict(base_range: Dict[str, float], init_d: Dict[str, float], fin_d: Dict[str, float]) -> Dict[str, float]:
            d = dict(base_range or {})
            stock_alias_norms = {_norm(v) for v in (INVENTORY_OPENING_ALIASES + INVENTORY_CLOSING_ALIASES)}
            for k in list(d.keys()):
                if _norm(k) in stock_alias_norms:
                    d.pop(k, None)
            # For display & calcolo: opening = month_from, closing = month_to
            d[init_voice] = _voice_get_any(init_d, INVENTORY_OPENING_ALIASES)
            d[fin_voice] = _voice_get_any(fin_d, INVENTORY_CLOSING_ALIASES)
            return d

        budget = build_source_dict(budget_r, budget_init, budget_fin)
        actual = build_source_dict(actual_r, actual_init, actual_fin)
        last = build_source_dict(last_r, last_init, last_fin)

        # computed rules
        labour_items = ["Labour fixed", "Stage", "External Labour", "Trasferimento", "Costo formazione", "Other cost"]
        delivery_items = ["Variable fees", "Other delivery fees"]
        other_ga_extra = [
            "Casse e HiTec",
            "Altri servizi esterni",
            "Commissioni Ticket",
            "Piccole attrezzaure - Cancelleria",
            "Costi assicurativi",
            "Affitto attrezzature",
            "Altro",
        ]
        ga_items = [
            "Rent",
            "Spese Condominiali",
            "Utilities",
            "Cleaning+Security",
            "Marketing",
            "Maintenance",
            "Spese Trasporto",
            "Other G&A",
        ]
        ebitda_other = ["Other personnel cost", "Bank commissions", "Consultancies", "Other taxes", "Other revenues"]

        def compute_all(src: Dict[str, float], *, waste_to_cogs: bool = False) -> Dict[str, float]:
            out = dict(src or {})
            revenues = _voice_get(out, "REVENUES")

            # Other G&A (special)
            other_ga_base = _voice_get(out, "Other G&A")
            other_ga_sum = sum(_voice_get(out, v) for v in other_ga_extra)
            out["Other G&A"] = other_ga_base + other_ga_sum

            # COGS (magazzino iniziale + acquistato + trasferimenti - magazzino finale)
            cogs = (
                _voice_get_any(out, INVENTORY_OPENING_ALIASES)
                + _voice_get(out, "Acquistato")
                + _voice_get(out, "Trasferimenti")
                - _voice_get_any(out, INVENTORY_CLOSING_ALIASES)
            )
            if waste_to_cogs:
                cogs += _voice_get(out, "Waste")

            out["COGS"] = cogs

            out["MARGINE DI CONTRIBUZIONE"] = revenues - cogs

            out["LABOUR COST"] = sum(_voice_get(out, v) for v in labour_items)
            out["DELIVERY FEES"] = sum(_voice_get(out, v) for v in delivery_items)
            out["G&A STORE"] = sum(_voice_get(out, v) for v in ga_items)
            out["TOTALE COSTI CONTROLLABILI"] = out["COGS"] + out["LABOUR COST"] + out["DELIVERY FEES"] + out["G&A STORE"]
            out["STORE EBITDA"] = revenues - out["TOTALE COSTI CONTROLLABILI"]
            out["EBITDA"] = out["STORE EBITDA"] - sum(_voice_get(out, v) for v in ebitda_other)
            return out

        budget_c = compute_all(budget, waste_to_cogs=True)
        actual_c = compute_all(actual)
        last_c = compute_all(last)

        revenues_budget = _voice_get(budget_c, "REVENUES")
        revenues_actual = _voice_get(actual_c, "REVENUES")
        revenues_last = _voice_get(last_c, "REVENUES")

        rows: List[Dict[str, Any]] = []
        for voice in PANEL_VOICES_ORDER:
            b = _voice_get(budget_c, voice)
            a = _voice_get(actual_c, voice)
            ly = _voice_get(last_c, voice)

            db = a - b
            dly = a - ly

            rows.append(
                {
                    "voice": voice,
                    "is_total": voice in COMPUTED_TOTAL_ROWS,
                    "is_subvoice": voice in OTHER_GA_SUBVOICES,
                    "budget": b,
                    "budget_pct": _pct(b, revenues_budget),
                    "actual": a,
                    "actual_pct": _pct(a, revenues_actual),
                    "diff": db,
                    "diff_pct": _diff_pct(db, b),
                    "last_year": ly,
                    "last_year_pct": _pct(ly, revenues_last),
                    "diff_last_year": dly,
                    "diff_last_year_pct": _diff_pct(dly, ly),
                }
            )

        months_label = (
            f"{MONTHS_IT.get(month_from, str(month_from))} {year}"
            if month_from == month_to
            else f"{MONTHS_IT.get(month_from, str(month_from))}-{MONTHS_IT.get(month_to, str(month_to))} {year}"
        )

        return {
            "store_code": ",".join(store_codes),
            "stores": store_codes,
            "year": int(year),
            "month_from": int(month_from),
            "month_to": int(month_to),
            "months_label": months_label,
            "rows": rows,
        }
    finally:
        for _c in (conn_actual, conn_budget):
            try:
                _c.close()
            except Exception:
                pass




def _fetch_month_voice_matrix(
    conn,
    table_or_view: str,
    store_codes: List[str],
    year: int,
    month_from: int,
    month_to: int,
) -> Dict[int, Dict[str, float]]:
    # Return {month: {voice: sum(value)}} for the given filters.
    layout = _detect_pl_layout(conn, table_or_view)

    stores = [s for s in (store_codes or []) if str(s).strip()]
    if not stores:
        return {}

    placeholders = ",".join(["?"] * len(stores))
    sql = (
        f"SELECT [{layout.month_col}] AS m, [{layout.voice_col}] AS v, "
        f"SUM(CAST([{layout.value_col}] AS float)) AS s "
        f"FROM {layout.table_ref} "
        f"WHERE [{layout.site_col}] IN ({placeholders}) "
        f"AND [{layout.year_col}] = ? "
        f"AND [{layout.month_col}] >= ? AND [{layout.month_col}] <= ? "
        f"GROUP BY [{layout.month_col}], [{layout.voice_col}]"
    )

    cur = conn.cursor()
    cur.execute(sql, *stores, year, month_from, month_to)

    out: Dict[int, Dict[str, float]] = {}
    for row in cur.fetchall():
        m = int(row[0]) if row[0] is not None else 0
        v = str(row[1] or "").strip()
        s = _to_float(row[2])
        if m <= 0 or not v:
            continue
        out.setdefault(m, {})
        out[m][v] = out[m].get(v, 0.0) + s
    return out


def get_andamento(
    store_code: str,
    year: int,
    month_from: int,
    month_to: int,
) -> Dict[str, Any]:
    """Serie temporali (mensili) delle macro-voci: Budget vs Actual vs Anno precedente.

    Output:
      - months: lista mesi (label IT)
      - cards: lista di grafici da mostrare (con metrica € o %)
    """
    store_raw = (store_code or "").strip()
    store_codes = [s.strip() for s in store_raw.split(",") if s.strip()]
    if not store_codes:
        raise RuntimeError("Nessuno store selezionato")

    # Normalizzazione: rimuovi duplicati preservando ordine
    _seen = set()
    store_codes = [s for s in store_codes if not (s in _seen or _seen.add(s))]

    # Sorgenti
    budget_table = "BudgetPL"  # DB ILP
    actual_view = "vw_DATIPL"  # DB DATABASE NEW

    # Range mesi
    mf = max(1, min(12, int(month_from)))
    mt = max(1, min(12, int(month_to)))
    if mt < mf:
        mf, mt = mt, mf

    months = [{"month": m, "label": MONTH_LABELS.get(m, str(m))} for m in range(mf, mt + 1)]

    with get_connection_ilp() as conn_budget, get_connection_database_new() as conn_actual:
        budget_matrix = _fetch_month_voice_matrix(conn_budget, budget_table, store_codes, int(year), mf, mt)
        actual_matrix = _fetch_month_voice_matrix(conn_actual, actual_view, store_codes, int(year), mf, mt)
        last_matrix = _fetch_month_voice_matrix(conn_actual, actual_view, store_codes, int(year) - 1, mf, mt)

    # Voci composte (replica logica P&L)
    other_ga_extra = [
        "Casse e HiTec",
        "Altri servizi esterni",
        "Commissioni Ticket",
        "Piccole attrezzaure - Cancelleria",
        "Costi assicurativi",
        "Affitto attrezzature",
        "Altro",
    ]
    labour_items = [
        "Labour fixed",
        "Stage",
        "External Labour",
        "Trasferimento",
        "Costo formazione",
        "Other cost",
    ]
    delivery_items = ["Variable fees", "Other delivery fees"]
    ga_items = [
        "Rent",
        "Spese Condominiali",
        "Utilities",
        "Cleaning+Security",
        "Marketing",
        "Maintenance",
        "Spese Trasporto",
        "Other G&A",
    ]
    ebitda_other = ["Other personnel cost", "Bank commissions", "Consultancies", "Other taxes", "Other revenues"]

    def _voice_get_ci(d: Dict[str, float], k: str) -> float:
        if not d:
            return 0.0
        if k in d:
            return _to_float(d.get(k))
        ku = _norm(k)
        for kk, vv in d.items():
            if _norm(kk) == ku:
                return _to_float(vv)
        return 0.0

    def compute_all(src: Dict[str, float], *, waste_to_cogs: bool = False) -> Dict[str, float]:
        out = dict(src or {})
        revenues = _voice_get_ci(out, "REVENUES")

        # Other G&A (special)
        other_ga_base = _voice_get_ci(out, "Other G&A")
        other_ga_sum = sum(_voice_get_ci(out, v) for v in other_ga_extra)
        out["Other G&A"] = other_ga_base + other_ga_sum

        # COGS
        cogs = (
            _voice_get_any(out, INVENTORY_OPENING_ALIASES)
            + _voice_get_ci(out, "Acquistato")
            + _voice_get_ci(out, "Trasferimenti")
            - _voice_get_any(out, INVENTORY_CLOSING_ALIASES)
        )
        if waste_to_cogs:
            cogs += _voice_get_ci(out, "Waste")
        out["COGS"] = cogs

        out["MARGINE DI CONTRIBUZIONE"] = revenues - cogs
        out["LABOUR COST"] = sum(_voice_get_ci(out, v) for v in labour_items)
        out["DELIVERY FEES"] = sum(_voice_get_ci(out, v) for v in delivery_items)
        out["G&A STORE"] = sum(_voice_get_ci(out, v) for v in ga_items)
        out["TOTALE COSTI CONTROLLABILI"] = out["COGS"] + out["LABOUR COST"] + out["DELIVERY FEES"] + out["G&A STORE"]
        out["STORE EBITDA"] = revenues - out["TOTALE COSTI CONTROLLABILI"]
        out["EBITDA"] = out["STORE EBITDA"] - sum(_voice_get_ci(out, v) for v in ebitda_other)
        return out

    # Config grafici (ordine di visualizzazione)
    card_specs = [
        {"voice": "REVENUES", "metric": "eur", "title": "REVENUES (€)"},
        {"voice": "COGS", "metric": "pct", "title": "COGS (Inc.%)"},
        {"voice": "MARGINE DI CONTRIBUZIONE", "metric": "eur", "title": "MARGINE DI CONTRIBUZIONE (€)"},
        {"voice": "LABOUR COST", "metric": "eur", "title": "LABOUR COST (€)"},
        {"voice": "DELIVERY FEES", "metric": "eur", "title": "DELIVERY FEES (€)"},
        {"voice": "G&A STORE", "metric": "eur", "title": "G&A STORE (€)"},
        {"voice": "TOTALE COSTI CONTROLLABILI", "metric": "eur", "title": "TOTALE COSTI CONTROLLABILI (€)"},
        {"voice": "STORE EBITDA", "metric": "eur", "title": "STORE EBITDA (€)"},
        {"voice": "STORE EBITDA", "metric": "pct", "title": "STORE EBITDA (Inc.%)"},
        {"voice": "EBITDA", "metric": "eur", "title": "EBITDA (€)"},
        {"voice": "EBITDA", "metric": "pct", "title": "EBITDA (Inc.%)"},
    ]

    cards: List[Dict[str, Any]] = []
    for idx, spec in enumerate(card_specs):
        cards.append(
            {
                "id": f"c{idx+1}",
                "voice": spec["voice"],
                "metric": spec["metric"],  # 'eur' | 'pct'
                "title": spec["title"],
                "budget": [],
                "actual": [],
                "last": [],
            }
        )

    # Popola serie mese per mese
    for m in range(mf, mt + 1):
        b = compute_all(budget_matrix.get(m, {}), waste_to_cogs=True)
        a = compute_all(actual_matrix.get(m, {}))
        l = compute_all(last_matrix.get(m, {}))

        rev_b = _to_float(_voice_get_ci(b, "REVENUES"))
        rev_a = _to_float(_voice_get_ci(a, "REVENUES"))
        rev_l = _to_float(_voice_get_ci(l, "REVENUES"))

        for i, spec in enumerate(card_specs):
            voice = spec["voice"]
            metric = spec["metric"]

            vb = _to_float(_voice_get_ci(b, voice))
            va = _to_float(_voice_get_ci(a, voice))
            vl = _to_float(_voice_get_ci(l, voice))

            if metric == "pct":
                vb = _to_float(_pct(vb, rev_b))
                va = _to_float(_pct(va, rev_a))
                vl = _to_float(_pct(vl, rev_l))

            cards[i]["budget"].append(vb)
            cards[i]["actual"].append(va)
            cards[i]["last"].append(vl)

    return {
        "stores": store_codes,
        "year": int(year),
        "month_from": int(mf),
        "month_to": int(mt),
        "months": months,
        "cards": cards,
    }
