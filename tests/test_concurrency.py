"""The tests this project exists for.

Correctness under concurrency is the hard part of a ledger, and it is invisible
to a single-threaded test suite. These run real parallel requests against real
Postgres row locks and assert the properties that must survive.
"""

import random
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import Account, Entry
from tests.conftest import API_KEY


def _post_transfer(src_id: str, dst_id: str, amount: int) -> int:
    """One transfer on its own client, so nothing is shared across threads."""
    with TestClient(app) as c:
        resp = c.post(
            "/transfers",
            headers={"X-API-Key": API_KEY, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "source_account_id": src_id,
                "destination_account_id": dst_id,
                "amount_minor": amount,
            },
        )
        return resp.status_code


def test_concurrent_transfers_conserve_money(client, make_account):
    """Money is never created or destroyed, whatever the interleaving.

    Ten accounts, funded from external, then 120 random transfers run 12 at a
    time. Afterwards every balance must still equal the sum of that account's
    entries, and the whole ledger must still sum to zero.
    """
    external = make_account(name=f"ext-{uuid.uuid4()}", allow_negative=True)
    accounts = [make_account() for _ in range(10)]

    for acct in accounts:
        assert _post_transfer(external["id"], acct["id"], 10_000) == 201

    pairs = []
    for _ in range(120):
        a, b = random.sample(accounts, 2)
        pairs.append((a["id"], b["id"], random.randint(1, 500)))

    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(lambda p: _post_transfer(*p), pairs))

    # Under contention some transfers may legitimately fail on insufficient
    # funds; nothing should fail for any other reason.
    assert set(codes) <= {201, 422}, f"unexpected statuses: {sorted(set(codes))}"
    assert codes.count(201) > 0, "no transfer succeeded - the test proved nothing"

    from app.db import SessionLocal

    with SessionLocal() as session:
        # 1. Every cached balance still agrees with the immutable entry log.
        for acct in accounts + [external]:
            account = session.get(Account, uuid.UUID(acct["id"]))
            summed = session.scalar(
                select(func.coalesce(func.sum(Entry.amount_minor), 0)).where(
                    Entry.account_id == account.id
                )
            )
            assert account.balance_minor == summed, (
                f"account {account.name} balance {account.balance_minor} != entries {summed}"
            )

        # 2. The ledger as a whole nets to zero: what the accounts hold is
        #    exactly what external is down.
        total = session.scalar(select(func.coalesce(func.sum(Account.balance_minor), 0)))
        assert total == 0, f"ledger does not balance: net {total}"

        # 3. Nothing went negative that was not allowed to.
        negatives = session.scalars(
            select(Account).where(Account.balance_minor < 0, Account.allow_negative.is_(False))
        ).all()
        assert not negatives, f"overdrawn accounts: {[a.name for a in negatives]}"


def test_no_overdraft_under_concurrent_withdrawals(client, funded, make_account):
    """The classic race: many withdrawals against one balance at once.

    An account holding exactly 10 withdrawals' worth is hit by 30 simultaneous
    withdrawals. A check-then-write without a row lock lets far more than 10
    through and drives the balance negative. Exactly 10 must succeed.
    """
    src = funded(1_000)
    sinks = [make_account() for _ in range(30)]

    with ThreadPoolExecutor(max_workers=15) as pool:
        codes = list(pool.map(lambda s: _post_transfer(src["id"], s["id"], 100), sinks))

    succeeded = codes.count(201)
    rejected = codes.count(422)

    assert succeeded == 10, f"expected exactly 10 successes, got {succeeded}"
    assert succeeded + rejected == 30, f"unexpected statuses: {sorted(set(codes))}"

    final = client.get(f"/accounts/{src['id']}").json()["balance_minor"]
    assert final == 0, f"balance ended at {final}, expected 0"


def test_concurrent_identical_idempotency_keys_post_once(client, funded, make_account):
    """Same key, fired simultaneously - the unique index has to arbitrate.

    Both requests pass the "does this key exist?" check before either commits,
    so correctness here depends on the database constraint, not the lookup.
    """
    src, dst = funded(10_000), make_account()
    shared_key = str(uuid.uuid4())

    def fire() -> int:
        with TestClient(app) as c:
            resp = c.post(
                "/transfers",
                headers={"X-API-Key": API_KEY, "Idempotency-Key": shared_key},
                json={
                    "source_account_id": src["id"],
                    "destination_account_id": dst["id"],
                    "amount_minor": 1_000,
                },
            )
            return resp.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: fire(), range(8)))

    assert set(codes) <= {200, 201}, f"unexpected statuses: {sorted(set(codes))}"
    assert codes.count(201) == 1, f"transfer applied {codes.count(201)} times, expected once"

    # The decisive assertion: one transfer's worth of money moved, not eight.
    assert client.get(f"/accounts/{dst['id']}").json()["balance_minor"] == 1_000
