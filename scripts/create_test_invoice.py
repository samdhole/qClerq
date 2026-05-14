"""Create a minimal valid PDF invoice for testing approval tier routing."""
import struct, zlib, textwrap

def make_pdf(filename: str, content: str) -> None:
    """Create a minimal PDF with the given text content."""
    # Compress content stream
    stream = content.encode("latin-1")

    # Build PDF manually
    lines = []
    offsets = {}

    def add(obj_id: int, data: str) -> None:
        offsets[obj_id] = len("\n".join(lines)) + (1 if lines else 0)
        lines.append(f"{obj_id} 0 obj\n{data}\nendobj")

    # Object 1: Catalog
    add(1, "<< /Type /Catalog /Pages 2 0 R >>")
    # Object 2: Pages
    add(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    # Object 3: Page
    add(3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>")
    # Object 4: Content stream
    stream_data = f"BT /F1 11 Tf 50 742 Td 14 TL\n"
    for line in content.split("\n"):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream_data += f"({escaped}) Tj T*\n"
    stream_data += "ET"
    stream_bytes = stream_data.encode("latin-1")
    add(4, f"<< /Length {len(stream_bytes)} >>\nstream\n{stream_data}\nendstream")
    # Object 5: Font
    add(5, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    body = "%PDF-1.4\n" + "\n".join(lines) + "\n"

    # xref
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


# Manager tier invoice: $1,850.00 USD (triggers manager approval)
manager_content = """\
INVOICE

Vendor: Apex Construction Services LLC
Address: 400 Industrial Blvd, Austin TX 78701
Tax ID: 74-3219087

Bill To: qClerq Inc.
Invoice Number: ACS-2026-0142
Invoice Date: 2026-05-14
Due Date: 2026-06-13
Currency: USD

SERVICES

  Concrete foundation work (Phase 2)     $1,200.00
  Site preparation and grading              $450.00
  Equipment rental (3 days)                $200.00

Subtotal:   $1,850.00
Tax (0%):       $0.00
Total Due:  $1,850.00

Payment Terms: Net 30
Bank: First National Bank
Account: 4821-XXXX-XXXX
Routing: 111000025

Thank you for your business.
"""

# CFO tier invoice: $7,500.00 USD (triggers CFO approval)
cfo_content = """\
INVOICE

Vendor: Meridian Enterprise Solutions Inc.
Address: 1200 Corporate Plaza, Dallas TX 75201
Tax ID: 75-4419203

Bill To: qClerq Inc.
Invoice Number: MES-2026-0089
Invoice Date: 2026-05-14
Due Date: 2026-06-13
Currency: USD

PROFESSIONAL SERVICES

  Enterprise software implementation         $4,500.00
  Custom API integration (80 hrs @ $25)      $2,000.00
  Project management and documentation       $1,000.00

Subtotal:   $7,500.00
Tax (0%):       $0.00
Total Due:  $7,500.00

Payment Terms: Net 30
Bank: Wells Fargo Business
Account: 7734-XXXX-XXXX
Routing: 121000248

Thank you for choosing Meridian.
"""

# Fresh manager-tier invoice with unique content (for duplicate-free testing)
# Note: no shipping/delivery line to avoid math ambiguity in extractor
fresh_manager_content = """\
INVOICE

Vendor: Pinnacle Office Supplies Co.
Address: 789 Commerce Ave, Houston TX 77002
Tax ID: 76-1234567

Bill To: qClerq Inc.
Invoice Number: POS-2026-0201
Invoice Date: 2026-05-14
Due Date: 2026-06-13
Currency: USD

LINE ITEMS

  Ergonomic office chairs (x4)              $1,200.00
  Standing desks (x2)                         $800.00
  Monitor mounts (x6)                         $150.00

Subtotal:   $2,150.00
Tax (0%):       $0.00
Total Due:  $2,150.00

Payment Terms: Net 30
Bank: Chase Business Banking
Account: 5519-XXXX-XXXX
Routing: 021000021

Thank you for your business.
"""

make_pdf("scripts/Invoice-ACS-2026-0142-manager.pdf", manager_content)
make_pdf("scripts/Invoice-MES-2026-0089-cfo.pdf", cfo_content)
make_pdf("scripts/Invoice-POS-2026-0201-manager.pdf", fresh_manager_content)
print("Done! Created manager and CFO tier test invoices.")
