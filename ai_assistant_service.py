from __future__ import annotations

from app_logging import log_swallowed
import os
import re
import json
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Tuple

import requests

from cruscotto_repository import (
    _hours_from_turni_rows,
    _labor_cost_from_turni_rows,
    load_cmo_rates,
    fetch_budget_day,
    fetch_dati_database_day,
    get_monthly_analysis,
    get_weekly_analysis,
)
from controlli_repository import get_pnl
from primanota_repository import load_primanota_month_agg, sum_delivery_voce_range
from spese_repository import search_spese_range_multi, sum_spese_month_by_day
from versamenti_repository import list_versamenti_periods_overlapping, search_versamenti_range_multi
from delivery_repository import export_weekly_rows
from orari_repository import list_turni_week


OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-5-mini").strip() or "gpt-5-mini"
OPENAI_TIMEOUT_SECONDS = int((os.getenv("OPENAI_TIMEOUT_SECONDS") or "45").strip())


_CHIUSURA_VOICE_TO_KEY = {
    "GIRO AFFARI": "giro_affari",
    "VENDITE LORDE": "vendite_lorde",
    "ANNULLATI": "annullati",
    "SCONTRINI": "scontrini",
    "POS": "pos",
    "CONTANTI": "contanti",
    "TICKET": "ticket_imported",
    "FATTURE": "fatture",
    "NUMERO FATTURE": "numero_fatture",
    "SPESE": "spese_imported",
    "OMAGGI": "omaggi",
    "VENDITE 4%": "vendite_4",
    "VENDITE 22%": "vendite_22",
    "DIFFERENZA CASSA": "differenza_cassa_imported",
}


def openai_is_configured() -> bool:
    return bool(OPENAI_API_KEY and OPENAI_MODEL)


def _normalize_text(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _money(value: Any) -> float:
    try:
        return round(float(value or 0.0), 2)
    except Exception:
        return 0.0


def _iter_months(start: date, end: date):
    cur = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cur <= last:
        yield cur.year, cur.month
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)


def _days_in_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _parse_explicit_dates(question: str) -> Tuple[date | None, date | None]:
    matches = re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", question or "")
    parsed: list[date] = []
    for dd, mm, yyyy in matches[:2]:
        try:
            parsed.append(date(int(yyyy), int(mm), int(dd)))
        except Exception:
            continue
    if len(parsed) >= 2:
        a, b = parsed[0], parsed[1]
        return (a, b) if a <= b else (b, a)
    if len(parsed) == 1:
        return parsed[0], parsed[0]
    return None, None


def parse_period(question: str, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    qn = _normalize_text(question)

    explicit_start, explicit_end = _parse_explicit_dates(question)
    if explicit_start and explicit_end:
        return {
            "start": explicit_start,
            "end": explicit_end,
            "label": f"{explicit_start.strftime('%d/%m/%Y')} - {explicit_end.strftime('%d/%m/%Y')}",
            "source": "explicit_dates",
        }

    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)

    if "settimana scorsa" in qn or "scorsa settimana" in qn:
        return {"start": last_monday, "end": last_sunday, "label": "settimana scorsa", "source": "keyword"}
    if "questa settimana" in qn:
        return {"start": this_monday, "end": this_monday + timedelta(days=6), "label": "questa settimana", "source": "keyword"}
    if "mese scorso" in qn or "scorso mese" in qn:
        first_this = date(today.year, today.month, 1)
        end_prev = first_this - timedelta(days=1)
        start_prev = date(end_prev.year, end_prev.month, 1)
        return {"start": start_prev, "end": end_prev, "label": "mese scorso", "source": "keyword"}
    if "questo mese" in qn:
        start = date(today.year, today.month, 1)
        next_month = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
        return {"start": start, "end": next_month - timedelta(days=1), "label": "questo mese", "source": "keyword"}
    if re.search(r"\boggi\b", qn):
        return {"start": today, "end": today, "label": "oggi", "source": "keyword"}
    if re.search(r"\bieri\b", qn):
        y = today - timedelta(days=1)
        return {"start": y, "end": y, "label": "ieri", "source": "keyword"}
    if re.search(r"\bdomani\b", qn):
        d = today + timedelta(days=1)
        return {"start": d, "end": d, "label": "domani", "source": "keyword"}
    if re.search(r"\bdopodomani\b", qn):
        d = today + timedelta(days=2)
        return {"start": d, "end": d, "label": "dopodomani", "source": "keyword"}

    # fallback prudente: settimana scorsa, che è il caso d'uso principale citato
    return {"start": last_monday, "end": last_sunday, "label": "settimana scorsa", "source": "default"}


def normalize_available_stores(raw_stores: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in raw_stores or []:
        code = str((row or {}).get("code") or (row or {}).get("store_code") or "").strip()
        name = str((row or {}).get("name") or (row or {}).get("store_name") or "").strip()
        if not code:
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": name or code})
    return out


def _pct_delta(current: Any, reference: Any) -> float | None:
    cur = _money(current)
    ref = _money(reference)
    if not ref:
        return None
    return round(((cur - ref) / ref) * 100.0, 2)


def classify_question(question: str) -> dict[str, Any]:
    qn = _normalize_text(question)
    admin_terms = (
        'spese', 'versamenti', 'versamento', 'amministrativ', 'ricevut', 'competenza',
        'banca', 'bancomat', 'distinte', 'distinta', 'cassa', 'document', 'bonifico'
    )
    business_terms = (
        'vendit', 'andat', 'budget', 'anno precedente', 'ly', 'giro affari', 'fatturat',
        'scontrin', 'delivery', 'deliveroo', 'glovo', 'just eat', 'ticket medio', 'produttiv', 'trend'
    )
    pnl_terms = (
        'pnl', 'conto economico', 'ebitda', 'store ebitda', 'margine', 'margine di contribuzione',
        'cogs', 'labour', 'labour cost', 'costo lavoro', 'costo del lavoro', 'costo del personale',
        'personnel cost', 'g&a', 'costi controllabili', 'delivery fees', 'commissioni delivery'
    )
    admin_score = sum(1 for t in admin_terms if t in qn)
    business_score = sum(1 for t in business_terms if t in qn)
    pnl_score = sum(1 for t in pnl_terms if t in qn)

    if admin_score and (business_score or pnl_score):
        mode = 'mixed'
    elif admin_score > business_score and admin_score > pnl_score:
        mode = 'administrative'
    else:
        mode = 'business'

    return {
        'mode': mode,
        'wants_budget': ('budget' in qn),
        'wants_last_year': ('anno precedente' in qn) or re.search(r'\bly\b', qn) is not None,
        'wants_delivery': any(t in qn for t in ('delivery', 'deliveroo', 'glovo', 'just eat', 'refund', 'rimbor', 'contestaz', 'provider')),
        'wants_pnl': pnl_score > 0 or 'controlli' in qn,
        'wants_admin': mode in ('administrative', 'mixed'),
        'wants_business': mode in ('business', 'mixed'),
        'brief_style': True,
    }




def infer_page_scope(page_context: str | None) -> str:
    raw = _normalize_text(page_context or '')
    if not raw:
        return 'general'
    if 'rendiconto versamenti' in raw or 'rendiconto.versamenti' in raw or '/rendiconto/versamenti' in raw:
        return 'versamenti'
    if 'rendiconto spese' in raw or 'rendiconto.spese' in raw or '/rendiconto/spese' in raw:
        return 'spese'
    if raw == 'orari' or raw.startswith('orari ') or ' orari ' in f' {raw} ' or 'gestione orari' in raw:
        return 'orari'
    if 'gestione delivery' in raw or 'delivery' in raw:
        return 'delivery'
    if 'controlli' in raw or 'pnl' in raw:
        return 'controlli'
    if 'cruscotto' in raw:
        return 'cruscotto'
    if 'dati rendiconto' in raw or 'riepilogo' in raw:
        return 'rendiconto'
    return 'general'


def _question_has_explicit_period(question: str) -> bool:
    qn = _normalize_text(question)
    if _parse_explicit_dates(question) != (None, None):
        return True
    keys = ('settimana scorsa', 'scorsa settimana', 'questa settimana', 'mese scorso', 'scorso mese', 'questo mese', 'oggi', 'ieri', 'domani', 'dopodomani')
    return any(k in qn for k in keys)


def _lookup_window(question: str, period: dict[str, Any]) -> tuple[date, date]:
    if _question_has_explicit_period(question):
        return period['start'], period['end']
    today = date.today()
    return date(today.year - 1, 1, 1), today


def _build_administrative_lookup(question: str, *, stores: list[dict[str, str]], period: dict[str, Any], page_scope: str, question_profile: dict[str, Any]) -> dict[str, Any]:
    codes = [str((s or {}).get('code') or '').strip() for s in (stores or []) if str((s or {}).get('code') or '').strip()]
    if not codes:
        return {'scope': page_scope, 'window': None, 'versamenti_matches': [], 'spese_matches': [], 'notes': []}

    start, end = _lookup_window(question, period)
    qn = _normalize_text(question)
    tessera_match = re.search(r"\b\d{12,20}\b", question or "")
    tessera = str(tessera_match.group(0)) if tessera_match else ''
    page_is_admin = page_scope in ('versamenti', 'spese', 'rendiconto')
    wants_admin = bool(question_profile.get('wants_admin')) or page_is_admin or bool(tessera)
    notes: list[str] = []
    out = {
        'scope': page_scope,
        'window': {'start_iso': start.isoformat(), 'end_iso': end.isoformat()},
        'versamenti_matches': [],
        'spese_matches': [],
        'notes': notes,
    }
    if not wants_admin:
        return out

    if tessera or page_scope == 'versamenti' or 'versament' in qn:
        try:
            vres = search_versamenti_range_multi(codes, start=start, end=end) or {}
            rows = vres.get('rows') or []
            if tessera:
                rows = [r for r in rows if str(r.get('tessera') or '').strip() == tessera]
                notes.append(f'Ricerca versamenti per tessera {tessera}.')
            elif 'riferimento' in qn or 'banca' in qn or 'nome' in qn:
                rows = [r for r in rows if qn in _normalize_text(' '.join([
                    str(r.get('nome') or ''),
                    str(r.get('riferimento') or ''),
                    str(r.get('tipo') or ''),
                ]))]
            out['versamenti_matches'] = rows[:100]
        except Exception as ex:
            notes.append(f'Lookup versamenti non disponibile: {ex}')

    if page_scope == 'spese' or 'spes' in qn or 'fornitor' in qn or 'document' in qn:
        try:
            sres = search_spese_range_multi(codes, start=start, end=end) or {}
            rows = sres.get('rows') or []
            if qn:
                tokens = [t for t in re.findall(r'[a-zA-Z0-9]{3,}', qn) if t.lower() not in {'spese','spesa','documento','fornitore'}]
                if tokens:
                    rows = [r for r in rows if all(tok.lower() in _normalize_text(' '.join([
                        str(r.get('tipo') or ''),
                        str(r.get('fornitore') or ''),
                        str(r.get('documento') or ''),
                    ])) for tok in tokens)]
            out['spese_matches'] = rows[:100]
        except Exception as ex:
            notes.append(f'Lookup spese non disponibile: {ex}')

    return out



def _week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _week_starts_in_range(start: date, end: date) -> list[date]:
    cur = _week_monday(start)
    out: list[date] = []
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=7)
    return out


def _build_delivery_lookup(question: str, *, stores: list[dict[str, str]], period: dict[str, Any], page_scope: str, question_profile: dict[str, Any]) -> dict[str, Any]:
    qn = _normalize_text(question)
    if not (question_profile.get('wants_delivery') or page_scope == 'delivery'):
        return {'rows': [], 'totals': {}, 'notes': []}
    codes = [str((s or {}).get('code') or '').strip() for s in (stores or []) if str((s or {}).get('code') or '').strip()]
    if not codes:
        return {'rows': [], 'totals': {}, 'notes': []}
    provider = 'ALL'
    if 'deliveroo' in qn:
        provider = 'DELIVEROO'
    elif 'glovo' in qn:
        provider = 'GLOVO'
    elif 'just eat' in qn or 'justeat' in qn:
        provider = 'JUST EAT'
    elif 'uber eats' in qn or 'ubereats' in qn:
        provider = 'UBER EATS'
    notes: list[str] = []
    try:
        rows = export_weekly_rows(
            store_codes=codes,
            week_start_from=_week_monday(period['start']),
            week_start_to=_week_monday(period['end']),
            platform=provider,
        ) or []
    except Exception as ex:
        return {'rows': [], 'totals': {}, 'notes': [f'Lookup delivery non disponibile: {ex}']}

    out_rows: list[dict[str, Any]] = []
    totals = {
        'payment_total': 0.0,
        'payment_online': 0.0,
        'payment_cash': 0.0,
        'orders': 0,
        'cancelled_orders': 0,
        'complaints_received': 0,
        'complaints_contested': 0,
        'appeals_accepted': 0,
        'refund_value': 0.0,
        'refunds_cancelled_value': 0.0,
    }
    for r in rows:
        try:
            ws = datetime.fromisoformat(str(r.get('week_start'))[:10]).date()
        except Exception:
            try:
                ws = date.fromisoformat(str(r.get('week_start'))[:10])
            except Exception:
                continue
        we = ws + timedelta(days=6)
        if we < period['start'] or ws > period['end']:
            continue
        out_rows.append(r)
        for k in ('payment_total','payment_online','payment_cash','refund_value','refunds_cancelled_value'):
            totals[k] = _money(totals.get(k,0.0) + _money(r.get(k)))
        for k in ('orders','cancelled_orders','complaints_received','complaints_contested','appeals_accepted'):
            totals[k] = int(totals.get(k,0) or 0) + int(r.get(k,0) or 0)
    if provider != 'ALL':
        notes.append(f'Filtro provider applicato: {provider}.')
    return {'rows': out_rows[:120], 'totals': totals, 'notes': notes}


def _build_controlli_lookup(question: str, *, stores: list[dict[str, str]], period: dict[str, Any], page_scope: str, question_profile: dict[str, Any]) -> dict[str, Any]:
    if not stores:
        return {'rows': [], 'notes': []}
    if not (page_scope == 'controlli' or question_profile.get('wants_pnl')):
        return {'rows': [], 'notes': []}
    if period['start'].year != period['end'].year:
        return {'rows': [], 'notes': ['Lookup controlli disponibile solo su periodi nello stesso anno.']}

    voice_terms = {
        'REVENUES': ('revenues', 'ricavi', 'vendite', 'fatturato'),
        'COGS': ('cogs', 'food cost', 'costo del venduto'),
        'MARGINE DI CONTRIBUZIONE': ('margine', 'margine di contribuzione'),
        'LABOUR COST': ('labour', 'labour cost', 'costo lavoro', 'costo del lavoro', 'costo del personale', 'personnel cost'),
        'DELIVERY FEES': ('delivery fees', 'commissioni delivery', 'commissioni delivery'),
        'G&A STORE': ('g&a', 'g&a store', 'costi generali'),
        'STORE EBITDA': ('store ebitda',),
        'EBITDA': ('ebitda',),
    }
    qn = _normalize_text(question)
    requested_voices = {
        voice
        for voice, aliases in voice_terms.items()
        if any(_normalize_text(alias) in qn for alias in aliases)
    }
    default_voices = {'REVENUES', 'COGS', 'MARGINE DI CONTRIBUZIONE', 'LABOUR COST', 'DELIVERY FEES', 'STORE EBITDA', 'EBITDA'}

    try:
        payload = get_pnl(
            ",".join(str((s or {}).get('code') or '').strip() for s in stores if str((s or {}).get('code') or '').strip()),
            period['start'].year,
            period['start'].month,
            period['end'].month,
        ) or {}
    except Exception as ex:
        return {'rows': [], 'notes': [f'Lookup controlli non disponibile: {ex}']}

    rows = payload.get('rows') or []
    selected_voices = requested_voices or default_voices
    rows = [r for r in rows if str(r.get('voice') or '').strip().upper() in selected_voices]
    voice_map = {
        str((r or {}).get('voice') or '').strip().upper(): r
        for r in rows
        if str((r or {}).get('voice') or '').strip()
    }
    ordered_rows = [voice_map[v] for v in selected_voices if v in voice_map]
    cost_voices = {'COGS', 'LABOUR COST', 'DELIVERY FEES', 'G&A STORE', 'TOTALE COSTI CONTROLLABILI'}
    margin_voices = {'MARGINE DI CONTRIBUZIONE', 'STORE EBITDA', 'EBITDA'}
    summarized_rows: list[dict[str, Any]] = []
    for r in ordered_rows[:24]:
        voice = str(r.get('voice') or '').strip()
        voice_upper = voice.upper()
        if voice_upper in cost_voices:
            voice_type = 'cost'
            better_when = 'lower'
        elif voice_upper in margin_voices or voice_upper == 'REVENUES':
            voice_type = 'performance'
            better_when = 'higher'
        else:
            voice_type = 'other'
            better_when = 'context'
        summarized_rows.append({
            'voice': voice,
            'voice_type': voice_type,
            'better_when': better_when,
            'actual_eur': _money(r.get('actual')),
            'actual_pct': round(_money(r.get('actual_pct')) * 100.0, 2) if r.get('actual_pct') is not None else None,
            'budget_eur': _money(r.get('budget')),
            'budget_pct': round(_money(r.get('budget_pct')) * 100.0, 2) if r.get('budget_pct') is not None else None,
            'diff_eur': _money(r.get('diff')),
            'diff_pct': round(_money(r.get('diff_pct')) * 100.0, 2) if r.get('diff_pct') is not None else None,
            'last_year_eur': _money(r.get('last_year')),
            'last_year_pct': round(_money(r.get('last_year_pct')) * 100.0, 2) if r.get('last_year_pct') is not None else None,
            'diff_last_year_eur': _money(r.get('diff_last_year')),
            'diff_last_year_pct': round(_money(r.get('diff_last_year_pct')) * 100.0, 2) if r.get('diff_last_year_pct') is not None else None,
            'is_total': bool(r.get('is_total')),
        })
    return {
        'rows': summarized_rows,
        'requested_voices': sorted(selected_voices),
        'notes': [f'Controlli/P&L aggregato da {period["start"].strftime("%m/%Y")} a {period["end"].strftime("%m/%Y")}.' ],
    }


def _build_orari_lookup(question: str, *, stores: list[dict[str, str]], period: dict[str, Any], page_scope: str, question_profile: dict[str, Any]) -> dict[str, Any]:
    qn = _normalize_text(question)
    triggers = ('turn', 'orari', 'causale', 'riposo', 'ferie', 'permesso', 'training', 'prestito', 'scheduling', 'ore')
    if not (page_scope == 'orari' or any(t in qn for t in triggers)):
        return {'rows': [], 'totals': {}, 'notes': []}
    rows_out: list[dict[str, Any]] = []
    notes: list[str] = []
    totals = {'hours': 0.0, 'rows': 0}
    for store in stores:
        for ws in _week_starts_in_range(period['start'], period['end']):
            try:
                wrows = list_turni_week(store_code=str(store['code']), start_day=ws, end_day=ws + timedelta(days=6), nominativi=None) or []
            except Exception as ex:
                notes.append(f'Lookup orari non disponibile per store {store["code"]}: {ex}')
                continue
            for r in wrows:
                try:
                    d = date.fromisoformat(str(r.get('data') or '')[:10])
                except Exception:
                    continue
                if d < period['start'] or d > period['end']:
                    continue
                blob = _normalize_text(' '.join([
                    str(r.get('nominativo') or ''),
                    str(r.get('causale') or ''),
                    str(r.get('causale2') or ''),
                    str(r.get('inquadramento') or ''),
                ]))
                tokens = [
                    t for t in re.findall(r"[A-Za-z0-9@._-]{3,}", question or '')
                    if _normalize_text(t) not in {
                        'come', 'andata', 'settimana', 'scorsa', 'mese', 'questo', 'oggi', 'ieri', 'domani', 'dopodomani',
                        'quante', 'quanti', 'della', 'dello', 'degli', 'delle', 'nello', 'nelle',
                        'store', 'training'
                    }
                ]
                if page_scope != 'orari' and tokens:
                    filtered = [tok for tok in tokens if not tok.startswith('@')]
                    if filtered and not any(_normalize_text(tok) in blob for tok in filtered):
                        continue
                mins = 0
                for s_key, e_key in (('inizio_1','fine_1'),('inizio_2','fine_2')):
                    s = str(r.get(s_key) or '').strip()
                    e = str(r.get(e_key) or '').strip()
                    if len(s) >= 5 and len(e) >= 5:
                        try:
                            sh, sm = int(s[:2]), int(s[3:5])
                            eh, em = int(e[:2]), int(e[3:5])
                            start_m = sh*60+sm
                            end_m = eh*60+em
                            if end_m < start_m:
                                end_m += 24*60
                            mins += max(0, end_m-start_m)
                        except Exception:
                            log_swallowed('ai_assistant_service:524')
                totals['hours'] = round(totals.get('hours',0.0) + (mins/60.0), 2)
                totals['rows'] = int(totals.get('rows',0) or 0) + 1
                row_copy = dict(r)
                row_copy['site'] = str(store['code'])
                row_copy['hours'] = round(mins/60.0, 2)
                rows_out.append(row_copy)
    return {'rows': rows_out[:150], 'totals': totals, 'notes': notes}

def resolve_store_scope(
    question: str,
    *,
    available_stores: list[dict[str, str]],
    role: str = "user",
    current_store_code: str | None = None,
) -> dict[str, Any]:
    mentions = re.findall(r"@([A-Za-z0-9][A-Za-z0-9._\\-]*)", question or "")
    stores = normalize_available_stores(available_stores)
    code_map = {s["code"]: s for s in stores}
    name_map = {_normalize_text(s["name"]): s for s in stores}

    selected: list[dict[str, str]] = []
    unresolved: list[str] = []

    if mentions:
        picked_codes: set[str] = set()
        for raw in mentions:
            token = str(raw or "").strip()
            if not token:
                continue
            norm = _normalize_text(token)
            match = code_map.get(token)
            if not match:
                match = name_map.get(norm)
            if not match:
                candidates = [
                    s for s in stores
                    if norm and (norm == _normalize_text(s["code"]) or norm in _normalize_text(s["name"]) or _normalize_text(s["code"]).startswith(norm))
                ]
                if len(candidates) == 1:
                    match = candidates[0]
            if match:
                if match["code"] not in picked_codes:
                    selected.append(match)
                    picked_codes.add(match["code"])
            else:
                unresolved.append(token)
    else:
        role_l = str(role or "").strip().lower()
        if role_l == "admin" and current_store_code and current_store_code in code_map:
            selected = [code_map[current_store_code]]
        else:
            selected = list(stores)

    return {
        "selected_stores": selected,
        "mentions": mentions,
        "unresolved_mentions": unresolved,
    }


def _aggregate_primanota_range(store_code: str, start: date, end: date) -> dict[str, Any]:
    metrics = {
        "vendite_lorde": 0.0,
        "annullati": 0.0,
        "pos": 0.0,
        "scontrini": 0.0,
        "distinte": 0.0,
        "ticket_si": 0.0,
        "ticket_no": 0.0,
        "delivery_si": 0.0,
        "delivery_no": 0.0,
        "coupon_si": 0.0,
        "coupon_no": 0.0,
        "spese": 0.0,
        "spese_gross": 0.0,
        "versamenti": 0.0,
        "versamenti_count": 0,
        "versato_days": 0,
        "delivery_breakdown": {},
    }

    for y, m in _iter_months(start, end):
        try:
            rows = load_primanota_month_agg(str(store_code), year=y, month=m) or []
        except Exception:
            rows = []
        for r in rows:
            try:
                d_iso = str(r.get("date") or "").strip()
                d = datetime.strptime(d_iso, "%Y-%m-%d").date()
            except Exception:
                continue
            if d < start or d > end:
                continue
            cat = str(r.get("categoria") or "").strip()
            voce = str(r.get("voce") or "").strip()
            tipo = str(r.get("tipo") or "SI").strip().upper()
            s_val = _money(r.get("sum"))
            if cat == "Dati chiusura":
                key = _CHIUSURA_VOICE_TO_KEY.get(voce.upper())
                if key in metrics:
                    metrics[key] += s_val
            elif cat == "Distinte":
                metrics["distinte"] += s_val
            elif cat == "Ticket":
                metrics["ticket_si" if tipo == "SI" else "ticket_no"] += s_val
            elif cat == "Delivery":
                metrics["delivery_si" if tipo == "SI" else "delivery_no"] += s_val
            elif cat == "Coupon":
                metrics["coupon_si" if tipo == "SI" else "coupon_no"] += s_val

        try:
            sp_by_day = sum_spese_month_by_day(store_code=str(store_code), year=y, month=m) or {}
        except Exception:
            sp_by_day = {}
        for d_iso, vals in sp_by_day.items():
            try:
                d = datetime.strptime(str(d_iso), "%Y-%m-%d").date()
            except Exception:
                continue
            if start <= d <= end:
                metrics["spese"] += _money((vals or {}).get("net"))
                metrics["spese_gross"] += _money((vals or {}).get("total"))

    try:
        vres = list_versamenti_periods_overlapping(
            store_code=str(store_code),
            start_iso=start.isoformat(),
            end_iso=end.isoformat(),
        ) or {}
        rows = vres.get("rows") or []
        metrics["versamenti_count"] = len(rows)
        covered_days: set[str] = set()
        vers_tot = Decimal("0")
        for vr in rows:
            try:
                vers_tot += Decimal(str(vr.get("valore_key") or "0"))
            except Exception:
                log_swallowed('ai_assistant_service:663')
            try:
                dal = datetime.strptime(str(vr.get("dal_iso") or ""), "%Y-%m-%d").date()
                al = datetime.strptime(str(vr.get("al_iso") or ""), "%Y-%m-%d").date()
            except Exception:
                continue
            if al < dal:
                dal, al = al, dal
            rs = max(dal, start)
            re = min(al, end)
            for day in _days_in_range(rs, re):
                covered_days.add(day.isoformat())
        metrics["versamenti"] = _money(vers_tot)
        metrics["versato_days"] = len(covered_days)
    except Exception:
        log_swallowed('ai_assistant_service:678')

    try:
        metrics["delivery_breakdown"] = sum_delivery_voce_range(
            store_code=str(store_code),
            start_iso=start.isoformat(),
            end_iso=end.isoformat(),
        ) or {}
    except Exception:
        metrics["delivery_breakdown"] = {}

    metrics["giro_affari"] = _money(metrics["vendite_lorde"] - metrics["annullati"])
    metrics["ticket_totale"] = _money(metrics["ticket_si"] + metrics["ticket_no"])
    metrics["delivery_totale"] = _money(metrics["delivery_si"] + metrics["delivery_no"])
    metrics["coupon_totale"] = _money(metrics["coupon_si"] + metrics["coupon_no"])
    metrics["diff_cassa"] = _money(
        metrics["distinte"] + metrics["ticket_si"] + metrics["delivery_si"] + metrics["coupon_si"] + metrics["pos"] + metrics["spese"] - metrics["giro_affari"]
    )
    return metrics


def _full_weeks_between(start: date, end: date) -> tuple[date, date] | None:
    if start.weekday() == 0 and end.weekday() == 6:
        return start, end
    return None


def _is_full_calendar_month(start: date, end: date) -> bool:
    if start.day != 1:
        return False
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    return end == (next_month - timedelta(days=1))


def _aggregate_cruscotto_kpi(store_code: str, start: date, end: date, *, actual_gross: Any = 0.0, delivery_gross: Any = 0.0) -> dict[str, Any]:
    today_ref = date.today()
    is_future = start > today_ref
    actual_net = _money(_money(actual_gross) / 1.1)
    delivery_net = _money(_money(delivery_gross) / 1.1)
    budget = 0.0
    ly = 0.0
    hours_total = 0.0
    hours_training = 0.0
    labor_cost = 0.0
    productivity_eur_per_hour = 0.0

    try:
        if start.weekday() == 0 and end.weekday() == 6 and (end - start).days == 6:
            weekly = get_weekly_analysis(store_code=str(store_code), week_start=start, delivery_voci=[]) or {}
            totals = weekly.get("totals") or {}
            budget = _money(totals.get("revenues_budget"))
            ly = _money(totals.get("revenues_ly"))
            hours_total = _money(totals.get("ore_totali"))
            hours_training = _money(totals.get("ore_training"))
            labor_cost = _money(totals.get("labor_cost"))
            productivity_eur_per_hour = _money(totals.get("produttivita"))
        elif _is_full_calendar_month(start, end):
            monthly = get_monthly_analysis(store_code=str(store_code), month_start=start, delivery_voci=[]) or {}
            totals = monthly.get("totals") or {}
            budget = _money(totals.get("revenues_budget"))
            ly = _money(totals.get("revenues_ly"))
            hours_total = _money(totals.get("ore_totali"))
            hours_training = _money(totals.get("ore_training"))
            labor_cost = _money(totals.get("labor_cost"))
            productivity_eur_per_hour = _money(totals.get("produttivita"))
        else:
            for day in _days_in_range(start, end):
                try:
                    budget += _money(fetch_budget_day(store_code=str(store_code), day=day))
                except Exception:
                    log_swallowed('ai_assistant_service:751')
                try:
                    ly_row = fetch_dati_database_day(store_code=str(store_code), day=_align_last_year_same_weekday(day)) or {}
                    ly += _money(_money(ly_row.get("fatturato_lordo")) / 1.1)
                except Exception:
                    log_swallowed('ai_assistant_service:755')
            try:
                turni = list_turni_week(store_code=str(store_code), start_day=start, end_day=end, nominativi=None) or []
                prod_by_day, _stage_by_day, train_by_day = _hours_from_turni_rows(turni)
                cmo_rates = load_cmo_rates(store_code=str(store_code))
                labor_cost_by_day = _labor_cost_from_turni_rows(turni, cmo_rates)
                hours_total = _money(sum(float(v or 0.0) for v in prod_by_day.values()))
                hours_training = _money(sum(float(v or 0.0) for v in train_by_day.values()))
                labor_cost = _money(sum(float(v or 0.0) for v in labor_cost_by_day.values()))
            except Exception:
                hours_total = 0.0
                hours_training = 0.0
                labor_cost = 0.0
    except Exception:
        budget = _money(budget)
        ly = _money(ly)
        hours_total = _money(hours_total)
        hours_training = _money(hours_training)
        labor_cost = _money(labor_cost)

    if not productivity_eur_per_hour:
        productivity_eur_per_hour = _money((actual_net / hours_total) if hours_total else 0.0)

    vs_budget = _money(actual_net - budget)
    vs_ly = _money(actual_net - ly)
    vs_budget_pct = _pct_delta(actual_net, budget)
    vs_ly_pct = _pct_delta(actual_net, ly)
    if is_future and not actual_net:
        vs_budget = None
        vs_ly = None
        vs_budget_pct = None
        vs_ly_pct = None

    return {
        "available": True,
        "period_kind": (
            "week" if (start.weekday() == 0 and end.weekday() == 6 and (end - start).days == 6)
            else "month" if _is_full_calendar_month(start, end)
            else "custom"
        ),
        "is_future": is_future,
        "revenues_actual_net": actual_net,
        "revenues_budget": _money(budget),
        "revenues_ly": _money(ly),
        "vs_budget": vs_budget,
        "vs_ly": vs_ly,
        "vs_budget_pct": vs_budget_pct,
        "vs_ly_pct": vs_ly_pct,
        "delivery_incidence_pct": (round((delivery_net / actual_net) * 100.0, 2) if actual_net else None),
        "delivery_net": delivery_net,
        "hours_total": _money(hours_total),
        "hours_training": _money(hours_training),
        "labor_cost": _money(labor_cost),
        "labor_cost_pct": (round((labor_cost / actual_net) * 100.0, 2) if actual_net else None),
        "productivity_eur_per_hour": _money(productivity_eur_per_hour),
    }


def _aggregate_delivery_weekly(store_code: str, start: date, end: date) -> dict[str, Any]:
    full = _full_weeks_between(start, end)
    if not full:
        return {}
    try:
        rows = export_weekly_rows(
            store_codes=[str(store_code)],
            week_start_from=full[0],
            week_start_to=full[1],
            platform="ALL",
        ) or []
    except Exception:
        rows = []
    out: dict[str, Any] = {}
    for r in rows:
        platform = str(r.get("platform") or "").strip().upper()
        if not platform:
            continue
        rec = out.setdefault(platform, {
            "payment_online": 0.0,
            "payment_cash": 0.0,
            "payment_total": 0.0,
            "orders": 0,
            "cancelled_orders": 0,
            "complaints_received": 0,
            "refund_value": 0.0,
            "complaints_contested": 0,
            "appeals_accepted": 0,
            "refunds_cancelled_value": 0.0,
        })
        for k in ("payment_online", "payment_cash", "payment_total", "refund_value", "refunds_cancelled_value"):
            rec[k] = _money(rec.get(k, 0.0) + _money(r.get(k)))
        for k in ("orders", "cancelled_orders", "complaints_received", "complaints_contested", "appeals_accepted"):
            rec[k] = int(rec.get(k, 0) or 0) + int(r.get(k, 0) or 0)
    return out


def _build_store_period_row(store: dict[str, str], start: date, end: date) -> dict[str, Any]:
    current = _aggregate_primanota_range(store["code"], start, end)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=(end - start).days)
    previous = _aggregate_primanota_range(store["code"], prev_start, prev_end)
    current_kpi = _aggregate_cruscotto_kpi(
        store["code"],
        start,
        end,
        actual_gross=current.get("giro_affari"),
        delivery_gross=current.get("delivery_totale"),
    )
    previous_kpi = _aggregate_cruscotto_kpi(
        store["code"],
        prev_start,
        prev_end,
        actual_gross=previous.get("giro_affari"),
        delivery_gross=previous.get("delivery_totale"),
    )
    delivery_weekly = _aggregate_delivery_weekly(store["code"], start, end)
    delta = {
        "giro_affari": _money(current["giro_affari"] - previous["giro_affari"]),
        "diff_cassa": _money(current["diff_cassa"] - previous["diff_cassa"]),
        "scontrini": int(current["scontrini"] - previous["scontrini"]),
        "pos": _money(current["pos"] - previous["pos"]),
        "spese": _money(current["spese"] - previous["spese"]),
        "versamenti": _money(current["versamenti"] - previous["versamenti"]),
        "delivery_totale": _money(current["delivery_totale"] - previous["delivery_totale"]),
        "revenues_actual_net": _money(current_kpi["revenues_actual_net"] - previous_kpi["revenues_actual_net"]),
    }
    return {
        "store_code": store["code"],
        "store_name": store["name"],
        "current": current,
        "previous": previous,
        "current_kpi": current_kpi,
        "previous_kpi": previous_kpi,
        "delta": delta,
        "delivery_weekly": delivery_weekly,
    }


def _aggregate_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys_float = ("giro_affari", "diff_cassa", "distinte", "pos", "spese", "spese_gross", "versamenti", "delivery_totale", "ticket_totale", "coupon_totale")
    keys_int = ("scontrini", "versamenti_count", "versato_days")
    out = {k: 0.0 for k in keys_float}
    out.update({k: 0 for k in keys_int})
    out.update({
        "revenues_actual_net": 0.0,
        "revenues_budget": 0.0,
        "revenues_ly": 0.0,
        "vs_budget": 0.0,
        "vs_ly": 0.0,
        "kpi_store_count": 0,
        "delivery_net": 0.0,
        "hours_total": 0.0,
        "hours_training": 0.0,
        "labor_cost": 0.0,
        "productivity_eur_per_hour": 0.0,
    })
    for row in rows:
        cur = row.get("current") or {}
        cur_kpi = row.get("current_kpi") or {}
        for k in keys_float:
            out[k] = _money(out.get(k, 0.0) + _money(cur.get(k)))
        for k in keys_int:
            out[k] = int(out.get(k, 0) or 0) + int(cur.get(k, 0) or 0)
        if cur_kpi.get("available"):
            out["kpi_store_count"] = int(out.get("kpi_store_count", 0) or 0) + 1
        for k in ("revenues_actual_net", "revenues_budget", "revenues_ly", "vs_budget", "vs_ly", "delivery_net", "hours_total", "hours_training", "labor_cost"):
            out[k] = _money(out.get(k, 0.0) + _money(cur_kpi.get(k)))
    out["vs_budget_pct"] = _pct_delta(out.get("revenues_actual_net"), out.get("revenues_budget"))
    out["vs_ly_pct"] = _pct_delta(out.get("revenues_actual_net"), out.get("revenues_ly"))
    out["delivery_incidence_pct"] = round((out.get("delivery_net", 0.0) / out.get("revenues_actual_net", 0.0)) * 100.0, 2) if out.get("revenues_actual_net", 0.0) else None
    out["labor_cost_pct"] = round((out.get("labor_cost", 0.0) / out.get("revenues_actual_net", 0.0)) * 100.0, 2) if out.get("revenues_actual_net", 0.0) else None
    out["productivity_eur_per_hour"] = _money((out.get("revenues_actual_net", 0.0) / out.get("hours_total", 0.0)) if out.get("hours_total", 0.0) else 0.0)
    return out


def build_analysis_context(
    question: str,
    *,
    available_stores: list[dict[str, str]],
    role: str = "user",
    current_store_code: str | None = None,
    page_context: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    period = parse_period(question, today=today)
    scope = resolve_store_scope(
        question,
        available_stores=available_stores,
        role=role,
        current_store_code=current_store_code,
    )
    selected = scope["selected_stores"] or []
    question_profile = classify_question(question)
    page_scope = infer_page_scope(page_context)
    rows = [_build_store_period_row(store, period["start"], period["end"]) for store in selected]
    rows = sorted(rows, key=lambda r: (_money((r.get("current") or {}).get("giro_affari")), str(r.get("store_name") or "").lower()), reverse=True)
    prev_end = period["start"] - timedelta(days=1)
    prev_start = prev_end - timedelta(days=(period["end"] - period["start"]).days)
    assumptions: list[str] = []
    if not scope.get("mentions"):
        if str(role or "").lower() == "admin" and current_store_code:
            assumptions.append("Nessun @store indicato: ho considerato lo store attualmente selezionato.")
        else:
            assumptions.append("Nessun @store indicato: ho considerato tutti gli store visibili per l'utente.")
    if period.get("source") == "default":
        assumptions.append("Periodo non specificato: ho considerato la settimana scorsa.")

    totals = _aggregate_totals(rows)
    if period["start"] > (today or date.today()) and not _money(totals.get("revenues_actual_net")):
        totals["vs_budget"] = None
        totals["vs_ly"] = None
        totals["vs_budget_pct"] = None
        totals["vs_ly_pct"] = None

    return {
        "question": question,
        "period": {
            "label": period["label"],
            "start_iso": period["start"].isoformat(),
            "end_iso": period["end"].isoformat(),
            "previous_start_iso": prev_start.isoformat(),
            "previous_end_iso": prev_end.isoformat(),
            "is_future": period["start"] > (today or date.today()),
            "is_single_day": period["start"] == period["end"],
        },
        "stores": [{"code": s["code"], "name": s["name"]} for s in selected],
        "question_profile": question_profile,
        "page_scope": page_scope,
        "page_context": page_context or "",
        "lookups": {
            "administrative": _build_administrative_lookup(question, stores=selected, period=period, page_scope=page_scope, question_profile=question_profile),
            "delivery": _build_delivery_lookup(question, stores=selected, period=period, page_scope=page_scope, question_profile=question_profile),
            "controlli": _build_controlli_lookup(question, stores=selected, period=period, page_scope=page_scope, question_profile=question_profile),
            "orari": _build_orari_lookup(question, stores=selected, period=period, page_scope=page_scope, question_profile=question_profile),
        },
        "rows": rows,
        "totals": totals,
        "assumptions": assumptions,
        "unresolved_mentions": scope.get("unresolved_mentions") or [],
    }


def _build_prompt(question: str, context: dict[str, Any]) -> str:
    profile = context.get("question_profile") or {}
    mode = str(profile.get("mode") or "business")
    return (
        "Sei un assistente gestionale per Store Hub 360. "
        "Rispondi in italiano con testo semplice, senza markdown, senza asterischi e senza titoli. "
        "Mantieni la risposta corta, precisa e utile: massimo 6 righe brevi. "
        "Usa solo i dati forniti e non inventare numeri. "
        "Quando citi scostamenti vs budget o anno precedente, indica sempre sia il valore sia la percentuale. "
        "Quando parli di delivery, indica anche l'incidenza percentuale sulle vendite. "
        "Quando parli di produttivita, ore lavorate o costo lavoro, usa i KPI operativi del contesto e cita i valori in modo diretto. "
        "Per vendite, budget, anno precedente e andamenti usa i valori netti iva 10 per cento. "
        "Per spese e versamenti usa i valori lordi. "
        "Se il periodo richiesto e' futuro, tratta budget e forecast come pianificazione: non presentarli come dato negativo solo perche' l'actual e' zero o non ancora disponibile. "
        "Se l'utente chiede un budget puntuale di un giorno futuro, rispondi in modo secco con il budget del giorno e solo il minimo contesto utile. "
        "Se il risultato e' sopra budget o sopra anno precedente, dillo chiaramente come dato positivo e non presentarlo come criticita'. "
        "Se il risultato e' sotto budget o sotto anno precedente, evidenzialo come punto di attenzione. "
        f"La domanda e' classificata come: {mode}. "
        "Se la domanda e' amministrativa, dai priorita a spese, versamenti, differenze e aspetti operativi. "
        "Se la domanda e' business, dai priorita a vendite, budget, anno precedente, scontrini e delivery. "
        "Se nel contesto trovi lookups amministrativi con versamenti_matches o spese_matches, usali come fonte prioritaria per rispondere a ricerche puntuali su tessere, riferimenti, documenti o fornitori. "
        "Se nel contesto trovi lookups controlli, usali come fonte prioritaria per domande su P&L, EBITDA, labour cost, COGS, margini e costi controllabili. "
        "Quando usi i lookups controlli, leggi le righe come valori gia pronti: actual_eur, budget_eur, last_year_eur e i corrispondenti actual_pct, budget_pct, last_year_pct, diff_pct, diff_last_year_pct sono gia espressi in percentuale. "
        "Per le voci di P&L cita, quando disponibili, sia il valore in euro sia l'incidenza percentuale; per gli scostamenti cita sia il delta in euro sia il delta percentuale. "
        "Nel P&L REVENUES, margini ed EBITDA sono voci di performance: piu alto e meglio. "
        "Nel P&L COGS, LABOUR COST, DELIVERY FEES, G&A STORE e TOTALE COSTI CONTROLLABILI sono costi: piu basso e meglio. "
        "Per i costi, se l'actual e' sopra budget o sopra anno precedente e anche l'incidenza percentuale e' peggiore, trattalo come dato negativo. "
        "Per i costi, se l'actual e' sotto budget o sotto anno precedente e l'incidenza percentuale migliora, trattalo come dato positivo. "
        "Quando l'utente chiede una voce P&L, confrontala sempre sia in valore assoluto sia in incidenza sulle REVENUES rispetto a budget e anno precedente, se disponibili. "
        "Se nel lookup controlli esiste una voce richiesta dall'utente, non dire che il dato manca. "
        "Se nel contesto trovi lookups delivery o orari, usali come fonte prioritaria per domande operative su provider, ordini, refund, contestazioni, nominativi, causali e ore. "
        "Se page_scope non e' general, resta focalizzato sui dati coerenti con quella pagina. "
        "Chiudi con una conclusione operativa molto breve.\n\n"
        f"Domanda utente:\n{question.strip()}\n\n"
        f"Contesto dati JSON:\n{json.dumps(context, ensure_ascii=False)}"
    )


def _clean_answer_text(text: str) -> str:
    cleaned = str(text or "").replace("**", "").replace("__", "").strip()
    lines: list[str] = []
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        line = re.sub(r"^[#>\-*]+\s*", "", line)
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def ask_storehub_assistant(
    question: str,
    *,
    available_stores: list[dict[str, str]],
    role: str = "user",
    current_store_code: str | None = None,
    page_context: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    if not openai_is_configured():
        raise RuntimeError("OpenAI non configurato: imposta OPENAI_API_KEY e OPENAI_MODEL.")

    context = build_analysis_context(
        question,
        available_stores=available_stores,
        role=role,
        current_store_code=current_store_code,
        page_context=page_context,
        today=today,
    )
    if not context["stores"]:
        raise RuntimeError("Nessuno store disponibile o nessun @store riconosciuto tra quelli accessibili.")

    prompt = _build_prompt(question, context)
    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    }
                ],
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers=headers,
        json=payload,
        timeout=OPENAI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json() or {}
    text = (data.get("output_text") or "").strip()
    if not text:
        parts: list[str] = []
        for item in (data.get("output") or []):
            for content in (item.get("content") or []):
                t = content.get("text")
                if t:
                    parts.append(str(t))
        text = "\n".join(parts).strip()
    if not text:
        raise RuntimeError("OpenAI non ha restituito testo.")
    text = _clean_answer_text(text)

    usage = data.get("usage") or {}
    return {
        "answer": text,
        "context": context,
        "model": OPENAI_MODEL,
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }
