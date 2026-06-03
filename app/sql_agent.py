"""SQL agent — Text-to-SQL over the pharma_sales tables.

Pipeline:
  1. Build a system prompt from the semantic layer + optional router-extracted scope.
  2. Ask Claude Sonnet to produce ONE Spark SQL SELECT — no prose, no markdown.
  3. Validate it's read-only (no DDL/DML, single statement).
  4. Execute via databricks-sql-connector against your Serverless SQL warehouse.
  5. Return {sql, columns, rows} so the orchestrator can cite the query.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage
from databricks import sql as dbsql

from app import config
from app.semantic_layer import get_schema_context


SYSTEM_PROMPT_TEMPLATE = """\
You are a precise Text-to-SQL agent for a pharma sales analytics database. Given a
business question, return EXACTLY ONE Spark SQL SELECT statement. No prose, no
comments, no markdown — just the SQL.

{schema_context}

Hard rules:
- Output ONLY the SQL — no ```sql fences, no explanation.
- Exactly one SELECT (or one WITH ... SELECT) statement. No semicolons separating
  statements.
- No DDL or DML: no INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, MERGE,
  USE, SET, GRANT, REVOKE, CALL.
- Use the canonical product / region names exactly as in the schema (case-insensitive
  match with LOWER() is fine when joining on user-provided text).
- Use the metric formulas defined in the semantic layer — do not invent new ones.
- Always include `LIMIT 100` unless the question explicitly requests fewer rows.
"""


# Keywords that must NOT appear at the statement level. We don't try to parse SQL
# fully — we reject anything containing these as a word boundary so the operator
# never has to reason about whether a generated query is safe.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|USE|SET|GRANT|REVOKE|CALL|REFRESH|MSCK)\b",
    re.IGNORECASE,
)


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("sql"):
            t = t[3:].lstrip()
    return t.strip().rstrip(";").strip()


def _validate(sql: str) -> str:
    """Reject anything that isn't a single read-only SELECT/CTE."""
    if not sql:
        raise ValueError("SQL agent returned empty SQL.")
    head = sql.lstrip("(").lstrip().upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError(f"Generated SQL is not a SELECT/CTE:\n{sql}")
    if ";" in sql:
        raise ValueError(f"Generated SQL contains multiple statements:\n{sql}")
    if _FORBIDDEN.search(sql):
        raise ValueError(f"Generated SQL contains a forbidden keyword:\n{sql}")
    return sql


async def _generate_sql_async(question: str, scope: dict | None) -> str:
    options = ClaudeAgentOptions(
        model=config.GENERATION_MODEL,
        system_prompt=SYSTEM_PROMPT_TEMPLATE.format(
            schema_context=get_schema_context(scope)
        ),
        allowed_tools=[],
    )
    raw: str | None = None
    async for msg in query(prompt=question, options=options):
        if isinstance(msg, ResultMessage):
            raw = msg.result
    if not raw:
        raise RuntimeError("SQL agent returned no result from the Agent SDK.")
    return _strip_fences(raw)


def generate_sql(question: str, scope: dict | None = None) -> str:
    """Generate and validate a single read-only Spark SQL SELECT for the question."""
    return _validate(asyncio.run(_generate_sql_async(question, scope)))


def _connect():
    if not (config.DATABRICKS_HOST and config.DATABRICKS_TOKEN and config.DATABRICKS_HTTP_PATH):
        raise RuntimeError(
            "DATABRICKS_HOST / DATABRICKS_TOKEN / DATABRICKS_HTTP_PATH must be set in .env"
        )
    # databricks-sql-connector wants the hostname only (no scheme, no trailing slash).
    host = config.DATABRICKS_HOST.replace("https://", "").replace("http://", "").rstrip("/")
    return dbsql.connect(
        server_hostname=host,
        http_path=config.DATABRICKS_HTTP_PATH,
        access_token=config.DATABRICKS_TOKEN,
    )


def _execute(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        columns = [c[0] for c in cur.description] if cur.description else []
        rows = cur.fetchall()
    return columns, rows


def run_sql_question(question: str, scope: dict | None = None) -> dict:
    """Top-level entry point. Returns {sql, columns, rows}."""
    sql = generate_sql(question, scope)
    columns, rows = _execute(sql)
    return {"sql": sql, "columns": columns, "rows": [tuple(r) for r in rows]}


SAMPLE_QUESTION = "What was total revenue by product in the Northeast?"


def _demo() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"Q: {SAMPLE_QUESTION}\n")
    out = run_sql_question(SAMPLE_QUESTION, scope={"region": "Northeast"})
    print("SQL:")
    print(out["sql"])
    print(f"\nColumns: {out['columns']}")
    print("Rows:")
    for r in out["rows"]:
        print(f"  {r}")


if __name__ == "__main__":
    _demo()
