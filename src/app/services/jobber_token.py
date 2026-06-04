# pattern: Functional Core
"""Pure helpers for reasoning about Jobber OAuth access-token freshness.

No I/O: the access token (a JWT) carries its own expiry, so freshness is a pure
function of the token string and the current time (passed in by the caller).
"""
from __future__ import annotations

import base64
import json


def jwt_exp(token: str) -> int | None:
    """Read the `exp` (epoch seconds) claim from a JWT without verifying its signature.

    Returns None for a malformed token or a missing/unparseable `exp` claim. We only
    need the expiry to decide *when* to refresh; the API verifies the signature for us.
    """
    try:
        segments = token.split(".")
        if len(segments) < 2:
            return None
        payload_b64 = segments[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        exp = claims.get("exp")
        return int(exp) if exp is not None else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def access_token_is_fresh(token: str, now_epoch: int, skew_seconds: int) -> bool:
    """True if the token's expiry is more than `skew_seconds` beyond `now_epoch`.

    The skew makes us refresh slightly early so an in-flight request never races the
    expiry boundary. A malformed token (no readable exp) is treated as stale.
    """
    exp = jwt_exp(token)
    if exp is None:
        return False
    return exp - skew_seconds > now_epoch
