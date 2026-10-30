from flask import Flask
from pathlib import Path

from app.auth import auth_bp
from app.extensions import csrf, limiter
from app.views import main_bp


def test_login_page_renders_the_refreshed_pharmacy_login_layout():
    project_root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(project_root / "app" / "templates"),
                static_folder=str(project_root / "app" / "static"))
    app.config.update(TESTING=True, SECRET_KEY="test-secret-key-for-login-page-123456", RATELIMIT_STORAGE_URI="memory://")
    csrf.init_app(app)
    limiter.init_app(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    with app.test_client() as client:
        response = client.get("/login")

    assert response.status_code == 200
    assert b"login-layout" in response.data
    assert b"More clarity." in response.data
    assert b"Create an account" in response.data
    assert b'name="email"' in response.data
    assert b'name="pharmacy_email"' in response.data
