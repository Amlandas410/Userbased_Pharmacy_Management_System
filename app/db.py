import os, re, json
import mysql.connector
from flask import g, session, request
from werkzeug.security import generate_password_hash

def cfg(prefix="PLATFORM_DB"):
    return {"host":os.getenv(prefix+"_HOST","127.0.0.1"),
            "port":int(os.getenv(prefix+"_PORT","3306")),
            "user":os.getenv(prefix+"_USER","root"),
            "password":os.getenv(prefix+"_PASSWORD",""),
            "database":os.getenv(prefix+"_NAME","pharmacy_platform"),
            "connection_timeout":8}

def platform_db():
    return mysql.connector.connect(**cfg())

def provision_db():
    c=cfg(); c.pop("database",None)
    c["user"]=os.getenv("PROVISION_DB_USER",c["user"])
    c["password"]=os.getenv("PROVISION_DB_PASSWORD",c["password"])
    return mysql.connector.connect(**c)

PLATFORM_SCHEMA = [
"CREATE TABLE IF NOT EXISTS pharmacies(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, pharmacy_name VARCHAR(160) NOT NULL, owner_email VARCHAR(254) NOT NULL UNIQUE, phone VARCHAR(32) NOT NULL, license_number VARCHAR(100), gstin VARCHAR(20), address TEXT NOT NULL, database_name VARCHAR(64) NOT NULL UNIQUE, status ENUM('provisioning','active','failed','suspended') NOT NULL DEFAULT 'provisioning', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS provisioning_attempts(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, pharmacy_id BIGINT UNSIGNED, status VARCHAR(24) NOT NULL, error_code VARCHAR(64), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX(pharmacy_id)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS platform_audit_logs(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, pharmacy_id BIGINT UNSIGNED, event_type VARCHAR(80) NOT NULL, actor VARCHAR(254), detail_json JSON, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX(pharmacy_id,created_at)) ENGINE=InnoDB"
]

TENANT_SCHEMA = [
"CREATE TABLE IF NOT EXISTS users(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, full_name VARCHAR(160) NOT NULL, email VARCHAR(254) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL, role ENUM('owner','admin','pharmacist','billing') NOT NULL DEFAULT 'billing', is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS categories(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS medicines(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, name VARCHAR(180) NOT NULL, category_id BIGINT UNSIGNED, manufacturer VARCHAR(160), barcode VARCHAR(100) UNIQUE, prescription_required BOOLEAN NOT NULL DEFAULT FALSE, is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE SET NULL, INDEX(name)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS inventory_batches(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, medicine_id BIGINT UNSIGNED NOT NULL, batch_number VARCHAR(100) NOT NULL, expiry_date DATE NOT NULL, purchase_price DECIMAL(12,2) NOT NULL, selling_price DECIMAL(12,2) NOT NULL, quantity INT UNSIGNED NOT NULL DEFAULT 0, reorder_level INT UNSIGNED NOT NULL DEFAULT 10, gst_percent DECIMAL(5,2) NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uq_batch(medicine_id,batch_number,expiry_date), FOREIGN KEY(medicine_id) REFERENCES medicines(id), CHECK(purchase_price>=0), CHECK(selling_price>=purchase_price), INDEX(expiry_date)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS suppliers(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, name VARCHAR(180) NOT NULL, contact_name VARCHAR(160), email VARCHAR(254), phone VARCHAR(32), address TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS customers(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, full_name VARCHAR(160) NOT NULL, phone VARCHAR(32), email VARCHAR(254), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX(phone)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS prescriptions(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, customer_id BIGINT UNSIGNED, reference_code VARCHAR(100) NOT NULL, prescriber_name VARCHAR(160), issued_date DATE, notes TEXT, reviewed_by BIGINT UNSIGNED, reviewed_at DATETIME, status ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL, FOREIGN KEY(reviewed_by) REFERENCES users(id)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS purchases(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, supplier_id BIGINT UNSIGNED NOT NULL, invoice_number VARCHAR(100) NOT NULL, purchase_date DATE NOT NULL, total_amount DECIMAL(12,2) NOT NULL DEFAULT 0, created_by BIGINT UNSIGNED NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uq_supplier_invoice(supplier_id,invoice_number), FOREIGN KEY(supplier_id) REFERENCES suppliers(id), FOREIGN KEY(created_by) REFERENCES users(id)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS purchase_items(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, purchase_id BIGINT UNSIGNED NOT NULL, batch_id BIGINT UNSIGNED NOT NULL, quantity INT UNSIGNED NOT NULL, unit_cost DECIMAL(12,2) NOT NULL, line_total DECIMAL(12,2) NOT NULL, FOREIGN KEY(purchase_id) REFERENCES purchases(id), FOREIGN KEY(batch_id) REFERENCES inventory_batches(id)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS sales(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, bill_number VARCHAR(40) NOT NULL UNIQUE, customer_id BIGINT UNSIGNED, customer_name VARCHAR(160) NOT NULL, subtotal DECIMAL(12,2) NOT NULL, discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0, tax_amount DECIMAL(12,2) NOT NULL DEFAULT 0, total_amount DECIMAL(12,2) NOT NULL, amount_paid DECIMAL(12,2) NOT NULL DEFAULT 0, payment_status ENUM('unpaid','partial','paid') NOT NULL DEFAULT 'unpaid', payment_method VARCHAR(30), paid_at DATETIME, recorded_profit DECIMAL(12,2) NOT NULL DEFAULT 0, prescription_id BIGINT UNSIGNED, created_by BIGINT UNSIGNED NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL, FOREIGN KEY(prescription_id) REFERENCES prescriptions(id) ON DELETE SET NULL, FOREIGN KEY(created_by) REFERENCES users(id), INDEX(created_at), INDEX(customer_id,payment_status)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS bill_payments(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, sale_id BIGINT UNSIGNED NOT NULL, payment_token VARCHAR(64) NOT NULL UNIQUE, amount DECIMAL(12,2) NOT NULL, payment_method ENUM('cash','upi','card','bank_transfer','other') NOT NULL, reference_note VARCHAR(120), received_by BIGINT UNSIGNED NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(sale_id) REFERENCES sales(id), FOREIGN KEY(received_by) REFERENCES users(id), INDEX(sale_id,created_at), CHECK(amount>0)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS sale_items(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, sale_id BIGINT UNSIGNED NOT NULL, batch_id BIGINT UNSIGNED NOT NULL, medicine_name VARCHAR(180) NOT NULL, batch_number VARCHAR(100) NOT NULL, quantity INT UNSIGNED NOT NULL, unit_price DECIMAL(12,2) NOT NULL, unit_cost DECIMAL(12,2) NOT NULL, tax_amount DECIMAL(12,2) NOT NULL DEFAULT 0, line_total DECIMAL(12,2) NOT NULL, line_profit DECIMAL(12,2) NOT NULL, FOREIGN KEY(sale_id) REFERENCES sales(id), FOREIGN KEY(batch_id) REFERENCES inventory_batches(id), INDEX(sale_id)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS sales_returns(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, sale_id BIGINT UNSIGNED NOT NULL, reason VARCHAR(255) NOT NULL, refund_amount DECIMAL(12,2) NOT NULL DEFAULT 0, created_by BIGINT UNSIGNED NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(sale_id) REFERENCES sales(id), FOREIGN KEY(created_by) REFERENCES users(id)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS sales_return_items(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, return_id BIGINT UNSIGNED NOT NULL, sale_item_id BIGINT UNSIGNED NOT NULL, quantity INT UNSIGNED NOT NULL, amount DECIMAL(12,2) NOT NULL, restock BOOLEAN NOT NULL DEFAULT FALSE, FOREIGN KEY(return_id) REFERENCES sales_returns(id), FOREIGN KEY(sale_item_id) REFERENCES sale_items(id)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS supplier_returns(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, supplier_id BIGINT UNSIGNED NOT NULL, batch_id BIGINT UNSIGNED NOT NULL, quantity INT UNSIGNED NOT NULL, reason VARCHAR(255) NOT NULL, created_by BIGINT UNSIGNED NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(supplier_id) REFERENCES suppliers(id), FOREIGN KEY(batch_id) REFERENCES inventory_batches(id), FOREIGN KEY(created_by) REFERENCES users(id)) ENGINE=InnoDB",
"CREATE TABLE IF NOT EXISTS audit_logs(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, user_id BIGINT UNSIGNED, event_type VARCHAR(100) NOT NULL, entity_type VARCHAR(80), entity_id VARCHAR(80), detail_json JSON, ip_address VARCHAR(45), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX(created_at), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL) ENGINE=InnoDB"
]

def init_platform():
    c=platform_db(); cur=c.cursor()
    for sql in PLATFORM_SCHEMA: cur.execute(sql)
    c.commit(); cur.close(); c.close()

def tenant_db_name(name):
    if not re.fullmatch(r"ph_[a-f0-9]{16}", name or ""): raise ValueError("Invalid tenant database mapping")
    x=cfg(); x["database"]=name
    return mysql.connector.connect(**x)

def ensure_payment_schema(connection):
    """Apply the additive payment migration to existing tenant databases."""
    cur = connection.cursor()
    try:
        cur.execute("SHOW COLUMNS FROM sales")
        columns = {row[0] for row in cur.fetchall()}
        needs_legacy_backfill = "payment_status" not in columns or "amount_paid" not in columns
        additions = {
            "amount_paid": "ALTER TABLE sales ADD COLUMN amount_paid DECIMAL(12,2) NOT NULL DEFAULT 0 AFTER total_amount",
            # Existing invoices were already completed by the pre-payment version of the app.
            "payment_status": "ALTER TABLE sales ADD COLUMN payment_status ENUM('unpaid','partial','paid') NOT NULL DEFAULT 'paid' AFTER amount_paid",
            "payment_method": "ALTER TABLE sales ADD COLUMN payment_method VARCHAR(30) NULL AFTER payment_status",
            "paid_at": "ALTER TABLE sales ADD COLUMN paid_at DATETIME NULL AFTER payment_method",
        }
        for column, sql in additions.items():
            if column not in columns:
                cur.execute(sql)
        if needs_legacy_backfill:
            cur.execute("UPDATE sales SET amount_paid=total_amount,paid_at=COALESCE(paid_at,created_at) WHERE payment_status='paid' AND amount_paid=0")
        cur.execute("CREATE TABLE IF NOT EXISTS bill_payments(id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, sale_id BIGINT UNSIGNED NOT NULL, payment_token VARCHAR(64) NOT NULL UNIQUE, amount DECIMAL(12,2) NOT NULL, payment_method ENUM('cash','upi','card','bank_transfer','other') NOT NULL, reference_note VARCHAR(120), received_by BIGINT UNSIGNED NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(sale_id) REFERENCES sales(id), FOREIGN KEY(received_by) REFERENCES users(id), INDEX(sale_id,created_at), CHECK(amount>0)) ENGINE=InnoDB")
        connection.commit()
    finally:
        cur.close()

def tenant_db():
    if "tenant_conn" not in g:
        if not getattr(g,"tenant_db_name",None): raise RuntimeError("No tenant selected")
        g.tenant_conn=tenant_db_name(g.tenant_db_name)
    if not getattr(g, "payment_schema_checked", False):
        ensure_payment_schema(g.tenant_conn)
        g.payment_schema_checked = True
    return g.tenant_conn

def close_db(error=None):
    c=g.pop("tenant_conn",None)
    if c and c.is_connected(): c.close()

def provision_tenant(name, owner, email, password):
    if not re.fullmatch(r"ph_[a-f0-9]{16}",name): raise ValueError("Invalid generated tenant name")
    c=provision_db(); cur=c.cursor()
    try:
        cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        runtime_user = os.getenv("PLATFORM_DB_USER", "platform_app").replace("`", "``").replace("'", "''")
        cur.execute(f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{runtime_user}'@'%'")
        c.database = name
        for sql in TENANT_SCHEMA: cur.execute(sql)
        cur.execute("INSERT INTO users(full_name,email,password_hash,role) VALUES(%s,%s,%s,'owner')",(owner,email,generate_password_hash(password)))
        c.commit()
    except Exception:
        c.rollback()
        try: cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
        except Exception: pass
        raise
    finally: cur.close(); c.close()

def audit(event, entity=None, entity_id=None, detail=None):
    c=tenant_db(); cur=c.cursor()
    cur.execute("INSERT INTO audit_logs(user_id,event_type,entity_type,entity_id,detail_json,ip_address) VALUES(%s,%s,%s,%s,%s,%s)",
        (session.get("user_id"),event,entity,str(entity_id) if entity_id else None,json.dumps(detail or {},default=str),request.remote_addr))
    c.commit(); cur.close()
