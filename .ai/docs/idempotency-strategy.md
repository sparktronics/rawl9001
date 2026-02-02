# Cloud Function Idempotency Strategy

**Document Created:** 2026-01-02  
**Status:** Implemented  
**Last Updated:** 2026-01-07

## Overview

This document describes the idempotency strategy for the PR Regression Review Cloud Function when triggered via Pub/Sub. The goal is to prevent duplicate processing while still allowing re-reviews when new commits are pushed.

---

## Problem Statement

### Pub/Sub Delivery Guarantees

Google Cloud Pub/Sub provides **at-least-once delivery**, meaning:
- The same message may be delivered multiple times
- Retries occur on acknowledgment failures or timeouts
- Network issues can cause duplicate deliveries

Without idempotency handling, the function could:
1. Post duplicate comments on the same PR
2. Waste Vertex AI quota on redundant reviews
3. Create unnecessary load on Azure DevOps API

### Requirements

| Requirement | Description |
|-------------|-------------|
| Prevent duplicates | Same PR + same commit should only be reviewed once |
| Allow re-reviews | New commits on the same PR should trigger new reviews |
| Handle race conditions | Simultaneous messages for the same PR should not both process |
| Simple implementation | Use existing GCS bucket, no additional infrastructure |

---

## Solution: GCS-Based Idempotency with PR ID + Commit SHA

### Strategy

Use a JSON marker file in GCS with the composite key: `{pr_id}-{commit_sha}`

```
gs://bucket/
└── idempotency/
    └── pr-357462-abc123def.json    ← Marker for PR 357462 at commit abc123def
```

### Decision Logic

```
┌─────────────────────────────────────────────────────────────┐
│  Pub/Sub Message Received                                   │
│  { "pr_id": 357462 }                                        │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Fetch PR metadata from Azure DevOps                     │
│     └─▶ Extract: latest_commit_sha                          │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Check idempotency marker                                │
│     └─▶ gs://bucket/idempotency/pr-{id}-{sha}.json          │
└─────────────────────────────────────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              │                       │
         File Exists             File Missing
              │                       │
              ▼                       ▼
┌─────────────────────┐   ┌─────────────────────────────────┐
│  SKIP PROCESSING    │   │  3. Create marker (atomic)      │
│  Already reviewed   │   │     └─▶ if_generation_match=0   │
│  this commit        │   └─────────────────────────────────┘
└─────────────────────┘               │
                          ┌───────────┴───────────┐
                          │                       │
                     Success                   Failed
                     (we won)              (race condition)
                          │                       │
                          ▼                       ▼
              ┌───────────────────┐   ┌─────────────────────┐
              │  4. Process PR    │   │  SKIP PROCESSING    │
              │     - Generate    │   │  Another instance   │
              │       review      │   │  is handling it     │
              │     - Post        │   └─────────────────────┘
              │       comment     │
              │     - Update      │
              │       marker      │
              └───────────────────┘
```

---

## Implementation

### Marker File Structure

```json
{
  "pr_id": 357462,
  "commit_sha": "abc123def456",
  "processed_at": "2026-01-02T10:30:00Z",
  "status": "completed",
  "max_severity": "warning",
  "commented": true
}
```

### Idempotency Check Function

```python
from google.cloud import storage
from google.api_core.exceptions import PreconditionFailed
import json
from datetime import datetime, timezone

def check_and_claim_processing(bucket_name: str, pr_id: int, commit_sha: str) -> bool:
    """
    Check if this PR+commit has been processed. If not, claim it atomically.
    
    Returns:
        True if we should process (we claimed it)
        False if already processed or claimed by another instance
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(f"idempotency/pr-{pr_id}-{commit_sha}.json")
    
    # Check if already processed
    if blob.exists():
        return False  # Already processed this commit
    
    # Try to claim it atomically
    # if_generation_match=0 means "only succeed if file doesn't exist"
    marker = {
        "pr_id": pr_id,
        "commit_sha": commit_sha,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "status": "processing"
    }
    
    try:
        blob.upload_from_string(
            json.dumps(marker, indent=2),
            content_type="application/json",
            if_generation_match=0  # Atomic: fails if file exists
        )
        return True  # We claimed it
    except PreconditionFailed:
        return False  # Another instance claimed it first


def update_marker_completed(bucket_name: str, pr_id: int, commit_sha: str, 
                            max_severity: str, commented: bool):
    """Update the marker after successful processing."""
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
```

### Integration with Cloud Function

The actual implementation uses the shared `process_pr_review()` function:

```python
@functions_framework.cloud_event
def review_pr_pubsub(cloud_event: CloudEvent) -> None:
    """Pub/Sub triggered Cloud Function with idempotency."""
    
    # 1. Parse Pub/Sub message
    message = json.loads(base64.b64decode(cloud_event.data["message"]["data"]))
    pr_id = message.get("pr_id")
    commit_sha = message.get("commit_sha")  # Optional: from webhook
    
    # 2. Fetch PR metadata
    pr = ado.get_pull_request(pr_id)
    if not commit_sha:
        commit_sha = pr["lastMergeSourceCommit"]["commitId"]
    
    # 3. Idempotency check
    if not check_and_claim_processing(bucket_name, pr_id, commit_sha):
        return  # Already processed
    
    # 4. Fetch file diffs
    file_diffs = ado.get_pr_diff(pr_id)
    
    # 5. Process using shared logic (returns ReviewResult)
    result = process_pr_review(config, ado, pr_id, pr, file_diffs)
    
    # 6. Update marker with completion status
    update_marker_completed(bucket_name, pr_id, commit_sha, result.max_severity, result.commented)
```

The `process_pr_review()` function handles:
- Building the review prompt
- Calling Gemini API
- Saving to Cloud Storage
- Posting comments and rejecting PR based on severity

Returns a `ReviewResult` dataclass with all details.

---

## Why Not Time-Based?

Initially considered: "Skip if marker exists and is older than 5 minutes"

### Problems with Time-Based Approach

| Issue | Explanation |
|-------|-------------|
| Ambiguous logic | "Older than 5 min" could mean stale (re-process) or already-done (skip) |
| Doesn't track commits | PR could be updated, but time hasn't passed |
| Arbitrary threshold | Why 5 min? What if review takes 6 min? |
| Clock skew | Different instances may have slightly different times |

### Why Commit SHA is Better

| Benefit | Explanation |
|---------|-------------|
| Precise | Exactly identifies what was reviewed |
| Intent-clear | New commit = new review, same commit = skip |
| No timing issues | No race conditions from clock differences |
| Audit trail | Can see exactly which commits were reviewed |

---

## Edge Cases

### 1. PR Updated During Processing

**Scenario:** PR 357462 is at commit `abc123`. Processing starts. User pushes new commit `def456`. Another Pub/Sub message arrives.

**Behavior:** 
- First instance processes `abc123`, creates marker `pr-357462-abc123.json`
- Second instance sees different commit `def456`, creates marker `pr-357462-def456.json`
- Both process correctly ✅

### 2. Duplicate Messages (Same Commit)

**Scenario:** Pub/Sub delivers the same message twice within milliseconds.

**Behavior:**
- First instance: `blob.exists()` → False → tries atomic create → succeeds → processes
- Second instance: `blob.exists()` → False → tries atomic create → `PreconditionFailed` → skips

Only one processes ✅

### 3. Processing Failure

**Scenario:** Function crashes after claiming marker but before completing.

**Options:**
1. **Leave marker** → Prevents retry, may leave PR unreviewed
2. **Delete marker on error** → Allows retry, but Pub/Sub already retries on exception
3. **Use "processing" status** → Later cleanup job can retry old "processing" markers

**Recommended:** Option 3 with a separate cleanup mechanism (Cloud Scheduler job).

### 4. Very Old PRs

**Scenario:** A PR from 6 months ago is somehow triggered again.

**Behavior:** If the commit SHA matches, skip. If it's a new commit (even on old PR), process.

---

## GCS Bucket Structure (Updated)

```
gs://bucket/
├── reviews/                         ← Review outputs (existing)
│   └── 2026/01/02/
│       └── pr-357462-143022-review.md
│
└── idempotency/                     ← NEW: Idempotency markers
    ├── pr-357462-abc123def.json
    ├── pr-357463-789xyz000.json
    └── ...
```

---

## Cleanup Strategy

Old idempotency markers should be cleaned up to prevent unbounded growth.

### Option A: GCS Lifecycle Policy (Recommended)

```bash
# Set lifecycle rule: delete objects older than 30 days
cat > lifecycle.json << 'EOF'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {
        "age": 30,
        "matchesPrefix": ["idempotency/"]
      }
    }
  ]
}
EOF

gcloud storage buckets update gs://YOUR_BUCKET --lifecycle-file=lifecycle.json
```

### Option B: Cloud Scheduler Cleanup Job

Run daily to delete markers older than X days.

---

## Deployment Changes

### 1. Update requirements.txt

```
google-cloud-storage>=2.0.0
```

(Already included for review storage)

### 2. Pub/Sub Trigger Deployment

```bash
# Create Pub/Sub topic
gcloud pubsub topics create pr-review-trigger

# Deploy with Pub/Sub trigger
gcloud functions deploy pr-regression-review \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=review_pr_pubsub \
  --trigger-topic=pr-review-trigger \
  --memory=512MB \
  --timeout=300s \
  --set-env-vars="GCS_BUCKET=...,..." \
  --set-secrets="..."
```

### 3. Configure Azure DevOps Webhook → Pub/Sub

Use Azure DevOps Service Hooks to publish to Pub/Sub when PR is created/updated.

---

## Testing

### Simulate Duplicate Messages

```bash
# Publish same message twice rapidly
MESSAGE='{"pr_id": 357462}'

gcloud pubsub topics publish pr-review-trigger --message="$MESSAGE" &
gcloud pubsub topics publish pr-review-trigger --message="$MESSAGE" &
wait

# Check logs - only one should process
gcloud functions logs read pr-regression-review --limit=20
```

### Verify Idempotency Markers

```bash
# List markers
gcloud storage ls gs://YOUR_BUCKET/idempotency/

# View specific marker
gcloud storage cat gs://YOUR_BUCKET/idempotency/pr-357462-abc123def.json
```

---

## Summary

| Aspect | Choice |
|--------|--------|
| Idempotency Key | `pr_id` + `commit_sha` |
| Storage | GCS JSON marker files |
| Race Condition Handling | Atomic create with `if_generation_match=0` |
| Re-review Trigger | New commit SHA (automatic) |
| Cleanup | GCS lifecycle policy (30 days) |

This approach ensures:
- ✅ No duplicate reviews for the same commit
- ✅ New commits trigger new reviews
- ✅ Race conditions handled atomically
- ✅ Simple implementation using existing GCS bucket
- ✅ Clear audit trail of what was processed

