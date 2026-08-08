# Build context must be the repo root:
#   docker build -f mcp/deploy/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install uv for fast, reproducible dependency installation
RUN pip install --no-cache-dir uv

# Install dependencies before copying source to leverage Docker layer cache
COPY mcp/pyproject.toml mcp/uv.lock mcp/README.md ./
RUN uv sync --frozen --no-dev

# Copy application source and shared data
# The package is installed rather than copied file-by-file, so the image runs
# exactly what a `pip install` produces — no drift between container and wheel.
COPY mcp/src/ ./src/
RUN uv pip install --no-deps .
# No data bundle: the ACI object model ships inside the niwaki dependency.

# Run as non-root
RUN groupadd --system app && useradd --system --gid app app
RUN chown -R app:app /app
USER app

EXPOSE 8000

CMD ["niwashi-mcp"]
