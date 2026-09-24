from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from crawl4tools.engine.pdf import PdfConversionError, pdf_bytes_to_markdown, pdf_to_markdown


def _temp_pdfs() -> set[Path]:
    return set(Path(tempfile.gettempdir()).glob("*.pdf"))


def test_sample_pdf_converts_to_markdown(sample_pdf: bytes) -> None:
    before = _temp_pdfs()
    markdown = pdf_bytes_to_markdown(sample_pdf)
    assert "Hello crawl4tools" in markdown
    assert _temp_pdfs() - before == set()


async def test_async_wrapper(sample_pdf: bytes) -> None:
    markdown = await pdf_to_markdown(sample_pdf)
    assert "Hello crawl4tools" in markdown


@pytest.mark.parametrize("data", [b"not a pdf at all", b"", b"%PDF-1.4\ngarbage"])
def test_corrupt_pdf_raises_conversion_error(data: bytes) -> None:
    before = _temp_pdfs()
    with pytest.raises(PdfConversionError):
        pdf_bytes_to_markdown(data)
    assert _temp_pdfs() - before == set()


async def test_async_wrapper_propagates_conversion_error() -> None:
    with pytest.raises(PdfConversionError):
        await pdf_to_markdown(b"not a pdf")
