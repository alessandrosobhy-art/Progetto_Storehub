from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import os
import sys
from typing import Any, Dict, List, Tuple

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DB_BACKEND", "sqlserver")
os.environ.setdefault("SQLSERVER_SERVER", r"10.24.1.1\SQLEXPRESS")
os.environ.setdefault("SQLSERVER_PASSWORD", "Metis2021@")

from app_db import get_connection_ilp
from distinta_cassa_ipratico_repository import (
    get_distinta_cassa_ipratico_snapshot,
    upsert_distinta_cassa_ipratico_snapshot,
)
from ipratico_repository import (
    API_URL,
    _clean_api_key,
    _extract_records,
    _find_store_mapping_table,
    _has_payment_session_match,
    _is_delivery_session,
    _is_refund_session,
    _is_sale_session,
    _is_training_session,
    _money2,
    _payment_effective_amount,
    _payment_parts,
    _provider_from_session,
    _receipt_amount,
    _title_provider,
    _to_decimal,
    _classify_payment_item,
)
from primanota_repository import get_elenchi_options
from rendiconto import _map_ipratico_import_for_distinta


START_DATE = date(2026, 1, 1)
END_DATE = date.today() - timedelta(days=1)
CHUNK_DAYS = 7
REQUEST_TIMEOUT = 90
OVERWRITE_EXISTING = False

# Se vuoi forzare alcune associazioni site -> api key nel codice, inseriscile qui.
# Formato: "4004": "13530:bc43c39e-6863-435c-872d-67759db380b8"
SITE_API_KEYS: Dict[str, str] = {}


def _load_site_api_keys_from_ilp() -> Dict[str, str]:
    conn = get_connection_ilp(read_only=True)
    try:
        table_schema, table_name, site_col, zucchetti_col = _find_store_mapping_table(conn)
        cur = conn.cursor()
        try:
            cur.execute(
                f"""
                SELECT
                    LTRIM(RTRIM(CAST([{site_col}] AS NVARCHAR(50)))) AS site,
                    LTRIM(RTRIM(CAST([{zucchetti_col}] AS NVARCHAR(255)))) AS api_key
                FROM [{table_schema}].[{table_name}]
                WHERE NULLIF(LTRIM(RTRIM(CAST([{site_col}] AS NVARCHAR(50)))), '') IS NOT NULL
                  AND NULLIF(LTRIM(RTRIM(CAST([{zucchetti_col}] AS NVARCHAR(255)))), '') IS NOT NULL
                ORDER BY 1
                """
            )
            rows = cur.fetchall() or []
        finally:
            try:
                cur.close()
            except Exception:
                pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    out: Dict[str, str] = {}
    for row in rows:
        site = str(row[0] or "").strip()
        api_key = _clean_api_key(row[1])
        if site and api_key:
            out[site] = api_key
    return out


def load_site_api_keys() -> Dict[str, str]:
    mapping = _load_site_api_keys_from_ilp()
    for site, api_key in (SITE_API_KEYS or {}).items():
        s = str(site or "").strip()
        k = _clean_api_key(api_key)
        if s and k:
            mapping[s] = k
    return dict(sorted(mapping.items(), key=lambda x: x[0]))


def iter_chunks(start_day: date, end_day: date, chunk_days: int = CHUNK_DAYS):
    cur = start_day
    while cur <= end_day:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end_day)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def fetch_chunk_records(api_key: str, start_day: date, end_day: date) -> List[Dict[str, Any]]:
    headers = {"accept": "application/json", "x-api-key": _clean_api_key(api_key)}
    params = {
        "dateFrom": start_day.isoformat(),
        "dateTo": (end_day + timedelta(days=1)).isoformat(),
    }
    resp = requests.get(API_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 400:
        body = ""
        try:
            body = resp.text[:500]
        except Exception:
            body = ""
        raise requests.HTTPError(
            f"{resp.status_code} {resp.reason} for {resp.url}. Body: {body}",
            response=resp,
        )
    return _extract_records(resp.json())


def fetch_chunk_records_resilient(api_key: str, start_day: date, end_day: date) -> List[Dict[str, Any]]:
    try:
        return fetch_chunk_records(api_key, start_day, end_day)
    except requests.HTTPError as exc:
        resp = getattr(exc, "response", None)
        status = int(getattr(resp, "status_code", 0) or 0)
        if status != 400 or start_day >= end_day:
            raise

        mid_ordinal = (start_day.toordinal() + end_day.toordinal()) // 2
        mid_day = date.fromordinal(mid_ordinal)
        if mid_day < start_day:
            mid_day = start_day
        if mid_day >= end_day:
            raise

        left = fetch_chunk_records_resilient(api_key, start_day, mid_day)
        right = fetch_chunk_records_resilient(api_key, mid_day + timedelta(days=1), end_day)
        return left + right


def record_day_iso(rec: Dict[str, Any]) -> str:
    v = rec.get("value") or {}
    ref = str(v.get("referenceDate") or "").strip()
    if ref:
        try:
            return datetime.strptime(ref[:10], "%Y-%m-%d").date().isoformat()
        except Exception:
            pass
        try:
            return datetime.strptime(ref[:10], "%d/%m/%Y").date().isoformat()
        except Exception:
            pass
    closure = str(v.get("closureDate") or "").strip()
    if closure:
        try:
            return datetime.fromisoformat(closure.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            pass
    return ""


def group_records_by_day(records: List[Dict[str, Any]], start_day: date, end_day: date) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records or []:
        day_iso = record_day_iso(rec)
        if day_iso:
            grouped[day_iso].append(rec)

    out: Dict[str, List[Dict[str, Any]]] = {}
    cur = start_day
    while cur <= end_day:
        key = cur.isoformat()
        out[key] = grouped.get(key, [])
        cur += timedelta(days=1)
    return out


def _iter_payment_rows(v: Dict[str, Any]) -> List[Tuple[str, Decimal]]:
    rows: List[Tuple[str, Decimal]] = []
    for item in _payment_parts(v):
        kind = _classify_payment_item(item, v)
        amount = _payment_effective_amount(item, v)
        if amount == 0:
            continue
        rows.append((kind, amount))
    return rows


def _delivery_split(v: Dict[str, Any]) -> Tuple[Decimal, Decimal]:
    cash_total = Decimal("0")
    online_total = Decimal("0")
    for kind, amount in _iter_payment_rows(v):
        if kind == "cash":
            cash_total += amount
        else:
            online_total += amount
    return cash_total, online_total


def _split_non_delivery_payments(v: Dict[str, Any]) -> Dict[str, Decimal]:
    totals = {"cash": Decimal("0"), "pos": Decimal("0"), "satispay": Decimal("0"), "ticket": Decimal("0"), "other": Decimal("0")}
    for kind, amount in _iter_payment_rows(v):
        if kind == "cash":
            totals["cash"] += amount
        elif kind == "pos":
            totals["pos"] += amount
        elif kind == "satispay":
            totals["satispay"] += amount
        elif kind == "ticket":
            totals["ticket"] += amount
        else:
            totals["other"] += amount
    return totals


def summarize_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    vendite_lorde = Decimal("0")
    annullati = Decimal("0")
    contanti = Decimal("0")
    pos = Decimal("0")
    satispay = Decimal("0")
    ticket = Decimal("0")
    scontrini = 0
    matched_sessions = 0
    skipped_unmatched = 0
    deliveries: Dict[Tuple[str, bool], Decimal] = {}
    warnings: List[str] = []

    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        v = rec.get("value") or {}
        if not isinstance(v, dict):
            continue

        amount = _receipt_amount(v)
        if _is_training_session(v):
            continue

        has_match = _has_payment_session_match(v)
        if not has_match:
            skipped_unmatched += 1

        if _is_refund_session(v):
            if not has_match:
                continue
            matched_sessions += 1
            annullati += abs(amount)
            continue

        if not _is_sale_session(v):
            continue
        if not has_match:
            continue

        matched_sessions += 1
        vendite_lorde += amount
        scontrini += 1

        if _is_delivery_session(v):
            provider = _provider_from_session(v)
            cash_total, online_total = _delivery_split(v)
            if cash_total == 0 and online_total == 0:
                sid = str(rec.get("id") or "").strip()
                warnings.append(f"Nessuna payment.amount utile per sessione delivery {sid}: inclusa nel totale ma esclusa dai bucket delivery.")
            if online_total != 0:
                deliveries[(provider, False)] = deliveries.get((provider, False), Decimal("0")) + online_total
            if cash_total != 0:
                deliveries[(provider, True)] = deliveries.get((provider, True), Decimal("0")) + cash_total
                contanti += cash_total
            continue

        split = _split_non_delivery_payments(v)
        contanti += split["cash"]
        pos += split["pos"]
        satispay += split["satispay"]
        ticket += split["ticket"]
        if split["other"] != 0:
            sid = str(rec.get("id") or "").strip()
            warnings.append(f"Pagamenti non classificati per sessione {sid}: {_money2(split['other'])} esclusi dai bucket.")

    delivery_rows = []
    for (provider, is_cash), total in sorted(deliveries.items(), key=lambda x: (str(x[0][0]).lower(), x[0][1])):
        delivery_rows.append(
            {
                "provider": provider,
                "is_cash": bool(is_cash),
                "valore": _money2(total),
                "label": f"{_title_provider(provider)}{' Contanti' if is_cash else ''}",
                "tipo": "NO" if is_cash else "SI",
            }
        )

    return {
        "chiusura": {
            "vendite_lorde": _money2(vendite_lorde),
            "annullati": _money2(annullati),
            "contanti": _money2(contanti),
            "pos": _money2(pos),
            "ticket": _money2(ticket),
            "scontrini": int(scontrini),
        },
        "ticket": _money2(ticket),
        "satispay": _money2(satispay),
        "deliveries": delivery_rows,
        "records_count": int(matched_sessions),
        "raw_records_count": int(len(records or [])),
        "skipped_unmatched": int(skipped_unmatched),
        "warnings": warnings,
    }


def backfill_site(site: str, api_key: str, start_day: date, end_day: date) -> Dict[str, int]:
    options = get_elenchi_options(store_code=str(site))
    stats = {"saved": 0, "skipped": 0, "errors": 0}

    for chunk_start, chunk_end in iter_chunks(start_day, end_day, CHUNK_DAYS):
        records = fetch_chunk_records_resilient(api_key, chunk_start, chunk_end)
        grouped = group_records_by_day(records, chunk_start, chunk_end)

        for day_iso, day_records in grouped.items():
            if not OVERWRITE_EXISTING:
                existing = get_distinta_cassa_ipratico_snapshot(store_code=str(site), data_iso=day_iso)
                if existing:
                    stats["skipped"] += 1
                    continue

            try:
                imported = summarize_records(day_records)
                mapped = _map_ipratico_import_for_distinta(
                    imported=imported,
                    options=options,
                    store_code=str(site),
                    d_iso=day_iso,
                )
                upsert_distinta_cassa_ipratico_snapshot(
                    store_code=str(site),
                    data_iso=day_iso,
                    imported_payload=imported,
                    mapped_payload=mapped,
                    save_origin="backfill_2026",
                    imported_manually=False,
                )
                stats["saved"] += 1
                print(f"[OK] {site} {day_iso} records={imported.get('records_count', 0)}/{imported.get('raw_records_count', 0)}")
            except Exception as exc:
                stats["errors"] += 1
                print(f"[ERR] {site} {day_iso}: {exc}")

    return stats


def main() -> None:
    if END_DATE < START_DATE:
        raise RuntimeError("END_DATE precedente a START_DATE.")

    site_api_keys = load_site_api_keys()
    if not site_api_keys:
        raise RuntimeError("Nessuna associazione site -> api key disponibile.")

    print(f"Backfill iPratico Distinta cassa dal {START_DATE.isoformat()} al {END_DATE.isoformat()}")
    print(f"Store trovati: {len(site_api_keys)}")
    grand = {"saved": 0, "skipped": 0, "errors": 0}

    for site, api_key in site_api_keys.items():
        print(f"\n=== STORE {site} ===")
        try:
            stats = backfill_site(site, api_key, START_DATE, END_DATE)
            for k in grand:
                grand[k] += int(stats.get(k, 0))
            print(f"[STORE {site}] saved={stats['saved']} skipped={stats['skipped']} errors={stats['errors']}")
        except Exception as exc:
            grand["errors"] += 1
            print(f"[STORE {site}] errore blocco store: {exc}")

    print("\n=== COMPLETATO ===")
    print(f"saved={grand['saved']} skipped={grand['skipped']} errors={grand['errors']}")


if __name__ == "__main__":
    main()
