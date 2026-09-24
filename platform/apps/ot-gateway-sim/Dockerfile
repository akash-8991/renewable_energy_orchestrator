FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY platform/packages/reo_common /app/platform/packages/reo_common
RUN pip install --no-cache-dir -e /app/platform/packages/reo_common

COPY platform/apps/ot-gateway-sim/requirements.txt /app/platform/apps/ot-gateway-sim/requirements.txt
RUN pip install --no-cache-dir -r /app/platform/apps/ot-gateway-sim/requirements.txt

COPY platform/apps/ot-gateway-sim /app/platform/apps/ot-gateway-sim

WORKDIR /app/platform/apps/ot-gateway-sim
EXPOSE 8010
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
