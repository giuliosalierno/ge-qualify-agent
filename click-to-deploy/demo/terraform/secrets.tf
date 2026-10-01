# HMAC key that signs per-conversation links (qualify/connectors/oauth_state.py).
# Generated per deployment, stored in Secret Manager, mounted as an env var.
# It is never output and never set as a plain env var.

resource "random_password" "oauth_state" {
  length  = 64
  special = false
}

resource "google_secret_manager_secret" "oauth_state" {
  project   = var.project_id
  secret_id = "${var.service_name}-oauth-state"

  replication {
    auto {}
  }

  depends_on = [time_sleep.after_apis]
}

resource "google_secret_manager_secret_version" "oauth_state" {
  secret      = google_secret_manager_secret.oauth_state.id
  secret_data = random_password.oauth_state.result
}

resource "google_secret_manager_secret_iam_member" "runtime_oauth_state" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.oauth_state.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}
