# -----------------------------------------------------------------------------
# Cloud Run service.
#
# Access model (same as production, minus the Load Balancer):
#   - allUsers is NOT an invoker. Only Gemini Enterprise's Discovery Engine
#     service agent may invoke the service (Cloud Run IAM).
#   - Cloud Run verifies GE's ID token, so the app trusts the verified claims
#     (A2A_TRUST_CLOUD_RUN_IAM=1) and additionally checks the caller is the GE
#     service agent (A2A_AUTH_MODE=enforce). Never combine that flag with an
#     allUsers invoker.
#   - No SharePoint, no Load Balancer, no IAP: STORAGE_PROVIDER=none.
# -----------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "agent" {
  project             = var.project_id
  name                = var.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.runtime.email

    scaling {
      # Scale to zero between demos; one instance max because A2A task state
      # is held in memory (sessions themselves are durable in GCS).
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = local.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle = true
      }

      dynamic "env" {
        for_each = {
          GOOGLE_CLOUD_PROJECT      = var.project_id
          GOOGLE_CLOUD_LOCATION     = var.genai_location
          GOOGLE_GENAI_USE_VERTEXAI = "TRUE"
          MODEL                     = var.model
          PROJECT_NUMBER            = var.project_number
          QUALIFY_GCS_BUCKET        = google_storage_bucket.records.name
          AGENT_URL                 = local.service_url
          A2A_AUDIENCES             = local.service_url
          A2A_AUTH_MODE             = var.a2a_auth_mode
          A2A_TRUST_CLOUD_RUN_IAM   = "1"
          STORAGE_PROVIDER          = "none"
          SIGNIN_CARD               = "0"
          WEB_OAUTH_CALLBACK        = "0"
        }
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "OAUTH_STATE_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.oauth_state.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    null_resource.build_image,
    google_project_iam_member.runtime,
    google_storage_bucket_iam_member.runtime_records,
    google_secret_manager_secret_iam_member.runtime_oauth_state,
    google_secret_manager_secret_version.oauth_state,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "ge_invoker" {
  project  = var.project_id
  location = google_cloud_run_v2_service.agent.location
  name     = google_cloud_run_v2_service.agent.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.ge_service_agent}"

  depends_on = [time_sleep.after_identities]
}
