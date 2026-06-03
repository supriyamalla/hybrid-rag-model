# Build plan — Pharma rep copilot (hybrid RAG)

> **For the assistant reading this (Claude Code):** Work through this **one step at a
> time**. For each step, briefly explain *why* we're doing it, make the change, run the
> verification, and then **stop and wait for me to confirm** before starting the next
> step. Don't batch steps. Keep secrets out of code (everything goes in `.env`). Don't
> recreate files that already exist — verify them instead.

## What we're building

A support assistant for pharma sales reps. A rep asks a question in a chat UI; a
**router** classifies it and sends it down the right path:

- **analytical** ("Q3 target attainment in the Northeast?") → Text-to-SQL over the
  existing `pharma_sales` Databricks tables.
- **knowledge** ("how does prior auth work for Pembrolizumab?") → document RAG over a
  synthetic rep-support corpus in Databricks Vector Search.
- **hybrid** → both, then merged.

Either way, Claude composes one grounded answer that **cites** where each fact came
from (a SQL result or a specific support doc).

**The differentiator is the router + semantic layer, not the model.** A naive RAG bot
just embeds the question and does top-k cosine. This one classifies the question and
extracts BI metadata filters (product, doc type, region) that constrain retrieval —
semantic search narrowed by structured attributes.

## Architecture

```
rep → Streamlit → router ─┬─ analytical → sql_agent  → Databricks SQL (pharma_sales)
                          └─ knowledge  → rag_agent  → Databricks Vector Search
                                         → Claude (grounded answer + citations)

offline (one-time): support_corpus.json → chunk → managed embeddings → Vector Search index
```

## Decisions already made (do not re-litigate)

- **Stack:** Python + Streamlit, Databricks backend, Claude API.
- **Hybrid routing**, not pure document RAG and not pure Text-to-SQL.
- **Databricks-managed embeddings** (Delta Sync index). Databricks computes the vectors
  and embeds the query automatically at search time — no embedding code on the query side.
- **Claude API** is used at three points: routing (Haiku), SQL generation, and answer
  synthesis (Sonnet). Models are set in `.env` (`ROUTER_MODEL`, `GENERATION_MODEL`).
- **Embeddings are NOT Claude** — they come from a Databricks embedding endpoint
  (`EMBEDDING_ENDPOINT`, e.g. `databricks-gte-large-en`).
- The structured `pharma_sales` tables (products, regions, sales_reps, sales, targets,
  prescribers) are **already built** in Databricks.
- The support corpus is **synthetic** and for demo use only.

## What already exists (verify, don't recreate)

- `README.md` — overview and run order.
- `requirements.txt`, `.env.example` — deps and config template.
- `data/support_corpus.json` — synthetic rep-support knowledge base (11 docs).
- `app/config.py` — loads settings from `.env`.
- `app/semantic_layer.py` — schema, metric definitions, disambiguation rules; renders
  the BI context the SQL agent uses.
- `app/router.py` — classifies the question and extracts metadata filters. **(The
  differentiator. Read this first.)**
- `app/ingest.py` — chunks the corpus, writes a Unity Catalog Delta table, creates the
  managed-embedding Vector Search index. Meant to run as a Databricks notebook/job.

**To build:** `app/sql_agent.py`, `app/rag_agent.py`, `app/orchestrator.py`,
`app/streamlit_app.py`.

## Constraints & gotchas

- **Unity Catalog required for Vector Search.** The corpus chunks table must live in a UC
  catalog, not `hive_metastore`. The `pharma_sales` tables being in `hive_metastore` is
  fine for the SQL path, but set `DATABRICKS_CATALOG`/`SCHEMA` to a UC location for the corpus.
- **Embedding endpoint must already exist** in the workspace (`EMBEDDING_ENDPOINT`).
- **`ingest.py` runs inside Databricks** (it uses the in-workspace Spark session to write
  the Delta table). The rest of the app connects remotely via the SQL connector + Vector
  Search SDK using `DATABRICKS_HOST`/`DATABRICKS_TOKEN`/`DATABRICKS_HTTP_PATH`.
- **Read-only SQL.** The SQL agent must only ever emit a single `SELECT`. Reject anything
  with DDL/DML keywords before executing.
- Keep `.env` out of git (add a `.gitignore`).

---

## Steps

### Step 1 — Run it and confirm the router works
**Why:** Smallest possible proof the plumbing is connected. The router only needs the
Claude API key — no Databricks — so it isolates one variable: is the API key + config +
core logic wired correctly?
**Tasks:** Create venv, `pip install -r requirements.txt`, `cp .env.example .env`, fill in
`ANTHROPIC_API_KEY` only.
**Done when:** `python -m app.router` prints three sample questions, each labeled
analytical/knowledge/hybrid with any extracted product/region.

### Step 2 — Stand up the document side (run ingestion)
**Why:** The SQL side already exists; the document side has no index yet. Nothing on the
knowledge path can work until the corpus is embedded and indexed.
**Tasks:** Confirm a UC catalog/schema and an embedding endpoint. Open `app/ingest.py` as
a Databricks notebook, set the env/constants, run it (creates the Delta table + endpoint +
Delta Sync index, then triggers a sync). Endpoint provisioning can take a few minutes;
re-run if the endpoint wasn't ready.
**Done when:** The index shows a completed sync, and a quick `index.similarity_search(...)`
returns a few chunks.

### Step 3 — Build `app/sql_agent.py` (analytical path)
**Why:** This is the Text-to-SQL half. It turns a rep's plain-English analytical question
into correct Spark SQL using the semantic layer, so answers are grounded in real numbers.
**Tasks:** Connect with `databricks-sql-connector`. Build a prompt from
`semantic_layer.get_schema_context()` + the question + any router-extracted scope. Ask the
generation model for a single `SELECT`. Validate it's read-only. Execute and return the
rows **plus the SQL** (for transparency/citation).
**Done when:** A function `run_sql_question(question) -> {sql, rows}` returns correct
results for "total revenue by product in the Northeast".

### Step 4 — Build `app/rag_agent.py` (knowledge path)
**Why:** This is the document-RAG half, and where the differentiator lives — retrieval is
**filtered** by the router's extracted metadata, not blind top-k.
**Tasks:** Use `VectorSearchClient` to get the index. Call
`similarity_search(query_text=question, columns=[...], num_results=TOP_K, filters={...})`
where `filters` is built from the router decision (product / doc_type / region). Drop
chunks below `SIMILARITY_THRESHOLD`. Return chunks with title, doc_id, content, score.
**Done when:** A function `retrieve(question, filters) -> [chunks]` returns relevant,
correctly-scoped support chunks, and the filter visibly narrows results.

### Step 5 — Build `app/orchestrator.py` (tie it together)
**Why:** This is the conductor: route → run the right path(s) → have Claude synthesize one
grounded, cited answer. It's what makes the two halves feel like one assistant.
**Tasks:** Call `router.classify()`. If `needs_sql`, run the SQL agent; if `needs_docs`,
run the RAG agent. Assemble a context block (SQL result table + retrieved chunks). Call the
generation model with a synthesis prompt that **requires citations** (the SQL query and/or
doc titles) and forbids unsupported claims. Return `{answer, route, sql, sources}`.
**Done when:** Analytical, knowledge, and hybrid questions each return a sensible cited answer.

### Step 6 — Build `app/streamlit_app.py` (the UI)
**Why:** Gives the rep an actual chat to use and makes the routing/sources visible — good
for a demo and for debugging.
**Tasks:** `st.chat_input` + history, call the orchestrator, render the answer, and show an
expander with the route taken, the SQL run, and the retrieved sources.
**Done when:** `streamlit run app/streamlit_app.py` answers all three question types and
shows where each answer came from.

### Step 7 (stretch) — Evaluation harness
**Why:** Building RAG is easy; knowing it's reliable is the hard part. A small Q/C/A check
set (does retrieval return relevant context? does the answer use it? is it correct?) is a
strong portfolio addition and pure BI rigor.
**Tasks:** A handful of labeled questions + simple retrieval and faithfulness checks.

## References

- Databricks Vector Search (create/query indexes): https://docs.databricks.com/aws/en/vector-search/vector-search
- Anthropic SDK / Messages API: https://docs.claude.com/en/api/overview
