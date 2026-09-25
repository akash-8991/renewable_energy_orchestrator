variable "aws_region" {
  description = "Primary EU region (doc 06 §2: primary EU region, 3 AZ)"
  type        = string
  default     = "eu-west-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "project_name" {
  type    = string
  default = "reo"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "az_count" {
  description = "doc 06 §2: EKS across 3 AZs — this Terraform uses ECS Fargate instead of EKS (documented simplification, see platform/docs/SIMPLIFICATIONS.md) but keeps the same 3-AZ resilience target."
  type        = number
  default     = 3
}

variable "db_instance_class" {
  description = "RDS PostgreSQL instance class. NOTE: RDS does not support the timescaledb extension — see SIMPLIFICATIONS.md for the two supported production paths (self-managed Timescale, or an Amazon Timestream adapter)."
  type        = string
  default     = "db.r6g.large"
}

variable "redis_node_type" {
  type    = string
  default = "cache.r6g.large"
}

variable "container_image_tag" {
  description = "Image tag pushed to each service's ECR repo by CI"
  type        = string
  default     = "latest"
}

variable "anthropic_api_key" {
  description = "Set via -var or TF_VAR_anthropic_api_key at apply time; stored in Secrets Manager, never in state as plaintext output"
  type        = string
  default     = ""
  sensitive   = true
}

variable "enable_secondary_region_dr" {
  description = "doc 04 §9: optional secondary EU region for DR — off by default to keep cost down until a client engagement scopes RTO/RPO"
  type        = bool
  default     = false
}
