# Ledger Service

A double-entry ledger API where the hard part — **staying correct under
concurrency** — is proven by the test suite rather than asserted in the README.

Money is never created or destroyed, retried requests never post twice, and an
account cannot be overdrawn even by a caller that bypasses the service entirely.
Each of those is a test that runs against real PostgreSQL on every push.

---

## The three properties CI proves

**1. Concurrent transfers conserve money.** Ten accounts, 120 random transfers,
12 at a time. Afterwards every account's cached balance must still equal the sum
of its immutable entries, and the whole ledger must net to zero.

**2. A balance cannot be raced into overdraft.** An account holding exactly ten
withdrawals' worth is hit by thirty simultaneous withdrawals. Exactly ten
succeed and the balance lands on zero. A check-then-write without a row lock
lets far more through and goes negative.

**3. Simultaneous retries post once.** Eight parallel requests carrying the same
`Idempotency-Key` — all of them pass the "has this key been used?" lookup before
any of them commits. Exactly one transfer is applied. Correctness here rests on
the unique index, not the lookup.

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/accounts` | 409 on duplicate name |
| `GET` | `/accounts/{id}` | |
| `GET` | `/accounts/{id}/entries` | Keyset pagination via `?before=&limit=` |
| `POST` | `/transfers` | Requires `Idempotency-Key`. **201** applied, **200** replayed |
| `GET` | `/healthz` | |

All endpoints require `X-API-Key` and are rate limited per key.

```bash
curl -X POST localhost:8000/transfers \
  -H "X-API-Key: $API_KEY" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"source_account_id":"...","destination_account_id":"...","amount_minor":2500}'
```

## Running it

```bash
docker compose up -d
pip install -r requirements-dev.txt
alembic upgrade head
pytest -v
uvicorn app.main:app --reload
```

Interactive docs at `/docs`. Point `DATABASE_URL` at any Postgres if you'd
rather not use compose.

## Ledger Console

Open `http://localhost:8000/` after starting the API for a same-origin browser
client. It creates accounts, posts idempotent transfers, and reads immutable
entry history through the public API. For local development, connect using the
configured `API_KEY` (the default is `dev-key-change-me`). The key is stored in
the browser session only, never persisted by the console.

---

## Design decisions

| Decision | Why |
|---|---|
| **Money as `BIGINT` minor units, never a float** | `0.1 + 0.2 != 0.3` in binary floating point. A ledger that drifts a cent per thousand transactions is worse than no ledger, because the error is small enough to go unnoticed and large enough to matter. |
| **Entries are the source of truth; `balance_minor` is a cached projection** | Reading a balance shouldn't mean summing a million rows, but the sum is what's *true*. Both are written in one transaction, and a test asserts they agree after 120 concurrent transfers. |
| **Accounts are locked in sorted id order** | The classic deadlock: transfers A→B and B→A each lock their own source, then wait forever for the other. Sorting by id means every transaction in the system takes the lower id first, so a wait cycle cannot form. |
| **Two separate `SELECT … FOR UPDATE` statements, not one `IN (…)`** | Lock acquisition order inside a single scan is a planner decision, not a guarantee. Two statements make the order explicit. |
| **No-overdraft is a `CHECK` constraint, not just a service check** | The service check exists to return a clean 422. The constraint is what makes the guarantee real — `test_database_rejects_an_overdraft_even_if_the_service_is_bypassed` writes straight to the table and the database still refuses. |
| **Idempotency enforced by a `UNIQUE` index** | Under concurrency, two requests can both see "key not used" before either commits. Only a unique index arbitrates. The loser catches the `IntegrityError` and returns the winner's transaction. |
| **A request fingerprint is stored alongside the key** | A key replayed with a *different* body is a client bug. Returning the original transfer would hide it, so that case is a 409 instead. |
| **Failed transfers don't consume the key** | Otherwise a client that fat-fingered an amount could never retry with the corrected one. |
| **201 for applied, 200 for replayed** | Lets a client distinguish "my retry worked" from "my retry created a second payment". |
| **Keyset pagination, never `OFFSET`** | `OFFSET` counts and discards every skipped row, so deep pages get slower as the ledger grows. Worse, new entries land at the top while a client is paging, shifting rows past the offset so they're never returned. A cursor on a monotonic id has neither problem — and there's a test that inserts rows mid-pagination to prove it. |
| **One extra row fetched per page** | Answers "is there another page?" without a second `COUNT(*)` over the account's history. |
| **Tests run on real PostgreSQL, never SQLite** | Row locking, unique-violation races, and `CHECK` constraints either don't exist in SQLite or behave differently. A green SQLite suite would prove nothing about production. |
| **Test schema comes from `alembic upgrade head`** | Building test tables with `create_all` means the migration that production runs is never exercised. Here every test run applies it. |
| **CI runs `downgrade base` then `upgrade head`** | A rollback path that has never been executed is a rollback path that does not work. |
| **CI diffs the live schema against the models** | Catches a model edited without a matching migration — a failure that otherwise stays invisible until deploy. |
| **`compare_digest` for the API key** | String `==` short-circuits on the first differing byte, leaking key material through response timing. |
| **Auth and rate limiting are router-level dependencies** | A new endpoint is protected by default, rather than protected only if the author remembers to add it. |
| **`pg8000` rather than `psycopg`** | Pure Python, so it installs identically on Linux CI and a Windows/ARM laptop. `psycopg`'s binary wheel has no win-arm64 build, which made local development impossible. |

## Known limitations

Deliberate, and worth naming rather than hiding:

- **Rate limiting is per process.** Behind multiple workers each replica keeps
  its own bucket. Moving the counter to Redis is the fix and wouldn't change
  the interface.
- **A single static API key**, not per-client credentials with scopes.
- **Transfers are same-currency only.** Cross-currency needs a rate source and a
  spread policy, which is a product decision rather than a technical one.
- **No outbox or event stream.** A real ledger usually publishes entries
  downstream for reconciliation.

## Cost

Zero. GitHub Actions is free on public repositories and PostgreSQL runs as a
service container on the runner. No cloud resources are created.
