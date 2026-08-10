import uuid


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def test_transfer_moves_money_and_records_both_sides(client, funded, make_account):
    src = funded(10_000)
    dst = make_account()

    resp = client.post(
        "/transfers",
        headers=key(),
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": 2_500,
            "reference": "invoice-42",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Double entry: two rows, summing to zero.
    assert len(body["entries"]) == 2
    assert sum(e["amount_minor"] for e in body["entries"]) == 0

    assert client.get(f"/accounts/{src['id']}").json()["balance_minor"] == 7_500
    assert client.get(f"/accounts/{dst['id']}").json()["balance_minor"] == 2_500


def test_insufficient_funds_is_rejected(client, funded, make_account):
    src = funded(100)
    dst = make_account()

    resp = client.post(
        "/transfers",
        headers=key(),
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": 101,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "insufficient_funds"
    # And nothing moved.
    assert client.get(f"/accounts/{src['id']}").json()["balance_minor"] == 100


def test_currency_mismatch_is_rejected(client, funded, make_account):
    src = funded(1_000, currency="USD")
    dst = make_account(currency="EUR")

    resp = client.post(
        "/transfers",
        headers=key(),
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": 100,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "currency_mismatch"


def test_self_transfer_is_rejected(client, funded):
    src = funded(1_000)
    resp = client.post(
        "/transfers",
        headers=key(),
        json={
            "source_account_id": src["id"],
            "destination_account_id": src["id"],
            "amount_minor": 100,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_transfer"


def test_non_positive_amount_is_rejected_by_schema(client, funded, make_account):
    src, dst = funded(1_000), make_account()
    for amount in (0, -50):
        resp = client.post(
            "/transfers",
            headers=key(),
            json={
                "source_account_id": src["id"],
                "destination_account_id": dst["id"],
                "amount_minor": amount,
            },
        )
        assert resp.status_code == 422


def test_unknown_account_is_404(client, funded):
    src = funded(1_000)
    resp = client.post(
        "/transfers",
        headers=key(),
        json={
            "source_account_id": src["id"],
            "destination_account_id": str(uuid.uuid4()),
            "amount_minor": 100,
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "account_not_found"


def test_transfer_requires_idempotency_key(client, funded, make_account):
    src, dst = funded(1_000), make_account()
    resp = client.post(
        "/transfers",
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": 100,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "missing_idempotency_key"


def test_requires_api_key(funded, make_account):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anon:
        assert anon.get(f"/accounts/{uuid.uuid4()}").status_code == 401
