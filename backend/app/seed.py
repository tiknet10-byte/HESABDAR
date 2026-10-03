"""Initial data: chart of accounts, typical salon lines/services, and optional demo data."""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import PaymentAccount, Service, ServiceLine, Staff
from .services import accounting, learning

# prices in Rial
DEFAULT_CATALOG = {
    ("مو", "#f472b6", "scissors"): [
        ("کوتاهی مو", 3_500_000, 0, ["کوتاهی", "اصلاح مو"]),
        ("رنگ مو", 18_000_000, 5_000_000, ["رنگ", "کالر"]),
        ("بالیاژ", 45_000_000, 10_000_000, ["هایلایت", "آمبره", "لایت"]),
        ("کراتین", 35_000_000, 10_000_000, ["صافی", "کراتینه", "پروتئین"]),
        ("براشینگ", 4_000_000, 0, ["سشوار", "براش"]),
    ],
    ("ناخن", "#a78bfa", "hand"): [
        ("کاشت ناخن", 9_000_000, 2_000_000, ["کاشت", "پودر", "ژل"]),
        ("ترمیم ناخن", 6_000_000, 1_000_000, ["ترمیم"]),
        ("مانیکور", 3_000_000, 0, ["مانیکور"]),
        ("پدیکور", 4_500_000, 0, ["پدیکور"]),
        ("لاک ژل", 3_500_000, 0, ["لاک", "ژلیش"]),
    ],
    ("پوست", "#34d399", "sparkles"): [
        ("پاکسازی پوست", 8_000_000, 2_000_000, ["فیشیال", "پاکسازی"]),
        ("هیدرافیشیال", 15_000_000, 5_000_000, ["هیدرا"]),
        ("میکرونیدلینگ", 20_000_000, 5_000_000, ["میکرو", "درمارولر"]),
    ],
    ("مژه و ابرو", "#fbbf24", "eye"): [
        ("اکستنشن مژه", 10_000_000, 3_000_000, ["اکستنشن", "مژه"]),
        ("لیفت مژه", 6_000_000, 2_000_000, ["لیفت"]),
        ("میکروبلیدینگ ابرو", 25_000_000, 8_000_000, ["میکروبلیدینگ", "فیبروز", "تاتو ابرو"]),
        ("اصلاح ابرو", 1_500_000, 0, ["اصلاح ابرو", "بند"]),
    ],
    ("میکاپ و عروس", "#fb7185", "crown"): [
        ("میکاپ", 15_000_000, 5_000_000, ["آرایش", "گریم"]),
        ("شینیون", 12_000_000, 4_000_000, ["مدل مو", "بافت"]),
        ("پکیج عروس", 150_000_000, 50_000_000, ["عروس", "عروسی"]),
    ],
}

DEFAULT_ACCOUNTS = [
    ("pos", "کارتخوان ملت", "ملت"),
    ("pos", "کارتخوان سامان", "سامان"),
    ("card", "کارت بانک ملی (مدیر)", "ملی"),
    ("card", "کارت پاسارگاد", "پاسارگاد"),
    ("cash", "صندوق نقدی", ""),
]


def seed_base(db: Session) -> None:
    accounting.ensure_chart(db)
    if db.scalar(select(func.count(ServiceLine.id))):
        return
    for (line_name, color, icon), services in DEFAULT_CATALOG.items():
        line = ServiceLine(name=line_name, color=color, icon=icon)
        db.add(line)
        db.flush()
        accounting.revenue_account_for_line(db, line_name)
        for name, price, deposit, aliases in services:
            s = Service(line_id=line.id, name=name, base_price=price, default_deposit=deposit, aliases=aliases,
                        min_price=int(price * 0.5), max_price=int(price * 2.5))
            db.add(s)
            db.flush()
            learning.learn_text(db, name, s.id, weight=3)
            for a in aliases:
                learning.learn_text(db, a, s.id, weight=3)
    for kind, name, bank in DEFAULT_ACCOUNTS:
        pa = PaymentAccount(kind=kind, name=name, bank_name=bank)
        db.add(pa)
        db.flush()
        accounting.cash_account_for(db, pa)
    db.flush()


FIRST = ["سارا", "مریم", "نگار", "الهام", "زهرا", "فاطمه", "نازنین", "شیما", "هانیه", "پریسا", "مهسا", "نیلوفر", "یاسمن", "ترانه", "آیدا"]
LAST = ["احمدی", "محمدی", "کریمی", "رضایی", "حسینی", "موسوی", "جعفری", "صادقی", "نوری", "کاظمی", "رحیمی", "عباسی"]


def seed_demo(db: Session, days: int = 120) -> dict:
    """Generate realistic history so dashboards, learning and forecasts have data to show."""
    rnd = random.Random(42)
    seed_base(db)
    services = db.scalars(select(Service)).all()
    accounts = db.scalars(select(PaymentAccount)).all()
    staff_names = ["لیلا (مو)", "رویا (ناخن)", "سمیرا (پوست)", "نسترن (میکاپ)"]
    staff = []
    for n in staff_names:
        st = Staff(full_name=n, commission_percent=30)
        db.add(st)
        staff.append(st)
    db.flush()
    customers = []
    for i in range(80):
        c, _ = accounting.find_or_create_customer(db, f"{rnd.choice(FIRST)} {rnd.choice(LAST)}", f"0912{1000000 + i * 7919 % 9000000:07d}")
        c.created_at = datetime.now() - timedelta(days=rnd.randint(0, days))
        customers.append(c)
    start = datetime.now() - timedelta(days=days)
    invoices = 0
    for d in range(days):
        day = start + timedelta(days=d)
        weekday_factor = 1.6 if day.weekday() in (2, 3) else 0.4 if day.weekday() == 4 else 1.0  # busy Wed/Thu, quiet Friday
        growth = 1 + d / days * 0.3
        for _ in range(int(rnd.randint(3, 8) * weekday_factor * growth)):
            c = rnd.choice(customers)
            picks = rnd.sample(services, k=rnd.choice([1, 1, 1, 2]))
            items = [{"service_id": s.id, "unit_price": int(s.base_price * rnd.uniform(0.9, 1.15) // 100000 * 100000),
                      "staff_id": rnd.choice(staff).id} for s in picks]
            at = day.replace(hour=rnd.randint(10, 20), minute=rnd.choice([0, 15, 30, 45]))
            if any(s.default_deposit for s in picks) and rnd.random() < 0.6:
                s = next(s for s in picks if s.default_deposit)
                accounting.record_deposit(db, customer=c, amount=s.default_deposit, payment_account=rnd.choice(accounts[2:4]),
                                          received_at=at - timedelta(days=rnd.randint(1, 7)), service_id=s.id)
            inv = accounting.issue_invoice(db, customer=c, items=items, apply_all_deposits=True, issued_at=at)
            due = inv.total - inv.paid
            if due > 0:
                accounting.record_payment(db, invoice=inv, payment_account=rnd.choice(accounts), amount=due, paid_at=at)
            invoices += 1
        if d % 30 == 0:
            accounting.record_expense(db, category="اجاره", amount=250_000_000, payment_account=accounts[2], spent_at=day)
        if d % 7 == 0:
            accounting.record_expense(db, category="مواد مصرفی", amount=rnd.randint(20, 60) * 1_000_000, payment_account=accounts[3], spent_at=day)
    # future bookings with deposits
    for _ in range(12):
        c = rnd.choice(customers)
        s = rnd.choice([s for s in services if s.default_deposit])
        accounting.record_deposit(db, customer=c, amount=s.default_deposit, payment_account=accounts[2], service_id=s.id,
                                  received_at=datetime.now() - timedelta(hours=rnd.randint(1, 72)))
    learning.refresh_price_stats(db)
    return {"customers": len(customers), "invoices": invoices}
