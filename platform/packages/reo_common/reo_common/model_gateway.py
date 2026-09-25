"""Vendor-neutral model gateway (TRD §5/§6, doc 07 "Prompt and Agent Design").

Every specialist agent calls `ModelGateway.complete_structured(...)` with a
Pydantic schema describing exactly the JSON shape it is allowed to return.
The gateway:
  - forces the underlying model to emit that schema (Anthropic tool-forcing),
  - validates the result server-side (a model that "almost" matches is a
    schema failure, not a warning),
  - retries once, then fails closed (`GatewayError`) rather than passing
    through free-form text,
  - logs model/version/tokens/latency for every call (TRD §5 guardrail),
  - never receives raw secrets/credentials — callers redact/tokenise first.

Three implementations: Anthropic (default per user's choice), OpenAI
(portability stub), and Mock (deterministic, zero-cost, used for tests and
whenever no API key is configured so the whole pipeline still runs).
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .config import get_settings

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


class ModelGateway(ABC):
    provider_name: str = "abstract"

    @abstractmethod
    def _raw_call(
        self,
        *,
        system_prompt: str,
        user_content: str,
        json_schema: dict,
        schema_name: str,
        images: list[tuple[bytes, str]] | None = None,
    ) -> str:
        """Return raw JSON text from the underlying provider. `images`, when
        given, is a list of (raw_bytes, media_type) pairs — e.g. rendered
        pages of a scanned document — sent alongside `user_content` for
        providers with vision support (D3: heterogeneous multimodal input)."""

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
        raw = ""
        for attempt in range(2):
            try:
                raw = self._raw_call(
                    system_prompt=full_system_prompt,
                    user_content=user_content,
                    json_schema=schema,
                    schema_name=schema_name,
                    images=images,
                )
                parsed = response_model.model_validate_json(raw)
                latency_ms = (time.perf_counter() - start) * 1000
                record = ModelCallRecord(
                    agent=agent,
                    tenant_id=tenant_id,
                    correlation_id=correlation_id,
                    provider=self.provider_name,
                    model=getattr(self, "model_name", "unknown"),
                    latency_ms=latency_ms,
                    schema_valid=True,
                    retried=retried,
                )
                logger.info("model_call", extra={"record": record.model_dump()})
                return parsed, record
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                retried = True
                continue

        latency_ms = (time.perf_counter() - start) * 1000
        record = ModelCallRecord(
            agent=agent, tenant_id=tenant_id, correlation_id=correlation_id,
            provider=self.provider_name, model=getattr(self, "model_name", "unknown"),
            latency_ms=latency_ms, schema_valid=False, retried=True,
        )
        logger.warning("model_call_failed_closed", extra={"record": record.model_dump(), "raw": raw[:500]})
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
    ) -> str:
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
        for block in resp.content:
            if block.type == "tool_use":
                return json.dumps(block.input)
        raise ValueError("no tool_use block in Anthropic response")


class OpenAIModelGateway(ModelGateway):
    provider_name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        from openai import OpenAI

        self.model_name = model or settings.openai_model
        self._client = OpenAI(api_key=api_key or settings.openai_api_key)

    def _raw_call(
        self, *, system_prompt: str, user_content: str, json_schema: dict, schema_name: str,
        images: list[tuple[bytes, str]] | None = None,
    ) -> str:
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
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
            },
        )
        return resp.choices[0].message.content or "{}"


class MockModelGateway(ModelGateway):
    """Deterministic, zero-cost, schema-valid mock. Used for tests and as the
    zero-config default so the full pipeline is runnable with no API key."""

    provider_name = "mock"
    model_name = "mock-deterministic-v1"

    def _raw_call(
        self, *, system_prompt: str, user_content: str, json_schema: dict, schema_name: str,
        images: list[tuple[bytes, str]] | None = None,
    ) -> str:
        return json.dumps(_mock_instance_for_schema(json_schema))


def _mock_instance_for_schema(schema: dict) -> dict:
    """Build a minimal, schema-valid instance by walking a JSON Schema. Good
    enough for deterministic tests and a keyless demo mode — not a substitute
    for real reasoning, which is why `model_provider=anthropic` is the
    documented production configuration."""

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


def get_model_gateway() -> ModelGateway:
    provider = settings.model_provider
    if provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicModelGateway()
    if provider == "openai" and settings.openai_api_key:
        return OpenAIModelGateway()
    if provider in ("anthropic", "openai"):
        logger.warning("model_provider=%s but no API key configured — falling back to mock gateway", provider)
    return MockModelGateway()
