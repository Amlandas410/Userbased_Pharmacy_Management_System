from app.db import ensure_payment_schema


class FakeCursor:
    def __init__(self, columns):
        self.columns = columns
        self.sql = []
        self.closed = False

    def execute(self, statement):
        self.sql.append(statement)

    def fetchall(self):
        return [(name,) for name in self.columns]

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, columns):
        self.cur = FakeCursor(columns)
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


def test_payment_schema_adds_columns_and_marks_legacy_sales_paid():
    connection = FakeConnection({"id", "total_amount", "created_at"})
    ensure_payment_schema(connection)
    sql = "\n".join(connection.cur.sql)
    assert "DEFAULT 'paid'" in sql
    assert "UPDATE sales SET amount_paid=total_amount" in sql
    assert "CREATE TABLE IF NOT EXISTS bill_payments" in sql
    assert connection.commits == 1
    assert connection.cur.closed


def test_payment_schema_is_additive_when_columns_already_exist():
    connection = FakeConnection({"id", "total_amount", "created_at", "amount_paid", "payment_status", "payment_method", "paid_at"})
    ensure_payment_schema(connection)
    sql = "\n".join(connection.cur.sql)
    assert "ALTER TABLE sales ADD COLUMN" not in sql
    assert "UPDATE sales SET amount_paid=total_amount" not in sql
    assert "CREATE TABLE IF NOT EXISTS bill_payments" in sql


def test_payment_schema_backfills_existing_paid_state_when_amount_column_is_missing():
    connection = FakeConnection({"id", "total_amount", "created_at", "payment_status"})
    ensure_payment_schema(connection)
    sql = "\n".join(connection.cur.sql)
    assert "ADD COLUMN amount_paid" in sql
    assert "UPDATE sales SET amount_paid=total_amount" in sql
