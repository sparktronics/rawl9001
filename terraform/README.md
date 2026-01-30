# Terraform Deployment for RAWL 9001

Infrastructure as Code for deploying the PR Regression Review System to Google Cloud Platform.

## Prerequisites

1. **Terraform CLI** (v1.5.0+)
   ```bash
   # macOS
   brew install terraform
   
   # Or download from https://terraform.io/downloads
   ```

2. **Google Cloud SDK**
   ```bash
   brew install google-cloud-sdk
   ```

3. **GCP Project** with billing enabled

4. **Authenticated gcloud CLI**
   ```bash
   gcloud auth login
   gcloud auth application-default login
   ```

## Quick Start

### Step 1: Create Secrets (Manual - Before Terraform)

Secrets must be created manually before running Terraform:

```bash
# Set your project
export PROJECT_ID="your-project-id"
gcloud config set project $PROJECT_ID

# Enable Secret Manager API first
gcloud services enable secretmanager.googleapis.com

# Create Azure DevOps PAT secret
echo -n "your-azure-devops-pat" | \
  gcloud secrets create azure-devops-pat --data-file=-

# Create API key secret
echo -n "your-api-key-value" | \
  gcloud secrets create pr-review-api-key --data-file=-
```

### Step 2: Configure Variables

```bash
cd terraform/
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
```

### Step 3: Initialize and Deploy

```bash
# Initialize Terraform
terraform init

# Preview changes
terraform plan

# Apply changes
terraform apply
```

### Step 4: Upload System Prompt (Optional)

```bash
# Get bucket name from Terraform output
BUCKET=$(terraform output -raw gcs_bucket_name)

# Upload system prompt
gsutil cp ../prompts/system-prompt.txt gs://${BUCKET}/prompts/system-prompt.txt
```

## What Gets Created

| Resource | Name | Purpose |
|----------|------|---------|
| **GCS Bucket** | `{gcs_bucket_name}` | Store PR reviews |
| **Pub/Sub Topic** | `pr-review-trigger` | Async PR review triggers |
| **Pub/Sub Topic** | `pr-review-dlq` | Dead letter queue |
| **Pub/Sub Subscription** | `pr-review-dlq-sub` | DLQ message inspection |
| **Service Account** | `pr-review-functions` | Function identity |
| **Cloud Function** | `pr-regression-review` | HTTP sync review |
| **Cloud Function** | `pr-review-pubsub` | Pub/Sub async review |
| **Cloud Function** | `pr-review-webhook` | Webhook receiver |
| **Cloud Function** | `process-dead-letter-queue` | DLQ processor |

## What You Create Manually

| Resource | Why Manual |
|----------|------------|
| **GCP Project** | Organization policies may apply |
| **Billing Account** | Requires org-level permissions |
| **Secret: azure-devops-pat** | Contains sensitive credential |
| **Secret: pr-review-api-key** | Contains sensitive credential |

## Files

| File | Purpose |
|------|---------|
| `main.tf` | Provider config, API enablement |
| `variables.tf` | Input variable definitions |
| `storage.tf` | GCS bucket configuration |
| `pubsub.tf` | Pub/Sub topics and subscriptions |
| `iam.tf` | Service account and IAM bindings |
| `functions.tf` | Cloud Functions (Gen2) |
| `outputs.tf` | Output values and test commands |
| `terraform.tfvars.example` | Example variable values |

## Common Operations

### View Outputs

```bash
# All outputs
terraform output

# Specific output
terraform output function_url_http

# Test commands
terraform output test_commands
```

### Update Functions After Code Changes

```bash
# Re-apply to deploy new code
terraform apply
```

### Destroy Everything

```bash
# Preview what will be destroyed
terraform plan -destroy

# Destroy (requires confirmation)
terraform destroy
```

> **Warning:** This will delete the GCS bucket and all PR reviews stored in it.

### Import Existing Resources

If you already have resources created manually:

```bash
# Import existing bucket
terraform import google_storage_bucket.reviews your-bucket-name

# Import existing Pub/Sub topic
terraform import google_pubsub_topic.pr_review_trigger projects/your-project/topics/pr-review-trigger
```

## Troubleshooting

### "Secret not found" Error

Ensure secrets are created before running Terraform:

```bash
gcloud secrets list --project=$PROJECT_ID
```

### API Not Enabled Error

Terraform enables APIs automatically, but it may take a few minutes. Re-run:

```bash
terraform apply
```

### Permission Denied

Ensure you have the required IAM roles:
- `roles/owner` or
- `roles/editor` + `roles/secretmanager.admin` + `roles/iam.serviceAccountAdmin`

### Function Deployment Fails

Check Cloud Build logs:

```bash
gcloud builds list --limit=5
gcloud builds log BUILD_ID
```

## Security Notes

1. **terraform.tfvars** - Contains project-specific config, don't commit
2. **terraform.tfstate** - Contains sensitive data, store securely or use remote backend
3. **Secrets** - Managed outside Terraform intentionally for security
4. **IAM** - Functions use a dedicated service account with minimal permissions
