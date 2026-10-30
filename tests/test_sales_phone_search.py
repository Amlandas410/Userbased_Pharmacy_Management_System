from flask import Flask

import app.views as views


class SalesCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=None):
        self.connection.queries.append((query, params))

    def fetchall(self):
        return []

    def close(self):
        pass


class SalesConnection:
    def __init__(self):
        self.queries = []

    def cursor(self, dictionary=False):
        return SalesCursor(self)


def make_client(monkeypatch, connection, rendered):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(views.main_bp)
    monkeypatch.setattr(views, "tenant_db", lambda: connection)
    monkeypatch.setattr(views, "render_template", lambda template, **kwargs: rendered.update(kwargs) or "sales-page")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["role"] = "billing"
    return client


def test_sales_search_matches_ten_digit_phone_number(monkeypatch):
    connection = SalesConnection()
    rendered = {}
    client = make_client(monkeypatch, connection, rendered)

    response = client.get("/sales?phone=9876543210")

    assert response.status_code == 200
    query, params = connection.queries[0]
    assert "REGEXP_REPLACE(COALESCE(c.phone" in query
    assert params == ("9876543210",)
    assert rendered["search_phone"] == "9876543210"
    assert rendered["search_error"] is None


def test_sales_search_rejects_invalid_phone_without_querying_database(monkeypatch):
    connection = SalesConnection()
    rendered = {}
    client = make_client(monkeypatch, connection, rendered)

    response = client.get("/sales?phone=12345")

    assert response.status_code == 200
    assert connection.queries == []
    assert rendered["rows"] == []
    assert "10-digit mobile number" in rendered["search_error"]


def test_sales_search_rejects_formatted_phone_number(monkeypatch):
    connection = SalesConnection()
    rendered = {}
    client = make_client(monkeypatch, connection, rendered)

    response = client.get("/sales?phone=98765%2043210")

    assert response.status_code == 200
    assert connection.queries == []
    assert "10-digit mobile number" in rendered["search_error"]
