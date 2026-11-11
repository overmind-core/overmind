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
  name               = "${var.project_name}-${var.environment}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.alb_sg_id]
  subnets            = var.public_subnet_ids
}

resource "aws_lb_target_group" "backend" {
  name        = "${var.project_name}-${var.environment}-api-tg"
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
  desired_count   = 2
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
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [var.app_sg_id]
  }
}

output "alb_dns_name" {
  value = aws_lb.app.dns_name
}
