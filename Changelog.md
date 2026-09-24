# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/xhighhongo41/crawl4tools/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/xhighhongo41/crawl4tools/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/xhighhongo41/crawl4tools/releases/tag/v0.0.1
