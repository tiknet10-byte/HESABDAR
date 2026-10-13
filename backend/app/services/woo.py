"""The clinic's website shop (WordPress + WooCommerce) and this system, kept in step.

The clinic computer is not reachable from the internet, so the website can't call it: every few minutes this
system asks the website (WooCommerce REST API, with a Read/Write key) what changed.

* Website -> here: every order becomes an invoice (channel "online") on the customer found by mobile (or a new
  customer with that mobile), with the money received on the account chosen for the payment method. A cancelled
  or refunded order voids its invoice (money back on the refund day); an order changed on the website (items,
  partial refund) is booked again so the invoice always equals what the website kept. Orders placed before the
  chosen start date are not booked: they are already in the history and stock brought over from Tizpardaz.
* Here -> website: stock changes queued by woo_queue (purchase +, in-person sale -, voids, stock count = exact
  quantity) change the website's stock of the product with the same SKU.
* Products are linked by SKU; the website's prices are kept as the products' online prices.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.security import decrypt_secret, encrypt_secret
from ..models import Customer, Invoice, Payment, PaymentAccount, Product, WooLog, WooOrder, WooOutbox, WooProduct, local_now
from . import accounting, inventory, routing, settings_store
from .customer_merge import person_key
from .textutil import normalize_mobile
from .woo_queue import CFG_KEY

log = logging.getLogger("hesabdar.woo")


class _HideKeys(logging.Filter):
    """httpx logs every request address; on hosts that need the key in the address (auth "query") the Consumer
    key / secret would land in the log file. They are hidden, the rest of the line is kept."""
    _re = re.compile(r"(consumer_(?:key|secret)=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - a log line that can't be built is left to logging itself
            return True
        if "consumer_" in msg:
            record.msg, record.args = self._re.sub(r"\1***", msg), ()
        return True


logging.getLogger("httpx").addFilter(_HideKeys())

STATE_KEY = "woo.state"
LINKS_KEY = "woo.links"
MASK = "••••••••"
DEFAULTS = {"url": "", "key": "", "secret": "", "enabled": False, "interval": 5, "start": None, "account_id": None,
            "method_accounts": {}, "push_stock": True, "pull_prices": True, "unit": "", "auth": "basic"}
# store currency -> Rial
FACTORS = {"IRR": 1, "IRT": 10, "IRHR": 1000, "IRHT": 10000}
CURRENCY_LABELS = {"IRR": "ریال", "IRT": "تومان", "IRHR": "هزار ریال", "IRHT": "هزار تومان"}
ACTIVE = {"processing", "on-hold", "completed"}  # real orders (the website took the goods out of its stock)
DEAD = {"cancelled", "refunded", "failed", "trash"}
SOURCE = "woocommerce"
CATALOG_EVERY = timedelta(hours=6)
MAX_ATTEMPTS = 20
PER_PAGE = 100  # the most WooCommerce gives in one page
SENT_META = "_hesabdar_sent"  # on a website product: the queued stock changes ("first-last" ids) it has already got

_lock = threading.Lock()
_transport: httpx.BaseTransport | None = None  # tests plug a fake website in here


class WooError(Exception):
    pass


# ------------------------------------------------------------------ settings
def config(db: Session) -> dict:
    cfg = {**DEFAULTS, **(settings_store.get(db, CFG_KEY) or {})}
    cfg["secret"] = decrypt_secret(cfg.get("secret"))
    return cfg


def state(db: Session) -> dict:
    return dict(settings_store.get(db, STATE_KEY) or {})


def _save_state(db: Session, st: dict) -> None:
    settings_store.set_value(db, STATE_KEY, st)


def _mask_key(k: str) -> str:
    return f"{k[:6]}…{k[-4:]}" if len(k) > 12 else ("••••" if k else "")


def public_config(db: Session) -> dict:
    cfg = config(db)
    return {**cfg, "secret": MASK if cfg["secret"] else "", "key": _mask_key(cfg["key"]), "has_key": bool(cfg["key"] and cfg["secret"])}


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url):
        url = "https://" + url
    url = re.sub(r"/wp-json.*$", "", url)
    if url:
        u = urlparse(url)
        if not u.netloc:
            raise WooError("آدرس سایت معتبر نیست")
        if u.scheme != "https" and u.hostname not in ("localhost", "127.0.0.1"):
            raise WooError("آدرس سایت باید با https شروع شود (کلید API روی http امن نیست)")
    return url


def save_config(db: Session, body: dict) -> dict:
    old = config(db)
    cfg = {**old}
    for k in DEFAULTS:
        if k in body and k not in ("key", "secret", "auth", "unit"):  # auth / unit: found by the connection test
            cfg[k] = body[k]
    cfg["url"] = normalize_url(cfg.get("url") or "")
    if cfg["url"] != old["url"]:  # another website: its currency and way of sending the key are found again
        cfg["unit"], cfg["auth"] = "", "basic"
    if body.get("key") and body["key"] != _mask_key(old["key"]):
        cfg["key"] = body["key"].strip()
    if body.get("secret") and body["secret"] != MASK:
        cfg["secret"] = body["secret"].strip()
    try:
        cfg["interval"] = max(1, min(int(cfg.get("interval") or 5), 1440))
        if cfg.get("start"):
            cfg["start"] = date.fromisoformat(str(cfg["start"])[:10]).isoformat()
        cfg["account_id"] = int(cfg["account_id"]) if cfg.get("account_id") else None
        cfg["method_accounts"] = {str(k): int(v) for k, v in (cfg.get("method_accounts") or {}).items() if v}
    except (TypeError, ValueError, AttributeError) as exc:
        raise WooError("یکی از تنظیمات سایت درست نیست (هر چند دقیقه، تاریخ شروع یا حساب دریافت)؛ دوباره انتخاب کنید") from exc
    for aid in [cfg["account_id"], *cfg["method_accounts"].values()]:
        if aid and db.get(PaymentAccount, aid) is None:
            raise WooError("حساب دریافتِ انتخاب‌شده برای سفارش‌های سایت پیدا نشد؛ دوباره انتخاب کنید")
    if cfg["enabled"] and not cfg.get("start"):
        raise WooError("تاریخ شروع ثبت سفارش‌های سایت را مشخص کنید")
    if cfg["enabled"] and not (cfg["url"] and cfg["key"] and cfg["secret"]):
        raise WooError("آدرس سایت، Consumer key و Consumer secret را وارد کنید")
    st = state(db)
    if (cfg["url"], cfg["key"]) != (old["url"], old["key"]) or cfg.get("start") != old.get("start"):
        st.pop("cursor", None)  # read the orders again from the start date
        st.pop("catalog_at", None)
        _save_state(db, st)
    settings_store.set_value(db, CFG_KEY, {**cfg, "secret": encrypt_secret(cfg["secret"]) if cfg["secret"] else ""})
    db.flush()
    return public_config(db)


def note(db: Session, message: str, level: str = "info") -> None:
    db.add(WooLog(level=level, message=message[:400]))
    (log.warning if level != "info" else log.info)("woo: %s", message)


# ------------------------------------------------------------------ website API
def _err(r: httpx.Response) -> str:
    try:
        msg = r.json().get("message") or ""
    except ValueError:
        msg = ""
    if r.status_code == 401:
        return "کلید یا رمز API اشتباه است (Consumer key / Consumer secret)"
    if r.status_code == 403:
        return "این کلید اجازهٔ کافی ندارد؛ در ووکامرس دسترسی کلید را «خواندن/نوشتن» (Read/Write) بگذارید"
    if r.status_code == 404:
        return "ووکامرس در این آدرس پیدا نشد؛ آدرس سایت را بررسی کنید" + (f" ({msg})" if msg else "")
    return f"خطای سایت ({r.status_code}){': ' + msg if msg else ''}"


def _https_only(request: httpx.Request) -> None:
    """The key goes with every request: never to a plain http address (e.g. a website that redirects to http)."""
    if request.url.scheme != "https" and request.url.host not in ("localhost", "127.0.0.1"):
        raise WooError("سایت درخواست را به آدرسی بدون https فرستاد؛ برای امن ماندن کلید API ارسال نشد. "
                       "در تنظیمات وردپرس آدرس سایت را با https بگذارید")


class Client:
    def __init__(self, cfg: dict) -> None:
        if not (cfg.get("url") and cfg.get("key") and cfg.get("secret")):
            raise WooError("اتصال به سایت تنظیم نشده است")
        self.base = cfg["url"] + "/wp-json/wc/v3"
        self.key, self.secret = cfg["key"], cfg["secret"]
        self.query_auth = cfg.get("auth") == "query"
        self.http = httpx.Client(timeout=40, transport=_transport, follow_redirects=True, event_hooks={"request": [_https_only]},
                                 headers={"User-Agent": "Hesabdar-Accounting/1.0", "Accept": "application/json"})

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> tuple[object, httpx.Headers]:
        p = dict(params or {})
        kw: dict = {}
        if self.query_auth:
            p.update(consumer_key=self.key, consumer_secret=self.secret)
        else:
            kw["auth"] = (self.key, self.secret)
        try:
            r = self.http.request(method, self.base + path, params=p, json=body, **kw)
        except httpx.HTTPError as exc:
            raise WooError(f"سایت در دسترس نیست ({type(exc).__name__})؛ اینترنت یا آدرس سایت را بررسی کنید") from exc
        if r.status_code == 401 and not self.query_auth:  # some hosts drop the Authorization header
            self.query_auth = True
            try:
                return self.request(method, path, params, body)
            except WooError:
                self.query_auth = False  # not the host: the key itself is wrong (it is not put in the address for nothing)
                raise
        if r.status_code >= 400:
            raise WooError(_err(r))
        try:
            return r.json(), r.headers
        except ValueError as exc:
            raise WooError("پاسخ سایت قابل خواندن نیست؛ آدرس سایت را بررسی کنید") from exc

    def get(self, path: str, **params) -> object:  # noqa: ANN003
        return self.request("GET", path, params)[0]

    def put(self, path: str, body: dict) -> object:
        return self.request("PUT", path, body=body)[0]

    def count(self, path: str) -> int:
        data, headers = self.request("GET", path, {"per_page": 1})
        try:
            return int(headers.get("x-wp-total"))
        except (TypeError, ValueError):
            return len(data) if isinstance(data, list) else 0

    def all(self, path: str, **params) -> list:  # noqa: ANN003
        """Every page of a list. A row taken out of the list while it is read (e.g. an order deleted) shifts the next
        pages back and a row could be skipped: the list is then read again from the first page (a row added only
        makes one row come twice). Afterwards self.read_at is the website's clock when the reading started (None if
        unknown) and self.stable is False if rows were still taken out on the last try."""
        self.read_at, self.stable = None, True
        for attempt in range(3):
            out, page, total, self.stable = [], 1, 0, True
            while True:
                data, headers = self.request("GET", path, {**params, "per_page": PER_PAGE, "page": page})
                if page == 1:
                    self.read_at = _server_time(headers)
                elif _total(headers) < total:
                    self.stable = False
                    if attempt < 2:
                        break
                total = max(total, _total(headers))
                out.extend(data or [])
                pages = int(headers.get("x-wp-totalpages") or 0)
                if not data or len(data) < PER_PAGE or (pages and page >= pages) or page >= 500:
                    return out
                page += 1
        return out


def _total(headers: httpx.Headers) -> int:
    try:
        return int(headers.get("x-wp-total") or 0)
    except ValueError:
        return 0


def _server_time(headers: httpx.Headers) -> datetime | None:
    """The website's clock (GMT) from the answer's Date header."""
    try:
        t = parsedate_to_datetime(headers.get("date"))
    except (TypeError, ValueError, IndexError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)


def factor(currency: str | None, cfg: dict | None = None) -> int:
    f = FACTORS.get((currency or "").upper()) or FACTORS.get((cfg or {}).get("unit") or "")
    if not f:
        raise WooError(f"واحد پول سایت ({currency or '؟'}) پشتیبانی نمی‌شود؛ ریال، تومان، هزار ریال یا هزار تومان باشد")
    return f


def rial(value: object, f: int) -> int:
    try:
        d = Decimal(str(value if value not in (None, "") else "0"))
    except InvalidOperation:
        return 0
    return int((d * f).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "")[:19])
    except ValueError:
        return None


def _when(x: dict, field: str) -> datetime | None:
    """A date of the website in this computer's (the clinic's) time. WooCommerce gives each date in the website's
    own time zone and in GMT; the website may be set to another zone (WordPress starts with UTC), so GMT is used."""
    gmt = _dt(x.get(f"{field}_gmt"))
    if gmt is None:
        return _dt(x.get(field))
    return gmt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def _paid_at(o: dict) -> datetime | None:
    """When the money of an order was received. Cash on delivery: when the order is completed (WooCommerce marks it
    paid as soon as it is placed, but the courier brings the money later)."""
    done = _when(o, "date_completed") if o.get("status") == "completed" else None
    if o.get("payment_method") == "cod":
        return done
    return _when(o, "date_paid") or done


def test_connection(db: Session) -> dict:
    cfg = config(db)
    c = Client(cfg)
    currency = cfg.get("unit") or ""
    try:
        general = c.get("/settings/general")
        currency = next((x.get("value") for x in general if x.get("id") == "woocommerce_currency"), currency)
    except WooError:
        pass  # a key without settings access: the currency comes from the orders
    products, orders = c.count("/products"), c.count("/orders")
    ok_currency = (currency or "").upper() in FACTORS
    stored = dict(settings_store.get(db, CFG_KEY) or {})  # a new object, so the change is saved
    stored.update(auth="query" if c.query_auth else "basic", unit=(currency or "").upper() if ok_currency else stored.get("unit", ""))
    settings_store.set_value(db, CFG_KEY, stored)
    st = state(db)
    st["store"] = {"currency": currency, "products": products, "orders": orders, "checked_at": local_now().isoformat(timespec="minutes")}
    _save_state(db, st)
    return {"ok": True, "currency": currency, "currency_label": CURRENCY_LABELS.get((currency or "").upper(), currency),
            "currency_ok": ok_currency, "products": products, "orders": orders}


# ------------------------------------------------------------------ products
def _site_product(db: Session, x: dict, f: int, parent: dict | None, seen: set[int]) -> WooProduct:
    wp = db.get(WooProduct, int(x["id"])) or WooProduct(id=int(x["id"]))
    if wp.id not in seen:
        db.add(wp)
    seen.add(wp.id)
    wp.parent_id = int(parent["id"]) if parent else None
    wp.sku = (x.get("sku") or "").strip()[:64]
    name = x.get("name") or ""
    if parent:
        attrs = "، ".join(str(a.get("option")) for a in x.get("attributes") or [] if a.get("option"))
        name = f"{parent.get('name')}{' - ' + attrs if attrs else ''}"
    wp.name = name[:200]
    wp.status = (x.get("status") or "")[:16]
    wp.price = rial(x["price"], f) if x.get("price") not in (None, "") else None
    wp.regular_price = rial(x["regular_price"], f) if x.get("regular_price") not in (None, "") else None
    wp.manage_stock = x.get("manage_stock") is True
    wp.stock_qty = int(x["stock_quantity"]) if x.get("stock_quantity") is not None else None
    wp.seen_at = local_now()
    return wp


def refresh_catalog(db: Session, c: Client, cfg: dict) -> dict:
    """Read the website's products (and variations), link them to the products here by SKU."""
    f = factor(cfg.get("unit"), cfg)
    seen: set[int] = set()
    for x in c.all("/products"):
        if x.get("type") == "variable":
            for v in c.all(f"/products/{x['id']}/variations"):
                _site_product(db, v, f, x, seen)
            if not x.get("sku"):
                continue
        _site_product(db, x, f, None, seen)
    db.flush()
    for wp in db.scalars(select(WooProduct).where(WooProduct.id.not_in(list(seen) or [-1]))):
        db.delete(wp)
    by_sku = {}
    for p in db.scalars(select(Product).where(Product.sku.is_not(None)).order_by(Product.is_active, Product.id)):
        by_sku[p.sku.strip().lower()] = p  # active products win (they come last)
    hand = {int(k): v for k, v in _links(db).items()}
    linked = prices = 0
    for wp in db.scalars(select(WooProduct)):
        p = by_sku.get(wp.sku.lower()) if wp.sku else None
        h = hand.get(wp.id, 0)  # chosen by hand: a product id, or None = unlinked by hand
        if h and db.get(Product, h) is not None:
            wp.product_id = h  # linked by hand (e.g. the SKU is written differently): kept
        elif h is None:
            wp.product_id = None
        elif p is not None:
            wp.product_id = p.id
        elif wp.sku:
            wp.product_id = None  # a SKU that is not here (any more)
        if wp.product_id:
            linked += 1
            p = db.get(Product, wp.product_id)
            if cfg.get("pull_prices", True) and wp.price and p.online_price != wp.price:
                p.online_price, p.last_online_price = wp.price, None  # the website's current price
                prices += 1
    st = state(db)
    st["catalog_at"] = local_now().isoformat(timespec="seconds")
    _save_state(db, st)
    db.flush()
    return {"site_products": len(seen), "linked": linked, "prices_updated": prices}


def link_product(db: Session, wp: WooProduct, p: Product | None) -> None:
    """Link a website product to a product here by hand (or unlink it); kept when the website is read again."""
    wp.product_id = p.id if p else None
    links = _links(db)  # a new dict, so the change is saved
    links[str(wp.id)] = p.id if p is not None else None  # None: not linked again by its SKU
    settings_store.set_value(db, LINKS_KEY, links)


def _links(db: Session) -> dict:
    """Website products linked by hand: {website id: product id, or None = unlinked by hand}. Kept apart from the
    sync state (which the sync job writes all the time), so a link saved during a sync is not overwritten."""
    return dict(settings_store.get(db, LINKS_KEY) or {})


def _path(wp: WooProduct) -> str:
    return f"/products/{wp.parent_id}/variations/{wp.id}" if wp.parent_id else f"/products/{wp.id}"


def _site_of(db: Session, product_id: int) -> WooProduct | None:
    return db.scalar(select(WooProduct).where(WooProduct.product_id == product_id).order_by(WooProduct.parent_id.is_not(None), WooProduct.id))


def _sent(site_product: dict) -> tuple[int, int] | None:
    """The queued changes ("first-last" ids) a website product has already got (written with each change)."""
    for m in site_product.get("meta_data") or []:
        if m.get("key") == SENT_META:
            try:
                first, last = (int(x) for x in str(m.get("value")).split("-"))
            except ValueError:
                return None
            return first, last
    return None


def push_stock(db: Session, c: Client, cfg: dict) -> dict:
    """Send the queued stock changes. A stock count (set) makes the website's quantity equal to the stock here;
    other changes add / take the same quantity on the website. Each change reaches the website once: the website's
    product keeps which changes it got, so a change whose answer was lost (timeout) is not added again."""
    out = defaultdict(int)
    pids = list(dict.fromkeys(db.scalars(select(WooOutbox.product_id).where(WooOutbox.status == "pending").order_by(WooOutbox.id))))
    refreshed = False
    for pid in pids:
        # the stock here and the changes waiting for the website, read in ONE statement: a sale saved meanwhile is in
        # both or in neither (a stock count sends this stock, so the changes sent with it must be exactly those in it)
        waiting = select(func.max(WooOutbox.id)).where(WooOutbox.product_id == pid, WooOutbox.status == "pending").scalar_subquery()
        row = db.execute(select(Product.stock_qty, waiting).where(Product.id == pid)).one_or_none()
        stock, last = row if row else (0, None)
        entries = list(db.scalars(select(WooOutbox).where(WooOutbox.product_id == pid, WooOutbox.status == "pending",
                                                          WooOutbox.id <= (last or 0)).order_by(WooOutbox.id)))
        if not entries:
            continue
        wp = _site_of(db, pid) if row else None
        if wp is None and row and not refreshed:
            refresh_catalog(db, c, cfg)
            refreshed = True
            wp = _site_of(db, pid)
        now = local_now()
        if wp is None:
            for e in entries:
                e.status, e.error, e.done_at = "skipped", "کالایی با این SKU روی سایت نیست", now
            out["skipped"] += len(entries)
            db.commit()
            continue
        try:
            current = c.get(_path(wp))
            sent = _sent(current)
            got = [e for e in entries if sent and sent[0] <= e.id <= sent[1]]
            for e in got:  # sent last time; only the website's answer was lost
                e.status, e.error, e.done_at = "done", "", now
            out["sent"] += len(got)
            entries = [e for e in entries if e not in got]
            before = current.get("stock_quantity")
            if not entries:
                db.commit()
                continue
            if any(e.kind == "set" for e in entries):
                target = max(0, int(stock or 0))
                body = {"manage_stock": True, "stock_quantity": target}
            elif current.get("manage_stock") is not True:
                for e in entries:
                    e.status, e.error, e.done_at = "skipped", "مدیریت موجودی این کالا در سایت خاموش است", now
                out["skipped"] += len(entries)
                db.commit()
                continue
            else:
                target = max(0, int(before or 0) + sum(e.qty for e in entries))
                body = {"stock_quantity": target}
            for e in entries:
                e.result = f"{before if before is not None else '—'} → {target}"
            c.put(_path(wp), {**body, "meta_data": [{"key": SENT_META, "value": f"{entries[0].id}-{entries[-1].id}"}]})
            wp.stock_qty, wp.manage_stock = target, True
            for e in entries:
                e.status, e.error, e.done_at = "done", "", now
            out["sent"] += len(entries)
        except WooError as exc:
            for e in entries:
                e.attempts += 1
                e.error = str(exc)[:256]
                if e.attempts >= MAX_ATTEMPTS:
                    e.status = "error"
            out["failed"] += len(entries)
            db.commit()
            if "در دسترس" in str(exc):  # the website is down: the rest waits for the next run
                raise
        db.commit()
    return dict(out)


def align_stock(db: Session, product_ids: list[int]) -> int:
    """Make the website's stock equal to the stock here for these products (sent on the next sync)."""
    n = 0
    for pid in product_ids:
        if _site_of(db, pid) is not None:
            db.add(WooOutbox(product_id=pid, kind="set", reason="هم‌سان‌سازی موجودی سایت با سیستم"))
            n += 1
    db.flush()
    return n


# ------------------------------------------------------------------ orders
def _account(db: Session, cfg: dict, method: str) -> PaymentAccount:
    aid = (cfg.get("method_accounts") or {}).get(method) or cfg.get("account_id") or routing.default_account(db, "products", prefer="card")
    pa = db.get(PaymentAccount, int(aid)) if aid else db.scalar(select(PaymentAccount).where(PaymentAccount.kind == "gateway",
                                                                                            PaymentAccount.is_active.is_(True)))
    if pa is None:
        raise WooError("حساب دریافت سفارش‌های سایت (درگاه پرداخت) را در تنظیمات انتخاب کنید")
    return pa


def _customer(db: Session, o: dict) -> Customer:
    """Found by mobile (or the website user); a namesake without a mobile gets this mobile; otherwise a new customer."""
    b, s = o.get("billing") or {}, o.get("shipping") or {}
    phone = normalize_mobile(b.get("phone")) or normalize_mobile(s.get("phone"))
    name = " ".join(x for x in ((b.get("first_name") or "").strip(), (b.get("last_name") or "").strip()) if x) or \
        " ".join(x for x in ((s.get("first_name") or "").strip(), (s.get("last_name") or "").strip()) if x)
    wid = int(o.get("customer_id") or 0) or None
    c = accounting.find_customer(db, phone) if phone else None
    if c is None and wid:
        c = db.scalar(select(Customer).where(Customer.woo_id == wid))
    if c is None and name:
        same = [x for x in db.scalars(select(Customer).where(Customer.mobile.is_(None))) if person_key(x.full_name) == person_key(name)]
        if len(same) == 1:
            c = same[0]
    if c is None:
        c = Customer(full_name=name or (f"مشتری سایت {phone}" if phone else "مشتری سایت"), mobile=phone, source=SOURCE,
                     legacy_code=accounting.next_customer_code(db), mobile_issue=None if phone else "missing",
                     notes=f"ایمیل: {b['email']}" if b.get("email") else "")
        db.add(c)
        db.flush()
        note(db, f"مشتری جدید از سایت: {c.full_name}{' - ' + phone if phone else ''}")
    elif phone and not c.mobile:
        c.mobile, c.mobile_issue = phone, None
    if wid and not c.woo_id:
        c.woo_id = wid
    return c


def _product(db: Session, li: dict, unit: int) -> Product:
    sku = (li.get("sku") or "").strip()
    vid = int(li.get("variation_id") or 0) or int(li.get("product_id") or 0)
    wp = db.get(WooProduct, vid) if vid else None
    hand = _links(db).get(str(vid)) if vid else None
    p = db.get(Product, hand) if hand else None  # linked by hand: wins over the SKU
    if p is None and sku:
        p = db.scalar(select(Product).where(func.lower(Product.sku) == sku.lower()).order_by(Product.is_active.desc(), Product.id))
    if p is None and wp is not None and wp.product_id:
        p = db.get(Product, wp.product_id)
    if p is None:
        p = Product(code=inventory.next_product_code(db), name=(li.get("name") or "کالای سایت")[:160], sku=sku or None,
                    sale_price=unit, online_price=unit, notes="ساخته‌شده خودکار از سفارش سایت")
        db.add(p)
        db.flush()
        note(db, f"کالای «{p.name}»{' (SKU ' + sku + ')' if sku else ''} در سیستم نبود و با کد {p.code} ساخته شد؛ خرید یا موجودی آن را ثبت کنید", "warn")
    if vid and (wp is None or not wp.product_id):
        wp = wp or WooProduct(id=vid, parent_id=int(li["product_id"]) if li.get("variation_id") else None, sku=sku, name=(li.get("name") or "")[:200])
        wp.product_id = p.id
        db.add(wp)
        db.flush()  # the same product on another line of the order finds this link (not a second one)
    return p


def _refunded_item(li: dict) -> int | None:
    for m in li.get("meta_data") or []:
        if m.get("key") == "_refunded_item_id":
            try:
                return int(m.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def plan(o: dict, refunds: list[dict], f: int) -> dict:
    """What the invoice of an order must be: products (after coupons and refunds), shipping, fees and tax,
    with an invoice total exactly equal to the money the website kept."""
    by_line_qty: dict[int, int] = defaultdict(int)
    by_line_amt: dict[int, int] = defaultdict(int)
    by_product_qty: dict[int, int] = defaultdict(int)
    by_product_amt: dict[int, int] = defaultdict(int)
    for r in refunds:
        for li in r.get("line_items") or []:
            rid = _refunded_item(li)
            q, a = abs(int(li.get("quantity") or 0)), abs(rial(li.get("total"), f))
            if rid:
                by_line_qty[rid] += q
                by_line_amt[rid] += a
            else:
                k = int(li.get("variation_id") or 0) or int(li.get("product_id") or 0)
                by_product_qty[k] += q
                by_product_amt[k] += a
    lines, extras, discount = [], [], 0
    for li in o.get("line_items") or []:
        k = int(li.get("variation_id") or 0) or int(li.get("product_id") or 0)
        rq = by_line_qty.pop(int(li["id"]), 0) + by_product_qty.pop(k, 0)
        ra = by_line_amt.pop(int(li["id"]), 0) + by_product_amt.pop(k, 0)
        qty0 = int(li.get("quantity") or 0)
        gross0, net0 = rial(li.get("subtotal"), f), rial(li.get("total"), f)
        qty, net = qty0 - rq, net0 - ra
        gross = gross0 - (gross0 * rq // qty0 if qty0 else 0)
        if qty <= 0:
            if net > 0:
                extras.append({"description": f"ماندهٔ {li.get('name')}", "quantity": 1, "unit_price": net})
            continue
        gross = max(gross, net, 0)
        unit = -(-gross // qty)
        lines.append({"li": li, "quantity": qty, "unit_price": unit, "discount": unit * qty - max(net, 0), "net": max(net, 0)})
    shipping = rial(o.get("shipping_total"), f)
    if shipping > 0:
        extras.append({"description": "هزینهٔ ارسال سفارش سایت", "quantity": 1, "unit_price": shipping})
    for fl in o.get("fee_lines") or []:
        a = rial(fl.get("total"), f)
        if a > 0:
            extras.append({"description": f"{fl.get('name') or 'هزینهٔ اضافه'} (سایت)", "quantity": 1, "unit_price": a})
        elif a < 0:
            discount += -a
    tax = rial(o.get("total_tax"), f)
    if tax > 0:
        extras.append({"description": "مالیات و عوارض سفارش سایت", "quantity": 1, "unit_price": tax})
    refunded = sum(abs(rial(r.get("total") if "total" in r else r.get("amount"), f)) for r in (o.get("refunds") or refunds))
    target = max(0, rial(o.get("total"), f) - refunded)
    ours = sum(x["net"] for x in lines) + sum(x["unit_price"] for x in extras) - discount
    if target > ours:  # rounding / refunded tax or shipping: the invoice must equal what the website kept
        extras.append({"description": "تفاوت گرد کردن سفارش سایت", "quantity": 1, "unit_price": target - ours})
    elif target < ours:
        discount += ours - target
    sub = sum(x["net"] for x in lines) + sum(x["unit_price"] for x in extras)
    if discount > sub:
        discount = sub
    key = [[x["li"].get("sku"), x["li"].get("product_id"), x["li"].get("variation_id"), x["quantity"], x["unit_price"], x["discount"]] for x in lines]
    key += [[x["description"], x["unit_price"]] for x in extras]
    b = o.get("billing") or {}
    fp = hashlib.sha1(json.dumps([key, discount, target, sorted(int(r["id"]) for r in (o.get("refunds") or [])), b.get("phone"),
                                  b.get("first_name"), b.get("last_name")], ensure_ascii=False).encode()).hexdigest()
    return {"lines": lines, "extras": extras, "discount": discount, "target": target, "refunded": refunded, "fp": fp}


def _give_back(db: Session, wo: WooOrder, customer: Customer, pa: PaymentAccount, refunds: list[dict], received_at: datetime,
               num: object, f: int, cap: int | None = None) -> int:
    """Money given back on the website: received with the order, paid back on the refund's own day, on the account
    it came in on; each refund once (wo.refund_ids). cap: at most this much in all. Returns the amount given back."""
    now = local_now()
    done, total = set(wo.refund_ids or []), 0
    for r in sorted(refunds, key=lambda r: int(r["id"])):
        if int(r["id"]) in done:
            continue
        done.add(int(r["id"]))
        amount = abs(rial(r.get("amount") if r.get("amount") is not None else r.get("total"), f))
        if cap is not None:
            amount = min(amount, cap - total)
        if amount <= 0:
            continue
        dep = accounting.record_deposit(db, customer=customer, amount=amount, payment_account=pa, received_at=received_at,
                                        source=SOURCE, notes=f"مبلغ برگشتی سفارش سایت #{num}")
        accounting.close_deposit(db, dep, "refund", pa, at=min(max(_when(r, "date_created") or now, received_at), now))
        total += amount
    wo.refund_ids = sorted(done)
    return total


def _book(db: Session, wo: WooOrder, o: dict, pl: dict, refunds: list[dict], f: int, cfg: dict,
          credits: list[int] | None = None, pa: PaymentAccount | None = None) -> Invoice | None:
    """credits: the customer's credits (money received here for the order's earlier invoice) that pay it first;
    pa: the account the website's money came in on before (booked again: the same account)."""
    now = local_now()
    created = min(_when(o, "date_created") or now, now)
    paid_at = _paid_at(o)
    paid_at = min(paid_at, now) if paid_at else None
    customer = _customer(db, o)
    method = o.get("payment_method_title") or o.get("payment_method") or ""
    items = []
    for x in pl["lines"]:
        p = _product(db, x["li"], x["unit_price"])
        items.append({"product_id": p.id, "quantity": x["quantity"], "unit_price": x["unit_price"], "discount": x["discount"],
                      "description": (x["li"].get("name") or p.name)[:256]})
    items += [{k: v for k, v in x.items()} for x in pl["extras"]]
    wo.customer_id = customer.id
    if not items:
        wo.state, wo.invoice_id = "skipped", None
        return None
    num = o.get("number") or o["id"]
    # only the credits named here (never the customer's service deposits: a website order is a product sale)
    inv = accounting.issue_invoice(db, customer=customer, items=items, discount=pl["discount"], issued_at=created, source=SOURCE,
                                   channel="online", notes=f"سفارش سایت #{num}{' - ' + method if method else ''}",
                                   apply_deposit_ids=credits or None)
    if paid_at and (inv.total > inv.paid or refunds):
        pa = pa or _account(db, cfg, o.get("payment_method") or "")
        if inv.total > inv.paid:
            accounting.record_payment(db, payment_account=pa, amount=inv.total - inv.paid, invoice=inv, reference=f"سفارش سایت #{num}",
                                      paid_at=max(paid_at, created), source=SOURCE)
        # money given back on the website: received with the order, paid back on the refund's day
        _give_back(db, wo, customer, pa, refunds, max(paid_at, created), num, f)
    elif not paid_at:
        wo.refund_ids = sorted(set(wo.refund_ids or []) | {int(r["id"]) for r in refunds})
    wo.invoice_id, wo.state, wo.paid, wo.fp = inv.id, "booked", bool(paid_at), pl["fp"]
    return inv


def _undo(db: Session, inv: Invoice, reason: str) -> tuple[list[Payment], list[int]]:
    """Void the invoice of an order on its own date (the sale as it was booked never happened). Returns the website's
    payments that were on it (their money is booked again by the caller) and the customer credits made of money
    received here at the clinic for it (e.g. «دریافت بدهی»): that money is real, it stays the customer's."""
    pays = list(db.scalars(select(Payment).where(Payment.invoice_id == inv.id, Payment.amount > 0)))
    accounting.void_invoice(db, inv, reason=reason, payments="cancel")
    customer, kept = db.get(Customer, inv.customer_id), []
    for p in pays:
        if p.source != SOURCE:
            dep = accounting.record_deposit(db, customer=customer, amount=p.amount, payment_account=db.get(PaymentAccount, p.payment_account_id),
                                            received_at=p.paid_at, reference=p.reference, kind="credit",
                                            notes=f"وجه دریافتی در کلینیک برای فاکتور {inv.number} (سفارش سایت)")
            kept.append(dep.id)
    return [p for p in pays if p.source == SOURCE], kept


def sync_order(db: Session, c: Client, cfg: dict, o: dict) -> str:
    """Bring one website order into the books (idempotent: an unchanged order changes nothing)."""
    start = date.fromisoformat(cfg["start"])
    oid = int(o["id"])
    wo = db.scalar(select(WooOrder).where(WooOrder.order_id == oid))
    created = _when(o, "date_created")
    if wo is None:
        if created is None or created.date() < start:
            return "before_start"
        wo = WooOrder(order_id=oid, refund_ids=[])
        db.add(wo)
    f = factor(o.get("currency"), cfg)
    st = o.get("status") or ""
    num = o.get("number") or str(oid)
    wo.number, wo.status, wo.created, wo.modified = str(num)[:32], st[:24], created, _when(o, "date_modified")
    wo.total, wo.method, wo.synced_at = rial(o.get("total"), f), (o.get("payment_method_title") or "")[:64], local_now()
    inv = db.get(Invoice, wo.invoice_id) if wo.invoice_id else None
    if wo.state == "local_void" or (inv is not None and inv.status == "void" and wo.state == "booked"):
        if wo.state != "local_void":
            note(db, f"فاکتور سفارش سایت #{num} در سیستم باطل شده؛ تغییرات بعدی این سفارش ثبت نمی‌شود", "warn")
        wo.state = "local_void"
        return "local_void"
    # every refund (the website gives them 10 to a page)
    refunds = c.all(f"/orders/{oid}/refunds") if o.get("refunds") else []
    pl = plan(o, refunds, f) if st in ACTIVE else None
    wo.refunded = pl["refunded"] if pl else sum(abs(rial(r.get("total"), f)) for r in o.get("refunds") or [])
    if st in ACTIVE and pl["target"] == 0 and not pl["lines"]:
        st = "refunded"  # everything was given back
    if st in ACTIVE:
        if inv is None or inv.status == "void":
            _book(db, wo, o, pl, refunds, f, cfg)
            return "booked"
        if pl["fp"] != wo.fp:  # changed on the website (items, partial refund): booked again as it is now
            web, kept = _undo(db, inv, f"سفارش سایت #{num} در سایت تغییر کرد")
            _book(db, wo, o, pl, refunds, f, cfg, credits=kept, pa=db.get(PaymentAccount, web[0].payment_account_id) if web else None)
            note(db, f"سفارش سایت #{num} تغییر کرده بود و دوباره ثبت شد")
            return "rebooked"
        paid_at = _paid_at(o)
        if paid_at and not wo.paid and inv.total > inv.paid:
            accounting.record_payment(db, payment_account=_account(db, cfg, o.get("payment_method") or ""), amount=inv.total - inv.paid,
                                      invoice=inv, reference=f"سفارش سایت #{num}", paid_at=min(max(paid_at, inv.issued_at), local_now()),
                                      source=SOURCE)
            wo.paid = True
            return "paid"
        return "unchanged"
    # cancelled, refunded... or put back to "pending payment" (the website puts its goods back in stock then too)
    if st in DEAD or (st == "pending" and inv is not None and inv.status != "void"):
        if inv is not None and inv.status != "void":
            # the sale is undone on its own date; the website's money was received on its day and goes back on the
            # day of each refund of the website (what no refund covers - cancelled without a refund there: today)
            web, kept = _undo(db, inv, f"سفارش سایت #{num} در سایت {STATUS_FA.get(st, st)} شد")
            paid = sum(p.amount for p in web)
            if paid:
                customer, pa, at = db.get(Customer, inv.customer_id), db.get(PaymentAccount, web[0].payment_account_id), min(p.paid_at for p in web)
                rest = paid - _give_back(db, wo, customer, pa, refunds, at, num, f, cap=paid)
                if rest > 0:
                    dep = accounting.record_deposit(db, customer=customer, amount=rest, payment_account=pa, received_at=at, source=SOURCE,
                                                    notes=f"مبلغ برگشتی سفارش سایت #{num} ({STATUS_FA.get(st, st)})")
                    accounting.close_deposit(db, dep, "refund", pa)
            else:
                wo.refund_ids = sorted(set(wo.refund_ids or []) | {int(r["id"]) for r in refunds})
            wo.state = "waiting" if st == "pending" else "cancelled"
            note(db, f"سفارش سایت #{num} {STATUS_FA.get(st, st)} شد؛ فاکتورش باطل{' و مبلغ آن برگشت داده' if paid else ''} شد"
                     + ("؛ پولی که در کلینیک برای آن گرفته شده بود اعتبار مشتری ماند (اگر پس داده‌اید، از صفحهٔ بیعانه‌ها استرداد بزنید)" if kept else ""),
                 "warn" if kept else "info")
            return "cancelled"
        if wo.state in ("", "waiting", "error"):
            wo.state = "skipped"
        return "skipped"
    if inv is None:
        wo.state = "waiting"  # pending payment / draft: nothing is sold yet
    return "waiting"


STATUS_FA = {"pending": "در انتظار پرداخت", "processing": "در حال انجام", "on-hold": "در انتظار بررسی", "completed": "تکمیل‌شده",
             "cancelled": "لغو", "refunded": "مسترد", "failed": "ناموفق", "trash": "حذف", "checkout-draft": "پیش‌نویس"}


def import_orders(db: Session, c: Client, cfg: dict) -> dict:
    if not cfg.get("start"):
        raise WooError("تاریخ شروع ثبت سفارش‌های سایت را در تنظیمات مشخص کنید")
    st = state(db)
    since = _dt(st.get("cursor")) or (datetime.fromisoformat(cfg["start"]) - timedelta(days=1))
    # in the order of their ids: an order changed while the pages are read keeps its place (in the order of change
    # it would jump to the end and push another order into a page already read; orders changed in the same second
    # have no fixed order either)
    params = {"orderby": "id", "order": "asc", "dates_are_gmt": "true",
              "modified_after": (since - timedelta(minutes=10)).isoformat(timespec="seconds")}
    orders = list({int(o["id"]): o for o in c.all("/orders", **params)}.values())  # a row read twice: once
    changed = [m for m in (_dt(o.get("date_modified_gmt")) or _dt(o.get("date_modified")) for o in orders) if m]
    # the next run reads again from here: the newest change read, but not after the website's clock when the reading
    # began (an order changed meanwhile may have been read before the change), and not on if the list kept changing
    cursor = max(changed, default=since)
    if c.read_at:
        cursor = min(cursor, c.read_at)
    if not c.stable:
        cursor = since
    out: dict[str, int] = defaultdict(int)
    methods = dict(st.get("methods") or {})
    for o in orders:
        if o.get("payment_method"):
            methods[o["payment_method"]] = o.get("payment_method_title") or o["payment_method"]
        try:
            with db.begin_nested():
                result = sync_order(db, c, cfg, o)
            db.commit()
            out[result] += 1
            wo = db.scalar(select(WooOrder).where(WooOrder.order_id == int(o["id"])))
            if wo is not None and wo.note:
                wo.note = ""
        except Exception as exc:  # noqa: BLE001 - one order that can't be booked must not stop the others
            db.rollback()
            out["error"] += 1
            if isinstance(exc, (WooError, accounting.AccountingError)):
                why = str(exc)
            else:
                log.exception("woo: order %s", o.get("id"))
                why = f"اطلاعات این سفارش در سایت ناقص یا غیرعادی است؛ سفارش را در سایت باز و بررسی کنید ({type(exc).__name__}: {exc})"
            wo = db.scalar(select(WooOrder).where(WooOrder.order_id == int(o["id"])))
            msg = f"سفارش سایت #{o.get('number') or o['id']} ثبت نشد: {why}"
            if wo is None:
                wo = WooOrder(order_id=int(o["id"]), number=str(o.get("number") or o["id"])[:32], refund_ids=[])
                db.add(wo)
            if wo.note != why[:256]:
                note(db, msg, "error")
            wo.state = wo.state if wo.invoice_id else "error"
            wo.status, wo.note = str(o.get("status") or "")[:24], why[:256]
            db.commit()
            if isinstance(exc, WooError) and "در دسترس" in str(exc):
                raise
            # read again next time (from before its change)
            cursor = min(cursor, _dt(o.get("date_modified_gmt")) or _dt(o.get("date_modified")) or since)
    cursor = max(cursor, since)
    st = state(db)
    st["cursor"], st["methods"] = cursor.isoformat(timespec="seconds"), methods
    _save_state(db, st)
    db.commit()
    return dict(out)


# ------------------------------------------------------------------ the job
def run_sync(db: Session, force_catalog: bool = False) -> dict:
    """Read the website (products, orders) and send the stock changes. One run at a time."""
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    try:
        cfg = config(db)
        if not (cfg.get("url") and cfg.get("key") and cfg.get("secret")):
            raise WooError("اتصال به سایت تنظیم نشده است")
        c = Client(cfg)
        out: dict = {}
        st = state(db)
        try:
            if (cfg.get("unit") or "") not in FACTORS:  # the store's currency isn't known yet: ask the website
                test_connection(db)
                cfg = config(db)
            catalog_at = _dt(st.get("catalog_at"))
            if force_catalog or catalog_at is None or local_now() - catalog_at > CATALOG_EVERY:
                out["catalog"] = refresh_catalog(db, c, cfg)
                db.commit()
            if cfg.get("enabled"):
                out["orders"] = import_orders(db, c, cfg)
                out["stock"] = push_stock(db, c, cfg)
            error = ""
        except WooError as exc:
            db.rollback()
            error = str(exc)
            note(db, f"هماهنگی با سایت انجام نشد: {exc}", "error")
        st = state(db)
        st["last_run"] = local_now().isoformat(timespec="seconds")
        st["last_error"] = error
        if not error:
            st["last_ok"] = st["last_run"]
        if c.query_auth and cfg.get("auth") != "query":
            stored = settings_store.get(db, CFG_KEY) or {}
            settings_store.set_value(db, CFG_KEY, {**stored, "auth": "query"})
        _save_state(db, st)
        db.commit()
        out["error"] = error
        return out
    finally:
        _lock.release()


def job() -> None:
    """Every minute: sync when it is due (the interval is chosen in Settings)."""
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        cfg = config(db)
        if not cfg.get("enabled"):
            return
        last = _dt(state(db).get("last_run"))
        if last and local_now() - last < timedelta(minutes=int(cfg.get("interval") or 5)):
            return
        try:
            run_sync(db)
        except WooError as exc:
            log.info("woo sync skipped: %s", exc)
