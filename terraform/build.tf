# Image builds run as a dedicated service account with exactly three permissions: read its own source bucket,
# push to the one registry repository, write build logs. (The default Cloud Build identity is far broader.)

resource "google_service_account" "build" {
  project      = var.project_id
  account_id   = "mcp-build"
  display_name = "MCP image builds (Cloud Build)"
  depends_on   = [google_project_service.apis]
}

resource "google_storage_bucket" "build_src" {
  project                     = var.project_id
  name                        = "${var.project_id}-mcp-build-src"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  lifecycle_rule {
    condition { age = 7 }
    action { type = "Delete" }
  }
}

resource "google_storage_bucket_iam_member" "build_src_read" {
  bucket = google_storage_bucket.build_src.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.build.email}"
}

resource "google_artifact_registry_repository_iam_member" "build_push" {
  project    = var.project_id
  location   = google_artifact_registry_repository.mcp.location
  repository = google_artifact_registry_repository.mcp.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.build.email}"
}

resource "google_project_iam_member" "build_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.build.email}"
}

output "build_sa" { value = google_service_account.build.id }
output "build_bucket" { value = google_storage_bucket.build_src.name }

# Nodes pull images as the project's default compute service account. Grant it read access to this ONE repository.
# (A dedicated, minimal node service account is better, but an Autopilot cluster cannot change it in place.)
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_artifact_registry_repository_iam_member" "nodes_pull" {
  project    = var.project_id
  location   = google_artifact_registry_repository.mcp.location
  repository = google_artifact_registry_repository.mcp.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}
