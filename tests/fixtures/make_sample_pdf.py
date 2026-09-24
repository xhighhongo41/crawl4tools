"""Generate ``sample.pdf``: a tiny one-page PDF whose text reads "Hello crawl4tools".

Run with ``uv run python tests/fixtures/make_sample_pdf.py``. The output is
committed, so this only needs to be re-run if the fixture must change.
"""

from __future__ import annotations

from pathlib import Path

_CONTENT = b"BT /F1 24 Tf 72 720 Td (Hello crawl4tools) Tj ET"

_OBJECTS = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    b"<< /Length " + str(len(_CONTENT)).encode() + b" >>\nstream\n" + _CONTENT + b"\nendstream",
]


def build_pdf() -> bytes:
    """Return the bytes of a minimal valid PDF with a correct xref table."""
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(_OBJECTS, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(_OBJECTS) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(_OBJECTS) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_offset}\n%%EOF\n".encode()
    return bytes(out)


if __name__ == "__main__":
    Path(__file__).with_name("sample.pdf").write_bytes(build_pdf())
