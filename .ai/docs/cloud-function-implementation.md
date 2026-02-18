# Cloud Function Implementation Plan

**Document Created:** 2026-01-01  
**Status:** Implemented  
**Last Updated:** 2026-01-07

## Overview

This document describes the implementation of the PR Regression Review script as a Google Cloud Function with automatic PR commenting and rejection capabilities.

---

## Architecture

The system supports multiple entry points that share a centralized review processing function:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           ENTRY POINTS                                   │
├─────────────────────┬─────────────────────┬─────────────────────────────┤
│  HTTP Endpoint      │  Pub/Sub Trigger    │  Webhook Receiver           │
│  review_pr()        │  review_pr_pubsub() │  receive_webhook()          │
│  (synchronous)      │  (async + idempot.) │  (queues to Pub/Sub)        │
└─────────┬───────────┴──────────┬──────────┴─────────────────────────────┘
          │                      │
          │    ┌─────────────────┤
          │    │                 │
          ▼    ▼                 │
┌─────────────────────────────┐  │
│  process_pr_review()        │  │  (shared core logic)
│  → ReviewResult dataclass   │  │
├─────────────────────────────┤  │
│  1. Build review prompt     │  │
│  2. Call Gemini API         │  │
│  3. Determine severity      │  │
│  4. Save to Cloud Storage   │  │
│  5. Post comment/reject PR  │  │
└─────────────────────────────┘  │
                                 │
                                 │ (queues message)
                                 ▼
                      ┌─────────────────────┐
                      │  Pub/Sub Topic      │
                      │  pr-review-trigger  │
                      └─────────────────────┘
```

### Entry Points

| Entry Point | Trigger | Returns | Idempotency |
|-------------|---------|---------|-------------|
| `review_pr` | HTTP POST | JSON response | No |
| `review_pr_pubsub` | Cloud Event (Pub/Sub) | None (raises on error) | Yes (GCS markers) |
| `receive_webhook` | HTTP POST | JSON (202 Accepted) | N/A (queues only) |

### Shared Core Logic

The `process_pr_review()` function contains all review logic:
- Called by both HTTP and Pub/Sub entry points
- Returns a `ReviewResult` dataclass with all review details
- Handles Gemini API calls, storage, commenting, and PR rejection

---

## Severity Detection Logic

The function parses the Gemini response for severity markers:

```python
def get_max_severity(review: str) -> str:
    if "**Severity:** blocking" in review:
        return "blocking"
    elif "**Severity:** warning" in review:
        return "warning"
    return "info"
```

### Actions by Severity

| Severity | Post Comment | Reject PR | Store in GCS |
|----------|--------------|-----------|--------------|
| blocking | ✅ | ✅ (vote -10) | ✅ |
| warning | ✅ | ❌ | ✅ |
| info | ❌ | ❌ | ✅ |

---

## API Contract

### Request

```
POST /
Content-Type: application/json
X-API-Key: <api-key>

{
  "pr_id": 12345
}
```

### Response (Success)

```json
{
  "pr_id": 12345,
  "title": "PR Title",
  "files_changed": 5,
  "max_severity": "blocking",
  "has_blocking": true,
  "has_warning": false,
  "action_taken": "rejected",
  "commented": true,
  "storage_path": "gs://bucket/reviews/2026/01/01/pr-12345-143022-review.md",
  "review_preview": "# PR Review: ..."
}
```

### Response (Error)

```json
{
  "error": "Error message"
}
```

| HTTP Status | Meaning |
|-------------|---------|
| 200 | Success |
| 400 | Bad request (missing/invalid pr_id) |
| 401 | Invalid or missing API key |
| 500 | Server error (missing config) |
| 502 | Azure DevOps API error |

---

## Environment Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `API_KEY` | Secret Manager | API key for request authentication |
| `GCS_BUCKET` | Environment | Cloud Storage bucket name |
| `AZURE_DEVOPS_PAT` | Secret Manager | Azure DevOps Personal Access Token |
| `AZURE_DEVOPS_ORG` | Environment | Azure DevOps organization |
| `AZURE_DEVOPS_PROJECT` | Environment | Azure DevOps project |
| `AZURE_DEVOPS_REPO` | Environment | Repository name or ID |
| `VERTEX_PROJECT` | Environment | GCP project for Vertex AI |
| `VERTEX_LOCATION` | Environment | GCP region (default: us-central1) |

---

## Azure DevOps API Endpoints Used

| Action | Method | Endpoint |
|--------|--------|----------|
| Get PR | GET | `/git/repositories/{repo}/pullrequests/{id}` |
| Get diffs (change list) | GET | `/git/repositories/{repo}/diffs/commits?baseVersion=...&targetVersion=...` |
| Get file | GET | `/git/repositories/{repo}/items?path=...` |
| Post comment | POST | `/git/repositories/{repo}/pullrequests/{id}/threads` |
| Reject PR | PUT | `/git/repositories/{repo}/pullrequests/{id}/reviewers/{userId}` |
| Get current user | GET | `/_apis/connectionData` |

The **diffs/commits** endpoint returns the list of changed files between two commits; the actual unified diff per file is generated client-side from file content at each commit. See [diff-approach.md](diff-approach.md).

---

## Cloud Storage Structure

```
gs://bucket/
└── reviews/
    └── 2026/
        └── 01/
            └── 01/
                ├── pr-12345-143022-review.md
                ├── pr-12346-150511-review.md
                └── ...
```

**Path format:** `reviews/{yyyy}/{mm}/{dd}/pr-{pr_id}-{HHmmss}-review.md`

---

## Code Structure

### Key Components

| Component | Description |
|-----------|-------------|
| `ReviewResult` | Dataclass holding all review results (severity, actions, paths) |
| `process_pr_review()` | Shared core logic for PR review processing |
| `review_pr()` | HTTP entry point (synchronous, returns JSON) |
| `review_pr_pubsub()` | Pub/Sub entry point (async, with idempotency) |
| `receive_webhook()` | Webhook receiver (queues to Pub/Sub) |
| `AzureDevOpsClient` | Client for Azure DevOps REST API |

### Files Modified/Created

| File | Change |
|------|--------|
| `main.py` | Cloud Function with shared `process_pr_review()` logic |
| `requirements.txt` | Added `functions-framework`, `google-cloud-storage`, `google-cloud-pubsub` |
| `.gcloudignore` | Created (excludes .env, docs, test files) |
| `README.md` | Updated with deployment commands |

---

## Deployment Commands

### 0. Enable Required GCP APIs

```bash
# Enable all required APIs for the Cloud Function
gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com

# Verify APIs are enabled
gcloud services list --enabled --filter="name:(cloudfunctions OR cloudbuild OR run OR artifactregistry OR storage OR secretmanager OR aiplatform)"
```

| API | Purpose |
|-----|---------|
| `cloudfunctions.googleapis.com` | Cloud Functions deployment and management |
| `cloudbuild.googleapis.com` | Building the function container |
| `run.googleapis.com` | Gen2 functions run on Cloud Run |
| `artifactregistry.googleapis.com` | Storing function container images |
| `storage.googleapis.com` | Cloud Storage for review files |
| `secretmanager.googleapis.com` | Secure storage of PAT and API key |
| `aiplatform.googleapis.com` | Vertex AI / Gemini API access |

### 1. Create Secrets

```bash
echo -n "your-azure-pat" | gcloud secrets create azure-devops-pat --data-file=-
echo -n "your-api-key" | gcloud secrets create pr-review-api-key --data-file=-
```

### 2. Create Bucket

```bash
gcloud storage buckets create gs://YOUR_BUCKET_NAME --location=us-central1
```

### 3. Deploy Function

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
  --set-env-vars="GCS_BUCKET=...,AZURE_DEVOPS_ORG=...,AZURE_DEVOPS_PROJECT=...,AZURE_DEVOPS_REPO=...,VERTEX_PROJECT=...,VERTEX_LOCATION=us-central1" \
  --set-secrets="AZURE_DEVOPS_PAT=azure-devops-pat:latest,API_KEY=pr-review-api-key:latest"
```

---

## Security Considerations

1. **API Key Authentication** — All requests must include valid `X-API-Key` header
2. **Secrets in Secret Manager** — PAT and API key stored securely, not in env vars
3. **No secrets in logs** — Function does not log sensitive values
4. **HTTPS only** — Cloud Functions enforce TLS

---

## Future Enhancements

- [x] ~~Webhook trigger from Azure DevOps~~ - Implemented via `receive_webhook()`
- [x] ~~Review result caching~~ - Implemented via idempotency markers (commit SHA)
- [x] ~~Centralized review logic~~ - Implemented via `process_pr_review()` shared function
- [ ] Filter files by extension (only review .js, .css, .html, .htl)
- [ ] Configurable severity thresholds
- [ ] Rate limiting / quota management

