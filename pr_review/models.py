"""Data models for PR review results."""

from dataclasses import dataclass


@dataclass
class ReviewResult:
    """Result of processing a PR review."""
    pr_id: int
    pr_title: str
    pr_author: str
    files_changed: int
    max_severity: str  # "blocking", "warning", or "info"
    has_blocking: bool
    has_warning: bool
    review_text: str
    storage_path: str
    commented: bool
    action_taken: str | None  # "rejected", "commented", or None


@dataclass
class FilterResult:
    """Result of filtering and limiting files for review."""
    filtered_files: list
    original_file_count: int
    non_code_files_filtered: int
    is_extensive: bool
    files_limited: int
