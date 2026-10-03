# Writing a plugin (افزونه)

A plugin is a folder with `plugin.py` defining `class Plugin(BasePlugin)`.
Put third-party plugins in `backend/plugins/<name>/`; they are discovered at startup and can be
enabled/disabled and configured from **Settings → Plugins** without touching core code.

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import require
from app.core.db import get_db
from app.plugins.base import AITool, BasePlugin


class Plugin(BasePlugin):
    name = "birthday_sms"
    title = "پیامک تبریک تولد"
    description = "ارسال خودکار تبریک و کد تخفیف در روز تولد مشتری"
    version = "0.1.0"
    config_schema = {"type": "object", "properties": {
        "discount_percent": {"type": "integer", "title": "درصد تخفیف"}}}
    default_config = {"discount_percent": 10}

    def setup(self):
        # 1) REST endpoints -> /api/plugins/birthday_sms/...
        r = APIRouter()

        @r.get("/today")
        def today(db: Session = Depends(get_db), _=Depends(require("read"))):
            return []
        self.router = r

        # 2) react to business events (runs in the same DB transaction)
        def on_customer(event, payload):
            db = payload["_db"]
            ...
        self.events = {"customer.created": on_customer}

    # 3) periodic jobs: [(interval_seconds, fn)]
    def jobs(self):
        return [(24 * 3600, self.send_greetings)]

    def send_greetings(self):
        ...

    # 4) give the AI assistant / MCP clients a new ability
    def ai_tools(self):
        return [AITool("birthdays_this_week", "Customers with a birthday this week.",
                       {"type": "object", "properties": {}}, lambda db, user: [], "read")]
```

### Events
`customer.created, deposit.created, deposit.applied, payment.created, invoice.issued, expense.created,
bank_transaction.imported, receipt.matched, receipt.mismatch, alert.created, backup.created`
— use `"*"` to receive all of them.

### Built-in plugins
| name | purpose |
|---|---|
| `bank_sync` | bank API providers, bank SMS webhook, statement import |
| `messaging_receipts` | SMS / WhatsApp / Instagram receipts, matching, chatbot |
| `sales_book_ocr` | scan sales book / invoices, validation, commit |
| `loyalty` | small example: loyalty points per purchase |

New bank? Subclass `BankProvider` in `bank_sync/plugin.py` (or your own plugin) and implement `fetch(account, since)`.
