# =============================================================================
# Outputs
# =============================================================================

# -----------------------------------------------------------------------------
# Project Information
# -----------------------------------------------------------------------------

output "project_id" {
  description = "The GCP project ID"
  value       = var.project_id
}

output "region" {
  description = "The GCP region"
  value       = var.region
}

# -----------------------------------------------------------------------------
# Function URLs
# -----------------------------------------------------------------------------

output "function_url_http" {
  description = "URL for the HTTP-triggered PR review function"
  value       = google_cloudfunctions2_function.pr_regression_review.service_config[0].uri
}

output "function_url_webhook" {
  description = "URL for the webhook receiver function"
  value       = google_cloudfunctions2_function.pr_review_webhook.service_config[0].uri
}

output "function_url_dlq" {
  description = "URL for the DLQ processor function"
  value       = google_cloudfunctions2_function.process_dlq.service_config[0].uri
}

# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------

output "gcs_bucket_name" {
  description = "Name of the GCS bucket for PR reviews"
  value       = google_storage_bucket.reviews.name
}

output "gcs_bucket_url" {
  description = "URL of the GCS bucket"
  value       = google_storage_bucket.reviews.url
}

# -----------------------------------------------------------------------------
# Pub/Sub
# -----------------------------------------------------------------------------

output "pubsub_topic" {
  description = "Name of the Pub/Sub topic for PR review triggers"
  value       = google_pubsub_topic.pr_review_trigger.name
}

output "pubsub_topic_id" {
  description = "Full ID of the Pub/Sub topic"
  value       = google_pubsub_topic.pr_review_trigger.id
}

output "dlq_topic" {
  description = "Name of the Dead Letter Queue topic"
  value       = google_pubsub_topic.dlq.name
}

output "dlq_subscription" {
  description = "Name of the DLQ subscription"
  value       = google_pubsub_subscription.dlq_sub.name
}

# -----------------------------------------------------------------------------
# Service Account
# -----------------------------------------------------------------------------

output "service_account_email" {
  description = "Email of the Cloud Functions service account"
  value       = google_service_account.cloud_functions.email
}

# -----------------------------------------------------------------------------
# Quick Start Commands
# -----------------------------------------------------------------------------

output "test_commands" {
  description = "Commands to test the deployment"
  value       = <<-EOT

    # Test HTTP function (replace YOUR_API_KEY with actual key)
    curl -X POST "${google_cloudfunctions2_function.pr_regression_review.service_config[0].uri}" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: YOUR_API_KEY" \
      -d '{"pr_id": 12345}'

    # Test webhook function
    curl -X POST "${google_cloudfunctions2_function.pr_review_webhook.service_config[0].uri}" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: YOUR_API_KEY" \
      -d '{"pr_id": 12345, "commit_sha": "abc123"}'

    # Publish to Pub/Sub topic
    gcloud pubsub topics publish ${google_pubsub_topic.pr_review_trigger.name} \
      --project=${var.project_id} \
      --message='{"pr_id": 12345, "commit_sha": "abc123", "source": "manual-test"}'

    # View function logs
    gcloud functions logs read pr-regression-review --region=${var.region} --limit=50

  EOT
}
