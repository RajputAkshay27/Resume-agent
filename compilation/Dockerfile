# --- Stage 1: Build ---
FROM python:3.12-slim AS builder

# Install uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app/compilation

# Copy dependency files
COPY compilation/pyproject.toml compilation/uv.lock ./

# Install dependencies into a virtual environment
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# --- Stage 2: Final ---
FROM python:3.12-slim AS runner

# Set non-interactive to avoid debconf warnings
ENV DEBIAN_FRONTEND=noninteractive

# Install LaTeX and other dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    # Minimal TeX Live components for PDF generation
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-fonts-recommended \
    texlive-plain-generic \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/compilation

# Copy virtual environment and source code
COPY --from=builder /app/compilation/.venv /app/compilation/.venv
COPY compilation/ .

# Ensure the venv is in the PATH
ENV PATH="/app/compilation/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8002

CMD ["python", "main.py"]
