FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY platform/packages/reo_common /app/platform/packages/reo_common
RUN pip install --no-cache-dir -e /app/platform/packages/reo_common

COPY platform/backend/requirements.txt /app/platform/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/platform/backend/requirements.txt

# Shared top-level packages (see docs/ARCHITECTURE.md "Repository layout")
# — copied into every Python service uniformly. They're a few hundred KB
# combined, and which ones a given service imports can change without an
# extra Dockerfile edit every time.
COPY platform/models /app/platform/models
COPY platform/database /app/platform/database
COPY platform/guardrails /app/platform/guardrails
COPY platform/evaluation /app/platform/evaluation
COPY platform/policy /app/platform/policy
COPY platform/output /app/platform/output

COPY platform/backend /app/platform/backend

WORKDIR /app/platform/backend
ENV PYTHONPATH=/app/platform:/app/platform/backend

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
