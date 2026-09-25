FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY platform/packages/reo_common /app/platform/packages/reo_common
RUN pip install --no-cache-dir -e /app/platform/packages/reo_common

COPY platform/output/export-worker/requirements.txt /app/platform/output/export-worker/requirements.txt
RUN pip install --no-cache-dir -r /app/platform/output/export-worker/requirements.txt

# Shared top-level packages — see backend/Dockerfile's comment. `output`
# itself (this service, plus output/audit.py) is copied below along with
# the rest, so it isn't repeated here.
COPY platform/models /app/platform/models
COPY platform/database /app/platform/database
COPY platform/guardrails /app/platform/guardrails
COPY platform/evaluation /app/platform/evaluation
COPY platform/policy /app/platform/policy

COPY platform/output /app/platform/output

WORKDIR /app/platform/output/export-worker
ENV PYTHONPATH=/app/platform:/app/platform/output/export-worker
CMD ["python", "worker.py"]
