"""
One-off script to create a minimal test PDF fixture for OCR pipeline tests.
Run with: python chatbot/fixtures/create_test_pdf.py
Output:   chatbot/fixtures/exam_timetable.pdf
"""

import io
import os

# We use pypdf's low-level writer so there's no extra dependency needed.
# The PDF contains plain text in a simple single-page format.

CONTENT = """\
Final Semester Exam Timetable
Mathematics - 25/03/2026
Physics - 28/03/2026
Chemistry - 01/04/2026
Computer Science - 05/04/2026
English - 08/04/2026
"""


def _build_minimal_pdf(text: str) -> bytes:
    """
    Build a minimal valid single-page PDF containing the given plain text.
    Uses raw PDF syntax — no external library needed beyond stdlib.
    """
    # PDF content stream: simple text rendering
    lines = text.strip().splitlines()
    tf_lines = []
    y = 700
    for line in lines:
        # Escape parentheses for PDF string syntax
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        tf_lines.append(f"BT /F1 12 Tf 50 {y} Td ({safe}) Tj ET")
        y -= 20

    stream_content = "\n".join(tf_lines)
    stream_bytes = stream_content.encode("latin-1")
    stream_len = len(stream_bytes)

    objects = []

    # Obj 1: Catalog
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # Obj 2: Pages
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")

    # Obj 3: Page
    objects.append(
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
        b"   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
        b"endobj\n"
    )

    # Obj 4: Content stream
    content_obj = (
        f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode()
        + stream_bytes
        + b"\nendstream\nendobj\n"
    )
    objects.append(content_obj)

    # Obj 5: Font
    objects.append(
        b"5 0 obj\n"
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        b"endobj\n"
    )

    # Build PDF body
    header = b"%PDF-1.4\n"
    body = b""
    offsets = []
    pos = len(header)
    for obj in objects:
        offsets.append(pos)
        body += obj
        pos += len(obj)

    # Cross-reference table
    xref_offset = len(header) + len(body)
    xref = f"xref\n0 {len(objects) + 1}\n"
    xref += "0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n"

    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\r\n"
    )

    return header + body + xref.encode() + trailer.encode()


if __name__ == "__main__":
    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, "exam_timetable.pdf")
    pdf_bytes = _build_minimal_pdf(CONTENT)
    with open(out_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"Created: {out_path} ({len(pdf_bytes)} bytes)")
    print("Subjects encoded in PDF:")
    for line in CONTENT.strip().splitlines():
        print(f"  {line}")
