from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import requests

from store_registry_repository import get_ipratico_api_key_for_store


API_URL = "https://apicb.ipraticocloud.com/api/public/closed-payment-sessions"


def _norm(v: Any) -> str:
    return str(v or "").strip().lower()


def _norm_key(v: Any) -> str:
    return "".join(ch for ch in _norm(v) if ch.isalnum())


def _clean_api_key(raw: Any) -> str:
    return str(raw or "").strip()


def _parse_iso_dt(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _to_decimal(v: Any) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def _money2(v: Any) -> float:
    return float(_to_decimal(v).quantize(Decimal("0.01")))


def _title_provider(name: str) -> str:
    s = str(name or "").strip()
    if not s:
        return "Delivery"
    return s.replace("_", " ").replace("-", " ").strip().title()


def _extract_records(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("items", "results", "data", "content", "rows"):
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def get_site_ipratico_api_key(site: str) -> str:
    site = str(site or "").strip()
    if not site:
        raise RuntimeError("Store/SITE mancante per il recupero chiave iPratico.")

    api_key = _clean_api_key(get_ipratico_api_key_for_store(site))
    if not api_key:
        raise RuntimeError(f"Chiave iPratico non trovata per lo store {site}.")
    return api_key


def fetch_closed_payment_sessions_for_day(site: str, day_iso: str) -> List[Dict[str, Any]]:
    api_key = get_site_ipratico_api_key(site)
    day = datetime.strptime(day_iso, "%Y-%m-%d").date()
    next_day = day + timedelta(days=1)
    headers = {"accept": "application/json", "x-api-key": api_key}
    params = {"dateFrom": day.isoformat(), "dateTo": next_day.isoformat()}
    resp = requests.get(API_URL, headers=headers, params=params, timeout=60)
    resp.raise_for_status()
    return _extract_records(resp.json())


def fetch_closed_payment_sessions_for_range(site: str, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
    start = datetime.strptime(str(start_iso)[:10], "%Y-%m-%d").date()
    end = datetime.strptime(str(end_iso)[:10], "%Y-%m-%d").date()
    if end < start:
        start, end = end, start
    if (end - start).days > 14:
        raise RuntimeError("Il periodo test iPratico non puo superare 14 giorni.")

    out: List[Dict[str, Any]] = []
    d = start
    while d <= end:
        out.extend(fetch_closed_payment_sessions_for_day(site, d.isoformat()))
        d += timedelta(days=1)
    return out


def _receipt_amount(v: Dict[str, Any]) -> Decimal:
    return _to_decimal(v.get("receiptAmount"))


def _gross_amount(v: Dict[str, Any]) -> Decimal:
    for key in ("grossTotal", "paymentsTotal", "originalOrderTotal", "creditTotal"):
        d = _to_decimal(v.get(key))
        if d != 0:
            return d
    return _receipt_amount(v)


def _is_refund_session(v: Dict[str, Any]) -> bool:
    purpose = _norm(v.get("purpose"))
    return purpose in {"refund.for.cancellation", "refund.returning.goods"}


def _is_training_session(v: Dict[str, Any]) -> bool:
    return _norm(v.get("documentType")) == "training.bill"


def _is_sale_session(v: Dict[str, Any]) -> bool:
    if _is_training_session(v):
        return False
    if _is_refund_session(v):
        return False
    if bool(v.get("isSuspendedPayment")):
        return False
    amount = _receipt_amount(v)
    return amount > 0


def _is_receipt_count_session(v: Dict[str, Any]) -> bool:
    # Allineiamo il conteggio scontrini all'export iPratico:
    # contiamo i fiscal.bill anche a importo zero, ma escludiamo invoice,
    # training, refund e pagamenti sospesi.
    if _is_training_session(v):
        return False
    if _is_refund_session(v):
        return False
    if bool(v.get("isSuspendedPayment")):
        return False
    return _norm(v.get("documentType")) == "fiscal.bill"


def _is_delivery_session(v: Dict[str, Any]) -> bool:
    if _norm(v.get("sourceType")) == "delivery":
        return True
    app = _norm(v.get("sourceApp"))
    return app in {"deliveroo", "glovo", "ubereats", "uber_eats", "justeat", "just_eat", "wolt", "foodora"}


def _provider_from_session(v: Dict[str, Any]) -> str:
    raw = str(v.get("sourceApp") or "").strip()
    if raw:
        return raw
    return "Delivery"


def _payment_parts(v: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = v.get("payments")
    return items if isinstance(items, list) else []


def _has_payment_session_match(v: Dict[str, Any]) -> bool:
    # Consideriamo valida solo la sessione che ha almeno una riga nella sotto-tabella payments
    # collegata alla closed_payment_session.
    return len(_payment_parts(v)) > 0


def _payment_label(item: Dict[str, Any], parent: Dict[str, Any]) -> str:
    tr = item.get("transactionDetail") or {}
    parts = [
        item.get("paymentMethod"),
        tr.get("moneyTypeName"),
        tr.get("moneyTypeId"),
        parent.get("paymentMethod"),
        parent.get("toGoPayment", {}).get("paymentMethod") if isinstance(parent.get("toGoPayment"), dict) else None,
    ]
    return " | ".join(str(x or "") for x in parts if str(x or "").strip())


def _delivery_payment_context_label(item: Dict[str, Any], parent: Dict[str, Any]) -> str:
    to_go = parent.get("toGoPayment") if isinstance(parent.get("toGoPayment"), dict) else {}
    tr = item.get("transactionDetail") if isinstance(item.get("transactionDetail"), dict) else {}
    parts = [
        item.get("paymentMethod"),
        tr.get("moneyTypeName"),
        tr.get("moneyTypeId"),
        parent.get("paymentMethod"),
        parent.get("paymentMethodName"),
        parent.get("moneyTypeName"),
        parent.get("moneyTypeId"),
        to_go.get("paymentMethod"),
        to_go.get("paymentMethodName"),
        to_go.get("moneyTypeName"),
        to_go.get("moneyTypeId"),
        to_go.get("type"),
    ]
    return " | ".join(str(x or "") for x in parts if str(x or "").strip())


def _classify_payment_label(label: str) -> str:
    s = _norm(label)
    if not s:
        return "other"
    if "satispay" in s:
        return "satispay"
    if "ticket" in s:
        return "ticket"
    if any(k in s for k in ["cash", "contant", "contanti", "contante", "cashondelivery", "money_type:cash", "moneytypecash"]):
        return "cash"
    if any(k in s for k in ["card", "cards", "bancomat", "pos", "carta", "credit", "debit", "visa", "mastercard", "amex", "nexi", "postevirtualpos", "xpay"]):
        return "pos"
    if "online" in s:
        return "online"
    return "other"


def _classify_payment_item(item: Dict[str, Any], parent: Dict[str, Any]) -> str:
    return _classify_payment_label(_payment_label(item, parent))


def _classify_delivery_payment_item(item: Dict[str, Any], parent: Dict[str, Any]) -> str:
    raw = " | ".join(
        part
        for part in (
            _payment_label(item, parent),
            _delivery_payment_context_label(item, parent),
        )
        if part
    )
    return _classify_payment_label(raw)


def _payment_effective_amount(item: Dict[str, Any], parent: Dict[str, Any]) -> Decimal:
    amount = _to_decimal(item.get("amount"))
    kind = _classify_payment_item(item, parent)
    if kind == "cash":
        # Per il contante iPratico espone in amount il dato consegnato al cassiere;
        # il reale incasso ? amount - change.
        amount -= _to_decimal(item.get("change"))
    return amount


def _iter_payment_rows(v: Dict[str, Any]) -> List[Tuple[str, Decimal]]:
    rows: List[Tuple[str, Decimal]] = []
    for item in _payment_parts(v):
        kind = _classify_payment_item(item, v)
        amount = _payment_effective_amount(item, v)
        if amount == 0:
            continue
        rows.append((kind, amount))
    return rows


def profile_payment_methods(site: str, start_iso: str, end_iso: str) -> Dict[str, Any]:
    records = fetch_closed_payment_sessions_for_range(site, start_iso, end_iso)
    methods: Dict[str, Dict[str, Any]] = {}
    raw_sessions = 0
    sessions_with_payments = 0

    for rec in records:
        if not isinstance(rec, dict):
            continue
        v = rec.get("value") or {}
        if not isinstance(v, dict):
            continue
        raw_sessions += 1
        payment_items = _payment_parts(v)
        if payment_items:
            sessions_with_payments += 1
        elif _is_delivery_session(v):
            payment_items = [_synthetic_delivery_payment_item(v)]
        for item in payment_items:
            amount = _payment_effective_amount(item, v)
            if amount == 0:
                continue
            raw_label = _payment_label(item, v) or _delivery_payment_context_label(item, v) or "Senza etichetta"
            context_label = ""
            if _is_delivery_session(v):
                provider = _provider_from_session(v)
                kind = _classify_delivery_payment_item(item, v)
                delivery_bucket = "cash" if kind == "cash" else "online"
                context_label = _delivery_payment_context_label(item, v)
                raw_suffix = f" - {raw_label}" if raw_label else ""
                label = f"{_title_provider(provider)} {'Contanti' if delivery_bucket == 'cash' else 'Online'}{raw_suffix}"
                key = _norm_key(f"delivery|{provider}|{delivery_bucket}|{raw_label}") or _norm_key(label) or "delivery"
                classified = f"delivery_{delivery_bucket}"
            else:
                label = raw_label
                key = _norm_key(label) or "senzaetichetta"
                classified = _classify_payment_label(label)
            row = methods.setdefault(
                key,
                {
                    "method_key": key,
                    "method_label": label,
                    "classified_as": classified,
                    "count": 0,
                    "amount": Decimal("0"),
                    "delivery_count": 0,
                    "delivery_amount": Decimal("0"),
                    "raw_payment_label": raw_label,
                    "delivery_source_app": str(v.get("sourceApp") or "").strip(),
                    "delivery_source_type": str(v.get("sourceType") or "").strip(),
                    "delivery_payment_context": context_label,
                    "examples": [],
                },
            )
            row["count"] += 1
            row["amount"] += amount
            if _is_delivery_session(v):
                row["delivery_count"] += 1
                row["delivery_amount"] += amount
            example = str(rec.get("id") or "").strip()
            if example and len(row["examples"]) < 3:
                row["examples"].append(example)

    rows = []
    for row in methods.values():
        rows.append(
            {
                "method_key": row["method_key"],
                "method_label": row["method_label"],
                "classified_as": row["classified_as"],
                "count": int(row["count"]),
                "amount": _money2(row["amount"]),
                "delivery_count": int(row["delivery_count"]),
                "delivery_amount": _money2(row["delivery_amount"]),
                "raw_payment_label": row.get("raw_payment_label") or "",
                "delivery_source_app": row.get("delivery_source_app") or "",
                "delivery_source_type": row.get("delivery_source_type") or "",
                "delivery_payment_context": row.get("delivery_payment_context") or "",
                "examples": list(row["examples"]),
            }
        )
    rows.sort(key=lambda r: (str(r.get("classified_as") or ""), str(r.get("method_label") or "").lower()))
    return {
        "site": str(site),
        "start": str(start_iso)[:10],
        "end": str(end_iso)[:10],
        "raw_sessions": int(raw_sessions),
        "sessions_with_payments": int(sessions_with_payments),
        "methods": rows,
    }


def _payment_fact_from_item(rec: Dict[str, Any], parent: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    amount = _payment_effective_amount(item, parent)
    raw_label = _payment_label(item, parent) or _delivery_payment_context_label(item, parent) or "Senza etichetta"
    is_delivery = _is_delivery_session(parent)
    provider = _provider_from_session(parent) if is_delivery else ""
    delivery_bucket = ""
    context_label = ""

    if is_delivery:
        kind = _classify_delivery_payment_item(item, parent)
        delivery_bucket = "cash" if kind == "cash" else "online"
        context_label = _delivery_payment_context_label(item, parent)
        raw_suffix = f" - {raw_label}" if raw_label else ""
        label = f"{_title_provider(provider)} {'Contanti' if delivery_bucket == 'cash' else 'Online'}{raw_suffix}"
        method_key = _norm_key(f"delivery|{provider}|{delivery_bucket}|{raw_label}") or _norm_key(label) or "delivery"
        classified_as = f"delivery_{delivery_bucket}"
    else:
        label = raw_label
        method_key = _norm_key(label) or "senzaetichetta"
        classified_as = _classify_payment_label(label)

    return {
        "session_id": str(rec.get("id") or "").strip(),
        "method_key": method_key,
        "method_label": label,
        "classified_as": classified_as,
        "amount": _money2(amount),
        "raw_payment_label": raw_label,
        "delivery_provider": provider,
        "delivery_bucket": delivery_bucket,
        "delivery_source_app": str(parent.get("sourceApp") or "").strip(),
        "delivery_source_type": str(parent.get("sourceType") or "").strip(),
        "delivery_payment_context": context_label,
    }


def import_distinta_day_detailed(site: str, day_iso: str) -> Dict[str, Any]:
    records = fetch_closed_payment_sessions_for_day(site, day_iso)

    vendite_lorde = Decimal("0")
    annullati = Decimal("0")
    scontrini = 0
    matched_sessions = 0
    skipped_unmatched = 0
    facts: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for rec in records:
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

        if has_match and _is_receipt_count_session(v):
            scontrini += 1

        if not _is_sale_session(v):
            continue

        if not has_match:
            continue

        matched_sessions += 1
        vendite_lorde += amount

        payment_items = _payment_parts(v)
        if not payment_items and _is_delivery_session(v):
            payment_items = [_synthetic_delivery_payment_item(v)]

        session_fact_count = 0
        for item in payment_items:
            fact = _payment_fact_from_item(rec, v, item)
            if abs(float(fact.get("amount") or 0.0)) <= 1e-9:
                continue
            facts.append(fact)
            session_fact_count += 1

        if session_fact_count == 0:
            sid = str(rec.get("id") or "").strip()
            warnings.append(f"Nessuna payment.amount utile per sessione {sid}: inclusa nel totale ma non mappata.")

    return {
        "chiusura": {
            "vendite_lorde": _money2(vendite_lorde),
            "annullati": _money2(annullati),
            "contanti": 0.0,
            "pos": 0.0,
            "ticket": 0.0,
            "scontrini": int(scontrini),
        },
        "payment_facts": facts,
        "records_count": int(matched_sessions),
        "raw_records_count": int(len(records)),
        "skipped_unmatched": int(skipped_unmatched),
        "warnings": warnings,
    }


def _synthetic_delivery_payment_item(v: Dict[str, Any]) -> Dict[str, Any]:
    to_go = v.get("toGoPayment") if isinstance(v.get("toGoPayment"), dict) else {}
    method = (
        to_go.get("paymentMethod")
        or to_go.get("paymentMethodName")
        or to_go.get("moneyTypeName")
        or v.get("paymentMethod")
        or v.get("paymentMethodName")
        or v.get("moneyTypeName")
        or v.get("paymentMethod")
        or v.get("sourceApp")
        or v.get("sourceType")
        or "delivery_without_payments"
    )
    return {
        "paymentMethod": method,
        "amount": _gross_amount(v),
        "change": 0,
        "transactionDetail": {
            "moneyTypeName": to_go.get("moneyTypeName") or to_go.get("paymentMethod") or method,
            "moneyTypeId": to_go.get("moneyTypeId"),
        },
    }


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


def import_distinta_day(site: str, day_iso: str) -> Dict[str, Any]:
    records = fetch_closed_payment_sessions_for_day(site, day_iso)

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

    for rec in records:
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

        if has_match and _is_receipt_count_session(v):
            scontrini += 1

        if not _is_sale_session(v):
            continue

        if not has_match:
            continue

        matched_sessions += 1
        vendite_lorde += amount

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
            warnings.append(f"Pagamenti non classificati per sessione {sid}: { _money2(split['other']) } esclusi dai bucket.")

    delivery_rows = []
    for (provider, is_cash), total in sorted(deliveries.items(), key=lambda x: (_norm(x[0][0]), x[0][1])):
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
        "raw_records_count": int(len(records)),
        "skipped_unmatched": int(skipped_unmatched),
        "warnings": warnings,
    }
