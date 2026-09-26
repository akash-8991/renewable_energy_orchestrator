"""Vendor-neutral model gateway (TRD §5/§6, doc 07 "Prompt and Agent Design").

Every specialist agent calls `ModelGateway.complete_structured(...)` with a
Pydantic schema describing exactly the JSON shape it is allowed to return.
The gateway:
  - forces the underlying model to emit that schema (Anthropic tool-forcing,
    or OpenAI/OpenRouter-style `response_format: json_schema` strict mode),
  - validates the result server-side (a model that "almost" matches is a
    schema failure, not a warning),
  - retries once, then fails closed (`GatewayError`) rather than passing
    through free-form text,
  - is gated by a per-tenant Redis rate limit *before* any provider call is
    made (see `rate_limiter`/`guardrails.rate_limit`) — a blocked call spends
    zero tokens, it's not a post-hoc throttle,
  - logs model/version/tokens/latency for every call (TRD §5 guardrail),
  - never receives raw secrets/credentials — callers redact/tokenise first.

Four implementations: OpenRouter (default — a single OpenAI-compatible
endpoint in front of many providers/models, https://openrouter.ai),
Anthropic (direct), OpenAI (direct), and Mock (deterministic, zero-cost,
used for tests and whenever no API key is configured so the whole pipeline
still runs).
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from .config import get_settings
from guardrails.circuit_breaker import ModelCallCircuitBreaker
from guardrails.rate_limit import ModelCallRateLimiter

logger = logging.getLogger("reo.model_gateway")
settings = get_settings()

T = TypeVar("T", bound=BaseModel)

UNTRUSTED_OPEN = "<untrusted_data source=\"{source}\">"
UNTRUSTED_CLOSE = "</untrusted_data>"


def wrap_untrusted(content: str, source: str) -> str:
    """Tag externally sourced content so the model treats it as data, never
    instructions (global system prompt rule #1)."""
    return f"{UNTRUSTED_OPEN.format(source=source)}\n{content}\n{UNTRUSTED_CLOSE}"


class GatewayError(RuntimeError):
    """Raised when the model fails to produce a schema-valid response after
    retry — callers must fail closed (downgrade autonomy / mark
    INSUFFICIENT_EVIDENCE), never fabricate a result."""


class RateLimitExceeded(GatewayError):
    """Raised when a call is blocked by the token-conservation rate limiter
    *before* it reaches the provider — a subclass of GatewayError so every
    existing `except GatewayError` call site already fails closed on it
    correctly with no code changes needed, but distinguishable by type/
    message for callers that want to react differently (e.g. not retrying
    immediately, unlike a schema-validity failure)."""


class ModelCallRecord(BaseModel):
    agent: str
    tenant_id: str
    correlation_id: str
    provider: str
    model: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    schema_valid: bool
    retried: bool = False
    error: str | None = None


class RawCallResult(BaseModel):
    raw_json: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ModelGateway(ABC):
    provider_name: str = "abstract"

    #: Set by a caller that wants every call this gateway instance makes —
    #: success or fail-closed failure — persisted somewhere durable (e.g. the
    #: `AgentCallLog` table via `evaluation.observability.persist_call_record`).
    #: Left unset, calls are only ever reached via the Python logger, same as
    #: before this existed. Kept as a plain attribute rather than a
    #: constructor arg so `MockModelGateway()` stays trivial to construct in
    #: the many tests that don't care about persistence.
    on_call_record: "Callable[[ModelCallRecord], None] | None" = None

    #: Set by `get_model_gateway()` for every non-mock provider (a
    #: `guardrails.rate_limit.ModelCallRateLimiter`) — checked at the top of
    #: `complete_structured` before any provider call. Left unset for
    #: `MockModelGateway` (zero-cost, no Redis needed for tests) and left
    #: settable/overridable here for anything constructing a gateway
    #: directly, same pattern as `on_call_record`.
    rate_limiter: "ModelCallRateLimiter | None" = None

    #: Set by a caller with a tenant's `PlatformSettings` row in hand (a
    #: `guardrails.circuit_breaker.ModelCallCircuitBreaker`) — checked before
    #: every provider call, and used to wrap `_raw_call` with a timeout, so a
    #: slow/down provider degrades a decision cycle gracefully instead of
    #: just running long. Left unset by default (no behavior change for
    #: callers that never opt in, and zero overhead for `MockModelGateway`).
    circuit_breaker: "ModelCallCircuitBreaker | None" = None

    @abstractmethod
    def _raw_call(
        self,
        *,
        system_prompt: str,
        user_content: str,
        json_schema: dict,
        schema_name: str,
        images: list[tuple[bytes, str]] | None = None,
    ) -> RawCallResult:
        """Return the raw JSON text from the underlying provider, plus token
        usage when the provider reports it. `images`, when given, is a list
        of (raw_bytes, media_type) pairs — e.g. rendered pages of a scanned
        document — sent alongside `user_content` for providers with vision
        support (D3: heterogeneous multimodal input)."""

    def complete_structured(
        self,
        *,
        agent: str,
        tenant_id: str,
        correlation_id: str,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
        tool_allowlist: list[str] | None = None,
        images: list[tuple[bytes, str]] | None = None,
    ) -> tuple[T, ModelCallRecord]:
        if self.rate_limiter is not None:
            allowed, reason = self.rate_limiter.allow(agent=agent, tenant_id=tenant_id)
            if not allowed:
                record = ModelCallRecord(
                    agent=agent, tenant_id=tenant_id, correlation_id=correlation_id,
                    provider=self.provider_name, model=getattr(self, "model_name", "unknown"),
                    latency_ms=0.0, schema_valid=False, retried=False, error=f"rate_limited: {reason}",
                )
                logger.warning("model_call_rate_limited", extra={"record": record.model_dump()})
                if self.on_call_record:
                    self.on_call_record(record)
                raise RateLimitExceeded(reason or f"{agent}: rate limit exceeded")

        if self.circuit_breaker is not None:
            allowed, reason = self.circuit_breaker.allow()
            if not allowed:
                record = ModelCallRecord(
                    agent=agent, tenant_id=tenant_id, correlation_id=correlation_id,
                    provider=self.provider_name, model=getattr(self, "model_name", "unknown"),
                    latency_ms=0.0, schema_valid=False, retried=False, error=f"circuit_open: {reason}",
                )
                logger.warning("model_call_circuit_open", extra={"record": record.model_dump()})
                if self.on_call_record:
                    self.on_call_record(record)
                raise GatewayError(reason or f"{agent}: circuit breaker open")

        schema = response_model.model_json_schema()
        schema_name = response_model.__name__

        allowlist_note = (
            f"\n\nYou may only reference these tools/services in requested_next_steps: "
            f"{tool_allowlist}." if tool_allowlist else ""
        )
        full_system_prompt = system_prompt + allowlist_note

        start = time.perf_counter()
        retried = False
        last_error: Exception | None = None
        last_result: RawCallResult | None = None
        for attempt in range(2):
            try:
                if self.circuit_breaker is not None:
                    last_result = self.circuit_breaker.call_with_timeout(
                        self._raw_call,
                        system_prompt=full_system_prompt,
                        user_content=user_content,
                        json_schema=schema,
                        schema_name=schema_name,
                        images=images,
                    )
                else:
                    last_result = self._raw_call(
                        system_prompt=full_system_prompt,
                        user_content=user_content,
                        json_schema=schema,
                        schema_name=schema_name,
                        images=images,
                    )
                parsed = response_model.model_validate_json(last_result.raw_json)
                latency_ms = (time.perf_counter() - start) * 1000
                record = ModelCallRecord(
                    agent=agent,
                    tenant_id=tenant_id,
                    correlation_id=correlation_id,
                    provider=self.provider_name,
                    model=getattr(self, "model_name", "unknown"),
                    latency_ms=latency_ms,
                    input_tokens=last_result.input_tokens,
                    output_tokens=last_result.output_tokens,
                    schema_valid=True,
                    retried=retried,
                )
                logger.info("model_call", extra={"record": record.model_dump()})
                if self.on_call_record:
                    self.on_call_record(record)
                if self.circuit_breaker is not None:
                    self.circuit_breaker.record_success()
                return parsed, record
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                retried = True
                if self.circuit_breaker is not None:
                    self.circuit_breaker.record_failure()
                continue
            except Exception as exc:
                # Any provider-level call failure — billing/quota (402),
                # rate limit (429), auth (401), network timeout, 5xx — not
                # just a schema-validation failure. This class's docstring
                # promises callers "retries once, then fails closed
                # (GatewayError) rather than passing through free-form
                # text"; narrowing the catch to only parsing exceptions let
                # a raw provider exception (e.g. openai.APIStatusError)
                # escape uncaught instead, which crashed the entire
                # decision's agent pass on the first agent that hit it and
                # left every later agent — including the explanation agent
                # Decision Centre reads — never even attempted, with
                # nothing committed to Decision.reasoning at all. This is
                # exactly the failure `except GatewayError` in
                # agent/worker.py already exists to handle gracefully per
                # agent; it just never got the chance to.
                last_error = exc
                retried = True
                if self.circuit_breaker is not None:
                    self.circuit_breaker.record_failure()
                continue

        latency_ms = (time.perf_counter() - start) * 1000
        record = ModelCallRecord(
            agent=agent, tenant_id=tenant_id, correlation_id=correlation_id,
            provider=self.provider_name, model=getattr(self, "model_name", "unknown"),
            latency_ms=latency_ms,
            input_tokens=last_result.input_tokens if last_result else None,
            output_tokens=last_result.output_tokens if last_result else None,
            schema_valid=False, retried=True, error=str(last_error)[:2000],
        )
        raw_for_log = last_result.raw_json if last_result else ""
        logger.warning("model_call_failed_closed", extra={"record": record.model_dump(), "raw": raw_for_log[:500]})
        if self.on_call_record:
            self.on_call_record(record)
        raise GatewayError(f"{agent}: schema-invalid response after retry: {last_error}")


class AnthropicModelGateway(ModelGateway):
    provider_name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        import anthropic

        self.model_name = model or settings.anthropic_model
        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key)

    def _raw_call(
        self, *, system_prompt: str, user_content: str, json_schema: dict, schema_name: str,
        images: list[tuple[bytes, str]] | None = None,
    ) -> RawCallResult:
        import base64

        tool = {
            "name": f"emit_{schema_name.lower()}",
            "description": f"Emit the {schema_name} structured result. This is the ONLY way to respond.",
            "input_schema": json_schema,
        }
        content: list[dict] = []
        for data, media_type in images or []:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(data).decode("ascii")},
            })
        content.append({"type": "text", "text": user_content})
        resp = self._client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            system=system_prompt,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": content}],
        )
        usage = getattr(resp, "usage", None)
        for block in resp.content:
            if block.type == "tool_use":
                return RawCallResult(
                    raw_json=json.dumps(block.input),
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                )
        raise ValueError("no tool_use block in Anthropic response")


class OpenAIModelGateway(ModelGateway):
    """OpenAI's own API — also the base class for `OpenRouterModelGateway`,
    since OpenRouter deliberately mirrors the OpenAI chat-completions wire
    format (including `response_format: json_schema` strict mode) regardless
    of which underlying model it's proxying to. Subclasses only need to
    change where the client points and what headers it sends, not the
    request/response handling itself."""

    provider_name = "openai"

    def __init__(
        self, api_key: str | None = None, model: str | None = None,
        base_url: str | None = None, default_headers: dict | None = None,
    ):
        from openai import OpenAI

        self.model_name = model or settings.openai_model
        self._client = OpenAI(
            api_key=api_key or settings.openai_api_key,
            base_url=base_url,
            default_headers=default_headers or None,
        )

    def _raw_call(
        self, *, system_prompt: str, user_content: str, json_schema: dict, schema_name: str,
        images: list[tuple[bytes, str]] | None = None,
    ) -> RawCallResult:
        import base64

        user_content_parts: list[dict] = [{"type": "text", "text": user_content}]
        for data, media_type in images or []:
            b64 = base64.b64encode(data).decode("ascii")
            user_content_parts.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}})
        resp = self._client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content_parts if images else user_content},
            ],
            # Every response here is a small structured-JSON evidence
            # envelope (a handful of findings/fields against a Pydantic
            # schema), never long-form text — but leaving max_tokens unset
            # makes some models/providers default to their max output
            # ceiling (16384 for gpt-4o-mini) regardless of what's actually
            # needed, and some providers (OpenRouter) reserve/charge credit
            # against the *requested* ceiling, not the tokens actually used.
            # Matches the Anthropic gateway's own max_tokens=4096 below.
            max_tokens=4096,
            # strict:True would additionally require OpenAI's own structural
            # rules on every schema (every property in `required`, explicit
            # `additionalProperties: false` on every object, no plain
            # Optional[...] fields) — rules Anthropic's tool-forcing never
            # needed, so the ~15 response models across every agent weren't
            # written to them. Rather than rewrite every schema to satisfy
            # one provider's strict-mode validator, this stays provider-
            # agnostic: the model gets the schema as strong guidance,
            # and our own `model_validate_json(...)` + retry-then-fail-closed
            # (complete_structured, above) is what actually enforces
            # correctness — the same safety net regardless of provider.
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": json_schema, "strict": False},
            },
        )
        usage = getattr(resp, "usage", None)
        return RawCallResult(
            raw_json=resp.choices[0].message.content or "{}",
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )


class OpenRouterModelGateway(OpenAIModelGateway):
    """OpenRouter (https://openrouter.ai) — one OpenAI-wire-format endpoint
    routing to many providers/models by name (e.g. "openai/gpt-4o-mini",
    "anthropic/claude-3.5-sonnet"). This is the default production provider
    for this deployment: point `model` at whichever underlying model is
    wanted without touching any calling code, since every agent only ever
    talks to `ModelGateway.complete_structured(...)`.

    Sends OpenRouter's optional `HTTP-Referer`/`X-Title` attribution
    headers when configured (`OPENROUTER_SITE_URL`/`OPENROUTER_SITE_NAME`) —
    these identify the app on OpenRouter's own leaderboard/logs, not an
    auth mechanism. `response_format: json_schema` strict-mode support
    depends on the specific underlying model OpenRouter routes to; the
    default model (`openai/gpt-4o-mini`) supports it natively."""

    provider_name = "openrouter"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        headers = {"X-Title": settings.openrouter_site_name}
        if settings.openrouter_site_url:
            headers["HTTP-Referer"] = settings.openrouter_site_url
        super().__init__(
            api_key=api_key or settings.openrouter_api_key,
            model=model or settings.openrouter_model,
            base_url="https://openrouter.ai/api/v1",
            default_headers=headers,
        )


class MockModelGateway(ModelGateway):
    """Deterministic, zero-cost, schema-valid mock. Used for tests and as the
    zero-config default so the full pipeline is runnable with no API key."""

    provider_name = "mock"
    model_name = "mock-deterministic-v1"

    def _raw_call(
        self, *, system_prompt: str, user_content: str, json_schema: dict, schema_name: str,
        images: list[tuple[bytes, str]] | None = None,
    ) -> RawCallResult:
        # rough deterministic stand-in for token counts, purely so the
        # observability dashboard has non-null numbers to render in mock/demo
        # mode — never meant to approximate real tokenization.
        return RawCallResult(
            raw_json=json.dumps(_mock_instance_for_schema(json_schema)),
            input_tokens=max(1, len(system_prompt) // 4 + len(user_content) // 4),
            output_tokens=max(1, len(json.dumps(json_schema)) // 8),
        )


def _mock_instance_for_schema(schema: dict) -> dict:
    """Build a minimal, schema-valid instance by walking a JSON Schema. Good
    enough for deterministic tests and a keyless demo mode — not a substitute
    for real reasoning, which is why `model_provider=openrouter` (or another
    real provider) is the documented production configuration."""

    def build(node: dict, defs: dict) -> object:
        if "$ref" in node:
            ref_name = node["$ref"].split("/")[-1]
            return build(defs.get(ref_name, {}), defs)
        t = node.get("type")
        if t == "object" or "properties" in node:
            out = {}
            props = node.get("properties", {})
            required = node.get("required", list(props.keys()))
            for key, sub in props.items():
                if key in required:
                    out[key] = build(sub, defs)
            return out
        if t == "array":
            return []
        if t == "string":
            if "enum" in node:
                return node["enum"][0]
            return "mock-value"
        if t == "number":
            return 0.5
        if t == "integer":
            return 0
        if t == "boolean":
            return False
        return None

    defs = schema.get("$defs", schema.get("definitions", {}))
    return build(schema, defs)  # type: ignore[return-value]


def _attach_rate_limiter(gateway: ModelGateway) -> ModelGateway:
    try:
        import redis as redis_lib

        client = redis_lib.from_url(settings.redis_url, decode_responses=True)
        gateway.rate_limiter = ModelCallRateLimiter(
            client, per_minute=settings.model_rate_limit_per_minute, per_day=settings.model_rate_limit_per_day,
        )
    except Exception:
        # Rate limiting is a token-conservation guardrail, not a hard
        # dependency — if Redis itself can't be reached at construction
        # time, proceed unlimited rather than refuse to run at all (the
        # limiter's own `allow()` already fails open on a per-call Redis
        # error for the same reason).
        logger.warning("could not construct model-call rate limiter (Redis unreachable) — proceeding unlimited", exc_info=True)
    return gateway


def get_model_gateway() -> ModelGateway:
    provider = settings.model_provider
    if provider == "openrouter" and settings.openrouter_api_key:
        return _attach_rate_limiter(OpenRouterModelGateway())
    if provider == "anthropic" and settings.anthropic_api_key:
        return _attach_rate_limiter(AnthropicModelGateway())
    if provider == "openai" and settings.openai_api_key:
        return _attach_rate_limiter(OpenAIModelGateway())
    if provider in ("openrouter", "anthropic", "openai"):
        logger.warning("model_provider=%s but no API key configured — falling back to mock gateway", provider)
    return MockModelGateway()
