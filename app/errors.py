class LedgerError(Exception):
    """Domain error carrying the HTTP status it should surface as."""

    status_code = 400
    code = "ledger_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AccountNotFound(LedgerError):
    status_code = 404
    code = "account_not_found"


class CurrencyMismatch(LedgerError):
    status_code = 422
    code = "currency_mismatch"


class InsufficientFunds(LedgerError):
    status_code = 422
    code = "insufficient_funds"


class InvalidTransfer(LedgerError):
    status_code = 422
    code = "invalid_transfer"


class IdempotencyConflict(LedgerError):
    """Same key replayed with a different body - never silently reinterpret it."""

    status_code = 409
    code = "idempotency_conflict"
