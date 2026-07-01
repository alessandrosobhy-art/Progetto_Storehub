from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote, urljoin

import requests


DEFAULT_REGISTER_URL = "https://admin.mobipos.it/api/web-service.php/thirdParty/token/register/{apiToken}"
DEFAULT_BASE_ENDPOINT = "https://mobipos.it"
DEFAULT_USER_AGENT = "StoreHub360/1.0"
TIMEOUT = 30


@dataclass
class MobiposResult:
    ok: bool
    status_code: int | None
    url: str
    method: str
    data: Any
    error: str | None = None


def _json_or_text(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text


def _clean_base(base_endpoint: str | None) -> str:
    base = str(base_endpoint or DEFAULT_BASE_ENDPOINT).strip() or DEFAULT_BASE_ENDPOINT
    return base.rstrip("/")


def _headers(api_token: str, session_token: str | None = None, user_agent: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": str(user_agent or DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT,
        "ApiToken": str(api_token or "").strip(),
    }
    token = str(session_token or "").strip()
    if token:
        headers["X-Token"] = token
    return headers


def _request(method: str, url: str, *, api_token: str = "", session_token: str = "", user_agent: str = "", json_body: dict | None = None) -> MobiposResult:
    try:
        resp = requests.request(
            method.upper(),
            url,
            headers=_headers(api_token, session_token, user_agent),
            json=json_body if json_body is not None else None,
            timeout=TIMEOUT,
        )
        data = _json_or_text(resp)
        return MobiposResult(
            ok=200 <= int(resp.status_code) < 300,
            status_code=int(resp.status_code),
            url=url,
            method=method.upper(),
            data=data,
            error=None if 200 <= int(resp.status_code) < 300 else _extract_error(data),
        )
    except Exception as exc:
        return MobiposResult(False, None, url, method.upper(), None, str(exc))


def _extract_error(data: Any) -> str:
    if isinstance(data, dict):
        return str(data.get("message") or data.get("error") or data)
    return str(data or "")


def register_api_token(api_token: str, *, user_agent: str = "") -> MobiposResult:
    token = str(api_token or "").strip()
    url = DEFAULT_REGISTER_URL.format(apiToken=quote(token, safe=""))
    return _request("GET", url, api_token=token, user_agent=user_agent)


def get_session_token(base_endpoint: str, api_token: str, *, user_agent: str = "") -> MobiposResult:
    url = urljoin(f"{_clean_base(base_endpoint)}/", "ws/auth.api.php/token")
    return _request("GET", url, api_token=api_token, user_agent=user_agent)


def get_settings(base_endpoint: str, api_token: str, session_token: str, *, user_agent: str = "") -> MobiposResult:
    url = urljoin(f"{_clean_base(base_endpoint)}/", "ws/api.php/thirdParty/settings")
    return _request("GET", url, api_token=api_token, session_token=session_token, user_agent=user_agent)


def get_settings_by_shop(base_endpoint: str, api_token: str, session_token: str, shop_id: str | int, *, user_agent: str = "") -> MobiposResult:
    shop = quote(str(shop_id or "").strip(), safe="")
    url = urljoin(f"{_clean_base(base_endpoint)}/", f"ws/api.php/thirdParty/settingsByShop/{shop}")
    return _request("GET", url, api_token=api_token, session_token=session_token, user_agent=user_agent)


def get_sales(
    base_endpoint: str,
    api_token: str,
    session_token: str,
    *,
    shop_id: str | int,
    from_date: str,
    to_date: str,
    grouped: bool = False,
    from_time: str = "",
    to_time: str = "",
    user_agent: str = "",
) -> MobiposResult:
    body: dict[str, Any] = {
        "fromDate": str(from_date or "")[:10],
        "toDate": str(to_date or "")[:10],
        "shop": int(str(shop_id).strip()) if str(shop_id or "").strip().isdigit() else str(shop_id or "").strip(),
        "grouped": bool(grouped),
    }
    if from_time:
        body["fromTime"] = str(from_time).strip()
    if to_time:
        body["toTime"] = str(to_time).strip()
    url = urljoin(f"{_clean_base(base_endpoint)}/", "ws/api.php/thirdParty/sales/list")
    return _request("POST", url, api_token=api_token, session_token=session_token, user_agent=user_agent, json_body=body)


def summarize_sales_payload(payload: Any) -> dict[str, Any]:
    sales = []
    if isinstance(payload, dict):
        raw = payload.get("sales")
        if isinstance(raw, list):
            sales = raw
    total_amount = 0.0
    total_receipts = 0
    total_pieces = 0.0
    payment_totals: dict[str, float] = {}
    cash_registers: dict[str, int] = {}
    rows_count = 0
    for sale in sales:
        if not isinstance(sale, dict):
            continue
        total_receipts += 1
        total_amount += _num(sale.get("totale"))
        total_pieces += _num(sale.get("totalePezzi") or sale.get("qty"))
        punto = str(sale.get("puntoCassa") or "").strip()
        if punto:
            cash_registers[punto] = cash_registers.get(punto, 0) + 1
        payments = sale.get("payments")
        if isinstance(payments, dict):
            for key, value in payments.items():
                label = str(key or "(vuoto)").strip() or "(vuoto)"
                payment_totals[label] = payment_totals.get(label, 0.0) + _num(value)
        rows = sale.get("rows")
        if isinstance(rows, list):
            rows_count += len(rows)
    return {
        "sales_count": len(sales),
        "receipts_count": total_receipts,
        "rows_count": rows_count,
        "total_amount": round(total_amount, 4),
        "total_pieces": round(total_pieces, 4),
        "payment_totals": sorted(
            [{"method": k, "amount": round(v, 4)} for k, v in payment_totals.items()],
            key=lambda x: (-abs(float(x.get("amount") or 0)), str(x.get("method") or "")),
        ),
        "cash_registers": sorted(
            [{"cash_register": k, "count": v} for k, v in cash_registers.items()],
            key=lambda x: (-int(x.get("count") or 0), str(x.get("cash_register") or "")),
        ),
    }


def validate_sales_period(from_date: str, to_date: str) -> str | None:
    try:
        start = datetime.strptime(str(from_date or "")[:10], "%Y-%m-%d").date()
        end = datetime.strptime(str(to_date or "")[:10], "%Y-%m-%d").date()
    except Exception:
        return "Date non valide: usa formato yyyy-MM-dd."
    if end < start:
        return "La data finale non puo essere precedente alla data iniziale."
    if (end - start).days > 6:
        return "La documentazione Mobipos indica un periodo massimo di 7 giorni per sales/list."
    if end > date.today():
        return "La data finale e nel futuro."
    return None


def _num(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except Exception:
        try:
            return float(str(value).replace(".", "").replace(",", "."))
        except Exception:
            return 0.0
