"""Unit tests for PR Regression Review Cloud Function.

Run with: pytest tests/ -v
"""

import pytest
from unittest.mock import MagicMock, patch

from google.api_core.exceptions import PreconditionFailed

from pr_review.azure_client import AzureDevOpsClient
from pr_review.storage import save_to_storage
from pr_review.severity import get_max_severity
from pr_review.prompt import build_review_prompt
from pr_review.idempotency import (
    check_and_claim_processing,
    update_marker_completed,
    update_marker_for_retry,
    update_marker_failed,
    MAX_RETRY_ATTEMPTS,
)
from pr_review.config import (
    DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD,
    DEFAULT_EXTENSIVE_PR_SIZE_THRESHOLD,
    load_webhook_config,
)
from pr_review.entry_points import receive_webhook
from pr_review.review import process_pr_review
from pr_review.models import ReviewResult
from pr_review.filtering import filter_non_code_files, is_extensive_pr


# =============================================================================
# ReviewResult Tests
# =============================================================================

class TestReviewResult:
    """Tests for ReviewResult dataclass."""

    def test_review_result_creation(self):
        """ReviewResult can be instantiated with all fields."""
        result = ReviewResult(
            pr_id=12345,
            pr_title="Test PR",
            pr_author="John Doe",
            files_changed=3,
            max_severity="warning",
            has_blocking=False,
            has_warning=True,
            review_text="# Review\n\nSome review text",
            storage_path="gs://bucket/reviews/2026/01/07/pr-12345.md",
            commented=True,
            action_taken="commented",
        )

        assert result.pr_id == 12345
        assert result.pr_title == "Test PR"
        assert result.pr_author == "John Doe"
        assert result.files_changed == 3
        assert result.max_severity == "warning"
        assert result.has_blocking is False
        assert result.has_warning is True
        assert "Review" in result.review_text
        assert result.storage_path.startswith("gs://")
        assert result.commented is True
        assert result.action_taken == "commented"

    def test_review_result_no_action(self):
        """ReviewResult with no action (info severity)."""
        result = ReviewResult(
            pr_id=12345,
            pr_title="Clean PR",
            pr_author="Jane Doe",
            files_changed=1,
            max_severity="info",
            has_blocking=False,
            has_warning=False,
            review_text="# Review\n\nNo issues found.",
            storage_path="gs://bucket/reviews/2026/01/07/pr-12345.md",
            commented=False,
            action_taken=None,
        )

        assert result.max_severity == "info"
        assert result.commented is False
        assert result.action_taken is None


# =============================================================================
# Core Review Logic Tests
# =============================================================================

class TestProcessPrReview:
    """Tests for process_pr_review shared function."""

    def test_process_review_blocking_severity(self, ado_client, sample_pr, sample_file_diffs, mocker):
        """process_pr_review returns action-required result and posts comment + failed status check."""
        config = {"GCS_BUCKET": "test-bucket"}

        # Mock Gemini to return action-required review
        mock_review = """# PR Review

### Finding: Critical regression
**Priority:** action-required

This change breaks existing functionality."""
        mocker.patch("pr_review.gemini.call_gemini", return_value=mock_review)
        mocker.patch("pr_review.storage.save_to_storage", return_value="gs://test-bucket/reviews/pr-12345.md")
        mocker.patch.object(ado_client, "post_pr_comment")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, sample_file_diffs)

        assert result.max_severity == "action-required"
        assert result.has_blocking is True
        assert result.commented is True
        assert result.action_taken == "status:failed"

        # Verify comment was posted with correct header
        ado_client.post_pr_comment.assert_called_once()
        comment_text = ado_client.post_pr_comment.call_args[0][1]
        assert "Automated Regression Review" in comment_text
        assert "Action required before merge" in comment_text
        assert "John Doe" in comment_text  # Author name included

        # Verify failed status check was posted (not a personal vote/rejection)
        ado_client.post_pr_status.assert_called_once()
        status_args = ado_client.post_pr_status.call_args
        assert status_args[0][0] == 12345  # pr_id positional
        assert status_args[1]["state"] == "failed"

    def test_process_review_blocking_with_just_comment_ticket(self, ado_client, sample_pr, sample_file_diffs, mocker):
        """process_pr_review posts comment but does not post failed status when JUST_COMMENT_TICKET is enabled."""
        config = {"GCS_BUCKET": "test-bucket", "JUST_COMMENT_TICKET": True}

        # Mock Gemini to return action-required review
        mock_review = """# PR Review

### Finding: Critical regression
**Priority:** action-required

This change breaks existing functionality."""
        mocker.patch("pr_review.gemini.call_gemini", return_value=mock_review)
        mocker.patch("pr_review.storage.save_to_storage", return_value="gs://test-bucket/reviews/pr-12345.md")
        mocker.patch.object(ado_client, "post_pr_comment")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, sample_file_diffs)

        assert result.max_severity == "action-required"
        assert result.has_blocking is True
        assert result.commented is True
        assert result.action_taken == "commented"  # Should be commented, not status:failed

        # Verify comment was posted
        ado_client.post_pr_comment.assert_called_once()
        comment_text = ado_client.post_pr_comment.call_args[0][1]
        assert "Automated Regression Review" in comment_text
        assert "Action required before merge" in comment_text

        # Verify failed status was NOT posted (due to JUST_COMMENT_TICKET)
        ado_client.post_pr_status.assert_not_called()

    def test_process_review_warning_severity(self, ado_client, sample_pr, sample_file_diffs, mocker):
        """process_pr_review returns review-recommended result, posts comment and succeeded status."""
        config = {"GCS_BUCKET": "test-bucket"}

        mock_review = """# PR Review

### Finding: Potential issue
**Priority:** review-recommended

This might cause problems."""
        mocker.patch("pr_review.gemini.call_gemini", return_value=mock_review)
        mocker.patch("pr_review.storage.save_to_storage", return_value="gs://test-bucket/reviews/pr-12345.md")
        mocker.patch.object(ado_client, "post_pr_comment")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, sample_file_diffs)

        assert result.max_severity == "review-recommended"
        assert result.has_blocking is False
        assert result.has_warning is True
        assert result.commented is True
        assert result.action_taken == "commented"

        # Verify comment was posted
        ado_client.post_pr_comment.assert_called_once()
        comment_text = ado_client.post_pr_comment.call_args[0][1]
        assert "Review recommended" in comment_text

        # Verify succeeded status was posted (not failed — warning does not block)
        ado_client.post_pr_status.assert_called_once()
        status_args = ado_client.post_pr_status.call_args
        assert status_args[0][0] == 12345
        assert status_args[1]["state"] == "succeeded"

    def test_process_review_info_severity(self, ado_client, sample_pr, sample_file_diffs, mocker):
        """process_pr_review returns note result: no comment, but succeeded status posted."""
        config = {"GCS_BUCKET": "test-bucket"}

        mock_review = """# PR Review

### Finding: Minor observation
**Priority:** note

Just a note."""
        mocker.patch("pr_review.gemini.call_gemini", return_value=mock_review)
        mocker.patch("pr_review.storage.save_to_storage", return_value="gs://test-bucket/reviews/pr-12345.md")
        mocker.patch.object(ado_client, "post_pr_comment")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, sample_file_diffs)

        assert result.max_severity == "note"
        assert result.has_blocking is False
        assert result.has_warning is False
        assert result.commented is False
        assert result.action_taken is None

        # Verify no comment was posted
        ado_client.post_pr_comment.assert_not_called()

        # Verify succeeded status was posted so branch policy is satisfied
        ado_client.post_pr_status.assert_called_once()
        status_args = ado_client.post_pr_status.call_args
        assert status_args[0][0] == 12345
        assert status_args[1]["state"] == "succeeded"

    def test_process_review_extracts_pr_metadata(self, ado_client, sample_pr, sample_file_diffs, mocker):
        """process_pr_review extracts title and author from PR metadata."""
        config = {"GCS_BUCKET": "test-bucket"}

        mocker.patch("pr_review.gemini.call_gemini", return_value="# Review\n**Severity:** info")
        mocker.patch("pr_review.storage.save_to_storage", return_value="gs://bucket/path.md")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, sample_file_diffs)

        assert result.pr_title == "Add new feature"
        assert result.pr_author == "John Doe"
        assert result.files_changed == 2  # sample_file_diffs has 2 files

    def test_process_review_stores_review_text(self, ado_client, sample_pr, sample_file_diffs, mocker):
        """process_pr_review includes full review text in result."""
        config = {"GCS_BUCKET": "test-bucket"}

        expected_review = "# Full Review Content\n\nDetailed analysis here."
        mocker.patch("pr_review.gemini.call_gemini", return_value=expected_review)
        mocker.patch("pr_review.storage.save_to_storage", return_value="gs://bucket/path.md")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, sample_file_diffs)

        assert result.review_text == expected_review

    def test_process_review_saves_to_storage(self, ado_client, sample_pr, sample_file_diffs, mocker):
        """process_pr_review saves review to cloud storage."""
        config = {"GCS_BUCKET": "my-bucket"}

        mocker.patch("pr_review.gemini.call_gemini", return_value="# Review")
        mock_save = mocker.patch("pr_review.storage.save_to_storage", return_value="gs://my-bucket/reviews/path.md")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, sample_file_diffs)

        mock_save.assert_called_once_with("my-bucket", 12345, "# Review")
        assert result.storage_path == "gs://my-bucket/reviews/path.md"


# =============================================================================
# AzureDevOpsClient Tests
# =============================================================================

class TestAzureDevOpsClientRequest:
    """Tests for AzureDevOpsClient._request method."""

    def test_request_get_success(self, ado_client, mocker):
        """GET request returns JSON response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 1, "name": "test"}

        mock_request = mocker.patch("pr_review.azure_client.requests.request", return_value=mock_response)

        result = ado_client._get("/test/endpoint")

        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "GET"
        assert "/test/endpoint" in call_args[0][1]
        assert result == {"id": 1, "name": "test"}

    def test_request_post_success(self, ado_client, mocker):
        """POST request sends payload and returns JSON."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"created": True}

        mock_request = mocker.patch("pr_review.azure_client.requests.request", return_value=mock_response)

        payload = {"content": "test data"}
        result = ado_client._post("/test/endpoint", payload)

        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[1]["json"] == payload
        assert result == {"created": True}

    def test_request_put_success(self, ado_client, mocker):
        """PUT request sends payload and returns JSON."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"updated": True}

        mock_request = mocker.patch("pr_review.azure_client.requests.request", return_value=mock_response)

        payload = {"vote": -10}
        result = ado_client._put("/test/endpoint", payload)

        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "PUT"
        assert call_args[1]["json"] == payload
        assert result == {"updated": True}


class TestAzureDevOpsClientMethods:
    """Tests for AzureDevOpsClient high-level methods."""

    def test_get_pull_request(self, ado_client, sample_pr, mocker):
        """get_pull_request fetches PR metadata."""
        mocker.patch.object(ado_client, "_get", return_value=sample_pr)

        result = ado_client.get_pull_request(12345)

        ado_client._get.assert_called_once_with("/git/repositories/test-repo/pullrequests/12345")
        assert result["pullRequestId"] == 12345
        assert result["title"] == "Add new feature"

    def test_get_file_content(self, ado_client, mocker):
        """get_file_content fetches file at specific commit."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "file content here"

        mocker.patch("pr_review.azure_client.requests.get", return_value=mock_response)

        result = ado_client.get_file_content("/src/file.js", "abc123")

        assert result == "file content here"

    def test_get_pr_diff(self, ado_client, sample_pr, mocker):
        """get_pr_diff aggregates file contents from source and target."""
        mocker.patch.object(ado_client, "get_pull_request", return_value=sample_pr)
        mocker.patch.object(ado_client, "get_pr_changes", return_value=[
            {
                "item": {"path": "/src/test.js", "isFolder": False},
                "changeType": "edit",
            }
        ])
        mocker.patch.object(
            ado_client,
            "get_file_content",
            side_effect=["new content", "old content"]
        )

        result = ado_client.get_pr_diff(12345)

        assert len(result) == 1
        assert result[0]["path"] == "/src/test.js"
        assert result[0]["change_type"] == "edit"
        assert result[0]["source_content"] == "new content"
        assert result[0]["target_content"] == "old content"

    def test_post_pr_status_failed(self, ado_client, mocker):
        """post_pr_status sends failed status with correct payload."""
        mock_post = mocker.patch.object(ado_client, "_post", return_value={"id": 1})

        result = ado_client.post_pr_status(
            12345,
            state="failed",
            description="Blocking regression risk detected.",
            context_name="rawl-review/ai-review",
            genre="rawl-review",
            target_url="gs://bucket/reviews/pr-12345.md",
        )

        mock_post.assert_called_once_with(
            "/git/repositories/test-repo/pullrequests/12345/statuses",
            {
                "state": "failed",
                "description": "Blocking regression risk detected.",
                "context": {"name": "rawl-review/ai-review", "genre": "rawl-review"},
                "targetUrl": "gs://bucket/reviews/pr-12345.md",
            },
        )
        assert result == {"id": 1}

    def test_post_pr_status_succeeded(self, ado_client, mocker):
        """post_pr_status sends succeeded status."""
        mock_post = mocker.patch.object(ado_client, "_post", return_value={"id": 2})

        ado_client.post_pr_status(12345, state="succeeded", description="AI review passed.")

        mock_post.assert_called_once()
        payload = mock_post.call_args[0][1]
        assert payload["state"] == "succeeded"
        assert payload["context"]["name"] == "rawl-review/ai-review"  # default
        assert "targetUrl" not in payload  # omitted when not provided

    def test_post_pr_status_omits_target_url_when_none(self, ado_client, mocker):
        """post_pr_status excludes targetUrl key when target_url is None."""
        mock_post = mocker.patch.object(ado_client, "_post", return_value={})

        ado_client.post_pr_status(12345, state="pending", description="Review in progress.", target_url=None)

        payload = mock_post.call_args[0][1]
        assert "targetUrl" not in payload

    def test_post_pr_status_custom_context(self, ado_client, mocker):
        """post_pr_status respects custom context_name and genre."""
        mock_post = mocker.patch.object(ado_client, "_post", return_value={})

        ado_client.post_pr_status(
            99,
            state="failed",
            description="Custom check failed.",
            context_name="custom/check",
            genre="custom-genre",
        )

        payload = mock_post.call_args[0][1]
        assert payload["context"]["name"] == "custom/check"
        assert payload["context"]["genre"] == "custom-genre"


# =============================================================================
# Cloud Storage Tests
# =============================================================================

class TestSaveToStorage:
    """Tests for save_to_storage function."""

    def test_save_to_storage_success(self, mocker):
        """save_to_storage uploads review to GCS bucket."""
        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.storage.storage.Client", return_value=mock_client)

        review_content = "# Review\n\nThis is a test review."
        result = save_to_storage("test-bucket", 12345, review_content)

        # Verify bucket was accessed
        mock_client.bucket.assert_called_once_with("test-bucket")

        # Verify blob was created with correct path pattern
        blob_call = mock_bucket.blob.call_args[0][0]
        assert blob_call.startswith("reviews/")
        assert "pr-12345" in blob_call
        assert blob_call.endswith("-review.md")

        # Verify upload was called
        mock_blob.upload_from_string.assert_called_once_with(
            review_content,
            content_type="text/markdown"
        )

        # Verify return path format
        assert result.startswith("gs://test-bucket/reviews/")


# =============================================================================
# Pure Logic Tests
# =============================================================================

class TestGetMaxSeverity:
    """Tests for get_max_severity function."""

    def test_get_max_severity_blocking(self):
        """Returns 'action-required' when action-required priority found."""
        review = """
        # Review

        ### Finding: Critical issue
        **Priority:** action-required

        This is an action-required issue.
        """
        assert get_max_severity(review) == "action-required"

    def test_get_max_severity_warning(self):
        """Returns 'review-recommended' when review-recommended priority found (no action-required)."""
        review = """
        # Review

        ### Finding: Potential issue
        **Priority:** review-recommended

        This could cause problems.
        """
        assert get_max_severity(review) == "review-recommended"

    def test_get_max_severity_info(self):
        """Returns 'note' when no action-required or review-recommended found."""
        review = """
        # Review

        ### Finding: Minor note
        **Priority:** note

        Just an observation.
        """
        assert get_max_severity(review) == "note"

    def test_get_max_severity_blocking_takes_precedence(self):
        """Returns 'action-required' even when review-recommended is also present."""
        review = """
        # Review

        ### Finding: Review recommended issue
        **Priority:** review-recommended

        ### Finding: Critical issue
        **Priority:** action-required
        """
        assert get_max_severity(review) == "action-required"

    def test_get_max_severity_empty_review(self):
        """Returns 'note' for empty review."""
        assert get_max_severity("") == "note"

    def test_get_max_severity_severity_blocking(self):
        """Returns 'action-required' when Severity: blocking format used."""
        review = """
        ### Finding: Critical issue
        **Severity:** blocking
        **Applies to:** some/file.java

        This is a blocking issue.
        """
        assert get_max_severity(review) == "action-required"

    def test_get_max_severity_severity_warning(self):
        """Returns 'review-recommended' when Severity: warning format used."""
        review = """
        ### Finding: Potential issue
        **Severity:** warning
        **Applies to:** some/file.java

        This could cause problems.
        """
        assert get_max_severity(review) == "review-recommended"

    def test_get_max_severity_severity_info(self):
        """Returns 'note' when Severity: info format used (no blocking/warning)."""
        review = """
        ### Finding: Minor note
        **Severity:** info
        **Applies to:** some/file.java

        Just an observation.
        """
        assert get_max_severity(review) == "note"

    def test_get_max_severity_severity_blocking_takes_precedence(self):
        """Returns 'action-required' when both blocking and warning Severity found."""
        review = """
        ### Finding: Review warning
        **Severity:** warning

        ### Finding: Critical issue
        **Severity:** blocking
        """
        assert get_max_severity(review) == "action-required"

    def test_get_max_severity_mixed_formats(self):
        """Returns 'action-required' when Priority and Severity formats are mixed."""
        review = """
        ### Finding: Warning issue
        **Priority:** review-recommended

        ### Finding: Critical issue
        **Severity:** blocking
        """
        assert get_max_severity(review) == "action-required"


class TestBuildReviewPrompt:
    """Tests for build_review_prompt function."""

    def test_build_review_prompt(self, sample_pr, sample_file_diffs):
        """build_review_prompt constructs prompt with PR context and diffs."""
        prompt = build_review_prompt(sample_pr, sample_file_diffs)

        # Check PR metadata is included
        assert "Add new feature" in prompt
        assert "12345" in prompt
        assert "John Doe" in prompt
        assert "feature/new-feature" in prompt
        assert "main" in prompt

        # Check file paths are included
        assert "/src/component.js" in prompt
        assert "/src/styles.css" in prompt

        # Check change types are included
        assert "edit" in prompt
        assert "add" in prompt

        # Check file contents are included
        assert "function newCode()" in prompt
        assert "function oldCode()" in prompt
        assert ".new-class" in prompt

    def test_build_review_prompt_with_description(self, sample_pr, sample_file_diffs):
        """build_review_prompt includes PR description."""
        prompt = build_review_prompt(sample_pr, sample_file_diffs)

        assert "This PR adds a new feature to the component." in prompt

    def test_build_review_prompt_ends_with_instruction(self, sample_pr, sample_file_diffs):
        """build_review_prompt ends with review instruction."""
        prompt = build_review_prompt(sample_pr, sample_file_diffs)

        assert prompt.strip().endswith("Please provide your regression-focused review.")


# =============================================================================
# Idempotency Tests
# =============================================================================

class TestCheckAndClaimProcessing:
    """Tests for check_and_claim_processing function."""

    def test_claim_success_when_marker_not_exists(self, mocker):
        """Returns True and creates marker when no existing marker."""
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        result = check_and_claim_processing("test-bucket", 12345, "abc123def456")

        assert result is True
        mock_bucket.blob.assert_called_once_with("idempotency/pr-12345-abc123def456.json")
        mock_blob.upload_from_string.assert_called_once()

        # Verify atomic write was used
        call_kwargs = mock_blob.upload_from_string.call_args[1]
        assert call_kwargs["if_generation_match"] == 0
        assert call_kwargs["content_type"] == "application/json"

    def test_skip_when_marker_completed(self, mocker):
        """Returns False when marker exists with completed status."""
        import json

        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_text.return_value = json.dumps({
            "pr_id": 12345,
            "commit_sha": "abc123def456",
            "status": "completed"
        })

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        result = check_and_claim_processing("test-bucket", 12345, "abc123def456")

        assert result is False
        mock_blob.upload_from_string.assert_not_called()

    def test_skip_when_marker_failed(self, mocker):
        """Returns False when marker exists with failed status."""
        import json

        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_text.return_value = json.dumps({
            "pr_id": 12345,
            "commit_sha": "abc123def456",
            "status": "failed",
            "retry_count": 3
        })

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        result = check_and_claim_processing("test-bucket", 12345, "abc123def456")

        assert result is False

    def test_allow_retry_when_processing_under_limit(self, mocker):
        """Returns True when marker is processing and retry_count < MAX."""
        import json

        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_text.return_value = json.dumps({
            "pr_id": 12345,
            "commit_sha": "abc123def456",
            "status": "processing",
            "retry_count": 1
        })

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        result = check_and_claim_processing("test-bucket", 12345, "abc123def456")

        assert result is True

    def test_skip_when_max_retries_exceeded(self, mocker):
        """Returns False when marker is processing but retry_count >= MAX."""
        import json

        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_text.return_value = json.dumps({
            "pr_id": 12345,
            "commit_sha": "abc123def456",
            "status": "processing",
            "retry_count": MAX_RETRY_ATTEMPTS
        })

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        result = check_and_claim_processing("test-bucket", 12345, "abc123def456")

        assert result is False

    def test_skip_on_race_condition(self, mocker):
        """Returns False when another instance claimed marker (PreconditionFailed)."""
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False
        mock_blob.upload_from_string.side_effect = PreconditionFailed("Precondition failed")

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        result = check_and_claim_processing("test-bucket", 12345, "abc123def456")

        assert result is False

    def test_marker_content_format(self, mocker):
        """Marker JSON contains expected fields."""
        import json

        mock_blob = MagicMock()
        mock_blob.exists.return_value = False

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        check_and_claim_processing("test-bucket", 12345, "abc123def456")

        # Get the JSON content that was uploaded
        uploaded_content = mock_blob.upload_from_string.call_args[0][0]
        marker = json.loads(uploaded_content)

        assert marker["pr_id"] == 12345
        assert marker["commit_sha"] == "abc123def456"
        assert marker["status"] == "processing"
        assert marker["retry_count"] == 0
        assert "claimed_at" in marker


class TestUpdateMarkerCompleted:
    """Tests for update_marker_completed function."""

    def test_update_marker_success(self, mocker):
        """Updates marker with completion status."""
        import json

        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        update_marker_completed("test-bucket", 12345, "abc123def456", "warning", True)

        mock_bucket.blob.assert_called_once_with("idempotency/pr-12345-abc123def456.json")
        mock_blob.upload_from_string.assert_called_once()

        # Verify marker content
        uploaded_content = mock_blob.upload_from_string.call_args[0][0]
        marker = json.loads(uploaded_content)

        assert marker["pr_id"] == 12345
        assert marker["commit_sha"] == "abc123def456"
        assert marker["status"] == "completed"
        assert marker["max_severity"] == "warning"
        assert marker["commented"] is True
        assert "processed_at" in marker


class TestUpdateMarkerForRetry:
    """Tests for update_marker_for_retry function."""

    def test_first_retry_returns_true(self, mocker):
        """Returns True on first retry (retry_count < MAX)."""
        import json

        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_text.return_value = json.dumps({
            "pr_id": 12345,
            "commit_sha": "abc123def456",
            "status": "processing",
            "retry_count": 0
        })

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        result = update_marker_for_retry("test-bucket", 12345, "abc123def456", "Test error")

        assert result is True

        # Verify marker was updated with incremented retry_count
        uploaded_content = mock_blob.upload_from_string.call_args[0][0]
        marker = json.loads(uploaded_content)
        assert marker["retry_count"] == 1
        assert marker["status"] == "processing"

    def test_max_retries_returns_false(self, mocker):
        """Returns False when max retries exceeded."""
        import json

        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_text.return_value = json.dumps({
            "pr_id": 12345,
            "commit_sha": "abc123def456",
            "status": "processing",
            "retry_count": MAX_RETRY_ATTEMPTS - 1  # One more will exceed
        })

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        result = update_marker_for_retry("test-bucket", 12345, "abc123def456", "Test error")

        assert result is False

        # Verify marker was updated with failed status
        uploaded_content = mock_blob.upload_from_string.call_args[0][0]
        marker = json.loads(uploaded_content)
        assert marker["status"] == "failed"
        assert marker["retry_count"] == MAX_RETRY_ATTEMPTS

    def test_no_existing_marker_starts_at_one(self, mocker):
        """Starts retry_count at 1 when no existing marker."""
        import json

        mock_blob = MagicMock()
        mock_blob.exists.return_value = False

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        result = update_marker_for_retry("test-bucket", 12345, "abc123def456", "Test error")

        assert result is True

        uploaded_content = mock_blob.upload_from_string.call_args[0][0]
        marker = json.loads(uploaded_content)
        assert marker["retry_count"] == 1


class TestUpdateMarkerFailed:
    """Tests for update_marker_failed function."""

    def test_marks_as_permanently_failed(self, mocker):
        """Creates marker with failed status and non_retryable reason."""
        import json

        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        update_marker_failed("test-bucket", 12345, "abc123def456", "401 Unauthorized")

        mock_bucket.blob.assert_called_once_with("idempotency/pr-12345-abc123def456.json")

        uploaded_content = mock_blob.upload_from_string.call_args[0][0]
        marker = json.loads(uploaded_content)

        assert marker["pr_id"] == 12345
        assert marker["commit_sha"] == "abc123def456"
        assert marker["status"] == "failed"
        assert marker["reason"] == "non_retryable_error"
        assert marker["error"] == "401 Unauthorized"
        assert "failed_at" in marker

    def test_truncates_long_error_messages(self, mocker):
        """Truncates error messages longer than 500 chars."""
        import json

        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        long_error = "x" * 1000
        update_marker_failed("test-bucket", 12345, "abc123def456", long_error)

        uploaded_content = mock_blob.upload_from_string.call_args[0][0]
        marker = json.loads(uploaded_content)

        assert len(marker["error"]) == 500


class TestIdempotencyKeyFormat:
    """Tests verifying the idempotency key format."""

    def test_different_commits_same_pr_have_different_keys(self, mocker):
        """Same PR with different commits creates different marker paths."""
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        # First commit
        check_and_claim_processing("test-bucket", 12345, "commit_a")
        first_call_path = mock_bucket.blob.call_args_list[0][0][0]

        # Second commit (same PR)
        check_and_claim_processing("test-bucket", 12345, "commit_b")
        second_call_path = mock_bucket.blob.call_args_list[1][0][0]

        assert first_call_path != second_call_path
        assert "pr-12345-commit_a" in first_call_path
        assert "pr-12345-commit_b" in second_call_path

    def test_same_commit_different_prs_have_different_keys(self, mocker):
        """Same commit on different PRs creates different marker paths."""
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        mocker.patch("pr_review.idempotency.storage.Client", return_value=mock_client)

        # First PR
        check_and_claim_processing("test-bucket", 11111, "same_commit")
        first_call_path = mock_bucket.blob.call_args_list[0][0][0]

        # Second PR (same commit SHA - edge case)
        check_and_claim_processing("test-bucket", 22222, "same_commit")
        second_call_path = mock_bucket.blob.call_args_list[1][0][0]

        assert first_call_path != second_call_path
        assert "pr-11111" in first_call_path
        assert "pr-22222" in second_call_path


# =============================================================================
# Webhook Receiver Tests
# =============================================================================

class TestLoadWebhookConfig:
    """Tests for load_webhook_config function."""

    def test_load_webhook_config_success(self, mocker):
        """Returns config dict when all required vars present."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
        })

        config, missing = load_webhook_config()

        assert missing == []
        assert config["VERTEX_PROJECT"] == "test-project"
        assert config["PUBSUB_TOPIC"] == "pr-review-trigger"  # default

    def test_load_webhook_config_custom_topic(self, mocker):
        """Uses custom PUBSUB_TOPIC when provided."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
            "PUBSUB_TOPIC": "custom-topic",
        })

        config, missing = load_webhook_config()

        assert config["PUBSUB_TOPIC"] == "custom-topic"

    def test_load_webhook_config_missing_vars(self, mocker):
        """Returns missing vars list when required vars missing."""
        mocker.patch.dict("os.environ", {}, clear=True)

        config, missing = load_webhook_config()

        assert "VERTEX_PROJECT" in missing


class TestReceiveWebhook:
    """Tests for receive_webhook function."""

    @pytest.fixture
    def mock_request(self, mocker):
        """Create a mock Flask request object."""
        request = MagicMock()
        request.headers = {}
        request.get_json = MagicMock(return_value={})
        return request

    def test_missing_pr_id(self, mock_request, mocker):
        """Returns 400 when pr_id is missing."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
        })
        mock_request.get_json.return_value = {"commit_sha": "abc123def"}

        response, status = receive_webhook(mock_request)

        assert status == 400
        assert "pr_id" in response["error"]

    def test_missing_commit_sha(self, mock_request, mocker):
        """Returns 400 when commit_sha is missing."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
        })
        mock_request.get_json.return_value = {"pr_id": 12345}

        response, status = receive_webhook(mock_request)

        assert status == 400
        assert "commit_sha" in response["error"]

    def test_invalid_pr_id_type(self, mock_request, mocker):
        """Returns 400 when pr_id is not a valid integer."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
        })
        mock_request.get_json.return_value = {"pr_id": "not-a-number", "commit_sha": "abc123def"}

        response, status = receive_webhook(mock_request)

        assert status == 400
        assert "integer" in response["error"]

    def test_commit_sha_too_short(self, mock_request, mocker):
        """Returns 400 when commit_sha is too short."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
        })
        mock_request.get_json.return_value = {"pr_id": 12345, "commit_sha": "abc"}

        response, status = receive_webhook(mock_request)

        assert status == 400
        assert "7 characters" in response["error"]

    def test_successful_publish(self, mock_request, mocker):
        """Returns 202 and publishes to Pub/Sub on success."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
            "PUBSUB_TOPIC": "test-topic",
        })
        mock_request.get_json.return_value = {
            "pr_id": 12345,
            "commit_sha": "abc123def456789"
        }

        # Mock Pub/Sub client
        mock_future = MagicMock()
        mock_future.result.return_value = "message-id-123"

        mock_publisher = MagicMock()
        mock_publisher.topic_path.return_value = "projects/test-project/topics/test-topic"
        mock_publisher.publish.return_value = mock_future

        mocker.patch("pr_review.entry_points.pubsub_v1.PublisherClient", return_value=mock_publisher)

        response, status = receive_webhook(mock_request)

        assert status == 202
        assert response["status"] == "queued"
        assert response["message_id"] == "message-id-123"
        assert response["pr_id"] == 12345
        assert response["commit_sha"] == "abc123de"  # truncated to 8 chars

        # Verify Pub/Sub was called correctly
        mock_publisher.topic_path.assert_called_once_with("test-project", "test-topic")
        mock_publisher.publish.assert_called_once()

    def test_pubsub_message_format(self, mock_request, mocker):
        """Verifies the Pub/Sub message contains expected fields."""
        import json

        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
        })
        mock_request.get_json.return_value = {
            "pr_id": 12345,
            "commit_sha": "abc123def456789"
        }

        mock_future = MagicMock()
        mock_future.result.return_value = "msg-123"

        mock_publisher = MagicMock()
        mock_publisher.topic_path.return_value = "projects/test-project/topics/pr-review-trigger"
        mock_publisher.publish.return_value = mock_future

        mocker.patch("pr_review.entry_points.pubsub_v1.PublisherClient", return_value=mock_publisher)

        receive_webhook(mock_request)

        # Get the message bytes that were published
        publish_call = mock_publisher.publish.call_args
        message_bytes = publish_call[0][1]  # Second positional arg
        message = json.loads(message_bytes.decode("utf-8"))

        assert message["pr_id"] == 12345
        assert message["commit_sha"] == "abc123def456789"
        assert message["source"] == "azure-devops-pipeline"
        assert "received_at" in message

    def test_pubsub_publish_failure(self, mock_request, mocker):
        """Returns 500 when Pub/Sub publish fails."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
        })
        mock_request.get_json.return_value = {
            "pr_id": 12345,
            "commit_sha": "abc123def456789"
        }

        mock_publisher = MagicMock()
        mock_publisher.topic_path.return_value = "projects/test-project/topics/test-topic"
        mock_publisher.publish.side_effect = Exception("Pub/Sub error")

        mocker.patch("pr_review.entry_points.pubsub_v1.PublisherClient", return_value=mock_publisher)

        response, status = receive_webhook(mock_request)

        assert status == 500
        assert "Failed to queue" in response["error"]

    def test_server_config_error(self, mock_request, mocker):
        """Returns 500 when server config is missing."""
        mocker.patch.dict("os.environ", {}, clear=True)

        response, status = receive_webhook(mock_request)

        assert status == 500
        assert "configuration error" in response["error"]

    def test_invalid_json_body(self, mock_request, mocker):
        """Returns 400 when request body is not valid JSON."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
        })
        mock_request.get_json.side_effect = Exception("Invalid JSON")

        response, status = receive_webhook(mock_request)

        assert status == 400
        assert "Invalid JSON" in response["error"]

    def test_empty_request_body(self, mock_request, mocker):
        """Returns 400 when request body is empty."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
        })
        mock_request.get_json.return_value = None

        response, status = receive_webhook(mock_request)

        assert status == 400
        assert "Empty" in response["error"]

    def test_pr_id_as_string_integer(self, mock_request, mocker):
        """Accepts pr_id as string that can be parsed as integer."""
        mocker.patch.dict("os.environ", {
            "VERTEX_PROJECT": "test-project",
        })
        mock_request.get_json.return_value = {
            "pr_id": "12345",  # String, not int
            "commit_sha": "abc123def456789"
        }

        mock_future = MagicMock()
        mock_future.result.return_value = "msg-123"

        mock_publisher = MagicMock()
        mock_publisher.topic_path.return_value = "projects/test-project/topics/test-topic"
        mock_publisher.publish.return_value = mock_future

        mocker.patch("pr_review.entry_points.pubsub_v1.PublisherClient", return_value=mock_publisher)

        response, status = receive_webhook(mock_request)

        assert status == 202
        assert response["pr_id"] == 12345  # Converted to int


# =============================================================================
# Non-Code File Filtering Tests
# =============================================================================

class TestFilterNonCodeFiles:
    """Tests for filter_non_code_files() function."""

    def test_filter_non_code_files_removes_md_files(self):
        """filter_non_code_files removes .md files from the list."""
        file_diffs = [
            {"path": "/src/component.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
            {"path": "/docs/README.md", "change_type": "add", "source_content": "# Docs", "target_content": None},
            {"path": "/src/styles.css", "change_type": "edit", "source_content": "css", "target_content": "old"},
            {"path": "/docs/CHANGELOG.MD", "change_type": "add", "source_content": "# Changelog", "target_content": None},
        ]

        filtered, count = filter_non_code_files(file_diffs)

        assert count == 2
        assert len(filtered) == 2
        assert all(not diff["path"].lower().endswith(".md") for diff in filtered)
        assert filtered[0]["path"] == "/src/component.js"
        assert filtered[1]["path"] == "/src/styles.css"

    def test_filter_non_code_files_removes_sh_files(self):
        """filter_non_code_files removes .sh files from the list."""
        file_diffs = [
            {"path": "/src/component.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
            {"path": "/scripts/deploy.sh", "change_type": "add", "source_content": "#!/bin/bash", "target_content": None},
            {"path": "/scripts/setup.SH", "change_type": "add", "source_content": "#!/bin/bash", "target_content": None},
        ]

        filtered, count = filter_non_code_files(file_diffs)

        assert count == 2
        assert len(filtered) == 1
        assert filtered[0]["path"] == "/src/component.js"

    def test_filter_non_code_files_removes_image_files(self):
        """filter_non_code_files removes image files from the list."""
        file_diffs = [
            {"path": "/src/component.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
            {"path": "/images/logo.png", "change_type": "add", "source_content": None, "target_content": None},
            {"path": "/images/banner.jpg", "change_type": "add", "source_content": None, "target_content": None},
            {"path": "/images/icon.svg", "change_type": "add", "source_content": None, "target_content": None},
            {"path": "/images/photo.jpeg", "change_type": "add", "source_content": None, "target_content": None},
        ]

        filtered, count = filter_non_code_files(file_diffs)

        assert count == 4
        assert len(filtered) == 1
        assert filtered[0]["path"] == "/src/component.js"

    def test_filter_non_code_files_case_insensitive(self):
        """filter_non_code_files handles case-insensitive extensions."""
        file_diffs = [
            {"path": "/docs/readme.md", "change_type": "add", "source_content": "content", "target_content": None},
            {"path": "/docs/README.MD", "change_type": "add", "source_content": "content", "target_content": None},
            {"path": "/scripts/deploy.SH", "change_type": "add", "source_content": "content", "target_content": None},
            {"path": "/images/logo.PNG", "change_type": "add", "source_content": None, "target_content": None},
            {"path": "/src/file.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
        ]

        filtered, count = filter_non_code_files(file_diffs)

        assert count == 4
        assert len(filtered) == 1
        assert filtered[0]["path"] == "/src/file.js"

    def test_filter_non_code_files_no_filtered_files(self):
        """filter_non_code_files returns all files when no filtered files present."""
        file_diffs = [
            {"path": "/src/component.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
            {"path": "/src/styles.css", "change_type": "add", "source_content": "css", "target_content": None},
        ]

        filtered, count = filter_non_code_files(file_diffs)

        assert count == 0
        assert len(filtered) == 2
        assert filtered == file_diffs

    def test_filter_non_code_files_only_filtered_files(self):
        """filter_non_code_files returns empty list when all files are filtered."""
        file_diffs = [
            {"path": "/docs/README.md", "change_type": "add", "source_content": "# Docs", "target_content": None},
            {"path": "/scripts/deploy.sh", "change_type": "add", "source_content": "#!/bin/bash", "target_content": None},
            {"path": "/images/logo.png", "change_type": "add", "source_content": None, "target_content": None},
        ]

        filtered, count = filter_non_code_files(file_diffs)

        assert count == 3
        assert len(filtered) == 0

    def test_filter_non_code_files_path_with_extension_in_name(self):
        """filter_non_code_files does not filter files with extension in path but not as file extension."""
        file_diffs = [
            {"path": "/src/md5hash.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
            {"path": "/src/markdown-parser.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
            {"path": "/src/shell-utils.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
            {"path": "/docs/README.md", "change_type": "add", "source_content": "# Docs", "target_content": None},
        ]

        filtered, count = filter_non_code_files(file_diffs)

        assert count == 1
        assert len(filtered) == 3
        assert any(diff["path"] == "/src/md5hash.js" for diff in filtered)
        assert any(diff["path"] == "/src/markdown-parser.js" for diff in filtered)
        assert any(diff["path"] == "/src/shell-utils.js" for diff in filtered)


# =============================================================================
# Extensive PR Detection Tests
# =============================================================================

class TestIsExtensivePr:
    """Tests for is_extensive_pr() function."""

    def test_is_extensive_pr_by_file_count(self):
        """is_extensive_pr returns True when file count exceeds threshold."""
        config = {"EXTENSIVE_PR_FILE_THRESHOLD": 5, "EXTENSIVE_PR_SIZE_THRESHOLD": 1000000}
        file_diffs = [
            {"path": f"/src/file{i}.js", "change_type": "edit", "source_content": "code", "target_content": "old"}
            for i in range(6)  # 6 files > threshold of 5
        ]

        assert is_extensive_pr(file_diffs, config) == True

    def test_is_extensive_pr_by_file_count_exact_threshold(self):
        """is_extensive_pr returns True when file count equals threshold."""
        config = {"EXTENSIVE_PR_FILE_THRESHOLD": 5, "EXTENSIVE_PR_SIZE_THRESHOLD": 1000000}
        file_diffs = [
            {"path": f"/src/file{i}.js", "change_type": "edit", "source_content": "code", "target_content": "old"}
            for i in range(5)  # 5 files == threshold of 5
        ]

        assert is_extensive_pr(file_diffs, config) == True

    def test_is_extensive_pr_by_file_count_below_threshold(self):
        """is_extensive_pr returns False when file count is below threshold."""
        config = {"EXTENSIVE_PR_FILE_THRESHOLD": 5, "EXTENSIVE_PR_SIZE_THRESHOLD": 1000000}
        file_diffs = [
            {"path": f"/src/file{i}.js", "change_type": "edit", "source_content": "code", "target_content": "old"}
            for i in range(4)  # 4 files < threshold of 5
        ]

        assert is_extensive_pr(file_diffs, config) == False

    def test_is_extensive_pr_by_size(self):
        """is_extensive_pr returns True when total size exceeds threshold."""
        config = {"EXTENSIVE_PR_FILE_THRESHOLD": 100, "EXTENSIVE_PR_SIZE_THRESHOLD": 1000}
        # Create files with content that exceeds size threshold
        large_content = "x" * 600  # 600 chars per file
        file_diffs = [
            {
                "path": f"/src/file{i}.js",
                "change_type": "edit",
                "source_content": large_content,
                "target_content": large_content
            }
            for i in range(2)  # 2 files * 600 * 2 = 2400 chars > 1000 threshold
        ]

        assert is_extensive_pr(file_diffs, config) == True

    def test_is_extensive_pr_by_size_exact_threshold(self):
        """is_extensive_pr returns True when total size equals threshold."""
        config = {"EXTENSIVE_PR_FILE_THRESHOLD": 100, "EXTENSIVE_PR_SIZE_THRESHOLD": 1000}
        # Create files with content that equals size threshold
        content = "x" * 250  # 250 chars per file, 2 files * 250 * 2 = 1000 chars
        file_diffs = [
            {
                "path": f"/src/file{i}.js",
                "change_type": "edit",
                "source_content": content,
                "target_content": content
            }
            for i in range(2)
        ]

        assert is_extensive_pr(file_diffs, config) == True

    def test_is_extensive_pr_by_size_below_threshold(self):
        """is_extensive_pr returns False when total size is below threshold."""
        config = {"EXTENSIVE_PR_FILE_THRESHOLD": 100, "EXTENSIVE_PR_SIZE_THRESHOLD": 1000}
        # Create files with small content
        file_diffs = [
            {
                "path": f"/src/file{i}.js",
                "change_type": "edit",
                "source_content": "code",
                "target_content": "old"
            }
            for i in range(2)  # 2 files * (4 + 3) = 14 chars < 1000 threshold
        ]

        assert is_extensive_pr(file_diffs, config) == False

    def test_is_extensive_pr_handles_none_content(self):
        """is_extensive_pr handles None content gracefully."""
        config = {"EXTENSIVE_PR_FILE_THRESHOLD": 100, "EXTENSIVE_PR_SIZE_THRESHOLD": 1000}
        file_diffs = [
            {"path": "/src/file.js", "change_type": "add", "source_content": None, "target_content": None},
            {"path": "/src/file2.js", "change_type": "edit", "source_content": "code", "target_content": None},
        ]

        # Should not raise error and should return False (small size)
        assert is_extensive_pr(file_diffs, config) == False

    def test_is_extensive_pr_default_thresholds(self):
        """is_extensive_pr uses default thresholds when not in config."""
        config = {}  # Empty config should use defaults
        # Default file threshold is DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD, so threshold+5 files should trigger
        file_diffs = [
            {"path": f"/src/file{i}.js", "change_type": "edit", "source_content": "code", "target_content": "old"}
            for i in range(DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD + 5)
        ]

        assert is_extensive_pr(file_diffs, config) == True


# =============================================================================
# Integration Tests for Process PR Review with Filtering
# =============================================================================

class TestProcessPrReviewWithFiltering:
    """Integration tests for process_pr_review() with markdown filtering."""

    def test_process_review_filters_non_code_files_always(self, ado_client, sample_pr, mocker):
        """process_pr_review filters non-code files for all PRs when enabled."""
        config = {
            "GCS_BUCKET": "test-bucket",
            "EXTENSIVE_PR_FILE_THRESHOLD": 2,
            "EXTENSIVE_PR_SIZE_THRESHOLD": 1000000,
            "FILTER_NON_CODE_FILES": True,
        }

        # PR with markdown files (filtering should happen regardless of PR size)
        file_diffs = [
            {"path": "/src/component.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
            {"path": "/docs/README.md", "change_type": "add", "source_content": "# Docs", "target_content": None},
            {"path": "/docs/CHANGELOG.md", "change_type": "add", "source_content": "# Changelog", "target_content": None},
        ]

        mocker.patch("pr_review.gemini.call_gemini", return_value="# Review\n\n**Priority:** note")
        mocker.patch("pr_review.storage.save_to_storage", return_value="gs://test-bucket/reviews/pr-12345.md")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, file_diffs)

        # Should have filtered out 2 markdown files, leaving 1 file
        assert result.files_changed == 1
        # Verify Gemini was called (prompt built with filtered files)
        assert result.review_text == "# Review\n\n**Priority:** note"

    def test_process_review_filters_non_code_files_for_small_pr(self, ado_client, sample_pr, mocker):
        """process_pr_review filters non-code files even for small PRs."""
        config = {
            "GCS_BUCKET": "test-bucket",
            "EXTENSIVE_PR_FILE_THRESHOLD": DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD,
            "EXTENSIVE_PR_SIZE_THRESHOLD": 1000000,
            "FILTER_NON_CODE_FILES": True,
        }

        # Small PR with markdown files (should still filter)
        file_diffs = [
            {"path": "/src/component.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
            {"path": "/docs/README.md", "change_type": "add", "source_content": "# Docs", "target_content": None},
        ]

        mocker.patch("pr_review.gemini.call_gemini", return_value="# Review\n\n**Priority:** note")
        mocker.patch("pr_review.storage.save_to_storage", return_value="gs://test-bucket/reviews/pr-12345.md")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, file_diffs)

        # Should filter markdown file, leaving 1 file
        assert result.files_changed == 1

    def test_process_review_filtering_disabled(self, ado_client, sample_pr, mocker):
        """process_pr_review does not filter non-code files when FILTER_NON_CODE_FILES is False, but still limits extensive PRs."""
        config = {
            "GCS_BUCKET": "test-bucket",
            "EXTENSIVE_PR_FILE_THRESHOLD": 5,  # Higher threshold so PR is not extensive
            "EXTENSIVE_PR_SIZE_THRESHOLD": 1000000,
            "FILTER_NON_CODE_FILES": False,  # Disabled
        }

        # PR with markdown files but not extensive (below threshold)
        file_diffs = [
            {"path": "/src/component.js", "change_type": "edit", "source_content": "code", "target_content": "old"},
            {"path": "/docs/README.md", "change_type": "add", "source_content": "# Docs", "target_content": None},
            {"path": "/docs/CHANGELOG.md", "change_type": "add", "source_content": "# Changelog", "target_content": None},
        ]

        mocker.patch("pr_review.gemini.call_gemini", return_value="# Review\n\n**Priority:** note")
        mocker.patch("pr_review.storage.save_to_storage", return_value="gs://test-bucket/reviews/pr-12345.md")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, file_diffs)

        # Should include all files (filtering disabled, and PR not extensive)
        assert result.files_changed == 3

    def test_process_review_limits_files_for_extensive_pr(self, ado_client, sample_pr, mocker):
        """process_pr_review limits files to threshold when PR is extensive."""
        config = {
            "GCS_BUCKET": "test-bucket",
            "EXTENSIVE_PR_FILE_THRESHOLD": 3,
            "EXTENSIVE_PR_SIZE_THRESHOLD": 1000000,
            "FILTER_NON_CODE_FILES": True,
        }

        # Create extensive PR with 5 files (should be limited to 3)
        file_diffs = [
            {"path": "/src/file1.js", "change_type": "edit", "source_content": "code1", "target_content": "old1"},
            {"path": "/src/file2.js", "change_type": "edit", "source_content": "code2", "target_content": "old2"},
            {"path": "/src/file3.js", "change_type": "edit", "source_content": "code3", "target_content": "old3"},
            {"path": "/src/file4.js", "change_type": "edit", "source_content": "code4", "target_content": "old4"},
            {"path": "/src/file5.js", "change_type": "edit", "source_content": "code5", "target_content": "old5"},
        ]

        mock_gemini = mocker.patch("pr_review.gemini.call_gemini", return_value="# Review\n\n**Priority:** review-recommended")
        mock_save = mocker.patch("pr_review.storage.save_to_storage", return_value="gs://test-bucket/reviews/pr-12345.md")
        mock_comment = mocker.patch.object(ado_client, "post_pr_comment")
        mocker.patch.object(ado_client, "post_pr_status")

        result = process_pr_review(config, ado_client, 12345, sample_pr, file_diffs)

        # Should be limited to 3 files
        assert result.files_changed == 3

        # Verify comment was posted with partial review notice
        assert mock_comment.called
        comment_text = mock_comment.call_args[0][1]
        assert "Partial Review" in comment_text
        assert "excluded 2 additional files" in comment_text
