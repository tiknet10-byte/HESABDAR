"""A small in-memory WooCommerce store (REST API v3 subset) for the tests and for trying the screens.

It behaves like WooCommerce where it matters here: orders take goods out of the stock (processing / on-hold /
completed), cancelling puts them back, refunds can restock, money is strings in the store currency, dates come
as site-local (the website's own time zone) and GMT, lists are paged with X-WP-Total / X-WP-TotalPages,
`modified_after` filters orders, `orderby` / `order` sort them, products keep `meta_data`, answers carry the
website's clock (Date header).
"""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import parse_qs

import httpx

GMT = timedelta(hours=3, minutes=30)  # the website's own time zone (Tehran) by default
TAKES_STOCK = {"processing", "on-hold", "completed"}


def _utc(local: datetime) -> datetime:
    """A time of this computer (naive local) in GMT."""
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _s(v: float | int) -> str:
    return f"{v:.2f}"


class FakeWoo:
    stores = 0  # each store gets its own ids (like different websites)

    def __init__(self, currency: str = "IRT", key: str = "ck_test_1234567890", secret: str = "cs_test_1234567890",
                 site_offset: timedelta = GMT, header_auth: bool = True) -> None:
        self.currency, self.key, self.secret = currency, key, secret
        self.offset = site_offset  # the website's time zone (GMT + offset)
        self.header_auth = header_auth  # False: the host drops the Authorization header (key in the query only)
        self.products: dict[int, dict] = {}
        self.orders: dict[int, dict] = {}
        self.refunds: dict[int, list[dict]] = {}
        FakeWoo.stores += 1
        self.ids = 100_000 * FakeWoo.stores
        self.down = False
        self.lose_put = 0  # the next N stock changes are saved, but their answer never arrives (timeout)
        self.on_orders_page: Callable[[int], None] | None = None  # called before a page of orders is served
        self.on_put: Callable[[int], None] | None = None  # called (with the product id) before a product is changed
        self.clock_shift = timedelta(0)  # the website's clock in its answers (Date header) = now + this
        self.redirect_http = False  # a badly set website: every https request is sent on to plain http
        self.insecure = 0  # requests that reached the website over plain http
        self.calls: list[tuple[str, str]] = []

    def _id(self) -> int:
        self.ids += 1
        return self.ids

    @staticmethod
    def now() -> datetime:
        return datetime.now().replace(microsecond=0)

    # ---------------------------------------------------------------- data
    def add_product(self, name: str, sku: str, price: int, stock: int | None = None, manage: bool = True) -> int:
        pid = self._id()
        self.products[pid] = {"id": pid, "name": name, "sku": sku, "type": "simple", "status": "publish", "price": str(price),
                              "regular_price": str(price), "manage_stock": manage, "stock_quantity": stock if manage else None,
                              "parent_id": 0, "attributes": [], "meta_data": []}
        return pid

    def add_variable(self, name: str, variations: list[tuple[str, str, int, int]]) -> tuple[int, list[int]]:
        pid = self._id()
        self.products[pid] = {"id": pid, "name": name, "sku": "", "type": "variable", "status": "publish", "price": "",
                              "regular_price": "", "manage_stock": False, "stock_quantity": None, "parent_id": 0, "attributes": []}
        vids = []
        for option, sku, price, stock in variations:
            vid = self._id()
            self.products[vid] = {"id": vid, "name": name, "sku": sku, "type": "variation", "status": "publish", "price": str(price),
                                  "regular_price": str(price), "manage_stock": True, "stock_quantity": stock, "parent_id": pid,
                                  "attributes": [{"name": "حجم", "option": option}], "meta_data": []}
            vids.append(vid)
        return pid, vids

    def _stock(self, pid: int, delta: int) -> None:
        p = self.products[pid]
        if p["manage_stock"]:
            p["stock_quantity"] = (p["stock_quantity"] or 0) + delta

    def _date(self, d: dict, field: str, local: datetime | None) -> None:
        """A date as WooCommerce gives it: in the website's time zone, and in GMT (local = this computer's time)."""
        if local is None:
            d[field] = d[f"{field}_gmt"] = None
            return
        g = _utc(local)
        d[field], d[f"{field}_gmt"] = (g + self.offset).isoformat(), g.isoformat()

    def _touch(self, o: dict, at: datetime | None = None) -> None:
        self._date(o, "date_modified", at or self.now())

    def add_order(self, items: list[tuple[int, int]], *, status: str = "processing", paid: bool = True, phone: str = "",
                  first: str = "", last: str = "", shipping: int = 0, coupon: int = 0, method: str = "zarinpal",
                  method_title: str = "درگاه زرین‌پال", created: datetime | None = None, customer_id: int = 0) -> dict:
        """items: [(product or variation id, qty)] at the product's price; coupon: taken off the first line.
        created: this computer's local time (the website shows it in its own time zone)."""
        oid = self._id()
        created = created or self.now() - timedelta(minutes=30)
        lines, total = [], 0
        for k, (pid, qty) in enumerate(items):
            p = self.products[pid]
            sub = int(p["price"]) * qty
            line_total = sub - (coupon if k == 0 else 0)
            lines.append({"id": self._id(), "name": p["name"], "product_id": p["parent_id"] or pid, "variation_id": pid if p["parent_id"] else 0,
                          "sku": p["sku"], "quantity": qty, "price": int(p["price"]), "subtotal": _s(sub), "total": _s(line_total), "total_tax": "0.00"})
            total += line_total
        total += shipping
        o = {"id": oid, "number": str(oid), "status": status, "currency": self.currency, "customer_id": customer_id,
             "payment_method": method, "payment_method_title": method_title,
             "billing": {"first_name": first, "last_name": last, "phone": phone, "email": f"{oid}@example.com"},
             "shipping": {"first_name": first, "last_name": last, "phone": ""},
             "line_items": lines, "shipping_total": _s(shipping), "fee_lines": [], "discount_total": _s(coupon), "total_tax": "0.00",
             "total": _s(total), "refunds": []}
        self._date(o, "date_created", created)
        self._date(o, "date_paid", created + timedelta(minutes=2) if paid else None)
        self._date(o, "date_completed", None)
        self._touch(o)
        self.orders[oid] = o
        self.refunds[oid] = []
        if status in TAKES_STOCK:
            for li in lines:
                self._stock(li["variation_id"] or li["product_id"], -li["quantity"])
        return o

    def set_status(self, oid: int, status: str, paid: bool | None = None) -> None:
        o = self.orders[oid]
        before, o["status"] = o["status"], status
        if (before in TAKES_STOCK) != (status in TAKES_STOCK):
            for li in o["line_items"]:
                self._stock(li["variation_id"] or li["product_id"], li["quantity"] if before in TAKES_STOCK else -li["quantity"])
        if paid and not o["date_paid"]:
            self._date(o, "date_paid", self.now())
        if status == "completed":
            self._date(o, "date_completed", self.now())
        self._touch(o)

    def refund(self, oid: int, amount: int, items: list[tuple[int, int, int]] | None = None, restock: bool = True,
               at: datetime | None = None) -> None:
        """items: [(order line id, qty, amount)]; at: when it was given back (this computer's local time)"""
        o = self.orders[oid]
        rid = self._id()
        lines = []
        for line_id, qty, amt in items or []:
            li = next(x for x in o["line_items"] if x["id"] == line_id)
            lines.append({"id": self._id(), "product_id": li["product_id"], "variation_id": li["variation_id"], "quantity": -qty,
                          "total": _s(-amt), "subtotal": _s(-amt), "meta_data": [{"key": "_refunded_item_id", "value": str(line_id)}]})
            if restock:
                self._stock(li["variation_id"] or li["product_id"], qty)
        r = {"id": rid, "amount": _s(amount), "reason": "", "line_items": lines}
        self._date(r, "date_created", at or self.now())
        self.refunds[oid].append(r)
        o["refunds"].append({"id": rid, "reason": "", "total": _s(-amount)})
        refunded = sum(float(r["amount"]) for r in self.refunds[oid])
        if refunded >= float(o["total"]):
            o["status"] = "refunded"
        self._touch(o)

    def trash(self, oid: int) -> None:
        """Moved to the trash in the website's admin: the order keeps its status underneath, its goods stay out of the
        stock, and the order list (status 'any') no longer shows it."""
        o = self.orders[oid]
        o["status"] = "trash"

    def delete(self, oid: int) -> None:
        """Deleted for good: the website no longer knows the order."""
        self.orders.pop(oid)

    # ---------------------------------------------------------------- HTTP
    def _authorized(self, request: httpx.Request, q: dict) -> bool:
        if q.get("consumer_key") == self.key and q.get("consumer_secret") == self.secret:
            return True
        auth = request.headers.get("authorization", "")
        if auth.startswith("Basic ") and self.header_auth:
            return base64.b64decode(auth[6:]).decode() == f"{self.key}:{self.secret}"
        return False

    def _page(self, items: list, q: dict) -> httpx.Response:
        per, page = int(q.get("per_page", 10)), int(q.get("page", 1))
        chunk = items[(page - 1) * per: page * per]
        pages = max(1, -(-len(items) // per))
        clock = datetime.now(timezone.utc) + self.clock_shift
        return httpx.Response(200, json=chunk, headers={"X-WP-Total": str(len(items)), "X-WP-TotalPages": str(pages),
                                                        "Date": format_datetime(clock, usegmt=True)})

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down", request=request)
        if request.url.scheme == "http":
            self.insecure += 1
        elif self.redirect_http:
            return httpx.Response(301, headers={"Location": str(request.url.copy_with(scheme="http"))})
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        path = request.url.path.split("/wp-json/wc/v3", 1)[-1]
        self.calls.append((request.method, path))
        if not self._authorized(request, q):
            return httpx.Response(401, json={"code": "woocommerce_rest_cannot_view", "message": "Sorry, you cannot list resources."})
        body = json.loads(request.content or b"{}") if request.method == "PUT" else {}
        if path == "/settings/general":
            return httpx.Response(200, json=[{"id": "woocommerce_currency", "value": self.currency}])
        if path == "/products":
            return self._page([p for p in self.products.values() if p["type"] != "variation"], q)
        m = re.fullmatch(r"/products/(\d+)/variations", path)
        if m:
            return self._page([p for p in self.products.values() if p["parent_id"] == int(m[1])], q)
        m = re.fullmatch(r"/products/(\d+)(?:/variations/(\d+))?", path)
        if m:
            p = self.products.get(int(m[2] or m[1]))
            if p is None:
                return httpx.Response(404, json={"message": "Invalid ID."})
            if request.method == "PUT":
                if self.on_put:
                    self.on_put(p["id"])
                for k in ("manage_stock", "stock_quantity"):
                    if k in body:
                        p[k] = body[k]
                for meta in body.get("meta_data") or []:  # like update_meta_data: a key is replaced, not repeated
                    p["meta_data"] = [x for x in p.setdefault("meta_data", []) if x["key"] != meta["key"]] + \
                        [{"id": self._id(), "key": meta["key"], "value": meta["value"]}]
                if self.lose_put:  # saved on the website, but the answer is lost on the way
                    self.lose_put -= 1
                    raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(200, json=p)
        if path == "/orders":
            if self.on_orders_page:
                self.on_orders_page(int(q.get("page", 1)))
            # status: a list; 'any' (the default) is every status but the trash (WooCommerce stops at 'any')
            wanted = (q.get("status") or "any").split(",")
            rows = [o for o in self.orders.values() if (o["status"] != "trash" if "any" in wanted else o["status"] in wanted)]
            if q.get("include"):
                ids = {int(x) for x in q["include"].split(",") if x}
                rows = [o for o in rows if o["id"] in ids]
            if q.get("modified_after"):
                after = datetime.fromisoformat(q["modified_after"])
                rows = [o for o in rows if datetime.fromisoformat(o["date_modified_gmt"]) > after]
            # like MySQL, orders with the same date have no fixed order: it may differ from one page to the next
            tie = 1 if int(q.get("page", 1)) % 2 else -1
            by = {"id": lambda o: o["id"], "modified": lambda o: (o["date_modified_gmt"], tie * o["id"])}.get(
                q.get("orderby", "date"), lambda o: (o["date_created_gmt"], tie * o["id"]))
            rows.sort(key=by, reverse=q.get("order", "desc") == "desc")
            return self._page(rows, q)
        m = re.fullmatch(r"/orders/(\d+)", path)
        if m:
            o = self.orders.get(int(m[1]))
            if o is None:
                return httpx.Response(404, json={"code": "woocommerce_rest_shop_order_invalid_id", "message": "Invalid ID.", "data": {"status": 404}})
            return httpx.Response(200, json=o)
        m = re.fullmatch(r"/orders/(\d+)/refunds", path)
        if m:
            return self._page(self.refunds.get(int(m[1]), []), q)
        return httpx.Response(404, json={"code": "rest_no_route", "message": "No route was found matching the URL and request method."})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
