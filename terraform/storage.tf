# =============================================================================
# Cloud Storage Configuration
# =============================================================================

# -----------------------------------------------------------------------------
# GCS Bucket for PR Reviews
# -----------------------------------------------------------------------------

resource "google_storage_bucket" "reviews" {
  name     = var.gcs_bucket_name
  location = var.gcs_bucket_location != "" ? var.gcs_bucket_location : var.region
  project  = var.project_id

  # Prevent accidental deletion
  force_destroy = false

  # Uniform bucket-level access (recommended)
  uniform_bucket_level_access = true

  # Versioning for audit trail
  versioning {
    enabled = true
  }

  # Lifecycle rules for cost management
  lifecycle_rule {
    condition {
      age = 365 # Days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 730 # 2 years
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  # Labels for organization
  labels = {
    environment = "production"
    purpose     = "pr-reviews"
    managed-by  = "terraform"
  }

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Grant Cloud Functions Service Account access to bucket
# -----------------------------------------------------------------------------

resource "google_storage_bucket_iam_member" "functions_storage_admin" {
  bucket = google_storage_bucket.reviews.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.cloud_functions.email}"
}
