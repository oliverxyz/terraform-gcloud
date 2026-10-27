terraform {

  required_version = "~> 1.16.0"

  backend "gcs" {
    bucket = "my-experience-512317-terraform"
    prefix = "bootstrap"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.8.0"
    }
  }
}