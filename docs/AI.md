# AI integration, MCP and continuous learning

The system is built so AI can both **use** it and **learn** from it. One tool registry
(`app/ai/tools.py` + every enabled plugin's `ai_tools()`) is exposed three ways, always
filtered by the caller's role permissions:

1. **Built-in assistant** — `/api/ai/chat`, Persian chat in the UI. Uses Claude (`claude-opus-5-5`
   by default, `HESABDAR_AI_MODEL` to change) with tool use, adaptive thinking and server-side
   refusal fallback. Without credentials it answers common questions offline.
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
