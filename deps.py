"""Shared FastAPI dependencies: limiter, templates, fmt filter."""
import os
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
templates = Jinja2Templates(directory="templates")

APP_URL = os.getenv("APP_URL", "https://content-market.up.railway.app")
templates.env.globals["APP_URL"] = APP_URL


def fmt(n):
    if n is None:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(int(n))


templates.env.filters["fmt"] = fmt
