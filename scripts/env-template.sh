# Environment Variables Template for RAWL 9001 PR Review
# Copy these and fill in your values, then export them

export AZURE_DEVOPS_PAT="your-personal-access-token-here"
export AZURE_DEVOPS_ORG="your-organization-name"
export AZURE_DEVOPS_PROJECT="your-project-name"
export AZURE_DEVOPS_REPO="your-repository-name"
export VERTEX_PROJECT="rawl-extractor"
export VERTEX_LOCATION="europe-west1"

# How to get Azure DevOps PAT:
# 1. Go to https://dev.azure.com/{your-org}/_usersSettings/tokens
# 2. Click "New Token"
# 3. Give it a name (e.g., "PR Review Script")
# 4. Select scopes:
#    - Code (Read)
#    - Pull Request Thread (Read)
# 5. Click Create and copy the token

# How to find your values:
# - ORG: From URL https://dev.azure.com/{ORG}/
# - PROJECT: Your project name in Azure DevOps
# - REPO: Your repository name or ID

