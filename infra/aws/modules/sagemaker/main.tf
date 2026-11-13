variable "project_name" { type = string }
variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "sft_bucket_arn" { type = string }
variable "weights_bucket_arn" { type = string }
variable "archive_bucket_arn" { type = string }

resource "aws_kms_key" "sagemaker" {
  description             = "KMS key for Overmind SageMaker training and serving volumes"
  deletion_window_in_days = 14
  enable_key_rotation     = true
}

data "aws_iam_policy_document" "sagemaker_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sagemaker_execution" {
  name               = "${var.project_name}-${var.environment}-sagemaker-exec"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_assume.json
}

data "aws_iam_policy_document" "sagemaker_permissions" {
  statement {
    sid = "S3Access"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
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
    sid = "ECRAccess"
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = ["*"]
  }

  statement {
    sid = "CloudWatchLogs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "cloudwatch:PutMetricData",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sagemaker_execution" {
  name   = "${var.project_name}-${var.environment}-sagemaker-policy"
  role   = aws_iam_role.sagemaker_execution.id
  policy = data.aws_iam_policy_document.sagemaker_permissions.json
}

output "execution_role_arn" {
  value = aws_iam_role.sagemaker_execution.arn
}

output "kms_key_arn" {
  value = aws_kms_key.sagemaker.arn
}
