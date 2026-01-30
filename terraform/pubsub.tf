# =============================================================================
# Pub/Sub Configuration
# =============================================================================

# -----------------------------------------------------------------------------
# Main PR Review Trigger Topic
# -----------------------------------------------------------------------------

resource "google_pubsub_topic" "pr_review_trigger" {
  name    = var.pubsub_topic_name
  project = var.project_id

  labels = {
    environment = "production"
    purpose     = "pr-review-trigger"
    managed-by  = "terraform"
  }

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Dead Letter Queue Topic
# -----------------------------------------------------------------------------

resource "google_pubsub_topic" "dlq" {
  name    = var.dlq_topic_name
  project = var.project_id

  labels = {
    environment = "production"
    purpose     = "dead-letter-queue"
    managed-by  = "terraform"
  }

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Dead Letter Queue Subscription (for inspecting failed messages)
# -----------------------------------------------------------------------------

resource "google_pubsub_subscription" "dlq_sub" {
  name    = "${var.dlq_topic_name}-sub"
  topic   = google_pubsub_topic.dlq.id
  project = var.project_id

  # How long to retain unacknowledged messages
  message_retention_duration = "604800s" # 7 days

  # How long Pub/Sub waits for acknowledgment
  ack_deadline_seconds = 60

  # Keep messages for replay
  retain_acked_messages = true

  # Expiration policy (never expire)
  expiration_policy {
    ttl = ""
  }

  labels = {
    environment = "production"
    purpose     = "dlq-inspection"
    managed-by  = "terraform"
  }
}

# -----------------------------------------------------------------------------
# IAM for Pub/Sub Service Account to publish to DLQ
# -----------------------------------------------------------------------------

resource "google_pubsub_topic_iam_member" "dlq_publisher" {
  topic   = google_pubsub_topic.dlq.id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
