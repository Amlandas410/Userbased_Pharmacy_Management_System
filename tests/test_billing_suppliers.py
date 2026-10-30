import datetime
import json
from pathlib import Path

from flask import Flask, session

import app.views as views
from app.auth import auth_bp


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.last_query = ""
        self.lastrowid = 41
        self.rowcount = 1

    def execute(self, query, params=None):
        self.last_query = query
        self.connection.queries.append((query, params))
        self.connection.in_transaction = True

    def fetchone(self):
        if "FROM users WHERE id=" in self.last_query:
            return {"id": 7, "role": "owner", "is_active": 1}
        if "FROM customers WHERE phone=" in self.last_query:
            return None
        return None

    def fetchall(self):
        if "FROM inventory_batches b JOIN medicines m" in self.last_query:
            return [{
                "id": 9,
                "name": "Medicine",
                "batch_number": "B-1",
                "expiry_date": datetime.date.today() + datetime.timedelta(days=30),
                "selling_price": 12,
                "purchase_price": 8,
                "gst_percent": 5,
                "quantity": 10,
                "prescription_required": 0,
            }]
        return []

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.queries = []
        self.in_transaction = False

    def cursor(self, dictionary=False):
        return FakeCursor(self)

    def commit(self):
        self.in_transaction = False

    def rollback(self):
        self.in_transaction = False

    def start_transaction(self):
        if self.in_transaction:
            raise RuntimeError("Transaction already in progress")
        self.in_transaction = True


def make_app(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(views, "tenant_db", lambda: connection)
    app = Flask(
        "test",
        template_folder=str(Path(__file__).parents[1] / "app" / "templates"),
    )
    app.secret_key = "test"
    app.register_blueprint(views.main_bp)
    app.register_blueprint(auth_bp)
    app.jinja_env.globals["csrf_token"] = lambda: "test-token"

    @app.context_processor
    def template_context():
        return {"current_role": session.get("role"), "current_pharmacy": "Test pharmacy"}

    return app, connection


def sign_in(client):
    with client.session_transaction() as user_session:
        user_session.update(user_id=7, role="owner", pharmacy_id=1)


def test_bill_completes_after_guard_closes_its_read_transaction(monkeypatch):
    app, connection = make_app(monkeypatch)

    with app.test_client() as client:
        sign_in(client)
        response = client.post(
            "/billing",
            data={
                "items_json": json.dumps([{"batch_id": 9, "quantity": 2}]),
                "customer_name": "Walk-in customer",
                "discount": "0",
            },
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/bills/41")
    assert any(query.startswith("UPDATE inventory_batches SET quantity=") for query, _ in connection.queries)
    assert any(query.startswith("INSERT INTO sale_items") for query, _ in connection.queries)
    assert not connection.in_transaction


def test_supplier_validation_rejects_bad_fields_without_inserting(monkeypatch):
    app, connection = make_app(monkeypatch)

    with app.test_client() as client:
        sign_in(client)
        response = client.post(
            "/suppliers",
            data={
                "name": " ",
                "contact_name": "Bad2Name",
                "email": "not-an-email",
                "phone": "abc",
                "address": "Bad<>Address",
            },
        )

    assert response.status_code == 200
    assert b"Supplier name must be" in response.data
    assert b"valid supplier email" in response.data
    assert b"valid supplier phone" in response.data
    assert b"Contact person must contain only letters and spaces." in response.data
    assert not any(query.startswith("INSERT INTO suppliers") for query, _ in connection.queries)


def test_supplier_validation_accepts_valid_optional_contact_details(monkeypatch):
    app, connection = make_app(monkeypatch)

    with app.test_client() as client:
        sign_in(client)
        response = client.post(
            "/suppliers",
            data={
                "name": "Trusted Supplier",
                "contact_name": "A Contact",
                "email": "sales@example.com",
                "phone": "9876543210",
                "address": "12 Market Road\nBengaluru",
            },
        )

    assert response.status_code == 200
    assert b"Supplier saved." in response.data
    inserts = [params for query, params in connection.queries if query.startswith("INSERT INTO suppliers")]
    assert inserts == [("Trusted Supplier", "A Contact", "sales@example.com", "9876543210", "12 Market Road\nBengaluru")]
