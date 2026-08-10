"""Properties that must hold for any ledger state, however it was reached."""

import uuid

from sqlalchemy import func, select

from app.models import Account, Entry, Transaction


def test_every_transaction_balances_to_zero(client, funded, make_account):
    src, dst = funded(5_000), make_account()
    for amount in (100, 250, 999):
        client.post(
            "/transfers",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={
                "source_account_id": src["id"],
                "destination_account_id": dst["id"],
                "amount_minor": amount,
            },
        )

    from app.db import SessionLocal

    with SessionLocal() as session:
        rows = session.execute(
            select(Entry.transaction_id, func.sum(Entry.amount_minor))
            .group_by(Entry.transaction_id)
        ).all()
        assert rows, "no transactions were recorded"
        for transaction_id, total in rows:
            assert total == 0, f"transaction {transaction_id} sums to {total}, not zero"


def test_every_transaction_has_exactly_two_entries(client, funded, make_account):
    src, dst = funded(5_000), make_account()
    client.post(
        "/transfers",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": 100,
        },
    )

    from app.db import SessionLocal

    with SessionLocal() as session:
        counts = session.execute(
            select(Entry.transaction_id, func.count()).group_by(Entry.transaction_id)
        ).all()
        for transaction_id, n in counts:
            assert n == 2, f"transaction {transaction_id} has {n} entries"


def test_database_rejects_an_overdraft_even_if_the_service_is_bypassed(client, make_account):
    """The CHECK constraint is the real guarantee, not the service check.

    This writes straight to the table, skipping every application-level
    validation, and the database still refuses.
    """
    import pytest
    from sqlalchemy.exc import DBAPIError

    from app.db import SessionLocal

    acct = make_account()

    with SessionLocal() as session:
        account = session.get(Account, uuid.UUID(acct["id"]))
        account.balance_minor = -1
        # DBAPIError rather than IntegrityError: drivers disagree on which
        # subclass a CHECK violation maps to (pg8000 raises ProgrammingError,
        # psycopg raises IntegrityError). Asserting on the constraint name keeps
        # the test precise without pinning it to one driver's taxonomy.
        with pytest.raises(DBAPIError) as excinfo:
            session.commit()
        assert "ck_accounts_no_overdraft" in str(excinfo.value)


def test_entries_are_never_orphaned(client, funded, make_account):
    src, dst = funded(1_000), make_account()
    client.post(
        "/transfers",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": 100,
        },
    )

    from app.db import SessionLocal

    with SessionLocal() as session:
        orphans = session.scalar(
            select(func.count())
            .select_from(Entry)
            .outerjoin(Transaction, Entry.transaction_id == Transaction.id)
            .where(Transaction.id.is_(None))
        )
        assert orphans == 0
