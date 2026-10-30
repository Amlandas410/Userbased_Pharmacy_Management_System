import json
from flask import Flask

import app.views as views


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.last_query = ""
        self.last_params = None

    def execute(self, query, params=None):
        self.last_query = query
        self.last_params = params
        self.connection.queries.append((query, params))

    def fetchone(self):
        if "GET_LOCK" in self.last_query:
            return {"acquired": 1}
        return None

    def fetchall(self):
        if "SELECT id,full_name FROM customers" in self.last_query:
            return [{"id": 8, "full_name": "Returning Customer"}]
        if "SELECT id,bill_number,total_amount,amount_paid,payment_status FROM sales" in self.last_query:
            return [
                {"id": 42, "bill_number": "PH-OLD-001", "total_amount": 500, "amount_paid": 200, "payment_status": "partial"},
                {"id": 43, "bill_number": "PH-OLD-002", "total_amount": 250, "amount_paid": 0, "payment_status": "unpaid"},
            ]
        if "SELECT b.id batch_id" in self.last_query:
            return []
        return []

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.queries = []
        self.rollbacks = 0
        self.commits = 0

    def cursor(self, dictionary=False):
        return FakeCursor(self)

    def start_transaction(self):
        pass

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1


def test_server_redirects_to_previous_bill_when_customer_has_outstanding_balance(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(views.main_bp)
    connection = FakeConnection()
    messages = []
    monkeypatch.setattr(views, "tenant_db", lambda: connection)
    monkeypatch.setattr(views, "render_template", lambda *args, **kwargs: "billing-page")
    monkeypatch.setattr(views, "flash", lambda message, category="message": messages.append((message, category)))

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["role"] = "billing"

    response = client.post("/billing", data={
        "customer_name": "Returning Customer",
        "customer_phone": "9876543210",
        "discount": "0.00",
        "items_json": json.dumps([{"batch_id": 10, "quantity": 1}]),
    })

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/bills/42")
    assert any("PH-OLD-001" in message for message, _ in messages)
    assert not any("INSERT INTO sales(" in query for query, _ in connection.queries)
    assert connection.rollbacks >= 1
    assert connection.commits == 0
