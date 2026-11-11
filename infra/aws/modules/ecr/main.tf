variable "project_name" { type = string }
variable "environment" { type = string }

locals {
  repositories = toset([
    "${var.project_name}-app",
    "${var.project_name}-train-u2026_10_7",
    "${var.project_name}-train-decision",
    "${var.project_name}-serve-vllm",
    "${var.project_name}-serve-sglang",
  ])
}

resource "aws_ecr_repository" "repos" {
  for_each             = local.repositories
  name                 = each.value
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

output "app_repository_url" {
  value = aws_ecr_repository.repos["${var.project_name}-app"].repository_url
}

output "repository_urls" {
  value = { for k, v in aws_ecr_repository.repos : k => v.repository_url }
}
