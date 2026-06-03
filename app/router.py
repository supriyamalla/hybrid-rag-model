"""Router — classify a rep's question and extract BI metadata filters.

This is the differentiator. A naive RAG bot embeds the question and does top-k cosine.
Here, we first decide:
  - WHICH data path(s) are needed (analytical / knowledge / hybrid), and
  - WHICH structured attributes (product, region, doc_type) scope the answer.

The extracted scope narrows both the SQL WHERE clause and the Vector Search filters,
so retrieval is semantic search constrained by BI metadata.

Auth: uses the Claude Agent SDK with subscription OAuth (`claude login`). No API key
required. Tools are disabled — the router is a single-turn JSON classifier.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, asdict, field
from typing import Literal

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

from app import config
from app.semantic_layer import KNOWN_PRODUCTS, KNOWN_REGIONS, KNOWN_DOC_TYPES

Route = Literal["analytical", "knowledge", "hybrid"]


@dataclass
class RouterDecision:
    route: Route
    needs_sql: bool
    needs_docs: bool
    filters: dict = field(default_factory=dict)  # {product, region, doc_type}
    reasoning: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


SYSTEM_PROMPT = f"""\
You are the router for a pharma sales-rep copilot. For every question, you decide which
data path(s) to use and extract structured metadata that scopes the answer.

There are three routes:

- analytical: the question asks for numbers, trends, rankings, or comparisons that come
  from the sales database (revenue, units, target attainment, by product / region / rep
  / quarter, etc.).
- knowledge: the question asks about policy, process, dosing, prior auth, patient
  assistance, adverse-event reporting, competitive comparisons, objection handling, or
  formulary status — answered from internal support documents.
- hybrid: the question needs both. Example: "Why did Cardiox underperform target in the
  Northeast last quarter, and what's the formulary situation there?" (numbers + docs).

Also extract:
- product: the specific product the question is about, if any. Must be one of:
  {KNOWN_PRODUCTS} — or null if the question is product-agnostic.
- region: the specific region, if any. Must be one of: {KNOWN_REGIONS} — or null.
- doc_type: the type of support doc, if the question implies one. Must be one of:
  {KNOWN_DOC_TYPES} — or null.

Rules:
- Only set a filter if it is explicitly named or strongly implied by the question.
- If the user names a product/region not in the known lists, set the filter to null
  (do not invent values).
- Return ONLY valid JSON in this exact shape, with no prose, no markdown, no code
  fences:
{{
  "route": "analytical" | "knowledge" | "hybrid",
  "filters": {{
    "product":  string | null,
    "region":   string | null,
    "doc_type": string | null
  }},
  "reasoning": short explanation (one sentence)
}}
"""


def _parse_json(text: str) -> dict:
    text = text.strip()
    # Strip accidental code fences just in case.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


async def _classify_async(question: str) -> RouterDecision:
    options = ClaudeAgentOptions(
        model=config.ROUTER_MODEL,
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=[],  # no tools — single-turn JSON classification
    )
    raw: str | None = None
    async for msg in query(prompt=question, options=options):
        if isinstance(msg, ResultMessage):
            raw = msg.result
    if not raw:
        raise RuntimeError("Router returned no result from the Agent SDK.")

    data = _parse_json(raw)

    route: Route = data.get("route", "knowledge")
    if route not in ("analytical", "knowledge", "hybrid"):
        route = "knowledge"

    filters = data.get("filters") or {}
    # Drop any value not in our known vocabularies (defensive against hallucinated values).
    clean = {
        "product": filters.get("product") if filters.get("product") in KNOWN_PRODUCTS else None,
        "region": filters.get("region") if filters.get("region") in KNOWN_REGIONS else None,
        "doc_type": filters.get("doc_type") if filters.get("doc_type") in KNOWN_DOC_TYPES else None,
    }

    return RouterDecision(
        route=route,
        needs_sql=route in ("analytical", "hybrid"),
        needs_docs=route in ("knowledge", "hybrid"),
        filters=clean,
        reasoning=data.get("reasoning", "").strip(),
    )


def classify(question: str) -> RouterDecision:
    """Synchronous wrapper around the async Agent SDK call."""
    return asyncio.run(_classify_async(question))


SAMPLE_QUESTIONS = [
    "What was Q3 2025 target attainment for Cardiozen in the Northeast?",
    "How does prior authorization work for Oncorix?",
    "Cardiozen underperformed target in the Northeast last quarter — what's the formulary situation there?",
]


def _demo() -> None:
    print(f"Router model: {config.ROUTER_MODEL}\n")
    for q in SAMPLE_QUESTIONS:
        d = classify(q)
        print(f"Q: {q}")
        print(f"  route:    {d.route}")
        print(f"  filters:  {d.filters}")
        print(f"  reason:   {d.reasoning}")
        print()


if __name__ == "__main__":
    import sys

    # Windows consoles default to cp1252; force UTF-8 so the em-dash in the
    # sample questions prints correctly instead of as a replacement char.
    sys.stdout.reconfigure(encoding="utf-8")
    _demo()
