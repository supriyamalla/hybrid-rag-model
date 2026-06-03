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

## 11. Databricks notebook can't see local `data/support_corpus.json`

**What we hit:** `ingest.py` reads the corpus from a local file path. The Databricks notebook runs in the cloud, with no access to your laptop's filesystem.

**Resolution for the demo:** Inlined the 11-doc corpus as a Python list in the notebook cell. For any larger / production-realistic corpus, the corpus would need to land in a Databricks Volume, DBFS, or be pulled from object storage (S3/ADLS).

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
