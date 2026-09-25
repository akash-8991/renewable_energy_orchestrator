FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY platform/packages/reo_common /app/platform/packages/reo_common
RUN pip install --no-cache-dir -e /app/platform/packages/reo_common

COPY platform/guardrails/ot-gateway-sim/requirements.txt /app/platform/guardrails/ot-gateway-sim/requirements.txt
RUN pip install --no-cache-dir -r /app/platform/guardrails/ot-gateway-sim/requirements.txt

# Shared top-level packages — see backend/Dockerfile's comment. `guardrails`
# itself (this service, plus ssrf.py/rate_limit.py/pii.py/validator.py) is
# copied below along with the rest, so it isn't repeated here.
COPY platform/models /app/platform/models
COPY platform/database /app/platform/database
COPY platform/evaluation /app/platform/evaluation
COPY platform/policy /app/platform/policy
COPY platform/output /app/platform/output

COPY platform/guardrails /app/platform/guardrails

WORKDIR /app/platform/guardrails/ot-gateway-sim
ENV PYTHONPATH=/app/platform:/app/platform/guardrails/ot-gateway-sim
EXPOSE 8010
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
