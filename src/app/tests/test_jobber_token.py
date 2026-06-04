"""Tests for the pure Jobber token-freshness core (no I/O)."""
from __future__ import annotations

import base64
import json

from app.services.jobber_token import access_token_is_fresh, jwt_exp


def _jwt(exp: int | None) -> str:
    """Build a fake JWT (header.payload.sig) carrying the given exp claim."""
    claims: dict = {} if exp is None else {"exp": exp}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"hdr.{payload}.sig"


def test_jwt_exp_reads_exp_claim():
    assert jwt_exp(_jwt(1780609365)) == 1780609365


def test_jwt_exp_returns_none_for_missing_claim():
    assert jwt_exp(_jwt(None)) is None


def test_jwt_exp_returns_none_for_malformed_token():
    assert jwt_exp("not-a-jwt") is None
    assert jwt_exp("") is None
    assert jwt_exp("a.b") is None  # only two segments


def test_fresh_when_exp_beyond_skew_window():
    now = 1000
    assert access_token_is_fresh(_jwt(now + 600), now, 120) is True


def test_stale_when_already_expired():
    now = 1000
    assert access_token_is_fresh(_jwt(now - 10), now, 120) is False


def test_stale_when_inside_skew_window():
    # exp is only 60s ahead but skew is 120s -> refresh early, treat as stale
    now = 1000
    assert access_token_is_fresh(_jwt(now + 60), now, 120) is False


def test_stale_when_token_malformed():
    assert access_token_is_fresh("garbage", 1000, 120) is False
