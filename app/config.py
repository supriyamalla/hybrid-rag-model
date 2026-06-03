"""Loads runtime configuration from .env. Import constants from here, not os.environ."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _opt(name: str, default: str) -> str:
    return os.environ.get(name) or default


# Claude — auth is handled by the Agent SDK via `claude login` (subscription OAuth).
# If ANTHROPIC_API_KEY is set, the SDK uses it INSTEAD of the subscription. Leaving
# this empty / unset is the normal path for subscription users.
ROUTER_MODEL = _opt("ROUTER_MODEL", "claude-haiku-4-5-20251001")
GENERATION_MODEL = _opt("GENERATION_MODEL", "claude-sonnet-4-6")

# Text-to-SQL: total attempts to generate+execute before giving up. On a failure the
# error is fed back to the model so it can self-correct (transient alias/typo errors).
SQL_MAX_ATTEMPTS = int(_opt("SQL_MAX_ATTEMPTS", "3"))

# Databricks workspace (for remote SQL + Vector Search SDK)
DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST", "")
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
DATABRICKS_HTTP_PATH = os.environ.get("DATABRICKS_HTTP_PATH", "")

# UC location for the support corpus (Vector Search requires UC)
DATABRICKS_CATALOG = _opt("DATABRICKS_CATALOG", "main")
DATABRICKS_SCHEMA = _opt("DATABRICKS_SCHEMA", "pharma_copilot")

# Existing pharma_sales tables (already built; can live anywhere)
SALES_CATALOG = _opt("SALES_CATALOG", "hive_metastore")
SALES_SCHEMA = _opt("SALES_SCHEMA", "pharma_sales")

# Vector Search
EMBEDDING_ENDPOINT = _opt("EMBEDDING_ENDPOINT", "databricks-gte-large-en")
VECTOR_SEARCH_ENDPOINT = _opt("VECTOR_SEARCH_ENDPOINT", "pharma_copilot_endpoint")
SOURCE_TABLE = _opt("SOURCE_TABLE", "support_corpus_chunks")
INDEX_NAME = _opt("INDEX_NAME", "support_corpus_index")

# Fully qualified names for the corpus side
SOURCE_TABLE_FQN = f"{DATABRICKS_CATALOG}.{DATABRICKS_SCHEMA}.{SOURCE_TABLE}"
INDEX_FQN = f"{DATABRICKS_CATALOG}.{DATABRICKS_SCHEMA}.{INDEX_NAME}"

# Retrieval
TOP_K = int(_opt("TOP_K", "5"))
SIMILARITY_THRESHOLD = float(_opt("SIMILARITY_THRESHOLD", "0.3"))
