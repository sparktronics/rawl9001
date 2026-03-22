"""Azure DevOps REST API client."""

import logging
import time

import requests

from pr_review.utils import timed_operation

logger = logging.getLogger("pr_review")


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
                path = item.get("path") or ""
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

    def post_pr_status(
        self,
        pr_id: int,
        state: str,
        description: str,
        context_name: str = "ai-review",
        genre: str = "rawl-reviews",
        target_url: str | None = None,
    ) -> dict:
        """Post a status check to a PR.

        Replaces vote-based rejection so the block is not tied to a personal identity.
        Tech leads with branch policy bypass permission can override a failed status.

        Args:
            pr_id: Pull request ID
            state: One of "succeeded", "failed", "pending", "error"
            description: Human-readable message shown in the Azure DevOps PR Checks tab
            context_name: Unique check identifier — must match the Status policy config in Azure DevOps
            genre: Grouping label for related checks
            target_url: Optional link to the full review (e.g. GCS review URL)

        Returns:
            API response dict
        """
        data: dict = {
            "state": state,
            "description": description,
            "context": {"name": context_name, "genre": genre},
        }
        if target_url:
            data["targetUrl"] = target_url
        logger.info(f"[DEBUG-STATUS] post_pr_status payload: {data}")
        return self._post(
            f"/git/repositories/{self.repo}/pullrequests/{pr_id}/statuses",
            data,
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
