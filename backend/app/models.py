"""Database models.

All money amounts are integers in **Rial** to avoid floating point errors.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core.db import Base


def local_now() -> datetime:
    """All stored times are the salon's local wall-clock time (naive). Set TZ on servers (e.g. Asia/Tehran)."""
    return datetime.now()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)


# ---------------------------------------------------------------- users & audit
class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), default="receptionist")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    """Tamper-evident audit trail: each row stores the hash of the previous row."""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=local_now, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict | list | str | int | None] = mapped_column(JSON, nullable=True)


# ---------------------------------------------------------------- catalog
class ServiceLine(TimestampMixin, Base):
    """A business line of the salon: hair, nails, skin, makeup, lashes, ..."""
    __tablename__ = "service_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    color: Mapped[str] = mapped_column(String(16), default="#c084fc")
    icon: Mapped[str] = mapped_column(String(32), default="sparkles")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    services: Mapped[list[Service]] = relationship(back_populates="line")


class Service(TimestampMixin, Base):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("service_lines.id"))
    name: Mapped[str] = mapped_column(String(128))
    base_price: Mapped[int] = mapped_column(BigInteger, default=0)
    min_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    default_deposit: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    aliases: Mapped[list] = mapped_column(JSON, default=list)  # alternate names learned from chats/books
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # learned statistics (updated by the learning engine)
    learned_avg_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    learned_count: Mapped[int] = mapped_column(Integer, default=0)
    line: Mapped[ServiceLine] = relationship(back_populates="services")


class Staff(TimestampMixin, Base):
    __tablename__ = "staff"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(128))
    mobile: Mapped[str | None] = mapped_column(String(20), nullable=True)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("service_lines.id"), nullable=True)
    commission_percent: Mapped[float] = mapped_column(Float, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------- customers
class Customer(TimestampMixin, Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(128))
    mobile: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    instagram: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    whatsapp: Mapped[str | None] = mapped_column(String(20), nullable=True)
    birth_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual|chat|ocr|import
    known_cards: Mapped[list] = mapped_column(JSON, default=list)  # masked cards customer paid from


# ---------------------------------------------------------------- money accounts
class PaymentAccount(TimestampMixin, Base):
    """Where money lands: POS terminals, bank cards, bank accounts, cash box, online gateway."""
    __tablename__ = "payment_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # pos | card | bank | cash | gateway
    name: Mapped[str] = mapped_column(String(128))
    bank_name: Mapped[str] = mapped_column(String(64), default="")
    card_mask: Mapped[str | None] = mapped_column(String(32), nullable=True)
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    terminal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner_name: Mapped[str] = mapped_column(String(128), default="")
    ledger_account_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_accounts.id"), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)  # bank-sync provider key
    provider_config: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------- operations
class Appointment(TimestampMixin, Base):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"), nullable=True)
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="booked")  # booked|done|cancelled|no_show
    quoted_price: Mapped[int] = mapped_column(BigInteger, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")


class Deposit(TimestampMixin, Base):
    """بیعانه - prepayment held as a liability until applied to an invoice."""
    __tablename__ = "deposits"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"), nullable=True)
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"), nullable=True)
    payment_account_id: Mapped[int] = mapped_column(ForeignKey("payment_accounts.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, index=True)
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="held")  # held|applied|refunded|forfeited
    source: Mapped[str] = mapped_column(String(32), default="manual")
    applied_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    service_guess: Mapped[dict] = mapped_column(JSON, default=dict)  # AI/learning suggestion
    notes: Mapped[str] = mapped_column(Text, default="")


class Invoice(TimestampMixin, Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, index=True)
    status: Mapped[str] = mapped_column(String(16), default="issued")  # issued|paid|partial|void
    discount: Mapped[int] = mapped_column(BigInteger, default=0)
    subtotal: Mapped[int] = mapped_column(BigInteger, default=0)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    paid: Mapped[int] = mapped_column(BigInteger, default=0)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    notes: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[list[InvoiceItem]] = relationship(cascade="all, delete-orphan", back_populates="invoice")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"), nullable=True)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("service_lines.id"), nullable=True)
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(256), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[int] = mapped_column(BigInteger)
    discount: Mapped[int] = mapped_column(BigInteger, default=0)
    invoice: Mapped[Invoice] = relationship(back_populates="items")

    @property
    def amount(self) -> int:
        return self.quantity * self.unit_price - self.discount


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    payment_account_id: Mapped[int] = mapped_column(ForeignKey("payment_accounts.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, index=True)
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")


class Expense(TimestampMixin, Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(64))  # rent, salary, supplies, ...
    amount: Mapped[int] = mapped_column(BigInteger)
    payment_account_id: Mapped[int] = mapped_column(ForeignKey("payment_accounts.id"))
    spent_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)


# ---------------------------------------------------------------- double-entry ledger
class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(16))  # asset|liability|equity|revenue|expense
    parent_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=local_now, index=True)
    description: Mapped[str] = mapped_column(String(256))
    ref_type: Mapped[str] = mapped_column(String(32), default="")
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lines: Mapped[list[JournalLine]] = relationship(cascade="all, delete-orphan", back_populates="entry")


class JournalLine(Base):
    __tablename__ = "journal_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("ledger_accounts.id"), index=True)
    debit: Mapped[int] = mapped_column(BigInteger, default=0)
    credit: Mapped[int] = mapped_column(BigInteger, default=0)
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # service line dimension
    entry: Mapped[JournalEntry] = relationship(back_populates="lines")


# ---------------------------------------------------------------- reconciliation
class BankTransaction(TimestampMixin, Base):
    """A movement seen on a bank account / card / POS (from bank API, statement, or bank SMS)."""
    __tablename__ = "bank_transactions"
    __table_args__ = (UniqueConstraint("payment_account_id", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_account_id: Mapped[int | None] = mapped_column(ForeignKey("payment_accounts.id"), nullable=True)
    external_id: Mapped[str] = mapped_column(String(128))
    amount: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[str] = mapped_column(String(8), default="in")  # in|out
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    counterparty_card: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="bank_api")  # bank_api|statement|bank_sms
    status: Mapped[str] = mapped_column(String(16), default="unmatched")  # unmatched|matched|ignored
    matched_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # deposit|payment|receipt
    matched_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class InboundReceipt(TimestampMixin, Base):
    """A payment receipt a customer sent via SMS / WhatsApp / Instagram (text or screenshot)."""
    __tablename__ = "inbound_receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))  # sms|whatsapp|instagram|manual
    sender: Mapped[str] = mapped_column(String(64), index=True)  # phone or instagram handle
    raw_text: Mapped[str] = mapped_column(Text, default="")
    image_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)  # amount, reference, card, datetime
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|matched|mismatch|registered|rejected
    bank_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("bank_transactions.id"), nullable=True)
    deposit_id: Mapped[int | None] = mapped_column(ForeignKey("deposits.id"), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str] = mapped_column(Text, default="")


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=local_now, index=True)
    level: Mapped[str] = mapped_column(String(8), default="warning")  # info|warning|danger
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    message: Mapped[str] = mapped_column(Text, default="")
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------- learning & conversations
class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=local_now, index=True)
    channel: Mapped[str] = mapped_column(String(16))
    peer: Mapped[str] = mapped_column(String(64), index=True)  # phone / handle
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    direction: Mapped[str] = mapped_column(String(4))  # in|out
    text: Mapped[str] = mapped_column(Text, default="")
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"), nullable=True)  # label for learning


class ConversationState(Base):
    """State machine for the receipt bot (e.g. waiting for customer's name)."""
    __tablename__ = "conversation_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    peer: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(32), default="idle")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)


class KnowledgeItem(Base):
    """Long-term memory: learned facts the AI and matching engine use (token->service weights, notes...)."""
    __tablename__ = "knowledge_items"
    __table_args__ = (UniqueConstraint("kind", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # token_service | note | faq | price_observation
    key: Mapped[str] = mapped_column(String(256))
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)


# ---------------------------------------------------------------- imports (OCR / sales book)
class ImportBatch(TimestampMixin, Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), default="sales_book")
    file_name: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="review")  # review|committed|discarded
    provider: Mapped[str] = mapped_column(String(32), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    rows: Mapped[list] = mapped_column(JSON, default=list)  # parsed rows with warnings
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class PluginState(Base):
    __tablename__ = "plugin_states"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
