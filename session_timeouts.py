import time as _time
from datetime import timedelta
from flask import session, request, flash, redirect, url_for

def install_timeouts(app, inactivity_seconds=600, absolute_hours=24):
    if getattr(app, "_timeouts_installed", False):
        return
    app._timeouts_installed = True
    app.permanent_session_lifetime = timedelta(hours=absolute_hours)

    EXCLUDE_PREFIXES = ("/static/",)
    EXCLUDE_PATHS = {"/login", "/logout", "/favicon.ico"}

    @app.before_request
    def _enforce_timeouts():
        p = request.path or "/"
        if p in EXCLUDE_PATHS or any(p.startswith(pref) for pref in EXCLUDE_PREFIXES):
            return
        if "uid" not in session:
            return
        now = _time.time()
        login_time = session.get("login_time")
        if isinstance(login_time, (int, float)) and now - login_time > (absolute_hours * 3600):
            _wipe_session()
            flash("Sessione scaduta (24 ore). Effettua di nuovo l'accesso.", "warning")
            return redirect(url_for("login"))
        last = session.get("last_activity")
        if isinstance(last, (int, float)) and now - last > inactivity_seconds:
            _wipe_session()
            mins = int(inactivity_seconds // 60) or 1
            flash(f"Sei stato disconnesso per inattività (~{mins} minuti).", "warning")
            return redirect(url_for("login"))
        session["last_activity"] = now

def session_permanent_on_login():
    session.permanent = True
    now = _time.time()
    session["login_time"] = now
    session["last_activity"] = now

def _wipe_session():
    for k in ("uid", "email", "name", "role", "sb_token", "login_time", "last_activity"):
        try:
            session.pop(k, None)
        except Exception:
            pass
