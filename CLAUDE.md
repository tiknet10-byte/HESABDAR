# HESABDAR - guide for Claude

Accounting for a beauty salon / skin clinic in Iran (services + product sales + website shop). The owner writes in
**Persian** and is not a programmer: always answer in Persian, simply, with clear steps. Staff using the program may be
slow or confused: screens must be obvious, forgiving and explained (every page has a beginner guide).

## How the owner runs it
* Windows PC in the clinic, folder `D:\HESABDAR`, local only (http://127.0.0.1:8000, SQLite in `backend\data\`).
* Updates arrive as a zip `HESABDAR-<version>-windows.zip` that we build and send; they extract it over the folder
  and run `update.bat` (stops the old server, `pip install`, `start.bat`). `start.bat` runs `manage.py run --open`.
* The web UI shows a red banner when the built UI version != server version (`backend/app/__init__.py`), so
  **bump the version, then rebuild the frontend** (vite reads the version at build time).

## Stack and layout
* Backend: FastAPI + SQLAlchemy 2, SQLite (WAL, foreign_keys ON), pytest. `backend/app/`:
  `models.py` (all tables), `api/` (auth, catalog, customers, finance, products, legacy, tizpardaz, woo, system),
  `services/` (accounting = double entry, inventory = stock + costing engine, reports, routing, customer_merge,
  service_catalog, codes, legacy_import (Chehreh), tizpardaz, woo + woo_queue (WooCommerce), backup, audit...),
  `plugins/` (bank_sync, messaging_receipts, sales_book_ocr, loyalty), `ai/` (Claude assistant, MCP).
* Frontend: React 19 + Vite + Tailwind 4, RTL Persian, recharts, lucide-react. `frontend/src/pages`, `components`,
  `lib/` (api, format, jalali, hooks, paysplit). Beginner guides: `components/Guide.tsx` (`GUIDES` per path).
* Docs: `docs/ARCHITECTURE.md` (ledger table), `AI.md`, `INTEGRATIONS.md`, `PLUGINS.md`, `SECURITY_BACKUP.md`.

## Commands (Linux dev container)
```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # once
cd backend && .venv/bin/python -m pytest -q          # all tests (one shared DB per session: use unique mobiles)
cd backend && ruff check app --select F,E9           # lint
cd frontend && npm ci && npx tsc -b && npm run build # typecheck + build (dist is served by the backend)
# try it: HESABDAR_DATABASE_URL=sqlite:////tmp/x.db .venv/bin/python manage.py run   (then create a user / demo)
```
Browser checks: Playwright with the preinstalled Chromium
(`import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'`); look for console errors.
Copy SQLite DBs with `sqlite3.backup` (WAL). Stop the server with `pkill -f "[m]anage.py run"`.

## Release / package (what the owner receives)
```bash
S=<scratch>/finalXYZ; mkdir -p $S/HESABDAR && git archive HEAD | tar -x -C $S/HESABDAR \
  && cp -r frontend/dist $S/HESABDAR/frontend/ && (cd $S && zip -qr HESABDAR-<ver>-windows.zip HESABDAR)
```
`.claude/` is excluded from the zip (`.gitattributes` export-ignore). Send the zip, then explain in Persian what changed
and how to use it. Commit messages end with the attribution lines the session asks for; no model names in the repo.

## Rules of the money (do not break)
* All amounts are **integer Rial**; the UI shows Toman (`money()`, `MoneyInput`). Dates are shown in Jalali.
* Every operation posts a **balanced journal entry** (`services/accounting.post`). Chart: 1100 cash/POS/card/bank
  (one sub-account each), 1200 receivables, 1300 inventory, 2100 customer deposits, 2200 staff payable,
  2300 supplier payables, 3100 equity, 3200 opening balances, 41xx revenue per service line, 4200 product sales,
  4800 forfeited deposits, 5100 expenses, 5200 staff commission, 5300 cash difference, 5400 COGS, 5500 shrinkage.
* Money dates can't be in the future (`ensure_not_future`). Nothing is deleted: invoices are **voided** (reversing
  entries); a void either cancels the payment on its own date ("cancel": it never happened) or refunds it today.
* Exactness matters to the owner: integer math, largest-remainder splits (`inventory._share`, `lib/paysplit.ts`),
  tests that check totals to the Rial.
* Schema changes: new **nullable** columns are added automatically at start (`main._add_missing_columns`); new tables
  are created; indexes in `main.INDEXES`. No migration tool.
* JSON settings (`settings_store`): always assign a **new** dict (mutating the stored one in place is not saved).
* Delete journal entries through the ORM (their lines go with them), never with a bulk delete.

## Features and where they live
* Deposits (بیعانه) + appointments: refunded deposit cancels its appointment; appointments easy to change / delete.
* Invoices: services + products in one invoice; channel in_person / online; payments split per service line onto
  that line's default POS/card (`services/routing.py`, Settings > cards). Deposits and products have their own.
* Products (`services/inventory.py`): system code (1..), website SKU, purchases from suppliers (discount/shipping
  shared into cost), stock moves, costing engine `replay()` with 3 methods (moving average, FIFO, monthly average),
  COGS posted per move, recost when history changes; stock count; profit report (products are a separate "line").
  Last selling price per product (`last_sale_price` / `last_online_price`) fills the next invoice.
* Customers: code (Chehreh code kept), mobile unique, other_mobiles/other_codes after merging namesakes
  (`services/customer_merge.py`; same first+last name with another mobile -> ask, merge everything), debtors filter,
  "دریافت بدهی" (settles unpaid invoices first).
* Imports: Chehreh (previous salon software) customers / deposits / receipts (`legacy_import.py`, history only);
  Tizpardaz (product accounting software) customers matched by NAME only («نام‌خانوادگی(نام)»), balances as
  opening balances, products as reference (code, SKU from `tizpardaz_defaults.py`, final stock), journal as history
  (`TradeHistory`, no money/stock effect). Every import batch can be undone.
* Website shop (WooCommerce, `services/woo.py`, Settings > «فروشگاه سایت»): the local program **polls** the site
  every N minutes (the clinic PC is not reachable from the internet). Orders -> online invoices (customer by mobile;
  a namesake without mobile gets it; else new customer), payment on the chosen account when paid, cancel/refund ->
  void with money back, changed/partly refunded order -> booked again, orders before the start date skipped.
  Stock changes here are queued in `woo_outbox` in the same transaction (purchase +, in-person sale -, voids, opening
  +; stock count = exact) and sent to the product with the same SKU. Tested against `backend/tests/fake_woo.py`
  (in-memory WooCommerce); **not yet tried on the real site**.

## Status (1.18.0)
* GitHub: the repository on GitHub is EMPTY - pushes were refused (403, the Claude GitHub App lacks write access).
  The history exists in the session container, the zips and the git bundle the owner received. Fix access first.
* Next possible work: connect the real website and fix what differs (currency IRT/IRR, security plugins blocking
  `wp-json`, proxy/VPN); proxy setting if needed; anything the owner reports from daily use.
