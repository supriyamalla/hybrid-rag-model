# Analytics copilot — hybrid RAG

A support assistant for pharma sales reps. A router classifies each question and sends
it down the right path:

- **analytical** → Text-to-SQL over the existing `pharma_sales` Databricks tables
- **knowledge** → document RAG over a rep-support corpus (built from synthetic PDFs) in Databricks Vector Search
- **hybrid** → both, merged by Claude into one cited answer

The differentiator is the **router + semantic layer**, not the model.

## Stack

Python · Streamlit · Databricks (SQL warehouse + Vector Search) · **Claude Agent SDK** (subscription auth — no API key billed).

- **Routing:** Claude Haiku (`ROUTER_MODEL`)
- **SQL generation & answer synthesis:** Claude Sonnet (`GENERATION_MODEL`)
- **Embeddings:** Databricks managed embedding endpoint (`EMBEDDING_ENDPOINT`) — NOT Claude

## Setup

```bash
python -m venv .venv
. .venv/Scripts/activate          # Windows; use source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env              # fill in Databricks values; Claude auth is separate

# Authenticate with your Claude subscription (Pro / Max). Opens a browser once.
claude login
```

> **Important:** if `ANTHROPIC_API_KEY` is set in your environment, the SDK uses
> that and bills per call instead of drawing from your subscription. Unset it
> (`Remove-Item Env:ANTHROPIC_API_KEY` on PowerShell) before running.

## Run order

1. **Router smoke test** (no Databricks required, just `claude login`):
   ```
   python -m app.router
   ```
2. **Build the support corpus from PDFs** (local — needs `claude login` for tagging):
   ```
   python -m scripts.build_synthetic_pdfs   # render data/pdf_source.json → data/pdfs/*.pdf
   python -m app.pdf_ingest                 # pymupdf text extract + Claude auto-tagging → data/support_corpus.json
   ```
3. **Ingest the corpus into Vector Search** — run `app/ingest.py` as a Databricks notebook inside the
   workspace. Chunks the corpus, writes the Delta table, and creates the Vector Search endpoint + Delta Sync index.
4. **Launch the app:**
   ```
   streamlit run app/streamlit_app.py
   ```

## Layout

```
app/
  config.py          # loads .env
  semantic_layer.py  # schema + metric definitions for the SQL agent
  router.py          # question classifier + metadata extractor (the differentiator)
  pdf_ingest.py      # PDF → text (pymupdf) → Claude auto-tagging → support_corpus.json
  ingest.py          # corpus → Delta → Vector Search index (run on Databricks)
  sql_agent.py       # analytical path
  rag_agent.py       # knowledge path (filtered retrieval)
  orchestrator.py    # ties router + agents + synthesis
  streamlit_app.py   # chat UI
scripts/
  build_synthetic_pdfs.py  # render data/pdf_source.json → data/pdfs/*.pdf (reportlab)
data/
  pdf_source.json      # source content for the synthetic PDFs
  pdfs/                # generated PDFs — the raw corpus input
  support_corpus.json  # extracted + tagged corpus — the Vector Search input
```

See `plan.md` for the step-by-step build plan.
