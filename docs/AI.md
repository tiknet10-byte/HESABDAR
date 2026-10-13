# AI integration, MCP and continuous learning

The system is built so AI can both **use** it and **learn** from it. One tool registry
(`app/ai/tools.py` + every enabled plugin's `ai_tools()`) is exposed three ways, always
filtered by the caller's role permissions:

1. **Built-in assistant** — `/api/ai/chat`, Persian chat in the UI. Uses Claude (`claude-opus-5-5`
   by default, `HESABDAR_AI_MODEL` to change) with tool use, adaptive thinking and server-side
   refusal fallback. Without credentials it answers common questions offline (revenue, profit and
   forecasts only for roles with the `reports` permission, like the reports page).
   The chat runs **read tools** by itself. Tools that change data (`create_customer`, `register_deposit`,
   `teach_service_alias`) are only **proposed**: the page shows a confirm button and runs the tool through
   `POST /api/ai/tools/{name}` (audited) when the user presses it. Customer names, notes and imported text can
   contain anything, so the model alone never changes the books. Each user's chat is kept in the browser tab under
   its own key and is cleared on logout.
2. **MCP server** — for Claude Desktop, Claude Code or any MCP client:
   ```bash
   cd backend && .venv/bin/python manage.py create-user ai ai_agent
   ```
   ```json
   {"mcpServers": {"hesabdar": {"command": "/path/backend/.venv/bin/python",
     "args": ["manage.py", "mcp", "--user", "ai"], "cwd": "/path/backend"}}}
   ```
3. **REST tool API** — `GET /api/ai/tools` returns JSON-schema tool definitions; `POST /api/ai/tools/{name}`
   runs one. Works with any function-calling model or automation (n8n, Zapier, custom agents).
   Every call is written to the audit log.

**The `ai_agent` role** (for the MCP user and automation logins) has `read`, `reports` and `write`: it reads data
and reports and may add customers and deposits, but it has no `finance` (refunds, expenses, staff payouts, opening
balances), `settings` (so not `teach_service_alias`), `void`, `capital`, `users` or `backup`. Its login is still a
real login: keep its password out of shared places, and deactivate it (or set a new password) when an automation is
no longer used — that ends its existing tokens at once.

| Tool | Permission |
|---|---|
| search_customers, customer_profile, list_services, open_deposits, alerts, reconciliation_status, guess_service | read |
| plugins: customer_receipts_to_review (messaging_receipts), loyalty_points (loyalty) | read |
| financial_summary, revenue_timeseries, revenue_forecast, business_insights, payment_accounts | reports |
| plugins: unmatched_bank_transactions (bank_sync), sales_book_batches_pending (sales_book_ocr) | reports |
| create_customer, register_deposit | write |
| teach_service_alias | settings |

A tool that only reads takes `read` or `reports` (the chat runs those by itself); `finance` means moving money, so a
tool with any other permission is always a proposal the user confirms in the chat.

`GET /api/ai/knowledge-export` exports the service catalog and everything learned, for syncing into an
external AI memory / RAG store.

## Vision
With AI enabled, receipt screenshots (WhatsApp / Instagram) and photos or PDFs of the sales book are read
by Claude vision and converted to structured JSON, then validated by the same deterministic rules as text input.

## Continuous learning (`app/services/learning.py`)
* **Prices** — the real median price of each service is recomputed from invoices; price checks warn when
  an invoice or a scanned book row is far from what the salon usually charges.
* **Vocabulary** — every labelled chat message, invoice line, sales-book row and user correction
  (e.g. confirming which service a deposit is for) updates token→service weights. The classifier then
  recognises how *your* customers talk ("کراتینه", "ژلیش", "لایت") and guesses which service a deposit belongs to.
* **Deposits** — typical deposit amounts per service and the customer's own history improve guesses.
* Settings → AI → *Retrain* rebuilds the model from the full history.

All learned knowledge lives in the database, so it is backed up, restored and portable.
