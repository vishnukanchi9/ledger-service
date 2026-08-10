import uuid


def _transfer(client, src, dst, amount=10):
    return client.post(
        "/transfers",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount_minor": amount,
        },
    )


def test_cursor_walks_every_entry_exactly_once(client, funded, make_account):
    src, dst = funded(10_000), make_account()
    for _ in range(25):
        assert _transfer(client, src, dst).status_code == 201

    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 10}
        if cursor is not None:
            params["before"] = cursor
        page = client.get(f"/accounts/{dst['id']}/entries", params=params).json()
        seen.extend(e["id"] for e in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert pages < 20, "cursor failed to terminate"

    assert len(seen) == 25
    assert len(set(seen)) == 25, "an entry was returned on more than one page"
    assert seen == sorted(seen, reverse=True), "entries should be newest first"


def test_new_entries_do_not_shift_rows_across_pages(client, funded, make_account):
    """The reason for keyset pagination rather than OFFSET.

    With OFFSET, inserting rows at the top between page 1 and page 2 pushes
    unread rows down past the offset and they are never returned. A cursor is
    anchored to an id, so it is unaffected.
    """
    src, dst = funded(10_000), make_account()
    for _ in range(10):
        _transfer(client, src, dst)

    first = client.get(f"/accounts/{dst['id']}/entries", params={"limit": 5}).json()
    assert len(first["items"]) == 5

    # Five more entries land while the client is paging.
    for _ in range(5):
        _transfer(client, src, dst)

    second = client.get(
        f"/accounts/{dst['id']}/entries",
        params={"limit": 5, "before": first["next_cursor"]},
    ).json()

    ids_first = {e["id"] for e in first["items"]}
    ids_second = {e["id"] for e in second["items"]}
    assert not (ids_first & ids_second), "pages overlapped"

    # The five originally-unread rows are exactly what page two returned.
    all_ids = sorted(ids_first | ids_second, reverse=True)
    assert len(all_ids) == 10


def test_page_size_is_capped(client, funded, make_account):
    from app.config import get_settings

    src, dst = funded(100_000), make_account()
    for _ in range(3):
        _transfer(client, src, dst)

    page = client.get(
        f"/accounts/{dst['id']}/entries", params={"limit": 100_000}
    ).json()
    assert len(page["items"]) <= get_settings().max_page_size


def test_entries_for_unknown_account_is_404(client):
    resp = client.get(f"/accounts/{uuid.uuid4()}/entries")
    assert resp.status_code == 404
