# RDS PostgreSQL, ElastiCache Redis, S3 buckets, Secrets Manager/KMS.
#
# NOTE (see SIMPLIFICATIONS.md): RDS PostgreSQL does not support the
# timescaledb extension. Two supported production paths: (1) self-manage
# TimescaleDB on EC2/ECS with EBS, or (2) swap the Telemetry/Forecast
# time-series store for Amazon Timestream behind the same repository
# interface used in `platform/apps/api`. This Terraform provisions plain
# RDS PostgreSQL for the operational/canonical schema; time-series storage
# choice is a deployment-time decision, not a code change.

resource "aws_kms_key" "main" {
  description             = "${var.project_name} data-at-rest encryption (tenant-managed key)"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnets"
  subnet_ids = aws_subnet.data[*].id
}

resource "aws_db_instance" "postgres" {
  identifier                  = "${var.project_name}-postgres"
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.db_instance_class
  allocated_storage           = 100
  max_allocated_storage       = 500
  storage_encrypted           = true
  kms_key_id                  = aws_kms_key.main.arn
  db_name                     = "reo"
  username                    = "reo_app"
  manage_master_user_password = true
  multi_az                    = true
  db_subnet_group_name        = aws_db_subnet_group.main.name
  vpc_security_group_ids      = [aws_security_group.data.id]
  backup_retention_period     = 7
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${var.project_name}-postgres-final"
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project_name}-cache-subnets"
  subnet_ids = aws_subnet.data[*].id
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "${var.project_name}-redis"
  description                = "Redis Streams event bus + cache - documented substitute for MSK/Kafka+MQTT (SIMPLIFICATIONS.md)"
  node_type                  = var.redis_node_type
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  engine                     = "redis"
  engine_version             = "7.1"
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = aws_kms_key.main.arn
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.data.id]
}

resource "aws_s3_bucket" "lakehouse" {
  bucket = "${var.project_name}-lakehouse-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "exports" {
  bucket = "${var.project_name}-exports-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "quarantine" {
  bucket = "${var.project_name}-quarantine-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "audit_vault" {
  bucket = "${var.project_name}-audit-vault-${data.aws_caller_identity.current.account_id}"
}

# Audit evidence is write-once where the tenant's retention policy requires
# it (TR-LED-01 / doc 05 §8 "Audit vault").
resource "aws_s3_bucket_object_lock_configuration" "audit_vault" {
  bucket = aws_s3_bucket.audit_vault.id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = 365 * 7 # default 7-year retention per Tenant.retention_years; override per tenant in a real deployment
    }
  }
}

resource "aws_s3_bucket_versioning" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_versioning" "audit_vault" {
  bucket = aws_s3_bucket.audit_vault.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "all" {
  for_each = {
    lakehouse  = aws_s3_bucket.lakehouse.id
    exports    = aws_s3_bucket.exports.id
    quarantine = aws_s3_bucket.quarantine.id
    audit      = aws_s3_bucket.audit_vault.id
  }
  bucket = each.value
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "all" {
  for_each = {
    lakehouse  = aws_s3_bucket.lakehouse.id
    exports    = aws_s3_bucket.exports.id
    quarantine = aws_s3_bucket.quarantine.id
    audit      = aws_s3_bucket.audit_vault.id
  }
  bucket                  = each.value
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "exports" {
  bucket = aws_s3_bucket.exports.id
  rule {
    id     = "expire-export-downloads"
    status = "Enabled"
    filter {}               # applies to all objects in the bucket
    expiration { days = 7 } # matches ExportJob.expires_at short-lived-URL policy
  }
}

data "aws_caller_identity" "current" {}

# Connector Studio credentials (FR-CON-002 / TR-AUTH-01) — one secret per
# CredentialRef row, created at runtime by the api service via boto3
# (see reo_common/secrets.py AwsSecretsManagerProvider), not by Terraform.
# This just grants the app tier's task role permission to do so.
resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name       = "${var.project_name}/model-gateway/anthropic-api-key"
  kms_key_id = aws_kms_key.main.key_id
}

resource "aws_secretsmanager_secret_version" "anthropic_api_key" {
  secret_id     = aws_secretsmanager_secret.anthropic_api_key.id
  secret_string = var.anthropic_api_key
}

# OpenRouter (https://openrouter.ai) is the platform's default model
# provider (MODEL_PROVIDER=openrouter — see reo_common/model_gateway.py's
# OpenRouterModelGateway) as of the OpenRouter migration; Anthropic/OpenAI
# direct remain supported (set var.model_provider) but aren't the default
# path this deployment wires up end to end.
resource "aws_secretsmanager_secret" "openrouter_api_key" {
  name       = "${var.project_name}/model-gateway/openrouter-api-key"
  kms_key_id = aws_kms_key.main.key_id
}

resource "aws_secretsmanager_secret_version" "openrouter_api_key" {
  secret_id     = aws_secretsmanager_secret.openrouter_api_key.id
  secret_string = var.openrouter_api_key
}

resource "aws_secretsmanager_secret" "jwt_secret" {
  name       = "${var.project_name}/auth/jwt-secret"
  kms_key_id = aws_kms_key.main.key_id
}

resource "random_password" "jwt_secret" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret_version" "jwt_secret" {
  secret_id     = aws_secretsmanager_secret.jwt_secret.id
  secret_string = random_password.jwt_secret.result
}
