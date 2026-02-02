# Webhook Receiver Cloud Function Plan

**Document Created:** 2026-01-03  
**Status:** Implemented

## Overview

This document describes the implementation plan for a webhook receiver Cloud Function that accepts HTTP requests from Azure DevOps pipelines and publishes messages to Pub/Sub for asynchronous processing.

---

## Architecture

```
┌─────────────────────────┐     HTTP POST         ┌─────────────────────────┐
│  Azure DevOps Pipeline  │ ─────────────────────▶│  Webhook Receiver       │
│  (on PR create/update)  │   X-API-Key header    │  Cloud Function         │
│                         │   + JSON payload      │  entry: receive_webhook │
└─────────────────────────┘                       └───────────┬─────────────┘
                                                              │
                                                              │ Publish
                                                              ▼
                                                  ┌─────────────────────────┐
                                                  │  Pub/Sub Topic          │
                                                  │  pr-review-trigger      │
                                                  └───────────┬─────────────┘
                                                              │
                                                              │ Trigger
                                                              ▼
                                                  ┌─────────────────────────┐
                                                  │  Worker Function        │
                                                  │  entry: review_pr_pubsub│
                                                  │  (existing + idempotency│
                                                  └─────────────────────────┘
```

---

## Infrastructure Setup (gcloud commands)

Run these commands to set up the required infrastructure before deploying the functions.

### Prerequisites

```bash
# Set your project ID
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"

# Ensure you're authenticated and using the correct project
gcloud auth login
gcloud config set project $PROJECT_ID
```

### 1. Enable Required APIs

```bash
gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  pubsub.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com
```

### 2. Create Pub/Sub Topic

```bash
# Create the topic for PR review messages
gcloud pubsub topics create pr-review-trigger

# Verify it was created
gcloud pubsub topics list --filter="name:pr-review-trigger"
```

### 3. Create Secrets (if not already created)

```bash
# API Key for webhook authentication
echo -n "your-secure-api-key-here" | \
  gcloud secrets create pr-review-api-key --data-file=-

# Azure DevOps PAT (if not already created)
echo -n "your-azure-devops-pat" | \
  gcloud secrets create azure-devops-pat --data-file=-
```

### 4. Create GCS Bucket (if not already created)

```bash
export BUCKET_NAME="pr-reviews-${PROJECT_ID}"

gcloud storage buckets create gs://${BUCKET_NAME} \
  --location=${REGION} \
  --uniform-bucket-level-access

# Set lifecycle policy for idempotency markers (auto-delete after 30 days)
cat > /tmp/lifecycle.json << 'EOF'
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

gcloud storage buckets update gs://${BUCKET_NAME} \
  --lifecycle-file=/tmp/lifecycle.json
```

### 5. Deploy Webhook Receiver Function

```bash
gcloud functions deploy pr-review-webhook \
  --gen2 \
  --runtime=python312 \
  --region=${REGION} \
  --source=. \
  --entry-point=receive_webhook \
  --trigger-http \
  --allow-unauthenticated \
  --memory=256MB \
  --timeout=30s \
  --set-env-vars="PUBSUB_TOPIC=pr-review-trigger,VERTEX_PROJECT=${PROJECT_ID}" \
  --set-secrets="API_KEY=pr-review-api-key:latest"

# Get the webhook URL
WEBHOOK_URL=$(gcloud functions describe pr-review-webhook \
  --gen2 --region=${REGION} \
  --format='value(serviceConfig.uri)')
echo "Webhook URL: ${WEBHOOK_URL}"
```

### 6. Deploy Worker Function (Pub/Sub triggered)

```bash
gcloud functions deploy pr-regression-review \
  --gen2 \
  --runtime=python312 \
  --region=${REGION} \
  --source=. \
  --entry-point=review_pr_pubsub \
  --trigger-topic=pr-review-trigger \
  --memory=512MB \
  --timeout=300s \
  --set-env-vars="GCS_BUCKET=${BUCKET_NAME},AZURE_DEVOPS_ORG=your-org,AZURE_DEVOPS_PROJECT=your-project,AZURE_DEVOPS_REPO=your-repo,VERTEX_PROJECT=${PROJECT_ID},VERTEX_LOCATION=${REGION}" \
  --set-secrets="AZURE_DEVOPS_PAT=azure-devops-pat:latest,API_KEY=pr-review-api-key:latest"
```

### 7. Grant IAM Permissions

```bash
# Get the webhook function's service account
WEBHOOK_SA=$(gcloud functions describe pr-review-webhook \
  --gen2 --region=${REGION} \
  --format='value(serviceConfig.serviceAccountEmail)')

# Grant Pub/Sub publisher permission to webhook function
gcloud pubsub topics add-iam-policy-binding pr-review-trigger \
  --member="serviceAccount:${WEBHOOK_SA}" \
  --role="roles/pubsub.publisher"

# Get the worker function's service account
WORKER_SA=$(gcloud functions describe pr-regression-review \
  --gen2 --region=${REGION} \
  --format='value(serviceConfig.serviceAccountEmail)')

# Grant GCS access to worker function (for reviews and idempotency markers)
gcloud storage buckets add-iam-policy-binding gs://${BUCKET_NAME} \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/storage.objectAdmin"

# Grant Vertex AI access to worker function
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/aiplatform.user"
```

### 8. Verify Deployment

```bash
# Test webhook receiver
curl -X POST "${WEBHOOK_URL}" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secure-api-key-here" \
  -d '{"pr_id": 123456, "commit_sha": "abc123def456"}'

# Check webhook logs
gcloud functions logs read pr-review-webhook --gen2 --region=${REGION} --limit=10

# Check worker logs
gcloud functions logs read pr-regression-review --gen2 --region=${REGION} --limit=10

# List Pub/Sub messages (for debugging)
gcloud pubsub topics list
```

### Quick Reference: All Commands in Order

```bash
# 1. Set variables
export PROJECT_ID="your-project"
export REGION="us-central1"
export BUCKET_NAME="pr-reviews-${PROJECT_ID}"

# 2. Enable APIs
gcloud services enable cloudfunctions.googleapis.com cloudbuild.googleapis.com run.googleapis.com artifactregistry.googleapis.com pubsub.googleapis.com storage.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com

# 3. Create Pub/Sub topic
gcloud pubsub topics create pr-review-trigger

# 4. Create secrets
echo -n "your-api-key" | gcloud secrets create pr-review-api-key --data-file=-
echo -n "your-ado-pat" | gcloud secrets create azure-devops-pat --data-file=-

# 5. Create bucket
gcloud storage buckets create gs://${BUCKET_NAME} --location=${REGION}

# 6. Deploy webhook receiver
gcloud functions deploy pr-review-webhook --gen2 --runtime=python312 --region=${REGION} --source=. --entry-point=receive_webhook --trigger-http --allow-unauthenticated --memory=256MB --timeout=30s --set-env-vars="PUBSUB_TOPIC=pr-review-trigger,VERTEX_PROJECT=${PROJECT_ID}" --set-secrets="API_KEY=pr-review-api-key:latest"

# 7. Deploy worker
gcloud functions deploy pr-regression-review --gen2 --runtime=python312 --region=${REGION} --source=. --entry-point=review_pr_pubsub --trigger-topic=pr-review-trigger --memory=512MB --timeout=300s --set-env-vars="GCS_BUCKET=${BUCKET_NAME},AZURE_DEVOPS_ORG=your-org,AZURE_DEVOPS_PROJECT=your-project,AZURE_DEVOPS_REPO=your-repo,VERTEX_PROJECT=${PROJECT_ID},VERTEX_LOCATION=${REGION}" --set-secrets="AZURE_DEVOPS_PAT=azure-devops-pat:latest,API_KEY=pr-review-api-key:latest"

# 8. Grant IAM permissions
WEBHOOK_SA=$(gcloud functions describe pr-review-webhook --gen2 --region=${REGION} --format='value(serviceConfig.serviceAccountEmail)')
gcloud pubsub topics add-iam-policy-binding pr-review-trigger --member="serviceAccount:${WEBHOOK_SA}" --role="roles/pubsub.publisher"
```

---

## Message Format

### HTTP Request (Pipeline → Webhook Receiver)

```http
POST /
Content-Type: application/json
X-API-Key: <api-key>

{
  "pr_id": 357462,
  "commit_sha": "abc123def456789..."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pr_id` | integer | ✅ | Azure DevOps Pull Request ID |
| `commit_sha` | string | ✅ | Source branch commit SHA (for idempotency) |

### Pub/Sub Message (Webhook Receiver → Worker)

```json
{
  "pr_id": 357462,
  "commit_sha": "abc123def456789...",
  "received_at": "2026-01-03T10:30:00Z",
  "source": "azure-devops-pipeline"
}
```

---

## Implementation Tasks

### 1. Create Pub/Sub Topic

```bash
gcloud pubsub topics create pr-review-trigger
```

### 2. Add Webhook Receiver Function to `main.py`

New entry point: `receive_webhook(request)`

**Responsibilities:**
1. Validate `X-API-Key` header
2. Parse and validate JSON payload (`pr_id`, `commit_sha`)
3. Publish message to Pub/Sub topic
4. Return success/error response

### 3. Update Worker Function (`review_pr_pubsub`)

**Changes:**
- Accept `commit_sha` from message (instead of fetching from ADO)
- If `commit_sha` not in message, fall back to fetching from ADO (backward compatibility)

### 4. Add Pub/Sub Client Dependency

```txt
# requirements.txt
google-cloud-pubsub>=2.0.0
```

### 5. Add Environment Variable

| Variable | Description |
|----------|-------------|
| `PUBSUB_TOPIC` | Pub/Sub topic name (default: `pr-review-trigger`) |

---

## Webhook Receiver Function Pseudocode

```python
@functions_framework.http
def receive_webhook(request):
    """
    HTTP webhook receiver for Azure DevOps pipeline.
    Validates request and publishes to Pub/Sub for async processing.
    """
    
    # 1. Validate API Key
    api_key = request.headers.get("X-API-Key")
    if api_key != config["API_KEY"]:
        return {"error": "Unauthorized"}, 401
    
    # 2. Parse JSON body
    data = request.get_json()
    pr_id = data.get("pr_id")
    commit_sha = data.get("commit_sha")
    
    if not pr_id or not commit_sha:
        return {"error": "Missing pr_id or commit_sha"}, 400
    
    # 3. Publish to Pub/Sub
    message = {
        "pr_id": pr_id,
        "commit_sha": commit_sha,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "source": "azure-devops-pipeline"
    }
    
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project_id, topic_name)
    
    future = publisher.publish(
        topic_path,
        json.dumps(message).encode("utf-8")
    )
    message_id = future.result()
    
    # 4. Return success
    return {
        "status": "queued",
        "message_id": message_id,
        "pr_id": pr_id,
        "commit_sha": commit_sha[:8]
    }, 202
```

---

## Deployment

### Deploy Both Functions (Same Codebase)

**Option A: Two separate deployments (same source, different entry points)**

```bash
# 1. Deploy webhook receiver (HTTP trigger)
gcloud functions deploy pr-review-webhook \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=receive_webhook \
  --trigger-http \
  --allow-unauthenticated \
  --memory=256MB \
  --timeout=30s \
  --set-env-vars="PUBSUB_TOPIC=pr-review-trigger,VERTEX_PROJECT=..." \
  --set-secrets="API_KEY=pr-review-api-key:latest"

# 2. Deploy worker (Pub/Sub trigger) - existing deployment command
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
  --set-secrets="AZURE_DEVOPS_PAT=...,API_KEY=..."
```

### Grant Pub/Sub Publish Permission

The webhook function's service account needs `roles/pubsub.publisher`:

```bash
# Get the webhook function's service account
SA=$(gcloud functions describe pr-review-webhook --gen2 --region=us-central1 --format='value(serviceConfig.serviceAccountEmail)')

# Grant publish permission
gcloud pubsub topics add-iam-policy-binding pr-review-trigger \
  --member="serviceAccount:$SA" \
  --role="roles/pubsub.publisher"
```

---

## Azure DevOps Pipeline Integration

Example YAML task to call the webhook:

```yaml
# azure-pipelines.yml
trigger: none

pr:
  branches:
    include:
      - main
      - develop

jobs:
- job: TriggerPRReview
  pool:
    vmImage: 'ubuntu-latest'
  steps:
  - script: |
      curl -X POST "$(WEBHOOK_URL)" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $(PR_REVIEW_API_KEY)" \
        -d '{
          "pr_id": $(System.PullRequest.PullRequestId),
          "commit_sha": "$(Build.SourceVersion)"
        }'
    displayName: 'Trigger PR Review'
```

| Variable | Source |
|----------|--------|
| `$(System.PullRequest.PullRequestId)` | ADO predefined variable |
| `$(Build.SourceVersion)` | The commit SHA that triggered the build |
| `$(WEBHOOK_URL)` | Pipeline variable (Cloud Function URL) |
| `$(PR_REVIEW_API_KEY)` | Pipeline secret variable |

---

## Response Codes

| Status | Meaning |
|--------|---------|
| 202 | Accepted - Message queued to Pub/Sub |
| 400 | Bad Request - Missing `pr_id` or `commit_sha` |
| 401 | Unauthorized - Invalid or missing API key |
| 500 | Server Error - Pub/Sub publish failed |

---

## Worker Function Architecture

The `review_pr_pubsub` function uses the shared `process_pr_review()` function:

1. **Accept `commit_sha` from message** (when provided by webhook)
2. **Fall back to fetching** from ADO if not provided (backward compatibility)
3. **Call shared logic** via `process_pr_review()`

```python
# In review_pr_pubsub:
pr_id = message.get("pr_id")
commit_sha = message.get("commit_sha")  # From webhook

# Fetch PR metadata and file diffs
pr = ado.get_pull_request(pr_id)
if not commit_sha:
    commit_sha = pr["lastMergeSourceCommit"]["commitId"]

file_diffs = ado.get_pr_diff(pr_id)

# Use shared core logic (returns ReviewResult dataclass)
result = process_pr_review(config, ado, pr_id, pr, file_diffs)

# Update idempotency marker
update_marker_completed(bucket_name, pr_id, commit_sha, result.max_severity, result.commented)
```

The same `process_pr_review()` function is used by both HTTP and Pub/Sub entry points, ensuring consistent behavior.

---

## Files to Modify

| File | Change |
|------|--------|
| `main.py` | Add `receive_webhook()` entry point |
| `main.py` | Update `review_pr_pubsub()` to use message `commit_sha` |
| `requirements.txt` | Add `google-cloud-pubsub>=2.0.0` |

---

## Testing Plan

### 1. Local Testing with Functions Framework

```bash
# Start webhook receiver locally
functions-framework --target=receive_webhook --port=8081

# Test with curl
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-key" \
  -d '{"pr_id": 357462, "commit_sha": "abc123def"}'
```

### 2. Integration Test

```bash
# Publish test message to verify end-to-end
gcloud pubsub topics publish pr-review-trigger \
  --message='{"pr_id": 357462, "commit_sha": "abc123def"}'

# Check worker logs
gcloud functions logs read pr-regression-review --limit=20
```

---

## Summary

| Component | Entry Point | Trigger | Purpose |
|-----------|-------------|---------|---------|
| Webhook Receiver | `receive_webhook` | HTTP | Accept pipeline requests, publish to Pub/Sub |
| Worker | `review_pr_pubsub` | Pub/Sub | Process PR review with idempotency |
| Direct HTTP (existing) | `review_pr` | HTTP | Synchronous review (unchanged) |

This design:
- ✅ Decouples ingestion from processing
- ✅ Leverages Pub/Sub for reliable delivery
- ✅ Uses commit SHA for idempotency
- ✅ Same codebase, multiple entry points
- ✅ Simple API key authentication

