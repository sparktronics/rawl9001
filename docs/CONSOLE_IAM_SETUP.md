# Google Cloud Console: IAM Setup Guide

Complete step-by-step instructions for granting service accounts permission to invoke PR Review Cloud Functions using the Google Cloud Console web interface.

---

## Table of Contents

1. [Create Service Account](#1-create-service-account)
2. [Grant Function Invoker Permission](#2-grant-function-invoker-permission)
3. [Verify Permissions](#3-verify-permissions)
4. [Create Service Account Key (Optional)](#4-create-service-account-key-optional)
5. [Test the Setup](#5-test-the-setup)

---

## 1. Create Service Account

### Step 1.1: Navigate to Service Accounts

1. **Open Google Cloud Console**
   - Go to: https://console.cloud.google.com
   - Make sure you're in the correct project (check top navbar)

2. **Navigate to IAM & Admin > Service Accounts**
   - Click the hamburger menu (☰) in the top-left
   - Scroll down to **"IAM & Admin"**
   - Click **"Service Accounts"**
   
   Direct link: `https://console.cloud.google.com/iam-admin/serviceaccounts`

### Step 1.2: Create New Service Account

1. **Click "CREATE SERVICE ACCOUNT"** (blue button at the top)

2. **Fill in Service Account Details:**
   
   **Service account name:**
   ```
   pr-review-caller
   ```
   
   **Service account ID:** (auto-generated)
   ```
   pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com
   ```
   
   **Service account description:**
   ```
   Service account for invoking PR review Cloud Functions from CI/CD pipelines
   ```

3. **Click "CREATE AND CONTINUE"**

### Step 1.3: Grant Service Account Permissions (Optional)

This step is optional - you can skip it by clicking "CONTINUE" then "DONE".

> **Note:** We'll grant function-specific permissions later. Project-level permissions are not needed.

4. **Click "CONTINUE"** (skip this step)

5. **Click "DONE"** on the "Grant users access" screen

**✅ Result:** You now have a service account created!

---

## 2. Grant Function Invoker Permission

Now we need to give the service account permission to invoke specific Cloud Functions.

### Step 2.1: Navigate to Cloud Functions

1. **Go to Cloud Functions**
   - Click hamburger menu (☰)
   - Scroll to **"Cloud Functions"** under "Serverless"
   - Click **"Cloud Functions"**
   
   Direct link: `https://console.cloud.google.com/functions/list`

2. **Wait for functions to load**
   - You should see your deployed functions in the list
   - Look for: `pr-regression-review`, `pr-review-webhook`, `process-dead-letter-queue`

### Step 2.2: Open Function Permissions

1. **Click on the function name** (e.g., `pr-regression-review`)
   - This opens the function details page

2. **Click the "PERMISSIONS" tab** at the top
   - It's next to "DETAILS", "SOURCE", "TESTING", etc.

### Step 2.3: Add IAM Policy Binding

1. **Click "GRANT ACCESS"** (blue button on the right side)
   - A side panel will open: "Add principals"

2. **Fill in the form:**

   **New principals:**
   ```
   pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com
   ```
   
   > **Tip:** Start typing "pr-review-caller" and it should autocomplete
   
   **Select a role:**
   - Click the "Select a role" dropdown
   - Type "Cloud Run Invoker" in the filter box
   - Select: **"Cloud Run Invoker"**
     - Full path: `Cloud Run > Cloud Run Invoker`
     - Role ID: `roles/run.invoker`

3. **Click "SAVE"**

**✅ Result:** The service account now appears in the Permissions list!

### Step 2.4: Repeat for Other Functions

Repeat Steps 2.2-2.3 for each HTTP function:

- ✅ `pr-regression-review` (synchronous PR review)
- ✅ `pr-review-webhook` (webhook receiver)  
- ✅ `process-dead-letter-queue` (DLQ processor)

> **Note:** You don't need to grant permissions for `pr-review-pubsub` - it's triggered by Pub/Sub internally, not HTTP.

---

## 3. Verify Permissions

### Step 3.1: Check Function Permissions

1. **Navigate to one of your functions**
   - Cloud Functions > Click function name
   - Go to **"PERMISSIONS"** tab

2. **Verify the service account is listed:**
   ```
   pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com
   Role: Cloud Run Invoker
   ```

3. **You should see:**
   - Principal (service account email)
   - Role: Cloud Run Invoker
   - Inherited: No (directly granted)

### Step 3.2: Alternative Verification via IAM Page

1. **Go to IAM & Admin > IAM**
   
   Direct link: `https://console.cloud.google.com/iam-admin/iam`

2. **Filter by service account:**
   - Use the filter box at the top
   - Type: `pr-review-caller`

3. **Click on the service account row**
   - It may show "No roles granted at project level" - this is fine!
   - We granted function-specific permissions, not project-level

4. **To see function-specific permissions:**
   - Click "View by RESOURCES" tab (instead of "View by PRINCIPALS")
   - Find your Cloud Run service (functions run on Cloud Run)
   - Expand to see the specific permissions

---

## 4. Create Service Account Key (Optional)

Only needed if you want to use the service account from outside GCP (e.g., Azure DevOps pipelines).

### Step 4.1: Navigate to Service Account

1. **Go to IAM & Admin > Service Accounts**
   
   Direct link: `https://console.cloud.google.com/iam-admin/serviceaccounts`

2. **Find and click your service account:**
   ```
   pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com
   ```

### Step 4.2: Create Key

1. **Click "KEYS" tab** at the top

2. **Click "ADD KEY" dropdown**
   - Select **"Create new key"**

3. **Select key type:**
   - Choose **"JSON"** (recommended)
   - Click **"CREATE"**

4. **Save the key file:**
   - A `.json` file will download automatically
   - **⚠️ IMPORTANT:** This is the only time you'll see this key!
   - Store it securely (Azure Key Vault, GitHub Secrets, etc.)
   - **NEVER commit it to git!**

**Key file location:**
```
~/Downloads/YOUR_PROJECT_ID-abc123def456.json
```

### Step 4.3: Secure the Key

**For Azure DevOps:**
1. Go to: Pipelines > Library > Secure files
2. Upload the JSON key file
3. Reference it in your pipeline

**For GitHub Actions:**
1. Go to: Settings > Secrets and variables > Actions
2. Create new secret: `GCP_SA_KEY`
3. Paste the entire JSON file contents

---

## 5. Test the Setup

### Step 5.1: Test from Cloud Shell

1. **Open Cloud Shell** (icon in top-right of Console)
   - Or go to: https://shell.cloud.google.com

2. **Get the function URL:**
   ```bash
   gcloud functions describe pr-regression-review \
     --region=us-central1 \
     --format='value(serviceConfig.uri)'
   ```

3. **Test with service account impersonation:**
   ```bash
   FUNCTION_URL=$(gcloud functions describe pr-regression-review \
     --region=us-central1 \
     --format='value(serviceConfig.uri)')
   
   # Get identity token as the service account
   TOKEN=$(gcloud auth print-identity-token \
     --impersonate-service-account=pr-review-caller@YOUR_PROJECT_ID.iam.gserviceaccount.com \
     --audiences=$FUNCTION_URL)
   
   # Test the function
   curl -X POST $FUNCTION_URL \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"pr_id": 12345}'
   ```

4. **Expected result:**
   - If successful: You'll get a JSON response with PR review results
   - If 403 Forbidden: Permissions not set correctly - repeat Step 2
   - If 401 Unauthorized: Token issue - regenerate token

### Step 5.2: Test from Local Machine (if you created a key)

1. **Authenticate with the service account key:**
   ```bash
   gcloud auth activate-service-account \
     --key-file=path/to/downloaded-key.json
   ```

2. **Get identity token:**
   ```bash
   FUNCTION_URL="https://us-central1-YOUR_PROJECT_ID.cloudfunctions.net/pr-regression-review"
   TOKEN=$(gcloud auth print-identity-token --audiences=$FUNCTION_URL)
   ```

3. **Call the function:**
   ```bash
   curl -X POST $FUNCTION_URL \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"pr_id": 12345}'
   ```

---

## Quick Reference: Required Roles

| Function Name | Service Account | Role Required |
|---------------|-----------------|---------------|
| `pr-regression-review` | `pr-review-caller` | `roles/run.invoker` |
| `pr-review-webhook` | `pr-review-caller` | `roles/run.invoker` |
| `process-dead-letter-queue` | `pr-review-caller` | `roles/run.invoker` |
| `pr-review-pubsub` | N/A | Internal (Pub/Sub triggers) |

---

## Troubleshooting

### Issue: "Permission Denied" when trying to grant access

**Cause:** You don't have permission to modify IAM policies.

**Solution:** Ask your GCP admin to grant you one of these roles:
- `roles/iam.serviceAccountAdmin` (for service account management)
- `roles/run.admin` (for Cloud Run/Functions IAM)
- `roles/owner` or `roles/editor` (project-level)

### Issue: Service account not showing in autocomplete

**Cause:** Service account may not exist or wrong project selected.

**Solution:**
1. Verify project in top navbar
2. Go to IAM & Admin > Service Accounts
3. Confirm service account exists
4. Copy the full email address manually

### Issue: "Error 403: Caller does not have permission" when testing

**Cause:** IAM bindings not propagated yet or incorrect.

**Solution:**
1. Wait 1-2 minutes for IAM changes to propagate
2. Verify permissions in Console (Step 3)
3. Try generating a new identity token
4. Check you're using the correct function URL

### Issue: Function shows "allUsers" in permissions

**Cause:** Function still has public access enabled.

**Solution:**
1. Go to function > Permissions tab
2. Find "allUsers" principal
3. Click the trash icon to remove it
4. Re-add only your service account

---

## Security Best Practices

### ✅ Do's

- ✅ Use service accounts for programmatic access
- ✅ Grant least privilege (only `roles/run.invoker`)
- ✅ Use function-specific permissions (not project-level)
- ✅ Rotate service account keys every 90 days
- ✅ Store keys in secret managers (never in code)
- ✅ Use Workload Identity when possible (GKE, Cloud Build)
- ✅ Monitor service account usage in Cloud Logging

### ❌ Don'ts

- ❌ Don't grant `roles/owner` or `roles/editor`
- ❌ Don't use `allUsers` or `allAuthenticatedUsers`
- ❌ Don't commit service account keys to git
- ❌ Don't share keys via email or chat
- ❌ Don't create keys unless absolutely necessary
- ❌ Don't grant project-level permissions when function-level works

---

## Visual Console Navigation

### Path to Service Accounts:
```
☰ Menu > IAM & Admin > Service Accounts
```

### Path to Cloud Functions:
```
☰ Menu > Cloud Functions
```

### Path to Function Permissions:
```
☰ Menu > Cloud Functions > [Click Function Name] > Permissions Tab > Grant Access
```

### Path to IAM Policies:
```
☰ Menu > IAM & Admin > IAM
```

---

## Additional Resources

- **GCP Console:** https://console.cloud.google.com
- **Service Accounts:** https://console.cloud.google.com/iam-admin/serviceaccounts
- **Cloud Functions:** https://console.cloud.google.com/functions/list
- **IAM Documentation:** https://cloud.google.com/iam/docs
- **Cloud Run IAM:** https://cloud.google.com/run/docs/securing/managing-access

---

## Summary Checklist

- [ ] Created service account `pr-review-caller`
- [ ] Granted `roles/run.invoker` to `pr-regression-review`
- [ ] Granted `roles/run.invoker` to `pr-review-webhook`
- [ ] Granted `roles/run.invoker` to `process-dead-letter-queue`
- [ ] Verified permissions in Console
- [ ] (Optional) Created and secured service account key
- [ ] Tested function invocation with service account
- [ ] Updated CI/CD pipeline to use service account authentication

---

**Need help?** See [AUTHENTICATION.md](./AUTHENTICATION.md) for programmatic usage examples and troubleshooting.
