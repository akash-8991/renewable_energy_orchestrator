"""PII detection/redaction (FR-DI-007, FR-060/061, doc 07 rule #6: "never
reproduce personal data/credentials; use redacted/tokenised values").

Regex-based detector for the common identifiable fields this platform could
plausibly encounter in free-text ingestion (maintenance notes, uploaded
documents, connector payloads) — email, phone, national ID-shaped numbers,
IBAN-shaped account numbers, and generic long-digit sequences. Not a
substitute for a full NER-based DPIA-grade classifier in a real deployment,
but real enough to (a) block PII from ever reaching a model-gateway call
unredacted and (b) demonstrate the redaction/tokenisation split the spec
requires: redacted text is what agents/analytics see; the reversible token
map is stored separately (`PiiVault`) for an authorised, audited reveal.
"""

from __future__ import annotations

import hashlib
import re

_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "phone": re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{3,4}(?!\d)"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    "long_digit_sequence": re.compile(r"\b\d{9,}\b"),  # national ID / passport / card-shaped numbers
}


def _token_for(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"[REDACTED:{kind}:{digest}]"


def detect_pii(text: str) -> list[dict]:
    findings = []
    for kind, pattern in _PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append({"kind": kind, "value": match.group(0), "span": match.span()})
    return findings


def redact(text: str) -> tuple[str, dict[str, str]]:
    """Returns (redacted_text, token_map) where token_map maps each
    generated token back to the original value — callers persist the
    token_map only in a restricted store (e.g. CredentialRef-style vault
    row), never alongside the redacted text itself."""
    redacted = text
    token_map: dict[str, str] = {}
    findings = detect_pii(text)
    # replace longest matches first so overlapping patterns don't corrupt spans
    for finding in sorted(findings, key=lambda f: -len(f["value"])):
        token = _token_for(finding["kind"], finding["value"])
        if finding["value"] in redacted:
            redacted = redacted.replace(finding["value"], token)
            token_map[token] = finding["value"]
    return redacted, token_map


def contains_pii(text: str) -> bool:
    return any(pattern.search(text) for pattern in _PATTERNS.values())
