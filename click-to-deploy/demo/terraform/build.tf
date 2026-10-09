# -----------------------------------------------------------------------------
# Container image. Built from this repository with Cloud Build in the target
# project, unless var.container_image points at a prebuilt one.
# -----------------------------------------------------------------------------

locals {
  # Inputs that change the image. The tag is their hash, so a re-apply with
  # unchanged sources is a no-op and a changed source gets a new revision.
  build_inputs = concat(
    ["Dockerfile", "requirements.txt", "pyproject.toml", "uv.lock", ".gcloudignore", "agent/instructions.md"],
    [for f in fileset(local.repo_root, "qualify/**") : f if !strcontains(f, "__pycache__")],
    [for f in fileset(local.repo_root, "skills/ge_capability_grounding/**") : f],
  )
  source_hash = sha1(join("", [for f in sort(local.build_inputs) : filesha1("${local.repo_root}/${f}")]))

  built_image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.service_name}/${var.service_name}:${substr(local.source_hash, 0, 12)}"
  image       = var.container_image != "" ? var.container_image : local.built_image
}

resource "google_artifact_registry_repository" "images" {
  count = var.container_image == "" ? 1 : 0

  project       = var.project_id
  location      = var.region
  repository_id = var.service_name
  format        = "DOCKER"
  description   = "GE Qualify Agent images"
  depends_on    = [time_sleep.after_apis]
}

resource "google_artifact_registry_repository_iam_member" "build_writer" {
  count = var.container_image == "" ? 1 : 0

  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.images[0].repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.build[0].email}"
}

# Terraform-managed staging bucket for the uploaded source. The build SA can
# read only this bucket, and destroy removes it (gcloud's default
# <project>_cloudbuild bucket would be left behind).
resource "google_storage_bucket" "build_staging" {
  count = var.container_image == "" ? 1 : 0

  project                     = var.project_id
  name                        = "${local.bucket_name}-build"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [time_sleep.after_apis]
}

resource "google_storage_bucket_iam_member" "build_staging_reader" {
  count = var.container_image == "" ? 1 : 0

  bucket = google_storage_bucket.build_staging[0].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.build[0].email}"
}

# IAM grants on a brand-new service account take a while to propagate. Without
# this pause the first build can fail with "could not resolve source" (the
# build SA cannot yet read the uploaded source tarball).
resource "time_sleep" "after_build_iam" {
  count = var.container_image == "" ? 1 : 0

  create_duration = "60s"
  depends_on = [
    google_artifact_registry_repository_iam_member.build_writer,
    google_project_iam_member.build,
    google_storage_bucket_iam_member.build_staging_reader,
  ]
}

resource "null_resource" "build_image" {
  count = var.container_image == "" ? 1 : 0

  triggers = {
    image = local.built_image
  }

  provisioner "local-exec" {
    working_dir = local.repo_root
    # Retries only the IAM-propagation failure ("could not resolve source");
    # any other build error fails immediately.
    command     = <<-EOT
      set -uo pipefail
      log="$(mktemp)"
      trap 'rm -f "$log"' EXIT
      for attempt in 1 2 3 4; do
        gcloud builds submit . \
          --project="${var.project_id}" \
          --region="${var.region}" \
          --config="click-to-deploy/demo/cloudbuild.yaml" \
          --substitutions="_IMAGE=${local.built_image}" \
          --gcs-source-staging-dir="gs://${google_storage_bucket.build_staging[0].name}/source" \
          --service-account="projects/${var.project_id}/serviceAccounts/${google_service_account.build[0].email}" \
          --quiet 2>&1 | tee "$log"
        rc=$${PIPESTATUS[0]}
        [ "$rc" -eq 0 ] && exit 0
        grep -q "could not resolve source" "$log" || exit "$rc"
        echo "Build SA permissions not propagated yet (attempt $attempt); retrying in 30s..."
        sleep 30
      done
      exit "$rc"
    EOT
    interpreter = ["/bin/bash", "-c"]
  }

  depends_on = [
    google_artifact_registry_repository_iam_member.build_writer,
    google_project_iam_member.build,
    time_sleep.after_identities,
    time_sleep.after_build_iam,
  ]
}
