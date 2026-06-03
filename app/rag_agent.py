"""RAG agent — filtered semantic retrieval over the support corpus index.

The differentiator vs naive top-K cosine: retrieval is **filtered** by metadata the
router extracted (product / region / doc_type). Same vector similarity, narrower
candidate set. The synthesis step (Step 5) only ever sees chunks that match the BI
scope the user actually asked about.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from databricks.vector_search.client import VectorSearchClient

from app import config


# Lazy index handle so importing this module makes no network call.
_INDEX: Any | None = None


def _get_index():
    global _INDEX
    if _INDEX is None:
        vsc = VectorSearchClient(disable_notice=True)
        _INDEX = vsc.get_index(
            endpoint_name=config.VECTOR_SEARCH_ENDPOINT,
            index_name=config.INDEX_FQN,
        )
    return _INDEX


@dataclass
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    title: str
    doc_type: str
    product: str | None
    region: str | None
    content: str
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


# Columns we ask Vector Search to return. Score is appended automatically by the API.
_COLUMNS = ["chunk_id", "doc_id", "title", "doc_type", "product", "region", "content"]


def _build_filters(filters: dict | None) -> dict | None:
    """Translate the router's filter dict into a Vector Search filter spec.

    Keys with None values are dropped (no constraint). Multiple keys are AND-ed by
    Vector Search. Returns None if no constraints remain so the SDK omits the
    `filters` argument entirely.
    """
    if not filters:
        return None
    spec = {k: v for k, v in filters.items() if v is not None and k in ("product", "region", "doc_type")}
    return spec or None


def retrieve(question: str, filters: dict | None = None) -> list[RetrievedChunk]:
    """Return up to TOP_K chunks matching `question`, narrowed by router filters.

    Chunks scoring below SIMILARITY_THRESHOLD are dropped (junk guard).
    """
    idx = _get_index()
    kwargs: dict[str, Any] = {
        "query_text": question,
        "columns": _COLUMNS,
        "num_results": config.TOP_K,
        "disable_notice": True,  # similarity_search re-emits the auth notice per call
    }
    vs_filters = _build_filters(filters)
    if vs_filters:
        kwargs["filters"] = vs_filters

    res = idx.similarity_search(**kwargs)

    # Score is appended to the column list; locate it by name so order changes don't break us.
    column_names = [c["name"] for c in res.get("manifest", {}).get("columns", [])]
    score_idx = column_names.index("score")
    field_idx = {name: i for i, name in enumerate(column_names)}

    chunks: list[RetrievedChunk] = []
    for row in res.get("result", {}).get("data_array", []):
        score = float(row[score_idx])
        if score < config.SIMILARITY_THRESHOLD:
            continue
        chunks.append(RetrievedChunk(
            chunk_id=row[field_idx["chunk_id"]],
            doc_id=row[field_idx["doc_id"]],
            title=row[field_idx["title"]],
            doc_type=row[field_idx["doc_type"]],
            product=row[field_idx["product"]],
            region=row[field_idx["region"]],
            content=row[field_idx["content"]],
            score=score,
        ))
    return chunks


def _demo() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    cases: list[tuple[str, dict | None]] = [
        ("how does prior authorization work?", None),
        ("how does prior authorization work?", {"product": "Oncorix"}),
        ("what's the formulary situation in the Northeast?", {"region": "Northeast"}),
    ]
    for q, f in cases:
        print(f"Q: {q}")
        print(f"   filters: {f}")
        results = retrieve(q, f)
        if not results:
            print("   (no chunks above threshold)")
        for c in results:
            print(f"   [{c.score:.3f}]  {c.title}  ({c.product or '—'} / {c.doc_type})")
        print()


if __name__ == "__main__":
    _demo()
