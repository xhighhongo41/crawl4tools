# crawl4tools

[日本語版 README はこちら](https://github.com/xhighhongo41/crawl4tools/blob/main/README_ja.md)

## What is crawl4tools

crawl4tools is a web crawler built on top of the [crawl4ai](https://github.com/unclecode/crawl4ai) library. It is designed to serve three purposes:

1. An HTTP server, `crawl4server`, that works directly as an **Open WebUI external web loader**, returning the body of a URL as Markdown. On the Open WebUI side, this only requires setting Web Loader Engine to `external` and the External Web Loader URL in the admin UI (or the equivalent `WEB_LOADER_ENGINE=external` and `EXTERNAL_WEB_LOADER_URL` environment variables).
2. An **MCP server** that lets AI agents such as Claude Code fetch the **full content** of a web page, rather than a summary.
3. A **local CLI** that downloads a given URL (or multiple URLs at once) as Markdown or other formats.

## Status

**Beta.** This release (1.0.0b3) is a beta of crawl4tools, published on [PyPI](https://pypi.org/project/crawl4tools/) and [Docker Hub](https://hub.docker.com/r/xhighhongo41/crawl4tools). It provides the local CLI `crawl4cli`, the MCP server `crawl4mcp`, and the combined Open WebUI web loader + MCP server `crawl4server`, with a Dockerfile and compose file. All three commands can show their messages in English or Japanese. Feedback from real use is welcome on the [Issues page](https://github.com/xhighhongo41/crawl4tools/issues); 1.0.0 will follow once this beta has been tested.

## Features

Available now (CLI):

- Download one or more URLs as Markdown, HTML, PDF, screenshot (PNG), MHTML, or the raw source
- PDFs are transcribed to Markdown; images and other non-HTML files are saved as they are
- Clear error messages for HTTP errors, unknown hosts, refused connections, timeouts, and a missing browser
- Downloading through an HTTP/HTTPS/SOCKS5 proxy, with a single retry over a direct connection when the proxy itself fails or breaks the result — including when a proxy that intercepts TLS ("SSL bumping") shows a TLS error, an error page from the proxy itself, or a bot challenge seen through it; a refusal by the proxy itself is never bypassed
- Messages in English or Japanese (see [Language of messages](#language-of-messages))

Available now (MCP server):

- Two tools: `fetch` returns pages directly to the client as Markdown (default), HTML, or a PNG screenshot (images come back as images, PDFs are transcribed to Markdown); `download` saves pages in any format (Markdown, HTML, PDF, screenshot, MHTML, or raw) into a directory on the server and returns the paths
- Several URLs per call (default limit 20), fetched concurrently under a server-wide limit (default 3), sharing one headless browser
- stdio (default) and Streamable HTTP transports
- Proxy support with a direct-connection fallback, configured on the server side, including for a proxy that intercepts TLS
- Settings via command-line options, `CRAWL4MCP_*` environment variables, or a YAML/JSON config file
- Messages in English or Japanese (see [Language of messages](#language-of-messages))

Available now (Open WebUI web loader, `crawl4server`):

- `POST /crawl` accepts `{"urls": [...]}` and returns Markdown plus metadata (source, URL, title, status code, content type) for each page Open WebUI can use; invalid or failing URLs are simply left out of the response instead of failing the whole batch
- Serves the same MCP endpoint as `crawl4mcp` on a second port, sharing one headless browser and one concurrency limit with the web loader
- `GET /health` for liveness checks, and an optional bearer API key on the web loader endpoint
- Settings via command-line options, `CRAWL4SERVER_*` environment variables, or a YAML/JSON config file
- Dockerfile and compose file for running the server in a container
- Messages in English or Japanese (see [Language of messages](#language-of-messages))

Planned:

- Authentication for the MCP endpoint

## Installation

The CLI and the MCP server need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install --with-executables-from playwright crawl4tools
playwright install chromium   # downloads the headless browser (once)
```

This installs `crawl4cli`, `crawl4mcp`, and `crawl4server` from [PyPI](https://pypi.org/project/crawl4tools/).

To install the development version instead, point `uv` at the git repository:

```sh
uv tool install --with-executables-from playwright git+https://github.com/xhighhongo41/crawl4tools
```

The browser is stored in Playwright's cache directory (for example `~/Library/Caches/ms-playwright` on macOS). crawl4ai also creates a `~/.crawl4ai` directory for its own data.

To run `crawl4server` in a container instead, see [Docker](#docker) below.

## Usage

```sh
crawl4cli https://example.com/                    # Markdown to stdout
crawl4cli -o page.md https://example.com/         # save to a file
crawl4cli -d out/ URL1 URL2 URL3                  # several URLs into a directory
crawl4cli -f screenshot https://example.com/      # saves example.com.png
crawl4cli --proxy http://proxy.local:8080 URL     # through a proxy
```

Every option can also be set with an environment variable named `CRAWL4CLI_` followed by the option's name in upper case, dashes replaced by underscores; the option always wins over the variable. The standard `HTTP_PROXY`/`HTTPS_PROXY` variables are not used.

| Option | Environment variable | Default | Meaning |
|---|---|---|---|
| `-f, --format` | `CRAWL4CLI_FORMAT` | `markdown` | `markdown`, `html`, `pdf`, `screenshot`, `mhtml`, or `raw` |
| `-o, --output FILE` | `CRAWL4CLI_OUTPUT` | *(none, prints to stdout)* | Save a single URL to FILE instead of stdout |
| `-d, --output-dir DIR` | `CRAWL4CLI_OUTPUT_DIR` | `.` | Directory for several URLs and binary formats |
| `--proxy URL` | `CRAWL4CLI_PROXY` | *(none)* | `http://`, `https://`, or `socks5://` proxy; credentials as `user:pass@host:port` |
| `--fallback/--no-fallback` | `CRAWL4CLI_FALLBACK` | `--fallback` (on) | Retry over a direct connection when the proxy itself appears to be at fault |
| `-j, --concurrency N` | `CRAWL4CLI_CONCURRENCY` | `3` | URLs fetched at once |
| `--timeout SECONDS` | `CRAWL4CLI_TIMEOUT` | `60` | Page load timeout per URL |
| `--citations` | `CRAWL4CLI_CITATIONS` | off | Turn links into numbered references listed at the end |
| `--fit` | `CRAWL4CLI_FIT` | off | Keep only the main content (drops menus, footers, and the like); falls back to the full page if nothing is left |
| `--no-links` | `CRAWL4CLI_NO_LINKS` | off | Drop links from the Markdown |
| `--no-images` | `CRAWL4CLI_NO_IMAGES` | off | Drop image references from the Markdown |
| `-q, --quiet` | `CRAWL4CLI_QUIET` | off | Less output on stderr |
| `-v, --verbose` | `CRAWL4CLI_VERBOSE` | off | More output on stderr |
| `--lang en\|ja` | `CRAWL4CLI_LANG` | follows the OS locale | Language of messages (see [Language of messages](#language-of-messages)) |

Only the document goes to stdout; notes, errors, and the summary go to stderr. File names are derived from the URL (`https://example.com/a/b` → `example.com_a_b.md`). The exit code is 0 when every URL succeeded, 1 when any failed, and 2 for invalid arguments.

## Open WebUI web loader (crawl4server)

`crawl4server` runs a single process that serves both an Open WebUI external web loader and an MCP server, on two separate ports, sharing one headless browser and one concurrency limit (`-j`) between them.

### Running

```sh
crawl4server
```

By default this listens on `127.0.0.1:8766` for the web loader (`POST /crawl`, `GET /health`) and `127.0.0.1:8765` for MCP (`/mcp`).

### Connecting Open WebUI

In Open WebUI (0.11.x or later), under Admin Settings > Web Search, set:

- **Web Loader Engine**: `external`
- **External Web Loader URL**: `http://<host>:8766/crawl`
- **External Web Loader API Key**: the value passed to `--loader-api-key` (leave empty if none)

The equivalent environment variables on the Open WebUI side are `WEB_LOADER_ENGINE=external`, `EXTERNAL_WEB_LOADER_URL`, and `EXTERNAL_WEB_LOADER_API_KEY`.

Open WebUI posts `{"urls": [...]}` to that URL and gets back a JSON array of `{"page_content": <Markdown>, "metadata": {"source", "url", "title", "status_code", "content_type"}}` documents. URLs that are invalid or fail are simply left out of the response (and logged to the server's stderr), so one bad URL never drops the whole batch. Open WebUI does not time out these requests itself, so tune `--timeout` and `-j`/`--concurrency` if searches feel slow.

### Options

Settings are resolved in this order: command-line option > `CRAWL4SERVER_*` environment variable (the option's name in upper case, dashes replaced by underscores, e.g. `CRAWL4SERVER_LOADER_API_KEY`) > config file (Config key column below; see [Configuration](#configuration)) > built-in default.

| Option | Environment variable | Config key | Default | Meaning |
|---|---|---|---|---|
| `--host` | `CRAWL4SERVER_HOST` | `host` | `127.0.0.1` | Host to listen on, both ports |
| `--loader-port` | `CRAWL4SERVER_LOADER_PORT` | `loader_port` | `8766` | Web loader port |
| `--loader-path` | `CRAWL4SERVER_LOADER_PATH` | `loader_path` | `/crawl` | HTTP path of the web loader endpoint |
| `--loader-api-key KEY` | `CRAWL4SERVER_LOADER_API_KEY` | `loader_api_key` | *(none)* | Require `Authorization: Bearer KEY` on the web loader (Open WebUI's External Web Loader API Key) |
| `--loader-fit/--no-loader-fit` | `CRAWL4SERVER_LOADER_FIT` | `loader_fit` | `--no-loader-fit` (off) | Keep only the main content of each page; off returns the full page as Markdown |
| `--mcp-port` | `CRAWL4SERVER_MCP_PORT` | `mcp_port` | `8765` | MCP Streamable HTTP port |
| `--mcp-path` | `CRAWL4SERVER_MCP_PATH` | `mcp_path` | `/mcp` | HTTP path of the MCP endpoint |
| `--proxy URL` | `CRAWL4SERVER_PROXY` | `proxy` | *(none)* | `http://`, `https://`, or `socks5://` proxy; credentials as `user:pass@host:port` |
| `--fallback/--no-fallback` | `CRAWL4SERVER_FALLBACK` | `fallback` | `--fallback` (on) | Retry over a direct connection when the proxy itself appears to be at fault |
| `--timeout SECONDS` | `CRAWL4SERVER_TIMEOUT` | `timeout` | `60` | Default per-URL timeout, for web loader requests and MCP tool calls that omit `timeout_s` |
| `-j, --concurrency N` | `CRAWL4SERVER_CONCURRENCY` | `concurrency` | `3` | Maximum URLs fetched at once across both ports |
| `--max-urls N` | `CRAWL4SERVER_MAX_URLS` | `max_urls` | `20` | Maximum URLs accepted per web loader request or MCP tool call; Open WebUI sends up to 20, so keep this at 20 or more |
| `--download-dir DIR` | `CRAWL4SERVER_DOWNLOAD_DIR` | `download_dir` | `.` | Root directory the MCP `download` tool saves files into |
| `--log-level LEVEL` | `CRAWL4SERVER_LOG_LEVEL` | `log_level` | `info` | Log level on stderr: `debug` (everything, incl. crawl4ai and uvicorn access logs), `info` (each fetch, warnings, errors), `error` (warnings and errors only) |
| `--keep-downloads` | `CRAWL4SERVER_KEEP_DOWNLOADS` | `keep_downloads` | off | Keep the files saved by the MCP `download` tool on the server after a client fetched them from their `file_url` (by default the server deletes its copy then) |
| `--lang en\|ja` | `CRAWL4SERVER_LANG` | `lang` | `en` | Language of messages the server produces while running (see [Language of messages](#language-of-messages)) |
| `--config FILE` | `CRAWL4SERVER_CONFIG` | — | *(none)* | YAML or JSON config file (see [Configuration](#configuration) below) |

### Configuration

The config file is YAML or JSON, at any path; point `--config` or `CRAWL4SERVER_CONFIG` at it. Its keys are the Config key column above; a key outside that set is an error. A relative path value (such as `download_dir`) is resolved from the directory the server is started in.

```yaml
host: 0.0.0.0
loader_port: 8766
loader_api_key: change-me
mcp_port: 8765
max_urls: 20
concurrency: 3
lang: ja
```

The same file as JSON:

```json
{
  "host": "0.0.0.0",
  "loader_port": 8766,
  "loader_api_key": "change-me",
  "mcp_port": 8765,
  "max_urls": 20,
  "concurrency": 3,
  "lang": "ja"
}
```

### Docker

`compose.yaml` pulls the published image from [Docker Hub](https://hub.docker.com/r/xhighhongo41/crawl4tools) (`xhighhongo41/crawl4tools`, built for linux/amd64 and linux/arm64):

```sh
git clone https://github.com/xhighhongo41/crawl4tools
cd crawl4tools
mkdir -p downloads
docker compose up -d
curl http://localhost:8766/health
```

Without compose, the same image can be pulled directly: `docker pull xhighhongo41/crawl4tools:1.0.0b3`. Beta versions are not tagged `latest`, so always use the version tag. To build the image locally instead of pulling it, run `docker build -t xhighhongo41/crawl4tools:1.0.0b3 .` and then `docker compose up -d`.

The compose file itself:

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

- `image`: the published Docker Hub image at this release's tag (`xhighhongo41/crawl4tools:1.0.0b3`); to use a local build instead, run the `docker build` command above first, then `docker compose up -d`.
- `ports`: `8766` is the Open WebUI web loader, `8765` is MCP; to publish only one of them, delete the other line (or bind a port to `127.0.0.1` only, e.g. `"127.0.0.1:8765:8765"`, to keep it off the network entirely).
- `environment`: uncomment a line to set it. Set `CRAWL4SERVER_LOADER_API_KEY` here to the value configured as Open WebUI's External Web Loader API Key. See the Options table above for every other `CRAWL4SERVER_*` variable.
- `volumes`: create the host `downloads/` directory first (`mkdir -p downloads`, done above) and make sure it is writable by uid 1000, the user the container runs as.
- `shm_size`: Chromium needs more than Docker's default 64 MB of `/dev/shm` to avoid crashing on larger pages.
- `restart`: `unless-stopped` restarts the container after a crash or a host reboot, but not after an explicit `docker compose down`.

When Open WebUI runs in the same compose project, point it at `http://crawl4tools:8766/crawl` instead of `localhost`. Stopping the container (`docker stop`, or `docker compose down`) lets requests already in progress finish, for up to 5 seconds, before closing the remaining connections.

### Security

The web loader checks the API key only when `--loader-api-key` is set; the MCP port has no authentication at all. When listening on a non-loopback host (as in the Docker setup), set a loader API key and do not expose the MCP port beyond a trusted network — `crawl4server` prints a warning to stderr at startup in that case. `crawl4server` serves plain HTTP; if you need HTTPS, terminate TLS at a reverse proxy placed in front of it.

## MCP server

`crawl4mcp` exposes the same fetching engine as an [MCP](https://modelcontextprotocol.io/) server, with two tools: `fetch` (return content directly) and `download` (save it to files). `crawl4server` (above) serves this same MCP endpoint alongside the Open WebUI web loader, from one process.

### Connecting a client

For [Claude Code](https://docs.claude.com/claude-code), over stdio (the default transport):

```sh
claude mcp add --transport stdio crawl4tools -- crawl4mcp
```

Or, over Streamable HTTP: start the server, then point Claude Code at it:

```sh
crawl4mcp --transport http
claude mcp add --transport http crawl4tools http://127.0.0.1:8765/mcp
```

For Claude Desktop, edit its config file (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`; Windows: `%APPDATA%\Claude\claude_desktop_config.json`) and add an entry under `mcpServers` (if the file already lists other servers there, add this one as another key, separated by a comma); restart Claude Desktop afterwards:

```json
{
  "mcpServers": {
    "crawl4tools": {
      "command": "crawl4mcp",
      "args": ["--download-dir", "/path/to/downloads"]
    }
  }
}
```

If Claude Desktop cannot find `crawl4mcp` (it does not always see your shell's PATH), replace `"command": "crawl4mcp"` with the full path shown by `which crawl4mcp`.

Other clients that support the Streamable HTTP transport (for example Open WebUI's MCP support) can connect to `http://<host>:<port>/mcp` once the server is running with `--transport http`.

### Tools

| Tool | Parameters |
|---|---|
| `fetch` | `urls` (required), `format` (`markdown` default, `html`, `screenshot`), `fit`, `citations`, `ignore_links`, `ignore_images`, `timeout_s` |
| `download` | `urls` (required), `format` (`markdown` default, `html`, `pdf`, `screenshot`, `mhtml`, `raw`), `directory`, `fit`, `citations`, `ignore_links`, `ignore_images`, `timeout_s` |

- `urls`: one or more `http`/`https` URLs, up to the server's per-call limit (`--max-urls`, default 20); duplicate URLs are fetched once.
- `format`: for `fetch`, the page comes back directly as `markdown` (default), `html`, or a `screenshot` (PNG); image URLs are returned as images, and PDFs are transcribed to Markdown. For `download`, the file is saved as `markdown` (default), `html`, `pdf`, `screenshot`, `mhtml`, or `raw`.
- `fit`, `citations`, `ignore_links`, `ignore_images`: the same content-shaping options as the CLI's `--fit`, `--citations`, `--no-links`, and `--no-images` (Markdown only).
- `timeout_s`: page load timeout for this call, in seconds; defaults to the server's `--timeout`.
- `directory` (`download` only): subdirectory of the server's download directory (`--download-dir`) to save into; it cannot resolve outside that directory. Defaults to the download directory itself.

When a call covers several URLs, each result starts with a `<!-- crawl4tools: url=... status=... -->` line; a URL that failed is reported as an `error: ...` line instead, and the call only fails when every URL fails. A proxy fallback or other remark about a result appears as a `<!-- note: ... -->` line. `download` overwrites files that already exist at the destination.

Over HTTP (`--transport http` or `crawl4server`), each file `download` saves also gets a `file_url`, served at `/files/<token>` on the same port; fetch it to your own machine with, for example, `curl -o <name> <file_url>` — its content never passes through the conversation. Over stdio, the server runs on the same machine as the client, so the returned path can be used as is. A `file_url` stops working once the server restarts (the token is forgotten), though the file itself is left in place.

`fetch`'s structured result also carries the page text at `pages[i].text`, alongside the same text in the `content` block; a client that reads `structuredContent` instead of `content` (Claude Code does) still gets it. Claude Code moves an MCP result to a file once it is larger than 25,000 tokens (`MAX_MCP_OUTPUT_TOKENS`); for a long page, `download` avoids that round trip.

### Options

Settings are resolved in this order: command-line option > `CRAWL4MCP_*` environment variable (the option's name in upper case, dashes replaced by underscores, e.g. `CRAWL4MCP_MAX_URLS`) > config file (Config key column below; see [Configuration](#configuration-1)) > built-in default.

| Option | Environment variable | Config key | Default | Meaning |
|---|---|---|---|---|
| `--transport` | `CRAWL4MCP_TRANSPORT` | `transport` | `stdio` | `stdio` or `http` |
| `--host` | `CRAWL4MCP_HOST` | `host` | `127.0.0.1` | Host to listen on (http transport only) |
| `--port` | `CRAWL4MCP_PORT` | `port` | `8765` | Port to listen on (http transport only) |
| `--path` | `CRAWL4MCP_PATH` | `path` | `/mcp` | HTTP path for the MCP endpoint (http transport only) |
| `--proxy URL` | `CRAWL4MCP_PROXY` | `proxy` | *(none)* | `http://`, `https://`, or `socks5://` proxy; credentials as `user:pass@host:port` |
| `--fallback/--no-fallback` | `CRAWL4MCP_FALLBACK` | `fallback` | `--fallback` (on) | Retry over a direct connection when the proxy itself appears to be at fault |
| `--timeout SECONDS` | `CRAWL4MCP_TIMEOUT` | `timeout` | `60` | Default per-URL timeout, used when a tool call omits `timeout_s` |
| `-j, --concurrency N` | `CRAWL4MCP_CONCURRENCY` | `concurrency` | `3` | Maximum URLs fetched at once across every tool call |
| `--max-urls N` | `CRAWL4MCP_MAX_URLS` | `max_urls` | `20` | Maximum URLs accepted in a single tool call |
| `--download-dir DIR` | `CRAWL4MCP_DOWNLOAD_DIR` | `download_dir` | `.` | Root directory the `download` tool saves files into |
| `--log-level LEVEL` | `CRAWL4MCP_LOG_LEVEL` | `log_level` | `info` | Log level on stderr: `debug` (everything, incl. crawl4ai and uvicorn access logs), `info` (each fetch, warnings, errors), `error` (warnings and errors only) |
| `--keep-downloads` | `CRAWL4MCP_KEEP_DOWNLOADS` | `keep_downloads` | off | Keep the files saved by the `download` tool on the server after a client fetched them from their `file_url` (by default the server deletes its copy then) |
| `--lang en\|ja` | `CRAWL4MCP_LANG` | `lang` | `en` | Language of messages the server produces while running (see [Language of messages](#language-of-messages)) |
| `--config FILE` | `CRAWL4MCP_CONFIG` | — | *(none)* | YAML or JSON config file (see [Configuration](#configuration-1) below) |

### Configuration

The config file is YAML or JSON, at any path; point `--config` or `CRAWL4MCP_CONFIG` at it. Its keys are the Config key column above; a key outside that set is an error. A relative path value (such as `download_dir`) is resolved from the directory the server is started in.

```yaml
transport: http
host: 127.0.0.1
port: 8765
max_urls: 50
concurrency: 5
download_dir: ./downloads
proxy: http://proxy.local:8080
lang: ja
```

The same file as JSON:

```json
{
  "transport": "http",
  "host": "127.0.0.1",
  "port": 8765,
  "max_urls": 50,
  "concurrency": 5,
  "download_dir": "./downloads",
  "proxy": "http://proxy.local:8080",
  "lang": "ja"
}
```

### Security

The Streamable HTTP transport has no authentication. By default the server listens on `127.0.0.1` only; if you bind it to another host, anyone who can reach that port can use the server, and `crawl4mcp` prints a warning to stderr when it starts. In stdio mode, stdout is reserved for the MCP protocol — all logging goes to stderr.

`/files/<token>` (see [Tools](#tools) above) serves the files `download` has saved, on the same port as MCP. The token is an unguessable random value, but the port itself still has no authentication — do not expose the MCP port beyond a trusted network.

## Language of messages

All three commands can show their messages in English (`en`) or Japanese (`ja`).

Choose the language with the `--lang` option, an environment variable (`CRAWL4CLI_LANG`, `CRAWL4MCP_LANG`, `CRAWL4SERVER_LANG`), or, for the two servers, the `lang` key in the config file. When more than one is set, the option wins, then the environment variable, then the config file.

Defaults: `crawl4cli` follows the OS locale (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, then `LANG`, whichever is set first) and uses Japanese when it starts with `ja` (for example `LANG=ja_JP.UTF-8`), otherwise English. `crawl4mcp` and `crawl4server` always default to English, regardless of the locale, so containers and MCP clients get stable output.

```sh
crawl4cli --lang ja https://example.com/
CRAWL4SERVER_LANG=ja crawl4server
```

This covers `--help`, error messages, notes and progress lines on stderr, the MCP tools' descriptions and result text, the servers' startup/warning lines, and the web loader's JSON error responses. `--help` always follows `--lang`/the environment variable (and, for `crawl4cli`, the locale) — the config file's `lang` only applies to messages produced while the server is running.

Always in English, unchanged by `--lang`: log output; the `error:`, `note:`, `saved:`, `done:`, `failed:` line prefixes; JSON keys; the `<!-- crawl4tools: url=... status=... -->` header lines; `GET /health`; `--version`; and anything printed by click or other libraries (for example `Usage:` or `Error: Invalid value ...`).

Each process uses one language for its whole run; there is no per-request language.

## Acknowledgements

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai). Crawl4AI is licensed under the Apache License 2.0.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](https://github.com/xhighhongo41/crawl4tools/blob/main/LICENSE) file for details.
