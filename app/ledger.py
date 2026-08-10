"""Transfer logic.

The whole service exists for one function, `execute_transfer`, and the three
things it has to get right: idempotency, lock ordering, and never letting the
balance and the entry log disagree.
"""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import (
    AccountNotFound,
    CurrencyMismatch,
    IdempotencyConflict,
    InsufficientFunds,
    InvalidTransfer,
)
from app.models import Account, Entry, Transaction


def fingerprint(source_id: uuid.UUID, dest_id: uuid.UUID, amount_minor: int) -> str:
    """Stable hash of the parts of a request that must not change on replay."""
    raw = f"{source_id}:{dest_id}:{amount_minor}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _lock_accounts(session: Session, first: uuid.UUID, second: uuid.UUID) -> dict[uuid.UUID, Account]:
    """Lock two account rows, always in the same global order.

    This is the deadlock fix, and it is the reason the ids are sorted rather
    than taken in source-then-destination order. Two simultaneous transfers,
    A->B and B->A, that each locked their own source first would hold one row
    apiece and wait forever for the other; Postgres would kill one of them.
    Sorting by id means every transaction in the system grabs the lower id
    first, so a cycle can never form.

    Locking in two statements rather than one `IN (...)` query is deliberate:
    row lock acquisition order within a single scan is a planner detail, not a
    guarantee.
    """
    locked: dict[uuid.UUID, Account] = {}
    for account_id in (first, second):
        account = session.scalar(select(Account).where(Account.id == account_id).with_for_update())
        if account is None:
            raise AccountNotFound(f"account {account_id} does not exist")
        locked[account_id] = account
    return locked


def execute_transfer(
    session: Session,
    *,
    idempotency_key: str,
    source_id: uuid.UUID,
    dest_id: uuid.UUID,
    amount_minor: int,
    reference: str | None = None,
) -> tuple[Transaction, bool]:
    """Move money between two accounts.

    Returns (transaction, created). `created` is False when an existing
    transaction was replayed, which lets the route answer 201 vs 200.
    """
    if amount_minor <= 0:
        raise InvalidTransfer("amount_minor must be positive")
    if source_id == dest_id:
        raise InvalidTransfer("source and destination must differ")

    digest = fingerprint(source_id, dest_id, amount_minor)

    # Fast path: the key has already been used.
    existing = session.scalar(
        select(Transaction).where(Transaction.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise IdempotencyConflict(
                "idempotency key was already used with different transfer parameters"
            )
        return existing, False

    low, high = sorted([source_id, dest_id], key=str)
    accounts = _lock_accounts(session, low, high)
    source, dest = accounts[source_id], accounts[dest_id]

    if source.currency != dest.currency:
        raise CurrencyMismatch(
            f"cannot transfer between {source.currency} and {dest.currency} accounts"
        )

    # Checked under the row lock, so a concurrent withdrawal cannot slip
    # between the read and the write. The database CHECK constraint is the
    # backstop; this exists to return a clean 422 instead of a 500.
    if not source.allow_negative and source.balance_minor < amount_minor:
        raise InsufficientFunds(
            f"balance {source.balance_minor} is below requested {amount_minor}"
        )

    transaction = Transaction(
        idempotency_key=idempotency_key,
        request_hash=digest,
        reference=reference,
    )
    session.add(transaction)
    try:
        # This flush is what inserts the transaction row, so it is where the
        # unique index on idempotency_key actually arbitrates. Two concurrent
        # requests can both pass the lookup above; only one survives here.
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        replayed = session.scalar(
            select(Transaction).where(Transaction.idempotency_key == idempotency_key)
        )
        if replayed is None:
            raise
        if replayed.request_hash != digest:
            raise IdempotencyConflict(
                "idempotency key was already used with different transfer parameters"
            ) from exc
        return replayed, False

    session.add_all(
        [
            Entry(
                transaction_id=transaction.id,
                account_id=source.id,
                amount_minor=-amount_minor,
                currency=source.currency,
            ),
            Entry(
                transaction_id=transaction.id,
                account_id=dest.id,
                amount_minor=amount_minor,
                currency=dest.currency,
            ),
        ]
    )

    source.balance_minor -= amount_minor
    dest.balance_minor += amount_minor

    # Deliberately unguarded. The balance was checked under the row lock, so the
    # no-overdraft CHECK constraint cannot fire here unless that logic is wrong -
    # and if it is, a 500 is the honest answer. Swallowing it would let the bug
    # hide behind a plausible-looking error response.
    session.flush()

    return transaction, True
