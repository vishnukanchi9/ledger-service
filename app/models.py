"""Schema for a double-entry ledger.

Two invariants matter, and both are enforced by the database rather than by
application code, because application code is what has bugs:

  1. an account cannot go negative unless it is explicitly allowed to
     (CHECK constraint), so money cannot be created from nothing even if the
     service logic is wrong
  2. an idempotency key can be used at most once (UNIQUE constraint), so a
     retried request cannot post a second transfer even under a race
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Money is a signed integer count of minor units (cents), never a float.
    # 0.1 + 0.2 != 0.3 in binary floating point, and a ledger that drifts by a
    # cent per thousand transactions is worse than useless.
    balance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Funds have to enter the system somewhere. A single external account is
    # allowed to go negative; it represents the outside world, and its negative
    # balance is exactly the total money held inside the ledger.
    allow_negative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    entries: Mapped[list["Entry"]] = relationship(back_populates="account")

    __table_args__ = (
        UniqueConstraint("name", name="uq_accounts_name"),
        # The no-overdraft rule lives here, not in the service layer. A bug in
        # the transfer path raises an IntegrityError instead of quietly
        # inventing money.
        CheckConstraint(
            "balance_minor >= 0 OR allow_negative",
            name="ck_accounts_no_overdraft",
        ),
        CheckConstraint("char_length(currency) = 3", name="ck_accounts_currency_len"),
    )


class Transaction(Base):
    """One transfer. Groups the entries that must balance to zero."""

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Clients retry. Without this, a timeout followed by a retry posts the
    # transfer twice, and the client has no way to tell that it did.
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    # Fingerprint of the request that created this transaction. A key replayed
    # with a *different* body is a client bug, and returning the original
    # transfer would hide it. Storing the hash lets that case 409 instead.
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    entries: Mapped[list["Entry"]] = relationship(
        back_populates="transaction", order_by="Entry.id", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_transactions_idempotency_key"),
    )


class Entry(Base):
    """A single signed movement against one account.

    Entries are the source of truth and are never updated or deleted.
    `Account.balance_minor` is a cached projection of them, maintained in the
    same transaction; `tests/test_invariants.py` asserts the two agree.
    """

    __tablename__ = "entries"

    # A monotonic integer id, deliberately not a UUID: it gives a total order
    # for keyset pagination. Paginating a ledger by OFFSET both degrades on
    # large accounts and silently skips rows when new entries land mid-scan.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )

    # Negative debits the account, positive credits it. The two entries of a
    # transfer sum to zero.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    transaction: Mapped[Transaction] = relationship(back_populates="entries")
    account: Mapped[Account] = relationship(back_populates="entries")

    __table_args__ = (
        CheckConstraint("amount_minor <> 0", name="ck_entries_nonzero"),
        # Covering index for the pagination query: newest entries for one
        # account, walked backwards by id.
        Index("ix_entries_account_id_desc", "account_id", id.desc()),
    )
