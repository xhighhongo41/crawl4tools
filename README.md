# crawl4tools

[日本語版 README はこちら](README_ja.md)

## What is crawl4tools

crawl4tools is a web crawler built on top of the [crawl4ai](https://github.com/unclecode/crawl4ai) library. It is designed to serve three purposes:

1. An HTTP server that works directly as an **Open WebUI external web loader**, returning the body of a URL as Markdown. On the Open WebUI side, this only requires setting `WEB_LOADER_ENGINE=external` and `EXTERNAL_WEB_LOADER_URL`.
2. An **MCP server** that lets AI agents such as Claude Code fetch the **full content** of a web page, rather than a summary.
3. A **local CLI** that downloads a given URL (or multiple URLs at once) as Markdown or other formats.

## Status

**Alpha.** This release (0.1.0) provides the local CLI, `crawl4cli`. The Open WebUI loader and the MCP server are not implemented yet.

## Features

Available now (CLI):

- Download one or more URLs as Markdown, HTML, PDF, screenshot (PNG), MHTML, or the raw source
- PDFs are transcribed to Markdown; images and other non-HTML files are saved as they are
- Clear error messages for HTTP errors, unknown hosts, refused connections, timeouts, and a missing browser
- Downloading through an HTTP/HTTPS/SOCKS5 proxy, with a single retry over a direct connection when the proxy itself fails

Planned:

- Open WebUI external web loader and MCP server, operated via Docker Compose
- Fallback to a direct connection when SSL bumping by the proxy breaks the result

## Installation

The CLI needs Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install --with-executables-from playwright git+https://github.com/xhighhongo41/crawl4tools
playwright install chromium   # downloads the headless browser (once)
```

The browser is stored in Playwright's cache directory (for example `~/Library/Caches/ms-playwright` on macOS). crawl4ai also creates a `~/.crawl4ai` directory for its own data.

The server (Docker Compose) is not available yet.

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

Only the document goes to stdout; notes, errors, and the summary go to stderr. File names are derived from the URL (`https://example.com/a/b` → `example.com_a_b.md`). The exit code is 0 when every URL succeeded, 1 when any failed, and 2 for invalid arguments. Every option can also be set with an environment variable named `CRAWL4CLI_<OPTION>`, for example `CRAWL4CLI_PROXY`. The standard `HTTP_PROXY`/`HTTPS_PROXY` variables are not used.

## Acknowledgements

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai). Crawl4AI is licensed under the Apache License 2.0.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.
