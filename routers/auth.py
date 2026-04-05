from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

import models
from database import get_db
from deps import limiter, templates
from helpers import get_login, hash_pw, verify_pw

_MAX_ATTEMPTS = 5
_LOCK_MINUTES = 15
_MIN_PW_SCORE = 2  # zxcvbn score 0–4; 2 = "fair"


def _check_password_strength(password: str, username: str) -> str | None:
    """Returns an error message if the password is too weak, None if OK."""
    try:
        from zxcvbn import zxcvbn
        result = zxcvbn(password, user_inputs=[username])
        if result["score"] < _MIN_PW_SCORE:
            suggestions = result["feedback"].get("suggestions", [])
            hint = suggestions[0] if suggestions else "Wähle ein stärkeres Passwort."
            return f"Passwort zu schwach. {hint}"
    except ImportError:
        pass  # zxcvbn not installed — fall back to length-only check
    return None


def _audit(db: Session, request: Request, action: str, user=None):
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else None)
    db.add(models.AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else None,
        action=action,
        ip_address=ip,
    ))
    db.commit()

router = APIRouter()


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"user": None, "error": None})


@router.post("/register", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def register(request: Request, username: str = Form(...), password: str = Form(...),
                   db: Session = Depends(get_db)):
    if len(username) < 3:
        return templates.TemplateResponse(request, "register.html",
            {"user": None, "error": "Username zu kurz (min. 3 Zeichen)"})
    if len(password) < 12:
        return templates.TemplateResponse(request, "register.html",
            {"user": None, "error": "Passwort zu kurz (min. 12 Zeichen)"})
    pw_error = _check_password_strength(password, username)
    if pw_error:
        return templates.TemplateResponse(request, "register.html",
            {"user": None, "error": pw_error})
    if db.query(models.User).filter(models.User.username == username).first():
        return templates.TemplateResponse(request, "register.html",
            {"user": None, "error": "Username bereits vergeben"})
    db_user = models.User(username=username, password_hash=hash_pw(password), balance=10000.0)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    request.session.clear()
    request.session["user_id"] = db_user.id
    return RedirectResponse("/", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    next_url = request.query_params.get("next", "")
    return templates.TemplateResponse(request, "login.html", {"user": None, "error": None, "next": next_url})


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def login(request: Request, username: str = Form(...), password: str = Form(...),
                next: str = Form(default=""), db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.username == username).first()

    # Account lockout check
    if db_user and db_user.locked_until and datetime.utcnow() < db_user.locked_until:
        remaining = int((db_user.locked_until - datetime.utcnow()).total_seconds() // 60) + 1
        return templates.TemplateResponse(request, "login.html",
            {"user": None, "error": f"Account gesperrt. Versuche es in {remaining} Minuten erneut.", "next": next})

    if not db_user or not verify_pw(password, db_user.password_hash):
        if db_user:
            db_user.failed_login_attempts = (db_user.failed_login_attempts or 0) + 1
            if db_user.failed_login_attempts >= _MAX_ATTEMPTS:
                db_user.locked_until = datetime.utcnow() + timedelta(minutes=_LOCK_MINUTES)
                db_user.failed_login_attempts = 0
            db.commit()
            _audit(db, request, "login_failed", db_user)
        return templates.TemplateResponse(request, "login.html",
            {"user": None, "error": "Ungültige Zugangsdaten", "next": next})

    # Successful login — reset lockout counters
    db_user.failed_login_attempts = 0
    db_user.locked_until = None
    db.commit()
    _audit(db, request, "login", db_user)

    request.session.clear()
    request.session["user_id"] = db_user.id
    # Only allow relative redirects to prevent open redirect
    if next and next.startswith("/") and not next.startswith("//"):
        return RedirectResponse(next, status_code=302)
    return RedirectResponse("/", status_code=302)


@router.post("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if db_user:
        _audit(db, request, "logout", db_user)
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@router.post("/vy/dismiss")
async def vy_dismiss(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if db_user and (db_user.tutorial_step or 0) == 0:
        db_user.tutorial_step = 1
        db.commit()
    return RedirectResponse("/", status_code=302)


@router.post("/tutorial/next")
async def tutorial_next(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    step = db_user.tutorial_step or 0
    # Allow advancing through trading tutorial (0–98→99) AND social onboarding (99→100)
    if db_user and step <= 99:
        db_user.tutorial_step = step + 1
        db.commit()
    return RedirectResponse(request.headers.get("referer", "/"), status_code=302)
