# Multi-stage image for the TraceAML API (entrypoint api.app:app).
#
# Build from the repository root so the local traceaml-graph path dependency
# (../../graph) is inside the build context:
#
#   docker build -f services/api/Dockerfile -t traceaml-api .
#
# Run with the ignored score/benchmark artifacts mounted read-only and a
# writable volume for the case review database:
#
#   docker run -p 8000:8000 \
#     -e TRACEAML_FALKORDB_HOST=host.docker.internal \
#     -v "$PWD/artifacts:/app/artifacts:ro" -v traceaml-cases:/app/data \
#     traceaml-api

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.12-slim-bookworm AS builder

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src

# Dependency layer: only lock and project metadata, so source edits reuse it.
COPY graph/pyproject.toml graph/README.md graph/
COPY services/api/pyproject.toml services/api/uv.lock services/api/README.md services/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-install-package traceaml-graph \
        --directory services/api

# Project layer: install the API and graph packages non-editable into the venv.
COPY graph/src graph/src
COPY services/api/src services/api/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --directory services/api


FROM python:3.12-slim-bookworm AS runner

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TRACEAML_REPOSITORY_ROOT=/app \
    TRACEAML_CASE_DB_PATH=/app/data/cases/cases.db

WORKDIR /app

RUN useradd --uid 1000 --create-home appuser \
    && mkdir -p /app/data/cases /app/artifacts \
    && chown -R appuser:appuser /app/data

COPY --from=builder /app/.venv /app/.venv
COPY docs/benchmarks /app/docs/benchmarks

USER appuser

EXPOSE 8000

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
