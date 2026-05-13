from __future__ import annotations

import hashlib
from typing import Literal


def compute_hash(pdf_bytes: bytes) -> str:
    """Compute SHA-256 hex digest of PDF bytes."""
    return hashlib.sha256(pdf_bytes).hexdigest()


def check_duplicate(
    file_hash: str,
    known_hashes: set[str],
) -> Literal["none", "possible", "likely"]:
    """Check file_hash against known_hashes set.

    Returns:
        "likely"   — hash is an exact match (same file submitted before)
        "none"     — hash not found in known_hashes
    """
    if file_hash in known_hashes:
        return "likely"
    return "none"
