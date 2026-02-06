"""Severity detection from review text."""


def get_max_severity(review: str) -> str:
    """Determine the highest priority found in the review.

    Args:
        review: Markdown review content

    Returns:
        One of: "action-required", "review-recommended", "note"
    """
    if "**Priority:** action-required" in review:
        return "action-required"
    elif "**Priority:** review-recommended" in review:
        return "review-recommended"
    return "note"
