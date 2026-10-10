variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "europe-west1"
}

variable "cluster_name" {
  type    = string
  default = "mcp-gw-eu"
}

variable "authorized_cidr" {
  description = "Your public IP as a /32: the only range allowed to reach the Kubernetes API."
  type        = string
}
