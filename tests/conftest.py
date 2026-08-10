"""Test fixtures.

Tests run against a real PostgreSQL instance, never SQLite. The behaviour under
test - SELECT ... FOR UPDATE, unique-violation races, CHECK constraints - either
does not exist in SQLite or behaves differently there, so a passing SQLite suite
would prove nothing about production.

Schema comes from `alembic upgrade head` rather than `create_all`, so every run
also exercises the migration that will be applied for real.
"""

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

API_KEY = "test-key"
os.environ.setdefault("API_KEY", API_KEY)
# Generous, so functional tests never trip the limiter; the limiter has its own
# test that lowers it deliberately.
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "100000")
os.environ.setdefault("RATE_LIMIT_BURST", "100000")


@pytest.fixture(scope="session", autouse=True)
def migrate() -> None:
    from app.config import get_settings

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def clean_tables(migrate):
    """Truncate between tests so ordering never matters.

    TRUNCATE rather than dropping and recreating: far faster, and it resets the
    entries id sequence, which the pagination tests depend on.
    """
    from app.db import engine

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE entries, transactions, accounts RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    with TestClient(app) as c:
        c.headers.update({"X-API-Key": API_KEY})
        yield c


@pytest.fixture
def make_account(client):
    def _make(name: str | None = None, currency: str = "USD", allow_negative: bool = False) -> dict:
        resp = client.post(
            "/accounts",
            json={
                "name": name or f"acct-{uuid.uuid4()}",
                "currency": currency,
                "allow_negative": allow_negative,
            },
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make


@pytest.fixture
def funded(client, make_account):
    """An account holding `amount`, funded from the external account.

    Money enters the ledger only by moving it off the external account, which
    is the one account permitted to go negative. Its balance is therefore the
    negative of everything held inside.
    """

    def _funded(amount: int, currency: str = "USD") -> dict:
        external = make_account(name=f"external-{uuid.uuid4()}", currency=currency, allow_negative=True)
        account = make_account(currency=currency)
        resp = client.post(
            "/transfers",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={
                "source_account_id": external["id"],
                "destination_account_id": account["id"],
                "amount_minor": amount,
            },
        )
        assert resp.status_code == 201, resp.text
        return client.get(f"/accounts/{account['id']}").json()

    return _funded
