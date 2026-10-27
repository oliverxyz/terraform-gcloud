provider "google" {
  project                     = var.project_id
  region                      = var.region
  zone                        = var.zone
  impersonate_service_account = var.sa_email
}

resource "google_project_service" "project_service" {
  project            = var.project_id
  for_each           = toset(["compute.googleapis.com", "container.googleapis.com", "sqladmin.googleapis.com", "servicenetworking.googleapis.com", "pubsub.googleapis.com", "storage.googleapis.com", "artifactregistry.googleapis.com", "iam.googleapis.com"])
  service            = each.key
  disable_on_destroy = false
}

resource "google_compute_network" "network" {
  name                    = "my-network"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.project_service]
}

resource "google_compute_subnetwork" "kubernetes_subnetwork" {
  name          = "kubernetes-subnet"
  region        = var.region
  network       = google_compute_network.network.id
  private_ip_google_access = true
  ip_cidr_range = "192.168.0.0/24"

  secondary_ip_range {
    range_name    = "pods-secondary-range"
    ip_cidr_range = "192.168.2.0/23"
  }

  secondary_ip_range {
    range_name    = "services-secondary-range"
    ip_cidr_range = "192.168.4.0/24"
  }
}