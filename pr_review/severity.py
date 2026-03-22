"""Severity detection from review text.

Supports two prompt formats:
  - Priority format: **Priority:** action-required | review-recommended | note
  - Severity format: **Severity:** blocking | warning | info

Both are normalised to: "action-required", "review-recommended", or "note".
"""

# Maps (label, value) patterns to the canonical severity.
# Order matters: first match wins, so blocking checks come first.
_BLOCKING_PATTERNS = (
    "**Priority:** action-required",
    "**Severity:** blocking",
)
_WARNING_PATTERNS = (
    "**Priority:** review-recommended",
    "**Severity:** warning",
)


def get_max_severity(review: str) -> str:
    """Determine the highest severity found in the review.

    Args:
        review: Markdown review content

    Returns:
        One of: "action-required", "review-recommended", "note"
    """
    if any(pattern in review for pattern in _BLOCKING_PATTERNS):
        return "action-required"
    if any(pattern in review for pattern in _WARNING_PATTERNS):
        return "review-recommended"
    return "note"
