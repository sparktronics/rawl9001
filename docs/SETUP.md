# Quick Setup Guide

## Environment Variables Required

The script needs these environment variables to run:

| Variable | Description | Example |
|----------|-------------|---------|
| `AZURE_DEVOPS_PAT` | Personal Access Token | `abc123...` |
| `AZURE_DEVOPS_ORG` | Organization name | `mycompany` |
| `AZURE_DEVOPS_PROJECT` | Project name | `MyProject` |
| `AZURE_DEVOPS_REPO` | Repository name | `my-repo` |
| `VERTEX_PROJECT` | GCP Project ID | `rawl-extractor` |
| `VERTEX_LOCATION` | GCP Region | `europe-west1` |

## Setup Options

### Option 1: Using .env File (Recommended for Development)

This is the most convenient method as the variables persist across terminal sessions:

1. **Copy the example file:**
   ```bash
   cp env.example .env
   ```

2. **Edit `.env` with your actual values:**
   ```bash
   nano .env   # or use your favorite editor
   ```

3. **Fill in your values in the `.env` file:**
   ```
   AZURE_DEVOPS_PAT=abc123your-actual-token
   AZURE_DEVOPS_ORG=mycompany
   AZURE_DEVOPS_PROJECT=MyProject
   AZURE_DEVOPS_REPO=my-repo
   VERTEX_PROJECT=rawl-extractor
   VERTEX_LOCATION=europe-west1
   ```

4. **Run the script** (it will automatically load `.env`):
   ```bash
   python3 main.py 357462
   ```

**Note:** The `.env` file is automatically ignored by git (listed in `.gitignore`) so your secrets stay safe!

### Option 2: Interactive Setup

```bash
source setup-env.sh
```

This will prompt you for each value and set them in your current terminal session.
**Note:** Variables set this way only last for the current terminal session.

### Option 3: Manual Export

**For current session only:**
```bash
export AZURE_DEVOPS_PAT="your-token"
export AZURE_DEVOPS_ORG="your-org"
export AZURE_DEVOPS_PROJECT="your-project"
export AZURE_DEVOPS_REPO="your-repo"
export VERTEX_PROJECT="rawl-extractor"
export VERTEX_LOCATION="europe-west1"
```

### Option 4: Use Template File

1. Copy the template:
   ```bash
   cp env-template.sh my-env.sh
   ```

2. Edit `my-env.sh` with your actual values

3. Source it:
   ```bash
   source my-env.sh
   ```

4. **Important**: Don't commit `my-env.sh` to git (it contains secrets!)

## Getting Your Azure DevOps PAT

1. Go to: `https://dev.azure.com/{your-org}/_usersSettings/tokens`
2. Click **"+ New Token"**
3. Give it a name: `"PR Review Script"`
4. Set expiration as needed
5. Select **Scopes**:
   - ✅ **Code** → Read
   - ✅ **Pull Request Thread** → Read
6. Click **Create**
7. **Copy the token immediately** (you won't see it again!)

## Verify Setup

Run this to check if all variables are set:

```bash
python3 -c "
import os
required = ['AZURE_DEVOPS_PAT', 'AZURE_DEVOPS_ORG', 'AZURE_DEVOPS_PROJECT', 
            'AZURE_DEVOPS_REPO', 'VERTEX_PROJECT', 'VERTEX_LOCATION']
missing = [var for var in required if not os.getenv(var)]
if missing:
    print(f'❌ Missing: {", ".join(missing)}')
else:
    print('✅ All environment variables are set!')
"
```

## Run the Script

Once environment variables are set:

```bash
python3 main.py 357462
```

Replace `357462` with your actual PR ID.

---

## Cloud Function Authentication

**Note:** When deploying to GCP as Cloud Functions, the functions use **IAM authentication** instead of API keys.

### For Deployed Functions

Deployed Cloud Functions require:
1. **IAM Permission:** Caller must have `roles/run.invoker` role
2. **Identity Token:** Valid Google-signed JWT in `Authorization: Bearer` header### Obtaining Identity Tokens

**For testing with your user account:**
```bash
# Get your identity token
gcloud auth print-identity-token

# Call function with authentication
curl -X POST https://FUNCTION_URL \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"pr_id": 12345}'
```

**For service accounts (CI/CD pipelines):**
```bash
# Authenticate as service account
gcloud auth activate-service-account --key-file=path/to/key.json

# Get identity token for the service account
gcloud auth print-identity-token

# Use in API calls
TOKEN=$(gcloud auth print-identity-token)
curl -H "Authorization: Bearer $TOKEN" ...
```

### Local Development

When testing locally with Functions Framework, IAM authentication is **not enforced**. You can call the function directly without authentication headers.

See [AUTHENTICATION.md](./AUTHENTICATION.md) for comprehensive authentication setup and troubleshooting.
