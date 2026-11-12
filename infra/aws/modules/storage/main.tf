variable "project_name" { type = string }
variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "app_sg_id" { type = string }
variable "db_instance_class" { type = string }
variable "redis_node_type" { type = string }
variable "db_multi_az" {
  type    = bool
  default = true
}
variable "db_skip_final_snapshot" {
  type    = bool
  default = false
}
variable "redis_num_cache_clusters" {
  type    = number
  default = 2
}
variable "s3_force_destroy" {
  type    = bool
  default = false
}

resource "random_id" "suffix" {
  byte_length = 4
}

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "db_password" {
  name = "${var.project_name}-${var.environment}-db-password-${random_id.suffix.hex}"
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = random_password.db.result
}

resource "aws_security_group" "data" {
  name        = "${var.project_name}-${var.environment}-data-sg"
  description = "RDS, ElastiCache and EFS security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.app_sg_id]
  }

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [var.app_sg_id]
  }

  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [var.app_sg_id]
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-${var.environment}-db-subnets"
  subnet_ids = var.private_subnet_ids
}

resource "aws_db_parameter_group" "postgres16" {
  name   = "${var.project_name}-${var.environment}-pg16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "postgres" {
  identifier                = "${var.project_name}-${var.environment}-pg"
  engine                    = "postgres"
  engine_version            = "16"
  instance_class            = var.db_instance_class
  allocated_storage         = 20
  max_allocated_storage     = 100
  storage_encrypted         = true
  db_name                   = "overbae"
  username                  = "overbae"
  password                  = random_password.db.result
  db_subnet_group_name      = aws_db_subnet_group.main.name
  vpc_security_group_ids    = [aws_security_group.data.id]
  parameter_group_name      = aws_db_parameter_group.postgres16.name
  multi_az                  = var.db_multi_az
  skip_final_snapshot       = var.db_skip_final_snapshot
  final_snapshot_identifier = var.db_skip_final_snapshot ? null : "${var.project_name}-${var.environment}-pg-final"
}

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.project_name}-${var.environment}-redis-subnets"
  subnet_ids = var.private_subnet_ids
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = substr("${var.project_name}-${var.environment}-redis", 0, 40)
  description                = "Overmind Celery broker and cache"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  num_cache_clusters         = var.redis_num_cache_clusters
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.redis.name
  security_group_ids         = [aws_security_group.data.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = false
  automatic_failover_enabled = var.redis_num_cache_clusters > 1
}

resource "aws_efs_file_system" "media" {
  creation_token = "${var.project_name}-${var.environment}-media"
  encrypted      = true
}

resource "aws_efs_mount_target" "media" {
  count           = length(var.private_subnet_ids)
  file_system_id  = aws_efs_file_system.media.id
  subnet_id       = var.private_subnet_ids[count.index]
  security_groups = [aws_security_group.data.id]
}

resource "aws_s3_bucket" "sft" {
  bucket        = "${var.project_name}-${var.environment}-sft-${random_id.suffix.hex}"
  force_destroy = var.s3_force_destroy
}

resource "aws_s3_bucket" "weights" {
  bucket        = "${var.project_name}-${var.environment}-weights-${random_id.suffix.hex}"
  force_destroy = var.s3_force_destroy
}

resource "aws_s3_bucket" "archive" {
  bucket        = "${var.project_name}-${var.environment}-checkpoints-${random_id.suffix.hex}"
  force_destroy = var.s3_force_destroy
}

resource "aws_s3_bucket" "static" {
  bucket        = "${var.project_name}-${var.environment}-static-${random_id.suffix.hex}"
  force_destroy = var.s3_force_destroy
}

output "db_endpoint" { value = aws_db_instance.postgres.address }
output "db_password" {
  value     = random_password.db.result
  sensitive = true
}
output "db_secret_arn" { value = aws_secretsmanager_secret.db_password.arn }
output "redis_endpoint" { value = aws_elasticache_replication_group.redis.primary_endpoint_address }
output "efs_file_system_id" { value = aws_efs_file_system.media.id }
output "sft_bucket_name" { value = aws_s3_bucket.sft.bucket }
output "weights_bucket_name" { value = aws_s3_bucket.weights.bucket }
output "archive_bucket_name" { value = aws_s3_bucket.archive.bucket }
output "sft_bucket_arn" { value = aws_s3_bucket.sft.arn }
output "weights_bucket_arn" { value = aws_s3_bucket.weights.arn }
output "archive_bucket_arn" { value = aws_s3_bucket.archive.arn }
