#!/bin/bash
# =============================================================================
# Cloud Function Deployment Script
# =============================================================================
# This script deploys the pr-regression-review Cloud Function to Google Cloud.
# It reuses existing environment variables and secrets configured during initial setup.
#
# Usage:
#   ./deploy.sh
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - GCP project configured (gcloud config set project YOUR_PROJECT_ID)
#   - Initial setup completed (secrets, storage bucket, APIs enabled)
#
# The script will:
#   1. Verify gcloud authentication and project configuration
#   2. Deploy the Cloud Function with existing settings
#   3. Display the deployment status and function URL
# =============================================================================

set -e  # Exit immediately if a command exits with a non-zero status

# Resolve repo root (works regardless of where the script is called from)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function configuration
FUNCTION_NAME="pr-regression-review"
REGION="us-central1"
RUNTIME="python312"
ENTRY_POINT="review_pr"
MEMORY="512Mi"
CPU="1"
TIMEOUT="900s"
MAX_INSTANCES="60"
CONCURRENCY="80"

# =============================================================================
# Helper Functions
# =============================================================================

print_header() {
    echo -e "\n${BLUE}===================================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}===================================================================${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

# =============================================================================
# Preflight Checks
# =============================================================================

print_header "Cloud Function Deployment - Preflight Checks"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    print_error "gcloud CLI is not installed"
    echo "Please install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi
print_success "gcloud CLI is installed"

# Check if user is authenticated
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q .; then
    print_error "No active gcloud authentication found"
    echo "Please run: gcloud auth login"
    exit 1
fi
ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" | head -n 1)
print_success "Authenticated as: $ACTIVE_ACCOUNT"

# Check if project is configured
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
if [ -z "$PROJECT_ID" ]; then
    print_error "No GCP project configured"
    echo "Please run: gcloud config set project YOUR_PROJECT_ID"
    exit 1
fi
print_success "Project configured: $PROJECT_ID"

# Check if required files exist
if [ ! -f "main.py" ]; then
    print_error "main.py not found in repository root ($REPO_ROOT)"
    exit 1
fi
print_success "main.py found"

if [ ! -f "requirements.txt" ]; then
    print_error "requirements.txt not found in repository root ($REPO_ROOT)"
    exit 1
fi
print_success "requirements.txt found"

if [ ! -d "pr_review" ]; then
    print_error "pr_review/ package not found in repository root ($REPO_ROOT)"
    exit 1
fi
print_success "pr_review/ package found"

# =============================================================================
# Deployment
# =============================================================================

print_header "Deploying Cloud Function: $FUNCTION_NAME"

print_info "Function configuration:"
echo "  Name: $FUNCTION_NAME"
echo "  Region: $REGION"
echo "  Runtime: $RUNTIME"
echo "  Entry Point: $ENTRY_POINT"
echo "  Memory: $MEMORY"
echo "  CPU: $CPU"
echo "  Timeout: $TIMEOUT"
echo "  Max Instances: $MAX_INSTANCES"
echo "  Concurrency: $CONCURRENCY"
echo ""

print_info "Starting deployment..."
echo ""

# Deploy the function (reuses existing env vars and secrets)
gcloud functions deploy "$FUNCTION_NAME" \
  --gen2 \
  --runtime="$RUNTIME" \
  --region="$REGION" \
  --source=. \
  --entry-point="$ENTRY_POINT" \
  --memory="$MEMORY" \
  --cpu="$CPU" \
  --timeout="$TIMEOUT" \
  --max-instances="$MAX_INSTANCES" \
  --concurrency="$CONCURRENCY" \
  --quiet

# Check if deployment was successful
if [ $? -eq 0 ]; then
    print_success "Deployment completed successfully!"

    # Get function URL
    print_header "Deployment Information"

    FUNCTION_URL=$(gcloud functions describe "$FUNCTION_NAME" \
        --region="$REGION" \
        --format="value(serviceConfig.uri)" 2>/dev/null)

    if [ -n "$FUNCTION_URL" ]; then
        echo -e "${GREEN}Function URL:${NC}"
        echo "  $FUNCTION_URL"
        echo ""
    fi

    print_info "View function details:"
    echo "  gcloud functions describe $FUNCTION_NAME --region=$REGION"
    echo ""

    print_info "View function logs:"
    echo "  gcloud functions logs read $FUNCTION_NAME --region=$REGION --limit=50"
    echo ""

    print_success "Deployment complete!"
else
    print_error "Deployment failed"
    echo ""
    echo "Check the error messages above for details."
    echo "Common issues:"
    echo "  - Required APIs not enabled"
    echo "  - Insufficient permissions"
    echo "  - Invalid function code or dependencies"
    exit 1
fi
