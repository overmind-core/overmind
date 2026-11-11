# Overmind AWS On-Prem / Customer VPC Stack

Turnkey Terraform stack for deploying Overmind inside a customer-owned AWS VPC with zero third-party model or compute dependencies:

- **`modules/networking`**: Multi-AZ VPC, public/private subnets, security groups, and AWS PrivateLink VPC Endpoints (`s3`, `ecr.api`, `ecr.dkr`, `logs`, `secretsmanager`, `sts`, `bedrock-runtime`, `sagemaker.api`, `sagemaker.runtime`).
- **`modules/storage`**: RDS PostgreSQL 16 (`pgvector`), ElastiCache Redis 7, EFS (`/data/media`), and encrypted S3 buckets (`sft`, `weights`, `checkpoints`, `static`).
- **`modules/ecr`**: Private ECR repositories (`overmind-app`, `overmind-train-u2026_10_7`, `overmind-train-decision`, `overmind-serve-vllm`, `overmind-serve-sglang`).
- **`modules/sagemaker`**: SageMaker Execution IAM Role and KMS encryption key for Training Jobs, Processing Jobs, and Inference Components.
- **`modules/ecs`**: ECS Fargate cluster, ALB, and worker services matching `docker/worker-topology.json`, running with `FINETUNING_BACKEND=sagemaker`, `AWS_BEDROCK_ENABLED=True`, and IAM Task Roles (no static AWS keys).

## Usage

```bash
cd infra/aws
terraform init
terraform plan -var="aws_region=eu-west-1"
terraform apply -var="aws_region=eu-west-1"
```
