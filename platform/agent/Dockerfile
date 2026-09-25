FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY platform/packages/reo_common /app/platform/packages/reo_common
RUN pip install --no-cache-dir -e /app/platform/packages/reo_common

COPY platform/agent/requirements.txt /app/platform/agent/requirements.txt
RUN pip install --no-cache-dir -r /app/platform/agent/requirements.txt

# Shared top-level packages — see backend/Dockerfile's comment.
COPY platform/models /app/platform/models
COPY platform/database /app/platform/database
COPY platform/guardrails /app/platform/guardrails
COPY platform/evaluation /app/platform/evaluation
COPY platform/policy /app/platform/policy
COPY platform/output /app/platform/output

COPY platform/agent /app/platform/agent

WORKDIR /app/platform/agent
ENV PYTHONPATH=/app/platform:/app/platform/agent
CMD ["python", "worker.py"]
