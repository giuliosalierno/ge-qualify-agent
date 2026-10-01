terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source = "hashicorp/google"
      # app_type / features on google_discovery_engine_search_engine
      # (Gemini Enterprise apps) need a recent provider.
      version = ">= 6.40, < 8.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.40, < 8.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.11"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2"
    }
  }
}

provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}
