"""Minimal session-cookie admin auth.

There's a single credential — ADMIN_TOKEN — so the "session" is just an
HMAC-signed, expiring cookie keyed by that token: changing the token invalidates
all existing sessions, and no server-side session store is needed. SameSite=Lax
gives reasonable CSRF protection for the admin POST forms.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request

from app.config import get_settings

COOKIE_NAME = "auj_admin"


def _sign(msg: str) -> str:
    key = (get_settings().admin_token or "").encode()
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def make_cookie() -> str:
    exp = int(time.time()) + get_settings().session_ttl_hours * 3600
    return f"{exp}.{_sign('admin:' + str(exp))}"


def _cookie_valid(value: str | None) -> bool:
    if not value or not get_settings().admin_token:
        return False
    try:
        exp_s, sig = value.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if time.time() > exp:
        return False
    return hmac.compare_digest(sig, _sign("admin:" + str(exp)))


def is_admin(request: Request) -> bool:
    return _cookie_valid(request.cookies.get(COOKIE_NAME))


def require_admin(request: Request) -> None:
    """FastAPI dependency: allow the request only for a logged-in admin, else
    redirect to the login page (307 preserves method for GETs)."""
    if not is_admin(request):
        raise HTTPException(status_code=307, headers={"Location": "/admin/login"})
