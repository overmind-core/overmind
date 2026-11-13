#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="${ROOT_DIR}/infra/aws"
FRONTEND_DIR="${ROOT_DIR}/frontend"
STATE_DIR="${STATE_DIR:-${ROOT_DIR}/scratch/aws-byoc-test}"
STATE_FILE="${STATE_FILE:-${STATE_DIR}/terraform.tfstate}"

export AWS_PROFILE="${AWS_PROFILE:-administrator}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
PROJECT_NAME="${PROJECT_NAME:-overmind-byoc-test}"
ENVIRONMENT="${ENVIRONMENT:-sandbox}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

deploy() {
  mkdir -p "${STATE_DIR}"

  local account_id
  account_id="$(aws sts get-caller-identity --profile "${AWS_PROFILE}" --query Account --output text)"
  echo "==> Deploying ${PROJECT_NAME}-${ENVIRONMENT} to account ${account_id} in ${AWS_REGION} (profile: ${AWS_PROFILE})"

  local tf_vars=(
    -var="aws_region=${AWS_REGION}"
    -var="project_name=${PROJECT_NAME}"
    -var="environment=${ENVIRONMENT}"
    -var="availability_zones=[\"${AWS_REGION}a\",\"${AWS_REGION}b\"]"
    -var="db_instance_class=db.t4g.medium"
    -var="db_multi_az=false"
    -var="db_skip_final_snapshot=true"
    -var="redis_node_type=cache.t4g.micro"
    -var="redis_num_cache_clusters=1"
    -var="s3_force_destroy=true"
    -var="enable_interface_vpc_endpoints=true"
    -var="image_tag=${IMAGE_TAG}"
  )

  echo "==> [1/5] Provisioning customer VPC, RDS, Redis, EFS, S3 (including static website bucket), CloudFront HTTPS endpoints, ECR, and IAM..."
  terraform -chdir="${TF_DIR}" init -input=false
  terraform -chdir="${TF_DIR}" apply -auto-approve -state="${STATE_FILE}" \
    "${tf_vars[@]}" \
    -var="ecs_backend_desired_count=1" \
    -var="ecs_worker_desired_count=1"

  echo "==> [2/5] Building and pushing linux/amd64 app image to ECR..."
  aws ecr get-login-password --profile "${AWS_PROFILE}" --region "${AWS_REGION}" | \
    docker login --username AWS --password-stdin "${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com"

  local app_repo="${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com/${PROJECT_NAME}-app:${IMAGE_TAG}"
  docker buildx build --platform linux/amd64 -t "${app_repo}" --push "${ROOT_DIR}"

  local cluster_name private_subnets app_sg backend_https_url static_bucket frontend_url
  cluster_name="$(terraform -chdir="${TF_DIR}" output -state="${STATE_FILE}" -raw ecs_cluster_name)"
  private_subnets="$(terraform -chdir="${TF_DIR}" output -state="${STATE_FILE}" -json private_subnet_ids | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)))')"
  app_sg="$(terraform -chdir="${TF_DIR}" output -state="${STATE_FILE}" -raw app_security_group_id)"
  backend_https_url="$(terraform -chdir="${TF_DIR}" output -state="${STATE_FILE}" -raw backend_https_url)"
  static_bucket="$(terraform -chdir="${TF_DIR}" output -state="${STATE_FILE}" -raw static_bucket_name)"
  frontend_url="$(terraform -chdir="${TF_DIR}" output -state="${STATE_FILE}" -raw frontend_website_url)"

  echo "==> [3/5] Running database migrations on ECS Fargate in private subnet..."
  local task_arn
  task_arn="$(aws ecs run-task \
    --profile "${AWS_PROFILE}" \
    --region "${AWS_REGION}" \
    --cluster "${cluster_name}" \
    --task-definition "${PROJECT_NAME}-${ENVIRONMENT}-backend" \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[${private_subnets}],securityGroups=[${app_sg}],assignPublicIp=DISABLED}" \
    --overrides '{"containerOverrides":[{"name":"backend","command":["python","manage.py","migrate","--noinput"]}]}' \
    --query 'tasks[0].taskArn' --output text)"

  echo "    Waiting for migration task ${task_arn}..."
  aws ecs wait tasks-stopped \
    --profile "${AWS_PROFILE}" \
    --region "${AWS_REGION}" \
    --cluster "${cluster_name}" \
    --tasks "${task_arn}"

  local exit_code
  exit_code="$(aws ecs describe-tasks \
    --profile "${AWS_PROFILE}" \
    --region "${AWS_REGION}" \
    --cluster "${cluster_name}" \
    --tasks "${task_arn}" \
    --query 'tasks[0].containers[0].exitCode' --output text)"
  if [[ "${exit_code}" != "0" ]]; then
    echo "ERROR: Migration task exited with code ${exit_code}" >&2
    exit 1
  fi

  echo "==> [4/5] Building Console UI (VITE_SELF_HOSTED=true, VITE_API_BASE_URL=${backend_https_url}) and syncing to S3 (${static_bucket})..."
  (
    cd "${FRONTEND_DIR}"
    bun install --frozen-lockfile
    VITE_API_BASE_URL="${backend_https_url}" \
    VITE_SELF_HOSTED="true" \
    VITE_CLERK_PUBLISHABLE_KEY="" \
      bun run build
  )
  aws s3 sync "${FRONTEND_DIR}/dist" "s3://${static_bucket}/" \
    --profile "${AWS_PROFILE}" \
    --region "${AWS_REGION}" \
    --delete

  local cf_id
  cf_id="$(terraform -chdir="${TF_DIR}" output -state="${STATE_FILE}" -raw static_cloudfront_id 2>/dev/null || true)"
  if [[ -n "${cf_id}" ]]; then
    aws cloudfront create-invalidation \
      --profile "${AWS_PROFILE}" \
      --distribution-id "${cf_id}" \
      --paths "/*" >/dev/null
  fi

  echo "==> [5/5] Starting ECS Fargate backend and worker services..."
  terraform -chdir="${TF_DIR}" apply -auto-approve -state="${STATE_FILE}" \
    "${tf_vars[@]}" \
    -var="ecs_backend_desired_count=1" \
    -var="ecs_worker_desired_count=1"

  echo "==> Waiting for ${backend_https_url}/health and ${frontend_url} to become healthy..."
  for _ in $(seq 1 40); do
    if curl -fsSL "${backend_https_url}/health" >/dev/null 2>&1 && curl -fsSL "${frontend_url}" >/dev/null 2>&1; then
      echo "==> Deployment ready!"
      echo "    Console UI (HTTPS):  ${frontend_url}"
      echo "    Backend API (HTTPS): ${backend_https_url}"
      echo "    Health Check:        $(curl -fsSL "${backend_https_url}/health")"
      echo "    ECS Cluster:         ${cluster_name}"
      return 0
    fi
    sleep 10
  done

  echo "WARNING: Health check did not return 200 within timeout. Check ECS service events in ${cluster_name}."
}

destroy() {
  echo "==> Destroying ${PROJECT_NAME}-${ENVIRONMENT} in ${AWS_REGION} (profile: ${AWS_PROFILE})"
  if [[ ! -f "${STATE_FILE}" ]]; then
    echo "No state file found at ${STATE_FILE}."
    exit 1
  fi

  terraform -chdir="${TF_DIR}" destroy -auto-approve -state="${STATE_FILE}" \
    -var="aws_region=${AWS_REGION}" \
    -var="project_name=${PROJECT_NAME}" \
    -var="environment=${ENVIRONMENT}" \
    -var="availability_zones=[\"${AWS_REGION}a\",\"${AWS_REGION}b\"]" \
    -var="db_instance_class=db.t4g.medium" \
    -var="db_multi_az=false" \
    -var="db_skip_final_snapshot=true" \
    -var="redis_node_type=cache.t4g.micro" \
    -var="redis_num_cache_clusters=1" \
    -var="s3_force_destroy=true" \
    -var="enable_interface_vpc_endpoints=true" \
    -var="ecs_backend_desired_count=0" \
    -var="ecs_worker_desired_count=0" \
    -var="image_tag=${IMAGE_TAG}"

  echo "==> Destroy complete."
}

case "${1:-}" in
  deploy)
    deploy
    ;;
  destroy)
    destroy
    ;;
  *)
    echo "Usage: $0 {deploy|destroy}" >&2
    exit 1
    ;;
esac
