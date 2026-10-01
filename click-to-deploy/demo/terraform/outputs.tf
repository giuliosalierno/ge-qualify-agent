output "service_url" {
  description = "Cloud Run URL of the agent (IAM-protected; only Gemini Enterprise may call it)."
  value       = local.service_url
}

output "agent_card_url" {
  description = "A2A agent card."
  value       = "${local.service_url}/.well-known/agent-card.json"
}

output "gemini_enterprise_engine_id" {
  description = "Gemini Enterprise app the agent is registered in."
  value       = local.ge_engine_id
}

output "gemini_enterprise_console_url" {
  description = "Open this, then the app's web URL, to try the agent."
  value       = "https://console.cloud.google.com/gen-app-builder/locations/global/engines/${local.ge_engine_id}/overview?project=${var.project_id}"
}

output "records_bucket" {
  description = "GCS bucket holding sessions and qualification records."
  value       = google_storage_bucket.records.name
}
