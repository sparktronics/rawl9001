"""Core PR review processing logic."""

import logging

from pr_review import filtering, gemini, storage
from pr_review.models import ReviewResult
from pr_review.prompt import build_review_prompt
from pr_review.severity import get_max_severity, parse_findings_json

logger = logging.getLogger("pr_review")


def _post_inline_comments(
    ado: "pr_review.azure_client.AzureDevOpsClient",
    pr_id: int,
    findings: list[dict],
) -> None:
    """Post inline comment threads for each finding. Failures are non-fatal.

    Args:
        ado: Initialized AzureDevOpsClient instance
        pr_id: Pull request ID
        findings: Validated finding dicts from parse_findings_json()
    """
    if not findings:
        return

    logger.info(f"[INLINE] Posting {len(findings)} inline comments to PR #{pr_id}")
    posted = 0
    skipped = 0

    for finding in findings:
        try:
            body = f"**{finding['title']}** (`{finding['priority']}`)\n\n{finding['inline_comment']}"
            ado.post_inline_comment(
                pr_id=pr_id,
                content=body,
                file_path=finding["file_path"],
                line_number=finding["line_number"],
                side=finding["side"],
            )
            posted += 1
            logger.debug(f"[INLINE] Posted on {finding['file_path']}:{finding['line_number']}")
        except Exception as exc:
            skipped += 1
            logger.warning(
                f"[INLINE] Failed to post on {finding['file_path']}:{finding['line_number']} "
                f"— {type(exc).__name__}: {exc}"
            )

    logger.info(f"[INLINE] Done: {posted} posted, {skipped} skipped")


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

    # Determine severity and parse structured findings for inline comments
    logger.info("[REVIEW] Analyzing severity")
    max_severity = get_max_severity(review)
    has_blocking = max_severity == "action-required"
    has_warning = max_severity == "review-recommended"
    logger.info(f"[REVIEW] Priority: {max_severity} | action_required={has_blocking} | review_recommended={has_warning}")
    findings = parse_findings_json(review)
    logger.info(f"[REVIEW] Parsed {len(findings)} inline-eligible findings")

    # Save to Cloud Storage
    logger.info("[REVIEW] Saving to Cloud Storage")
    storage_path = storage.save_to_storage(config["GCS_BUCKET"], pr_id, review)

    # Status check config (used to enforce blocking via Azure DevOps branch policy)
    status_context = config.get("STATUS_CONTEXT_NAME", "ai-review")
    status_genre = config.get("STATUS_GENRE", "rawl-reviews")
    just_comment = config.get("JUST_COMMENT_TICKET", False)
    logger.info(f"[DEBUG-STATUS] status_context={status_context!r}, status_genre={status_genre!r}, just_comment={just_comment!r} (type={type(just_comment).__name__})")

    # Take action based on severity
    commented = False
    action_taken = None

    logger.info(f"[DEBUG-STATUS] Decision inputs: has_blocking={has_blocking}, has_warning={has_warning}, max_severity={max_severity!r}")

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

        post_comment_response = ado.post_pr_comment(pr_id, comment_header + review)
        logger.info(f"[ACTION] ADO response: {post_comment_response}")
        commented = True
        logger.info("[ACTION] Comment posted successfully")

        _post_inline_comments(ado, pr_id, findings)

        if has_blocking:
            if just_comment:
                logger.info("[ACTION] JUST_COMMENT_TICKET enabled — posting succeeded status so branch policy is satisfied")
                logger.info(f"[DEBUG-STATUS] About to post SUCCEEDED status (just_comment path): context_name={status_context!r}, genre={status_genre!r}")
                just_comment_status_resp = ado.post_pr_status(
                    pr_id,
                    state="succeeded",
                    description="AI review found issues (see comment) — merge not blocked per team policy.",
                    context_name=status_context,
                    genre=status_genre,
                    target_url=storage_path,
                )
                logger.info(f"[DEBUG-STATUS] SUCCEEDED status response (just_comment path): {just_comment_status_resp}")
                action_taken = "commented"
            else:
                logger.info("[ACTION] Posting failed status check to PR due to blocking issues")
                logger.info(f"[DEBUG-STATUS] About to post FAILED status: context_name={status_context!r}, genre={status_genre!r}, target_url={storage_path!r}")
                response_ado_status = ado.post_pr_status(
                    pr_id,
                    state="failed",
                    description="AI review found blocking regression risk. Tech leads may override via branch policy bypass.",
                    context_name=status_context,
                    genre=status_genre,
                    target_url=storage_path,
                )
                logger.info(f"[DEBUG-STATUS] FAILED status response: {response_ado_status}")
                action_taken = "status:failed"
                logger.info(f"[ACTION] PR #{pr_id} status set to failed")
        else:
            # Post succeeded so branch policy is satisfied for non-blocking outcomes
            logger.info(f"[DEBUG-STATUS] About to post SUCCEEDED status (warning path): context_name={status_context!r}, genre={status_genre!r}")
            warning_status_resp = ado.post_pr_status(
                pr_id,
                state="succeeded",
                description="AI review passed — review recommended but not blocking.",
                context_name=status_context,
                genre=status_genre,
                target_url=storage_path,
            )
            logger.info(f"[DEBUG-STATUS] SUCCEEDED status response (warning path): {warning_status_resp}")
            action_taken = "commented"
    else:
        logger.info("[ACTION] No issues found — posting succeeded status")
        logger.info(f"[DEBUG-STATUS] About to post SUCCEEDED status (clean path): context_name={status_context!r}, genre={status_genre!r}")
        clean_status_resp = ado.post_pr_status(
            pr_id,
            state="succeeded",
            description="AI review passed — no issues found.",
            context_name=status_context,
            genre=status_genre,
            target_url=storage_path,
        )
        logger.info(f"[DEBUG-STATUS] SUCCEEDED status response (clean path): {clean_status_resp}")

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
