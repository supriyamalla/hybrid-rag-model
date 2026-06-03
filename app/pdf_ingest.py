"""PDF ingestion — local pipeline that replaces the hand-written corpus.

Pipeline (runs on your laptop):
  1. For every PDF in data/pdfs/:
       a. Extract full text with pymupdf.
       b. Call Claude Haiku ONCE per PDF on the opening text to extract structured
          metadata: {product, region, doc_type, title}. The model is constrained to
          the canonical vocabularies from app.semantic_layer (so the router's filters
          continue to bind to real values).
       c. Defensively drop product/region values not in the vocabulary.
  2. Write the result to data/support_corpus.json — same shape the Databricks
     ingestion notebook already consumes (doc_id, title, doc_type, product, region,
     content). The Databricks cell then chunks the content per doc and pushes to
     Vector Search exactly as before.

Run:
    python -m app.pdf_ingest
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import fitz  # pymupdf
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

from app import config
from app.semantic_layer import KNOWN_PRODUCTS, KNOWN_REGIONS, KNOWN_DOC_TYPES


ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "pdfs"
CORPUS_PATH = ROOT / "data" / "support_corpus.json"

# Send only the opening text to the tagger — enough to identify product and topic
# without spending tokens on the full doc body.
METADATA_PEEK_CHARS = 3000


TAG_SYSTEM_PROMPT = f"""\
You tag pharmaceutical support documents with structured metadata.

Given the PDF filename and the opening text of a document, return JSON with:

- product: one of {KNOWN_PRODUCTS}, or null if the document is product-agnostic.
- region: one of {KNOWN_REGIONS}, or null if not region-specific.
- doc_type: one of {KNOWN_DOC_TYPES}. Definitions:
    - prior_auth          payer authorization workflows, PA submissions, appeals
    - dosing              drug dosing, titration, administration, dose modifications
    - patient_assistance  PAP, co-pay support, financial assistance programs
    - adverse_events      safety profile, AE reporting policy, side effects
    - formulary           payer formulary tier placement, regional payer coverage
    - competitive         comparison to competitor products / approved messaging
    - objection_handling  scripted responses to field objections
  For multi-topic docs, pick the DOMINANT topic.
- title: a clean human-readable title for the doc.

Rules:
- If a product or region is named but is NOT in the canonical list, return null.
  Do not invent values.
- doc_type is mandatory.
- Return ONLY valid JSON in this exact shape (no prose, no markdown, no code fences):
{{
  "product": string | null,
  "region": string | null,
  "doc_type": string,
  "title": string
}}
"""


def _extract_text(pdf_path: Path) -> str:
    parts: list[str] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            parts.append(page.get_text())
    return "\n".join(parts).strip()


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


async def _tag_async(filename: str, text_peek: str) -> dict:
    options = ClaudeAgentOptions(
        model=config.ROUTER_MODEL,
        system_prompt=TAG_SYSTEM_PROMPT,
        allowed_tools=[],
    )
    user_prompt = f"PDF filename: {filename}\n\nOpening text:\n{text_peek}"
    raw: str | None = None
    async for msg in query(prompt=user_prompt, options=options):
        if isinstance(msg, ResultMessage):
            raw = msg.result
    if not raw:
        raise RuntimeError(f"Tagger returned no result for {filename}")
    return _parse_json(raw)


def tag_pdf(filename: str, full_text: str) -> dict:
    return asyncio.run(_tag_async(filename, full_text[:METADATA_PEEK_CHARS]))


def _doc_id_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    return re.sub(r"[^A-Z0-9]+", "-", stem.upper()).strip("-")


def ingest_all() -> list[dict]:
    if not PDF_DIR.exists():
        raise RuntimeError(f"PDF directory not found: {PDF_DIR}. Run `python -m scripts.build_synthetic_pdfs` first.")
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        raise RuntimeError(f"No PDFs found in {PDF_DIR}.")

    docs: list[dict] = []
    for pdf in pdfs:
        print(f"[..] {pdf.name}")
        text = _extract_text(pdf)
        if not text:
            print("     skipped — no text extracted")
            continue

        tags = tag_pdf(pdf.name, text)
        product = tags.get("product") if tags.get("product") in KNOWN_PRODUCTS else None
        region = tags.get("region") if tags.get("region") in KNOWN_REGIONS else None
        doc_type = tags.get("doc_type")
        if doc_type not in KNOWN_DOC_TYPES:
            print(f"     [warn] LLM returned doc_type={doc_type!r}, falling back to 'prior_auth'")
            doc_type = "prior_auth"

        docs.append({
            "doc_id": _doc_id_from_filename(pdf.name),
            "title": tags.get("title") or Path(pdf.name).stem.replace("_", " "),
            "doc_type": doc_type,
            "product": product,
            "region": region,
            "content": text,
        })
        print(f"     tagged: product={product}, region={region}, doc_type={doc_type}")
    return docs


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    docs = ingest_all()
    CORPUS_PATH.write_text(
        json.dumps(docs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n[ok] wrote {len(docs)} docs to {CORPUS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
