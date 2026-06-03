"""Semantic layer: schema + metric definitions + disambiguation rules for the SQL agent.

The SQL agent is generic Text-to-SQL; what makes it correct on *this* business is the
context rendered here. Update this file when the BI definitions change — not the agent.
"""
from textwrap import dedent
from app import config

FQN = f"{config.SALES_CATALOG}.{config.SALES_SCHEMA}"


TABLES = dedent(f"""\
    Database location: {FQN}
    Dialect: Spark SQL (Databricks).

    Tables (column — note):

    {FQN}.products    (8 rows)
      product_id        — PK
      product_name      — one of: 'BreathEase', 'CardioPlus', 'Cardiozen', 'ImmunoShield',
                          'NeuroCalm', 'Neurovex', 'OncoMax', 'Oncorix'
      brand_name        — marketed brand string (e.g. 'Cardiozen 10mg', 'Oncorix 50mg')
      therapeutic_area  — one of: 'Oncology', 'Cardiology', 'Neurology', 'Immunology',
                          'Respiratory'
      launch_date       DATE
      unit_price        DECIMAL — list price per unit USD

    {FQN}.regions     (12 rows: one per state, sharing 4 region_name values)
      region_id    — PK; each region_name corresponds to ~3 rows
      region_name  — one of: 'Northeast', 'Southeast', 'Midwest', 'West'
      territory    — sub-region grouping within a region_name (e.g. 'Atlantic',
                     'New England', 'Pacific', 'Great Lakes'); several states share one
      state        — US state for this row (one row per region_id)

    {FQN}.sales_reps  (20 rows)
      rep_id          — PK
      rep_name
      region_id       — FK -> regions.region_id (a state-level row, not a region_name)
      hire_date       DATE
      seniority_level — one of: 'Junior', 'Mid', 'Senior'

    {FQN}.prescribers
      prescriber_id    — PK
      prescriber_name
      specialty
      region_id        — FK -> regions.region_id
      tier             — prescriber value tier: one of 'High', 'Medium', 'Low'

    {FQN}.sales       (1000 rows, sale_date BETWEEN 2024-01-01 AND 2025-12-31)
      sale_id        — PK
      sale_date      DATE
      product_id     — FK -> products.product_id
      rep_id         — FK -> sales_reps.rep_id
      region_id      — FK -> regions.region_id; redundant — always equals the selling
                       rep's region (the sales_reps -> regions join gives the same result)
      prescriber_id  — FK -> prescribers.prescriber_id
      units_sold     INT     — units sold (Rx volume proxy)
      revenue        DECIMAL — USD net revenue
      discount_pct   DECIMAL — discount as a fraction (0.00–0.20, i.e. 0–20%)

    {FQN}.targets
      target_id      — PK
      rep_id         — FK -> sales_reps.rep_id
      product_id     — FK -> products.product_id
      quarter        INT     — quarter number 1–4 (NOT a date)
      year           INT     — calendar year
      target_revenue DECIMAL — quarterly revenue target USD
""")


METRICS = dedent("""\
    Metric definitions (use these — do not invent new formulas):

    - revenue: SUM(sales.revenue)
    - units:   SUM(sales.units_sold)
    - target_attainment_revenue = (total actual revenue) / (total target revenue).
      CRITICAL: aggregate the numerator and denominator INDEPENDENTLY, then divide. Do
      NOT join sales directly to targets — that join fans out (a rep's target row is
      duplicated once per sale) and silently drops reps who have a target but no matching
      sales; both corrupt the ratio.
        Numerator: SUM(sales.revenue) over the in-scope sales rows. Filter the quarter on
          sales.sale_date (Q4 2025 = sale_date BETWEEN 2025-10-01 AND 2025-12-31).
        Denominator: SUM(targets.target_revenue) over the DISTINCT in-scope target rows,
          filtered on the same product / region / rep set with targets.quarter and
          targets.year (Q4 2025 = quarter = 4 AND year = 2025).
      Typical shape: one CTE aggregates sales, another aggregates targets, then divide.
      (Note: targets has no unit target, so target attainment is revenue-based only.)
    - Quarter convention: Q1 = Jan–Mar, Q2 = Apr–Jun, Q3 = Jul–Sep, Q4 = Oct–Dec.
      Use date_trunc('quarter', sale_date) when grouping sales by quarter; use the
      targets.quarter / targets.year integer columns when joining to targets.
""")


DISAMBIGUATION = dedent("""\
    Disambiguation rules:

    - "Sales" without qualification means revenue (SUM(sales.revenue)).
    - "Performance" means target attainment, default to revenue-based.
    - Region names are matched on regions.region_name (case-insensitive). Note that each
      region_name (e.g. 'Northeast') groups ~3 territory rows; joining sales -> sales_reps
      -> regions and filtering on region_name correctly aggregates across the territories.
    - Product names are matched on products.product_name (case-insensitive).
    - The sales table only contains data from 2024-01-01 to 2025-12-31. When the user says
      "this quarter" / "current quarter" / "latest quarter", interpret as Q4 2025
      (2025-10-01 to 2025-12-31) — the most recent quarter with data — NOT
      date_trunc('quarter', current_date()), which would return zero rows.
    - Always return at most 100 rows. Add LIMIT 100 if no LIMIT is present.
    - Output a single SELECT statement. No DDL, DML, USE, SET, or multi-statements.
""")


def get_schema_context(scope: dict | None = None) -> str:
    """Render the full BI context the SQL agent uses to generate a query.

    `scope` is the router-extracted metadata (e.g. {"product": "Cardiox", "region":
    "Northeast"}); if provided, it's appended as a hint so the model biases toward it.
    """
    parts = [TABLES, METRICS, DISAMBIGUATION]
    if scope:
        hints = [f"- {k}: {v}" for k, v in scope.items() if v]
        if hints:
            parts.append("Router-extracted scope (apply as WHERE filters where applicable):\n" + "\n".join(hints))
    return "\n".join(parts)


# Canonical vocabularies — also handed to the router so it extracts values that exist.
KNOWN_PRODUCTS = [
    "BreathEase",
    "CardioPlus",
    "Cardiozen",
    "ImmunoShield",
    "NeuroCalm",
    "Neurovex",
    "OncoMax",
    "Oncorix",
]
KNOWN_REGIONS = ["Northeast", "Southeast", "Midwest", "West"]
KNOWN_DOC_TYPES = [
    "prior_auth",
    "dosing",
    "patient_assistance",
    "adverse_events",
    "formulary",
    "competitive",
    "objection_handling",
]
