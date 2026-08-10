"""Initial ledger schema

Revision ID: 0001
Revises:
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("balance_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("allow_negative", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_accounts_name"),
        # The no-overdraft rule is a database constraint so that no code path -
        # including a future one written by someone who has not read the
        # service layer - can drive a normal account negative.
        sa.CheckConstraint("balance_minor >= 0 OR allow_negative", name="ck_accounts_no_overdraft"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_accounts_currency_len"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # This unique index is what actually makes retries safe. Two concurrent
        # requests carrying the same key race here, and exactly one wins.
        sa.UniqueConstraint("idempotency_key", name="uq_transactions_idempotency_key"),
    )

    op.create_table(
        "entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("amount_minor <> 0", name="ck_entries_nonzero"),
    )

    # Serves the keyset pagination query directly: filter on account, walk id
    # backwards, no sort step.
    op.create_index(
        "ix_entries_account_id_desc",
        "entries",
        ["account_id", sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_entries_account_id_desc", table_name="entries")
    op.drop_table("entries")
    op.drop_table("transactions")
    op.drop_table("accounts")
