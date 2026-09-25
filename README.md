# crawl4tools

[日本語版 README はこちら](README_ja.md)

## What is crawl4tools

crawl4tools is a web crawler built on top of the [crawl4ai](https://github.com/unclecode/crawl4ai) library. It is designed to serve three purposes:

1. An HTTP server, `crawl4server`, that works directly as an **Open WebUI external web loader**, returning the body of a URL as Markdown. On the Open WebUI side, this only requires setting Web Loader Engine to `external` and the External Web Loader URL in the admin UI (or the equivalent `WEB_LOADER_ENGINE=external` and `EXTERNAL_WEB_LOADER_URL` environment variables).
2. An **MCP server** that lets AI agents such as Claude Code fetch the **full content** of a web page, rather than a summary.
3. A **local CLI** that downloads a given URL (or multiple URLs at once) as Markdown or other formats.

## Status

**Alpha.** This release (1.0.0b1) provides the local CLI `crawl4cli`, the MCP server `crawl4mcp`, and the combined Open WebUI web loader + MCP server `crawl4server`, with a Dockerfile and compose file. All three commands can show their messages in English or Japanese.

## Features

Available now (CLI):

- Download one or more URLs as Markdown, HTML, PDF, screenshot (PNG), MHTML, or the raw source
- PDFs are transcribed to Markdown; images and other non-HTML files are saved as they are
- Clear error messages for HTTP errors, unknown hosts, refused connections, timeouts, and a missing browser
- Downloading through an HTTP/HTTPS/SOCKS5 proxy, with a single retry over a direct connection when the proxy itself fails
- Messages in English or Japanese (see [Language of messages](#language-of-messages))

Available now (MCP server):

- Two tools: `fetch` returns pages directly to the client as Markdown (default), HTML, or a PNG screenshot (images come back as images, PDFs are transcribed to Markdown); `download` saves pages in any format (Markdown, HTML, PDF, screenshot, MHTML, or raw) into a directory on the server and returns the paths
- Several URLs per call (default limit 20), fetched concurrently under a server-wide limit (default 3), sharing one headless browser
- stdio (default) and Streamable HTTP transports
- Proxy support with a direct-connection fallback, configured on the server side
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

- Prebuilt Docker image on a registry
- Authentication for the MCP endpoint
- Fallback to a direct connection when SSL bumping by the proxy breaks the result

## Installation

The CLI and the MCP server need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install --with-executables-from playwright git+https://github.com/xhighhongo41/crawl4tools
playwright install chromium   # downloads the headless browser (once)
```

This installs `crawl4cli`, `crawl4mcp`, and `crawl4server`.

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

| Option | Meaning |
|---|---|
| `-f, --format` | `markdown` (default), `html`, `pdf`, `screenshot`, `mhtml`, or `raw` |
| `-o, --output FILE` | Save a single URL to FILE instead of stdout |
| `-d, --output-dir DIR` | Directory for several URLs and binary formats (default: current directory) |
| `--proxy URL` | `http://`, `https://`, or `socks5://` proxy; credentials as `user:pass@host:port` |
| `--no-fallback` | Do not retry over a direct connection when the proxy fails |
| `-j, --concurrency N` | URLs fetched at once (default: 3) |
| `--timeout SECONDS` | Page load timeout per URL (default: 60) |
| `--fit` | Keep only the main content (drops menus, footers, and the like); falls back to the full page if nothing is left |
| `--citations` | Turn links into numbered references listed at the end |
| `--no-links`, `--no-images` | Drop links or image references from the Markdown |
| `-q, --quiet` / `-v, --verbose` | Less or more output on stderr |
| `--lang en\|ja` | Language of messages (default: follows the OS locale; see [Language of messages](#language-of-messages)) |

Only the document goes to stdout; notes, errors, and the summary go to stderr. File names are derived from the URL (`https://example.com/a/b` → `example.com_a_b.md`). The exit code is 0 when every URL succeeded, 1 when any failed, and 2 for invalid arguments. Every option can also be set with an environment variable named `CRAWL4CLI_<OPTION>`, for example `CRAWL4CLI_PROXY`. The standard `HTTP_PROXY`/`HTTPS_PROXY` variables are not used.

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

| Option | Meaning |
|---|---|
| `--host` | Host to listen on, both ports (default: `127.0.0.1`) |
| `--loader-port` | Web loader port (default: `8766`) |
| `--loader-path` | HTTP path of the web loader endpoint (default: `/crawl`) |
| `--loader-api-key KEY` | Require `Authorization: Bearer KEY` on the web loader (Open WebUI's External Web Loader API Key) |
| `--loader-fit` / `--no-loader-fit` | Keep only the main content of each page (default: off, full-page Markdown) |
| `--mcp-port` | MCP Streamable HTTP port (default: `8765`) |
| `--mcp-path` | HTTP path of the MCP endpoint (default: `/mcp`) |
| `--proxy URL` | `http://`, `https://`, or `socks5://` proxy; credentials as `user:pass@host:port` |
| `--no-fallback` | Do not retry over a direct connection when the proxy fails |
| `--timeout SECONDS` | Default per-URL timeout, for web loader requests and MCP tool calls that omit `timeout_s` (default: 60) |
| `-j, --concurrency N` | Maximum URLs fetched at once across both ports (default: 3) |
| `--max-urls N` | Maximum URLs accepted per web loader request or MCP tool call; Open WebUI sends up to 20, so keep this at 20 or more (default: 20) |
| `--download-dir DIR` | Root directory the MCP `download` tool saves files into (default: current directory) |
| `--config FILE` | YAML or JSON config file (see Configuration below) |
| `-v, --verbose` | Verbose logging on stderr |
| `--lang en\|ja` | Language of messages the server produces while running (default: English; see [Language of messages](#language-of-messages)) |

### Configuration

Settings are resolved in this order: command-line options > `CRAWL4SERVER_*` environment variables (for example `CRAWL4SERVER_LOADER_API_KEY`) > config file (`--config` or `CRAWL4SERVER_CONFIG`) > built-in defaults. Config file keys are the same option names in snake_case:

```yaml
host: 0.0.0.0
loader_port: 8766
loader_api_key: change-me
mcp_port: 8765
max_urls: 20
concurrency: 3
lang: ja
```

### Docker

The repository ships a `Dockerfile` and `compose.yaml` (no image is published to a registry yet, so build it locally):

```sh
git clone https://github.com/xhighhongo41/crawl4tools
cd crawl4tools
mkdir -p downloads
docker compose up -d --build
curl http://localhost:8766/health
```

Set `CRAWL4SERVER_LOADER_API_KEY` in `compose.yaml`'s `environment` section. The container runs as uid 1000, so `downloads/` must be writable by it; `shm_size: 1gb` is set for Chromium. When Open WebUI runs in the same compose project, point it at `http://crawl4tools:8766/crawl` instead of `localhost`.

### Security

The web loader checks the API key only when `--loader-api-key` is set; the MCP port has no authentication at all. When listening on a non-loopback host (as in the Docker setup), set a loader API key and do not expose the MCP port beyond a trusted network — `crawl4server` prints a warning to stderr at startup in that case.

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

For Claude Desktop, add an entry to `claude_desktop_config.json`:

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

### Options

| Option | Meaning |
|---|---|
| `--transport` | `stdio` (default) or `http` |
| `--host` | Host to listen on (http transport only; default: `127.0.0.1`) |
| `--port` | Port to listen on (http transport only; default: `8765`) |
| `--path` | HTTP path for the MCP endpoint (http transport only; default: `/mcp`) |
| `--proxy URL` | `http://`, `https://`, or `socks5://` proxy; credentials as `user:pass@host:port` |
| `--no-fallback` | Do not retry over a direct connection when the proxy fails |
| `--timeout SECONDS` | Default per-URL timeout, used when a tool call omits `timeout_s` (default: 60) |
| `-j, --concurrency N` | Maximum URLs fetched at once across every tool call (default: 3) |
| `--max-urls N` | Maximum URLs accepted in a single tool call (default: 20) |
| `--download-dir DIR` | Root directory the `download` tool saves files into (default: current directory) |
| `--config FILE` | YAML or JSON config file (see Configuration below) |
| `-v, --verbose` | Verbose logging on stderr |
| `--lang en\|ja` | Language of messages the server produces while running (default: English; see [Language of messages](#language-of-messages)) |

### Configuration

Settings are resolved in this order: command-line options > `CRAWL4MCP_*` environment variables > config file > built-in defaults.

Every option can be set with an environment variable named `CRAWL4MCP_` followed by the option's name in upper case with dashes replaced by underscores — for example `CRAWL4MCP_MAX_URLS` for `--max-urls`, or `CRAWL4MCP_CONCURRENCY` for `--concurrency`. `CRAWL4MCP_CONFIG` points to the config file itself, same as `--config`.

The config file is YAML (JSON also works, since JSON is valid YAML), with the same option names as keys, using underscores instead of dashes; unknown keys are an error:

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

Relative paths in the config file (such as `download_dir`) are resolved from the current directory the server is started in.

### Security

The Streamable HTTP transport has no authentication. By default the server listens on `127.0.0.1` only; if you bind it to another host, anyone who can reach that port can use the server, and `crawl4mcp` prints a warning to stderr when it starts. In stdio mode, stdout is reserved for the MCP protocol — all logging goes to stderr.

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

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.
