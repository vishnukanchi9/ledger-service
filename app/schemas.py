import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    allow_negative: bool = False

    @field_validator("currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    currency: str
    balance_minor: int
    allow_negative: bool
    created_at: datetime


class TransferCreate(BaseModel):
    source_account_id: uuid.UUID
    destination_account_id: uuid.UUID
    # Minor units, so the wire format has no decimal point and no rounding
    # question. 1050 is $10.50; there is nothing to misread.
    amount_minor: int = Field(gt=0, description="Positive amount in minor units (cents)")
    reference: str | None = Field(default=None, max_length=255)


class EntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: uuid.UUID
    account_id: uuid.UUID
    amount_minor: int
    currency: str
    created_at: datetime


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idempotency_key: str
    reference: str | None
    created_at: datetime
    entries: list[EntryOut]


class EntryPage(BaseModel):
    items: list[EntryOut]
    # Opaque to clients, but it is the id of the last row on this page. Absent
    # when there is nothing further back.
    next_cursor: int | None
