# -----------------------------------------------------------------------------
# Gemini Enterprise: create an app (unless one is supplied) and register the
# agent in it. Agent registration has no Terraform resource yet, so it runs
# the idempotent REST script in ../scripts/register_agent.sh.
# -----------------------------------------------------------------------------

resource "google_discovery_engine_data_store" "demo" {
  count = var.ge_engine_id == "" ? 1 : 0

  project                     = var.project_id
  location                    = "global"
  data_store_id               = "${var.service_name}-ds-${local.ge_id_suffix}"
  display_name                = "${local.ge_app_display_name} data store"
  industry_vertical           = "GENERIC"
  content_config              = "NO_CONTENT"
  solution_types              = ["SOLUTION_TYPE_SEARCH"]
  create_advanced_site_search = false

  depends_on = [time_sleep.after_identities]
}

resource "google_discovery_engine_search_engine" "demo" {
  count = var.ge_engine_id == "" ? 1 : 0

  project           = var.project_id
  engine_id         = "${var.service_name}-app-${local.ge_id_suffix}"
  collection_id     = "default_collection"
  location          = "global"
  display_name      = local.ge_app_display_name
  data_store_ids    = [google_discovery_engine_data_store.demo[0].data_store_id]
  industry_vertical = "GENERIC"
  app_type          = "APP_TYPE_INTRANET"

  search_engine_config {
    search_tier                = "SEARCH_TIER_STANDARD"
    required_subscription_tier = "SUBSCRIPTION_TIER_ENTERPRISE"
    search_add_ons             = ["SEARCH_ADD_ON_LLM"]
  }

  features = {
    "agent-sharing-without-admin-approval" = "FEATURE_STATE_ON"
    "disable-agent-sharing"                = "FEATURE_STATE_OFF"
  }
}

data "google_client_config" "current" {}

# A fresh project has no Gemini Enterprise licence, and the registration API
# rejects unlicensed callers. Start the free trial if needed and license the
# demo users. Not undone on destroy: licence configs have no delete API and the
# free trial expires on its own after one month.
resource "null_resource" "ge_license" {
  count = var.ensure_ge_license ? 1 : 0

  triggers = {
    users = join(" ", distinct(concat([var.gcp_account_name], var.ge_license_users)))
  }

  provisioner "local-exec" {
    command     = "${path.module}/../scripts/ensure_ge_license.sh"
    interpreter = ["/bin/bash", "-c"]
    environment = {
      ACCESS_TOKEN   = data.google_client_config.current.access_token
      PROJECT_ID     = var.project_id
      PROJECT_NUMBER = var.project_number
      LICENSE_USERS  = self.triggers.users
    }
  }

  depends_on = [google_discovery_engine_search_engine.demo]
}

resource "null_resource" "register_agent" {
  count = var.register_agent ? 1 : 0

  triggers = {
    engine_id = local.ge_engine_id
    agent_url = local.service_url
    # Re-sync the card snapshot GE stores whenever new code ships. The image
    # tag is a hash of the source and is known at plan time; the service's
    # latest_ready_revision is not (it changes mid-apply, which Terraform
    # rejects as an inconsistent final plan).
    image = local.image
  }

  provisioner "local-exec" {
    command     = "${path.module}/../scripts/register_agent.sh"
    interpreter = ["/bin/bash", "-c"]
    environment = {
      ACCESS_TOKEN = data.google_client_config.current.access_token
      PROJECT_ID   = var.project_id
      ENGINE_ID    = local.ge_engine_id
      AGENT_URL    = local.service_url
      DISPLAY_NAME = var.agent_display_name
    }
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.ge_invoker,
    null_resource.ge_license,
  ]
}
