from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app_db import get_connection, sql_date
from daily_sales_repository import list_daily_sales_range
from primanota_repository import load_primanota_day
from orari_repository import list_turni_week
from sales_repository import list_sales_week
from delivery_repository import list_delivery_providers, list_weekly_rows
from orari_config_repository import list_orari_causali


def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _delivery_provider_aliases() -> Dict[str, str]:
    def norm(s: Any) -> str:
        return re.sub(r"\s+", " ", str(s or "").replace("_", " ").strip().upper()).strip()

    aliases: Dict[str, str] = {}
    try:
        for p in list_delivery_providers(active_only=True):
            platform = norm(p.get("platform"))
            if not platform:
                continue
            aliases[platform] = platform
            label = norm(p.get("label"))
            if label:
                aliases[label] = platform
            key = norm(p.get("provider_key"))
            if key:
                aliases[key] = platform
    except Exception:
        pass
    return aliases


def _to_date_iso(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    s = str(d).strip()
    if not s:
        return None
    try:
        if len(s) >= 10 and s[4] == '-' and s[7] == '-':
            return datetime.strptime(s[:10], '%Y-%m-%d').date().isoformat()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        return None


def _align_last_year_same_weekday(d: date) -> date:
    y = d.year - 1
    try:
        base = d.replace(year=y)
    except Exception:
        base = d - timedelta(days=365)
    delta = (d.weekday() - base.weekday()) % 7
    return base + timedelta(days=delta)


def _float(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, Decimal):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except Exception:
        return 0.0


def _int(v: Any) -> int:
    try:
        return int(round(_float(v)))
    except Exception:
        return 0


def _parse_hhmm(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.hour * 60 + v.minute
    s = str(v).strip()
    if not s:
        return None
    if len(s) >= 5 and s[2] in (':', '.'):
        try:
            h = int(s[:2]); m = int(s[3:5])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h * 60 + m
        except Exception:
            return None
    return None


def _duration_minutes(start_min: Optional[int], end_min: Optional[int]) -> int:
    if start_min is None or end_min is None:
        return 0
    s = int(start_min); e = int(end_min)
    if e < s:
        e += 24 * 60
    dur = e - s
    if dur < 0:
        return 0
    if dur > 16 * 60:
        return 0
    return dur


def _hours_from_turni_rows(turni: List[Dict[str, Any]]) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
    """Calcola:
    - ore_totali per giorno (solo ore "produttive", escludendo causali: ferie/permesso/allattamento/off/prestito e sempre training)
    - ore_stage per giorno (subset delle ore_totali per inquadramento == stage)
    - ore_training per giorno (solo ore con causale == training)
    """
    tot_by_day: Dict[str, float] = {}
    stage_by_day: Dict[str, float] = {}
    training_by_day: Dict[str, float] = {}

    try:
        causali_rules = {
            str((r or {}).get("name") or "").strip().lower(): dict(r or {})
            for r in list_orari_causali(active_only=True)
        }
    except Exception:
        causali_rules = {}
    excluded_nonprod = {"ferie", "permesso", "allattamento", "off", "prestito", "malattia", "riposo festivo", "training"}

    for r in turni:
        d_iso = str(r.get("data") or "").strip()
        if not d_iso:
            continue

        inq = str(r.get("inquadramento") or "").strip().lower()

        # Turno 1
        in1 = _parse_hhmm(r.get("inizio_1"))
        fi1 = _parse_hhmm(r.get("fine_1"))
        mins1 = _duration_minutes(in1, fi1)

        # Turno 2
        in2 = _parse_hhmm(r.get("inizio_2"))
        fi2 = _parse_hhmm(r.get("fine_2"))
        mins2 = _duration_minutes(in2, fi2)

        caus1 = str(r.get("causale") or "").strip().lower()
        caus2 = str(r.get("causale2") or "").strip().lower()

        def handle_shift(mins: int, caus: str) -> Tuple[float, float]:
            """Ritorna (ore_produttive, ore_training)."""
            if not mins:
                return 0.0, 0.0
            ore = mins / 60.0
            rule = causali_rules.get(str(caus or "").strip().lower()) or {}
            if bool(rule.get("counts_training", caus == "training")):
                return 0.0, ore
            if rule:
                return (ore if bool(rule.get("counts_productivity")) else 0.0), 0.0
            if caus in excluded_nonprod:
                return 0.0, 0.0
            return ore, 0.0

        ore_prod_1, ore_train_1 = handle_shift(mins1, caus1)
        ore_prod_2, ore_train_2 = handle_shift(mins2, caus2)

        ore_prod = ore_prod_1 + ore_prod_2
        ore_train = ore_train_1 + ore_train_2

        if ore_train:
            training_by_day[d_iso] = training_by_day.get(d_iso, 0.0) + ore_train

        if ore_prod:
            tot_by_day[d_iso] = tot_by_day.get(d_iso, 0.0) + ore_prod
            if inq == "stage":
                stage_by_day[d_iso] = stage_by_day.get(d_iso, 0.0) + ore_prod

    return tot_by_day, stage_by_day, training_by_day



def _access_has_table(cur, table_name: str) -> bool:
    t = str(table_name).strip().lower()
    try:
        for row in cur.tables(tableType="TABLE"):
            rn = str(getattr(row, "table_name", "") or getattr(row, "TABLE_NAME", "") or "").strip().lower()
            if rn == t:
                return True
    except Exception:
        pass
    return False


def _access_columns(cur, table_name: str) -> List[str]:
    cols: List[str] = []
    try:
        for c in cur.columns(table=table_name):
            name = str(getattr(c, "column_name", "") or getattr(c, "COLUMN_NAME", "") or "").strip()
            if name:
                cols.append(name)
    except Exception:
        pass
    return cols


def _pick_col(cols: List[str], candidates: Tuple[str, ...]) -> Optional[str]:
    norm = {c.strip().lower(): c for c in cols if c}
    for cand in candidates:
        if cand.strip().lower() in norm:
            return norm[cand.strip().lower()]
    for c in cols:
        for cand in candidates:
            if cand.strip().lower() in c.strip().lower():
                return c
    return None



# --- CMO / costo del lavoro -------------------------------------------------

_CMO_RATES_CACHE: Dict[str, Dict[str, float]] = {}


def _norm_contract(v: Any) -> str:
    x = re.sub(r"\s+", " ", str(v or "").strip().upper())
    return x


def load_cmo_rates(store_code: str) -> Dict[str, float]:
    """Carica mappa {CONTRATTO: costo_orario} da tabella CMO.

    - CONTRATTO in CMO corrisponde a Inquadramento in STAFF_P.
    - VALORE è il costo orario.
    """
    k = str(store_code or "").strip()
    if k in _CMO_RATES_CACHE:
        return _CMO_RATES_CACHE[k]

    rates: Dict[str, float] = {}
    try:
        conn = get_connection(store_code)
        try:
            cur = conn.cursor()
            if not _access_has_table(cur, "CMO"):
                _CMO_RATES_CACHE[k] = rates
                return rates

            cols = _access_columns(cur, "CMO")
            contr_col = _pick_col(cols, ("CONTRATTO", "Contratto", "INQUADRAMENTO", "Inquadramento"))
            val_col = _pick_col(cols, ("VALORE", "Valore", "COSTO", "Costo", "COSTO_ORARIO", "CostoOrario"))
            if not contr_col or not val_col:
                _CMO_RATES_CACHE[k] = rates
                return rates

            sql = f"SELECT {_qname(contr_col)}, {_qname(val_col)} FROM {_qname('CMO')}"
            cur.execute(sql)
            for row in cur.fetchall() or []:
                try:
                    contr = _norm_contract(row[0] if len(row) > 0 else None)
                    if not contr:
                        continue
                    val = _float(row[1] if len(row) > 1 else None)
                    if val:
                        rates[contr] = float(val)
                except Exception:
                    continue
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        rates = {}

    _CMO_RATES_CACHE[k] = rates
    return rates


def _match_rate(inq: str, rates: Dict[str, float]) -> float:
    if not inq:
        return 0.0
    k = _norm_contract(inq)
    if k in rates:
        return float(rates.get(k) or 0.0)

    # fallback: match parziale (es. inquadramento con dettagli extra)
    for kk, vv in (rates or {}).items():
        try:
            if kk and (kk in k or k in kk):
                return float(vv or 0.0)
        except Exception:
            continue
    return 0.0


def _labor_cost_from_turni_rows(turni: List[Dict[str, Any]], rates: Dict[str, float]) -> Dict[str, float]:
    """Ritorna costo del lavoro per giorno (EUR) per le ore presenti in STAFF_P.

    Regole:
    - esclude causali non produttive (ferie/permesso/allattamento/off/prestito/malattia)
    - include training (è comunque costo del lavoro)
    - costo = ore * costo_orario (da CMO)
    """
    cost_by_day: Dict[str, float] = {}
    try:
        causali_rules = {
            str((r or {}).get("name") or "").strip().lower(): dict(r or {})
            for r in list_orari_causali(active_only=True)
        }
    except Exception:
        causali_rules = {}
    excluded_nonprod = {"ferie", "permesso", "off", "prestito", "malattia"}

    for r in turni or []:
        d_iso = str(r.get("data") or "").strip()
        if not d_iso:
            continue

        rate = _match_rate(str(r.get("inquadramento") or ""), rates)

        # Turno 1
        in1 = _parse_hhmm(r.get("inizio_1"))
        fi1 = _parse_hhmm(r.get("fine_1"))
        mins1 = _duration_minutes(in1, fi1)

        # Turno 2
        in2 = _parse_hhmm(r.get("inizio_2"))
        fi2 = _parse_hhmm(r.get("fine_2"))
        mins2 = _duration_minutes(in2, fi2)

        caus1 = str(r.get("causale") or "").strip().lower()
        caus2 = str(r.get("causale2") or "").strip().lower()

        def cost_shift(mins: int, caus: str) -> float:
            if not mins:
                return 0.0
            rule = causali_rules.get(str(caus or "").strip().lower()) or {}
            if rule and not bool(rule.get("counts_labor_cost")):
                return 0.0
            if caus in excluded_nonprod:
                return 0.0
            ore = mins / 60.0
            return ore * rate

        c1 = cost_shift(mins1, caus1)
        c2 = cost_shift(mins2, caus2)
        tot = c1 + c2
        if tot:
            cost_by_day[d_iso] = cost_by_day.get(d_iso, 0.0) + tot

    return cost_by_day

def fetch_dati_database_day(*, store_code: str, day: date) -> Dict[str, Any]:
    try:
        from daily_sales_repository import get_daily_sales_day

        row = get_daily_sales_day(store_code=str(store_code), data_iso=day.isoformat())
        if row:
            return {
                "fatturato_lordo": _float(row.get("gross_revenue")),
                "scontrini": _int(row.get("receipts_count")),
            }
    except Exception:
        pass

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        table = "DatiDatabase"
        if not _access_has_table(cur, table):
            return {"fatturato_lordo": 0.0, "scontrini": 0}

        cols = _access_columns(cur, table)
        site_col = _pick_col(cols, ("Site", "SITE", "Store", "Negozio"))
        data_col = _pick_col(cols, ("Data", "DATA", "Date"))
        fatt_col = _pick_col(cols, ("FatturatoLordo", "Fatturato", "GiroAffariLordo"))
        scontr_col = _pick_col(cols, ("Scontrini", "SCONTRINI", "Receipt"))

        if not data_col or not fatt_col:
            return {"fatturato_lordo": 0.0, "scontrini": 0}

        where = [f"{sql_date(_qname(data_col))}=?"]
        params: List[Any] = [day]
        if site_col:
            where.append(f"{_qname(site_col)}=?")
            params.append(str(store_code).strip())

        sql = f"SELECT {_qname(fatt_col)} AS fatt, {_qname(scontr_col) if scontr_col else '0'} AS scontr FROM {_qname(table)} WHERE {' AND '.join(where)}"
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return {"fatturato_lordo": 0.0, "scontrini": 0}
        return {"fatturato_lordo": _float(row[0]), "scontrini": _int(row[1] if scontr_col else 0)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fetch_dati_database_range(*, store_code: str, start_day: date, end_day: date) -> Dict[str, Dict[str, Any]]:
    if end_day < start_day:
        start_day, end_day = end_day, start_day
    try:
        rows = list_daily_sales_range(
            store_code=str(store_code),
            start_day=start_day,
            end_day=end_day,
        ) or {}
    except Exception:
        rows = {}
    out: Dict[str, Dict[str, Any]] = {}
    for d_iso, row in rows.items():
        out[str(d_iso)] = {
            "fatturato_lordo": _float((row or {}).get("gross_revenue")),
            "scontrini": _int((row or {}).get("receipts_count")),
        }
    return out


def fetch_budget_day(*, store_code: str, day: date) -> float:
    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        table = "BudgetGiorno"
        if not _access_has_table(cur, table):
            return 0.0

        cols = _access_columns(cur, table)
        site_col = _pick_col(cols, ("Site", "SITE", "Store", "Negozio"))
        data_col = _pick_col(cols, ("Data", "DATA", "Date"))
        fatt_col = _pick_col(cols, ("FatturatoNetto", "Fatturato", "Sales", "Budget"))

        if not data_col or not fatt_col:
            return 0.0

        where = [f"{sql_date(_qname(data_col))}=?"]
        params: List[Any] = [day]
        if site_col:
            where.append(f"{_qname(site_col)}=?")
            params.append(str(store_code).strip())

        sql = f"SELECT {_qname(fatt_col)} FROM {_qname(table)} WHERE {' AND '.join(where)}"
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return 0.0
        return _float(row[0])
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fetch_budget_range(*, store_code: str, start_day: date, end_day: date) -> Dict[str, float]:
    if end_day < start_day:
        start_day, end_day = end_day, start_day

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        table = "BudgetGiorno"
        if not _access_has_table(cur, table):
            return {}

        cols = _access_columns(cur, table)
        site_col = _pick_col(cols, ("Site", "SITE", "Store", "Negozio"))
        data_col = _pick_col(cols, ("Data", "DATA", "Date"))
        fatt_col = _pick_col(cols, ("FatturatoNetto", "Fatturato", "Sales", "Budget"))

        if not data_col or not fatt_col:
            return {}

        where = [
            f"{sql_date(_qname(data_col))}>=?",
            f"{sql_date(_qname(data_col))}<=?",
        ]
        params: List[Any] = [start_day, end_day]
        if site_col:
            where.append(f"{_qname(site_col)}=?")
            params.append(str(store_code).strip())

        sql = f"""
        SELECT {sql_date(_qname(data_col))} AS d, {_qname(fatt_col)} AS budget
          FROM {_qname(table)}
         WHERE {' AND '.join(where)}
         ORDER BY {sql_date(_qname(data_col))}
        """
        cur.execute(sql, params)
        out: Dict[str, float] = {}
        for row in cur.fetchall() or []:
            d_val = row[0]
            if isinstance(d_val, datetime):
                d_iso = d_val.date().isoformat()
            elif isinstance(d_val, date):
                d_iso = d_val.isoformat()
            else:
                d_iso = str(d_val or "").strip()[:10]
            if d_iso:
                out[d_iso] = _float(row[1])
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_day_summary_kpis(*, store_code: str, day: date) -> Dict[str, Any]:
    """KPI giornalieri per pop-up post login.

    Restituisce:
    - budget_net: budget del giorno (tabella BudgetGiorno, valore netto)
    - ly_date: data LY allineata (stessa weekday) come in Cruscotto
    - ly_revenues_net: revenues net della giornata LY (da DatiDatabase /1.1)
    - forecast_net: previsione net della giornata (tabella Sales/Orari), se presente
    """
    out: Dict[str, Any] = {
        "day": day.isoformat(),
        "budget_net": 0.0,
        "ly_date": None,
        "ly_revenues_net": 0.0,
        "forecast_net": None,
    }

    # Budget del giorno
    try:
        b = fetch_budget_day(store_code=str(store_code), day=day)
        out["budget_net"] = float(b or 0.0)
    except Exception:
        out["budget_net"] = 0.0

    # Last year (stessa weekday) come cruscotto
    try:
        ly_d = _align_last_year_same_weekday(day)
        out["ly_date"] = ly_d.isoformat()
        ly = fetch_dati_database_day(store_code=str(store_code), day=ly_d)
        ly_lordo = float(ly.get("fatturato_lordo") or 0.0) if isinstance(ly, dict) else 0.0
        out["ly_revenues_net"] = (ly_lordo / 1.1) if ly_lordo else 0.0
    except Exception:
        out["ly_date"] = None
        out["ly_revenues_net"] = 0.0

    # Previsione netta del giorno (tabella Sales / valori già netti)
    try:
        from sales_repository import list_sales_week

        m = list_sales_week(store_code=str(store_code), start_day=day, end_day=day) or {}
        k = day.isoformat()
        if k in m:
            try:
                out["forecast_net"] = float(m.get(k) or 0.0)
            except Exception:
                out["forecast_net"] = 0.0
        else:
            out["forecast_net"] = None
    except Exception:
        out["forecast_net"] = None

    return out



def get_weekly_analysis(*, store_code: str, week_start: date, delivery_voci: List[str]) -> Dict[str, Any]:
    week_end = week_start + timedelta(days=6)

    # Previsioni (tabella Sales): valori già netti ("Previsione netta" in Orari)
    sales_by_day = list_sales_week(store_code=str(store_code), start_day=week_start, end_day=week_end)

    turni = list_turni_week(store_code=str(store_code), start_day=week_start, end_day=week_end, nominativi=None)
    ore_tot_by_day, ore_stage_by_day, ore_training_by_day = _hours_from_turni_rows(turni)
    cmo_rates = load_cmo_rates(store_code=str(store_code))
    labor_cost_by_day = _labor_cost_from_turni_rows(turni, cmo_rates)
    ly_week_start = _align_last_year_same_weekday(week_start)
    ly_week_end = ly_week_start + timedelta(days=6)
    ly_by_day = fetch_dati_database_range(store_code=str(store_code), start_day=ly_week_start, end_day=ly_week_end)
    budget_by_day = fetch_budget_range(store_code=str(store_code), start_day=week_start, end_day=week_end)

    provider_aliases = _delivery_provider_aliases()
    days_out: List[Dict[str, Any]] = []

    for i in range(7):
        d = week_start + timedelta(days=i)
        d_iso = d.isoformat()

        rev_forecast = float(sales_by_day.get(d_iso, 0.0) or 0.0)

        primanota = load_primanota_day(store_code=str(store_code), data_iso=d_iso, categories=["Dati chiusura", "Delivery"])

        vendite_lorde = 0.0
        annullati = 0.0
        scontrini = 0
        has_actual = False

        delivery_lordo_tot = 0.0
        delivery_online_lordo = 0.0
        delivery_cash_lordo = 0.0
        delivery_by_voce: Dict[str, float] = {v: 0.0 for v in delivery_voci}
        delivery_by_provider: Dict[str, Dict[str, float]] = {}

        for r in primanota:
            cat = str(r.get("categoria") or "").strip()
            voce = str(r.get("voce") or "").strip()
            val = _float(r.get("valore"))
            if cat == "Dati chiusura":
                vu = voce.upper()
                if vu == "VENDITE LORDE":
                    vendite_lorde += val
                    has_actual = True
                elif vu == "ANNULLATI":
                    annullati += val
                    has_actual = True
                elif vu == "SCONTRINI":
                    scontrini = _int(val)
                    has_actual = True
            elif cat == "Delivery":
                delivery_lordo_tot += val

                voce_up = re.sub(r"\s+", " ", (voce or "").upper()).strip()
                if voce_up:
                    is_cash = bool(re.search(r"\bCONTANTI\b", voce_up))
                    provider = re.sub(r"\bCONTANTI\b", "", voce_up).strip()
                    provider = re.sub(r"\s+", " ", provider).strip() or voce_up
                    provider = provider_aliases.get(provider, provider)

                    if is_cash:
                        delivery_cash_lordo += val
                    else:
                        delivery_online_lordo += val

                    bucket = delivery_by_provider.setdefault(provider, {"total": 0.0, "online": 0.0, "cash": 0.0})
                    bucket["total"] += val
                    if is_cash:
                        bucket["cash"] += val
                    else:
                        bucket["online"] += val

                if voce in delivery_by_voce:
                    delivery_by_voce[voce] += val

        revenue_lordo = vendite_lorde - annullati
        revenue_net = revenue_lordo / 1.1 if revenue_lordo else 0.0

        # Valore usato per grafico + produttività: se manca l'Actual usa la previsione
        revenue_chart = revenue_net if has_actual else rev_forecast
        projection_source = "Actual" if has_actual else "Previsione"

        ly_d = _align_last_year_same_weekday(d)
        ly = ly_by_day.get(ly_d.isoformat(), {"fatturato_lordo": 0.0, "scontrini": 0})
        ly_rev_net = (ly.get("fatturato_lordo") or 0.0) / 1.1 if (ly.get("fatturato_lordo") or 0.0) else 0.0
        ly_receipts = int(ly.get("scontrini") or 0)

        budget = budget_by_day.get(d_iso, 0.0)

        # Budget può arrivare come float (schema attuale) o come dict (legacy)
        if isinstance(budget, dict):
            budget_netto = float(budget.get("budget_netto") or 0.0)
        else:
            budget_netto = float(budget or 0.0)

        # Budget può arrivare come float (schema attuale) o come dict (legacy)
        if isinstance(budget, dict):
            budget_netto = float(budget.get("budget_netto") or 0.0)
        else:
            budget_netto = float(budget or 0.0)
        # budget può essere float (valore netto) oppure dict (legacy)
        if isinstance(budget, dict):
            budget_netto = float(budget.get("budget_netto") or 0.0)
        else:
            budget_netto = float(budget or 0.0)


        delivery_net_tot = delivery_lordo_tot / 1.1 if delivery_lordo_tot else 0.0
        delivery_online_net = delivery_online_lordo / 1.1 if delivery_online_lordo else 0.0
        delivery_cash_net = delivery_cash_lordo / 1.1 if delivery_cash_lordo else 0.0
        delivery_by_voce_net = {k: (v / 1.1 if v else 0.0) for k, v in delivery_by_voce.items()}

        delivery_by_provider_net: Dict[str, Dict[str, float]] = {}
        for prov, bucket in (delivery_by_provider or {}).items():
            try:
                tot = float(bucket.get("total") or 0.0)
                onl = float(bucket.get("online") or 0.0)
                cas = float(bucket.get("cash") or 0.0)
            except Exception:
                tot, onl, cas = 0.0, 0.0, 0.0
            delivery_by_provider_net[prov] = {
                "total": (tot / 1.1) if tot else 0.0,
                "online": (onl / 1.1) if onl else 0.0,
                "cash": (cas / 1.1) if cas else 0.0,
            }

        delivery_inc = (delivery_net_tot / revenue_net * 100.0) if revenue_net else 0.0

        avg_receipt = (revenue_net / scontrini) if scontrini else 0.0
        avg_receipt_ly = (ly_rev_net / ly_receipts) if ly_receipts else 0.0

        ore_tot = float(ore_tot_by_day.get(d_iso, 0.0))
        ore_stage = float(ore_stage_by_day.get(d_iso, 0.0))
        ore_training = float(ore_training_by_day.get(d_iso, 0.0))
        labor_cost = float(labor_cost_by_day.get(d_iso, 0.0))
        prod = (revenue_chart / ore_tot) if ore_tot else 0.0

        days_out.append(
            {
                "date": d_iso,
                "ly_date": ly_d.isoformat(),
                "revenues_actual": revenue_net,
                "revenues_forecast": rev_forecast,
                "revenues_chart": revenue_chart,
                "projection_source": projection_source,
                "revenues_ly": ly_rev_net,
                "revenues_budget": budget_netto,
                "receipts_actual": int(scontrini or 0),
                "receipts_ly": int(ly_receipts or 0),
                "avg_receipt_actual": avg_receipt,
                "avg_receipt_ly": avg_receipt_ly,
                "delivery_total": delivery_net_tot,
                "delivery_online": delivery_online_net,
                "delivery_cash": delivery_cash_net,
                "delivery_inc": delivery_inc,
                "delivery_by_voce": delivery_by_voce_net,
                "delivery_by_provider": delivery_by_provider_net,
                "labor_cost": labor_cost,
                "ore_totali": ore_tot,
                "ore_stage": ore_stage,
                "ore_training": ore_training,
                "produttivita": prod,
            }
        )

    totals: Dict[str, Any] = {}
    tot_rev = sum(d["revenues_actual"] for d in days_out)
    tot_rev_forecast = sum(d.get("revenues_forecast", 0.0) or 0.0 for d in days_out)
    tot_rev_chart = sum(d.get("revenues_chart", 0.0) or 0.0 for d in days_out)
    tot_rev_ly = sum(d["revenues_ly"] for d in days_out)
    tot_rev_budget = sum(d["revenues_budget"] for d in days_out)
    tot_receipts = sum(d["receipts_actual"] for d in days_out)
    tot_receipts_ly = sum(d["receipts_ly"] for d in days_out)
    tot_delivery = sum(d["delivery_total"] for d in days_out)
    tot_delivery_online = sum(d.get("delivery_online", 0.0) or 0.0 for d in days_out)
    tot_delivery_cash = sum(d.get("delivery_cash", 0.0) or 0.0 for d in days_out)
    tot_ore = sum(d["ore_totali"] for d in days_out)
    tot_ore_stage = sum(d["ore_stage"] for d in days_out)
    tot_ore_training = sum(d.get("ore_training", 0.0) or 0.0 for d in days_out)
    tot_labor_cost = sum(d.get("labor_cost", 0.0) or 0.0 for d in days_out)

    totals["revenues_actual"] = tot_rev
    totals["revenues_forecast"] = tot_rev_forecast
    totals["revenues_chart"] = tot_rev_chart
    totals["projection_days_actual"] = sum(1 for d in days_out if d.get("projection_source") == "Actual")
    totals["projection_days_forecast"] = sum(1 for d in days_out if d.get("projection_source") == "Previsione")
    totals["revenues_ly"] = tot_rev_ly
    totals["revenues_budget"] = tot_rev_budget
    totals["receipts_actual"] = tot_receipts
    totals["receipts_ly"] = tot_receipts_ly
    totals["avg_receipt_actual"] = (tot_rev / tot_receipts) if tot_receipts else 0.0
    totals["avg_receipt_ly"] = (tot_rev_ly / tot_receipts_ly) if tot_receipts_ly else 0.0
    totals["delivery_total"] = tot_delivery
    totals["delivery_online"] = tot_delivery_online
    totals["delivery_cash"] = tot_delivery_cash
    totals["delivery_inc"] = (tot_delivery / tot_rev_chart * 100.0) if tot_rev_chart else 0.0

    tot_delivery_by_voce: Dict[str, float] = {v: 0.0 for v in delivery_voci}
    for d in days_out:
        for v in delivery_voci:
            tot_delivery_by_voce[v] += float((d.get("delivery_by_voce") or {}).get(v, 0.0) or 0.0)
    totals["delivery_by_voce"] = tot_delivery_by_voce

    tot_delivery_providers: Dict[str, Dict[str, float]] = {}
    for d in days_out:
        for prov, bucket in (d.get("delivery_by_provider") or {}).items():
            agg = tot_delivery_providers.setdefault(prov, {"total": 0.0, "online": 0.0, "cash": 0.0})
            try:
                agg["total"] += float(bucket.get("total") or 0.0)
                agg["online"] += float(bucket.get("online") or 0.0)
                agg["cash"] += float(bucket.get("cash") or 0.0)
            except Exception:
                pass
    totals["delivery_providers"] = tot_delivery_providers

    totals["ore_totali"] = tot_ore
    totals["ore_stage"] = tot_ore_stage
    totals["ore_training"] = tot_ore_training
    totals["labor_cost"] = tot_labor_cost
    totals["labor_cost_pct"] = (tot_labor_cost / tot_rev * 100.0) if tot_rev else 0.0
    # Produttività settimanale: usa il fatturato "chart" (Actual o Previsione se mancante)
    totals["produttivita"] = (tot_rev_chart / tot_ore) if tot_ore else 0.0

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "days": days_out,
        "totals": totals,
    }


def get_monthly_analysis(*, store_code: str, month_start: date, delivery_voci: List[str]) -> Dict[str, Any]:
    """Analisi mensile con la stessa logica dell'analisi settimanale.

    - Calcola giorno per giorno.
    - Se per alcuni giorni del mese non esistono dati (né Actual, né Previsione), l'analisi si ferma all'ultimo giorno con dati.
    """
    # normalizza a primo giorno del mese
    ms = month_start.replace(day=1)
    # ultimo giorno del mese
    import calendar as _cal
    last_day = _cal.monthrange(ms.year, ms.month)[1]
    me = ms.replace(day=last_day)

    # Previsioni (tabella Sales): valori già netti ("Previsione netta" in Orari)
    sales_by_day = list_sales_week(store_code=str(store_code), start_day=ms, end_day=me)

    turni = list_turni_week(store_code=str(store_code), start_day=ms, end_day=me, nominativi=None)
    ore_tot_by_day, ore_stage_by_day, ore_training_by_day = _hours_from_turni_rows(turni)
    cmo_rates = load_cmo_rates(store_code=str(store_code))
    labor_cost_by_day = _labor_cost_from_turni_rows(turni, cmo_rates)
    ly_month_start = _align_last_year_same_weekday(ms)
    ly_month_end = _align_last_year_same_weekday(me)
    ly_by_day = fetch_dati_database_range(store_code=str(store_code), start_day=ly_month_start, end_day=ly_month_end)
    budget_by_day = fetch_budget_range(store_code=str(store_code), start_day=ms, end_day=me)

    provider_aliases = _delivery_provider_aliases()
    days_out: List[Dict[str, Any]] = []
    last_included: Optional[date] = None

    d = ms
    while d <= me:
        d_iso = d.isoformat()

        # previsione: consideriamo "presente" solo se esiste una riga in Sales per quel giorno (anche se valore 0)
        has_forecast = d_iso in sales_by_day
        rev_forecast = float(sales_by_day.get(d_iso, 0.0) or 0.0)

        primanota = load_primanota_day(store_code=str(store_code), data_iso=d_iso, categories=["Dati chiusura", "Delivery"])

        vendite_lorde = 0.0
        annullati = 0.0
        scontrini = 0
        has_actual = False

        delivery_lordo_tot = 0.0
        delivery_online_lordo = 0.0
        delivery_cash_lordo = 0.0
        delivery_by_voce: Dict[str, float] = {v: 0.0 for v in delivery_voci}
        delivery_by_provider: Dict[str, Dict[str, float]] = {}

        for r in primanota:
            cat = str(r.get("categoria") or "").strip()
            voce = str(r.get("voce") or "").strip()
            val = _float(r.get("valore"))
            if cat == "Dati chiusura":
                vu = str(voce).strip().upper()
                if vu == "VENDITE LORDE":
                    vendite_lorde += val
                    has_actual = True
                elif vu == "ANNULLATI":
                    annullati += val
                    has_actual = True
                elif vu == "SCONTRINI":
                    try:
                        scontrini += int(val)
                    except Exception:
                        pass
                    has_actual = True
            elif cat == "Delivery":
                # Totale delivery (lorda) + split Online/Contanti + split per provider
                if not voce:
                    continue

                v_up = str(voce).strip().upper()
                # Regola: "CONTANTI" => cash, altrimenti online (robusta per provider futuri)
                is_cash = "CONTANTI" in v_up

                import re as _re
                provider_base = _re.sub(r"\bCONTANTI\b", "", v_up)
                provider_base = _re.sub(r"\s+", " ", provider_base).strip()
                provider_base = provider_base or v_up
                provider_base = provider_aliases.get(provider_base, provider_base)

                delivery_lordo_tot += val
                if is_cash:
                    delivery_cash_lordo += val
                else:
                    delivery_online_lordo += val

                # dettaglio voci (se richiesto)
                if voce in delivery_by_voce:
                    delivery_by_voce[voce] += val

                bucket = delivery_by_provider.setdefault(
                    provider_base,
                    {"total_lordo": 0.0, "online_lordo": 0.0, "cash_lordo": 0.0},
                )
                bucket["total_lordo"] += val
                if is_cash:
                    bucket["cash_lordo"] += val
                else:
                    bucket["online_lordo"] += val

        revenue_lordo = vendite_lorde - annullati
        revenue_net = revenue_lordo / 1.1 if revenue_lordo else 0.0

        if has_actual:
            revenue_chart = revenue_net
            projection_source = "Actual"
        elif has_forecast:
            revenue_chart = rev_forecast
            projection_source = "Previsione"
        else:
            revenue_chart = 0.0
            projection_source = ""

        if has_actual or has_forecast:
            if last_included is None or d > last_included:
                last_included = d

        ly_d = _align_last_year_same_weekday(d)
        ly = ly_by_day.get(ly_d.isoformat(), {"fatturato_lordo": 0.0, "scontrini": 0})
        ly_rev_net = (ly.get("fatturato_lordo") or 0.0) / 1.1 if (ly.get("fatturato_lordo") or 0.0) else 0.0
        ly_receipts = int(ly.get("scontrini") or 0)

        budget = budget_by_day.get(d_iso, 0.0)

        # Budget può arrivare come float (schema attuale) o come dict (legacy)
        if isinstance(budget, dict):
            budget_netto = float(budget.get("budget_netto") or 0.0)
        else:
            budget_netto = float(budget or 0.0)

        delivery_net_tot = delivery_lordo_tot / 1.1 if delivery_lordo_tot else 0.0
        delivery_online_net = delivery_online_lordo / 1.1 if delivery_online_lordo else 0.0
        delivery_cash_net = delivery_cash_lordo / 1.1 if delivery_cash_lordo else 0.0

        delivery_by_voce_net: Dict[str, float] = {
            v: (delivery_by_voce.get(v, 0.0) / 1.1 if delivery_by_voce.get(v, 0.0) else 0.0)
            for v in delivery_voci
        }

        delivery_by_provider_net: Dict[str, Dict[str, float]] = {}
        for prov, b in delivery_by_provider.items():
            tl = float(b.get("total_lordo") or 0.0)
            ol = float(b.get("online_lordo") or 0.0)
            cl = float(b.get("cash_lordo") or 0.0)
            delivery_by_provider_net[prov] = {
                "total": tl / 1.1 if tl else 0.0,
                "online": ol / 1.1 if ol else 0.0,
                "cash": cl / 1.1 if cl else 0.0,
            }

        ore_tot = float(ore_tot_by_day.get(d_iso, 0.0) or 0.0)
        ore_stage = float(ore_stage_by_day.get(d_iso, 0.0) or 0.0)
        ore_training = float(ore_training_by_day.get(d_iso, 0.0) or 0.0)
        labor_cost = float(labor_cost_by_day.get(d_iso, 0.0) or 0.0)

        prod = (revenue_chart / ore_tot) if ore_tot else 0.0

        days_out.append(
            {
                "date": d_iso,
                "projection_source": projection_source,
                "revenues_actual": revenue_net if has_actual else 0.0,
                "revenues_forecast": rev_forecast,
                "revenues_chart": revenue_chart,
                "revenues_ly": ly_rev_net,
                "ly_date": ly_d.isoformat(),
                "revenues_budget": budget_netto,
                "receipts_actual": scontrini if has_actual else 0,
                "receipts_ly": ly_receipts,
                "avg_receipt_actual": (revenue_net / scontrini) if (has_actual and scontrini) else 0.0,
                "avg_receipt_ly": (ly_rev_net / ly_receipts) if ly_receipts else 0.0,
                "delivery_total": delivery_net_tot,
                "delivery_online": delivery_online_net,
                "delivery_cash": delivery_cash_net,
                "delivery_inc": (delivery_net_tot / revenue_chart * 100.0) if revenue_chart else 0.0,
                "delivery_by_voce": delivery_by_voce_net,
                "delivery_by_provider": delivery_by_provider_net,
                "labor_cost": labor_cost,
                "ore_totali": ore_tot,
                "ore_stage": ore_stage,
                "ore_training": ore_training,
                "produttivita": prod,
            }
        )

        d += timedelta(days=1)

    # Se il mese non ha dati: ritorna struttura vuota ma coerente
    if last_included is None:
        return {
            "month_start": ms.isoformat(),
            "month_end": me.isoformat(),
            "display_end": ms.isoformat(),
            "is_full_month": False,
            "days": [],
            "totals": {
                "revenues_actual": 0.0,
                "revenues_forecast": 0.0,
                "revenues_chart": 0.0,
                "projection_days_actual": 0,
                "projection_days_forecast": 0,
                "projection_days_missing": 0,
                "revenues_ly": 0.0,
                "revenues_budget": 0.0,
                "receipts_actual": 0,
                "receipts_ly": 0,
                "avg_receipt_actual": 0.0,
                "avg_receipt_ly": 0.0,
                "delivery_total": 0.0,
                "delivery_online": 0.0,
                "delivery_cash": 0.0,
                "delivery_inc": 0.0,
                "delivery_by_voce": {v: 0.0 for v in delivery_voci},
                "delivery_providers": {},
                "ore_totali": 0.0,
                "ore_stage": 0.0,
                "ore_training": 0.0,
                "produttivita": 0.0,
            },
        }

    # Taglia al last_included (ultimo giorno con Actual o Previsione)
    days_out = [x for x in days_out if date.fromisoformat(str(x.get("date"))) <= last_included]

    totals: Dict[str, Any] = {}
    tot_rev = sum(d.get("revenues_actual", 0.0) or 0.0 for d in days_out)
    tot_rev_forecast = sum(d.get("revenues_forecast", 0.0) or 0.0 for d in days_out)
    tot_rev_chart = sum(d.get("revenues_chart", 0.0) or 0.0 for d in days_out)
    tot_rev_ly = sum(d.get("revenues_ly", 0.0) or 0.0 for d in days_out)
    tot_rev_budget = sum(d.get("revenues_budget", 0.0) or 0.0 for d in days_out)
    tot_receipts = sum(int(d.get("receipts_actual", 0) or 0) for d in days_out)
    tot_receipts_ly = sum(int(d.get("receipts_ly", 0) or 0) for d in days_out)
    tot_delivery = sum(d.get("delivery_total", 0.0) or 0.0 for d in days_out)
    tot_delivery_online = sum(d.get("delivery_online", 0.0) or 0.0 for d in days_out)
    tot_delivery_cash = sum(d.get("delivery_cash", 0.0) or 0.0 for d in days_out)
    tot_ore = sum(d.get("ore_totali", 0.0) or 0.0 for d in days_out)
    tot_ore_stage = sum(d.get("ore_stage", 0.0) or 0.0 for d in days_out)
    tot_ore_training = sum(d.get("ore_training", 0.0) or 0.0 for d in days_out)
    tot_labor_cost = sum(d.get("labor_cost", 0.0) or 0.0 for d in days_out)

    totals["revenues_actual"] = tot_rev
    totals["revenues_forecast"] = tot_rev_forecast
    totals["revenues_chart"] = tot_rev_chart
    totals["projection_days_actual"] = sum(1 for d in days_out if d.get("projection_source") == "Actual")
    totals["projection_days_forecast"] = sum(1 for d in days_out if d.get("projection_source") == "Previsione")
    totals["projection_days_missing"] = sum(1 for d in days_out if not d.get("projection_source"))
    totals["revenues_ly"] = tot_rev_ly
    totals["revenues_budget"] = tot_rev_budget
    totals["receipts_actual"] = tot_receipts
    totals["receipts_ly"] = tot_receipts_ly
    totals["avg_receipt_actual"] = (tot_rev / tot_receipts) if tot_receipts else 0.0
    totals["avg_receipt_ly"] = (tot_rev_ly / tot_receipts_ly) if tot_receipts_ly else 0.0
    totals["delivery_total"] = tot_delivery
    totals["delivery_online"] = tot_delivery_online
    totals["delivery_cash"] = tot_delivery_cash
    totals["delivery_inc"] = (tot_delivery / tot_rev_chart * 100.0) if tot_rev_chart else 0.0

    tot_delivery_by_voce: Dict[str, float] = {v: 0.0 for v in delivery_voci}
    for d in days_out:
        for v in delivery_voci:
            tot_delivery_by_voce[v] += float((d.get("delivery_by_voce") or {}).get(v, 0.0) or 0.0)
    totals["delivery_by_voce"] = tot_delivery_by_voce

    tot_delivery_providers: Dict[str, Dict[str, float]] = {}
    for d in days_out:
        for prov, bucket in (d.get("delivery_by_provider") or {}).items():
            agg = tot_delivery_providers.setdefault(prov, {"total": 0.0, "online": 0.0, "cash": 0.0})
            try:
                agg["total"] += float(bucket.get("total") or 0.0)
                agg["online"] += float(bucket.get("online") or 0.0)
                agg["cash"] += float(bucket.get("cash") or 0.0)
            except Exception:
                pass
    totals["delivery_providers"] = tot_delivery_providers

    totals["ore_totali"] = tot_ore
    totals["ore_stage"] = tot_ore_stage
    totals["ore_training"] = tot_ore_training
    totals["labor_cost"] = tot_labor_cost
    totals["labor_cost_pct"] = (tot_labor_cost / tot_rev * 100.0) if tot_rev else 0.0
    totals["produttivita"] = (tot_rev_chart / tot_ore) if tot_ore else 0.0

    return {
        "month_start": ms.isoformat(),
        "month_end": me.isoformat(),
        "display_end": last_included.isoformat(),
        "is_full_month": last_included == me,
        "days": days_out,
        "totals": totals,
    }


def _it_num(x: float | int | None, decimals: int = 0) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except Exception:
        return "—"
    s = f"{v:,.{decimals}f}"
    # en -> it
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def _it_eur(x: float | int | None, decimals: int = 0) -> str:
    if x is None:
        return "—"
    return f"€{_it_num(x, decimals)}"


def _it_pct(x: float | int | None, decimals: int = 1) -> str:
    if x is None:
        return "—"
    return f"{_it_num(x, decimals)}%"


def _safe_pct(delta: float, base: float) -> float | None:
    try:
        if base == 0:
            return None
        return (delta / base) * 100.0
    except Exception:
        return None


def get_weekly_kpi_overview(store_code: str, week_start: date) -> Dict[str, Any]:
    """Panoramica KPI settimanale (Cruscotto).

    Integra:
    - Revenues (Actual/Prev), Budget, Last Year
    - Delivery & Reclami (da dbo.DELIVERY_WEEKLY)
    - Costo del lavoro (€/%, da CMO)
    - Tag values per relazione (note)
    """

    ws = week_start
    we = ws + timedelta(days=6)
    prev_ws = ws - timedelta(days=7)

    # Weekly core (revenues/receipts/hours/budget/ly)
    cur = get_weekly_analysis(store_code=store_code, week_start=ws, delivery_voci=[])
    prev = get_weekly_analysis(store_code=store_code, week_start=prev_ws, delivery_voci=[])

    cur_tot = cur.get("totals") or {}
    prev_tot = prev.get("totals") or {}

    rev_actual = float(cur_tot.get("revenues_actual") or 0.0)
    rev_budget = float(cur_tot.get("revenues_budget") or 0.0)
    rev_ly = float(cur_tot.get("revenues_ly") or 0.0)

    delta_budget_val = rev_actual - rev_budget
    delta_budget_pct = _safe_pct(delta_budget_val, rev_budget)

    delta_ly_val = rev_actual - rev_ly
    delta_ly_pct = _safe_pct(delta_ly_val, rev_ly)

    receipts_cur = int(cur_tot.get("receipts_actual") or 0)
    receipts_prev = int(prev_tot.get("receipts_actual") or 0)

    hours_cur = float(cur_tot.get("ore_totali") or 0.0)
    hours_prev = float(prev_tot.get("ore_totali") or 0.0)

    prod_cur = float(cur_tot.get("produttivita") or 0.0)
    prod_prev = float(prev_tot.get("produttivita") or 0.0)
    prod_delta = prod_cur - prod_prev
    prod_delta_pct = _safe_pct(prod_delta, prod_prev) if prod_prev else None

    rev_prev_actual = float(prev_tot.get("revenues_actual") or 0.0)
    rev_prev_budget = float(prev_tot.get("revenues_budget") or 0.0)
    rev_prev_ly = float(prev_tot.get("revenues_ly") or 0.0)

    prev_delta_budget_val = rev_prev_actual - rev_prev_budget
    prev_delta_budget_pct = _safe_pct(prev_delta_budget_val, rev_prev_budget)

    prev_delta_ly_val = rev_prev_actual - rev_prev_ly
    prev_delta_ly_pct = _safe_pct(prev_delta_ly_val, rev_prev_ly)

    # Labor cost (week / prev week) + supporto MTD
    labor_cost_cur = float(cur_tot.get("labor_cost") or 0.0)
    labor_cost_prev = float(prev_tot.get("labor_cost") or 0.0)
    labor_pct_cur = (labor_cost_cur / rev_actual * 100.0) if rev_actual else 0.0
    labor_pct_prev = (labor_cost_prev / rev_prev_actual * 100.0) if rev_prev_actual else 0.0
    labor_delta_val = labor_cost_cur - labor_cost_prev
    labor_delta_pct = _safe_pct(labor_delta_val, labor_cost_prev) if labor_cost_prev else None
    labor_delta_pp = labor_pct_cur - labor_pct_prev

    # Week -2 (per scostamenti week -1)
    prev2_ws = ws - timedelta(days=14)
    prev2 = get_weekly_analysis(store_code=store_code, week_start=prev2_ws, delivery_voci=[])
    prev2_tot = (prev2.get("totals") or {})
    labor_cost_prev2 = float(prev2_tot.get("labor_cost") or 0.0)
    rev_prev2_actual = float(prev2_tot.get("revenues_actual") or 0.0)
    labor_pct_prev2 = (labor_cost_prev2 / rev_prev2_actual * 100.0) if rev_prev2_actual else 0.0

    # Last year (allineato per weekday, coerente con revenues_ly)
    def _sum_labor_and_hours(start_day: date, end_day: date) -> Tuple[float, float]:
        try:
            turni = list_turni_week(store_code=str(store_code), start_day=start_day, end_day=end_day, nominativi=None)
            ore_tot_by_day, _, _ = _hours_from_turni_rows(turni)
            cmo_rates = load_cmo_rates(store_code=str(store_code))
            labor_cost_by_day = _labor_cost_from_turni_rows(turni, cmo_rates)
            tot_hours = 0.0
            tot_cost = 0.0
            d = start_day
            while d <= end_day:
                di = d.isoformat()
                tot_hours += float(ore_tot_by_day.get(di, 0.0) or 0.0)
                tot_cost += float(labor_cost_by_day.get(di, 0.0) or 0.0)
                d += timedelta(days=1)
            return tot_cost, tot_hours
        except Exception:
            return 0.0, 0.0

    ly_ws = _align_last_year_same_weekday(ws)
    ly_prev_ws = _align_last_year_same_weekday(prev_ws)
    ly_we = ly_ws + timedelta(days=6)
    ly_prev_we = ly_prev_ws + timedelta(days=6)

    labor_cost_ly, hours_ly = _sum_labor_and_hours(ly_ws, ly_we)
    labor_cost_prev_ly, hours_prev_ly = _sum_labor_and_hours(ly_prev_ws, ly_prev_we)

    labor_pct_ly = (labor_cost_ly / rev_ly * 100.0) if rev_ly else 0.0
    labor_pct_prev_ly = (labor_cost_prev_ly / rev_prev_ly * 100.0) if rev_prev_ly else 0.0

    labor_delta_vs_ly_val = labor_cost_cur - labor_cost_ly
    labor_delta_vs_ly_pct = _safe_pct(labor_delta_vs_ly_val, labor_cost_ly) if labor_cost_ly else None
    labor_delta_vs_ly_pp = labor_pct_cur - labor_pct_ly

    labor_prev_delta_vs_ly_val = labor_cost_prev - labor_cost_prev_ly
    labor_prev_delta_vs_ly_pct = _safe_pct(labor_prev_delta_vs_ly_val, labor_cost_prev_ly) if labor_cost_prev_ly else None
    labor_prev_delta_vs_ly_pp = labor_pct_prev - labor_pct_prev_ly

    proj = {
        "days_actual": int(cur_tot.get("projection_days_actual") or 0),
        "days_forecast": int(cur_tot.get("projection_days_forecast") or 0),
        "days_missing": int(cur_tot.get("projection_days_missing") or 0),
    }

    # Month-to-date (actual only) for month of week_end (Sunday of selected week)
    month_end = we
    month_start = date(month_end.year, month_end.month, 1)
    m_analysis = get_monthly_analysis(store_code=store_code, month_start=month_start, delivery_voci=[])
    m_display_end_iso = str((m_analysis or {}).get("display_end") or month_end.isoformat())
    try:
        m_display_end = date.fromisoformat(m_display_end_iso[:10])
    except Exception:
        m_display_end = month_end
    mtd_end = month_end if month_end <= m_display_end else m_display_end

    mtd_actual = 0.0
    mtd_budget = 0.0
    mtd_ly = 0.0
    mtd_receipts = 0
    mtd_receipts_ly = 0
    for drow in (m_analysis.get("days") or []):
        di = str(drow.get("date") or "").strip()
        if not di:
            continue
        if di <= mtd_end.isoformat():
            try:
                mtd_actual += float(drow.get("revenues_actual") or 0.0)
            except Exception:
                pass
            try:
                mtd_budget += float(drow.get("revenues_budget") or 0.0)
            except Exception:
                pass
            try:
                mtd_ly += float(drow.get("revenues_ly") or 0.0)
            except Exception:
                pass
            try:
                mtd_receipts += int(drow.get("receipts_actual") or 0)
            except Exception:
                pass
            try:
                mtd_receipts_ly += int(drow.get("receipts_ly") or 0)
            except Exception:
                pass

    # MTD labor cost / hours (stesso periodo MTD di revenues)
    mtd_labor_cost = 0.0
    mtd_hours = 0.0
    for drow in (m_analysis.get("days") or []):
        di = str(drow.get("date") or "").strip()
        if not di:
            continue
        if di <= mtd_end.isoformat():
            try:
                mtd_labor_cost += float(drow.get("labor_cost") or 0.0)
            except Exception:
                pass
            try:
                mtd_hours += float(drow.get("ore_totali") or 0.0)
            except Exception:
                pass
    mtd_labor_pct = (mtd_labor_cost / mtd_actual * 100.0) if mtd_actual else 0.0

    # MTD fino a fine settimana precedente (per confronti "vs week -1" sul progressivo)
    mtd_prev_week_end = ws - timedelta(days=1)  # domenica prima della settimana selezionata
    mtd_prev_cost = None
    mtd_prev_hours = None
    mtd_prev_pct = None
    mtd_delta_vs_prev_val = None
    mtd_delta_vs_prev_pp = None
    if mtd_prev_week_end >= month_start and mtd_prev_week_end <= mtd_end:
        _c = 0.0
        _h = 0.0
        _r = 0.0
        for drow in (m_analysis.get("days") or []):
            di = str(drow.get("date") or "").strip()
            if not di or di > mtd_prev_week_end.isoformat():
                continue
            try:
                _c += float(drow.get("labor_cost") or 0.0)
            except Exception:
                pass
            try:
                _h += float(drow.get("ore_totali") or 0.0)
            except Exception:
                pass
            try:
                _r += float(drow.get("revenues_actual") or 0.0)
            except Exception:
                pass
        mtd_prev_cost = _c
        mtd_prev_hours = _h
        mtd_prev_pct = (_c / _r * 100.0) if _r else 0.0
        mtd_delta_vs_prev_val = mtd_labor_cost - _c
        mtd_delta_vs_prev_pp = mtd_labor_pct - (mtd_prev_pct or 0.0)

    # MTD last year (stesso giorno del mese) per confronto
    try:
        import calendar as _cal
        ly_ms = date(month_start.year - 1, month_start.month, 1)
        ly_last_day = _cal.monthrange(ly_ms.year, ly_ms.month)[1]
        ly_end = date(ly_ms.year, ly_ms.month, min(mtd_end.day, ly_last_day))
    except Exception:
        ly_ms = date(month_start.year - 1, month_start.month, 1)
        ly_end = ly_ms

    mtd_labor_cost_ly, mtd_hours_ly = _sum_labor_and_hours(ly_ms, ly_end)

    # Revenues last year MTD: StoreHub-native con fallback legacy DatiDatabase.
    mtd_rev_ly = 0.0
    try:
        from daily_sales_repository import get_revenues_net_range

        mtd_rev_ly = float(
            get_revenues_net_range(
                store_code=str(store_code),
                start_day=ly_ms,
                end_day=ly_end,
            )
            or 0.0
        )
    except Exception:
        mtd_rev_ly = 0.0

    mtd_labor_pct_ly = (mtd_labor_cost_ly / mtd_rev_ly * 100.0) if mtd_rev_ly else 0.0
    mtd_labor_delta_vs_ly_val = mtd_labor_cost - mtd_labor_cost_ly
    mtd_labor_delta_vs_ly_pct = _safe_pct(mtd_labor_delta_vs_ly_val, mtd_labor_cost_ly) if mtd_labor_cost_ly else None
    mtd_labor_delta_vs_ly_pp = mtd_labor_pct - mtd_labor_pct_ly

    mtd_delta_budget_val = mtd_actual - mtd_budget
    mtd_delta_budget_pct = _safe_pct(mtd_delta_budget_val, mtd_budget)

    mtd_delta_ly_val = mtd_actual - mtd_ly
    mtd_delta_ly_pct = _safe_pct(mtd_delta_ly_val, mtd_ly)

    # Delivery weekly (SQL)
    rows = list_weekly_rows(store_code=store_code, week_start=ws)
    rows_prev = list_weekly_rows(store_code=store_code, week_start=prev_ws)
    def _norm_provider(name: str) -> str:
        x = re.sub(r"\s+", " ", str(name or "").strip().upper())
        x = re.sub(r"\bCONTANTI\b", "", x).strip()
        x = re.sub(r"\s+", " ", x).strip()
        return x

    rows_by_platform = {_norm_provider(r.platform): r for r in (rows or []) if str(getattr(r, 'platform', '') or '').strip()}
    prev_rows_by_platform = {_norm_provider(r.platform): r for r in (rows_prev or []) if str(getattr(r, 'platform', '') or '').strip()}
    try:
        delivery_provider_config = {
            _norm_provider(p.get("platform")): p
            for p in list_delivery_providers(active_only=True)
            if str(p.get("platform") or "").strip()
        }
    except Exception:
        delivery_provider_config = {}

    # Importi € delivery da Distinte (DATIPRIMANOTA -> categoria Delivery), già convertiti a netto in get_weekly_analysis
    delivery_total = float(cur_tot.get('delivery_total') or 0.0)
    delivery_online = float(cur_tot.get('delivery_online') or 0.0)
    delivery_cash = float(cur_tot.get('delivery_cash') or 0.0)
    delivery_providers = cur_tot.get('delivery_providers') or {}

    delivery_total_prev = float(prev_tot.get('delivery_total') or 0.0)
    delivery_online_prev = float(prev_tot.get('delivery_online') or 0.0)
    delivery_cash_prev = float(prev_tot.get('delivery_cash') or 0.0)
    orders_total = sum(int(r.orders or 0) for r in rows)
    cancelled_orders_total = sum(int(getattr(r, 'cancelled_orders', 0) or 0) for r in rows)
    complaints_received = sum(int(r.complaints_received or 0) for r in rows)
    refund_value = sum(float(r.refund_value or 0) for r in rows)
    refunds_cancelled = sum(float(r.refunds_cancelled_value or 0) for r in rows)
    refunds_net = refund_value - refunds_cancelled
    complaints_contested = sum(int(r.complaints_contested or 0) for r in rows)
    appeals_accepted = sum(int(r.appeals_accepted or 0) for r in rows)

    delivery_incidence_pct = (delivery_total / rev_actual * 100.0) if rev_actual else 0.0
    avg_delivery_receipt = (delivery_total / orders_total) if orders_total else 0.0
    refunds_incidence_pct = (refunds_net / delivery_total * 100.0) if delivery_total else 0.0
    complaints_rate_pct = (complaints_received / orders_total * 100.0) if orders_total else 0.0
    complaints_rate_net_pct = ((max(complaints_received - appeals_accepted, 0)) / orders_total * 100.0) if orders_total else 0.0
    orders_incidence_receipts_pct = (orders_total / receipts_cur * 100.0) if receipts_cur else 0.0

    # prev week metrics (for deltas)
    orders_prev = sum(int(r.orders or 0) for r in rows_prev)
    cancelled_orders_prev = sum(int(getattr(r, 'cancelled_orders', 0) or 0) for r in rows_prev)
    refund_prev = sum(float(r.refund_value or 0) for r in rows_prev)
    refunds_cancelled_prev = sum(float(r.refunds_cancelled_value or 0) for r in rows_prev)
    refunds_net_prev = refund_prev - refunds_cancelled_prev
    complaints_prev = sum(int(r.complaints_received or 0) for r in rows_prev)

    delivery_incidence_prev_pct = (delivery_total_prev / rev_prev_actual * 100.0) if rev_prev_actual else 0.0
    refunds_incidence_prev_pct = (refunds_net_prev / delivery_total_prev * 100.0) if delivery_total_prev else 0.0
    complaints_rate_prev_pct = (complaints_prev / orders_prev * 100.0) if orders_prev else 0.0
    orders_incidence_prev_pct = (orders_prev / receipts_prev * 100.0) if receipts_prev else 0.0

    def _normalize_opening_pct(platform_name: Any, stored_pct_value: Any) -> float | None:
        try:
            pct = float(stored_pct_value)
        except Exception:
            return None
        if pct < 0:
            pct = 0.0
        if pct > 100:
            pct = 100.0
        plat_name = _norm_provider(platform_name)
        provider_cfg = delivery_provider_config.get(plat_name) or {}
        opening_mode = str(provider_cfg.get("opening_mode") or "opening").strip().lower()
        if opening_mode == 'closure':
            pct = 100.0 - pct
        if pct < 0:
            pct = 0.0
        if pct > 100:
            pct = 100.0
        return pct

    def _opening_estimates(actual_total: float, opening_pct_value: Any) -> tuple[float | None, float | None]:
        try:
            pct = float(opening_pct_value)
        except Exception:
            return None, None
        if pct <= 0:
            return None, None
        if pct > 100:
            pct = 100.0
        if actual_total < 0:
            actual_total = 0.0
        potential = actual_total / (pct / 100.0) if pct > 0 else None
        if potential is None:
            return None, None
        lost = max((potential - actual_total), 0.0)
        return potential, lost

    opening_actual_considered = 0.0
    opening_potential_estimated = 0.0
    opening_lost_sales_estimated = 0.0

    platforms_out: list[dict[str, Any]] = []

    all_platforms = set()
    try:
        all_platforms |= set((delivery_providers or {}).keys())
    except Exception:
        pass
    try:
        all_platforms |= set((rows_by_platform or {}).keys())
    except Exception:
        pass

    for plat in sorted(p for p in all_platforms if str(p or '').strip()):
        r = (rows_by_platform or {}).get(plat)
        pr = (prev_rows_by_platform or {}).get(plat)

        fin = (delivery_providers or {}).get(plat) or {}
        total = float(fin.get('total') or 0.0)
        total_online = float(fin.get('online') or 0.0)
        total_cash = float(fin.get('cash') or 0.0)

        rating_delta = None
        if r and (r.rating_value is not None) and pr and (pr.rating_value is not None) and str(r.rating_unit) == str(pr.rating_unit):
            try:
                rating_delta = float(r.rating_value) - float(pr.rating_value)
            except Exception:
                rating_delta = None

        opening_pct_val = _normalize_opening_pct(plat, getattr(r, 'opening_pct', None) if r else None)
        opening_potential_est, opening_lost_est = _opening_estimates(total, opening_pct_val)
        if opening_potential_est is not None:
            opening_actual_considered += total
            opening_potential_estimated += opening_potential_est
            opening_lost_sales_estimated += float(opening_lost_est or 0.0)

        platforms_out.append(
            {
                'platform': plat,
                'label': str((delivery_provider_config.get(plat) or {}).get('label') or plat),
                'opening_mode': str((delivery_provider_config.get(plat) or {}).get('opening_mode') or 'opening'),
                'total': total,
                'total_online': total_online,
                'total_cash': total_cash,
                'orders': int(r.orders or 0) if r else 0,
                'cancelled_orders': int(getattr(r, 'cancelled_orders', 0) or 0) if r else 0,
                'complaints_received': int(r.complaints_received or 0) if r else 0,
                'complaints_contested': int(r.complaints_contested or 0) if r else 0,
                'appeals_accepted': int(r.appeals_accepted or 0) if r else 0,
                'refund_value': float(r.refund_value or 0) if r else 0.0,
                'refunds_cancelled_value': float(r.refunds_cancelled_value or 0) if r else 0.0,
                'opening_pct': opening_pct_val,
                'opening_potential_sales_est': opening_potential_est,
                'opening_lost_sales_est': opening_lost_est,
                'rating_value': float(r.rating_value) if (r and r.rating_value is not None) else None,
                'rating_unit': str(r.rating_unit or '') if r else '',
                'rating_delta': rating_delta,
            }
        )

    opening_weighted_pct = (opening_actual_considered / opening_potential_estimated * 100.0) if opening_potential_estimated else None
    opening_calc_coverage_pct = (opening_actual_considered / delivery_total * 100.0) if delivery_total else None

    tags: list[dict[str, str]] = [
        {"key": "REV_ACTUAL", "label": "Revenues actual", "value_formatted": _it_eur(rev_actual, 0)},
        {"key": "REV_PREV_WEEK", "label": "Revenues settimana precedente (actual)", "value_formatted": _it_eur(rev_prev_actual, 0)},
        {"key": "REV_MTD", "label": "Revenues progressivo mese (actual)", "value_formatted": _it_eur(mtd_actual, 0)},
        {"key": "REV_PREV_WEEK_BUDGET", "label": "Revenues settimana precedente (budget)", "value_formatted": _it_eur(rev_prev_budget, 0)},
        {"key": "REV_PREV_WEEK_LASTYEAR", "label": "Revenues settimana precedente (last year)", "value_formatted": _it_eur(rev_prev_ly, 0)},
        {"key": "REV_PREV_DELTA_BUDGET_EUR", "label": "Scostamento settimana precedente vs budget (EUR)", "value_formatted": _it_eur(prev_delta_budget_val, 0)},
        {"key": "REV_PREV_DELTA_BUDGET_PCT", "label": "Scostamento settimana precedente vs budget (%)", "value_formatted": _it_pct(prev_delta_budget_pct, 1) if prev_delta_budget_pct is not None else "—"},
        {"key": "REV_PREV_DELTA_LASTYEAR_EUR", "label": "Scostamento settimana precedente vs last year (EUR)", "value_formatted": _it_eur(prev_delta_ly_val, 0)},
        {"key": "REV_PREV_DELTA_LASTYEAR_PCT", "label": "Scostamento settimana precedente vs last year (%)", "value_formatted": _it_pct(prev_delta_ly_pct, 1) if prev_delta_ly_pct is not None else "—"},
        {"key": "REV_MTD_BUDGET", "label": "Revenues progressivo mese (budget)", "value_formatted": _it_eur(mtd_budget, 0)},
        {"key": "REV_MTD_LASTYEAR", "label": "Revenues progressivo mese (last year)", "value_formatted": _it_eur(mtd_ly, 0)},
        {"key": "RECEIPTS_MTD", "label": "Scontrini progressivo mese (actual)", "value_formatted": _it_num(mtd_receipts, 0)},
        {"key": "REV_MTD_DELTA_BUDGET_EUR", "label": "Scostamento progressivo mese vs budget (EUR)", "value_formatted": _it_eur(mtd_delta_budget_val, 0)},
        {"key": "REV_MTD_DELTA_BUDGET_PCT", "label": "Scostamento progressivo mese vs budget (%)", "value_formatted": _it_pct(mtd_delta_budget_pct, 1) if mtd_delta_budget_pct is not None else "—"},
        {"key": "REV_MTD_DELTA_LASTYEAR_EUR", "label": "Scostamento progressivo mese vs last year (EUR)", "value_formatted": _it_eur(mtd_delta_ly_val, 0)},
        {"key": "REV_MTD_DELTA_LASTYEAR_PCT", "label": "Scostamento progressivo mese vs last year (%)", "value_formatted": _it_pct(mtd_delta_ly_pct, 1) if mtd_delta_ly_pct is not None else "—"},
        {"key": "REV_BUDGET", "label": "Revenues budget", "value_formatted": _it_eur(rev_budget, 0)},
        {"key": "REV_LASTYEAR", "label": "Revenues last year", "value_formatted": _it_eur(rev_ly, 0)},
        {"key": "REV_DELTA_BUDGET_EUR", "label": "Scostamento vs budget (EUR)", "value_formatted": _it_eur(delta_budget_val, 0)},
        {"key": "REV_DELTA_BUDGET_PCT", "label": "Scostamento vs budget (%)", "value_formatted": _it_pct(delta_budget_pct, 1) if delta_budget_pct is not None else "—"},
        {"key": "DELIVERY_TOTAL", "label": "Totale delivery (da Distinte)", "value_formatted": _it_eur(delivery_total, 0)},
        {"key": "DELIVERY_TOTAL_ONLINE", "label": "Totale delivery online (da Distinte)", "value_formatted": _it_eur(delivery_online, 0)},
        {"key": "DELIVERY_TOTAL_CASH", "label": "Totale delivery contanti (da Distinte)", "value_formatted": _it_eur(delivery_cash, 0)},
        {"key": "DELIVERY_INC_PCT", "label": "Incidenza delivery su revenues", "value_formatted": _it_pct(delivery_incidence_pct, 1)},
        {"key": "DELIVERY_ORDERS", "label": "Numero ordini delivery", "value_formatted": _it_num(orders_total, 0)},
        {"key": "DELIVERY_CANCELLED_ORDERS", "label": "Ordini delivery cancellati", "value_formatted": _it_num(cancelled_orders_total, 0)},
        {"key": "DELIVERY_OPENING_PCT_W", "label": "% apertura (media pesata su delivery coperto)", "value_formatted": _it_pct(opening_weighted_pct, 2) if opening_weighted_pct is not None else "—"},
        {"key": "DELIVERY_OPENING_LOST_SALES_EST", "label": "Vendite delivery perse stimate (da % apertura)", "value_formatted": _it_eur(opening_lost_sales_estimated, 0)},
        {"key": "DELIVERY_AVG_RECEIPT", "label": "Scontrino medio delivery", "value_formatted": _it_eur(avg_delivery_receipt, 2)},
        {"key": "DELIVERY_REFUNDS_NET", "label": "Valore rimborsi netto contestazioni accettate", "value_formatted": _it_eur(refunds_net, 0)},
        {"key": "DELIVERY_REFUNDS_INC_PCT", "label": "Incidenza rimborsi su delivery", "value_formatted": _it_pct(refunds_incidence_pct, 2)},
        {"key": "DELIVERY_COMPLAINT_RATE_PCT", "label": "% rimborsi su ordini", "value_formatted": _it_pct(complaints_rate_pct, 2)},
        {"key": "RECEIPTS", "label": "Scontrini totali store", "value_formatted": _it_num(receipts_cur, 0)},
        {"key": "DELIVERY_ORDERS_INC_RECEIPTS_PCT", "label": "Ordini delivery su scontrini", "value_formatted": _it_pct(orders_incidence_receipts_pct, 1)},
        {"key": "HOURS", "label": "Ore totali staff", "value_formatted": _it_num(hours_cur, 1)},
        {"key": "LAB_COST_EUR", "label": "Costo del lavoro (EUR)", "value_formatted": _it_eur(labor_cost_cur, 0)},
        {"key": "LAB_COST_PCT", "label": "Costo del lavoro su revenues", "value_formatted": _it_pct(labor_pct_cur, 2)},
        {"key": "LAB_COST_PREV_EUR", "label": "Costo del lavoro settimana precedente (EUR)", "value_formatted": _it_eur(labor_cost_prev, 0)},
        {"key": "LAB_COST_PREV_PCT", "label": "Costo del lavoro settimana precedente su revenues", "value_formatted": _it_pct(labor_pct_prev, 2)},
        {"key": "LAB_COST_MTD_EUR", "label": "Costo del lavoro progressivo mese (EUR)", "value_formatted": _it_eur(mtd_labor_cost, 0)},
        {"key": "LAB_COST_MTD_PCT", "label": "Costo del lavoro progressivo mese su revenues", "value_formatted": _it_pct(mtd_labor_pct, 2)},
        {"key": "LAB_COST_MTD_LY_EUR", "label": "Costo del lavoro progressivo mese last year (EUR)", "value_formatted": _it_eur(mtd_labor_cost_ly, 0)},
        {"key": "LAB_COST_MTD_LY_PCT", "label": "Costo del lavoro progressivo mese last year su revenues", "value_formatted": _it_pct(mtd_labor_pct_ly, 2)},
        {"key": "PROD_EURH", "label": "Produttività (€/h)", "value_formatted": _it_eur(prod_cur, 2) + "/h"},
    ]

    # dynamic provider tags (da Distinte)
    try:
        for prov in sorted((delivery_providers or {}).keys()):
            safe = re.sub(r'[^A-Z0-9]+', '_', str(prov or '').upper()).strip('_')
            if not safe:
                continue
            fin = (delivery_providers or {}).get(prov) or {}
            tags.append({"key": f"DELIVERY_{safe}_TOTAL", "label": f"Delivery {prov} totale (da Distinte)", "value_formatted": _it_eur(fin.get('total') or 0.0, 0)})
            tags.append({"key": f"DELIVERY_{safe}_ONLINE", "label": f"Delivery {prov} online (da Distinte)", "value_formatted": _it_eur(fin.get('online') or 0.0, 0)})
            tags.append({"key": f"DELIVERY_{safe}_CASH", "label": f"Delivery {prov} contanti (da Distinte)", "value_formatted": _it_eur(fin.get('cash') or 0.0, 0)})
    except Exception:
        pass

    return {
        "week_start": ws.isoformat(),
        "week_end": we.isoformat(),
        "revenues": {
            "actual": rev_actual,
            "prev_week_actual": rev_prev_actual,
            "prev_week_budget": rev_prev_budget,
            "prev_week_last_year": rev_prev_ly,
            "prev_week_delta_vs_budget_value": prev_delta_budget_val,
            "prev_week_delta_vs_budget_pct": prev_delta_budget_pct,
            "prev_week_delta_vs_last_year_value": prev_delta_ly_val,
            "prev_week_delta_vs_last_year_pct": prev_delta_ly_pct,
            "mtd_actual": mtd_actual,
            "mtd_start": month_start.isoformat(),
            "mtd_end": mtd_end.isoformat(),
            "mtd_budget": mtd_budget,
            "mtd_last_year": mtd_ly,
            "mtd_delta_vs_budget_value": mtd_delta_budget_val,
            "mtd_delta_vs_budget_pct": mtd_delta_budget_pct,
            "mtd_delta_vs_last_year_value": mtd_delta_ly_val,
            "mtd_delta_vs_last_year_pct": mtd_delta_ly_pct,
            "budget": rev_budget,
            "last_year": rev_ly,
            "delta_vs_budget_value": delta_budget_val,
            "delta_vs_budget_pct": delta_budget_pct,
            "delta_vs_last_year_value": delta_ly_val,
            "delta_vs_last_year_pct": delta_ly_pct,
            "projection": proj,
        },
        "receipts": {
            "current": receipts_cur,
            "prev_week": receipts_prev,
            "mtd_current": mtd_receipts,
            "mtd_last_year": mtd_receipts_ly,
        },
        "delivery": {
            "total_delivery": delivery_total,
            "total_online": delivery_online,
            "total_cash": delivery_cash,
            "total_orders": orders_total,
            "cancelled_orders": cancelled_orders_total,
            "complaints_received": complaints_received,
            "complaints_contested": complaints_contested,
            "appeals_accepted": appeals_accepted,
            "refunds_value": refund_value,
            "refunds_cancelled_value": refunds_cancelled,
            "refunds_net": refunds_net,
            "delivery_incidence_pct": delivery_incidence_pct,
            "avg_delivery_receipt": avg_delivery_receipt,
            "refunds_incidence_pct": refunds_incidence_pct,
            "complaints_rate_pct": complaints_rate_pct,
            "complaints_rate_net_pct": complaints_rate_net_pct,
            "orders_incidence_receipts_pct": orders_incidence_receipts_pct,
            "opening_weighted_pct": opening_weighted_pct,
            "opening_calc_coverage_pct": opening_calc_coverage_pct,
            "opening_actual_considered": opening_actual_considered,
            "opening_potential_sales_est": opening_potential_estimated,
            "opening_lost_sales_est": opening_lost_sales_estimated,
            "platforms": platforms_out,
            "prev_week": {
                "total_delivery": delivery_total_prev,
                "total_online": delivery_online_prev,
                "total_cash": delivery_cash_prev,
                "total_orders": orders_prev,
                "cancelled_orders": cancelled_orders_prev,
                "refunds_net": refunds_net_prev,
                "delivery_incidence_pct": delivery_incidence_prev_pct,
                "refunds_incidence_pct": refunds_incidence_prev_pct,
                "complaints_rate_pct": complaints_rate_prev_pct,
                "orders_incidence_receipts_pct": orders_incidence_prev_pct,
            },
        },
        "labor_cost": {
            "cost_eur": labor_cost_cur,
            "pct": labor_pct_cur,
            "prev_week_cost_eur": labor_cost_prev,
            "prev_week_pct": labor_pct_prev,
            "prev2_week_cost_eur": labor_cost_prev2,
            "prev2_week_pct": labor_pct_prev2,
            "last_year_cost_eur": labor_cost_ly,
            "last_year_pct": labor_pct_ly,
            "prev_week_last_year_cost_eur": labor_cost_prev_ly,
            "prev_week_last_year_pct": labor_pct_prev_ly,
            "mtd_cost_eur": mtd_labor_cost,
            "mtd_pct": mtd_labor_pct,
            "mtd_start": month_start.isoformat(),
            "mtd_end": mtd_end.isoformat(),
            "mtd_last_year_cost_eur": mtd_labor_cost_ly,
            "mtd_last_year_pct": mtd_labor_pct_ly,
            "mtd_prev_week_end": mtd_prev_week_end.isoformat(),
            "mtd_prev_cost_eur": mtd_prev_cost,
            "mtd_prev_pct": mtd_prev_pct,
            "mtd_prev_hours": mtd_prev_hours,
            "mtd_delta_vs_prev_cost_eur": mtd_delta_vs_prev_val,
            "mtd_delta_vs_prev_pp": mtd_delta_vs_prev_pp,
            "delta_cost_eur": labor_delta_val,
            "delta_pct": labor_delta_pct,
            "delta_pp": labor_delta_pp,
            "delta_vs_last_year_cost_eur": labor_delta_vs_ly_val,
            "delta_vs_last_year_pct": labor_delta_vs_ly_pct,
            "delta_vs_last_year_pp": labor_delta_vs_ly_pp,
            "prev_week_delta_vs_prev_cost_eur": labor_cost_prev - labor_cost_prev2,
            "prev_week_delta_vs_prev_pp": labor_pct_prev - labor_pct_prev2,
            "prev_week_delta_vs_last_year_cost_eur": labor_prev_delta_vs_ly_val,
            "prev_week_delta_vs_last_year_pct": labor_prev_delta_vs_ly_pct,
            "prev_week_delta_vs_last_year_pp": labor_prev_delta_vs_ly_pp,
            "mtd_delta_vs_last_year_cost_eur": mtd_labor_delta_vs_ly_val,
            "mtd_delta_vs_last_year_pct": mtd_labor_delta_vs_ly_pct,
            "mtd_delta_vs_last_year_pp": mtd_labor_delta_vs_ly_pp,
            "hours": hours_cur,
            "prev_week_hours": hours_prev,
            "prev2_week_hours": float(prev2_tot.get("ore_totali") or 0.0),
            "last_year_hours": hours_ly,
            "prev_week_last_year_hours": hours_prev_ly,
            "mtd_hours": mtd_hours,
            "mtd_last_year_hours": mtd_hours_ly,
        },
        "productivity": {
            "current": prod_cur,
            "prev_week": prod_prev,
            "delta_value": prod_delta,
            "delta_pct": prod_delta_pct,
            "hours": hours_cur,
        },
        "tags": tags,
    }
