"""Tests for the Jobber token-refresh shell (cache, refresh, rotation, fallback)."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services import jobber_auth

FAR_FUTURE = 9999999999  # year 2286 — always "fresh"


def _jwt(exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"hdr.{payload}.sig"


def _settings(**over):
    base = dict(
        jobber_client_id="cid",
        jobber_client_secret="csecret",
        jobber_refresh_token="rt-initial",
        jobber_access_token="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _resp(payload: dict, ok: bool = True) -> MagicMock:
    r = MagicMock()
    r.json.return_value = payload
    if not ok:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401)
        )
    return r


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    jobber_auth._cached_access_token = None
    with patch.object(jobber_auth, "_TOKEN_STORE", tmp_path / "jobber_token.json"):
        yield
    jobber_auth._cached_access_token = None


def test_refresh_returns_and_caches_access_token():
    with patch("app.services.jobber_auth.httpx.post") as post:
        post.return_value = _resp({"access_token": _jwt(FAR_FUTURE), "refresh_token": "rt-initial"})
        tok = jobber_auth.get_access_token(_settings())
        assert tok == _jwt(FAR_FUTURE)
        # second call returns the still-fresh cached token without re-POSTing
        assert jobber_auth.get_access_token(_settings()) == tok
        assert post.call_count == 1


def test_rotation_persists_new_refresh_token():
    with patch("app.services.jobber_auth.httpx.post") as post:
        post.return_value = _resp({"access_token": _jwt(FAR_FUTURE), "refresh_token": "rt-rotated"})
        jobber_auth.get_access_token(_settings())
        stored = json.loads(jobber_auth._TOKEN_STORE.read_text(encoding="utf-8"))
        assert stored["refresh_token"] == "rt-rotated"


def test_persisted_refresh_token_is_used_next_time():
    # seed the store with a rotated token; _load should prefer it over settings
    jobber_auth._TOKEN_STORE.write_text(json.dumps({"refresh_token": "rt-from-store"}), encoding="utf-8")
    with patch("app.services.jobber_auth.httpx.post") as post:
        post.return_value = _resp({"access_token": _jwt(FAR_FUTURE), "refresh_token": "rt-from-store"})
        jobber_auth.get_access_token(_settings(jobber_refresh_token="rt-initial"))
        sent = post.call_args.kwargs["json"]
        assert sent["refresh_token"] == "rt-from-store"
        assert sent["grant_type"] == "refresh_token"


def test_legacy_static_token_when_no_refresh_token():
    s = _settings(jobber_refresh_token="", jobber_access_token="static-token")
    with patch("app.services.jobber_auth.httpx.post") as post:
        assert jobber_auth.get_access_token(s) == "static-token"
        post.assert_not_called()


def test_no_credentials_raises():
    with pytest.raises(Exception):
        jobber_auth.get_access_token(_settings(jobber_refresh_token="", jobber_access_token=""))


def test_refresh_http_failure_raises():
    with patch("app.services.jobber_auth.httpx.post") as post:
        post.return_value = _resp({}, ok=False)
        with pytest.raises(httpx.HTTPStatusError):
            jobber_auth.get_access_token(_settings())


def test_invalidate_forces_new_refresh():
    with patch("app.services.jobber_auth.httpx.post") as post:
        post.return_value = _resp({"access_token": _jwt(FAR_FUTURE), "refresh_token": "rt-initial"})
        jobber_auth.get_access_token(_settings())
        jobber_auth.invalidate()
        jobber_auth.get_access_token(_settings())
        assert post.call_count == 2
