import os
from contextlib import nullcontext

# Carica .env PRIMA di importare moduli che leggono variabili d'ambiente a import-time
# (in particolare db_integration). Con `flask run` questo avviene anche via Flask CLI,
# ma con Waitress/WSGI no, e senza questa precauzione le chiavi Supabase (SERVICE_ROLE)
# risultano vuote e l'elenco store non si carica.
from dotenv import load_dotenv, find_dotenv

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DOTENV_FILE = os.getenv("ENV_FILE") or os.path.join(_BASE_DIR, ".env")
if os.path.exists(_DOTENV_FILE):
    load_dotenv(_DOTENV_FILE, override=True)
else:
    load_dotenv(find_dotenv(".env", usecwd=True), override=True)

from flask import request, jsonify, session, current_app, abort, Response
import time
from ui_enrichment import enrich_reviews_for_template
from urllib.parse import unquote
from db_integration import (
    reviews_for_ui,
    media_for_ui,
    on_review_reply_saved,
    on_media_deleted,
    on_media_uploaded,
    delete_warehouse_user_store_assignments_for_store,
    delete_warehouse_store,
    get_warehouse_stores,
    get_user_warehouse_stores,
    set_user_warehouse_stores,
    get_all_warehouse_user_stores,
    upsert_user_store_assignment,
    delete_user_store_assignment,
    upsert_warehouse_store,
)
from listini_repository import load_admin_pricelist, apply_pricelist_to_stores, parse_pricelist_csv
from supplier_orders_repository import (
    ensure_supplier_orders_schema,
    migrate_legacy_pricelists,
    ensure_default_price_list_assignments,
    list_listino_types,
    list_fornitori as list_supplier_fornitori,
    load_pricelist as load_unified_pricelist,
    upsert_pricelist_rows,
    copy_price_list_products,
    assign_default_price_list_to_store,
    remove_price_list_store_assignment,
)
from supplier_order_flow_repository import (
    ensure_supplier_order_flow_schema,
    get_supplier_user_assignments,
    replace_supplier_user_assignments,
)
from admin_labor_cost_repository import (
    get_setup_overview as labor_cost_get_setup_overview,
    list_company_extras as labor_cost_list_company_extras,
    list_company_profiles as labor_cost_list_company_profiles,
    get_company_profile as labor_cost_get_company_profile,
    save_company_profile as labor_cost_save_company_profile,
    list_company_role_rates as labor_cost_list_company_role_rates,
    replace_company_extras as labor_cost_replace_company_extras,
    list_employee_configs as labor_cost_list_employee_configs,
    upsert_employee_config as labor_cost_upsert_employee_config,
    import_employee_configs_csv as labor_cost_import_employee_configs_csv,
    projection_test as labor_cost_projection_test,
)
from app_db import get_connection, get_storehub_database_name, storehub_database_context
from warehouse import warehouse_bp
from rendiconto import rendiconto_bp
from orari import orari_bp
from controlli import controlli_bp
from links import links_bp

from estrazioni import estrazioni_bp


from cruscotto import cruscotto_bp
from mbo import mbo_bp
import os, io, time, logging, json, re, csv, tempfile
from typing import Any, Dict, Optional
import threading
from uuid import uuid4
from functools import wraps
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from itertools import combinations
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort, flash, send_from_directory
from markupsafe import escape
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from werkzeug.middleware.proxy_fix import ProxyFix
from ai_assistant_service import ask_storehub_assistant, openai_is_configured
from versamenti_repository import search_versamenti_range_multi
from admin_versamenti_finance_match_repository import (
    build_app_record_key,
    get_matches_for_app_keys,
    list_finance_versamenti,
    replace_app_matches,
)
from admin_pos_finance_match_repository import (
    build_app_record_key as build_pos_app_record_key,
    build_finance_row_uid as build_pos_finance_row_uid,
    get_matches_for_app_keys as get_pos_matches_for_app_keys,
    list_finance_pos_rows,
    replace_app_matches as replace_pos_app_matches,
)
from finance_store_mapping_repository import (
    delete_code_assignment,
    get_assignments_by_code,
    get_code_catalog_map,
    import_code_catalog_rows,
    list_code_assignments,
    list_code_catalog,
    parse_code_catalog_text,
    save_code_assignment,
    update_code_assignment,
)
from primanota_repository import load_primanota_month_agg
from daily_sales_repository import (
    historical_daily_sales_import_targets,
    upsert_historical_daily_sales_from_csv_row,
)
from controller_monitoring import register_controller_monitoring

APP_BUILD_VERSION = os.getenv("APP_VERSION") or "v2026.07.02.1"
ADMIN_USERS_UI_VERSION = APP_BUILD_VERSION


  


def _to_stars(val):
    if val is None:
        return 0
    if isinstance(val, int):
        return max(0, min(5, val))
    s = str(val).strip().upper()
    mapping = {"ONE":1, "TWO":2, "THREE":3, "FOUR":4, "FIVE":5}
    if s.isdigit():
        try:
            return max(0, min(5, int(s)))
        except Exception:
            return 0
    return mapping.get(s, 0)

# Imaging
try:
    from PIL import Image, ImageOps, UnidentifiedImageError
    _PIL_OK = True
except Exception:
    _PIL_OK = False
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
register_controller_monitoring(
    app,
    app_name="fp",
    version=str(os.getenv("APP_VERSION") or ADMIN_USERS_UI_VERSION),
)


@app.get('/service-worker.js')
def service_worker():
    response = send_from_directory(app.static_folder, 'service-worker.js', mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache'
    return response

app.register_blueprint(warehouse_bp)
app.register_blueprint(rendiconto_bp)
app.register_blueprint(orari_bp)

app.register_blueprint(controlli_bp)

app.register_blueprint(cruscotto_bp)
app.register_blueprint(links_bp)
app.register_blueprint(estrazioni_bp)
app.register_blueprint(mbo_bp)


# --- Module access guard (per profilo di visualizzazione) ---
# Usa un guard globale invece di before_request sui blueprint: evita errori Flask
# se i blueprint vengono registrati prima dei decorator.
@app.before_request
def _guard_modules_by_blueprint():
    bp = request.blueprint
    if not bp:
        return None
    supplier_order_endpoints = {
        "warehouse.fornitori",
        "warehouse.listini_anagrafica",
        "warehouse.listini_prezzi",
        "warehouse.supplier_orders_home",
        "warehouse.supplier_orders_send",
        "warehouse.supplier_orders_sent",
        "warehouse.supplier_orders_sent_to_ddt",
        "warehouse.supplier_orders_received",
        "warehouse.supplier_orders_received_complete",
        "warehouse.supplier_orders_received_line",
        "warehouse.supplier_orders_received_reopen",
        "warehouse.supplier_orders_received_add_product",
        "warehouse.supplier_order_pdf",
    }
    if request.endpoint in supplier_order_endpoints:
        return _enforce_module("supplier_orders")
    if request.endpoint in {
        "cruscotto.pnl_store",
        "cruscotto.api_pnl_store",
        "cruscotto.api_pnl_store_options",
    }:
        return _enforce_module("cruscotto_pnl_store")
    if request.endpoint in {"mbo.soft_skills_fill", "mbo.custom_survey_fill"}:
        return None
    if bp == "mbo":
        return _enforce_module("mbo")
    if bp == "estrazioni":
        hq_endpoints = {
            "estrazioni.scheduling",
            "estrazioni.verifica_rendiconto",
            "estrazioni.api_verifica_rendiconto",
            "estrazioni.api_verifica_rendiconto_save",
            "estrazioni.api_verifica_rendiconto_export_xlsx",
            "estrazioni.finance",
            "estrazioni.finance_xlsx",
            "admin_pos_finance_match_test",
            "admin_pos_finance_match_test_save",
        }
        return _enforce_module("estrazioni_hq" if request.endpoint in hq_endpoints else "estrazioni")
    mapping = {
        "warehouse": "magazzino",
        "rendiconto": "rendiconto",
        "orari": "orari",
        "controlli": "controlli",
        "cruscotto": "cruscotto",
        "links": "links",
    }
    module_key = mapping.get(bp)
    if module_key:
        return _enforce_module(module_key)
    return None

# ---- Security defaults ----
# Stronger secret key default for dev if no configured secret is available.
# Azure/App Service setups may use either FLASK_SECRET or FLASK_SECRET_KEY.
_flask_secret = os.getenv('FLASK_SECRET') or os.getenv('FLASK_SECRET_KEY')
if not _flask_secret:
    try:
        _flask_secret = os.urandom(32)
    except Exception:
        _flask_secret = 'change-me-please'
app.secret_key = _flask_secret

# Harden session cookie
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# In production behind HTTPS (Render), this should be True.
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', '1') == '1'
app.config['PREFERRED_URL_SCHEME'] = 'https'


def _is_truthy_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


@app.before_request
def _redirect_insecure_requests():
    """Avoid silent login loops when secure session cookies meet plain HTTP."""
    if not _is_truthy_env(os.getenv('FORCE_HTTPS'), default=True):
        return None
    if request.is_secure:
        return None
    host = (request.host or '').split(':', 1)[0].strip().lower()
    if host in {'127.0.0.1', 'localhost'}:
        return None
    forwarded_proto = (request.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip().lower()
    if forwarded_proto == 'https':
        return None
    secure_url = request.url.replace('http://', 'https://', 1)
    return redirect(secure_url, code=308)


# Google app.secret_key = os.getenv('FLASK_SECRET', 'secret')

# --- Session timeouts: 7 giorni inattivit?, 30 giorni assoluti ---
app.permanent_session_lifetime = timedelta(days=30)




# --- Legacy calendar / todo / outlook sync DISABILITATI ---
# Il progetto Magazzino non usa più queste pagine/API: blocchiamo le route legacy per poter cancellare i file.
DISABLE_LEGACY_CALENDAR_TODO_OUTLOOK = True
_LEGACY_BLOCK_PREFIXES = (
    "/calendar",
    "/api/calendar",
    "/todo-list",
    "/api/personal_todos",
    "/api/todo_catalog",
    "/api/place_catalog",
    "/sync-outlook",
    "/ms",
)

@app.before_request
def _block_legacy_calendar_todo_outlook():
    if not DISABLE_LEGACY_CALENDAR_TODO_OUTLOOK:
        return None
    path = request.path or ""
    if path.startswith("/static/"):
        return None
    # Consentiamo l'OAuth Microsoft (utile anche per test SharePoint) anche quando
    # le funzionalità legacy calendario/outlook sono disabilitate.
    if path.startswith("/ms/login") or path.startswith("/ms/callback"):
        return None
    for p in _LEGACY_BLOCK_PREFIXES:
        if path.startswith(p):
            return abort(404)
    return None

def _normalize_loc_name_to_location_id(value: str):
    """Converte 'accounts/.../locations/123' -> 'locations/123'. Lascia intatti già-normalizzati."""
    if not value:
        return None
    s = str(value)
    if s.startswith('locations/'):
        return s
    if 'locations/' in s:
        # prendi la parte dopo 'locations/'
        try:
            part = s.split('locations/', 1)[1].split('/')[0]
            if part:
                return f'locations/{part}'
        except Exception:
            pass
    return s  # fallback

def _fetch_allowed_locations_via_rls():
    """Legge da user_locations con RLS e supporta diversi nomi colonna (location_id, loc_name, location, name)."""
    if not session.get('sb_token'):
        return []
    candidates = ['location_id', 'loc_name', 'location', 'name']
    ul_url = f"{SUPABASE_URL}/rest/v1/user_locations"
    for col in candidates:
        params = {'select': col}
        resp = _session().get(ul_url, headers=_sb_headers_user(), params=params, timeout=20)
        if resp.status_code == 200:
            rows = resp.json() or []
            out = []
            for r in rows:
                v = r.get(col)
                v = _normalize_loc_name_to_location_id(v)
                if v:
                    out.append(v)
            # dedupe preservando ordine
            seen = set(); allowed = []
            for v in out:
                if v not in seen:
                    seen.add(v); allowed.append(v)
            return allowed
        # 400: column does not exist -> prova prossimo col
    return []


@app.before_request
def _enforce_timeouts():
    # Skip static and login endpoints
    if request.endpoint in ('static', 'login', 'login_post'):
        return
    uid = session.get('uid')
    if not uid:
        return
    now = int(time.time())
    login_at = session.get('login_at', now)
    last_seen = session.get('last_seen', now)
    if now - login_at > 30 * 24 * 3600:
        session.clear()
        flash("Sessione scaduta (30 giorni). Esegui di nuovo il login.", 'warning')
        return redirect(url_for('login'))
    if now - last_seen > 7 * 24 * 3600:
        session.clear()
        flash("Sessione scaduta per inattività. Accedi di nuovo.", 'warning')
        return redirect(url_for('login'))

    # Forza cambio password se scaduta (30 giorni)
    if session.get('pw_force_change'):
        ep = request.endpoint or ''
        allowed = {
            'static',
            'login', 'login_post',
            'logout',
            'account_change_password',
            'forgot_password', 'forgot_password_post',
            'reset_password', 'reset_password_post',
        }
        if ep not in allowed:
            return redirect(url_for('account_change_password'))

    session['last_seen'] = now


CLIENT_ID     = os.getenv('GMB_CLIENT_ID')
CLIENT_SECRET = os.getenv('GMB_CLIENT_SECRET')
REFRESH_TOKEN = os.getenv('GMB_REFRESH_TOKEN')
ACCOUNT_ID    = os.getenv('GMB_ACCOUNT_ID')

# ImgBB
IMGBB_API_KEY     = os.getenv('IMGBB_API_KEY', '').strip()
IMGBB_EXPIRATION  = int(os.getenv('IMGBB_EXPIRATION', '1200'))  # secondi

# Supabase (auth e permessi per-utente)
SUPABASE_URL      = os.getenv('SUPABASE_URL', '').rstrip('/')
SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY', '').strip()

SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()
try:
    from runtime_monitoring import init_runtime

    log = init_runtime(app, logger_name='app')
except Exception:
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('app')

# ----------------- HTTP session -----------------
def _session():
    s = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    s.mount('https://', HTTPAdapter(max_retries=retries))
    return s

def _raise_with_body(resp: requests.Response):
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        body = ''
        try:
            body = resp.text
        except Exception:
            pass
        raise requests.HTTPError(f'{e}\nResponse body: {body}') from e

def _extract_error_message_from_http_exc(exc: Exception) -> str:
    """Estrae un messaggio 'pulito' dagli errori HTTP con 'Response body:'.

    Nota: usato soprattutto per gli errori Supabase del login (password grant).
    """
    msg = str(exc) or ""
    body = ""
    if "Response body:" in msg:
        body = msg.split("Response body:", 1)[1].strip()
    # se il body è JSON, prova a prenderne un campo sensato
    if body:
        try:
            data = json.loads(body)
            for k in ("error_description", "message", "msg", "error"):
                v = data.get(k)
                if v:
                    return str(v)
        except Exception:
            pass
        # fallback: body testuale
        return body[:500]
    return msg[:500]


def _friendly_login_error(exc: Exception) -> str:
    """Mappa gli errori Supabase del login in messaggi utente-friendly (IT)."""
    raw = _extract_error_message_from_http_exc(exc)
    low = raw.lower()

    # Evita enumerazione account: stesso messaggio per user-not-found / password errata
    if "invalid login credentials" in low or "invalid_grant" in low or "user not found" in low:
        return "Email o password non corretti."

    if "email not confirmed" in low or "email_not_confirmed" in low:
        return "Email non confermata. Controlla la posta e conferma l’account."

    if "too many requests" in low or "rate limit" in low or "429" in low:
        return "Troppi tentativi. Riprova tra qualche minuto."

    if "invalid email" in low or "email address is invalid" in low:
        return "Email non valida."

    if isinstance(exc, requests.Timeout) or "timeout" in low:
        return "Servizio non raggiungibile. Riprova tra poco."

    # fallback generico (non mostrare errori grezzi Supabase)
    return "Accesso non riuscito. Verifica le credenziali e riprova."


# ----------------- Google OAuth2 -----------------
def get_access_token():
    r = _session().post(
        'https://oauth2.googleapis.com/token',
        data={
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN,
            'grant_type': 'refresh_token'
        },
        timeout=10
    )
    _raise_with_body(r)
    return r.json()['access_token']

def extract_loc_id(name: str) -> str:
    parts = name.split('/')
    return name if parts[0] == 'locations' else f"locations/{parts[-1]}"

# ----------------- Supabase helpers -----------------
def _sb_headers(token: str | None = None, is_json=True):
    h = {'apikey': SUPABASE_ANON_KEY}
    if is_json:
        h['Content-Type'] = 'application/json'
    if token:
        h['Authorization'] = f'Bearer {token}'
        h['Accept'] = 'application/json'
    return h


def _sb_headers_user(is_json=True):
    """Header RLS-aware: usa il JWT dell'utente loggato (Supabase applica le RLS)."""
    from flask import session
    h = {'apikey': SUPABASE_ANON_KEY}
    if is_json:
        h['Content-Type'] = 'application/json'
    token = session.get('sb_token')
    if token:
        h['Authorization'] = f'Bearer {token}'
    return h


def _sb_headers_admin():
    """Header con Service Role Key per chiamate admin."""
    h = {'apikey': SUPABASE_ANON_KEY, 'Content-Type': 'application/json'}
    if SUPABASE_SERVICE_ROLE_KEY:
        h['Authorization'] = f'Bearer {SUPABASE_SERVICE_ROLE_KEY}'
    return h


def sb_admin_set_password(uid: str, new_password: str):
    """Aggiorna la password di un utente come admin (GoTrue Admin) applicando policy e aggiornando metadati."""
    ok, msg = validate_password_policy(new_password)
    if not ok:
        raise ValueError(msg)

    # recupera metadata esistenti per confronto "non uguale" e merge
    try:
        u = sb_admin_get_auth_user(uid) or {}
    except Exception:
        u = {}
    meta = (u.get("user_metadata") or {}) if isinstance(u, dict) else {}
    prev_hash = meta.get("pw_prev_hash")
    if prev_hash:
        if _pw_fingerprint(str(uid), new_password) == str(prev_hash):
            raise ValueError("La nuova password non può essere uguale alla precedente.")

    merged = dict(meta) if isinstance(meta, dict) else {}
    now_dt = datetime.now(timezone.utc)
    merged["pw_prev_hash"] = _pw_fingerprint(str(uid), new_password)
    merged["pw_changed_at"] = _dt_to_iso_utc(now_dt)

    url = f"{SUPABASE_URL}/auth/v1/admin/users/{uid}"
    payload = {"password": new_password, "user_metadata": merged}
    r = _session().put(url, headers=_sb_headers_admin(), json=payload, timeout=20)
    _raise_with_body(r)
    return True

def sb_admin_invite_user(email: str, redirect_to: str = None):
    """Invia un invito via email usando GoTrue Admin /invite."""
    url = f"{SUPABASE_URL}/auth/v1/invite"
    payload = {"email": email}
    if redirect_to:
        payload["redirect_to"] = redirect_to
    r = _session().post(url, headers=_sb_headers_admin(), json=payload, timeout=20)
    _raise_with_body(r)
    try:
        return r.json()
    except Exception:
        return {"status": r.status_code}


def sb_admin_create_user_with_password(email: str, password: str, *, name: str = "", role: str = "user") -> str:
    ok, msg = validate_password_policy(password)
    if not ok:
        raise ValueError(msg)
    url = f"{SUPABASE_URL}/auth/v1/admin/users"
    payload = {
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": {"name": name or "", "role": role or "user"},
    }
    r = _session().post(url, headers=_sb_headers_admin(), json=payload, timeout=20)
    _raise_with_body(r)
    data = r.json() or {}
    uid = data.get("id") or (data.get("user") or {}).get("id")
    if not uid:
        raise RuntimeError("Utente creato ma id non restituito da Supabase.")
    return str(uid)


def sb_request_password_reset(email: str, redirect_to: str):
    """Invia email di reset password via Supabase GoTrue."""
    url = f"{SUPABASE_URL}/auth/v1/recover"
    payload = {"email": email, "redirect_to": redirect_to}
    r = _session().post(url, headers={'apikey': SUPABASE_ANON_KEY, 'Content-Type': 'application/json'}, json=payload, timeout=20)
    if r.status_code not in (200, 204):
        _raise_with_body(r)
    return True


def _auth_reset_redirect_url() -> str:
    configured = str(os.environ.get("APP_RESET_REDIRECT") or "").strip()
    if configured and "localhost" not in configured.lower() and "127.0.0.1" not in configured:
        return configured
    return url_for("reset_password", _external=True)


# ----------------- Password policy helpers -----------------
PW_MIN_LEN = 8
PW_ALLOWED_SYMBOLS = "@-$%!?"
PW_EXPIRE_DAYS = 30

def _pw_has_number(s: str) -> bool:
    return any(ch.isdigit() for ch in (s or ""))

def _pw_has_allowed_symbol(s: str) -> bool:
    allowed = set(PW_ALLOWED_SYMBOLS)
    return any(ch in allowed for ch in (s or ""))

def validate_password_policy(pw: str) -> tuple[bool, str]:
    pw = pw or ""
    if len(pw) < PW_MIN_LEN:
        return False, f"La password deve essere lunga almeno {PW_MIN_LEN} caratteri."
    if not _pw_has_number(pw):
        return False, "La password deve contenere almeno un numero."
    if not _pw_has_allowed_symbol(pw):
        return False, f"La password deve contenere almeno un simbolo tra: {PW_ALLOWED_SYMBOLS}"
    return True, ""

def _pw_fingerprint(uid: str, pw: str) -> str:
    """Fingerprint non reversibile per verificare 'non uguale alla precedente'."""
    import hashlib
    secret = app.secret_key
    if isinstance(secret, (bytes, bytearray)):
        secret_b = bytes(secret)
    else:
        secret_b = str(secret).encode("utf-8", errors="ignore")
    raw = (str(uid) + "|" + (pw or "")).encode("utf-8", errors="ignore") + b"|" + secret_b
    return hashlib.sha256(raw).hexdigest()

def _parse_dt_any(v):
    """Parsa timestamp salvati in user_metadata (ISO o epoch)."""
    if not v:
        return None
    try:
        # epoch seconds
        if isinstance(v, (int, float)) and v > 0:
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        s = str(v).strip()
        # normalize Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # datetime.fromisoformat supports '+00:00'
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _dt_to_iso_utc(dt: datetime) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()

def sb_user_update_metadata(access_token: str, meta: dict) -> bool:
    """Aggiorna user_metadata dell'utente autenticato (Bearer access_token)."""
    if not meta:
        return True
    url = f"{SUPABASE_URL}/auth/v1/user"
    # merge con metadata esistenti per non sovrascrivere altri campi
    try:
        u = sb_auth_user(access_token)
        existing = (u or {}).get("user_metadata") or {}
    except Exception:
        existing = {}
    merged = dict(existing)
    merged.update(meta)
    payload = {"data": merged}
    r = _session().put(
        url,
        headers={'apikey': SUPABASE_ANON_KEY, 'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
        json=payload,
        timeout=20
    )
    _raise_with_body(r)
    return True

def sb_admin_get_auth_user(uid: str) -> dict:
    url = f"{SUPABASE_URL}/auth/v1/admin/users/{uid}"
    r = _session().get(url, headers=_sb_headers_admin(), timeout=20)
    _raise_with_body(r)
    try:
        return r.json() or {}
    except Exception:
        return {}



def sb_user_update_password(access_token: str, new_password: str, *, uid: str | None = None):
    """Aggiorna la password dell'utente autenticato (Bearer access_token) applicando policy e aggiornando metadati.

    - Impone policy (min 8, almeno un numero, almeno un simbolo tra @-$%!?).
    - Salva su user_metadata:
        pw_changed_at (ISO UTC)
        pw_prev_hash  (fingerprint della password corrente)
    - Se pw_prev_hash esiste, vieta di reimpostare la stessa password.
    """
    ok, msg = validate_password_policy(new_password)
    if not ok:
        raise ValueError(msg)

    # recupera uid e metadata per confronto "non uguale" e merge
    user_obj = {}
    try:
        user_obj = sb_auth_user(access_token) or {}
    except Exception:
        user_obj = {}

    if uid is None:
        uid = user_obj.get("id")

    meta = (user_obj.get("user_metadata") or {}) if isinstance(user_obj, dict) else {}
    prev_hash = meta.get("pw_prev_hash")
    if uid and prev_hash:
        if _pw_fingerprint(str(uid), new_password) == str(prev_hash):
            raise ValueError("La nuova password non può essere uguale alla precedente.")

    merged = dict(meta) if isinstance(meta, dict) else {}
    now_dt = datetime.now(timezone.utc)
    if uid:
        merged["pw_prev_hash"] = _pw_fingerprint(str(uid), new_password)
    merged["pw_changed_at"] = _dt_to_iso_utc(now_dt)

    url = f"{SUPABASE_URL}/auth/v1/user"
    payload = {"password": new_password, "data": merged}
    r = _session().put(
        url,
        headers={'apikey': SUPABASE_ANON_KEY, 'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
        json=payload,
        timeout=20
    )
    _raise_with_body(r)
    return True

def _sb_admin_headers(is_json=True):
    """Headers per chiamate Admin (richiede SUPABASE_SERVICE_ROLE_KEY)."""
    if not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Funzione admin non disponibile: manca SUPABASE_SERVICE_ROLE_KEY in .env")
    h = {'apikey': SUPABASE_SERVICE_ROLE_KEY, 'Authorization': f'Bearer {SUPABASE_SERVICE_ROLE_KEY}'}
    if is_json:
        h['Content-Type'] = 'application/json'
    return h

def sb_auth_signin(email: str, password: str) -> dict:
    url = f'{SUPABASE_URL}/auth/v1/token?grant_type=password'
    r = _session().post(url, headers=_sb_headers(is_json=True),
                        json={'email': email, 'password': password}, timeout=20)
    _raise_with_body(r)
    return r.json()

def sb_auth_user(token: str) -> dict:
    url = f'{SUPABASE_URL}/auth/v1/user'
    r = _session().get(url, headers=_sb_headers(token, is_json=False), timeout=20)
    _raise_with_body(r)
    return r.json()

def sb_get_my_profile(token: str, uid: str) -> dict | None:
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    params = {'select': 'id,email,name,role,access_profile_id,ai_enabled,theme_key,is_master', 'id': f'eq.{uid}', 'limit': '1'}
    try:
        r = _session().get(url, headers=_sb_headers(token, is_json=False), params=params, timeout=20)
        _raise_with_body(r)
    except Exception:
        # compat: ambienti dove access_profile_id non esiste ancora
        for fields in (
            'id,email,name,role,access_profile_id,ai_enabled,theme_key',
            'id,email,name,role,access_profile_id,ai_enabled',
            'id,email,name,role',
        ):
            params = {'select': fields, 'id': f'eq.{uid}', 'limit': '1'}
            try:
                r = _session().get(url, headers=_sb_headers(token, is_json=False), params=params, timeout=20)
                _raise_with_body(r)
                break
            except Exception:
                continue
        else:
            raise
    arr = r.json() or []
    return arr[0] if arr else None

def sb_get_profile_by_id(token: str, uid: str, fields: str = 'id,email,name,role') -> dict | None:
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    params = {'select': fields, 'id': f'eq.{uid}', 'limit': '1'}
    r = _session().get(url, headers=_sb_headers(token, is_json=False), params=params, timeout=20)
    _raise_with_body(r)
    arr = r.json() or []
    return arr[0] if arr else None

def sb_admin_get_profile_by_id(uid: str, fields: str = 'id,email,name,role,access_profile_id,ai_enabled,theme_key,is_master') -> dict | None:
    if not uid:
        return None
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    params = {'select': fields, 'id': f'eq.{uid}', 'limit': '1'}
    try:
        r = _session().get(url, headers=_sb_admin_headers(is_json=False), params=params, timeout=20)
        _raise_with_body(r)
    except Exception:
        fallback_fields = 'id,email,name,role,access_profile_id,ai_enabled,theme_key'
        params = {'select': fallback_fields, 'id': f'eq.{uid}', 'limit': '1'}
        try:
            r = _session().get(url, headers=_sb_admin_headers(is_json=False), params=params, timeout=20)
            _raise_with_body(r)
        except Exception:
            fallback_fields = 'id,email,name,role,access_profile_id'
            params = {'select': fallback_fields, 'id': f'eq.{uid}', 'limit': '1'}
            r = _session().get(url, headers=_sb_admin_headers(is_json=False), params=params, timeout=20)
            _raise_with_body(r)
    arr = r.json() or []
    return arr[0] if arr else None

def sb_admin_list_profiles() -> list:
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    for fields in (
        'id,email,name,role,created_at,access_profile_id,ai_enabled,theme_key,is_master',
        'id,email,name,role,created_at,access_profile_id,ai_enabled,theme_key',
        'id,email,name,role,created_at',
    ):
        params = {'select': fields, 'order': 'created_at.asc'}
        try:
            r = _session().get(url, headers=_sb_admin_headers(is_json=False), params=params, timeout=20)
            _raise_with_body(r)
            return r.json() or []
        except Exception:
            continue
    return []

def sb_list_profiles(token: str) -> list:
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    params = {'select': 'id,email,name,role,created_at,ai_enabled,theme_key,is_master', 'order': 'created_at.asc'}
    try:
        r = _session().get(url, headers=_sb_headers(token, is_json=False), params=params, timeout=20)
        _raise_with_body(r)
    except Exception:
        params = {'select': 'id,email,name,role,created_at', 'order': 'created_at.asc'}
        r = _session().get(url, headers=_sb_headers(token, is_json=False), params=params, timeout=20)
        _raise_with_body(r)
    return r.json() or []

def sb_admin_update_profile(uid: str, **fields):
    if not fields:
        return True
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    params = {'id': f'eq.{uid}'}
    r = _session().patch(url, headers=_sb_admin_headers(is_json=True), params=params, data=json.dumps(fields), timeout=20)
    _raise_with_body(r)
    return True


def sb_admin_delete_profile(uid: str) -> bool:
    """Cancella il profilo e le associazioni location usando service role."""
    url = f'{SUPABASE_URL}/rest/v1/user_locations'
    r = _session().delete(url, headers=_sb_admin_headers(is_json=False), params={'user_id': f'eq.{uid}'}, timeout=20)
    _raise_with_body(r)
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    r = _session().delete(url, headers=_sb_admin_headers(is_json=False), params={'id': f'eq.{uid}'}, timeout=20)
    _raise_with_body(r)
    return True

def sb_update_profile(token: str, uid: str, **fields):
    """
    Aggiorna il record del profilo (public.profiles) con i campi passati.
    Esempio: sb_update_profile(token, uid, name="Mario", role="user", can_view_reviews=True)
    """
    if not fields:
        return True
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    params = {'id': f'eq.{uid}'}
    r = _session().patch(url, headers=_sb_headers(token), params=params, data=json.dumps(fields), timeout=20)
    _raise_with_body(r)
    return True


def _current_tenant_ai_enabled() -> bool:
    try:
        from tenant_config_repository import get_tenant

        tenant_key = str(session.get("tenant_key") or os.getenv("STOREHUB_TENANT_KEY") or "default").strip() or "default"
        return bool((get_tenant(tenant_key) or {}).get("ai_enabled", True))
    except Exception:
        return True


def _normalize_ai_access(u: dict | None) -> bool:
    if not u:
        return False
    if not _current_tenant_ai_enabled():
        return False
    return bool(u.get("ai_enabled", False))


THEME_OPTIONS = [
    {"key": "base", "label": "Azzurro", "description": "Tema chiaro principale, più luminoso e vicino allo stile Calendar."},
    {"key": "classic", "label": "Grigio classico", "description": "La variante più neutra, vicina al layout attuale."},
    {"key": "midnight", "label": "Blu notte", "description": "Più profondo ma comunque leggibile, senza diventare troppo cupo."},
    {"key": "mint", "label": "Verde acqua", "description": "Chiaro e fresco, con accenti acquamarina molto morbidi."},
    {"key": "sand", "label": "Sabbia", "description": "Più caldo e luminoso, con toni beige e blu profondo come contrasto."},
    {"key": "lavender", "label": "Lavanda", "description": "Neutro freddo con accenti lilla discreti e poco invasivi."},
]
THEME_KEYS = {str(x["key"]) for x in THEME_OPTIONS}


def _normalize_theme_key(value: str | None) -> str:
    key = str(value or "").strip().lower()
    return key if key in THEME_KEYS else "base"


def sb_admin_delete_auth_user(uid: str) -> bool:
    """Elimina l'utente dall'Auth (richiede service role)."""
    url = f'{SUPABASE_URL}/auth/v1/admin/users/{uid}'
    r = _session().delete(url, headers=_sb_admin_headers(is_json=False), timeout=20)
    _raise_with_body(r)
    return True

def sb_admin_set_password(uid: str, new_password: str) -> bool:
    """Imposta una nuova password per l'utente (richiede service role)."""
    if not new_password or len(new_password) < 6:
        raise ValueError("Password non valida (min 6 caratteri).")
    url = f'{SUPABASE_URL}/auth/v1/admin/users/{uid}'
    r = _session().put(url, headers=_sb_admin_headers(), json={'password': new_password}, timeout=20)
    _raise_with_body(r)

def sb_get_user_locations(token: str, uid: str) -> list[str]:
    url = f'{SUPABASE_URL}/rest/v1/user_locations'
    params = {'select': 'loc_name', 'user_id': f'eq.{uid}'}
    r = _session().get(url, headers=_sb_headers(token, is_json=False), params=params, timeout=20)
    _raise_with_body(r)
    return [row['loc_name'] for row in (r.json() or [])]

def sb_set_user_locations(token: str, uid: str, loc_names: list[str]):
    # cancella e reinserisce
    url = f'{SUPABASE_URL}/rest/v1/user_locations'
    r = _session().delete(url, headers=_sb_headers(token, is_json=False),
                          params={'user_id': f'eq.{uid}'}, timeout=20)
    _raise_with_body(r)
    if loc_names:
        payload = [{'user_id': uid, 'loc_name': extract_loc_id(ln)} for ln in set(loc_names)]
        r2 = _session().post(url, headers=_sb_headers(token), data=json.dumps(payload), timeout=20)
        _raise_with_body(r2)
    return True

# ----------------- AUTH helpers -----------------
def current_user():
    uid = session.get('uid')
    if not uid:
        return None
    base = {
        'uid': uid,
        'email': session.get('email'),
        'name': session.get('name'),
        'role': session.get('role'),
        'ai_enabled': session.get('ai_enabled'),
        'is_master': bool(session.get('is_master')),
        'theme_key': _normalize_theme_key(session.get('theme_key')),
        'sb_token': session.get('sb_token'),
        'access_profile_id': session.get('access_profile_id'),
        'access_modules': session.get('access_modules') if isinstance(session.get('access_modules'), dict) else None,
        **_session_tenant_snapshot(),
    }

    # Arricchisci con i flag profilo se presenti (best-effort)
    try:
        token = base.get('sb_token')
        prof = None
        try:
            prof = sb_get_profile_by_id(token, uid, fields='id,email,name,role,access_profile_id,ai_enabled,theme_key,is_master')
        except Exception:
            # compat: se la colonna non esiste ancora
            prof = sb_get_profile_by_id(token, uid, fields='id,email,name,role')

        if (not prof or not prof.get('access_profile_id')) and uid:
            try:
                admin_prof = sb_admin_get_profile_by_id(str(uid))
                if admin_prof:
                    prof = admin_prof
            except Exception:
                pass

        if prof:
            # campi base da DB (preferisci DB alla sessione)
            for k in ['email', 'name', 'role', 'access_profile_id', 'ai_enabled', 'theme_key', 'is_master']:
                if k in prof and prof.get(k) is not None:
                    base[k] = prof.get(k)

            for k in ['can_view_anagrafica','can_edit_anagrafica','can_view_reviews','can_reply_reviews','can_access_media_single','can_access_media_bulk']:
                if k in prof:
                    base[k] = prof.get(k)

            try:
                session['email'] = base.get('email')
                session['name'] = base.get('name')
                session['access_profile_id'] = base.get('access_profile_id')
                session['ai_enabled'] = _normalize_ai_access(base)
                if str(base.get('role') or '').strip().lower() == 'master':
                    base['is_master'] = True
                session['is_master'] = _is_platform_master(base)
                session['theme_key'] = _normalize_theme_key(base.get('theme_key'))
                if session.get("tenant_role") and not _is_platform_master(base):
                    base["role"] = session.get("tenant_role")
                    session["role"] = session.get("tenant_role")
                else:
                    session['role'] = base.get('role')
            except Exception:
                pass
    except Exception:
        pass

    base['ai_enabled'] = _normalize_ai_access(base)
    if str(base.get('role') or '').strip().lower() == 'master':
        base['is_master'] = True
    if session.get("tenant_role") and not _is_platform_master(base):
        base["role"] = session.get("tenant_role")
        base["tenant_role"] = session.get("tenant_role")
        base["tenant_key"] = session.get("tenant_key")
        base["tenant_name"] = session.get("tenant_name")
    base['theme_key'] = _normalize_theme_key(base.get('theme_key'))
    base.update(_normalize_user_perms(base))
    mods = _normalize_user_modules(base)
    mods = _apply_tenant_module_gates(mods, base)
    base.update(mods)
    try:
        session['access_modules'] = {k: bool(v) for k, v in mods.items() if k.startswith('mod_')}
    except Exception:
        pass
    return base

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('uid'):
            nxt = request.full_path if request.query_string else request.path
            return redirect(url_for('login', next=nxt))
        return view(*args, **kwargs)
    return wrapped

# ---- Permessi granulari ----
PERM_FIELDS = [
    "can_view_anagrafica",
    "can_edit_anagrafica",
    "can_view_reviews",
    "can_reply_reviews",
    "can_access_media_single",
    "can_access_media_bulk",
]

def _normalize_user_perms(u: dict) -> dict:
    if not u:
        return {k: False for k in PERM_FIELDS}
    if u.get("role") == "admin":
        return {k: True for k in PERM_FIELDS}
    out = {}
    out["can_view_anagrafica"]     = bool(u.get("can_view_anagrafica", True))
    out["can_edit_anagrafica"]     = bool(u.get("can_edit_anagrafica", False))
    out["can_view_reviews"]        = bool(u.get("can_view_reviews", True))
    out["can_reply_reviews"]       = bool(u.get("can_reply_reviews", False))
    out["can_access_media_single"] = bool(u.get("can_access_media_single", True))
    out["can_access_media_bulk"]   = bool(u.get("can_access_media_bulk", False))
    return out


# ---- Moduli visibili (profili di visualizzazione) ----
ACCESS_MODULES = [
    ("dashboard", "Dashboard"),
    ("magazzino", "Magazzino"),
    ("supplier_orders", "Ordini al fornitore"),
    ("mbo", "MBO"),
    ("rendiconto", "Rendiconto"),
    ("estrazioni", "Estrazioni"),
    ("estrazioni_hq", "Estrazioni HQ"),
    ("orari", "Gestione Orari"),
    ("controlli", "Controlli di Gestione"),
    ("cruscotto", "Cruscotto"),
    ("links", "Link"),
]

# Cache in-memory (best-effort) per evitare roundtrip ripetuti
_ACCESS_PROFILE_CACHE: dict[str, dict] = {}
_ACCESS_PROFILE_TENANT_COLUMN: bool | None = None


def _all_module_flags(value: bool) -> dict:
    out = {f"mod_{k}": bool(value) for k, _ in ACCESS_MODULES}
    out["mod_cruscotto_pnl_store"] = bool(value)
    return out


def _module_flags_from_modules_dict(mods: dict | None) -> dict | None:
    if not isinstance(mods, dict):
        return None
    out = {f"mod_{k}": bool(mods.get(k, False)) for k, _ in ACCESS_MODULES}
    out["mod_cruscotto_pnl_store"] = False
    return out


def _module_flags_from_session_snapshot(snapshot: dict | None) -> dict | None:
    if not isinstance(snapshot, dict):
        return None
    out = {}
    for k, _ in ACCESS_MODULES:
        if f"mod_{k}" in snapshot:
            out[f"mod_{k}"] = bool(snapshot.get(f"mod_{k}"))
        elif k in snapshot:
            out[f"mod_{k}"] = bool(snapshot.get(k))
        else:
            return None
    out["mod_cruscotto_pnl_store"] = bool(
        snapshot.get("mod_cruscotto_pnl_store", snapshot.get("cruscotto_pnl_store", False))
    )
    return out


def _is_platform_master(u: dict | None) -> bool:
    if not u:
        return False
    role_l = str((u or {}).get("role") or "").strip().lower()
    return bool((u or {}).get("is_master")) or role_l == "master"


def _session_tenant_snapshot() -> dict:
    return {
        "tenant_key": session.get("tenant_key"),
        "tenant_name": session.get("tenant_name"),
        "tenant_role": session.get("tenant_role"),
        "tenant_database": session.get("tenant_database"),
    }


def _effective_tenant_role(tenant: dict | None, role: str | None = None) -> str:
    effective = str(role if role is not None else (tenant or {}).get("tenant_role") or "").strip().lower() or "user"
    if effective == "admin" and not bool((tenant or {}).get("tenant_admin_enabled", True)):
        return "user"
    return effective


def _apply_tenant_to_session(tenant: dict | None) -> None:
    if not tenant:
        for key in ("tenant_key", "tenant_name", "tenant_role", "tenant_database"):
            session.pop(key, None)
        return
    tenant_key = str(tenant.get("tenant_key") or "").strip()
    tenant_role = _effective_tenant_role(tenant)
    session["tenant_key"] = tenant_key
    session["tenant_name"] = str(tenant.get("display_name") or tenant_key).strip() or tenant_key
    session["tenant_database"] = str(tenant.get("database_name") or "").strip()
    session["tenant_role"] = tenant_role
    session["role"] = tenant_role


def _resolve_login_tenant(uid: str | None, email: str | None, preferred_tenant_key: str | None = None) -> dict | None:
    try:
        from tenant_config_repository import resolve_user_tenant

        return resolve_user_tenant(user_id=uid, email=email, preferred_tenant_key=preferred_tenant_key)
    except Exception:
        current_app.logger.exception("Errore risoluzione tenant utente")
        return None


def _current_tenant_key_for_admin() -> str:
    if _is_platform_master(current_user()) and _master_admin_tenant_key():
        return _master_admin_tenant_key()
    return str(session.get("tenant_key") or os.getenv("STOREHUB_TENANT_KEY") or "default").strip() or "default"


def _master_admin_tenant_key() -> str:
    return str(session.get("master_admin_tenant_key") or "").strip()


def _master_admin_context_is_active() -> bool:
    tenant_key = _master_admin_tenant_key()
    if not tenant_key:
        return False
    try:
        from tenant_config_repository import get_tenant

        tenant = get_tenant(tenant_key)
        if not bool(tenant.get("master_can_admin")) or not bool(tenant.get("is_active", True)):
            session.pop("master_admin_tenant_key", None)
            return False
        session["master_admin_tenant_key"] = str(tenant.get("tenant_key") or tenant_key).strip()
        session["tenant_database"] = str(tenant.get("database_name") or "").strip()
        return True
    except Exception:
        current_app.logger.exception("Errore verifica contesto admin master")
        return False


def _resolve_master_admin_tenant(*, allow_query: bool = True) -> dict | None:
    if not _is_platform_master(current_user()):
        return None
    try:
        from tenant_config_repository import get_tenant

        tenant_key = ""
        if allow_query:
            tenant_key = str(request.values.get("tenant_key") or "").strip()
        tenant_key = tenant_key or _master_admin_tenant_key()
        if not tenant_key:
            return None
        tenant = get_tenant(tenant_key)
        if not bool(tenant.get("master_can_admin")):
            session.pop("master_admin_tenant_key", None)
            return None
        session["master_admin_tenant_key"] = str(tenant.get("tenant_key") or tenant_key).strip()
        return tenant
    except Exception:
        current_app.logger.exception("Errore risoluzione tenant admin master")
        return None


def _tenant_users_for_current_admin() -> list[dict]:
    if _is_platform_master(current_user()) and not _master_admin_tenant_key():
        return []
    try:
        from tenant_config_repository import list_tenant_users

        return list_tenant_users(_current_tenant_key_for_admin()) or []
    except Exception:
        return []


def _tenant_user_lookup_for_current_admin() -> tuple[dict[str, dict], dict[str, dict]]:
    rows = _tenant_users_for_current_admin()
    by_uid: dict[str, dict] = {}
    by_email: dict[str, dict] = {}
    for row in rows:
        if not bool(row.get("is_active", True)):
            continue
        uid = str(row.get("user_id") or "").strip()
        email = str(row.get("email") or "").strip().lower()
        if uid:
            by_uid[uid] = row
        if email:
            by_email[email] = row
    return by_uid, by_email


def _require_user_in_current_tenant(uid: str, profile: dict | None = None) -> dict | None:
    if _is_platform_master(current_user()) and not _master_admin_tenant_key():
        return None
    by_uid, by_email = _tenant_user_lookup_for_current_admin()
    row = by_uid.get(str(uid or "").strip())
    if not row and profile:
        row = by_email.get(str(profile.get("email") or "").strip().lower())
    if not row:
        abort(403)
    return row


def _first_allowed_endpoint_for_user(u: dict | None) -> str:
    if not u:
        return 'login'
    role_l = str(u.get('role') or '').strip().lower()
    if _is_platform_master(u):
        return 'master_home'
    if role_l == 'admin':
        return 'dashboard'
    if role_l == 'fornitore':
        return 'warehouse.supplier_orders_received'
    ordered = [
        ('dashboard', 'dashboard'),
        ('supplier_orders', 'warehouse.supplier_orders_home'),
        ('mbo', 'mbo.home'),
        ('magazzino', 'warehouse.inventory_new'),
        ('rendiconto', 'rendiconto.distinta_cassa'),
        ('estrazioni', 'estrazioni.scheduling'),
        ('estrazioni_hq', 'estrazioni.scheduling'),
        ('orari', 'orari.anagrafica'),
        ('controlli', 'controlli.pnl'),
        ('cruscotto', 'cruscotto.analisi_settimanale'),
        ('links', 'links.home'),
    ]
    for module_key, endpoint in ordered:
        if u.get(f'mod_{module_key}', False):
            return endpoint
    return 'logout'


def _access_profiles_support_tenant_key() -> bool:
    global _ACCESS_PROFILE_TENANT_COLUMN
    if _ACCESS_PROFILE_TENANT_COLUMN is not None:
        return bool(_ACCESS_PROFILE_TENANT_COLUMN)
    url = f"{SUPABASE_URL}/rest/v1/access_profiles"
    params = {"select": "id,tenant_key", "limit": "1"}
    try:
        r = _session().get(url, headers=_sb_admin_headers(is_json=False), params=params, timeout=20)
        _raise_with_body(r)
        _ACCESS_PROFILE_TENANT_COLUMN = True
    except Exception:
        _ACCESS_PROFILE_TENANT_COLUMN = False
    return bool(_ACCESS_PROFILE_TENANT_COLUMN)


def sb_admin_list_access_profiles(tenant_key: str | None = None) -> list[dict]:
    """Legge tutti i profili di visualizzazione (service role)."""
    url = f"{SUPABASE_URL}/rest/v1/access_profiles"
    tenant = str(tenant_key or "").strip()
    if tenant and not _access_profiles_support_tenant_key():
        return [] if tenant != "default" else sb_admin_list_access_profiles(None)
    params = {
        "select": "id,name,description,modules,created_at,updated_at,tenant_key" if tenant else "id,name,description,modules,created_at,updated_at",
        "order": "name.asc",
    }
    if tenant:
        if tenant == "default":
            params["or"] = "(tenant_key.eq.default,tenant_key.is.null)"
        else:
            params["tenant_key"] = f"eq.{tenant}"
    r = _session().get(url, headers=_sb_admin_headers(is_json=False), params=params, timeout=20)
    _raise_with_body(r)
    return r.json() or []

def sb_admin_get_access_profile(profile_id: str, tenant_key: str | None = None) -> dict | None:
    if not profile_id:
        return None
    url = f"{SUPABASE_URL}/rest/v1/access_profiles"
    with_tenant = _access_profiles_support_tenant_key()
    params = {"select": "id,name,description,modules,tenant_key" if with_tenant else "id,name,description,modules", "id": f"eq.{profile_id}", "limit": "1"}
    r = _session().get(url, headers=_sb_admin_headers(is_json=False), params=params, timeout=20)
    _raise_with_body(r)
    arr = r.json() or []
    profile = arr[0] if arr else None
    if profile and tenant_key and with_tenant:
        tenant = str(tenant_key or "").strip()
        owner = str(profile.get("tenant_key") or "").strip()
        if tenant != "default" and owner != tenant:
            return None
        if tenant == "default" and owner not in ("", "default"):
            return None
    return profile

def sb_admin_upsert_access_profile(profile_id: str | None, name: str, description: str | None, modules: dict, tenant_key: str | None = None) -> str:
    """Crea/aggiorna un profilo; ritorna l'id."""
    url = f"{SUPABASE_URL}/rest/v1/access_profiles"
    payload = {
        "name": (name or "").strip(),
        "description": (description or None),
        "modules": modules or {},
    }
    tenant = str(tenant_key or "").strip()
    if tenant and _access_profiles_support_tenant_key():
        payload["tenant_key"] = tenant
    if not payload["name"]:
        raise ValueError("Nome profilo obbligatorio")

    headers = _sb_admin_headers(is_json=True)
    headers["Prefer"] = "resolution=merge-duplicates,return=representation"

    if profile_id:
        params = {"id": f"eq.{profile_id}"}
        r = _session().patch(url, headers=headers, params=params, data=json.dumps(payload), timeout=20)
        _raise_with_body(r)
        # PATCH con return=representation
        try:
            arr = r.json() or []
            if arr and isinstance(arr, list) and arr[0].get("id"):
                return arr[0]["id"]
        except Exception:
            pass
        return profile_id

    # create
    r = _session().post(url, headers=headers, data=json.dumps([payload]), timeout=20)
    _raise_with_body(r)
    arr = r.json() or []
    if arr and isinstance(arr, list) and arr[0].get("id"):
        return arr[0]["id"]
    raise RuntimeError("Creazione profilo fallita: nessun id restituito")

def sb_admin_delete_access_profile(profile_id: str) -> None:
    if not profile_id:
        return
    url = f"{SUPABASE_URL}/rest/v1/access_profiles"
    r = _session().delete(url, headers=_sb_admin_headers(is_json=False), params={"id": f"eq.{profile_id}"}, timeout=20)
    _raise_with_body(r)

def _get_access_profile_cached(profile_id: str) -> dict | None:
    if not profile_id:
        return None
    now = time.time()
    ent = _ACCESS_PROFILE_CACHE.get(profile_id)
    if ent and (now - ent.get("ts", 0)) < 60:
        return ent.get("data")
    try:
        data = sb_admin_get_access_profile(profile_id)
    except Exception:
        data = None
    _ACCESS_PROFILE_CACHE[profile_id] = {"ts": now, "data": data}
    return data

def _normalize_user_modules(u: dict) -> dict:
    """Ritorna flag mod_* per i moduli.

    Regole:
    - nessun utente -> niente accesso
    - admin -> accesso completo
    - nessun profilo assegnato -> accesso completo (compatibilità)
    - profilo assegnato ma non leggibile -> NON fare fail-open: usa snapshot di sessione,
      altrimenti nega tutti i moduli
    """
    if not u:
        return _all_module_flags(False)

    role_l = str(u.get("role") or "").strip().lower()
    if _is_platform_master(u):
        return _all_module_flags(False)
    if role_l == "admin":
        return _all_module_flags(True)

    prof_id = str(u.get("access_profile_id") or "").strip()
    if not prof_id:
        if role_l == "fornitore":
            return _all_module_flags(False)
        return _all_module_flags(True)

    prof = _get_access_profile_cached(prof_id) if prof_id else None
    mods = (prof.get("modules") or {}) if isinstance(prof, dict) else None
    out = _module_flags_from_modules_dict(mods)
    if out is not None:
        return out

    snap = _module_flags_from_session_snapshot(u.get("access_modules"))
    if snap is not None:
        return snap

    return _all_module_flags(False)


def _apply_tenant_module_gates(mods: dict, u: dict | None) -> dict:
    if not isinstance(mods, dict):
        return _all_module_flags(False)
    if _is_platform_master(u):
        return mods
    tenant_key = str(session.get("tenant_key") or "").strip()
    if not tenant_key:
        return mods
    try:
        from tenant_config_repository import list_tenant_modules

        rows = list_tenant_modules(tenant_key) or []
        enabled = {
            str(row.get("module_key") or "").strip(): bool(row.get("is_enabled"))
            for row in rows
            if str(row.get("module_key") or "").strip()
        }
        if not enabled:
            return mods
        gated = dict(mods)
        for module_key, _label in ACCESS_MODULES:
            flag = f"mod_{module_key}"
            gated[flag] = bool(gated.get(flag)) and bool(enabled.get(module_key, False))
        gated["mod_cruscotto_pnl_store"] = (
            bool(gated.get("mod_cruscotto"))
            and bool(gated.get("mod_controlli"))
            and bool(enabled.get("cruscotto_pnl_store", False))
        )
        return gated
    except Exception:
        current_app.logger.exception("Errore filtro moduli tenant")
        return _all_module_flags(False)


def module_required(module_key: str):
    """Decorator: blocca l'accesso se il modulo non è abilitato per l'utente."""
    from functools import wraps as _wraps
    def deco(view):
        @_wraps(view)
        def wrapped(*args, **kwargs):
            _enforce_module(module_key)
            return view(*args, **kwargs)
        return wrapped
    return deco

def _enforce_module(module_key: str):
    u = current_user()
    if not u:
        abort(403)
    if _is_platform_master(u) and _master_admin_context_is_active():
        return None
    if not _is_platform_master(u):
        enabled = True
        try:
            from tenant_config_repository import current_tenant_key, is_tenant_module_enabled

            tenant_key = str(session.get("tenant_key") or current_tenant_key()).strip() or current_tenant_key()
            enabled = is_tenant_module_enabled(module_key, tenant_key)
            if module_key == "cruscotto_pnl_store":
                enabled = bool(enabled) and is_tenant_module_enabled("controlli", tenant_key)
        except Exception:
            current_app.logger.exception("Errore verifica modulo tenant")
            abort(403)
        if not enabled:
            abort(403)
    if u.get("role") == "admin":
        return None
    if module_key == "cruscotto_pnl_store" and u.get("mod_cruscotto_pnl_store", False):
        return None
    if u.get(f"mod_{module_key}", False):
        return None
    abort(403)

def require_perm(flag_name: str, redirect_endpoint: str = "dashboard"):
    from functools import wraps as _wraps
    def deco(view):
        @_wraps(view)
        def wrapped(*args, **kwargs):
            u = current_user()
            if not u:
                flash("Devi autenticarti.", "warning")
                return redirect(url_for("login"))
            if (u.get("role") == "admin") or u.get(flag_name):
                return view(*args, **kwargs)
            if request.method == "POST":
                abort(403)
            flash("Permesso negato per questa sezione.", "danger")
            return redirect(url_for(redirect_endpoint))
        return wrapped
    return deco

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u:
            abort(403)
        if u.get('role') == 'admin':
            return view(*args, **kwargs)
        if _is_platform_master(u):
            if _resolve_master_admin_tenant():
                return view(*args, **kwargs)
            flash("Seleziona prima il tenant da amministrare.", "warning")
            return redirect(url_for("master_admin_tenant_select"))
        abort(403)
    return wrapped


def master_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not _is_platform_master(u):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def admin_or_master_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u:
            abort(403)
        if u.get('role') == 'admin':
            return view(*args, **kwargs)
        if _is_platform_master(u):
            if _resolve_master_admin_tenant():
                return view(*args, **kwargs)
            flash("Seleziona prima il tenant da amministrare.", "warning")
            return redirect(url_for("master_admin_tenant_select"))
        abort(403)
    return wrapped


@app.get('/master')
@login_required
@master_required
def master_home():
    return render_template('master_home.html')


def _current_ui_language() -> str:
    try:
        from translation_repository import SUPPORTED_LANGUAGES

        allowed = {str(x.get("code") or "").strip().lower() for x in SUPPORTED_LANGUAGES}
    except Exception:
        allowed = {"it", "en", "fr", "es"}
    tenant_allowed = {str(x.get("code") or "").strip().lower() for x in _supported_ui_languages()}
    if tenant_allowed:
        allowed = allowed & tenant_allowed
    if not allowed:
        allowed = {"it"}
    requested = str(request.args.get("ui_lang") or session.get("ui_language") or "it").strip().lower()
    if requested not in allowed:
        requested = "it"
        session["ui_language"] = requested
    if request.args.get("ui_lang"):
        session["ui_language"] = requested
    return requested


def _template_translate(namespace: str, translation_key: str, source_text: str) -> str:
    try:
        from translation_repository import translate_text

        return translate_text(namespace, translation_key, source_text, _current_ui_language())
    except Exception:
        return str(source_text or "")


def _supported_ui_languages() -> list[dict]:
    try:
        from translation_repository import SUPPORTED_LANGUAGES

        supported = SUPPORTED_LANGUAGES
    except Exception:
        supported = [
            {"code": "it", "label": "Italiano"},
            {"code": "en", "label": "English"},
            {"code": "fr", "label": "Français"},
            {"code": "es", "label": "Español"},
            {"code": "pt", "label": "Portugues"},
        ]


    try:
        from tenant_config_repository import get_tenant

        tenant_key = str(session.get("tenant_key") or session.get("master_admin_tenant_key") or "default").strip() or "default"
        tenant = get_tenant(tenant_key)
        codes = {
            str(x or "").strip().lower()
            for x in str(tenant.get("enabled_language_codes") or "it").replace(";", ",").split(",")
            if str(x or "").strip()
        }
        if not bool(tenant.get("multilanguage_enabled")):
            codes = {"it"}
        codes.add("it")
        filtered = [lang for lang in supported if str(lang.get("code") or "").strip().lower() in codes]
        return filtered or [{"code": "it", "label": "Italiano"}]
    except Exception:
        return [lang for lang in supported if str(lang.get("code") or "").strip().lower() == "it"] or [{"code": "it", "label": "Italiano"}]


def _all_ui_languages() -> list[dict]:
    try:
        from translation_repository import SUPPORTED_LANGUAGES

        return SUPPORTED_LANGUAGES
    except Exception:
        return [
            {"code": "it", "label": "Italiano"},
            {"code": "en", "label": "English"},
            {"code": "fr", "label": "Francais"},
            {"code": "es", "label": "Espanol"},
            {"code": "pt", "label": "Portugues"},
        ]


@app.post('/account/language')
@login_required
def account_set_language():
    allowed = {str(x.get("code") or "").strip().lower() for x in _supported_ui_languages()}
    language = str(request.form.get("ui_language") or "it").strip().lower()
    if language not in allowed:
        language = "it"
    session["ui_language"] = language
    next_url = request.form.get("next") or request.referrer or url_for(_first_allowed_endpoint_for_user(current_user()))
    return redirect(next_url)


@app.route('/master/translations', methods=['GET', 'POST'])
@login_required
@master_required
def master_translations():
    import json

    from app_db import storehub_database_context
    from tenant_config_repository import ensure_current_tenant, get_tenant, list_tenants
    from translation_repository import (
        SUPPORTED_LANGUAGES,
        backfill_supported_languages,
        list_base_translation_groups,
        list_tenant_translation_overrides,
        list_translation_templates,
        reset_translation_key,
        seed_pilot_translations,
        update_translation_key,
        update_base_translation_keys,
        upsert_tenant_translation_keys,
    )

    ensure_current_tenant()
    tenants = list_tenants()
    tenant_key = (
        request.values.get("tenant_key")
        or _master_admin_tenant_key()
        or session.get("master_tenant_key")
        or os.getenv("STOREHUB_TENANT_KEY")
        or "default"
    )
    tenant = get_tenant(tenant_key)
    if not tenant and tenants:
        tenant = tenants[0]
        tenant_key = str(tenant.get("tenant_key") or "").strip()
    if not tenant:
        flash("Crea prima un tenant.", "warning")
        return redirect(url_for("master_tenants"))

    tenant_key = str(tenant.get("tenant_key") or tenant_key).strip() or "default"
    db_name = str(tenant.get("database_name") or "").strip()
    session["master_tenant_key"] = tenant_key

    language = str(request.values.get("language") or "it").strip().lower()
    view_mode = str(request.values.get("view") or "base").strip().lower()
    if view_mode not in {"base", "tenant"}:
        view_mode = "base"
    template_key = str(request.values.get("template") or "").strip()

    try:
        with storehub_database_context(db_name) if db_name else nullcontext():
            seed_pilot_translations()
            backfill_supported_languages(include_platform=True, include_tenant=True)
            try:
                from cash_statement_config_repository import seed_cash_statement_translations, seed_default_cash_statement_config

                seed_default_cash_statement_config(tenant_key=tenant_key)
                seed_cash_statement_translations(tenant_key=tenant_key)
            except Exception:
                current_app.logger.exception("Seed traduzioni distinta cassa non riuscito")
            if request.method == "POST":
                action = "reset" if request.form.get("reset_row") else str(request.form.get("action") or "save").strip()
                if action == "reset":
                    index = str(request.form.get("reset_row") or "").strip()
                    if index:
                        reset_translation_key(
                            request.form.get(f"namespace_{index}") or "",
                            request.form.get(f"translation_key_{index}") or "",
                            request.form.get(f"language_code_{index}") or language,
                        )
                        flash("Traduzione ripristinata al valore base.", "success")
                    return redirect(url_for("master_translations", tenant_key=tenant_key, language=language, template=template_key, view="tenant"))
                saved = 0
                row_count = int(request.form.get("row_count") or 0)
                if action in {"save_base", "save_tenant"}:
                    for i in range(row_count):
                        value = request.form.get(f"text_value_{i}") or ""
                        base_value = request.form.get(f"base_text_value_{i}") or ""
                        occ_raw = request.form.get(f"occurrences_{i}") or "[]"
                        try:
                            occurrences = json.loads(occ_raw)
                        except Exception:
                            occurrences = []
                        if action == "save_base":
                            if str(value or "") == str(base_value or ""):
                                continue
                            saved += update_base_translation_keys(occurrences, language, value)
                        else:
                            if str(value or "") == str(base_value or ""):
                                continue
                            saved += upsert_tenant_translation_keys(occurrences, language, value)
                    flash(f"Traduzioni salvate: {saved}.", "success")
                    next_view = "base" if action == "save_base" else "tenant"
                    return redirect(url_for("master_translations", tenant_key=tenant_key, language=language, template=template_key, view=next_view))
                for i in range(row_count):
                    ns = request.form.get(f"namespace_{i}") or ""
                    key = request.form.get(f"translation_key_{i}") or ""
                    lang = request.form.get(f"language_code_{i}") or language
                    value = request.form.get(f"text_value_{i}") or ""
                    if update_translation_key(ns, key, lang, value):
                        saved += 1
                flash(f"Traduzioni salvate: {saved}.", "success")
                return redirect(url_for("master_translations", tenant_key=tenant_key, language=language, template=template_key, view=view_mode))
            base_groups = list_base_translation_groups(language_code=language, template=template_key)
            tenant_rows = list_tenant_translation_overrides(language_code=language, template=template_key)
    except Exception as e:
        current_app.logger.exception("Errore gestione traduzioni")
        flash(f"Errore traduzioni: {e}", "danger")
        base_groups = []
        tenant_rows = []

    templates = list_translation_templates()
    return render_template(
        "master_translations.html",
        tenants=tenants,
        tenant=tenant,
        selected_tenant_key=tenant_key,
        languages=SUPPORTED_LANGUAGES,
        selected_language=language,
        templates=templates,
        selected_template=template_key,
        view_mode=view_mode,
        base_groups=base_groups,
        tenant_rows=tenant_rows,
    )


@app.get('/master/tenant-assignments')
@login_required
@master_required
def master_tenant_assignments():
    from tenant_config_repository import (
        ensure_current_tenant,
        list_storage_rules,
        list_tenant_modules,
        list_tenant_stores,
        list_tenant_users,
        list_tenants,
    )

    ensure_current_tenant()
    tenants = list_tenants()
    cards = []
    for tenant in tenants:
        key = str(tenant.get("tenant_key") or "").strip()
        if not key:
            continue
        try:
            users = list_tenant_users(key)
        except Exception:
            users = []
        try:
            stores = list_tenant_stores(key, active_only=False)
        except Exception:
            stores = []
        try:
            modules = list_tenant_modules(key)
        except Exception:
            modules = []
        try:
            storage_rules = list_storage_rules(key)
        except Exception:
            storage_rules = []
        translation_overrides = 0
        try:
            from app_db import storehub_database_context
            from translation_repository import ensure_translations_schema

            db_name = str(tenant.get("database_name") or "").strip()
            with storehub_database_context(db_name) if db_name else nullcontext():
                ensure_translations_schema()
                conn = get_connection(None, read_only=True)
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(1) FROM dbo.StoreHubTranslations WHERE customized = 1")
                    translation_overrides = int(cur.fetchone()[0] or 0)
                finally:
                    conn.close()
        except Exception:
            translation_overrides = 0
        cards.append(
            {
                "tenant": tenant,
                "users": users,
                "stores": stores,
                "modules": modules,
                "storage_rules": storage_rules,
                "active_users": [u for u in users if bool(u.get("is_active"))],
                "active_stores": [s for s in stores if bool(s.get("is_active"))],
                "active_modules": [m for m in modules if bool(m.get("is_enabled"))],
                "active_storage_rules": [r for r in storage_rules if bool(r.get("is_active"))],
                "translation_overrides": translation_overrides,
            }
        )
    return render_template("master_tenant_assignments.html", cards=cards)


@app.route('/master/users', methods=['GET', 'POST'])
@login_required
@master_required
def master_users():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        name = (request.form.get('name') or '').strip()
        if not email:
            flash("Inserisci un'email valida.", "warning")
            return redirect(url_for("master_users"))
        try:
            redirect_to = _auth_reset_redirect_url()
            resp = sb_admin_invite_user(email, redirect_to=redirect_to)
            uid_val = None
            if isinstance(resp, dict):
                uid_val = resp.get('id') or (resp.get('user') or {}).get('id') or resp.get('user_id')
            elif isinstance(resp, str):
                uid_val = resp
            if not uid_val:
                profiles = sb_admin_list_profiles()
                profile = next((p for p in profiles if str(p.get("email") or "").strip().lower() == email), None)
                uid_val = (profile or {}).get("id")
            if uid_val:
                try:
                    sb_admin_update_profile(uid_val, name=name, role="master", ai_enabled=False, is_master=True)
                except Exception:
                    sb_admin_update_profile(uid_val, name=name, role="master", ai_enabled=False)
                flash("Invito Master inviato.", "success")
            else:
                flash("Invito inviato, ma non ho trovato il profilo da aggiornare a Master.", "warning")
            return redirect(url_for("master_users"))
        except Exception as e:
            current_app.logger.exception("Errore creazione utente master")
            flash(f"Errore creando utente Master: {e}", "danger")

    try:
        profiles = sb_admin_list_profiles()
    except Exception as e:
        current_app.logger.exception("Errore lista utenti master")
        flash(f"Errore caricando utenti Master: {e}", "danger")
        profiles = []
    users = [
        p for p in profiles
        if bool(p.get("is_master")) or str(p.get("role") or "").strip().lower() == "master"
    ]
    users.sort(key=lambda p: str(p.get("email") or "").lower())
    return render_template("master_users.html", users=users)


def _create_or_update_first_tenant_admin(
    *,
    tenant_key: str,
    email: str,
    name: str = "",
    password: str = "",
) -> str:
    mail = str(email or "").strip().lower()
    if not mail:
        raise ValueError("Email admin obbligatoria.")
    if not password:
        raise ValueError("Password admin obbligatoria.")
    existing_profiles = sb_admin_list_profiles()
    existing = next((p for p in existing_profiles if str(p.get("email") or "").strip().lower() == mail), None)
    uid_val = str((existing or {}).get("id") or "").strip()
    if not uid_val:
        uid_val = sb_admin_create_user_with_password(mail, password, name=name, role="user")
    else:
        sb_admin_set_password(uid_val, password)
    sb_admin_update_profile(
        uid_val,
        name=str(name or (existing or {}).get("name") or "").strip(),
        role="user",
        ai_enabled=False,
        is_master=False,
    )
    from tenant_config_repository import upsert_tenant_user

    upsert_tenant_user(
        tenant_key=tenant_key,
        email=mail,
        user_id=uid_val,
        tenant_role="admin",
        is_active=True,
    )
    return uid_val


@app.route('/master/admin-tenant', methods=['GET', 'POST'])
@login_required
@master_required
def master_admin_tenant_select():
    from tenant_config_repository import list_tenants

    tenants = [t for t in (list_tenants() or []) if bool((t or {}).get("master_can_admin")) and bool((t or {}).get("is_active", True))]
    if request.method == "POST":
        tenant_key = str(request.form.get("tenant_key") or "").strip()
        tenant = next((t for t in tenants if str(t.get("tenant_key") or "").strip() == tenant_key), None)
        if not tenant:
            flash("Tenant non abilitato per admin da Master.", "warning")
            return redirect(url_for("master_admin_tenant_select"))
        session["master_admin_tenant_key"] = tenant_key
        flash(f"Contesto admin impostato: {tenant.get('display_name') or tenant_key}.", "success")
        return redirect(url_for("admin_stores", tenant_key=tenant_key))
    active_tenant_key = _master_admin_tenant_key()
    return render_template(
        "master_admin_tenant_select.html",
        tenants=tenants,
        active_tenant_key=active_tenant_key,
    )


@app.route('/master/tenants', methods=['GET', 'POST'])
@login_required
@master_required
def master_tenants():
    from tenant_config_repository import (
        ensure_current_tenant,
        get_tenant,
        list_store_field_configs,
        list_tenant_modules,
        list_tenant_stores,
        list_storage_rules,
        list_tenant_users,
        list_tenants,
        save_tenant,
        save_storage_rule,
        set_tenant_module_enabled,
        set_storage_rule_active,
        set_store_field_group_visible,
        set_store_field_visible,
        set_tenant_active,
        set_tenant_user_active,
        tenant_status,
        upsert_tenant_store,
        upsert_tenant_user,
    )

    ensure_current_tenant()
    status = None
    edit_key = (
        request.args.get("tenant_key")
        or request.form.get("tenant_key")
        or session.get("master_tenant_key")
        or "default"
    )
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        try:
            if action == "save_tenant":
                tenant = save_tenant(
                    tenant_key=request.form.get("tenant_key") or "",
                    display_name=request.form.get("display_name") or "",
                    database_name=request.form.get("database_name") or "",
                    is_active=(request.form.get("is_active") == "1"),
                    sql_server=request.form.get("sql_server") or "",
                    sql_user=request.form.get("sql_user") or "",
                    storage_type=request.form.get("storage_type") or "local_vm",
                    storage_base_path=request.form.get("storage_base_path") or "",
                    sharepoint_sharing_url=request.form.get("sharepoint_sharing_url") or "",
                    sharepoint_base_path=request.form.get("sharepoint_base_path") or "",
                    notes=request.form.get("notes") or "",
                    tenant_admin_enabled=(request.form.get("tenant_admin_enabled") == "1"),
                    master_can_admin=(request.form.get("master_can_admin") == "1"),
                    max_users=request.form.get("max_users") or None,
                    max_stores=request.form.get("max_stores") or None,
                    scheduling_enabled=(request.form.get("scheduling_enabled") == "1"),
                    ai_enabled=(request.form.get("ai_enabled") == "1"),
                    multilanguage_enabled=(request.form.get("multilanguage_enabled") == "1"),
                    enabled_language_codes=request.form.getlist("enabled_language_codes"),
                )
                first_admin_email = (request.form.get("first_admin_email") or "").strip().lower()
                first_admin_name = (request.form.get("first_admin_name") or "").strip()
                first_admin_password = (request.form.get("first_admin_password") or "").strip()
                if bool(tenant.get("tenant_admin_enabled")) and first_admin_email and first_admin_password:
                    try:
                        _create_or_update_first_tenant_admin(
                            tenant_key=tenant.get("tenant_key"),
                            email=first_admin_email,
                            name=first_admin_name,
                            password=first_admin_password,
                        )
                        flash("Primo admin tenant creato e associato.", "success")
                    except Exception as admin_exc:
                        current_app.logger.exception("Errore creazione primo admin tenant")
                        flash(f"Tenant salvato, ma il primo admin non e stato creato: {admin_exc}", "warning")
                session["master_tenant_key"] = tenant.get("tenant_key")
                flash("Tenant salvato.", "success")
                return redirect(url_for("master_tenants", tenant_key=tenant.get("tenant_key")))
            if action == "toggle_active":
                key = request.form.get("tenant_key") or ""
                active = (request.form.get("active") == "1")
                if set_tenant_active(key, active):
                    flash("Stato tenant aggiornato.", "success")
                else:
                    flash("Tenant non trovato.", "warning")
                return redirect(url_for("master_tenants", tenant_key=key))
            if action == "check_status":
                edit_key = request.form.get("tenant_key") or edit_key
                status = tenant_status(edit_key)
                session["master_tenant_key"] = edit_key
            if action == "initialize_tenant_schema":
                edit_key = request.form.get("tenant_key") or edit_key
                tenant = get_tenant(edit_key)
                from tenant_bootstrap_repository import initialize_tenant_database

                status = tenant_status(edit_key)
                status["bootstrap"] = initialize_tenant_database(
                    str(tenant.get("database_name") or "").strip(),
                    tenant_key=edit_key,
                )
                session["master_tenant_key"] = edit_key
                if status["bootstrap"].get("ok"):
                    flash("Schema tenant inizializzato.", "success")
                else:
                    flash("Inizializzazione schema completata con errori: controlla il dettaglio.", "warning")
            if action == "assign_user":
                edit_key = request.form.get("tenant_key") or edit_key
                user_email = (request.form.get("user_email") or "").strip().lower()
                profiles = sb_admin_list_profiles()
                profile = next((p for p in profiles if str(p.get("email") or "").strip().lower() == user_email), {})
                tenant_role = request.form.get("tenant_role") or profile.get("role") or "user"
                if str(tenant_role or "").strip().lower() == "master":
                    tenant_role = "user"
                tenant = get_tenant(edit_key)
                if str(tenant_role or "").strip().lower() == "admin" and not bool(tenant.get("tenant_admin_enabled")):
                    raise ValueError("Questo tenant non abilita admin propri: usa il master come admin.")
                upsert_tenant_user(
                    tenant_key=edit_key,
                    email=user_email,
                    user_id=profile.get("id"),
                    tenant_role=tenant_role,
                    is_active=True,
                )
                flash("Utente associato al tenant.", "success")
                return redirect(url_for("master_tenants", tenant_key=edit_key))
            if action == "toggle_user":
                edit_key = request.form.get("tenant_key") or edit_key
                user_email = request.form.get("user_email") or ""
                active = (request.form.get("active") == "1")
                if set_tenant_user_active(edit_key, user_email, active):
                    flash("Associazione utente aggiornata.", "success")
                else:
                    flash("Associazione utente non trovata.", "warning")
                return redirect(url_for("master_tenants", tenant_key=edit_key))
            if action == "save_storage_rule":
                edit_key = request.form.get("tenant_key") or edit_key
                save_storage_rule(
                    tenant_key=edit_key,
                    category_key=request.form.get("category_key") or "",
                    display_name=request.form.get("display_name") or "",
                    provider=request.form.get("provider") or "local_vm",
                    base_path=request.form.get("base_path") or "",
                    sharepoint_sharing_url=request.form.get("sharepoint_sharing_url") or "",
                    bucket_name=request.form.get("bucket_name") or "",
                    is_active=(request.form.get("is_active") == "1"),
                    notes=request.form.get("notes") or "",
                )
                flash("Regola storage salvata.", "success")
                return redirect(url_for("master_tenants", tenant_key=edit_key))
            if action == "toggle_storage_rule":
                edit_key = request.form.get("tenant_key") or edit_key
                category_key = request.form.get("category_key") or ""
                active = (request.form.get("active") == "1")
                if set_storage_rule_active(edit_key, category_key, active):
                    flash("Regola storage aggiornata.", "success")
                else:
                    flash("Regola storage non trovata.", "warning")
                return redirect(url_for("master_tenants", tenant_key=edit_key))
            if action == "toggle_store_field":
                edit_key = request.form.get("tenant_key") or edit_key
                field_key = request.form.get("field_key") or ""
                visible = (request.form.get("visible") == "1")
                if set_store_field_visible(edit_key, field_key, visible):
                    flash("Visibilita campo store aggiornata.", "success")
                else:
                    flash("Campo store non aggiornato.", "warning")
                return redirect(url_for("master_tenants", tenant_key=edit_key))
            if action == "toggle_store_field_group":
                edit_key = request.form.get("tenant_key") or edit_key
                field_group = request.form.get("field_group") or ""
                visible = (request.form.get("visible") == "1")
                count = set_store_field_group_visible(edit_key, field_group, visible)
                flash(f"Visibilita gruppo aggiornata: {count} campi.", "success")
                return redirect(url_for("master_tenants", tenant_key=edit_key, section="fields"))
            if action == "toggle_tenant_module":
                edit_key = request.form.get("tenant_key") or edit_key
                module_key = request.form.get("module_key") or ""
                enabled = (request.form.get("enabled") == "1")
                if set_tenant_module_enabled(edit_key, module_key, enabled):
                    flash("Visibilita modulo aggiornata.", "success")
                else:
                    flash("Modulo non aggiornato.", "warning")
                return redirect(url_for("master_tenants", tenant_key=edit_key, section="modules"))
            if action == "save_tenant_store":
                edit_key = request.form.get("tenant_key") or edit_key
                store_code = request.form.get("store_code") or ""
                store_name = request.form.get("store_name") or ""
                is_active = (request.form.get("is_active") == "1")
                upsert_tenant_store(
                    tenant_key=edit_key,
                    store_code=store_code,
                    store_name=store_name,
                    is_active=is_active,
                )
                try:
                    from app_db import storehub_database_context
                    from store_registry_repository import upsert_store_registry

                    tenant = get_tenant(edit_key)
                    tenant_db = str(tenant.get("database_name") or "").strip()
                    if tenant_db:
                        with storehub_database_context(tenant_db):
                            upsert_store_registry(
                                store_code=store_code,
                                store_name=store_name,
                                is_active=is_active,
                            )
                            assign_default_price_list_to_store(store_code)
                except Exception as seed_exc:
                    current_app.logger.warning("Inizializzazione store tenant non completa: %s", seed_exc)
                    flash(f"Store associato, ma anagrafica tenant non inizializzata: {seed_exc}", "warning")
                flash("Store tenant salvato.", "success")
                return redirect(url_for("master_tenants", tenant_key=edit_key))
        except Exception as e:
            current_app.logger.exception("Errore gestione tenant")
            flash(f"Errore gestione tenant: {e}", "danger")

    tenants = list_tenants()
    edit_tenant = get_tenant(edit_key)
    if not edit_tenant and tenants:
        edit_tenant = tenants[0]
    selected_tenant_key = str((edit_tenant or {}).get("tenant_key") or "")
    tenant_users = list_tenant_users(selected_tenant_key) if selected_tenant_key else []
    storage_rules = list_storage_rules(selected_tenant_key) if selected_tenant_key else []
    tenant_modules = list_tenant_modules(selected_tenant_key) if selected_tenant_key else []
    store_field_configs = list_store_field_configs(selected_tenant_key) if selected_tenant_key else []
    tenant_stores = list_tenant_stores(selected_tenant_key, active_only=False) if selected_tenant_key else []
    selected_tenant_status = None
    if selected_tenant_key:
        try:
            selected_tenant_status = tenant_status(selected_tenant_key)
        except Exception:
            selected_tenant_status = None
    tenant_metrics = {}
    for t in tenants:
        key = str((t or {}).get("tenant_key") or "").strip()
        if not key:
            continue
        try:
            users_all = list_tenant_users(key)
            stores_all = list_tenant_stores(key, active_only=False)
            modules_all = list_tenant_modules(key)
            rules_all = list_storage_rules(key)
            tenant_metrics[key] = {
                "users_total": len(users_all or []),
                "users_active": len([u for u in (users_all or []) if bool(u.get("is_active"))]),
                "stores_total": len(stores_all or []),
                "stores_active": len([s for s in (stores_all or []) if bool(s.get("is_active"))]),
                "modules_enabled": len([m for m in (modules_all or []) if bool(m.get("is_enabled"))]),
                "modules_total": len(modules_all or []),
                "storage_active": len([r for r in (rules_all or []) if bool(r.get("is_active"))]),
            }
        except Exception:
            tenant_metrics[key] = {
                "users_total": 0,
                "users_active": 0,
                "stores_total": 0,
                "stores_active": 0,
                "modules_enabled": 0,
                "modules_total": 0,
                "storage_active": 0,
            }
    all_stores = []
    try:
        all_stores = get_warehouse_stores() or []
    except Exception:
        all_stores = []
    profiles = []
    try:
        profiles = sb_admin_list_profiles()
    except Exception:
        profiles = []
    return render_template(
        "master_tenants.html",
        tenants=tenants,
        edit_tenant=edit_tenant,
        tenant_users=tenant_users,
        storage_rules=storage_rules,
        tenant_modules=tenant_modules,
        store_field_configs=store_field_configs,
        tenant_stores=tenant_stores,
        selected_tenant_status=selected_tenant_status,
        tenant_metrics=tenant_metrics,
        all_stores=all_stores,
        profiles=profiles,
        status=status,
        selected_tenant_key=selected_tenant_key,
        active_section=(request.args.get("section") or "dashboard").strip().lower(),
        all_ui_languages=_all_ui_languages(),
    )


@app.route('/master/tenant-wizard', methods=['GET', 'POST'])
@login_required
@master_required
def master_tenant_wizard():
    from tenant_config_repository import (
        ensure_current_tenant,
        get_tenant,
        list_tenants,
        save_tenant,
        tenant_status,
    )

    ensure_current_tenant()
    selected_key = (request.values.get("tenant_key") or "").strip()
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        try:
            if action == "create_tenant":
                tenant_key = (request.form.get("tenant_key") or "").strip()
                tenant_admin_enabled = request.form.get("tenant_admin_enabled") == "1"
                tenant = save_tenant(
                    tenant_key=tenant_key,
                    display_name=request.form.get("display_name") or tenant_key,
                    database_name=request.form.get("database_name") or "",
                    is_active=True,
                    storage_type=request.form.get("storage_type") or "local_vm",
                    storage_base_path=request.form.get("storage_base_path") or "",
                    sharepoint_sharing_url=request.form.get("sharepoint_sharing_url") or "",
                    sharepoint_base_path=request.form.get("sharepoint_base_path") or "",
                    tenant_admin_enabled=tenant_admin_enabled,
                    master_can_admin=(request.form.get("master_can_admin") == "1") or not tenant_admin_enabled,
                    max_users=request.form.get("max_users") or None,
                    max_stores=request.form.get("max_stores") or None,
                    scheduling_enabled=(request.form.get("scheduling_enabled") == "1"),
                    ai_enabled=(request.form.get("ai_enabled") == "1"),
                    multilanguage_enabled=(request.form.get("multilanguage_enabled") == "1"),
                    enabled_language_codes=request.form.getlist("enabled_language_codes"),
                    notes=request.form.get("notes") or "",
                )
                selected_key = str(tenant.get("tenant_key") or tenant_key).strip()
                first_admin_email = (request.form.get("first_admin_email") or "").strip().lower()
                first_admin_password = (request.form.get("first_admin_password") or "").strip()
                if tenant_admin_enabled and first_admin_email and first_admin_password:
                    _create_or_update_first_tenant_admin(
                        tenant_key=selected_key,
                        email=first_admin_email,
                        name=request.form.get("first_admin_name") or "",
                        password=first_admin_password,
                    )
                    flash("Tenant creato e primo admin associato.", "success")
                else:
                    flash("Tenant creato. Completa gli step della checklist.", "success")
                session["master_tenant_key"] = selected_key
                return redirect(url_for("master_tenant_wizard", tenant_key=selected_key))
        except Exception as e:
            current_app.logger.exception("Errore wizard tenant")
            flash(f"Errore wizard tenant: {e}", "danger")

    tenants = list_tenants()
    tenant = get_tenant(selected_key) if selected_key else None
    status = None
    if tenant and selected_key:
        try:
            status = tenant_status(selected_key)
        except Exception:
            status = None
    return render_template(
        "master_tenant_wizard.html",
        tenants=tenants,
        tenant=tenant,
        selected_tenant_key=selected_key,
        status=status,
        all_ui_languages=_all_ui_languages(),
    )


@app.route('/master/orari-config', methods=['GET', 'POST'])
@login_required
@master_required
def master_orari_config():
    from tenant_config_repository import ensure_current_tenant, get_tenant, list_tenants
    from orari_config_repository import (
        list_orari_causali,
        list_orari_inquadramenti,
        seed_default_orari_config,
        delete_orari_causale,
        delete_orari_inquadramento,
        upsert_orari_causale,
        upsert_orari_inquadramento,
    )

    ensure_current_tenant()
    tenants = list_tenants()
    tenant_key = (
        request.form.get("tenant_key")
        or request.args.get("tenant_key")
        or session.get("master_tenant_key")
        or "default"
    )
    tenant_key = str(tenant_key or "default").strip() or "default"
    session["master_tenant_key"] = tenant_key

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        try:
            if action == "seed_defaults":
                seed_default_orari_config(tenant_key)
                flash("Configurazione orari standard inserita.", "success")
            elif action == "delete_inquadramento":
                if delete_orari_inquadramento(request.form.get("name") or "", tenant_key):
                    flash("Inquadramento eliminato.", "success")
                else:
                    flash("Inquadramento non trovato.", "warning")
            elif action == "save_inquadramento":
                upsert_orari_inquadramento(
                    {
                        "name": request.form.get("name") or "",
                        "sort_order": request.form.get("sort_order") or 0,
                        "requires_contract_match": request.form.get("requires_contract_match") == "1",
                        "is_active": request.form.get("is_active") == "1",
                    },
                    tenant_key,
                )
                flash("Inquadramento salvato.", "success")
            elif action == "delete_causale":
                if delete_orari_causale(request.form.get("name") or "", tenant_key):
                    flash("Causale eliminata.", "success")
                else:
                    flash("Causale non trovata.", "warning")
            elif action == "save_causale":
                upsert_orari_causale(
                    {
                        "name": request.form.get("name") or "",
                        "sort_order": request.form.get("sort_order") or 0,
                        "justifies_contract_hours": request.form.get("justifies_contract_hours") == "1",
                        "counts_productivity": request.form.get("counts_productivity") == "1",
                        "counts_training": request.form.get("counts_training") == "1",
                        "counts_labor_cost": request.form.get("counts_labor_cost") == "1",
                        "requires_loan_store": request.form.get("requires_loan_store") == "1",
                        "requires_time_range": request.form.get("requires_time_range") == "1",
                        "auto_extra_eligible": request.form.get("auto_extra_eligible") == "1",
                        "is_active": request.form.get("is_active") == "1",
                    },
                    tenant_key,
                )
                flash("Causale salvata.", "success")
            return redirect(url_for("master_orari_config", tenant_key=tenant_key))
        except Exception as e:
            current_app.logger.exception("Errore configurazione orari")
            flash(f"Errore configurazione orari: {e}", "danger")

    seed_default_orari_config(tenant_key)
    return render_template(
        "master_orari_config.html",
        tenants=tenants,
        selected_tenant_key=tenant_key,
        tenant=get_tenant(tenant_key),
        inquadramenti=list_orari_inquadramenti(tenant_key, active_only=False),
        causali=list_orari_causali(tenant_key, active_only=False),
    )


@app.route('/admin/stores', methods=['GET', 'POST'])
@login_required
@admin_or_master_required
def admin_stores():
    from app_db import storehub_database_context
    from tenant_config_repository import (
        current_tenant_key,
        cleanup_shadow_default_tenant_stores,
        delete_tenant_store,
        get_tenant,
        list_store_field_configs,
        list_tenant_stores,
        list_tenant_store_codes,
        upsert_tenant_store,
    )
    from store_registry_repository import delete_store_registry, get_store_registry, list_store_registry, seed_store_registry_from_ilp, upsert_store_registry

    tenant_key = str(session.get("tenant_key") or current_tenant_key()).strip() or current_tenant_key()
    platform_master = _is_platform_master(current_user())
    db_context = nullcontext()
    if platform_master:
        tenant = _resolve_master_admin_tenant()
        if not tenant:
            flash("Seleziona prima il tenant da amministrare.", "warning")
            return redirect(url_for("master_admin_tenant_select"))
        tenant_key = str(tenant.get("tenant_key") or "").strip() or tenant_key
        try:
            tenant_db = str(tenant.get("database_name") or "").strip()
            if tenant_db:
                db_context = storehub_database_context(tenant_db)
        except Exception:
            db_context = nullcontext()

    with db_context:
        is_default_tenant = tenant_key in {current_tenant_key(), "default"}
        store_field_configs = list_store_field_configs(tenant_key)
        visible_store_fields = {
            str(row.get("field_key") or "").strip()
            for row in store_field_configs
            if bool(row.get("is_visible"))
        }
        if request.method == "POST":
            action = (request.form.get("action") or "save").strip().lower()
            code = (request.form.get("code") or "").strip()
            try:
                if action == "seed_legacy":
                    stores_for_seed = get_warehouse_stores(include_inactive=True) or []
                    if platform_master:
                        allowed_codes = list_tenant_store_codes(tenant_key, active_only=False)
                        stores_for_seed = [
                            s for s in stores_for_seed
                            if str((s or {}).get("code") or "").strip() in allowed_codes
                        ] if allowed_codes else []
                    count = seed_store_registry_from_ilp(stores_for_seed, only_missing=False)
                    flash(f"Anagrafica store popolata da legacy ILP: {count} store aggiornati.", "success")
                elif action == "delete":
                    delete_tenant_store(tenant_key, code)
                    delete_store_registry(code)
                    remove_price_list_store_assignment(code)
                    if is_default_tenant:
                        delete_warehouse_user_store_assignments_for_store(code)
                        delete_warehouse_store(code)
                    flash("Store eliminato.", "success")
                else:
                    name = (request.form.get("name") or "").strip()
                    is_active = (request.form.get("is_active") or "1") == "1"
                    sort_order = int(request.form.get("sort_order") or 0)
                    if is_default_tenant:
                        upsert_warehouse_store(code=code, name=name, is_active=is_active, sort_order=sort_order)
                    upsert_tenant_store(
                        tenant_key=tenant_key,
                        store_code=code,
                        store_name=name,
                        is_active=is_active,
                    )
                    existing_reg = get_store_registry(code) or {}

                    def registry_value(field_key: str) -> object:
                        if field_key in visible_store_fields:
                            return request.form.get(field_key) or ""
                        return existing_reg.get(field_key)

                    upsert_store_registry(
                        store_code=code,
                        store_name=name,
                        is_active=is_active,
                        sort_order=sort_order,
                        area_manager=registry_value("area_manager"),
                        yoobic_address=registry_value("yoobic_address"),
                        closure_date=registry_value("closure_date"),
                        opening_date=registry_value("opening_date"),
                        address_line1=registry_value("address_line1"),
                        address_line2=registry_value("address_line2"),
                        postal_code=registry_value("postal_code"),
                        city=registry_value("city"),
                        province=registry_value("province"),
                        country=registry_value("country"),
                        phone=registry_value("phone"),
                        email=registry_value("email"),
                        google_location_id=registry_value("google_location_id"),
                        glovo_store_id=registry_value("glovo_store_id"),
                        deliveroo_store_id=registry_value("deliveroo_store_id"),
                        ipratico_api_key=registry_value("ipratico_api_key"),
                        notes=registry_value("notes"),
                    )
                    assign_default_price_list_to_store(code)
                    flash("Store salvato e associato al tenant.", "success")
            except Exception as e:
                current_app.logger.exception("Errore gestione store admin")
                flash(f"Errore gestione store: {e}", "danger")
            return redirect(url_for("admin_stores", tenant_key=tenant_key))

        try:
            tenant_store_rows = list_tenant_stores(tenant_key, active_only=False)
            registry_by_code = {
                str(row.get("store_code") or "").strip(): row
                for row in (list_store_registry(include_inactive=True) or [])
            }
            if is_default_tenant:
                removed_shadows = cleanup_shadow_default_tenant_stores(tenant_key, set(registry_by_code.keys()))
                if removed_shadows:
                    tenant_store_rows = list_tenant_stores(tenant_key, active_only=False)
            stores = []
            for tenant_store in tenant_store_rows:
                code = str((tenant_store or {}).get("store_code") or "").strip()
                if not code:
                    continue
                reg = registry_by_code.get(code) or {}
                stores.append(
                    {
                        "code": code,
                        "name": reg.get("store_name") or (tenant_store or {}).get("store_name") or code,
                        "is_active": bool(reg.get("is_active", (tenant_store or {}).get("is_active", True))),
                        "sort_order": int(reg.get("sort_order") or 0),
                        "area_manager": reg.get("area_manager") or "",
                        "yoobic_address": reg.get("yoobic_address") or "",
                        "closure_date": reg.get("closure_date"),
                        "google_location_id": reg.get("google_location_id") or "",
                        "glovo_store_id": reg.get("glovo_store_id") or "",
                        "deliveroo_store_id": reg.get("deliveroo_store_id") or "",
                        "ipratico_api_key": reg.get("ipratico_api_key") or "",
                        "notes": reg.get("notes") or "",
                        "opening_date": reg.get("opening_date"),
                        "address_line1": reg.get("address_line1") or "",
                        "address_line2": reg.get("address_line2") or "",
                        "postal_code": reg.get("postal_code") or "",
                        "city": reg.get("city") or "",
                        "province": reg.get("province") or "",
                        "country": reg.get("country") or "",
                        "phone": reg.get("phone") or "",
                        "email": reg.get("email") or "",
                    }
                )
            stores.sort(key=lambda s: (int(s.get("sort_order") or 0), str(s.get("code") or "")))
        except Exception as e:
            current_app.logger.exception("Errore caricamento store admin")
            flash(f"Errore caricamento store: {e}", "warning")
            stores = []
            store_field_configs = []
            visible_store_fields = set()
    return render_template(
        "admin_stores.html",
        stores=stores,
        tenant_key=tenant_key,
        store_field_configs=store_field_configs,
        visible_store_fields=visible_store_fields,
    )


@app.route('/admin/area-managers', methods=['GET', 'POST'])
@login_required
@admin_or_master_required
def admin_area_managers():
    from app_db import storehub_database_context
    from tenant_config_repository import current_tenant_key, get_tenant, list_tenant_store_codes
    from store_registry_repository import (
        assign_area_manager_to_stores,
        list_area_managers,
        list_store_registry,
        set_area_manager_active,
        sync_area_managers_from_store_registry,
        upsert_area_manager,
    )

    tenant_key = str(session.get("tenant_key") or current_tenant_key()).strip() or current_tenant_key()
    platform_master = _is_platform_master(current_user())
    db_context = nullcontext()
    if platform_master:
        tenant = _resolve_master_admin_tenant()
        if not tenant:
            flash("Seleziona prima il tenant da amministrare.", "warning")
            return redirect(url_for("master_admin_tenant_select"))
        tenant_key = str(tenant.get("tenant_key") or "").strip() or tenant_key
        try:
            tenant_db = str(tenant.get("database_name") or "").strip()
            if tenant_db:
                db_context = storehub_database_context(tenant_db)
        except Exception:
            db_context = nullcontext()

    with db_context:
        if request.method == "POST":
            action = (request.form.get("action") or "").strip().lower()
            try:
                if action == "save_am":
                    upsert_area_manager(
                        name=request.form.get("name") or "",
                        code=request.form.get("code") or "",
                        email=request.form.get("email") or "",
                        phone=request.form.get("phone") or "",
                        is_active=(request.form.get("is_active") == "1"),
                    )
                    flash("Area manager salvato.", "success")
                elif action == "toggle_am":
                    name = request.form.get("name") or ""
                    active = (request.form.get("active") == "1")
                    if set_area_manager_active(name, active):
                        flash("Area manager aggiornato.", "success")
                    else:
                        flash("Area manager non trovato.", "warning")
                elif action == "assign":
                    count = assign_area_manager_to_stores(
                        request.form.get("area_manager") or "",
                        request.form.getlist("store_codes"),
                    )
                    flash(f"Assegnazione aggiornata: {count} store.", "success")
                elif action == "sync_from_stores":
                    count = sync_area_managers_from_store_registry()
                    flash(f"Elenco area manager sincronizzato dagli store: {count} nominativi.", "success")
            except Exception as e:
                current_app.logger.exception("Errore gestione area manager")
                flash(f"Errore gestione area manager: {e}", "danger")
            return redirect(url_for("admin_area_managers", tenant_key=tenant_key))

        area_managers = list_area_managers(include_inactive=True)
        registry_rows = list_store_registry(include_inactive=True)
        try:
            stores = get_warehouse_stores(include_inactive=True) or []
            if platform_master:
                allowed_codes = list_tenant_store_codes(tenant_key, active_only=False)
                stores = [s for s in stores if str((s or {}).get("code") or "").strip() in allowed_codes] if allowed_codes else []
        except Exception:
            stores = []
        registry_by_code = {str(r.get("store_code") or "").strip(): r for r in registry_rows}
        for store in stores:
            reg = registry_by_code.get(str((store or {}).get("code") or "").strip()) or {}
            store["area_manager"] = reg.get("area_manager") or ""

    return render_template(
        "admin_area_managers.html",
        tenant_key=tenant_key,
        area_managers=area_managers,
        stores=stores,
    )


def _digits_only_local(v: str) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _norm_store_match_text(v: str) -> str:
    s = str(v or "").upper().strip()
    for token in ("I LOVE POKE", "ILOVE POKE", "LOVE POKE"):
        s = s.replace(token, " ")
    for ch in "-_/.,;:()[]{}":
        s = s.replace(ch, " ")
    s = " ".join(part for part in s.split() if part)
    return s


def _store_match_tokens(v: str) -> list[str]:
    generic = {
        "ILOVEPOKE", "I", "LOVE", "POKE", "THE", "STORE", "RISTORANTE", "RIST", "LOC", "SEDE",
        "CC", "C", "IL", "LA", "LE", "LO", "DI", "DEL", "DELLA", "DELLE", "PIAZZA", "CENTRO",
    }
    compact = _norm_store_match_text(v).replace(" ", "")
    raw_parts = [p for p in _norm_store_match_text(v).split() if p]
    out: list[str] = []
    if compact and compact not in generic:
        out.append(compact)
    for part in raw_parts:
        c = part.replace(" ", "")
        if len(c) >= 3 and c not in generic and c not in out:
            out.append(c)
    return out


def _normalize_bank_name(v: str) -> str:
    raw = str(v or "").upper().strip()
    compact = "".join(ch for ch in raw if ch.isalnum())
    if not compact:
        return ""
    if "INTESA" in compact or "SANPAOLO" in compact or "SANPAULO" in compact:
        return "INTESA"
    if "UNICREDIT" in compact or "UNCRIT" in compact:
        return "UNICREDIT"
    if compact.startswith("BPM") or "BANCOBPM" in compact or "BANCOBPM" in compact:
        return "BPM"
    return compact


def _build_store_match_catalog(stores: list[dict]) -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    for s in stores or []:
        code = str((s or {}).get("code") or "").strip()
        name = str((s or {}).get("name") or code).strip()
        if not code:
            continue
        aliases = _store_match_tokens(name)
        if code not in aliases:
            aliases.append(code)
        catalog[code] = {
            "code": code,
            "name": name,
            "norm": _norm_store_match_text(name),
            "aliases": aliases,
        }
    return catalog


def _infer_finance_store(finance_row: dict, store_catalog: dict[str, dict]) -> dict:
    note = str((finance_row or {}).get("nota") or "").strip()
    note_norm = _norm_store_match_text(note)
    note_compact = note_norm.replace(" ", "")
    if not note_compact:
        return {"code": "", "name": "", "score": 0.0, "method": ""}

    best = {"code": "", "name": "", "score": 0.0, "method": ""}
    note_tokens = set(_store_match_tokens(note))
    for code, meta in (store_catalog or {}).items():
        score = 0.0
        method = ""
        for alias in meta.get("aliases") or []:
            alias = str(alias or "").strip().upper()
            if not alias:
                continue
            if alias == code and alias in note_compact:
                score = max(score, 100.0)
                method = "store code"
            elif alias and alias in note_compact:
                score = max(score, 96.0 if alias == meta.get("norm", "").replace(" ", "") else 92.0)
                method = "alias contains"
        if score < 90:
            aliases = set(meta.get("aliases") or [])
            overlap = len(note_tokens & aliases)
            if overlap:
                token_ratio = overlap / max(1, len(aliases))
                cand = round(70.0 + token_ratio * 20.0, 2)
                if cand > score:
                    score = cand
                    method = "token overlap"
        if score < 75:
            sim = SequenceMatcher(None, meta.get("norm", ""), note_norm).ratio()
            if sim >= 0.55:
                cand = round(sim * 100.0, 2)
                if cand > score:
                    score = cand
                    method = "similarity"
        if score > best["score"]:
            best = {"code": code, "name": str(meta.get("name") or code), "score": score, "method": method}
    return best


def _money_to_float_local(v: Any) -> float:
    raw = str(v or "").strip()
    if raw and ("." in raw) and ("," in raw):
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    try:
        return float(raw or 0)
    except Exception:
        return 0.0


def _parse_finance_date_local(value: Any):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        try:
            return value if not isinstance(value, str) else None
        except Exception:
            pass
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def _extract_finance_codes_from_texts_local(*values: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in values:
        s = str(raw or "").strip()
        if not s:
            continue
        for code in re.findall(r"(?<!\d)(\d{6,20})(?!\d)", s):
            norm = "".join(ch for ch in code if ch.isdigit())
            if norm and norm not in seen:
                seen.add(norm)
                found.append(norm)
    return found


def _norm_finance_store_name_local(value: str) -> str:
    s = str(value or "").upper().strip()
    s = s.replace("I LOVE POKE", " ")
    s = s.replace("ILOVE POKE", " ")
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _finance_store_aliases_local(value: str) -> set[str]:
    raw = str(value or "").strip()
    aliases: set[str] = set()
    norm = _norm_finance_store_name_local(raw)
    if norm:
        aliases.add(norm)
        parts = [p.strip() for p in re.split(r"\s*-\s*", raw) if p.strip()]
        for p in parts:
            pn = _norm_finance_store_name_local(p)
            if pn and not pn.isdigit():
                aliases.add(pn)
        for token in norm.split():
            if token and not token.isdigit() and len(token) >= 3:
                aliases.add(token)
    return aliases


def _build_app_store_catalog_local(stores: list[dict]) -> list[dict]:
    out: list[dict] = []
    for raw in stores or []:
        code = str((raw or {}).get("code") or "").strip()
        name = str((raw or {}).get("name") or "").strip()
        if not code and not name:
            continue
        aliases = _finance_store_aliases_local(name)
        norm_name = _norm_finance_store_name_local(name)
        tokens = {tok for tok in norm_name.split() if len(tok) >= 3 and not tok.isdigit()}
        if code:
            aliases.add(code.upper())
        out.append(
            {
                "code": code,
                "name": name,
                "label": f"{code} - {name}" if code and name else (name or code),
                "aliases": {a for a in aliases if a},
                "tokens": tokens,
            }
        )
    return out


def _resolve_store_code_from_assignments_local(code: str, record_date, assignments_by_code: dict[str, list[dict]]) -> str:
    rows = list(assignments_by_code.get(str(code or "").strip(), []) or [])
    if not rows:
        return ""
    if record_date is None:
        return str((rows[0] or {}).get("StoreCode") or "").strip()
    best_code = ""
    best_from = None
    for row in rows:
        valid_from = _parse_finance_date_local((row or {}).get("ValidFrom"))
        if valid_from is None:
            continue
        if valid_from <= record_date and (best_from is None or valid_from > best_from):
            best_from = valid_from
            best_code = str((row or {}).get("StoreCode") or "").strip()
    return best_code


def _match_store_from_text_local(combined_text: str, app_catalog: list[dict]) -> tuple[str, str, float]:
    text_norm = _norm_finance_store_name_local(combined_text)
    if not text_norm:
        return "", "", 0.0

    best_score = 0.0
    best_label = ""
    best_code = ""
    text_tokens = {tok for tok in text_norm.split() if len(tok) >= 3 and not tok.isdigit()}

    for item in app_catalog:
        score = 0.0
        for alias in item.get("aliases") or []:
            alias_norm = _norm_finance_store_name_local(alias)
            if not alias_norm or len(alias_norm) < 3:
                continue
            if text_norm == alias_norm:
                score = max(score, 100.0)
            elif alias_norm in text_norm:
                score = max(score, 92.0 if len(alias_norm) >= 6 else 84.0)
        item_tokens = set(item.get("tokens") or set())
        if item_tokens and text_tokens:
            overlap = len(item_tokens & text_tokens)
            if overlap:
                coverage = overlap / max(1, len(item_tokens))
                score = max(score, float(int(60 + coverage * 30)))
        if score > best_score:
            best_score = score
            best_code = str(item.get("code") or "").strip()
            best_label = str(item.get("label") or "").strip()

    return best_code, best_label, best_score


def _infer_finance_store_pos(finance_row: dict, stores: list[dict], catalog_map: dict[str, dict] | None = None, assignments_by_code: dict[str, list[dict]] | None = None, app_catalog: list[dict] | None = None, app_by_code: dict[str, str] | None = None) -> dict:
    texts = [
        str((finance_row or {}).get("store") or "").strip(),
        str((finance_row or {}).get("descrizione") or "").strip(),
        str((finance_row or {}).get("nota") or "").strip(),
    ]
    codes = _extract_finance_codes_from_texts_local(*texts)
    catalog_map = catalog_map if catalog_map is not None else get_code_catalog_map(codes)
    assignments_by_code = assignments_by_code if assignments_by_code is not None else get_assignments_by_code(codes)
    app_catalog = app_catalog if app_catalog is not None else _build_app_store_catalog_local(stores)
    app_by_code = app_by_code if app_by_code is not None else {str((s or {}).get("code") or "").strip(): str((s or {}).get("name") or "").strip() for s in stores or []}
    record_date = (
        _parse_finance_date_local((finance_row or {}).get("data_contabile_raw"))
        or _parse_finance_date_local((finance_row or {}).get("data_valuta_raw"))
        or _parse_finance_date_local((finance_row or {}).get("data_inserimento"))
    )

    for code in codes:
        resolved = _resolve_store_code_from_assignments_local(code, record_date, assignments_by_code)
        if resolved:
            return {
                "code": resolved,
                "name": app_by_code.get(resolved, resolved),
                "score": 100.0,
                "method": "assigned code",
                "code_value": code,
            }

    combined = " ".join([t for t in texts if t]).strip()
    for code in codes:
        code_info = catalog_map.get(code) or {}
        code_label = str(code_info.get("SourceLabel") or "").strip()
        if not code_label:
            continue
        resolved, _label, score = _match_store_from_text_local(code_label, app_catalog)
        if resolved:
            return {
                "code": resolved,
                "name": app_by_code.get(resolved, resolved),
                "score": score,
                "method": "catalog label",
                "code_value": code,
            }

    resolved, _label, score = _match_store_from_text_local(combined, app_catalog)
    if resolved:
        return {
            "code": resolved,
            "name": app_by_code.get(resolved, resolved),
            "score": score,
            "method": "text",
            "code_value": codes[0] if codes else "",
        }
    return {"code": "", "name": "", "score": 0.0, "method": "", "code_value": codes[0] if codes else ""}


def _infer_pos_emitter(finance_row: dict) -> str:
    combined = " ".join(
        [
            str((finance_row or {}).get("banca") or ""),
            str((finance_row or {}).get("categoria") or ""),
            str((finance_row or {}).get("tipo") or ""),
            str((finance_row or {}).get("store") or ""),
            str((finance_row or {}).get("descrizione") or ""),
            str((finance_row or {}).get("nota") or ""),
        ]
    ).upper()
    if "AMEX" in combined or "AMERICAN EXPRESS" in combined:
        return "AMEX"
    if "NUMIA" in combined:
        return "NUMIA"
    if "NEXI" in combined:
        return "NEXI"
    if "AXERVE" in combined:
        return "AXERVE"
    if "WORLDLINE" in combined:
        return "WORLDLINE"
    if "SUMUP" in combined:
        return "SUMUP"
    return ""


def _build_finance_match_label(fin: dict) -> str:
    parts = [
        str((fin or {}).get("data_contabile_raw") or "").strip() or "-",
        f"{float((fin or {}).get('importo') or 0):.2f}".replace(".", ","),
        str((fin or {}).get("banca") or "").strip(),
        str((fin or {}).get("nota") or "").strip(),
        str((fin or {}).get("numero_tessera") or "").strip(),
    ]
    return " | ".join([p for p in parts if p])


def _build_pos_finance_match_label(fin: dict) -> str:
    parts = [
        str((fin or {}).get("source_table") or "").strip().upper(),
        str((fin or {}).get("data_contabile_raw") or "").strip() or "-",
        f"{float((fin or {}).get('importo') or 0):.2f}".replace(".", ","),
        str((fin or {}).get("emitter") or "").strip(),
        str((fin or {}).get("descrizione") or "").strip(),
        str((fin or {}).get("nota") or "").strip(),
    ]
    return " | ".join([p for p in parts if p])


def _score_finance_candidate(app_row: dict, finance_row: dict, store_name: str) -> tuple[float, dict]:
    reasons: list[str] = []
    score = 0.0
    app_bank = _normalize_bank_name((app_row or {}).get("riferimento"))
    fin_bank = _normalize_bank_name((finance_row or {}).get("banca"))
    strong_tessera_match = False

    if app_bank and fin_bank and app_bank != fin_bank:
        return 0.0, {"reasons": ["banca diversa"]}

    if app_bank and fin_bank and app_bank == fin_bank:
        score += 20.0
        reasons.append("banca")

    app_amount = float((app_row or {}).get("valore") or 0.0)
    fin_amount = float((finance_row or {}).get("importo") or 0.0)
    if abs(app_amount - fin_amount) < 0.01:
        score += 45.0
        reasons.append("importo")
    elif abs(app_amount - fin_amount) <= 1.0:
        score += 15.0
        reasons.append("importo vicino")

    app_tess = _digits_only_local((app_row or {}).get("tessera"))
    fin_tess = _digits_only_local((finance_row or {}).get("numero_tessera"))
    if app_tess and app_tess != "0" and fin_tess:
        if fin_tess == app_tess:
            score += 35.0
            reasons.append("tessera completa")
            strong_tessera_match = True
        elif len(fin_tess) <= 4 and app_tess.endswith(fin_tess):
            score += 18.0
            reasons.append("ultime 4 tessera")

    app_date = str((app_row or {}).get("data_versamento_iso") or "").strip()
    fin_dates = [
        str((finance_row or {}).get("data_contabile_iso") or "").strip(),
        str((finance_row or {}).get("data_valuta_iso") or "").strip(),
    ]
    best_date_pts = 0.0
    has_antecedent_date = False
    if app_date:
        try:
            d_app = datetime.strptime(app_date, "%Y-%m-%d").date()
        except Exception:
            d_app = None
        if d_app:
            for fd in fin_dates:
                if not fd:
                    continue
                try:
                    d_fin = datetime.strptime(fd, "%Y-%m-%d").date()
                except Exception:
                    continue
                delta = abs((d_fin - d_app).days)
                if delta == 0:
                    best_date_pts = max(best_date_pts, 25.0)
                elif delta == 1:
                    best_date_pts = max(best_date_pts, 18.0)
                elif delta <= 3:
                    best_date_pts = max(best_date_pts, 10.0)
    if best_date_pts:
        score += best_date_pts
        reasons.append("data")

    inferred_code = str((finance_row or {}).get("inferred_store_code") or "").strip()
    inferred_score = float((finance_row or {}).get("inferred_store_score") or 0.0)
    app_site = str((app_row or {}).get("site") or "").strip()
    if inferred_code and inferred_code == app_site:
        if inferred_score >= 95:
            score += 40.0
            reasons.append("store note forte")
        elif inferred_score >= 85:
            score += 32.0
            reasons.append("store note")
        elif inferred_score >= 70:
            score += 20.0
            reasons.append("store probabile")
    else:
        note_norm = _norm_store_match_text((finance_row or {}).get("nota"))
        store_norm = _norm_store_match_text(store_name)
        if note_norm and store_norm:
            if store_norm in note_norm or note_norm in store_norm:
                score += 20.0
                reasons.append("store/note")
            else:
                sim = SequenceMatcher(None, store_norm, note_norm).ratio()
                if sim >= 0.55:
                    pts = round(sim * 20.0, 2)
                    score += pts
                    reasons.append("similarita note")

    amount_diff = abs(app_amount - fin_amount)
    if amount_diff > 1.0 and not (strong_tessera_match and amount_diff <= 5.0):
        score = min(score, 69.0)
        reasons.append("importo da verificare")

    return min(score, 100.0), {"reasons": reasons}


def _load_app_match_row(*, app_record_key: str, app_store: str, start_iso: str, end_iso: str) -> dict | None:
    store_code = str(app_store or "").strip()
    if not store_code:
        return None
    try:
        start = datetime.strptime(str(start_iso or "").strip(), "%Y-%m-%d").date()
        end = datetime.strptime(str(end_iso or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return None
    res = search_versamenti_range_multi([store_code], start=start, end=end) or {}
    for row in (res.get("rows") or []):
        if build_app_record_key(row) == str(app_record_key or "").strip():
            return row
    return None


def _selected_finance_rows_for_match(finance_ids: list[int], *, start_iso: str, end_iso: str) -> list[dict]:
    if not finance_ids:
        return []
    finance_rows = list_finance_versamenti(start_iso=start_iso, end_iso=end_iso, assignment_filter="all") or []
    by_id = {int(r["id"]): r for r in finance_rows if r.get("id") is not None}
    missing = [fid for fid in finance_ids if fid not in by_id]
    if missing:
        extra_rows = list_finance_versamenti(assignment_filter="all") or []
        for row in extra_rows:
            by_id.setdefault(int(row["id"]), row)
    return [by_id[fid] for fid in finance_ids if fid in by_id]


def _validate_finance_match_selection(
    *,
    app_row: dict,
    selected_finance_rows: list[dict],
    app_record_key: str,
    amount_tolerance: float = 1.0,
) -> dict:
    app_value = float((app_row or {}).get("valore") or 0.0)
    if not selected_finance_rows:
        return {"ok": True, "finance_total": 0.0, "app_value": app_value, "diff": 0.0}

    app_site = str((app_row or {}).get("site") or "").strip()
    app_bank = _normalize_bank_name((app_row or {}).get("riferimento"))
    app_tess = _digits_only_local((app_row or {}).get("tessera"))
    app_date = str((app_row or {}).get("data_versamento_iso") or "").strip()
    try:
        d_app = datetime.strptime(app_date, "%Y-%m-%d").date() if app_date else None
    except Exception:
        d_app = None

    total = 0.0
    banks: set[str] = set()
    used_ids: set[int] = set()
    warnings: list[str] = []

    for fin in selected_finance_rows:
        fin_id = int(fin.get("id") or 0)
        if not fin_id or fin_id in used_ids:
            return {"ok": False, "error": "Lo stesso versamento finance non può essere usato più di una volta nello stesso match."}
        used_ids.add(fin_id)

        assigned_key = str(fin.get("assigned_app_record_key") or "").strip()
        if bool(fin.get("assigned")) and assigned_key and assigned_key != str(app_record_key or "").strip():
            return {"ok": False, "error": f"Il versamento finance {fin_id} è già associato a un altro versamento app."}

        fin_bank = _normalize_bank_name(fin.get("banca"))
        if fin_bank:
            banks.add(fin_bank)
        if app_bank and fin_bank and app_bank != fin_bank:
            return {"ok": False, "error": f"Il versamento finance {fin_id} appartiene a una banca diversa da quella indicata nel versamento app."}

        inferred_code = str(fin.get("inferred_store_code") or "").strip()
        inferred_score = float(fin.get("inferred_store_score") or 0.0)
        if inferred_code and app_site and inferred_code != app_site and inferred_score >= 70.0:
            return {"ok": False, "error": f"Il versamento finance {fin_id} sembra appartenere allo store {inferred_code}, non allo store {app_site}."}

        fin_tess = _digits_only_local(fin.get("numero_tessera"))
        if app_tess and app_tess != "0" and fin_tess:
            if len(fin_tess) > 4 and fin_tess != app_tess:
                warnings.append(f"Il versamento finance {fin_id} ha una tessera diversa da quella registrata su Store Hub.")
            if len(fin_tess) <= 4 and not app_tess.endswith(fin_tess):
                warnings.append(f"Il versamento finance {fin_id} ha finali tessera non coerenti con Store Hub.")

        fin_date = str(fin.get("data_contabile_iso") or fin.get("data_valuta_iso") or "").strip()
        if d_app and fin_date:
            try:
                d_fin = datetime.strptime(fin_date, "%Y-%m-%d").date()
            except Exception:
                d_fin = None
            if d_fin and abs((d_fin - d_app).days) > 3:
                return {"ok": False, "error": f"Il versamento finance {fin_id} è troppo distante dalla data del versamento app."}

        total += float(fin.get("importo") or 0.0)

    if len(banks) > 1:
        return {"ok": False, "error": "Non è possibile associare nello stesso match versamenti finance di banche diverse."}

    diff = round(total - app_value, 2)
    if abs(diff) > float(amount_tolerance or 0.0):
        return {
            "ok": False,
            "error": (
                f"Il totale dei versamenti finance selezionati ({total:,.2f} €) non è coerente con il valore "
                f"registrato in Store Hub ({app_value:,.2f} €). Scostamento: {diff:,.2f} €."
            ).replace(",", "X").replace(".", ",").replace("X", "."),
            "finance_total": total,
            "app_value": app_value,
            "diff": diff,
            "warnings": warnings,
        }

    return {"ok": True, "finance_total": total, "app_value": app_value, "diff": diff, "warnings": warnings}


def _pick_finance_combo(app_row: dict, finance_pool: list[dict]) -> list[dict]:
    if not finance_pool:
        return []
    app_amount = float((app_row or {}).get("valore") or 0.0)
    app_date = str((app_row or {}).get("data_versamento_iso") or "").strip()
    app_site = str((app_row or {}).get("site") or "").strip()
    app_bank = _normalize_bank_name((app_row or {}).get("riferimento"))
    app_tess = _digits_only_local((app_row or {}).get("tessera"))

    try:
        d_app = datetime.strptime(app_date, "%Y-%m-%d").date() if app_date else None
    except Exception:
        d_app = None

    eligible: list[dict] = []
    for fin in finance_pool:
        if bool(fin.get("assigned_to_other")):
            continue
        fin_bank = _normalize_bank_name(fin.get("banca"))
        if app_bank and fin_bank and app_bank != fin_bank:
            continue
        inferred_code = str(fin.get("inferred_store_code") or "").strip()
        inferred_score = float(fin.get("inferred_store_score") or 0.0)
        if inferred_code and inferred_code != app_site:
            continue
        if inferred_code == app_site and inferred_score < 75:
            continue
        fin_date = str(fin.get("data_contabile_iso") or fin.get("data_valuta_iso") or "").strip()
        if d_app and fin_date:
            try:
                d_fin = datetime.strptime(fin_date, "%Y-%m-%d").date()
            except Exception:
                d_fin = None
            if d_fin and abs((d_fin - d_app).days) > 3:
                continue
        eligible.append(fin)

    best_combo: list[dict] = []
    best_penalty = 999999.0
    for size in (2, 3):
        for combo in combinations(eligible, size):
            banks = {str((x or {}).get("banca") or "").strip().upper() for x in combo if str((x or {}).get("banca") or "").strip()}
            if len(banks) > 1:
                continue
            if app_tess and app_tess != "0":
                invalid_tess = False
                for fin in combo:
                    fin_tess = _digits_only_local(fin.get("numero_tessera"))
                    if not fin_tess:
                        continue
                    if len(fin_tess) > 4 and fin_tess != app_tess:
                        invalid_tess = True
                        break
                    if len(fin_tess) <= 4 and not app_tess.endswith(fin_tess):
                        invalid_tess = True
                        break
                if invalid_tess:
                    continue
            total = sum(float(x.get("importo") or 0.0) for x in combo)
            diff = abs(total - app_amount)
            if diff < 0.01:
                return list(combo)
            if diff <= 1.0:
                avg_inferred = sum(float(x.get("inferred_store_score") or 0.0) for x in combo) / size
                penalty = diff - (avg_inferred / 1000.0)
                if penalty < best_penalty:
                    best_penalty = penalty
                    best_combo = list(combo)
    return best_combo


def _prepare_admin_finance_match_page(
    *,
    store_code: str,
    start_iso: str,
    end_iso: str,
    page_view: str,
) -> dict:
    stores = get_warehouse_stores() or []
    store_catalog = _build_store_match_catalog(stores)
    code_to_name = {str(s.get("code") or "").strip(): str(s.get("name") or "").strip() for s in stores}

    selected_store_codes = [str(store_code).strip()] if str(store_code or "").strip() else [str(s.get("code") or "").strip() for s in stores if str(s.get("code") or "").strip()]
    app_start = datetime.strptime(start_iso, "%Y-%m-%d").date()
    app_end = datetime.strptime(end_iso, "%Y-%m-%d").date()

    if not selected_store_codes:
        return {
            "stores": stores,
            "selected_store_code": str(store_code or "").strip(),
            "start_iso": start_iso,
            "end_iso": end_iso,
            "page_view": page_view,
            "rows": [],
            "finance_total": 0,
            "app_total": 0,
            "page_counts": {"queue": 0, "assigned": 0, "unmatched_app": 0, "unassigned_finance": 0},
        }

    app_res = search_versamenti_range_multi(selected_store_codes, start=app_start, end=app_end) or {}
    app_rows = app_res.get("rows") or []
    for row in app_rows:
        row["app_record_key"] = build_app_record_key(row)
        row["store_name"] = code_to_name.get(str(row.get("site") or "").strip(), str(row.get("site") or "").strip())

    match_map = get_matches_for_app_keys([str(r.get("app_record_key") or "") for r in app_rows])
    finance_rows = list_finance_versamenti(start_iso=start_iso, end_iso=end_iso, assignment_filter="all")
    for fin in finance_rows:
        inferred = _infer_finance_store(fin, store_catalog)
        fin["inferred_store_code"] = str(inferred.get("code") or "")
        fin["inferred_store_name"] = str(inferred.get("name") or "")
        fin["inferred_store_score"] = float(inferred.get("score") or 0.0)
        fin["inferred_store_method"] = str(inferred.get("method") or "")
    if selected_store_codes and len(selected_store_codes) == 1:
        only_store = selected_store_codes[0]
        finance_rows = [
            fin for fin in finance_rows
            if not str(fin.get("inferred_store_code") or "").strip()
            or str(fin.get("inferred_store_code") or "").strip() == only_store
            or str(fin.get("assigned_app_record_key") or "").startswith(f"{only_store}|")
        ]
    finance_by_id = {int(r["id"]): r for r in finance_rows}

    current_fin_ids = {
        int(m["finance_id"])
        for matches in match_map.values()
        for m in (matches or [])
    }
    if current_fin_ids:
        extra_fin = list_finance_versamenti(start_iso=start_iso, end_iso=end_iso, assignment_filter="all")
        for row in extra_fin:
            inferred = _infer_finance_store(row, store_catalog)
            row["inferred_store_code"] = str(inferred.get("code") or "")
            row["inferred_store_name"] = str(inferred.get("name") or "")
            row["inferred_store_score"] = float(inferred.get("score") or 0.0)
            row["inferred_store_method"] = str(inferred.get("method") or "")
            if int(row["id"]) in current_fin_ids and int(row["id"]) not in finance_by_id:
                finance_by_id[int(row["id"])] = row

    enriched_rows: list[dict] = []
    for row in app_rows:
        app_key = str(row.get("app_record_key") or "")
        current_matches = sorted(match_map.get(app_key) or [], key=lambda x: int(x.get("slot_no") or 0))
        assigned_finance_ids = [int(x["finance_id"]) for x in current_matches]
        assigned_count = len(assigned_finance_ids)

        scored: list[dict] = []
        for fin in finance_by_id.values():
            fin_id = int(fin["id"])
            assigned_to_other = bool(fin.get("assigned")) and str(fin.get("assigned_app_record_key") or "") != app_key
            if assigned_to_other:
                continue
            score, meta = _score_finance_candidate(row, fin, str(row.get("store_name") or ""))
            if score <= 0 and fin_id not in assigned_finance_ids:
                continue
            fin_amount = float(fin.get("importo") or 0.0)
            app_amount = float(row.get("valore") or 0.0)
            scored.append(
                {
                    "finance_id": fin_id,
                    "score": float(score),
                    "label": _build_finance_match_label(fin),
                    "importo": fin_amount,
                    "amount_diff": round(fin_amount - app_amount, 2),
                    "assigned_to_other": assigned_to_other,
                    "assigned_here": fin_id in assigned_finance_ids,
                    "inferred_store_name": str(fin.get("inferred_store_name") or ""),
                    "inferred_store_score": float(fin.get("inferred_store_score") or 0.0),
                    "match_source": "auto" if score >= 90 else "manual",
                    "reasons": meta.get("reasons") or [],
                }
            )

        scored.sort(key=lambda x: (not x["assigned_here"], -float(x["score"]), -int(x["finance_id"])))
        top_candidates = scored[:30]
        meaningful_candidates = [c for c in top_candidates if float(c.get("score") or 0.0) >= 70.0]
        has_candidates = bool(meaningful_candidates)

        slot_values = {1: "", 2: "", 3: ""}
        slot_scores = {1: 0.0, 2: 0.0, 3: 0.0}
        for m in current_matches:
            slot_no = int(m.get("slot_no") or 0)
            if slot_no in slot_values:
                slot_values[slot_no] = str(m.get("finance_id") or "")
                slot_scores[slot_no] = float(m.get("match_score") or 0.0)

        current_total = 0.0
        current_diff = 0.0
        current_match_ok = True
        if assigned_finance_ids:
            selected_fin_rows = [finance_by_id.get(fid) for fid in assigned_finance_ids if finance_by_id.get(fid)]
            current_total = round(sum(float((fr or {}).get("importo") or 0.0) for fr in selected_fin_rows), 2)
            current_diff = round(current_total - float(row.get("valore") or 0.0), 2)
            current_match_ok = abs(current_diff) <= 1.0

        if assigned_count == 0:
            single_auto_pick = next(
                (
                    c for c in top_candidates
                    if float(c.get("score") or 0.0) >= 90.0
                    and not bool(c.get("assigned_to_other"))
                    and abs(float(c.get("amount_diff") or 0.0)) <= 1.0
                ),
                None,
            )
            if single_auto_pick:
                slot_values[1] = str(single_auto_pick["finance_id"])
                slot_scores[1] = float(single_auto_pick["score"])

        auto_combo_hint = ""
        if assigned_count == 0 and not slot_values[1]:
            combo = _pick_finance_combo(
                row,
                [
                    {
                        **finance_by_id.get(int(c["finance_id"]), {}),
                        "assigned_to_other": bool(c.get("assigned_to_other")),
                    }
                    for c in top_candidates
                ],
            )
            if combo:
                for idx, fin in enumerate(combo[:3], start=1):
                    slot_values[idx] = str(fin.get("id") or "")
                    slot_scores[idx] = 95.0
                auto_combo_hint = "Suggerito match combinato per importo e store"
                has_candidates = True

        row_state = "assigned" if assigned_count > 0 else ("queue" if has_candidates else "unmatched_app")
        enriched_rows.append(
            {
                **row,
                "current_matches": current_matches,
                "candidate_options": top_candidates,
                "slot_values": slot_values,
                "slot_scores": slot_scores,
                "assigned_count": assigned_count,
                "current_match_total": current_total,
                "current_match_diff": current_diff,
                "current_match_ok": current_match_ok,
                "auto_combo_hint": auto_combo_hint,
                "has_candidates": has_candidates,
                "row_state": row_state,
            }
        )

    page_counts = {
        "queue": sum(1 for r in enriched_rows if r.get("row_state") == "queue"),
        "assigned": sum(1 for r in enriched_rows if r.get("row_state") == "assigned"),
        "unmatched_app": sum(1 for r in enriched_rows if r.get("row_state") == "unmatched_app"),
        "unassigned_finance": sum(1 for fin in finance_by_id.values() if not bool(fin.get("assigned"))),
    }

    if page_view == "assigned":
        page_rows = [r for r in enriched_rows if r.get("row_state") == "assigned"]
        page_rows.sort(key=lambda r: (bool(r.get("current_match_ok", True)), -abs(float(r.get("current_match_diff") or 0.0)), str(r.get("store_name") or "")))
    elif page_view == "unmatched_app":
        page_rows = [r for r in enriched_rows if r.get("row_state") == "unmatched_app"]
    else:
        page_rows = [r for r in enriched_rows if r.get("row_state") == "queue"]

    finance_unassigned_rows = []
    if page_view == "unassigned_finance":
        finance_unassigned_rows = [fin for fin in finance_by_id.values() if not bool(fin.get("assigned"))]
        finance_unassigned_rows.sort(
            key=lambda x: (
                str(x.get("inferred_store_name") or ""),
                str(x.get("data_contabile_iso") or x.get("data_valuta_iso") or ""),
                -int(x.get("id") or 0),
            )
        )

    return {
        "stores": stores,
        "selected_store_code": str(store_code or "").strip(),
        "start_iso": start_iso,
        "end_iso": end_iso,
        "page_view": page_view,
        "rows": page_rows,
        "finance_rows": finance_unassigned_rows,
        "finance_total": len(finance_by_id),
        "app_total": len(enriched_rows),
        "page_counts": page_counts,
    }


def _iter_month_keys_between(start_date: date, end_date: date) -> list[tuple[int, int]]:
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    out: list[tuple[int, int]] = []
    y, m = start_date.year, start_date.month
    while (y, m) <= (end_date.year, end_date.month):
        out.append((y, m))
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    return out


def _load_app_pos_rows(*, selected_store_codes: list[str], start_iso: str, end_iso: str, code_to_name: dict[str, str]) -> list[dict]:
    try:
        d0 = datetime.strptime(str(start_iso or "").strip(), "%Y-%m-%d").date()
        d1 = datetime.strptime(str(end_iso or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return []
    if d1 < d0:
        d0, d1 = d1, d0

    month_keys = _iter_month_keys_between(d0, d1)
    rows: list[dict] = []
    for site in selected_store_codes:
        day_map: dict[str, dict] = {}
        for y, m in month_keys:
            try:
                prim_rows = load_primanota_month_agg(str(site), year=y, month=m, categories=["Dati chiusura"]) or []
            except Exception:
                prim_rows = []
            for r in prim_rows:
                d_iso = str(r.get("date") or "").strip()
                if not d_iso or d_iso < start_iso or d_iso > end_iso:
                    continue
                voce = str(r.get("voce") or "").strip().upper()
                key = "vendite_lorde" if voce == "VENDITE LORDE" else "annullati" if voce == "ANNULLATI" else "pos" if voce == "POS" else "scontrini" if voce == "SCONTRINI" else ""
                if not key:
                    continue
                rec = day_map.setdefault(
                    d_iso,
                    {
                        "site": str(site),
                        "store_name": code_to_name.get(str(site), str(site)),
                        "date_iso": d_iso,
                        "vendite_lorde": 0.0,
                        "annullati": 0.0,
                        "pos": 0.0,
                        "scontrini": 0.0,
                    },
                )
                try:
                    rec[key] += float(r.get("sum") or 0.0)
                except Exception:
                    pass
        for d_iso, rec in sorted(day_map.items()):
            rec["giro_affari"] = float(rec.get("vendite_lorde") or 0.0) - float(rec.get("annullati") or 0.0)
            rec["app_record_key"] = build_pos_app_record_key(rec)
            rows.append(rec)
    return rows


def _score_pos_finance_candidate(app_row: dict, finance_row: dict) -> tuple[float, dict]:
    reasons: list[str] = []
    score = 0.0

    app_amount = float((app_row or {}).get("pos") or 0.0)
    fin_amount = float((finance_row or {}).get("importo") or 0.0)
    amount_diff = abs(app_amount - fin_amount)
    if amount_diff < 0.01:
        score += 42.0
        reasons.append("importo")
    elif amount_diff <= 1.0:
        score += 14.0
        reasons.append("importo vicino")

    inferred_code = str((finance_row or {}).get("inferred_store_code") or "").strip()
    inferred_score = float((finance_row or {}).get("inferred_store_score") or 0.0)
    app_site = str((app_row or {}).get("site") or "").strip()
    if inferred_code and inferred_code == app_site:
        if inferred_score >= 95:
            score += 48.0
            reasons.append("store forte")
        elif inferred_score >= 85:
            score += 40.0
            reasons.append("store")
        elif inferred_score >= 70:
            score += 28.0
            reasons.append("store probabile")
    elif inferred_code and inferred_code != app_site and inferred_score >= 70:
        return 0.0, {"reasons": ["store diverso"], "amount_diff": amount_diff}

    app_date = str((app_row or {}).get("date_iso") or "").strip()
    fin_dates = [
        str((finance_row or {}).get("data_contabile_iso") or "").strip(),
        str((finance_row or {}).get("data_valuta_iso") or "").strip(),
    ]
    best_date_pts = 0.0
    has_antecedent_date = False
    if app_date:
        try:
            d_app = datetime.strptime(app_date, "%Y-%m-%d").date()
        except Exception:
            d_app = None
        if d_app:
            for fd in fin_dates:
                if not fd:
                    continue
                try:
                    d_fin = datetime.strptime(fd, "%Y-%m-%d").date()
                except Exception:
                    continue
                if d_fin < d_app:
                    has_antecedent_date = True
                    continue
                delta = abs((d_fin - d_app).days)
                if delta == 0:
                    best_date_pts = max(best_date_pts, 6.0)
                elif delta <= 3:
                    best_date_pts = max(best_date_pts, 4.5)
                elif delta <= 7:
                    best_date_pts = max(best_date_pts, 3.0)
                elif delta <= 14:
                    best_date_pts = max(best_date_pts, 2.0)
                elif delta <= 30:
                    best_date_pts = max(best_date_pts, 1.0)
    if has_antecedent_date:
        return 0.0, {"reasons": ["data antecedente"], "amount_diff": amount_diff}
    if best_date_pts:
        score += best_date_pts
        reasons.append("data")

    emitter = str((finance_row or {}).get("emitter") or "").strip()
    if emitter:
        score += 6.0
        reasons.append(f"emettitore {emitter.lower()}")
    elif amount_diff > 1.0:
        reasons.append("importo da verificare")

    return min(score, 100.0), {"reasons": reasons, "amount_diff": amount_diff}


def _pick_pos_finance_combo(app_row: dict, finance_pool: list[dict]) -> list[dict]:
    if not finance_pool:
        return []
    app_amount = float((app_row or {}).get("pos") or 0.0)
    app_date = str((app_row or {}).get("date_iso") or "").strip()
    app_site = str((app_row or {}).get("site") or "").strip()
    try:
        d_app = datetime.strptime(app_date, "%Y-%m-%d").date() if app_date else None
    except Exception:
        d_app = None

    eligible: list[dict] = []
    for fin in finance_pool:
        if bool(fin.get("assigned_to_other")):
            continue
        inferred_code = str(fin.get("inferred_store_code") or "").strip()
        inferred_score = float(fin.get("inferred_store_score") or 0.0)
        if inferred_code and inferred_code != app_site and inferred_score >= 70:
            continue
        fin_date = str(fin.get("data_contabile_iso") or fin.get("data_valuta_iso") or "").strip()
        if d_app and fin_date:
            try:
                d_fin = datetime.strptime(fin_date, "%Y-%m-%d").date()
            except Exception:
                d_fin = None
            if d_fin and d_fin < d_app:
                continue
            if d_fin and abs((d_fin - d_app).days) > 45:
                continue
        eligible.append(fin)

    best_combo: list[dict] = []
    best_penalty = 999999.0
    for size in (2, 3):
        for combo in combinations(eligible, size):
            total = sum(float(x.get("importo") or 0.0) for x in combo)
            diff = abs(total - app_amount)
            if diff > 1.0:
                continue
            emitters = [str(x.get("emitter") or "").strip() for x in combo if str(x.get("emitter") or "").strip()]
            duplicate_penalty = 0.0
            diversity_bonus = 0.0
            if emitters:
                duplicate_penalty = max(0, len(emitters) - len(set(emitters))) * 12.0
                if len(set(emitters)) > 1:
                    diversity_bonus += 6.0
                if "AMEX" in emitters and len(set(emitters)) > 1:
                    diversity_bonus += 6.0
                if len(set(emitters)) == 1 and len(emitters) > 1:
                    duplicate_penalty += 8.0
            avg_store = sum(float(x.get("inferred_store_score") or 0.0) for x in combo) / size
            penalty = diff * 100.0 + duplicate_penalty - diversity_bonus - (avg_store / 8.0)
            if penalty < best_penalty:
                best_penalty = penalty
                best_combo = list(combo)
    return best_combo


def _selected_pos_finance_rows_for_match(finance_uids: list[str], *, start_iso: str, end_iso: str) -> list[dict]:
    if not finance_uids:
        return []
    try:
        d0 = datetime.strptime(str(start_iso or "").strip(), "%Y-%m-%d").date()
        d1 = datetime.strptime(str(end_iso or "").strip(), "%Y-%m-%d").date() + timedelta(days=45)
    except Exception:
        d0 = d1 = None
    finance_rows = list_finance_pos_rows(
        start_iso=d0.isoformat() if d0 else "",
        end_iso=d1.isoformat() if d1 else "",
        assignment_filter="all",
    ) or []
    by_uid = {str(r.get("finance_row_uid") or "").strip().lower(): r for r in finance_rows}
    return [by_uid[uid] for uid in finance_uids if uid in by_uid]


def _validate_pos_finance_match_selection(*, app_row: dict, selected_finance_rows: list[dict], app_record_key: str, amount_tolerance: float = 1.0) -> dict:
    app_value = float((app_row or {}).get("pos") or 0.0)
    if not selected_finance_rows:
        return {"ok": True, "finance_total": 0.0, "app_value": app_value, "diff": 0.0}

    app_site = str((app_row or {}).get("site") or "").strip()
    app_date = str((app_row or {}).get("date_iso") or "").strip()
    try:
        d_app = datetime.strptime(app_date, "%Y-%m-%d").date() if app_date else None
    except Exception:
        d_app = None

    total = 0.0
    used_uids: set[str] = set()
    for fin in selected_finance_rows:
        fin_uid = str(fin.get("finance_row_uid") or "").strip().lower()
        if not fin_uid or fin_uid in used_uids:
            return {"ok": False, "error": "Lo stesso movimento finance non può essere usato più di una volta nello stesso match."}
        used_uids.add(fin_uid)

        assigned_key = str(fin.get("assigned_app_record_key") or "").strip()
        if bool(fin.get("assigned")) and assigned_key and assigned_key != str(app_record_key or "").strip():
            return {"ok": False, "error": f"Il movimento finance {fin_uid} è già associato a un altro record app."}

        inferred_code = str(fin.get("inferred_store_code") or "").strip()
        inferred_score = float(fin.get("inferred_store_score") or 0.0)
        if inferred_code and app_site and inferred_code != app_site and inferred_score >= 70.0:
            return {"ok": False, "error": f"Il movimento finance {fin_uid} sembra appartenere allo store {inferred_code}, non allo store {app_site}."}

        fin_date = str(fin.get("data_contabile_iso") or fin.get("data_valuta_iso") or "").strip()
        if d_app and fin_date:
            try:
                d_fin = datetime.strptime(fin_date, "%Y-%m-%d").date()
            except Exception:
                d_fin = None
            if d_fin and d_fin < d_app:
                return {"ok": False, "error": f"Il movimento finance {fin_uid} è antecedente alla data della distinta cassa."}

        total += float(fin.get("importo") or 0.0)

    diff = round(total - app_value, 2)
    if abs(diff) > float(amount_tolerance or 0.0):
        return {
            "ok": False,
            "error": (
                f"Il totale dei movimenti finance selezionati ({total:,.2f} €) non è coerente con il valore POS "
                f"registrato in Store Hub ({app_value:,.2f} €). Scostamento: {diff:,.2f} €."
            ).replace(",", "X").replace(".", ",").replace("X", "."),
            "finance_total": total,
            "app_value": app_value,
            "diff": diff,
        }
    return {"ok": True, "finance_total": total, "app_value": app_value, "diff": diff}


_MATCH_OVERRIDEABLE_MARKERS = (
    "banca diversa",
    "tessera diversa",
    "finali tessera",
    "troppo distante",
    "sembra appartenere allo store",
    "non è coerente con il valore",
    "non Ã¨ coerente con il valore",
)

_MATCH_HARD_BLOCK_MARKERS = (
    "già associato",
    "giÃ  associato",
    "non può essere usato più di una volta",
    "non puÃ² essere usato piÃ¹ di una volta",
    "banche diverse",
)


def _classify_match_error(message: str) -> str:
    msg = str(message or "").strip().lower()
    if any(marker in msg for marker in _MATCH_HARD_BLOCK_MARKERS):
        return "hard"
    if any(marker in msg for marker in _MATCH_OVERRIDEABLE_MARKERS):
        return "overrideable"
    return "hard"


def _parse_batch_payload() -> list[dict]:
    raw = (request.form.get("batch_payload") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _prepare_admin_pos_finance_match_page(*, store_code: str, start_iso: str, end_iso: str, page_view: str) -> dict:
    stores = get_warehouse_stores() or []
    code_to_name = {str(s.get("code") or "").strip(): str(s.get("name") or "").strip() for s in stores}
    selected_store_codes = [str(store_code).strip()] if str(store_code or "").strip() else [str(s.get("code") or "").strip() for s in stores if str(s.get("code") or "").strip()]
    if not selected_store_codes:
        return {
            "stores": stores,
            "selected_store_code": str(store_code or "").strip(),
            "start_iso": start_iso,
            "end_iso": end_iso,
            "page_view": page_view,
            "rows": [],
            "finance_rows": [],
            "page_counts": {"queue": 0, "assigned": 0, "unmatched_app": 0, "unassigned_finance": 0},
        }

    app_rows = _load_app_pos_rows(selected_store_codes=selected_store_codes, start_iso=start_iso, end_iso=end_iso, code_to_name=code_to_name)
    match_map = get_pos_matches_for_app_keys([str(r.get("app_record_key") or "") for r in app_rows])

    try:
        d0 = datetime.strptime(str(start_iso or "").strip(), "%Y-%m-%d").date()
        d1 = datetime.strptime(str(end_iso or "").strip(), "%Y-%m-%d").date() + timedelta(days=45)
    except Exception:
        d0 = d1 = None
    finance_rows = list_finance_pos_rows(
        start_iso=d0.isoformat() if d0 else "",
        end_iso=d1.isoformat() if d1 else "",
        assignment_filter="all",
    ) or []
    app_catalog = _build_app_store_catalog_local(stores)
    app_by_code = {str((s or {}).get("code") or "").strip(): str((s or {}).get("name") or "").strip() for s in stores or []}
    all_codes: set[str] = set()
    for fin in finance_rows:
        all_codes.update(
            _extract_finance_codes_from_texts_local(
                fin.get("store"),
                fin.get("descrizione"),
                fin.get("nota"),
            )
        )
    catalog_map = get_code_catalog_map(all_codes) if all_codes else {}
    assignments_by_code = get_assignments_by_code(all_codes) if all_codes else {}
    for fin in finance_rows:
        inferred = _infer_finance_store_pos(fin, stores, catalog_map, assignments_by_code, app_catalog, app_by_code)
        fin["inferred_store_code"] = str(inferred.get("code") or "")
        fin["inferred_store_name"] = str(inferred.get("name") or "")
        fin["inferred_store_score"] = float(inferred.get("score") or 0.0)
        fin["inferred_store_method"] = str(inferred.get("method") or "")
        fin["inferred_code_value"] = str(inferred.get("code_value") or "")
        fin["emitter"] = _infer_pos_emitter(fin)
    if selected_store_codes and len(selected_store_codes) == 1:
        only_store = selected_store_codes[0]
        finance_rows = [
            fin for fin in finance_rows
            if not str(fin.get("inferred_store_code") or "").strip()
            or str(fin.get("inferred_store_code") or "").strip() == only_store
            or str(fin.get("assigned_app_record_key") or "").startswith(f"{only_store}|")
        ]
    finance_by_uid = {str(r.get("finance_row_uid") or "").strip().lower(): r for r in finance_rows}

    current_fin_uids = {
        str(m.get("finance_row_uid") or "").strip().lower()
        for matches in match_map.values()
        for m in (matches or [])
        if str(m.get("finance_row_uid") or "").strip()
    }
    if current_fin_uids:
        extra_fin = list_finance_pos_rows(assignment_filter="all") or []
        extra_codes: set[str] = set(all_codes)
        for row in extra_fin:
            extra_codes.update(
                _extract_finance_codes_from_texts_local(
                    row.get("store"),
                    row.get("descrizione"),
                    row.get("nota"),
                )
            )
        if extra_codes != all_codes:
            catalog_map = get_code_catalog_map(extra_codes) if extra_codes else {}
            assignments_by_code = get_assignments_by_code(extra_codes) if extra_codes else {}
        for row in extra_fin:
            row["emitter"] = _infer_pos_emitter(row)
            inferred = _infer_finance_store_pos(row, stores, catalog_map, assignments_by_code, app_catalog, app_by_code)
            row["inferred_store_code"] = str(inferred.get("code") or "")
            row["inferred_store_name"] = str(inferred.get("name") or "")
            row["inferred_store_score"] = float(inferred.get("score") or 0.0)
            row["inferred_store_method"] = str(inferred.get("method") or "")
            row["inferred_code_value"] = str(inferred.get("code_value") or "")
            uid = str(row.get("finance_row_uid") or "").strip().lower()
            if uid in current_fin_uids and uid not in finance_by_uid:
                finance_by_uid[uid] = row

    enriched_rows: list[dict] = []
    for row in app_rows:
        if float(row.get("pos") or 0.0) <= 0:
            continue
        app_key = str(row.get("app_record_key") or "")
        current_matches = sorted(match_map.get(app_key) or [], key=lambda x: int(x.get("slot_no") or 0))
        assigned_finance_uids = [str(x.get("finance_row_uid") or "").strip().lower() for x in current_matches if str(x.get("finance_row_uid") or "").strip()]
        assigned_count = len(assigned_finance_uids)

        scored: list[dict] = []
        for fin in finance_by_uid.values():
            fin_uid = str(fin.get("finance_row_uid") or "").strip().lower()
            assigned_to_other = bool(fin.get("assigned")) and str(fin.get("assigned_app_record_key") or "") != app_key
            if assigned_to_other:
                continue
            score, meta = _score_pos_finance_candidate(row, fin)
            if score <= 0 and fin_uid not in assigned_finance_uids:
                continue
            fin_amount = float(fin.get("importo") or 0.0)
            app_amount = float(row.get("pos") or 0.0)
            scored.append(
                {
                    "finance_row_uid": fin_uid,
                    "finance_source_table": str(fin.get("source_table") or "").strip(),
                    "finance_row_id": int(fin.get("id") or 0),
                    "score": float(score),
                    "label": _build_pos_finance_match_label(fin),
                    "importo": fin_amount,
                    "amount_diff": round(fin_amount - app_amount, 2),
                    "assigned_to_other": assigned_to_other,
                    "assigned_here": fin_uid in assigned_finance_uids,
                    "inferred_store_name": str(fin.get("inferred_store_name") or ""),
                    "inferred_store_score": float(fin.get("inferred_store_score") or 0.0),
                    "emitter": str(fin.get("emitter") or ""),
                    "match_source": "auto" if score >= 90 else "manual",
                    "reasons": meta.get("reasons") or [],
                }
            )
        scored.sort(key=lambda x: (not x["assigned_here"], -float(x["score"]), str(x.get("emitter") or ""), -int(x["finance_row_id"])))
        top_candidates = scored[:30]
        meaningful_candidates = [c for c in top_candidates if float(c.get("score") or 0.0) >= 55.0]
        has_candidates = bool(meaningful_candidates)

        slot_values = {1: "", 2: "", 3: ""}
        slot_scores = {1: 0.0, 2: 0.0, 3: 0.0}
        for m in current_matches:
            slot_no = int(m.get("slot_no") or 0)
            if slot_no in slot_values:
                slot_values[slot_no] = str(m.get("finance_row_uid") or "")
                slot_scores[slot_no] = float(m.get("match_score") or 0.0)

        current_total = 0.0
        current_diff = 0.0
        current_match_ok = True
        if assigned_finance_uids:
            selected_fin_rows = [finance_by_uid.get(fid) for fid in assigned_finance_uids if finance_by_uid.get(fid)]
            current_total = round(sum(float((fr or {}).get("importo") or 0.0) for fr in selected_fin_rows), 2)
            current_diff = round(current_total - float(row.get("pos") or 0.0), 2)
            current_match_ok = abs(current_diff) <= 1.0

        if assigned_count == 0:
            single_auto_pick = next(
                (
                    c for c in top_candidates
                    if float(c.get("score") or 0.0) >= 90.0
                    and not bool(c.get("assigned_to_other"))
                    and abs(float(c.get("amount_diff") or 0.0)) <= 1.0
                ),
                None,
            )
            if single_auto_pick:
                slot_values[1] = str(single_auto_pick["finance_row_uid"])
                slot_scores[1] = float(single_auto_pick["score"])

        auto_combo_hint = ""
        if assigned_count == 0 and not slot_values[1]:
            combo = _pick_pos_finance_combo(
                row,
                [{**finance_by_uid.get(str(c["finance_row_uid"]).lower(), {}), "assigned_to_other": bool(c.get("assigned_to_other"))} for c in top_candidates],
            )
            if combo:
                for idx, fin in enumerate(combo[:3], start=1):
                    slot_values[idx] = str(fin.get("finance_row_uid") or "")
                    slot_scores[idx] = 95.0
                emitters = [str(fin.get("emitter") or "").strip() for fin in combo if str(fin.get("emitter") or "").strip()]
                auto_combo_hint = "Suggerito match combinato"
                if emitters and len(set(emitters)) > 1:
                    auto_combo_hint += f" ({' + '.join(dict.fromkeys(emitters))})"
                has_candidates = True

        selected_preview_uids = [
            str(slot_values[idx] or "").strip().lower()
            for idx in (1, 2, 3)
            if str(slot_values[idx] or "").strip()
        ]
        preview_rows = [finance_by_uid.get(uid) for uid in selected_preview_uids if finance_by_uid.get(uid)]
        preview_total = round(sum(float((fr or {}).get("importo") or 0.0) for fr in preview_rows), 2) if preview_rows else 0.0
        preview_diff = round(preview_total - float(row.get("pos") or 0.0), 2) if preview_rows else 0.0
        preview_ok = abs(preview_diff) <= 1.0 if preview_rows else True

        row_state = "assigned" if assigned_count > 0 else ("queue" if has_candidates else "unmatched_app")
        enriched_rows.append(
            {
                **row,
                "current_matches": current_matches,
                "candidate_options": top_candidates,
                "slot_values": slot_values,
                "slot_scores": slot_scores,
                "assigned_count": assigned_count,
                "current_match_total": current_total,
                "current_match_diff": current_diff,
                "current_match_ok": current_match_ok,
                "auto_combo_hint": auto_combo_hint,
                "has_candidates": has_candidates,
                "selected_preview_total": preview_total,
                "selected_preview_diff": preview_diff,
                "selected_preview_ok": preview_ok,
                "row_state": row_state,
            }
        )

    page_counts = {
        "queue": sum(1 for r in enriched_rows if r.get("row_state") == "queue"),
        "assigned": sum(1 for r in enriched_rows if r.get("row_state") == "assigned"),
        "unmatched_app": sum(1 for r in enriched_rows if r.get("row_state") == "unmatched_app"),
        "unassigned_finance": sum(1 for fin in finance_by_uid.values() if not bool(fin.get("assigned"))),
    }

    if page_view == "assigned":
        page_rows = [r for r in enriched_rows if r.get("row_state") == "assigned"]
        page_rows.sort(key=lambda r: (bool(r.get("current_match_ok", True)), -abs(float(r.get("current_match_diff") or 0.0)), str(r.get("store_name") or "")))
    elif page_view == "unmatched_app":
        page_rows = [r for r in enriched_rows if r.get("row_state") == "unmatched_app"]
    else:
        page_rows = [r for r in enriched_rows if r.get("row_state") == "queue"]

    finance_unassigned_rows = []
    if page_view == "unassigned_finance":
        finance_unassigned_rows = [fin for fin in finance_by_uid.values() if not bool(fin.get("assigned"))]
        finance_unassigned_rows.sort(
            key=lambda x: (
                str(x.get("inferred_store_name") or ""),
                str(x.get("data_contabile_iso") or x.get("data_valuta_iso") or ""),
                str(x.get("emitter") or ""),
                -int(x.get("id") or 0),
            )
        )

    return {
        "stores": stores,
        "selected_store_code": str(store_code or "").strip(),
        "start_iso": start_iso,
        "end_iso": end_iso,
        "page_view": page_view,
        "rows": page_rows,
        "finance_rows": finance_unassigned_rows,
        "page_counts": page_counts,
    }


def ai_enabled_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u:
            return redirect(url_for('login'))
        if not _normalize_ai_access(u):
            abort(403)
        return view(*args, **kwargs)
    return wrapped

def user_can_access_loc(loc_name: str) -> bool:
    u = current_user()
    if not u:
        return False
    # Admin vede tutto
    if u.get('role') == 'admin':
        return True

    target = extract_loc_id(loc_name)

    # 1) Prova con le assegnazioni da Supabase (RLS) per l'utente corrente
    try:
        allowed = set(sb_get_user_locations(u.get('sb_token'), u.get('uid')) or [])
        if target in allowed:
            return True
    except Exception as e:
        log.warning("user_can_access_loc: errore lettura user_locations: %s", e)

    # 2) Fallback: usa list_locations() che è già filtrata per utente non-admin
    try:
        locs = list_locations() or []
        allowed2 = { extract_loc_id((l.get('name') or '').strip()) for l in locs if l.get('name') }
        return target in allowed2
    except Exception as e:
        log.warning("user_can_access_loc: fallback list_locations fallito: %s", e)
        return False

@app.context_processor
def inject_user():
    u = current_user()
    ai_available_stores = []
    master_can_admin_active = False
    try:
        if u and _normalize_ai_access(u):
            ai_available_stores = _load_ai_available_stores_for_user(u)
    except Exception:
        ai_available_stores = []
    try:
        if u and _is_platform_master(u):
            master_can_admin_active = bool(_resolve_master_admin_tenant(allow_query=False))
    except Exception:
        master_can_admin_active = False
    return {
        'user': u,
        'app_build_version': APP_BUILD_VERSION,
        'ai_available_stores': ai_available_stores,
        'master_can_admin_active': master_can_admin_active,
        'master_admin_tenant_key': _master_admin_tenant_key(),
        'supported_languages': _supported_ui_languages(),
        'ui_language': _current_ui_language(),
        'tr': _template_translate,
        'theme_options': THEME_OPTIONS,
        'active_theme_key': _normalize_theme_key((u or {}).get('theme_key') if isinstance(u, dict) else session.get('theme_key')),
        'home_url': url_for(_first_allowed_endpoint_for_user(u)) if u else url_for('login'),
        'SB_URL': SUPABASE_URL,
        'SB_ANON_KEY': SUPABASE_ANON_KEY,
        'DEFAULT_ACCESS_BASE_PATH': os.getenv('ACCESS_BASE_PATH') or r"C:\\FILE\\F & B INVESTMENT HOLDING srl\\F & B - OPS\\CONDIVISA\\DatiFile",
        # Default SharePoint settings (usati dal modal di scelta connessione)
        'DEFAULT_SP_SHARING_URL': 'https://fbinvestmentholding.sharepoint.com/:f:/r/sites/FB/F%20%20B/I%20LOVE%20POKE/OPS/CONDIVISA?e=5%3a10c1b246d77d49bdac14bfa6179c9028&sharingv2=true&fromShare=true&at=9',
        'DEFAULT_SP_BASE_SUBPATH': 'DatiFile',
        'DEFAULT_SP_DB_FILENAME': 'Datifile.mdb',
        # Scopes consigliati per usare SharePoint via Graph (download/upload DB)
        'MS_SCOPES_SHAREPOINT': os.getenv(
            'MS_SCOPES_SHAREPOINT',
            'offline_access User.Read Files.ReadWrite.All Sites.ReadWrite.All'
        ),
    }


def _ai_store_fallback_from_session(user: dict | None = None) -> list[dict]:
    user = user or {}
    fallback: list[dict] = []
    try:
        cached = session.get('ai_available_stores_cache')
        if isinstance(cached, list) and cached:
            seen: set[str] = set()
            for row in cached:
                if not isinstance(row, dict):
                    continue
                code = str(row.get('code') or row.get('store_code') or '').strip()
                name = str(row.get('name') or row.get('store_name') or code).strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                fallback.append({'code': code, 'name': name})
            if fallback:
                return fallback
    except Exception:
        pass

    code = str(session.get('store_code') or '').strip()
    name = str(session.get('store_name') or code).strip()
    if code:
        fallback.append({'code': code, 'name': name or code})
    return fallback


def _load_ai_available_stores_for_user(user: dict | None) -> list[dict]:
    user = user or {}
    role_l = str(user.get('role') or '').strip().lower()
    try:
        if role_l == 'admin':
            stores = get_warehouse_stores() or []
        else:
            stores = get_user_warehouse_stores(str(user.get('uid') or '')) or []
    except Exception:
        stores = _ai_store_fallback_from_session(user)
    normalized: list[dict] = []
    seen: set[str] = set()
    for row in stores or []:
        code = str((row or {}).get('code') or (row or {}).get('store_code') or '').strip()
        name = str((row or {}).get('name') or (row or {}).get('store_name') or code).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        normalized.append({'code': code, 'name': name or code})
    try:
        if normalized:
            session['ai_available_stores_cache'] = normalized
    except Exception:
        pass
    return normalized

# ----------------- Google Business API -----------------
def list_locations():
    """Ritorna location filtrate per utente (se non admin)."""
    token = get_access_token()
    url = f'https://mybusinessbusinessinformation.googleapis.com/v1/accounts/{ACCOUNT_ID}/locations'
    resp = _session().get(url, headers={'Authorization': f'Bearer {token}'},
                          params={'readMask': 'name,title,storeCode', 'pageSize': 100}, timeout=15)
    _raise_with_body(resp)
    locs = resp.json().get('locations', []) or []

    u = current_user()
    if not u or u['role'] == 'admin':
        return locs
    try:
        allowed = set(sb_get_user_locations(u['sb_token'], u['uid']))
    except Exception as e:
        log.warning("Impossibile recuperare user_locations: %s", e)
        return []
    return [l for l in locs if extract_loc_id(l.get('name', '')) in allowed]

def get_location(loc_name):
    token = get_access_token()
    full = extract_loc_id(loc_name)
    url = f'https://mybusinessbusinessinformation.googleapis.com/v1/{full}'
    resp = _session().get(url, headers={'Authorization': f'Bearer {token}'},
                          params={'readMask': 'name,title,storeCode,phoneNumbers,websiteUri,storefrontAddress,regularHours,profile,categories,latlng'},
                          timeout=15)
    _raise_with_body(resp)
    data = resp.json()
    data.setdefault('profile', {})
    data.setdefault('regularHours', {'periods': []})
    data.setdefault('latlng', {'latitude': None, 'longitude': None})
    return data

def list_reviews_v4(loc_name, page_size=10, order_by='updateTime desc'):
    token = get_access_token()
    loc_id = extract_loc_id(loc_name).split('/')[1]
    parent = f'accounts/{ACCOUNT_ID}/locations/{loc_id}'
    url = f'https://mybusiness.googleapis.com/v4/{parent}/reviews'
    resp = _session().get(url, headers={'Authorization': f'Bearer {token}'},
                          params={'pageSize': page_size, 'orderBy': order_by}, timeout=15)
    _raise_with_body(resp)
    data = resp.json()
    reviews = data.get('reviews', [])
    avg = data.get('averageRating')
    total = data.get('totalReviewCount')
    for r in reviews:
        stars_map = {'ONE': 1, 'TWO': 2, 'THREE': 3, 'FOUR': 4, 'FIVE': 5}
        r['_stars'] = stars_map.get(r.get('starRating', ''), 0)
        rr = r.get('reviewReply') or {}
        r['_replyText'] = rr.get('comment', '')
    return reviews, avg, total

def update_review_reply_v4(review_name: str, text: str):
    token = get_access_token()
    url = f'https://mybusiness.googleapis.com/v4/{review_name}/reply'
    resp = _session().put(url, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                          json={'comment': text}, timeout=15)
    _raise_with_body(resp)
    return resp.json()

# --------------------- MEDIA (v4) ---------------------
GBP_MEDIA_CATEGORIES = [
    'COVER', 'PROFILE', 'LOGO',
    'EXTERIOR', 'INTERIOR', 'PRODUCT',
    'AT_WORK', 'FOOD_AND_DRINK', 'MENU',
    'COMMON_AREA', 'ROOMS', 'TEAMS',
    'ADDITIONAL'
]

def list_media_v4_limited(loc_name, max_items=60):
    token = get_access_token()
    loc_id = extract_loc_id(loc_name).split('/')[1]
    parent = f'accounts/{ACCOUNT_ID}/locations/{loc_id}'
    url = f'https://mybusiness.googleapis.com/v4/{parent}/media'
    items, page_token = [], None
    while True and len(items) < max_items:
        page_size = min(100, max_items - len(items))
        params = {'pageSize': page_size}
        if page_token:
            params['pageToken'] = page_token
        resp = _session().get(url, headers={'Authorization': f'Bearer {token}'}, params=params, timeout=20)
        _raise_with_body(resp)
        data = resp.json()
        items.extend(data.get('mediaItems', []))
        page_token = data.get('nextPageToken')
        if not page_token or len(items) >= max_items:
            break
    return items

def media_create_v4(loc_id: str, category: str, source_url: str, media_format: str = 'PHOTO'):
    token = get_access_token()
    parent = f'accounts/{ACCOUNT_ID}/locations/{loc_id}'
    url = f'https://mybusiness.googleapis.com/v4/{parent}/media'
    body = {'mediaFormat': media_format,
            'locationAssociation': {'category': category},
            'sourceUrl': source_url}
    resp = _session().post(url, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                           json=body, timeout=30)
    _raise_with_body(resp)
    return resp.json()

def media_delete_v4(media_name: str):
    token = get_access_token()
    url = f'https://mybusiness.googleapis.com/v4/{media_name}'
    resp = _session().delete(url, headers={'Authorization': f'Bearer {token}'}, timeout=20)
    _raise_with_body(resp)
    return True

# ---------- Utility immagini ----------
MAX_BYTES = 5 * 1024 * 1024
MIN_BYTES = 10 * 1024

def _normalize_image_for_gbp(file_storage):
    filename = getattr(file_storage, 'filename', '') or ''
    raw = file_storage.read()
    if not raw:
        raise ValueError('File vuoto')

    if not _PIL_OK:
        ext = os.path.splitext(filename.lower())[1]
        if ext not in ('.jpg', '.jpeg', '.png'):
            raise ValueError('Formato non supportato senza Pillow (usa JPG o PNG).')
        if len(raw) < MIN_BYTES:
            raise ValueError('File sotto 10KB.')
        return raw, ('image/jpeg' if ext in ('.jpg', '.jpeg') else 'image/png')

    try:
        im = Image.open(io.BytesIO(raw))
    except UnidentifiedImageError:
        raise ValueError('Immagine illeggibile o corrotta.')

    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass

    if im.mode not in ('RGB', 'L'):
        im = im.convert('RGB')

    out = io.BytesIO()
    im.save(out, format='JPEG', quality=88, optimize=True, progressive=False)
    raw = out.getvalue()
    mime = 'image/jpeg'

    if len(raw) > MAX_BYTES:
        out = io.BytesIO()
        im.save(out, format='JPEG', quality=80, optimize=True, progressive=False)
        raw = out.getvalue()
        if len(raw) > MAX_BYTES:
            raise ValueError('File superiore a 5MB anche dopo compressione.')
    if len(raw) < MIN_BYTES:
        out = io.BytesIO()
        im.save(out, format='JPEG', quality=95, optimize=False, progressive=False)
        raw = out.getvalue()
        if len(raw) < MIN_BYTES:
            raise ValueError('File sotto 10KB (requisito Google).')

    return raw, mime

# ---------- ImgBB ----------
def _imggb_upload(raw: bytes, filename: str | None, mime: str, expiration: int) -> str:
    if not IMGBB_API_KEY:
        raise RuntimeError("IMGBB_API_KEY mancante")
    params = {'key': IMGBB_API_KEY}
    if expiration:
        expiration = max(60, min(int(expiration), 15552000))
        params['expiration'] = str(expiration)
    files = {'image': (filename or 'upload.jpg', raw, mime)}
    r = _session().post('https://api.imgbb.com/1/upload', params=params, files=files, timeout=30)
    _raise_with_body(r)
    data = r.json()
    if not data.get('success'):
        raise RuntimeError(f"ImgBB upload non riuscito: {data}")
    d = data.get('data') or {}
    url = (d.get('image') or {}).get('url') or d.get('url') or d.get('display_url')
    if not url:
        raise RuntimeError(f"ImgBB: risposta inattesa {data}")
    return url

def _media_create_from_url_retry(loc_id: str, category: str, base_url: str,
                                 raw: bytes | None = None, mime: str | None = None, fname: str | None = None,
                                 attempts: int = 4, sleeps: list[int] = (0, 2, 5, 10)):
    url = base_url
    for i in range(attempts):
        try:
            return media_create_v4(loc_id, category=category, source_url=url, media_format='PHOTO')
        except requests.HTTPError as e:
            msg = str(e)
            is_1000 = ('ValidationError' in msg and '1000' in msg and 'Fetching image failed' in msg)
            if not is_1000 or i == attempts - 1:
                raise
            wait = sleeps[i] if i < len(sleeps) else sleeps[-1]
            if wait:
                logging.warning("Create fallito (1000) per loc=%s; attendo %ss e ritento...", loc_id, wait)
                time.sleep(wait)
            if raw is not None and i >= 1:
                try:
                    url = _imggb_upload(raw, fname or 'upload.jpg', mime or 'image/jpeg', IMGBB_EXPIRATION)
                    logging.info("Rigenerato URL ImgBB per retry: %s", url)
                except Exception as up_e:
                    logging.warning("Reupload ImgBB per retry fallito: %s", up_e)

# ---------- Helpers Analytics (solo quick-stats) ----------
def _parse_gmb_time(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith('Z'):
            base = ts[:-1]
            return datetime.fromisoformat(base).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(ts)
    except Exception:
        return None

# --------------------- ROUTES: AUTH ---------------------
@app.get('/login')
def login():
    if session.get('uid'):
        if str(session.get('role') or '').strip().lower() == 'fornitore':
            return redirect(url_for('warehouse.supplier_orders_received'))
        return redirect(url_for(_first_allowed_endpoint_for_user(current_user())))
    return render_template('login.html')

@app.post('/login')
def login_post():
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''
    nxt = request.args.get('next') or request.form.get('next') or url_for('dashboard')
    try:
        auth = sb_auth_signin(email, password)
        token = auth.get('access_token')
        if not token:
            flash("Accesso non riuscito.", 'danger')
            return redirect(url_for('login', next=_safe_next_url(nxt)))
        user_obj = sb_auth_user(token)
        uid = user_obj.get('id')
        prof = sb_get_my_profile(token, uid) or {}
        if uid and not prof.get('access_profile_id'):
            try:
                admin_prof = sb_admin_get_profile_by_id(str(uid))
                if admin_prof:
                    prof = {**prof, **admin_prof}
            except Exception:
                pass
        session['uid'] = uid
        session['email'] = user_obj.get('email') or email
        session['name'] = prof.get('name') or session['email']
        session['role'] = prof.get('role') or 'user'
        if bool(prof.get('is_master')) and str(session.get('role') or '').strip().lower() == 'user':
            session['role'] = 'master'
        session['ai_enabled'] = _normalize_ai_access({
            'role': prof.get('role') or 'user',
            'ai_enabled': prof.get('ai_enabled'),
        })
        session['theme_key'] = _normalize_theme_key(prof.get('theme_key'))
        session['is_master'] = bool(prof.get('is_master')) or str(session.get('role') or '').strip().lower() == 'master'
        session['sb_token'] = token
        session['access_profile_id'] = prof.get('access_profile_id')

        if bool(session.get('is_master')) or str(session.get('role') or '').strip().lower() == 'master':
            _apply_tenant_to_session(None)
        else:
            tenant = _resolve_login_tenant(uid, session.get('email') or email, session.get('tenant_key'))
            if not tenant:
                session.clear()
                flash("Utente non associato ad alcun tenant attivo.", "danger")
                return redirect(url_for('login'))
            _apply_tenant_to_session(tenant)

        session.permanent = True
        session['login_at'] = int(time.time())
        session['last_seen'] = int(time.time())

        try:
            login_user_snapshot = {
                'role': session.get('role'),
                'access_profile_id': session.get('access_profile_id'),
                'is_master': session.get('is_master'),
            }
            session['access_modules'] = _normalize_user_modules(login_user_snapshot)
        except Exception:
            session['access_modules'] = None

        # ---------------- Password expiry (30 giorni) ----------------
        try:
            meta = (user_obj.get('user_metadata') or {}) if isinstance(user_obj, dict) else {}
            meta_updates = {}

            changed_dt = _parse_dt_any(meta.get('pw_changed_at'))
            if not changed_dt:
                # Utenti pre-esistenti: baseline "adesso" alla prima login post-aggiornamento
                changed_dt = datetime.now(timezone.utc)
                meta_updates['pw_changed_at'] = _dt_to_iso_utc(changed_dt)

            # fingerprint della password corrente (per vietare riuso alla scadenza / reset)
            if uid and not meta.get('pw_prev_hash'):
                meta_updates['pw_prev_hash'] = _pw_fingerprint(str(uid), password)

            if meta_updates:
                try:
                    sb_user_update_metadata(token, meta_updates)
                except Exception:
                    pass

            expires_dt = changed_dt + timedelta(days=PW_EXPIRE_DAYS)
            session['pw_changed_at'] = _dt_to_iso_utc(changed_dt)
            session['pw_expires_at'] = _dt_to_iso_utc(expires_dt)
            session['pw_force_change'] = (datetime.now(timezone.utc) >= expires_dt)
        except Exception:
            session['pw_force_change'] = False

        # ---------------- Store selection ----------------
        role_l = str(session.get('role') or '').lower()
        if bool(session.get('is_master')) or role_l == 'master':
            session.pop('store_code', None)
            session.pop('store_name', None)
        elif role_l not in {'admin', 'fornitore'}:
            # Se l'utente ha una sola associazione, seleziona automaticamente
            try:
                assigned = get_user_warehouse_stores(str(uid))
            except Exception:
                assigned = []
            if assigned and len(assigned) == 1:
                row = assigned[0] or {}
                session['store_code'] = row.get('store_code')
                session['store_name'] = row.get('store_name') or row.get('store_code')
            else:
                # Forziamo la scelta dello store all’avvio della sessione
                session.pop('store_code', None)
                session.pop('store_name', None)

        # Pop-up riepilogo giornata (mostrato una sola volta all'avvio post-login, quando lo store è selezionato)
        session['show_day_summary_popup'] = True

        # Connessione DB: per ora default su LOCALE (con fallback automatico tra 2 percorsi)
        session['db_mode'] = 'local'

        # Se la password è scaduta, obbliga al cambio prima di proseguire
        if session.get('pw_force_change'):
            flash('Password scaduta: impostane una nuova per continuare.', 'warning')
            return redirect(url_for('account_change_password'))

        flash('Accesso eseguito.', 'success')

        # Pulizia cache SharePoint (se usata)
        for k in (
            "sp_sharing_url",
            "sp_base_subpath",
            "sp_db_filename",
            "sp_resolved_for",
            "sp_resolved_drive_id",
            "sp_resolved_folder_item_id",
            "sp_resolved_folder_name",
            "sp_resolved_web_url",
        ):
            session.pop(k, None)
        nxt = _safe_next_url(nxt)
        if role_l == 'fornitore':
            default_target = url_for('warehouse.supplier_orders_received')
        else:
            default_target = url_for(_first_allowed_endpoint_for_user(current_user()))
        if not nxt or nxt == url_for('dashboard'):
            return redirect(default_target)
        return redirect(nxt)
    except Exception as e:
        logging.exception("Login fallito")
        flash(_friendly_login_error(e), 'danger')
        return redirect(url_for('login', next=_safe_next_url(nxt)))

@app.get('/logout')
def logout():
    session.clear()
    flash("Disconnesso.", 'success')
    return redirect(url_for('login'))


@app.post('/account/theme')
@login_required
def account_set_theme():
    payload = request.get_json(silent=True) if request.is_json else {}
    theme_key = _normalize_theme_key((request.form.get('theme_key') or (payload or {}).get('theme_key')))
    session['theme_key'] = theme_key
    token = session.get('sb_token')
    uid = session.get('uid')
    if token and uid:
        try:
            sb_update_profile(token, uid, theme_key=theme_key)
        except Exception:
            current_app.logger.exception("Errore salvataggio preferenza tema")
    if request.is_json:
        return jsonify({"ok": True, "theme_key": theme_key})
    flash("Tema aggiornato.", "success")
    return redirect(request.referrer or url_for(_first_allowed_endpoint_for_user(current_user())))


@app.post('/set-connection')
@login_required
def set_connection():
    """Salva in sessione la modalità di connessione al DB Access (locale/cloud).

    Nota: i parametri SharePoint sono fissi e non modificabili da interfaccia.
    """
    mode = (request.form.get('db_mode') or '').strip().lower()
    next_url = request.form.get('next') or request.args.get('next') or url_for('dashboard')

    if mode not in ('local', 'locale', 'cloud'):
        flash('Seleziona una modalità di connessione valida.', 'warning')
        return redirect(_safe_next_url(next_url))

    # Normalizza
    norm = 'cloud' if mode == 'cloud' else 'local'
    session['db_mode'] = norm

    # Invalida cache resolved SharePoint quando si cambia modalità (o si riconferma cloud)
    for k in (
        'sp_resolved_for',
        'sp_resolved_drive_id',
        'sp_resolved_folder_item_id',
        'sp_resolved_folder_name',
        'sp_resolved_web_url',
    ):
        session.pop(k, None)

    if norm == 'cloud':
        session['sp_sharing_url'] = 'https://fbinvestmentholding.sharepoint.com/:f:/r/sites/FB/F%20%20B/I%20LOVE%20POKE/OPS/CONDIVISA?e=5%3a10c1b246d77d49bdac14bfa6179c9028&sharingv2=true&fromShare=true&at=9'
        session['sp_base_subpath'] = 'DatiFile'
        session['sp_db_filename'] = 'Datifile.mdb'
        flash('Connessione impostata: Cloud (SharePoint).', 'success')
    else:
        # Pulisce parametri SharePoint: non servono in locale
        for k in ('sp_sharing_url', 'sp_base_subpath', 'sp_db_filename'):
            session.pop(k, None)
        flash('Connessione impostata: Locale.', 'success')

    return redirect(_safe_next_url(next_url))
@app.get('/auth/forgot')
def forgot_password():
    if session.get('uid'):
        return redirect(url_for('dashboard'))
    return render_template('forgot_password.html')

@app.post('/auth/forgot')
def forgot_password_post():
    email = (request.form.get('email') or '').strip().lower()
    if not email:
        flash("Inserisci la tua email.", 'warning')
        return redirect(url_for('forgot_password'))
    try:
        # Invia comunque la richiesta di reset (evita enumerazione)
        redirect_to = url_for('reset_password', _external=True)
        sb_request_password_reset(email, redirect_to)
        flash("Se l\'email esiste, riceverai un link per reimpostare la password.", 'info')
    except Exception as e:
        flash(f'Errore nell\'invio del link: {e}', 'danger')
    return redirect(url_for('login'))

@app.get('/auth/reset')
def reset_password():
    return render_template('reset_password.html')

@app.post('/auth/reset')
def reset_password_post():
    token = (request.form.get('token') or '').strip()
    new_password = (request.form.get('new_password') or '').strip()
    confirm_password = (request.form.get('confirm_password') or '').strip()
    if not token or not new_password:
        flash("Dati mancanti per reimpostare la password.", 'danger')
        return redirect(url_for('reset_password'))
    if new_password != confirm_password:
        flash("Le password non coincidono.", 'warning')
        return redirect(url_for('reset_password'))
    try:
        sb_user_update_password(token, new_password)
        flash("Password aggiornata. Ora puoi accedere con la nuova password.", 'success')
        return redirect(url_for('login'))
    except ValueError as ve:
        flash(str(ve), 'warning')
        return redirect(url_for('reset_password'))
    except Exception as e:
        flash(f'Errore durante l\'aggiornamento della password: {e}', 'danger')
        return redirect(url_for('reset_password'))

@app.route('/account/change-password', methods=['GET','POST'])
@login_required
def account_change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password') or ''
        new_password = (request.form.get('new_password') or '').strip()
        confirm_password = (request.form.get('confirm_password') or '').strip()
        if not current_password:
            flash("Inserisci la password attuale.", 'warning')
            return redirect(url_for('account_change_password'))
        if not new_password:
            flash("Inserisci una nuova password.", 'warning')
            return redirect(url_for('account_change_password'))
        if new_password != confirm_password:
            flash("Le password non coincidono.", 'warning')
            return redirect(url_for('account_change_password'))
        if new_password == current_password:
            flash("La nuova password non può essere uguale alla precedente.", 'warning')
            return redirect(url_for('account_change_password'))
        try:
            # verifica password attuale con signin "sonda"
            email = session.get('email') or ''
            sb_auth_signin(email, current_password)
        except Exception:
            flash("La password attuale non è corretta.", 'danger')
            return redirect(url_for('account_change_password'))
        try:
            token = session.get('sb_token')
            if not token:
                raise RuntimeError('Sessione scaduta. Effettua nuovamente il login.')
            sb_user_update_password(token, new_password, uid=str(session.get('uid') or ''))
            now_dt = datetime.now(timezone.utc)
            session['pw_force_change'] = False
            session['pw_changed_at'] = _dt_to_iso_utc(now_dt)
            session['pw_expires_at'] = _dt_to_iso_utc(now_dt + timedelta(days=PW_EXPIRE_DAYS))
            flash("Password aggiornata.", 'success')
            return redirect(url_for('dashboard'))
        except ValueError as ve:
            flash(str(ve), 'warning')
        except Exception as e:
            flash(f'Errore aggiornando la password: {e}', 'danger')
    return render_template('account_change_password.html')

# --------------------- ROUTES: ADMIN ---------------------

@app.get('/admin/catalogs')
@login_required
@admin_required
def admin_catalogs():
    token = session.get('sb_token')
    places = []
    todos = []
    try:
        rp = _sb_get('place_catalog', token, params={'select':'name,active','order':'name.asc'})
        places = rp.json() if getattr(rp, 'ok', False) else []
    except Exception as e:
        current_app.logger.warning('place_catalog load failed: %s', e)
    try:
        rt = _sb_get('todo_catalog', token, params={'select':'name,active','order':'name.asc'})
        todos = rt.json() if getattr(rt, 'ok', False) else []
    except Exception as e:
        current_app.logger.warning('todo_catalog load failed: %s', e)
    return render_template('admin_catalogs.html', places=places, todos=todos)


@app.route('/admin/rendiconto/distinta-cassa-config', methods=['GET', 'POST'])
@login_required
@master_required
def admin_distinta_cassa_config():
    from cash_statement_config_repository import (
        align_cash_statement_config_to_tenant,
        create_cash_statement_field,
        create_cash_statement_section,
        delete_cash_statement_field,
        delete_cash_statement_section,
        list_cash_statement_config,
        seed_default_cash_statement_config,
        set_cash_statement_field_visible,
        set_cash_statement_section_visible,
        update_cash_statement_field_behavior,
    )
    from tenant_config_repository import ensure_current_tenant, get_tenant, list_tenants

    ensure_current_tenant()
    tenant_key = (
        request.form.get("tenant_key")
        or request.args.get("tenant_key")
        or session.get("master_tenant_key")
        or ""
    ).strip()
    tenants = list_tenants()
    if not tenant_key and tenants:
        tenant_key = str(tenants[0].get("tenant_key") or "").strip()
    tenant = get_tenant(tenant_key)
    tenant_key = tenant.get("tenant_key")
    session["tenant_database"] = str(tenant.get("database_name") or "").strip()
    session["master_tenant_key"] = tenant_key
    align_cash_statement_config_to_tenant(tenant_key=tenant_key)
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        try:
            if action == "create_section":
                create_cash_statement_section(
                    label=request.form.get("section_label") or "",
                    label_en=request.form.get("section_label_en") or "",
                    label_fr=request.form.get("section_label_fr") or "",
                    label_es=request.form.get("section_label_es") or "",
                    section_kind=request.form.get("section_kind") or "fields",
                    sort_order=int(request.form.get("section_sort_order") or 100),
                    tenant_key=tenant_key,
                )
                flash("Sezione creata.", "success")
            elif action == "create_field":
                create_cash_statement_field(
                    section_key=request.form.get("field_section_key") or "",
                    label=request.form.get("field_label") or "",
                    label_en=request.form.get("field_label_en") or "",
                    label_fr=request.form.get("field_label_fr") or "",
                    label_es=request.form.get("field_label_es") or "",
                    value_type=request.form.get("field_value_type") or "money",
                    behavior=request.form.get("field_behavior") or "none",
                    formula_expression=request.form.get("field_formula") or "",
                    canonical_metric=request.form.get("field_canonical_metric") or "",
                    legacy_tipo=request.form.get("field_legacy_tipo") or "",
                    sort_order=int(request.form.get("field_sort_order") or 100),
                    required=(request.form.get("field_required") == "1"),
                    tenant_key=tenant_key,
                )
                flash("Voce creata.", "success")
            elif action == "toggle_section":
                visible = (request.form.get("visible") or "") == "1"
                set_cash_statement_section_visible(request.form.get("section_key") or "", visible=visible, tenant_key=tenant_key)
                flash("Visibilita sezione aggiornata.", "success")
            elif action == "toggle_field":
                visible = (request.form.get("visible") or "") == "1"
                set_cash_statement_field_visible(
                    section_key=request.form.get("section_key") or "",
                    field_key=request.form.get("field_key") or "",
                    visible=visible,
                    tenant_key=tenant_key,
                )
                flash("Visibilita voce aggiornata.", "success")
            elif action == "delete_section":
                ok = delete_cash_statement_section(
                    section_key=request.form.get("section_key") or "",
                    tenant_key=tenant_key,
                )
                flash("Sezione eliminata." if ok else "Sezione non eliminabile.", "success" if ok else "warning")
            elif action == "delete_field":
                ok = delete_cash_statement_field(
                    section_key=request.form.get("section_key") or "",
                    field_key=request.form.get("field_key") or "",
                    tenant_key=tenant_key,
                )
                flash("Voce eliminata." if ok else "Voce non eliminabile.", "success" if ok else "warning")
            elif action == "update_field_behavior":
                ok = update_cash_statement_field_behavior(
                    section_key=request.form.get("section_key") or "",
                    field_key=request.form.get("field_key") or "",
                    behavior=request.form.get("field_behavior") or "none",
                    tenant_key=tenant_key,
                )
                flash("Comportamento voce aggiornato." if ok else "Voce non trovata.", "success" if ok else "warning")
        except Exception as e:
            current_app.logger.exception("Errore configurazione distinta cassa")
            flash(f"Errore configurazione distinta cassa: {e}", "danger")
        return redirect(url_for("admin_distinta_cassa_config", tenant_key=tenant_key))
    config = list_cash_statement_config(tenant_key=tenant_key)
    fields_by_section = {}
    for field in config.get("fields") or []:
        fields_by_section.setdefault(field.get("section_key"), []).append(field)
    return render_template(
        "admin_distinta_cassa_config.html",
        sections=config.get("sections") or [],
        fields_by_section=fields_by_section,
        tenant=tenant,
        tenants=tenants,
        selected_tenant_key=tenant_key,
    )


@app.route('/admin/rendiconto/ipratico-config', methods=['GET', 'POST'])
@login_required
@master_required
def admin_ipratico_config():
    from cash_statement_config_repository import list_cash_statement_config, seed_default_cash_statement_config
    from ipratico_config_repository import (
        delete_ipratico_payment_mapping,
        get_ipratico_config,
        list_ipratico_payment_mappings,
        save_ipratico_config,
        set_ipratico_payment_mapping_active,
        upsert_ipratico_payment_mappings,
    )
    from ipratico_repository import profile_payment_methods
    from primanota_repository import get_elenchi_options
    from tenant_config_repository import ensure_current_tenant, get_tenant, list_tenants, save_tenant_name

    today = date.today()
    ensure_current_tenant()
    tenant_key = (
        request.form.get("tenant_key")
        or request.args.get("tenant_key")
        or session.get("master_tenant_key")
        or ""
    ).strip()
    tenants = list_tenants()
    if not tenant_key and tenants:
        tenant_key = str(tenants[0].get("tenant_key") or "").strip()
    tenant = get_tenant(tenant_key)
    tenant_key = tenant.get("tenant_key")
    session["tenant_database"] = str(tenant.get("database_name") or "").strip()
    session["master_tenant_key"] = tenant_key
    seed_default_cash_statement_config(tenant_key=tenant_key)
    config = get_ipratico_config(tenant_key=tenant_key)
    statement_config = list_cash_statement_config(tenant_key=tenant_key)
    target_options = _ipratico_mapping_target_options(statement_config.get("fields") or [], {})
    profile = None
    profile_error = None
    form_state = {
        "store_code": (request.form.get("store_code") or request.args.get("store_code") or "").strip(),
        "start": (request.form.get("start") or request.args.get("start") or (today - timedelta(days=max(int(config.get("test_days") or 4) - 1, 0))).isoformat())[:10],
        "end": (request.form.get("end") or request.args.get("end") or today.isoformat())[:10],
    }
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "save_config":
            enabled = (request.form.get("enabled") or "") == "1"
            test_days = int(request.form.get("test_days") or config.get("test_days") or 4)
            config = save_ipratico_config(enabled=enabled, test_days=test_days, tenant_key=tenant_key)
            tenant = save_tenant_name(tenant_key, request.form.get("tenant_display_name") or tenant.get("display_name") or "")
            flash("Configurazione iPratico salvata.", "success")
            return redirect(url_for("admin_ipratico_config", tenant_key=tenant_key))
        if not config.get("enabled"):
            flash("Integrazione iPratico disattivata: riattivala per eseguire test o modificare mappature.", "warning")
            return redirect(url_for("admin_ipratico_config", tenant_key=tenant_key))
        if action == "save_mappings":
            rows = []
            for method_key in request.form.getlist("method_key"):
                safe_key = str(method_key or "").strip()
                method_label = (request.form.get(f"method_label_{safe_key}") or "").strip()
                target_raw = (request.form.get(f"target_{safe_key}") or "").strip()
                target_tipo = (request.form.get(f"target_tipo_{safe_key}") or "").strip().upper()
                target_section_key = target_field_key = target_label = ""
                if target_raw:
                    parts = target_raw.split("|", 2)
                    if len(parts) == 3:
                        target_section_key, target_field_key, target_label = parts
                rows.append(
                    {
                        "method_key": safe_key,
                        "method_label": method_label,
                        "target_section_key": target_section_key,
                        "target_field_key": target_field_key,
                        "target_label": target_label,
                        "target_tipo": target_tipo,
                        "is_active": bool(target_raw),
                    }
                )
            saved = upsert_ipratico_payment_mappings(rows, tenant_key=tenant_key)
            flash(f"Mappature iPratico salvate: {saved}.", "success")
            return redirect(url_for("admin_ipratico_config", tenant_key=tenant_key))
        if action == "seed_standard_mappings":
            try:
                store_options = get_elenchi_options(store_code=form_state["store_code"]) if form_state["store_code"] else {}
                seeded_rows = _build_ipratico_standard_mapping_rows(store_options)
                saved = upsert_ipratico_payment_mappings(seeded_rows, tenant_key=tenant_key)
                flash(f"Mappature standard aggiunte/aggiornate: {saved}.", "success")
            except Exception as e:
                current_app.logger.exception("Errore completamento mappature standard iPratico")
                flash(f"Errore completamento mappature standard: {e}", "danger")
            return redirect(url_for("admin_ipratico_config", tenant_key=tenant_key, store_code=form_state["store_code"], start=form_state["start"], end=form_state["end"]))
        if action == "toggle_mapping":
            method_key = (request.form.get("method_key") or "").strip()
            active = (request.form.get("active") or "") == "1"
            if set_ipratico_payment_mapping_active(method_key, active=active, tenant_key=tenant_key):
                flash("Mappatura aggiornata.", "success")
            else:
                flash("Mappatura non trovata.", "warning")
            return redirect(url_for("admin_ipratico_config", tenant_key=tenant_key, store_code=form_state["store_code"], start=form_state["start"], end=form_state["end"]))
        if action == "delete_mapping":
            method_key = (request.form.get("method_key") or "").strip()
            if delete_ipratico_payment_mapping(method_key, tenant_key=tenant_key):
                flash("Mappatura eliminata.", "success")
            else:
                flash("Mappatura non trovata.", "warning")
            return redirect(url_for("admin_ipratico_config", tenant_key=tenant_key, store_code=form_state["store_code"], start=form_state["start"], end=form_state["end"]))
        if action == "test_profile":
            if not form_state["store_code"]:
                profile_error = "Inserisci uno store per eseguire il test."
            else:
                try:
                    profile = profile_payment_methods(form_state["store_code"], form_state["start"], form_state["end"])
                    target_options = _ipratico_mapping_target_options(
                        statement_config.get("fields") or [],
                        get_elenchi_options(store_code=form_state["store_code"]),
                    )
                except Exception as e:
                    current_app.logger.exception("Errore test metodi pagamento iPratico")
                    profile_error = str(e)
    mappings = list_ipratico_payment_mappings(tenant_key=tenant_key)
    mapping_by_method = {str(m.get("method_key") or ""): m for m in mappings}
    return render_template(
        "admin_ipratico_config.html",
        config=config,
        tenant=tenant,
        tenants=tenants,
        selected_tenant_key=tenant_key,
        mappings=mappings,
        mapping_by_method=mapping_by_method,
        target_options=target_options,
        profile=profile,
        profile_error=profile_error,
        form_state=form_state,
    )


@app.route('/master/mobipos-test', methods=['GET', 'POST'])
@login_required
@master_required
def master_mobipos_test():
    from mobipos_repository import (
        DEFAULT_BASE_ENDPOINT,
        DEFAULT_USER_AGENT,
        get_sales,
        get_session_token,
        get_settings,
        get_settings_by_shop,
        register_api_token,
        summarize_sales_payload,
        validate_sales_period,
    )
    from tenant_config_repository import ensure_current_tenant, get_tenant, list_tenants

    ensure_current_tenant()
    tenants = list_tenants()
    tenant_key = (
        request.values.get("tenant_key")
        or session.get("master_tenant_key")
        or (str(tenants[0].get("tenant_key") or "").strip() if tenants else "")
    ).strip()
    tenant = get_tenant(tenant_key) if tenant_key else None
    if tenant:
        tenant_key = str(tenant.get("tenant_key") or tenant_key).strip()
        session["master_tenant_key"] = tenant_key
        session["tenant_database"] = str(tenant.get("database_name") or "").strip()

    today = date.today()
    default_start = (today - timedelta(days=2)).isoformat()
    form_state = {
        "tenant_key": tenant_key,
        "api_token": (request.form.get("api_token") or "").strip(),
        "session_token": (request.form.get("session_token") or "").strip(),
        "base_endpoint": (request.form.get("base_endpoint") or request.args.get("base_endpoint") or DEFAULT_BASE_ENDPOINT).strip() or DEFAULT_BASE_ENDPOINT,
        "user_agent": (request.form.get("user_agent") or request.args.get("user_agent") or DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT,
        "shop_id": (request.form.get("shop_id") or request.args.get("shop_id") or "").strip(),
        "from_date": (request.form.get("from_date") or request.args.get("from_date") or default_start)[:10],
        "to_date": (request.form.get("to_date") or request.args.get("to_date") or today.isoformat())[:10],
        "grouped": (request.form.get("grouped") or "") == "1",
    }
    result = None
    sales_summary = None
    action = ""
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        try:
            if action == "register":
                result = register_api_token(form_state["api_token"], user_agent=form_state["user_agent"])
                if result.ok and isinstance(result.data, dict) and result.data.get("endpoint"):
                    form_state["base_endpoint"] = str(result.data.get("endpoint") or form_state["base_endpoint"]).strip()
            elif action == "session_token":
                result = get_session_token(form_state["base_endpoint"], form_state["api_token"], user_agent=form_state["user_agent"])
                if result.ok and isinstance(result.data, dict) and result.data.get("token"):
                    form_state["session_token"] = str(result.data.get("token") or "").strip()
            elif action == "settings":
                result = get_settings(
                    form_state["base_endpoint"],
                    form_state["api_token"],
                    form_state["session_token"],
                    user_agent=form_state["user_agent"],
                )
            elif action == "settings_by_shop":
                if not form_state["shop_id"]:
                    raise ValueError("Inserisci shop id per settingsByShop.")
                result = get_settings_by_shop(
                    form_state["base_endpoint"],
                    form_state["api_token"],
                    form_state["session_token"],
                    form_state["shop_id"],
                    user_agent=form_state["user_agent"],
                )
            elif action == "sales":
                if not form_state["shop_id"]:
                    raise ValueError("Inserisci shop id per sales/list.")
                period_error = validate_sales_period(form_state["from_date"], form_state["to_date"])
                if period_error:
                    raise ValueError(period_error)
                result = get_sales(
                    form_state["base_endpoint"],
                    form_state["api_token"],
                    form_state["session_token"],
                    shop_id=form_state["shop_id"],
                    from_date=form_state["from_date"],
                    to_date=form_state["to_date"],
                    grouped=form_state["grouped"],
                    user_agent=form_state["user_agent"],
                )
                sales_summary = summarize_sales_payload(result.data)
            else:
                raise ValueError("Azione Mobipos non riconosciuta.")
        except Exception as e:
            current_app.logger.exception("Errore pagina test Mobipos")
            result = {
                "ok": False,
                "status_code": None,
                "method": "-",
                "url": "-",
                "error": str(e),
                "data": None,
            }
    return render_template(
        "master_mobipos_test.html",
        tenants=tenants,
        tenant=tenant,
        selected_tenant_key=tenant_key,
        form_state=form_state,
        result=result,
        sales_summary=sales_summary,
        action=action,
        docs_url="https://mobipos.it/ws/docs/third-party-api/index",
    )


@app.route('/master/offer-plan-test', methods=['GET', 'POST'])
@login_required
@master_required
def master_offer_plan_test():
    from offer_plan_test_repository import (
        build_offer_plan_preview,
        get_offer_recipe,
        list_default_suppliers,
        list_offer_recipes,
        load_default_dos_materials,
        save_offer_recipe,
        set_offer_recipe_active,
    )

    today = date.today()
    materials = load_default_dos_materials(limit=1800)
    suppliers = list_default_suppliers()
    recipes = list_offer_recipes(include_inactive=True)
    active_recipes = [r for r in recipes if r.get("is_active")]
    edit_recipe_id = (request.form.get("recipe_uuid") or request.args.get("edit_recipe") or "").strip()
    recipe_form = get_offer_recipe(edit_recipe_id) if edit_recipe_id else None
    if not recipe_form:
        recipe_form = {
            "row_uuid": "",
            "recipe_name": "",
            "family": "",
            "production_area": "",
            "is_active": True,
            "ingredients": [],
        }
    form_state = {
        "week_code": (request.form.get("week_code") or request.args.get("week_code") or f"S{today.isocalendar().week:02d}").strip(),
        "test_date": (request.form.get("test_date") or request.args.get("test_date") or today.isoformat())[:10],
    }
    offer_rows = []
    for i in range(1, 9):
        offer_rows.append(
            {
                "production_date": (request.form.get(f"offer_{i}_production_date") or "").strip(),
                "service": (request.form.get(f"offer_{i}_service") or "").strip(),
                "family": (request.form.get(f"offer_{i}_family") or "").strip(),
                "area": (request.form.get(f"offer_{i}_area") or "").strip(),
                "recipe_uuid": (request.form.get(f"offer_{i}_recipe_uuid") or "").strip(),
                "portions": (request.form.get(f"offer_{i}_portions") or "").strip(),
            }
        )
    ingredient_rows = []
    for i in range(1, 13):
        ingredient_rows.append(
            {
                "supplier": (request.form.get(f"ingredient_{i}_supplier") or "").strip(),
                "material": (request.form.get(f"ingredient_{i}_material") or "").strip(),
                "unit": (request.form.get(f"ingredient_{i}_unit") or "").strip(),
                "cell": (request.form.get(f"ingredient_{i}_cell") or "").strip(),
                "qty_per_portion": (request.form.get(f"ingredient_{i}_qty_per_portion") or "").strip(),
                "thaw_hours": (request.form.get(f"ingredient_{i}_thaw_hours") or "").strip(),
                "note": (request.form.get(f"ingredient_{i}_note") or "").strip(),
            }
        )
    action = (request.form.get("action") or "").strip()
    if request.method == "POST" and action == "save_recipe":
        try:
            saved_id = save_offer_recipe(
                edit_recipe_id,
                {
                    "recipe_name": request.form.get("recipe_name"),
                    "family": request.form.get("recipe_family"),
                    "production_area": request.form.get("recipe_production_area"),
                    "is_active": request.form.get("recipe_is_active") == "1",
                },
                ingredient_rows,
            )
            flash("Ricetta salvata.", "success")
            return redirect(url_for("master_offer_plan_test", edit_recipe=saved_id))
        except Exception as e:
            current_app.logger.exception("Errore salvataggio ricetta piano offerta")
            flash(f"Errore salvataggio ricetta: {e}", "danger")
            recipe_form = {
                "row_uuid": edit_recipe_id,
                "recipe_name": (request.form.get("recipe_name") or "").strip(),
                "family": (request.form.get("recipe_family") or "").strip(),
                "production_area": (request.form.get("recipe_production_area") or "").strip(),
                "is_active": request.form.get("recipe_is_active") == "1",
                "ingredients": ingredient_rows,
            }
    elif request.method == "POST" and action == "toggle_recipe":
        try:
            target_id = (request.form.get("toggle_recipe_uuid") or "").strip()
            set_offer_recipe_active(target_id, request.form.get("toggle_active") == "1")
            flash("Stato ricetta aggiornato.", "success")
            return redirect(url_for("master_offer_plan_test"))
        except Exception as e:
            current_app.logger.exception("Errore stato ricetta piano offerta")
            flash(f"Errore stato ricetta: {e}", "danger")

    if request.method != "POST":
        for idx, recipe in enumerate(active_recipes[:3]):
            production_date = today + timedelta(days=idx)
            offer_rows[idx].update(
                {
                    "production_date": production_date.isoformat(),
                    "service": "Pranzo",
                    "family": recipe.get("family") or "",
                    "area": recipe.get("production_area") or "",
                    "recipe_uuid": recipe.get("row_uuid") or "",
                    "portions": "10",
                }
            )
    preview = None
    if request.method == "POST" and action == "generate_preview":
        try:
            active_full_recipes = []
            for recipe in active_recipes:
                full = get_offer_recipe(recipe.get("row_uuid"))
                if full and full.get("is_active"):
                    active_full_recipes.append(full)
            preview = build_offer_plan_preview(form_state["test_date"], offer_rows, active_full_recipes)
        except Exception as e:
            current_app.logger.exception("Errore test piano offerta")
            flash(f"Errore calcolo piano offerta: {e}", "danger")
    recipe_ingredients = list(recipe_form.get("ingredients") or [])
    while len(recipe_ingredients) < 12:
        recipe_ingredients.append(
            {"supplier": "", "material": "", "unit": "", "cell": "", "qty_per_portion": "", "thaw_hours": "0", "note": ""}
        )
    families = sorted({str(r.get("family") or "").strip() for r in recipes if str(r.get("family") or "").strip()}, key=str.lower)
    production_areas = sorted(
        {str(r.get("production_area") or "").strip() for r in recipes if str(r.get("production_area") or "").strip()},
        key=str.lower,
    )
    return render_template(
        "master_offer_plan_test.html",
        form_state=form_state,
        offer_rows=offer_rows,
        recipes=recipes,
        active_recipes=active_recipes,
        recipe_form=recipe_form,
        recipe_ingredients=recipe_ingredients,
        suppliers=suppliers,
        families=families,
        production_areas=production_areas,
        materials=materials,
        preview=preview,
    )


def _ipratico_mapping_target_options(fields: list[dict], elenchi_options: dict | None = None) -> list[dict]:
    section_labels = {
        "dati_chiusura": "Dati chiusura",
        "ticket": "Ticket",
        "delivery": "Delivery",
        "coupon": "Coupon",
        "distinte": "Distinte",
    }
    relevant_keys = {
        "vendite_lorde",
        "annullati",
        "scontrini",
        "pos",
        "contanti",
        "ticket",
    }
    out = []
    for field in fields or []:
        if not field.get("is_active") or not field.get("is_visible"):
            continue
        key = str(field.get("field_key") or "")
        section = str(field.get("section_key") or "")
        if key not in relevant_keys:
            continue
        label = str(field.get("label") or key)
        section_label = section_labels.get(section, section)
        value = f"{section}|{key}|{label}"
        out.append({"value": value, "label": f"{section_label} - {label}", "section_key": section, "field_key": key, "default_tipo": ""})
    opts = elenchi_options or {}
    for section, field_key, collection_key, section_label in (
        ("ticket", "ticket_line", "tickets", "Ticket"),
        ("delivery", "delivery_line", "deliveries", "Delivery"),
        ("coupon", "coupon_line", "coupons", "Coupon"),
    ):
        for opt in opts.get(collection_key) or []:
            label = str((opt or {}).get("value") or "").strip()
            if not label:
                continue
            tipo = str((opt or {}).get("tipo") or "").strip().upper()
            out.append(
                {
                    "value": f"{section}|{field_key}|{label}",
                    "label": f"{section_label} - {label}",
                    "section_key": section,
                    "field_key": field_key,
                    "default_tipo": tipo,
                }
            )
    return out


@app.route("/master/delivery-providers", methods=["GET", "POST"])
@login_required
@master_required
def admin_delivery_providers():
    from delivery_repository import (
        delete_delivery_provider,
        ensure_delivery_providers_schema,
        list_delivery_providers,
        save_delivery_provider,
    )
    from primanota_repository import get_elenchi_options

    if not _resolve_master_admin_tenant():
        flash("Seleziona prima il tenant da configurare.", "warning")
        return redirect(url_for("master_admin_tenant_select"))

    def _delivery_base(value: str) -> str:
        raw = re.sub(r"\s+", " ", str(value or "").strip()).strip()
        return re.sub(r"\bCONTANTI\b", "", raw, flags=re.IGNORECASE).strip()

    def _delivery_provider_options() -> list[dict]:
        try:
            opts = get_elenchi_options(store_code="")
            deliveries = opts.get("deliveries") or []
        except Exception:
            deliveries = []
        found: dict[str, dict] = {}
        for row in deliveries:
            voce = str((row or {}).get("value") or "").strip()
            base = _delivery_base(voce)
            if not base:
                continue
            key = base.upper()
            item = found.setdefault(key, {"platform": key, "label": base, "voices": []})
            item["voices"].append(voce)
        return sorted(found.values(), key=lambda x: str(x.get("label") or "").lower())

    ensure_delivery_providers_schema(seed_defaults=False)
    delivery_options = _delivery_provider_options()
    valid_platforms = {str(o.get("platform") or "").strip().upper() for o in delivery_options}
    if request.method == "POST":
        actions = [str(v or "").strip() for v in request.form.getlist("action") if str(v or "").strip()]
        action = actions[-1] if actions else ""
        try:
            if action in {"save", "create"}:
                row_uuid = (request.form.get("row_uuid") or "").strip() if action == "save" else ""
                platform = str(request.form.get("platform") or "").strip().upper()
                if valid_platforms and platform not in valid_platforms:
                    raise ValueError("Seleziona un provider presente nelle voci Delivery della distinta cassa.")
                save_delivery_provider(
                    row_uuid=row_uuid or None,
                    provider_key=request.form.get("provider_key") or platform,
                    platform=platform,
                    label=request.form.get("label") or "",
                    logo_filename=request.form.get("logo_filename") or "",
                    rating_unit=request.form.get("rating_unit") or "number",
                    opening_mode=request.form.get("opening_mode") or "opening",
                    opening_label=request.form.get("opening_label") or "",
                    is_active=(request.form.get("is_active") or "") == "1",
                    sort_order=int(request.form.get("sort_order") or 0),
                )
                flash("Provider delivery salvato.", "success")
            elif action == "delete":
                row_uuid = (request.form.get("row_uuid") or "").strip()
                if delete_delivery_provider(row_uuid):
                    flash("Provider delivery eliminato.", "success")
                else:
                    flash("Provider delivery non trovato.", "warning")
        except Exception as e:
            current_app.logger.exception("Errore salvataggio provider delivery")
            flash(f"Errore configurazione provider delivery: {e}", "danger")
        return redirect(url_for("admin_delivery_providers"))

    return render_template(
        "admin_delivery_providers.html",
        providers=list_delivery_providers(active_only=False),
        delivery_options=delivery_options,
    )


def _build_ipratico_standard_mapping_rows(elenchi_options: dict | None = None) -> list[dict]:
    rows = [
        _mapping_row("rule:payment:cash", "Regola pagamento contanti", "dati_chiusura", "contanti", "CONTANTI", ""),
        _mapping_row("rule:payment:contanti", "Regola pagamento contanti", "dati_chiusura", "contanti", "CONTANTI", ""),
        _mapping_row("rule:payment:cashondelivery", "Regola cash on delivery", "dati_chiusura", "contanti", "CONTANTI", ""),
        _mapping_row("rule:payment:pos", "Regola pagamento POS", "dati_chiusura", "pos", "POS", ""),
        _mapping_row("rule:payment:card", "Regola pagamento carta", "dati_chiusura", "pos", "POS", ""),
        _mapping_row("rule:payment:bancomat", "Regola pagamento bancomat", "dati_chiusura", "pos", "POS", ""),
        _mapping_row("rule:payment:visa", "Regola pagamento Visa", "dati_chiusura", "pos", "POS", ""),
        _mapping_row("rule:payment:mastercard", "Regola pagamento Mastercard", "dati_chiusura", "pos", "POS", ""),
        _mapping_row("rule:payment:nexi", "Regola pagamento Nexi", "dati_chiusura", "pos", "POS", ""),
    ]
    opts = elenchi_options or {}
    ticket_target = _first_option(opts.get("tickets") or [], ["ticket"]) or _first_option(opts.get("tickets") or [], [])
    if ticket_target:
        rows.append(_mapping_row("rule:payment:ticket", "Regola pagamento ticket", "ticket", "ticket_line", ticket_target["value"], ticket_target.get("tipo") or "SI"))
    else:
        rows.append(_mapping_row("rule:payment:ticket", "Regola pagamento ticket", "dati_chiusura", "ticket", "TICKET", ""))

    satispay_target = _first_option(opts.get("coupons") or [], ["satispay"])
    if satispay_target:
        rows.append(_mapping_row("rule:payment:satispay", "Regola pagamento Satispay", "coupon", "coupon_line", satispay_target["value"], satispay_target.get("tipo") or "SI"))

    delivery_options = opts.get("deliveries") or []
    providers = ["deliveroo", "glovo", "ubereats", "uber eats", "justeat", "just eat", "wolt"]
    for provider in providers:
        provider_key = provider.replace(" ", "")
        cash_target = _first_option(delivery_options, [provider, "contant"]) or _first_option(delivery_options, [provider, "cash"])
        online_target = _first_option_excluding(delivery_options, [provider], ["contant", "cash"])
        if cash_target:
            rows.append(_mapping_row(f"rule:delivery:{provider_key}:cash", f"Regola {provider.title()} contanti", "delivery", "delivery_line", cash_target["value"], cash_target.get("tipo") or "NO"))
        if online_target:
            rows.append(_mapping_row(f"rule:delivery:{provider_key}:online", f"Regola {provider.title()} online", "delivery", "delivery_line", online_target["value"], online_target.get("tipo") or "SI"))
    return rows


def _mapping_row(method_key: str, method_label: str, section: str, field: str, label: str, tipo: str) -> dict:
    return {
        "method_key": method_key,
        "method_label": method_label,
        "target_section_key": section,
        "target_field_key": field,
        "target_label": label,
        "target_tipo": tipo,
        "is_active": True,
        "notes": "Mappatura standard StoreHub",
    }


def _first_option(options: list[dict], tokens: list[str]) -> dict | None:
    for opt in options or []:
        label = str((opt or {}).get("value") or "").lower()
        if all(token.lower() in label for token in tokens):
            return opt
    return None


def _first_option_excluding(options: list[dict], tokens: list[str], excluded_tokens: list[str]) -> dict | None:
    for opt in options or []:
        label = str((opt or {}).get("value") or "").lower()
        if all(token.lower() in label for token in tokens) and not any(token.lower() in label for token in excluded_tokens):
            return opt
    return None

@app.post('/admin/catalogs/add')
@login_required
@admin_required
def admin_catalogs_add():
    token = session.get('sb_token')
    kind = (request.form.get('kind') or '').strip()
    name = (request.form.get('name') or '').strip()
    if not name:
        flash('Nome obbligatorio', 'warning')
        return redirect(url_for('admin_catalogs'))
    table = 'place_catalog' if kind=='place' else 'todo_catalog'
    try:
        _sb_upsert(table, token, json_body=[{'name': name, 'active': True}], on_conflict='name')
        flash(('Luogo' if table=='place_catalog' else 'To-Do') + f' aggiunto: {name}', 'success')
    except Exception as e:
        flash(f'Errore aggiungendo: {e}', 'danger')
    return redirect(url_for('admin_catalogs'))

@app.post('/admin/catalogs/delete')
@login_required
@admin_required
def admin_catalogs_delete():
    token = session.get('sb_token')
    kind = (request.form.get('kind') or '').strip()
    names = request.form.getlist('names') or []
    if not names:
        flash('Seleziona almeno un elemento', 'warning')
        return redirect(url_for('admin_catalogs'))
    table = 'place_catalog' if kind=='place' else 'todo_catalog'
    def pgrest_in(values):
        safe=[]
        for v in values:
            if v is None: continue
            s=str(v).replace('"','\\"')
            safe.append(f'"{s}"')
        return f'in.({",".join(safe)})'
    try:
        r = _sb_delete(table, token, params={'name': pgrest_in(names)})
        if not getattr(r, 'ok', False):
            flash(f'Errore: {getattr(r, "text", "")}', 'danger')
        else:
            flash(f'Eliminati {len(names)} elementi', 'success')
    except Exception as e:
        flash(f'Errore eliminazione: {e}', 'danger')
    return redirect(url_for('admin_catalogs'))
@app.get('/admin/users')
@login_required
@admin_or_master_required
def admin_users():
    users = []
    try:
        if _is_platform_master(current_user()) and not _resolve_master_admin_tenant():
            flash("Seleziona prima il tenant da amministrare.", "warning")
            return redirect(url_for("master_admin_tenant_select"))
        tenant_rows = _tenant_users_for_current_admin()
        profiles = sb_admin_list_profiles()
        profiles_by_uid = {str(p.get("id") or "").strip(): p for p in profiles or [] if str(p.get("id") or "").strip()}
        profiles_by_email = {str(p.get("email") or "").strip().lower(): p for p in profiles or [] if str(p.get("email") or "").strip()}
        for tenant_row in tenant_rows or []:
            if not bool(tenant_row.get("is_active", True)):
                continue
            uid = str(tenant_row.get("user_id") or "").strip()
            email = str(tenant_row.get("email") or "").strip().lower()
            profile = profiles_by_uid.get(uid) or profiles_by_email.get(email) or {}
            u = dict(profile)
            u["id"] = str(profile.get("id") or uid)
            u["email"] = str(profile.get("email") or email)
            u["name"] = str(profile.get("name") or "")
            u["global_role"] = profile.get("role")
            u["tenant_saved_role"] = str(tenant_row.get("tenant_role") or profile.get("role") or "user").strip().lower()
            u["role"] = _effective_tenant_role(tenant_row, u["tenant_saved_role"])
            u["tenant_key"] = tenant_row.get("tenant_key")
            users.append(u)
        ensure_supplier_order_flow_schema()
        for u in users or []:
            try:
                u['supplier_assignments_count'] = len(get_supplier_user_assignments(str(u.get('id') or '')))
            except Exception:
                u['supplier_assignments_count'] = 0
    except Exception as e:
        flash(f'Errore caricando utenti: {e}', 'danger')
    return render_template(
        'admin_users.html',
        users=users,
        admin_users_ui_version=ADMIN_USERS_UI_VERSION,
        selected_tenant_key=_current_tenant_key_for_admin(),
        is_platform_master=_is_platform_master(current_user()),
    )


# --------------------- ROUTES: ADMIN - IMPORT CSV UTENTI ---------------------

import csv
import io


def _decode_upload_bytes(data: bytes) -> str:
    """Decodifica bytes CSV in stringa (utf-8/utf-8-sig/cp1252) in modo robusto."""
    if data is None:
        return ''
    for enc in ('utf-8-sig', 'utf-8', 'cp1252'):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode('utf-8', errors='replace')


def _sniff_csv_dialect(text: str) -> csv.Dialect:
    sample = (text or '')[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=[',', ';', '\t'])
    except Exception:
        # fallback: CSV italiani spesso usano ';'
        class _D(csv.Dialect):
            delimiter = ';'
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = '\n'
            quoting = csv.QUOTE_MINIMAL
        return _D()


def _historical_import_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), "storehub_historical_imports")
    os.makedirs(path, exist_ok=True)
    return path


def _historical_import_path(import_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", str(import_id or ""))
    if not safe:
        raise ValueError("Import non valido.")
    return os.path.join(_historical_import_dir(), f"{safe}.json")


def _guess_historical_target(header: str) -> str:
    h = _norm_header(header)
    guesses = {
        "store": "store_code",
        "site": "store_code",
        "codicestore": "store_code",
        "codicenegozio": "store_code",
        "negozio": "store_code",
        "data": "business_date",
        "date": "business_date",
        "giorno": "business_date",
        "venditelorde": "gross_revenue",
        "grossrevenue": "gross_revenue",
        "giroaffari": "gross_revenue",
        "netrevenue": "net_revenue",
        "venditenette": "net_revenue",
        "annullati": "cancelled_amount",
        "cancelledamount": "cancelled_amount",
        "scontrini": "receipts_count",
        "receiptcount": "receipts_count",
        "pos": "pos_amount",
        "contanti": "cash_amount",
        "cash": "cash_amount",
        "ticket": "ticket_total",
        "delivery": "delivery_total",
        "deliveryonline": "delivery_online_amount",
        "deliverycontanti": "delivery_cash_amount",
        "coupon": "coupon_total",
        "spese": "expenses_net",
        "expenses": "expenses_net",
        "distinte": "cash_deposits_total",
        "versamentocontanti": "cash_deposits_total",
        "differenzacassa": "cash_difference",
        "cashdifference": "cash_difference",
    }
    return guesses.get(h, "")


@app.route('/admin/rendiconto/import-storico-giornaliero', methods=['GET', 'POST'])
@login_required
@master_required
def admin_historical_daily_sales_import():
    from tenant_config_repository import ensure_current_tenant, get_tenant, list_tenants

    ensure_current_tenant()
    tenants = list_tenants()
    tenant_key = (request.values.get("tenant_key") or os.getenv("STOREHUB_TENANT_KEY") or "default").strip() or "default"
    tenant = get_tenant(tenant_key)
    targets = historical_daily_sales_import_targets()

    if request.method == "GET":
        return render_template(
            "admin_import_storico_giornaliero.html",
            step="upload",
            tenants=tenants,
            tenant=tenant,
            selected_tenant_key=tenant_key,
            targets=targets,
        )

    action = (request.form.get("action") or "").strip()
    if action == "upload_csv":
        file = request.files.get("csv_file")
        if not file or not file.filename:
            flash("Seleziona un file CSV.", "warning")
            return redirect(url_for("admin_historical_daily_sales_import", tenant_key=tenant_key))
        raw = file.read()
        if not raw:
            flash("File CSV vuoto.", "warning")
            return redirect(url_for("admin_historical_daily_sales_import", tenant_key=tenant_key))
        text = _decode_upload_bytes(raw)
        dialect = _sniff_csv_dialect(text)
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        headers = [str(h or "").strip() for h in (reader.fieldnames or []) if str(h or "").strip()]
        if not headers:
            flash("Intestazioni CSV non trovate.", "danger")
            return redirect(url_for("admin_historical_daily_sales_import", tenant_key=tenant_key))
        rows = []
        for idx, row in enumerate(reader):
            if idx >= 50000:
                break
            clean = {h: str((row or {}).get(h) or "").strip() for h in headers}
            if any(clean.values()):
                rows.append(clean)
        if not rows:
            flash("Nessuna riga valida nel CSV.", "warning")
            return redirect(url_for("admin_historical_daily_sales_import", tenant_key=tenant_key))
        import_id = uuid4().hex
        payload = {
            "tenant_key": tenant_key,
            "filename": file.filename,
            "headers": headers,
            "rows": rows,
        }
        with open(_historical_import_path(import_id), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        guesses = {h: _guess_historical_target(h) for h in headers}
        return render_template(
            "admin_import_storico_giornaliero.html",
            step="map",
            tenants=tenants,
            tenant=tenant,
            selected_tenant_key=tenant_key,
            targets=targets,
            import_id=import_id,
            filename=file.filename,
            headers=headers,
            guesses=guesses,
            preview=rows[:10],
            row_count=len(rows),
        )

    if action == "confirm_import":
        import_id = request.form.get("import_id") or ""
        try:
            with open(_historical_import_path(import_id), "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            flash("Import non trovato o scaduto: ricarica il CSV.", "danger")
            return redirect(url_for("admin_historical_daily_sales_import", tenant_key=tenant_key))
        tenant_key = str(payload.get("tenant_key") or tenant_key).strip() or "default"
        headers = list(payload.get("headers") or [])
        rows = list(payload.get("rows") or [])
        mapping = {}
        used_targets = {}
        for header in headers:
            target = (request.form.get(f"map__{header}") or "").strip()
            if not target:
                continue
            if target in used_targets:
                flash(f"Il campo '{target}' è mappato due volte: correggi la mappatura.", "danger")
                return redirect(url_for("admin_historical_daily_sales_import", tenant_key=tenant_key))
            used_targets[target] = header
            mapping[header] = target
        if "store_code" not in used_targets or "business_date" not in used_targets:
            flash("Mappatura obbligatoria: store e data.", "danger")
            return redirect(url_for("admin_historical_daily_sales_import", tenant_key=tenant_key))

        ok = 0
        errors = []
        for idx, row in enumerate(rows, start=2):
            try:
                upsert_historical_daily_sales_from_csv_row(csv_row=row, mapping=mapping, tenant_key=tenant_key)
                ok += 1
            except Exception as exc:
                if len(errors) < 20:
                    errors.append(f"Riga {idx}: {exc}")
        try:
            os.remove(_historical_import_path(import_id))
        except Exception:
            pass
        result = {"ok": ok, "errors": errors, "total": len(rows)}
        flash(f"Import storico completato: {ok}/{len(rows)} righe scritte.", "success" if not errors else "warning")
        return render_template(
            "admin_import_storico_giornaliero.html",
            step="result",
            tenants=tenants,
            tenant=get_tenant(tenant_key),
            selected_tenant_key=tenant_key,
            targets=targets,
            result=result,
        )

    flash("Azione non valida.", "warning")
    return redirect(url_for("admin_historical_daily_sales_import", tenant_key=tenant_key))


def _norm_header(s: str) -> str:
    return (s or '').strip().lower().replace(' ', '').replace('_', '')


def _sb_admin_get_profile_by_email(email: str) -> dict | None:
    """Lookup profilo per email usando Service Role (no RLS)."""
    if not email:
        return None
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    params = {'select': 'id,email,name,role', 'email': f'eq.{email}', 'limit': '1'}
    r = _session().get(url, headers=_sb_headers_admin(), params=params, timeout=20)
    _raise_with_body(r)
    arr = r.json() or []
    return arr[0] if arr else None


def _sb_admin_upsert_profile(uid: str, email: str, name: str, role: str) -> None:
    if not uid:
        raise ValueError('uid mancante')
    role_l = str(role or 'user').strip().lower() or 'user'
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    payload = [{'id': uid, 'email': email, 'name': name or None, 'role': role_l, 'ai_enabled': False}]

    # PostgREST upsert: serve Prefer resolution=merge-duplicates per aggiornare record già esistenti
    headers = _sb_headers_admin()
    headers['Prefer'] = 'resolution=merge-duplicates'
    # Per le chiamate admin a PostgREST è più robusto usare la service role anche come apikey
    if SUPABASE_SERVICE_ROLE_KEY:
        headers['apikey'] = SUPABASE_SERVICE_ROLE_KEY

    r = _session().post(
        url,
        headers=headers,
        params={'on_conflict': 'id'},
        data=json.dumps(payload),
        timeout=20
    )
    _raise_with_body(r)


def _ensure_user_and_profile(email: str, name: str, role: str) -> tuple[str | None, bool, str | None]:
    """Ritorna (uid, created, warning). created=True se è stato inviato invito."""
    email = (email or '').strip().lower()
    if not email:
        return None, False, 'Email mancante'

    prof = None
    try:
        prof = _sb_admin_get_profile_by_email(email)
    except Exception:
        prof = None

    if prof and prof.get('id'):
        uid = prof['id']
        try:
            _sb_admin_upsert_profile(uid, email, name, role)
        except Exception:
            pass
        return uid, False, None

    # Non trovato: invito (crea auth user e normalmente trigger crea profile)
    created = False
    uid = None
    warn = None
    try:
        redirect_to = _auth_reset_redirect_url()
        resp = sb_admin_invite_user(email, redirect_to=redirect_to)
        created = True
        if isinstance(resp, dict):
            uid = resp.get('id') or (resp.get('user') or {}).get('id') or resp.get('user_id')
    except Exception as e:
        # Se l'utente esiste già ma manca profilo, segnala
        warn = f'Invito non inviato ({e})'

    # Riprova lookup profilo qualche volta (trigger async)
    for _ in range(12):
        try:
            prof = _sb_admin_get_profile_by_email(email)
            if prof and prof.get('id'):
                uid = prof['id']
                break
        except Exception:
            pass
        time.sleep(0.25)

    if uid:
        try:
            _sb_admin_upsert_profile(uid, email, name, role)
        except Exception:
            pass
        return uid, created, warn

    # ultimo tentativo: se dall'invite abbiamo l'uid, crea la riga profilo
    if uid:
        try:
            _sb_admin_upsert_profile(uid, email, name, role)
            return uid, created, warn
        except Exception as e:
            return None, created, f'Impossibile creare profilo: {e}'

    return None, created, (warn or 'Profilo non trovato/creato')


@app.route('/admin/users/import-csv', methods=['GET', 'POST'])
@login_required
@admin_or_master_required
def admin_users_import_csv():
    """Import massivo utenti + assegnazioni store da CSV."""
    platform_master = _is_platform_master(current_user())
    if request.method == 'GET':
        return render_template('admin_users_import_csv.html', result=None)

    file = request.files.get('csv_file')
    if not file:
        flash('Seleziona un file CSV.', 'warning')
        return redirect(url_for('admin_users_import_csv'))

    try:
        stores = get_warehouse_stores() or []
        code_to_name = {str(s.get('code')): (s.get('name') or '') for s in stores if s.get('code')}
    except Exception as e:
        current_app.logger.exception('Errore caricando elenco store')
        flash(f'Errore caricando elenco store: {e}', 'danger')
        return redirect(url_for('admin_users_import_csv'))

    raw_bytes = file.read() or b''
    text = _decode_upload_bytes(raw_bytes)
    dialect = _sniff_csv_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    # Mappa intestazioni (robusta)
    header_map = {}
    if reader.fieldnames:
        for h in reader.fieldnames:
            header_map[_norm_header(h)] = h

    def pick(row, *keys):
        for k in keys:
            hk = header_map.get(_norm_header(k))
            if hk and hk in row:
                return row.get(hk)
        return None

    allowed_roles = {'user', 'admin', 'supervisor', 'fornitore'}
    groups: dict[str, dict] = {}
    per_row = []
    warnings = []
    errors = []

    line_no = 1
    for row in reader:
        line_no += 1
        email = (pick(row, 'MAIL', 'EMAIL') or '').strip().lower()
        name = (pick(row, 'NOME', 'NAME') or '').strip()
        role = (pick(row, 'RUOLO', 'ROLE') or 'user').strip().lower()
        site = (pick(row, 'SITE', 'STORE', 'STORECODE', 'STORE_CODE') or '').strip()

        if not email or not name or not role or not site:
            msg = f'Riga {line_no}: campi obbligatori mancanti (MAIL/NOME/RUOLO/SITE).'
            errors.append(msg)
            per_row.append({'email': email, 'name': name, 'role': role, 'site': site, 'status': 'err', 'message': msg})
            continue
        if role not in allowed_roles:
            msg = f'Riga {line_no}: ruolo non valido: {role}. Valori ammessi: user/admin/supervisor/fornitore.'
            errors.append(msg)
            per_row.append({'email': email, 'name': name, 'role': role, 'site': site, 'status': 'err', 'message': msg})
            continue
        if site not in code_to_name:
            msg = f'Riga {line_no}: store {site} non trovato in warehouse_stores.'
            errors.append(msg)
            per_row.append({'email': email, 'name': name, 'role': role, 'site': site, 'status': 'err', 'message': msg})
            continue

        g = groups.get(email)
        if not g:
            g = {'email': email, 'name': name, 'role': role, 'sites': set(), 'rows': []}
            groups[email] = g
        else:
            if g.get('name') != name:
                warnings.append(f'Email {email}: NOME diverso tra righe (uso "{g.get("name")}").')
            if g.get('role') != role:
                warnings.append(f'Email {email}: RUOLO diverso tra righe (uso "{g.get("role")}").')

        if site in g['sites']:
            per_row.append({'email': email, 'name': name, 'role': role, 'site': site, 'status': 'warn', 'message': 'Duplicato (già presente nel CSV)'})
        else:
            g['sites'].add(site)
            g['rows'].append((name, role, site))
            per_row.append({'email': email, 'name': name, 'role': role, 'site': site, 'status': 'ok', 'message': 'In coda'})

    created_users = 0
    updated_users = 0
    assignments_ok = 0

    # esito per chiave (email,site)
    status_by_key: dict[tuple[str, str], tuple[str, str]] = {}

    for email, g in groups.items():
        uid, created, warn = _ensure_user_and_profile(g['email'], g['name'], g['role'])
        if warn:
            warnings.append(f'{email}: {warn}')
        if not uid:
            msg = f'{email}: impossibile creare/trovare utente.'
            errors.append(msg)
            for site in g['sites']:
                status_by_key[(email, site)] = ('err', msg)
            continue
        if not platform_master:
            try:
                sb_admin_update_profile(uid, role="user", ai_enabled=False, is_master=False)
            except Exception:
                try:
                    sb_admin_update_profile(uid, role="user", ai_enabled=False)
                except Exception:
                    pass
        try:
            if not str(g.get("role") or "").strip().lower() == "master":
                from tenant_config_repository import upsert_tenant_user

                upsert_tenant_user(
                    tenant_key=_current_tenant_key_for_admin(),
                    email=email,
                    user_id=uid,
                    tenant_role=g["role"],
                    is_active=True,
                )
        except Exception as e:
            warnings.append(f"{email}: associazione tenant non aggiornata ({e})")

        if created:
            created_users += 1
        else:
            updated_users += 1

        for site in sorted(g['sites']):
            try:
                upsert_user_store_assignment(uid, site, code_to_name.get(site) or None)
                assignments_ok += 1
                status_by_key[(email, site)] = ('ok', 'OK')
            except Exception as e:
                current_app.logger.exception('Errore assegnando store %s a %s', site, email)
                msg = f'Errore assegnazione store {site}: {e}'
                errors.append(f'{email}: {msg}')
                status_by_key[(email, site)] = ('err', msg)

    # aggiorna per_row con esiti finali
    final_rows = []
    for r in per_row:
        email = r.get('email')
        site = r.get('site')
        if not email or not site:
            final_rows.append(r)
            continue
        key = (email, site)
        if r.get('status') == 'warn' and 'Duplicato' in (r.get('message') or ''):
            final_rows.append(r)
            continue
        if key in status_by_key:
            st, msg = status_by_key[key]
            r = dict(r)
            r['status'] = st
            r['message'] = msg
        final_rows.append(r)

    result = {
        'created_users': created_users,
        'updated_users': updated_users,
        'assignments_ok': assignments_ok,
        'warnings': warnings,
        'errors': errors,
        'rows': final_rows,
    }

    return render_template('admin_users_import_csv.html', result=result)


@app.route('/admin/estrazioni/fabbisogno-sites', methods=['GET', 'POST'])
@login_required
@master_required
def admin_fabbisogno_sites():
    """Gestione tabella site -> codice fabbisogno (per export Scheduling)."""
    from estrazioni_repository import (
        list_fabbisogno_sites,
        upsert_fabbisogno_site,
        delete_fabbisogno_site,
        import_fabbisogno_csv,
    )

    import_result = None

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip().lower()
        try:
            if action == 'upsert':
                site = (request.form.get('site') or '').strip()
                codice = (request.form.get('codice_fabbisogno') or '').strip()
                upsert_fabbisogno_site(site, codice)
                flash('Mappatura salvata.', 'success')

            elif action == 'delete':
                site = (request.form.get('site') or '').strip()
                delete_fabbisogno_site(site)
                flash('Mappatura eliminata.', 'success')

            elif action == 'import_csv':
                file = request.files.get('csv_file')
                if not file:
                    flash('Seleziona un file CSV.', 'warning')
                    return redirect(url_for('admin_fabbisogno_sites'))
                raw_bytes = file.read() or b''
                text = _decode_upload_bytes(raw_bytes)
                ok, warnings = import_fabbisogno_csv(text)
                import_result = {'ok': ok, 'warnings': warnings}
                flash(f'Import completato: {ok} righe OK.', 'success' if ok else 'warning')
            else:
                flash('Azione non valida.', 'warning')
        except Exception as e:
            current_app.logger.exception('Errore admin fabbisogno sites')
            flash(f'Errore: {e}', 'danger')

    try:
        rows = list_fabbisogno_sites()
    except Exception as e:
        current_app.logger.exception('Errore caricando fabbisogno sites')
        flash(f'Errore caricando tabella: {e}', 'danger')
        rows = []

    return render_template('admin_fabbisogno_sites.html', rows=rows, import_result=import_result)


@app.route('/admin/finance-store-map', methods=['GET', 'POST'])
@login_required
@master_required
def admin_finance_store_map():
    import_result = None
    search = (request.args.get('q') or request.form.get('return_search') or '').strip()
    selected_code = (request.args.get('code') or request.form.get('selected_code') or '').strip()
    try:
        edit_assignment_id = int(request.args.get('edit_assignment_id') or 0)
    except Exception:
        edit_assignment_id = 0

    try:
        stores = get_warehouse_stores() or []
    except Exception as e:
        current_app.logger.exception('Errore caricando store app per finance map')
        flash(f'Errore caricando gli store app: {e}', 'danger')
        stores = []

    store_name_by_code = {
        str((s or {}).get('code') or '').strip(): str((s or {}).get('name') or '').strip()
        for s in (stores or [])
        if str((s or {}).get('code') or '').strip()
    }

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip().lower()
        try:
            if action == 'add_code':
                code = (request.form.get('code') or '').strip()
                label = (request.form.get('label') or '').strip()
                ok = import_code_catalog_rows(
                    [{"code": code, "label": label, "normalized_label": label}],
                    seed_source='admin_manual',
                )
                flash(f'Codice salvato: {ok}.', 'success' if ok else 'warning')
                selected_code = code
            elif action == 'import_codes':
                parsed = parse_code_catalog_text(request.form.get('catalog_text') or '')
                ok = import_code_catalog_rows(parsed.get('rows') or [], seed_source='admin_import')
                import_result = {'ok': ok, 'warnings': parsed.get('warnings') or []}
                flash(f'Codici importati/aggiornati: {ok}.', 'success' if ok else 'warning')
            elif action == 'assign_code':
                code = (request.form.get('code') or '').strip()
                store_code = (request.form.get('store_code') or '').strip()
                valid_from = (request.form.get('valid_from') or '').strip()
                note = (request.form.get('note') or '').strip()
                assignment_id = int(request.form.get('assignment_id') or 0)
                if assignment_id:
                    update_code_assignment(
                        assignment_id=assignment_id,
                        code=code,
                        store_code=store_code,
                        valid_from=valid_from,
                        note=note,
                    )
                    flash('Assegnazione aggiornata.', 'success')
                else:
                    save_code_assignment(code=code, store_code=store_code, valid_from=valid_from, note=note)
                    flash('Assegnazione salvata.', 'success')
                selected_code = code
            elif action == 'delete_assignment':
                assignment_id = int(request.form.get('assignment_id') or 0)
                delete_code_assignment(assignment_id)
                flash('Assegnazione eliminata.', 'success')
                selected_code = (request.form.get('selected_code') or '').strip() or selected_code
            else:
                flash('Azione non valida.', 'warning')
        except Exception as e:
            current_app.logger.exception('Errore admin finance store map')
            flash(f'Errore: {e}', 'danger')

    try:
        catalog_rows = list_code_catalog(search=search)
        assignment_rows = list_code_assignments(code=selected_code)
    except Exception as e:
        current_app.logger.exception('Errore caricando finance store map')
        flash(f'Errore caricando la mappatura finance: {e}', 'danger')
        catalog_rows = []
        assignment_rows = []

    for row in catalog_rows:
        current_store_code = str((row or {}).get('CurrentStoreCode') or '').strip()
        row['CurrentStoreName'] = store_name_by_code.get(current_store_code, '')

    for row in assignment_rows:
        row['StoreName'] = store_name_by_code.get(str((row or {}).get('StoreCode') or '').strip(), '')

    edit_assignment = None
    if edit_assignment_id:
        for row in assignment_rows:
            if int((row or {}).get('AssignmentId') or 0) == edit_assignment_id:
                edit_assignment = row
                selected_code = str((row or {}).get('CodeValue') or '').strip() or selected_code
                break

    return render_template(
        'admin_finance_store_map.html',
        search=search,
        selected_code=selected_code,
        rows=catalog_rows,
        code_options=catalog_rows,
        assignments=assignment_rows,
        stores=stores,
        import_result=import_result,
        edit_assignment=edit_assignment,
    )

# REMOVED_admin_users_new
@app.route('/admin/users/new-invite', methods=['GET','POST'])
@login_required
@admin_or_master_required
def admin_users_new_invite():
    ensure_supplier_order_flow_schema()
    platform_master = _is_platform_master(current_user())
    try:
        supplier_options = list_supplier_fornitori()
    except Exception:
        supplier_options = []
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        name  = (request.form.get('name') or '').strip()
        role  = (request.form.get('role') or 'user').strip()
        is_master = False
        if str(role or '').strip().lower() == 'master':
            role = 'user'
        supplier_row_uuids = request.form.getlist('supplier_row_uuids')

        if not email:
            flash("Inserisci un'email valida.", 'warning')
            return render_template('admin_user_invite_form.html', user_edit=None, supplier_options=supplier_options, assigned_supplier_ids=set(supplier_row_uuids), is_platform_master=platform_master)
        if str(role or '').strip().lower() == 'fornitore' and not [x for x in supplier_row_uuids if str(x).strip()]:
            flash("Per un utente fornitore devi associare almeno un fornitore.", 'warning')
            return render_template('admin_user_invite_form.html', user_edit=None, supplier_options=supplier_options, assigned_supplier_ids=set(supplier_row_uuids), is_platform_master=platform_master)

        try:
            # 1) invito via Supabase
            redirect_to = _auth_reset_redirect_url()
            new_uid = sb_admin_invite_user(email, redirect_to=redirect_to)

            # 2) aggiorna SOLO name e role
            # La risposta di /invite può non contenere l'uid; estrailo o recuperalo via email
            resp = new_uid
            uid_val = None
            try:
                if isinstance(resp, dict):
                    uid_val = resp.get('id') or (resp.get('user') or {}).get('id') or resp.get('user_id')
                elif isinstance(resp, str):
                    uid_val = resp
            except Exception:
                uid_val = None

            if not uid_val:
                # tenta lookup via email su public.profiles (il trigger dovrebbe aver già creato la riga)
                try:
                    url = f"{SUPABASE_URL}/rest/v1/profiles"
                    params = {'select': 'id,email', 'email': f'eq.{email}', 'limit': '1'}
                    r = _session().get(url, headers=_sb_admin_headers(is_json=False), params=params, timeout=20)
                    _raise_with_body(r)
                    arr = r.json() or []
                    if arr:
                        uid_val = arr[0].get('id')
                except Exception as ee:
                    logging.warning(f'Lookup uid via email fallito: {ee}')

            if uid_val:
                global_role = role if platform_master else "user"
                try:
                    sb_admin_update_profile(
                        uid_val,
                        name=name,
                        role=global_role,
                        ai_enabled=False,
                        is_master=is_master,
                    )
                except Exception as e:
                    try:
                        sb_admin_update_profile(
                            uid_val,
                            name=name,
                            role=global_role,
                            ai_enabled=False,
                        )
                        flash("Invito inviato, ma il flag Master non è stato aggiornato: aggiungi la colonna profiles.is_master su Supabase.", "warning")
                    except Exception:
                        logging.warning(f'Update profilo parziale (name/role) fallito: {e}')
                try:
                    if str(role or '').strip().lower() == 'fornitore':
                        replace_supplier_user_assignments(uid_val, supplier_row_uuids)
                    else:
                        replace_supplier_user_assignments(uid_val, [])
                except Exception as e:
                    logging.warning(f'Update mapping fornitore fallito: {e}')
                try:
                    if not is_master:
                        from tenant_config_repository import upsert_tenant_user

                        upsert_tenant_user(
                            tenant_key=_current_tenant_key_for_admin(),
                            email=email,
                            user_id=uid_val,
                            tenant_role=role,
                            is_active=True,
                        )
                except Exception as e:
                    logging.warning(f'Associazione tenant utente fallita: {e}')

            flash('Invito inviato.', 'success')
            return redirect(url_for('admin_users'))
        except Exception as e:
            flash(f"Errore inviando l'invito: {e}", 'danger')
            return render_template('admin_user_invite_form.html', user_edit=None, supplier_options=supplier_options, assigned_supplier_ids=set(supplier_row_uuids), is_platform_master=platform_master)

    # GET
    return render_template('admin_user_invite_form.html', user_edit=None, supplier_options=supplier_options, assigned_supplier_ids=set(), is_platform_master=platform_master)

@app.route('/admin/users/<uid>/edit', methods=['GET','POST'])
@login_required
@admin_or_master_required
def admin_users_edit(uid):
    ensure_supplier_order_flow_schema()
    # Utente target
    try:
        try:
            target = sb_admin_get_profile_by_id(
                uid,
                fields='id,email,name,role,access_profile_id,ai_enabled,is_master'
            ) or {}
        except Exception:
            target = sb_admin_get_profile_by_id(
                uid,
                fields='id,email,name,role'
            ) or {}
    except Exception as e:
        flash(f'Errore caricando utente: {e}', 'danger')
        return redirect(url_for('admin_users'))
    tenant_row = _require_user_in_current_tenant(uid, target)
    if tenant_row:
        target["global_role"] = target.get("role")
        target["tenant_saved_role"] = str(tenant_row.get("tenant_role") or target.get("role") or "user").strip().lower()
        target["role"] = _effective_tenant_role(tenant_row, target["tenant_saved_role"])
        target["tenant_key"] = tenant_row.get("tenant_key")

    # Profili di visualizzazione
    try:
        access_profiles = sb_admin_list_access_profiles(_current_tenant_key_for_admin())
    except Exception:
        access_profiles = []

    # Store disponibili + store assegnati
    try:
        stores = get_warehouse_stores()
    except Exception:
        stores = []
    available_store_codes = {str(s.get("code") or "").strip() for s in (stores or []) if str(s.get("code") or "").strip()}
    try:
        supplier_options = list_supplier_fornitori()
    except Exception:
        supplier_options = []
    try:
        assigned_rows = get_user_warehouse_stores(uid)
        assigned_codes = {
            str(r.get("store_code")).strip()
            for r in (assigned_rows or [])
            if str(r.get("store_code") or "").strip() in available_store_codes
        }
    except Exception:
        assigned_codes = set()
    try:
        assigned_supplier_ids = set(get_supplier_user_assignments(uid))
    except Exception:
        assigned_supplier_ids = set()
    tenant_ai_enabled = _current_tenant_ai_enabled()

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        role = (request.form.get('role') or 'user').strip()
        is_master = False
        tenant_admin_enabled = bool((tenant_row or {}).get("tenant_admin_enabled", True))
        if str(role or '').strip().lower() == 'master':
            role = 'user'
        if not _is_platform_master(current_user()) and str(role or '').strip().lower() == 'admin' and not tenant_admin_enabled:
            role = 'user'
        access_profile_id = (request.form.get('access_profile_id') or '').strip() or None
        ai_enabled = ('ai_enabled' in request.form)
        supplier_row_uuids = request.form.getlist('supplier_row_uuids')
        if str(role or '').lower() == 'admin':
            ai_enabled = ai_enabled and _current_tenant_ai_enabled()
        else:
            ai_enabled = ai_enabled and _current_tenant_ai_enabled()
        if str(role or '').strip().lower() == 'fornitore' and not [x for x in supplier_row_uuids if str(x).strip()]:
            flash("Per un utente fornitore devi associare almeno un fornitore.", 'warning')
            return render_template(
                'admin_user_form.html',
                user_edit=target,
                access_profiles=access_profiles,
                stores=stores,
                assigned_store_codes=assigned_codes,
                supplier_options=supplier_options,
                assigned_supplier_ids=set(str(x).strip() for x in supplier_row_uuids if str(x).strip()),
                admin_users_ui_version=ADMIN_USERS_UI_VERSION,
                is_platform_master=_is_platform_master(current_user()),
                tenant_ai_enabled=tenant_ai_enabled,
            )

        # Cambio password admin (opzionale) con conferma
        new_password = (request.form.get('new_password') or '').strip()
        new_password_confirm = (request.form.get('new_password_confirm') or '').strip()

        if new_password or new_password_confirm:
            if new_password != new_password_confirm:
                flash("Le password non coincidono: password non aggiornata.", 'warning')
            else:
                try:
                    sb_admin_set_password(uid, new_password)
                    flash("Password aggiornata.", 'success')
                except Exception as e:
                    flash(f'Errore aggiornando la password: {e}', 'danger')

        # Salva profilo (nome/ruolo/profilo visualizzazione)
        try:
            global_role = role if _is_platform_master(current_user()) else str(target.get("global_role") or "user")
            if not _is_platform_master(current_user()) and str(global_role).strip().lower() == "master":
                global_role = "user"
            update_fields = {
                "name": name,
                "role": global_role,
                "access_profile_id": access_profile_id,
                "ai_enabled": ai_enabled,
                "is_master": is_master,
            }
            try:
                sb_admin_update_profile(uid, **update_fields)
            except Exception:
                update_fields.pop("is_master", None)
                sb_admin_update_profile(uid, **update_fields)
                flash("Profilo salvato, ma il flag Master non è stato aggiornato: aggiungi la colonna profiles.is_master su Supabase.", "warning")
            # Aggiorna target per re-render in caso di altri errori dopo
            target.update({"name": name, "role": role, "access_profile_id": access_profile_id, "ai_enabled": ai_enabled, "is_master": is_master})
            if not _is_platform_master(current_user()):
                try:
                    from tenant_config_repository import upsert_tenant_user

                    upsert_tenant_user(
                        tenant_key=_current_tenant_key_for_admin(),
                        email=str(target.get("email") or "").strip().lower(),
                        user_id=uid,
                        tenant_role=_effective_tenant_role(tenant_row, role),
                        is_active=True,
                    )
                except Exception as e:
                    flash(f"Profilo salvato, ma ruolo tenant non aggiornato: {e}", "warning")
            if str(uid) == str(session.get('uid')):
                session['name'] = name
                session['role'] = role
                session['is_master'] = is_master
                session['access_profile_id'] = access_profile_id
                session['ai_enabled'] = _normalize_ai_access({'role': role, 'ai_enabled': ai_enabled})
                session['access_modules'] = _normalize_user_modules({
                    'role': role,
                    'is_master': is_master,
                    'access_profile_id': access_profile_id,
                    'access_modules': session.get('access_modules'),
                })
        except Exception as e:
            flash(f'Errore aggiornando utente: {e}', 'danger')

        # Salva store assegnati (sostituzione completa)
        store_codes = [
            str(c).strip()
            for c in request.form.getlist('store_codes')
            if str(c).strip() in available_store_codes
        ]
        if str(role or '').strip().lower() == 'fornitore':
            assigned_codes = set()
        else:
            try:
                code_to_name = {str(s.get("code")): (s.get("name") or None) for s in (stores or []) if s.get("code")}
                set_user_warehouse_stores(uid, store_codes, code_to_name=code_to_name)
                assigned_codes = {str(c).strip() for c in store_codes if str(c).strip()}
                flash("Store assegnati aggiornati.", "success")
            except Exception as e:
                flash(f"Errore aggiornando store assegnati: {e}", "danger")

        try:
            if str(role or '').strip().lower() == 'fornitore':
                replace_supplier_user_assignments(uid, supplier_row_uuids)
                assigned_supplier_ids = set(str(x).strip() for x in supplier_row_uuids if str(x).strip())
            else:
                replace_supplier_user_assignments(uid, [])
                assigned_supplier_ids = set()
            flash("Fornitori associati aggiornati.", "success")
        except Exception as e:
            flash(f"Errore aggiornando fornitori associati: {e}", "danger")

        return redirect(url_for('admin_users'))

    return render_template(
        'admin_user_form.html',
        user_edit=target,
        access_profiles=access_profiles,
        stores=stores,
        assigned_store_codes=assigned_codes,
        supplier_options=supplier_options,
        assigned_supplier_ids=assigned_supplier_ids,
        admin_users_ui_version=ADMIN_USERS_UI_VERSION,
        is_platform_master=_is_platform_master(current_user()),
        tenant_ai_enabled=tenant_ai_enabled,
    )


# REMOVED_admin_users_locations
@app.post('/admin/users/<uid>/recover')
@login_required
@admin_or_master_required
def admin_users_recover(uid):
    try:
        prof = sb_admin_get_profile_by_id(uid, fields='id,email,name,role')
        _require_user_in_current_tenant(uid, prof)
        if not prof or not prof.get('email'):
            flash("Utente non trovato o senza email.", 'warning')
            return redirect(url_for('admin_users'))
        email = prof['email']
        url = f'{SUPABASE_URL}/auth/v1/recover'
        r = _session().post(url, headers=_sb_headers(is_json=True), json={'email': email}, timeout=20)
        _raise_with_body(r)
        flash(f"Email di reset inviata a {escape(email)}. Verifica le impostazioni 'Site URL' in Supabase Auth se non arriva.", 'success')
    except Exception as e:
        logging.exception("Errore recover")
        flash(f'Errore invio reset: {e}', 'danger')
    return redirect(url_for('admin_users'))
# <<<<<<<



@app.post('/admin/users/<uid>/delete')
@login_required
@admin_or_master_required
def admin_users_delete(uid):
    """Elimina utente: prova a rimuovere dall'Auth (se disponibile) e sempre pulisce DB."""
    if not _is_platform_master(current_user()):
        try:
            prof = sb_admin_get_profile_by_id(uid, fields='id,email,name,role') or {}
            _require_user_in_current_tenant(uid, prof)
            from tenant_config_repository import delete_tenant_user

            if delete_tenant_user(_current_tenant_key_for_admin(), str(prof.get("email") or "").strip().lower()):
                flash("Utente rimosso dal tenant.", "success")
            else:
                flash("Associazione tenant non trovata.", "warning")
        except Exception as e:
            logging.exception("Errore rimozione utente da tenant")
            flash(f"Errore rimuovendo utente dal tenant: {e}", "danger")
        return redirect(url_for('admin_users'))

    # Piattaforma Master: cancellazione globale.
    # Prima puliamo DB (associazioni + profilo)
    try:
        sb_admin_delete_profile(uid)
    except Exception as e:
        logging.exception("Errore cancellando record DB utente")
        flash(f'Errore cancellando dati utente: {e}', 'danger')
        return redirect(url_for('admin_users'))

    # Poi proviamo a eliminare dall'Auth (se abbiamo la service role)
    try:
        if SUPABASE_SERVICE_ROLE_KEY:
            sb_admin_delete_auth_user(uid)
            flash("Utente eliminato (DB + Auth).", 'success')
        else:
            flash(
                "Utente rimosso dal DB. Per rimuoverlo dall'Auth usa la service role o la console Supabase.",
                'warning',
            )
    except Exception as e:
        logging.exception("Errore eliminando da Auth")
        flash(f'Utente rimosso dal DB ma non da Auth: {e}', 'warning')

    return redirect(url_for('admin_users'))


# --------------------- ROUTES: ADMIN - PROFILI VISUALIZZAZIONE ---------------------

def _modules_from_form() -> dict:
    selected = set(request.form.getlist("modules"))
    mods = {}
    for k, _label in ACCESS_MODULES:
        mods[k] = (k in selected)
    return mods

@app.get('/admin/access-profiles')
@login_required
@admin_or_master_required
def admin_access_profiles():
    tenant_key = _current_tenant_key_for_admin()
    profiles = []
    try:
        profiles = sb_admin_list_access_profiles(tenant_key)
    except Exception as e:
        flash(f"Errore caricamento profili: {e}", "danger")
        profiles = []

    users = []
    try:
        users = sb_admin_list_profiles()
    except Exception:
        users = []
    if not _is_platform_master(current_user()):
        by_uid, by_email = _tenant_user_lookup_for_current_admin()
        users = [
            u for u in users
            if by_uid.get(str(u.get("id") or "").strip())
            or by_email.get(str(u.get("email") or "").strip().lower())
        ]
    cnt = {}
    for u in users:
        pid = u.get("access_profile_id")
        if pid:
            cnt[str(pid)] = cnt.get(str(pid), 0) + 1

    return render_template("admin_access_profiles.html", profiles=profiles, user_counts=cnt, module_catalog=ACCESS_MODULES, selected_tenant_key=tenant_key, tenant_profile_scope_available=_access_profiles_support_tenant_key())

@app.route('/admin/access-profiles/new', methods=['GET','POST'])
@login_required
@admin_or_master_required
def admin_access_profiles_new():
    tenant_key = _current_tenant_key_for_admin()
    tenant_scope_available = _access_profiles_support_tenant_key()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        modules = _modules_from_form()
        try:
            if not tenant_scope_available and tenant_key != "default":
                raise RuntimeError("La tabella access_profiles non ha ancora la colonna tenant_key: aggiungila su Supabase prima di creare profili tenant-specific.")
            pid = sb_admin_upsert_access_profile(None, name=name, description=description, modules=modules, tenant_key=tenant_key)
            flash("Profilo creato.", "success")
            return redirect(url_for("admin_access_profiles_edit", profile_id=pid))
        except Exception as e:
            flash(f"Errore creazione profilo: {e}", "danger")

    profile = {"id": None, "name": "", "description": "", "modules": {}}
    return render_template("admin_access_profile_form.html", mode="new", profile=profile, module_catalog=ACCESS_MODULES, selected_tenant_key=tenant_key, tenant_profile_scope_available=tenant_scope_available)

@app.route('/admin/access-profiles/<profile_id>', methods=['GET','POST'])
@login_required
@admin_or_master_required
def admin_access_profiles_edit(profile_id):
    tenant_key = _current_tenant_key_for_admin()
    tenant_scope_available = _access_profiles_support_tenant_key()
    profile = None
    try:
        profile = sb_admin_get_access_profile(profile_id, tenant_key=tenant_key)
    except Exception as e:
        flash(f"Errore lettura profilo: {e}", "danger")

    if not profile:
        flash("Profilo non trovato.", "warning")
        return redirect(url_for("admin_access_profiles"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        modules = _modules_from_form()
        try:
            sb_admin_upsert_access_profile(profile_id, name=name, description=description, modules=modules, tenant_key=tenant_key)
            # invalida cache
            _ACCESS_PROFILE_CACHE.pop(str(profile_id), None)
            flash("Profilo aggiornato.", "success")
            return redirect(url_for("admin_access_profiles_edit", profile_id=profile_id))
        except Exception as e:
            flash(f"Errore aggiornamento profilo: {e}", "danger")

    assigned_users = []
    try:
        assigned_users = [
            u for u in sb_admin_list_profiles()
            if str(u.get("access_profile_id") or "") == str(profile_id)
        ]
    except Exception:
        assigned_users = []
    if not _is_platform_master(current_user()):
        by_uid, by_email = _tenant_user_lookup_for_current_admin()
        assigned_users = [
            u for u in assigned_users
            if by_uid.get(str(u.get("id") or "").strip())
            or by_email.get(str(u.get("email") or "").strip().lower())
        ]

    return render_template("admin_access_profile_form.html", mode="edit", profile=profile, module_catalog=ACCESS_MODULES, assigned_users=assigned_users, selected_tenant_key=tenant_key, tenant_profile_scope_available=tenant_scope_available)

@app.post('/admin/access-profiles/<profile_id>/delete')
@login_required
@admin_or_master_required
def admin_access_profiles_delete(profile_id):
    try:
        tenant_key = _current_tenant_key_for_admin()
        profile = sb_admin_get_access_profile(profile_id, tenant_key=tenant_key)
        if not profile:
            flash("Profilo non trovato per questo tenant.", "warning")
            return redirect(url_for("admin_access_profiles"))
        sb_admin_delete_access_profile(profile_id)
        _ACCESS_PROFILE_CACHE.pop(str(profile_id), None)
        flash("Profilo eliminato.", "success")
    except Exception as e:
        flash(f"Errore eliminazione profilo: {e}", "danger")
    return redirect(url_for("admin_access_profiles"))

@app.route('/admin/access-profiles/<profile_id>/assign', methods=['GET','POST'])
@login_required
@admin_or_master_required
def admin_access_profiles_assign(profile_id):
    tenant_key = _current_tenant_key_for_admin()
    prof = None
    try:
        prof = sb_admin_get_access_profile(profile_id, tenant_key=tenant_key)
    except Exception as e:
        flash(f"Errore lettura profilo: {e}", "danger")
    if not prof:
        flash("Profilo non trovato.", "warning")
        return redirect(url_for("admin_access_profiles"))

    # lista utenti
    users = sb_admin_list_profiles()
    if not _is_platform_master(current_user()):
        by_uid, by_email = _tenant_user_lookup_for_current_admin()
        users = [
            u for u in users
            if by_uid.get(str(u.get("id") or "").strip())
            or by_email.get(str(u.get("email") or "").strip().lower())
        ]

    if request.method == "POST":
        user_ids = request.form.getlist("user_ids")
        user_ids = [u for u in user_ids if u]
        if not user_ids:
            flash("Seleziona almeno un utente.", "warning")
            return redirect(url_for("admin_access_profiles_assign", profile_id=profile_id))

        ok = 0
        err = 0
        for uid in user_ids:
            if not _is_platform_master(current_user()):
                _require_user_in_current_tenant(uid)
            try:
                sb_admin_update_profile(uid, access_profile_id=profile_id)
                if str(uid) == str(session.get('uid')):
                    session['access_profile_id'] = profile_id
                    session['access_modules'] = _normalize_user_modules({
                        'role': session.get('role'),
                        'is_master': session.get('is_master'),
                        'access_profile_id': profile_id,
                        'access_modules': session.get('access_modules'),
                    })
                ok += 1
            except Exception:
                err += 1
        if ok and not err:
            flash(f"Profilo assegnato a {ok} utenti.", "success")
        elif ok and err:
            flash(f"Profilo assegnato a {ok} utenti, {err} errori.", "warning")
        else:
            flash("Nessuna assegnazione effettuata.", "danger")
        return redirect(url_for("admin_access_profiles_edit", profile_id=profile_id))

    return render_template("admin_access_profile_assign.html", profile=prof, users=users, selected_tenant_key=tenant_key)

# --------------------- ROUTES: ADMIN - LISTINI ---------------------

# Nota: l'export dei listini usa un worker in subprocess per evitare crash del driver Access/ODBC
# dentro al processo Flask (alcuni ambienti Windows possono essere instabili con export massivi).

import sys
import subprocess
import tempfile
from pathlib import Path

_LISTINI_JOB_DIR = Path(os.getenv("LISTINI_JOB_DIR") or os.path.join(tempfile.gettempdir(), "fp_listini_jobs"))
try:
    _LISTINI_JOB_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def _listini_job_paths(job_id: str) -> tuple[Path, Path]:
    return (
        _LISTINI_JOB_DIR / f"{job_id}_input.json",
        _LISTINI_JOB_DIR / f"{job_id}_progress.json",
    )


def _json_write_atomic(path: Path, obj: dict) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        # best-effort fallback
        try:
            path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def _json_read_safe(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        txt = path.read_text(encoding="utf-8", errors="ignore")
        return json.loads(txt)
    except Exception:
        return None


def _tail_text_file(path: Path, max_lines: int = 120) -> list[str]:
    try:
        if not path or not path.exists():
            return []
        txt = path.read_text(encoding="utf-8", errors="ignore")
        lines = txt.splitlines()
        return lines[-max_lines:]
    except Exception:
        return []


def _listini_jobs_cleanup_files(max_age_hours: int = 48) -> None:
    try:
        now = time.time()
        max_age = max_age_hours * 3600
        for p in _LISTINI_JOB_DIR.glob("*_progress.json"):
            try:
                if (now - p.stat().st_mtime) > max_age:
                    inp = _LISTINI_JOB_DIR / p.name.replace("_progress.json", "_input.json")
                    try:
                        inp.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        try:
                            if inp.exists():
                                inp.unlink()
                        except Exception:
                            pass
                    try:
                        p.unlink()
                    except Exception:
                        pass
            except Exception:
                continue
    except Exception:
        pass


def _run_listini_apply_job(app_obj, job_id: str, prog_path: Path, payload: dict) -> None:
    with app_obj.app_context():
        listino_type = str(payload.get("type") or "FoodPaper").strip() or "FoodPaper"
        price_list_uuid = str(payload.get("price_list_id") or payload.get("listino_uuid") or "").strip()
        tenant_database = str(payload.get("tenant_database") or "").strip()
        rows = payload.get("rows") or []
        scope = str(payload.get("scope") or payload.get("mode") or "master").strip().lower()
        progress = {
            "id": job_id,
            "type": listino_type,
            "price_list_id": price_list_uuid,
            "tenant_database": tenant_database,
            "source_store": "9001",
            "scope": scope,
            "running": True,
            "finished": False,
            "done": 0,
            "total": 1,
            "stores_ok": 0,
            "stores_fail": 0,
            "current_store": "9001",
            "logs": ["Salvataggio SQL avviato."],
            "failures": [],
            "error": None,
            "pid": None,
            "ts": time.time(),
        }
        _json_write_atomic(prog_path, progress)
        try:
            if not price_list_uuid:
                progress.update(
                    {
                        "running": False,
                        "finished": True,
                        "done": 0,
                        "stores_ok": 0,
                        "stores_fail": 1,
                        "logs": (progress.get("logs") or []) + ["Seleziona un elenco prezzi prima di salvare."],
                        "failures": [{"store": "9001", "error": "Elenco prezzi mancante."}],
                        "error": "Seleziona un elenco prezzi prima di salvare.",
                        "ts": time.time(),
                    }
                )
                _json_write_atomic(prog_path, progress)
                return

            db_context = storehub_database_context(tenant_database) if tenant_database else nullcontext()
            with db_context:
                ensure_supplier_orders_schema()
                try:
                    migrate_legacy_pricelists()
                except Exception as exc:
                    current_app.logger.exception("Listini multipli non inizializzati durante apply/start")
                    progress["logs"].append(f"Inizializzazione listini non completata: {exc}")
                    _json_write_atomic(prog_path, progress)

                res = upsert_pricelist_rows(listino_type, rows, price_list_uuid or None)
            ok = bool(res.get("ok"))
            progress.update(
                {
                    "running": False,
                    "finished": True,
                    "done": 1 if ok else 0,
                    "stores_ok": 1 if ok else 0,
                    "stores_fail": 0 if ok else 1,
                    "logs": (progress.get("logs") or []) + (["Listino pubblicato sulla tabella condivisa."] if ok else []),
                    "failures": [] if ok else [{"store": "9001", "error": res.get("error") or "Errore salvataggio"}],
                    "error": None if ok else (res.get("error") or "Errore salvataggio"),
                    "ts": time.time(),
                }
            )
        except Exception as exc:
            current_app.logger.exception("Errore worker salvataggio listino")
            progress.update(
                {
                    "running": False,
                    "finished": True,
                    "done": 0,
                    "stores_ok": 0,
                    "stores_fail": 1,
                    "failures": [{"store": "9001", "error": str(exc)}],
                    "error": str(exc),
                    "ts": time.time(),
                }
            )
        _json_write_atomic(prog_path, progress)


@app.get('/admin/listini')
@login_required
@admin_required
def admin_listini():
    return redirect(url_for('warehouse.listini_prezzi'))



@app.get('/admin/links')
@login_required
@admin_required
def admin_links():
    from links_repository import list_links, LINKS_BUCKET
    tenant_key = _current_tenant_key_for_admin()
    try:
        links = list_links(active_only=False, tenant_key=tenant_key)
    except Exception as e:
        flash(f"Errore caricamento link: {e}", 'danger')
        links = []
    return render_template('admin_links.html', links=links, bucket_name=LINKS_BUCKET, selected_tenant_key=tenant_key)


# ---- Categorie Link (Admin) ----
@app.get('/admin/links/categories')
@login_required
@admin_required
def admin_link_categories():
    from links_repository import list_categories
    tenant_key = _current_tenant_key_for_admin()
    try:
        categories = list_categories(include_inactive=True, tenant_key=tenant_key)
    except Exception as e:
        flash(f"Errore caricamento categorie: {e}", 'danger')
        categories = []
    return render_template('admin_link_categories.html', categories=categories, selected_tenant_key=tenant_key)


@app.route('/admin/links/categories/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_link_categories_new():
    from links_repository import create_category
    tenant_key = _current_tenant_key_for_admin()
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        sort_order = int(request.form.get('sort_order') or 0)
        is_active = (request.form.get('is_active') or '1') == '1'

        if not name:
            flash("Il nome categoria è obbligatorio.", "warning")
            return render_template('admin_link_category_form.html', mode='new', category=None, selected_tenant_key=tenant_key)

        try:
            create_category(name=name, sort_order=sort_order, is_active=is_active, tenant_key=tenant_key)
            flash("Categoria salvata.", "success")
            return redirect(url_for('admin_link_categories'))
        except Exception as e:
            flash(f"Errore salvataggio categoria: {e}", "danger")

    return render_template('admin_link_category_form.html', mode='new', category=None, selected_tenant_key=tenant_key)


@app.route('/admin/links/categories/<category_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_link_categories_edit(category_id):
    from links_repository import get_category, update_category
    tenant_key = _current_tenant_key_for_admin()
    category = None
    try:
        category = get_category(category_id, tenant_key=tenant_key)
    except Exception as e:
        flash(f"Errore lettura categoria: {e}", "danger")

    if not category:
        flash("Categoria non trovata.", "warning")
        return redirect(url_for('admin_link_categories'))

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        sort_order = int(request.form.get('sort_order') or 0)
        is_active = (request.form.get('is_active') or '1') == '1'

        if not name:
            flash("Il nome categoria è obbligatorio.", "warning")
            return render_template('admin_link_category_form.html', mode='edit', category=category, selected_tenant_key=tenant_key)

        try:
            update_category(category_id=category_id, name=name, sort_order=sort_order, is_active=is_active, tenant_key=tenant_key)
            flash("Categoria aggiornata.", "success")
            return redirect(url_for('admin_link_categories'))
        except Exception as e:
            flash(f"Errore aggiornamento categoria: {e}", "danger")

    return render_template('admin_link_category_form.html', mode='edit', category=category, selected_tenant_key=tenant_key)


@app.post('/admin/links/categories/<category_id>/delete')
@login_required
@admin_required
def admin_link_categories_delete(category_id):
    from links_repository import delete_category
    tenant_key = _current_tenant_key_for_admin()
    try:
        delete_category(category_id, tenant_key=tenant_key)
        flash("Categoria eliminata. I link associati restano senza categoria.", "success")
    except Exception as e:
        flash(f"Errore eliminazione categoria: {e}", "danger")
    return redirect(url_for('admin_link_categories'))


# ---- Link (Admin) ----
@app.route('/admin/links/new', methods=['GET','POST'])
@login_required
@admin_required
def admin_links_new():
    from links_repository import upsert_link, storage_upload_image, list_categories
    tenant_key = _current_tenant_key_for_admin()
    categories = []
    try:
        categories = list_categories(include_inactive=True, tenant_key=tenant_key)
    except Exception:
        categories = []

    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        urlv = (request.form.get('url') or '').strip()
        sort_order = int(request.form.get('sort_order') or 0)
        is_active = 'is_active' in request.form
        category_id = (request.form.get('category_id') or '').strip() or None

        image_path = None
        f = request.files.get('image')
        if f and f.filename:
            try:
                file_bytes = f.read()
                image_path, _ = storage_upload_image(
                    file_bytes=file_bytes,
                    filename=f.filename,
                    content_type=f.mimetype or 'application/octet-stream',
                    link_id=None,
                )
            except Exception as e:
                flash(f"Errore upload immagine: {e}", 'danger')
                return render_template('admin_link_form.html', link=None, categories=categories, selected_tenant_key=tenant_key)

        try:
            upsert_link(
                link_id=None,
                title=title,
                url=urlv,
                sort_order=sort_order,
                is_active=is_active,
                image_path=image_path,
                category_id=category_id,
                tenant_key=tenant_key,
            )
            flash('Link salvato.', 'success')
            return redirect(url_for('admin_links'))
        except Exception as e:
            flash(f"Errore salvataggio link: {e}", 'danger')

    return render_template('admin_link_form.html', link=None, categories=categories, selected_tenant_key=tenant_key)


@app.route('/admin/links/<link_id>', methods=['GET','POST'])
@login_required
@admin_required
def admin_links_edit(link_id):
    from links_repository import get_link, upsert_link, storage_upload_image, storage_delete_image, list_categories
    tenant_key = _current_tenant_key_for_admin()
    categories = []
    try:
        categories = list_categories(include_inactive=True, tenant_key=tenant_key)
    except Exception:
        categories = []

    link = None
    try:
        link = get_link(link_id, tenant_key=tenant_key)
    except Exception as e:
        flash(f"Errore lettura link: {e}", 'danger')

    if not link:
        flash('Link non trovato.', 'warning')
        return redirect(url_for('admin_links'))

    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        urlv = (request.form.get('url') or '').strip()
        sort_order = int(request.form.get('sort_order') or 0)
        is_active = 'is_active' in request.form
        category_id = (request.form.get('category_id') or '').strip() or None

        image_path = link.get('image_path')
        f = request.files.get('image')
        if f and f.filename:
            try:
                file_bytes = f.read()
                new_path, _ = storage_upload_image(
                    file_bytes=file_bytes,
                    filename=f.filename,
                    content_type=f.mimetype or 'application/octet-stream',
                    link_id=link_id,
                )
                if image_path and image_path != new_path:
                    try:
                        storage_delete_image(image_path)
                    except Exception:
                        pass
                image_path = new_path
            except Exception as e:
                flash(f"Errore upload immagine: {e}", 'danger')
                return render_template('admin_link_form.html', link=link, categories=categories, selected_tenant_key=tenant_key)

        try:
            link = upsert_link(
                link_id=link_id,
                title=title,
                url=urlv,
                sort_order=sort_order,
                is_active=is_active,
                image_path=image_path,
                category_id=category_id,
                tenant_key=tenant_key,
            )
            flash('Link aggiornato.', 'success')
            return redirect(url_for('admin_links'))
        except Exception as e:
            flash(f"Errore aggiornamento link: {e}", 'danger')

    return render_template('admin_link_form.html', link=link, categories=categories, selected_tenant_key=tenant_key)


@app.post('/admin/links/<link_id>/delete')
@login_required
@admin_required
def admin_links_delete(link_id):
    from links_repository import get_link, delete_link, storage_delete_image
    tenant_key = _current_tenant_key_for_admin()
    try:
        link = get_link(link_id, tenant_key=tenant_key)
        delete_link(link_id, tenant_key=tenant_key)
        try:
            storage_delete_image((link or {}).get('image_path'))
        except Exception:
            pass
        flash('Link eliminato.', 'success')
    except Exception as e:
        flash(f"Errore eliminazione link: {e}", 'danger')
    return redirect(url_for('admin_links'))


@app.get('/admin/api/listini/load')
@login_required
@admin_required
def admin_api_listini_load():
    listino_type = (request.args.get('type') or 'FoodPaper').strip()
    price_list_uuid = (request.args.get('price_list_id') or request.args.get('listino_uuid') or '').strip()
    try:
        ensure_supplier_orders_schema()
        try:
            migrate_legacy_pricelists()
            ensure_default_price_list_assignments()
        except Exception:
            current_app.logger.exception("Listini multipli non inizializzati, uso fallback Listino DOS")
        data = load_unified_pricelist(listino_type, price_list_uuid or None)
        if not data.get('ok'):
            return jsonify({'ok': False, 'error': data.get('error') or 'Errore caricamento'}), 400

        return jsonify({
            'ok': True,
            'source_store': data.get('source_store'),
            'price_list_uuid': data.get('price_list_uuid'),
            'price_list_name': data.get('price_list_name'),
            'listino_type': data.get('listino_type'),
            'table': data.get('table'),
            'columns': data.get('columns') or [],
            'rows': data.get('rows') or [],
            'key_column': data.get('key_column'),
            'desc_column': data.get('desc_column'),
            'supplier_column': data.get('supplier_column'),
            'conv_table': data.get('conv_table'),
            'suppliers': data.get('suppliers') or [],
            'groups': data.get('groups') or [],
            'types': data.get('types') or [],
            'price_lists': data.get('price_lists') or [],
        })
    except Exception as e:
        current_app.logger.exception("Errore admin_api_listini_load")
        return jsonify({'ok': False, 'error': str(e)}), 500




@app.get('/admin/costo-lavoro-test')
@login_required
@master_required
def admin_costo_lavoro_test_page():
    return render_template('admin_costo_lavoro_test.html')


@app.route('/admin/versamenti-finance-match-test', methods=['GET'])
@login_required
def admin_versamenti_finance_match_test():
    _enforce_module("estrazioni_hq")
    today = datetime.now().date()
    start_default = today.replace(day=1).isoformat()
    end_default = today.isoformat()

    if 'store_code' in request.args:
        store_code = (request.args.get('store_code') or '').strip()
    else:
        store_code = (session.get('store_code') or '').strip()
    start_iso = (request.args.get('start') or '').strip() or start_default
    end_iso = (request.args.get('end') or '').strip() or end_default
    page_view = (request.args.get('view') or 'queue').strip().lower()
    if page_view not in {'queue', 'assigned', 'unmatched_app', 'unassigned_finance'}:
        page_view = 'queue'

    try:
        page_ctx = _prepare_admin_finance_match_page(
            store_code=store_code,
            start_iso=start_iso,
            end_iso=end_iso,
            page_view=page_view,
        )
    except Exception as e:
        current_app.logger.exception('Errore pagina test match versamenti finance')
        flash(f'Errore caricamento pagina test: {e}', 'danger')
        page_ctx = {
            'stores': get_warehouse_stores() or [],
            'selected_store_code': store_code,
            'start_iso': start_iso,
            'end_iso': end_iso,
            'page_view': page_view,
            'rows': [],
            'finance_rows': [],
            'finance_total': 0,
            'app_total': 0,
            'page_counts': {"queue": 0, "assigned": 0, "unmatched_app": 0, "unassigned_finance": 0},
        }

    return render_template('admin_versamenti_finance_match.html', **page_ctx)


@app.post('/admin/versamenti-finance-match-test/save')
@login_required
def admin_versamenti_finance_match_test_save():
    _enforce_module("estrazioni_hq")
    app_record_key = (request.form.get('app_record_key') or '').strip()
    app_record_id = (request.form.get('app_record_id') or '').strip()
    app_store = (request.form.get('app_store') or '').strip()
    return_start = (request.form.get('return_start') or '').strip()
    return_end = (request.form.get('return_end') or '').strip()

    items = []
    for slot_no in (1, 2, 3):
        fin_id = (request.form.get(f'finance_slot_{slot_no}') or '').strip()
        score = (request.form.get(f'finance_score_{slot_no}') or '').strip()
        if not fin_id:
            continue
        items.append(
            {
                'finance_id': fin_id,
                'slot_no': slot_no,
                'match_score': score or '0',
                'match_source': 'manual',
            }
        )

    try:
        if items:
            app_row = _load_app_match_row(
                app_record_key=app_record_key,
                app_store=app_store,
                start_iso=return_start,
                end_iso=return_end,
            )
            if not app_row:
                raise ValueError("Non riesco a rileggere il versamento app dal filtro corrente. Ricarica la pagina e riprova.")

            finance_ids = []
            for item in items:
                try:
                    finance_ids.append(int(item.get('finance_id') or 0))
                except Exception:
                    continue
            finance_rows = _selected_finance_rows_for_match(finance_ids, start_iso=return_start, end_iso=return_end)
            if len(finance_rows) != len(finance_ids):
                raise ValueError("Uno o più versamenti finance selezionati non sono più disponibili. Ricarica la pagina.")

            validation = _validate_finance_match_selection(
                app_row=app_row,
                selected_finance_rows=finance_rows,
                app_record_key=app_record_key,
            )
            if not bool(validation.get("ok")):
                raise ValueError(str(validation.get("error") or "Match non valido."))
            validation_warnings = [str(w).strip() for w in (validation.get("warnings") or []) if str(w).strip()]
        else:
            validation_warnings = []

        replace_app_matches(
            app_record_key=app_record_key,
            app_record_id=app_record_id,
            app_store=app_store,
            items=items,
        )
        if items:
            flash('Associazione versamenti salvata.', 'success')
            for warning in validation_warnings:
                flash(warning, 'warning')
        else:
            flash('Associazione rimossa, i versamenti finance sono di nuovo liberi.', 'success')
    except Exception as e:
        current_app.logger.exception('Errore salvataggio match versamenti finance')
        flash(f'Errore salvataggio associazione: {e}', 'danger')

    return redirect(
        url_for(
            'admin_versamenti_finance_match_test',
            store_code=(request.form.get('return_store_code') or '').strip(),
            start=(request.form.get('return_start') or '').strip(),
            end=(request.form.get('return_end') or '').strip(),
            view=(request.form.get('return_view') or 'queue').strip(),
        )
    )


@app.post('/admin/versamenti-finance-match-test/batch-save')
@login_required
def admin_versamenti_finance_match_test_batch_save():
    _enforce_module("estrazioni_hq")
    force_confirm = (request.form.get("force_confirm") or "").strip() == "1"
    payload = _parse_batch_payload()
    return_store_code = (request.form.get('return_store_code') or '').strip()
    return_start = (request.form.get('return_start') or '').strip()
    return_end = (request.form.get('return_end') or '').strip()
    return_view = (request.form.get('return_view') or 'queue').strip()

    pending_save: list[dict] = []
    warnings_list: list[dict] = []
    hard_errors: list[str] = []
    tessera_warning_count = 0
    for item in payload:
        app_record_key = str(item.get("app_record_key") or "").strip()
        app_store = str(item.get("app_store") or "").strip()
        app_record_id = str(item.get("app_record_id") or "").strip()
        items = list(item.get("items") or [])
        app_row = _load_app_match_row(app_record_key=app_record_key, app_store=app_store, start_iso=return_start, end_iso=return_end)
        if not app_row:
            hard_errors.append(f"{app_store}: record app non trovato nel filtro corrente.")
            continue
        finance_ids = []
        for entry in items:
            try:
                finance_ids.append(int(entry.get("finance_id") or 0))
            except Exception:
                continue
        finance_rows = _selected_finance_rows_for_match(finance_ids, start_iso=return_start, end_iso=return_end)
        if len(finance_rows) != len(finance_ids):
            hard_errors.append(f"{app_store} {app_row.get('nome')}: uno o più versamenti finance non sono più disponibili.")
            continue
        validation = _validate_finance_match_selection(app_row=app_row, selected_finance_rows=finance_rows, app_record_key=app_record_key)
        if not bool(validation.get("ok")):
            msg = str(validation.get("error") or "Match non valido.")
            if _classify_match_error(msg) == "overrideable":
                warnings_list.append({"title": f"{app_row.get('store_name') or app_store} - {app_row.get('nome') or ''}", "message": msg})
            else:
                hard_errors.append(msg)
                continue
        tessera_warning_count += len([w for w in (validation.get("warnings") or []) if str(w).strip()])
        pending_save.append({"app_record_key": app_record_key, "app_record_id": app_record_id, "app_store": app_store, "items": items})

    if hard_errors:
        flash("Errori bloccanti: " + " | ".join(hard_errors[:8]), "danger")
        return redirect(url_for('admin_versamenti_finance_match_test', store_code=return_store_code, start=return_start, end=return_end, view=return_view))

    if warnings_list and not force_confirm:
        return render_template(
            'confirm_bulk_match.html',
            title="Conferma convalida massiva versamenti",
            subtitle="Abbiamo trovato anomalie nei match selezionati. Possiamo procedere solo con tua conferma.",
            warnings=warnings_list,
            batch_payload=json.dumps(payload, ensure_ascii=False),
            post_url=url_for('admin_versamenti_finance_match_test_batch_save'),
            return_store_code=return_store_code,
            return_start=return_start,
            return_end=return_end,
            return_view=return_view,
        )

    saved = 0
    for item in pending_save:
        replace_app_matches(
            app_record_key=item["app_record_key"],
            app_record_id=item["app_record_id"],
            app_store=item["app_store"],
            items=item["items"],
        )
        saved += 1
    flash(f'Convalida massiva completata: {saved} record salvati.', 'success')
    if tessera_warning_count:
        flash('Alcuni versamenti sono stati convalidati con numero tessera non coerente tra Store Hub e finance.', 'warning')
    return redirect(url_for('admin_versamenti_finance_match_test', store_code=return_store_code, start=return_start, end=return_end, view=return_view))


@app.route('/admin/pos-finance-match-test', methods=['GET'])
@login_required
def admin_pos_finance_match_test():
    _enforce_module("estrazioni_hq")
    today = datetime.now().date()
    start_default = today.replace(day=1).isoformat()
    end_default = today.isoformat()

    if 'store_code' in request.args:
        store_code = (request.args.get('store_code') or '').strip()
    else:
        store_code = (session.get('store_code') or '').strip()
    start_iso = (request.args.get('start') or '').strip() or start_default
    end_iso = (request.args.get('end') or '').strip() or end_default
    page_view = (request.args.get('view') or 'queue').strip().lower()
    if page_view not in {'queue', 'assigned', 'unmatched_app', 'unassigned_finance'}:
        page_view = 'queue'

    try:
        page_ctx = _prepare_admin_pos_finance_match_page(
            store_code=store_code,
            start_iso=start_iso,
            end_iso=end_iso,
            page_view=page_view,
        )
    except Exception as e:
        current_app.logger.exception('Errore pagina test match POS finance')
        flash(f'Errore caricamento convalida POS: {e}', 'danger')
        page_ctx = {
            'stores': get_warehouse_stores() or [],
            'selected_store_code': store_code,
            'start_iso': start_iso,
            'end_iso': end_iso,
            'page_view': page_view,
            'rows': [],
            'finance_rows': [],
            'page_counts': {"queue": 0, "assigned": 0, "unmatched_app": 0, "unassigned_finance": 0},
        }

    return render_template('admin_pos_finance_match.html', **page_ctx)


@app.post('/admin/pos-finance-match-test/save')
@login_required
def admin_pos_finance_match_test_save():
    _enforce_module("estrazioni_hq")
    app_record_key = (request.form.get('app_record_key') or '').strip()
    app_store = (request.form.get('app_store') or '').strip()
    app_date = (request.form.get('app_date') or '').strip()
    return_start = (request.form.get('return_start') or '').strip()
    return_end = (request.form.get('return_end') or '').strip()

    items = []
    for slot_no in (1, 2, 3):
        fin_uid = (request.form.get(f'finance_slot_{slot_no}') or '').strip().lower()
        score = (request.form.get(f'finance_score_{slot_no}') or '').strip()
        if not fin_uid:
            continue
        source_table, _, row_id = fin_uid.partition(':')
        try:
            row_id_int = int(row_id or 0)
        except Exception:
            row_id_int = 0
        if not source_table or not row_id_int:
            continue
        items.append(
            {
                'finance_row_uid': fin_uid,
                'finance_source_table': source_table,
                'finance_row_id': row_id_int,
                'slot_no': slot_no,
                'match_score': score or '0',
                'match_source': 'manual',
            }
        )

    try:
        if items:
            stores = get_warehouse_stores() or []
            code_to_name = {str(s.get("code") or "").strip(): str(s.get("name") or "").strip() for s in stores}
            app_rows = _load_app_pos_rows(
                selected_store_codes=[app_store] if app_store else [],
                start_iso=return_start,
                end_iso=return_end,
                code_to_name=code_to_name,
            )
            app_row = next((r for r in app_rows if str(r.get("app_record_key") or "") == app_record_key), None)
            if not app_row:
                raise ValueError("Non riesco a rileggere il record POS dal filtro corrente. Ricarica la pagina e riprova.")

            finance_uids = [str(item.get('finance_row_uid') or '').strip().lower() for item in items if str(item.get('finance_row_uid') or '').strip()]
            finance_rows = _selected_pos_finance_rows_for_match(finance_uids, start_iso=return_start, end_iso=return_end)
            if len(finance_rows) != len(finance_uids):
                raise ValueError("Uno o più movimenti finance selezionati non sono più disponibili. Ricarica la pagina.")

            finance_by_uid = {str(r.get("finance_row_uid") or "").strip().lower(): r for r in finance_rows}
            for r in finance_rows:
                r["emitter"] = _infer_pos_emitter(r)
                inferred = _infer_finance_store_pos(r, stores)
                r["inferred_store_code"] = str(inferred.get("code") or "")
                r["inferred_store_name"] = str(inferred.get("name") or "")
                r["inferred_store_score"] = float(inferred.get("score") or 0.0)
            finance_rows = [finance_by_uid[uid] for uid in finance_uids if uid in finance_by_uid]

            validation = _validate_pos_finance_match_selection(
                app_row=app_row,
                selected_finance_rows=finance_rows,
                app_record_key=app_record_key,
            )
            if not bool(validation.get("ok")):
                raise ValueError(str(validation.get("error") or "Match non valido."))

        replace_pos_app_matches(
            app_record_key=app_record_key,
            app_store=app_store,
            app_date=app_date,
            items=items,
        )
        if items:
            flash('Associazione POS salvata.', 'success')
        else:
            flash('Associazione POS rimossa, i movimenti finance sono di nuovo liberi.', 'success')
    except Exception as e:
        current_app.logger.exception('Errore salvataggio match POS finance')
        flash(f'Errore salvataggio associazione POS: {e}', 'danger')

    return redirect(
        url_for(
            'admin_pos_finance_match_test',
            store_code=(request.form.get('return_store_code') or '').strip(),
            start=(request.form.get('return_start') or '').strip(),
            end=(request.form.get('return_end') or '').strip(),
            view=(request.form.get('return_view') or 'queue').strip(),
        )
    )


@app.post('/admin/pos-finance-match-test/batch-save')
@login_required
def admin_pos_finance_match_test_batch_save():
    _enforce_module("estrazioni_hq")
    force_confirm = (request.form.get("force_confirm") or "").strip() == "1"
    payload = _parse_batch_payload()
    return_store_code = (request.form.get('return_store_code') or '').strip()
    return_start = (request.form.get('return_start') or '').strip()
    return_end = (request.form.get('return_end') or '').strip()
    return_view = (request.form.get('return_view') or 'queue').strip()

    stores = get_warehouse_stores() or []
    code_to_name = {str(s.get("code") or "").strip(): str(s.get("name") or "").strip() for s in stores}
    app_rows = _load_app_pos_rows(
        selected_store_codes=[return_store_code] if return_store_code else [str(s.get("code") or "").strip() for s in stores if str(s.get("code") or "").strip()],
        start_iso=return_start,
        end_iso=return_end,
        code_to_name=code_to_name,
    )
    app_by_key = {str(r.get("app_record_key") or ""): r for r in app_rows}

    pending_save: list[dict] = []
    warnings_list: list[dict] = []
    hard_errors: list[str] = []
    for item in payload:
        app_record_key = str(item.get("app_record_key") or "").strip()
        app_store = str(item.get("app_store") or "").strip()
        app_date = str(item.get("app_date") or "").strip()
        items = list(item.get("items") or [])
        app_row = app_by_key.get(app_record_key)
        if not app_row:
            hard_errors.append(f"{app_store} {app_date}: record POS non trovato nel filtro corrente.")
            continue
        finance_uids = [str(entry.get("finance_row_uid") or "").strip().lower() for entry in items if str(entry.get("finance_row_uid") or "").strip()]
        finance_rows = _selected_pos_finance_rows_for_match(finance_uids, start_iso=return_start, end_iso=return_end)
        if len(finance_rows) != len(finance_uids):
            hard_errors.append(f"{app_store} {app_date}: uno o più movimenti finance non sono più disponibili.")
            continue
        finance_by_uid = {str(r.get("finance_row_uid") or "").strip().lower(): r for r in finance_rows}
        for r in finance_rows:
            r["emitter"] = _infer_pos_emitter(r)
            inferred = _infer_finance_store_pos(r, stores)
            r["inferred_store_code"] = str(inferred.get("code") or "")
            r["inferred_store_name"] = str(inferred.get("name") or "")
            r["inferred_store_score"] = float(inferred.get("score") or 0.0)
        finance_rows = [finance_by_uid[uid] for uid in finance_uids if uid in finance_by_uid]
        validation = _validate_pos_finance_match_selection(app_row=app_row, selected_finance_rows=finance_rows, app_record_key=app_record_key)
        if not bool(validation.get("ok")):
            msg = str(validation.get("error") or "Match non valido.")
            if _classify_match_error(msg) == "overrideable":
                warnings_list.append({"title": f"{app_row.get('store_name') or app_store} - {app_date}", "message": msg})
            else:
                hard_errors.append(msg)
                continue
        pending_save.append({"app_record_key": app_record_key, "app_store": app_store, "app_date": app_date, "items": items})

    if hard_errors:
        flash("Errori bloccanti: " + " | ".join(hard_errors[:8]), "danger")
        return redirect(url_for('admin_pos_finance_match_test', store_code=return_store_code, start=return_start, end=return_end, view=return_view))

    if warnings_list and not force_confirm:
        return render_template(
            'confirm_bulk_match.html',
            title="Conferma convalida massiva POS",
            subtitle="Abbiamo trovato anomalie nei match selezionati. Possiamo procedere solo con tua conferma.",
            warnings=warnings_list,
            batch_payload=json.dumps(payload, ensure_ascii=False),
            post_url=url_for('admin_pos_finance_match_test_batch_save'),
            return_store_code=return_store_code,
            return_start=return_start,
            return_end=return_end,
            return_view=return_view,
        )

    saved = 0
    for item in pending_save:
        replace_pos_app_matches(
            app_record_key=item["app_record_key"],
            app_store=item["app_store"],
            app_date=item["app_date"],
            items=item["items"],
        )
        saved += 1
    flash(f'Convalida massiva POS completata: {saved} record salvati.', 'success')
    return redirect(url_for('admin_pos_finance_match_test', store_code=return_store_code, start=return_start, end=return_end, view=return_view))


@app.get('/admin/api/labor-cost/setup')
@login_required
@master_required
def admin_api_labor_cost_setup():
    try:
        return jsonify(labor_cost_get_setup_overview())
    except Exception as e:
        current_app.logger.exception('Errore admin_api_labor_cost_setup')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/admin/api/labor-cost/company-profiles')
@login_required
@master_required
def admin_api_labor_cost_company_profiles():
    try:
        return jsonify({'ok': True, 'rows': labor_cost_list_company_profiles()})
    except Exception as e:
        current_app.logger.exception('Errore admin_api_labor_cost_company_profiles')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/admin/api/labor-cost/company-profiles/<int:profile_id>')
@login_required
@master_required
def admin_api_labor_cost_company_profile_detail(profile_id):
    try:
        prof = labor_cost_get_company_profile(profile_id)
        if not prof:
            return jsonify({'ok': False, 'error': 'Profilo non trovato'}), 404
        extras = labor_cost_list_company_extras(profile_id)
        role_rates = labor_cost_list_company_role_rates(profile_id)
        return jsonify({'ok': True, 'profile': prof, 'extras': extras, 'role_rates': role_rates})
    except Exception as e:
        current_app.logger.exception('Errore admin_api_labor_cost_company_profile_detail')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/admin/api/labor-cost/company-profiles/save')
@login_required
@master_required
def admin_api_labor_cost_company_profile_save():
    try:
        payload = request.get_json(force=True, silent=False) or {}
        res = labor_cost_save_company_profile(payload)
        return jsonify(res)
    except Exception as e:
        current_app.logger.exception('Errore admin_api_labor_cost_company_profile_save')
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.post('/admin/api/labor-cost/company-profiles/<int:profile_id>/extras/save')
@login_required
@master_required
def admin_api_labor_cost_company_extras_save(profile_id):
    try:
        payload = request.get_json(force=True, silent=True) or {}
        extras = payload.get('extras') or []
        res = labor_cost_replace_company_extras(profile_id, extras)
        return jsonify(res)
    except Exception as e:
        current_app.logger.exception('Errore admin_api_labor_cost_company_extras_save')
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.get('/admin/api/labor-cost/employees/list')
@login_required
@master_required
def admin_api_labor_cost_employees_list():
    try:
        store_code = (request.args.get('store_code') or '').strip() or None
        limit = request.args.get('limit', '300')
        rows = labor_cost_list_employee_configs(store_code=store_code, limit=int(limit))
        return jsonify({'ok': True, 'rows': rows})
    except Exception as e:
        current_app.logger.exception('Errore admin_api_labor_cost_employees_list')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/admin/api/labor-cost/employees/upsert')
@login_required
@master_required
def admin_api_labor_cost_employees_upsert():
    try:
        payload = request.get_json(force=True, silent=False) or {}
        res = labor_cost_upsert_employee_config(payload)
        return jsonify(res)
    except Exception as e:
        current_app.logger.exception('Errore admin_api_labor_cost_employees_upsert')
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.post('/admin/api/labor-cost/employees/import-csv')
@login_required
@master_required
def admin_api_labor_cost_employees_import_csv():
    try:
        f = request.files.get('file')
        if f is None:
            return jsonify({'ok': False, 'error': 'File CSV mancante'}), 400
        content = f.read() or b''
        default_store = (request.form.get('default_store_code') or '').strip() or None
        pid_raw = (request.form.get('default_company_profile_id') or '').strip()
        pid = int(pid_raw) if pid_raw else None
        res = labor_cost_import_employee_configs_csv(content, default_store_code=default_store, default_company_profile_id=pid)
        return jsonify(res)
    except Exception as e:
        current_app.logger.exception('Errore admin_api_labor_cost_employees_import_csv')
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.post('/admin/api/labor-cost/projection-test')
@login_required
@master_required
def admin_api_labor_cost_projection_test():
    try:
        payload = request.get_json(force=True, silent=False) or {}
        res = labor_cost_projection_test(
            store_code=str(payload.get('store_code') or '').strip(),
            week_start=str(payload.get('week_start') or '').strip(),
            company_profile_id=int(payload.get('company_profile_id')) if str(payload.get('company_profile_id') or '').strip() else None,
            revenues_actual=payload.get('revenues_actual'),
        )
        return jsonify(res)
    except Exception as e:
        current_app.logger.exception('Errore admin_api_labor_cost_projection_test')
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.post('/admin/api/listini/apply')
@login_required
@admin_required
def admin_api_listini_apply():
    """Export sincrono (fallback)."""
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        payload = {}

    listino_type = (payload.get('type') or 'FoodPaper').strip()
    price_list_uuid = str(payload.get('price_list_id') or payload.get('listino_uuid') or '').strip()
    columns = payload.get('columns') or []
    rows = payload.get('rows') or []
    source_store = str(payload.get('source_store') or '9001')

    if not price_list_uuid:
        return jsonify({'ok': False, 'error': 'Seleziona un elenco prezzi prima di salvare.'}), 400

    try:
        ensure_supplier_orders_schema()
        try:
            migrate_legacy_pricelists()
            ensure_default_price_list_assignments()
        except Exception:
            current_app.logger.exception("Listini multipli non inizializzati durante salvataggio")
        res = upsert_pricelist_rows(listino_type, rows, price_list_uuid or None)
        status = 200 if res.get('ok') else 400
        return jsonify(res), status
    except Exception as e:
        current_app.logger.exception("Errore admin_api_listini_apply")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/admin/api/listini/copy-products')
@login_required
@admin_required
def admin_api_listini_copy_products():
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        payload = {}

    source_price_list_id = str(payload.get('source_price_list_id') or payload.get('source_listino_uuid') or '').strip()
    target_price_list_id = str(payload.get('target_price_list_id') or payload.get('target_listino_uuid') or '').strip()
    tipo_listino = str(payload.get('type') or payload.get('tipo_listino') or '').strip()
    overwrite = bool(payload.get('overwrite'))
    products = payload.get('products') or []
    if not isinstance(products, list):
        products = []

    if not source_price_list_id or not target_price_list_id:
        return jsonify({
            'ok': False,
            'error': 'Seleziona un elenco prezzi origine e uno destinazione validi.',
        }), 400

    try:
        ensure_supplier_orders_schema()
        try:
            migrate_legacy_pricelists()
            ensure_default_price_list_assignments()
        except Exception:
            current_app.logger.exception("Listini multipli non inizializzati durante copia prodotti")
        res = copy_price_list_products(
            source_price_list_id,
            target_price_list_id,
            tipo_listino or None,
            overwrite=overwrite,
            products=products,
        )
        status = 200 if res.get('ok') else 400
        return jsonify(res), status
    except Exception as e:
        current_app.logger.exception("Errore admin_api_listini_copy_products")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/admin/api/listini/export')
@login_required
@admin_required
def admin_api_listini_export():
    price_list_uuid = str(request.args.get('price_list_id') or request.args.get('listino_uuid') or '').strip()
    listino_type = str(request.args.get('type') or request.args.get('tipo_listino') or '').strip()
    if not listino_type:
        return jsonify({'ok': False, 'error': 'Tipo listino obbligatorio.'}), 400
    if not price_list_uuid:
        return jsonify({'ok': False, 'error': 'Seleziona un elenco prezzi prima di esportare.'}), 400

    try:
        ensure_supplier_orders_schema()
        try:
            migrate_legacy_pricelists()
            ensure_default_price_list_assignments()
        except Exception:
            current_app.logger.exception("Listini multipli non inizializzati durante export")

        res = load_unified_pricelist(listino_type, price_list_uuid or None)
        if not res.get('ok'):
            return jsonify(res), 400

        columns = list(res.get('columns') or ["FORNITORE", "Descrizione", "GRUPPO", "CODICE", "PREZZO", "UNITA", "QTACAR", "QTAINT", "CONV"])
        rows = list(res.get('rows') or [])

        def _format_csv_value(column, value):
            if value is None:
                return ''
            col = str(column or '').strip().upper()
            if col in {'PREZZO', 'CONV'}:
                try:
                    from decimal import Decimal

                    dec = value if isinstance(value, Decimal) else Decimal(str(value).replace(',', '.'))
                    text = f"{dec:.4f}"
                    return text.replace('.', ',')
                except Exception:
                    return str(value).replace('.', ',')
            if col in {'QTACAR', 'QTAINT'}:
                try:
                    dec = float(str(value).replace(',', '.'))
                    if dec.is_integer():
                        return str(int(dec))
                except Exception:
                    pass
            return str(value)

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, delimiter=';', lineterminator='\r\n', extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({c: _format_csv_value(c, row.get(c)) for c in columns})

        safe_list = re.sub(r'[^A-Za-z0-9_-]+', '_', str(res.get('price_list_name') or 'listino')).strip('_') or 'listino'
        safe_type = re.sub(r'[^A-Za-z0-9_-]+', '_', listino_type).strip('_') or 'tipo'
        filename = f"{safe_list}_{safe_type}.csv"
        csv_text = output.getvalue()
        return Response(
            "\ufeff" + csv_text,
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        current_app.logger.exception("Errore admin_api_listini_export")
        return jsonify({'ok': False, 'error': str(e)}), 500




@app.post('/admin/api/listini/import-csv')
@login_required
@admin_required
def admin_api_listini_import_csv():
    """Importa un CSV e restituisce le righe da unire nella tabella listino (senza scrivere sul DB)."""
    listino_type = (request.form.get('type') or 'FoodPaper').strip() or 'FoodPaper'
    price_list_uuid = (request.form.get('price_list_id') or request.form.get('listino_uuid') or '').strip()
    if not price_list_uuid:
        return jsonify({'ok': False, 'error': 'Seleziona un elenco prezzi prima di importare.'}), 400

    f = request.files.get('file')
    if f is None:
        return jsonify({'ok': False, 'error': 'File CSV mancante.'}), 400

    try:
        csv_bytes = f.read()
    except Exception:
        csv_bytes = b''

    try:
        ensure_supplier_orders_schema()
        try:
            migrate_legacy_pricelists()
            ensure_default_price_list_assignments()
        except Exception:
            current_app.logger.exception("Listini multipli non inizializzati durante import CSV")
        meta = load_unified_pricelist(listino_type, price_list_uuid or None)
        if not meta.get('ok'):
            return jsonify({'ok': False, 'error': meta.get('error') or 'Errore caricamento schema listino.'}), 400

        target_columns = meta.get('columns') or []
        desc_col = meta.get('desc_column') or ''
        supplier_col = meta.get('supplier_column') or ''

        res = parse_pricelist_csv(
            csv_bytes=csv_bytes,
            target_columns=target_columns,
            desc_col=desc_col,
            supplier_col=supplier_col,
        )
        status = 200 if res.get('ok') else 400
        # include schema info for client merge
        res['listino_type'] = listino_type
        res['table'] = meta.get('table')
        res['desc_column'] = desc_col
        res['supplier_column'] = supplier_col
        res['columns'] = target_columns
        return jsonify(res), status
    except Exception as e:
        current_app.logger.exception(e)
        return jsonify({'ok': False, 'error': 'Errore import CSV.'}), 500

@app.post('/admin/api/listini/apply/start')
@login_required
@admin_required
def admin_api_listini_apply_start():
    """Avvia salvataggio listino in background e restituisce subito job_id."""
    _listini_jobs_cleanup_files()

    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        payload = {}

    listino_type = (payload.get('type') or 'FoodPaper').strip()
    price_list_uuid = str(payload.get('price_list_id') or payload.get('listino_uuid') or '').strip()
    rows = payload.get('rows') or []
    scope = str(payload.get('scope') or payload.get('mode') or 'master').strip().lower()
    if not price_list_uuid:
        return jsonify({'ok': False, 'error': 'Seleziona un elenco prezzi prima di salvare.'}), 400
    try:
        payload = dict(payload)
        payload['tenant_database'] = get_storehub_database_name()
        job_id = uuid4().hex
        _input_path, prog_path = _listini_job_paths(job_id)
        progress = {
            'id': job_id,
            'type': listino_type,
            'price_list_id': price_list_uuid,
            'source_store': '9001',
            'scope': scope,
            'running': True,
            'finished': False,
            'done': 0,
            'total': 1,
            'stores_ok': 0,
            'stores_fail': 0,
            'current_store': '9001',
            'logs': ['Salvataggio SQL in coda.'],
            'failures': [],
            'error': None,
            'pid': None,
            'ts': time.time(),
        }
        _json_write_atomic(prog_path, progress)
        try:
            _input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        worker = threading.Thread(
            target=_run_listini_apply_job,
            args=(current_app._get_current_object(), job_id, prog_path, payload),
            daemon=True,
        )
        worker.start()
        return jsonify({'ok': True, 'job_id': job_id, 'total_stores': 1, 'scope': scope}), 200
    except Exception as e:
        current_app.logger.exception("Errore admin_api_listini_apply_start")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/admin/api/listini/apply/progress')
@login_required
@admin_required
def admin_api_listini_apply_progress():
    """Ritorna stato job export listini (da file progress)."""
    _listini_jobs_cleanup_files()
    job_id = (request.args.get('job_id') or '').strip()
    if not job_id:
        return jsonify({'ok': False, 'error': 'job_id mancante'}), 400

    _, prog_path = _listini_job_paths(job_id)
    st = _json_read_safe(prog_path)
    if not st:
        return jsonify({'ok': False, 'error': 'job non trovato'}), 404

    # output ridotto
    out = {
        'id': st.get('id') or job_id,
        'type': st.get('type'),
        'source_store': st.get('source_store'),
        'scope': st.get('scope'),
        'running': bool(st.get('running')),
        'finished': bool(st.get('finished')),
        'done': int(st.get('done') or 0),
        'total': int(st.get('total') or 0),
        'stores_ok': int(st.get('stores_ok') or 0),
        'stores_fail': int(st.get('stores_fail') or 0),
        'current_store': st.get('current_store') or '',
        'error': st.get('error'),
        'logs': (st.get('logs') or [])[-80:],
        'failures': st.get('failures') or [],
    }

    # Se i log del progress sono vuoti ma esiste un file log del worker, includi una tail (utile per errori di avvio).
    try:
        if not out.get("logs"):
            wlp = st.get("worker_log")
            if wlp:
                tail = _tail_text_file(Path(str(wlp)), max_lines=80)
                if tail:
                    out["logs"] = tail
    except Exception:
        pass
    return jsonify({'ok': True, 'job': out}), 200

# --------------------- ROUTES: APP ---------------------
@app.route('/summary-reviews', endpoint='summary_reviews')
def home():
    # Home "lite": la tendina mostra SOLO le location autorizzate per l'utente
    locs = list_locations()
    return render_template('home.html', locations=locs)

# Endpoint veloce per la home "lite"

@app.get('/api/location/quick-stats')
@login_required
def api_location_quick_stats():
    """Statistiche veloci per una location (view reviews_with_title) con RLS. Param: loc, days."""
    loc = request.args.get('loc')
    try:
        days = int(request.args.get('days') or 7)
        if days not in (7, 30, 60, 90, 180, 365):
            days = 7
    except Exception:
        days = 7

    if not loc:
        return jsonify({'error': 'loc mancante'}), 400

    # opzionale: controllo applicativo extra oltre RLS
    try:
        if not user_can_access_loc(loc):
            return jsonify({'error': 'Non autorizzato per questa location'}), 403
    except Exception:
        pass

    since_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if not session.get('sb_token'):
        return jsonify({'error': 'no token'}), 401

    url = f"{SUPABASE_URL}/rest/v1/reviews_with_title"
    params = {
        'select': 'review_id,location_id,title,comment,create_time,update_time,star_rating,reviewer',
        'location_id': f'eq.{loc}',
        'create_time': f'gte.{since_iso}',
        'order': 'create_time.desc',
    }
    r = _session().get(url, headers=_sb_headers_user(), params=params, timeout=25)
    _raise_with_body(r)
    rows = r.json() or []

    def to_num_star(v):
        if v is None: return None
        if isinstance(v, (int, float)):
            try: return int(v)
            except Exception: return None
        m = {'ONE':1,'TWO':2,'THREE':3,'FOUR':4,'FIVE':5}
        return m.get(str(v).strip().upper())

    def norm_comment(c):
        if isinstance(c, str) and c.strip().lower() == 'none':
            return ''
        return c or ''

    total = len(rows)
    stars = [to_num_star(x.get('star_rating')) for x in rows if to_num_star(x.get('star_rating'))]
    pos   = sum(1 for x in rows if (to_num_star(x.get('star_rating')) or 0) >= 3)
    neg   = sum(1 for x in rows if 0 < (to_num_star(x.get('star_rating')) or 0) <= 2)
    avg   = round(sum(stars)/len(stars), 1) if stars else None

    latest_pos5 = [{
        "reviewer": x.get("reviewer"),
        "stars": to_num_star(x.get("star_rating")) or 0,
        "comment": norm_comment(x.get("comment")),
        "time": x.get("create_time") or x.get("update_time")
    } for x in rows if (to_num_star(x.get("star_rating")) or 0) >= 3][:5]

    latest_neg5 = [{
        "reviewer": x.get("reviewer"),
        "stars": to_num_star(x.get("star_rating")) or 0,
        "comment": norm_comment(x.get("comment")),
        "time": x.get("create_time") or x.get("update_time")
    } for x in rows if 0 < (to_num_star(x.get("star_rating")) or 0) <= 2][:5]

    return jsonify({
        "total_period": total,
        "positive_period": pos,
        "negative_period": neg,
        "avg": avg,
        "latest_pos5": latest_pos5,
        "latest_neg5": latest_neg5,
    })

@app.route('/gestione-anagrafica')
@login_required
@require_perm('can_view_anagrafica')
def gestione_anagrafica():
    locs = list_locations()
    return render_template('index.html', locations=locs)

@app.route('/api/location/<path:loc_name>')
@login_required
@require_perm('can_view_anagrafica')
def api_location(loc_name):
    if not user_can_access_loc(loc_name):
        return jsonify({'error': 'Non autorizzato per questa location'}), 403
    return jsonify(get_location(loc_name))

@app.route('/edit/<path:loc_name>', methods=['GET', 'POST'])
@login_required
@require_perm('can_edit_anagrafica')
def edit(loc_name):
    if not user_can_access_loc(loc_name):
        flash("Non sei autorizzato per questa location.", 'danger')
        return redirect(url_for('gestione_anagrafica'))
    full = extract_loc_id(loc_name)
    if request.method == 'POST':
        updates = {}
        for f in ('title', 'storeCode', 'websiteUri'):
            v = request.form.get(f)
            if v: updates[f] = v
        desc = request.form.get('description', '').strip()
        if desc:
            updates['profile'] = {'description': desc}
        days = ['MONDAY','TUESDAY','WEDNESDAY','THURSDAY','FRIDAY','SATURDAY','SUNDAY']
        periods = []
        for d in days:
            o = request.form.get(f'open_{d}')
            c = request.form.get(f'close_{d}')
            if o and c:
                oh, om = map(int, o.split(':'))
                ch, cm = map(int, c.split(':'))
                periods.append({
                    'openDay': d, 'openTime': {'hours': oh, 'minutes': om},
                    'closeDay': d, 'closeTime': {'hours': ch, 'minutes': cm}
                })
        if periods:
            updates['regularHours'] = {'periods': periods}
        try:
            if updates:
                token = get_access_token()
                url = f'https://mybusinessbusinessinformation.googleapis.com/v1/{full}'
                resp = _session().patch(url,
                                        headers={'Authorization': f'Bearer {token}','Content-Type':'application/json'},
                                        params={'updateMask': ','.join(updates.keys())},
                                        json=updates,
                                        timeout=15)
                _raise_with_body(resp)
            flash("Salvataggio riuscito!", 'success')
        except Exception as e:
            flash(str(e), 'danger')
        return redirect(url_for('edit', loc_name=loc_name))

    loc = get_location(loc_name)
    media = []  # non carichiamo media qui
    return render_template('edit.html', location=loc, media=media)

# -------- Gestione Recensioni (invariata, +permessi) --------
@app.route('/gestione-recensioni')
@login_required
@require_perm('can_view_reviews')
def gestione_recensioni():
    
    # days dalla query (default 7)
    try:
        days = int(request.args.get('days') or 7)
    except Exception:
        days = 7
    if days not in (7, 30, 90):
        days = 7

    locs = list_locations()
    return render_template('reviews_index.html', locations=locs, days=days)

@app.route('/reviews/<path:loc_name>')
@login_required
def reviews(loc_name):
    loc_name = unquote(loc_name)
    if not user_can_access_loc(loc_name):
        flash("Non sei autorizzato per questa location.", 'danger')
        return redirect(url_for('gestione_recensioni'))
    try:
        # days dalla query (default 7)
        try:
            days = int(request.args.get('days') or 7)
        except Exception:
            days = 7
        if days not in (7, 30, 90):
            days = 7
        since_dt = datetime.now(timezone.utc) - timedelta(days=days)
        loc = get_location(loc_name)
        title = loc.get('title')
        location_id = 'locations/' + extract_loc_id(loc_name).split('/')[1]
        items = reviews_for_ui(
            location_id=location_id,
            fetch_google=lambda: list_reviews_v4(loc_name, order_by='updateTime desc')[0],
            limit=100
        )
        try:
            loc_id = extract_loc_id(loc_name).split('/')[1]
            review_prefix = f'accounts/{ACCOUNT_ID}/locations/{loc_id}/reviews/'
            for r in items:
                rid = r.get('reviewId') or (r.get('name','').split('/reviews/')[-1] if r.get('name') else None)
                r['_review_name'] = r.get('name') or (review_prefix + rid if rid else None)
                r['_stars'] = _to_stars(r.get('starRating'))
        except Exception:
            for r in items:
                r.setdefault('_stars', _to_stars(r.get('starRating')))
        # Filtro temporale su createTime/updateTime rispetto a 'since_dt'
        def _parse_iso_utc(ts: str):
            if not ts:
                return None
            s = ts.strip()
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            if 'T' in s and '.' in s:
                head, tail = s.split('.', 1)
                tz = '+00:00'
                if len(tail) >= 6 and (tail[-6] in ['+', '-']):
                    tz = tail[-6:]
                    frac = ''.join(c for c in tail[:-6] if c.isdigit())
                else:
                    frac = ''.join(c for c in tail if c.isdigit())
                frac = (frac + '000000')[:6] if frac else '000000'
                s = f"{head}.{frac}{tz}"
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None

        items = [
            r for r in items
            if (
                (_parse_iso_utc(r.get('createTime') or r.get('updateTime')) or datetime.min.replace(tzinfo=timezone.utc))
                >= since_dt
            )
        ]

        # Passa locations per tendina e selezione corrente
        locs = list_locations()
        return render_template('reviews.html', loc_name=loc_name, location_title=title, reviews=items, days=days, locations=locs, loc_selected=loc_name)
    except Exception as e:
        logging.exception('Errore caricando recensioni')
        flash(f'Errore caricando recensioni: {e}', 'danger')
        return redirect(url_for('gestione_recensioni'))

@app.route('/reviews/respond', methods=['POST'])
@login_required
@require_perm('can_reply_reviews')
def reviews_respond():
    review_name = request.form.get('review_name', '')
    reply_text  = request.form.get('reply_text', '').strip()
    loc_name    = unquote(request.form.get('loc_name', ''))
    if not user_can_access_loc(loc_name):
        flash("Non sei autorizzato per questa location.", 'danger')
        return redirect(url_for('gestione_recensioni'))
    try:
        resp = update_review_reply_v4(review_name, reply_text)
        google_update_time = None
        try:
            if isinstance(resp, dict):
                google_update_time = (resp.get('reviewReply') or {}).get('updateTime')
        except Exception:
            google_update_time = None
        from datetime import datetime
        on_review_reply_saved(
            review_id=review_name,
            reply_text=reply_text,
            reply_update_time=google_update_time or (datetime.utcnow().isoformat() + 'Z'),
            actor_uid=session.get('uid')
        )
        flash("Risposta salvata.", 'success')
    except Exception as e:
        logging.exception('Errore durante la risposta recensione')
        flash(f'Errore scrittura risposta: {e}', 'danger')
    return redirect(url_for('reviews', loc_name=loc_name))

@app.get('/media/bulk')
@login_required
@require_perm('can_access_media_bulk')
def media_bulk_page():
    locs = list_locations()
    return render_template('media_bulk.html', locations=locs, categories=GBP_MEDIA_CATEGORIES)

@app.post('/media/upload-bulk')
@login_required
@require_perm('can_access_media_bulk')
def media_upload_bulk():
    selected = request.form.getlist('loc_names')
    # Filtra per permessi
    selected = [ln for ln in selected if user_can_access_loc(ln)]
    chosen_category = (request.form.get('category') or 'ADDITIONAL').strip().upper() or 'ADDITIONAL'
    files = request.files.getlist('photos')
    if not selected or not files:
        flash("Seleziona almeno una location e almeno un file.", 'warning')
        return redirect(url_for('media_bulk_page'))

    prepped = []
    for f in files:
        try:
            f.stream.seek(0)
            raw, mime = _normalize_image_for_gbp(f)
            fname = getattr(f, 'filename', None)
            url = _imggb_upload(raw, fname or 'upload.jpg', mime, IMGBB_EXPIRATION)
            logging.info("ImgBB OK (bulk): %s", url)
            prepped.append({'fname': fname, 'raw': raw, 'mime': mime, 'url': url})
        except Exception as e:
            logging.exception('Preprocessing fallito: %s', getattr(f, 'filename', ''))
            prepped.append({'fname': getattr(f, 'filename', None), 'raw': None, 'mime': None, 'url': None, 'err': e})

    ok, fail = 0, []
    for loc_name in selected:
        loc_id = extract_loc_id(loc_name).split('/')[1]
        for item in prepped:
            fname = item.get('fname'); base_url = item.get('url')
            raw = item.get('raw'); mime = item.get('mime')
            if not base_url:
                fail.append(f'{loc_name}: {fname or "file"} → errore in preparazione: {item.get("err")}')
                continue
            try:
                _media_create_from_url_retry(loc_id, chosen_category, base_url,
                                             raw=raw, mime=mime, fname=fname,
                                             attempts=4, sleeps=[0,2,5,10])
                ok += 1
                time.sleep(0.6)
            except Exception as e:
                logging.exception('Upload fallito per %s', loc_name)
                fail.append(f'{loc_name}: {fname or "file"} → {e}')
    if ok:  flash(f'Caricamento completato: {ok} item creati.', 'success')
    if fail: flash("Alcuni upload non sono riusciti:<br>' + '<br>'.join(fail", 'danger')
    return redirect(url_for('media_bulk_page'))

@app.get('/media/location')
@login_required
@require_perm('can_access_media_single')
def media_location_page():
    locs = list_locations()
    loc_selected = request.args.get('loc', '').strip()
    limit = max(12, min(int(request.args.get('limit', '60')), 600))
    media_items, title = [], None
    if loc_selected:
        if not user_can_access_loc(loc_selected):
            flash("Non sei autorizzato per questa location.", 'danger')
            return redirect(url_for('media_location_page'))
        try:
            location_id = 'locations/' + extract_loc_id(loc_selected).split('/')[1]
            media_items = media_for_ui(
                location_id=location_id,
                fetch_google=lambda: list_media_v4_limited(loc_selected, max_items=limit),
                limit=limit
            )
            title = get_location(loc_selected).get('title')
        except Exception as e:
            logging.exception('Errore caricando media singola location')
            flash(f'Errore caricando foto: {e}', 'danger')
    return render_template('media_location.html', locations=locs, loc_selected=loc_selected, location_title=title, media_items=media_items, categories=GBP_MEDIA_CATEGORIES, limit=limit)

@app.post('/media/upload-single')
@login_required
@require_perm('can_access_media_single')
def media_upload_single():
    loc_name = (request.form.get('loc_name') or '').strip()
    chosen_category = (request.form.get('category') or 'ADDITIONAL').strip().upper() or 'ADDITIONAL'
    files = request.files.getlist('photos')
    if not loc_name or not files:
        flash("Seleziona una location e almeno un file.", 'warning')
        return redirect(url_for('media_location_page'))
    if not user_can_access_loc(loc_name):
        flash("Non sei autorizzato per questa location.", 'danger')
        return redirect(url_for('media_location_page'))
    loc_id = extract_loc_id(loc_name).split('/')[1]
    ok, fail = 0, []
    for f in files:
        try:
            f.stream.seek(0)
            raw, mime = _normalize_image_for_gbp(f)
            fname = getattr(f, 'filename', None)
            base_url = _imggb_upload(raw, fname or 'upload.jpg', mime, IMGBB_EXPIRATION)
            logging.info("ImgBB OK (single): %s", base_url)
            _media_create_from_url_retry(loc_id, chosen_category, base_url,
                                         raw=raw, mime=mime, fname=fname,
                                         attempts=4, sleeps=[0,2,5,10])
            ok += 1
            time.sleep(0.4)
        except Exception as e:
            logging.exception('Upload single fallito')
            fail.append(f'{getattr(f,"filename","")} → {e}')
    if ok:  flash(f'Caricate {ok} foto sulla location selezionata.', 'success')
    if fail: flash("Alcuni upload non sono riusciti:<br>' + '<br>'.join(fail", 'danger')
    return redirect(url_for('media_location_page', loc=loc_name))

@app.post('/media/delete')
@login_required
@require_perm('can_access_media_single')
def media_delete():
    loc_name = request.form.get('loc_name', '').strip()
    names = request.form.getlist('media_names')
    if not names:
        flash("Nessuna foto selezionata per l'eliminazione.", 'warning')
        return redirect(url_for('media_location_page', loc=loc_name) if loc_name else url_for('media_location_page'))
    if loc_name and not user_can_access_loc(loc_name):
        flash("Non sei autorizzato per questa location.", 'danger')
        return redirect(url_for('media_location_page'))
    ok, fail = 0, []
    for n in names:
        try:
            media_delete_v4(n)
            on_media_deleted(media_id=n, actor_uid=session.get('uid'))
            ok += 1
        except Exception as e:
            logging.exception('Delete fallita per %s', n)
            fail.append(f'{n} → {e}')
    if ok:
        flash(f'Eliminate {ok} foto.', 'success')
    if fail:
        flash("Alcune eliminazioni non sono riuscite:<br>' + '<br>'.join(fail", 'danger')
    return redirect(url_for('media_location_page', loc=loc_name) if loc_name else url_for('media_location_page'))


def sb_delete_profile(token: str, uid: str) -> bool:
    """Cancella il profilo e le associazioni location dal database (non dall'Auth)."""
    # delete user_locations
    url = f'{SUPABASE_URL}/rest/v1/user_locations'
    r = _session().delete(url, headers=_sb_headers(token), params={'user_id': f'eq.{uid}'}, timeout=20)
    _raise_with_body(r)
    # delete profile
    url = f'{SUPABASE_URL}/rest/v1/profiles'
    r = _session().delete(url, headers=_sb_headers(token), params={'id': f'eq.{uid}'}, timeout=20)
    _raise_with_body(r)
    return True


# --- New Dashboard routes ---
@app.route('/')
def root_redirect():
    return redirect(url_for('dashboard'))


@app.route('/home')
@login_required
def home():
    return redirect(url_for(_first_allowed_endpoint_for_user(current_user())))


@app.route('/ai-assistant', methods=['GET', 'POST'])
@login_required
@ai_enabled_required
def ai_assistant():
    user = current_user() or {}
    role_l = str(user.get('role') or '').strip().lower()
    answer = None
    context = None
    usage = None
    question = (request.form.get('question') or request.args.get('q') or '').strip()
    page_context = (request.form.get('page_context') or request.args.get('page_context') or '').strip()

    try:
        available_stores = _load_ai_available_stores_for_user(user)
    except Exception as e:
        available_stores = []
        flash(f'Errore caricando gli store disponibili: {e}', 'danger')

    if request.method == 'POST':
        if not question:
            flash('Inserisci una domanda per l’assistente AI.', 'warning')
        elif not openai_is_configured():
            flash('AI non configurata: manca la chiave OpenAI o il modello.', 'danger')
        else:
            try:
                result = ask_storehub_assistant(
                    question,
                    available_stores=available_stores,
                    role=role_l,
                    current_store_code=str(session.get('store_code') or '').strip() or None,
                    page_context=page_context or request.endpoint or request.path,
                )
                answer = result.get('answer')
                context = result.get('context')
                usage = result.get('usage')
            except Exception as e:
                current_app.logger.exception('Errore assistente AI')
                flash(f'Errore generando la risposta AI: {e}', 'danger')

    return render_template(
        'ai_assistant.html',
        user=user,
        page_title='Assistente AI',
        question=question,
        answer=answer,
        analysis_context=context,
        ai_usage=usage,
        ai_configured=openai_is_configured(),
        available_store_count=len(available_stores or []),
        available_stores=available_stores,
        page_context=page_context,
    )


@app.post('/api/ai-assistant/query')
@login_required
@ai_enabled_required
def api_ai_assistant_query():
    user = current_user() or {}
    role_l = str(user.get('role') or '').strip().lower()
    payload = request.get_json(silent=True) if request.is_json else {}
    payload = payload if isinstance(payload, dict) else {}
    question = str(request.form.get('question') or payload.get('question') or '').strip()
    page_context = str(request.form.get('page_context') or payload.get('page_context') or '').strip()

    try:
        available_stores = _load_ai_available_stores_for_user(user)
    except Exception as e:
        current_app.logger.exception('Errore caricando store AI')
        return jsonify({'ok': False, 'error': f'Errore caricando gli store disponibili: {e}'}), 500

    if not question:
        return jsonify({'ok': False, 'error': 'Inserisci una domanda per l’assistente AI.'}), 400
    if not openai_is_configured():
        return jsonify({'ok': False, 'error': 'AI non configurata: manca la chiave OpenAI o il modello.'}), 503

    try:
        result = ask_storehub_assistant(
            question,
            available_stores=available_stores,
            role=role_l,
            current_store_code=str(session.get('store_code') or '').strip() or None,
            page_context=page_context or request.endpoint or request.path,
        )
        return jsonify({
            'ok': True,
            'answer': result.get('answer'),
            'context': result.get('context'),
            'usage': result.get('usage'),
            'model': result.get('model'),
        })
    except Exception as e:
        current_app.logger.exception('Errore assistente AI API')
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/dashboard')
@login_required
@module_required('dashboard')
def dashboard():
    user = current_user()

    store_code = session.get('store_code')
    store_name = session.get('store_name') or store_code
    role_l = str(session.get('role') or '').strip().lower()
    convalide_store = []

    daily_summary = None
    # Mostra il pop-up solo se lo store è selezionato (es. admin senza store: mai) e solo una volta per login.
    if store_code and session.get('show_day_summary_popup'):
        try:
            from datetime import date
            from cruscotto_repository import get_day_summary_kpis
            from cash_statement_config_repository import list_cash_statement_dashboard_customizations

            today = date.today()
            kpis = get_day_summary_kpis(store_code=str(store_code), day=today) or {}
            tenant_key = str(session.get("tenant_key") or "default").strip() or "default"
            cash_customizations = list_cash_statement_dashboard_customizations(tenant_key=tenant_key)
            daily_summary = {
                "store_code": str(store_code),
                "store_name": str(store_name or store_code),
                "day": kpis.get("day") or today.isoformat(),
                "budget_net": float(kpis.get("budget_net") or 0.0),
                "ly_date": kpis.get("ly_date"),
                "ly_revenues_net": float(kpis.get("ly_revenues_net") or 0.0),
                "forecast_net": kpis.get("forecast_net"),
                "cash_statement_customizations": cash_customizations,
            }
        except Exception:
            daily_summary = None

        # Consuma il flag solo se abbiamo realmente uno store selezionato (così chi ha il modal store obbligatorio non lo perde).
        session.pop('show_day_summary_popup', None)

    if store_code and role_l == 'admin':
        try:
            from rendiconto_convalide_repository import list_convalide_store
            convalide_store = list_convalide_store(store_code=str(store_code))
        except Exception:
            convalide_store = []

    return render_template(
        'dashboard.html',
        user=user,
        page_title='Dashboard',
        daily_summary=daily_summary,
        convalide_store=convalide_store,
    )




@app.get('/api/group-stats')
@login_required
def api_group_stats():
    """
    Aggregati gruppo dal DB (view reviews_with_title) con RLS e filtro esplicito per location visibili.
    Param: days (7|30|60|90|180|365), default 30.
    Positive = >=3★, Negative = <=2★
    """
    import requests
    from datetime import datetime, timedelta, timezone

    def to_num_star(v):
        if v is None: return None
        if isinstance(v, (int, float)):
            try: return int(v)
            except Exception: return None
        m = {'ONE':1,'TWO':2,'THREE':3,'FOUR':4,'FIVE':5}
        s = str(v).strip().upper()
        if s.isdigit():
            try: return int(s)
            except Exception: return None
        return m.get(s)

    try:
        days = int(request.args.get('days') or 30)
        if days not in (7, 30, 60, 90, 180, 365):
            days = 30
    except Exception:
        days = 30

    if not session.get('sb_token'):
        return jsonify({'error': 'no token'}), 401

    allowed = _fetch_allowed_locations_via_rls()
    if not allowed:
        return jsonify({
            'totals': {'reviews': 0, 'avgStars': None, 'withComment': 0, 'positives': 0, 'negatives': 0,
                       'positivesPct': None, 'negativesPct': None},
            'topLocations': [],
            'latestNegatives': [],
            'latestPositives': [],
            'perLocation': [],
            'timeseries': []
        })

    # PostgREST IN helper: in.("val1","val2")
    def pgrest_in(values):
        safe = []
        for v in values:
            if v is None: continue
            s = str(v).replace('"','\\"')
            safe.append(f'"{s}"')
        return f'in.({",".join(safe)})'

    since_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Filtro opzionale per location selezionate (intersezione con allowed)
    locs_arg = (request.args.get('locs') or '').strip()
    only = (request.args.get('only') or '').strip().lower()
    selected = []
    if locs_arg:
        for raw in locs_arg.split(','):
            v = (raw or '').strip()
            if v:
                selected.append(v)
    # Interseca con allowed per sicurezza
    if selected:
        allowed_set = set(allowed)
        selected = [v for v in selected if v in allowed_set]

    target_ids = (selected or allowed)

    # Carico le righe dal materialized view con titolo
    url = f"{SUPABASE_URL}/rest/v1/reviews_with_title"
    params = {
        'select': 'review_id,location_id,title,comment,create_time,update_time,star_rating,reviewer',
        'create_time': f'gte.{since_iso}',
        'location_id': pgrest_in(target_ids),
        'order': 'create_time.desc',
        'limit': 100000
    }
    r = _session().get(url, headers=_sb_headers_user(), params=params, timeout=40)
    _raise_with_body(r)
    rows = r.json() or []

    total = 0; sum_stars = 0; with_comment = 0; pos = 0; neg = 0
    by_loc = {}; latest_neg = []; latest_pos = []

    for row in rows:
        s = to_num_star(row.get('star_rating'))
        cm = row.get('comment') or ''
        if isinstance(cm, str) and cm.strip().lower() == 'none':
            cm = ''
        total += 1
        if s is not None: sum_stars += s
        if cm: with_comment += 1

        lid = (row.get('location_id') or '').strip()
        title = row.get('title') or (lid.split('/',1)[1] if lid else '')
        agg = by_loc.get(lid)
        if not agg:
            agg = {'location_id': lid, 'title': title, 'total': 0, 'sum': 0, 'pos': 0, 'neg': 0}
            by_loc[lid] = agg
        agg['total'] += 1
        if s is not None: agg['sum'] += s
        if s is not None and s >= 3:
            pos += 1; agg['pos'] += 1
            latest_pos.append({
                'create_time': row.get('create_time') or row.get('update_time'),
                'location_id': lid,
                'location_title': title,
                'reviewer_display_name': row.get('reviewer_display_name') or row.get('reviewer') or 'Utente',
                'stars': s,
                'comment': cm
            })
        if s is not None and s <= 2:
            neg += 1; agg['neg'] += 1
            latest_neg.append({
                'create_time': row.get('create_time') or row.get('update_time'),
                'location_id': lid,
                'location_title': title,
                'reviewer_display_name': row.get('reviewer_display_name') or row.get('reviewer') or 'Utente',
                'stars': s,
                'comment': cm
            })

    avg = (sum_stars/total) if total else None
    latest_neg.sort(key=lambda x: x.get('create_time') or '', reverse=True)
    latest_pos.sort(key=lambda x: x.get('create_time') or '', reverse=True)

    # Top locations (per numero di recensioni nel periodo)
    top_locations = []
    for lid, agg in by_loc.items():
        t = agg['total']
        top_locations.append({
            'location_id': lid,
            'title': agg['title'],
            'total': t,
            'avgStars': (agg['sum']/t) if t else None,
            'positivesPct': (agg['pos']/t*100) if t else None
        })
    top_locations.sort(key=lambda x: (x['positivesPct'] if x['positivesPct'] is not None else -1), reverse=True)

    # overallStars (lifetime): usa review_stats se disponibile, altrimenti calcolo unico su reviews
    overall_map = {}
    try:
        # Prova cache
        params_cache = {
            'select': 'location_id,overall_avg',
            'location_id': pgrest_in(target_ids),
            'limit': 100000
        }
        r_overall = _session().get(f"{SUPABASE_URL}/rest/v1/review_stats", headers=_sb_headers_user(), params=params_cache, timeout=30)
        if r_overall.ok:
            for row in (r_overall.json() or []):
                lid = (row.get('location_id') or '').strip()
                if lid:
                    overall_map[lid] = row.get('overall_avg')
        # Fallback bulk (solo se cache assente)
        if not overall_map:
            params_all = {
                'select': 'location_id,star_rating',
                'location_id': pgrest_in(target_ids),
                'limit': 200000
            }
            r_all = _session().get(f"{SUPABASE_URL}/rest/v1/reviews", headers=_sb_headers_user(), params=params_all, timeout=60)
            if r_all.ok:
                tmp = {}
                for x in (r_all.json() or []):
                    lid2 = (x.get('location_id') or '').strip()
                    s2 = to_num_star(x.get('star_rating'))
                    if lid2 and s2 is not None:
                        t = tmp.get(lid2) or {'sum': 0.0, 'n': 0}
                        t['sum'] += float(s2); t['n'] += 1
                        tmp[lid2] = t
                for lid2, t in tmp.items():
                    overall_map[lid2] = (t['sum']/t['n']) if t['n'] else None
    except Exception:
        overall_map = {}

    # Tabella per location (tutte le allowed, anche senza recensioni nel periodo)
    try:
        locs = list_locations()  # restituisce già filtrate per utente se non admin
    except Exception:
        locs = []
    title_map = { (loc.get('name') or '').strip(): (loc.get('title') or '') for loc in (locs or []) }

    per_location = []
    seen = set()
    # Prima quelle con dati nel periodo
    for lid, agg in by_loc.items():
        t = agg.get('total') or 0
        per_location.append({
            'location_id': lid,
            'title': agg.get('title') or title_map.get(lid) or lid,
            'positives': agg.get('pos') or 0,
            'negatives': agg.get('neg') or 0,
            'avgStars': ((agg.get('sum') or 0) / t) if t else None,
            'overallStars': overall_map.get(lid),
            'total': t
        })
        seen.add(lid)
    # Poi aggiungo le allowed senza dati nel periodo
    for lid in allowed:
        lid = (lid or '').strip()
        if not lid or lid in seen: 
            continue
        per_location.append({
            'location_id': lid,
            'title': title_map.get(lid) or lid,
            'positives': 0,
            'negatives': 0,
            'avgStars': None,
            'overallStars': overall_map.get(lid),
            'total': 0
        })
    per_location.sort(key=lambda x: (x['title'] or '').lower())

    
    # Timeseries (by creation date) + per location
    ts_map = {}
    ts_by_loc_map = {}
    for row in rows:
        dt = row.get('create_time') or row.get('update_time') or ''
        d = str(dt)[:10]
        if not d: 
            continue
        rec = ts_map.get(d)
        if not rec:
            rec = {'date': d, 'total': 0, 'positives': 0, 'negatives': 0, 'sumStars': 0}
            ts_map[d] = rec
        s2 = to_num_star(row.get('star_rating'))
        rec['total'] += 1
        if s2 is not None:
            rec['sumStars'] += s2
            if s2 >= 3: rec['positives'] += 1
            if s2 <= 2: rec['negatives'] += 1
        # per location
        lid2 = (row.get('location_id') or '').strip()
        if lid2:
            inner = ts_by_loc_map.get(lid2)
            if not inner:
                inner = {}
                ts_by_loc_map[lid2] = inner
            rloc = inner.get(d)
            if not rloc:
                rloc = {'date': d, 'total': 0, 'positives': 0, 'negatives': 0, 'sumStars': 0}
                inner[d] = rloc
            rloc['total'] += 1
            if s2 is not None:
                rloc['sumStars'] += s2
                if s2 >= 3: rloc['positives'] += 1
                if s2 <= 2: rloc['negatives'] += 1

    timeseries = []
    for d in sorted(ts_map.keys()):
        r2 = ts_map[d]
        avgd = (r2['sumStars']/r2['total']) if r2['total'] else None
        timeseries.append({'date': d, 'total': r2['total'], 'positives': r2['positives'], 'negatives': r2['negatives'], 'avgStars': avgd})

    timeseriesByLocation = {}
    for lid2, dmap in ts_by_loc_map.items():
        seq = []
        for d in sorted(dmap.keys()):
            r2 = dmap[d]
            avgd = (r2['sumStars']/r2['total']) if r2['total'] else None
            seq.append({'date': d, 'total': r2['total'], 'positives': r2['positives'], 'negatives': r2['negatives'], 'avgStars': avgd})
        timeseriesByLocation[lid2] = seq

    # Se richiesto solo il timeseries (per filtrare grafici senza toccare KPI/tabelle)
    if only == 'timeseries':
        return jsonify({'timeseries': timeseries,
        'timeseriesByLocation': timeseriesByLocation, 'timeseriesByLocation': timeseriesByLocation})


    return jsonify({
        'totals': {
            'reviews': total,
            'avgStars': (sum_stars/total) if total else None,
            'withComment': with_comment,
            'positives': pos,
            'negatives': neg,
            'positivesPct': (pos/total*100) if total else None,
            'negativesPct': (neg/total*100) if total else None
        },
        'topLocations': top_locations[:10],
        'latestNegatives': latest_neg[:10],
        'latestPositives': latest_pos[:10],
        'perLocation': per_location,
        'timeseries': timeseries,
        'timeseriesByLocation': timeseriesByLocation
    })

# REMOVED_admin_users_post_create
from urllib.parse import urlparse

def _safe_next_url(nxt: str | None) -> str:
    """Allow only relative in-site paths to avoid open redirect.

    Falls back to dashboard if invalid.
    """
    try:
        if not nxt:
            return url_for('dashboard')
        # Only allow absolute-paths starting with '/' and not '//' (scheme-relative)
        if nxt.startswith('/') and not nxt.startswith('//'):
            return nxt
    except Exception:
        pass
    return url_for('dashboard')

# === Calendar & Supervisor features (appended) ===
from flask import jsonify, request, abort
import datetime as _dt

def _require_login():
    if 'sb_token' not in session:
        return redirect(url_for('login', next=request.full_path))
    return None

def _is_admin():
    try:
        u = current_user()
        return (u or {}).get('role') == 'admin'
    except Exception:
        return False

def _is_supervisor():
    try:
        u = current_user()
        return (u or {}).get('role') == 'supervisor'
    except Exception:
        return False

@app.get('/calendar')
@login_required
def calendar_page():
    # pagina calendario
    guard = _require_login()
    if guard: return guard
    return render_template('calendar.html', user=current_user(), page_title='Calendario')

def _sb_get(path, token, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    h = _sb_headers(token, is_json=False)
    return _session().get(url, headers=h, params=params or {})

def _sb_upsert(path, token, json_body, on_conflict=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    h = _sb_headers(token, is_json=True)
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
        h['Prefer'] = 'resolution=merge-duplicates'
    return _session().post(url, headers=h, json=json_body)

def _sb_delete(path, token, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    h = _sb_headers(token, is_json=False)
    h['Prefer'] = 'return=representation'
    return _session().delete(url, headers=h, params=params or {}, timeout=20)


@app.get('/calendar/users')
@login_required
def calendar_users():
    if 'sb_token' not in session:
        return redirect(url_for('login', next=request.full_path))
    token = session.get('sb_token')
    me = current_user() or {}
    my_id = me.get('uid') or me.get('id')
    my_role = (me.get('role') or 'user').lower()

    users = []
    if my_id:
        users.append({
            'id': my_id,
            'name': me.get('name') or me.get('email'),
            'email': me.get('email'),
            'role': my_role,
            'is_self': True
        })

    try:
        if my_role == 'admin':
            url = f"{SUPABASE_URL}/rest/v1/profiles"
            params = {'select':'id,name,email,role','order':'email.asc'}
            r = _session().get(url, headers=_sb_headers(token, is_json=False), params=params, timeout=20)
            if r.ok:
                arr = r.json() or []
                for u in arr:
                    if u.get('id') == my_id:
                        u['is_self'] = True
                users = arr
        elif my_role == 'supervisor':
            # 1) Tentativo via RPC SECURITY DEFINER
            try:
                rpc_url = f"{SUPABASE_URL}/rest/v1/rpc/assigned_profiles"
                r_rpc = _session().post(rpc_url, headers=_sb_headers(token, is_json=True), json={}, timeout=20)
            except Exception:
                r_rpc = None

            if r_rpc is not None and r_rpc.ok:
                assigned = r_rpc.json() or []
                seen = {u['id'] for u in users if 'id' in u}
                for a in assigned:
                    if a.get('id') and a['id'] not in seen:
                        a['is_self'] = (a['id'] == my_id)
                        users.append(a)
            else:
                # 2) Fallback: query assignments + filtro IN su profiles (UUID quotati)
                asg_url = f"{SUPABASE_URL}/rest/v1/supervisor_assignments"
                r_asg = _session().get(asg_url, headers=_sb_headers(token, is_json=False),
                                       params={'select':'user_id,can_edit','supervisor_id': f'eq.{my_id}'}, timeout=20)
                if r_asg.ok:
                    ids = [a.get('user_id') for a in (r_asg.json() or []) if a.get('user_id')]
                    if my_id and my_id not in ids:
                        ids.insert(0, my_id)
                    # dedup preservando ordine
                    ids = [i for i in dict.fromkeys(ids)]
                    if ids:
                        in_list = '(' + ','.join(f'"{i}"' for i in ids) + ')'
                        p_url = f"{SUPABASE_URL}/rest/v1/profiles"
                        r2 = _session().get(p_url, headers=_sb_headers(token, is_json=False),
                                            params={'select':'id,name,email,role','id':'in.'+in_list,'order':'email.asc'},
                                            timeout=20)
                        if r2.ok:
                            profs = r2.json() or []
                            # merge con self
                            pres = {u['id']: u for u in users if 'id' in u}
                            for p in profs:
                                p['is_self'] = (p.get('id') == my_id)
                                pres[p['id']] = p
                            users = list(pres.values())
    except Exception as e:
        app.logger.exception("calendar_users error: %s", e)

    if my_id:
        users.sort(key=lambda u: (0 if u.get('id') == my_id else 1, (u.get('email') or u.get('name') or '').lower()))
    return jsonify({'users': users})
   
@app.get('/calendar/user-perms')
@login_required
def calendar_user_perms():
    guard = _require_login()
    if guard: return guard
    token = session.get('sb_token')
    me = current_user() or {}
    my_id = me.get('uid') or me.get('id')
    my_role = (me.get('role') or 'user').lower()

    perms = {}

    try:
        if my_role == 'admin':
            # Admin: tutti EDIT
            r = _sb_get('profiles', token, params={'select': 'id'})
            if r.ok:
                for row in (r.json() or []):
                    uid = row.get('id')
                    if uid:
                        perms[uid] = 'edit'

        elif my_role == 'supervisor':
            # Supervisor: sé stesso EDIT, assegnati EDIT/VIEW in base a can_edit
            if my_id:
                perms[my_id] = 'edit'
            r_asg = _sb_get(
                'supervisor_assignments',
                token,
                params={'select': 'user_id,can_edit', 'supervisor_id': f'eq.{my_id}'}
            )
            if r_asg.ok:
                for row in (r_asg.json() or []):
                    uid = row.get('user_id')
                    if uid:
                        perms[uid] = 'edit' if row.get('can_edit') else 'view'

        else:
            # User: solo sé stesso EDIT
            if my_id:
                perms[my_id] = 'edit'

    except Exception as e:
        app.logger.exception("calendar_user_perms error: %s", e)

    return jsonify({'perms': perms})



@app.get('/api/calendar/positions')
@login_required
def api_positions_get():
    guard = _require_login()
    if guard: return guard
    token = session.get('sb_token')
    user_id = request.args.get('user_id')
    month = request.args.get('month')
    week_start = request.args.get('week_start')
    params = {'select':'user_id,day,pos1,pos2,pos3,pos1_color,pos2_color,pos3_color','user_id':'eq.'+user_id}
    if month:
        # between month first..last
        y, m = month.split('-'); y=int(y); m=int(m)
        start = _dt.date(y,m,1)
        if m==12: end = _dt.date(y+1,1,1) - _dt.timedelta(days=1)
        else: end = _dt.date(y,m+1,1) - _dt.timedelta(days=1)
        params['and'] = f'(day.gte.{start.isoformat()},day.lte.{end.isoformat()})'
    elif week_start:
        d0 = _dt.date.fromisoformat(week_start); d6 = d0 + _dt.timedelta(days=6)
        params['and'] = f'(day.gte.{d0.isoformat()},day.lte.{d6.isoformat()})'
    r = _sb_get('calendar_positions', token, params=params)
    rows = r.json() if r.ok else []
    return jsonify({'rows': rows}), (200 if r.ok else 500)

@app.put('/api/calendar/positions')
@login_required
def api_positions_put():
    guard = _require_login()
    if guard: return guard
    token = session.get('sb_token')
    body = request.get_json(force=True) or {}
    # upsert single row
    r = _sb_upsert('calendar_positions', token, json_body=[body], on_conflict='user_id,day')
    # Cascade delete dei ToDo se qualche posN viene svuotata
    try:
        if r.ok:
            uid = body.get('user_id')
            day = body.get('day')
            for n in (1,2,3):
                key = f'pos{n}'
                if key in body and (body.get(key) is None or str(body.get(key)).strip()==''):
                    _sb_delete('calendar_todos', token, params={
                        'user_id': f'eq.{uid}', 'day': f'eq.{day}', 'position_index': f'eq.{n}'
                    })
    except Exception as e:
        app.logger.warning(f'cascade delete todos failed: {e}')
    return jsonify({'ok': r.ok, 'detail': r.text}), (200 if r.ok else 400)
    
# ---- Todos ----
@app.get('/api/calendar/todos')
@login_required
def api_todos_get():
    guard = _require_login()
    if guard: return guard
    token = session.get('sb_token')
    user_id = request.args.get('user_id')
    day = request.args.get('day')
    position_index = request.args.get('position_index')
    week_start = request.args.get('week_start')

    if not user_id:
        return jsonify({'rows': []})

    params = {
        'select': 'user_id,day,position_index,todo_index,text,done,color',
        'user_id': f'eq.{user_id}'
    }

    # Day-specific query (optional position filter)
    if day:
        params['day'] = f'eq.{day}'
        if position_index is not None and position_index != "":
            try:
                pi = int(position_index)
                params['position_index'] = f'eq.{pi}'
            except Exception:
                return jsonify({'rows': []})
        r = _sb_get('calendar_todos', token, params=params)
        rows = r.json() if r.ok else []
        return jsonify({'rows': rows}), (200 if r.ok else 500)

    # Week range query
    if week_start:
        d0 = _dt.date.fromisoformat(week_start); d6 = d0 + _dt.timedelta(days=6)
        params['and'] = f'(day.gte.{d0.isoformat()},day.lte.{d6.isoformat()})'
        r = _sb_get('calendar_todos', token, params=params)
        rows = r.json() if r.ok else []
        return jsonify({'rows': rows}), (200 if r.ok else 500)

    # Fallback
    return jsonify({'rows': []})
@app.put('/api/calendar/todos')
@login_required
def api_todos_put():
    guard = _require_login()
    if guard: return guard
    token = session.get('sb_token')
    body = request.get_json(force=True) or {}
    r = _sb_upsert('calendar_todos', token, json_body=[body], on_conflict='user_id,day,position_index,todo_index')
    return jsonify({'ok': r.ok, 'detail': r.text}), (200 if r.ok else 400)

@app.delete('/api/calendar/todos')
@login_required
def api_todos_delete():
    guard = _require_login()
    if guard: return guard
    token = session.get('sb_token')
    data = request.get_json(silent=True) or request.args
    user_id = data.get('user_id')
    day = data.get('day')
    position_index = data.get('position_index')
    todo_index = data.get('todo_index')  # opzionale

    if not user_id or not day or position_index is None:
        return jsonify({'ok': False, 'detail': 'Missing user_id/day/position_index'}), 400
    try:
        pos_idx = int(position_index)
    except Exception:
        return jsonify({'ok': False, 'detail': 'position_index must be int'}), 400

    params = {
        'user_id': f'eq.{user_id}',
        'day': f'eq.{day}',
        'position_index': f'eq.{pos_idx}',
    }
    if todo_index is not None and str(todo_index) != '':
        try:
            params['todo_index'] = f'eq.{int(todo_index)}'
        except Exception:
            return jsonify({'ok': False, 'detail': 'todo_index must be int'}), 400

    r = _sb_delete('calendar_todos', token, params=params)
    return jsonify({'ok': r.ok, 'detail': r.text}), (200 if r.ok else 400)

# ---- Admin: supervisor assignments ----
# ---- Admin: supervisor assignments ----
@app.get('/admin/supervisors')
@login_required
@admin_required
def admin_supervisors():
    """DEPRECATO: le assegnazioni store sono gestite dentro la scheda utente."""
    flash("La gestione assegnazioni store è stata spostata nella modifica utente.", "info")
    return redirect(url_for("admin_users"))

@app.post('/admin/supervisors/assign')
@login_required
@admin_required
def admin_supervisors_assign():
    flash("Funzione spostata: modifica l'utente e seleziona gli store.", "info")
    return redirect(url_for("admin_users"))

@app.post('/admin/supervisors/remove')
@login_required
@admin_required
def admin_supervisors_remove():
    flash("Funzione spostata: modifica l'utente e seleziona gli store.", "info")
    return redirect(url_for("admin_users"))

@app.get('/api/todo_catalog')
@login_required
def api_todo_catalog():
    token = session.get('sb_token')
    try:
        r = _sb_get('todo_catalog', token, params={'select':'name,active','order':'name.asc'})
        rows = r.json() if getattr(r, 'ok', False) else []
        names = [ (row.get('name') or '').strip() for row in rows if (row.get('active') is None) or bool(row.get('active')) ]
        names = [n for n in names if n]
        names.sort(key=lambda s: s.lower())
        return jsonify({'names': names, 'count': len(names)})
    except Exception as e:
        current_app.logger.exception('todo_catalog exception')
        return abort(500, str(e))






@app.get("/api/place_catalog_diag")
@login_required
def api_place_catalog_diag():
    try:
        r = _sb_get("place_catalog", {"select":"name,active","order":"name.asc"})
        info = {}
        payload = None

        if hasattr(r, "ok"):
            info["response_type"] = type(r).__name__
            info["ok"] = getattr(r, "ok", None)
            info["status_code"] = getattr(r, "status_code", None)
            if not r.ok:
                return jsonify({"error":"_sb_get not ok", "text":getattr(r,"text",None), "info":info}), 500
            payload = r.json() if hasattr(r, "json") else None
            info["payload_type"] = type(payload).__name__ if payload is not None else None
        else:
            info["response_type"] = type(r).__name__
            payload = r

        rows = []
        if isinstance(payload, list):
            rows = payload
            info["rows_source"] = "list"
        elif isinstance(payload, dict):
            for key in ("data","result","rows"):
                val = payload.get(key)
                if isinstance(val, list):
                    rows = val
                    info["rows_source"] = key
                    break

        preview = rows[:5] if isinstance(rows, list) else None
        names = [ (row.get("name") or "").strip() for row in (rows or []) if (row.get("active") is None) or bool(row.get("active")) ]
        return jsonify({"info":info, "names_count":len(names), "preview":preview})
    except Exception as e:
        return jsonify({"exception": str(e)}), 500



# === BEGIN place_chat header (appendice) ===
try:
    login_required  # type: ignore  # noqa
except Exception:
    def login_required(f):
        return f
# === END place_chat header (appendice) ===

# === BEGIN place_chat GET (appendice) ===





@app.get('/api/place_chat')
def api_place_chat():
    thread_key = request.args.get('thread_key', '').strip()
    if not thread_key:
        return abort(400, 'thread_key mancante')
    since = request.args.get('since', '').strip()
    limit = request.args.get('limit', '').strip() or '200'
    try:
        url = os.getenv('SUPABASE_URL'); key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        if not url or not key:
            return abort(500, 'SUPABASE env mancanti')
        endpoint = url.rstrip('/') + '/rest/v1/place_chat_messages'
        params = {'select': 'id,author_id,author_name,message,created_at','thread_key': f'eq.{thread_key}','order': 'created_at.asc','limit': limit}
        if since: params['created_at'] = f'gt.{since}'
        hdr = {'apikey': key,'Authorization':'Bearer '+key,'Accept':'application/json'}
        r = requests.get(endpoint, params=params, headers=hdr, timeout=10)
        if r.status_code != 200: return abort(500, f'chat GET {r.status_code}: {r.text}')
        rows = r.json() or []
        return jsonify({'messages': rows, 'count': len(rows)})
    except Exception as e:
        return abort(500, str(e))



@app.post('/api/place_chat')
def api_place_chat_post():
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return abort(400, 'JSON non valido')
    msg = (payload.get('message') or '').strip()
    if not msg: return abort(400, 'message mancante')
    thread_key = (payload.get('thread_key') or '').strip()
    day_label = (payload.get('day_label') or '').strip()
    pos_index = payload.get('pos_index')
    place_name = (payload.get('place_name') or '').strip()
    if not thread_key or not day_label or pos_index is None:
        return abort(400, 'metadati chat mancanti')
    author_id = request.headers.get('X-User-Id') or ''
    author_name = request.headers.get('X-User-Name') or 'utente'
    try:
        url = os.getenv('SUPABASE_URL'); key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        if not url or not key: return abort(500, 'SUPABASE env mancanti')
        endpoint = url.rstrip('/') + '/rest/v1/place_chat_messages'
        hdr = {'apikey': key,'Authorization':'Bearer '+key,'Accept':'application/json','Content-Type':'application/json'}
        row={'thread_key':thread_key,'day_label':day_label,'pos_index':int(pos_index),'place_name':place_name,'author_id':str(author_id),'author_name':str(author_name),'message':msg}
        r = requests.post(endpoint, headers=hdr, json=row, timeout=10)
        if r.status_code not in (200,201): return abort(500, f'chat POST {r.status_code}: {r.text}')
        return jsonify({'ok': True})
    except Exception as e:
        return abort(500, str(e))


@app.post('/api_place_chat')  # retrocompat opzionale
def api_place_chat_post_legacy():
    return api_place_chat_post()



@app.delete('/api/place_chat')
def api_place_chat_delete():
    thread_key = request.args.get('thread_key', '').strip()
    if not thread_key: return abort(400, 'thread_key mancante')
    try:
        url = os.getenv('SUPABASE_URL'); key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        if not url or not key: return abort(500, 'SUPABASE env mancanti')
        endpoint = url.rstrip('/') + '/rest/v1/place_chat_messages'
        hdr = {'apikey': key,'Authorization':'Bearer '+key,'Accept':'application/json'}
        r = requests.delete(endpoint, params={'thread_key': f'eq.{thread_key}'}, headers=hdr, timeout=10)
        if r.status_code not in (200,204): return abort(500, f'chat DELETE {r.status_code}: {r.text}')
        return jsonify({'ok': True})
    except Exception as e:
        return abort(500, str(e))


@app.delete('/api_place_chat')  # retrocompat opzionale
def api_place_chat_delete_legacy():
    return api_place_chat_delete()











# === BEGIN day_chat API (appendice) v3 (scoped by calendar) ===
try:
    login_required  # type: ignore  # noqa: F821
except Exception:
    def login_required(f):
        return f

@app.get('/api/day_chat')
@login_required
def api_day_chat_get():
    day_label = (request.args.get('day_label') or '').strip()
    calendar_user_id = (request.args.get('calendar_user_id') or '').strip()
    if not day_label: return abort(400, 'day_label mancante')
    if not calendar_user_id: return abort(400, 'calendar_user_id mancante')
    limit = (request.args.get('limit') or '200').strip()
    url, key = os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    if not url or not key: return abort(500, 'SUPABASE env mancanti')
    r = requests.get(url.rstrip('/') + '/rest/v1/day_chat_messages',
                     params={'select':'id,author_id,author_name,message,created_at',
                             'day_label':f'eq.{day_label}',
                             'calendar_user_id':f'eq.{calendar_user_id}',
                             'order':'created_at.asc','limit':limit},
                     headers={'apikey':key,'Authorization':'Bearer '+key,'Accept':'application/json'},
                     timeout=10)
    if r.status_code!=200: return abort(500, f'day_chat GET {r.status_code}: {r.text}')
    rows = r.json() or []
    return jsonify({'messages': rows, 'count': len(rows)})

@app.post('/api/day_chat')
@login_required
def api_day_chat_post():
    try:
        p = request.get_json(force=True) or {}
    except Exception:
        return abort(400, 'JSON non valido')
    msg = (p.get('message') or '').strip()
    day_label = (p.get('day_label') or '').strip()
    cal_id = (p.get('calendar_user_id') or '').strip()
    a_id = (p.get('author_id') or '').strip()
    a_name = (p.get('author_name') or '').strip()

    try:
        from flask_login import current_user as cu  # type: ignore
    except Exception:
        cu = None

    if not a_id:
        a_id = (request.headers.get('X-User-Id') or str(getattr(cu,'uid','') or getattr(cu,'id',''))).strip()
    if not a_name:
        a_name = (request.headers.get('X-User-Name') or getattr(cu,'name',None) or getattr(cu,'email',None) or 'utente')

    if not msg: return abort(400, 'message mancante')
    if not day_label: return abort(400, 'day_label mancante')
    if not cal_id:
        cal_id = (request.headers.get('X-Calendar-User-Id') or request.headers.get('X-User-Id') or str(getattr(cu,'uid','') or getattr(cu,'id','')) or '').strip()
    if not cal_id: return abort(400, 'calendar_user_id mancante')

    url, key = os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    if not url or not key: return abort(500, 'SUPABASE env mancanti')
    r = requests.post(url.rstrip('/') + '/rest/v1/day_chat_messages',
                      headers={'apikey':key,'Authorization':'Bearer '+key,'Accept':'application/json','Content-Type':'application/json'},
                      json={'day_label':day_label,'calendar_user_id':cal_id,'author_id':a_id,'author_name':a_name,'message':msg},
                      timeout=10)
    if r.status_code not in (200,201): return abort(500, f'day_chat POST {r.status_code}: {r.text}')
    return jsonify({'ok': True})
# === END day_chat API (appendice) v3 (scoped by calendar) ===

# --- BEGIN user_chat API (appendice) ---
import os as _uc_os, requests as _uc_req
from flask import request as _uc_request, jsonify as _uc_jsonify, abort as _uc_abort, current_app as _uc_app

def _uc_env(name):
    try:
        v = _uc_app.config.get(name)
        if v: return v
    except Exception:
        pass
    return _uc_os.environ.get(name)

def _uc_headers():
    try:
        return _sb_headers()  # type: ignore[name-defined]
    except Exception:
        pass
    sb_key = (_uc_env('SUPABASE_SERVICE_KEY') or _uc_env('SUPABASE_KEY'))
    if not sb_key:
        _uc_abort(500, 'supabase_not_configured')
    return {'apikey': sb_key, 'Authorization': f'Bearer {sb_key}', 'Content-Type': 'application/json'}

def _uc_table(name: str) -> str:
    try:
        return _sb_table(name)  # type: ignore[name-defined]
    except Exception:
        pass
    base = (_uc_env('SUPABASE_URL') or '').rstrip('/')
    if not base:
        _uc_abort(500, 'supabase_not_configured')
    return f"{base}/rest/v1/{name}"

@app.route('/api/user_chat', methods=['GET'])
def uc_get():
    try:
        user_id = _uc_request.args.get('user_id','').strip()
        day_label = _uc_request.args.get('day_label','').strip()
        limit = int(_uc_request.args.get('limit', '200'))
        if not user_id or not day_label:
            return _uc_abort(400, 'missing user_id/day_label')
        params = {
            'select': '*',
            'user_id': f'eq.{user_id}',
            'day_label': f'eq.{day_label}',
            'order': 'created_at.asc',
            'limit': str(limit)
        }
        r = _uc_req.get(_uc_table('user_chat_messages'), headers=_uc_headers(), params=params, timeout=10)
        if r.status_code != 200:
            return _uc_abort(500, f'GET {r.status_code}: {r.text}')
        rows = r.json()
        return _uc_jsonify({'messages': rows, 'count': len(rows)})
    except Exception as e:
        return _uc_abort(500, str(e))

@app.route('/api/user_chat', methods=['POST'])
def uc_post():
    try:
        data = _uc_request.get_json(force=True, silent=True) or {}
        user_id = (data.get('user_id') or '').strip()
        day_label = (data.get('day_label') or '').strip()
        message = (data.get('message') or '').strip()
        author_id = (data.get('author_id') or '').strip()
        author_name = (data.get('author_name') or '').strip()
        if not user_id or not day_label or not message:
            return _uc_abort(400, 'missing user_id/day_label/message')
        row = {
            'user_id': user_id,
            'day_label': day_label,
            'message': message,
            'author_id': author_id or None,
            'author_name': author_name or None,
        }
        r = _uc_req.post(_uc_table('user_chat_messages'), headers=_uc_headers(), json=row, timeout=10)
        if r.status_code not in (200,201):
            return _uc_abort(500, f'POST {r.status_code}: {r.text}')
        return _uc_jsonify({'ok': True, 'inserted': row})
    except Exception as e:
        return _uc_abort(500, str(e))

@app.route('/api/day_places', methods=['GET'])
def uc_day_places():
    try:
        user_id = _uc_request.args.get('user_id','').strip()
        day_label = _uc_request.args.get('day_label','').strip()
        return _uc_jsonify({'places': [], 'count': 0})
    except Exception as e:
        return _uc_abort(500, str(e))
# --- END user_chat API (appendice) ---


# --- BEGIN place_chat_v3 API (appendice) ---
import os as _pc_os, requests as _pc_req
from flask import request as _pc_reqq, jsonify as _pc_jsonify, abort as _pc_abort, current_app as _pc_app

def _pc_env(name):
    try:
        v = _pc_app.config.get(name)
        if v: return v
    except Exception:
        pass
    return _pc_os.environ.get(name)

def _pc_headers():
    try:
        return _sb_headers()  # type: ignore[name-defined]
    except Exception:
        pass
    key = (_pc_env('SUPABASE_SERVICE_KEY') or _pc_env('SUPABASE_KEY'))
    if not key:
        _pc_abort(500, 'supabase_not_configured')
    return {'apikey': key, 'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}

def _pc_table(t):
    try:
        return _sb_table(t)  # type: ignore[name-defined]
    except Exception:
        pass
    base = (_pc_env('SUPABASE_URL') or '').rstrip('/')
    if not base:
        _pc_abort(500, 'supabase_not_configured')
    return f"{base}/rest/v1/{t}"

TABLE = 'place_chat_messages_v3'

@app.route('/api/place_chat', methods=['GET'])
def place_chat_get():
    try:
        cal = (_pc_reqq.args.get('calendar_user_id') or '').strip()
        day = (_pc_reqq.args.get('day_label') or '').strip()
        pos = (_pc_reqq.args.get('pos_index') or '').strip()
        limit = int(_pc_reqq.args.get('limit') or '300')
        if not cal or not day or not pos:
            return _pc_abort(400, 'missing calendar_user_id/day_label/pos_index')
        params = {
            'select':'*',
            'calendar_user_id': f"eq.{cal}",
            'day_label': f"eq.{day}",
            'pos_index': f"eq.{pos}",
            'order': 'created_at.asc',
            'limit': str(limit),
        }
        r = _pc_req.get(_pc_table(TABLE), headers=_pc_headers(), params=params, timeout=10)
        if r.status_code != 200:
            return _pc_abort(500, f"GET {r.status_code}: {r.text}")
        rows = r.json()
        return _pc_jsonify({'messages': rows, 'count': len(rows)})
    except Exception as e:
        return _pc_abort(500, str(e))

@app.route('/api/place_chat', methods=['POST'])
def place_chat_post():
    try:
        body = _pc_reqq.get_json(force=True, silent=True) or {}
        cal = (body.get('calendar_user_id') or '').strip()
        day = (body.get('day_label') or '').strip()
        pos = body.get('pos_index')
        message = (body.get('message') or '').strip()
        if not cal or not day or message == '' or pos is None:
            return _pc_abort(400, 'missing calendar_user_id/day_label/pos_index/message')
        row = {
            'calendar_user_id': cal,
            'day_label': day,
            'pos_index': int(pos),
            'place_name': (body.get('place_name') or None),
            'author_id': (body.get('author_id') or None),
            'author_name': (body.get('author_name') or None),
            'message': message,
        }
        r = _pc_req.post(_pc_table(TABLE), headers=_pc_headers(), json=row, timeout=10)
        if r.status_code not in (200, 201):
            return _pc_abort(500, f"POST {r.status_code}: {r.text}")
        return _pc_jsonify({'ok': True, 'inserted': row})
    except Exception as e:
        return _pc_abort(500, str(e))
# --- END place_chat_v3 API (appendice) ---


@app.get('/statistiche')
@login_required
def stats_page():
    return render_template('statistiche.html')



@app.get('/api/stats/places')
@login_required
def api_stats_places():
    from stats_config import TABLE, COL_DATE, COL_USER, COL_PLACES, PLACES_ARE_CSV
    token = session.get('sb_token')
    start = request.args.get('start') or ''
    end = request.args.get('end') or ''
    user_id = (request.args.get('user_id') or '').strip()
    params = {}
    if start: params[COL_DATE] = f"gte.{start}"
    if end: params[COL_DATE] = f"lt.{end}"
    if user_id: params[COL_USER] = f"eq.{user_id}"
    params['select'] = f"{COL_DATE},{COL_PLACES}"
    params['limit'] = 5000
    r = _sb_get(TABLE, token, params=params)
    rows = r.json() if getattr(r,'ok',False) else []
    out = []
    for it in rows:
        d = it.get(COL_DATE)
        raw = it.get(COL_PLACES)
        places = []
        if isinstance(raw, list):
            places = [str(x).strip() for x in raw if x]
        elif isinstance(raw, str):
            if PLACES_ARE_CSV:
                places = [x.strip() for x in raw.split(',') if x.strip()]
            else:
                try:
                    arr = json.loads(raw)
                    if isinstance(arr, list): places = [str(x).strip() for x in arr if x]
                except Exception:
                    pass
        out.append({'day_label': d, 'places': places})
    return jsonify({'rows': out, 'count': len(out)})



# === Appendix: To Do List page route ===
try:
    from flask import render_template
    @app.route("/todo-list")
    def todo_list_page():
        return render_template("todo_list.html")
except Exception as _e:
    print("[warn] To Do List route not attached:", _e)


# === Appendix: Personal To Do APIs ========================================
@app.get('/api/personal_todos')
@login_required
def api_personal_todos_get():
    guard = _require_login()
    if guard: return guard
    token = session.get('sb_token')
    user_id = request.args.get('user_id') or (g.user and (g.user.get('uid') or g.user.get('id')))
    day = request.args.get('day')
    if not user_id or not day:
        return jsonify({'rows': []})
    params = {
        'select': 'id,user_id,day,text,color,done,created_at,updated_at',
        'user_id': f'eq.{user_id}',
        'day': f'eq.{day}',
        'order': 'created_at.asc'
    }
    r = _sb_get('personal_todos', token, params=params)
    if r.status_code != 200:
        return jsonify({'rows': []}), 200
    return jsonify({'rows': r.json() or []})

@app.post('/api/personal_todos')
@login_required
def api_personal_todos_upsert():
    guard = _require_login()
    if guard: return guard
    token = session.get('sb_token')
    body = request.get_json(force=True) or {}
    # If new insert requires user_id and day
    # Upsert by id if provided, else insert new
    rows = [body]
    r = _sb_upsert('personal_todos', token, json_body=rows, on_conflict='id')
    return jsonify({'ok': r.ok, 'detail': r.text}), (200 if r.ok else 400)

@app.delete('/api/personal_todos')
@login_required
def api_personal_todos_delete():
    guard = _require_login()
    if guard: return guard
    token = session.get('sb_token')
    data = request.get_json(silent=True) or request.args
    id_ = data.get('id')
    if not id_:
        return jsonify({'ok': False, 'error': 'missing id'}), 400
    r = _sb_delete('personal_todos', token, params={'id': f'eq.{id_}'})
    return jsonify({'ok': r.ok, 'detail': r.text}), (200 if r.ok else 400)


def _sb_patch(path, token, params=None, json_body=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    h = _sb_headers(token, is_json=True)
    h['Prefer'] = 'return=representation'
    return _session().patch(url, headers=h, params=params or {}, json=json_body or {}, timeout=20)


@app.patch('/api/personal_todos')
@login_required
def api_personal_todos_patch():
    guard = _require_login()
    if guard: return guard
    token = session.get('sb_token')
    body = request.get_json(force=True) or {}
    id_ = body.get('id')
    if not id_:
        return jsonify({'ok': False, 'error': 'missing id'}), 400
    # Only allow updating a single row by id
    params = {'id': f'eq.{id_}'}
    # Only pass allowed fields
    payload = {}
    for k in ('done','text','color'):
        if k in body: payload[k] = body[k]
    if not payload:
        return jsonify({'ok': False, 'error': 'no changes'}), 400
    r = _sb_patch('personal_todos', token, params=params, json_body=payload)
    return jsonify({'ok': r.ok, 'detail': r.text}), (200 if r.ok else 400)


# === Appendix: Outlook sync (Microsoft Graph) — FIX UID SAFE ==============
# Incolla questo blocco IN CODA a app.py sostituendo la versione precedente.
# Non assume g.user; usa _current_uid() con fallback su sessione.

import os, base64
from datetime import datetime, timedelta, timezone
import requests
from flask import request, session, redirect, jsonify, render_template, g

# --- Config ---
MS_TENANT = os.getenv('MS_TENANT', 'common')
MS_CLIENT_ID = os.getenv('MS_CLIENT_ID', '')
MS_CLIENT_SECRET = os.getenv('MS_CLIENT_SECRET', '')
MS_REDIRECT_URI = os.getenv('MS_REDIRECT_URI', 'http://localhost:5000/ms/callback')
MS_SCOPES = os.getenv('MS_SCOPES', 'offline_access Calendars.ReadWrite')
APP_TZ = os.getenv('APP_TIMEZONE', 'Europe/Rome')

MS_AUTH = f"https://login.microsoftonline.com/{MS_TENANT}/oauth2/v2.0/authorize"
MS_TOKEN = f"https://login.microsoftonline.com/{MS_TENANT}/oauth2/v2.0/token"
MS_API = "https://graph.microsoft.com/v1.0"

def _ms_headers(tok):
    return {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}

# --- Helper utente robusto ---
def _current_uid():
    # 1) g.user come dict o oggetto
    u = getattr(g, 'user', None)
    if u is not None:
        if isinstance(u, dict):
            for k in ('uid','id','user_id'):
                if u.get(k):
                    return u.get(k)
        else:
            for k in ('uid','id','user_id'):
                val = getattr(u, k, None)
                if val:
                    return val
    # 2) sessione diretta
    for k in ('user_id','uid','id'):
        if k in session and session.get(k):
            return session.get(k)
    # 3) sessione annidata
    for parent in ('user','profile','auth'):
        obj = session.get(parent)
        if isinstance(obj, dict):
            for k in ('uid','id','user_id'):
                if obj.get(k):
                    return obj.get(k)
    return None

# --- OAuth start ---
@app.route('/ms/login')
@login_required
def ms_login():
    guard = _require_login()
    if guard: return guard
    uid = _current_uid()
    if not uid:
        return redirect('/login?next=/sync-outlook')
    session['pending_uid'] = uid

    state = base64.urlsafe_b64encode(os.urandom(24)).decode()
    session['ms_state'] = state
    nxt = request.args.get('next') or '/sync-outlook'
    session['ms_next'] = nxt
    # Permette di richiedere scopes diversi (es. Files/Sites per SharePoint test)
    req_scopes = (request.args.get('scopes') or '').strip()
    if req_scopes:
        session['ms_scopes'] = req_scopes
    scopes = session.get('ms_scopes') or MS_SCOPES
    params = {
        'client_id': MS_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': MS_REDIRECT_URI,
        'response_mode': 'query',
        'scope': scopes,
        'state': state,
        'prompt': 'select_account'
    }
    return redirect(f"{MS_AUTH}?"+requests.compat.urlencode(params))

# --- OAuth callback: NIENTE login_required ---
@app.route('/ms/callback')
def ms_callback():
    st = request.args.get('state')
    if not st or st != session.get('ms_state'):
        return "Invalid state", 400
    code = request.args.get('code')
    if not code:
        return "Missing code", 400

    scopes = session.get('ms_scopes') or MS_SCOPES
    data = {
        'client_id': MS_CLIENT_ID,
        'client_secret': MS_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': MS_REDIRECT_URI,
        'scope': scopes
    }
    tok = requests.post(MS_TOKEN, data=data, timeout=30)
    if not tok.ok:
        return f"Token error: {tok.text}", 400
    js = tok.json()
    access_token = js.get('access_token')
    refresh_token = js.get('refresh_token')
    expires_in = js.get('expires_in', 3600)
    exp = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)-60)

    uid = session.get('pending_uid') or _current_uid()
    if not uid:
        return "No bound user for this OAuth session", 400

    if not access_token:
        return "Token error: access_token missing", 400

    sb_token = session.get('sb_token')

    row = {
        'user_id': uid,
        'provider': 'microsoft',
        'access_token': access_token,
        'expires_at': exp.isoformat()
    }
    # Evita di sovrascrivere il refresh_token con NULL (può succedere su alcuni flussi OAuth)
    if refresh_token:
        row['refresh_token'] = refresh_token
    body = [row]

    # Salvataggio token: prova con JWT utente (RLS). Se mancante o bloccato, fallback con Service Role.
    save_ok = False
    try:
        r_up = _sb_upsert('ms_tokens', sb_token, json_body=body, on_conflict='user_id,provider')
        save_ok = r_up.status_code in (200, 201, 204)
    except Exception as e:
        logging.warning("ms_callback: upsert ms_tokens fallito (user jwt): %s", e)
        save_ok = False

    if (not save_ok) and SUPABASE_SERVICE_ROLE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/ms_tokens?on_conflict=user_id,provider"
            h = _sb_admin_headers(is_json=True)
            h['Prefer'] = 'resolution=merge-duplicates'
            r2 = _session().post(url, headers=h, json=body, timeout=20)
            save_ok = r2.status_code in (200, 201, 204)
        except Exception as e:
            logging.warning("ms_callback: upsert ms_tokens fallito (service role): %s", e)
            save_ok = False

    if not save_ok:
        flash("Impossibile salvare il token Microsoft. Riprova (E01).", "danger")

    session.pop('pending_uid', None)
    session.pop('ms_scopes', None)
    session.pop('ms_state', None)
    nxt = session.get('ms_next') or '/sync-outlook'
    return redirect(nxt)

# --- Token loader con refresh ---
def _ms_load_token():
    sb_token = session.get('sb_token')
    uid = _current_uid()
    if not uid:
        return None
    r = _sb_get('ms_tokens', sb_token, params={
        'select':'access_token,refresh_token,expires_at',
        'user_id':f'eq.{uid}','provider':'eq.microsoft'
    })
    if r.status_code!=200 or not r.json():
        return None
    row = r.json()[0]
    try:
        exp = datetime.fromisoformat(row['expires_at'].replace('Z','+00:00'))
    except Exception:
        exp = datetime.now(timezone.utc) - timedelta(seconds=1)
    if exp <= datetime.now(timezone.utc):
        data = {
            'client_id': MS_CLIENT_ID,
            'client_secret': MS_CLIENT_SECRET,
            'grant_type': 'refresh_token',
            'refresh_token': row.get('refresh_token'),
            'scope': MS_SCOPES,
            'redirect_uri': MS_REDIRECT_URI
        }
        tok = requests.post(MS_TOKEN, data=data, timeout=30)
        if not tok.ok:
            return None
        js = tok.json()
        access_token = js.get('access_token')
        refresh_token = js.get('refresh_token') or row.get('refresh_token')
        expires_in = js.get('expires_in', 3600)
        exp = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)-60)
        body = [{
            'user_id': uid,
            'provider': 'microsoft',
            'access_token': access_token,
            'refresh_token': refresh_token,
            'expires_at': exp.isoformat()
        }]
        _sb_upsert('ms_tokens', sb_token, json_body=body, on_conflict='user_id,provider')
        return access_token
    return row['access_token']

# --- Pagina Sync ---
@app.get('/sync-outlook')
@login_required
def sync_outlook_page():
    return render_template('sync_outlook.html')

@app.get('/api/ms/status')
@login_required
def api_ms_status():
    guard = _require_login()
    if guard: return guard
    tok = _ms_load_token()
    return jsonify({'connected': bool(tok)})

# --- Utilità date ---
def _month_window(month_ym):
    y,m = map(int, month_ym.split('-'))
    start = datetime(y,m,1,0,0,0, tzinfo=timezone.utc)
    if m==12: nxt = datetime(y+1,1,1,0,0,0,tzinfo=timezone.utc)
    else:     nxt = datetime(y,m+1,1,0,0,0,tzinfo=timezone.utc)
    end = nxt - timedelta(seconds=1)
    return start, end

# --- Preview ---

# --- Helper: espansione eventi all‑day multi‑giorno ---
def _expand_allday_days(start_dt_str, end_dt_str):
    # Espande un evento all‑day Graph su tutti i giorni [start, end).
    # Accetta datetime ISO; end è esclusivo (Graph usa fine = giorno successivo 00:00).
    from datetime import datetime, timedelta
    def _parse_date(s):
        try:
            s2 = s.replace('Z','+00:00')
            return datetime.fromisoformat(s2).date()
        except Exception:
            return datetime.fromisoformat((s[:10] if len(s)>=10 else s) + 'T00:00:00').date()
    try:
        sdate = _parse_date(start_dt_str)
        edate = _parse_date(end_dt_str)
    except Exception:
        head = (start_dt_str[:10] if start_dt_str else '')
        return [head] if head else []
    days = []
    cur = sdate
    while cur < edate:
        days.append(cur.isoformat())
        cur = cur + timedelta(days=1)
    if not days:
        days.append(sdate.isoformat())
    return days



@app.get('/api/ms/sync/preview')
@login_required
def api_ms_sync_preview():
    guard = _require_login()
    if guard: return guard
    tok = _ms_load_token()
    if not tok: return jsonify({'error':'not_connected'}), 400
    month = request.args.get('month')
    if not month: return jsonify({'error':'month_required'}), 400

    start, end = _month_window(month)
    hdr = _ms_headers(tok); hdr['Prefer'] = f'outlook.timezone="{APP_TZ}"'
    params = {'startDateTime': start.isoformat(), 'endDateTime': end.isoformat(), '$select':'subject,start,end,isAllDay,id'}
    gr = requests.get(f"{MS_API}/me/calendarView", headers=hdr, params=params, timeout=30)
    if not gr.ok: return jsonify({'error':'graph_failed','detail':gr.text}), 400

    events = [e for e in gr.json().get('value',[]) if e.get('isAllDay')]
    remote = {}
    for e in events:
        subject = (e.get('subject') or '').strip()
        sdt = (e.get('start') or {}).get('dateTime') or ''
        edt = (e.get('end') or {}).get('dateTime') or sdt
        for day in _expand_allday_days(sdt, edt):
            if day < start.date().isoformat() or day > end.date().isoformat():
                continue
            remote.setdefault(day, set()).add(subject)

    uid = _current_uid()
    sbt = session.get('sb_token')
    rr = _sb_get('calendar_positions', sbt, params={
        'select':'day,pos1,pos2,pos3','user_id':f'eq.{uid}','day':f'gte.{start.date().isoformat()}'
    })
    local = {}
    if getattr(rr, 'status_code', 500) == 200:
        for row in rr.json():
            d = row['day']
            if d < start.date().isoformat() or d > end.date().isoformat():
                continue
            for k in ('pos1','pos2','pos3'):
                v = row.get(k)
                if v:
                    local.setdefault(d, set()).add((v or '').strip())

    days = sorted(set(local.keys()) | set(remote.keys()))
    out = {'days':{}}
    for d in days:
        L = local.get(d, set())
        R = remote.get(d, set())
        out['days'][d] = {
            'common': sorted(L & R, key=str.casefold),
            'create_remote': sorted(L - R, key=str.casefold),
            'create_local':  sorted(R - L, key=str.casefold),
            'delete_remote': sorted(R - L, key=str.casefold)
        }
    return jsonify(out)


@app.get('/api/ms/token_scopes')
@login_required
def api_ms_token_scopes():
    tok = _ms_load_token()
    if not tok: return jsonify({'connected': False}), 400
    parts = tok.split('.')
    scp = None; aud = None; tid = None; upn = None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + '==').decode())
        scp = payload.get('scp'); aud = payload.get('aud'); tid = payload.get('tid'); upn = payload.get('upn') or payload.get('preferred_username')
    except Exception as e:
        pass
    return jsonify({'connected': True, 'scp': scp, 'aud': aud, 'tenant': tid, 'user': upn})

@app.post('/api/ms/sync/apply')
@login_required
def api_ms_sync_apply():
    guard = _require_login()
    if guard:
        return guard
    tok = _ms_load_token()
    if not tok:
        return jsonify({'error':'not_connected'}), 400

    body = request.get_json(force=True) or {}
    cr = body.get('create_remote') or []   # [{day,text}]
    cl = body.get('create_local') or []    # [{day,text}]
    dr = body.get('delete_remote') or []   # [{day,text}]
    rrn = body.get('rename_remote') or []  # [{day,from_text,to_text}]

    # Build cache of existing all-day events by day+subject
    days = sorted({x.get('day') for x in (cr + dr + rrn) if x.get('day')})
    cache = {}
    if days:
        first = days[0]; last = days[-1]
        hdr = _ms_headers(tok)
        hdr['Prefer'] = f'outlook.timezone="{APP_TZ}"'
        params = {
            'startDateTime': f"{first}T00:00:00",
            'endDateTime': f"{last}T23:59:59",
            '$select': 'id,subject,start,end,isAllDay'
        }
        try:
            gr = requests.get(f"{MS_API}/me/calendarView", headers=hdr, params=params, timeout=30)
            if gr.ok:
                for ev in (gr.json() or {}).get('value', []):
                    if not ev.get('isAllDay'):
                        continue
                    day = ((ev.get('start') or {}).get('dateTime') or '')[:10]
                    subj = (ev.get('subject') or '').strip()
                    if day and subj:
                        cache.setdefault(day, {})[subj] = ev.get('id')
        except Exception as e:
            pass

    def _next_day(ymd):
        from datetime import datetime as dt, timedelta as td
        y, m, d = map(int, ymd.split('-'))
        return (dt(y, m, d) + td(days=1)).strftime('%Y-%m-%d')

    def _graph_create(day, subject):
        payload = {
            "subject": subject,
            "isAllDay": True,
            "start": {"dateTime": f"{day}T00:00:00", "timeZone": APP_TZ},
            "end":   {"dateTime": f"{_next_day(day)}T00:00:00", "timeZone": APP_TZ}
        }
        rr = requests.post(f"{MS_API}/me/events", headers=_ms_headers(tok), json=payload, timeout=30)
        if rr.status_code in (200, 201):
            ev_id = (rr.json() or {}).get('id')
            if ev_id:
                cache.setdefault(day, {})[subject] = ev_id
            return True
        return False

    def _graph_patch_subject(day, from_text, to_text):
        ev_id = cache.get(day, {}).get(from_text)
        if not ev_id:
            return False
        rr = requests.patch(f"{MS_API}/me/events/{ev_id}", headers=_ms_headers(tok), json={"subject": to_text}, timeout=30)
        if rr.status_code in (200,204):
            # update cache
            try:
                del cache[day][from_text]
            except Exception:
                pass
            cache.setdefault(day, {})[to_text] = ev_id
            return True
        return False

    def _graph_delete(day, subject):
        ev_id = cache.get(day, {}).get(subject)
        if not ev_id:
            return False
        rr = requests.delete(f"{MS_API}/me/events/{ev_id}", headers=_ms_headers(tok), timeout=30)
        return rr.status_code in (200,204)

    created_remote = 0
    renamed_remote = 0
    deleted_remote = 0
    created_local  = 0

    # 1) Create remote all-day events
    for x in cr:
        day = x.get('day'); subject = (x.get('text') or '').strip()
        if not day or not subject:
            continue
        if subject in cache.get(day, {}):
            # Already there, skip
            continue
        if _graph_create(day, subject):
            created_remote += 1

    # 2) Rename remote events
    for x in rrn:
        day = x.get('day'); f = (x.get('from_text') or '').strip(); t = (x.get('to_text') or '').strip()
        if not day or not f or not t or f == t:
            continue
        if _graph_patch_subject(day, f, t):
            renamed_remote += 1

    # 3) Delete remote events
    for x in dr:
        day = x.get('day'); subject = (x.get('text') or '').strip()
        if not day or not subject:
            continue
        if _graph_delete(day, subject):
            deleted_remote += 1

    # 4) Create local calendar positions for 'cl' with red color when slot is used
    try:
        uid = _current_uid()
    except Exception:
        uid = None
    sbt = session.get('sb_token')
    if uid and sbt and cl:
        days_set = sorted({x.get('day') for x in cl if x.get('day')})
        existing = {}
        if days_set:
            r0 = _sb_get('calendar_positions', sbt, params={'select': 'day,pos1,pos2,pos3', 'user_id': f'eq.{uid}', 'day': f'in.({",".join(days_set)})'})
            if r0.ok:
                for row in r0.json():
                    existing[row['day']] = [row.get('pos1') or '', row.get('pos2') or '', row.get('pos3') or '']
        for x in cl:
            day = x.get('day'); text = (x.get('text') or '').strip()
            if not day or not text:
                continue
            arr = existing.get(day, ['', '', ''])
            if text in arr:
                continue
            # append into first free slot
            for i in range(3):
                if not arr[i]:
                    arr[i] = text
                    break
            payload = [{
                'user_id': uid, 'day': day,
                'pos1': arr[0], 'pos2': arr[1], 'pos3': arr[2],
                'pos1_color': '#ef4444' if arr[0]==text else None,
                'pos2_color': '#ef4444' if arr[1]==text else None,
                'pos3_color': '#ef4444' if arr[2]==text else None,
            }]
            _sb_upsert('calendar_positions', sbt, json_body=payload, on_conflict='user_id,day')
            existing[day] = arr
            created_local += 1

    return jsonify({
        'ok': True,
        'message': f'Outlook creati: {created_remote}, rinominati: {renamed_remote}, cancellati: {deleted_remote}, Calendar creati: {created_local}'
    })
    sbt = session.get('sb_token')

    hdr = _ms_headers(tok); hdr['Prefer'] = f'outlook.timezone="{APP_TZ}"'

    # Build cache of remote all-day events for involved days
    days_all = sorted({x['day'] for x in (cr+cl+dr+rrn) if 'day' in x})
    cache = {}
    if days_all:
        start = min(days_all); end = max(days_all)
        gr = requests.get(f"{MS_API}/me/calendarView", headers=hdr, params={'startDateTime': f"{start}T00:00:00", 'endDateTime': f"{end}T23:59:59", '$select':'id,subject,start,end,isAllDay'}, timeout=30)
        if gr.ok:
            for e in gr.json().get('value',[]):
                if not e.get('isAllDay'): continue
                sd = (e.get('start') or {}).get('dateTime','')[:10]
                sub = (e.get('subject') or '').strip()
                cache.setdefault(sd, {})[sub] = e.get('id')

    def _graph_create(day, subject):
        payload = {"subject": subject, "isAllDay": True,
                   "start": {"dateTime": f"{day}T00:00:00", "timeZone": APP_TZ},
                   "end":   {"dateTime": f"{day}T00:00:00", "timeZone": APP_TZ}}
        r = requests.post(f"{MS_API}/me/events", headers=hdr, json=payload, timeout=30)
        return r.ok

    created_remote = 0; deleted_remote = 0; created_local = 0; renamed_remote = 0

    # 1) Create Outlook events for 'cr'
    for x in cr:
        if _graph_create(x['day'], x['text']): created_remote += 1

    # 2) Rename Outlook events per 'rrn'
    for x in rrn:
        day = x.get('day'); src = (x.get('from_text') or '').strip(); dst = (x.get('to_text') or '').strip()
        if not day or not src or not dst or src == dst: continue
        ev_id = cache.get(day,{}).get(src)
        if ev_id:
            pr = requests.patch(f"{MS_API}/me/events/{ev_id}", headers=hdr, json={'subject': dst}, timeout=30)
            if pr.status_code in (200,204):
                renamed_remote += 1
                # update cache so that later deletes use new key if needed
                try:
                    del cache[day][src]
                except Exception:
                    pass
                cache.setdefault(day, {})[dst] = ev_id

    # 3) Delete selected Outlook events in 'dr'
    for x in dr:
        ev_id = cache.get(x['day'],{}).get(x['text'])
        if ev_id:
            rr = requests.delete(f"{MS_API}/me/events/{ev_id}", headers=hdr, timeout=30)
            if rr.status_code in (200,204): deleted_remote += 1

    # 4) Create local places for 'cl' with red chip, respecting max 3 per day
    # Read existing positions for target days
    days_set = sorted({x['day'] for x in cl})
    existing = {}
    if days_set:
        rr = _sb_get('calendar_positions', sbt, params={'select':'day,pos1,pos2,pos3,user_id', 'user_id': f'eq.{uid}', 'day': f'in.({",".join(days_set)})'})
        if rr.status_code==200:
            for row in rr.json():
                existing[row['day']] = [row.get('pos1') or '', row.get('pos2') or '', row.get('pos3') or '']

    for x in cl:
        day = x['day']; text = x['text']
        arr = existing.get(day, ['', '', ''])
        if text in arr: continue
        if arr.count('') == 0:
            continue  # capacity exceeded; UI deve aver gestito
        for i in range(3):
            if not arr[i]:
                arr[i] = text
                break
        payload = [ {'user_id': uid, 'day': day, 'pos1': arr[0] or None, 'pos2': arr[1] or None, 'pos3': arr[2] or None,
                     'pos1_color': '#ef4444' if arr[0]==text else None,
                     'pos2_color': '#ef4444' if arr[1]==text else None,
                     'pos3_color': '#ef4444' if arr[2]==text else None } ]
        _sb_upsert('calendar_positions', sbt, json_body=payload, on_conflict='user_id,day')
        existing[day] = arr
        created_local += 1

    return jsonify({'ok': True, 'message': f'Outlook creati: {created_remote}, Outlook rinominati: {renamed_remote}, Outlook cancellati: {deleted_remote}, Calendar creati: {created_local}' })
    
    # === Appendix: Outlook Sync UID guard =====================================
# Scopo: evitare AttributeError: 'user' su g.user nelle route /api/ms/*
# Non modifica funzioni esistenti. Inietta un before_request che garantisce g.user.

import json, base64
from flask import g, session, request

def _sx_try_set_g_user_from_session() -> bool:
    # 1) Se già presente, ok
    if hasattr(g, 'user') and isinstance(getattr(g, 'user', None), dict):
        return True
    # 2) Prova da sessione applicativa
    cand = session.get('user') or session.get('profile') or session.get('auth_user') or {}
    if isinstance(cand, dict) and (cand.get('uid') or cand.get('id') or cand.get('user_id') or cand.get('email')):
        g.user = cand
        return True
    # 3) Prova dal JWT Supabase (sb_token) senza verifica firma: solo per leggere 'sub'
    sbt = session.get('sb_token')
    if isinstance(sbt, str) and '.' in sbt:
        try:
            payload_b64 = sbt.split('.')[1]
            # padding URL-safe
            pad = '=' * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad).decode('utf-8'))
            uid = payload.get('sub') or payload.get('user_id') or payload.get('id')
            if uid:
                g.user = {'uid': uid, 'email': payload.get('email')}
                return True
        except Exception:
            pass
    # 4) Fallback: imposta almeno un dict vuoto per evitare AttributeError
    g.user = {}
    return False

@app.before_request
def _sx_ms_uid_guard():
    # Applica il guard solo alle API Microsoft e alla pagina di sync
    p = request.path or ''
    if p.startswith('/api/ms/') or p.startswith('/ms/') or p.startswith('/sync-outlook'):
        _sx_try_set_g_user_from_session()
        

# === Appendix: Outlook sync — all-day-like + end-exclusive window (2025-10-20) ===
# Drop-in: incolla in fondo a app.py. Non rimuove le funzioni esistenti.
# Sostituisce le view function degli endpoint via app.view_functions.

from datetime import datetime, timedelta, timezone
import json, base64
from flask import request, jsonify, session, g

# Safe defaults se non presenti
APP_TZ = globals().get('APP_TZ', 'Europe/Rome')
MS_API = globals().get('MS_API', 'https://graph.microsoft.com/v1.0')

def _current_uid():
    try:
        return session.get('uid') or getattr(g, 'user', {}).get('uid') or getattr(g, 'user', {}).get('id')
    except Exception:
        return session.get('uid')

def _parse_iso(dt):
    if not dt: 
        return None
    s = dt.replace('Z','+00:00')
    try:
        return datetime.fromisoformat(s)
    except Exception:
        try:
            # taglia frazioni troppo lunghe
            if 'T' in s and '.' in s:
                head, tail = s.split('.',1)
                tz = '+00:00'
                if len(tail) >= 6 and (tail[-6] in ['+','-']):
                    tz = tail[-6:]
                    frac = ''.join(c for c in tail[:-6] if c.isdigit())
                else:
                    frac = ''.join(c for c in tail if c.isdigit())
                frac = (frac + '000000')[:6]
                s = f"{head}.{frac}{tz}"
            return datetime.fromisoformat(s)
        except Exception:
            return None

def _expand_allday_days(start_iso, end_iso):
    """Ritorna lista di YYYY-MM-DD per eventi all‑day o like‑all‑day.
       Se end coincide con mezzanotte del giorno successivo, l'ultimo giorno è incluso.
    """
    ds = _parse_iso(start_iso) or datetime.now(timezone.utc)
    de = _parse_iso(end_iso) or ds
    # normalizza a timezone naive UTC
    if ds.tzinfo: 
        ds = ds.astimezone(timezone.utc).replace(tzinfo=None)
    if de.tzinfo: 
        de = de.astimezone(timezone.utc).replace(tzinfo=None)
    if de <= ds:
        de = ds + timedelta(days=1)
    # Se fine è a mezzanotte, considera end esclusivo ma includi giorno precedente
    inclusive_end = de - timedelta(seconds=1)
    days = []
    cur = ds.date()
    last = inclusive_end.date()
    while cur <= last:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days

def _is_allday_like(ev):
    try:
        if ev.get('isAllDay'):
            return True
        s = (ev.get('start') or {}).get('dateTime') or ''
        e = (ev.get('end') or {}).get('dateTime') or s
        ds = _parse_iso(s) or datetime.now(timezone.utc)
        de = _parse_iso(e) or ds
        # start a mezzanotte
        dsz = ds.astimezone(timezone.utc)
        diff = (de - ds).total_seconds()
        return (dsz.hour==0 and dsz.minute==0 and dsz.second==0 and diff>=86400 and diff % 86400 == 0)
    except Exception:
        return False

def _month_window(month_str):
    y, m = map(int, month_str.split('-'))
    start = datetime(y, m, 1, 0, 0, 0, tzinfo=timezone.utc)
    # end = ultimo secondo del mese
    if m == 12:
        end = datetime(y+1, 1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    else:
        end = datetime(y, m+1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    return start, end

def _headers(tok):
    return {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json', 'Prefer': f'outlook.timezone="{APP_TZ}"'}

def api_ms_sync_preview__override():
    try:
        guard = _require_login()
    except Exception:
        guard = None
    if guard: 
        return guard
    try:
        tok = _ms_load_token()
    except Exception:
        tok = None
    if not tok:
        return jsonify({'error':'not_connected'}), 400
    month = request.args.get('month')
    if not month: 
        return jsonify({'error':'month_required'}), 400

    start, end = _month_window(month)
    end_excl = end + timedelta(seconds=1)

    params = {
        'startDateTime': start.isoformat(),
        'endDateTime': end_excl.isoformat(),
        '$select': 'subject,start,end,isAllDay,id'
    }
    import requests as _r
    gr = _r.get(f"{MS_API}/me/calendarView", headers=_headers(tok), params=params, timeout=30)
    if not getattr(gr, 'ok', False):
        return jsonify({'error':'graph_failed','detail': getattr(gr, 'text', '')}), 400

    remote = {}
    try:
        vals = (gr.json() or {}).get('value', []) or []
    except Exception:
        vals = []
    for e in vals:
        if not _is_allday_like(e): 
            continue
        subj = (e.get('subject') or '').strip()
        sdt = (e.get('start') or {}).get('dateTime') or ''
        edt = (e.get('end') or {}).get('dateTime') or sdt
        for day in _expand_allday_days(sdt, edt):
            if day < start.date().isoformat() or day > end.date().isoformat():
                continue
            remote.setdefault(day, set()).add(subj)

    # Locali
    uid = _current_uid()
    sbt = session.get('sb_token')
    local = {}
    try:
        r = _sb_get('calendar_positions', sbt, params={
            'select':'day,pos1,pos2,pos3,user_id', 'user_id': f'eq.{uid}', 'day': f'gte.{start.date().isoformat()}'
        })
        if getattr(r, 'status_code', 500)==200:
            for row in r.json() or []:
                d = row.get('day')
                if not d or d < start.date().isoformat() or d > end.date().isoformat():
                    continue
                for k in ('pos1','pos2','pos3'):
                    v = (row.get(k) or '').strip()
                    if v:
                        local.setdefault(d, set()).add(v)
    except Exception:
        pass

    days = sorted(set(local.keys()) | set(remote.keys()))
    out = {'days':{}}
    for d in days:
        L = local.get(d, set()); R = remote.get(d, set())
        out['days'][d] = {
            'common': sorted(L & R, key=str.casefold),
            'create_remote': sorted(L - R, key=str.casefold),
            'create_local':  sorted(R - L, key=str.casefold),
            'delete_remote': sorted(R - L, key=str.casefold)
        }
    return jsonify(out)

def api_ms_sync_apply__override():
    try:
        guard = _require_login()
    except Exception:
        guard = None
    if guard: 
        return guard
    try:
        tok = _ms_load_token()
    except Exception:
        tok = None
    if not tok:
        return jsonify({'error':'not_connected'}), 400
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        body = {}
    cr = body.get('create_remote') or []
    cl = body.get('create_local') or []
    dr = body.get('delete_remote') or []
    rrn = body.get('rename_remote') or []

    uid = _current_uid()
    sbt = session.get('sb_token')
    import requests as _r
    hdr = _headers(tok)

    # Cache eventi per id
    days_all = sorted({x['day'] for x in (cr+cl+dr+rrn) if isinstance(x, dict) and x.get('day')})
    cache = {}
    if days_all:
        start = min(days_all); end = max(days_all)
        gr = _r.get(f"{MS_API}/me/calendarView", headers=hdr, params={
            'startDateTime': f"{start}T00:00:00",
            'endDateTime':   f"{end}T23:59:59",
            '$select':'id,subject,start,end,isAllDay'
        }, timeout=30)
        if getattr(gr, 'ok', False):
            for e in (gr.json() or {}).get('value', []) or []:
                if not _is_allday_like(e): 
                    continue
                sd = (e.get('start') or {}).get('dateTime','')[:10]
                sub = (e.get('subject') or '').strip()
                if sd and sub:
                    cache.setdefault(sd, {})[sub] = e.get('id')

    def _graph_create(day, subject):
        payload = {"subject": subject, "isAllDay": True,
                   "start": {"dateTime": f"{day}T00:00:00", "timeZone": APP_TZ},
                   "end":   {"dateTime": f"{day}T00:00:00", "timeZone": APP_TZ}}
        r = _r.post(f"{MS_API}/me/events", headers=hdr, json=payload, timeout=30)
        return getattr(r, 'ok', False)

    created_remote = 0; deleted_remote = 0; created_local = 0; renamed_remote = 0

    for x in cr:
        if isinstance(x, dict) and x.get('day') and x.get('text'):
            if _graph_create(x['day'], x['text']): created_remote += 1

    for x in rrn:
        day = (x.get('day') or '').strip() if isinstance(x, dict) else ''
        src = (x.get('from_text') or '').strip() if isinstance(x, dict) else ''
        dst = (x.get('to_text') or '').strip() if isinstance(x, dict) else ''
        if not day or not src or not dst or src == dst: 
            continue
        ev_id = cache.get(day,{}).get(src)
        if ev_id:
            pr = _r.patch(f"{MS_API}/me/events/{ev_id}", headers=hdr, json={'subject': dst}, timeout=30)
            if getattr(pr, 'status_code', 500) in (200,204):
                renamed_remote += 1
                try: 
                    del cache[day][src]
                except Exception: 
                    pass
                cache.setdefault(day, {})[dst] = ev_id

    for x in dr:
        day = (x.get('day') or '').strip() if isinstance(x, dict) else ''
        txt = (x.get('text') or '').strip() if isinstance(x, dict) else ''
        if not day or not txt: 
            continue
        ev_id = cache.get(day,{}).get(txt)
        if ev_id:
            rr = _r.delete(f"{MS_API}/me/events/{ev_id}", headers=hdr, timeout=30)
            if getattr(rr, 'status_code', 500) in (200,204): 
                deleted_remote += 1

    # Scritture locali
    days_set = sorted({x['day'] for x in cl if isinstance(x, dict) and x.get('day')})
    existing = {}
    if days_set:
        try:
            rr = _sb_get('calendar_positions', sbt, params={'select':'day,pos1,pos2,pos3,user_id', 'user_id': f'eq.{uid}', 'day': f'in.({",".join(days_set)})'})
            if getattr(rr, 'status_code', 500) == 200:
                for row in rr.json() or []:
                    existing[row['day']] = [row.get('pos1') or '', row.get('pos2') or '', row.get('pos3') or '']
        except Exception:
            pass

    for x in cl:
        if not isinstance(x, dict): 
            continue
        day = (x.get('day') or '').strip()
        text = (x.get('text') or '').strip()
        if not day or not text: 
            continue
        arr = existing.get(day, ['', '', ''])
        if text in arr: 
            continue
        if arr.count('') == 0: 
            continue
        for i in range(3):
            if not arr[i]:
                arr[i] = text
                break
        try:
            payload = [ {'user_id': uid, 'day': day,
                         'pos1': arr[0] or None, 'pos2': arr[1] or None, 'pos3': arr[2] or None,
                         'pos1_color': '#ef4444' if arr[0]==text else None,
                         'pos2_color': '#ef4444' if arr[1]==text else None,
                         'pos3_color': '#ef4444' if arr[2]==text else None } ]
            _sb_upsert('calendar_positions', sbt, json_body=payload, on_conflict='user_id,day')
            existing[day] = arr
            created_local += 1
        except Exception:
            pass

    return jsonify({'ok': True, 'message': f'Outlook creati: {created_remote}, Outlook rinominati: {renamed_remote}, Outlook cancellati: {deleted_remote}, Calendar creati: {created_local}' })

# ---- Rebind delle view functions senza toccare le definizioni originali ----
try:
    app.view_functions['api_ms_sync_preview'] = api_ms_sync_preview__override
    app.view_functions['api_ms_sync_apply']   = api_ms_sync_apply__override
except Exception:
    pass

# === Appendix: MS Graph debug probe — paged (2025‑10‑20) =========================
from datetime import datetime, timedelta, timezone
from flask import request, jsonify
import requests

def _dbg_month_window(month_str):
    y, m = map(int, month_str.split('-'))
    start = datetime(y, m, 1, 0, 0, 0, tzinfo=timezone.utc)
    if m == 12:
        end = datetime(y+1, 1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    else:
        end = datetime(y, m+1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    return start, end, end + timedelta(seconds=1)

def _dbg_headers(tok):
    try:
        h = _ms_headers(tok); h['Prefer'] = f'outlook.timezone="{APP_TZ}"'; return h
    except Exception:
        return {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json', 'Prefer': f'outlook.timezone="{APP_TZ}"'}

@app.get('/api/ms/debug/probe')
def api_ms_debug_probe():
    try:
        guard = _require_login()
    except Exception:
        guard = None
    if guard: return guard
    try:
        tok = _ms_load_token()
    except Exception:
        tok = None
    if not tok: return jsonify({'error':'not_connected'}), 400
    month = request.args.get('month')
    if not month: return jsonify({'error':'month_required'}), 400

    start, end, end_excl = _dbg_month_window(month)
    url = f"{MS_API}/me/calendarView"
    params = {'startDateTime': start.isoformat(), 'endDateTime': end_excl.isoformat(), '$select': 'id,subject,start,end,isAllDay,showAs', '$top': 999}
    hdr = _dbg_headers(tok)
    items = []
    while True:
        r = requests.get(url, headers=hdr, params=params, timeout=30)
        if not getattr(r, 'ok', False): return jsonify({'error':'graph_failed','detail': getattr(r,'text','')}), 400
        js = r.json() or {}
        items.extend(js.get('value', []) or [])
        nxt = js.get('@odata.nextLink')
        if not nxt: break
        url = nxt; params = None

    out = [{
        'subject': (e.get('subject') or '').strip(),
        'isAllDay': bool(e.get('isAllDay')),
        'start': (e.get('start') or {}).get('dateTime'),
        'end':   (e.get('end') or {}).get('dateTime'),
    } for e in items]
    return jsonify({'ok': True, 'count': len(out), 'events': out})

# === Appendix: Microsoft Graph paging + all‑day‑like (2025‑10‑20) ==================
from datetime import datetime, timedelta, timezone
from flask import request, jsonify, session, g
import requests

APP_TZ = globals().get('APP_TZ', 'Europe/Rome')
MS_API = globals().get('MS_API', 'https://graph.microsoft.com/v1.0')

def _current_uid():
    try:
        return session.get('uid') or getattr(g, 'user', {}).get('uid') or getattr(g, 'user', {}).get('id')
    except Exception:
        return session.get('uid')

def _parse_iso(dt):
    if not dt: return None
    s = dt.replace('Z','+00:00')
    try:
        return datetime.fromisoformat(s)
    except Exception:
        try:
            if 'T' in s and '.' in s:
                head, tail = s.split('.',1)
                tz = '+00:00'
                if len(tail) >= 6 and (tail[-6] in ['+','-']):
                    tz = tail[-6:]
                    frac = ''.join(c for c in tail[:-6] if c.isdigit())
                else:
                    frac = ''.join(c for c in tail if c.isdigit())
                frac = (frac + '000000')[:6]
                s = f"{head}.{frac}{tz}"
            return datetime.fromisoformat(s)
        except Exception:
            return None

def _expand_allday_days(start_iso, end_iso):
    ds = _parse_iso(start_iso) or datetime.now(timezone.utc)
    de = _parse_iso(end_iso) or ds
    if ds.tzinfo: ds = ds.astimezone(timezone.utc).replace(tzinfo=None)
    if de.tzinfo: de = de.astimezone(timezone.utc).replace(tzinfo=None)
    if de <= ds: de = ds + timedelta(days=1)
    inclusive_end = de - timedelta(seconds=1)
    out = []
    cur = ds.date(); last = inclusive_end.date()
    while cur <= last:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out

def _is_allday_like(ev):
    try:
        if ev.get('isAllDay'): return True
        s = (ev.get('start') or {}).get('dateTime') or ''
        e = (ev.get('end') or {}).get('dateTime') or s
        ds = _parse_iso(s) or datetime.now(timezone.utc)
        de = _parse_iso(e) or ds
        dsz = ds.astimezone(timezone.utc)
        diff = (de - ds).total_seconds()
        return (dsz.hour==0 and dsz.minute==0 and dsz.second==0 and diff>=86400 and diff % 86400 == 0)
    except Exception:
        return False

def _month_window(month_str):
    y, m = map(int, month_str.split('-'))
    start = datetime(y, m, 1, 0, 0, 0, tzinfo=timezone.utc)
    if m == 12:
        end = datetime(y+1, 1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    else:
        end = datetime(y, m+1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    return start, end

def _ms_headers(tok):
    h = {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}
    h['Prefer'] = f'outlook.timezone="{APP_TZ}"'
    return h

def _graph_calendarview_all(tok, start_iso, end_iso, select='subject,start,end,isAllDay,id', top=999):
    # Scarica TUTTE le pagine di /me/calendarView seguendo @odata.nextLink
    url = f"{MS_API}/me/calendarView"
    params = {'startDateTime': start_iso, 'endDateTime': end_iso, '$select': select, '$top': top}
    hdr = _ms_headers(tok)
    items = []
    while True:
        r = requests.get(url, headers=hdr, params=params, timeout=30)
        if not getattr(r, 'ok', False):
            return None, getattr(r, 'text', 'graph_error')
        js = r.json() or {}
        items.extend(js.get('value', []) or [])
        next_link = js.get('@odata.nextLink')
        if not next_link: break
        url = next_link  # contiene già query string
        params = None    # evita di ri‑allegare params
    return items, None

def api_ms_sync_preview__paged():
    try:
        guard = _require_login()
    except Exception:
        guard = None
    if guard: return guard
    try:
        tok = _ms_load_token()
    except Exception:
        tok = None
    if not tok: return jsonify({'error':'not_connected'}), 400
    month = request.args.get('month')
    if not month: return jsonify({'error':'month_required'}), 400

    start, end = _month_window(month)
    end_excl = end + timedelta(seconds=1)

    events, err = _graph_calendarview_all(tok, start.isoformat(), end_excl.isoformat(),
                                          select='id,subject,start,end,isAllDay,showAs')
    if err: return jsonify({'error':'graph_failed','detail': err}), 400

    remote = {}
    for e in events:
        if not _is_allday_like(e): continue
        subj = (e.get('subject') or '').strip()
        sdt = (e.get('start') or {}).get('dateTime') or ''
        edt = (e.get('end') or {}).get('dateTime') or sdt
        for day in _expand_allday_days(sdt, edt):
            if start.date().isoformat() <= day <= end.date().isoformat():
                remote.setdefault(day, set()).add(subj)

    uid = _current_uid()
    sbt = session.get('sb_token')
    local = {}
    try:
        r = _sb_get('calendar_positions', sbt, params={
            'select':'day,pos1,pos2,pos3,user_id', 'user_id': f'eq.{uid}', 'day': f'gte.{start.date().isoformat()}'
        })
        if getattr(r, 'status_code', 500)==200:
            for row in r.json() or []:
                d = row.get('day')
                if not d or not (start.date().isoformat() <= d <= end.date().isoformat()): 
                    continue
                for k in ('pos1','pos2','pos3'):
                    v = (row.get(k) or '').strip()
                    if v: local.setdefault(d, set()).add(v)
    except Exception:
        pass

    days = sorted(set(local.keys()) | set(remote.keys()))
    out = {'days':{}}
    for d in days:
        L = local.get(d, set()); R = remote.get(d, set())
        out['days'][d] = {
            'common': sorted(L & R, key=str.casefold),
            'create_remote': sorted(L - R, key=str.casefold),
            'create_local':  sorted(R - L, key=str.casefold),
            'delete_remote': sorted(R - L, key=str.casefold)
        }
    return jsonify(out)

def api_ms_sync_apply__paged():
    try:
        guard = _require_login()
    except Exception:
        guard = None
    if guard: return guard
    try:
        tok = _ms_load_token()
    except Exception:
        tok = None
    if not tok: return jsonify({'error':'not_connected'}), 400

    try:
        body = request.get_json(force=True) or {}
    except Exception:
        body = {}
    cr  = body.get('create_remote') or []
    cl  = body.get('create_local') or []
    dr  = body.get('delete_remote') or []
    rrn = body.get('rename_remote') or []

    uid = _current_uid()
    sbt = session.get('sb_token')

    days_all = sorted({x['day'] for x in (cr+cl+dr+rrn) if isinstance(x, dict) and x.get('day')})
    cache = {}
    if days_all:
        start = f"{min(days_all)}T00:00:00"
        end   = f"{max(days_all)}T23:59:59"
        events, err = _graph_calendarview_all(tok, start, end, select='id,subject,start,end,isAllDay', top=999)
        if not err:
            for e in events:
                if not _is_allday_like(e): continue
                sd = (e.get('start') or {}).get('dateTime','')[:10]
                sub = (e.get('subject') or '').strip()
                if sd and sub: cache.setdefault(sd, {})[sub] = e.get('id')

    def _graph_create(day, subject):
        payload = {"subject": subject, "isAllDay": True,
                   "start": {"dateTime": f"{day}T00:00:00", "timeZone": APP_TZ},
                   "end":   {"dateTime": f"{day}T00:00:00", "timeZone": APP_TZ}}
        r = requests.post(f"{MS_API}/me/events", headers=_ms_headers(tok), json=payload, timeout=30)
        return getattr(r, 'ok', False)

    created_remote = 0; deleted_remote = 0; created_local = 0; renamed_remote = 0

    for x in cr:
        if isinstance(x, dict) and x.get('day') and x.get('text'):
            if _graph_create(x['day'], x['text']): created_remote += 1

    for x in rrn:
        day = (x.get('day') or '').strip() if isinstance(x, dict) else ''
        src = (x.get('from_text') or '').strip() if isinstance(x, dict) else ''
        dst = (x.get('to_text') or '').strip() if isinstance(x, dict) else ''
        if not day or not src or not dst or src == dst: continue
        ev_id = cache.get(day,{}).get(src)
        if ev_id:
            pr = requests.patch(f"{MS_API}/me/events/{ev_id}", headers=_ms_headers(tok), json={'subject': dst}, timeout=30)
            if getattr(pr, 'status_code', 500) in (200,204):
                renamed_remote += 1
                try: del cache[day][src]
                except Exception: pass
                cache.setdefault(day, {})[dst] = ev_id

    for x in dr:
        day = (x.get('day') or '').strip() if isinstance(x, dict) else ''
        txt = (x.get('text') or '').strip() if isinstance(x, dict) else ''
        if not day or not txt: continue
        ev_id = cache.get(day,{}).get(txt)
        if ev_id:
            rr = requests.delete(f"{MS_API}/me/events/{ev_id}", headers=_ms_headers(tok), timeout=30)
            if getattr(rr, 'status_code', 500) in (200,204): deleted_remote += 1

    days_set = sorted({x['day'] for x in cl if isinstance(x, dict) and x.get('day')})
    existing = {}
    if days_set:
        try:
            rr = _sb_get('calendar_positions', sbt, params={'select':'day,pos1,pos2,pos3,user_id', 'user_id': f'eq.{uid}', 'day': f'in.({",".join(days_set)})'})
            if getattr(rr, 'status_code', 500) == 200:
                for row in rr.json() or []:
                    existing[row['day']] = [row.get('pos1') or '', row.get('pos2') or '', row.get('pos3') or '']
        except Exception:
            pass

    for x in cl:
        if not isinstance(x, dict): continue
        day = (x.get('day') or '').strip()
        text = (x.get('text') or '').strip()
        if not day or not text: continue
        arr = existing.get(day, ['', '', ''])
        if text in arr: continue
        if arr.count('') == 0: continue
        for i in range(3):
            if not arr[i]: arr[i] = text; break
        try:
            payload = [ {'user_id': uid, 'day': day,
                         'pos1': arr[0] or None, 'pos2': arr[1] or None, 'pos3': arr[2] or None,
                         'pos1_color': '#ef4444' if arr[0]==text else None,
                         'pos2_color': '#ef4444' if arr[1]==text else None,
                         'pos3_color': '#ef4444' if arr[2]==text else None } ]
            _sb_upsert('calendar_positions', sbt, json_body=payload, on_conflict='user_id,day')
            existing[day] = arr; created_local += 1
        except Exception:
            pass

    return jsonify({'ok': True, 'message': f'Outlook creati: {created_remote}, Outlook rinominati: {renamed_remote}, Outlook cancellati: {deleted_remote}, Calendar creati: {created_local}' })

try:
    app.view_functions['api_ms_sync_preview'] = api_ms_sync_preview__paged
    app.view_functions['api_ms_sync_apply']   = api_ms_sync_apply__paged
except Exception:
    pass
# === Appendix: Microsoft Graph paging + all‑day‑like (2025‑10‑20) ==================
from datetime import datetime, timedelta, timezone
from flask import request, jsonify, session, g
import requests

APP_TZ = globals().get('APP_TZ', 'Europe/Rome')
MS_API = globals().get('MS_API', 'https://graph.microsoft.com/v1.0')

def _current_uid():
    try:
        return session.get('uid') or getattr(g, 'user', {}).get('uid') or getattr(g, 'user', {}).get('id')
    except Exception:
        return session.get('uid')

def _parse_iso(dt):
    if not dt: return None
    s = dt.replace('Z','+00:00')
    try:
        return datetime.fromisoformat(s)
    except Exception:
        try:
            if 'T' in s and '.' in s:
                head, tail = s.split('.',1)
                tz = '+00:00'
                if len(tail) >= 6 and (tail[-6] in ['+','-']):
                    tz = tail[-6:]
                    frac = ''.join(c for c in tail[:-6] if c.isdigit())
                else:
                    frac = ''.join(c for c in tail if c.isdigit())
                frac = (frac + '000000')[:6]
                s = f"{head}.{frac}{tz}"
            return datetime.fromisoformat(s)
        except Exception:
            return None

def _expand_allday_days(start_iso, end_iso):
    ds = _parse_iso(start_iso) or datetime.now(timezone.utc)
    de = _parse_iso(end_iso) or ds
    if ds.tzinfo: ds = ds.astimezone(timezone.utc).replace(tzinfo=None)
    if de.tzinfo: de = de.astimezone(timezone.utc).replace(tzinfo=None)
    if de <= ds: de = ds + timedelta(days=1)
    inclusive_end = de - timedelta(seconds=1)
    out = []
    cur = ds.date(); last = inclusive_end.date()
    while cur <= last:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out

def _is_allday_like(ev):
    try:
        if ev.get('isAllDay'): return True
        s = (ev.get('start') or {}).get('dateTime') or ''
        e = (ev.get('end') or {}).get('dateTime') or s
        ds = _parse_iso(s) or datetime.now(timezone.utc)
        de = _parse_iso(e) or ds
        dsz = ds.astimezone(timezone.utc)
        diff = (de - ds).total_seconds()
        return (dsz.hour==0 and dsz.minute==0 and dsz.second==0 and diff>=86400 and diff % 86400 == 0)
    except Exception:
        return False

def _month_window(month_str):
    y, m = map(int, month_str.split('-'))
    start = datetime(y, m, 1, 0, 0, 0, tzinfo=timezone.utc)
    if m == 12:
        end = datetime(y+1, 1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    else:
        end = datetime(y, m+1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    return start, end

def _ms_headers(tok):
    h = {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}
    h['Prefer'] = f'outlook.timezone="{APP_TZ}"'
    return h

def _graph_calendarview_all(tok, start_iso, end_iso, select='subject,start,end,isAllDay,id', top=999):
    # Scarica TUTTE le pagine di /me/calendarView seguendo @odata.nextLink
    url = f"{MS_API}/me/calendarView"
    params = {'startDateTime': start_iso, 'endDateTime': end_iso, '$select': select, '$top': top}
    hdr = _ms_headers(tok)
    items = []
    while True:
        r = requests.get(url, headers=hdr, params=params, timeout=30)
        if not getattr(r, 'ok', False):
            return None, getattr(r, 'text', 'graph_error')
        js = r.json() or {}
        items.extend(js.get('value', []) or [])
        next_link = js.get('@odata.nextLink')
        if not next_link: break
        url = next_link  # contiene già query string
        params = None    # evita di ri‑allegare params
    return items, None

def api_ms_sync_preview__paged():
    try:
        guard = _require_login()
    except Exception:
        guard = None
    if guard: return guard
    try:
        tok = _ms_load_token()
    except Exception:
        tok = None
    if not tok: return jsonify({'error':'not_connected'}), 400
    month = request.args.get('month')
    if not month: return jsonify({'error':'month_required'}), 400

    start, end = _month_window(month)
    end_excl = end + timedelta(seconds=1)

    events, err = _graph_calendarview_all(tok, start.isoformat(), end_excl.isoformat(),
                                          select='id,subject,start,end,isAllDay,showAs')
    if err: return jsonify({'error':'graph_failed','detail': err}), 400

    remote = {}
    for e in events:
        if not _is_allday_like(e): continue
        subj = (e.get('subject') or '').strip()
        sdt = (e.get('start') or {}).get('dateTime') or ''
        edt = (e.get('end') or {}).get('dateTime') or sdt
        for day in _expand_allday_days(sdt, edt):
            if start.date().isoformat() <= day <= end.date().isoformat():
                remote.setdefault(day, set()).add(subj)

    uid = _current_uid()
    sbt = session.get('sb_token')
    local = {}
    try:
        r = _sb_get('calendar_positions', sbt, params={
            'select':'day,pos1,pos2,pos3,user_id', 'user_id': f'eq.{uid}', 'day': f'gte.{start.date().isoformat()}'
        })
        if getattr(r, 'status_code', 500)==200:
            for row in r.json() or []:
                d = row.get('day')
                if not d or not (start.date().isoformat() <= d <= end.date().isoformat()): 
                    continue
                for k in ('pos1','pos2','pos3'):
                    v = (row.get(k) or '').strip()
                    if v: local.setdefault(d, set()).add(v)
    except Exception:
        pass

    days = sorted(set(local.keys()) | set(remote.keys()))
    out = {'days':{}}
    for d in days:
        L = local.get(d, set()); R = remote.get(d, set())
        out['days'][d] = {
            'common': sorted(L & R, key=str.casefold),
            'create_remote': sorted(L - R, key=str.casefold),
            'create_local':  sorted(R - L, key=str.casefold),
            'delete_remote': sorted(R - L, key=str.casefold)
        }
    return jsonify(out)

def api_ms_sync_apply__paged():
    try:
        guard = _require_login()
    except Exception:
        guard = None
    if guard: return guard
    try:
        tok = _ms_load_token()
    except Exception:
        tok = None
    if not tok: return jsonify({'error':'not_connected'}), 400

    try:
        body = request.get_json(force=True) or {}
    except Exception:
        body = {}
    cr  = body.get('create_remote') or []
    cl  = body.get('create_local') or []
    dr  = body.get('delete_remote') or []
    rrn = body.get('rename_remote') or []

    uid = _current_uid()
    sbt = session.get('sb_token')

    days_all = sorted({x['day'] for x in (cr+cl+dr+rrn) if isinstance(x, dict) and x.get('day')})
    cache = {}
    if days_all:
        start = f"{min(days_all)}T00:00:00"
        end   = f"{max(days_all)}T23:59:59"
        events, err = _graph_calendarview_all(tok, start, end, select='id,subject,start,end,isAllDay', top=999)
        if not err:
            for e in events:
                if not _is_allday_like(e): continue
                sd = (e.get('start') or {}).get('dateTime','')[:10]
                sub = (e.get('subject') or '').strip()
                if sd and sub: cache.setdefault(sd, {})[sub] = e.get('id')

    def _graph_create(day, subject):
        payload = {"subject": subject, "isAllDay": True,
                   "start": {"dateTime": f"{day}T00:00:00", "timeZone": APP_TZ},
                   "end":   {"dateTime": f"{day}T00:00:00", "timeZone": APP_TZ}}
        r = requests.post(f"{MS_API}/me/events", headers=_ms_headers(tok), json=payload, timeout=30)
        return getattr(r, 'ok', False)

    created_remote = 0; deleted_remote = 0; created_local = 0; renamed_remote = 0

    for x in cr:
        if isinstance(x, dict) and x.get('day') and x.get('text'):
            if _graph_create(x['day'], x['text']): created_remote += 1

    for x in rrn:
        day = (x.get('day') or '').strip() if isinstance(x, dict) else ''
        src = (x.get('from_text') or '').strip() if isinstance(x, dict) else ''
        dst = (x.get('to_text') or '').strip() if isinstance(x, dict) else ''
        if not day or not src or not dst or src == dst: continue
        ev_id = cache.get(day,{}).get(src)
        if ev_id:
            pr = requests.patch(f"{MS_API}/me/events/{ev_id}", headers=_ms_headers(tok), json={'subject': dst}, timeout=30)
            if getattr(pr, 'status_code', 500) in (200,204):
                renamed_remote += 1
                try: del cache[day][src]
                except Exception: pass
                cache.setdefault(day, {})[dst] = ev_id

    for x in dr:
        day = (x.get('day') or '').strip() if isinstance(x, dict) else ''
        txt = (x.get('text') or '').strip() if isinstance(x, dict) else ''
        if not day or not txt: continue
        ev_id = cache.get(day,{}).get(txt)
        if ev_id:
            rr = requests.delete(f"{MS_API}/me/events/{ev_id}", headers=_ms_headers(tok), timeout=30)
            if getattr(rr, 'status_code', 500) in (200,204): deleted_remote += 1

    days_set = sorted({x['day'] for x in cl if isinstance(x, dict) and x.get('day')})
    existing = {}
    if days_set:
        try:
            rr = _sb_get('calendar_positions', sbt, params={'select':'day,pos1,pos2,pos3,user_id', 'user_id': f'eq.{uid}', 'day': f'in.({",".join(days_set)})'})
            if getattr(rr, 'status_code', 500) == 200:
                for row in rr.json() or []:
                    existing[row['day']] = [row.get('pos1') or '', row.get('pos2') or '', row.get('pos3') or '']
        except Exception:
            pass

    for x in cl:
        if not isinstance(x, dict): continue
        day = (x.get('day') or '').strip()
        text = (x.get('text') or '').strip()
        if not day or not text: continue
        arr = existing.get(day, ['', '', ''])
        if text in arr: continue
        if arr.count('') == 0: continue
        for i in range(3):
            if not arr[i]: arr[i] = text; break
        try:
            payload = [ {'user_id': uid, 'day': day,
                         'pos1': arr[0] or None, 'pos2': arr[1] or None, 'pos3': arr[2] or None,
                         'pos1_color': '#ef4444' if arr[0]==text else None,
                         'pos2_color': '#ef4444' if arr[1]==text else None,
                         'pos3_color': '#ef4444' if arr[2]==text else None } ]
            _sb_upsert('calendar_positions', sbt, json_body=payload, on_conflict='user_id,day')
            existing[day] = arr; created_local += 1
        except Exception:
            pass

    return jsonify({'ok': True, 'message': f'Outlook creati: {created_remote}, Outlook rinominati: {renamed_remote}, Outlook cancellati: {deleted_remote}, Calendar creati: {created_local}' })

try:
    app.view_functions['api_ms_sync_preview'] = api_ms_sync_preview__paged
    app.view_functions['api_ms_sync_apply']   = api_ms_sync_apply__paged
except Exception:
    pass

