# Architecture

```
frontend/ (React + Vite + Tailwind, RTL Persian UI)
   │  REST /api (JWT)
backend/  (FastAPI + SQLAlchemy 2)
   ├─ app/core        config, db (SQLite WAL / PostgreSQL), security, event bus
   ├─ app/models.py   all tables (amounts are integer Rial)
   ├─ app/services    accounting (double entry) · matching (reconciliation) · learning
   │                  reports & forecast · receipt_parser · jalali · backup · audit
   ├─ app/api         auth · catalog · customers · finance · system (reports/ai/plugins/backups)
   ├─ app/ai          tools registry · assistant (Claude tool use) · MCP stdio server · vision
   └─ app/plugins     manager + builtin/{bank_sync, messaging_receipts, sales_book_ocr, loyalty}
```

## Money & ledger
* All amounts are **integers in Rial**; the UI displays Toman.
* Every business operation posts a balanced journal entry (`services/accounting.py`):

| Operation | Debit | Credit |
|---|---|---|
| Deposit received | Cash/POS/Card account | 2100 Customer deposits (liability) |
| Invoice issued | 1200 Receivables | 41xx Revenue of each service line, 4200 product sales (net of discount, largest-remainder split per item) |
| Deposit applied | 2100 Customer deposits | 1200 Receivables |
| Payment | Cash/POS/Card account | 1200 Receivables |
| Deposit refunded | 2100 | Cash account |
| Deposit forfeited (no-show) | 2100 | 48xx Forfeited deposits of the deposit's service line (4800 when the line is unknown) |
| Expense | 51xx Expense category | Cash account |
| Staff commission ((net - treasury) x staff %, per row) | 5200 | 2200 Staff payable |
| Product sold / returned | 5400 COGS / 1300 | 1300 Inventory / 5400 |
| Purchase on credit | 1300 Inventory | 2300 Supplier payables |
| Owner puts money in / takes it out | Cash account / 3100 | 3100 Capital / Cash account |
| Owner pays a supplier from own pocket | 2300 | 3100 Capital |
| Person's credit turned into capital | 2100 (or 2300) | 3100 Capital |
| Gold/coins/currency bought / sold | 14xx at cost / Cash | Cash / 14xx at average cost + 4900 gain (or Dr 4900 loss) |
| Owner brings / takes gold or dollars | 14xx / 3100 | 3100 / 14xx at average cost |
| Opening balances (cash, debts, stock, gold) | the asset | 3200 Opening balances |
| Invoice voided | reversing entry (on the invoice's own date when "cancel": it never happened; today when refunded) | |

Each POS terminal / card / bank account gets its own ledger sub-account (11xx), so balances per terminal are always available. Each service line gets its own revenue account (41xx) and forfeit account (48xx); each gold/currency holding its own 14xx.

**Deposit vs credit.** `Deposit.kind` None/"deposit" = بیعانه, a prepayment for a SERVICE: it pays only the services of an invoice (after their share of the discount) and can be forfeited. "credit" = اعتبار, the customer's money kept with us (balances brought over from Tizpardaz, money of a voided invoice kept for them, website refunds): pays anything, never forfeited.

**Treasury (خزانه)** (`services/treasury.py`, table `treasury_rates`): per service line and Jalali month, a rate in
hundredths of a percent (a month without its own rate takes the line's latest earlier one; none: 0). On every service
row at issue: treasury = net x rate (integer, half up), staff share = (net - treasury) x staff % (the commission
above), salon share = the rest; treasury + staff + salon = net. The treasury has no account of its own: it is the
salon's (41xx stays whole); only 5200/2200 get smaller. Saving a month's rate later (owner/admin, preview first)
books that month's rows again - only that month (later months that already have invoices keep the rate they used, as
an "auto" row): the "commission" entry of each changed invoice is deleted through the ORM and posted again on its
date, and for a voided invoice the commission part of its "invoice_void" reversal too, on the reversal's date.

**Profit** on the dashboard and the capital page comes from the ledger (`capital.profit_breakdown`): every revenue (41xx, 42xx, 48xx, 49xx) minus every expense (51xx expenses, 52xx staff share, 53xx cash differences, 54xx COGS, 55xx stock shortages) dated in the period. **Net worth** (`capital.position`) = assets - liabilities = capital 3100 + opening 3200 + profit to date; the page shows how it changed: worth at the start + put in - taken out + profit (+ openings entered) = worth now.

**Concurrency.** Status changes that must happen once (pay, void, refund/forfeit/use a deposit, supplier payments, capital/holding voids) are conditional UPDATEs checked in the database (`accounting._claim`, atomic `paid` updates), so a double click can't book twice.

## Events
Core emits events (`deposit.created`, `invoice.issued`, `bank_transaction.imported`, `receipt.mismatch`, …). Plugin handlers run inside the caller's DB transaction in a SAVEPOINT (`payload["_db"]`), so they can write atomically and a failing plugin is rolled back alone.

## Reconciliation flow
```
customer receipt (SMS/WhatsApp/Instagram/manual) ─► InboundReceipt ─┐
bank API / bank SMS / statement CSV ─► BankTransaction ─────────────┤
                                                                     ▼
                        matching.find_bank_match (amount, ±window, reference, card digits)
               ├─ same reference but different amount ─► mismatch alert (tampered receipt)
               ├─ match + known customer ─► deposit registered on that customer
               ├─ match + unknown sender ─► bot asks for name ─► customer created ─► deposit
               └─ no bank record after grace period ─► "receipt without deposit" alert
```

## Local vs web
* **Local**: SQLite file in `backend/data/`, secrets generated on first run, the API serves the built UI on one port, automatic encrypted backups.
* **Web**: same image via Docker, PostgreSQL optional, secrets from environment, run behind HTTPS.
