# =============================================================================
# Cloud Functions (Gen2) Configuration
# =============================================================================

# -----------------------------------------------------------------------------
# Archive the source code for deployment
# -----------------------------------------------------------------------------

data "archive_file" "function_source" {
  type        = "zip"
  output_path = "${path.module}/.terraform/function-source.zip"

  source {
    content  = file("${path.module}/../main.py")
    filename = "main.py"
  }

  source {
    content  = file("${path.module}/../requirements.txt")
    filename = "requirements.txt"
  }
}

# -----------------------------------------------------------------------------
# Upload source code to GCS
# -----------------------------------------------------------------------------

resource "google_storage_bucket_object" "function_source" {
  name   = "function-source/${data.archive_file.function_source.output_md5}.zip"
  bucket = google_storage_bucket.reviews.name
  source = data.archive_file.function_source.output_path

  depends_on = [google_storage_bucket.reviews]
}

# -----------------------------------------------------------------------------
# Common environment variables for all functions
# -----------------------------------------------------------------------------

locals {
  common_env_vars = {
    GCS_BUCKET           = var.gcs_bucket_name
    AZURE_DEVOPS_ORG     = var.azure_devops_org
    AZURE_DEVOPS_PROJECT = var.azure_devops_project
    AZURE_DEVOPS_REPO    = var.azure_devops_repo
    VERTEX_PROJECT       = var.vertex_project != "" ? var.vertex_project : var.project_id
    VERTEX_LOCATION      = var.vertex_location
    GEMINI_MODEL         = var.gemini_model
    PUBSUB_TOPIC         = var.pubsub_topic_name
    DLQ_SUBSCRIPTION     = "${var.dlq_topic_name}-sub"
    SYSTEM_PROMPT_BLOB_PATH = var.system_prompt_blob_path
  }

  common_secret_env_vars = [
    {
      key        = "AZURE_DEVOPS_PAT"
      project_id = var.project_id
      secret     = "azure-devops-pat"
      version    = "latest"
    },
    {
      key        = "API_KEY"
      project_id = var.project_id
      secret     = "pr-review-api-key"
      version    = "latest"
    }
  ]
}

# -----------------------------------------------------------------------------
# Function 1: HTTP Trigger (Synchronous Review)
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "pr_regression_review" {
  name        = "pr-regression-review"
  location    = var.region
  project     = var.project_id
  description = "RAWL 9001 - PR regression review via HTTP (synchronous)"

  build_config {
    runtime     = "python312"
    entry_point = "review_pr"

    source {
      storage_source {
        bucket = google_storage_bucket.reviews.name
        object = google_storage_bucket_object.function_source.name
      }
    }
  }

  service_config {
    max_instance_count             = var.function_max_instances
    min_instance_count             = 0
    available_memory               = var.function_memory
    available_cpu                  = var.function_cpu
    timeout_seconds                = var.function_timeout
    max_instance_request_concurrency = var.function_concurrency
    service_account_email          = google_service_account.cloud_functions.email
    ingress_settings               = "ALLOW_ALL"
    all_traffic_on_latest_revision = true

    dynamic "environment_variables" {
      for_each = local.common_env_vars
      content {
        # This is handled differently - see below
      }
    }

    environment_variables = local.common_env_vars

    dynamic "secret_environment_variables" {
      for_each = local.common_secret_env_vars
      content {
        key        = secret_environment_variables.value.key
        project_id = secret_environment_variables.value.project_id
        secret     = secret_environment_variables.value.secret
        version    = secret_environment_variables.value.version
      }
    }
  }

  labels = {
    environment = "production"
    purpose     = "pr-review"
    trigger     = "http"
    managed-by  = "terraform"
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.azure_pat_accessor,
    google_secret_manager_secret_iam_member.api_key_accessor,
  ]
}

# NOTE: IAM invoker permissions are now managed in iam.tf
# Only authorized service accounts and users can invoke this function

# -----------------------------------------------------------------------------
# Function 2: Pub/Sub Trigger (Asynchronous Review)
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "pr_review_pubsub" {
  name        = "pr-review-pubsub"
  location    = var.region
  project     = var.project_id
  description = "RAWL 9001 - PR regression review via Pub/Sub (asynchronous)"

  build_config {
    runtime     = "python312"
    entry_point = "review_pr_pubsub"

    source {
      storage_source {
        bucket = google_storage_bucket.reviews.name
        object = google_storage_bucket_object.function_source.name
      }
    }
  }

  service_config {
    max_instance_count             = var.function_max_instances
    min_instance_count             = 0
    available_memory               = var.function_memory
    available_cpu                  = var.function_cpu
    timeout_seconds                = var.function_timeout
    max_instance_request_concurrency = 1 # Process one message at a time
    service_account_email          = google_service_account.cloud_functions.email
    ingress_settings               = "ALLOW_INTERNAL_ONLY"
    all_traffic_on_latest_revision = true

    environment_variables = local.common_env_vars

    dynamic "secret_environment_variables" {
      for_each = local.common_secret_env_vars
      content {
        key        = secret_environment_variables.value.key
        project_id = secret_environment_variables.value.project_id
        secret     = secret_environment_variables.value.secret
        version    = secret_environment_variables.value.version
      }
    }
  }

  event_trigger {
    trigger_region = var.region
    event_type     = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic   = google_pubsub_topic.pr_review_trigger.id
    retry_policy   = "RETRY_POLICY_RETRY"
  }

  labels = {
    environment = "production"
    purpose     = "pr-review"
    trigger     = "pubsub"
    managed-by  = "terraform"
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.azure_pat_accessor,
    google_secret_manager_secret_iam_member.api_key_accessor,
    google_pubsub_topic.pr_review_trigger,
  ]
}

# -----------------------------------------------------------------------------
# Function 3: Webhook Receiver (Azure DevOps Integration)
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "pr_review_webhook" {
  name        = "pr-review-webhook"
  location    = var.region
  project     = var.project_id
  description = "RAWL 9001 - Webhook receiver for Azure DevOps pipeline integration"

  build_config {
    runtime     = "python312"
    entry_point = "receive_webhook"

    source {
      storage_source {
        bucket = google_storage_bucket.reviews.name
        object = google_storage_bucket_object.function_source.name
      }
    }
  }

  service_config {
    max_instance_count             = var.function_max_instances
    min_instance_count             = 0
    available_memory               = "256Mi" # Lighter weight
    available_cpu                  = "0.167"
    timeout_seconds                = 30
    max_instance_request_concurrency = var.function_concurrency
    service_account_email          = google_service_account.cloud_functions.email
    ingress_settings               = "ALLOW_ALL"
    all_traffic_on_latest_revision = true

    environment_variables = {
      VERTEX_PROJECT = var.vertex_project != "" ? var.vertex_project : var.project_id
      PUBSUB_TOPIC   = var.pubsub_topic_name
    }

    dynamic "secret_environment_variables" {
      for_each = local.common_secret_env_vars
      content {
        key        = secret_environment_variables.value.key
        project_id = secret_environment_variables.value.project_id
        secret     = secret_environment_variables.value.secret
        version    = secret_environment_variables.value.version
      }
    }
  }

  labels = {
    environment = "production"
    purpose     = "webhook"
    trigger     = "http"
    managed-by  = "terraform"
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.api_key_accessor,
  ]
}

# NOTE: IAM invoker permissions are now managed in iam.tf
# Only authorized service accounts and users can invoke this function

# -----------------------------------------------------------------------------
# Function 4: DLQ Processor
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "process_dlq" {
  name        = "process-dead-letter-queue"
  location    = var.region
  project     = var.project_id
  description = "RAWL 9001 - Process failed messages from Dead Letter Queue"

  build_config {
    runtime     = "python312"
    entry_point = "process_dead_letter_queue"

    source {
      storage_source {
        bucket = google_storage_bucket.reviews.name
        object = google_storage_bucket_object.function_source.name
      }
    }
  }

  service_config {
    max_instance_count             = 1 # Only one instance for DLQ processing
    min_instance_count             = 0
    available_memory               = "256Mi"
    available_cpu                  = "0.167"
    timeout_seconds                = 540
    max_instance_request_concurrency = 1
    service_account_email          = google_service_account.cloud_functions.email
    ingress_settings               = "ALLOW_ALL"
    all_traffic_on_latest_revision = true

    environment_variables = local.common_env_vars

    dynamic "secret_environment_variables" {
      for_each = local.common_secret_env_vars
      content {
        key        = secret_environment_variables.value.key
        project_id = secret_environment_variables.value.project_id
        secret     = secret_environment_variables.value.secret
        version    = secret_environment_variables.value.version
      }
    }
  }

  labels = {
    environment = "production"
    purpose     = "dlq-processor"
    trigger     = "http"
    managed-by  = "terraform"
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.azure_pat_accessor,
    google_secret_manager_secret_iam_member.api_key_accessor,
  ]
}

# NOTE: IAM invoker permissions are now managed in iam.tf
# Only authorized service accounts and users can invoke this function
