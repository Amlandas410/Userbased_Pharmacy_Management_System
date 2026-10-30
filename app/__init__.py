import os
import secrets
from datetime import timedelta

from flask import Flask, g, redirect, session, url_for
from dotenv import load_dotenv

from .extensions import csrf, limiter
from .db import close_db, init_platform, platform_db
from .auth import auth_bp
from .views import main_bp

load_dotenv()


def create_app():
    app = Flask(__name__)
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    secret_key = os.getenv("SECRET_KEY", "").strip()
    if len(secret_key) < 32 or "replace-with" in secret_key.lower():
        if app_env == "production":
            raise RuntimeError("Set SECRET_KEY to a unique random value of at least 32 characters before production startup.")
        secret_key = secrets.token_urlsafe(48)
        app.logger.warning("SECRET_KEY is not configured; using an ephemeral development key. Set SECRET_KEY in .env to keep sessions across restarts.")

    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_REFRESH_EACH_REQUEST=False,
        RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    csrf.init_app(app)
    limiter.init_app(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    @app.before_request
    def tenant_context():
        g.tenant_db_name = None
        if session.get("pharmacy_id"):
            connection = platform_db()
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute("SELECT id,status,database_name FROM pharmacies WHERE id=%s", (session["pharmacy_id"],))
                tenant = cursor.fetchone()
            finally:
                cursor.close()
                connection.close()
            if not tenant or tenant["status"] != "active":
                session.clear()
                return redirect(url_for("auth.login"))
            g.tenant_db_name = tenant["database_name"]

    @app.teardown_appcontext
    def teardown(error):
        close_db(error)

    @app.context_processor
    def context():
        return {"current_pharmacy": session.get("pharmacy_name"), "current_role": session.get("role")}

    @app.errorhandler(403)
    def forbidden(error):
        return "You do not have permission to access this page.", 403

    @app.errorhandler(404)
    def missing(error):
        return "Page not found.", 404

    with app.app_context():
        init_platform()
    return app
