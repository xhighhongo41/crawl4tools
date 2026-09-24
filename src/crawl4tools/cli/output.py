"""Writing fetch outcomes to stdout or to disk.

Pure(ish) helpers with no click dependency for their core logic (writing to
stdout still goes through :func:`click.echo` so it interacts correctly with
click's output streams in tests), so they can be unit-tested directly.
"""

from __future__ import annotations

from pathlib import Path

import click

from crawl4tools.engine import FetchOutcome, NameAllocator, filename_for


def payload_bytes(outcome: FetchOutcome) -> bytes:
    """Return the raw bytes to write for *outcome*.

    Binary payloads (``outcome.data``) take priority; text payloads are
    encoded as UTF-8. Returns ``b""`` if the outcome carries neither.
    """
    if outcome.data is not None:
        return outcome.data
    return (outcome.text or "").encode("utf-8")


def write_stdout(outcome: FetchOutcome) -> None:
    """Write ``outcome.text`` to stdout with exactly one trailing newline."""
    text = outcome.text or ""
    message = text if text.endswith("\n") else f"{text}\n"
    click.echo(message, nl=False)


def write_outcome(
    outcome: FetchOutcome,
    url: str,
    *,
    output_path: Path | None,
    directory: Path,
    allocator: NameAllocator,
    to_stdout: bool,
) -> Path | None:
    """Write *outcome* to its destination and return the saved path.

    Exactly one of three destinations is used: stdout (when *to_stdout* is
    True), *output_path* (when given), or an allocator-assigned path under
    *directory*. Returns None when written to stdout.

    Raises:
        OSError: if the file could not be written. The exception's
            ``filename`` attribute is set to the path that was targeted.
    """
    if to_stdout:
        write_stdout(outcome)
        return None

    if output_path is not None:
        target = output_path
    else:
        directory.mkdir(parents=True, exist_ok=True)
        target = allocator.allocate(filename_for(url, outcome.suggested_extension))

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload_bytes(outcome))
    except OSError as exc:
        if exc.filename is None:
            exc.filename = str(target)
        raise
    return target
