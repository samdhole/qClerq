"""Create a deliberately degraded invoice PDF to test low-confidence routing (Phase 5.2).

Design choices that drive low confidence:
- No invoice number (field missing)
- Vendor name garbled / illegible
- Subtotal + tax != total (math contradiction)
- Dates inconsistent (due before invoice)
- Line items present but totals don't match
- Partial / torn-looking content suggesting bad scan
"""


def make_pdf(filename: str, content: str) -> None:
    """Create a minimal PDF with the given text content (same builder as create_test_invoice.py)."""
    lines = []
    offsets = {}

    def add(obj_id: int, data: str) -> None:
        offsets[obj_id] = len("\n".join(lines)) + (1 if lines else 0)
        lines.append(f"{obj_id} 0 obj\n{data}\nendobj")

    add(1, "<< /Type /Catalog /Pages 2 0 R >>")
    add(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add(3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>")

    stream_data = "BT /F1 11 Tf 50 742 Td 14 TL\n"
    for line in content.split("\n"):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream_data += f"({escaped}) Tj T*\n"
    stream_data += "ET"
    stream_bytes = stream_data.encode("latin-1")
    add(4, f"<< /Length {len(stream_bytes)} >>\nstream\n{stream_data}\nendstream")
    add(5, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    body = "%PDF-1.4\n" + "\n".join(lines) + "\n"
    xref_pos = len(body)
    xref = "xref\n"
    xref += f"0 {len(offsets) + 1}\n"
    xref += "0000000000 65535 f \n"
    for i in range(1, len(offsets) + 1):
        xref += f"{offsets[i] + 9:010d} 00000 n \n"

    trailer = (
        f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    )

    with open(filename, "wb") as f:
        f.write((body + xref + trailer).encode("latin-1"))
    print(f"Created {filename}")


# Deliberately degraded invoice — simulates a bad scan / torn document
low_confidence_content = """\
[ SCANNED DOCUMENT - POOR QUALITY ]

   I N V O I C E

Vendor: V3nd0r N4m3 C0rp???  (illegible)
Address: [smudged]
Tax ID: XX-XXXXXXX

Bill To: [torn section]
Invoice Number:               <-- MISSING
Invoice Date: 2026-03-??
Due Date: 2026-01-15         <-- BEFORE invoice date

ITEMS

  Consulting services (partial)       $???
  Materials - qty unknown              $312.50
  [line obscured]                         ???

Subtotal:    $750.00
Tax:          $95.00
Shipping:     $30.00
              --------
Total Due:   $600.00          <-- DOES NOT MATCH subtotal+tax+shipping

Payment Terms: [illegible]

NOTE: This document has been digitally reconstructed from
a damaged original. Some fields may be inaccurate.
Please contact sender to verify amounts before payment.
"""

make_pdf("scripts/Invoice-LOW-CONFIDENCE-scan.pdf", low_confidence_content)
print("Done. Submit to /extract and expect confidence_overall < 0.70")
