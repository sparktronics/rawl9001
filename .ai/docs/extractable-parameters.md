# Extractable Parameters

**Last Updated:** 2026-01-07

Hardcoded values in `main.py` that should be configurable via environment variables.

## Parameters to Extract

| Current Value | Location | Suggested Env Var | Default | Status |
|---------------|----------|-------------------|---------|--------|
| `"7.1-preview"` | Line 121 | `AZURE_DEVOPS_API_VERSION` | `7.1-preview` | Pending |
| `"gemini-2.5-pro"` | Line 109 | `GEMINI_MODEL` | `gemini-2.5-pro` | ✅ Implemented |
| `8192` | Line 800 | `GEMINI_MAX_TOKENS` | `8192` | Pending |
| `0.2` | Line 801 | `GEMINI_TEMPERATURE` | `0.2` | Pending |
| `"pr-review-trigger"` | load_webhook_config | `PUBSUB_TOPIC` | `pr-review-trigger` | ✅ Implemented |
| `"reviews/"` | Line 362 | `GCS_REVIEWS_PREFIX` | `reviews/` | Pending |
| `"idempotency/"` | Lines 406, 478, 516, 580 | `GCS_IDEMPOTENCY_PREFIX` | `idempotency/` | Pending |

Note: Line numbers may shift as code is modified. Use `grep` to find current locations.

## Implementation Approach

### 1. Update `load_config()`

Add optional vars with defaults:

```python
# Optional with defaults
config["VERTEX_LOCATION"] = os.environ.get("VERTEX_LOCATION", "us-central1")
config["AZURE_DEVOPS_API_VERSION"] = os.environ.get("AZURE_DEVOPS_API_VERSION", "7.1-preview")
config["GEMINI_MODEL"] = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
config["GEMINI_MAX_TOKENS"] = int(os.environ.get("GEMINI_MAX_TOKENS", "8192"))
config["GEMINI_TEMPERATURE"] = float(os.environ.get("GEMINI_TEMPERATURE", "0.2"))
config["GCS_REVIEWS_PREFIX"] = os.environ.get("GCS_REVIEWS_PREFIX", "reviews/")
config["GCS_IDEMPOTENCY_PREFIX"] = os.environ.get("GCS_IDEMPOTENCY_PREFIX", "idempotency/")
config["PUBSUB_TIMEOUT"] = int(os.environ.get("PUBSUB_TIMEOUT", "30"))
```

### 2. Update Usages

**AzureDevOpsClient:**
```python
class AzureDevOpsClient:
    def __init__(self, org: str, project: str, repo: str, pat: str, api_version: str = "7.1-preview"):
        self.api_version = api_version
        # ... rest unchanged
```

**call_gemini():**
```python
def call_gemini(config: dict, prompt: str) -> str:
    model_name = config["GEMINI_MODEL"]
    # ...
    config={
        "max_output_tokens": config["GEMINI_MAX_TOKENS"],
        "temperature": config["GEMINI_TEMPERATURE"],
    }
```

**save_to_storage():**
```python
blob_path = f"{config['GCS_REVIEWS_PREFIX']}{date_path}/pr-{pr_id}-{timestamp}-review.md"
```

**Idempotency functions:**
```python
blob = bucket.blob(f"{config['GCS_IDEMPOTENCY_PREFIX']}pr-{pr_id}-{commit_sha}.json")
```

### 3. Pass Config Through

Functions that need config will require it as a parameter (most already have it).

## Priority

1. **High:** `GEMINI_MODEL` - allows testing different models
2. **Medium:** `GEMINI_TEMPERATURE`, `GEMINI_MAX_TOKENS` - tuning output
3. **Low:** Path prefixes, API version - rarely changed



