from app.services.dedupe import check_duplicate, compute_hash


class TestCheckDuplicate:
    """AC1.5: Deduplication check."""

    def test_new_hash_is_clean(self):
        """AC1.5: check_duplicate(hash, {}) returns 'none' for new hash."""
        assert check_duplicate("abc123", set()) == "none"

    def test_known_hash_is_likely_duplicate(self):
        """AC1.5: check_duplicate(hash, {hash}) returns 'likely' for known hash."""
        h = "deadbeef"
        assert check_duplicate(h, {h}) == "likely"

    def test_unknown_hash_when_set_has_others(self):
        """New hash not in set returns 'none' even when set is not empty."""
        known = {"aaa", "bbb", "ccc"}
        assert check_duplicate("new_hash", known) == "none"

    def test_exact_match_required(self):
        """Only exact hash match triggers 'likely' status."""
        h1 = "abc123"
        h2 = "abc124"  # very similar but different
        assert check_duplicate(h1, {h2}) == "none"


class TestComputeHash:
    """SHA-256 hash computation."""

    def test_compute_hash_returns_hex_string(self):
        """compute_hash returns a 64-character hex string (SHA-256)."""
        h = compute_hash(b"some pdf bytes")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_compute_hash_is_deterministic(self):
        """compute_hash returns same result for same input."""
        data = b"invoice data"
        assert compute_hash(data) == compute_hash(data)

    def test_compute_hash_differs_for_different_bytes(self):
        """Different input produces different hash."""
        assert compute_hash(b"file A") != compute_hash(b"file B")

    def test_compute_hash_empty_bytes(self):
        """compute_hash works with empty bytes."""
        h = compute_hash(b"")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_compute_hash_large_data(self):
        """compute_hash works with large data."""
        large_data = b"x" * 1000000  # 1MB
        h = compute_hash(large_data)
        assert len(h) == 64

    def test_compute_hash_sensitivity(self):
        """Single byte difference produces completely different hash."""
        h1 = compute_hash(b"abc")
        h2 = compute_hash(b"abd")
        # Hashes should differ significantly (not just in one character)
        assert h1 != h2
        # Count differing characters
        diffs = sum(c1 != c2 for c1, c2 in zip(h1, h2))
        assert diffs > 10  # SHA-256 has avalanche effect, many bits should flip
