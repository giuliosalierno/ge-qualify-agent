# -----------------------------------------------------------------------------
# Identities. Dedicated service accounts with only the roles they use; the
# Compute default SA is never relied on (Argolis often strips its grants).
# -----------------------------------------------------------------------------

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "${var.service_name}-run"
  display_name = "GE Qualify Agent - Cloud Run runtime"
  depends_on   = [time_sleep.after_apis]
}

resource "google_project_iam_member" "runtime" {
  for_each = toset([
    "roles/aiplatform.user", # Gemini on Vertex AI
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_service_account" "build" {
  count = var.container_image == "" ? 1 : 0

  project      = var.project_id
  account_id   = "${var.service_name}-build"
  display_name = "GE Qualify Agent - Cloud Build"
  depends_on   = [time_sleep.after_apis]
}

resource "google_project_iam_member" "build" {
  # Source is read from the dedicated staging bucket (build.tf), not via a
  # project-wide storage role that would also expose the records bucket.
  for_each = var.container_image == "" ? toset([
    "roles/logging.logWriter",
  ]) : toset([])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.build[0].email}"
}

# Gemini Enterprise calls the agent as its Discovery Engine service agent.
# Creating the identity up front makes the invoker binding below valid on a
# brand-new project where the agent has never been provisioned.
resource "google_project_service_identity" "discoveryengine" {
  provider = google-beta

  project    = var.project_id
  service    = "discoveryengine.googleapis.com"
  depends_on = [time_sleep.after_apis]
}

resource "time_sleep" "after_identities" {
  depends_on = [
    google_project_service_identity.discoveryengine,
    google_service_account.runtime,
    google_service_account.build,
  ]
  create_duration = "30s"
}

# The Argolis user who clicked deploy can open the Gemini Enterprise app.
resource "google_project_iam_member" "demo_user_ge" {
  project = var.project_id
  role    = "roles/discoveryengine.user"
  member  = "user:${var.gcp_account_name}"
}
