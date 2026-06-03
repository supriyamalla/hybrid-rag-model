"""Corpus ingestion — RUN THIS INSIDE A DATABRICKS NOTEBOOK.

What it does:
  1. Loads data/support_corpus.json.
  2. Chunks each doc (small corpus → simple paragraph-level chunking).
  3. Writes the chunks to a Delta table in Unity Catalog (CDF enabled, required for
     Delta Sync indexes).
  4. Ensures a Vector Search endpoint exists.
  5. Creates a Delta Sync index with MANAGED EMBEDDINGS — Databricks computes the
     vectors using EMBEDDING_ENDPOINT and re-embeds at query time. No embedding code
     lives on the application side.
  6. Triggers an initial sync.

Why a notebook: this script writes to a Delta table via the in-workspace Spark session.
Outside Databricks there is no Spark session, so it cannot run from the laptop.

The application side (rag_agent.py, orchestrator.py) talks to the index remotely via the
Vector Search SDK + a personal access token — that part does NOT need Spark.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

# Inside a Databricks notebook, `spark` is provided automatically. For local-import
# linting, we fall back to a no-op so this file can be imported without Spark.
try:
    spark  # type: ignore[name-defined]
except NameError:
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()

from databricks.vector_search.client import VectorSearchClient

from app import config

CHUNK_TARGET_CHARS = 800   # paragraph-level; the corpus is short, so most docs will be 1 chunk


# ---------- 1. Load + chunk ----------

def _paragraph_chunks(text: str, target: int = CHUNK_TARGET_CHARS) -> list[str]:
    """Greedy chunking: accumulate paragraphs until ~target chars, then cut."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    chunks, buf = [], ""
    for p in paragraphs:
        if buf and len(buf) + len(p) + 2 > target:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


def build_rows(corpus_path: str | Path) -> list[dict]:
    docs = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
    rows = []
    for d in docs:
        for i, chunk in enumerate(_paragraph_chunks(d["content"])):
            rows.append({
                "chunk_id": f"{d['doc_id']}-{i:02d}",
                "doc_id":   d["doc_id"],
                "title":    d["title"],
                "doc_type": d["doc_type"],
                "product":  d.get("product"),
                "region":   d.get("region"),
                "content":  chunk,
            })
    return rows


# ---------- 2. Write Delta table ----------

def write_delta(rows: list[dict]) -> None:
    df = spark.createDataFrame(rows)
    spark.sql(f"CREATE CATALOG IF NOT EXISTS {config.DATABRICKS_CATALOG}")
    spark.sql(f"CREATE SCHEMA  IF NOT EXISTS {config.DATABRICKS_CATALOG}.{config.DATABRICKS_SCHEMA}")
    (df.write
       .mode("overwrite")
       .option("overwriteSchema", "true")
       .saveAsTable(config.SOURCE_TABLE_FQN))
    # CDF is required for Delta Sync indexes.
    spark.sql(f"ALTER TABLE {config.SOURCE_TABLE_FQN} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
    print(f"[ok] wrote {len(rows)} rows to {config.SOURCE_TABLE_FQN}")


# ---------- 3. Endpoint + index ----------

def ensure_endpoint(vsc: VectorSearchClient) -> None:
    existing = {e["name"] for e in vsc.list_endpoints().get("endpoints", [])}
    if config.VECTOR_SEARCH_ENDPOINT in existing:
        print(f"[ok] endpoint {config.VECTOR_SEARCH_ENDPOINT} already exists")
        return
    print(f"[..] creating endpoint {config.VECTOR_SEARCH_ENDPOINT} (provisioning can take a few minutes)")
    vsc.create_endpoint(name=config.VECTOR_SEARCH_ENDPOINT, endpoint_type="STANDARD")
    # Poll until ONLINE.
    for _ in range(60):
        info = vsc.get_endpoint(config.VECTOR_SEARCH_ENDPOINT)
        state = info.get("endpoint_status", {}).get("state", "UNKNOWN")
        print(f"     endpoint state = {state}")
        if state == "ONLINE":
            return
        time.sleep(15)
    raise RuntimeError("Endpoint did not become ONLINE in time; re-run after a few minutes.")


def ensure_index(vsc: VectorSearchClient) -> None:
    try:
        vsc.get_index(endpoint_name=config.VECTOR_SEARCH_ENDPOINT, index_name=config.INDEX_FQN)
        print(f"[ok] index {config.INDEX_FQN} already exists; triggering sync")
        idx = vsc.get_index(endpoint_name=config.VECTOR_SEARCH_ENDPOINT, index_name=config.INDEX_FQN)
        idx.sync()
        return
    except Exception:
        pass

    print(f"[..] creating delta-sync index {config.INDEX_FQN}")
    vsc.create_delta_sync_index(
        endpoint_name=config.VECTOR_SEARCH_ENDPOINT,
        index_name=config.INDEX_FQN,
        source_table_name=config.SOURCE_TABLE_FQN,
        pipeline_type="TRIGGERED",
        primary_key="chunk_id",
        embedding_source_column="content",
        embedding_model_endpoint_name=config.EMBEDDING_ENDPOINT,
    )
    print(f"[ok] index created; initial sync running in background")


# ---------- 4. Quick smoke test ----------

def smoke_test(vsc: VectorSearchClient) -> None:
    idx = vsc.get_index(endpoint_name=config.VECTOR_SEARCH_ENDPOINT, index_name=config.INDEX_FQN)
    res = idx.similarity_search(
        query_text="how does prior authorization work for Pembrolizumab?",
        columns=["chunk_id", "title", "doc_type", "product", "content"],
        num_results=3,
    )
    rows = res.get("result", {}).get("data_array", [])
    print(f"[smoke] returned {len(rows)} chunks")
    for r in rows:
        print("  -", r[:4])


def main() -> None:
    corpus_path = Path(__file__).resolve().parent.parent / "data" / "support_corpus.json"
    rows = build_rows(corpus_path)
    write_delta(rows)

    vsc = VectorSearchClient(disable_notice=True)
    ensure_endpoint(vsc)
    ensure_index(vsc)
    # Initial sync is async — give it a moment, then peek.
    time.sleep(10)
    try:
        smoke_test(vsc)
    except Exception as e:
        print(f"[smoke] index not ready yet ({e}); re-run smoke_test() once sync completes")


if __name__ == "__main__":
    main()
