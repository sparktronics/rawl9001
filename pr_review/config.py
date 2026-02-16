"""Configuration loading from environment variables."""

import os

DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD = 60
DEFAULT_EXTENSIVE_PR_SIZE_THRESHOLD = 500000


def load_config() -> tuple[dict, list]:
    """Load configuration from environment variables.

    Note: API_KEY is no longer required as authentication is handled by GCP IAM.

    Returns:
        tuple: (config dict, list of missing required vars)
    """
    required = [
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
    config["EXTENSIVE_PR_FILE_THRESHOLD"] = int(
        os.environ.get("EXTENSIVE_PR_FILE_THRESHOLD", str(DEFAULT_EXTENSIVE_PR_FILE_THRESHOLD))
    )
    config["EXTENSIVE_PR_SIZE_THRESHOLD"] = int(
        os.environ.get("EXTENSIVE_PR_SIZE_THRESHOLD", str(DEFAULT_EXTENSIVE_PR_SIZE_THRESHOLD))
    )
    config["FILTER_NON_CODE_FILES"] = os.environ.get("FILTER_NON_CODE_FILES", "true").lower() == "true"

    # Optional: If set, only comment on PRs without rejecting them
    config["JUST_COMMENT_TICKET"] = os.environ.get("JUST_COMMENT_TICKET", "").lower() == "true"

    return config, missing


def load_webhook_config() -> tuple[dict, list]:
    """Load minimal configuration for webhook receiver.

    Note: API_KEY is no longer required as authentication is handled by GCP IAM.

    Returns:
        tuple: (config dict, list of missing required vars)
    """
    required = ["VERTEX_PROJECT"]

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
