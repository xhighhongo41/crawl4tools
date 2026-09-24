# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- English and Japanese messages for `crawl4cli`, `crawl4mcp`, and `crawl4server`: `--help` text, error messages, notes and progress lines on stderr, the MCP tools' descriptions and result text, the servers' startup/warning lines, and the web loader's JSON error responses
- `--lang en|ja` option, `CRAWL4CLI_LANG`/`CRAWL4MCP_LANG`/`CRAWL4SERVER_LANG` environment variables, and a `lang` config file key (servers) to choose the message language, resolved as option > environment variable > config file

### Changed

- `crawl4cli` now follows the OS locale (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`) to pick its default language; `crawl4mcp` and `crawl4server` still default to English regardless of the locale

## [0.3.0] - 2026-09-24

### Added

- `crawl4server` command: serves the Open WebUI web loader and MCP on two ports of one process, sharing one headless browser and one concurrency limit
- Open WebUI external web loader endpoint (`POST /crawl`, `GET /health`, optional bearer API key)
- `CRAWL4SERVER_*` environment variables and a YAML/JSON config file (`--config`)
- `Dockerfile` and `compose.yaml` for running `crawl4server` in a container
- Page title in fetch results (web loader `metadata.title`, MCP structured page data `title`)

### Changed

- `starlette` and `uvicorn` are now direct dependencies; the empty `server` extra was removed

## [0.2.0] - 2026-09-24

### Added

- `crawl4mcp` MCP server with `fetch` (return content directly) and `download` (save to files) tools
- stdio and Streamable HTTP transports
- Per-call URL limit (default 20) and server-wide concurrency limit (default 3), both configurable
- Configuration via command-line options, `CRAWL4MCP_*` environment variables, or a YAML/JSON config file (`--config`)
- `Fetcher.fetch`/`fetch_many` accept per-call options while sharing one engine

## [0.1.0] - 2026-09-24

### Added

- `crawl4cli` command: download one or more URLs as Markdown, HTML, PDF, screenshot, MHTML, or raw source
- PDF transcription to Markdown; non-HTML resources such as images are saved as they are
- Error messages by cause (HTTP status, unknown host, refused connection, timeout, missing browser) and exit codes 0/1/2
- Proxy support (`--proxy`) with a single retry over a direct connection when the proxy fails (`--no-fallback` to disable)
- `--fit` to keep only the main content of a page, using crawl4ai's pruning content filter
- Concurrent downloads of several URLs into a directory (`-d`, `-j`)
- `CRAWL4CLI_*` environment variables for every option
- Shared fetch engine (`crawl4tools.engine`) for the planned servers
- `tools/check.sh` and `tools/release_check.sh` for development

## [0.0.1] - 2026-09-23

### Added

- Project scaffolding (license, NOTICE, .gitignore, pyproject metadata)
- README in English and Japanese
- This Changelog

No code is included in this release.

[Unreleased]: https://github.com/xhighhongo41/crawl4tools/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/xhighhongo41/crawl4tools/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/xhighhongo41/crawl4tools/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/xhighhongo41/crawl4tools/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/xhighhongo41/crawl4tools/releases/tag/v0.0.1
