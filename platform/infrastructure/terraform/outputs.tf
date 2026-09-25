output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "ecr_repository_urls" {
  value = { for k, v in aws_ecr_repository.service : k => v.repository_url }
}

output "postgres_endpoint" {
  value     = aws_db_instance.postgres.address
  sensitive = true
}

output "redis_primary_endpoint" {
  value     = aws_elasticache_replication_group.redis.primary_endpoint_address
  sensitive = true
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}
