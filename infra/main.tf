provider "google" {
  project                     = var.project_id
  region                      = var.region
  zone                        = var.zone
  impersonate_service_account = var.sa_email
}

resource "google_project_service" "project_service" {
  project            = var.project_id
  for_each           = toset(["compute.googleapis.com", "container.googleapis.com", "sqladmin.googleapis.com", "servicenetworking.googleapis.com", "pubsub.googleapis.com", "storage.googleapis.com", "artifactregistry.googleapis.com", "iam.googleapis.com", "secretmanager.googleapis.com"])
  service            = each.key
  disable_on_destroy = false
}

resource "google_compute_network" "network" {
  name                    = "my-network"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.project_service]
}

resource "google_compute_subnetwork" "kubernetes_subnetwork" {
  name                     = "kubernetes-subnet"
  region                   = var.region
  network                  = google_compute_network.network.id
  private_ip_google_access = true
  ip_cidr_range            = ".0.0/24"192.168

  secondary_ip_range {
    range_name    = "pods-secondary-range"
    ip_cidr_range = "192.168.2.0/23"
  }

  secondary_ip_range {
    range_name    = "services-secondary-range"
    ip_cidr_range = "192.168.4.0/24"
  }
}

resource "google_compute_router" "compute_router" {
  count   = var.create_router ? 1 : 0
  region  = var.region
  name    = "my-router"
  network = google_compute_network.network.id
}

resource "google_compute_router_nat" "compute_router_nat" {
  count = var.create_router ? 1 : 0

  router                             = google_compute_router.compute_router[0].name
  name                               = "my-router-nat"
  region                             = var.region
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = google_compute_subnetwork.kubernetes_subnetwork.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }


  nat_ip_allocate_option = "AUTO_ONLY"
}

resource "google_pubsub_topic" "topic" {
  name = "my-topic"
}

resource "google_pubsub_subscription" "subscription" {
  name  = "my-subscription"
  topic = google_pubsub_topic.topic.id
  expiration_policy {
    ttl = ""
  }
}

resource "google_storage_bucket" "main_storage" {
  name                        = "${var.project_id}-app-files"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = false
  }
  soft_delete_policy {
    retention_duration_seconds = 0
  }
}

resource "google_artifact_registry_repository" "registry_repository" {
  repository_id          = "my-repository"
  location               = var.region
  cleanup_policy_dry_run = false
  format                 = "DOCKER"
  vulnerability_scanning_config {
    enablement_config = "DISABLED"
  }
  cleanup_policies {
    id     = "keep-last-three"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "delete-all"
    action = "DELETE"
    condition {
      tag_state = "ANY"
    }

  }
}