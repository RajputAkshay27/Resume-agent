# --- Stage 1: Build ---
FROM python:3.12-slim AS builder

# Install uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app/agent

# Copy dependency files
COPY agent/pyproject.toml agent/uv.lock ./

# Install dependencies into a virtual environment
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# --- Stage 2: Final ---
FROM python:3.12-slim AS runner

WORKDIR /app/agent

# Copy virtual environment and source code
COPY --from=builder /app/agent/.venv /app/agent/.venv
COPY agent/ .

# Ensure the venv is in the PATH
ENV PATH="/app/agent/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "main.py"]
