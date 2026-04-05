import logging
import os
import secrets

from fastapi import FastAPI, Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from database import Base, engine
from deps import limiter, templates
from csrf import CSRFMiddleware

import scheduler  # starts APScheduler and runs migrate() on import
from routers import auth, market, trading, portfolio, social, pwa, push, account, premium, admin

Base.metadata.create_all(bind=engine)
scheduler.migrate()

app = FastAPI(title="Clip Capital")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/static", StaticFiles(directory="static"), name="static")

_session_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
_redis_url   = os.getenv("REDIS_URL")

if _redis_url:
    # Redis-backed sessions — shared across workers, survives restarts
    try:
        from starlette_session import SessionMiddleware as RedisSessionMiddleware
        from starlette_session.backends import BackendType
        app.add_middleware(
            RedisSessionMiddleware,
            secret_key=_session_key,
            max_age=86400 * 30,
            backend_type=BackendType.redis,
            backend_url=_redis_url,
        )
        logging.getLogger(__name__).info("Sessions: Redis backend (%s)", _redis_url.split("@")[-1])
    except ImportError:
        logging.getLogger(__name__).warning(
            "REDIS_URL set but starlette-session not installed — falling back to cookie sessions. "
            "Add starlette-session to requirements.txt to enable Redis sessions."
        )
        app.add_middleware(SessionMiddleware, secret_key=_session_key, max_age=86400 * 30)
else:
    app.add_middleware(SessionMiddleware, secret_key=_session_key, max_age=86400 * 30)

app.add_middleware(CSRFMiddleware)


# ── error handlers ─────────────────────────────────────────────────────────────

@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request, "terms.html", {})


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(request, "error.html",
        {"code": 404, "title": "Seite nicht gefunden",
         "message": "Das Video, die Liga oder die Seite, die du suchst, existiert nicht (mehr)."},
        status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    return templates.TemplateResponse(request, "error.html",
        {"code": 500, "title": "Serverfehler",
         "message": "Etwas ist schiefgelaufen. Versuche es in einem Moment erneut."},
        status_code=500)


# ── routers ────────────────────────────────────────────────────────────────────

app.include_router(pwa.router)
app.include_router(auth.router)
app.include_router(market.router)
app.include_router(trading.router)
app.include_router(portfolio.router)
app.include_router(social.router)
app.include_router(push.router)
app.include_router(account.router)
app.include_router(premium.router)
app.include_router(admin.router)


# ── startup seed ───────────────────────────────────────────────────────────────

try:
    scheduler.seed_market()
    scheduler.generate_daily_drop()
except Exception:
    logging.getLogger(__name__).warning("startup seed failed", exc_info=True)
