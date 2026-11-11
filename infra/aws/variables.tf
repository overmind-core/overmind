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
