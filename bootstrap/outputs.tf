output "sa_email" {
  description = "Email of the service account"
  value       = google_service_account.service_account.email
}