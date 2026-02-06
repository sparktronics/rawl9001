#!/bin/bash
# Setup script for environment variables
# Usage: source setup-env.sh

echo "🔧 Setting up environment variables for RAWL 9001 PR Review"
echo "=========================================="
echo ""

# Prompt for Azure DevOps values
read -p "Enter Azure DevOps PAT (Personal Access Token): " AZURE_DEVOPS_PAT
read -p "Enter Azure DevOps Organization: " AZURE_DEVOPS_ORG
read -p "Enter Azure DevOps Project: " AZURE_DEVOPS_PROJECT
read -p "Enter Azure DevOps Repository: " AZURE_DEVOPS_REPO

# Prompt for GCP values
read -p "Enter GCP Project ID [rawl-extractor]: " VERTEX_PROJECT
VERTEX_PROJECT=${VERTEX_PROJECT:-rawl-extractor}

read -p "Enter GCP Location [europe-west1]: " VERTEX_LOCATION
VERTEX_LOCATION=${VERTEX_LOCATION:-europe-west1}

# Export the variables
export AZURE_DEVOPS_PAT
export AZURE_DEVOPS_ORG
export AZURE_DEVOPS_PROJECT
export AZURE_DEVOPS_REPO
export VERTEX_PROJECT
export VERTEX_LOCATION

echo ""
echo "✅ Environment variables set!"
echo ""
echo "These variables are now available in your current shell session."
echo "To make them permanent, add them to your ~/.zshrc or ~/.bashrc"
echo ""
echo "You can now run:"
echo "  python3 main.py <PR_ID>"

