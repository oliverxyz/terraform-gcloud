provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

resource "google_service_account" "service_account" {
  account_id   = var.sa_id
  display_name = var.sa_id
}

resource "google_project_iam_member" "project_iam" {
  project  = var.project_id
  for_each = toset(["roles/container.admin", "roles/cloudsql.admin", "roles/pubsub.admin", "roles/storage.admin", "roles/artifactregistry.admin", "roles/compute.networkAdmin", "roles/compute.securityAdmin", "roles/iam.serviceAccountAdmin", "roles/iam.serviceAccountUser", "roles/serviceusage.serviceUsageAdmin", "roles/resourcemanager.projectIamAdmin"])
  role     = each.key
  member   = google_service_account.service_account.member
}

resource "google_service_account_iam_member" "account_iam" {
  service_account_id = google_service_account.service_account.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "user:${var.personal_email}"
}

resource "google_storage_bucket" "terraform_storage" {
  name                        = "${var.project_id}-terraform"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = true
  }
}