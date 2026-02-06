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
    """Sample file diffs for prompt building."""
    return [
        {
            "path": "/src/component.js",
            "change_type": "edit",
            "source_content": "function newCode() { return true; }",
            "target_content": "function oldCode() { return false; }",
        },
        {
            "path": "/src/styles.css",
            "change_type": "add",
            "source_content": ".new-class { color: red; }",
            "target_content": None,
        },
    ]
