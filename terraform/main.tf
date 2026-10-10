resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "container.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "serviceusage.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_container_cluster" "this" {
  project  = var.project_id
  name     = var.cluster_name
  location = var.region

  # Autopilot: Workload Identity, Shielded Nodes and Dataplane V2 (NetworkPolicy) are on by default.
  enable_autopilot    = true
  deletion_protection = false

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = var.authorized_cidr
      display_name = "operator"
    }
  }

  release_channel { channel = "REGULAR" }

  depends_on = [google_project_service.apis]
}

output "cluster" { value = google_container_cluster.this.name }
