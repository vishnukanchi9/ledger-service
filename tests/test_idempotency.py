import uuid


def test_replayed_key_does_not_post_twice(client, funded, make_account):
    """The retry case: same key, same body, twice."""
    src, dst = funded(10_000), make_account()
    k = {"Idempotency-Key": str(uuid.uuid4())}
    body = {
        "source_account_id": src["id"],
        "destination_account_id": dst["id"],
        "amount_minor": 3_000,
    }

    first = client.post("/transfers", headers=k, json=body)
    second = client.post("/transfers", headers=k, json=body)

    # 201 created, then 200 replayed - the client can tell the difference.
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    # And the money moved exactly once.
    assert client.get(f"/accounts/{dst['id']}").json()["balance_minor"] == 3_000
    assert client.get(f"/accounts/{src['id']}").json()["balance_minor"] == 7_000


def test_same_key_with_different_body_conflicts(client, funded, make_account):
    """Reusing a key for a different transfer is a client bug, not a retry.

    Returning the original transfer would hide it, so this is a 409.
    """
    src, dst = funded(10_000), make_account()
    k = {"Idempotency-Key": str(uuid.uuid4())}

    first = client.post(
        "/transfers",
        headers=k,
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": 1_000,
        },
    )
    assert first.status_code == 201

    second = client.post(
        "/transfers",
        headers=k,
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": 9_999,
        },
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_conflict"
    assert client.get(f"/accounts/{dst['id']}").json()["balance_minor"] == 1_000


def test_failed_transfer_does_not_burn_the_key(client, funded, make_account):
    """A rejected transfer must leave the key reusable.

    Otherwise a client that mis-typed an amount could never retry with the
    corrected one.
    """
    src, dst = funded(500), make_account()
    k = {"Idempotency-Key": str(uuid.uuid4())}

    rejected = client.post(
        "/transfers",
        headers=k,
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": 100_000,
        },
    )
    assert rejected.status_code == 422

    retried = client.post(
        "/transfers",
        headers=k,
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": 200,
        },
    )
    assert retried.status_code == 201
    assert client.get(f"/accounts/{dst['id']}").json()["balance_minor"] == 200
