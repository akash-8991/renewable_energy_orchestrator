FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY platform/packages/reo_common /app/platform/packages/reo_common
RUN pip install --no-cache-dir -e /app/platform/packages/reo_common

COPY platform/apps/edge-simulator/requirements.txt /app/platform/apps/edge-simulator/requirements.txt
RUN pip install --no-cache-dir -r /app/platform/apps/edge-simulator/requirements.txt

COPY platform/apps/edge-simulator /app/platform/apps/edge-simulator

WORKDIR /app/platform/apps/edge-simulator
CMD ["python", "simulator.py"]
