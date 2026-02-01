FROM python:3.11-slim

WORKDIR /app

# System deps for Playwright (will be installed by --with-deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
 && python -m playwright install --with-deps chromium

COPY app /app/app

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
ENV DB_PATH=/app/data/workbench.db
ENV WORKSPACES_DIR=/app/data/workspaces
ENV ARTIFACTS_DIR=/app/data/artifacts
ENV LOGS_DIR=/app/data/logs

EXPOSE 8787

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
