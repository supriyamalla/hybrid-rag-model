"""Generate synthetic pharma rep-support PDFs from data/pdf_source.json.

Run once (or whenever pdf_source.json changes):
    python -m scripts.build_synthetic_pdfs

Output: data/pdfs/*.pdf
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "pdf_source.json"
OUT_DIR = ROOT / "data" / "pdfs"


def _styles():
    base = getSampleStyleSheet()
    base.add(ParagraphStyle(
        name="DocSubtitle",
        parent=base["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=11,
        textColor="#555555",
        spaceAfter=18,
    ))
    base.add(ParagraphStyle(
        name="SectionHeading",
        parent=base["Heading2"],
        fontSize=14,
        spaceBefore=14,
        spaceAfter=6,
        textColor="#222222",
    ))
    base.add(ParagraphStyle(
        name="DocBody",
        parent=base["Normal"],
        fontSize=10.5,
        leading=14,
        spaceAfter=8,
    ))
    return base


def _build_pdf(doc_data: dict, out_path: Path) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        title=doc_data["title"],
    )
    story: list = [Paragraph(html.escape(doc_data["title"]), styles["Title"])]
    if doc_data.get("subtitle"):
        story.append(Paragraph(html.escape(doc_data["subtitle"]), styles["DocSubtitle"]))
    story.append(Spacer(1, 0.15 * inch))

    for section in doc_data["sections"]:
        story.append(Paragraph(html.escape(section["heading"]), styles["SectionHeading"]))
        for para in section["body"].split("\n\n"):
            para = para.strip()
            if not para:
                continue
            story.append(Paragraph(html.escape(para), styles["DocBody"]))

    doc.build(story)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    for doc_data in source["docs"]:
        out_path = OUT_DIR / doc_data["filename"]
        _build_pdf(doc_data, out_path)
        print(f"[ok] wrote {out_path.relative_to(ROOT)}")
    print(f"\nGenerated {len(source['docs'])} PDFs in {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
