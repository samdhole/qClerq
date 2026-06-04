# pattern: Imperative Shell
"""Jobber OAuth token manager: trade the durable refresh token for short-lived
access tokens, transparently, with rotation-safe persistence.

Jobber access tokens live ~60 minutes; the refresh token is the durable credential.
With refresh-token rotation ON (required to publish in Jobber's marketplace) every
refresh returns a NEW, single-use refresh token that MUST be persisted immediately —
reusing a spent one triggers reuse-detection and revokes the whole chain. So we:

  * cache the access token in memory and refresh proactively (a skew before expiry),
  * persist any rotated refresh token atomically to a writable store the app owns
    (NOT .env, which is read once at boot and not safely rewritable), and
  * serialise refreshes behind a lock (single-flight) so two concurrent syncs can't
    both consume the refresh token and lock each other out.

Single-worker assumption (same as vendor_matcher): a threading.Lock suffices. A
multi-worker deployment would need a shared store + distributed lock.

If no refresh token is configured, falls back to the legacy static
`JOBBER_ACCESS_TOKEN` so existing setups keep working until they wire the refresh flow.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
import time
from typing import Any

import httpx

from app.services.jobber_token import access_token_is_fresh

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.getjobber.com/api/oauth/token"
_TOKEN_STORE = pathlib.Path(__file__).parent.parent / "data" / "jobber_token.json"
_SKEW_SECONDS = 120  # refresh this long before the token actually expires

_lock = threading.Lock()
_cached_access_token: str | None = None


def _load_refresh_token(settings: Any) -> str | None:
    """The current refresh token: the rotated one from the store if present, else the
    seed value from settings. The store wins because rotation makes it newer."""
    try:
        data = json.loads(_TOKEN_STORE.read_text(encoding="utf-8"))
        stored = data.get("refresh_token")
        if stored:
            return stored
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return getattr(settings, "jobber_refresh_token", "") or None


def _save_refresh_token(refresh_token: str) -> None:
    """Persist the rotated refresh token atomically (temp file + os.replace)."""
    _TOKEN_STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TOKEN_STORE.with_name(_TOKEN_STORE.name + ".tmp")
    tmp.write_text(json.dumps({"refresh_token": refresh_token}), encoding="utf-8")
    os.replace(tmp, _TOKEN_STORE)


def _refresh(settings: Any, refresh_token: str) -> str:
    """Exchange the refresh token for a fresh access token; persist a rotated refresh
    token if the response returns a new one. Returns the access token. Raises on failure."""
    resp = httpx.post(
        _TOKEN_URL,
        json={
            "client_id": getattr(settings, "jobber_client_id", ""),
            "client_secret": getattr(settings, "jobber_client_secret", ""),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("jobber token refresh returned no access_token")

    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        # rotation: the old token is now spent — save the new one before anything uses it
        _save_refresh_token(new_refresh)
        logger.info("jobber refresh token rotated and persisted")

    return access_token


def get_access_token(settings: Any) -> str:
    """Return a valid Jobber access token, refreshing transparently when needed."""
    global _cached_access_token

    if _cached_access_token and access_token_is_fresh(
        _cached_access_token, int(time.time()), _SKEW_SECONDS
    ):
        return _cached_access_token

    refresh_token = _load_refresh_token(settings)
    if not refresh_token:
        # Legacy path: no refresh token wired — use the static access token as-is.
        static = getattr(settings, "jobber_access_token", "")
        if static:
            return static
        raise RuntimeError(
            "no Jobber credentials configured (set JOBBER_REFRESH_TOKEN, or JOBBER_ACCESS_TOKEN)"
        )

    with _lock:
        # Another thread may have refreshed while we waited for the lock.
        if _cached_access_token and access_token_is_fresh(
            _cached_access_token, int(time.time()), _SKEW_SECONDS
        ):
            return _cached_access_token
        _cached_access_token = _refresh(settings, refresh_token)
        return _cached_access_token


def invalidate() -> None:
    """Drop the cached access token so the next call refreshes. Use after a 401."""
    global _cached_access_token
    _cached_access_token = None
