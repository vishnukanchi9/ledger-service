from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.ledger import execute_transfer
from app.models import Transaction
from app.schemas import TransactionOut, TransferCreate

router = APIRouter(prefix="/transfers", tags=["transfers"])


@router.post("", response_model=TransactionOut)
def create_transfer(
    payload: TransferCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
) -> Transaction:
    """Post a transfer.

    201 when the transfer was applied, 200 when an existing one was replayed.
    The distinction is what lets a client tell "my retry worked" from "my retry
    created a second payment".
    """
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_idempotency_key",
                "message": "Idempotency-Key header is required for transfers",
            },
        )

    transaction, created = execute_transfer(
        session,
        idempotency_key=idempotency_key,
        source_id=payload.source_account_id,
        dest_id=payload.destination_account_id,
        amount_minor=payload.amount_minor,
        reference=payload.reference,
    )
    session.commit()

    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return transaction
