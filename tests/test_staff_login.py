from werkzeug.security import generate_password_hash
from flask import Flask, session

import app.auth as auth
from app.extensions import csrf, limiter
from app.views import main_bp


class PlatformCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=None):
        self.connection.params = params

    def fetchone(self):
        return {
            "id": 7,
            "pharmacy_name": "Test Pharmacy",
            "database_name": "ph_test",
            "status": "active",
        }

    def close(self):
        pass


class PlatformConnection:
    def cursor(self, dictionary=False):
        return PlatformCursor(self)

    def close(self):
        pass


class TenantCursor:
    def execute(self, query, params=None):
        self.params = params

    def fetchone(self):
        return {
            "id": 3,
            "full_name": "Staff User",
            "email": "staff@example.com",
            "password_hash": generate_password_hash("valid-test-password"),
            "role": "billing",
            "is_active": True,
        }

    def close(self):
        pass


class TenantConnection:
    def cursor(self, dictionary=False):
        return TenantCursor()

    def close(self):
        pass


def test_staff_can_sign_in_using_pharmacy_owner_email(monkeypatch):
    platform = PlatformConnection()
    monkeypatch.setattr(auth, "platform_db", lambda: platform)
    monkeypatch.setattr(auth, "tenant_db_name", lambda database_name: TenantConnection())

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test-secret-key-for-staff-login-123456", WTF_CSRF_ENABLED=False)
    csrf.init_app(app)
    limiter.init_app(app)
    app.register_blueprint(auth.auth_bp)
    app.register_blueprint(main_bp)

    with app.test_client() as client:
        response = client.post("/login", data={
            "email": "staff@example.com",
            "pharmacy_email": "owner@example.com",
            "password": "valid-test-password",
        })

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard")
        with client.session_transaction() as user_session:
            assert user_session["user_id"] == 3
            assert user_session["role"] == "billing"
            assert user_session["pharmacy_id"] == 7

    assert platform.params == ("owner@example.com",)
