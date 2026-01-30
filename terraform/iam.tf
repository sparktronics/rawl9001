# =============================================================================
# IAM Configuration
# =============================================================================

# -----------------------------------------------------------------------------
# Service Account for Cloud Functions
# -----------------------------------------------------------------------------

resource "google_service_account" "cloud_functions" {
  account_id   = "pr-review-functions"
  display_name = "PR Review Cloud Functions Service Account"
  description  = "Service account for RAWL 9001 PR review Cloud Functions"
  project      = var.project_id

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Secret Manager Access for Azure DevOps PAT
# -----------------------------------------------------------------------------

resource "google_secret_manager_secret_iam_member" "azure_pat_accessor" {
  secret_id = data.google_secret_manager_secret.azure_devops_pat.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_functions.email}"
  project   = var.project_id
}

# -----------------------------------------------------------------------------
# Secret Manager Access for API Key
# -----------------------------------------------------------------------------

resource "google_secret_manager_secret_iam_member" "api_key_accessor" {
  secret_id = data.google_secret_manager_secret.api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_functions.email}"
  project   = var.project_id
}

# -----------------------------------------------------------------------------
# Vertex AI User Role (for Gemini API access)
# -----------------------------------------------------------------------------

resource "google_project_iam_member" "vertex_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_functions.email}"
}

# -----------------------------------------------------------------------------
# Pub/Sub Publisher (for webhook to publish to topic)
# -----------------------------------------------------------------------------

resource "google_pubsub_topic_iam_member" "functions_publisher" {
  topic  = google_pubsub_topic.pr_review_trigger.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.cloud_functions.email}"
}

# -----------------------------------------------------------------------------
# Pub/Sub Subscriber (for DLQ processing)
# -----------------------------------------------------------------------------

resource "google_pubsub_subscription_iam_member" "functions_subscriber" {
  subscription = google_pubsub_subscription.dlq_sub.id
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.cloud_functions.email}"
}

# -----------------------------------------------------------------------------
# Cloud Run Invoker (for Pub/Sub to invoke Cloud Functions)
# -----------------------------------------------------------------------------

resource "google_project_iam_member" "pubsub_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# =============================================================================
# Service Accounts for Function Invocation (IAM Authentication)
# =============================================================================

# -----------------------------------------------------------------------------
# Service Account for Authorized Callers (e.g., CI/CD Pipelines)
# -----------------------------------------------------------------------------

resource "google_service_account" "function_caller" {
  account_id   = "pr-review-caller"
  display_name = "PR Review Function Caller"
  description  = "Service account authorized to invoke PR review HTTP functions"
  project      = var.project_id

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Service Account for Testing/Development
# -----------------------------------------------------------------------------

resource "google_service_account" "function_tester" {
  account_id   = "pr-review-tester"
  display_name = "PR Review Function Tester"
  description  = "Service account for testing and debugging PR review functions"
  project      = var.project_id

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Grant Invoker Permission to Service Accounts for HTTP Functions
# -----------------------------------------------------------------------------

# Grant pr-review-caller permission to invoke all HTTP functions
resource "google_cloud_run_service_iam_member" "caller_invoker" {
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

  depends_on = [
    google_cloudfunctions2_function.pr_regression_review,
    google_cloudfunctions2_function.pr_review_webhook,
    google_cloudfunctions2_function.process_dlq,
  ]
}

# Grant pr-review-tester permission to invoke all HTTP functions
resource "google_cloud_run_service_iam_member" "tester_invoker" {
  for_each = toset([
    google_cloudfunctions2_function.pr_regression_review.name,
    google_cloudfunctions2_function.pr_review_webhook.name,
    google_cloudfunctions2_function.process_dlq.name,
  ])

  location = var.region
  project  = var.project_id
  service  = each.value
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.function_tester.email}"

  depends_on = [
    google_cloudfunctions2_function.pr_regression_review,
    google_cloudfunctions2_function.pr_review_webhook,
    google_cloudfunctions2_function.process_dlq,
  ]
}

# -----------------------------------------------------------------------------
# Grant Invoker Permission to Authorized Users (Optional - for testing)
# -----------------------------------------------------------------------------

# Allow specific users to invoke functions directly (useful for testing)
resource "google_cloud_run_service_iam_member" "user_invokers" {
  for_each = {
    for pair in flatten([
      for user in var.authorized_users : [
        for func in [
          google_cloudfunctions2_function.pr_regression_review.name,
          google_cloudfunctions2_function.pr_review_webhook.name,
          google_cloudfunctions2_function.process_dlq.name,
        ] : {
          user = user
          func = func
        }
      ]
    ]) : "${pair.user}-${pair.func}" => pair
  }

  location = var.region
  project  = var.project_id
  service  = each.value.func
  role     = "roles/run.invoker"
  member   = "user:${each.value.user}"

  depends_on = [
    google_cloudfunctions2_function.pr_regression_review,
    google_cloudfunctions2_function.pr_review_webhook,
    google_cloudfunctions2_function.process_dlq,
  ]
}
