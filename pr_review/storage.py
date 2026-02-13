"""Cloud Storage operations for saving reviews."""

import logging
from datetime import datetime, timezone

from google.cloud import storage

from pr_review.utils import timed_operation

logger = logging.getLogger("pr_review")


def save_to_storage(bucket_name: str, pr_id: int, review: str) -> str:
    """Save review to Cloud Storage with date partitioning.

    Args:
        bucket_name: GCS bucket name
        pr_id: Pull request ID
        review: Markdown review content

    Returns:
        Full GCS path (gs://bucket/path)
    """
    logger.info(f"[GCS] Saving review for PR #{pr_id} to bucket: {bucket_name}")
    logger.debug(f"[GCS] Review content size: {len(review)} chars")

    with timed_operation() as elapsed:
        try:
            client = storage.Client()
            bucket = client.bucket(bucket_name)

            # Date partitioning: yyyy/mm/dd
            now = datetime.now(timezone.utc)
            date_path = now.strftime("%Y/%m/%d")
            timestamp = now.strftime("%H%M%S")

            blob_path = f"reviews/{date_path}/pr-{pr_id}-{timestamp}-review.md"
            blob = bucket.blob(blob_path)

            blob.upload_from_string(review, content_type="text/markdown")

            full_path = f"gs://{bucket_name}/{blob_path}"
            logger.info(f"[GCS] Upload complete: {blob_path} | {len(review)} bytes | {elapsed():.0f}ms")

            return full_path
        except Exception as e:
            logger.error(f"[GCS] Upload FAILED | {elapsed():.0f}ms | Error: {str(e)}")
            raise


def save_prompt_input(bucket_name: str, pr_id: int, system_prompt: str, user_prompt: str) -> str:
    """Save prompt input to Cloud Storage for debugging.

    Args:
        bucket_name: GCS bucket name
        pr_id: Pull request ID
        system_prompt: System instruction/prompt
        user_prompt: User prompt content

    Returns:
        Full GCS path (gs://bucket/path)
    """
    logger.info(f"[GCS] Saving prompt input for PR #{pr_id} to bucket: {bucket_name}")
    logger.debug(f"[GCS] System prompt size: {len(system_prompt)} chars, User prompt size: {len(user_prompt)} chars")

    with timed_operation() as elapsed:
        try:
            client = storage.Client()
            bucket = client.bucket(bucket_name)

            # Date partitioning: yyyy/mm/dd (same as review)
            now = datetime.now(timezone.utc)
            date_path = now.strftime("%Y/%m/%d")
            timestamp = now.strftime("%H%M%S")

            blob_path = f"reviews/{date_path}/pr-{pr_id}-{timestamp}-prompt-input.txt"
            blob = bucket.blob(blob_path)

            # Combine system and user prompts with clear separation
            combined_content = f"""=== SYSTEM PROMPT ===

{system_prompt}

=== USER PROMPT ===

{user_prompt}
"""

            blob.upload_from_string(combined_content, content_type="text/plain")

            full_path = f"gs://{bucket_name}/{blob_path}"
            logger.info(f"[GCS] Prompt input saved: {blob_path} | {len(combined_content)} bytes | {elapsed():.0f}ms")

            return full_path
        except Exception as e:
            logger.error(f"[GCS] Prompt input save FAILED | {elapsed():.0f}ms | Error: {str(e)}")
            raise
