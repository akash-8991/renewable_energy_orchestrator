from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg2://reo:reo@localhost:5432/reo"
    async_database_url: str = "postgresql+asyncpg://reo:reo@localhost:5432/reo"

    # Redis (stream bus + cache)
    redis_url: str = "redis://localhost:6379/0"

    # Object storage (SeaweedFS's S3 gateway locally / S3 in AWS — same boto3
    # client either way; SeaweedFS replaced MinIO after MinIO discontinued
    # free Docker image distribution entirely, see docs/SIMPLIFICATIONS.md)
    s3_endpoint_url: str | None = "http://localhost:9000"
    # Used only for generating presigned URLs handed to a browser/external
    # client. Inside docker-compose, `s3_endpoint_url` is the container-
    # network hostname ("seaweedfs"), which a browser on the host can't
    # reach — this is the host-reachable equivalent. Defaults to
    # s3_endpoint_url when unset (e.g. real AWS S3, where the endpoint is
    # already public).
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

    # Real external IdP (production-readiness gap: "authlib OIDC client
    # wired but never tested against a real IdP") — the bundled Keycloak
    # container (infrastructure/docker-compose.yml) is a real, standard,
    # spec-compliant OIDC provider, not a mock. `oidc_issuer` is the
    # container-network base URL the api process calls server-to-server
    # (token exchange, JWKS) — e.g. http://keycloak:8080/realms/reo.
    # `oidc_authorize_url_public` is the host-reachable equivalent the
    # browser is actually redirected to — the same split as
    # S3_ENDPOINT_URL/S3_PUBLIC_ENDPOINT_URL, for the same reason (a
    # container-network hostname means nothing to a browser on the host).
    oidc_issuer: str | None = None
    oidc_authorize_url_public: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    frontend_base_url: str = "http://localhost:5173"

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

    # Model gateway circuit breaker (guardrails/circuit_breaker.py) — process-
    # wide defaults, used the first time a tenant's PlatformSettings row is
    # created (see backend/app/routers/configuration.py). From then on each
    # tenant's own row (editable from the dashboard's Configuration Studio)
    # is the source of truth, not these env defaults.
    model_gateway_circuit_breaker_enabled_default: bool = True
    model_gateway_timeout_seconds_default: float = 30.0
    model_gateway_circuit_failure_threshold_default: int = 3
    model_gateway_circuit_cooldown_seconds_default: int = 60

    # Data ingestion
    data_watch_dir: str = "/data"

    # OT command gateway (separate service/trust boundary — see docs/ARCHITECTURE.md)
    ot_gateway_url: str = "http://ot-gateway-sim:8010"

    # Real external IdP (Configuration Studio's "Identity Provider" panel
    # toggles per-tenant sso_enabled; these three describe the one OIDC
    # provider this deployment federates with — the bundled Keycloak
    # container locally, see infrastructure/docker-compose.yml).
    oidc_redirect_uri: str = "http://localhost:8000/auth/sso/callback"

    # Platform
    environment: str = "local"
    default_tenant_slug: str = "demo-utility"


@lru_cache
def get_settings() -> Settings:
    return Settings()
