import datetime

import pytest
from flask import Flask, render_template, session

from app.auth import auth_bp
import app.views as views


class FakeCursor:
    def __init__(self, medicine, queries):
        self.medicine = medicine
        self.queries = queries
        self.lastrowid = 7
        self.rowcount = 1
        self.last_query = ""

    def execute(self, query, params=None):
        self.last_query = query
        self.queries.append((query, params))

    def fetchone(self):
        return self.medicine

    def fetchall(self):
        if "SELECT b.id batch_id,m.name medicine_name" in self.last_query:
            return [{
                "batch_id": self.medicine["batch_id"],
                "medicine_name": self.medicine["name"],
                "batch_number": self.medicine["batch_number"],
                "expiry_date": self.medicine["expiry_date"],
                "quantity": self.medicine["quantity"],
            }]
        if "b.quantity>0 AND b.quantity<=b.reorder_level" in self.last_query:
            return [self.medicine] if 0 < self.medicine["quantity"] <= self.medicine["reorder_level"] else []
        if "b.quantity>0 AND b.expiry_date<CURDATE()" in self.last_query:
            return [self.medicine] if self.medicine["quantity"] > 0 and self.medicine["expiry_date"] < datetime.date.today() else []
        return [self.medicine]

    def close(self):
        pass


class FakeConnection:
    def __init__(self, medicine):
        self.medicine = medicine
        self.queries = []

    def cursor(self, dictionary=False):
        assert dictionary
        return FakeCursor(self.medicine, self.queries)

    def start_transaction(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


@pytest.fixture
def medicine_app(monkeypatch):
    medicine = {
        "id": 12,
        "medicine_id": 12,
        "name": "Pain relief",
        "category_id": 3,
        "category": "Analgesic",
        "manufacturer": "Maker",
        "barcode": "12345",
        "batch_id": 25,
        "batch_number": "B-1",
        "expiry_date": datetime.date.today() + datetime.timedelta(days=30),
        "purchase_price": 5,
        "selling_price": 8,
        "quantity": 20,
        "reorder_level": 4,
        "gst_percent": 5,
    }
    connection = FakeConnection(medicine)
    audit_calls = []

    def record_audit(*args, **kwargs):
        audit_calls.append((args, kwargs))

    monkeypatch.setattr(views, "tenant_db", lambda: connection)
    monkeypatch.setattr(views, "audit", record_audit)
    app = Flask("app")
    app.secret_key = "test"
    app.register_blueprint(views.main_bp)
    app.register_blueprint(auth_bp)

    @app.context_processor
    def template_context():
        return {"current_role": session.get("role"), "current_pharmacy": "Test pharmacy"}

    app.jinja_env.globals["csrf_token"] = lambda: "test-token"
    return app, connection


@pytest.mark.parametrize("role", ["admin", "pharmacist", "billing"])
def test_only_owner_can_access_medicine_edit(medicine_app, monkeypatch, role):
    app, _ = medicine_app
    monkeypatch.setattr(views, "tenant_db", lambda: pytest.fail("unauthorized request reached the database"))

    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(user_id=2, role=role)
        get_response = client.get("/medicines/batches/25/edit")
        post_response = client.post("/medicines/batches/25/edit", data={"name": "Changed"})

    assert get_response.status_code == 403
    assert post_response.status_code == 403


def test_owner_can_view_medicine_edit_form(medicine_app):
    app, _ = medicine_app

    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(user_id=1, role="owner")
        response = client.get("/medicines/batches/25/edit")

    assert response.status_code == 200
    assert b'value="Pain relief"' in response.data
    assert b'value="B-1"' in response.data
    assert b"Save changes" in response.data


def test_add_medicine_form_offers_new_and_restock_options(medicine_app):
    app, _ = medicine_app

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session.update(user_id=2, role="pharmacist")
        response = client.get("/medicines/add")

    assert response.status_code == 200
    assert b"Add medicine &amp; batch" in response.data
    assert b"Restock existing medicine" in response.data
    assert b"/medicines/restock" in response.data


def test_staff_can_view_restock_form_with_existing_batches(medicine_app):
    app, _ = medicine_app

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session.update(user_id=2, role="pharmacist")
        response = client.get("/medicines/restock")

    assert response.status_code == 200
    assert b"Restock existing medicine" in response.data
    assert b"Pain relief" in response.data
    assert b"Batch B-1" in response.data
    assert b"Current stock 20" in response.data


def test_restock_adds_quantity_to_selected_batch(medicine_app):
    app, connection = medicine_app

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session.update(user_id=2, role="pharmacist")
        response = client.post("/medicines/restock", data={"batch_id": "25", "quantity": "12"})

    updates = [(query, params) for query, params in connection.queries if query.startswith("UPDATE")]
    assert response.status_code == 302
    assert len(updates) == 1
    assert "SET b.quantity=b.quantity+%s" in updates[0][0]
    assert updates[0][1] == (12, 25, 12)


def test_restock_requires_a_positive_quantity(medicine_app):
    app, connection = medicine_app

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session.update(user_id=2, role="pharmacist")
        response = client.post("/medicines/restock", data={"batch_id": "25", "quantity": "0"})

    assert response.status_code == 302
    assert not any(query.startswith("UPDATE") for query, _ in connection.queries)


def test_billing_staff_cannot_restock_medicine(medicine_app, monkeypatch):
    app, _ = medicine_app
    monkeypatch.setattr(views, "tenant_db", lambda: pytest.fail("unauthorized request reached the database"))

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session.update(user_id=3, role="billing")
        response = client.post("/medicines/restock", data={"batch_id": "25", "quantity": "10"})

    assert response.status_code == 403


@pytest.mark.parametrize(("role", "can_edit"), [("owner", True), ("admin", False), ("pharmacist", False), ("billing", False)])
def test_only_owner_sees_medicine_edit_action(medicine_app, role, can_edit):
    app, _ = medicine_app

    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(user_id=1, role=role)
        response = client.get("/medicines")

    assert response.status_code == 200
    assert (b">Edit</a>" in response.data) is can_edit
    assert (b">Delete</button>" in response.data) is can_edit


@pytest.mark.parametrize(
    ("status", "quantity", "expiry", "expected_name", "heading"),
    [
        ("low_stock", 2, datetime.date.today() + datetime.timedelta(days=30), True, b"Low stock"),
        ("low_stock", 20, datetime.date.today() + datetime.timedelta(days=30), False, b"Low stock"),
        ("expired", 5, datetime.date.today() - datetime.timedelta(days=1), True, b"Expired batches"),
        ("expired", 5, datetime.date.today() + datetime.timedelta(days=30), False, b"Expired batches"),
    ],
)
def test_medicine_list_shows_only_selected_stock_status(medicine_app, status, quantity, expiry, expected_name, heading):
    app, connection = medicine_app
    connection.medicine["quantity"] = quantity
    connection.medicine["expiry_date"] = expiry

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session.update(user_id=1, role="owner")
        response = client.get(f"/medicines?status={status}")

    query = next(query for query, _ in connection.queries if query.startswith("SELECT m.id"))
    assert response.status_code == 200
    assert heading in response.data
    assert (b"Pain relief" in response.data) is expected_name
    if status == "low_stock":
        assert "b.quantity>0 AND b.quantity<=b.reorder_level" in query
    else:
        assert "b.quantity>0 AND b.expiry_date<CURDATE()" in query


def test_dashboard_stock_cards_link_to_filtered_inventory(medicine_app):
    app, _ = medicine_app

    with app.test_request_context("/dashboard"):
        session.update(user_id=1, role="owner", user_name="Owner")
        response = render_template(
            "dashboard.html",
            stock={"medicine_count": 1, "total_stock": 20, "inventory_cost": 100, "inventory_retail": 150},
            today={"revenue": 0, "collected": 0, "outstanding": 0, "bills": 0, "profit": 0},
            low=1,
            expired=1,
            recent=[],
        )

    assert 'href="/medicines?status=low_stock"' in response
    assert 'href="/medicines?status=expired"' in response


@pytest.mark.parametrize("role", ["admin", "pharmacist", "billing"])
def test_only_owner_can_delete_medicine(medicine_app, monkeypatch, role):
    app, _ = medicine_app
    monkeypatch.setattr(views, "tenant_db", lambda: pytest.fail("unauthorized request reached the database"))

    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(user_id=2, role=role)
        response = client.post("/medicines/12/delete")

    assert response.status_code == 403


def test_owner_deletes_medicine_without_removing_historical_batches(medicine_app):
    app, connection = medicine_app

    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(user_id=1, role="owner")
        response = client.post("/medicines/12/delete")

    updates = [(query, params) for query, params in connection.queries if query.startswith("UPDATE")]
    assert response.status_code == 302
    assert updates == [("UPDATE medicines SET is_active=0 WHERE id=%s AND is_active=1", (12,))]


def test_owner_can_update_medicine_and_batch_details(medicine_app):
    app, connection = medicine_app
    expiry = (datetime.date.today() + datetime.timedelta(days=60)).isoformat()
    form = {
        "name": "Updated medicine",
        "category": "Updated category",
        "manufacturer": "Updated maker",
        "barcode": "67890",
        "batch_number": "B-2",
        "expiry_date": expiry,
        "purchase_price": "6.25",
        "selling_price": "9.50",
        "quantity": "15",
        "reorder_level": "5",
        "gst_percent": "5",
    }

    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(user_id=1, role="owner")
        response = client.post("/medicines/batches/25/edit", data=form)

    updates = [(query, params) for query, params in connection.queries if query.startswith("UPDATE")]
    assert response.status_code == 302
    assert len(updates) == 2
    assert "UPDATE medicines SET name=" in updates[0][0]
    assert updates[0][1] == ("Updated medicine", 7, "Updated maker", "67890", 12)
    assert "UPDATE inventory_batches SET batch_number=" in updates[1][0]
    assert updates[1][1][-2:] == (25, 12)


def test_owner_cannot_save_invalid_medicine_edit(medicine_app):
    app, connection = medicine_app
    expiry = (datetime.date.today() + datetime.timedelta(days=60)).isoformat()
    form = {
        "name": "Updated medicine",
        "category": "",
        "manufacturer": "",
        "barcode": "",
        "batch_number": "B-2",
        "expiry_date": expiry,
        "purchase_price": "10",
        "selling_price": "9",
        "quantity": "15",
        "reorder_level": "5",
        "gst_percent": "5",
    }

    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(user_id=1, role="owner")
        response = client.post("/medicines/batches/25/edit", data=form)

    assert response.status_code == 200
    assert b"Selling price must be at least purchase price" in response.data
    assert not any(query.startswith("UPDATE") for query, _ in connection.queries)
