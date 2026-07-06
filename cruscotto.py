from __future__ import annotations

from app_logging import log_swallowed
from datetime import date, datetime, timedelta
import re
import time
from typing import Any, Dict, List

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from controlli_repository import get_pnl
from cruscotto_pnl_store_repository import (
    MONTH_NAMES,
    list_visible_months,
    maintenance_years,
    period_is_visible,
    save_visible_months_for_year,
)
from db_integration import get_warehouse_stores, get_user_warehouse_stores
from primanota_repository import get_elenchi_options, load_primanota_month_agg
from spese_repository import sum_spese_month_by_day
from versamenti_repository import list_versamenti_month

from cruscotto_repository import get_weekly_analysis, get_monthly_analysis, get_weekly_kpi_overview
from kpi_notes_repository import get_note as get_kpi_note, upsert_note as upsert_kpi_note

cruscotto_bp = Blueprint("cruscotto", __name__, url_prefix="/cruscotto")


def _ensure_session_keys():
    session.setdefault("store_code", session.get("store_code"))
    session.setdefault("store_name", session.get("store_name"))


def _parse_week_start(value: str) -> date:
    value = (value or "").strip()
    if value:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except Exception:
            log_swallowed('cruscotto:40')
    today = date.today()
    return today - timedelta(days=today.weekday())


def _parse_month_start(value: str) -> date:
    value = (value or "").strip()
    if value:
        try:
            d = datetime.strptime(value[:10], "%Y-%m-%d").date()
            return d.replace(day=1)
        except Exception:
            log_swallowed('cruscotto:52')
        try:
            # support "YYYY-MM"
            d = datetime.strptime(value[:7], "%Y-%m").date()
            return d.replace(day=1)
        except Exception:
            log_swallowed('cruscotto:58')
    today = date.today()
    return today.replace(day=1)


def _is_admin() -> bool:
    role = str(session.get("role") or "").strip().lower()
    if role == "admin":
        return True
    if bool(session.get("is_master")) or role == "master":
        try:
            from tenant_config_repository import get_tenant

            tenant_key = str(session.get("master_admin_tenant_key") or "").strip()
            if not tenant_key:
                return False
            return bool(get_tenant(str(tenant_key or "")).get("master_can_admin"))
        except Exception:
            return False
    return False


def _allowed_stores_for_current_user() -> List[Dict[str, Any]]:
    uid = str(session.get("uid") or "").strip()
    try:
        all_stores = get_warehouse_stores() or []
    except Exception:
        all_stores = []

    if _is_admin():
        stores = all_stores
    else:
        assigned = []
        if uid:
            try:
                assigned = get_user_warehouse_stores(uid) or []
            except Exception:
                assigned = []
        allowed_codes = {str(r.get("store_code") or "").strip() for r in assigned if r.get("store_code")}
        if allowed_codes:
            stores = [s for s in all_stores if str((s or {}).get("code") or "").strip() in allowed_codes]
        else:
            current_code = str(session.get("store_code") or "").strip()
            stores = [s for s in all_stores if str((s or {}).get("code") or "").strip() == current_code] if current_code else []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in stores or []:
        code = str((s or {}).get("code") or (s or {}).get("store_code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": str((s or {}).get("name") or (s or {}).get("store_name") or code).strip()})

    return sorted(out, key=lambda x: (x.get("name", "").lower(), x.get("code", "").lower()))


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except Exception:
        return default


def _norm_voice(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def _rows_until_delivery_fees(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows or []:
        out.append(row)
        if _norm_voice(str(row.get("voice") or "")) == _norm_voice("DELIVERY FEES"):
            break
    return out


def _build_kpi_items_from_pnl(pnl: Dict[str, Any]) -> List[Dict[str, Any]]:
    wanted = ["REVENUES", "COGS", "LABOUR COST", "EBITDA"]
    wanted_norm = {_norm_voice(v): v for v in wanted}

    rows_by_voice = {}
    for r in (pnl.get("rows") or []):
        v = str(r.get("voice") or "").strip()
        if v:
            rows_by_voice[_norm_voice(v)] = r

    items: list[dict[str, Any]] = []
    for key_norm, label in wanted_norm.items():
        r = rows_by_voice.get(key_norm)
        item: dict[str, Any] = {"kpi": label}
        if not r:
            item.update({"missing": True, "status": "na"})
            items.append(item)
            continue

        budget = float(r.get("budget") or 0.0)
        actual = float(r.get("actual") or 0.0)
        budget_pct = float(r.get("budget_pct") or 0.0)
        actual_pct = float(r.get("actual_pct") or 0.0)
        diff = float(r.get("diff") or (actual - budget))
        diff_pct = r.get("diff_pct", None)

        status = "na"
        if label == "REVENUES":
            if diff_pct is not None:
                dp = float(diff_pct)
                status = "green" if dp >= 0 else ("yellow" if dp >= -0.005 else "red")
            item.update({"budget": budget, "actual": actual, "diff": diff, "diff_pct": diff_pct})
        else:
            delta_pp = actual_pct - budget_pct
            if label in ("COGS", "LABOUR COST"):
                status = "green" if delta_pp <= 0 else ("yellow" if delta_pp <= 0.005 else "red")
            elif label == "EBITDA":
                status = "green" if delta_pp >= 0 else ("yellow" if delta_pp >= -0.005 else "red")
            item.update({"budget_pct": budget_pct, "actual_pct": actual_pct, "delta_pp": delta_pp})
        item["status"] = status
        items.append(item)
    return items




@cruscotto_bp.get("/analisi-settimanale")
def analisi_settimanale():
    _ensure_session_keys()
    return render_template("cruscotto_analisi_settimanale.html")


@cruscotto_bp.get("/analisi-kpi-settimanale")
def analisi_kpi_settimanale():
    _ensure_session_keys()
    return render_template("cruscotto_analisi_kpi_settimanale.html")


@cruscotto_bp.get("/analisi-mensile")
def analisi_mensile():
    _ensure_session_keys()
    return render_template("cruscotto_analisi_mensile.html")


@cruscotto_bp.get("/pnl-store")
def pnl_store():
    _ensure_session_keys()
    today = date.today()
    return render_template(
        "cruscotto_pnl_store.html",
        default_year=today.year,
        default_month=today.month,
        is_admin=_is_admin(),
    )


@cruscotto_bp.get("/pnl-store/visibilita")
def pnl_store_visibility():
    _ensure_session_keys()
    if not _is_admin():
        return redirect(url_for("cruscotto.pnl_store"))
    year = _int_arg("year", date.today().year)
    visible = list_visible_months(year, year)
    visible_months = {int(r["month"]) for r in visible}
    return render_template(
        "cruscotto_pnl_store_visibility.html",
        selected_year=year,
        years=maintenance_years(year),
        month_names=MONTH_NAMES,
        visible_months=visible_months,
    )


@cruscotto_bp.post("/pnl-store/visibilita")
def pnl_store_visibility_save():
    _ensure_session_keys()
    if not _is_admin():
        return redirect(url_for("cruscotto.pnl_store"))
    try:
        year = int(request.form.get("year") or date.today().year)
        months = [int(m) for m in request.form.getlist("months")]
        save_visible_months_for_year(year, months)
        flash("Visibilita P&L store aggiornata.", "success")
    except Exception as e:
        current_app.logger.exception("Errore salvataggio visibilita P&L store")
        flash(f"Errore salvataggio: {e}", "danger")
        year = date.today().year
    return redirect(url_for("cruscotto.pnl_store_visibility", year=year))


@cruscotto_bp.get("/api/pnl-store/options")
def api_pnl_store_options():
    try:
        stores = _allowed_stores_for_current_user()
        visible = list_visible_months()
        return jsonify({"ok": True, "stores": stores, "visible_months": visible, "is_admin": _is_admin()})
    except Exception as e:
        current_app.logger.exception("Errore api_pnl_store_options")
        return jsonify({"ok": False, "error": str(e)}), 500


@cruscotto_bp.get("/api/pnl-store")
def api_pnl_store():
    try:
        year = _int_arg("year", date.today().year)
        month_from = _int_arg("month_from", date.today().month)
        month_to = _int_arg("month_to", month_from)
        month_from = max(1, min(12, month_from))
        month_to = max(1, min(12, month_to))
        if month_from > month_to:
            month_from, month_to = month_to, month_from

        if not period_is_visible(year, month_from, month_to):
            return jsonify({"ok": False, "error": "Periodo non abilitato alla visualizzazione."}), 403

        allowed = _allowed_stores_for_current_user()
        allowed_map = {str(s["code"]): s for s in allowed if s.get("code")}
        requested = [
            s.strip()
            for s in str(request.args.get("stores") or request.args.get("store") or "").split(",")
            if s.strip()
        ]
        if requested:
            selected = [code for code in dict.fromkeys(requested) if code in allowed_map]
            denied = [code for code in requested if code not in allowed_map]
            if denied:
                return jsonify({"ok": False, "error": "Uno o piu store selezionati non sono disponibili per questo account."}), 403
        else:
            selected = list(allowed_map.keys())

        if not selected:
            return jsonify({"ok": False, "error": "Nessuno store disponibile per questo account."}), 400

        pnl = get_pnl(",".join(selected), year, month_from, month_to)
        full_rows = pnl.get("rows") or []
        pnl["rows"] = _rows_until_delivery_fees(full_rows)

        breakdown = []
        if len(selected) > 1:
            for code in selected:
                store_pnl = get_pnl(code, year, month_from, month_to)
                breakdown.append(
                    {
                        "store_code": code,
                        "store_name": allowed_map.get(code, {}).get("name") or code,
                        "items": _build_kpi_items_from_pnl(store_pnl),
                    }
                )

        return jsonify(
            {
                "ok": True,
                "store_code": pnl.get("store_code"),
                "stores": selected,
                "store_labels": [allowed_map.get(code, {"code": code, "name": code}) for code in selected],
                "year": pnl.get("year"),
                "month_from": pnl.get("month_from"),
                "month_to": pnl.get("month_to"),
                "months_label": pnl.get("months_label"),
                "rows": pnl.get("rows") or [],
                "kpi_items": _build_kpi_items_from_pnl({"rows": full_rows}),
                "store_breakdown": breakdown,
            }
        )
    except Exception as e:
        current_app.logger.exception("Errore api_pnl_store")
        return jsonify({"ok": False, "error": str(e)}), 400




@cruscotto_bp.get("/api/delivery-options")
def api_delivery_options():
    store_code = session.get("store_code")
    if not store_code:
        return jsonify({"ok": False, "error": "Seleziona prima uno store."}), 400
    try:
        opt = get_elenchi_options(store_code=str(store_code))
        deliveries = [str(o.get("value") or "").strip() for o in (opt.get("deliveries") or []) if str(o.get("value") or "").strip()]
        return jsonify({"ok": True, "deliveries": deliveries})
    except Exception as e:
        current_app.logger.exception("Errore api_delivery_options")
        return jsonify({"ok": False, "error": str(e)}), 500


@cruscotto_bp.post("/api/weekly")
def api_weekly():
    store_code = session.get("store_code")
    if not store_code:
        return jsonify({"ok": False, "error": "Seleziona prima uno store."}), 400

    payload = request.get_json(silent=True) or {}
    week_start = _parse_week_start(str(payload.get("week_start") or ""))

    try:
        opt = get_elenchi_options(store_code=str(store_code))
        delivery_voci = [str(o.get("value") or "").strip() for o in (opt.get("deliveries") or []) if str(o.get("value") or "").strip()]
    except Exception:
        delivery_voci = []

    try:
        data = get_weekly_analysis(store_code=str(store_code), week_start=week_start, delivery_voci=delivery_voci)
        data["delivery_voci"] = delivery_voci
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        current_app.logger.exception("Errore api_weekly")
        return jsonify({"ok": False, "error": str(e)}), 500


@cruscotto_bp.post("/api/kpi-weekly")
def api_kpi_weekly():
    store_code = session.get("store_code")
    if not store_code:
        return jsonify({"ok": False, "error": "Seleziona prima uno store."}), 400

    payload = request.get_json(silent=True) or {}
    week_start = _parse_week_start(str(payload.get("week_start") or ""))

    try:
        data = get_weekly_kpi_overview(store_code=str(store_code), week_start=week_start)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        current_app.logger.exception("Errore api_kpi_weekly")
        return jsonify({"ok": False, "error": str(e)}), 500



@cruscotto_bp.post("/api/monthly")
def api_monthly():
    store_code = session.get("store_code")
    if not store_code:
        return jsonify({"ok": False, "error": "Seleziona prima uno store."}), 400

    payload = request.get_json(silent=True) or {}
    month_start = _parse_month_start(str(payload.get("month_start") or ""))

    # Delivery voci (stessa logica del weekly; se non presenti le ignoriamo)
    try:
        opt = get_elenchi_options(store_code=str(store_code))
        delivery_voci = [str(o.get("value") or "").strip() for o in (opt.get("deliveries") or []) if str(o.get("value") or "").strip()]
    except Exception:
        delivery_voci = []

    try:
        data = get_monthly_analysis(store_code=str(store_code), month_start=month_start, delivery_voci=delivery_voci)
        data["delivery_voci"] = delivery_voci
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        current_app.logger.exception("Errore api_monthly")
        return jsonify({"ok": False, "error": str(e)}), 500


@cruscotto_bp.get("/api/kpi-note")
def api_kpi_note_get():
    store_code = session.get("store_code")
    if not store_code:
        return jsonify({"ok": False, "error": "Seleziona prima uno store."}), 400

    week_start = _parse_week_start(str(request.args.get("week_start") or ""))
    try:
        note = get_kpi_note(store_code=str(store_code), period_type="W", period_start=week_start)
        return jsonify({"ok": True, "note": note})
    except Exception as e:
        current_app.logger.exception("Errore api_kpi_note_get")
        return jsonify({"ok": False, "error": str(e)}), 500


@cruscotto_bp.post("/api/kpi-note")
def api_kpi_note_save():
    store_code = session.get("store_code")
    if not store_code:
        return jsonify({"ok": False, "error": "Seleziona prima uno store."}), 400

    payload = request.get_json(silent=True) or {}
    week_start = _parse_week_start(str(payload.get("week_start") or ""))
    text = str(payload.get("text") or "")
    try:
        note = upsert_kpi_note(store_code=str(store_code), period_type="W", period_start=week_start, text=text)
        return jsonify({"ok": True, "note": note})
    except Exception as e:
        current_app.logger.exception("Errore api_kpi_note_save")
        return jsonify({"ok": False, "error": str(e)}), 500
