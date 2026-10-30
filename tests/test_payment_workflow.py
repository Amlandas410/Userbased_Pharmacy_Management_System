from flask import Flask

import app.views as views


class PaymentCursor:
    def __init__(self, connection):
        self.connection = connection
        self.last_query = ""
        self.last_params = None

    def execute(self, query, params=None):
        self.last_query = query
        self.last_params = params
        self.connection.queries.append((query, params))

    def fetchone(self):
        if "SELECT id,bill_number,total_amount,amount_paid,payment_status FROM sales" in self.last_query:
            return self.connection.sale
        return None

    def close(self):
        pass


class PaymentConnection:
    def __init__(self, sale):
        self.sale = sale
        self.queries = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, dictionary=False):
        return PaymentCursor(self)

    def start_transaction(self):
        pass

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
    with client.session_transaction() as sess:
        sess["user_id"] = 2
        sess["role"] = "billing"
    return client


def test_payment_can_be_recorded_as_partial(monkeypatch):
    sale = {"id": 21, "bill_number": "PH-21", "total_amount": 100, "amount_paid": 0, "payment_status": "unpaid"}
    connection = PaymentConnection(sale)
    messages = []
    client = make_client(monkeypatch, connection, messages)
    response = client.post("/bills/21/payments", data={
        "payment_amount": "35.50", "payment_method": "upi",
        "reference_note": "UPI-123", "payment_token": "abcdefghijklmnopqrstuvwx12345678",
    })
    assert response.status_code == 302
    updates = [params for query, params in connection.queries if query.startswith("UPDATE sales SET amount_paid")]
    assert updates == [(views.decimal.Decimal("35.50"), "partial", "upi", None, 21)]
    assert connection.commits == 1
    assert any("now partial" in message for message, _ in messages)


def test_payment_rejects_amount_above_outstanding_balance(monkeypatch):
    sale = {"id": 22, "bill_number": "PH-22", "total_amount": 100, "amount_paid": 80, "payment_status": "partial"}
    connection = PaymentConnection(sale)
    messages = []
    client = make_client(monkeypatch, connection, messages)
    response = client.post("/bills/22/payments", data={
        "payment_amount": "21.00", "payment_method": "cash",
        "payment_token": "abcdefghijklmnopqrstuvwx12345678",
    })
    assert response.status_code == 302
    assert any("cannot exceed" in message.lower() for message, _ in messages)
    assert not any("INSERT INTO bill_payments" in query for query, _ in connection.queries)
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_payment_rejects_duplicate_or_invalid_payment_method_before_writing(monkeypatch):
    sale = {"id": 23, "bill_number": "PH-23", "total_amount": 100, "amount_paid": 0, "payment_status": "unpaid"}
    connection = PaymentConnection(sale)
    messages = []
    client = make_client(monkeypatch, connection, messages)
    response = client.post("/bills/23/payments", data={
        "payment_amount": "10", "payment_method": "bitcoin",
        "payment_token": "abcdefghijklmnopqrstuvwx12345678",
    })
    assert response.status_code == 302
    assert any("payment method" in message.lower() for message, _ in messages)
    assert not any("INSERT INTO bill_payments" in query for query, _ in connection.queries)
    assert connection.commits == 0
