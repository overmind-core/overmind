terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

module "networking" {
  source                         = "./modules/networking"
  project_name                   = var.project_name
  environment                    = var.environment
  aws_region                     = var.aws_region
  vpc_cidr                       = var.vpc_cidr
  availability_zones             = var.availability_zones
  enable_interface_vpc_endpoints = var.enable_interface_vpc_endpoints
}

module "storage" {
  source                   = "./modules/storage"
  project_name             = var.project_name
  environment              = var.environment
  vpc_id                   = module.networking.vpc_id
  private_subnet_ids       = module.networking.private_subnet_ids
  app_sg_id                = module.networking.app_security_group_id
  db_instance_class        = var.db_instance_class
  db_multi_az              = var.db_multi_az
  db_skip_final_snapshot   = var.db_skip_final_snapshot
  redis_node_type          = var.redis_node_type
  redis_num_cache_clusters = var.redis_num_cache_clusters
  s3_force_destroy         = var.s3_force_destroy
}

module "ecr" {
  source       = "./modules/ecr"
  project_name = var.project_name
  environment  = var.environment
}

module "sagemaker" {
  source             = "./modules/sagemaker"
  project_name       = var.project_name
  environment        = var.environment
  vpc_id             = module.networking.vpc_id
  private_subnet_ids = module.networking.private_subnet_ids
  sft_bucket_arn     = module.storage.sft_bucket_arn
  weights_bucket_arn = module.storage.weights_bucket_arn
  archive_bucket_arn = module.storage.archive_bucket_arn
}

module "ecs" {
  source                      = "./modules/ecs"
  project_name                = var.project_name
  environment                 = var.environment
  aws_region                  = var.aws_region
  vpc_id                      = module.networking.vpc_id
  public_subnet_ids           = module.networking.public_subnet_ids
  private_subnet_ids          = module.networking.private_subnet_ids
  app_sg_id                   = module.networking.app_security_group_id
  alb_sg_id                   = module.networking.alb_security_group_id
  app_image_uri               = "${module.ecr.app_repository_url}:${var.image_tag}"
  db_endpoint                 = module.storage.db_endpoint
  db_secret_arn               = module.storage.db_secret_arn
  redis_endpoint              = module.storage.redis_endpoint
  efs_file_system_id          = module.storage.efs_file_system_id
  sft_bucket_name             = module.storage.sft_bucket_name
  weights_bucket_name         = module.storage.weights_bucket_name
  archive_bucket_name         = module.storage.archive_bucket_name
  sft_bucket_arn              = module.storage.sft_bucket_arn
  weights_bucket_arn          = module.storage.weights_bucket_arn
  archive_bucket_arn          = module.storage.archive_bucket_arn
  sagemaker_execution_role_arn = module.sagemaker.execution_role_arn
  systemone_endpoint_name      = var.systemone_endpoint_name
  backend_desired_count        = var.ecs_backend_desired_count
  worker_desired_count         = var.ecs_worker_desired_count
  hf_token                     = var.hf_token
  sandbox_token                = var.sandbox_token
  frontend_url                 = "https://${module.storage.static_website_endpoint}"
  cors_allowed_origins         = "https://${module.storage.static_website_endpoint},http://${module.storage.static_website_endpoint}"
}
