"""
app.py — Vaani Flask application.

Run locally:   python app.py
Production:    gunicorn wsgi:app
Create admin:  python manage.py create-admin
"""

import csv
import io
import logging
import os
from datetime import datetime, timezone
from functools import wraps

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFError, CSRFProtect

import admins
import availability as av
import bookings
from brain import get_ai_response
from config import settings
from database import (audit, get_admin_bookings, get_admin_bookings_for_export,
                      get_admin_stats, init_db, recent_audit)
from security import clear_login_failures, is_locked, record_login_failure
from services import qr

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=["300 per hour", "60 per minute"])


def _bootstrap_rag() -> None:
    if not settings.rag_autobuild or os.path.exists("chroma_db"):
        return
    try:
        from rag_store import build_index
        logger.info("Building RAG index for the first time...")
        build_index()
    except Exception as exc:
        logger.warning("RAG index not built (is Ollama running?): %s", exc)


def _warm_up_llm() -> None:
    """Load the LLM in the background so the first caller does not hit a cold start."""
    import threading
    from brain import warm_up_llm
    threading.Thread(target=warm_up_llm, daemon=True).start()


def create_app() -> Flask:
    if len(settings.flask_secret_key) < 32:
        raise RuntimeError(
            "FLASK_SECRET_KEY missing or too short (32+ chars required). Generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    app = Flask(__name__)
    app.secret_key = settings.flask_secret_key
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.is_production,
        SESSION_COOKIE_NAME="vaani_sess",
        PERMANENT_SESSION_LIFETIME=3600,
        WTF_CSRF_TIME_LIMIT=3600,
        MAX_CONTENT_LENGTH=64 * 1024,
        RATELIMIT_STORAGE_URI=settings.ratelimit_storage_uri,
    )
    csrf.init_app(app)
    limiter.init_app(app)
    init_db()
    _register_routes(app)
    return app


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _client_ip() -> str:
    return get_remote_address() or "unknown"


def _actor() -> str:
    return session.get("admin_user", "anonymous")


def require_role(*roles):
    """Admin login required; when roles are given, the admin must hold one of them."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("admin_user"):
                return redirect(url_for("admin_login", next=request.full_path))
            if roles and session.get("admin_role") not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _parse_admin_filters(source) -> dict:
    status = (source.get("status") or "all").strip().lower()
    if status not in ("all", "confirmed", "cancelled"):
        status = "all"

    dates = {}
    for key in ("date_from", "date_to"):
        value = (source.get(key) or "").strip()
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            value = ""
        dates[key] = value

    try:
        page = max(1, int(source.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(source.get("page_size") or 10)
    except (TypeError, ValueError):
        page_size = 10
    if page_size not in (10, 20, 50, 100):
        page_size = 10

    return {"query": (source.get("query") or "").strip()[:200], "status": status,
            **dates, "page": page, "page_size": page_size}


def _safe_next(target: str, fallback: str) -> str:
    """Only allow relative redirects inside /admin (prevents open redirects)."""
    target = (target or "").strip()
    if target.startswith("/admin") and not target.startswith("//") and "\\" not in target:
        return target
    return fallback


# ── ROUTES ────────────────────────────────────────────────────────────────────
def _register_routes(app: Flask) -> None:

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.route("/health")
    @csrf.exempt
    def health():
        return jsonify({"status": "ok", "ts": datetime.now(timezone.utc).isoformat()})

    # ── Voice assistant ──────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return render_template("index.html", restaurant_name=settings.restaurant_name)

    @app.route("/send_message", methods=["POST"])
    @csrf.exempt  # JSON API: protected by SameSite cookie + rate limit
    @limiter.limit("60 per minute; 300 per hour")
    def send_message():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON body"}), 400
        msg = (data.get("message") or "").strip()
        if not msg:
            return jsonify({"error": "message is required"}), 400
        if len(msg) > 500:
            return jsonify({"error": "message too long (max 500 chars)"}), 400

        state = session.get("state") or {}
        try:
            reply, audio, new_state = get_ai_response(msg, state)
        except Exception:
            logger.exception("get_ai_response crashed")
            return jsonify({"error": "Internal server error"}), 500

        session["state"] = new_state
        session.modified = True
        return jsonify({"response": reply, "audio": audio})

    @app.route("/reset_state", methods=["POST"])
    @csrf.exempt
    @limiter.limit("30 per minute")
    def reset_state():
        session["state"] = {}
        return jsonify({"ok": True})

    # ── QR image (unguessable signed URL, generated on the fly) ──────────────
    @app.route("/qr/<path:qr_value>.png")
    @limiter.limit("30 per minute")
    def qr_image(qr_value):
        if not bookings.lookup_by_qr(qr_value):
            abort(404)
        png = qr.png_bytes(bookings.checkin_url(qr_value))
        return Response(png, mimetype="image/png", headers={"Cache-Control": "private, max-age=86400"})

    # ── Admin auth ───────────────────────────────────────────────────────────
    @app.route("/admin/login", methods=["GET", "POST"])
    @limiter.limit("20 per minute")
    def admin_login():
        no_admins = admins.count_admins() == 0
        next_url = _safe_next(request.args.get("next"), url_for("admin"))
        if request.method == "GET":
            return render_template("admin_login.html", no_admins=no_admins)

        username = (request.form.get("username") or "").strip().lower()
        ip_key, user_key = f"ip:{_client_ip()}", f"user:{username}"
        if is_locked(ip_key) or is_locked(user_key):
            return render_template("admin_login.html", no_admins=no_admins,
                                   error="Too many failed attempts. Try again in a few minutes."), 429

        admin = admins.authenticate(username, request.form.get("password", ""),
                                    request.form.get("otp", ""))
        if not admin:
            record_login_failure(ip_key)
            record_login_failure(user_key)
            logger.warning("Admin login failure for %r from %s", username, _client_ip())
            return render_template("admin_login.html", no_admins=no_admins,
                                   error="Incorrect username, password or code."), 401

        clear_login_failures(ip_key)
        clear_login_failures(user_key)
        session.clear()
        session.permanent = True
        session["admin_user"] = admin.username
        session["admin_role"] = admin.role
        audit(admin.username, "login", ip=_client_ip())
        return redirect(next_url)

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        if session.get("admin_user"):
            audit(_actor(), "logout", ip=_client_ip())
        session.clear()
        return redirect(url_for("admin_login"))

    # ── Admin dashboard ──────────────────────────────────────────────────────
    @app.route("/admin")
    @require_role()
    def admin():
        f = _parse_admin_filters(request.args)
        query = {k: f[k] for k in ("query", "status", "date_from", "date_to")}
        rows, total = get_admin_bookings(**query, page=f["page"], page_size=f["page_size"])
        total_pages = max(1, (total - 1) // f["page_size"] + 1)
        if f["page"] > total_pages:
            f["page"] = total_pages
            rows, total = get_admin_bookings(**query, page=f["page"], page_size=f["page_size"])

        return render_template(
            "admin.html", bookings=rows, filters=f, page=f["page"], page_size=f["page_size"],
            total=total, total_pages=total_pages,
            stats=get_admin_stats(today=av.today().isoformat(), **query),
            role=session.get("admin_role"), admin_user=_actor(),
            audit_rows=recent_audit() if session.get("admin_role") == "owner" else [],
        )

    @app.route("/admin/cancel/<int:booking_id>", methods=["POST"])
    @require_role()
    def admin_cancel_route(booking_id: int):
        if bookings.admin_cancel(booking_id):
            audit(_actor(), "cancel", f"booking:{booking_id}", _client_ip())
        return redirect(_safe_next(request.args.get("next"), url_for("admin")))

    @app.route("/admin/restore/<int:booking_id>", methods=["POST"])
    @require_role()
    def admin_restore_route(booking_id: int):
        result = bookings.restore(booking_id)
        if result == "ok":
            audit(_actor(), "restore", f"booking:{booking_id}", _client_ip())
        elif result == "clash":
            audit(_actor(), "restore_blocked", f"booking:{booking_id}", _client_ip())
        return redirect(_safe_next(request.args.get("next"), url_for("admin")))

    @app.route("/admin/export")
    @require_role("owner")
    def admin_export():
        f = _parse_admin_filters(request.args)
        rows = get_admin_bookings_for_export(query=f["query"], status=f["status"],
                                             date_from=f["date_from"], date_to=f["date_to"])
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Date", "Time", "Guests", "Name", "Phone", "Status", "Table", "Checked in"])
        writer.writerows(rows)
        audit(_actor(), "export", f"{len(rows)} rows", _client_ip())
        filename = f"bookings_export_{datetime.now():%Y%m%d_%H%M%S}.csv"
        return Response(output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    # ── Check-in (QR code opens this page on a staff phone) ──────────────────
    @app.route("/admin/checkin/<path:qr_value>", methods=["GET", "POST"])
    @require_role()
    def admin_checkin(qr_value):
        booking = bookings.lookup_by_qr(qr_value)
        if not booking:
            return render_template("checkin.html", booking=None, error="This QR code is not valid."), 404
        message = ""
        if request.method == "POST":
            if bookings.check_in(booking["id"]):
                audit(_actor(), "check_in", f"booking:{booking['id']}", _client_ip())
                message = "Guest checked in."
            booking = bookings.lookup_by_qr(qr_value)
        return render_template("checkin.html", booking=booking, message=message, error="")

    # ── Errors ───────────────────────────────────────────────────────────────
    def _wants_html() -> bool:
        return request.path.startswith("/admin") or request.accept_mimetypes.accept_html

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        logger.warning("CSRF validation failed: %s | path=%s", e, request.path)
        if request.path.startswith("/admin"):
            return render_template("admin_login.html", no_admins=False,
                                   error="Session expired. Please log in again."), 400
        return jsonify({"error": "CSRF validation failed"}), 400

    @app.errorhandler(429)
    def ratelimit_error(e):
        if request.path.startswith("/admin"):
            return render_template("admin_login.html", no_admins=False, error=str(e.description)), 429
        return jsonify({"error": "Too many requests. Please slow down."}), 429

    @app.errorhandler(403)
    def forbidden(_e):
        if _wants_html():
            return render_template("error.html", code=403, message="You do not have access to this page."), 403
        return jsonify({"error": "Forbidden"}), 403

    @app.errorhandler(404)
    def not_found(_e):
        if _wants_html():
            return render_template("error.html", code=404, message="Page not found."), 404
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def server_error(_e):
        if _wants_html():
            return render_template("error.html", code=500, message="Something went wrong."), 500
        return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    _bootstrap_rag()
    _warm_up_llm()
    application = create_app()
    application.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
                    host="127.0.0.1", port=int(os.getenv("PORT", 5000)))
