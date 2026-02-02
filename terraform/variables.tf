# =============================================================================
# Input Variables
# =============================================================================

# -----------------------------------------------------------------------------
# Project Configuration
# -----------------------------------------------------------------------------

variable "project_id" {
  description = "The GCP project ID to deploy resources into"
  type        = string
}

variable "region" {
  description = "The GCP region for resources"
  type        = string
  default     = "us-central1"
}

# -----------------------------------------------------------------------------
# Storage Configuration
# -----------------------------------------------------------------------------

variable "gcs_bucket_name" {
  description = "Name of the GCS bucket for storing PR reviews"
  type        = string
}

variable "gcs_bucket_location" {
  description = "Location for the GCS bucket (defaults to region)"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Azure DevOps Configuration
# -----------------------------------------------------------------------------

variable "azure_devops_org" {
  description = "Azure DevOps organization name"
  type        = string
}

variable "azure_devops_project" {
  description = "Azure DevOps project name (URL-encoded if contains spaces)"
  type        = string
}

variable "azure_devops_repo" {
  description = "Azure DevOps repository name or ID"
  type        = string
}

# -----------------------------------------------------------------------------
# Vertex AI Configuration
# -----------------------------------------------------------------------------

variable "vertex_project" {
  description = "GCP project ID for Vertex AI (defaults to project_id)"
  type        = string
  default     = ""
}

variable "vertex_location" {
  description = "GCP region for Vertex AI"
  type        = string
  default     = "us-central1"
}

variable "gemini_model" {
  description = "Gemini model to use for reviews"
  type        = string
  default     = "gemini-2.5-pro"
}

# -----------------------------------------------------------------------------
# Cloud Function Configuration
# -----------------------------------------------------------------------------

variable "function_memory" {
  description = "Memory allocation for Cloud Functions"
  type        = string
  default     = "512Mi"
}

variable "function_cpu" {
  description = "CPU allocation for Cloud Functions"
  type        = string
  default     = "1"
}

variable "function_timeout" {
  description = "Timeout for Cloud Functions in seconds"
  type        = number
  default     = 900
}

variable "function_max_instances" {
  description = "Maximum number of function instances"
  type        = number
  default     = 60
}

variable "function_concurrency" {
  description = "Maximum concurrent requests per instance"
  type        = number
  default     = 80
}

# -----------------------------------------------------------------------------
# Pub/Sub Configuration
# -----------------------------------------------------------------------------

variable "pubsub_topic_name" {
  description = "Name of the Pub/Sub topic for PR review triggers"
  type        = string
  default     = "pr-review-trigger"
}

variable "dlq_topic_name" {
  description = "Name of the Dead Letter Queue topic"
  type        = string
  default     = "pr-review-dlq"
}

variable "dlq_max_delivery_attempts" {
  description = "Maximum delivery attempts before sending to DLQ"
  type        = number
  default     = 5
}

# -----------------------------------------------------------------------------
# Optional: System Prompt Configuration
# -----------------------------------------------------------------------------

variable "system_prompt_blob_path" {
  description = "GCS path to system prompt file"
  type        = string
  default     = "prompts/system-prompt.txt"
}

# -----------------------------------------------------------------------------
# IAM Authentication Configuration
# -----------------------------------------------------------------------------

variable "authorized_users" {
  description = "List of user emails authorized to invoke functions directly (for testing/debugging). Example: ['user1@example.com', 'user2@example.com']"
  type        = list(string)
  default     = []
}
