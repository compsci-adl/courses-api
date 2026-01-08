# First, build the application in the `/app` directory.
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS builder

# Install the project into `/app`
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Omit development dependencies
ENV UV_NO_DEV=1

# Disable Python downloads, because we want to use the system interpreter
# across both images.
ENV UV_PYTHON_DOWNLOADS=0

# Ensure system build tools are available
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config libssl-dev libsqlite3-dev ca-certificates curl && rm -rf /var/lib/apt/lists/*

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Then, use a final image without uv
FROM python:3.13-slim-trixie

# Copy the application from the builder
COPY --from=builder /app /app

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Use `/app` as the working directory
WORKDIR /app

# Run the bot
ENTRYPOINT ["fastapi", "run", "src/server.py"]
