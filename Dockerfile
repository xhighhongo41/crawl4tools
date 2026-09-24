# crawl4tools server image
#
# Build:   docker compose up --build
# Run:     docker compose up -d
#
# The image bundles Playwright's Chromium build (and its OS dependencies),
# so no separate browser install step is needed at runtime. The final
# process runs as the unprivileged "crawl" user, not root.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CRAWL4_AI_BASE_DIRECTORY=/data \
    HOME=/data

# Pinned uv binary, copied straight from the official distroless image.
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

WORKDIR /app

# Copy only what hatchling's wheel build and `uv sync` need, so source-only
# edits don't bust the dependency layer's cache.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src

# Install the locked, non-dev dependency set plus the project itself
# (non-editable, so /app/src is not required at runtime) into /app/.venv,
# built against the image's own interpreter.
RUN UV_PYTHON=/usr/local/bin/python3 uv sync --frozen --no-dev --no-editable

# Install Chromium and the OS libraries it needs (requires root; apt-get is
# invoked by `--with-deps`). Drop the apt cache afterwards to keep the
# layer small.
RUN /app/.venv/bin/playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

# Non-root runtime user; its home doubles as crawl4ai's cache/DB directory
# (CRAWL4_AI_BASE_DIRECTORY=/data above) and holds the download directory.
RUN useradd --create-home --home-dir /data --uid 1000 crawl && \
    mkdir -p /data/downloads && \
    chown -R crawl:crawl /data /ms-playwright

USER crawl
WORKDIR /data

ENV PATH=/app/.venv/bin:$PATH

# Open WebUI external web loader (8766) and MCP Streamable HTTP (8765).
EXPOSE 8766 8765

# Assumes the default loader port (8766); override the healthcheck if
# CRAWL4SERVER_LOADER_PORT is changed. No curl in the slim base image, so
# the check is done with Python's stdlib instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8766/health', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["crawl4server"]
CMD ["--host", "0.0.0.0", "--download-dir", "/data/downloads"]
