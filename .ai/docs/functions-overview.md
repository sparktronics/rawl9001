# Cloud Functions Overview

**Document Created:** 2026-01-20  
**Status:** Active  
**For:** Developers

## Overview

The PR Regression Review system consists of three Cloud Functions that work together to provide synchronous, asynchronous, and webhook-based PR review capabilities. This document describes how these functions integrate, handle idempotency, and the security patterns required in GCP.

---

## The Three Functions

### 1. `review_pr` - Synchronous HTTP Endpoint

**Entry Point:** `review_pr(request)`  
**Trigger:** HTTP POST  
**Purpose:** Direct, synchronous PR review with immediate response

```
┌──────────────┐    HTTP POST       ┌─────────────────┐
│   Client/    │ ─────────────────▶ │   review_pr     │
│   Curl/API   │   X-API-Key        │   (HTTP)        │
│              │   {"pr_id": 123}   │                 │
└──────────────┘                    └────────┬────────┘
                                             │
                                             │ Returns JSON
                                             ▼
                                    { "pr_id": 123,
                                      "max_severity": "blocking",
                                      "action_taken": "rejected",
                                      "storage_path": "gs://...",
                                      ... }
```

**Use Cases:**
- Manual testing and debugging
- Direct API integrations
- Immediate feedback required
- One-off PR reviews

**Characteristics:**
- ✅ Returns complete JSON response
- ✅ Synchronous execution (waits for completion)
- ❌ No idempotency handling
- ❌ Client must wait for full review (up to 5 minutes)

---

### 2. `review_pr_pubsub` - Asynchronous Worker

**Entry Point:** `review_pr_pubsub(cloud_event)`  
**Trigger:** Pub/Sub topic `pr-review-trigger`  
**Purpose:** Reliable async processing with idempotency

```
┌──────────────────┐   Subscribe    ┌────────────────────┐
│   Pub/Sub Topic  │ ──────────────▶│ review_pr_pubsub   │
│  pr-review-      │   Cloud Event  │    (Worker)        │
│  trigger         │                │                    │
└──────────────────┘                └─────────┬──────────┘
                                              │
                                              │ Process
                                              ▼
                                    ┌───────────────────────┐
                                    │ 1. Check idempotency  │
                                    │ 2. Fetch PR & diffs   │
                                    │ 3. Call Gemini        │
                                    │ 4. Post comment/reject│
                                    │ 5. Update marker      │
                                    └───────────────────────┘
```

**Use Cases:**
- Production PR reviews
- High-volume processing
- Retry on transient failures
- Decoupled from caller

**Characteristics:**
- ✅ Idempotency via GCS markers (`pr_id` + `commit_sha`)
- ✅ Automatic retries on failure
- ✅ Dead Letter Queue support
- ✅ Does not return data (raises exception on error)
- ✅ Scales automatically with message volume

---

### 3. `receive_webhook` - Webhook Receiver

**Entry Point:** `receive_webhook(request)`  
**Trigger:** HTTP POST (from Azure DevOps pipeline)  
**Purpose:** Fast webhook acknowledgment, queues to Pub/Sub

```
┌──────────────────┐   HTTP POST     ┌───────────────────┐
│ Azure DevOps     │ ───────────────▶│ receive_webhook   │
│ Pipeline         │  X-API-Key      │   (HTTP)          │
│ (on PR event)    │  {pr_id, sha}   │                   │
└──────────────────┘                 └─────────┬─────────┘
                                               │
                                               │ Publish
                                               ▼
                                     ┌──────────────────┐
                                     │  Pub/Sub Topic   │
                                     │  pr-review-      │
                                     │  trigger         │
                                     └────────┬─────────┘
                                              │
                                              │ Triggers
                                              ▼
                                     ┌──────────────────┐
                                     │ review_pr_pubsub │
                                     │    (Worker)      │
                                     └──────────────────┘
```

**Use Cases:**
- Azure DevOps pipeline integration
- Webhook endpoints requiring fast response
- Event-driven architecture
- Decoupling ingestion from processing

**Characteristics:**
- ✅ Fast response (< 1 second)
- ✅ Validates request and queues message
- ✅ Returns 202 Accepted immediately
- ✅ Minimal configuration (only needs API_KEY + PUBSUB_TOPIC)
- ❌ Does not process review itself

---

## How They Work Together

### Shared Core Logic

All three functions delegate to a centralized `process_pr_review()` function:

```python
@dataclass
class ReviewResult:
    pr_id: int
    pr_title: str
    max_severity: str
    has_blocking: bool
    has_warning: bool
    storage_path: str
    commented: bool
    action_taken: str | None  # "rejected", "commented", or None
```

**`process_pr_review()` handles:**
1. Building review prompt from PR diff
2. Calling Vertex AI Gemini API
3. Determining severity from response
4. Saving review to Cloud Storage
5. Posting PR comments (if blocking/warning)
6. Rejecting PR (if blocking severity)

This ensures **consistent behavior** regardless of entry point.

---

### Integration Pattern: Webhook → Pub/Sub → Worker

**Recommended production architecture:**

```
Azure DevOps Pipeline
    │
    │ 1. PR created/updated
    │
    ▼
receive_webhook (HTTP)
    │
    │ 2. Validate API key
    │    Parse pr_id + commit_sha
    │
    ▼
Publish to Pub/Sub
    │
    │ 3. Message queued
    │    Returns 202 Accepted
    │
    ▼
Pub/Sub Topic (pr-review-trigger)
    │
    │ 4. Delivers message (at-least-once)
    │
    ▼
review_pr_pubsub (Worker)
    │
    │ 5. Check idempotency marker
    │    Process review
    │    Update marker
    │
    ▼
Azure DevOps PR
    │
    │ 6. Comment posted
    │    PR rejected (if blocking)
```

**Benefits of this pattern:**
- **Fast webhook response** (< 1s) prevents timeouts
- **Automatic retries** on transient failures
- **Idempotency** prevents duplicate reviews
- **Dead Letter Queue** captures permanent failures
- **Scalability** handles burst traffic
- **Observability** via Pub/Sub metrics + Cloud Function logs

---

## Idempotency Strategy

### Problem

Pub/Sub provides **at-least-once delivery**, meaning the same message may be delivered multiple times due to:
- Network issues
- Acknowledgment timeouts
- Retry logic

Without idempotency, this causes:
- ❌ Duplicate PR comments
- ❌ Wasted Vertex AI quota
- ❌ Unnecessary Azure DevOps API calls

### Solution: GCS Markers with Composite Key

**Key:** `pr_id` + `commit_sha`

```
gs://bucket/idempotency/
├── pr-357462-abc123def456.json   ← PR 357462 at commit abc123de
├── pr-357462-def789ghi012.json   ← PR 357462 at commit def789gh (new commit)
└── pr-357463-xyz456abc789.json   ← Different PR
```

**Marker structure:**
```json
{
  "pr_id": 357462,
  "commit_sha": "abc123def456789",
  "status": "completed",
  "processed_at": "2026-01-20T10:30:00Z",
  "max_severity": "warning",
  "commented": true
}
```

### How It Works

```
┌────────────────────────────────────────────────────────────┐
│ Pub/Sub Message: {"pr_id": 357462, "commit_sha": "abc123"}│
└──────────────────────────────┬─────────────────────────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │ Check if marker exists │
                  │ pr-357462-abc123.json  │
                  └─────────┬──────────────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
          Exists                      Missing
              │                           │
              ▼                           ▼
    ┌──────────────────┐      ┌───────────────────────────┐
    │ SKIP PROCESSING  │      │ Atomic Create (GCS)       │
    │ Already reviewed │      │ if_generation_match=0     │
    └──────────────────┘      └─────────┬─────────────────┘
                                        │
                              ┌─────────┴──────────┐
                              │                    │
                          Success             Failed
                        (we claimed it)   (race condition)
                              │                    │
                              ▼                    ▼
                    ┌──────────────────┐  ┌─────────────────┐
                    │ PROCESS REVIEW   │  │ SKIP - Another  │
                    │ Call Gemini      │  │ instance claimed│
                    │ Post comment     │  │ it first        │
                    │ Update marker    │  └─────────────────┘
                    └──────────────────┘
```

**Key mechanism:** `if_generation_match=0`
- GCS atomic operation: "only create if file doesn't exist"
- If two instances try simultaneously, only one succeeds
- Winner processes, loser skips

### Why Commit SHA?

| Scenario | Behavior |
|----------|----------|
| Same PR, same commit, duplicate message | ✅ Skip (already reviewed) |
| Same PR, new commit pushed | ✅ Process (new code to review) |
| Different PR | ✅ Process (different PR) |

**Alternative considered:** Time-based markers (skip if < 5 min old)
- ❌ Arbitrary threshold
- ❌ Doesn't track actual code changes
- ❌ Clock skew issues

---

## Retry Handling & Dead Letter Queue

### Retry Strategy

The `review_pr_pubsub` function uses intelligent retry logic:

```python
# HTTP errors
- 401, 403, 404 → Non-retryable (acknowledge immediately → DLQ)
- 500, 502, 503 → Retryable (up to 5 attempts)
- Timeout → Retryable (up to 5 attempts)

# Tracking
- Idempotency marker tracks retry_count
- After MAX_RETRY_ATTEMPTS (3), mark as "failed"
- Acknowledge message (prevents infinite retries)
```

### Dead Letter Queue Configuration

```bash
# Create DLQ topic and subscription
gcloud pubsub topics create pr-review-dlq
gcloud pubsub subscriptions create pr-review-dlq-sub --topic=pr-review-dlq

# Configure main subscription with DLQ
gcloud pubsub subscriptions update pr-review-sub \
  --dead-letter-topic=pr-review-dlq \
  --max-delivery-attempts=5
```

**DLQ Reprocessing Function:** `process_dead_letter_queue`
- Validates credentials before processing
- Pulls messages from DLQ
- Resets idempotency markers
- Republishes to main topic

---

## GCP Security & Permissions

### Required IAM Roles by Function

#### 1. `review_pr` (HTTP)

| Resource | Role | Why |
|----------|------|-----|
| Cloud Storage bucket | `roles/storage.objectAdmin` | Save reviews, manage idempotency markers |
| Vertex AI | `roles/aiplatform.user` | Call Gemini API |
| Secret Manager | `roles/secretmanager.secretAccessor` | Access AZURE_DEVOPS_PAT, API_KEY |

#### 2. `review_pr_pubsub` (Worker)

| Resource | Role | Why |
|----------|------|-----|
| Cloud Storage bucket | `roles/storage.objectAdmin` | Save reviews, manage idempotency markers |
| Vertex AI | `roles/aiplatform.user` | Call Gemini API |
| Secret Manager | `roles/secretmanager.secretAccessor` | Access AZURE_DEVOPS_PAT, API_KEY |

#### 3. `receive_webhook` (Webhook)

| Resource | Role | Why |
|----------|------|-----|
| Pub/Sub topic | `roles/pubsub.publisher` | Publish messages to pr-review-trigger |
| Secret Manager | `roles/secretmanager.secretAccessor` | Access API_KEY (for authentication) |

### Service Accounts

Each function uses the **default compute service account** unless custom SA configured:

```
{PROJECT_NUMBER}-compute@developer.gserviceaccount.com
```

**Best practice:** Create dedicated service accounts per function:

```bash
# Create service account for webhook receiver
gcloud iam service-accounts create pr-webhook-sa \
  --display-name="PR Review Webhook Receiver"

# Grant Pub/Sub publisher permission
gcloud pubsub topics add-iam-policy-binding pr-review-trigger \
  --member="serviceAccount:pr-webhook-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/pubsub.publisher"

# Deploy with custom SA
gcloud functions deploy pr-review-webhook \
  --service-account=pr-webhook-sa@PROJECT_ID.iam.gserviceaccount.com \
  ...
```

### Secrets Configuration

```bash
# Create secrets
echo -n "your-azure-pat" | gcloud secrets create azure-devops-pat --data-file=-
echo -n "your-api-key" | gcloud secrets create pr-review-api-key --data-file=-

# Grant access to function service account
gcloud secrets add-iam-policy-binding azure-devops-pat \
  --member="serviceAccount:SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding pr-review-api-key \
  --member="serviceAccount:SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor"
```

### Storage Bucket Security

```bash
# Create bucket with uniform access
gcloud storage buckets create gs://BUCKET_NAME \
  --location=us-central1 \
  --uniform-bucket-level-access

# Grant function access
gcloud storage buckets add-iam-policy-binding gs://BUCKET_NAME \
  --member="serviceAccount:SA_EMAIL" \
  --role="roles/storage.objectAdmin"

# Set lifecycle policy for idempotency markers
cat > lifecycle.json << 'EOF'
{
  "rule": [{
    "action": {"type": "Delete"},
    "condition": {
      "age": 30,
      "matchesPrefix": ["idempotency/"]
    }
  }]
}
EOF

gcloud storage buckets update gs://BUCKET_NAME \
  --lifecycle-file=lifecycle.json
```

### Network Security

**Cloud Functions Gen2** runs on Cloud Run:

```bash
# Allow unauthenticated (uses API key in application)
--allow-unauthenticated

# OR require IAM authentication
--no-allow-unauthenticated

# If IAM auth, invoke with:
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://FUNCTION_URL
```

**Best practice for webhooks:**
- Use `--allow-unauthenticated` (webhook needs public access)
- Validate `X-API-Key` header in function code
- Store API key in Secret Manager

---

## Environment Variables Reference

### Minimal Configuration per Function

#### `review_pr` (HTTP)
```bash
Required:
- API_KEY (secret)
- GCS_BUCKET
- AZURE_DEVOPS_PAT (secret)
- AZURE_DEVOPS_ORG
- AZURE_DEVOPS_PROJECT
- AZURE_DEVOPS_REPO
- VERTEX_PROJECT

Optional:
- VERTEX_LOCATION (default: us-central1)
- GEMINI_MODEL (default: gemini-2.5-pro)
```

#### `review_pr_pubsub` (Worker)
```bash
Required:
- GCS_BUCKET
- AZURE_DEVOPS_PAT (secret)
- AZURE_DEVOPS_ORG
- AZURE_DEVOPS_PROJECT
- AZURE_DEVOPS_REPO
- VERTEX_PROJECT

Optional:
- VERTEX_LOCATION (default: us-central1)
- GEMINI_MODEL (default: gemini-2.5-pro)
- DLQ_SUBSCRIPTION (default: pr-review-dlq-sub)
```

#### `receive_webhook` (Webhook)
```bash
Required:
- API_KEY (secret)
- VERTEX_PROJECT

Optional:
- PUBSUB_TOPIC (default: pr-review-trigger)
```

---

## Deployment Order

### 1. Prerequisites

```bash
# Enable APIs
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

### 2. Create Infrastructure

```bash
# Secrets
echo -n "your-azure-pat" | gcloud secrets create azure-devops-pat --data-file=-
echo -n "your-api-key" | gcloud secrets create pr-review-api-key --data-file=-

# Storage bucket
gcloud storage buckets create gs://BUCKET_NAME --location=us-central1

# Pub/Sub topic
gcloud pubsub topics create pr-review-trigger

# DLQ (optional but recommended)
gcloud pubsub topics create pr-review-dlq
gcloud pubsub subscriptions create pr-review-dlq-sub --topic=pr-review-dlq
```

### 3. Deploy Functions (Order Matters!)

**Step 1: Deploy Worker First** (needs to exist before webhook publishes)

```bash
gcloud functions deploy pr-review-pubsub \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=review_pr_pubsub \
  --trigger-topic=pr-review-trigger \
  --memory=512MB \
  --timeout=300s \
  --set-env-vars="GCS_BUCKET=...,AZURE_DEVOPS_ORG=...,..." \
  --set-secrets="AZURE_DEVOPS_PAT=azure-devops-pat:latest"
```

**Step 2: Deploy Webhook Receiver**

```bash
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
```

**Step 3: Deploy HTTP Function (Optional)**

```bash
gcloud functions deploy pr-regression-review \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=review_pr \
  --trigger-http \
  --allow-unauthenticated \
  --memory=512MB \
  --timeout=300s \
  --set-env-vars="GCS_BUCKET=...,AZURE_DEVOPS_ORG=...,..." \
  --set-secrets="AZURE_DEVOPS_PAT=azure-devops-pat:latest,API_KEY=pr-review-api-key:latest"
```

### 4. Configure IAM Permissions

```bash
# Get service accounts
WEBHOOK_SA=$(gcloud functions describe pr-review-webhook \
  --gen2 --region=us-central1 \
  --format='value(serviceConfig.serviceAccountEmail)')

WORKER_SA=$(gcloud functions describe pr-review-pubsub \
  --gen2 --region=us-central1 \
  --format='value(serviceConfig.serviceAccountEmail)')

# Grant Pub/Sub publisher to webhook
gcloud pubsub topics add-iam-policy-binding pr-review-trigger \
  --member="serviceAccount:${WEBHOOK_SA}" \
  --role="roles/pubsub.publisher"

# Grant storage access to worker
gcloud storage buckets add-iam-policy-binding gs://BUCKET_NAME \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/storage.objectAdmin"
```

---

## Monitoring & Observability

### Key Metrics to Monitor

| Metric | Location | Purpose |
|--------|----------|---------|
| Function executions | Cloud Functions > Metrics | Invocation count |
| Error rate | Cloud Functions > Metrics | Failed executions |
| Execution time | Cloud Functions > Metrics | Performance |
| Pub/Sub messages | Pub/Sub > Metrics | Queue depth, delivery rate |
| DLQ messages | Pub/Sub > DLQ subscription | Permanent failures |
| Storage operations | Cloud Storage > Metrics | Review saves, marker operations |
| Vertex AI usage | Vertex AI > Dashboard | Token usage, cost |

### Log Queries (Cloud Logging)

**View all PR reviews:**
```
resource.type="cloud_function"
resource.labels.function_name=~"pr-review.*"
jsonPayload.message=~".*COMPLETE.*"
```

**Find idempotency skips:**
```
resource.type="cloud_function"
resource.labels.function_name="pr-review-pubsub"
jsonPayload.message=~".*already processed.*"
```

**Track errors:**
```
resource.type="cloud_function"
resource.labels.function_name=~"pr-review.*"
severity="ERROR"
```

### Alerting Policy Examples

```bash
# Alert on high error rate
gcloud alpha monitoring policies create \
  --notification-channels=CHANNEL_ID \
  --display-name="PR Review Function Errors" \
  --condition-display-name="Error rate > 10%" \
  --condition-threshold-value=0.1 \
  --condition-threshold-duration=300s
```

---

## Testing the Integration

### 1. Test Webhook Receiver

```bash
WEBHOOK_URL=$(gcloud functions describe pr-review-webhook \
  --gen2 --region=us-central1 --format='value(serviceConfig.uri)')

curl -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"pr_id": 357462, "commit_sha": "abc123def456"}'

# Expected response (202):
# {
#   "status": "queued",
#   "message_id": "1234567890",
#   "pr_id": 357462,
#   "commit_sha": "abc123de"
# }
```

### 2. Verify Pub/Sub Message

```bash
# Check that message was published
gcloud pubsub subscriptions pull pr-review-sub --limit=1 --auto-ack

# View worker function logs
gcloud functions logs read pr-review-pubsub \
  --gen2 --region=us-central1 --limit=20
```

### 3. Test End-to-End

```bash
# Trigger via webhook
curl -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"pr_id": 357462, "commit_sha": "abc123def456"}'

# Wait a few seconds, then check:

# 1. Worker logs
gcloud functions logs read pr-review-pubsub --gen2 --region=us-central1 --limit=50

# 2. Idempotency marker created
gcloud storage cat gs://BUCKET_NAME/idempotency/pr-357462-abc123def456.json

# 3. Review saved
gcloud storage ls gs://BUCKET_NAME/reviews/2026/01/20/

# 4. PR comment posted (check Azure DevOps)
```

### 4. Test Idempotency

```bash
# Send same message twice
curl -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"pr_id": 357462, "commit_sha": "abc123def456"}' &

curl -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"pr_id": 357462, "commit_sha": "abc123def456"}' &

wait

# Check logs - should see "already processed" for second message
gcloud functions logs read pr-review-pubsub --gen2 --region=us-central1 --limit=10
```

---

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Webhook returns 401 | Invalid API key | Check X-API-Key header matches secret |
| Worker not triggered | Missing IAM permission | Grant `roles/pubsub.publisher` to webhook SA |
| Duplicate comments | Idempotency marker not created | Check GCS bucket permissions |
| Review not saved | Storage access denied | Grant `roles/storage.objectAdmin` to worker SA |
| Gemini API error | Missing Vertex AI permission | Grant `roles/aiplatform.user` to worker SA |
| DLQ messages accumulating | Credentials expired/invalid | Check AZURE_DEVOPS_PAT, rotate if needed |

### Debug Commands

```bash
# View function configuration
gcloud functions describe FUNCTION_NAME --gen2 --region=us-central1

# Check service account permissions
gcloud projects get-iam-policy PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:SA_EMAIL"

# View recent errors
gcloud functions logs read FUNCTION_NAME \
  --gen2 --region=us-central1 \
  --filter="severity=ERROR" \
  --limit=20

# Test Pub/Sub publishing manually
gcloud pubsub topics publish pr-review-trigger \
  --message='{"pr_id": 123456, "commit_sha": "abc123def"}'
```

---

## Summary

### Function Comparison

| Feature | review_pr (HTTP) | review_pr_pubsub (Worker) | receive_webhook |
|---------|------------------|---------------------------|-----------------|
| **Trigger** | HTTP POST | Pub/Sub message | HTTP POST |
| **Response** | JSON (200) | None (raises on error) | JSON (202) |
| **Idempotency** | No | Yes (GCS markers) | N/A (queues only) |
| **Timeout** | 300s | 300s | 30s |
| **Memory** | 512MB | 512MB | 256MB |
| **Use Case** | Testing, direct API | Production reviews | Pipeline integration |
| **Retries** | No | Yes (Pub/Sub) | No (fast fail) |

### Architecture Decision Record

**Why three functions?**

1. **Separation of Concerns**
   - Webhook: Fast ingestion (< 1s response)
   - Worker: Heavy processing (up to 5 min)
   - HTTP: Direct access for testing

2. **Reliability**
   - Pub/Sub provides delivery guarantees
   - DLQ captures permanent failures
   - Idempotency prevents duplicates

3. **Scalability**
   - Webhook can handle high request rate
   - Worker auto-scales based on queue depth
   - Each function sized appropriately

4. **Cost Efficiency**
   - Webhook uses minimal resources (256MB)
   - Worker only runs when needed
   - No always-on servers

**Recommended production setup:**
- ✅ Use `receive_webhook` + `review_pr_pubsub` for Azure DevOps integration
- ✅ Configure Dead Letter Queue with retry limits
- ✅ Set up monitoring alerts on error rate and DLQ depth
- ✅ Use custom service accounts with least-privilege permissions
- ✅ Keep `review_pr` HTTP function for manual testing/debugging

---

## Additional Resources

- **Main Implementation:** `main.py` (all three entry points)
- **Deployment Guide:** `README.md`
- **Idempotency Details:** `.cursor/docs/idempotency-strategy.md`
- **Webhook Integration:** `.cursor/docs/webhook-receiver-plan.md`
- **Cloud Function Design:** `.cursor/docs/cloud-function-implementation.md`

