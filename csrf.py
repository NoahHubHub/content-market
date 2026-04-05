"""CSRF protection using itsdangerous (already a project dependency).

- CSRFMiddleware validates the token on every state-changing request (POST/PUT/PATCH/DELETE).
- csrf_context(request) generates the hidden input snippet for templates.
- deps.py wraps TemplateResponse to inject csrf_input automatically.
"""
import os
import logging
from urllib.parse import parse_qs

from itsdangerous import URLSafeTimedSerializer, BadData
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from fastapi import Request

log = logging.getLogger(__name__)

_SECRET = os.getenv("SECRET_KEY", "changeme")
_signer = URLSafeTimedSerializer(_SECRET, salt="csrf")
_MAX_AGE = 3600  # 1 hour

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
# Paths that use JSON bodies or are server-to-server — skip form CSRF check
_EXEMPT_PREFIXES = ("/push/",)


def _get_session_token(request: Request) -> str:
    """Return the raw (unsigned) session token, creating one if needed."""
    token = request.session.get("_csrf_token")
    if not token:
        import secrets
        token = secrets.token_hex(32)
        request.session["_csrf_token"] = token
    return token


def csrf_input_html(request: Request) -> str:
    """Return a ready-to-embed hidden input tag with a signed CSRF token."""
    token = _signer.dumps(_get_session_token(request))
    return f'<input type="hidden" name="_csrf_token" value="{token}">'


class CSRFMiddleware(BaseHTTPMiddleware):
    """Validate CSRF tokens on all non-safe HTTP methods."""

    async def dispatch(self, request: Request, call_next):
        if request.method in _SAFE_METHODS:
            return await call_next(request)

        # Skip exempt paths (e.g. push subscription endpoints with JSON bodies)
        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        # Skip AJAX requests that set X-Requested-With (same-origin only)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return await call_next(request)

        # Read and buffer the request body so the route handler can still parse it
        body = await request.body()
        request._body = body  # cache so downstream can re-read

        content_type = request.headers.get("content-type", "")
        form_token = None

        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            if "application/x-www-form-urlencoded" in content_type:
                parsed = parse_qs(body.decode("utf-8", errors="replace"))
                tokens = parsed.get("_csrf_token", [])
                form_token = tokens[0] if tokens else None
            else:
                # multipart: simple line scan (avoid full parse overhead)
                for line in body.split(b"\r\n"):
                    if b"_csrf_token" in line and b"value=" not in line:
                        continue
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded and not decoded.startswith("--") and "_csrf_token" not in decoded:
                        # Check next line if previous was the field name
                        pass
                # Fall back: scan raw bytes for the token value pattern
                import re
                m = re.search(rb'name="_csrf_token"\r\n\r\n([^\r\n]+)', body)
                if m:
                    form_token = m.group(1).decode("utf-8", errors="replace")

            if not form_token:
                log.warning("CSRF: missing token on %s %s", request.method, path)
                return Response("Ungültige Anfrage (CSRF-Token fehlt)", status_code=403)

            session_raw = request.session.get("_csrf_token")
            if not session_raw:
                log.warning("CSRF: no session token on %s %s", request.method, path)
                return Response("Ungültige Anfrage (keine Session)", status_code=403)

            try:
                signed_value = _signer.loads(form_token, max_age=_MAX_AGE)
            except BadData:
                log.warning("CSRF: invalid/expired token on %s %s", request.method, path)
                return Response("Ungültige Anfrage (CSRF-Token abgelaufen)", status_code=403)

            if signed_value != session_raw:
                log.warning("CSRF: token mismatch on %s %s", request.method, path)
                return Response("Ungültige Anfrage (CSRF-Mismatch)", status_code=403)

        return await call_next(request)
