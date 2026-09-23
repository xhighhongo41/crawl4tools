# crawl4tools

[日本語版 README はこちら](README_ja.md)

## What is crawl4tools

crawl4tools is a web crawler built on top of the [crawl4ai](https://github.com/unclecode/crawl4ai) library. It is designed to serve three purposes:

1. An HTTP server that works directly as an **Open WebUI external web loader**, returning the body of a URL as Markdown. On the Open WebUI side, this only requires setting `WEB_LOADER_ENGINE=external` and `EXTERNAL_WEB_LOADER_URL`.
2. An **MCP server** that lets AI agents such as Claude Code fetch the **full content** of a web page, rather than a summary.
3. A **local CLI** that downloads a given URL (or multiple URLs at once) as Markdown or other formats.

## Status

**Pre-alpha. Nothing is functional yet.** This release (0.0.1) only contains the license, documentation, and project configuration; no code has been implemented.

## Planned features

- Three usage modes: Open WebUI external web loader, MCP server, and local CLI
- Selectable output formats provided by crawl4ai: Markdown, raw HTML, PDF, screenshot, and MHTML
- Downloading through an HTTP/HTTPS proxy, with automatic fallback to a direct connection when the proxy fails or SSL bumping breaks the result
- Server operation via Docker Compose
- One-step installation of the CLI, e.g. via `uv tool install`

## Installation

Not yet available. Once released, installation is planned to be possible via Docker Compose (for the server) and `uv tool install` or similar (for the CLI).

## Usage

Not yet available. Usage instructions will be added once the server, MCP server, and CLI are implemented.

## Acknowledgements

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai). Crawl4AI is licensed under the Apache License 2.0.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.
