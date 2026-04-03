"""Severity detection from review text.

Supports two prompt formats:
  - Priority format: **Priority:** action-required | review-recommended | note
  - Severity format: **Severity:** blocking | warning | info

Both are normalised to: "action-required", "review-recommended", or "note".
"""

import json
import logging

from pr_review.prompt import FINDINGS_JSON_DELIMITER

logger = logging.getLogger("pr_review")

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

# Priorities eligible for inline comments (note-level findings are skipped)
INLINE_COMMENT_SEVERITIES = frozenset({"action-required", "review-recommended"})

_FINDING_REQUIRED_FIELDS = frozenset({"title", "priority", "file_path", "line_number", "side", "inline_comment"})
_VALID_SIDES = frozenset({"right", "left"})


def parse_findings_json(review: str) -> list[dict]:
    """Extract structured inline-comment findings from a Gemini review response.

    Gemini appends a JSON block after the markdown, separated by FINDINGS_JSON_DELIMITER.
    Only findings with a priority in INLINE_COMMENT_SEVERITIES are returned.

    Args:
        review: Full Gemini response text (markdown + optional JSON block)

    Returns:
        List of validated finding dicts, each containing:
            title, priority, file_path, line_number (int), side, inline_comment.
        Returns an empty list on any parse or validation failure.
    """
    if FINDINGS_JSON_DELIMITER not in review:
        logger.debug("[SEVERITY] No findings JSON delimiter found in review")
        return []

    _, _, json_portion = review.partition(FINDINGS_JSON_DELIMITER)
    json_portion = json_portion.strip()

    if not json_portion:
        logger.debug("[SEVERITY] Findings delimiter present but no JSON content follows")
        return []

    try:
        data = json.loads(json_portion)
    except json.JSONDecodeError as exc:
        logger.warning(f"[SEVERITY] Failed to parse findings JSON: {exc}")
        return []

    raw_findings = data.get("findings")
    if not isinstance(raw_findings, list):
        logger.warning("[SEVERITY] findings JSON missing 'findings' list key")
        return []

    validated = []
    for idx, finding in enumerate(raw_findings):
        missing = _FINDING_REQUIRED_FIELDS - finding.keys()
        if missing:
            logger.warning(f"[SEVERITY] Finding #{idx} missing fields {missing} — skipping")
            continue

        priority = finding["priority"]
        if priority not in INLINE_COMMENT_SEVERITIES:
            logger.debug(f"[SEVERITY] Finding #{idx} priority={priority!r} not eligible for inline comment — skipping")
            continue

        line_number = finding["line_number"]
        if not isinstance(line_number, int) or line_number < 1:
            logger.warning(f"[SEVERITY] Finding #{idx} invalid line_number={line_number!r} — skipping")
            continue

        side = finding["side"]
        if side not in _VALID_SIDES:
            logger.warning(f"[SEVERITY] Finding #{idx} invalid side={side!r} — skipping")
            continue

        validated.append({
            "title": str(finding["title"]),
            "priority": priority,
            "file_path": str(finding["file_path"]),
            "line_number": line_number,
            "side": side,
            "inline_comment": str(finding["inline_comment"]),
        })

    logger.info(f"[SEVERITY] Parsed {len(validated)} eligible findings for inline comments")
    return validated


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
