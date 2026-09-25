terraform {
  required_version = ">= 1.7"
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

  # Uncomment and point at a real backend before any `terraform apply`.
  # backend "s3" {
  #   bucket         = "reo-terraform-state-<account-id>"
  #   key            = "reo/terraform.tfstate"
  #   region         = "eu-west-1"
  #   dynamodb_table = "reo-terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "renewable-energy-orchestrator"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
