# Backend image - serves both the API and the worker (see CMD override in
# docker-compose.yml). Single image, two processes, matches the doc's
# "Orchestration service: Python (FastAPI)" (2.3) with the worker sharing
# the same codebase and dependencies.
FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY alembic.ini ./

RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "geniebot.main:app", "--host", "0.0.0.0", "--port", "8000"]
