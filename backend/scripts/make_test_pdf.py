"""构造一个最小合法 PDF（含 IMRaD 风格文本），用于测试全文上传管线。"""
from __future__ import annotations

import os


def make_pdf(texts: list[str], path: str) -> None:
    content_lines = ["BT /F1 12 Tf 72 700 Td 14 TL"]
    for i, t in enumerate(texts):
        esc = t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content_lines.append(f"({esc}) Tj" if i == 0 else f"T* ({esc}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out: list[bytes] = [b"%PDF-1.4\n"]
    offsets: list[int] = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(b"".join(out)))
        out.append(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")

    xref_pos = len(b"".join(out))
    out.append(f"xref\n0 {len(objects) + 1}\n".encode())
    out.append(b"0000000000 65535 f \n")
    for off in offsets:
        out.append(f"{off:010d} 00000 n \n".encode())
    out.append(
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_pos).encode()
        + b"\n%%EOF\n"
    )
    with open(path, "wb") as f:
        f.write(b"".join(out))


if __name__ == "__main__":
    texts = [
        "We investigate the effect of crosslinking density on waterborne polyurethane coatings.",
        "We used FTIR, DSC and tensile testing with n=30 samples following the experimental method.",
        "Results show significant improvement in mechanical properties (p<0.05).",
        "We conclude that multi-crosslinked bio-based networks offer superior durability.",
        "Data availability: all raw data and code at github.com/acme/wpucoating.",
    ]
    target = os.path.join(os.path.dirname(__file__), "..", "data", "test_fulltext.pdf")
    make_pdf(texts, os.path.normpath(target))
    print("PDF created:", os.path.getsize(os.path.normpath(target)), "bytes")
