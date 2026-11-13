variable "project_name" { type = string }
variable "environment" { type = string }
variable "aws_region" { type = string }
variable "vpc_id" { type = string }
variable "public_subnet_ids" { type = list(string) }
variable "private_subnet_ids" { type = list(string) }
variable "app_sg_id" { type = string }
variable "alb_sg_id" { type = string }
variable "app_image_uri" { type = string }
variable "db_endpoint" { type = string }
variable "db_secret_arn" { type = string }
variable "redis_endpoint" { type = string }
variable "efs_file_system_id" { type = string }
variable "sft_bucket_name" { type = string }
variable "weights_bucket_name" { type = string }
variable "archive_bucket_name" { type = string }
variable "sft_bucket_arn" { type = string }
variable "weights_bucket_arn" { type = string }
variable "archive_bucket_arn" { type = string }
variable "sagemaker_execution_role_arn" { type = string }
variable "systemone_endpoint_name" { type = string }
variable "backend_desired_count" {
  type    = number
  default = 2
}
variable "worker_desired_count" {
  type    = number
  default = 1
}
variable "hf_token" {
  type    = string
  default = "hf_placeholder"
}
variable "sandbox_token" {
  type    = string
  default = "sandbox_placeholder"
}
variable "frontend_url" {
  type    = string
  default = "http://localhost:5173"
}
variable "cors_allowed_origins" {
  type    = string
  default = "http://localhost:5173"
}

locals {
  worker_topology = jsondecode(file("${path.module}/../../../../docker/worker-topology.json"))

  shared_env = [
    { name = "AWS_REGION", value = var.aws_region },
    { name = "FINETUNING_BACKEND", value = "sagemaker" },
    { name = "AWS_BEDROCK_ENABLED", value = "True" },
    { name = "VITE_SELF_HOSTED", value = "true" },
    { name = "POSTGRES_HOST", value = var.db_endpoint },
    { name = "POSTGRES_DB", value = "overbae" },
    { name = "POSTGRES_USER", value = "overbae" },
    { name = "CELERY_BROKER_URL", value = "redis://${var.redis_endpoint}:6379/0" },
    { name = "CACHE_URL", value = "redis://${var.redis_endpoint}:6379/1" },
    { name = "MEDIA_ROOT", value = "/data/media" },
    { name = "AWS_BUCKET_NAME", value = var.archive_bucket_name },
    { name = "AWS_SFT_BUCKET_NAME", value = var.sft_bucket_name },
    { name = "AWS_WEIGHTS_BUCKET_NAME", value = var.weights_bucket_name },
    { name = "SAGEMAKER_EXECUTION_ROLE_ARN", value = var.sagemaker_execution_role_arn },
    { name = "SAGEMAKER_SYSTEMONE_ENDPOINT_NAME", value = var.systemone_endpoint_name },
    { name = "SANDBOX_URL", value = "http://sandbox.overmind.local:8080" },
    { name = "SANDBOX_TOKEN", value = var.sandbox_token },
    { name = "HF_TOKEN", value = var.hf_token },
    { name = "DJANGO_ALLOWED_HOSTS", value = "*" },
    { name = "FRONTEND_URL", value = var.frontend_url },
    { name = "DJANGO_CORS_ALLOWED_ORIGINS", value = var.cors_allowed_origins },
  ]
}

resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-${var.environment}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${var.project_name}-${var.environment}"
  retention_in_days = 30
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.project_name}-${var.environment}-ecs-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.db_secret_arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "${var.project_name}-${var.environment}-ecs-exec-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${var.project_name}-${var.environment}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task_permissions" {
  statement {
    sid = "S3Buckets"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:HeadBucket",
    ]
    resources = [
      var.sft_bucket_arn,
      "${var.sft_bucket_arn}/*",
      var.weights_bucket_arn,
      "${var.weights_bucket_arn}/*",
      var.archive_bucket_arn,
      "${var.archive_bucket_arn}/*",
    ]
  }

  statement {
    sid = "BedrockInvoke"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = ["*"]
  }

  statement {
    sid = "SageMakerControlAndRuntime"
    actions = [
      "sagemaker:CreateTrainingJob",
      "sagemaker:DescribeTrainingJob",
      "sagemaker:StopTrainingJob",
      "sagemaker:CreateProcessingJob",
      "sagemaker:DescribeProcessingJob",
      "sagemaker:StopProcessingJob",
      "sagemaker:CreateModel",
      "sagemaker:CreateEndpointConfig",
      "sagemaker:CreateEndpoint",
      "sagemaker:UpdateEndpoint",
      "sagemaker:DeleteEndpoint",
      "sagemaker:DescribeEndpoint",
      "sagemaker:CreateInferenceComponent",
      "sagemaker:UpdateInferenceComponent",
      "sagemaker:DeleteInferenceComponent",
      "sagemaker:DescribeInferenceComponent",
      "sagemaker:InvokeEndpoint",
      "sagemaker:InvokeEndpointWithResponseStream",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "PassSageMakerRole"
    actions   = ["iam:PassRole"]
    resources = [var.sagemaker_execution_role_arn]
  }

  statement {
    sid = "AutoScalingAndPricing"
    actions = [
      "application-autoscaling:RegisterScalableTarget",
      "application-autoscaling:PutScalingPolicy",
      "application-autoscaling:DeleteScalingPolicy",
      "application-autoscaling:DeregisterScalableTarget",
      "pricing:GetProducts",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "${var.project_name}-${var.environment}-ecs-task-policy"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_permissions.json
}

resource "aws_lb" "app" {
  name               = substr("${var.project_name}-${var.environment}-alb", 0, 32)
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.alb_sg_id]
  subnets            = var.public_subnet_ids
}

resource "aws_lb_target_group" "backend" {
  name        = substr("${var.project_name}-${var.environment}-tg", 0, 32)
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path = "/health"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
}

resource "aws_cloudfront_distribution" "api" {
  enabled         = true
  is_ipv6_enabled = true
  price_class     = "PriceClass_100"

  origin {
    domain_name = aws_lb.app.dns_name
    origin_id   = "alb-backend"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "alb-backend"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
    min_ttl                = 0
    default_ttl            = 0
    max_ttl                = 0

    forwarded_values {
      query_string = true
      headers      = ["*"]
      cookies {
        forward = "all"
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${var.project_name}-${var.environment}-backend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "2048"
  memory                   = "4096"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  volume {
    name = "media"
    efs_volume_configuration {
      file_system_id     = var.efs_file_system_id
      transit_encryption = "ENABLED"
    }
  }

  container_definitions = jsonencode([
    {
      name         = "backend"
      image        = var.app_image_uri
      essential    = true
      portMappings = [{ containerPort = 8000, hostPort = 8000, protocol = "tcp" }]
      environment  = local.shared_env
      secrets      = [{ name = "POSTGRES_PASSWORD", valueFrom = var.db_secret_arn }]
      mountPoints  = [{ sourceVolume = "media", containerPath = "/data/media", readOnly = false }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "backend"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "backend" {
  name            = "${var.project_name}-${var.environment}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = var.backend_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [var.app_sg_id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = 8000
  }
}

resource "aws_ecs_task_definition" "workers" {
  for_each                 = local.worker_topology
  family                   = "${var.project_name}-${var.environment}-${each.key}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "2048"
  memory                   = "4096"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  volume {
    name = "media"
    efs_volume_configuration {
      file_system_id     = var.efs_file_system_id
      transit_encryption = "ENABLED"
    }
  }

  container_definitions = jsonencode([
    {
      name        = each.key
      image       = var.app_image_uri
      essential   = true
      command     = each.value
      environment = local.shared_env
      secrets     = [{ name = "POSTGRES_PASSWORD", valueFrom = var.db_secret_arn }]
      mountPoints = [{ sourceVolume = "media", containerPath = "/data/media", readOnly = false }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = each.key
        }
      }
    }
  ])
}

resource "aws_ecs_service" "workers" {
  for_each        = local.worker_topology
  name            = "${var.project_name}-${var.environment}-${each.key}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.workers[each.key].arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [var.app_sg_id]
  }
}

output "alb_dns_name" {
  value = aws_lb.app.dns_name
}

output "api_https_domain" {
  value = aws_cloudfront_distribution.api.domain_name
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "backend_task_definition_arn" {
  value = aws_ecs_task_definition.backend.arn
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}
