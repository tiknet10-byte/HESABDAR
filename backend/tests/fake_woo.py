"""A small in-memory WooCommerce store (REST API v3 subset) for the tests and for trying the screens.

It behaves like WooCommerce where it matters here: orders take goods out of the stock (processing / on-hold /
completed), cancelling puts them back, refunds can restock, money is strings in the store currency, dates come
as site-local and GMT, lists are paged with X-WP-Total / X-WP-TotalPages, `modified_after` filters orders.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import httpx

GMT = timedelta(hours=3, minutes=30)
TAKES_STOCK = {"processing", "on-hold", "completed"}


def _s(v: float | int) -> str:
    return f"{v:.2f}"


class FakeWoo:
    stores = 0  # each store gets its own ids (like different websites)

    def __init__(self, currency: str = "IRT", key: str = "ck_test_1234567890", secret: str = "cs_test_1234567890") -> None:
        self.currency, self.key, self.secret = currency, key, secret
        self.products: dict[int, dict] = {}
        self.orders: dict[int, dict] = {}
        self.refunds: dict[int, list[dict]] = {}
        FakeWoo.stores += 1
        self.ids = 100_000 * FakeWoo.stores
        self.down = False
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
                              "parent_id": 0, "attributes": []}
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
                                  "attributes": [{"name": "حجم", "option": option}]}
            vids.append(vid)
        return pid, vids

    def _stock(self, pid: int, delta: int) -> None:
        p = self.products[pid]
        if p["manage_stock"]:
            p["stock_quantity"] = (p["stock_quantity"] or 0) + delta

    def _touch(self, o: dict) -> None:
        now = self.now()
        o["date_modified"] = now.isoformat()
        o["date_modified_gmt"] = (now - GMT).isoformat()

    def add_order(self, items: list[tuple[int, int]], *, status: str = "processing", paid: bool = True, phone: str = "",
                  first: str = "", last: str = "", shipping: int = 0, coupon: int = 0, method: str = "zarinpal",
                  method_title: str = "درگاه زرین‌پال", created: datetime | None = None, customer_id: int = 0) -> dict:
        """items: [(product or variation id, qty)] at the product's price; coupon: taken off the first line."""
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
             "date_created": created.isoformat(), "date_created_gmt": (created - GMT).isoformat(),
             "date_paid": (created + timedelta(minutes=2)).isoformat() if paid else None, "date_completed": None,
             "payment_method": method, "payment_method_title": method_title,
             "billing": {"first_name": first, "last_name": last, "phone": phone, "email": f"{oid}@example.com"},
             "shipping": {"first_name": first, "last_name": last, "phone": ""},
             "line_items": lines, "shipping_total": _s(shipping), "fee_lines": [], "discount_total": _s(coupon), "total_tax": "0.00",
             "total": _s(total), "refunds": []}
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
            o["date_paid"] = self.now().isoformat()
        if status == "completed":
            o["date_completed"] = self.now().isoformat()
        self._touch(o)

    def refund(self, oid: int, amount: int, items: list[tuple[int, int, int]] | None = None, restock: bool = True) -> None:
        """items: [(order line id, qty, amount)]"""
        o = self.orders[oid]
        rid = self._id()
        lines = []
        for line_id, qty, amt in items or []:
            li = next(x for x in o["line_items"] if x["id"] == line_id)
            lines.append({"id": self._id(), "product_id": li["product_id"], "variation_id": li["variation_id"], "quantity": -qty,
                          "total": _s(-amt), "subtotal": _s(-amt), "meta_data": [{"key": "_refunded_item_id", "value": str(line_id)}]})
            if restock:
                self._stock(li["variation_id"] or li["product_id"], qty)
        self.refunds[oid].append({"id": rid, "date_created": self.now().isoformat(), "amount": _s(amount), "reason": "", "line_items": lines})
        o["refunds"].append({"id": rid, "reason": "", "total": _s(-amount)})
        refunded = sum(float(r["amount"]) for r in self.refunds[oid])
        if refunded >= float(o["total"]):
            o["status"] = "refunded"
        self._touch(o)

    # ---------------------------------------------------------------- HTTP
    def _authorized(self, request: httpx.Request, q: dict) -> bool:
        if q.get("consumer_key") == self.key and q.get("consumer_secret") == self.secret:
            return True
        auth = request.headers.get("authorization", "")
        if auth.startswith("Basic "):
            return base64.b64decode(auth[6:]).decode() == f"{self.key}:{self.secret}"
        return False

    @staticmethod
    def _page(items: list, q: dict) -> httpx.Response:
        per, page = int(q.get("per_page", 10)), int(q.get("page", 1))
        chunk = items[(page - 1) * per: page * per]
        pages = max(1, -(-len(items) // per))
        return httpx.Response(200, json=chunk, headers={"X-WP-Total": str(len(items)), "X-WP-TotalPages": str(pages)})

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down", request=request)
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
                for k in ("manage_stock", "stock_quantity"):
                    if k in body:
                        p[k] = body[k]
            return httpx.Response(200, json=p)
        if path == "/orders":
            rows = list(self.orders.values())
            if q.get("modified_after"):
                after = datetime.fromisoformat(q["modified_after"])
                rows = [o for o in rows if datetime.fromisoformat(o["date_modified_gmt"]) > after]
            rows.sort(key=lambda o: (o["date_modified_gmt"], o["id"]))
            return self._page(rows, q)
        m = re.fullmatch(r"/orders/(\d+)/refunds", path)
        if m:
            return httpx.Response(200, json=self.refunds.get(int(m[1]), []))
        return httpx.Response(404, json={"code": "rest_no_route", "message": "No route was found matching the URL and request method."})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
