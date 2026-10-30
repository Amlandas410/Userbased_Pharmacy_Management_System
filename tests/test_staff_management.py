from flask import Flask

import app.views as views
from werkzeug.security import check_password_hash


class StaffCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=None):
        self.connection.queries.append((query, params))
        self.query = query

    def fetchone(self):
        return {"id": 2, "role": "billing"}

    def fetchall(self):
        return []

    def close(self):
        pass


class StaffConnection:
    def __init__(self):
        self.queries = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, dictionary=False):
        return StaffCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def make_client(monkeypatch, connection, messages):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(views.main_bp)
    monkeypatch.setattr(views, "tenant_db", lambda: connection)
    monkeypatch.setattr(views, "flash", lambda message, category="message": messages.append((message, category)))
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session.update(user_id=1, role="admin")
    return client


def test_admin_can_edit_staff_profile_role_and_access(monkeypatch):
    connection = StaffConnection()
    messages = []
    client = make_client(monkeypatch, connection, messages)

    response = client.post("/staff/2/edit", data={
        "full_name": "Updated Staff",
        "email": "updated@example.com",
        "role": "pharmacist",
        "is_active": "0",
    })

    assert response.status_code == 302
    assert ("UPDATE users SET full_name=%s,email=%s,role=%s,is_active=%s WHERE id=%s",
            ("Updated Staff", "updated@example.com", "pharmacist", False, 2)) in connection.queries
    assert connection.commits == 1
    assert any("updated" in message.lower() for message, _ in messages)


def test_admin_can_change_staff_password(monkeypatch):
    connection = StaffConnection()
    messages = []
    client = make_client(monkeypatch, connection, messages)

    response = client.post("/staff/2/edit", data={
        "full_name": "Updated Staff",
        "email": "updated@example.com",
        "role": "pharmacist",
        "is_active": "1",
        "password": "new-strong-password",
    })

    assert response.status_code == 302
    query, params = next(
        (query, params) for query, params in connection.queries
        if query.startswith("UPDATE users SET full_name")
    )
    assert "password_hash=%s" in query
    assert params[:4] == ("Updated Staff", "updated@example.com", "pharmacist", True)
    assert check_password_hash(params[4], "new-strong-password")
    assert params[5] == 2
    assert connection.commits == 1
    assert any("password updated" in message.lower() for message, _ in messages)


def test_admin_cannot_set_short_staff_password(monkeypatch):
    connection = StaffConnection()
    messages = []
    client = make_client(monkeypatch, connection, messages)

    response = client.post("/staff/2/edit", data={
        "full_name": "Updated Staff",
        "email": "updated@example.com",
        "role": "billing",
        "is_active": "1",
        "password": "short",
    })

    assert response.status_code == 302
    assert connection.queries == []
    assert connection.commits == 0
    assert any("12 characters" in message for message, _ in messages)


def test_admin_cannot_edit_or_deactivate_own_account(monkeypatch):
    connection = StaffConnection()
    messages = []
    client = make_client(monkeypatch, connection, messages)

    response = client.post("/staff/1/edit", data={
        "full_name": "Admin",
        "email": "admin@example.com",
        "role": "admin",
        "is_active": "0",
    })

    assert response.status_code == 302
    assert connection.queries == []
    assert connection.commits == 0
    assert any("own account" in message.lower() for message, _ in messages)
