"""Formatting of per-outcome and per-run CLI messages."""

from __future__ import annotations

from crawl4tools.engine import FetchOutcome


def error_line(outcome: FetchOutcome) -> str:
    """Return the ``error: ...`` line to print for a failed *outcome*."""
    if outcome.error is not None:
        return f"error: {outcome.error}"
    return f"error: fetch failed: {outcome.url}"


def summary_lines(total_ok: int, failed_urls: list[str]) -> list[str]:
    """Return the run summary: a ``done: ...`` line plus one per failed URL."""
    lines = [f"done: {total_ok} succeeded, {len(failed_urls)} failed"]
    lines.extend(f"  failed: {url}" for url in failed_urls)
    return lines


def exit_code(failed_count: int) -> int:
    """Return the process exit code for a run with *failed_count* failures."""
    return 1 if failed_count else 0
