from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg2://reo:reo@localhost:5432/reo"
    async_database_url: str = "postgresql+asyncpg://reo:reo@localhost:5432/reo"

    # Redis (stream bus + cache)
    redis_url: str = "redis://localhost:6379/0"

    # Object storage (MinIO locally / S3 in AWS — same boto3 client)
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_access_key: str = "reo-minio"
    s3_secret_key: str = "reo-minio-secret"
    s3_region: str = "eu-west-1"
    s3_bucket_exports: str = "reo-exports"
    s3_bucket_lakehouse: str = "reo-lakehouse"
    s3_bucket_quarantine: str = "reo-quarantine"

    # Auth
    jwt_secret: str = "dev-only-change-me-in-every-real-deployment"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60 * 8

    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None

    # Secrets vault (local Fernet master key; AWS deployment uses Secrets Manager/KMS instead)
    vault_master_key: str = "y2N4x8dGm4KxG3nq6z2m3sVfR8k1cQ2s1r7pR9lY1yE="
    secrets_provider: str = "local"  # local | aws

    # Model gateway
    model_provider: str = "mock"  # anthropic | openai | mock
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"

    # Data ingestion
    data_watch_dir: str = "/data"

    # Platform
    environment: str = "local"
    default_tenant_slug: str = "demo-utility"


@lru_cache
def get_settings() -> Settings:
    return Settings()
