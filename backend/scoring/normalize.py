"""Fixed-anchor linear normalization to 0-100."""

from __future__ import annotations


def normalize(value: float | None, lo: float, hi: float) -> float | None:
    """Map value to 0-100: <= lo -> 0, >= hi -> 100, linear in between. None stays None.

    Unknown data must stay unknown, never become 0 (which would read as "no risk").
    """
    if hi <= lo:
        raise ValueError(f"anchor hi ({hi}) must be greater than lo ({lo})")
    if value is None:
        return None
    return round(max(0.0, min(100.0, (value - lo) / (hi - lo) * 100.0)), 1)


def weighted_average(parts: list[tuple[float | None, float]]) -> float | None:
    """Weighted average of (score, weight). If any score is None the result is None."""
    if any(s is None for s, _ in parts):
        return None
    total = sum(w for _, w in parts)
    return round(sum(s * w for s, w in parts) / total, 1)  # type: ignore[operator]
