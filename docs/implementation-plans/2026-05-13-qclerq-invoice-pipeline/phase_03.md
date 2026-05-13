# qClerq Invoice Pipeline — Phase 3: Vendor Normalization

**Goal:** Resolve raw vendor strings to canonical names via a 3-layer cascade (exact lookup → fuzzy → LLM fallback), with automatic promotion of LLM-resolved names to the local lookup table.

**Architecture:** `vendor_matcher.py` holds the cascade. Layer 1: dict lookup against `src/app/data/vendors.json`. Layer 2: `cleanco.basename()` to strip legal suffixes then `rapidfuzz.fuzz.token_sort_ratio` ≥ 88 against known canonical names. Layer 3: Claude LLM resolves novel vendors and promotes the result back to `vendors.json`. `vendor_normalized` is never empty — falls back to `vendor_raw` if all layers fail.

**Tech Stack:** Python 3.11+, `rapidfuzz>=3.9.0`, `cleanco>=2.2`, `anthropic>=0.101.0`, pytest with `unittest.mock`

**Scope:** Phase 3 of 8

**Codebase verified:** 2026-05-13

---

## Acceptance Criteria Coverage

### qclerq-invoice-pipeline.AC5: Vendor normalization
- **qclerq-invoice-pipeline.AC5.1 Success:** Known vendor (in vendors.json) resolves to canonical name without LLM call
- **qclerq-invoice-pipeline.AC5.2 Success:** Near-match vendor (typo/suffix variation) resolves via rapidfuzz at ≥88 score
- **qclerq-invoice-pipeline.AC5.3 Success:** Novel vendor resolved by LLM fallback and promoted to vendors.json
- **qclerq-invoice-pipeline.AC5.4 Edge:** vendor_normalized is never empty — falls back to vendor_raw if all layers fail

---

## Discrepancy Notes

- `src/app/data/` directory does NOT exist — must be created.
- `src/app/data/vendors.json` does NOT exist — must be created with ~20 seed entries.
- `src/app/CONTEXT.md` mentions "local CSV fallback" but design plan specifies `vendors.json`. Use `vendors.json`. Update CONTEXT.md line 53 after this phase.
- `cleanco.prepare_default_terms()` and `CompanyDesignator` do NOT exist in cleanco v2.x. Use `from cleanco import basename` only.
- `rapidfuzz.fuzz.token_sort_ratio` returns `float` 0–100 (not 0.0–1.0). Threshold 88 means `score >= 88.0`.
- `rapidfuzz` v3.x does NOT apply preprocessing by default — pass `processor=rapidfuzz.utils.default_process` to lowercase and strip punctuation before comparing.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Create src/app/data/ directory and vendors.json seed file

**Verifies:** qclerq-invoice-pipeline.AC5.1 (exact lookup against vendors.json)

**Files:**
- Create: `src/app/data/vendors.json`

**Step 1: Create the data directory and seed file**

```bash
mkdir src/app/data
```

Write `src/app/data/vendors.json`:

```json
{
  "Acme Corp LLC": "Acme Corp",
  "Acme Corp.": "Acme Corp",
  "ACME CORPORATION": "Acme Corp",
  "Home Depot": "The Home Depot",
  "Home Depot Inc": "The Home Depot",
  "The Home Depot Inc.": "The Home Depot",
  "Lowes": "Lowe's",
  "Lowe's Companies Inc": "Lowe's",
  "Amazon": "Amazon.com",
  "Amazon.com Inc": "Amazon.com",
  "Amazon Web Services": "Amazon Web Services",
  "AWS": "Amazon Web Services",
  "Grainger": "W.W. Grainger",
  "W.W. Grainger Inc.": "W.W. Grainger",
  "Fastenal": "Fastenal Company",
  "Fastenal Co": "Fastenal Company",
  "Office Depot": "Office Depot",
  "Office Depot Inc.": "Office Depot",
  "Staples": "Staples Inc",
  "Staples Inc.": "Staples Inc",
  "UPS": "UPS",
  "United Parcel Service": "UPS",
  "FedEx": "FedEx",
  "Federal Express": "FedEx"
}
```

**Step 2: Verify file is valid JSON**

```bash
python -c "import json; data = json.loads(open('src/app/data/vendors.json').read()); print(len(data), 'entries')"
```

Expected: prints `24 entries` (or the count you wrote)

**Step 3: Commit**

```bash
git add src/app/data/vendors.json
git commit -m "feat: add vendors.json seed file with common vendor name mappings"
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Implement src/app/services/vendor_matcher.py

**Verifies:** qclerq-invoice-pipeline.AC5.1, qclerq-invoice-pipeline.AC5.2, qclerq-invoice-pipeline.AC5.3, qclerq-invoice-pipeline.AC5.4

**Files:**
- Modify: `src/app/services/vendor_matcher.py` (currently empty stub)

**Implementation:**

Three-layer cascade:
1. Exact dict lookup (case-sensitive first, then case-insensitive)
2. `cleanco.basename()` strip → `rapidfuzz.fuzz.token_sort_ratio` ≥ 88 against all canonical values
3. Claude LLM call → result promoted to `vendors.json`
4. Fallback: return `vendor_raw` unchanged

```python
from __future__ import annotations

import json
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
    canonical_names = list(set(mapping.values()))

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
            model="claude-sonnet-4-6",
            max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip()
        return result if result else None
    except Exception:
        return None
```

**Step 1: Write the implementation above to `src/app/services/vendor_matcher.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.services.vendor_matcher import normalize; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/services/vendor_matcher.py
git commit -m "feat: implement vendor normalization cascade (exact, fuzzy, LLM, fallback)"
```
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-4) -->

<!-- START_TASK_3 -->
### Task 3: Add vendor normalization tests to test_extraction_goldens.py

**Verifies:** qclerq-invoice-pipeline.AC5.1, qclerq-invoice-pipeline.AC5.2, qclerq-invoice-pipeline.AC5.3, qclerq-invoice-pipeline.AC5.4

**Files:**
- Modify: `src/app/tests/test_extraction_goldens.py` (append vendor normalization tests)

**What to test:**
- `AC5.1`: `normalize("Acme Corp LLC")` returns `"Acme Corp"` — no LLM call (exact match)
- `AC5.1`: `normalize("ACME CORPORATION")` returns `"Acme Corp"` — case-insensitive match
- `AC5.2`: `normalize("Acme Corpp")` (typo) returns `"Acme Corp"` — fuzzy match at ≥88
- `AC5.3`: Novel vendor triggers `_llm_resolve`, result is promoted to `vendors.json`, returns LLM result — mock `anthropic.Anthropic` call
- `AC5.4`: When LLM returns `None` (mock failure), `normalize()` returns the original `vendor_raw` unchanged
- `AC5.4`: Empty string input returns empty string unchanged

Use `tmp_path` pytest fixture to avoid writing to the real `vendors.json` during tests. Patch `src.app.services.vendor_matcher._VENDORS_PATH` to point to a temp file.

**Step 1: Append vendor normalization test functions to `src/app/tests/test_extraction_goldens.py`**

Key test structure:
```python
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from src.app.services.vendor_matcher import normalize


@pytest.fixture
def vendor_file(tmp_path):
    data = {
        "Acme Corp LLC": "Acme Corp",
        "ACME CORPORATION": "Acme Corp",
        "The Home Depot Inc.": "The Home Depot",
    }
    p = tmp_path / "vendors.json"
    p.write_text(json.dumps(data))
    return p


# ... test functions that patch vendor_matcher._VENDORS_PATH to vendor_file
```

**Step 2: Run tests**

```bash
pytest src/app/tests/test_extraction_goldens.py -v -k "vendor"
```

Expected: All vendor normalization tests pass.

**Step 3: Run full test file to confirm nothing broken**

```bash
pytest src/app/tests/test_extraction_goldens.py -v
```

Expected: All tests pass.

**Step 4: Commit**

```bash
git add src/app/tests/test_extraction_goldens.py
git commit -m "test: add vendor normalization tests (AC5.1-AC5.4)"
```
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Update src/app/CONTEXT.md — fix CSV reference

**Verifies:** None

**Files:**
- Modify: `src/app/CONTEXT.md` line 53 — replace "local CSV fallback" with `vendors.json` reference

**Step 1: Update line 53 of src/app/CONTEXT.md**

Find the line:
```
`vendor_matcher.py` needs a seed `Vendor Map` loaded from the Google Sheet (or a local CSV fallback) on startup
```

Replace with:
```
`vendor_matcher.py` loads from `src/app/data/vendors.json` on every call; LLM-resolved matches are auto-promoted back to this file
```

**Step 2: Commit**

```bash
git add src/app/CONTEXT.md
git commit -m "docs: update CONTEXT.md vendor_matcher note to reflect vendors.json approach"
```
<!-- END_TASK_4 -->

<!-- END_SUBCOMPONENT_B -->

---

## Phase 3 Done When

- `python -c "from src.app.services.vendor_matcher import normalize; print(normalize('Acme Corp LLC'))"` prints `Acme Corp`
- `pytest src/app/tests/test_extraction_goldens.py -v` passes (all vendor + extraction tests)
- Novel vendor with mocked LLM response is promoted to `vendors.json`
