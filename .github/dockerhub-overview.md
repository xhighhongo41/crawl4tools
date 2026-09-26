# crawl4tools

Web crawler server for Open WebUI's external web loader and MCP clients, built on
[crawl4ai](https://github.com/unclecode/crawl4ai). This image runs `crawl4server`, a single
process that serves an Open WebUI external web loader on one port and an MCP (Model Context
Protocol) server on another, sharing one headless browser and one concurrency limit between
them.

## Quick start

```sh
mkdir -p downloads
# Save the compose.yaml below as ./compose.yaml
docker compose up -d
curl http://127.0.0.1:8766/health
```

## compose.yaml

<!-- compose.yaml -->
```yaml
# crawl4tools server (crawl4server): Open WebUI external web loader + MCP.
#
# The image is pulled from Docker Hub; `docker compose up -d` fetches
# xhighhongo41/crawl4tools:1.0.0b3. To build locally instead, run
# `docker build -t xhighhongo41/crawl4tools:1.0.0b3 .` first.
#
# Open WebUI: Admin Settings > Web Search > Web Loader Engine = "external",
# URL = http://<host>:8766/crawl (or http://crawl4tools:8766/crawl when
# Open WebUI runs in this same compose project), API key = the value set
# for CRAWL4SERVER_LOADER_API_KEY below.
#
# MCP (Streamable HTTP): http://<host>:8765/mcp — this endpoint has no
# authentication, so do not publish port 8765 beyond a trusted network.
#
# Before first run: mkdir -p downloads (must be writable by uid 1000, the
# "crawl" user the container runs as).

services:
  crawl4tools:
    image: xhighhongo41/crawl4tools:1.0.0b3
    ports:
      - "8766:8766"
      - "8765:8765"
    environment:
      # Every crawl4server CLI option also reads an env var named
      # CRAWL4SERVER_<OPTION_UPPER_SNAKE>; CRAWL4SERVER_HOST below mirrors
      # the --host already passed in the Dockerfile's CMD, kept here as a
      # harmless, real example of the naming scheme.
      CRAWL4SERVER_HOST: "0.0.0.0"
      # CRAWL4SERVER_LOADER_API_KEY: change-me
      # CRAWL4SERVER_PROXY: socks5://host:1080
      # CRAWL4SERVER_CONCURRENCY: "3"
      # CRAWL4SERVER_MAX_URLS: "20"
      # CRAWL4SERVER_TIMEOUT: "60"
      # CRAWL4SERVER_LANG: ja
    volumes:
      # Host directory for downloaded/converted output; create it first
      # (mkdir -p downloads) and make sure uid 1000 can write to it.
      - ./downloads:/data/downloads
    # Chromium needs more than Docker's default 64m /dev/shm to avoid
    # crashing on larger pages.
    shm_size: "1gb"
    restart: unless-stopped
```

## Environment variables

Every option also has a `--flag` and a config-file key; see the full reference in the
[GitHub README](https://github.com/xhighhongo41/crawl4tools#readme).

| Variable | Default | Meaning |
|---|---|---|
| `CRAWL4SERVER_HOST` | `127.0.0.1` | Host to listen on, both ports (use `0.0.0.0` in a container) |
| `CRAWL4SERVER_LOADER_PORT` | `8766` | Web loader port |
| `CRAWL4SERVER_MCP_PORT` | `8765` | MCP Streamable HTTP port |
| `CRAWL4SERVER_LOADER_API_KEY` | *(none)* | Require `Authorization: Bearer KEY` on the web loader (Open WebUI's External Web Loader API Key) |
| `CRAWL4SERVER_PROXY` | *(none)* | `http://`, `https://`, or `socks5://` proxy; credentials as `user:pass@host:port` |
| `CRAWL4SERVER_CONCURRENCY` | `3` | Maximum URLs fetched at once across both ports |
| `CRAWL4SERVER_MAX_URLS` | `20` | Maximum URLs accepted per web loader request or MCP tool call |
| `CRAWL4SERVER_TIMEOUT` | `60` | Default per-URL timeout, in seconds |
| `CRAWL4SERVER_LANG` | `en` | Language of messages the server produces while running (`en` or `ja`) |
| `CRAWL4SERVER_VERBOSE` | off | Verbose logging on stderr |

## Ports and volumes

- `8766`: the Open WebUI external web loader (`POST /crawl`, `GET /health`).
- `8765`: the MCP Streamable HTTP endpoint (`/mcp`); this endpoint has no authentication, so
  do not publish it beyond a trusted network.
- `/data/downloads`: where the MCP `download` tool saves files inside the container; mount a
  host directory here (writable by uid 1000, the `crawl` user the container runs as).

## Links

- [GitHub README](https://github.com/xhighhongo41/crawl4tools#readme)
- [PyPI](https://pypi.org/project/crawl4tools/)
- [Changelog](https://github.com/xhighhongo41/crawl4tools/blob/main/Changelog.md)
- [Issues](https://github.com/xhighhongo41/crawl4tools/issues)

This product includes software developed by UncleCode (https://x.com/unclecode) as part of
the Crawl4AI project (https://github.com/unclecode/crawl4ai). Crawl4AI is licensed under the
Apache License 2.0.
