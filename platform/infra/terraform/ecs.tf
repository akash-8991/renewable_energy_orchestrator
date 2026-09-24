# ECS Fargate is used in place of EKS (doc 05/06's AWS reference) —
# documented simplification (SIMPLIFICATIONS.md): same container images,
# same service boundaries, no cluster-ops overhead for a first deployment.
# Moving to EKS later is an infra-only change; no application code depends
# on the orchestrator.

locals {
  services = {
    api              = { port = 8000, cpu = 1024, memory = 2048, public = true }
    optimizer-worker = { port = null, cpu = 2048, memory = 4096, public = false }
    agent-worker     = { port = null, cpu = 1024, memory = 2048, public = false }
    ot-gateway-sim   = { port = 8010, cpu = 512, memory = 1024, public = false }
    edge-simulator   = { port = null, cpu = 512, memory = 1024, public = false }
    export-worker    = { port = null, cpu = 512, memory = 1024, public = false }
  }
}

resource "aws_ecr_repository" "service" {
  for_each             = local.services
  name                 = "${var.project_name}-${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.main.arn
  }
}

resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "service" {
  for_each          = local.services
  name              = "/ecs/${var.project_name}/${each.key}"
  retention_in_days = 90
  kms_key_id        = aws_kms_key.main.arn
}

resource "aws_iam_role" "task_execution" {
  name = "${var.project_name}-task-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name = "${var.project_name}-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

# Least-privilege: app tier can read/write its own secrets prefix and the
# buckets it needs, nothing else (SEC-01..12 least-privilege / SoD).
resource "aws_iam_role_policy" "task_secrets" {
  name = "${var.project_name}-task-secrets"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:CreateSecret", "secretsmanager:PutSecretValue"]
        Resource = ["arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.project_name}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.main.arn]
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.lakehouse.arn, "${aws_s3_bucket.lakehouse.arn}/*",
          aws_s3_bucket.exports.arn, "${aws_s3_bucket.exports.arn}/*",
          aws_s3_bucket.quarantine.arn, "${aws_s3_bucket.quarantine.arn}/*",
          aws_s3_bucket.audit_vault.arn, "${aws_s3_bucket.audit_vault.arn}/*",
        ]
      }
    ]
  })
}

resource "aws_ecs_task_definition" "service" {
  for_each                 = local.services
  family                   = "${var.project_name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = each.key
    image     = "${aws_ecr_repository.service[each.key].repository_url}:${var.container_image_tag}"
    essential = true
    portMappings = each.value.port == null ? [] : [{ containerPort = each.value.port, protocol = "tcp" }]
    environment = [
      { name = "ENVIRONMENT", value = var.environment },
      { name = "SECRETS_PROVIDER", value = "aws" },
      { name = "MODEL_PROVIDER", value = "anthropic" },
      { name = "S3_REGION", value = var.aws_region },
    ]
    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
      { name = "REDIS_URL", valueFrom = aws_secretsmanager_secret.redis_url.arn },
      { name = "JWT_SECRET", valueFrom = aws_secretsmanager_secret.jwt_secret.arn },
      { name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.anthropic_api_key.arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.service[each.key].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = each.key
      }
    }
  }])
}

resource "aws_security_group" "service" {
  for_each    = local.services
  name_prefix = "${var.project_name}-${each.key}-"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = each.value.port != null ? each.value.port : 0
    to_port         = each.value.port != null ? each.value.port : 65535
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id, aws_security_group.app.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_service" "service" {
  for_each        = local.services
  name            = "${var.project_name}-${each.key}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.service[each.key].arn
  desired_count   = each.key == "api" ? 3 : 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = each.key == "ot-gateway-sim" ? aws_subnet.ot_dmz[*].id : aws_subnet.app[*].id
    security_groups  = [aws_security_group.service[each.key].id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = each.key == "api" ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.api[0].arn
      container_name   = each.key
      container_port   = each.value.port
    }
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

resource "aws_lb" "main" {
  name               = "${var.project_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "api" {
  count       = 1
  name        = "${var.project_name}-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"
  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  # certificate_arn must be supplied at apply time (ACM cert for the tenant's domain)
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[0].arn
  }
}

# Database/Redis connection strings, populated from the actual RDS/ElastiCache
# endpoints so app tasks never hardcode them.
resource "aws_secretsmanager_secret" "database_url" {
  name       = "${var.project_name}/db/database-url"
  kms_key_id = aws_kms_key.main.key_id
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql+psycopg2://reo_app:${random_password.db_placeholder.result}@${aws_db_instance.postgres.address}:5432/reo"
  lifecycle {
    ignore_changes = [secret_string] # real password is RDS-managed (manage_master_user_password); rotate via Secrets Manager rotation, not Terraform
  }
}

resource "random_password" "db_placeholder" {
  length  = 1
  special = false
}

resource "aws_secretsmanager_secret" "redis_url" {
  name       = "${var.project_name}/cache/redis-url"
  kms_key_id = aws_kms_key.main.key_id
}

resource "aws_secretsmanager_secret_version" "redis_url" {
  secret_id     = aws_secretsmanager_secret.redis_url.id
  secret_string = "rediss://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0"
}
