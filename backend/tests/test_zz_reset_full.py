"""Runs last (file name): resetting data works with everything the earlier tests left behind - products, stock
moves, purchases, suppliers, Tizpardaz invoices, website orders."""


def test_reset_with_products_purchases_history_and_website_orders(client):
    assert client.get("/api/products?all=1").json(), "earlier tests leave products"
    assert client.get("/api/trade-docs").json(), "and Tizpardaz invoices"
    body = {"password": "Secret123", "confirm": "حذف"}
    r = client.post("/api/admin/reset", json={**body, "scope": "transactions"})
    assert r.status_code == 200, r.text
    assert client.get("/api/purchases").json() == [] and client.get("/api/trade-docs").json() == []
    assert client.get("/api/woo/orders").json() == []
    products = client.get("/api/products?all=1").json()
    assert products and all(p["stock_qty"] == 0 and p["last_sale_price"] is None for p in products)  # catalog kept, stock gone
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"] == 0
    r = client.post("/api/admin/reset", json={**body, "scope": "customers"})
    assert r.status_code == 200, r.text
    assert client.get("/api/customers").json()["total"] == 0
    assert all(s["customer_id"] is None for s in client.get("/api/suppliers").json())
    r = client.post("/api/admin/reset", json={**body, "scope": "factory"})
    assert r.status_code == 200, r.text
    assert client.get("/api/products?all=1").json() == [] and client.get("/api/suppliers").json() == []
