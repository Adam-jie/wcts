FROM --platform=linux/arm64 python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
WORKDIR /app

# The SDK requirement is pinned to an upstream Git revision.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY testfix ./testfix
COPY agentarts/entry.py agentarts/requirements.txt ./agentarts/
RUN python -m pip install --no-cache-dir . -r agentarts/requirements.txt
RUN useradd --create-home --uid 10001 testfix \
    && mkdir -p /app/artifacts \
    && chown testfix:testfix /app/artifacts

USER testfix
EXPOSE 8080
CMD ["python", "agentarts/entry.py"]
