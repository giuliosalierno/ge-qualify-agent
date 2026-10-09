locals {
  # Repository root, three levels up from click-to-deploy/demo/terraform.
  # In a go/demos auto-provisioned C2D repository ({id}-{slug}), the upstream
  # app source is synced under demo/cloud-gtm/ge-qualification-agent/main.
  c2d_root  = abspath("${path.module}/../../..")
  sync_root = abspath("${path.module}/../../../demo/cloud-gtm/ge-qualification-agent/main")
  repo_root = fileexists("${local.c2d_root}/Dockerfile") ? local.c2d_root : local.sync_root

  # Deterministic Cloud Run URL. Known before the service exists, so the
  # service can be told its own URL (AGENT_URL, A2A audience) on first deploy
  # without a second pass.
  service_url = "https://${var.service_name}-${var.project_number}.${var.region}.run.app"

  # Every name derives from service_name, so a second deployment in a project
  # that already runs the agent (e.g. a test next to production) cannot
  # collide with or take over existing resources.
  bucket_name = "${var.project_id}-${var.service_name}"

  # Discovery Engine deletes asynchronously and keeps IDs reserved for hours,
  # so destroy followed by deploy in the same project needs fresh IDs.
  ge_id_suffix = random_id.ge.hex

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

resource "random_id" "ge" {
  byte_length = 3
  keepers = {
    service_name = var.service_name
  }
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
