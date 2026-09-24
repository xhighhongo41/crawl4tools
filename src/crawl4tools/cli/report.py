"""Formatting of per-outcome and per-run CLI messages.

Every function takes the :data:`~crawl4tools.i18n.Translator` of the
message language. Only the message after the prefix is translated; the
``error:`` / ``note:`` / ``done:`` / ``failed:`` prefixes stay in English so
that scripts can keep matching them whatever the language is.
"""

from __future__ import annotations

from crawl4tools.engine import FetchOutcome, Note
from crawl4tools.i18n import Translator


def error_line(outcome: FetchOutcome, t: Translator) -> str:
    """Return the ``error: ...`` line to print for a failed *outcome*.

    The message is translated with *t*; without an error object, the
    generic "fetch failed" message is used.
    """
    if outcome.error is not None:
        return f"error: {outcome.error.render(t)}"
    message = t.gettext("fetch failed: {url}").format(url=outcome.url)
    return f"error: {message}"


def note_line(url: str, note: Note, t: Translator) -> str:
    """Return the ``note: <url>: ...`` line for a *note* about *url*, translated with *t*."""
    return f"note: {url}: {note.render(t)}"


def summary_lines(total_ok: int, failed_urls: list[str], t: Translator) -> list[str]:
    """Return the run summary: a ``done: ...`` line plus one per failed URL.

    The counts in the ``done:`` line are translated with *t*; the
    ``  failed: <url>`` lines contain nothing to translate.
    """
    counts = t.gettext("{succeeded} succeeded, {failed} failed").format(
        succeeded=total_ok, failed=len(failed_urls)
    )
    lines = [f"done: {counts}"]
    lines.extend(f"  failed: {url}" for url in failed_urls)
    return lines


def exit_code(failed_count: int) -> int:
    """Return the process exit code for a run with *failed_count* failures."""
    return 1 if failed_count else 0
