variable "aws_region" {
  description = "AWS region for the customer VPC deployment"
  type        = string
  default     = "eu-west-1"
}

variable "project_name" {
  description = "Resource name prefix"
  type        = string
  default     = "overmind"
}

variable "environment" {
  description = "Deployment environment label"
  type        = string
  default     = "enterprise"
}

variable "vpc_cidr" {
  description = "CIDR block for the customer VPC"
  type        = string
  default     = "10.42.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones for multi-AZ subnets"
  type        = list(string)
  default     = ["eu-west-1a", "eu-west-1b"]
}

variable "db_instance_class" {
  description = "RDS PostgreSQL instance class"
  type        = string
  default     = "db.r6g.large"
}

variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
  default     = "cache.r6g.large"
}

variable "db_multi_az" {
  description = "Whether the RDS PostgreSQL instance runs in Multi-AZ mode"
  type        = bool
  default     = true
}

variable "db_skip_final_snapshot" {
  description = "Whether to skip final DB snapshot on destroy"
  type        = bool
  default     = false
}

variable "redis_num_cache_clusters" {
  description = "Number of cache clusters in the Redis replication group"
  type        = number
  default     = 2
}

variable "s3_force_destroy" {
  description = "Whether S3 buckets allow force_destroy on teardown"
  type        = bool
  default     = false
}

variable "ecs_backend_desired_count" {
  description = "Desired count for the backend API ECS service"
  type        = number
  default     = 2
}

variable "ecs_worker_desired_count" {
  description = "Desired count for each Celery worker ECS service"
  type        = number
  default     = 1
}

variable "enable_interface_vpc_endpoints" {
  description = "Whether to provision PrivateLink interface VPC endpoints"
  type        = bool
  default     = true
}

variable "hf_token" {
  description = "Hugging Face token for gated base models"
  type        = string
  default     = "hf_placeholder"
}

variable "sandbox_token" {
  description = "Shared bearer token for the isolated Data Workshop sandbox"
  type        = string
  default     = "sandbox_placeholder"
}

variable "image_tag" {
  description = "Container image tag for the Overmind application and workers"
  type        = string
  default     = "latest"
}

variable "systemone_endpoint_name" {
  description = "Optional VPC SageMaker endpoint name for SystemOne (Jev) decisions"
  type        = string
  default     = ""
}
