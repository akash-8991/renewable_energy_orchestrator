#!/usr/bin/env bash
# Sets (or updates) one API-key variable in infrastructure/.env — the file
# docker-compose.yml actually reads (gitignored; see ../.env.example for the
# full list of variables this file can hold).
#
# Usage:
#   ./set_api_key.sh OPENROUTER_API_KEY              # prompts, input hidden
#   ./set_api_key.sh OPENROUTER_API_KEY sk-or-v1-...  # non-interactive (e.g. CI)
#
# Also accepts ANTHROPIC_API_KEY / OPENAI_API_KEY — and for any of the three,
# offers to set MODEL_PROVIDER to match, so the key you just set is actually
# the one the platform uses.
#
# Never hardcode a real key in this file or pass one on the command line in
# a shared/logged shell — prefer the interactive prompt, which isn't echoed
# and isn't recorded in shell history.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/../.env.example"

KEY_NAME="${1:-}"
VALID_KEYS="OPENROUTER_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY"

if [[ -z "$KEY_NAME" ]]; then
  echo "Usage: $0 <OPENROUTER_API_KEY|ANTHROPIC_API_KEY|OPENAI_API_KEY> [value]" >&2
  exit 1
fi

if [[ ! " $VALID_KEYS " == *" $KEY_NAME "* ]]; then
  echo "Error: '$KEY_NAME' isn't one of: $VALID_KEYS" >&2
  echo "(edit infrastructure/.env directly for any other variable)" >&2
  exit 1
fi

if [[ -n "${2:-}" ]]; then
  KEY_VALUE="$2"
else
  read -r -s -p "Paste your $KEY_NAME value (input hidden): " KEY_VALUE
  echo
fi

if [[ -z "$KEY_VALUE" ]]; then
  echo "Error: empty value, nothing written." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "No infrastructure/.env yet — creating it from .env.example."
  cp "$ENV_EXAMPLE" "$ENV_FILE"
fi

# Upsert KEY_NAME=... in place: replace the line if it exists (commented out
# or not), otherwise append it. `#` is escaped in the replacement value since
# it would otherwise start a comment mid-line in the resulting .env.
ESCAPED_VALUE=$(printf '%s' "$KEY_VALUE" | sed -e 's/[&/\]/\\&/g')
if grep -qE "^#?${KEY_NAME}=" "$ENV_FILE"; then
  sed -i.bak -E "s/^#?${KEY_NAME}=.*/${KEY_NAME}=${ESCAPED_VALUE}/" "$ENV_FILE"
  rm -f "$ENV_FILE.bak"
else
  printf '\n%s=%s\n' "$KEY_NAME" "$KEY_VALUE" >> "$ENV_FILE"
fi

MASKED="${KEY_VALUE:0:6}...${KEY_VALUE: -4}"
echo "Set $KEY_NAME=$MASKED in infrastructure/.env"

# Point MODEL_PROVIDER at the key you just set, so it's actually used.
case "$KEY_NAME" in
  OPENROUTER_API_KEY) PROVIDER=openrouter ;;
  ANTHROPIC_API_KEY)  PROVIDER=anthropic ;;
  OPENAI_API_KEY)     PROVIDER=openai ;;
esac

CURRENT_PROVIDER=$(grep -E "^MODEL_PROVIDER=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 || true)
if [[ "$CURRENT_PROVIDER" != "$PROVIDER" ]]; then
  read -r -p "Also set MODEL_PROVIDER=$PROVIDER (currently '${CURRENT_PROVIDER:-unset}')? [Y/n] " REPLY
  if [[ ! "$REPLY" =~ ^[Nn]$ ]]; then
    if grep -qE "^#?MODEL_PROVIDER=" "$ENV_FILE"; then
      sed -i.bak -E "s/^#?MODEL_PROVIDER=.*/MODEL_PROVIDER=${PROVIDER}/" "$ENV_FILE"
      rm -f "$ENV_FILE.bak"
    else
      printf '\nMODEL_PROVIDER=%s\n' "$PROVIDER" >> "$ENV_FILE"
    fi
    echo "Set MODEL_PROVIDER=$PROVIDER in infrastructure/.env"
  fi
fi

echo "Restart the affected containers to pick this up: docker compose up -d --build api agent-worker"
