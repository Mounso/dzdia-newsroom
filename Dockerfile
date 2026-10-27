# syntax=docker/dockerfile:1
# One image for `api`, `worker` and `migrate` (ADR-004), built in two stages (ADR-006): uv builds
# the virtualenv, the final stage only runs it, without uv or build tools, as a non-root user.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: this layer stays cached until uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
# --no-editable: the project is installed into the venv, so the final stage needs no source tree.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
USER app
EXPOSE 8000
CMD ["python", "-m", "dzdia_newsroom.api"]
