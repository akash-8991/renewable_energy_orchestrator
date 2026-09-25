FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY platform/packages/reo_common /app/platform/packages/reo_common
RUN pip install --no-cache-dir -e /app/platform/packages/reo_common

COPY platform/policy/requirements.txt /app/platform/policy/requirements.txt
RUN pip install --no-cache-dir -r /app/platform/policy/requirements.txt

# Shared top-level packages — see backend/Dockerfile's comment. `policy`
# itself (the solver/cycle/scenario files) is copied below along with the
# rest, so it isn't repeated here.
COPY platform/models /app/platform/models
COPY platform/database /app/platform/database
COPY platform/guardrails /app/platform/guardrails
COPY platform/evaluation /app/platform/evaluation
COPY platform/output /app/platform/output

COPY platform/policy /app/platform/policy

WORKDIR /app/platform/policy
ENV PYTHONPATH=/app/platform:/app/platform/policy
CMD ["python", "worker.py"]
