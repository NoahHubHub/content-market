import os
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from database import Base, engine
from deps import limiter, templates

import scheduler  # starts APScheduler and runs migrate() on import
from routers import auth, market, trading, portfolio, social, pwa, push, account

Base.metadata.create_all(bind=engine)
scheduler.migrate()

app = FastAPI(title="Content Market")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/static", StaticFiles(directory="static"), name="static")

_session_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=_session_key, max_age=86400 * 30)


# ── error handlers ─────────────────────────────────────────────────────────────

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse("error.html",
        {"request": request, "code": 404, "title": "Seite nicht gefunden",
         "message": "Das Video, die Liga oder die Seite, die du suchst, existiert nicht (mehr)."},
        status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    return templates.TemplateResponse("error.html",
        {"request": request, "code": 500, "title": "Serverfehler",
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


# ── startup seed ───────────────────────────────────────────────────────────────

try:
    scheduler.seed_market()
    scheduler.generate_daily_drop()
except Exception:
    pass
