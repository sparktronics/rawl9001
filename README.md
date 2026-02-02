# RAWL 9001 POC - PR Regression Review System

This is RAWL 9001 - the Review Agent With Learning. "I'm sorry, Dave. I can't let you merge that."

A Cloud Function that fetches Pull Requests from Azure DevOps, sends them to Gemini (Vertex AI) for regression-focused review of AEM frontend components, and automatically comments or rejects PRs based on severity.

## Features

- Fetches PR metadata and full file contents from Azure DevOps
- Sends both "before" and "after" versions to Gemini for comparison
- Generates a regression-focused review targeting AEM/HTL/JS/CSS
- Stores reviews in Cloud Storage with date partitioning (`yyyy/mm/dd`)
- **Auto-comments** on PRs with blocking or warning findings
- **Auto-rejects** PRs with blocking severity issues

## Severity Actions

| Severity | PR Comment | PR Rejection | Storage |
|----------|------------|--------------|---------|
| blocking | ✅ | ✅ | ✅ |
| warning | ✅ | ❌ | ✅ |
| info | ❌ | ❌ | ✅ |

## Build & Deploy

### Prerequisites

- GCP project created
- `gcloud` CLI installed
- Azure DevOps PAT with required permissions (see below)

### Step 1: Authenticate with GCP

```bash
# Login to GCP
gcloud auth login

# Set your project
gcloud config set project YOUR_PROJECT_ID

# Verify
gcloud config get-value project
```

### Step 2: Enable Required APIs

```bash
gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com
```

### Step 3: Create Secrets

```bash
# Create Azure DevOps PAT secret
echo -n "your-azure-pat" | gcloud secrets create azure-devops-pat --data-file=-

# Grant Cloud Functions access to Azure PAT secret
# Note: Replace with your actual service account email
gcloud secrets add-iam-policy-binding azure-devops-pat \
  --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Step 3b: Set Up IAM Authentication

The functions use GCP IAM for authentication instead of API keys. Create service accounts for authorized callers:

**Option A: Using Google Cloud Console (Web UI)**

For detailed step-by-step instructions with visual navigation, see:
📖 [**CONSOLE_IAM_SETUP.md**](./CONSOLE_IAM_SETUP.md)

**Option B: Using gcloud CLI**

```bash
# Create service account for authorized callers (e.g., CI/CD pipelines)
gcloud iam service-accounts create pr-review-caller \
  --display-name="PR Review Function Caller"

# Grant the service account permission to invoke Cloud Functions
# (This will be done automatically via Terraform, or manually after deployment)

# For testing, grant yourself permission
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="user:YOUR_EMAIL@example.com" \
  --role="roles/run.invoker" \
  --condition=None
```

**Option C: Using Terraform (Recommended)**

See the [Terraform deployment section](#terraform-deployment) below.

### Step 4: Create Storage Bucket

```bash
gcloud storage buckets create gs://YOUR_BUCKET_NAME --location=us-central1
```

### Step 5: Create Pub/Sub Topic

```bash
# Create the topic for PR review triggers
gcloud pubsub topics create pr-review-trigger

# Verify
gcloud pubsub topics list | grep pr-review
```

### Step 6: Deploy Cloud Functions

#### Option A: HTTP Function (Synchronous)

For direct HTTP calls (testing, manual triggers). **Requires IAM authentication.**

```bash
gcloud functions deploy pr-regression-review \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=review_pr \
  --trigger-http \
  --no-allow-unauthenticated \
  --memory=512MB \
  --timeout=300s \
  --set-env-vars="GCS_BUCKET=rawl9001,AZURE_DEVOPS_ORG=batdigital,AZURE_DEVOPS_PROJECT=Consumer%20Platforms,AZURE_DEVOPS_REPO=AEM-Platform-Core,VERTEX_LOCATION=global,VERTEX_PROJECT=cog01k6msqf1e7e5z9m5grb69qmrm,GEMINI_MODEL=gemini-3-pro-preview" \
  --set-secrets="AZURE_DEVOPS_PAT=azure-devops-pat:latest"

# After deployment, grant invoker permission to authorized service accounts
gcloud functions add-iam-policy-binding pr-regression-review \
  --region=us-central1 \
  --member="serviceAccount:pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

#### Option B: Pub/Sub Function (Asynchronous - Recommended for Production)

For async processing via Pub/Sub (with idempotency and retry handling). **No IAM authentication needed - triggered internally by Pub/Sub.**

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
  --set-env-vars="GCS_BUCKET=rawl9001bat,AZURE_DEVOPS_ORG=batdigital,AZURE_DEVOPS_PROJECT=Consumer%20Platforms,AZURE_DEVOPS_REPO=AEM-Platform-Core,VERTEX_LOCATION=global,VERTEX_PROJECT=cog01k6msqf1e7e5z9m5grb69qmrm" \
  --set-secrets="AZURE_DEVOPS_PAT=azure-devops-pat:latest"
```

#### Option C: Webhook Receiver (For Azure DevOps Pipeline Integration)

Receives webhooks and publishes to Pub/Sub for async processing. **Requires IAM authentication.**

```bash
gcloud functions deploy pr-review-webhook \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=receive_webhook \
  --trigger-http \
  --no-allow-unauthenticated \
  --memory=256MB \
  --timeout=30s \
  --set-env-vars="PUBSUB_TOPIC=pr-review-trigger"

# After deployment, grant invoker permission
gcloud functions add-iam-policy-binding pr-review-webhook \
  --region=us-central1 \
  --member="serviceAccount:pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

### Step 6b: API Gateway (Optional)

To expose the HTTP review function through API Gateway (e.g. for API key auth, custom domain, or rate limiting), create the API and gateway after the API has been created in the project.

**Prerequisites:** Enable the API Gateway API (`gcloud services enable apigateway.googleapis.com`). Create the API resource if needed: `gcloud api-gateway apis create pr-review-api --project=YOUR_PROJECT_ID`. The HTTP function `pr-regression-review` must be deployed; a service account with `roles/run.invoker` on that function is used for backend auth.

**1. Configure the API** (create an API config from the OpenAPI spec):

```bash
gcloud api-gateway api-configs create pr-review-config \
  --api=pr-review-api \
  --openapi-spec=api/api-spec.yaml \
  --backend-auth-service-account=YOUR_BACKEND_SA@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

Replace `YOUR_BACKEND_SA` with the service account that API Gateway will use to call the Cloud Function (e.g. `id-pr-review-caller` or `pr-review-caller`), and `YOUR_PROJECT_ID` with your GCP project ID. That service account must have `roles/run.invoker` on the `pr-regression-review` function.

**2. Deploy the API Gateway** (create the gateway using that config):

```bash
gcloud api-gateway gateways create pr-review-gateway \
  --api=pr-review-api \
  --api-config=pr-review-config \
  --location=us-central1
```

After creation, use the gateway URL (shown in the output or via `gcloud api-gateway gateways describe pr-review-gateway --location=us-central1`) to send requests to `/review` instead of the Cloud Function URL directly.

**Updating the API spec:** `api-configs update` cannot change the OpenAPI spec (only display name and labels). To roll out a new spec:

1. Create a new API config (use a new ID, e.g. `pr-review-config-v2`):
   ```bash
   gcloud api-gateway api-configs create pr-review-config-v2 \
     --api=pr-review-api \
     --openapi-spec=api/api-spec.yaml \
     --backend-auth-service-account=YOUR_BACKEND_SA@YOUR_PROJECT_ID.iam.gserviceaccount.com
   ```
2. Point the gateway at the new config:
   ```bash
   gcloud api-gateway gateways update pr-review-gateway \
     --api=pr-review-api \
     --api-config=pr-review-config-v2 \
     --location=us-central1
   ```

### Step 7: Verify Deployment

#### Verify HTTP Function

```bash
# Get the function URL
gcloud functions describe pr-regression-review --region=us-central1 --format="value(serviceConfig.uri)"

# Test the function with IAM authentication
curl -X POST "$(gcloud functions describe pr-regression-review --region=us-central1 --format='value(serviceConfig.uri)')" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}'
```

#### Verify Pub/Sub Function

```bash
# Publish a test message
gcloud pubsub topics publish pr-review-trigger \
  --project=rawl-extractor \
  --message='{"pr_id": 12345, "commit_sha": "abc123def456789", "source": "manual-test"}'

# Check function logs
gcloud functions logs read pr-review-pubsub --region=us-central1 --limit=50
```

#### Verify Webhook Function

```bash
# Get webhook URL
WEBHOOK_URL=$(gcloud functions describe pr-review-webhook --region=us-central1 --format='value(serviceConfig.uri)')

# Test webhook with IAM authentication
curl -X POST "$WEBHOOK_URL" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345, "commit_sha": "abc123def456789"}'
```

### Redeploying After Changes

After modifying the cloud function code (e.g., `main.py`, `requirements.txt`), you can quickly redeploy using the deployment script:

#### Option 1: Using the Deployment Script (Recommended)

The `deploy.sh` script automates the deployment process with built-in validation:

```bash
./deploy.sh
```

The script will:
- ✅ Verify gcloud authentication and project configuration
- ✅ Check that required files exist (main.py, requirements.txt)
- ✅ Deploy the function with existing environment variables and secrets
- ✅ Display the function URL and useful commands

**Prerequisites:**
- gcloud CLI installed and authenticated (`gcloud auth login`)
- GCP project configured (`gcloud config set project YOUR_PROJECT_ID`)
- Initial setup completed (secrets, storage bucket, APIs enabled - see steps 1-4 above)

**Make the script executable (first time only):**
```bash
chmod +x deploy.sh
```

#### Option 2: Manual Deployment

If you prefer to deploy manually or need to override specific settings:

```bash
# HTTP function
gcloud functions deploy pr-regression-review \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=review_pr

# Pub/Sub function
gcloud functions deploy pr-review-pubsub \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=review_pr_pubsub

# Webhook function
gcloud functions deploy pr-review-webhook \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=receive_webhook
```

> **Note:** Environment variables and secrets persist between deployments unless explicitly changed. The deployment script reuses all existing configuration automatically.

## Usage

### HTTP Request

```bash
# Obtain identity token and call function
curl -X POST https://REGION-PROJECT_ID.cloudfunctions.net/pr-regression-review \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}'
```

**Authentication Note:** Functions require GCP IAM authentication. You must:
1. Have the `roles/run.invoker` permission
2. Include a valid Google-signed identity token in the `Authorization: Bearer` header
3. See [AUTHENTICATION.md](./AUTHENTICATION.md) for detailed authentication setup

### Response

```json
{
  "pr_id": 12345,
  "title": "PR title here",
  "files_changed": 5,
  "max_severity": "blocking",
  "has_blocking": true,
  "has_warning": false,
  "action_taken": "rejected",
  "commented": true,
  "storage_path": "gs://bucket/reviews/2026/01/01/pr-12345-143022-review.md",
  "review_preview": "First 500 chars..."
}
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GCS_BUCKET` | Yes | Cloud Storage bucket for reviews |
| `AZURE_DEVOPS_PAT` | Yes | Azure DevOps Personal Access Token |
| `AZURE_DEVOPS_ORG` | Yes | Azure DevOps organization name |
| `AZURE_DEVOPS_PROJECT` | Yes | Azure DevOps project name |
| `AZURE_DEVOPS_REPO` | Yes | Repository name or ID |
| `VERTEX_PROJECT` | Yes | GCP project ID for Vertex AI |
| `VERTEX_LOCATION` | No | GCP region (default: `us-central1`) |
| `GEMINI_MODEL` | No | Gemini model to use (default: `gemini-2.5-pro`) |
| `DLQ_SUBSCRIPTION` | No | Dead Letter Queue subscription name (default: `pr-review-dlq-sub`) |
| `SYSTEM_PROMPT_BLOB_PATH` | No | GCS path to system prompt file (default: `prompts/system-prompt.txt`) |
| `FILTER_NON_CODE_FILES` | No | Filter out non-code files (.md, .sh, images) from review (default: `true`) |
| `EXTENSIVE_PR_FILE_THRESHOLD` | No | File count threshold for extensive PR detection (default: `20`) |
| `EXTENSIVE_PR_SIZE_THRESHOLD` | No | Character count threshold for extensive PR detection (default: `500000`) |

## File Filtering

The system automatically filters out non-code files from PR reviews to focus on code changes that may cause regressions.

### Filtered File Types

The following file types are excluded from review by default:

- **Markdown files**: `.md`
- **Shell scripts**: `.sh`
- **Image files**: `.jpg`, `.jpeg`, `.png`, `.gif`, `.svg`, `.bmp`, `.webp`, `.ico`, `.tiff`, `.tif`

### Configuration

File filtering can be controlled via the `FILTER_NON_CODE_FILES` environment variable:

- `FILTER_NON_CODE_FILES=true` (default): Non-code files are filtered out
- `FILTER_NON_CODE_FILES=false`: All files are included in the review

### Extensive PR Handling

For large PRs, the system automatically limits the number of files reviewed to prevent token limit issues:

- **File count threshold**: When a PR exceeds `EXTENSIVE_PR_FILE_THRESHOLD` files (default: 20), only the first N files are reviewed
- **Size threshold**: When total file content exceeds `EXTENSIVE_PR_SIZE_THRESHOLD` characters (default: 500,000), the PR is treated as extensive

When files are limited, a notice is added to the PR comment indicating that a partial review was performed.

## System Prompt Configuration

The system prompt can be stored externally in GCS, allowing you to update the review instructions without redeploying the function.

### Initial Upload

The system prompt file is available at `prompts/system-prompt.txt`. Upload it to GCS:

```bash
# Upload to GCS (replace with your bucket name)
gsutil cp prompts/system-prompt.txt gs://${GCS_BUCKET}/prompts/system-prompt.txt

# Or with explicit bucket name
gsutil cp prompts/system-prompt.txt gs://rawl9001/prompts/system-prompt.txt

# Verify upload
gsutil cat gs://${GCS_BUCKET}/prompts/system-prompt.txt | head -20
```

### Updating the Prompt

To update the prompt without redeploying:

```bash
# Edit the file locally
nano prompts/system-prompt.txt

# Upload the updated version
gsutil cp prompts/system-prompt.txt gs://${GCS_BUCKET}/prompts/system-prompt.txt

# Changes take effect immediately on the next request
```

### Fallback Behavior

If the GCS fetch fails (file missing, permissions issue, etc.), the function falls back to the embedded default prompt and logs a warning. This ensures the service remains available even if the external prompt is unavailable.

## Azure DevOps PAT Permissions

Your PAT needs:
- **Code (Read)** - To fetch file contents
- **Pull Request Threads (Read & Write)** - To post comments
- **Pull Request (Read & Write)** - To fetch PR metadata and vote/reject

## Review Focus Areas

The Gemini prompt detects:

| Risk Type | Examples |
|-----------|----------|
| Dialog Elimination | Removed AEM dialogs, restructured author interfaces |
| Function Removal | Deleted public JS functions other components may call |
| Behavior Changes | Modified logic affecting existing features |
| API Stability | Changed data-attributes, CSS classes, JS interfaces |
| HTL Contract Changes | Modified Sling Model properties, template parameters |
| CSS Breaking Changes | Renamed/removed classes, changed specificity |

## Local Development

### Running Locally with Functions Framework

The Cloud Functions Framework allows you to run and debug your function locally before deploying.

#### 1. Install Dependencies

```bash
pip3 install -r requirements.txt
```

#### 2. Set Environment Variables

Option A: Use a `.env` file (recommended for development):

```bash
# Copy the example and fill in your values
cp env.example .env
# Edit .env with your actual credentials
```

Option B: Export directly in your shell:

```bash
export GCS_BUCKET="your-bucket"
export AZURE_DEVOPS_PAT="your-pat"
export AZURE_DEVOPS_ORG="your-org"
export AZURE_DEVOPS_PROJECT="your-project"
export AZURE_DEVOPS_REPO="your-repo"
export VERTEX_PROJECT="your-gcp-project"
export VERTEX_LOCATION="us-central1"
```

#### 3. Start the Local Server

```bash
# Run with Python module execution (most reliable)
python3 -m functions_framework --target=review_pr --debug --port=8080

# Or if functions-framework is in your PATH
source .env
functions-framework --target=review_pr --debug --port=8080
```

The server will start on `http://localhost:8080` with:
- ✅ Debug mode enabled (auto-reload on file changes)
- ✅ Detailed logging
- ✅ Flask debugger active

#### 4. Test the Function

```bash
# When testing locally, IAM authentication is not enforced by the functions framework
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}'
```

**Note:** Local testing with Functions Framework does not enforce IAM authentication. In production (deployed to GCP), IAM authentication is required.

### Debugging with Cursor/VS Code

A `.vscode/launch.json` configuration is included for debugging:

1. Open **Run and Debug** panel (⌘+Shift+D / Ctrl+Shift+D)
2. Select **"Debug Cloud Function (Local)"**
3. Press **F5** to start debugging
4. Set breakpoints in `main.py`
5. Send a request with curl
6. Debug interactively!

The debugger configuration automatically:
- Loads environment variables from `.env`
- Attaches to the local server
- Allows stepping through code and inspecting variables

### Tips

- **Auto-reload**: With `--debug`, the server restarts when you edit files
- **Logging**: Check the console for detailed request/response logs
- **Network access**: Use `http://0.0.0.0:8080` to test from other devices on your network
- **Stop server**: Press `Ctrl+C` in the terminal

## Dead Letter Queue (DLQ) Management

### Overview

For production deployments, configure a Dead Letter Queue to capture failed messages after retry attempts are exhausted. This prevents infinite retry loops and allows manual inspection of failures.

### DLQ Configuration

### Step 1: Create Dead Letter Topic and Subscription

```bash
# Create dead letter topic
gcloud pubsub topics create pr-review-dlq

# Create subscription to inspect failed messages
gcloud pubsub subscriptions create pr-review-dlq-sub \
  --topic=pr-review-dlq \
  --ack-deadline=60
```

### Step 2: Configure Main Subscription with DLQ

```bash
# Update your existing subscription to use the DLQ
gcloud pubsub subscriptions update pr-review-sub \
  --dead-letter-topic=pr-review-dlq \
  --max-delivery-attempts=5
```

### Step 3: Grant Required Permissions

The Pub/Sub service account needs permissions to publish to the DLQ:

```bash
# Get your project number
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format="value(projectNumber)")

# Grant publisher role on DLQ topic
gcloud pubsub topics add-iam-policy-binding pr-review-dlq \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role="roles/pubsub.publisher"

# Grant subscriber role on main subscription (for acknowledgment)
gcloud pubsub subscriptions add-iam-policy-binding pr-review-sub \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role="roles/pubsub.subscriber"
```

### How It Works

1. **Retryable errors** (500s, timeouts): Pub/Sub retries up to `max-delivery-attempts` (5)
2. **Non-retryable errors** (401, 403, 404): Function acknowledges immediately; message goes to DLQ after max attempts
3. **Failed messages** include metadata: `CloudPubSubDeadLetterSourceDeliveryCount`, `CloudPubSubDeadLetterSourceSubscription`

### Monitoring Failed Messages

```bash
# Pull messages from DLQ for inspection
gcloud pubsub subscriptions pull pr-review-dlq-sub --limit=10 --auto-ack

# View without acknowledging
gcloud pubsub subscriptions pull pr-review-dlq-sub --limit=10
```

### Retry Behavior Summary

| Error Type | HTTP Code | Retries | Final Destination |
|------------|-----------|---------|-------------------|
| Auth failure | 401, 403 | 0 (immediate fail) | DLQ |
| Not found | 404 | 0 (immediate fail) | DLQ |
| Server error | 500, 502, 503 | Up to 5 | DLQ if all fail |
| Timeout | - | Up to 5 | DLQ if all fail |
| Gemini error | - | Up to 3 (app-level) | Marked failed in GCS |

### Processing Dead Letter Queue Messages

After fixing issues that caused messages to be sent to the DLQ (e.g., renewing an expired PAT), you can reprocess those messages using the DLQ processing function.

#### Deploy the DLQ Processing Function

```bash
gcloud functions deploy process-dead-letter-queue \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=process_dead_letter_queue \
  --trigger-http \
  --allow-unauthenticated \
  --memory=256MB \
  --timeout=540s \
  --no-allow-unauthenticated \
  --set-env-vars="GCS_BUCKET=rawl9001,AZURE_DEVOPS_ORG=batdigital,AZURE_DEVOPS_PROJECT=Consumer%20Platforms,AZURE_DEVOPS_REPO=AEM-Platform-Core,VERTEX_PROJECT=rawl-extractor,VERTEX_LOCATION=us-central1,PUBSUB_TOPIC=pr-review-trigger,DLQ_SUBSCRIPTION=pr-review-dlq-sub" \
  --set-secrets="AZURE_DEVOPS_PAT=azure-devops-pat:latest"

# Grant invoker permission
gcloud functions add-iam-policy-binding process-dead-letter-queue \
  --region=us-central1 \
  --member="serviceAccount:pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

#### Using the DLQ Processing Function

The function validates credentials before processing and provides detailed reporting.

**Dry Run (Preview what would be reprocessed):**
```bash
# Dry run with IAM authentication
curl -X POST "$(gcloud functions describe process-dead-letter-queue --region=us-central1 --format='value(serviceConfig.uri)')" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"max_messages": 10, "dry_run": true}'
```

**Process Messages:**
```bash
curl -X POST "$(gcloud functions describe process-dead-letter-queue --region=us-central1 --format='value(serviceConfig.uri)')" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"max_messages": 100}'
```

**Response:**
```json
{
  "status": "completed",
  "messages_pulled": 5,
  "messages_republished": 5,
  "messages_failed": 0,
  "dry_run": false,
  "details": [
    {
      "pr_id": 12345,
      "commit_sha": "abc123de",
      "status": "republished",
      "new_message_id": "987654321"
    }
  ]
}
```

#### How It Works

1. **Validates Credentials**: Tests Azure DevOps PAT before processing to ensure it's working
2. **Pulls Messages**: Retrieves messages from the DLQ subscription (up to `max_messages`)
3. **Resets Idempotency**: Deletes idempotency markers to allow reprocessing
4. **Republishes**: Sends messages back to the main `pr-review-trigger` topic for processing
5. **Acknowledges**: Removes successfully processed messages from the DLQ

#### Best Practices

- Always run with `dry_run: true` first to preview what will be reprocessed
- Process in batches (e.g., 10-100 messages at a time) for large DLQs
- Monitor the main processing function logs after reprocessing to verify success
- If messages continue to fail, investigate root cause before reprocessing more

## Limitations

- Large PRs with many files may hit Gemini token limits
- Binary files are skipped automatically
- Timeout set to 300s (5 min) — very large PRs may need adjustment
