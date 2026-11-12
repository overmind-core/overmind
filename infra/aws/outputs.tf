output "vpc_id" {
  description = "Customer VPC ID"
  value       = module.networking.vpc_id
}

output "alb_dns_name" {
  description = "Application Load Balancer DNS name"
  value       = module.ecs.alb_dns_name
}

output "sft_bucket_name" {
  description = "S3 bucket for SageMaker training datasets and telemetry"
  value       = module.storage.sft_bucket_name
}

output "weights_bucket_name" {
  description = "S3 bucket for base and fine-tuned model weights"
  value       = module.storage.weights_bucket_name
}

output "archive_bucket_name" {
  description = "S3 bucket for checkpoint archives"
  value       = module.storage.archive_bucket_name
}

output "sagemaker_execution_role_arn" {
  description = "IAM Role ARN used by SageMaker Training, Processing, and Endpoints"
  value       = module.sagemaker.execution_role_arn
}

output "ecr_repositories" {
  description = "ECR repository URLs for Overmind images"
  value       = module.ecr.repository_urls
}

output "ecs_cluster_name" {
  description = "ECS Fargate cluster name"
  value       = module.ecs.cluster_name
}

output "backend_task_definition_arn" {
  description = "ECS backend task definition ARN"
  value       = module.ecs.backend_task_definition_arn
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = module.networking.public_subnet_ids
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = module.networking.private_subnet_ids
}

output "app_security_group_id" {
  description = "Application security group ID"
  value       = module.networking.app_security_group_id
}
