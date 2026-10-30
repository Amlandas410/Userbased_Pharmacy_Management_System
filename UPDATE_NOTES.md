# MediLedger update notes

## Changes in this revision

- Added invoice payment tracking with **Unpaid**, **Partially paid**, and **Paid** states.
- Added payment receipt records with amount, method, optional reference, receiving staff ID, and timestamp.
- Added support for partial payments and remaining-balance calculations. The server locks an invoice row, rejects invalid or excessive payments, updates the status, and writes an audit event in one transaction.
- Added customer history lookup by normalized phone number. The billing screen shows prior invoices and outstanding balances.
- Prevented a returning customer from creating a new bill while any earlier invoice still has a balance. The check is enforced server-side and serialized per phone number; browser-side checking is only a usability layer.
- Reworked the login screen with a responsive pharmacy-oriented split layout.
- Strengthened medicine form and CSV validation for required fields, batch/barcode formats, real future expiry dates, decimal prices, quantities, and GST ranges.
- Removed the fixed development secret fallback. Development now warns and uses an ephemeral secret when not configured; production startup requires a strong configured `SECRET_KEY`.
- Added regression tests and developer setup instructions.

## Database upgrade behavior

The app applies an additive payment migration the first time an existing tenant database is used after upgrade. Existing pre-payment invoices are marked as already paid for historical compatibility; newly created invoices begin unpaid. **Back up all MySQL databases before upgrading.** Test the migration against a copy of your data before using it with live records.

## Verification performed in this environment

- `64 passed` using `python -m pytest -q`
- `python -m compileall -q app tests` passed
- `node --check app/static/app.js` passed
- All 13 Jinja templates compiled successfully
- `docker-compose.yml` parsed successfully and contains `db`, `db-provisioner`, and `web` services
- The final ZIP was checked for archive corruption and scanned to confirm it does not include `.env`, `.venv`, `.git`, Python bytecode, or pytest caches

## Verification limits and known project limitations

A live MySQL server and Docker executable were not available in this environment. Therefore, these changes have **not** been exercised against an actual MySQL instance or through a real Docker Compose startup. Database transaction behavior and MySQL advisory locks need a local integration test before real use.

The base application still has documented limitations: staff accounts cannot independently sign in because tenant discovery currently uses the pharmacy owner's email; Compose's database provisioner is broadly privileged; and several modules (including purchase receiving, returns, prescription review, and complete staff account lifecycle) are not full workflows. Review `README.md`, especially its security and production-readiness section. This revision should be treated as a development/MVP build, not certified production pharmacy software.

## Windows setup

1. Extract the ZIP.
2. Copy `.env.example` to `.env`.
3. Replace every placeholder password and set a unique random `SECRET_KEY` of at least 32 characters. Do not commit `.env`.
4. Back up any existing MySQL data before applying the migration.
5. Start Docker Desktop, then run `docker compose up --build` from the project folder.
6. Open `http://127.0.0.1:8000`.
7. To run tests, create a virtual environment, install `requirements-dev.txt`, and run `python -m pytest -q`.

Do not run `docker compose down -v` unless you deliberately intend to remove the named MySQL data volume.
