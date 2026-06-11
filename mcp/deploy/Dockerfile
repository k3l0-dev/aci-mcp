# Build from the repo root:
#   docker build -f mcp/deploy/Dockerfile mcp/
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install uv for fast, reproducible dependency installation
RUN pip install --no-cache-dir uv

# Install dependencies before copying source to leverage Docker layer cache
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application source and registry data
COPY main.py ./
COPY registry/ ./registry/
COPY apic/ ./apic/
COPY data/ ./data/

# Run as non-root
RUN groupadd --system app && useradd --system --gid app app
RUN chown -R app:app /app
USER app

EXPOSE 8000

CMD ["python", "main.py"]
