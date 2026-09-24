"""PDF to Markdown conversion built on crawl4ai's PDF processor.

crawl4ai is imported lazily so importing this module stays cheap.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


class PdfConversionError(Exception):
    """The PDF could not be parsed or converted to Markdown."""


def pdf_bytes_to_markdown(data: bytes) -> str:
    """Convert the PDF document in *data* to Markdown.

    Page texts are stripped and joined with blank lines; pages without text
    are dropped, so a PDF with no text layer yields an empty string.

    Raises:
        PdfConversionError: if the PDF cannot be parsed or converted.
    """
    # delete=False plus explicit unlink: the processor re-opens the file by
    # path, which a still-open NamedTemporaryFile does not allow on Windows.
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        path = Path(handle.name)
    try:
        path.write_bytes(data)
        from crawl4ai.processors.pdf.processor import NaivePDFProcessorStrategy

        result = NaivePDFProcessorStrategy(extract_images=False).process(path)
        texts = [page.markdown.strip() for page in result.pages if page.markdown]
        return "\n\n".join(text for text in texts if text)
    except Exception as exc:
        raise PdfConversionError(str(exc) or type(exc).__name__) from exc
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


async def pdf_to_markdown(data: bytes) -> str:
    """Asynchronously convert *data* to Markdown in a worker thread.

    Raises:
        PdfConversionError: if the PDF cannot be parsed or converted.
    """
    return await asyncio.to_thread(pdf_bytes_to_markdown, data)
