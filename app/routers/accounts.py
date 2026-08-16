import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.errors import AccountNotFound, LedgerError
from app.models import Account, Entry
from app.schemas import AccountCreate, AccountOut, EntryPage

router = APIRouter(prefix="/accounts", tags=["accounts"])


class DuplicateAccount(LedgerError):
    status_code = 409
    code = "duplicate_account"


@router.get("", response_model=list[AccountOut])
def list_accounts(session: Session = Depends(get_session)) -> list[Account]:
    """Return accounts for the console, ordered predictably by creation time."""
    return list(session.scalars(select(Account).order_by(Account.created_at.desc())))


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, session: Session = Depends(get_session)) -> Account:
    account = Account(
        name=payload.name,
        currency=payload.currency,
        allow_negative=payload.allow_negative,
    )
    session.add(account)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateAccount(f"account named {payload.name!r} already exists") from exc
    return account


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: uuid.UUID, session: Session = Depends(get_session)) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise AccountNotFound(f"account {account_id} does not exist")
    return account


@router.get("/{account_id}/entries", response_model=EntryPage)
def list_entries(
    account_id: uuid.UUID,
    response: Response,
    before: int | None = Query(default=None, description="Return entries with id < this value"),
    limit: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> EntryPage:
    """Newest entries first, walked backwards by id.

    Keyset pagination rather than LIMIT/OFFSET. OFFSET has to count and discard
    every skipped row, so deep pages get slower the longer the ledger gets; and
    because new entries land at the top while a client is paging, OFFSET also
    shifts rows across page boundaries and silently skips them. A cursor on a
    monotonic id has neither problem.
    """
    settings = get_settings()
    page_size = min(limit or settings.default_page_size, settings.max_page_size)

    if session.get(Account, account_id) is None:
        raise AccountNotFound(f"account {account_id} does not exist")

    stmt = select(Entry).where(Entry.account_id == account_id)
    if before is not None:
        stmt = stmt.where(Entry.id < before)
    stmt = stmt.order_by(Entry.id.desc()).limit(page_size + 1)

    rows = list(session.scalars(stmt))
    # One extra row was requested purely to answer "is there another page?"
    # without a second COUNT query.
    has_more = len(rows) > page_size
    items = rows[:page_size]

    response.headers["Cache-Control"] = "no-store"
    return EntryPage(
        items=[e for e in items],
        next_cursor=items[-1].id if (has_more and items) else None,
    )
