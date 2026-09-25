FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY platform/packages/reo_common /app/platform/packages/reo_common
RUN pip install --no-cache-dir -e /app/platform/packages/reo_common

COPY platform/infrastructure/edge-simulator/requirements.txt /app/platform/infrastructure/edge-simulator/requirements.txt
RUN pip install --no-cache-dir -r /app/platform/infrastructure/edge-simulator/requirements.txt

# Shared top-level packages — see backend/Dockerfile's comment.
COPY platform/models /app/platform/models
COPY platform/database /app/platform/database
COPY platform/guardrails /app/platform/guardrails
COPY platform/evaluation /app/platform/evaluation
COPY platform/policy /app/platform/policy
COPY platform/output /app/platform/output

COPY platform/infrastructure/edge-simulator /app/platform/infrastructure/edge-simulator

WORKDIR /app/platform/infrastructure/edge-simulator
ENV PYTHONPATH=/app/platform:/app/platform/infrastructure/edge-simulator
CMD ["python", "simulator.py"]
