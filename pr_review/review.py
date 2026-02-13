"""Core PR review processing logic."""

import logging

from pr_review import filtering, gemini, storage
from pr_review.models import ReviewResult
from pr_review.prompt import build_review_prompt
from pr_review.severity import get_max_severity

logger = logging.getLogger("pr_review")


def process_pr_review(
    config: dict,
    ado: "pr_review.azure_client.AzureDevOpsClient",
    pr_id: int,
    pr: dict,
    file_diffs: list,
    debug: bool = False,
) -> ReviewResult:
    """
    Core PR review logic shared by HTTP and Pub/Sub entry points.

    This function handles:
    1. Building the review prompt
    2. Calling Gemini for analysis
    3. Determining severity
    4. Saving review to Cloud Storage
    5. Posting comments and/or rejecting PR based on severity

    Args:
        config: Configuration dictionary with GCS_BUCKET etc.
        ado: Initialized AzureDevOpsClient instance
        pr_id: Pull Request ID
        pr: PR metadata dict from Azure DevOps
        file_diffs: List of file diff dicts
        debug: If True, save prompt inputs to GCS

    Returns:
        ReviewResult with all review details and actions taken
    """
    pr_title = pr.get("title", "Untitled")
    pr_author = pr.get("createdBy", {}).get("displayName", "Unknown")

    logger.info(f"[REVIEW] Starting review for PR #{pr_id}: '{pr_title}' by {pr_author}")
    logger.info(f"[REVIEW] Files to review: {len(file_diffs)}")

    # Filter and limit files based on configuration
    filter_result = filtering.filter_and_limit_files(file_diffs, config)
    file_diffs = filter_result.filtered_files
    is_extensive = filter_result.is_extensive
    files_limited = filter_result.files_limited

    # Build prompt and call Gemini
    logger.info("[REVIEW] Building prompt and calling Gemini")
    prompt = build_review_prompt(pr, file_diffs)
    logger.info(f"[REVIEW] Prompt built: {len(prompt)} chars")

    review = gemini.call_gemini(config, prompt, debug=debug, pr_id=pr_id)

    # Determine severity
    logger.info("[REVIEW] Analyzing severity")
    max_severity = get_max_severity(review)
    has_blocking = max_severity == "action-required"
    has_warning = max_severity == "review-recommended"
    logger.info(f"[REVIEW] Priority: {max_severity} | action_required={has_blocking} | review_recommended={has_warning}")

    # Save to Cloud Storage
    logger.info("[REVIEW] Saving to Cloud Storage")
    storage_path = storage.save_to_storage(config["GCS_BUCKET"], pr_id, review)

    # Take action based on severity
    commented = False
    action_taken = None

    if has_blocking or has_warning:
        logger.info(f"[ACTION] Posting review comment to PR #{pr_id}")

        # Build comment with standard header
        comment_header = "## 🔍 Automated Regression Review\n\n"

        # Add partial review notice if PR was extensive and files were limited
        if is_extensive and files_limited > 0:
            comment_header += "⚠️ **Partial Review:** This PR is extensive. Review is limited to the first "
            comment_header += f"{len(file_diffs)} files (excluded {files_limited} additional files). "
            comment_header += "Please review remaining files manually.\n\n"

        if has_blocking:
            comment_header += f"**Hey {pr_author}!** We found some items that need attention before this PR can move forward. Please review the findings below—we're here to help ensure a smooth merge.\n\n"
            comment_header += "⚠️ **Status:** Action required before merge\n\n"
        else:
            comment_header += f"**Hey {pr_author}!** Nice work on this PR. We found a few items worth verifying before merge.\n\n"
            comment_header += "👀 **Status:** Review recommended\n\n"

        comment_header += f"📁 Full review saved to: `{storage_path}`\n\n---\n\n"

        ado.post_pr_comment(pr_id, comment_header + review)
        commented = True
        logger.info("[ACTION] Comment posted successfully")

        if has_blocking:
            logger.info("[ACTION] Rejecting PR due to blocking issues")
            user_id = ado.get_current_user_id()
            ado.reject_pr(pr_id, user_id)
            action_taken = "rejected"
            logger.info(f"[ACTION] PR #{pr_id} rejected")
        else:
            action_taken = "commented"
    else:
        logger.info("[ACTION] No issues found - no action taken on PR")

    logger.info(f"[REVIEW] Complete | Severity: {max_severity} | Action: {action_taken or 'none'}")

    return ReviewResult(
        pr_id=pr_id,
        pr_title=pr_title,
        pr_author=pr_author,
        files_changed=len(file_diffs),  # Use filtered count
        max_severity=max_severity,
        has_blocking=has_blocking,
        has_warning=has_warning,
        review_text=review,
        storage_path=storage_path,
        commented=commented,
        action_taken=action_taken,
    )
