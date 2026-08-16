from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import require_api_key
from app.config import get_settings
from app.errors import LedgerError
from app.ratelimit import TokenBucket
from app.routers import accounts, transfers

settings = get_settings()
bucket = TokenBucket(settings.rate_limit_per_minute, settings.rate_limit_burst)

app = FastAPI(
    title="Ledger Service",
    version="1.0.0",
    description="Double-entry ledger with idempotent transfers.",
)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


async def rate_limit(api_key: str = Depends(require_api_key)) -> None:
    bucket.check(api_key)


@app.exception_handler(LedgerError)
async def ledger_error_handler(_: Request, exc: LedgerError) -> JSONResponse:
    """One shape for every domain error, so clients can branch on `code`."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def console() -> FileResponse:
    """A same-origin demo console for the ledger API."""
    return FileResponse(static_dir / "index.html")


# Auth and the rate limiter are router-level dependencies, so a new endpoint is
# protected by default rather than protected only if someone remembers.
app.include_router(accounts.router, dependencies=[Depends(rate_limit)])
app.include_router(transfers.router, dependencies=[Depends(rate_limit)])
