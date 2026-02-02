# IAM Authentication Implementation Plan

## Overview
Migrate from custom API key authentication to GCP native IAM authentication for Cloud Functions. This provides enterprise-grade security, eliminates custom secret management, and leverages GCP's identity infrastructure.

## Current State

### Authentication Flow
- **Method:** Custom API key validation
- **Header:** `X-API-Key: <secret>`
- **Validation:** Application-level comparison against `API_KEY` environment variable
- **Access Control:** `allUsers` can invoke (public endpoint with API key)

### Files Affected
1. `main.py` - Contains API key validation logic (3 functions)
2. `terraform/functions.tf` - Grants `allUsers` invoker role
3. `terraform/iam.tf` - May need new service accounts
4. `test_main.py` - Tests API key validation
5. `README.md` - Documents API key usage
6. `SETUP.md` - Setup instructions for API keys

---

## Target State

### Authentication Flow
- **Method:** GCP IAM with signed identity tokens (JWT)
- **Header:** `Authorization: Bearer <google-signed-jwt>`
- **Validation:** Automatic by Cloud Run (before code execution)
- **Access Control:** Specific service accounts/users granted `roles/run.invoker`

### Security Benefits
✅ Automatic token rotation (no manual key management)
✅ Fine-grained IAM policies
✅ Audit trails via Cloud Logging
✅ Industry-standard JWT authentication
✅ Zero additional infrastructure cost
✅ Supports workload identity federation

---

## Implementation Steps

### Phase 1: Terraform Infrastructure Changes

#### 1.1 Create Service Accounts (`terraform/iam.tf`)
```terraform
# Service account for authorized callers (e.g., CI/CD pipelines, other services)
resource "google_service_account" "function_caller" {
  account_id   = "pr-review-caller"
  display_name = "PR Review Function Caller"
  description  = "Service account authorized to invoke PR review functions"
}

# Optional: Service account for human users (testing/debugging)
resource "google_service_account" "function_tester" {
  account_id   = "pr-review-tester"
  display_name = "PR Review Function Tester"
  description  = "Service account for testing PR review functions"
}
```

#### 1.2 Update Function IAM Bindings (`terraform/functions.tf`)

**Changes for `pr_regression_review` (HTTP function):**
- **REMOVE:** Lines 137-146 (allUsers invoker binding)
- **ADD:** Specific service account invoker bindings

**Changes for `pr_review_webhook` (HTTP webhook):**
- **REMOVE:** Lines 278-287 (allUsers invoker binding)
- **ADD:** Specific service account invoker bindings

**Changes for `process_dlq` (HTTP DLQ processor):**
- **REMOVE:** Lines 349-358 (allUsers invoker binding)
- **ADD:** Specific service account invoker bindings

**KEEP UNCHANGED:** `pr_review_pubsub` (Pub/Sub trigger - internal only)

#### 1.3 Add IAM Policy Bindings (`terraform/iam.tf`)
```terraform
# Grant function_caller permission to invoke HTTP functions
resource "google_cloud_run_service_iam_member" "pr_review_caller_invoker" {
  for_each = toset([
    google_cloudfunctions2_function.pr_regression_review.name,
    google_cloudfunctions2_function.pr_review_webhook.name,
    google_cloudfunctions2_function.process_dlq.name,
  ])
  
  location = var.region
  project  = var.project_id
  service  = each.value
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.function_caller.email}"
}
```

#### 1.4 Update Variables (`terraform/variables.tf`)
```terraform
variable "authorized_users" {
  description = "List of users authorized to invoke functions (for testing)"
  type        = list(string)
  default     = []
}
```

---

### Phase 2: Application Code Changes

#### 2.1 Update `main.py`

**Function: `review_pr` (lines 1143-1265)**
- **REMOVE:** Lines 1167-1172 (API key validation)
- **REMOVE:** `API_KEY` from required config (line 96)
- **UPDATE:** Docstring to document IAM authentication

**Function: `receive_webhook` (lines 1733-1827)**
- **REMOVE:** Lines 1750-1758 (API key validation)
- **UPDATE:** Docstring to document IAM authentication

**Function: `process_dead_letter_queue` (lines 1666-1731)**
- **REMOVE:** Lines similar to API key validation (if present)
- **UPDATE:** Docstring to document IAM authentication

**Function: `load_config` (lines 89-114)**
- **REMOVE:** `API_KEY` from required variables list (line 96)
- **UPDATE:** Related documentation

**Constants Section (lines 78-83)**
- No changes needed (API_KEY was never a constant)

#### 2.2 Update Configuration Loading
```python
def load_config() -> tuple[dict, list]:
    """Load configuration from environment variables.
    
    Returns:
        tuple: (config dict, list of missing required vars)
    """
    required = [
        # "API_KEY",  # REMOVED - Using GCP IAM authentication
        "GCS_BUCKET",
        "AZURE_DEVOPS_PAT",
        "AZURE_DEVOPS_ORG", 
        "AZURE_DEVOPS_PROJECT",
        "AZURE_DEVOPS_REPO",
        "VERTEX_PROJECT",
    ]
```

---

### Phase 3: Test Updates

#### 3.1 Update `test_main.py`

**Remove/Update Tests:**
- `test_review_pr_missing_api_key()` - Remove (no longer applicable)
- `test_review_pr_invalid_api_key()` - Remove (no longer applicable)
- `test_receive_webhook_missing_api_key()` - Remove (no longer applicable)
- `test_receive_webhook_invalid_api_key()` - Remove (no longer applicable)

**Update Fixture:**
```python
@pytest.fixture
def mock_env_vars():
    """Mock environment variables for testing."""
    return {
        # "API_KEY": "test-api-key-12345",  # REMOVED
        "GCS_BUCKET": "test-bucket",
        "AZURE_DEVOPS_PAT": "test-pat",
        # ... rest of vars
    }
```

**Update Test Requests:**
```python
# OLD: headers={"X-API-Key": "test-api-key-12345"}
# NEW: No headers needed (IAM validated before function)
```

**Add New Tests:**
```python
def test_review_pr_iam_authenticated():
    """Test that review_pr works without API key (IAM handles auth)."""
    # Test that function executes when IAM allows access
    
def test_config_loads_without_api_key():
    """Test that config loads successfully without API_KEY."""
```

---

### Phase 4: Documentation Updates

#### 4.1 Update `README.md`

**Section: Authentication**
- Replace API key documentation with IAM authentication
- Add examples for obtaining identity tokens
- Document service account setup

**Section: Deployment**
- Update deployment instructions
- Add IAM role assignment steps

**Section: Testing**
- Update test commands with identity token usage

#### 4.2 Update `SETUP.md`

**Remove:**
- API key generation steps
- Secret manager setup for API keys

**Add:**
- Service account creation
- IAM role assignment
- Identity token generation for testing

#### 4.3 Create `AUTHENTICATION.md`

New comprehensive guide covering:
- IAM authentication overview
- Service account setup
- Obtaining identity tokens
- Integration with CI/CD pipelines
- Troubleshooting authentication issues

---

### Phase 5: Deployment Scripts

#### 5.1 Update `deploy.sh`
- No changes needed (uses Terraform/existing config)

#### 5.2 Update `setup-env.sh`
- Remove API key generation
- Add service account creation (optional)

---

## Migration Strategy

### For Existing Deployments

**Step 1: Create Service Accounts**
```bash
cd terraform
terraform plan -target=google_service_account.function_caller
terraform apply -target=google_service_account.function_caller
```

**Step 2: Grant Permissions (Dual Mode)**
```bash
# Keep allUsers temporarily, add service accounts
terraform apply
```

**Step 3: Test with New Authentication**
```bash
# Test with identity token
TOKEN=$(gcloud auth print-identity-token \
  --impersonate-service-account=pr-review-caller@PROJECT_ID.iam.gserviceaccount.com)

curl -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}' \
  https://FUNCTION_URL
```

**Step 4: Remove allUsers Access**
```bash
# Update terraform to remove allUsers
terraform apply
```

**Step 5: Update Clients**
- Update Azure DevOps pipelines
- Update any scripts/tools
- Update documentation

---

## Rollback Plan

If issues arise:

**Step 1: Revert Terraform Changes**
```bash
git revert <commit-hash>
terraform apply
```

**Step 2: Restore API Key Validation**
```bash
git checkout feature/filter-non-coding-files -- main.py
```

**Step 3: Redeploy**
```bash
./deploy.sh
```

---

## Testing Checklist

### Infrastructure
- [ ] Service accounts created successfully
- [ ] IAM bindings applied correctly
- [ ] Functions deployed without errors
- [ ] No unauthorized access (test without token)
- [ ] Authorized access works (test with valid token)

### Application
- [ ] Config loads without API_KEY
- [ ] Functions execute successfully with IAM auth
- [ ] All three HTTP functions protected (review_pr, receive_webhook, process_dlq)
- [ ] Pub/Sub function unaffected
- [ ] Logs show successful authentication

### Integration
- [ ] Azure DevOps pipeline updated and tested
- [ ] Manual curl requests work with identity tokens
- [ ] Service-to-service calls work
- [ ] Error handling works (invalid/missing tokens)

### Tests
- [ ] All unit tests pass
- [ ] Removed API key tests
- [ ] Added IAM-specific tests
- [ ] No test fixtures reference API_KEY

### Documentation
- [ ] README.md updated
- [ ] SETUP.md updated
- [ ] AUTHENTICATION.md created
- [ ] Code comments updated
- [ ] Terraform comments updated

---

## Timeline

**Estimated Duration:** 2-3 hours

1. **Terraform Changes:** 30 minutes
2. **Code Updates:** 30 minutes
3. **Test Updates:** 45 minutes
4. **Documentation:** 45 minutes
5. **Testing & Validation:** 30 minutes

---

## Success Criteria

✅ All HTTP functions require IAM authentication
✅ No custom API key validation in code
✅ Service accounts can invoke functions
✅ Unauthorized requests are rejected by Cloud Run (before code execution)
✅ All tests pass
✅ Documentation is complete and accurate
✅ Zero downtime migration path available

---

## References

- [Cloud Functions IAM](https://cloud.google.com/functions/docs/securing/managing-access-iam)
- [Cloud Run Authentication](https://cloud.google.com/run/docs/authenticating/overview)
- [Service Accounts](https://cloud.google.com/iam/docs/service-accounts)
- [Identity Tokens](https://cloud.google.com/docs/authentication/get-id-token)
