"""Orchestrator — ties router + SQL agent + RAG agent + Claude synthesis into one
grounded, cited answer. This is what makes the two halves feel like one assistant.

Pipeline:
  1. router.classify(question)        → route + filters
  2. sql_agent.run_sql_question(...)  → SQL + rows           (if needs_sql)
  3. rag_agent.retrieve(...)          → ranked chunks         (if needs_docs)
  4. Claude Sonnet synthesis prompt   → one grounded answer with inline citations
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict, field
from typing import Any

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

from app import config
from app import router as router_mod
from app import sql_agent
from app import rag_agent


@dataclass
class CopilotResult:
    question: str
    route: str
    reasoning: str
    filters: dict
    answer: str
    sql: str | None = None
    sql_error: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)   # [{doc_id, title, score}]

    def to_dict(self) -> dict:
        return asdict(self)


SYNTHESIS_SYSTEM_PROMPT = """\
You are a copilot for pharmaceutical sales representatives. Given the rep's question
and the CONTEXT below — which may contain a SQL result table and/or one or more
support documents — write ONE grounded answer.

Hard requirements:

1. Only state facts present in the CONTEXT. Do NOT use prior knowledge to fill gaps.
   If the context doesn't cover part of the question, say so explicitly.

2. Cite every factual claim inline:
   - For numbers / aggregates from the SQL result, append `[SQL]`.
   - For statements taken from a support doc, append `[<doc_id>]` (use the exact
     doc_id shown in the context, e.g. `[PA-ONCORIX-001]`).
   - A sentence that combines a number and a doc statement may carry both, e.g.
     "Revenue was $12M in Q3 [SQL], which is below target despite Tier-2 formulary
     placement in the region [FORM-CARDIOZEN-NE]."

3. If a SQL result is empty or returned an error, mention that briefly and answer
   what you can from the documents.

4. Keep it tight and professional. No marketing language, no preamble, no follow-up
   questions back to the rep. Aim for 4–8 sentences unless the question genuinely
   needs more.
"""


def _build_context_block(sql_result: dict | None, chunks: list) -> str:
    parts: list[str] = []
    if sql_result is not None:
        parts.append("=== SQL RESULT ===")
        if sql_result.get("sql_error"):
            parts.append(f"Query failed: {sql_result['sql_error']}")
        else:
            parts.append("Query:")
            parts.append(sql_result["sql"])
            parts.append("")
            parts.append(f"Columns: {sql_result['columns']}")
            parts.append("Rows:")
            if not sql_result["rows"]:
                parts.append("  (no rows returned)")
            else:
                for r in sql_result["rows"]:
                    parts.append(f"  {r}")
    if chunks:
        if parts:
            parts.append("")
        parts.append("=== SUPPORT DOCUMENTS ===")
        for c in chunks:
            header = (
                f"\n[{c.doc_id}] {c.title}  "
                f"(score={c.score:.3f}, doc_type={c.doc_type}, "
                f"product={c.product or '—'}, region={c.region or '—'})"
            )
            parts.append(header)
            parts.append(c.content)
    if not parts:
        parts.append("(no context retrieved)")
    return "\n".join(parts)


async def _synthesize_async(question: str, context: str) -> str:
    options = ClaudeAgentOptions(
        model=config.GENERATION_MODEL,
        system_prompt=SYNTHESIS_SYSTEM_PROMPT,
        allowed_tools=[],
    )
    user_prompt = f"REP QUESTION: {question}\n\nCONTEXT:\n{context}"
    raw: str | None = None
    async for msg in query(prompt=user_prompt, options=options):
        if isinstance(msg, ResultMessage):
            raw = msg.result
    if not raw:
        raise RuntimeError("Synthesis returned no result from the Agent SDK.")
    return raw.strip()


def answer(question: str) -> CopilotResult:
    decision = router_mod.classify(question)

    sql_result: dict | None = None
    if decision.needs_sql:
        try:
            sql_result = sql_agent.run_sql_question(question, scope=decision.filters)
        except Exception as e:
            # Don't crash the whole pipeline — surface the SQL failure to the
            # synthesis step so it can answer from docs (or apologize) and let
            # the UI show what went wrong.
            sql_result = {
                "sql": "",
                "columns": [],
                "rows": [],
                "sql_error": f"{type(e).__name__}: {e}",
            }

    chunks: list = []
    if decision.needs_docs:
        chunks = rag_agent.retrieve(question, filters=decision.filters)

    context = _build_context_block(sql_result, chunks)
    answer_text = asyncio.run(_synthesize_async(question, context))

    return CopilotResult(
        question=question,
        route=decision.route,
        reasoning=decision.reasoning,
        filters=decision.filters,
        answer=answer_text,
        sql=(sql_result or {}).get("sql") or None,
        sql_error=(sql_result or {}).get("sql_error"),
        columns=(sql_result or {}).get("columns") or [],
        rows=(sql_result or {}).get("rows") or [],
        sources=[
            {"doc_id": c.doc_id, "title": c.title, "score": c.score}
            for c in chunks
        ],
    )


SAMPLE_QUESTIONS = [
    "What was total revenue by product in the Northeast?",
    "How does prior authorization work for Oncorix?",
    "Cardiozen underperformed target in the Northeast last quarter — what's the formulary situation there?",
]


def _demo() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for q in SAMPLE_QUESTIONS:
        print("=" * 80)
        print(f"Q: {q}")
        r = answer(q)
        print(f"\nRoute:     {r.route}   (filters: {r.filters})")
        print(f"Reasoning: {r.reasoning}")
        if r.sql:
            print(f"\nSQL:\n  {r.sql}")
            print(f"Rows:    {len(r.rows)}")
        if r.sql_error:
            print(f"\nSQL ERROR: {r.sql_error}")
        if r.sources:
            print(f"\nSources:   {[s['doc_id'] for s in r.sources]}")
        print(f"\nAnswer:\n{r.answer}\n")


if __name__ == "__main__":
    _demo()
