locals {
  # Repository root, three levels up from click-to-deploy/demo/terraform.
  repo_root = abspath("${path.module}/../../..")

  # Deterministic Cloud Run URL. Known before the service exists, so the
  # service can be told its own URL (AGENT_URL, A2A audience) on first deploy
  # without a second pass.
  service_url = "https://${var.service_name}-${var.project_number}.${var.region}.run.app"

  bucket_name = "${var.project_id}-qualify-records"

  ge_service_agent    = "service-${var.project_number}@gcp-sa-discoveryengine.iam.gserviceaccount.com"
  ge_engine_id        = var.ge_engine_id != "" ? var.ge_engine_id : google_discovery_engine_search_engine.demo[0].engine_id
  ge_app_display_name = "GE Qualify Demo"

  apis = [
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "discoveryengine.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# API enablement and new service agents take a while to propagate; resources
# created immediately afterwards fail intermittently without this pause.
resource "time_sleep" "after_apis" {
  depends_on      = [google_project_service.apis]
  create_duration = "60s"
}
