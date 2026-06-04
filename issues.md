# Issues encountered & how we resolved them

A running log of friction points hit while following [plan.md](plan.md), with the resolution that worked. Add new entries as they come up.

---

## 1. Scaffold files didn't exist yet

**What we hit:** The plan said "verify, don't recreate" for a list of files (`config.py`, `semantic_layer.py`, `router.py`, `ingest.py`, etc.), but only `plan.md` existed in the working directory.

**Resolution:** Built the scaffold first as a pre-Step-1 phase: `requirements.txt`, `.env.example`, `.gitignore`, `README.md`, `data/support_corpus.json`, `app/__init__.py`, `app/config.py`, `app/semantic_layer.py`, `app/router.py`, `app/ingest.py`.

**Lesson:** Always verify file presence before assuming a plan's "already exists" list.

---

## 2. Switched from Claude API to Claude Agent SDK

**Decision:** Use `claude-agent-sdk` (Pro/Max subscription auth) instead of the raw `anthropic` SDK to avoid per-call API billing.

**Changes:**
- `requirements.txt`: `anthropic` → `claude-agent-sdk>=0.2.87`
- `app/router.py`: rewrote to use `query()` + `ClaudeAgentOptions` (async), with a sync `classify()` wrapper via `asyncio.run`
- `app/config.py`: dropped the `ANTHROPIC_API_KEY` constant — SDK handles auth
- `.env.example` + `README.md`: documented `claude login` setup and the env-var precedence trap (see #4)

**Cost model:** Subscription Agent SDK calls draw from a monthly credit pool (Pro: $20, Max 5x: $100, Max 20x: $200), billed at API rates. Not unlimited, but bundled with subscription.

---

## 3. `ModuleNotFoundError: claude_agent_sdk`

**Cause:** Either `pip install -r requirements.txt` hadn't run, or `python` was resolving to a system Python instead of the venv's Python.

**Resolution:**
```powershell
.\.venv\Scripts\Activate.ps1     # prompt should show (.venv)
Get-Command python               # path should be inside .venv\Scripts\
pip install -r requirements.txt
```

---

## 4. Misleading SDK error: `Claude Code returned an error result: success`

**What we saw:** Router crashed with this contradictory message. The actual underlying response was a `billing_error` with text "Credit balance is too low".

**Root cause:** `ANTHROPIC_API_KEY` was set at User scope on the machine and pointed to a depleted API key. The Agent SDK's auth precedence is:
1. `ANTHROPIC_API_KEY` env var (used here — wrong)
2. Subscription OAuth (what we wanted)

When the API key is set, it **always** wins over subscription auth, regardless of `claude login`.

**Diagnostic that found it:** A minimal probe script (`debug_sdk.py`) that printed every raw message yielded by `query()`. The init message showed `'apiKeySource': 'ANTHROPIC_API_KEY'` — proof the SDK was on the wrong auth path.

**Resolution:**
```powershell
# Find which scope it was set at:
[System.Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")

# Clear it permanently at that scope:
[System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", $null, "User")
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue

# Then RESTART VS Code so the env-var change reaches new processes.
```

**Always include this warning** in setup docs going forward.

---

## 5. VS Code Claude Code extension ≠ standalone CLI

**Confusion:** "Isn't Claude Code CLI part of VS Code where we're chatting?"

**Answer:** No. The VS Code extension is a self-contained integration. It does **not** put a `claude` command on the system PATH, and its credentials are not guaranteed to be visible to a Python subprocess running `claude-agent-sdk`.

**Resolution:** Install the standalone CLI separately:
```powershell
npm install -g @anthropic-ai/claude-code
claude --version
claude login   # opens browser; sign in with the claude.ai Pro/Max account
```

The bundled SDK binary reads from the credentials file the standalone `claude login` writes (`~/.claude/.credentials.json`).

---

## 6. Em-dash UnicodeEncodeError on Windows console

**What we hit:** `python -m app.router` was hitting encoding issues on the em-dash (`—`) in sample questions because Windows consoles default to cp1252.

**Resolution:** Added at the top of `__main__` in `app/router.py`:
```python
import sys
sys.stdout.reconfigure(encoding="utf-8")
```

---

## 7. `pharma_sales` not in `hive_metastore` after all

**What plan assumed:** Sales tables live in `hive_metastore.pharma_sales` (legacy location).

**What we found:** Running `SHOW TABLES IN hive_metastore.pharma_sales` returned `UC_HIVE_METASTORE_DISABLED_EXCEPTION` — the user's workspace has Hive Metastore disabled entirely (UC-only workspace). The sales tables actually live in `workspace.pharma_sales`.

**Resolution:** Updated `.env` and `.env.example`:
```
SALES_CATALOG=workspace
SALES_SCHEMA=pharma_sales
```

**Lesson:** Don't assume `hive_metastore` exists on modern Databricks workspaces. Check `SHOW CATALOGS` and try the actual namespace before committing to one in config.

---

## 8. `pharma_copilot` schema confusion

**Question:** "Why is `DATABRICKS_SCHEMA=pharma_copilot` set when I haven't set it anywhere?"

**Answer:** It was a default I (Claude) picked when writing `.env.example` — a placeholder name for a *new* schema where the support corpus chunks table will live (separate from `pharma_sales`, which is for the SQL agent). The schema doesn't exist yet; `ingest.py` creates it. Could be renamed freely.

**Lesson:** Surface picked defaults explicitly when introducing new config. Don't let invented names look authoritative.

---

## 9. `.env` file didn't exist yet, even after Step 1

**What happened:** Only `.env.example` existed. Step 1 (router) still worked because:
- Claude auth came from `claude login` (no env var needed)
- `ROUTER_MODEL` had a fallback default in `config.py`

But Steps 3+ need real Databricks credentials, so we needed a live `.env`:
```powershell
Copy-Item .env.example .env
```
Then fill in `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_HTTP_PATH` when needed.

---

## 10. Databricks Free Edition — no all-purpose clusters / no Libraries UI

**What we hit:** User is on Databricks **Free Edition**, which has no all-purpose clusters and no per-cluster **Libraries** tab. Compute is serverless-only. The "Cluster → Libraries → Install new" route I initially gave doesn't exist on Free Edition.

**Resolution:** Install libraries inside the notebook itself:
```python
%pip install databricks-vectorsearch
dbutils.library.restartPython()
```

**Open risk:** Vector Search **endpoint creation** may or may not be available on Free Edition — endpoint *listing* worked, but creation could fail with a tier/quota error. We'll find out when we run the ingestion cell.

---

## 12. Vector Search index provisioning takes 5–15 min on Free Edition

**What we hit:** Right after `create_delta_sync_index(...)` returns, calling `idx.describe()` or `idx.similarity_search(...)` raises `VectorSearchException: Something went wrong unexpectedly`. The index existed but wasn't queryable yet.

**Root cause:** First-time endpoint + index provisioning on Databricks Free Edition takes considerably longer than the docs suggest — closer to 5–15 minutes for embedding warm-up and the initial sync. During that window the API surfaces a generic "Something went wrong" error rather than a clear "still provisioning" status.

**Resolution:** Poll `idx.describe()["status"]` until `ready == True` before issuing any `similarity_search` calls:
```python
import time
for _ in range(30):
    s = idx.describe()["status"]
    print(f"ready={s.get('ready')}  state={s.get('detailed_state')}")
    if s.get("ready"): break
    time.sleep(30)
```

**Lesson:** Always wait for `ready=True` after creating a Vector Search index. The "background sync" message after `create_delta_sync_index` is misleading on Free Edition — it implies seconds, not minutes.

---

## 13. PDF-extracted text has no `\n\n` paragraph breaks — original chunker degraded to 1-chunk-per-doc

**What we hit:** After switching from hand-written JSON to PDFs, the existing paragraph-split chunker (`text.split("\n\n")`) found no paragraph breaks because pymupdf extracts PDF text with single `\n` between lines, not blank lines between paragraphs. Result: each 2000+ char PDF doc would become one giant chunk — bad for embedding quality and retrieval precision.

**Resolution:** Smarter chunker that tries paragraph split first, falls back to line-grouping for PDF-extracted text:
```python
def chunk(text, target=800):
    text = text.strip()
    if len(text) <= target:
        return [text]
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paras) <= 1:
        paras = [ln.strip() for ln in text.split("\n") if ln.strip()]
    # ... group into ~target-char chunks ...
```

**Lesson:** Any chunker assumption about input formatting needs to handle both authored content (JSON, markdown — paragraph-aware) and machine-extracted content (PDF, OCR — line-only). Test both before committing to a strategy.

---

## 14. Re-ingest after corpus regeneration — pulling from GitHub raw URL beats copy-pasting

**What we did:** When regenerating the corpus from PDFs, the Databricks notebook needs the new content. Options were: (a) paste the new corpus into the notebook cell as a Python literal, (b) sync repo via Databricks Repos and read from `/Workspace/Repos/...`, (c) `urlopen(raw.githubusercontent.com/...)` from inside the notebook.

**Chose (c)** because the repo was already on GitHub and the file was small. Every future re-ingest is now "push locally → re-run the cell" with no copy-paste. Zero Databricks Repos setup.

**Lesson:** When iterating on a data file that lives in a public repo, the raw URL is the cleanest sync mechanism for a notebook that doesn't share a filesystem with your laptop. For private repos, Databricks Repos becomes the right answer instead.

---

## 19. Verified the re-ingested index actually holds the new PDF chunks (and nothing stale)

**What we did:** After the Databricks re-ingest (#14), confirmed from the laptop that the Vector Search index contained the new PDF-derived corpus and none of the old 11-doc corpus — no Spark needed, since the app side talks to the index remotely via the Vector Search SDK + PAT.

**Method:** `idx.describe()` for the row count, then a filter-search per new `doc_id` to prove presence:
```python
from databricks.vector_search.client import VectorSearchClient
from app import config
idx = VectorSearchClient(disable_notice=True).get_index(
    endpoint_name=config.VECTOR_SEARCH_ENDPOINT, index_name=config.INDEX_FQN)
print(idx.describe()["status"])          # ready, detailed_state, indexed_row_count
idx.similarity_search(query_text="overview", columns=["chunk_id", "doc_id"],
                      filters={"doc_id": "ONCORIX-FIELD-REFERENCE-GUIDE"},
                      num_results=20, disable_notice=True)
```

**Result:** `ready=True`, `state=ONLINE_NO_PENDING_UPDATE`, `indexed_row_count=12`. All 5/5 new docs present — chunk counts: AE policy 2, Cardiozen brief 3, Cardiozen formulary 2, ImmunoShield 3, Oncorix 2. The per-doc counts sum to **exactly 12 = total row count**, proving the prior corpus was fully overwritten with no leftovers.

**Lesson:** To verify a Delta-Sync index after an ingest without Spark, filter-search by `doc_id` and cross-check that the per-doc chunk counts sum to `indexed_row_count`. A matching sum proves both presence (new docs in) and cleanliness (old docs gone) in one shot.

---

## 11. Databricks notebook can't see local `data/support_corpus.json`

**What we hit:** `ingest.py` reads the corpus from a local file path. The Databricks notebook runs in the cloud, with no access to your laptop's filesystem.

**Resolution for the demo:** Inlined the 11-doc corpus as a Python list in the notebook cell. For any larger / production-realistic corpus, the corpus would need to land in a Databricks Volume, DBFS, or be pulled from object storage (S3/ADLS).

---

## 13. Vector Search auth notice printed on every query despite `disable_notice=True`

**What we hit:** `python -m app.rag_agent` printed `[NOTICE] Using a notebook authentication token...` once per query, even though `VectorSearchClient(disable_notice=True)` was already set in `app/rag_agent.py`.

**Root cause:** `index.similarity_search(...)` has its **own** `disable_notice` parameter that defaults to `False` and re-emits the notice on every call — independent of the client-level setting. Suppressing it on the client doesn't suppress it on the per-query call.

**Resolution:** Pass `disable_notice=True` in the `similarity_search` kwargs in `retrieve()`:
```python
kwargs = {..., "disable_notice": True}  # similarity_search re-emits the notice per call
```

**Lesson:** SDK notice flags aren't always inherited from the client. Check the method signature too.

---

## 14. SQL agent hallucinated `targets.period_start` — semantic layer drifted from real schema

**What we hit:** The hybrid query in `python -m app.orchestrator` failed with `[UNRESOLVED_COLUMN] t.period_start cannot be resolved`. The suggestion list (`t.product_id`, `r.state`, `pr.tier`, `p.brand_name`) showed columns that didn't match `app/semantic_layer.py`.

**Root cause:** `semantic_layer.py` documented the `targets` table with `period_start DATE` / `period_end DATE` / `target_units INT` columns that **don't exist**. The real table stores the period as integer columns: `quarter INT` (1–4) and `year INT`, plus `target_revenue`. The SQL agent faithfully used the documented (wrong) columns.

**Resolution:** Introspected the live schema with `DESCRIBE` and corrected the `targets` block + the `target_attainment_revenue` metric to join on `targets.year = YEAR(sale_date) AND targets.quarter = QUARTER(sale_date)`. Removed the `target_attainment_units` metric (no `target_units` column exists).

**Lesson:** The semantic layer is the SQL agent's only source of truth — when a query fails on a "missing" column, verify the doc against `DESCRIBE`, not the other way around.

---

## 15. `sales.units` doesn't exist — actual column is `units_sold`

**What we hit:** After fixing #14, a re-run failed on `s.units cannot be resolved. Did you mean s.units_sold`. SQL generation is non-deterministic, so this latent bug only surfaced when the agent happened to generate a units-based query.

**Root cause:** Same schema drift as #14 — `semantic_layer.py` listed the column as `units` (in both the `sales` table block and the `units` metric formula), but the real column is `units_sold`.

**Resolution:** Changed both the `sales` table column listing and the `units` metric to `units_sold`.

**Lesson:** Non-deterministic text-to-SQL means latent schema-doc bugs hide until a specific generation path hits them. After fixing one column error, audit the whole schema doc against `DESCRIBE` rather than fixing one at a time.

---

## 16. `target_attainment_revenue` was non-deterministic AND wrong (66.6% vs 40.3%)

**What we hit:** The same hybrid question produced **66.6%** attainment on one run and **40.3%** on another — identical revenue ($14,387), different target denominator ($21,599 vs ~$35,717).

**Root cause:** The metric guidance told the agent to join `sales ⋈ targets` and divide `SUM(revenue) / SUM(target_revenue)`. That single join (a) fans out — a rep's target row is duplicated once per sale — and (b) silently drops reps who have a target but no matching sales. Both corrupt the denominator, and the agent computed it differently across runs. Ground truth (verified by summing the 5 distinct Northeast Cardiozen Q4-2025 target rows directly): **$14,387.10 / $35,717.00 = 40.3%**. The 66.6% was wrong.

**Resolution:** Rewrote the `target_attainment_revenue` definition in `semantic_layer.py` to require aggregating numerator and denominator **independently** (one CTE for sales, one for targets, then divide) — never a direct sales↔targets join. Verified deterministic: 3/3 runs returned 0.4028.

**Lesson:** Any metric that divides two aggregates from different fact/dimension tables must aggregate them separately. A join-then-aggregate fans out and is both wrong and unstable.

---

## 17. Transient text-to-SQL alias typo broke the hybrid query

**What we hit:** A later orchestrator run failed with `tg.target_revenue cannot be resolved. Did you mean tgt.target_revenue` — the model defined a CTE as `tgt` but referenced it as `tg` on one line. Pure self-inconsistency, not a schema problem. The two-CTE shape required by the #16 fix gave the model more aliases to juggle, so an occasional typo slipped through.

**Root cause:** `run_sql_question()` generated once and executed once. Any single malformed generation killed the query with no recovery.

**Resolution:** Added a retry-on-error loop in `app/sql_agent.py` (`config.SQL_MAX_ATTEMPTS`, default 3). On any generation/validation/execution error, the failed SQL + error message are fed back to the model to self-correct, up to N attempts; retries log to stderr. Verified by injecting a forced bad-alias first attempt — the loop recovered on attempt 2 and returned the correct 40.3%.

**Lesson:** Text-to-SQL is inherently flaky even with a correct prompt. A self-correcting retry loop (feed the DB error back to the model) is the standard mitigation and turns transient typos into a non-event.

---

## 18. Semantic layer missing several real columns

**What we hit:** `DESCRIBE` revealed columns absent from `semantic_layer.py`, so the agent couldn't answer questions using them (e.g. "revenue by prescriber tier").

**Root cause:** Documentation drift — the schema doc was a subset of the real tables.

**Resolution:** Added, with values verified against the live data:
- `regions`: `territory` (sub-region grouping), `state` (US state; one row per `region_id`)
- `sales_reps`: `seniority_level` — 'Junior' / 'Mid' / 'Senior'
- `prescribers`: `tier` — 'High' / 'Medium' / 'Low'
- `sales`: `region_id` (redundant — always equals the rep's region), `discount_pct` (a **fraction** 0.00–0.20, i.e. 0–20%, not 0–100)

Verified a cross-tab query over tier × seniority × avg discount runs correctly.

**Lesson:** Check actual value ranges, not just column names — `discount_pct` reads like a 0–100 percentage but is stored as a 0–0.20 fraction; documenting the wrong scale would skew every discount calculation.

---

## Useful diagnostics learned along the way

- **Where is `ANTHROPIC_API_KEY` set?**
  ```powershell
  [System.Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
  [System.Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "Machine")
  echo "Session: $env:ANTHROPIC_API_KEY"
  ```

- **Which Python is `python` actually running?**
  ```powershell
  Get-Command python | Select-Object -ExpandProperty Source
  pip list | Select-String <package>
  ```

- **Catalog/schema discovery in Databricks:**
  ```python
  display(spark.sql("SHOW CATALOGS"))
  display(spark.sql("SHOW SCHEMAS IN workspace"))
  display(spark.sql("SHOW TABLES IN workspace.pharma_sales"))
  ```

- **Vector Search endpoint listing:**
  ```python
  from databricks.vector_search.client import VectorSearchClient
  vsc = VectorSearchClient(disable_notice=True)
  print([e["name"] for e in vsc.list_endpoints().get("endpoints", [])])
  ```

- **Verify the semantic layer against the real schema** (root cause of #14–#16, #18).
  Reuse the SQL agent's own executor to `DESCRIBE` every table and check actual value
  ranges before trusting the docs:
  ```python
  from app.sql_agent import _execute
  from app import config
  fqn = f"{config.SALES_CATALOG}.{config.SALES_SCHEMA}"
  for t in ["sales", "targets", "products", "regions", "sales_reps", "prescribers"]:
      cols, rows = _execute(f"DESCRIBE {fqn}.{t}")
      print(t, [(r[0], r[1]) for r in rows if r[0] and not str(r[0]).startswith("#")])
  # And sanity-check scales/enums, e.g.:
  print(_execute(f"SELECT MIN(discount_pct), MAX(discount_pct) FROM {fqn}.sales")[1])
  ```
