from flask import Blueprint, render_template, request, session, redirect, url_for, flash

from links_repository import list_links_grouped

links_bp = Blueprint("links", __name__, url_prefix="/link")


def _ensure_session_keys() -> None:
    session.setdefault("store_code", None)
    session.setdefault("store_name", None)


def _require_login():
    if not session.get("uid") or not session.get("sb_token"):
        return redirect(url_for("login", next=request.full_path))
    return None


@links_bp.before_request
def _links_before_request():
    _ensure_session_keys()
    guard = _require_login()
    if guard:
        return guard
    return None


@links_bp.get("/")
def home():
    try:
        tenant_key = str(session.get("tenant_key") or "default").strip() or "default"
        groups = list_links_grouped(tenant_key=tenant_key)
    except Exception as e:
        flash(f"Impossibile caricare i link: {e}", "danger")
        groups = []

    return render_template("links.html", groups=groups)
