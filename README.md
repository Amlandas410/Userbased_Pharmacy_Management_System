# MediLedger Pharmacy Management

MediLedger is a browser-based pharmacy inventory and sales-management starter built with Python, Flask, MySQL, Jinja templates, and Docker Compose. It is designed as a multi-tenant SaaS-style application: a central platform database keeps track of registered pharmacies, and each pharmacy's operational records are stored in its own MySQL database.

This document describes the repository as it currently works: its architecture, the purpose of its technologies, the registration and billing flows, database tables, routes, configuration, local and Docker setup, tests, and known limitations. Some database tables are groundwork for future functionality and do not yet have complete workflows; those are identified below.

> **Intended use:** This repository is an MVP/development starter, not certified pharmacy, accounting, tax, or prescription-management software. Do not use it for real patient data or as the sole system of record without appropriate legal, security, operational, and clinical review.

## Contents

- [What the application does](#what-the-application-does)
- [Technology choices](#technology-choices)
- [Architecture and request lifecycle](#architecture-and-request-lifecycle)
- [Repository layout](#repository-layout)
- [Data model](#data-model)
- [User roles and access](#user-roles-and-access)
- [Application workflows](#application-workflows)
- [HTTP routes](#http-routes)
- [Run with Docker](#run-with-docker)
- [Run locally](#run-locally)
- [Configuration reference](#configuration-reference)
- [Tests](#tests)
- [Operational notes and troubleshooting](#operational-notes-and-troubleshooting)
- [Security, limitations, and production readiness](#security-limitations-and-production-readiness)

## What the application does

The current application provides:

- Pharmacy registration, owner sign-in, and tenant database provisioning.
- A central platform database for tenant identity, status, and provisioning history.
- Medicine catalog and batch inventory, including barcode, category, supplier-facing details, expiry date, purchase/selling price, quantity, reorder level, and a GST percentage.
- Dashboard totals for stock, today's sales, low-stock batches, and expired batches.
- Medicine creation, restocking, owner-only editing, and soft deletion (archiving).
- UTF-8 CSV import for up to 1,000 medicine-batch rows, with complete validation before a transactional write, and a downloadable import template.
- Multi-line billing, optional customer lookup/creation by phone, discount and simple percentage tax calculation, printed invoice view, and JSON bill download.
- Transactional stock deduction with batch row locks and rejection of insufficient, expired, or prescription-required stock.
- Sales history, supplier creation/listing, and basic sales/inventory reports with CSV export.
- Owner/admin staff-account creation, role assignments, and audit records for selected events.
- Responsive HTML pages and a browser-persisted light/dark theme.

The application does **not** currently implement a complete purchasing/receiving workflow, sales or supplier returns, prescription review, customer management screens, or a tenant-aware staff sign-in flow. The schema contains tables for several of these future features; a table alone does not mean the corresponding workflow is available.

## Technology choices

| Technology | Where it is used | Why it is used here |
| --- | --- | --- |
| Python 3.12 | Application runtime and Docker image | The application is written in Python; the runtime version is pinned in the image for consistent deployments. |
| Flask 3 | `app/__init__.py`, `app/auth.py`, and `app/views.py` | Provides the application factory, request lifecycle, routing, sessions, and integration with Jinja. |
| Jinja / HTML | `app/templates/` | Renders server-side pages using data from Flask routes. Server-side rendering keeps the UI relatively simple and avoids a separate frontend build pipeline. |
| MySQL 8.4 / InnoDB | Compose database service and `mysql-connector-python` | Stores platform and tenant data. InnoDB provides transactions, foreign keys, and row locking used by billing and CSV imports. |
| `mysql-connector-python` | `app/db.py` and application routes | Connects Python to MySQL using parameterized SQL statements. |
| Flask-WTF CSRF protection | `app/extensions.py` | Adds CSRF tokens/checks for state-changing form submissions. |
| Flask-Limiter | `app/extensions.py`, registration and login routes | Applies IP-based request limits to reduce abusive automated registration and login attempts. |
| Werkzeug password hashing | Registration, login, and staff creation | Stores password hashes instead of plaintext passwords and verifies passwords at sign-in. |
| `python-dotenv` | App initialization | Loads configuration from a local `.env` file during development. Container configuration is also passed through environment variables. |
| Gunicorn | Docker image command | Runs the Flask WSGI application as a production-style application server inside the container; Flask's built-in development server is used only for local debugging. |
| Docker | `Dockerfile` | Builds a repeatable Python runtime image with application dependencies and runs the web process as a non-root user. |
| Docker Compose | `docker-compose.yml` | Starts MySQL, initializes the tenant-provisioning DB account, starts the web application in dependency order, and persists database files in a named volume. |
| Vanilla JavaScript and CSS | `app/static/app.js`, `app/static/style.css` | Implements browser-side theme switching, batch filtering, and billing cart calculations without a JavaScript framework or frontend compilation step. |
| Pytest | `tests/` | Exercises selected pure logic, routes/static assets, and validation behavior. The current suite is not a comprehensive database or tenant-isolation test suite. |

### Python runtime dependencies

`requirements.txt` declares the following supported version ranges. Installing the requirements file installs the latest versions allowed by those ranges; there is no lockfile in this repository.

| Package | Declared range | Purpose |
| --- | --- | --- |
| `Flask` | `>=3.1,<4` | Web framework, routes, sessions, and Jinja integration. |
| `Flask-WTF` | `>=1.2,<2` | CSRF protection. |
| `Flask-Limiter` | `>=3.8,<5` | Per-IP request limits. |
| `mysql-connector-python` | `>=9,<10` | MySQL database connectivity. |
| `gunicorn` | `>=23,<24` | WSGI server used by the Linux Docker image. |
| `python-dotenv` | `>=1,<2` | Local `.env` loading. |

### Why the application uses two kinds of database

There is one **platform database** (default name `pharmacy_platform`) and one **tenant database per registered pharmacy**:

- The platform database stores the pharmacy's registration details, generated tenant database name, and provisioning state. The app needs this data before it knows which tenant database to connect to.
- A tenant database stores that pharmacy's users, medicine batches, customers, sales, and other pharmacy-specific records. Separate databases make the tenant boundary explicit and reduce accidental cross-pharmacy data mixing compared with a shared table containing a tenant ID on every row.
- This is logical separation, not strong credential isolation in the current implementation: the web app uses a shared MySQL runtime account that receives access to tenant databases. See [Security, limitations, and production readiness](#security-limitations-and-production-readiness).

## Architecture and request lifecycle

### Application startup

1. `wsgi.py` imports `create_app()` and creates the Flask application.
2. `app/__init__.py` loads environment configuration, sets session and request-size options, initializes CSRF protection and rate limiting, and registers the authentication and main application blueprints.
3. The app runs `init_platform()` inside an application context. This creates the platform tables if they do not already exist.
4. In Docker, Gunicorn imports the WSGI object as `wsgi:app` and serves it on port `8000` inside the container.

The schema setup uses `CREATE TABLE IF NOT EXISTS`; this is initial schema creation, **not** a versioned migration system. It will not safely evolve existing tables when application schemas change.

### Tenant registration and provisioning

1. The registration page validates required pharmacy/owner details, email syntax, matching passwords, and a minimum 12-character password. Registration is limited to five requests per hour per client IP.
2. The app inserts a `pharmacies` row with a provisioning status and records a provisioning attempt in the platform database.
3. Once the platform row has an ID, the tenant database name is generated as `ph_` followed by that ID in 16-character lowercase hexadecimal form (for example, `ph_0000000000000001`). The application validates this format before using the name in database operations.
4. A separate provisioning connection creates the tenant database, grants the runtime account access, creates its tables, and inserts the first user with the `owner` role. The password is stored as a Werkzeug-generated hash.
5. On success, the platform record is marked active and a platform audit event is written. On failure, the tenant record/attempt is marked failed and the error is logged.
6. On an authenticated request, `app/__init__.py` reads the pharmacy ID from the signed Flask session, looks up its current status and database mapping in the platform database, and sets the server-side tenant database name for the request. Missing, suspended, failed, or otherwise inactive tenant mappings clear the session.
7. `tenant_db()` in `app/db.py` opens the tenant connection lazily and stores it for the request. The application closes that connection at request teardown.

The tenant name is not accepted from a browser form or URL to choose the database. It is looked up from the platform mapping and checked against the generated-name pattern.

### Sign-in and permissions

Flask's signed cookie session holds the pharmacy ID/name, user ID/name, role, and an eight-hour permanent session lifetime. It is signed using `SECRET_KEY`; it is not an encrypted storage location for secrets. Passwords are checked against the hash in the tenant `users` table.

The current sign-in lookup first finds a pharmacy by its **owner email** in the platform database and then checks for a matching tenant user. As a result, accounts created through the Staff page do not have a working tenant-selection/sign-in path in this starter. This limitation must be fixed before staff accounts are relied on.

### Billing transaction

The billing page's JavaScript helps build the cart and display an estimate, but the server recalculates all trusted values from MySQL. On submission, the server:

1. Validates the requested batch IDs, quantities, customer fields, and discount.
2. Begins a database transaction and locks the requested inventory rows with `SELECT ... FOR UPDATE`.
3. Rechecks that every batch exists, has sufficient stock, is not expired, and is not marked as prescription-required.
4. Calculates amounts using decimal values and each batch's GST percentage.
5. Writes the sale and sale-item snapshots, deducts stock using a quantity guard, and writes a sale audit entry.
6. Commits the transaction, or rolls it back on an error.

This protects against the browser changing prices or bypassing stock checks and helps avoid two simultaneous bills selling the same last units. The displayed tax and profit values are simple estimates, not accounting or tax determinations.

## Repository layout

```text
.
├── app/
│   ├── __init__.py          # Flask app factory, session setup, tenant request context
│   ├── auth.py              # Pharmacy registration, owner login, logout
│   ├── db.py                # DB configuration, schemas, tenant provisioning, audit helper
│   ├── extensions.py        # Shared CSRF and rate-limit extensions
│   ├── views.py             # Dashboard, inventory, billing, reports, staff, and health routes
│   ├── static/
│   │   ├── app.js           # Theme toggle, inventory filtering, billing cart UI
│   │   └── style.css        # Responsive layout, forms, tables, invoice, light/dark themes
│   └── templates/           # Jinja pages for auth and each application area
├── tests/                   # Focused pytest tests
├── .env.example             # Names and example placeholders for runtime settings
├── .gitignore               # Excludes local secrets, virtualenv, caches, and logs
├── Dockerfile               # Builds and runs the Python web container
├── docker-compose.yml       # MySQL, one-shot DB provisioner, web, and persistent volume
├── requirements.txt         # Python runtime dependencies
└── wsgi.py                  # Gunicorn/Flask WSGI entry point
```

### Code and page map

| File or directory | Responsibility |
| --- | --- |
| `app/__init__.py` | `create_app()` configures Flask, security extensions, blueprints, request-time tenant selection, teardown cleanup, and shared template context. It also initializes the platform schema. |
| `app/auth.py` | Public registration, owner login, and POST logout routes. Registration records the tenant before calling the provisioner; login verifies the password hash before creating the session. |
| `app/db.py` | Platform/runtime/provisioning connection factories, both SQL schema definitions, generated tenant-name validation, tenant database creation, request-scoped tenant connection handling, and the audit helper. |
| `app/extensions.py` | Creates the shared `CSRFProtect` and IP-keyed `Limiter` instances, which are initialized by the app factory. |
| `app/views.py` | Main blueprint, role guard, money and CSV parsing helpers, dashboard/inventory workflows, billing and invoice exports, sales/supplier/report/staff routes, and `/health`. |
| `app/templates/base.html` | Shared page shell, navigation, role labels, flash messages, CSRF tokens, and the theme preference bootstrap. |
| `app/templates/register.html`, `login.html` | Public pharmacy registration and sign-in forms. |
| `app/templates/dashboard.html` | Stock and sales summary cards, inventory alerts, and recent sales. |
| `app/templates/medicines.html` | Inventory search/filter table, CSV import controls, and medicine actions. |
| `app/templates/medicine_form.html` | Medicine and batch creation/edit form. |
| `app/templates/medicine_restock.html` | Existing batch selection and quantity-add form. |
| `app/templates/billing.html` | Available batch list, browser cart, customer/discount inputs, and submit form. Server-side code remains authoritative for all prices and stock. |
| `app/templates/bill.html` | Bill/invoice presentation and print/download actions. |
| `app/templates/sales.html` | Recent sales table with links to bill details. |
| `app/templates/suppliers.html` | Supplier create form and supplier list. |
| `app/templates/reports.html` | Daily sales and inventory valuation report presentation. |
| `app/templates/staff.html` | Tenant user list and staff-account creation form. |
| `app/static/app.js` | Theme switching/persistence, client-side inventory batch search, and the billing cart display and estimate. |
| `app/static/style.css` | Color tokens, page layout, responsive breakpoints, forms, tables, invoice print behavior, and dark theme. |
| `wsgi.py` | Exposes the Flask application object Gunicorn imports as `wsgi:app`. |

## Data model

The schema is defined in `app/db.py`. All listed tables are created when a tenant database is provisioned; some tables are currently reserved for future workflows.

### Platform database tables

| Table | Purpose |
| --- | --- |
| `pharmacies` | Pharmacy identity and owner contact details, unique owner email, generated tenant database mapping, status (`provisioning`, `active`, `failed`, or `suspended`), and creation time. |
| `provisioning_attempts` | Records each tenant provisioning attempt, its status, and a non-sensitive error code. |
| `platform_audit_logs` | Records platform-level events such as successful pharmacy registration, including actor and JSON details. |

### Per-pharmacy tenant tables

| Table | Purpose and implementation status |
| --- | --- |
| `users` | Tenant users, password hashes, role, activation flag, and creation time. Owner is created at registration; staff records can be added by an owner/admin, but staff sign-in is not fully wired. |
| `categories` | Reusable, uniquely named medicine categories. |
| `medicines` | Product identity: name, category, manufacturer, unique optional barcode, prescription-required flag, and active/archive flag. |
| `inventory_batches` | Stock by batch: expiry, purchase and selling price, quantity, reorder threshold, and GST percent. A medicine/batch/expiry combination is unique. |
| `suppliers` | Supplier contact details. The current UI supports adding and listing suppliers, not receiving purchase orders. |
| `customers` | Customer name, phone, and email. Billing can find/create a customer by phone; there is no separate customer-management page. |
| `prescriptions` | Prescription reference, customer, prescriber, dates, notes, review user/time, and status. This is schema groundwork only; no review workflow is implemented. Prescription-required medicines are blocked at billing. |
| `purchases` | Supplier invoice header and creator information. Schema only; there is no purchase-entry workflow. |
| `purchase_items` | Purchase line items linked to inventory batches. Schema only; receiving stock from purchases is not implemented. |
| `sales` | Bill header, customer snapshot, subtotal, discount, tax, total, estimated profit, creator, and time. |
| `sale_items` | Bill line snapshots (medicine/batch names, quantity, unit prices/costs, tax, and profit) linked to the batch and sale. |
| `sales_returns` | Intended return/refund header. Schema only; return processing is not implemented. |
| `sales_return_items` | Intended returned sale lines and restock choice. Schema only; return processing is not implemented. |
| `supplier_returns` | Intended supplier return record. Schema only; no supplier-return workflow is implemented. |
| `audit_logs` | Tenant-level event name, actor, entity, JSON details, request IP, and timestamp. Used for selected events such as sales and medicine changes. It is not a complete audit trail for every action. |

Foreign keys preserve relationships between operational records; archive/soft-delete behavior on medicines helps retain historical sale references. Monetary values use MySQL `DECIMAL` columns and Python `Decimal` calculations rather than binary floating point.

## User roles and access

| Role | Current access |
| --- | --- |
| `owner` | Dashboard, inventory and CSV operations, billing/sales, suppliers, reports, staff administration, medicine batch editing, and medicine archiving. |
| `admin` | Dashboard, inventory and CSV operations, billing/sales, suppliers, reports, and staff administration. Cannot edit/archive medicines through the owner-only routes. |
| `pharmacist` | Dashboard, inventory and CSV operations, billing/sales, and suppliers. |
| `billing` | Dashboard, inventory viewing, billing, and sales history/bill access. |

Route guards enforce role checks on the server; hiding a navigation link in a template is not the authorization control. The staff page can create `admin`, `pharmacist`, or `billing` records (not another owner), but see the sign-in limitation above.

## Application workflows

### Inventory

- A medicine is the catalog identity; its lots are stored separately in `inventory_batches`, so the same medicine can have different expiry dates, costs, prices, and quantities by batch.
- Adding a medicine creates its catalog row and its initial batch. Restocking adds units to an existing active, non-expired batch.
- The inventory page supports text search and filters for low-stock and expired batches. The dashboard cards link to the relevant filters.
- Editing a batch and archiving a medicine are owner-only. Archiving sets `medicines.is_active=0` rather than deleting historical records.
- CSV import requires these columns exactly once: `name`, `category`, `manufacturer`, `barcode`, `batch_number`, `expiry_date`, `purchase_price`, `selling_price`, `quantity`, `reorder_level`, and `gst_percent`. Required per-row values are name, batch number, expiry, purchase price, and selling price. Expiry must be in the future; selling price cannot be below purchase price; numeric fields are validated. Empty quantity defaults to zero, empty reorder level to ten, and empty GST to zero. All rows are parsed before writing, and the database write is transactional/all-or-nothing. The upload limit is governed by Flask's 2 MiB request size limit.
- CSV import can group rows for the same medicine identity into multiple batches. It does not provide a general import/update mechanism for an already-existing catalog item; database uniqueness conflicts fail the transaction.

### Billing and records

- The cart offers available, active, non-expired batches. The browser's cart and total are for usability only; the server validates and recalculates the bill.
- A customer is optional. Supplying a phone number allows billing to reuse a customer with that phone or create a new customer record. A blank phone results in a walk-in/uncatalogued customer reference.
- Tax is the batch's `gst_percent` applied to the line's selling-price subtotal. A discount is applied to the bill total and may not exceed subtotal plus tax.
- The system stores both a sale header and line snapshots, decrements stock in the same transaction, and records a selected audit event. Invoice pages can be printed from the browser; each bill can also be downloaded as JSON.
- Prescription-required products cannot be billed, because the repository does not implement prescription capture and pharmacist approval.

### Dashboard and reports

- Dashboard inventory totals include active medicines with stock. Low-stock counts include batches with positive stock at or below their reorder level; expired counts include active batches with positive stock whose expiry date has passed.
- Daily dashboard sales are for the database server's current date. Reports aggregate sales over the last 30 days and show active inventory cost and retail valuation.
- Report CSV is a summary export (daily sales plus inventory valuation), not a full accounting ledger.
- The `recorded_profit` calculation is an estimate based on selling price minus batch purchase price, with bill discount deducted at the bill level. It does not model all costs, discount allocation, returns, or accounting rules.

### User interface

- Jinja templates provide the pages and shared navigation. `app/static/style.css` defines responsive layouts, forms, tables, themes, and print styles for invoices.
- `app/static/app.js` handles dark/light theme preference, client-side batch-table text filtering, and billing cart display.
- Theme preference is stored in browser `localStorage`; if there is no saved preference, the browser's preferred color scheme is used. This is a display preference only and is not stored on the server.
- The stylesheet imports DM Sans and Manrope from Google Fonts. An internet connection may be required for those fonts; local layout/styles still load from the app.

## HTTP routes

All routes below are relative to the application origin (for example, `http://127.0.0.1:8000`). Unless indicated otherwise, pages require an active signed-in tenant session.

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/` | Public | Redirects to dashboard when signed in, otherwise to login. |
| `GET`, `POST` | `/register` | Public | Register a pharmacy and provision its tenant. |
| `GET`, `POST` | `/login` | Public | Sign in using the current owner-email lookup flow. |
| `POST` | `/logout` | Signed-in | Clear the session. CSRF protected. |
| `GET` | `/dashboard` | All roles | Stock, today's sales, alerts, and recent bills. |
| `GET` | `/medicines` | All roles | Search and view active medicine batches; supports `q` and `status=low_stock` or `status=expired`. |
| `GET`, `POST` | `/medicines/add` | Owner, admin, pharmacist | Add a medicine and its first batch. |
| `GET`, `POST` | `/medicines/restock` | Owner, admin, pharmacist | Add quantity to an active, non-expired batch. |
| `GET`, `POST` | `/medicines/batches/<batch_id>/edit` | Owner | Edit the medicine identity and selected batch. |
| `POST` | `/medicines/<medicine_id>/delete` | Owner | Archive a medicine from active inventory. |
| `GET` | `/medicines/template.csv` | Owner, admin, pharmacist | Download the CSV import header template. |
| `POST` | `/medicines/import` | Owner, admin, pharmacist | Validate and import medicine batches. |
| `GET`, `POST` | `/billing` | All roles | Build and submit a bill. |
| `GET` | `/bills/<sale_id>` | All roles | View/print a bill. |
| `GET` | `/bills/<sale_id>/json` | All roles | Download a bill and its line items as JSON. |
| `GET` | `/sales` | All roles | View recent sales (up to 500 rows). |
| `GET`, `POST` | `/suppliers` | Owner, admin, pharmacist | Add and list suppliers. |
| `GET` | `/reports` | Owner, admin | View 30-day sales summary and inventory valuation. |
| `GET` | `/reports/export.csv` | Owner, admin | Download the report summary as CSV. |
| `GET`, `POST` | `/staff` | Owner, admin | List users and create non-owner staff records. |
| `GET` | `/health` | Public | Returns `{"status":"ok"}` when the Flask route responds; it does not verify MySQL readiness. |

## Run with Docker

### Prerequisites

- Docker Desktop (Windows/macOS) or Docker Engine with the Compose plugin (Linux).
- Docker Compose v2 (`docker compose` command).

### Start the application

1. From the repository root, create your local environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

   On macOS/Linux, use `cp .env.example .env`.

2. Edit `.env`. Replace every placeholder password and the Flask secret with long, unique values. Do not commit `.env`.
3. Start the containers:

   ```powershell
   docker compose up --build
   ```

   The first run builds the web image and initializes MySQL. Leave this terminal open to see logs; add `-d` to run detached.
4. Open [http://127.0.0.1:8000/register](http://127.0.0.1:8000/register), register a development pharmacy, and sign in with its owner account.

### What Compose starts and why

| Service | Role |
| --- | --- |
| `db` | Runs MySQL 8.4, creates the initial platform database and platform application user, and stores MySQL data in the `mysql_data` named volume. Its health check waits for MySQL to respond before dependent startup proceeds. |
| `db-provisioner` | A one-shot MySQL client container that waits until `db` is healthy, then uses the MySQL root account to create/update the tenant provisioning account and grant it global privileges. Compose waits for this task to finish before starting `web`. This is a convenient local bootstrap, **not** a least-privilege production setup. |
| `web` | Builds from `Dockerfile`, receives `.env`, and runs Gunicorn. It publishes host port `8000` on `127.0.0.1` only, so it is reachable from the local machine but not directly exposed on every network interface. |

The Compose dependency order prevents the web service from starting before MySQL and the provisioner are ready. It does not provide MySQL high availability, automated backups, TLS termination, or production secret management.

### Docker image details

`Dockerfile` starts from `python:3.12-slim`, sets `/app` as the working directory, installs `requirements.txt`, copies the project, creates an unprivileged `appuser` (UID `10001`), and launches:

```text
gunicorn --workers 2 --threads 4 --timeout 60 --bind 0.0.0.0:8000 wsgi:app
```

The container listens on all of its own interfaces so Compose can route traffic to it; Compose separately binds the host port to loopback. `PYTHONDONTWRITEBYTECODE` avoids writing Python bytecode files, and `PYTHONUNBUFFERED` makes container logs appear promptly.

### Useful Docker commands

```powershell
docker compose ps
docker compose logs -f web
docker compose logs -f db
docker compose restart web
docker compose down
```

`docker compose down` removes the containers and network but keeps the named `mysql_data` volume. Removing that volume permanently deletes the database contents; do so only when intentionally resetting local data and after checking the exact volume name. Keep secrets in `.env`, never in the Dockerfile or source control.

## Run locally

Local mode runs Flask on the host and expects a separately installed MySQL server. The database must be reachable using the `PLATFORM_DB_*` settings, and the configured provisioning account must be able to create tenant databases and grant the runtime account access to them.

1. Install Python 3.12 or newer and MySQL 8.x.
2. Create/configure the platform database and MySQL users. For local development, the provisioning account needs the permissions required by `CREATE DATABASE`, table creation, and `GRANT` operations in `app/db.py`. Avoid using MySQL root as the application's runtime account.
3. Create `.env` from `.env.example`, set the database host to `127.0.0.1` (or your MySQL host), and set passwords and a unique secret.
4. Create and activate a virtual environment, then install dependencies:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

   In Command Prompt, activate with `.venv\Scripts\activate.bat`. On macOS/Linux, use `python3.12 -m venv .venv` followed by `source .venv/bin/activate`.
5. Start Flask's development server:

   ```powershell
   flask --app wsgi:app run --debug
   ```

6. Visit [http://127.0.0.1:5000/register](http://127.0.0.1:5000/register).

The debug server is intended only for local development. Use the Docker/Gunicorn path or an appropriately configured production WSGI deployment for hosted use.

## Configuration reference

The variable names and placeholder format are shown in `.env.example`. Values are read from the process environment; `python-dotenv` loads `.env` for local runs. Compose also reads `.env` for variable interpolation and passes it to the web container.

| Variable | Purpose | Example/development behavior |
| --- | --- | --- |
| `SECRET_KEY` | Signs Flask sessions and supports security-related token generation. | Required operationally; the app has an unsafe fallback for development if unset. Always set a long random secret outside throwaway local use. Changing it invalidates existing sessions. |
| `PLATFORM_DB_HOST` | MySQL host for platform and runtime tenant connections. | Compose service name `db` inside Docker; usually `127.0.0.1` for a host-run app. |
| `PLATFORM_DB_PORT` | MySQL TCP port. | Defaults to `3306`. |
| `PLATFORM_DB_USER` | Platform DB and tenant runtime connection user. | Compose defaults to `platform_app`; this same account is granted access to provisioned tenant DBs. |
| `PLATFORM_DB_PASSWORD` | Password for `PLATFORM_DB_USER`. | Must be set for Compose. |
| `PLATFORM_DB_NAME` | Platform database name. | Defaults to `pharmacy_platform`. |
| `PROVISION_DB_USER` | Separate account used by the application to create tenant databases and grant runtime access. | Compose defaults to `tenant_provisioner`; must have appropriate MySQL privileges. |
| `PROVISION_DB_PASSWORD` | Password for the provisioning account. | Must be set for Compose. |
| `MYSQL_ROOT_PASSWORD` | MySQL root password used during database initialization and by the one-shot Compose provisioner. | Must be set for Compose. Keep it private and do not use the root account for normal web requests. |
| `COOKIE_SECURE` | Enables Flask's `Secure` session-cookie flag when the value is `true` (case-insensitive). | `.env.example` uses `false` for local HTTP. Enable behind correctly configured HTTPS in production. |
| `REGISTRATION_ENABLED` | Enables/disables public pharmacy registration. | Defaults to enabled when unset; set to `false` to disable registration. |

In the Compose example, `db-provisioner` uses `MYSQL_ROOT_PASSWORD` to bootstrap the configured `PROVISION_DB_USER` and gives it global privileges with grant option. The Flask process then uses the provisioner credentials only for provisioning and uses `PLATFORM_DB_USER` for normal DB connections. Restrict and redesign these privileges before deploying beyond local development.

## Tests

Install the application dependencies and pytest in the development environment, then run from the repository root. Pytest is a test/development tool and is not listed in the runtime `requirements.txt`:

```powershell
python -m pip install -r requirements.txt
python -m pip install pytest
python -m pytest
```

The current tests cover selected behavior, including medicine CSV validation, generated tenant database-name format, registration email syntax, medicine editing-related behavior, and theme assets. They do not provide comprehensive end-to-end coverage, MySQL integration tests, concurrency tests, role tests, provisioning-failure recovery tests, or tenant-isolation assurance. A passing test suite is not evidence that the application is production-ready.

## Operational notes and troubleshooting

### Web container cannot connect to MySQL

- Check `docker compose ps` and `docker compose logs db db-provisioner web`.
- Confirm the Compose `db` service is healthy and that the provisioner completed successfully.
- Check that `.env` contains matching database credentials and that no placeholder values remain.
- From a host-run Flask process, use the host's MySQL address (commonly `127.0.0.1`); `db` is a Compose network name and normally resolves only inside the Compose network.

### Tenant registration is saved but provisioning fails

The platform row and attempt are retained with a failed status so the failure is observable; the registration page asks the platform administrator to retry provisioning. Inspect server logs for the underlying exception and verify the provisioner user's `CREATE DATABASE`, schema, and `GRANT` privileges. There is no built-in retry/admin recovery interface yet.

### Port 8000 is already in use

Change the host side of the port mapping in `docker-compose.yml` (for example, from `127.0.0.1:8000:8000` to `127.0.0.1:8080:8000`) and use the new host port. The container continues to listen on port `8000`.

### Data persistence and backups

MySQL files are held in the Compose named volume `mysql_data`, so recreating the web container does not itself erase the database. A Docker volume is not a backup. Production operations need tested, encrypted, off-host backups and regular restore exercises.

### Health endpoint

`/health` confirms that Flask can serve that route only. It does not check MySQL, tenant provisioning, schema readiness, or external dependencies and should not be treated as a complete readiness or monitoring check.

## Security, limitations, and production readiness

This project is an educational/development starter. Before handling real pharmacy, personal, financial, or health data, the implementation and deployment need a dedicated review. In particular:

- **Session and request settings:** Flask sessions are signed cookies configured as HTTP-only and `SameSite=Lax`, with an eight-hour permanent lifetime after login. `COOKIE_SECURE` controls the HTTPS-only `Secure` flag; it is false in the example development environment. Flask rejects requests larger than 2 MiB. Flask-Limiter applies a default limit of 300 requests per hour per IP, with tighter route limits of five registrations per hour and ten login attempts per minute. These defaults should be assessed for the real deployment, proxy setup, and traffic pattern.
- **Shared runtime DB account:** The same `PLATFORM_DB_USER` credential is used for platform lookups and tenant connections. The provisioner grants that user privileges over each tenant database. A production design should use tenant-specific principals or a dedicated data-access service with strict least privilege.
- **Over-privileged Docker bootstrap:** The Compose provisioner uses MySQL root and grants the provisioner global privileges with `WITH GRANT OPTION`. This is convenient for a local demo but grants broad authority. Replace it with tightly scoped, operationally managed provisioning before production.
- **Staff sign-in is incomplete:** The login route resolves tenants by owner email, so staff accounts cannot independently identify their pharmacy and authenticate through the current flow.
- **No production-grade account lifecycle:** Email verification, password reset, MFA, session revocation, and secure staff onboarding are not implemented.
- **No robust schema migrations or retry service:** `CREATE TABLE IF NOT EXISTS` initializes schemas but does not apply versioned schema changes. Failed tenant provisioning has no built-in retry UI or job queue.
- **Prescription workflows are absent:** Prescription-required medicines are intentionally rejected at billing. Prescription records in the schema do not implement upload, review, approval, or legal safeguards.
- **Purchases and returns are incomplete:** The database has purchase and return tables, but no complete purchase receiving, sale return, supplier return, or stock reconciliation workflows.
- **Financial values are estimates:** Profit is calculated from selling price less recorded batch purchase price, with discount deducted at the bill level. Tax is a simple configurable percentage. Neither is accounting-grade; validate local GST/tax, invoice, and bookkeeping requirements with qualified professionals.
- **Audit coverage is partial:** Selected operations create audit records, but not every sensitive action has a complete immutable audit trail, and the audit mechanism is not a tamper-proof compliance log.
- **Tests are limited:** Add database-backed integration tests, tenant-isolation tests, role/authorization tests, failure/retry tests, and billing concurrency tests before real use.
- **Deployment controls are external/not included:** Use HTTPS, secure cookie settings, a secret manager, network restrictions, monitoring, incident response, dependency/image maintenance, and tested backup/restore procedures.
- **Regulatory and privacy review is required:** Confirm applicable pharmacy licensing, prescription handling, GST/tax, invoicing, privacy, data residency, and record-retention obligations in every deployment jurisdiction.

For production, also set `REGISTRATION_ENABLED=false` unless public self-registration is intentionally part of the reviewed operating model, configure `COOKIE_SECURE=true` behind HTTPS, replace the broad Compose database grants, and do not expose the development Flask server.
