"""Idempotency management via GCS markers to prevent duplicate processing."""

import json
import logging
from datetime import datetime, timezone

from google.cloud import storage
from google.api_core.exceptions import PreconditionFailed

logger = logging.getLogger("pr_review")

MAX_RETRY_ATTEMPTS = 3  # Maximum number of retry attempts before giving up


def check_and_claim_processing(bucket_name: str, pr_id: int, commit_sha: str) -> bool:
    """
    Check if this PR+commit has been processed. If not, claim it atomically.

    Uses GCS conditional writes (if_generation_match=0) to ensure only one
    instance can claim processing for a given PR+commit combination.

    Also handles retry logic: if a marker exists with status "processing" and
    retry_count < MAX_RETRY_ATTEMPTS, allows processing to continue (retry).

    Args:
        bucket_name: GCS bucket name
        pr_id: Pull request ID
        commit_sha: The commit SHA being reviewed

    Returns:
        True if we should process (we claimed it or it's a valid retry)
        False if already completed, failed permanently, or claimed by another instance
    """
    logger.info(f"[IDEMPOTENCY] Checking marker for PR #{pr_id} @ {commit_sha[:8]}")

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(f"idempotency/pr-{pr_id}-{commit_sha}.json")

    # Check if marker exists
    if blob.exists():
        # Read existing marker to check status
        try:
            marker_data = json.loads(blob.download_as_text())
            status = marker_data.get("status", "unknown")
            retry_count = marker_data.get("retry_count", 0)

            if status == "completed":
                logger.info(f"[IDEMPOTENCY] PR #{pr_id} @ {commit_sha[:8]} already completed - SKIPPING")
                return False

            if status == "failed":
                logger.info(f"[IDEMPOTENCY] PR #{pr_id} @ {commit_sha[:8]} permanently failed after {retry_count} attempts - SKIPPING")
                return False

            if status == "processing":
                # This is a retry - check if we've exceeded max attempts
                if retry_count >= MAX_RETRY_ATTEMPTS:
                    logger.warning(f"[IDEMPOTENCY] PR #{pr_id} @ {commit_sha[:8]} exceeded max retries ({MAX_RETRY_ATTEMPTS}) - SKIPPING")
                    return False
                logger.info(f"[IDEMPOTENCY] PR #{pr_id} @ {commit_sha[:8]} retry attempt {retry_count + 1}/{MAX_RETRY_ATTEMPTS}")
                return True

        except json.JSONDecodeError:
            logger.warning(f"[IDEMPOTENCY] Corrupted marker for PR #{pr_id} - allowing processing")
            # Fall through to create new marker
        except Exception as e:
            logger.warning(f"[IDEMPOTENCY] Error reading marker: {e} - allowing processing")
            return True  # Allow processing on read errors

    # Try to claim it atomically
    # if_generation_match=0 means "only succeed if file doesn't exist"
    marker = {
        "pr_id": pr_id,
        "commit_sha": commit_sha,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "status": "processing",
        "retry_count": 0
    }

    try:
        blob.upload_from_string(
            json.dumps(marker, indent=2),
            content_type="application/json",
            if_generation_match=0  # Atomic: fails if file exists
        )
        logger.info(f"[IDEMPOTENCY] Claimed processing for PR #{pr_id} @ {commit_sha[:8]}")
        return True
    except PreconditionFailed:
        logger.info(f"[IDEMPOTENCY] Race condition - another instance claimed PR #{pr_id} @ {commit_sha[:8]} - SKIPPING")
        return False


def update_marker_completed(bucket_name: str, pr_id: int, commit_sha: str,
                            max_severity: str, commented: bool) -> None:
    """
    Update the idempotency marker after successful processing.

    Args:
        bucket_name: GCS bucket name
        pr_id: Pull request ID
        commit_sha: The commit SHA that was reviewed
        max_severity: The maximum severity found in the review
        commented: Whether a comment was posted to the PR
    """
    logger.info(f"[IDEMPOTENCY] Updating marker for PR #{pr_id} @ {commit_sha[:8]} -> completed")

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(f"idempotency/pr-{pr_id}-{commit_sha}.json")

    marker = {
        "pr_id": pr_id,
        "commit_sha": commit_sha,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "max_severity": max_severity,
        "commented": commented
    }

    blob.upload_from_string(
        json.dumps(marker, indent=2),
        content_type="application/json"
    )
    logger.info(f"[IDEMPOTENCY] Marker updated: severity={max_severity}, commented={commented}")


def update_marker_for_retry(bucket_name: str, pr_id: int, commit_sha: str, error_msg: str) -> bool:
    """
    Update idempotency marker after a processing failure to track retry attempts.

    Increments the retry counter. If max retries exceeded, marks as permanently failed.

    Args:
        bucket_name: GCS bucket name
        pr_id: Pull request ID
        commit_sha: The commit SHA
        error_msg: Error message describing the failure

    Returns:
        True if retry should be attempted (re-raise exception)
        False if max retries exceeded (acknowledge message to stop retries)
    """
    logger.info(f"[IDEMPOTENCY] Updating marker for retry: PR #{pr_id} @ {commit_sha[:8]}")

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(f"idempotency/pr-{pr_id}-{commit_sha}.json")

    # Read existing marker to get retry count
    retry_count = 0
    try:
        if blob.exists():
            marker_data = json.loads(blob.download_as_text())
            retry_count = marker_data.get("retry_count", 0)
    except Exception as e:
        logger.warning(f"[IDEMPOTENCY] Error reading marker: {e}")

    # Increment retry count
    retry_count += 1

    if retry_count >= MAX_RETRY_ATTEMPTS:
        # Max retries exceeded - mark as permanently failed
        marker = {
            "pr_id": pr_id,
            "commit_sha": commit_sha,
            "status": "failed",
            "retry_count": retry_count,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "last_error": error_msg[:500]  # Truncate long error messages
        }
        blob.upload_from_string(
            json.dumps(marker, indent=2),
            content_type="application/json"
        )
        logger.error(f"[IDEMPOTENCY] PR #{pr_id} @ {commit_sha[:8]} marked as FAILED after {retry_count} attempts")
        return False  # Don't retry - acknowledge message

    # Update marker with incremented retry count
    marker = {
        "pr_id": pr_id,
        "commit_sha": commit_sha,
        "status": "processing",
        "retry_count": retry_count,
        "last_attempt_at": datetime.now(timezone.utc).isoformat(),
        "last_error": error_msg[:500]
    }
    blob.upload_from_string(
        json.dumps(marker, indent=2),
        content_type="application/json"
    )
    logger.info(f"[IDEMPOTENCY] PR #{pr_id} @ {commit_sha[:8]} retry count: {retry_count}/{MAX_RETRY_ATTEMPTS}")
    return True  # Retry - re-raise exception


def update_marker_failed(bucket_name: str, pr_id: int, commit_sha: str, error_msg: str) -> None:
    """
    Mark an idempotency marker as permanently failed (non-retryable error).

    Used for errors like 401/403/404 that won't be resolved by retrying.

    Args:
        bucket_name: GCS bucket name
        pr_id: Pull request ID
        commit_sha: The commit SHA
        error_msg: Error message describing the failure
    """
    logger.info(f"[IDEMPOTENCY] Marking PR #{pr_id} @ {commit_sha[:8]} as permanently FAILED")

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(f"idempotency/pr-{pr_id}-{commit_sha}.json")

    marker = {
        "pr_id": pr_id,
        "commit_sha": commit_sha,
        "status": "failed",
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "error": error_msg[:500],
        "reason": "non_retryable_error"
    }

    blob.upload_from_string(
        json.dumps(marker, indent=2),
        content_type="application/json"
    )
    logger.error(f"[IDEMPOTENCY] PR #{pr_id} @ {commit_sha[:8]} marked as FAILED (non-retryable)")
