from __future__ import annotations

import json
import logging
import pathlib
import threading

from cleanco import basename
from rapidfuzz import fuzz, utils as rfu

_VENDORS_PATH = pathlib.Path(__file__).parent.parent / "data" / "vendors.json"
_FUZZY_THRESHOLD = 88.0
_lock = threading.Lock()


def _load_vendors() -> dict[str, str]:
    try:
        return json.loads(_VENDORS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_vendors(mapping: dict[str, str]) -> None:
    _VENDORS_PATH.write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _strip_suffix(name: str) -> str:
    stripped = basename(name)
    if stripped:
        return stripped
    return name


def normalize(vendor_raw: str, anthropic_api_key: str = "") -> str:
    """Resolve vendor_raw to canonical name via 3-layer cascade.

    Always returns a non-empty string (falls back to vendor_raw).
    """
    if not vendor_raw or not vendor_raw.strip():
        return vendor_raw

    mapping = _load_vendors()

    # Layer 1: exact lookup (case-sensitive)
    if vendor_raw in mapping:
        return mapping[vendor_raw]

    # Layer 1b: case-insensitive exact lookup
    lower = vendor_raw.lower()
    for raw, canonical in mapping.items():
        if raw.lower() == lower:
            return canonical

    # Layer 2: fuzzy match using cleanco strip + token_sort_ratio
    stripped_query = _strip_suffix(vendor_raw)
    canonical_names = sorted(set(mapping.values()))

    best_score = 0.0
    best_match: str | None = None
    for canonical in canonical_names:
        stripped_canonical = _strip_suffix(canonical)
        score = fuzz.token_sort_ratio(
            stripped_query,
            stripped_canonical,
            processor=rfu.default_process,
        )
        if score > best_score:
            best_score = score
            best_match = canonical

    if best_score >= _FUZZY_THRESHOLD and best_match is not None:
        return best_match

    # Layer 3: LLM fallback
    if anthropic_api_key:
        llm_result = _llm_resolve(vendor_raw, canonical_names, anthropic_api_key)
        if llm_result:
            with _lock:
                mapping = _load_vendors()
                mapping[vendor_raw] = llm_result
                _save_vendors(mapping)
            return llm_result

    # Layer 4: fallback to raw name
    return vendor_raw


def _llm_resolve(
    vendor_raw: str,
    known_canonicals: list[str],
    api_key: str,
) -> str | None:
    """Ask Claude to resolve a novel vendor name.

    Returns canonical name string or None on failure.
    """
    import anthropic
    from app.config import CLAUDE_MODEL

    known_str = "\n".join(f"- {c}" for c in known_canonicals[:50])
    prompt = (
        f"I have a vendor name: '{vendor_raw}'\n\n"
        f"Known canonical vendor names:\n{known_str}\n\n"
        "If this vendor matches one of the known canonicals (accounting for typos, "
        "abbreviations, or legal suffixes), return ONLY the exact canonical name with no "
        "explanation.\n"
        "If this is a completely new vendor not in the list, return ONLY a clean, "
        "normalized version of the vendor name (remove legal suffixes like LLC, Inc., Corp., "
        "but keep the meaningful name). Return ONLY the name, nothing else."
    )
    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip()
        return result if result else None
    except Exception as e:
        logging.warning(f"LLM vendor resolution failed for '{vendor_raw}': {e}")
        return None
