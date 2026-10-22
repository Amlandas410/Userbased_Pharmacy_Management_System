import os
from datetime import timedelta
from flask import Flask, session, g, redirect, url_for
from dotenv import load_dotenv
from .extensions import csrf, limiter
from .db import init_platform, platform_db, close_db
from .auth import auth_bp
from .views import main_bp

load_dotenv()
def create_app():
    app=Flask(__name__)
    app.config.update(SECRET_KEY=os.getenv("SECRET_KEY","unsafe-development-secret-change-me"),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE","false").lower()=="true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=2*1024*1024)
    csrf.init_app(app); limiter.init_app(app)
    app.register_blueprint(auth_bp); app.register_blueprint(main_bp)
    @app.before_request
    def tenant_context():
        g.tenant_db_name=None
        if session.get("pharmacy_id"):
            c=platform_db(); cur=c.cursor(dictionary=True)
            cur.execute("SELECT id,status,database_name FROM pharmacies WHERE id=%s",(session["pharmacy_id"],))
            t=cur.fetchone(); cur.close(); c.close()
            if not t or t["status"]!="active":
                session.clear(); return redirect(url_for("auth.login"))
            g.tenant_db_name=t["database_name"]
    @app.teardown_appcontext
    def teardown(error): close_db(error)
    @app.context_processor
    def context(): return {"current_pharmacy":session.get("pharmacy_name"),"current_role":session.get("role")}
    @app.errorhandler(403)
    def forbidden(e): return "You do not have permission to access this page.",403
    @app.errorhandler(404)
    def missing(e): return "Page not found.",404
    with app.app_context(): init_platform()
    return app
