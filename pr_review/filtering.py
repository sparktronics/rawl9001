"""File filtering for non-code files and extensive PRs."""

import logging

from pr_review.config import DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD, DEFAULT_EXTENSIVE_PR_SIZE_THRESHOLD
from pr_review.models import FilterResult

logger = logging.getLogger("pr_review")

# File extensions to filter out from review (non-code files)
FILTERED_FILE_EXTENSIONS = {
    ".md",  # Markdown files
    ".sh",  # Shell scripts
    # Image file extensions
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".bmp", ".webp", ".ico", ".tiff", ".tif"
}


def filter_non_code_files(file_diffs: list) -> tuple[list, int]:
    """Filter out non-code files (.md, .sh, and image files) from file_diffs list.

    Filters files based on path ending with any of the filtered extensions (case-insensitive).
    Logs which files were filtered.

    Args:
        file_diffs: List of file diff dicts with 'path' key

    Returns:
        tuple: (filtered_file_diffs, count_of_filtered_files)
    """
    filtered = []
    filtered_count = 0
    filtered_paths = []

    for diff in file_diffs:
        path = diff.get("path", "")
        path_lower = path.lower()

        # Check if path ends with any filtered extension
        should_filter = any(path_lower.endswith(ext) for ext in FILTERED_FILE_EXTENSIONS)

        if should_filter:
            filtered_count += 1
            filtered_paths.append(path)
            logger.debug(f"[FILTER] Excluding non-code file: {path}")
        else:
            filtered.append(diff)

    if filtered_count > 0:
        logger.info(f"[FILTER] Filtered out {filtered_count} non-code file(s): {', '.join(filtered_paths)}")

    return filtered, filtered_count


def is_extensive_pr(file_diffs: list, config: dict) -> bool:
    """Determine if a PR is extensive based on file count or total diff size.

    A PR is considered extensive if:
    - File count exceeds EXTENSIVE_PR_FILE_THRESHOLD, OR
    - Total diff size (sum of all file diff lengths) exceeds EXTENSIVE_PR_SIZE_THRESHOLD

    Args:
        file_diffs: List of file diff dicts with 'diff' key (unified diff text)
        config: Configuration dictionary with threshold values

    Returns:
        True if PR is considered extensive
    """
    file_count = len(file_diffs)
    file_threshold = config.get("EXTENSIVE_PR_FILE_THRESHOLD", DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD)

    if file_count >= file_threshold:
        logger.info(f"[EXTENSIVE] PR detected as extensive: {file_count} files (threshold: {file_threshold})")
        return True

    total_size = sum(len(entry.get("diff") or "") for entry in file_diffs)
    size_threshold = config.get("EXTENSIVE_PR_SIZE_THRESHOLD", DEFAULT_EXTENSIVE_PR_SIZE_THRESHOLD)

    if total_size >= size_threshold:
        logger.info(f"[EXTENSIVE] PR detected as extensive: {total_size} chars (threshold: {size_threshold})")
        return True

    return False


def filter_and_limit_files(file_diffs: list, config: dict) -> FilterResult:
    """Filter non-code files (.md, .sh, images) and limit files for extensive PRs.

    Applies two filtering operations:
    1. Filters out non-code files (.md, .sh, and image files) if FILTER_NON_CODE_FILES is enabled
    2. Limits files to EXTENSIVE_PR_FILE_THRESHOLD if PR is extensive

    Args:
        file_diffs: List of file diff dicts with 'path' key
        config: Configuration dictionary with filtering settings

    Returns:
        FilterResult with filtered files and metadata about filtering operations
    """
    original_file_count = len(file_diffs)
    non_code_files_filtered = 0

    # Filter non-code files if enabled
    if config.get("FILTER_NON_CODE_FILES", True):
        logger.info(f"[FILTER] Filtering non-code files (.md, .sh, images) from review")
        file_diffs, non_code_files_filtered = filter_non_code_files(file_diffs)
        logger.info(f"[FILTER] After filtering: {len(file_diffs)} files remaining (removed {non_code_files_filtered} non-code files)")

        if len(file_diffs) == 0:
            logger.warning(f"[FILTER] All files were non-code files - review will proceed with empty file list")
    else:
        logger.debug(f"[FILTER] Non-code file filtering disabled via configuration")

    # Check if PR is extensive and limit files if needed
    is_ext = is_extensive_pr(file_diffs, config)
    files_limited = 0

    if is_ext:
        file_threshold = config.get("EXTENSIVE_PR_FILE_THRESHOLD", DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD)
        if len(file_diffs) > file_threshold:
            files_limited = len(file_diffs) - file_threshold
            file_diffs = file_diffs[:file_threshold]
            logger.info(f"[LIMIT] Extensive PR detected - limiting review to first {file_threshold} files (excluded {files_limited} files)")

    return FilterResult(
        filtered_files=file_diffs,
        original_file_count=original_file_count,
        non_code_files_filtered=non_code_files_filtered,
        is_extensive=is_ext,
        files_limited=files_limited
    )
