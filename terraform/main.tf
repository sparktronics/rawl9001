# =============================================================================
# RAWL 9001 - PR Regression Review System
# Terraform Configuration for Google Cloud Platform
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

# -----------------------------------------------------------------------------
# Provider Configuration
# -----------------------------------------------------------------------------

provider "google" {
  project = var.project_id
  region  = var.region
}

# -----------------------------------------------------------------------------
# Enable Required APIs
# -----------------------------------------------------------------------------

resource "google_project_service" "apis" {
  for_each = toset([
    "cloudfunctions.googleapis.com",
    "cloudbuild.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "aiplatform.googleapis.com",
    "pubsub.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false

  timeouts {
    create = "10m"
    update = "10m"
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "google_project" "current" {
  project_id = var.project_id
}

# Reference existing secrets (created manually outside Terraform)
data "google_secret_manager_secret" "azure_devops_pat" {
  secret_id = "azure-devops-pat"
  project   = var.project_id

  depends_on = [google_project_service.apis]
}

data "google_secret_manager_secret" "api_key" {
  secret_id = "pr-review-api-key"
  project   = var.project_id

  depends_on = [google_project_service.apis]
}
