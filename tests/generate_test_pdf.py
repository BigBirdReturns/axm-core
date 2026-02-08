#!/usr/bin/env python3
"""Generate a test PDF with native text for routing tests."""

import sys
from pathlib import Path

try:
    import pymupdf
except ImportError:
    print("PyMuPDF not installed, cannot generate test PDF")
    sys.exit(1)

# Test content
content = """Hemostatic Agents

Hemostatic agents are substances that stop bleeding by promoting clot formation.

Common hemostatic agents include:
• Tranexamic acid - inhibits fibrinolysis
• Thrombin - directly converts fibrinogen to fibrin  
• Gelatin sponges - provide matrix for clot formation
• Combat gauze - kaolin-impregnated gauze for field use

These agents are critical in trauma care and surgical settings.
"""

# Create PDF
output_path = Path(__file__).parent / "fixtures" / "test_native.pdf"
output_path.parent.mkdir(parents=True, exist_ok=True)

doc = pymupdf.open()
page = doc.new_page(width=595, height=842)  # A4 size

# Add text
text_rect = pymupdf.Rect(50, 50, 545, 792)
page.insert_textbox(
    text_rect,
    content,
    fontsize=11,
    fontname="helv",
    align=pymupdf.TEXT_ALIGN_LEFT,
)

# Save
doc.save(str(output_path))
doc.close()

print(f"✅ Created test PDF: {output_path}")
print(f"   Size: {output_path.stat().st_size} bytes")
