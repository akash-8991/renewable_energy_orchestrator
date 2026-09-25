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
    # Used only for generating presigned URLs handed to a browser/external
    # client. Inside docker-compose, `s3_endpoint_url` is the container-
    # network hostname ("minio"), which a browser on the host can't reach —
    # this is the host-reachable equivalent. Defaults to s3_endpoint_url
    # when unset (e.g. real AWS S3, where the endpoint is already public).
    s3_public_endpoint_url: str | None = None
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
    model_provider: str = "mock"  # openrouter | anthropic | openai | mock
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    # OpenRouter (https://openrouter.ai) — single OpenAI-compatible endpoint in
    # front of many providers/models. Default provider as of this deployment;
    # see reo_common/model_gateway.py's OpenRouterModelGateway.
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_site_url: str | None = None  # optional, sent as HTTP-Referer for OpenRouter's app attribution
    openrouter_site_name: str = "Renewable Energy Orchestrator"  # sent as X-Title

    # Model call rate limiting (token-conservation guardrail, not just abuse
    # prevention) — a Redis-backed, per-tenant counter enforced in
    # ModelGateway.complete_structured *before* any provider call is made, so
    # a call that would exceed budget spends zero tokens. Applied to every
    # non-mock provider automatically by get_model_gateway(); the mock
    # gateway is exempt (zero-cost, and heavily used in tests with no Redis).
    model_rate_limit_per_minute: int = 20
    model_rate_limit_per_day: int = 2000

    # Data ingestion
    data_watch_dir: str = "/data"

    # OT command gateway (separate service/trust boundary — see docs/ARCHITECTURE.md)
    ot_gateway_url: str = "http://ot-gateway-sim:8010"

    # Platform
    environment: str = "local"
    default_tenant_slug: str = "demo-utility"


@lru_cache
def get_settings() -> Settings:
    return Settings()
