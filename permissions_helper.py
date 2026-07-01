# permissions_helper.py
from functools import wraps
from flask import redirect, url_for, flash, request, abort

# Elenco standard dei flag
PERM_FIELDS = [
    "can_view_anagrafica",
    "can_edit_anagrafica",
    "can_view_reviews",
    "can_reply_reviews",
    "can_access_media_single",
    "can_access_media_bulk",
]

def normalize_user_perms(user: dict) -> dict:
    """
    Ritorna tutti i flag True/False con default sicuri.
    Gli admin hanno tutto True a prescindere.
    """
    if not user:
        return {k: False for k in PERM_FIELDS}
    if user.get("is_admin"):
        return {k: True for k in PERM_FIELDS}
    out = {}
    out["can_view_anagrafica"]     = bool(user.get("can_view_anagrafica", True))
    out["can_edit_anagrafica"]     = bool(user.get("can_edit_anagrafica", False))
    out["can_view_reviews"]        = bool(user.get("can_view_reviews", True))
    out["can_reply_reviews"]       = bool(user.get("can_reply_reviews", False))
    out["can_access_media_single"] = bool(user.get("can_access_media_single", True))
    out["can_access_media_bulk"]   = bool(user.get("can_access_media_bulk", False))
    return out

def require_perm(flag_name: str, redirect_endpoint: str = "dashboard"):
    """
    Decorator: consente la view solo se l'utente ha il flag, oppure è admin.
    Usa current_user() definito in app.py.
    """
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            from app import current_user  # import locale per evitare cicli
            user = current_user()
            if not user:
                flash("Devi autenticarti.", "warning")
                return redirect(url_for("login"))
            if user.get("is_admin") or user.get(flag_name):
                return view(*args, **kwargs)
            # Se è una POST, restituiamo 403; se è GET, reindirizziamo
            if request.method == "POST":
                abort(403)
            flash("Permesso negato per questa sezione.", "danger")
            return redirect(url_for(redirect_endpoint))
        return wrapped
    return deco
