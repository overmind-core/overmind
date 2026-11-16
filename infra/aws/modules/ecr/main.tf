variable "project_name" { type = string }
variable "environment" { type = string }

locals {
  train_stacks = toset([
    "u2026_7",
    "u2026_8_tf510",
    "u2026_8_tf515",
    "u2026_8_gptoss",
    "u2026_8_18",
    "u2026_9_2",
    "u2026_10_7",
    "u2026_10_3_decision",
  ])
  repositories = toset(concat(
    ["${var.project_name}-app", "${var.project_name}-serve-vllm", "${var.project_name}-serve-sglang"],
    [for stack in local.train_stacks : "${var.project_name}-train-${stack}"],
  ))
}

resource "aws_ecr_repository" "repos" {
  for_each             = local.repositories
  name                 = each.value
  image_tag_mutability = "MUTABLE"
  force_delete         = true

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
