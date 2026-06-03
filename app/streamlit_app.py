"""Streamlit chat UI for the pharma rep copilot.

Run with:
    streamlit run app/streamlit_app.py

What it does:
  - Chat input + persistent history (via st.session_state).
  - On each question, calls orchestrator.answer(...) and renders the synthesized
    answer as the assistant turn.
  - Each assistant message has a collapsed "How this answer was assembled" expander
    showing the route the router picked, the SQL that ran (+ its rows), and the
    retrieved support docs (+ similarity scores).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app import config, orchestrator


st.set_page_config(
    page_title="Pharma Rep Copilot",
    page_icon="💊",
    layout="wide",
)


def _render_assistant_block(result: orchestrator.CopilotResult) -> None:
    """Render one orchestrator result: the answer + a provenance expander."""
    st.markdown(result.answer)

    with st.expander(f"How this answer was assembled  ·  route: {result.route}", expanded=False):
        st.markdown(f"**Router reasoning:** {result.reasoning}")
        active_filters = {k: v for k, v in (result.filters or {}).items() if v}
        if active_filters:
            st.markdown(f"**Extracted filters:** `{active_filters}`")
        else:
            st.caption("No metadata filters extracted (unfiltered semantic search).")

        if result.sql:
            st.markdown("---")
            st.markdown("**SQL executed:**")
            st.code(result.sql, language="sql")
            if result.rows:
                df = pd.DataFrame(result.rows, columns=result.columns)
                st.dataframe(df, width="stretch", hide_index=True)
            else:
                st.caption("(query returned no rows)")
        elif result.sql_error:
            st.markdown("---")
            st.error(f"SQL failed: {result.sql_error}")

        if result.sources:
            st.markdown("---")
            st.markdown("**Retrieved support docs:**")
            src_df = pd.DataFrame(result.sources)
            src_df["score"] = src_df["score"].map(lambda x: f"{x:.3f}")
            src_df = src_df.rename(
                columns={"doc_id": "Doc ID", "title": "Title", "score": "Similarity"}
            )
            st.dataframe(src_df, width="stretch", hide_index=True)


def _sidebar() -> None:
    with st.sidebar:
        st.subheader("How it works")
        st.markdown(
            "A router classifies every question and picks the path:\n\n"
            "- **Analytical** → Text-to-SQL over `workspace.pharma_sales`\n"
            "- **Knowledge** → filtered Vector Search over the support corpus\n"
            "- **Hybrid** → both, merged with inline citations\n\n"
            "The differentiator is the router + semantic layer — not the model. "
            "Retrieval is semantic search **constrained by BI metadata** "
            "(product, region, doc_type) the router extracted."
        )

        st.subheader("Try these")
        st.markdown(
            "- *What was total revenue by product in the Northeast?*\n"
            "- *How does prior authorization work for Oncorix?*\n"
            "- *Cardiozen Q3 2025 attainment in the Northeast — and what's the formulary situation?*"
        )

        st.subheader("Models")
        st.markdown(
            f"- **Router:** `{config.ROUTER_MODEL}`\n"
            f"- **SQL + synthesis:** `{config.GENERATION_MODEL}`\n"
            f"- **Embeddings:** `{config.EMBEDDING_ENDPOINT}` (Databricks-managed)"
        )

        if st.button("Clear chat", type="secondary"):
            st.session_state.messages = []
            st.rerun()


def main() -> None:
    st.title("💊 Pharma Rep Copilot")
    st.caption(
        "Ask a question. The router decides whether to query the sales database, "
        "the support docs, or both — and Claude composes one cited answer."
    )

    _sidebar()

    if "messages" not in st.session_state:
        st.session_state.messages = []  # [{"role": "user"|"assistant", "content": str | CopilotResult}]

    # Re-render history on each rerun.
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                _render_assistant_block(msg["content"])

    # New question.
    question = st.chat_input("Ask a question about sales performance, support docs, or both…")
    if not question:
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Routing → retrieving → synthesizing…"):
            try:
                result = orchestrator.answer(question)
            except Exception as e:  # noqa: BLE001 — UI shouldn't crash on pipeline failure
                st.error(f"Pipeline error: {type(e).__name__}: {e}")
                return
        _render_assistant_block(result)
        st.session_state.messages.append({"role": "assistant", "content": result})


if __name__ == "__main__":
    main()
