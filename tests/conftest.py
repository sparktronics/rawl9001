"""Shared pytest fixtures for PR review tests."""

import pytest

from pr_review.azure_client import AzureDevOpsClient


@pytest.fixture
def ado_client():
    """Create an AzureDevOpsClient instance for testing."""
    return AzureDevOpsClient(
        org="test-org",
        project="test-project",
        repo="test-repo",
        pat="fake-pat-token",
    )


@pytest.fixture
def sample_pr():
    """Sample PR metadata response."""
    return {
        "pullRequestId": 12345,
        "title": "Add new feature",
        "description": "This PR adds a new feature to the component.",
        "createdBy": {"displayName": "John Doe"},
        "sourceRefName": "refs/heads/feature/new-feature",
        "targetRefName": "refs/heads/main",
        "lastMergeSourceCommit": {"commitId": "abc123def456"},
        "lastMergeTargetCommit": {"commitId": "789xyz000111"},
    }


@pytest.fixture
def sample_file_diffs():
    """Sample file diffs for prompt building (unified diff only)."""
    return [
        {
            "path": "/src/component.js",
            "change_type": "edit",
            "diff": "--- a/src/component.js (target)\n+++ b/src/component.js (source)\n@@ -1,1 +1,1 @@\n-function oldCode() { return false; }\n+function newCode() { return true; }\n",
        },
        {
            "path": "/src/styles.css",
            "change_type": "add",
            "diff": "--- /dev/null\n+++ b/src/styles.css\n@@ -0,0 +1 @@\n+.new-class { color: red; }\n",
        },
    ]
