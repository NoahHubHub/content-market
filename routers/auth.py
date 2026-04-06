import base64
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

import models
from database import get_db
from deps import limiter, templates
from helpers import get_login, hash_pw, verify_pw

# ── OAuth constants ────────────────────────────────────────────────────────────

_GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_INFO_URL  = "https://www.googleapis.com/oauth2/v2/userinfo"
# Only identity scopes — youtube.readonly is NOT requested because the app
# uses YOUTUBE_API_KEY (server-side API key) for all YouTube data access.
# No user YouTube data is accessed on behalf of the user.
_SCOPES = " ".join([
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
])


def _fernet() -> Fernet:
    """Derive a Fernet key from SECRET_KEY."""
    secret = os.getenv("SECRET_KEY", "dev-secret-not-for-production")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def _google_redirect_uri(request: Request) -> str:
    app_url = os.getenv("APP_URL", "").rstrip("/")
    if app_url:
        return f"{app_url}/auth/google/callback"
    # Fall back to deriving from request (works in local dev)
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/google/callback"


def _make_username_from_email(email: str, db: Session) -> str:
    """Generate a unique username from a Google email address."""
    base = re.sub(r"[^a-zA-Z0-9_]", "", email.split("@")[0])[:20] or "user"
    if len(base) < 3:
        base = base + "user"
    candidate = base
    counter = 1
    while db.query(models.User).filter(models.User.username == candidate).first():
        candidate = f"{base}{counter}"
        counter += 1
    return candidate

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
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    consent: str = Form(default=""),
    db: Session = Depends(get_db),
):
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
    if consent != "on":
        return templates.TemplateResponse(request, "register.html",
            {"user": None, "error": "Bitte stimme den Nutzungsbedingungen und der Datenschutzerklärung zu."})
    if db.query(models.User).filter(models.User.username == username).first():
        return templates.TemplateResponse(request, "register.html",
            {"user": None, "error": "Username bereits vergeben"})
    now = datetime.utcnow()
    db_user = models.User(
        username=username,
        password_hash=hash_pw(password),
        balance=10000.0,
        consent_accepted=True,
        consent_at=now,
    )
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


# ── Google OAuth 2.0 ──────────────────────────────────────────────────────────

@router.get("/auth/google/login")
async def google_login(request: Request):
    """Redirect to Google's OAuth 2.0 consent screen."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id:
        return templates.TemplateResponse(request, "error.html", {
            "code": 503,
            "title": "Google-Login nicht konfiguriert",
            "message": "GOOGLE_CLIENT_ID ist nicht gesetzt. Bitte wende dich an den Betreiber.",
        }, status_code=503)

    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state

    params = {
        "client_id":     client_id,
        "redirect_uri":  _google_redirect_uri(request),
        "response_type": "code",
        "scope":         _SCOPES,
        "state":         state,
        "access_type":   "offline",   # request refresh token
        "prompt":        "select_account",
    }
    url = f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"
    return RedirectResponse(url, status_code=302)


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str = None,
    state: str = None,
    error: str = None,
):
    """Handle the OAuth 2.0 callback from Google."""
    if error:
        return templates.TemplateResponse(request, "error.html", {
            "code": 403,
            "title": "Google-Login abgebrochen",
            "message": "Du hast den Zugriff abgelehnt oder es ist ein Fehler aufgetreten.",
        }, status_code=403)

    # CSRF check
    expected_state = request.session.pop("oauth_state", None)
    if not state or state != expected_state:
        return templates.TemplateResponse(request, "error.html", {
            "code": 400,
            "title": "Ungültige Anfrage",
            "message": "OAuth-State ungültig. Bitte versuche es erneut.",
        }, status_code=400)

    client_id     = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return templates.TemplateResponse(request, "error.html", {
            "code": 503,
            "title": "Google-Login nicht konfiguriert",
            "message": "OAuth-Credentials fehlen. Bitte wende dich an den Betreiber.",
        }, status_code=503)

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(_GOOGLE_TOKEN_URL, data={
            "code":          code,
            "client_id":     client_id,
            "client_secret": client_secret,
            "redirect_uri":  _google_redirect_uri(request),
            "grant_type":    "authorization_code",
        })
    if token_resp.status_code != 200:
        return templates.TemplateResponse(request, "error.html", {
            "code": 502,
            "title": "Token-Austausch fehlgeschlagen",
            "message": "Google hat keinen gültigen Token zurückgegeben. Bitte versuche es erneut.",
        }, status_code=502)

    token_data   = token_resp.json()
    access_token = token_data.get("access_token")
    expires_in   = token_data.get("expires_in", 3600)
    token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
    # Refresh token is NOT stored (Option A — not needed for identity-only OAuth)

    # Fetch user info
    async with httpx.AsyncClient() as client:
        info_resp = await client.get(
            _GOOGLE_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if info_resp.status_code != 200:
        return templates.TemplateResponse(request, "error.html", {
            "code": 502,
            "title": "Nutzerinfo nicht abrufbar",
            "message": "Google hat keine Nutzerinformationen zurückgegeben. Bitte versuche es erneut.",
        }, status_code=502)

    info      = info_resp.json()
    google_id = info.get("id")
    email     = info.get("email", "")

    # Check if user already exists
    db_user = db.query(models.User).filter(models.User.google_id == google_id).first()
    if db_user is None and email:
        db_user = db.query(models.User).filter(models.User.google_email == email).first()
    is_returning = db_user is not None and db_user.consent_accepted

    if is_returning:
        # Existing user who already gave consent — update token + log in directly
        db_user.google_access_token = _encrypt(access_token)
        db_user.google_token_expiry = token_expiry
        if not db_user.google_id:
            db_user.google_id = google_id
        db.commit()
        _audit(db, request, "google_login", db_user)
        request.session.clear()
        request.session["user_id"] = db_user.id
        return RedirectResponse("/", status_code=302)

    # New user or user who hasn't consented yet:
    # Store OAuth data in session temporarily — nothing written to DB until consent given
    request.session["pending_oauth"] = {
        "google_id":      google_id,
        "email":          email,
        "access_token":   _encrypt(access_token),   # encrypt before storing in session
        "token_expiry":   token_expiry.isoformat(),
        "is_new":         db_user is None,
    }
    return RedirectResponse("/auth/token-consent", status_code=302)


@router.get("/auth/token-consent", response_class=HTMLResponse)
async def token_consent_page(request: Request):
    """Explicit consent before any data is written to DB — shown before T&C consent."""
    pending = request.session.get("pending_oauth")
    if not pending:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "token_consent.html", {
        "user": None,
        "email": pending.get("email", ""),
    })


@router.post("/auth/token-consent")
async def token_consent_accept(
    request: Request,
    db: Session = Depends(get_db),
    consent: str = Form(default=""),
):
    """User explicitly consents to token storage — NOW create/update user in DB."""
    pending = request.session.get("pending_oauth")
    if not pending:
        return RedirectResponse("/login", status_code=302)

    if consent != "on":
        return templates.TemplateResponse(request, "token_consent.html", {
            "user": None,
            "email": pending.get("email", ""),
            "error": "Zustimmung erforderlich um fortzufahren.",
        })

    google_id    = pending["google_id"]
    email        = pending["email"]
    access_token = pending["access_token"]   # already encrypted
    token_expiry = datetime.fromisoformat(pending["token_expiry"])

    # Now find or create user
    db_user = db.query(models.User).filter(models.User.google_id == google_id).first()
    if db_user is None and email:
        db_user = db.query(models.User).filter(models.User.google_email == email).first()

    is_new = db_user is None
    if is_new:
        username = _make_username_from_email(email, db)
        db_user = models.User(
            username=username,
            password_hash=None,
            balance=10000.0,
            google_id=google_id,
            google_email=email,
        )
        db.add(db_user)
        db.flush()
    else:
        if not db_user.google_id:
            db_user.google_id = google_id

    db_user.google_access_token = access_token
    db_user.google_token_expiry = token_expiry
    if email and not db_user.google_email:
        db_user.google_email = email

    db.commit()
    db.refresh(db_user)

    _audit(db, request, "token_consent_accepted", db_user)

    # Clear pending data, set session
    request.session.pop("pending_oauth", None)
    request.session["user_id"] = db_user.id

    # Still need T&C consent?
    if is_new or not db_user.consent_accepted:
        return RedirectResponse("/auth/consent", status_code=302)
    return RedirectResponse("/", status_code=302)


@router.get("/auth/consent", response_class=HTMLResponse)
async def consent_page(request: Request, db: Session = Depends(get_db)):
    """Show T&C + Privacy consent page."""
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    if db_user.consent_accepted:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "consent.html", {"user": db_user})


@router.post("/auth/consent")
async def consent_accept(
    request: Request,
    db: Session = Depends(get_db),
    consent: str = Form(default=""),
):
    """Record T&C acceptance."""
    db_user = get_login(request, db)
    if not db_user:
        return RedirectResponse("/login", status_code=302)
    if consent != "on":
        return templates.TemplateResponse(request, "consent.html", {
            "user": db_user,
            "error": "Du musst den Nutzungsbedingungen zustimmen, um fortzufahren.",
        })
    db_user.consent_accepted = True
    db_user.consent_at = datetime.utcnow()
    db.commit()
    _audit(db, request, "consent_accepted", db_user)
    return RedirectResponse("/", status_code=302)
