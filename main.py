#!/usr/bin/env python3
"""
RAWL 9001 POC - PR Regression Review Cloud Function

HTTP Cloud Function that fetches a Pull Request from Azure DevOps, sends it to
Gemini (Vertex AI) for regression-focused review, stores the result in Cloud Storage,
and optionally comments/rejects the PR based on severity.

Environment Variables:
    API_KEY               - API key for authenticating requests
    GCS_BUCKET            - Cloud Storage bucket for storing reviews
    AZURE_DEVOPS_PAT      - Personal Access Token
    AZURE_DEVOPS_ORG      - Organization name
    AZURE_DEVOPS_PROJECT  - Project name  
    AZURE_DEVOPS_REPO     - Repository name (or ID)
    VERTEX_PROJECT        - GCP Project ID
    VERTEX_LOCATION       - GCP Region (default: us-central1)
    PUBSUB_TOPIC          - Pub/Sub topic for webhook messages (default: pr-review-trigger)

Entry Points:
    review_pr          - HTTP endpoint for synchronous PR review
    review_pr_pubsub   - Pub/Sub triggered async PR review (with idempotency)
    receive_webhook    - HTTP webhook receiver that publishes to Pub/Sub
"""

import os
import json
import logging
import time
import requests
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

import base64

import functions_framework
from cloudevents.http import CloudEvent
from google import genai
from google.cloud import storage
from google.cloud import pubsub_v1
from google.api_core.exceptions import PreconditionFailed


# =============================================================================
# Timing Utilities for External Operations
# =============================================================================

@contextmanager
def timed_operation():
    """Context manager that tracks operation timing.
    
    Yields a callable that returns elapsed milliseconds since context entry.
    Use for external API calls and storage operations only.
    
    Example:
        with timed_operation() as elapsed:
            response = requests.get(url)
            logger.info(f"Request completed in {elapsed():.0f}ms")
    """
    start_time = time.time()
    yield lambda: (time.time() - start_time) * 1000


# =============================================================================
# Logging Configuration
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Constants
# =============================================================================

DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD = 20  # Default file count threshold for extensive PR detection
DEFAULT_EXTENSIVE_PR_SIZE_THRESHOLD = 500000  # Default character count threshold for extensive PR detection (500KB)


# =============================================================================
# Configuration
# =============================================================================

def load_config() -> tuple[dict, list]:
    """Load configuration from environment variables.
    
    Returns:
        tuple: (config dict, list of missing required vars)
    """
    required = [
        "API_KEY",
        "GCS_BUCKET",
        "AZURE_DEVOPS_PAT",
        "AZURE_DEVOPS_ORG", 
        "AZURE_DEVOPS_PROJECT",
        "AZURE_DEVOPS_REPO",
        "VERTEX_PROJECT",
    ]
    
    config = {}
    missing = []
    
    for var in required:
        value = os.environ.get(var)
        if not value:
            missing.append(var)
        config[var] = value
    
    # Optional with defaults
    config["VERTEX_LOCATION"] = os.environ.get("VERTEX_LOCATION", "us-central1")
    config["DLQ_SUBSCRIPTION"] = os.environ.get("DLQ_SUBSCRIPTION", "pr-review-dlq-sub")
    config["GEMINI_MODEL"] = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
    
    # Extensive PR filtering configuration
    config["EXTENSIVE_PR_FILE_THRESHOLD"] = int(os.environ.get("EXTENSIVE_PR_FILE_THRESHOLD", str(DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD)))
    config["EXTENSIVE_PR_SIZE_THRESHOLD"] = int(os.environ.get("EXTENSIVE_PR_SIZE_THRESHOLD", str(DEFAULT_EXTENSIVE_PR_SIZE_THRESHOLD)))
    config["FILTER_MARKDOWN_FILES"] = os.environ.get("FILTER_MARKDOWN_FILES", "true").lower() == "true"
    
    return config, missing


# =============================================================================
# Azure DevOps API Client
# =============================================================================

class AzureDevOpsClient:
    """Simple client for Azure DevOps REST API."""
    
    API_VERSION = "7.1-preview"
    
    def __init__(self, org: str, project: str, repo: str, pat: str):
        self.org = org
        self.project = project
        self.base_url = f"https://dev.azure.com/{org}/{project}/_apis"
        self.repo = repo
        self.auth = ("", pat)  # Basic auth with empty username
    
    def _request(self, method: str, endpoint: str, data: dict = None, extra_params: dict = None) -> dict:
        """Make HTTP request to Azure DevOps API with timing.
        
        Args:
            method: HTTP method (GET, POST, PUT)
            endpoint: API endpoint path
            data: Request body for POST/PUT requests
            extra_params: Additional query parameters (merged with api-version)
            
        Returns:
            Response JSON as dict
        """
        url = f"{self.base_url}{endpoint}"
        params = extra_params.copy() if extra_params else {}
        params["api-version"] = self.API_VERSION
        headers = {"Content-Type": "application/json"} if method in ("POST", "PUT") else None
        
        logger.info(f"[ADO {method}] {endpoint}")
        if data:
            logger.debug(f"[ADO {method}] Payload keys: {list(data.keys())}")
        
        start_time = time.time()
        try:
            response = requests.request(
                method, url, auth=self.auth, params=params, headers=headers, json=data
            )
            elapsed = (time.time() - start_time) * 1000
            
            logger.info(f"[ADO {method}] {endpoint} | Status: {response.status_code} | {elapsed:.0f}ms")
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"[ADO {method}] {endpoint} | FAILED | Status: {e.response.status_code} | {elapsed:.0f}ms")
            logger.error(f"[ADO {method}] Error response: {e.response.text[:500]}")
            raise
    
    def _get(self, endpoint: str, params: dict = None) -> dict:
        """Make GET request to Azure DevOps API."""
        return self._request("GET", endpoint, extra_params=params)
    
    def _post(self, endpoint: str, data: dict) -> dict:
        """Make POST request to Azure DevOps API."""
        return self._request("POST", endpoint, data=data)
    
    def _put(self, endpoint: str, data: dict) -> dict:
        """Make PUT request to Azure DevOps API."""
        return self._request("PUT", endpoint, data=data)
    
    def get_pull_request(self, pr_id: int) -> dict:
        """Fetch PR metadata."""
        return self._get(f"/git/repositories/{self.repo}/pullrequests/{pr_id}")
    
    def get_pr_iterations(self, pr_id: int) -> list:
        """Get PR iterations (each push creates a new iteration)."""
        result = self._get(f"/git/repositories/{self.repo}/pullrequests/{pr_id}/iterations")
        return result.get("value", [])
    
    def get_pr_changes(self, pr_id: int, iteration_id: int = None) -> list:
        """Get changed files in a PR iteration."""
        if iteration_id is None:
            iterations = self.get_pr_iterations(pr_id)
            if not iterations:
                return []
            iteration_id = iterations[-1]["id"]  # Latest iteration
        
        result = self._get(
            f"/git/repositories/{self.repo}/pullrequests/{pr_id}/iterations/{iteration_id}/changes"
        )
        return result.get("changeEntries", [])
    
    def get_file_content(self, path: str, commit_id: str) -> str:
        """Fetch file content at a specific commit."""
        url = f"{self.base_url}/git/repositories/{self.repo}/items"
        params = {
            "path": path,
            "versionDescriptor.version": commit_id,
            "versionDescriptor.versionType": "commit",
            "api-version": self.API_VERSION,
        }
        
        logger.debug(f"[ADO FILE] Fetching: {path} @ {commit_id[:8]}")
        
        with timed_operation() as elapsed:
            try:
                response = requests.get(url, auth=self.auth, params=params)
                response.raise_for_status()
                logger.debug(f"[ADO FILE] {path} | {len(response.text)} bytes | {elapsed():.0f}ms")
                return response.text
            except requests.HTTPError as e:
                logger.debug(f"[ADO FILE] {path} | Not found (status {e.response.status_code}) | {elapsed():.0f}ms")
                return None  # File might not exist in this version
    
    def get_pr_diff(self, pr_id: int) -> list:
        """
        Get full diff for a PR with file contents from both source and target.
        Returns list of dicts with path, change_type, source_content, target_content.
        """
        logger.info(f"[ADO] Fetching full diff for PR #{pr_id}")
        
        with timed_operation() as elapsed:
            pr = self.get_pull_request(pr_id)
            source_commit = pr["lastMergeSourceCommit"]["commitId"]
            target_commit = pr["lastMergeTargetCommit"]["commitId"]
            logger.info(f"[ADO] PR commits: source={source_commit[:8]} target={target_commit[:8]}")
            
            changes = self.get_pr_changes(pr_id)
            logger.info(f"[ADO] Found {len(changes)} changed items in PR")
            
            file_diffs = []
            files_processed = 0
            for change in changes:
                item = change.get("item", {})
                path = item.get("path", "")
                change_type = change.get("changeType", "unknown")
                
                # Skip folders
                if item.get("isFolder"):
                    logger.debug(f"[ADO] Skipping folder: {path}")
                    continue
                
                # Get content from both versions
                source_content = self.get_file_content(path, source_commit)
                target_content = self.get_file_content(path, target_commit)
                
                file_diffs.append({
                    "path": path,
                    "change_type": change_type,
                    "source_content": source_content,  # New version (PR branch)
                    "target_content": target_content,  # Old version (target branch)
                })
                files_processed += 1
            
            logger.info(f"[ADO] Diff complete: {files_processed} files | {elapsed():.0f}ms total")
            
            return file_diffs
    
    def post_pr_comment(self, pr_id: int, content: str) -> dict:
        """Post a comment thread on a PR.
        
        Args:
            pr_id: Pull request ID
            content: Markdown content for the comment
            
        Returns:
            API response dict
        """
        data = {
            "comments": [
                {
                    "parentCommentId": 0,
                    "content": content,
                    "commentType": 1,  # Text comment
                }
            ],
            "status": 1,  # Active
        }
        return self._post(f"/git/repositories/{self.repo}/pullrequests/{pr_id}/threads", data)
    
    def reject_pr(self, pr_id: int, reviewer_id: str) -> dict:
        """Reject a PR by voting -10 (reject).
        
        Args:
            pr_id: Pull request ID
            reviewer_id: The reviewer's identity ID (usually the PAT owner's ID)
            
        Returns:
            API response dict
        """
        # Vote values: 10=approved, 5=approved with suggestions, 0=no vote, -5=waiting, -10=rejected
        data = {"vote": -10}
        return self._put(
            f"/git/repositories/{self.repo}/pullrequests/{pr_id}/reviewers/{reviewer_id}",
            data
        )
    
    def get_current_user_id(self) -> str:
        """Get the current user's ID (PAT owner) from Azure DevOps.
        
        Returns:
            User's identity ID string
        """
        # Use the connection data endpoint to get current user info
        url = f"https://dev.azure.com/{self.org}/_apis/connectionData"
        params = {"api-version": self.API_VERSION}
        
        logger.info("[ADO] Fetching current user identity")
        
        with timed_operation() as elapsed:
            try:
                response = requests.get(url, auth=self.auth, params=params)
                response.raise_for_status()
                data = response.json()
                
                user_id = data["authenticatedUser"]["id"]
                user_name = data["authenticatedUser"].get("providerDisplayName", "unknown")
                logger.info(f"[ADO] Current user: {user_name} (id={user_id[:8]}...) | {elapsed():.0f}ms")
                
                return user_id
            except requests.HTTPError as e:
                logger.error(f"[ADO] Failed to get user identity | Status: {e.response.status_code} | {elapsed():.0f}ms")
                raise


# =============================================================================
# Cloud Storage
# =============================================================================

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


# =============================================================================
# Idempotency - Prevent duplicate processing via GCS markers
# =============================================================================

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


# =============================================================================
# Severity Detection
# =============================================================================

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


# =============================================================================
# File Filtering for Extensive PRs
# =============================================================================

def filter_markdown_files(file_diffs: list) -> tuple[list, int]:
    """Filter out .md files from file_diffs list.
    
    Filters files based on path ending with .md (case-insensitive).
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
        # Check if path ends with .md (case-insensitive)
        if path.lower().endswith(".md"):
            filtered_count += 1
            filtered_paths.append(path)
            logger.debug(f"[FILTER] Excluding markdown file: {path}")
        else:
            filtered.append(diff)
    
    if filtered_count > 0:
        logger.info(f"[FILTER] Filtered out {filtered_count} markdown file(s): {', '.join(filtered_paths)}")
    
    return filtered, filtered_count


def is_extensive_pr(file_diffs: list, config: dict) -> bool:
    """Determine if a PR is extensive based on file count or total size.
    
    A PR is considered extensive if:
    - File count exceeds EXTENSIVE_PR_FILE_THRESHOLD, OR
    - Total content size (sum of all file content lengths) exceeds EXTENSIVE_PR_SIZE_THRESHOLD
    
    Args:
        file_diffs: List of file diff dicts with 'source_content' and 'target_content' keys
        config: Configuration dictionary with threshold values
        
    Returns:
        True if PR is considered extensive
    """
    file_count = len(file_diffs)
    file_threshold = config.get("EXTENSIVE_PR_FILE_THRESHOLD", DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD)
    
    # Check file count threshold
    if file_count >= file_threshold:
        logger.info(f"[EXTENSIVE] PR detected as extensive: {file_count} files (threshold: {file_threshold})")
        return True
    
    # Calculate total content size
    total_size = 0
    for diff in file_diffs:
        source_content = diff.get("source_content") or ""
        target_content = diff.get("target_content") or ""
        total_size += len(source_content) + len(target_content)
    
    size_threshold = config.get("EXTENSIVE_PR_SIZE_THRESHOLD", DEFAULT_EXTENSIVE_PR_SIZE_THRESHOLD)
    
    # Check size threshold
    if total_size >= size_threshold:
        logger.info(f"[EXTENSIVE] PR detected as extensive: {total_size} chars (threshold: {size_threshold})")
        return True
    
    return False


# =============================================================================
# Gemini Review Prompt
# =============================================================================

SYSTEM_PROMPT = """You are a supportive senior AEM frontend developer helping your team ship quality code with confidence. You are also a senior QA engineer and accessibility expert. Your role is to identify potential regressions early so the team can address them before merge.

Your expertise covers:
- AEM 6.5 components and dialogs
- HTL (Sightly) templating
- Vanilla JavaScript (no frameworks)
- CSS styling
- HTML structure 
- Web Content Accessibility Guidelines 2.2 

## Review Focus: Regression Analysis for AEM Frontend Components

Analyze the pull request changes for potential regressions that could affect existing functionality:

1. **Dialog Changes**: Removed or restructured AEM dialogs that authors depend on
2. **Function Changes**: Deleted public functions or methods that other components may call
3. **Behavior Changes**: Modified logic that changes how existing features work
4. **API Stability**: Changes to data-attributes, CSS classes, or JS interfaces that consumers rely on
5. **HTL Contract Changes**: Modified Sling Model properties, template parameters, or data structures
6. **CSS Changes**: Renamed/removed classes, changed specificity, or removed styles
7. **HTML Structure Changes**: Modified HTML structure, properties that are passed to the javascript that do not include default values, prefer using java model or layout that affects page rendering

## Output Format

Generate a markdown review report with these sections:

# PR Review: {PR Title}

**PR #{id}** | Author: {author} | {date}

## Summary
Brief description of what this PR changes (2-3 sentences).

## What's Working Well
Acknowledge positive patterns, good practices, or thoughtful implementations observed in the PR. If the changes are solid, say so.

## Impact Assessment

### ⚠️ Requires Attention Before Merge
List changes that need to be addressed or verified before merging. Each item should explain:
- What changed
- What to verify
- Who should be consulted

### 👀 Worth Verifying
List changes that should be tested depending on usage. Include:
- The change in question
- Potential impact
- Suggested verification steps

### ✅ Low Concern
List changes that are unlikely to cause issues but are worth noting for awareness.

## Recommended Test Coverage
Specific test scenarios that should be validated before merge (focus on accessibility and regression testing):
1. {scenario with expected behavior}
2. {scenario with expected behavior}
...

## Detailed Findings

For each significant finding, use this format:

### Finding: {Brief description}

**Priority:** action-required | review-recommended | note
**Applies to:** {file path}
**Category:** security | aem | frontend | testing

{Explanation of the finding in plain sentences}

#### Before
```{language}
{old code}
```

#### After
```{language}
{new code}
```

#### Context for the Team
{Explain the impact and reasoning so everyone understands the tradeoffs}

#### Suggested Approach
{Brief explanation of a recommended path forward. Choose the simplest approach.}
```{language}
{suggested code}
``` 


---

## Guidelines

- Be specific about file paths and line references
- Prioritize findings by potential impact, not code style
- Focus on helping the team understand what to validate before deploying
- If no significant concerns are found, acknowledge the solid work
- Keep the report under 200 lines total
- Focus only on concrete concerns observed in the diff
- Keep feedback clear and actionable

## Priority Guidelines

Use the following criteria to determine priority:

- **action-required**: Security vulnerabilities, breaking changes, data loss risks, or production-impacting bugs that need resolution before merge
- **review-recommended**: Code quality considerations, performance concerns, potential edge cases, or changes benefiting from extra testing
- **note**: Minor observations, suggestions for future consideration, or context that may be helpful

## Closing the Review

The main objective is to provide clear impact analysis of changes on the existing codebase. Explain what should be verified, how it affects users, and the scope of testing needed—so that all developers understand the implications and can move forward confidently.

When the PR is solid overall, acknowledge the author's effort. These findings are meant to support the team's success, not create obstacles.
"""


def build_review_prompt(pr: dict, file_diffs: list) -> str:
    """Build the prompt with PR context and file diffs."""
    
    prompt_parts = [
        f"# Pull Request to Review\n",
        f"**Title:** {pr.get('title', 'Untitled')}",
        f"**ID:** {pr.get('pullRequestId')}",
        f"**Author:** {pr.get('createdBy', {}).get('displayName', 'Unknown')}",
        f"**Description:**\n{pr.get('description', 'No description provided.')}\n",
        f"**Source Branch:** {pr.get('sourceRefName', '').replace('refs/heads/', '')}",
        f"**Target Branch:** {pr.get('targetRefName', '').replace('refs/heads/', '')}\n",
        "---\n",
        "# File Changes\n",
    ]
    
    for diff in file_diffs:
        path = diff["path"]
        change_type = diff["change_type"]
        
        prompt_parts.append(f"## {path}")
        prompt_parts.append(f"**Change Type:** {change_type}\n")
        
        if change_type in ("delete", "delete, sourceRename"):
            prompt_parts.append("### Deleted Content (TARGET - being removed):")
            prompt_parts.append(f"```\n{diff['target_content'] or '(empty)'}\n```\n")
        
        elif change_type in ("add",):
            prompt_parts.append("### Added Content (SOURCE - new file):")
            prompt_parts.append(f"```\n{diff['source_content'] or '(empty)'}\n```\n")
        
        else:  # edit, rename, etc.
            prompt_parts.append("### Before (TARGET - current version):")
            prompt_parts.append(f"```\n{diff['target_content'] or '(file did not exist)'}\n```\n")
            prompt_parts.append("### After (SOURCE - proposed changes):")
            prompt_parts.append(f"```\n{diff['source_content'] or '(file will be deleted)'}\n```\n")
        
        prompt_parts.append("---\n")
    
    prompt_parts.append("\nPlease provide your regression-focused review.")
    
    return "\n".join(prompt_parts)


# =============================================================================
# Vertex AI / Gemini
# =============================================================================

def call_gemini(config: dict, prompt: str) -> str:
    """Send prompt to Gemini via Vertex AI and return response."""
    
    model_name = config["GEMINI_MODEL"]
    project = config["VERTEX_PROJECT"]
    location = config["VERTEX_LOCATION"]
    
    logger.info(f"[GEMINI] Calling Vertex AI | Model: {model_name} | Project: {project} | Location: {location}")
    logger.info(f"[GEMINI] Prompt size: {len(prompt)} chars | System prompt: {len(SYSTEM_PROMPT)} chars")
    logger.debug(f"[GEMINI] Config: max_output_tokens=8192, temperature=0.2")
    
    with timed_operation() as elapsed:
        try:
            # Initialize the GenAI client for Vertex AI
            client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
            )
            
            logger.debug(f"[GEMINI] Client initialized in {elapsed():.0f}ms")
            
            # Generate content with system instruction
            generate_start = time.time()
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "system_instruction": SYSTEM_PROMPT,
                    "max_output_tokens": 8192,
                    "temperature": 0.2,  # Lower for more focused analysis
                },
            )
            
            generate_time = (time.time() - generate_start) * 1000
            response_size = len(response.text) if response.text else 0
            
            logger.info(f"[GEMINI] Response received | {response_size} chars | Generate: {generate_time:.0f}ms | Total: {elapsed():.0f}ms")
            
            # Log usage metadata if available
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = response.usage_metadata
                logger.info(f"[GEMINI] Tokens - Input: {getattr(usage, 'prompt_token_count', 'N/A')} | Output: {getattr(usage, 'candidates_token_count', 'N/A')}")
            
            return response.text
            
        except Exception as e:
            logger.error(f"[GEMINI] API call FAILED | {elapsed():.0f}ms | Error type: {type(e).__name__}")
            logger.error(f"[GEMINI] Error details: {str(e)}")
            raise


# =============================================================================
# Core Review Logic (Shared by HTTP and Pub/Sub Entry Points)
# =============================================================================

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
    markdown_files_filtered: int
    is_extensive: bool
    files_limited: int


def filter_and_limit_files(file_diffs: list, config: dict) -> FilterResult:
    """Filter markdown files and limit files for extensive PRs.
    
    Applies two filtering operations:
    1. Filters out markdown files if FILTER_MARKDOWN_FILES is enabled
    2. Limits files to EXTENSIVE_PR_FILE_THRESHOLD if PR is extensive
    
    Args:
        file_diffs: List of file diff dicts with 'path' key
        config: Configuration dictionary with filtering settings
        
    Returns:
        FilterResult with filtered files and metadata about filtering operations
    """
    original_file_count = len(file_diffs)
    markdown_files_filtered = 0
    
    # Filter markdown files if enabled
    if config.get("FILTER_MARKDOWN_FILES", True):
        logger.info(f"[FILTER] Filtering markdown files from review")
        file_diffs, markdown_files_filtered = filter_markdown_files(file_diffs)
        logger.info(f"[FILTER] After filtering: {len(file_diffs)} files remaining (removed {markdown_files_filtered} markdown files)")
        
        if len(file_diffs) == 0:
            logger.warning(f"[FILTER] All files were markdown files - review will proceed with empty file list")
    else:
        logger.debug(f"[FILTER] Markdown filtering disabled via configuration")
    
    # Check if PR is extensive and limit files if needed
    is_extensive = is_extensive_pr(file_diffs, config)
    files_limited = 0
    
    if is_extensive:
        file_threshold = config.get("EXTENSIVE_PR_FILE_THRESHOLD", DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD)
        if len(file_diffs) > file_threshold:
            files_limited = len(file_diffs) - file_threshold
            file_diffs = file_diffs[:file_threshold]
            logger.info(f"[LIMIT] Extensive PR detected - limiting review to first {file_threshold} files (excluded {files_limited} files)")
    
    return FilterResult(
        filtered_files=file_diffs,
        original_file_count=original_file_count,
        markdown_files_filtered=markdown_files_filtered,
        is_extensive=is_extensive,
        files_limited=files_limited
    )


def process_pr_review(
    config: dict,
    ado: "AzureDevOpsClient",
    pr_id: int,
    pr: dict,
    file_diffs: list,
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
        
    Returns:
        ReviewResult with all review details and actions taken
    """
    pr_title = pr.get("title", "Untitled")
    pr_author = pr.get("createdBy", {}).get("displayName", "Unknown")
    
    logger.info(f"[REVIEW] Starting review for PR #{pr_id}: '{pr_title}' by {pr_author}")
    logger.info(f"[REVIEW] Files to review: {len(file_diffs)}")
    
    # Filter and limit files based on configuration
    filter_result = filter_and_limit_files(file_diffs, config)
    file_diffs = filter_result.filtered_files
    is_extensive = filter_result.is_extensive
    files_limited = filter_result.files_limited
    
    # Build prompt and call Gemini
    logger.info("[REVIEW] Building prompt and calling Gemini")
    prompt = build_review_prompt(pr, file_diffs)
    logger.info(f"[REVIEW] Prompt built: {len(prompt)} chars")
    
    review = call_gemini(config, prompt)
    
    # Determine severity
    logger.info("[REVIEW] Analyzing severity")
    max_severity = get_max_severity(review)
    has_blocking = max_severity == "action-required"
    has_warning = max_severity == "review-recommended"
    logger.info(f"[REVIEW] Priority: {max_severity} | action_required={has_blocking} | review_recommended={has_warning}")
    
    # Save to Cloud Storage
    logger.info("[REVIEW] Saving to Cloud Storage")
    storage_path = save_to_storage(config["GCS_BUCKET"], pr_id, review)
    
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


# =============================================================================
# HTTP Cloud Function Entry Point
# =============================================================================

def make_response(data: dict, status: int = 200) -> tuple:
    """Create a JSON response tuple."""
    return (json.dumps(data), status, {"Content-Type": "application/json"})


@functions_framework.http
def review_pr(request):
    """HTTP Cloud Function entry point for PR regression review.
    
    Request:
        POST with JSON body: {"pr_id": 12345}
        Header: X-API-Key: <your-api-key>
        
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
        
        # Validate API key
        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key != config["API_KEY"]:
            logger.warning("[AUTH] Invalid or missing API key")
            return make_response({"error": "Invalid or missing API key"}, 401)
        logger.info("[AUTH] API key validated")
        
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
            logger.info(f"[REQUEST] Processing PR #{pr_id}")
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
            result = process_pr_review(config, ado, pr_id, pr, file_diffs)
            
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


# =============================================================================
# Pub/Sub Cloud Function Entry Point (with Idempotency)
# =============================================================================

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
            "source": "azure-devops-pipeline"
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
            if message_commit_sha:
                logger.info(f"[PUBSUB] Processing PR #{pr_id} @ {message_commit_sha[:8]} (from message)")
            else:
                logger.info(f"[PUBSUB] Processing PR #{pr_id} (commit_sha will be fetched from ADO)")
            
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
            result = process_pr_review(config, ado, pr_id, pr, file_diffs)
            
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


# =============================================================================
# Webhook Receiver Cloud Function Entry Point
# =============================================================================

def load_webhook_config() -> tuple[dict, list]:
    """Load minimal configuration for webhook receiver.
    
    Returns:
        tuple: (config dict, list of missing required vars)
    """
    required = ["API_KEY", "VERTEX_PROJECT"]
    
    config = {}
    missing = []
    
    for var in required:
        value = os.environ.get(var)
        if not value:
            missing.append(var)
        config[var] = value
    
    # Optional with default
    config["PUBSUB_TOPIC"] = os.environ.get("PUBSUB_TOPIC", "pr-review-trigger")
    
    return config, missing


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

    Request Format:
        POST /
        Content-Type: application/json
        X-API-Key: <api-key>

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

        # Validate API key
        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key != config["API_KEY"]:
            logger.warning("[AUTH] Invalid or missing API key")
            return make_response({"error": "Invalid or missing API key"}, 401)

        logger.info("[AUTH] API key validated")

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

    Request Format:
        POST /
        Content-Type: application/json
        X-API-Key: <api-key>

        {
            "pr_id": 357462,
            "commit_sha": "abc123def456789..."
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
        
        # Load minimal config (only need API_KEY and PUBSUB_TOPIC)
        config, missing = load_webhook_config()
        if missing:
            logger.error(f"[CONFIG] Missing required environment variables: {missing}")
            return {"error": f"Server configuration error: missing {missing}"}, 500
        
        # Validate API key
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            logger.warning("[AUTH] Missing X-API-Key header")
            return {"error": "Missing X-API-Key header"}, 401
        
        if api_key != config["API_KEY"]:
            logger.warning("[AUTH] Invalid API key")
            return {"error": "Invalid API key"}, 401
        
        logger.info("[AUTH] API key validated")
        
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
        
        logger.info(f"[WEBHOOK] PR #{pr_id} @ {commit_sha[:8]}")
        
        # Build Pub/Sub message
        message = {
            "pr_id": pr_id,
            "commit_sha": commit_sha,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "source": "azure-devops-pipeline"
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
