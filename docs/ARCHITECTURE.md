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
| Staff share of a forfeited deposit ((amount - treasury) x staff %, ref `forfeit_commission`) | 5200 | 2200 |
| Expense (voided: the reverse on its own date, ref `expense_void`; its line on the 51xx leg; «از خزانه» is a flag on the Expense, no other entry) | 51xx Expense category | Cash account |
| Staff commission ((net - treasury) x staff %, per row) | 5200 | 2200 Staff payable |
| Employee's salary for a month (ref `salary`) | 51xx «حقوق و دستمزد» | 2200 (the employee) |
| Advance / settlement to a staff member or employee (ref `staff_payout`, ref_id = StaffPayout) | 2200 (the person) | Cash account (or 3100 when the owner paid from own pocket; 3200 when paid before the program started, `before_start`) |
| Staff share of a booked month of the previous software (ref `history_share`, ref_id = HistoryMonth, at the month's last moment) | 3200 Opening balances | 2200 (per staff member and line) |
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
salon's (41xx stays whole); only 5200/2200 get smaller (1.23: it is the fund of the line's expenses, see below). Saving a month's rate later (owner/admin, preview first)
books that month's rows again - only that month (later months that already have invoices keep the rate they used, as
an "auto" row): the "commission" entry of each changed invoice is deleted through the ORM and posted again on its
date, and for a voided invoice the commission part of its "invoice_void" reversal too, on the reversal's date.

**Forfeited deposits (1.22)** are shared like a service of their line: at the forfeit the line's treasury of that
month and the deposit's staff member's share are kept on the deposit (`forfeit_line_id`, `treasury_bp/amount`,
`commission_percent/amount`) and the share is booked 5200/2200 on the forfeit's date (`forfeit_commission`); a new
rate for that month books them again (`accounting.repost_forfeit_commissions`). Forfeits from before 1.22
(`treasury_bp` NULL) stay all the salon's. `reports.staff_shares`: revenue + forfeits = treasury + staff + salon.

**Treasury as a fund of each line (1.23, `services/treasury_fund.py`, page /treasury).** A line's treasury is the
fund for that line's expenses (consumables, repairs): accrued per month = the treasury of its invoice rows (sold in the
month, returns taken back in the return's month), forfeits and the previous software's services (= the month's
treasury on the staff page, tested); spent = expenses with `from_treasury` of that line (not voided; ordinary 51xx
expenses: the profit falls the same) and memo rows `TreasuryMove` ("spent_before": spent before the program started;
"opening": the fund's balance at the start) - no ledger entry for those. The balance is carried forward per line and per
staff member (an expense's `staff_id`). Nothing of this is an account: the treasury stays inside the salon's income.

**The previous software's staff work (1.23, `services/history_shares.py`).** Imported done appointments
(`reports._old_service()`) dated before `books_start` (setting «books.start», default the first invoice / opening
balance) are shared like invoice rows: line of the service, the month's treasury rate, the staff % (the month's stored
`HistoryMonth.percents` or today's). Live until the month is booked; booking freezes them on the appointment (`line_id`,
`treasury_bp/amount`, `commission_percent/amount`) and posts Dr 3200 / Cr 2200 (profit unchanged: that revenue was never
in these books). A booked month is booked again on a new treasury rate (treasury.save), a staff member set for a name
not matched at import (relink), an import or its undo, a service moved to a product, a moved start day. Payouts of
those months: `StaffPayout.before_start` (Dr 2200 / Cr 3200), allowed only before the start. `reports.staff_shares`
includes these rows (`history`, `history_share`, `totals.history_unbooked`), so staff share = 5200 + booked
`history_share` + the unbooked (shown only).

**Bank lines that are not a customer's money (1.23, `services/bank_booking.py`, `services/bank_parties.py`).** Card
numbers / IBANs are never stored: the other side of a line is a keyed hash + mask + name, descriptions are masked
(`seal_old` masks older rows once, and encrypts a bank API token). An out line (or an in line no customer's money
took) is linked to an entry made by hand (payout, expense, owner's money; same account and amount within 3 days, same
reference when both have one - in both orders, `after_record`), else booked by a `BankRule` (a staff member's card from
their statement; exact hash, one rule, automatic, 45 days, not a duplicate of another source) through the normal
services (`staffpay.pay(source="bank")`, `record_expense`, `capital.add_or_withdraw`), else waits in the inbox.
`undo` voids what was booked on its own date and turns the rule's automatic booking off. The same money from two
sources (SMS + statement/API, same account, amount and direction within 15 minutes) is kept as `duplicate`. A bank SMS
whose direction is unclear is "unknown" (never matched).

**Staff and employees' money (1.22, `services/staffpay.py`).** 2200 per `staff_id` is what we owe each person (minus:
they owe us). Practitioners earn their share (commission, forfeit share); employees (`Staff.kind = "employee"`, no
line, no percent) earn the month's salary when it is entered (`SalaryAccrual`, an expense of that month, dated now or
at the month's last moment). Advances during the month and the month-end settlement are `StaffPayout` rows (month it
is for: jy/jm); a payout entered by mistake is voided on its own date. `staffpay.statement` = owed at the start +
earned - given = owed at the end, and what was paid for that month after it ended. Payouts booked before 1.22 (an
entry whose ref_id was the staff id) get their row at start (`backfill_payouts`). A payout receipt (picture or text)
is read by `services/payout_receipt.py` to fill the form (nothing is booked by it); its picture is kept under
`data/receipts/staff/<sha256>`. The capital page shows people who owe us as an asset, not as less owed to the others.

**Amounts** above `accounting.MAX_RIAL` (10^15 Rial) are refused (a key held down); a number too big for the database
gives a Persian 400, never a broken sum. Receipt pictures of booked payouts are kept in the database
(`receipt_images`), so backups carry them.

**Expenses in the reports** (summary, daily series, by category) come from the ledger 51xx (`reports.expense_rows`),
so salaries and voided expenses agree with the profit.

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
