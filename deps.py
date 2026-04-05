"""Shared FastAPI dependencies: limiter, templates, fmt filter."""
import os
from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

APP_URL = os.getenv("APP_URL", "https://clip-capital.up.railway.app")

_base_templates = Jinja2Templates(directory="templates")
_base_templates.env.globals["APP_URL"] = APP_URL


def fmt(n):
    if n is None:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(int(n))


_base_templates.env.filters["fmt"] = fmt


class _CSRFTemplates:
    """Thin wrapper around Jinja2Templates that auto-injects csrf_input."""

    def __init__(self, base: Jinja2Templates):
        self._base = base
        # Expose env so callers can add globals/filters the normal way
        self.env = base.env

    def TemplateResponse(self, request: Request, name: str, context: dict = None, **kwargs):
        from csrf import csrf_input_html
        ctx = context or {}
        ctx.setdefault("csrf_input", csrf_input_html(request))
        return self._base.TemplateResponse(request, name, ctx, **kwargs)


templates = _CSRFTemplates(_base_templates)
