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
| Invoice issued | 1200 Receivables | 41xx Revenue of each service line (net of discount) |
| Deposit applied | 2100 Customer deposits | 1200 Receivables |
| Payment | Cash/POS/Card account | 1200 Receivables |
| Deposit refunded | 2100 | Cash account |
| Deposit forfeited (no-show) | 2100 | 4800 Forfeited-deposit income |
| Expense | 51xx Expense category | Cash account |
| Invoice voided | reversing entry | |

Each POS terminal / card / bank account gets its own ledger sub-account (11xx), so balances per terminal are always available. Each service line gets its own revenue account (41xx).

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
