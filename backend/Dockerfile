# Multi-stage: build deps in a fat builder, copy only the install tree into
# a slim runtime. Drops gcc / build-essential / .git from the final image,
# taking it from ~900MB (single-stage) to ~250MB.

FROM python:3.11-slim AS builder

# build-essential + gcc needed for any wheel-from-source builds in
# requirements.txt (e.g. older pydantic cores, lxml). Removed in runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
# --prefix=/install so we can copy the whole tree cleanly in the next stage.
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11-slim AS runtime

# Runtime deps: libpq (in case we swap sqlite→pg later), curl for healthcheck.
# Keep the list small — every byte shows up in every deploy.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. uvicorn doesn't need root; running as `app` means a
# container escape lands in a low-priv account.
RUN useradd --create-home --shell /bin/false app

WORKDIR /app

# Copy the installed packages from builder.
COPY --from=builder /install /usr/local

# Copy only the app source — .dockerignore keeps tests/.env/data/ out.
COPY app/ ./app/
COPY scripts/ ./scripts/

# DATA_DIR is /app/data — created at runtime by the volume mount, but
# ensure it exists for non-volume local runs.
RUN mkdir -p /app/data && chown -R app:app /app/data

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
