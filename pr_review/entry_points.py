"""Cloud Function entry points with functions_framework decorators."""

import base64
import json
import logging
from datetime import datetime, timezone

import requests
import functions_framework
from cloudevents.http import CloudEvent
from google.cloud import pubsub_v1
from google.cloud import storage

from pr_review.azure_client import AzureDevOpsClient
from pr_review.config import load_config, load_webhook_config
from pr_review.idempotency import (
    check_and_claim_processing,
    update_marker_completed,
    update_marker_failed,
    update_marker_for_retry,
)
from pr_review.review import process_pr_review
from pr_review.utils import make_response, timed_operation

logger = logging.getLogger("pr_review")


@functions_framework.http
def review_pr(request):
    """HTTP Cloud Function entry point for PR regression review.

    Authentication:
        Requires GCP IAM authentication with roles/run.invoker permission.
        Callers must provide a valid Google-signed identity token in the
        Authorization: Bearer <token> header. Authentication is enforced by
        Cloud Run before this function is invoked.

    Request:
        POST with JSON body: {"pr_id": 12345, "debug": false}
        Header: Authorization: Bearer <google-identity-token>

    Response:
        JSON with review results and actions taken
    """
    with timed_operation() as elapsed:
        logger.info("=" * 60)
        logger.info("[REQUEST] PR Review function invoked")
        logger.info(f"[REQUEST] Method: {request.method} | Path: {request.path}")

        # Load config
        config, missing = load_config()
        if missing:
            logger.error(f"[CONFIG] Missing required environment variables: {missing}")
            return make_response(
                {"error": f"Missing config: {', '.join(missing)}"}, 500
            )
        logger.info("[CONFIG] All required environment variables loaded")

        # Note: Authentication is handled by Cloud Run IAM.
        # Only authorized service accounts/users with roles/run.invoker can reach this code.
        logger.info("[AUTH] Request authenticated via GCP IAM")

        # Parse request
        try:
            request_json = request.get_json(silent=True)
            if not request_json:
                logger.warning("[REQUEST] Empty or invalid JSON body")
                return make_response({"error": "Request body must be JSON"}, 400)

            pr_id = request_json.get("pr_id")
            if not pr_id:
                logger.warning("[REQUEST] Missing pr_id in request body")
                return make_response({"error": "Missing required field: pr_id"}, 400)

            pr_id = int(pr_id)
            debug = request_json.get("debug", False)
            logger.info(f"[REQUEST] Processing PR #{pr_id} | Debug: {debug}")
        except (ValueError, TypeError) as e:
            logger.error(f"[REQUEST] Invalid pr_id format: {e}")
            return make_response({"error": f"Invalid pr_id: {e}"}, 400)

        # Initialize Azure DevOps client
        logger.info(f"[ADO] Initializing client | Org: {config['AZURE_DEVOPS_ORG']} | Project: {config['AZURE_DEVOPS_PROJECT']} | Repo: {config['AZURE_DEVOPS_REPO']}")
        ado = AzureDevOpsClient(
            org=config["AZURE_DEVOPS_ORG"],
            project=config["AZURE_DEVOPS_PROJECT"],
            repo=config["AZURE_DEVOPS_REPO"],
            pat=config["AZURE_DEVOPS_PAT"],
        )

        try:
            # Fetch PR data
            logger.info(f"[FLOW] Step 1/3: Fetching PR metadata")
            pr = ado.get_pull_request(pr_id)
            pr_title = pr.get("title", "Untitled")
            pr_author = pr.get("createdBy", {}).get("displayName", "Unknown")
            logger.info(f"[FLOW] PR: '{pr_title}' by {pr_author}")

            # Fetch file diffs
            logger.info(f"[FLOW] Step 2/3: Fetching file diffs")
            file_diffs = ado.get_pr_diff(pr_id)

            if not file_diffs:
                logger.info(f"[FLOW] No file changes found | Total time: {elapsed():.0f}ms")
                return make_response({
                    "pr_id": pr_id,
                    "title": pr_title,
                    "message": "No file changes found in this PR",
                    "has_blocking": False,
                    "has_warning": False,
                    "action_taken": None,
                    "commented": False,
                    "storage_path": None,
                })

            logger.info(f"[FLOW] Found {len(file_diffs)} files to review")
            for diff in file_diffs:
                logger.debug(f"[FLOW]   - {diff['path']} ({diff['change_type']})")

            # Process the review using shared logic
            logger.info(f"[FLOW] Step 3/3: Processing review")
            result = process_pr_review(config, ado, pr_id, pr, file_diffs, debug=debug)

            logger.info(f"[COMPLETE] PR #{pr_id} review finished | Severity: {result.max_severity} | Action: {result.action_taken or 'none'} | Total time: {elapsed():.0f}ms")
            logger.info("=" * 60)

            return make_response({
                "pr_id": result.pr_id,
                "title": result.pr_title,
                "files_changed": result.files_changed,
                "max_severity": result.max_severity,
                "has_blocking": result.has_blocking,
                "has_warning": result.has_warning,
                "action_taken": result.action_taken,
                "commented": result.commented,
                "storage_path": result.storage_path,
                "review_preview": result.review_text[:500] + "..." if len(result.review_text) > 500 else result.review_text,
            })

        except requests.HTTPError as e:
            logger.error(f"[ERROR] Azure DevOps API error | Status: {e.response.status_code} | {elapsed():.0f}ms")
            logger.error(f"[ERROR] Response body: {e.response.text[:500]}")
            return make_response({
                "error": f"Azure DevOps API error: {e.response.status_code} - {e.response.text}"
            }, 502)
        except Exception as e:
            logger.error(f"[ERROR] Internal error | Type: {type(e).__name__} | {elapsed():.0f}ms")
            logger.error(f"[ERROR] Details: {str(e)}", exc_info=True)
            return make_response({"error": f"Internal error: {str(e)}"}, 500)


@functions_framework.cloud_event
def review_pr_pubsub(cloud_event: CloudEvent) -> None:
    """
    Pub/Sub triggered Cloud Function entry point for PR regression review.

    Includes idempotency handling to prevent duplicate processing when
    Pub/Sub delivers the same message multiple times (at-least-once delivery).

    Pub/Sub Message Format:
        {
            "pr_id": 12345,
            "commit_sha": "abc123def...",  // Optional: provided by webhook receiver
            "received_at": "2026-01-03T10:30:00Z",
            "source": "azure-devops-pipeline",
            "debug": false  // Optional: enable prompt input debugging
        }

    The function will:
    1. Parse the PR ID and optional commit_sha from the Pub/Sub message
    2. Fetch PR metadata (use commit_sha from message if provided)
    3. Check idempotency marker (skip if already processed)
    4. Process the PR review
    5. Update the marker on completion
    """
    with timed_operation() as elapsed:
        logger.info("=" * 60)
        logger.info("[PUBSUB] PR Review function invoked via Pub/Sub")

        # Load config
        config, missing = load_config()
        if missing:
            logger.error(f"[CONFIG] Missing required environment variables: {missing}")
            # Don't raise - acknowledge message to prevent infinite retries on config errors
            return
        logger.info("[CONFIG] All required environment variables loaded")

        # Parse Pub/Sub message
        try:
            message_data = cloud_event.data.get("message", {}).get("data", "")
            if message_data:
                decoded = base64.b64decode(message_data).decode("utf-8")
                message = json.loads(decoded)
            else:
                logger.error("[PUBSUB] Empty message data")
                return

            pr_id = message.get("pr_id")
            if not pr_id:
                logger.error("[PUBSUB] Missing pr_id in message")
                return

            pr_id = int(pr_id)

            # Extract commit_sha from message (provided by webhook receiver)
            message_commit_sha = message.get("commit_sha")
            debug = message.get("debug", False)

            if message_commit_sha:
                logger.info(f"[PUBSUB] Processing PR #{pr_id} @ {message_commit_sha[:8]} (from message) | Debug: {debug}")
            else:
                logger.info(f"[PUBSUB] Processing PR #{pr_id} (commit_sha will be fetched from ADO) | Debug: {debug}")

        except (ValueError, TypeError, json.JSONDecodeError) as e:
            logger.error(f"[PUBSUB] Failed to parse message: {e}")
            return  # Acknowledge to prevent retries on malformed messages

        # Initialize Azure DevOps client
        logger.info(f"[ADO] Initializing client | Org: {config['AZURE_DEVOPS_ORG']} | Project: {config['AZURE_DEVOPS_PROJECT']}")
        ado = AzureDevOpsClient(
            org=config["AZURE_DEVOPS_ORG"],
            project=config["AZURE_DEVOPS_PROJECT"],
            repo=config["AZURE_DEVOPS_REPO"],
            pat=config["AZURE_DEVOPS_PAT"],
        )

        commit_sha = None

        try:
            # Fetch PR metadata
            logger.info(f"[FLOW] Step 1/4: Fetching PR metadata")
            pr = ado.get_pull_request(pr_id)
            pr_title = pr.get("title", "Untitled")
            pr_author = pr.get("createdBy", {}).get("displayName", "Unknown")

            # Use commit_sha from message if provided, otherwise fetch from PR metadata
            if message_commit_sha:
                commit_sha = message_commit_sha
                logger.info(f"[FLOW] Using commit_sha from message: {commit_sha[:8]}")
            else:
                last_merge_commit = pr.get("lastMergeSourceCommit")
                if not last_merge_commit or "commitId" not in last_merge_commit:
                    logger.warning(f"[SKIP] PR #{pr_id} has no lastMergeSourceCommit - may be draft or empty")
                    logger.info("=" * 60)
                    return
                commit_sha = last_merge_commit["commitId"]
                logger.info(f"[FLOW] Fetched commit_sha from ADO: {commit_sha[:8]}")

            logger.info(f"[FLOW] PR: '{pr_title}' by {pr_author} @ commit {commit_sha[:8]}")

            # Idempotency check
            logger.info(f"[FLOW] Step 2/4: Checking idempotency")
            bucket_name = config["GCS_BUCKET"]
            if not check_and_claim_processing(bucket_name, pr_id, commit_sha):
                logger.info(f"[COMPLETE] PR #{pr_id} @ {commit_sha[:8]} already processed | {elapsed():.0f}ms")
                logger.info("=" * 60)
                return  # Already processed - acknowledge and exit

            # Fetch file diffs
            logger.info(f"[FLOW] Step 3/4: Fetching file diffs")
            file_diffs = ado.get_pr_diff(pr_id)

            if not file_diffs:
                logger.info(f"[FLOW] No file changes found")
                update_marker_completed(bucket_name, pr_id, commit_sha, "info", False)
                logger.info(f"[COMPLETE] PR #{pr_id} - no files to review | {elapsed():.0f}ms")
                logger.info("=" * 60)
                return

            logger.info(f"[FLOW] Found {len(file_diffs)} files to review")

            # Process the review using shared logic
            logger.info(f"[FLOW] Step 4/4: Processing review")
            result = process_pr_review(config, ado, pr_id, pr, file_diffs, debug=debug)

            # Update idempotency marker with completion status
            update_marker_completed(bucket_name, pr_id, commit_sha, result.max_severity, result.commented)

            logger.info(f"[COMPLETE] PR #{pr_id} @ {commit_sha[:8]} review finished | Severity: {result.max_severity} | {elapsed():.0f}ms")
            logger.info("=" * 60)

        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 0
            error_msg = f"Azure DevOps API error: {status_code}"
            logger.error(f"[ERROR] {error_msg} | {elapsed():.0f}ms")

            # Non-retryable HTTP errors - acknowledge immediately (will go to DLQ)
            # 401: Unauthorized (bad PAT), 403: Forbidden (no permissions), 404: PR not found
            non_retryable_codes = {401, 403, 404}
            if status_code in non_retryable_codes:
                logger.error(f"[DLQ] Non-retryable error {status_code} for PR #{pr_id} - acknowledging for DLQ")
                if commit_sha:
                    # Mark as permanently failed
                    update_marker_failed(config["GCS_BUCKET"], pr_id, commit_sha, error_msg)
                logger.info("=" * 60)
                raise  # Re-raise to send to DLQ (Pub/Sub will not retry after max attempts)

            # Retryable errors - update counter and check limit
            if commit_sha:
                should_retry = update_marker_for_retry(config["GCS_BUCKET"], pr_id, commit_sha, error_msg)
                if not should_retry:
                    logger.error(f"[ABORT] PR #{pr_id} @ {commit_sha[:8]} max retries exceeded - giving up")
                    logger.info("=" * 60)
                    return  # Acknowledge message to stop retries
            raise  # Re-raise to trigger Pub/Sub retry

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            logger.error(f"[ERROR] Internal error | Type: {type(e).__name__} | {elapsed():.0f}ms")
            logger.error(f"[ERROR] Details: {str(e)}", exc_info=True)
            # Update retry counter and check if we should retry
            if commit_sha:
                should_retry = update_marker_for_retry(config["GCS_BUCKET"], pr_id, commit_sha, error_msg)
                if not should_retry:
                    logger.error(f"[ABORT] PR #{pr_id} @ {commit_sha[:8]} max retries exceeded - giving up")
                    logger.info("=" * 60)
                    return  # Acknowledge message to stop retries
            raise  # Re-raise to trigger Pub/Sub retry


@functions_framework.http
def process_dead_letter_queue(request):
    """
    HTTP-triggered function to process messages from the Dead Letter Queue.

    This function should be called manually after resolving issues that caused
    messages to be sent to the DLQ (e.g., after renewing an expired PAT).

    It will:
    1. Validate Azure DevOps credentials before processing
    2. Pull messages from the DLQ subscription
    3. Reset idempotency markers to allow reprocessing
    4. Republish messages to the main processing topic

    Authentication:
        Requires GCP IAM authentication with roles/run.invoker permission.
        Callers must provide a valid Google-signed identity token in the
        Authorization: Bearer <token> header. Authentication is enforced by
        Cloud Run before this function is invoked.

    Request Format:
        POST /
        Content-Type: application/json
        Authorization: Bearer <google-identity-token>

        {
            "max_messages": 10,  // Optional: max messages to process (default: 100)
            "dry_run": false     // Optional: if true, only report what would be done
        }

    Response (200 OK):
        {
            "status": "completed",
            "messages_pulled": 5,
            "messages_republished": 5,
            "messages_failed": 0,
            "dry_run": false,
            "details": [...]
        }
    """
    with timed_operation() as elapsed:
        logger.info("=" * 60)
        logger.info("[DLQ] Dead Letter Queue processing function invoked")

        # Load config
        config, missing = load_config()
        if missing:
            logger.error(f"[CONFIG] Missing required environment variables: {missing}")
            return make_response({"error": f"Missing config: {', '.join(missing)}"}, 500)

        # Note: Authentication is handled by Cloud Run IAM.
        # Only authorized service accounts/users with roles/run.invoker can reach this code.
        logger.info("[AUTH] Request authenticated via GCP IAM")

        # Parse request parameters
        request_json = request.get_json(silent=True) or {}
        max_messages = request_json.get("max_messages", 100)
        dry_run = request_json.get("dry_run", False)

        try:
            max_messages = int(max_messages)
            if max_messages < 1 or max_messages > 1000:
                return make_response({"error": "max_messages must be between 1 and 1000"}, 400)
        except (ValueError, TypeError):
            return make_response({"error": "max_messages must be an integer"}, 400)

        logger.info(f"[DLQ] Processing parameters: max_messages={max_messages}, dry_run={dry_run}")

        # Step 1: Validate Azure DevOps credentials before processing
        logger.info("[DLQ] Step 1/3: Validating Azure DevOps credentials")
        ado = AzureDevOpsClient(
            org=config["AZURE_DEVOPS_ORG"],
            project=config["AZURE_DEVOPS_PROJECT"],
            repo=config["AZURE_DEVOPS_REPO"],
            pat=config["AZURE_DEVOPS_PAT"],
        )

        try:
            # Test credentials by getting current user
            user_id = ado.get_current_user_id()
            logger.info(f"[DLQ] Credentials validated successfully (user: {user_id[:8]}...)")
        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response else 500
            logger.error(f"[DLQ] Credential validation FAILED: {status_code}")
            return make_response({
                "error": "Azure DevOps credentials validation failed. Please check your PAT.",
                "status_code": status_code
            }, 401)
        except requests.RequestException as e:
            logger.error(f"[DLQ] Credential validation error: {str(e)}")
            return make_response({
                "error": f"Failed to validate credentials: {str(e)}"
            }, 500)

        # Step 2: Pull messages from DLQ
        logger.info(f"[DLQ] Step 2/3: Pulling up to {max_messages} messages from DLQ")

        subscriber = pubsub_v1.SubscriberClient()
        dlq_subscription_path = subscriber.subscription_path(
            config["VERTEX_PROJECT"],
            config.get("DLQ_SUBSCRIPTION", "pr-review-dlq-sub")
        )

        try:
            with timed_operation() as pull_elapsed:
                response = subscriber.pull(
                    request={
                        "subscription": dlq_subscription_path,
                        "max_messages": max_messages,
                    },
                    timeout=30,
                )

            messages_pulled = len(response.received_messages)
            logger.info(f"[DLQ] Pulled {messages_pulled} messages from DLQ | {pull_elapsed():.0f}ms")

        except Exception as e:
            logger.error(f"[DLQ] Failed to pull messages from DLQ: {e}")
            return make_response({
                "error": f"Failed to pull from DLQ: {str(e)}"
            }, 500)

        if messages_pulled == 0:
            logger.info(f"[DLQ] No messages in DLQ | {elapsed():.0f}ms")
            logger.info("=" * 60)
            return make_response({
                "status": "completed",
                "messages_pulled": 0,
                "messages_republished": 0,
                "messages_failed": 0,
                "dry_run": dry_run,
                "message": "No messages found in DLQ"
            })

        # Step 3: Process and republish messages
        logger.info(f"[DLQ] Step 3/3: Processing {messages_pulled} messages")

        publisher = pubsub_v1.PublisherClient()
        main_topic_path = publisher.topic_path(
            config["VERTEX_PROJECT"],
            config.get("PUBSUB_TOPIC", "pr-review-trigger")
        )

        messages_republished = 0
        messages_failed = 0
        details = []
        ack_ids = []

        storage_client = storage.Client()
        bucket = storage_client.bucket(config["GCS_BUCKET"])

        for received_message in response.received_messages:
            pr_id = None
            commit_sha = None
            try:
                # Decode message
                message_data = json.loads(received_message.message.data.decode("utf-8"))
                pr_id = message_data.get("pr_id")
                commit_sha = message_data.get("commit_sha")

                logger.info(f"[DLQ] Processing PR #{pr_id} @ {commit_sha[:8] if commit_sha else 'unknown'}")

                if not pr_id:
                    logger.warning(f"[DLQ] Skipping message with missing pr_id (will acknowledge to clear from DLQ)")
                    details.append({
                        "pr_id": None,
                        "status": "skipped",
                        "reason": "missing pr_id"
                    })
                    messages_failed += 1
                    ack_ids.append(received_message.ack_id)
                    continue

                if dry_run:
                    logger.info(f"[DLQ] DRY RUN: Would republish PR #{pr_id}")
                    details.append({
                        "pr_id": pr_id,
                        "commit_sha": commit_sha[:8] if commit_sha else None,
                        "status": "dry_run",
                        "action": "would republish"
                    })
                    messages_republished += 1
                    # Tracked but won't actually ack - the ack block checks dry_run
                    ack_ids.append(received_message.ack_id)
                    continue

                # Reset idempotency marker if commit_sha is available
                if commit_sha:
                    marker_blob = bucket.blob(f"idempotency/pr-{pr_id}-{commit_sha}.json")
                    if marker_blob.exists():
                        logger.info(f"[DLQ] Deleting idempotency marker for PR #{pr_id} @ {commit_sha[:8]}")
                        marker_blob.delete()

                # Republish to main topic
                republish_message = {
                    "pr_id": pr_id,
                    "commit_sha": commit_sha,
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "source": "dlq-reprocessing",
                    "original_message_id": received_message.message.message_id
                }

                with timed_operation() as pub_elapsed:
                    future = publisher.publish(
                        main_topic_path,
                        json.dumps(republish_message).encode("utf-8")
                    )
                    new_message_id = future.result(timeout=10)

                logger.info(f"[DLQ] Republished PR #{pr_id} | new message_id={new_message_id} | {pub_elapsed():.0f}ms")

                details.append({
                    "pr_id": pr_id,
                    "commit_sha": commit_sha[:8] if commit_sha else None,
                    "status": "republished",
                    "new_message_id": new_message_id
                })

                messages_republished += 1
                ack_ids.append(received_message.ack_id)

            except Exception as e:
                logger.error(f"[DLQ] Failed to process message: {e}")
                details.append({
                    "pr_id": pr_id,
                    "status": "failed",
                    "error": str(e)
                })
                messages_failed += 1

        # Acknowledge successfully processed messages
        if ack_ids and not dry_run:
            try:
                subscriber.acknowledge(
                    request={
                        "subscription": dlq_subscription_path,
                        "ack_ids": ack_ids,
                    }
                )
                logger.info(f"[DLQ] Acknowledged {len(ack_ids)} messages")
            except Exception as e:
                logger.error(f"[DLQ] Failed to acknowledge messages: {e}")

        logger.info(f"[COMPLETE] DLQ processing finished | Pulled: {messages_pulled} | Republished: {messages_republished} | Failed: {messages_failed} | {elapsed():.0f}ms")
        logger.info("=" * 60)

        return make_response({
            "status": "completed",
            "messages_pulled": messages_pulled,
            "messages_republished": messages_republished,
            "messages_failed": messages_failed,
            "dry_run": dry_run,
            "details": details
        })


@functions_framework.http
def receive_webhook(request):
    """
    HTTP webhook receiver for Azure DevOps pipeline.

    Validates the request and publishes a message to Pub/Sub for async processing.
    This decouples the webhook acknowledgment from the actual PR review processing.

    Authentication:
        Requires GCP IAM authentication with roles/run.invoker permission.
        Callers must provide a valid Google-signed identity token in the
        Authorization: Bearer <token> header. Authentication is enforced by
        Cloud Run before this function is invoked.

    Request Format:
        POST /
        Content-Type: application/json
        Authorization: Bearer <google-identity-token>

        {
            "pr_id": 357462,
            "commit_sha": "abc123def456789...",
            "debug": false
        }

    Response (202 Accepted):
        {
            "status": "queued",
            "message_id": "1234567890",
            "pr_id": 357462,
            "commit_sha": "abc123de"
        }
    """
    with timed_operation() as elapsed:
        logger.info("=" * 60)
        logger.info("[WEBHOOK] PR Review webhook received")

        # Load minimal config
        config, missing = load_webhook_config()
        if missing:
            logger.error(f"[CONFIG] Missing required environment variables: {missing}")
            return {"error": f"Server configuration error: missing {missing}"}, 500

        # Note: Authentication is handled by Cloud Run IAM.
        # Only authorized service accounts/users with roles/run.invoker can reach this code.
        logger.info("[AUTH] Request authenticated via GCP IAM")

        # Parse JSON body
        try:
            data = request.get_json(force=True)
        except Exception as e:
            logger.error(f"[PARSE] Invalid JSON body: {e}")
            return {"error": "Invalid JSON body"}, 400

        if not data:
            logger.error("[PARSE] Empty request body")
            return {"error": "Empty request body"}, 400

        # Validate required fields
        pr_id = data.get("pr_id")
        commit_sha = data.get("commit_sha")
        debug = data.get("debug", False)

        if not pr_id:
            logger.error("[PARSE] Missing pr_id in request")
            return {"error": "Missing required field: pr_id"}, 400

        if not commit_sha:
            logger.error("[PARSE] Missing commit_sha in request")
            return {"error": "Missing required field: commit_sha"}, 400

        # Validate types
        try:
            pr_id = int(pr_id)
        except (ValueError, TypeError):
            logger.error(f"[PARSE] Invalid pr_id: {pr_id}")
            return {"error": "pr_id must be an integer"}, 400

        if not isinstance(commit_sha, str) or len(commit_sha) < 7:
            logger.error(f"[PARSE] Invalid commit_sha: {commit_sha}")
            return {"error": "commit_sha must be a string of at least 7 characters"}, 400

        logger.info(f"[WEBHOOK] PR #{pr_id} @ {commit_sha[:8]} | Debug: {debug}")

        # Build Pub/Sub message
        message = {
            "pr_id": pr_id,
            "commit_sha": commit_sha,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "source": "azure-devops-pipeline",
            "debug": debug
        }

        # Publish to Pub/Sub
        try:
            publisher = pubsub_v1.PublisherClient()
            topic_path = publisher.topic_path(config["VERTEX_PROJECT"], config["PUBSUB_TOPIC"])

            message_bytes = json.dumps(message).encode("utf-8")

            with timed_operation() as pubsub_elapsed:
                future = publisher.publish(topic_path, message_bytes)
                message_id = future.result(timeout=30)

            logger.info(f"[PUBSUB] Published message {message_id} to {config['PUBSUB_TOPIC']} | {pubsub_elapsed():.0f}ms")

        except Exception as e:
            logger.error(f"[PUBSUB] Failed to publish message: {e}")
            return {"error": f"Failed to queue message: {str(e)}"}, 500

        logger.info(f"[COMPLETE] Webhook processed | PR #{pr_id} queued | {elapsed():.0f}ms")
        logger.info("=" * 60)

        return {
            "status": "queued",
            "message_id": message_id,
            "pr_id": pr_id,
            "commit_sha": commit_sha[:8]
        }, 202
