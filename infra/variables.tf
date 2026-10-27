variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "zone" {
  type = string
}

variable "sa_email" {
  type = string
}

variable "create_router" {
  type    = bool
  default = false
}
