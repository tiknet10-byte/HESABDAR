"""Receipts from customers via SMS / WhatsApp / Instagram + chatbot (concept/beta).

Pipeline:
  incoming message --> store in conversation history (used for learning)
     |-- looks like a receipt (text or screenshot)? -> InboundReceipt -> parse -> reconcile with bank
     |      |-- sender is a known customer   -> deposit registered on their account (after bank match)
     |      '-- unknown sender               -> bot asks for the full name, then creates the customer
     '-- waiting for name?                    -> save name, create/attach customer, register receipt

Channel adapters normalise each platform's webhook payload into (sender, text, image).
Outgoing replies are returned in the webhook response (SMS forwarder apps send them) and
stored in the outbox; WhatsApp/Instagram replies are sent through their APIs when configured.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ....ai import claude
from ....api.deps import require
from ....core.db import SessionLocal, get_db
from ....core.security import verify_signature
from ....models import BankTransaction, ConversationMessage, ConversationState, Customer, InboundReceipt, PaymentAccount, local_now
from ....services import accounting, learning, matching, settings_store
from ....services.receipt_parser import looks_like_receipt, parse_receipt
from ....services.textutil import normalize_mobile
from ...base import AITool, BasePlugin

log = logging.getLogger("hesabdar.messaging")

RECEIPT_VISION_PROMPT = (
    "This is a screenshot of an Iranian bank payment receipt (card-to-card, transfer or POS). Extract: "
    '{"is_receipt": bool, "amount_rial": int|null, "reference": str|null, "card_last4": str|null, '
    '"datetime_jalali": "YYYY/MM/DD HH:MM"|null, "bank": str|null, "status_success": bool, "raw_text": str}. '
    "If the amount is shown in Toman convert to Rial (x10). Use English digits."
)
# a saved receipt file gets its extension from this list only (never from what the sender wrote)
IMAGE_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "application/pdf": "pdf"}
# pictures sent on Instagram are downloaded only from Meta's own servers, over https, at most 8 MB
MEDIA_HOSTS = ("fbcdn.net", "fbsbx.com", "cdninstagram.com")
MAX_MEDIA = 8 * 1024 * 1024


def meta_signature_ok(raw: bytes, header: str | None, secret: str | None) -> bool:
    """Meta signs every webhook POST: X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(app secret, raw body)."""
    if not secret or not header:
        return False
    good = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, header.strip())


def fetch_media(url: str) -> tuple[bytes, str] | None:
    """Download a picture a customer sent (Instagram gives a link). Anything else is refused (no requests to
    the clinic's own network)."""
    try:
        u = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError):
        return None
    host = (u.host or "").lower()
    if u.scheme != "https" or u.port not in (None, 443) or not any(host == h or host.endswith("." + h) for h in MEDIA_HOSTS):
        log.warning("refused to download receipt image from %s", host)
        return None
    try:
        with httpx.stream("GET", u, timeout=20, follow_redirects=False) as resp:
            if resp.status_code != 200:
                return None
            data = bytearray()
            for chunk in resp.iter_bytes():
                data += chunk
                if len(data) > MAX_MEDIA:
                    return None
            return bytes(data), resp.headers.get("content-type", "image/jpeg")
    except httpx.HTTPError:
        return None


# ------------------------------------------------------------------ adapters
def parse_whatsapp(payload: dict) -> list[dict]:
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []):
                out.append({"sender": m.get("from"), "text": (m.get("text") or {}).get("body", "") or (m.get("image") or {}).get("caption", ""),
                            "media_id": (m.get("image") or {}).get("id")})
    return out


def parse_instagram(payload: dict) -> list[dict]:
    out = []
    for entry in payload.get("entry", []):
        for ev in entry.get("messaging", []):
            msg = ev.get("message") or {}
            if msg.get("is_echo"):
                continue  # a copy of a message the clinic itself sent
            img = next((a.get("payload", {}).get("url") for a in msg.get("attachments", []) if a.get("type") == "image"), None)
            out.append({"sender": (ev.get("sender") or {}).get("id"), "text": msg.get("text", ""), "image_url": img})
    return out


def send_reply(channel: str, to: str, text: str, config: dict) -> None:
    """Best-effort outbound message through official APIs (requires tokens in plugin config)."""
    try:
        if channel == "whatsapp" and config.get("whatsapp_token") and config.get("whatsapp_phone_id"):
            httpx.post(f"https://graph.facebook.com/v21.0/{config['whatsapp_phone_id']}/messages", timeout=20,
                       headers={"Authorization": f"Bearer {config['whatsapp_token']}"},
                       json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}})
        elif channel == "instagram" and config.get("instagram_token"):
            httpx.post("https://graph.facebook.com/v21.0/me/messages", timeout=20,
                       params={"access_token": config["instagram_token"]},
                       json={"recipient": {"id": to}, "message": {"text": text}})
        elif channel == "sms" and config.get("sms_gateway_url"):
            httpx.post(config["sms_gateway_url"], timeout=20, json={"to": to, "text": text,
                                                                     "key": config.get("sms_gateway_key", "")})
    except httpx.HTTPError:
        log.exception("failed to send %s reply", channel)


# ------------------------------------------------------------------ bot core
def _state(db: Session, channel: str, peer: str) -> ConversationState:
    st = db.scalar(select(ConversationState).where(ConversationState.channel == channel, ConversationState.peer == peer))
    if st is None:
        st = ConversationState(channel=channel, peer=peer, state="idle", data={})
        db.add(st)
        db.flush()
    return st


def _identify(db: Session, channel: str, sender: str) -> Customer | None:
    if channel == "instagram":
        return accounting.find_customer(db, instagram=sender)
    return accounting.find_customer(db, mobile=sender)


def _fmt_toman(rial: int | None) -> str:
    return f"{(rial or 0) // 10:,}"


def handle_incoming(db: Session, channel: str, sender: str, text: str = "", image: bytes | None = None,
                    image_type: str = "image/jpeg", config: dict | None = None) -> dict:
    config = config or {}
    image_type = (image_type or "").split(";")[0].strip().lower().replace("image/jpg", "image/jpeg")
    if image_type not in IMAGE_EXT:
        image_type = "image/jpeg"
    sender = normalize_mobile(sender) or sender
    customer = _identify(db, channel, sender)
    db.add(ConversationMessage(channel=channel, peer=sender, customer_id=customer.id if customer else None, direction="in",
                               text=text or ("[image]" if image else "")))
    st = _state(db, channel, sender)
    replies: list[str] = []
    receipt: InboundReceipt | None = None

    # 1) waiting for the customer's name
    if st.state == "awaiting_name" and text and not looks_like_receipt(text):
        name = text.strip()[:120]
        mobile = sender if channel != "instagram" else None
        insta = sender if channel == "instagram" else None
        mobile_in_text = normalize_mobile(next((w for w in text.split() if normalize_mobile(w)), None))
        customer, created = accounting.find_or_create_customer(db, name, mobile or mobile_in_text, insta, source=f"chat_{channel}")
        if created and mobile_in_text and mobile_in_text in name:  # never rename a customer we already had
            customer.full_name = name.replace(mobile_in_text, "").strip() or customer.full_name
        rid = (st.data or {}).get("receipt_id")
        st.state, st.data = "idle", {}
        if rid:
            receipt = db.get(InboundReceipt, rid)
            receipt.customer_id = customer.id
            matching.reconcile_receipt(db, receipt)
        replies.append(_status_reply(db, receipt, customer))

    # 2) a new receipt
    elif image is None and text and looks_like_receipt(text) and (receipt := _same_receipt(db, channel, sender, text)):
        # the same receipt again (sent twice, or a webhook delivered again): no second receipt
        if customer is None:
            st.state, st.data = "awaiting_name", {"receipt_id": receipt.id}
            replies.append(settings_store.get(db, "bot.ask_name_message"))
        else:
            replies.append(_status_reply(db, receipt, customer))

    elif image is not None or (text and looks_like_receipt(text)):
        parsed = parse_receipt(text) if text else {}
        if image is not None and claude.available():
            try:
                v = claude.vision_extract(image, image_type, RECEIPT_VISION_PROMPT)
                vt = v.get("raw_text") or ""
                parsed = {**parse_receipt(vt), **{k: v for k, v in {"amount": v.get("amount_rial"), "reference": v.get("reference"),
                                                                    "card_last4": v.get("card_last4"), "bank": v.get("bank")}.items() if v}}
                text = text or vt
                if v.get("datetime_jalali"):
                    parsed.update({k: x for k, x in parse_receipt(v["datetime_jalali"]).items() if k == "datetime" and x})
            except Exception:
                log.exception("vision parsing failed")
        receipt = InboundReceipt(channel=channel, sender=sender, raw_text=text or "", parsed=parsed,
                                 amount=parsed.get("amount"), customer_id=customer.id if customer else None)
        db.add(receipt)
        db.flush()
        if image is not None:
            from ....core.config import get_settings
            d = get_settings().backup_dir.parent / "receipts"
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{receipt.id}.{IMAGE_EXT[image_type]}"
            if path.resolve().parent != d.resolve():
                raise HTTPException(400, "invalid file name")
            path.write_bytes(image)
            receipt.image_path = str(path)
        if receipt.amount:
            matching.reconcile_receipt(db, receipt)
        else:
            receipt.status = "pending"
            receipt.note = "مبلغ خوانده نشد - نیاز به بررسی دستی"
        if customer is None:
            st.state, st.data = "awaiting_name", {"receipt_id": receipt.id}
            replies.append(settings_store.get(db, "bot.ask_name_message"))
        else:
            replies.append(_status_reply(db, receipt, customer))
        # learn from the customer's recent messages which service the deposit is for
        if receipt.deposit_id:
            _learn_from_recent_chat(db, channel, sender, receipt)

    # 3) normal chat message -> keep for learning; optionally guess service interest
    else:
        guess = learning.classify_text(db, text) if text else []
        if guess:
            st.data = {**(st.data or {}), "last_service_guess": guess[0]}

    for reply in replies:
        if reply:
            db.add(ConversationMessage(channel=channel, peer=sender, customer_id=customer.id if customer else None,
                                       direction="out", text=reply))
            if settings_store.get(db, "bot.auto_reply", True) and channel in ("whatsapp", "instagram"):
                send_reply(channel, sender, reply, config)
    db.commit()
    return {"replies": [r for r in replies if r], "receipt_id": receipt.id if receipt else None,
            "receipt_status": receipt.status if receipt else None, "customer_id": customer.id if customer else None}


def _same_receipt(db: Session, channel: str, sender: str, text: str) -> InboundReceipt | None:
    """A receipt with exactly this text from the same sender in the last day (not rejected)."""
    return db.scalar(select(InboundReceipt).where(
        InboundReceipt.channel == channel, InboundReceipt.sender == sender, InboundReceipt.raw_text == text,
        InboundReceipt.status != "rejected", InboundReceipt.created_at >= local_now() - timedelta(hours=24))
        .order_by(InboundReceipt.id.desc()).limit(1))


def _status_reply(db: Session, receipt: InboundReceipt | None, customer: Customer) -> str:
    if receipt is None:
        return f"{customer.full_name} عزیز، مشخصات شما ثبت شد. 🌸"
    if receipt.status == "registered":
        return settings_store.get(db, "bot.confirm_message").format(name=customer.full_name, amount=_fmt_toman(receipt.amount))
    return settings_store.get(db, "bot.mismatch_message")


def _learn_from_recent_chat(db: Session, channel: str, peer: str, receipt: InboundReceipt) -> None:
    from ....models import Deposit

    dep = db.get(Deposit, receipt.deposit_id)
    msgs = db.scalars(select(ConversationMessage).where(ConversationMessage.channel == channel, ConversationMessage.peer == peer,
                                                        ConversationMessage.direction == "in")
                      .order_by(ConversationMessage.at.desc()).limit(10)).all()
    text = " ".join(m.text for m in msgs)
    guess = learning.classify_text(db, text)
    if dep and guess:
        dep.service_guess = {**(dep.service_guess or {}), "from_chat": guess}
        if not dep.service_id and guess[0]["score"] >= 0.6:
            dep.service_id = guess[0]["service_id"]


# ------------------------------------------------------------------ plugin
class IncomingIn(BaseModel):
    channel: str = "sms"
    sender: str
    text: str = ""
    image_base64: str | None = None
    image_type: str = "image/jpeg"


class AssignIn(BaseModel):
    customer_id: int | None = None
    full_name: str | None = None
    mobile: str | None = None
    payment_account_id: int | None = None
    service_id: int | None = None
    force: bool = False  # register without bank confirmation (manager decision)


class Plugin(BasePlugin):
    name = "messaging_receipts"
    title = "دریافت رسید از پیامک، واتساپ و اینستاگرام"
    description = ("تطبیق رسیدهای ارسالی مشتریان با واریزهای بانک، هشدار در صورت مغایرت، "
                   "و چت خودکار برای دریافت مشخصات مشتری جدید")
    version = "0.9.0"
    category = "integration"
    status = "beta"
    enabled_by_default = False  # it takes messages from outside: the owner turns it on after setting it up
    config_schema = {"type": "object", "properties": {
        "app_secret": {"type": "string", "title": "App Secret برنامه متا (برای امضای وبهوک واتساپ و اینستاگرام)", "secret": True},
        "whatsapp_verify_token": {"type": "string", "title": "توکن تأیید وبهوک واتساپ"},
        "whatsapp_token": {"type": "string", "title": "توکن WhatsApp Cloud API", "secret": True},
        "whatsapp_phone_id": {"type": "string", "title": "Phone Number ID"},
        "instagram_token": {"type": "string", "title": "توکن Instagram Messaging", "secret": True},
        "sms_gateway_url": {"type": "string", "title": "آدرس درگاه ارسال پیامک"},
        "sms_gateway_key": {"type": "string", "title": "کلید درگاه پیامک", "secret": True},
    }}
    ui = [{"path": "/reconciliation", "title": "تطبیق رسیدها", "icon": "scan"}]

    def setup(self) -> None:
        r = APIRouter()
        plugin = self

        @r.post("/simulate")
        def simulate(body: IncomingIn, db: Session = Depends(get_db), _=Depends(require("write"))):
            """Test the bot from the UI (or receive a manually forwarded receipt)."""
            return handle_incoming(db, body.channel, body.sender, body.text, _image(body), body.image_type, plugin.ctx.config)

        @r.post("/webhook/sms")
        async def sms_webhook(request: Request, db: Session = Depends(get_db)):
            """For SMS-forwarder apps on the salon phone: POST JSON {sender, text}, signed with HMAC-SHA256."""
            raw = await request.body()
            if not verify_signature(raw, request.headers.get("X-Hesabdar-Signature")):
                raise HTTPException(401, "invalid signature")
            body = IncomingIn.model_validate_json(raw)
            return handle_incoming(db, "sms", body.sender, body.text, _image(body), body.image_type, plugin.ctx.config)

        @r.get("/webhook/whatsapp")
        def wa_verify(mode: str = Query(alias="hub.mode", default=""), token: str = Query(alias="hub.verify_token", default=""),
                      challenge: str = Query(alias="hub.challenge", default="")):
            if mode == "subscribe" and token and token == plugin.ctx.config.get("whatsapp_verify_token"):
                return PlainTextResponse(challenge)
            raise HTTPException(403)

        async def meta_payload(request: Request) -> dict:
            """Only Meta may post here: the body must carry its signature made with the App Secret of the settings."""
            raw = await request.body()
            secret = plugin.ctx.config.get("app_secret")
            if not secret:
                raise HTTPException(401, "App Secret متا در تنظیمات افزونه وارد نشده است")
            if not meta_signature_ok(raw, request.headers.get("X-Hub-Signature-256"), secret):
                raise HTTPException(401, "invalid signature")
            try:
                data = json.loads(raw)
            except ValueError as exc:
                raise HTTPException(400, "invalid json") from exc
            return data if isinstance(data, dict) else {}

        @r.post("/webhook/whatsapp")
        async def wa_webhook(request: Request, db: Session = Depends(get_db)):
            msgs = [m for m in parse_whatsapp(await meta_payload(request)) if m.get("sender")]

            def run() -> int:  # off the event loop: replies and the database may take a while
                for m in msgs:
                    handle_incoming(db, "whatsapp", m["sender"], m["text"], None, config=plugin.ctx.config)
                return len(msgs)
            return {"handled": await asyncio.to_thread(run)}

        @r.post("/webhook/instagram")
        async def ig_webhook(request: Request, db: Session = Depends(get_db)):
            msgs = [m for m in parse_instagram(await meta_payload(request)) if m.get("sender")]

            def run() -> int:
                for m in msgs:
                    got = fetch_media(m["image_url"]) if m.get("image_url") else None
                    img, ctype = got if got else (None, "image/jpeg")
                    handle_incoming(db, "instagram", m["sender"], m["text"], img, ctype, config=plugin.ctx.config)
                return len(msgs)
            return {"handled": await asyncio.to_thread(run)}

        @r.get("/receipts")
        def receipts(status: str | None = None, db: Session = Depends(get_db), _=Depends(require("read"))):
            q = select(InboundReceipt).order_by(InboundReceipt.created_at.desc()).limit(300)
            if status:
                q = q.where(InboundReceipt.status == status)
            names = {c.id: c.full_name for c in db.scalars(select(Customer))}
            return [{"id": x.id, "channel": x.channel, "sender": x.sender, "amount": x.amount, "status": x.status,
                     "confidence": x.confidence, "note": x.note, "parsed": x.parsed, "raw_text": x.raw_text,
                     "customer_id": x.customer_id, "customer": names.get(x.customer_id), "deposit_id": x.deposit_id,
                     "bank_transaction_id": x.bank_transaction_id, "created_at": x.created_at.isoformat()} for x in db.scalars(q)]

        @r.post("/receipts/{rid}/recheck")
        def recheck(rid: int, db: Session = Depends(get_db), user=Depends(require("finance"))):
            x = db.get(InboundReceipt, rid) or _404()
            matching.reconcile_receipt(db, x, user=user)
            db.commit()
            return {"status": x.status, "note": x.note}

        @r.post("/receipts/{rid}/assign")
        def assign(rid: int, body: AssignIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
            x = db.get(InboundReceipt, rid) or _404()
            if body.customer_id:
                c = db.get(Customer, body.customer_id) or _404()
            elif x.customer_id and not body.full_name and not body.mobile:
                c = db.get(Customer, x.customer_id) or _404()  # the sender is already known (e.g. on Instagram)
            else:
                c, _ = accounting.find_or_create_customer(db, body.full_name, body.mobile or x.sender, source="receipt", user=user)
            x.customer_id = c.id
            if not x.bank_transaction_id and not body.force:
                matching.reconcile_receipt(db, x, user=user)  # look for its bank transaction now
            # matched on amount + time only (or chosen by the manager without the bank): this person confirms it
            if not x.deposit_id and (x.bank_transaction_id or body.force):
                tx = db.get(BankTransaction, x.bank_transaction_id) if x.bank_transaction_id else None
                pa = db.get(PaymentAccount, body.payment_account_id) if body.payment_account_id else None
                try:
                    matching.register_receipt(db, x, c, payment_account=pa, tx=tx, service_id=body.service_id, user=user)
                except accounting.AccountingError as exc:
                    raise HTTPException(400, str(exc)) from exc
                if body.force and not tx:
                    x.note = f"ثبت دستی بدون تأیید بانک توسط {user.username}"
            db.commit()
            return {"status": x.status, "deposit_id": x.deposit_id}

        @r.post("/receipts/{rid}/reject")
        def reject(rid: int, db: Session = Depends(get_db), _=Depends(require("finance"))):
            x = db.get(InboundReceipt, rid) or _404()
            if x.deposit_id:
                raise HTTPException(400, "این رسید ثبت شده و بیعانه دارد؛ اگر اشتباه بوده، بیعانه را از صفحه بیعانه‌ها برگردانید")
            x.status = "rejected"
            tx = db.get(BankTransaction, x.bank_transaction_id) if x.bank_transaction_id else None
            if tx is not None and tx.matched_type == "receipt" and tx.matched_id == x.id:
                # the bank money it was matched to is free again (for the real payer's receipt)
                tx.status, tx.matched_type, tx.matched_id = "unmatched", None, None
                x.bank_transaction_id = None
                matching.on_bank_transaction(db, tx)
            db.commit()
            return {"ok": True}

        @r.get("/conversations")
        def conversations(peer: str | None = None, db: Session = Depends(get_db), _=Depends(require("read"))):
            q = select(ConversationMessage).order_by(ConversationMessage.at.desc()).limit(500)
            if peer:
                q = q.where(ConversationMessage.peer == peer)
            return [{"id": m.id, "at": m.at.isoformat(), "channel": m.channel, "peer": m.peer, "direction": m.direction,
                     "text": m.text, "customer_id": m.customer_id, "service_id": m.service_id} for m in db.scalars(q)]

        @r.post("/conversations/{mid}/label")
        def label(mid: int, service_id: int, db: Session = Depends(get_db), _=Depends(require("write"))):
            """Teach the system: this message was about this service."""
            m = db.get(ConversationMessage, mid) or _404()
            m.service_id = service_id
            learning.learn_text(db, m.text, service_id)
            db.commit()
            return {"ok": True}

        self.router = r

    def jobs(self):  # noqa: ANN201
        def expire() -> None:
            with SessionLocal() as db:
                matching.expire_pending_receipts(db)
                db.commit()
        return [(3600, expire)]

    def ai_tools(self) -> list[AITool]:
        def pending(db, user):  # noqa: ANN001, ANN202
            q = select(InboundReceipt).where(InboundReceipt.status.in_(["pending", "mismatch", "matched"])).limit(50)
            return [{"id": x.id, "sender": x.sender, "amount": x.amount, "status": x.status, "note": x.note,
                     "created_at": x.created_at.isoformat()} for x in db.scalars(q)]
        return [AITool("customer_receipts_to_review", "Receipts sent by customers that are pending, matched-but-unassigned or mismatched.",
                       {"type": "object", "properties": {}}, pending)]


def _404():  # noqa: ANN202
    raise HTTPException(404, "not found")


def _image(body: IncomingIn) -> bytes | None:
    try:
        return base64.b64decode(body.image_base64) if body.image_base64 else None
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "تصویر رسید خراب است") from exc

