# -----------------------------------------------------------------------------
# Demo settings. Defaults are what go/demos deploys.
# -----------------------------------------------------------------------------

variable "region" {
  description = "Region for Cloud Run, Artifact Registry and Cloud Build."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name. Prefixes every other resource name (bucket, secret, service accounts, GE app)."
  type        = string
  default     = "ge-qualify-agent"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,22}$", var.service_name))
    error_message = "service_name: 3-23 chars, lowercase letters, digits and hyphens (service account IDs are limited to 30 chars)."
  }
}

variable "model" {
  description = "Gemini model used for extraction and chat."
  type        = string
  default     = "gemini-3.8-flash"
}

variable "genai_location" {
  description = "Vertex AI location for Gemini calls. Gemini 3 models are served from global."
  type        = string
  default     = "global"
}

variable "agent_display_name" {
  description = "Agent name in Gemini Enterprise. Registration updates an existing agent with this name, so it must be unique per GE app."
  type        = string
  default     = "GE Use Case Qualification Agent"
}

variable "container_image" {
  description = "Prebuilt image to deploy. Empty (default) builds the repo with Cloud Build in the target project."
  type        = string
  default     = ""
}

variable "ge_engine_id" {
  description = "Existing Gemini Enterprise app (engine) ID to register the agent in. Empty (default) creates a new app."
  type        = string
  default     = ""
}

variable "register_agent" {
  description = "Register the agent in Gemini Enterprise after deploy."
  type        = bool
  default     = true
}

variable "ensure_ge_license" {
  description = "Start the Gemini Enterprise free trial if the project has no active licence, and assign licences to the demo users. Agent registration fails without a licence."
  type        = bool
  default     = true
}

variable "ge_license_users" {
  description = "Extra users (emails) to license, in addition to gcp_account_name."
  type        = list(string)
  default     = []
}

variable "seed_demo_data" {
  description = "Upload synthetic qualified use cases so portfolio and technical review work on the first conversation."
  type        = bool
  default     = true
}

variable "a2a_auth_mode" {
  description = "A2A caller verification: enforce (reject non-GE callers) or log."
  type        = string
  default     = "enforce"

  validation {
    condition     = contains(["enforce", "log"], var.a2a_auth_mode)
    error_message = "a2a_auth_mode must be enforce or log."
  }
}
