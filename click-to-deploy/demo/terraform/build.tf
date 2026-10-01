# -----------------------------------------------------------------------------
# Container image. Built from this repository with Cloud Build in the target
# project, unless var.container_image points at a prebuilt one.
# -----------------------------------------------------------------------------

locals {
  # Inputs that change the image. The tag is their hash, so a re-apply with
  # unchanged sources is a no-op and a changed source gets a new revision.
  build_inputs = concat(
    ["Dockerfile", "pyproject.toml", "uv.lock", ".gcloudignore", "agent/instructions.md"],
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

resource "null_resource" "build_image" {
  count = var.container_image == "" ? 1 : 0

  triggers = {
    image = local.built_image
  }

  provisioner "local-exec" {
    working_dir = local.repo_root
    command     = <<-EOT
      set -euo pipefail
      gcloud builds submit . \
        --project="${var.project_id}" \
        --region="${var.region}" \
        --config="click-to-deploy/demo/cloudbuild.yaml" \
        --substitutions="_IMAGE=${local.built_image}" \
        --service-account="projects/${var.project_id}/serviceAccounts/${google_service_account.build[0].email}" \
        --quiet
    EOT
    interpreter = ["/bin/bash", "-c"]
  }

  depends_on = [
    google_artifact_registry_repository_iam_member.build_writer,
    google_project_iam_member.build,
    time_sleep.after_identities,
  ]
}
