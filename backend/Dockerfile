# --- builder ---------------------------------------------------------------
# Installs only production dependencies (no pytest/ruff/import-linter) into
# an isolated venv, kept separate from the runtime stage to keep the final
# image small and free of build tooling.
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY app ./app
RUN uv venv /opt/venv && \
    UV_PROJECT_ENVIRONMENT=/opt/venv uv pip install --python /opt/venv/bin/python --no-cache -e .

# --- runtime -----------------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY alembic_control_plane ./alembic_control_plane
COPY alembic_control_plane.ini ./

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
