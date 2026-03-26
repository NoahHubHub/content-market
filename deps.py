"""Shared FastAPI dependencies: limiter, templates, fmt filter."""
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
templates = Jinja2Templates(directory="templates")


def fmt(n):
    if n is None:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(int(n))


templates.env.filters["fmt"] = fmt
