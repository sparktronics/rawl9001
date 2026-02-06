# Authentication Guide

## Overview

The PR Review Cloud Functions use **GCP IAM (Identity and Access Management)** for authentication instead of custom API keys. This provides enterprise-grade security with automatic token rotation, fine-grained access control, and comprehensive audit trails.

## Authentication Method

**Type:** OAuth 2.0 / OpenID Connect (OIDC)  
**Token:** Google-signed JWT (Identity Token)  
**Header:** `Authorization: Bearer <identity-token>`  
**Enforcement:** Cloud Run (before function code executes)

### How It Works

```
┌─────────┐                  ┌──────────────┐                  ┌──────────────┐
│ Caller  │                  │  Cloud Run   │                  │   Function   │
│         │                  │  (IAM Check) │                  │     Code     │
└────┬────┘                  └──────┬───────┘                  └──────┬───────┘
     │                              │                                 │
     │ 1. HTTP Request              │                                 │
     │    + Identity Token          │                                 │
     ├─────────────────────────────>│                                 │
     │                              │                                 │
     │                              │ 2. Validate Token               │
     │                              │    Check Signature              │
     │                              │    Check Expiration             │
     │                              │    Check Audience               │
     │                              │                                 │
     │                              │ 3. Check IAM Policy             │
     │                              │    Does caller have             │
     │                              │    roles/run.invoker?           │
     │                              │                                 │
     │                              │ 4. Forward Request              │
     │                              ├────────────────────────────────>│
     │                              │                                 │
     │                              │ 5. Function Executes            │
     │                              │<────────────────────────────────┤
     │                              │                                 │
     │ 6. Response                  │                                 │
     │<─────────────────────────────┤                                 │
     │                              │                                 │
```

**Key Points:**
- Authentication happens **before** your function code runs
- Invalid or missing tokens are rejected by Cloud Run (return 401/403)
- Your function code only needs to handle business logic

---

## Setup Guide

> **Prefer the Google Cloud Console (Web UI)?** See [CONSOLE_IAM_SETUP.md](./CONSOLE_IAM_SETUP.md) for detailed step-by-step instructions with screenshots and visual navigation.

### 1. Service Account Creation

Create service accounts for authorized systems/users:

**Using gcloud CLI:**
```bash
# For CI/CD pipelines and automated systems
gcloud iam service-accounts create pr-review-caller \
  --display-name="PR Review Function Caller" \
  --description="Service account for invoking PR review functions"

# For testing and debugging
gcloud iam service-accounts create pr-review-tester \
  --display-name="PR Review Function Tester" \
  --description="Service account for testing PR review functions"
```

**Using Google Cloud Console:**
See [CONSOLE_IAM_SETUP.md](./CONSOLE_IAM_SETUP.md#1-create-service-account) for step-by-step instructions.

### 2. Grant IAM Permissions

**Option A: Using Google Cloud Console (Web UI) - Easiest**

See [CONSOLE_IAM_SETUP.md](./CONSOLE_IAM_SETUP.md#2-grant-function-invoker-permission) for detailed step-by-step instructions with visual navigation.

**Option B: Using gcloud CLI (Command Line)**

```bash
# Grant permission to invoke a specific function
gcloud functions add-iam-policy-binding pr-regression-review \
  --region=us-central1 \
  --member="serviceAccount:pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# Grant to a user (for testing)
gcloud functions add-iam-policy-binding pr-regression-review \
  --region=us-central1 \
  --member="user:your-email@example.com" \
  --role="roles/run.invoker"
```

**Option C: Using Terraform (Recommended for Infrastructure as Code)**

The terraform configuration in this repository automatically creates service accounts and IAM bindings. See `terraform/iam.tf`:

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

### 3. Obtain Identity Tokens

Identity tokens are short-lived JWTs that prove your identity.

#### For Your User Account

```bash
# Get identity token for yourself
TOKEN=$(gcloud auth print-identity-token)

# View token details (optional)
echo $TOKEN | cut -d '.' -f 2 | base64 -d | jq .

# Use in API call
curl -X POST https://FUNCTION_URL \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}'
```

#### For Service Accounts

**Method 1: Using gcloud (if you have permission to impersonate)**

```bash
# Impersonate service account and get token
TOKEN=$(gcloud auth print-identity-token \
  --impersonate-service-account=pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --audiences=https://FUNCTION_URL)

# Use token
curl -H "Authorization: Bearer $TOKEN" ...
```

**Method 2: Using Service Account Key (for CI/CD)**

```bash
# Download service account key
gcloud iam service-accounts keys create ~/pr-review-caller-key.json \
  --iam-account=pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com

# Authenticate with key
gcloud auth activate-service-account \
  --key-file=~/pr-review-caller-key.json

# Get identity token
TOKEN=$(gcloud auth print-identity-token \
  --audiences=https://FUNCTION_URL)

# Use token
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" ...
```

**Method 3: Using Workload Identity (for GKE/Cloud Run)**

If calling from another GCP service:

```python
import google.auth.transport.requests
import google.oauth2.id_token

def get_identity_token(target_audience):
    """Get identity token using default credentials."""
    request = google.auth.transport.requests.Request()
    token = google.oauth2.id_token.fetch_id_token(request, target_audience)
    return token

# Usage
function_url = "https://us-central1-PROJECT_ID.cloudfunctions.net/pr-regression-review"
token = get_identity_token(function_url)

# Make request
import requests
response = requests.post(
    function_url,
    headers={"Authorization": f"Bearer {token}"},
    json={"pr_id": 12345}
)
```

---

## Integration Examples

### Azure DevOps Pipeline

Add this to your `azure-pipelines.yml`:

```yaml
steps:
  - task: DownloadSecureFile@1
    name: serviceAccountKey
    inputs:
      secureFile: 'pr-review-caller-key.json'

  - script: |
      # Install gcloud SDK
      curl https://sdk.cloud.google.com | bash
      exec -l $SHELL
      gcloud --version
    displayName: 'Install gcloud'

  - script: |
      # Authenticate
      gcloud auth activate-service-account \
        --key-file=$(serviceAccountKey.secureFilePath)
      
      # Get function URL
      FUNCTION_URL="https://us-central1-YOUR_PROJECT.cloudfunctions.net/pr-review-webhook"
      
      # Get identity token
      TOKEN=$(gcloud auth print-identity-token --audiences=$FUNCTION_URL)
      
      # Call webhook
      curl -X POST $FUNCTION_URL \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "pr_id": $(System.PullRequest.PullRequestId),
          "commit_sha": "$(Build.SourceVersion)"
        }'
    displayName: 'Trigger PR Review'
```

### GitHub Actions

```yaml
name: PR Review

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@v1
        with:
          credentials_json: '${{ secrets.GCP_SA_KEY }}'

      - name: Set up Cloud SDK
        uses: google-github-actions/setup-gcloud@v1

      - name: Trigger PR Review
        run: |
          FUNCTION_URL="https://us-central1-PROJECT.cloudfunctions.net/pr-review-webhook"
          TOKEN=$(gcloud auth print-identity-token --audiences=$FUNCTION_URL)
          
          curl -X POST $FUNCTION_URL \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "{
              \"pr_id\": ${{ github.event.pull_request.number }},
              \"commit_sha\": \"${{ github.event.pull_request.head.sha }}\"
            }"
```

### Python Script

```python
#!/usr/bin/env python3
"""
Script to call PR review function with IAM authentication.
"""

import json
import subprocess
import requests

def get_identity_token():
    """Get identity token using gcloud."""
    result = subprocess.run(
        ["gcloud", "auth", "print-identity-token"],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()

def review_pr(pr_id: int, function_url: str):
    """Trigger PR review."""
    token = get_identity_token()
    
    response = requests.post(
        function_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json={"pr_id": pr_id},
        timeout=300
    )
    
    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    import sys
    pr_id = int(sys.argv[1])
    function_url = "https://us-central1-PROJECT.cloudfunctions.net/pr-regression-review"
    
    result = review_pr(pr_id, function_url)
    print(json.dumps(result, indent=2))
```

### cURL Examples

**Test with your user credentials:**
```bash
curl -X POST https://REGION-PROJECT.cloudfunctions.net/pr-regression-review \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}'
```

**Test with service account:**
```bash
# Set function URL
FUNCTION_URL="https://us-central1-PROJECT.cloudfunctions.net/pr-regression-review"

# Get token with specific audience
TOKEN=$(gcloud auth print-identity-token \
  --impersonate-service-account=pr-review-caller@PROJECT.iam.gserviceaccount.com \
  --audiences=$FUNCTION_URL)

# Call function
curl -X POST $FUNCTION_URL \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}'
```

---

## Troubleshooting

### Error: 401 Unauthorized

**Symptoms:**
```json
{
  "error": {
    "code": 401,
    "message": "Unauthorized",
    "status": "UNAUTHENTICATED"
  }
}
```

**Common Causes:**
1. **Missing or invalid token**
   - Solution: Ensure you're sending `Authorization: Bearer <token>` header
   - Check: `echo $TOKEN` to verify token exists

2. **Expired token**
   - Identity tokens expire after 1 hour
   - Solution: Generate a new token

3. **Wrong audience**
   - Token audience must match the function URL
   - Solution: Use `--audiences` flag when generating token

**Fix:**
```bash
# Regenerate token with correct audience
FUNCTION_URL="https://us-central1-PROJECT.cloudfunctions.net/pr-regression-review"
TOKEN=$(gcloud auth print-identity-token --audiences=$FUNCTION_URL)

# Test
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}' $FUNCTION_URL
```

### Error: 403 Forbidden

**Symptoms:**
```json
{
  "error": {
    "code": 403,
    "message": "Forbidden",
    "status": "PERMISSION_DENIED"
  }
}
```

**Cause:** Your account/service account doesn't have `roles/run.invoker` permission.

**Fix:**
```bash
# Check current IAM policy
gcloud functions get-iam-policy pr-regression-review --region=us-central1

# Add your account
gcloud functions add-iam-policy-binding pr-regression-review \
  --region=us-central1 \
  --member="user:your-email@example.com" \
  --role="roles/run.invoker"

# Verify
gcloud functions get-iam-policy pr-regression-review --region=us-central1 | grep your-email
```

### Error: Invalid Token Format

**Symptoms:**
- Token doesn't start with `eyJ`
- Token is very short (< 100 characters)
- Decoding token fails

**Cause:** Not using an identity token (might be an access token).

**Fix:**
```bash
# Use print-identity-token (NOT print-access-token)
gcloud auth print-identity-token  # ✅ Correct

# NOT this:
gcloud auth print-access-token    # ❌ Wrong
```

### Local Testing Without Authentication

When using Functions Framework locally, IAM is not enforced:

```bash
# Start function locally
functions-framework --target=review_pr --debug

# Call without authentication
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}'
```

**Note:** This only works locally. Deployed functions always require authentication.

---

## Security Best Practices

### 1. Service Account Keys

⚠️ **Minimize Service Account Key Usage**

- Keys are long-lived credentials that can be stolen
- Prefer Workload Identity or short-lived tokens
- If you must use keys:
  - Store in secret manager (Azure Key Vault, GitHub Secrets, etc.)
  - Rotate regularly (every 90 days)
  - Limit permissions (principle of least privilege)
  - Never commit to version control

### 2. IAM Permissions

✅ **Grant Minimal Permissions**

```bash
# Good: Grant only to specific functions
gcloud functions add-iam-policy-binding FUNCTION_NAME \
  --role="roles/run.invoker" \
  --member="serviceAccount:SA@PROJECT.iam.gserviceaccount.com"

# Bad: Grant project-wide
gcloud projects add-iam-policy-binding PROJECT_ID \
  --role="roles/run.invoker" \
  --member="serviceAccount:SA@PROJECT.iam.gserviceaccount.com"
```

### 3. Token Audience

✅ **Always Specify Audience**

```bash
# Good: Specific audience
gcloud auth print-identity-token --audiences=https://FUNCTION_URL

# Bad: No audience (token works anywhere)
gcloud auth print-identity-token
```

### 4. Token Expiration

Identity tokens expire after **1 hour**. For long-running processes:

```python
import time
from datetime import datetime, timedelta

class TokenManager:
    def __init__(self):
        self.token = None
        self.expires_at = None
    
    def get_token(self):
        if not self.token or datetime.now() >= self.expires_at:
            self.refresh_token()
        return self.token
    
    def refresh_token(self):
        # Get new token
        self.token = subprocess.run(
            ["gcloud", "auth", "print-identity-token"],
            capture_output=True, text=True, check=True
        ).stdout.strip()
        
        # Set expiration (55 minutes to be safe)
        self.expires_at = datetime.now() + timedelta(minutes=55)
```

---

## Comparison: API Keys vs IAM Authentication

| Feature | API Keys (Old) | IAM Authentication (Current) |
|---------|---------------|------------------------------|
| **Token Type** | Static string | JWT (signed, expiring) |
| **Rotation** | Manual | Automatic |
| **Expiration** | Never (unless manually rotated) | 1 hour |
| **Revocation** | Update secret everywhere | Instant (revoke IAM permission) |
| **Granularity** | All-or-nothing | Per-function, per-user |
| **Audit Trail** | Application logs only | Cloud Logging (all requests) |
| **Compliance** | Manual management | Built-in (SOC 2, ISO 27001) |
| **Cost** | Secret Manager storage | Free |
| **Key Management** | Required | Not required |

---

## Reference Links

- [Cloud Functions IAM](https://cloud.google.com/functions/docs/securing/managing-access-iam)
- [Cloud Run Authentication](https://cloud.google.com/run/docs/authenticating/overview)
- [Service Accounts Best Practices](https://cloud.google.com/iam/docs/best-practices-service-accounts)
- [Identity Tokens](https://cloud.google.com/docs/authentication/get-id-token)
- [Workload Identity](https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity)

---

## Quick Reference

### Get Identity Token
```bash
gcloud auth print-identity-token
```

### Call Function
```bash
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}' \
  https://FUNCTION_URL
```

### Grant Permission
```bash
gcloud functions add-iam-policy-binding FUNCTION_NAME \
  --region=REGION \
  --member="user:EMAIL" \
  --role="roles/run.invoker"
```

### Check Permissions
```bash
gcloud functions get-iam-policy FUNCTION_NAME --region=REGION
```

### Debug Token
```bash
# View token claims
gcloud auth print-identity-token | cut -d '.' -f 2 | base64 -d | jq .
```
