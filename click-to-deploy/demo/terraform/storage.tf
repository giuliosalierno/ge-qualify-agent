# -----------------------------------------------------------------------------
# Durable record store: sessions, records and the completion index the
# portfolio and technical review read. Optionally seeded with synthetic data.
# -----------------------------------------------------------------------------

resource "google_storage_bucket" "records" {
  project                     = var.project_id
  name                        = local.bucket_name
  location                    = var.data_location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  # Demo bucket: destroy must leave a clean project.
  force_destroy = true

  versioning {
    enabled = false
  }

  depends_on = [time_sleep.after_apis]
}

resource "google_storage_bucket_iam_member" "runtime_records" {
  bucket = google_storage_bucket.records.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

locals {
  seed_dir   = "${path.module}/../seed"
  seed_files = var.seed_demo_data ? fileset(local.seed_dir, "{records,portfolio}/*.json") : toset([])
}

resource "google_storage_bucket_object" "seed" {
  for_each = local.seed_files

  bucket       = google_storage_bucket.records.name
  name         = each.value
  source       = "${local.seed_dir}/${each.value}"
  content_type = "application/json"
}
