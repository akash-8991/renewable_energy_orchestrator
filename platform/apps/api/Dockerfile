FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY platform/packages/reo_common /app/platform/packages/reo_common
RUN pip install --no-cache-dir -e /app/platform/packages/reo_common

COPY platform/apps/api/requirements.txt /app/platform/apps/api/requirements.txt
RUN pip install --no-cache-dir -r /app/platform/apps/api/requirements.txt

COPY platform/db /app/platform/db
COPY platform/apps/api /app/platform/apps/api

WORKDIR /app/platform/apps/api
ENV PYTHONPATH=/app/platform/apps/api

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
