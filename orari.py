from __future__ import annotations

import re
import time

from datetime import date as _date, datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, jsonify, Response

from staff_repository import list_staff, insert_staff, update_staff, delete_staff, set_staff_active
from orari_repository import list_turni_week, save_turni_week, list_compiled_nominativi_week, overwrite_week_from_week, update_turni_inquadramento_by_nominativo, relink_turni_staff_identity
from orari_visibility_repository import get_visible_people_week, save_visible_people_week
from sales_repository import list_sales_week, save_sales_week
from dati_database_repository import list_fatturato_lordo_range
from legenda_repository import list_legenda, insert_legenda, delete_legenda
from db_integration import get_warehouse_stores
from orari_config_repository import list_orari_causali, list_orari_inquadramenti, orari_config_for_frontend


# Deve coincidere con i colori selezionabili nella pagina Orari (quadrati prestabiliti)
ORARI_COLOR_SWATCHES = [
    "#f8f9fa",
    "#e9ecef",
    "#fff3cd",
    "#d1e7dd",
    "#cff4fc",
    "#f8d7da",
    "#ffe5d0",
    "#e2d9f3",
    "#d2f4ea",
    "#f7d6e6",
]


orari_bp = Blueprint("orari", __name__, url_prefix="/orari")
_ORARI_TTL_CACHE: dict[str, dict] = {}


def _orari_ttl_cached(key: str, ttl_seconds: int, loader):
    now = time.time()
    cached = _ORARI_TTL_CACHE.get(key)
    if isinstance(cached, dict) and (now - float(cached.get("ts") or 0)) < ttl_seconds:
        return cached.get("value")
    value = loader()
    _ORARI_TTL_CACHE[key] = {"ts": now, "value": value}
    return value


def _orari_tenant_key() -> str:
    return str(session.get("tenant_key") or "default").strip() or "default"


def _orari_config_for_frontend_cached() -> dict:
    tenant_key = _orari_tenant_key()
    return _orari_ttl_cached(
        f"orari_frontend_cfg:{tenant_key}",
        300,
        lambda: orari_config_for_frontend(tenant_key),
    ) or {"inquadramenti": [], "causali": [], "inquadramenti_by_key": {}, "causali_by_key": {}}


def _list_orari_inquadramenti_cached(*, active_only: bool = True) -> list[dict]:
    tenant_key = _orari_tenant_key()
    return _orari_ttl_cached(
        f"orari_inquadramenti:{tenant_key}:{1 if active_only else 0}",
        300,
        lambda: list_orari_inquadramenti(tenant_key, active_only=active_only),
    ) or []


def _ensure_session_keys() -> None:
    session.setdefault("store_code", None)
    session.setdefault("store_name", None)


def _require_login():
    if not session.get("uid"):
        nxt = request.full_path if request.query_string else request.path
        return redirect(url_for("login", next=nxt))
    return None


def _require_store():
    store_code = session.get("store_code")
    if store_code:
        return None
    nxt = request.full_path if request.query_string else request.path
    flash("Seleziona prima uno store.", "warning")
    return redirect(url_for("warehouse.select_store", next=nxt))


def _is_scheduling_enabled_for_tenant() -> bool:
    try:
        from tenant_config_repository import current_tenant_key, get_tenant

        tenant_key = str(session.get("tenant_key") or current_tenant_key()).strip() or current_tenant_key()
        tenant = get_tenant(tenant_key)
        return bool(tenant.get("scheduling_enabled"))
    except Exception:
        current_app.logger.exception("Errore lettura flag scheduling tenant")
        return False


def _monday(d: _date) -> _date:
    return d - timedelta(days=d.weekday())


def _aligned_prev_year_same_weekday(d: _date) -> _date:
    y = d.year - 1
    m = d.month
    day = d.day
    try:
        base = _date(y, m, day)
    except Exception:
        # Gestione giorni non presenti (es. 29/02)
        if m == 2:
            base = _date(y, 2, 28)
        else:
            base = _date(y, m, 1) + timedelta(days=31)
            base = _date(base.year, base.month, 1) - timedelta(days=1)
    delta = (d.weekday() - base.weekday()) % 7
    if delta > 3:
        delta -= 7
    return base + timedelta(days=delta)


def _easter_sunday(year: int) -> _date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return _date(year, month, day)


def _is_public_holiday_it(d: _date) -> bool:
    y = d.year
    fixed = {
        _date(y, 1, 1),
        _date(y, 1, 6),
        _date(y, 4, 25),
        _date(y, 5, 1),
        _date(y, 6, 2),
        _date(y, 8, 15),
        _date(y, 11, 1),
        _date(y, 12, 8),
        _date(y, 12, 25),
        _date(y, 12, 26),
    }
    easter = _easter_sunday(y)
    fixed.add(easter)
    fixed.add(easter + timedelta(days=1))
    return d in fixed


def _week_days(week_start: _date) -> list[dict]:
    names = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
    out = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        out.append(
            {
                "date": d.isoformat(),
                "dow": i,
                "name": names[i],
                "label": f"{names[i]} {d.day:02d}/{d.month:02d}",
                "is_holiday": bool(_is_public_holiday_it(d)),
                "is_sunday": d.weekday() == 6,
            }
        )
    return out


@orari_bp.route("/")
def home():
    _ensure_session_keys()
    r = _require_login()
    if r:
        return r
    return redirect(url_for("orari.anagrafica"))


@orari_bp.route("/anagrafica", methods=["GET", "POST"])
def anagrafica():
    _ensure_session_keys()
    r = _require_login()
    if r:
        return r
    r = _require_store()
    if r:
        return r

    store_code = session.get("store_code")

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()

        # Legenda colori (Gestione Orari)
        if action in ("legend_create", "legend_delete"):
            try:
                if action == "legend_create":
                    nomelegenda = (request.form.get("nomelegenda") or "").strip()
                    colorelegenda = (request.form.get("colorelegenda") or "").strip()
                    if not nomelegenda or not colorelegenda:
                        flash("Compila nome e colore della legenda.", "warning")
                    else:
                        ok = insert_legenda(store_code=str(store_code), nomelegenda=nomelegenda, colorelegenda=colorelegenda)
                        flash("Legenda salvata." if ok else "Colore non valido: seleziona uno dei colori disponibili.", "success" if ok else "warning")
                else:
                    orig_nome = (request.form.get("orig_nomelegenda") or "").strip()
                    orig_col = (request.form.get("orig_colorelegenda") or "").strip()
                    ok = delete_legenda(store_code=str(store_code), nomelegenda=orig_nome, colorelegenda=orig_col)
                    flash("Legenda eliminata." if ok else "Nessuna legenda eliminata.", "success")
            except Exception:
                current_app.logger.exception("Errore salvataggio legenda")
                flash("Errore durante il salvataggio della legenda.", "danger")
            return redirect(url_for("orari.anagrafica"))

        nome_cognome = (request.form.get("nome_cognome") or "").strip()
        ruolo = (request.form.get("ruolo") or "").strip()
        ore = (request.form.get("ore_contrattuali") or "").strip()

        scheduling_enabled = _is_scheduling_enabled_for_tenant()
        codice_dipendente = (request.form.get("codice_dipendente") or "").strip() if scheduling_enabled else ""
        orig_ruolo = (request.form.get("orig_ruolo") or "").strip()
        retro_raw = (request.form.get("retroactive_inquadramento") or "").strip().lower()
        retroactive_inquadramento = retro_raw in ("1", "true", "yes", "y", "on")
        scheduling_raw = (request.form.get("scheduling") or "").strip().lower()
        scheduling = (scheduling_raw in ("1", "true", "yes", "y", "on")) if scheduling_enabled else False
        try:
            ore_i = int(ore)
        except Exception:
            ore_i = 0
        ore_i = max(1, min(40, ore_i)) if ore_i else 0

        try:
            if action in ("delete", "deactivate", "activate"):
                staff_id = request.form.get("staff_id")
                orig_nome = (request.form.get("orig_nome_cognome") or "").strip()
                if action == "delete":
                    ok = delete_staff(store_code=str(store_code), staff_id=staff_id, orig_nome_cognome=orig_nome)
                    flash("Persona eliminata." if ok else "Nessuna riga eliminata.", "success")
                elif action == "deactivate":
                    ok = set_staff_active(store_code=str(store_code), staff_id=staff_id, orig_nome_cognome=orig_nome, active=False)
                    flash("Persona disattivata." if ok else "Nessuna riga aggiornata.", "success")
                else:
                    ok = set_staff_active(store_code=str(store_code), staff_id=staff_id, orig_nome_cognome=orig_nome, active=True)
                    flash("Persona riattivata." if ok else "Nessuna riga aggiornata.", "success")

            elif action == "update":
                staff_id = request.form.get("staff_id")
                orig_nome = (request.form.get("orig_nome_cognome") or "").strip()
                if not nome_cognome or not ruolo or not ore_i:
                    flash("Compila tutti i campi.", "warning")
                else:
                    ok = update_staff(
                        store_code=str(store_code),
                        staff_id=staff_id,
                        orig_nome_cognome=orig_nome,
                        nome_cognome=nome_cognome,
                        ruolo=ruolo,
                        ore_contrattuali=ore_i,
                        codice_dipendente=codice_dipendente,
                        scheduling=scheduling,
                    )
                    rename_changed = str(orig_nome or "").strip().lower() and (str(orig_nome or "").strip().lower() != str(nome_cognome or "").strip().lower())
                    if ok:
                        try:
                            relink_turni_staff_identity(
                                store_code=str(store_code),
                                staff_id=staff_id,
                                old_nominativo=orig_nome,
                                new_nominativo=nome_cognome,
                                inquadramento=ruolo,
                            )
                        except Exception:
                            current_app.logger.exception("Errore riallineamento anagrafica/orari")
                    ruolo_changed = str(orig_ruolo or "").strip().lower() and (str(orig_ruolo or "").strip().lower() != str(ruolo or "").strip().lower())
                    retro_msg = ""
                    if ok and ruolo_changed and retroactive_inquadramento:
                        try:
                            rr = update_turni_inquadramento_by_nominativo(
                                store_code=str(store_code),
                                nominativo=nome_cognome,
                                inquadramento=ruolo,
                            )
                            retro_n = int((rr or {}).get("updated") or 0)
                            retro_msg = f" Aggiornati {retro_n} record orari (retroattivo)."
                        except Exception:
                            current_app.logger.exception("Errore aggiornamento retroattivo inquadramento orari")
                            retro_msg = " Anagrafica salvata, ma non è stato possibile aggiornare retroattivamente gli orari."
                    elif ok and ruolo_changed and not retroactive_inquadramento:
                        retro_msg = " La modifica dell'inquadramento varrà dai prossimi salvataggi orari."
                    if ok and rename_changed and not retro_msg:
                        retro_msg = " Il nominativo e' stato riallineato anche sugli orari gia' salvati."
                    flash(("Anagrafica aggiornata." + retro_msg) if ok else "Nessuna riga aggiornata.", "success")
            else:
                if not nome_cognome or not ruolo or not ore_i:
                    flash("Compila tutti i campi.", "warning")
                else:
                    insert_staff(
                        store_code=str(store_code),
                        nome_cognome=nome_cognome,
                        ruolo=ruolo,
                        ore_contrattuali=ore_i,
                        codice_dipendente=codice_dipendente,
                        scheduling=scheduling,
                    )
                    flash("Persona inserita.", "success")
        except Exception:
            current_app.logger.exception("Errore salvataggio staff")
            flash("Errore durante il salvataggio.", "danger")

        return redirect(url_for("orari.anagrafica"))

    # In Anagrafica mostriamo anche gli inattivi per permettere la riattivazione.
    staff = []
    try:
        staff = list_staff(store_code=str(store_code), only_active=False)
    except Exception:
        current_app.logger.exception("Errore list_staff")

    legenda = []
    try:
        legenda = list_legenda(store_code=str(store_code))
    except Exception:
        current_app.logger.exception("Errore list_legenda")

    inquadramenti = []
    try:
        inquadramenti = _list_orari_inquadramenti_cached(active_only=True)
    except Exception:
        current_app.logger.exception("Errore list_orari_inquadramenti")

    return render_template(
        "orari_anagrafica.html",
        staff=staff,
        legenda=legenda,
        color_swatches=ORARI_COLOR_SWATCHES,
        scheduling_enabled=_is_scheduling_enabled_for_tenant(),
        inquadramenti=inquadramenti,
    )


@orari_bp.route("/orari", methods=["GET"])
def orari_page():
    _ensure_session_keys()
    r = _require_login()
    if r:
        return r
    r = _require_store()
    if r:
        return r

    store_code = session.get("store_code")

    staff = []
    try:
        staff = list_staff(store_code=str(store_code), only_active=True)
    except Exception:
        current_app.logger.exception("Errore list_staff")

    today = _date.today()
    week_start = _monday(today)
    legenda = []
    try:
        legenda = list_legenda(store_code=str(store_code))
    except Exception:
        current_app.logger.exception("Errore list_legenda")

    orari_config = {"inquadramenti": [], "causali": [], "inquadramenti_by_key": {}, "causali_by_key": {}}
    try:
        orari_config = _orari_config_for_frontend_cached()
    except Exception:
        current_app.logger.exception("Errore orari_config_for_frontend")

    return render_template(
        "orari_orari.html",
        staff=staff,
        week_start=week_start.isoformat(),
        legenda=legenda,
        orari_config=orari_config,
    )




@orari_bp.get("/pdf")
def orari_pdf():
    _ensure_session_keys()
    r = _require_login()
    if r:
        return r
    r = _require_store()
    if r:
        return r

    store_code = session.get("store_code")
    week_start_s = (request.args.get("week_start") or "").strip()
    noms_s = (request.args.get("n") or "").strip()

    try:
        week_start = _date.fromisoformat(week_start_s) if week_start_s else _monday(_date.today())
    except Exception:
        week_start = _monday(_date.today())

    week_end = week_start + timedelta(days=6)

    nominativi: list[str] = []
    if noms_s:
        nominativi = [x.strip() for x in noms_s.split("||") if x.strip()]

    if not nominativi:
        nominativi = get_visible_people_week(store_code=str(store_code), week_start=week_start)
    if not nominativi:
        nominativi = list_compiled_nominativi_week(store_code=str(store_code), start_day=week_start, end_day=week_end)
        try:
            active_set = {str(s.get("nome_cognome") or "").strip() for s in list_staff(store_code=str(store_code), only_active=True)}
            nominativi = [n for n in nominativi if n in active_set]
        except Exception:
            nominativi = []

    turni = list_turni_week(store_code=str(store_code), start_day=week_start, end_day=week_end, nominativi=nominativi)
    sales = list_sales_week(store_code=str(store_code), start_day=week_start, end_day=week_end)

    prev_out: dict[str, dict] = {}
    try:
        aligned_days: list[_date] = []
        for i in range(7):
            d0 = week_start + timedelta(days=i)
            aligned_days.append(_aligned_prev_year_same_weekday(d0))

        lordo_map = list_fatturato_lordo_range(
            store_code=str(store_code),
            start_day=min(aligned_days),
            end_day=max(aligned_days),
        )

        for i in range(7):
            d0 = week_start + timedelta(days=i)
            a = _aligned_prev_year_same_weekday(d0)
            lordo = lordo_map.get(a.isoformat())
            netto = (float(lordo) / 1.1) if lordo is not None else None
            prev_out[d0.isoformat()] = {
                "aligned_date": a.isoformat(),
                "net": round(netto, 2) if netto is not None else None,
            }
    except Exception:
        prev_out = {}

    try:
        from orari_pdf import build_orari_pdf
    except ModuleNotFoundError:
        return render_template("orari_pdf_missing.html")
    except ImportError:
        return render_template("orari_pdf_missing.html")

    # Mappa ore contrattuali per nominativo (per replicare i totali sotto il nome nella UI)
    staff_map = {}
    try:
        staff_all = list_staff(store_code=str(store_code), only_active=False)
        for s in staff_all or []:
            n = str((s or {}).get('nome_cognome') or '').strip()
            if not n:
                continue
            try:
                staff_map[n] = int((s or {}).get('ore_contrattuali') or 0)
            except Exception:
                staff_map[n] = 0
    except Exception:
        staff_map = {}

    legenda = []
    try:
        legenda = list_legenda(store_code=str(store_code))
    except Exception:
        legenda = []

    pdf_bytes = build_orari_pdf(
        site=str(store_code),
        week_start=week_start,
        nominativi=nominativi,
        turni=turni,
        staff_map=staff_map,
        sales=sales or {},
        prev_year=prev_out or {},
        legenda=legenda,
    )

    filename = f"Orari_{store_code}_{week_start.isoformat()}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@orari_bp.get("/api/staff")
def api_staff():
    _ensure_session_keys()
    r = _require_login()
    if r:
        return jsonify({"ok": False, "error": "login"}), 401
    store_code = session.get("store_code")
    if not store_code:
        return jsonify({"ok": False, "error": "store"}), 400
    try:
        staff = list_staff(store_code=str(store_code), only_active=True)
        return jsonify({"ok": True, "staff": staff})
    except Exception as e:
        current_app.logger.exception("Errore api_staff")
        return jsonify({"ok": False, "error": str(e)}), 500

@orari_bp.get("/api/stores-all")
def api_stores_all():
    """Elenco store COMPLETO (non filtrato per utente).

    Usato nella pagina Orari per selezionare la destinazione dei prestiti.
    """
    _ensure_session_keys()
    r = _require_login()
    if r:
        return jsonify({"ok": False, "error": "login"}), 401
    try:
        stores = get_warehouse_stores() or []
        out = []
        for s in stores:
            if isinstance(s, str):
                code = s.strip()
                name = code
            else:
                code = str((s or {}).get("code") or (s or {}).get("site") or "").strip()
                name = str((s or {}).get("name") or (s or {}).get("label") or code).strip()
            if not code:
                continue
            out.append({"code": code, "name": name})

        # Ordina per nome (fallback code)
        try:
            out = sorted(out, key=lambda x: (str(x.get("name") or "").lower(), str(x.get("code") or "").lower()))
        except Exception:
            pass

        return jsonify({"stores": out})
    except Exception as e:
        current_app.logger.exception("Errore api_stores_all")
        return jsonify({"stores": [], "error": str(e)}), 500



@orari_bp.post("/api/orari/week")
def api_week():
    _ensure_session_keys()
    r = _require_login()
    if r:
        return jsonify({"ok": False, "error": "login"}), 401
    store_code = session.get("store_code")
    if not store_code:
        return jsonify({"ok": False, "error": "store"}), 400

    payload = request.get_json(silent=True) or {}
    week_start_s = str(payload.get("week_start") or "").strip()
    noms = payload.get("nominativi") or []
    try:
        week_start = _date.fromisoformat(week_start_s) if week_start_s else _monday(_date.today())
    except Exception:
        week_start = _monday(_date.today())
    week_start = _monday(week_start)
    week_end = week_start + timedelta(days=6)
    auto_selected = []
    try:
        noms = [str(x).strip() for x in (noms or []) if str(x).strip()]
    except Exception:
        noms = []
    if not noms:
        auto_selected = get_visible_people_week(store_code=str(store_code), week_start=week_start)
        if not auto_selected:
            auto_selected = list_compiled_nominativi_week(store_code=str(store_code), start_day=week_start, end_day=week_end)
        try:
            active_set = {str(s.get('nome_cognome') or '').strip() for s in list_staff(store_code=str(store_code), only_active=True)}
            auto_selected = [n for n in auto_selected if n in active_set]
        except Exception:
            auto_selected = []
        noms = auto_selected


    try:
        turni = list_turni_week(
            store_code=str(store_code),
            start_day=week_start,
            end_day=week_end,
            nominativi=noms,
        )
        sales = list_sales_week(store_code=str(store_code), start_day=week_start, end_day=week_end)
        prev_out = {}
        try:
            aligned = []
            for i in range(7):
                d = week_start + timedelta(days=i)
                aligned.append(_aligned_prev_year_same_weekday(d))
            if aligned:
                min_d = min(aligned)
                max_d = max(aligned)
                lordo_map = list_fatturato_lordo_range(store_code=str(store_code), start_day=min_d, end_day=max_d)
            else:
                lordo_map = {}

            for i in range(7):
                d = week_start + timedelta(days=i)
                a = _aligned_prev_year_same_weekday(d)
                lordo = lordo_map.get(a.isoformat())
                netto = (float(lordo) / 1.1) if lordo is not None else None
                prev_out[d.isoformat()] = {
                    "aligned_date": a.isoformat(),
                    "net": round(netto, 2) if netto is not None else None,
                }
        except Exception:
            current_app.logger.exception("Errore calcolo prev_year")
            prev_out = {}

        return jsonify({"ok": True, "week_start": week_start.isoformat(), "days": _week_days(week_start), "turni": turni, "sales": sales, "prev_year": prev_out, "auto_selected": auto_selected})
    except Exception as e:
        current_app.logger.exception("Errore api_week")
        return jsonify({"ok": False, "error": str(e)}), 500


@orari_bp.post("/api/orari/overwrite-week")
def api_overwrite_week():
    _ensure_session_keys()
    r = _require_login()
    if r:
        return jsonify({"ok": False, "error": "login"}), 401
    store_code = session.get("store_code")
    if not store_code:
        return jsonify({"ok": False, "error": "store"}), 400

    payload = request.get_json(silent=True) or {}
    src_s = str(payload.get("source_week_start") or "").strip()
    tgt_s = str(payload.get("target_week_start") or "").strip()
    if not src_s or not tgt_s:
        return jsonify({"ok": False, "error": "missing_params"}), 400

    try:
        src_d = _date.fromisoformat(src_s)
        tgt_d = _date.fromisoformat(tgt_s)
    except Exception:
        return jsonify({"ok": False, "error": "bad_date"}), 400

    try:
        res = overwrite_week_from_week(
            store_code=str(store_code),
            source_week_start=_monday(src_d),
            target_week_start=_monday(tgt_d),
        )
        return jsonify(res)
    except Exception as e:
        current_app.logger.exception("Errore api_overwrite_week")
        return jsonify({"ok": False, "error": str(e)}), 500


@orari_bp.post("/api/orari/save")
def api_save():
    _ensure_session_keys()
    r = _require_login()
    if r:
        return jsonify({"ok": False, "error": "login"}), 401
    store_code = session.get("store_code")
    if not store_code:
        return jsonify({"ok": False, "error": "store"}), 400

    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows") or []

    sales_payload = payload.get("sales")
    week_start_s = str(payload.get("week_start") or "").strip()
    visible_nominativi = payload.get("visible_nominativi") or []

    def _parse_money(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if not s:
            return None
        s = s.replace('€', '').replace(' ', '')
        if ',' in s and '.' in s:
            s = s.replace('.', '').replace(',', '.')
        elif ',' in s:
            s = s.replace(',', '.')
        try:
            return float(s)
        except Exception:
            return None

    def _t2m(s: str | None):
        s = (s or "").strip()
        if not s:
            return None
        m = re.match(r"^(\d{1,2}):(\d{2})$", s)
        if not m:
            return None
        h = int(m.group(1))
        mi = int(m.group(2))
        if h < 0 or h > 23 or mi < 0 or mi > 59:
            return None
        return h * 60 + mi

    def _validate_shift(inizio: str | None, fine: str | None):
        a = _t2m(inizio)
        b = _t2m(fine)
        # Se uno dei due manca, non blocchiamo (utente può completare dopo)
        if a is None or b is None:
            return None
        if b >= a:
            return None
        # attraversamento mezzanotte consentito solo per turni che iniziano nel pomeriggio/sera
        # e finiscono al mattino presto (es: 22:00-01:00)
        if a >= 12 * 60 and b <= 8 * 60:
            return None
        return "La fine non può essere prima dell'inizio."

    # Validazioni richieste dal layout
    errors: list[str] = []
    try:
        causali_rules = {
            str((r or {}).get("name") or "").strip().lower(): dict(r or {})
            for r in list_orari_causali(active_only=True)
        }
    except Exception:
        causali_rules = {}

    def _causale_rule(value: str) -> dict:
        return causali_rules.get(str(value or "").strip().lower()) or {}

    for row in rows:
        nom = str(row.get("nominativo") or "").strip()
        data = str(row.get("data") or "").strip()

        caus1 = str(row.get("causale") or "").strip()
        s_prestito1 = str(row.get("S_Prestito") or row.get("s_prestito") or "").strip()

        caus2 = str(row.get("causale2") or "").strip()
        s_prestito2 = str(row.get("S_Prestito2") or row.get("s_prestito2") or "").strip()

        r1 = _causale_rule(caus1)
        r2 = _causale_rule(caus2)
        if bool(r1.get("requires_loan_store", str(caus1).strip().lower() == "prestito")) and not s_prestito1:
            errors.append(f"{nom} {data}: Turno 1 - seleziona lo store per Prestito.")
        if bool(r2.get("requires_loan_store", str(caus2).strip().lower() == "prestito")) and not s_prestito2:
            errors.append(f"{nom} {data}: Turno 2 - seleziona lo store per Prestito.")

        in1 = str(row.get("inizio_1") or "").strip()
        fi1 = str(row.get("fine_1") or "").strip()
        in2 = str(row.get("inizio_2") or "").strip()
        fi2 = str(row.get("fine_2") or "").strip()

        if bool(r1.get("requires_time_range", str(caus1).strip().lower() == "riposo festivo")) and not (in1 and fi1):
            errors.append(f"{nom} {data}: Turno 1 - Riposo Festivo richiede un turno associato.")
        if bool(r2.get("requires_time_range", str(caus2).strip().lower() == "riposo festivo")) and not (in2 and fi2):
            errors.append(f"{nom} {data}: Turno 2 - Riposo Festivo richiede un turno associato.")

        e1 = _validate_shift(row.get("inizio_1"), row.get("fine_1"))
        if e1:
            errors.append(f"{nom} {data}: Turno 1 - {e1}")
        e2 = _validate_shift(row.get("inizio_2"), row.get("fine_2"))
        if e2:
            errors.append(f"{nom} {data}: Turno 2 - {e2}")
    # Previsioni vendite obbligatorie (una per giorno)
    try:
        ws = _date.fromisoformat(week_start_s) if week_start_s else _monday(_date.today())
    except Exception:
        ws = _monday(_date.today())
    ws = _monday(ws)
    required_dates = [(ws + timedelta(days=i)).isoformat() for i in range(7)]
    try:
        visible_nominativi = [str(x or "").strip() for x in (visible_nominativi or []) if str(x or "").strip()]
    except Exception:
        visible_nominativi = []

    sales_map = {}
    if isinstance(sales_payload, dict):
        for k, v in sales_payload.items():
            sales_map[str(k)] = v
    elif isinstance(sales_payload, list):
        for it in sales_payload:
            if isinstance(it, dict):
                di = str(it.get('date') or it.get('data') or '').strip()
                if di:
                    sales_map[di] = it.get('sales')

    sales_out = {}
    for d_iso in required_dates:
        val = _parse_money(sales_map.get(d_iso))
        if val is None:
            errors.append(f"{d_iso}: previsione vendite obbligatoria.")
        else:
            sales_out[d_iso] = val


    if errors:
        return jsonify({"ok": False, "error": "validation", "details": errors[:50]}), 400
    try:
        # Salva previsioni vendite
        save_sales_week(store_code=str(store_code), sales_by_day=sales_out)
        if visible_nominativi:
            save_visible_people_week(store_code=str(store_code), week_start=ws, names=visible_nominativi)
        # Aggiungi inquadramento a ogni riga (derivato da STAFF.Ruolo)
        try:
            staff_list = list_staff(store_code=str(store_code))
            _m = {}
            _id_m = {}
            for s in (staff_list or []):
                n = str(s.get('nome_cognome') or '').strip().lower()
                sid = str(s.get('id') or '').strip()
                ruolo = str(s.get('ruolo') or '').strip()
                if n:
                    _m[n] = ruolo
                if sid:
                    _id_m[sid] = ruolo
            for r0 in (rows or []):
                sid = str(r0.get('staff_id') or '').strip()
                nn = str(r0.get('nominativo') or '').strip().lower()
                r0['inquadramento'] = _id_m.get(sid) or _m.get(nn, '')
        except Exception:
            # Non blocchiamo il salvataggio se l'inquadramento non è recuperabile
            pass

        res = save_turni_week(store_code=str(store_code), rows=rows)
        res['sales_saved'] = len(sales_out)
        return jsonify(res)
    except Exception as e:
        current_app.logger.exception("Errore api_save")
        return jsonify({"ok": False, "error": str(e)}), 500
