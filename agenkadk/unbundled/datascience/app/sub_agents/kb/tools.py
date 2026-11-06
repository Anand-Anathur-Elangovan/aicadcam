import json
import logging
import os
from typing import Any
from google.cloud import bigquery

logger = logging.getLogger(__name__)
BQ_PROJECT = "vz-it-np-voev-dev-voevdo-0"

KB_TABLE         = f"`{BQ_PROJECT}.empan_dev_tbls.vea_sn_kb_knowledge`"
CONFLUENCE_TABLE = f"`{BQ_PROJECT}.oneconfluence.oneconfluence_page`"


def _bq_client():
    return bigquery.Client(project=BQ_PROJECT)


def _tokenize(query: str) -> list[str]:
    """Split a keyword string into search terms. Also adds hyphen variants."""
    stop = {
        "the", "and", "for", "with", "this", "that", "have", "from", "are", "was",
        "how", "can", "what", "where", "when", "why", "who", "get", "set", "use",
        "my", "your", "our", "its", "will", "not", "but", "into", "about", "does",
        "please", "want", "need", "able", "help", "give", "show", "find", "know",
        # Too generic — match thousands of unrelated articles
        "connect", "connection", "network", "mobile", "access", "issue",
        "problem", "error", "work", "working", "troubleshoot", "setup",
        "configure", "install", "using", "service", "application", "app",
    }
    raw = [t.strip("?.,!/") for t in query.lower().split()]
    terms = [t for t in raw if len(t) > 2 and t not in stop]

    # Add hyphen/no-hyphen variants (wifi→wi-fi, wi-fi→wifi)
    extras = []
    for t in terms:
        if "-" in t:
            extras.append(t.replace("-", ""))   # wi-fi → wifi
        else:
            hyphen_map = {"wifi": "wi-fi", "email": "e-mail", "elearning": "e-learning"}
            if t in hyphen_map:
                extras.append(hyphen_map[t])
    terms = list(dict.fromkeys(terms + extras))[:15]
    return terms or [query.lower()[:80]]


def search_kb_articles(query: str, max_results: int = 15) -> dict[str, Any]:
    """Search ServiceNow KB articles using BigQuery full-text SEARCH().

    IMPORTANT: Pass expanded keywords/synonyms, NOT a full sentence.
    Good: "wifi wireless vz-connect mobile network"
    Bad:  "how to connect wifi in my mobile"

    Args:
        query: Space-separated keywords and synonyms to search for.
        max_results: Maximum number of articles to return (default 5).

    Returns:
        dict with articles (list of dicts), count, error.
    """
    try:
        client = _bq_client()
        terms = _tokenize(query)
        # BQ SEARCH() does stemming/tokenization natively — much better than LIKE
        # Also keep LIKE as a fallback OR clause for exact phrase matching
        search_str = " ".join(terms)
        like_conditions = " OR ".join(
            f"(LOWER(short_description) LIKE '%{t}%' OR LOWER(kb_snippet) LIKE '%{t}%' "
            f"OR LOWER(u_knowledge_keyword) LIKE '%{t}%')"
            for t in terms
        )

        sql = f"""
            SELECT
                number,
                short_description,
                SUBSTR(`text`, 1, 2000)      AS text,
                kb_snippet,
                category,
                topic,
                u_portal_url                 AS source_url,
                sys_updated_on               AS updated_on,
                sys_view_count               AS view_count,
                rating,
                workflow_state
            FROM {KB_TABLE}
            WHERE workflow_state = 'published'
              AND LOWER(active) = 'true'
              AND LOWER(kb_knowledge_base_display_value) = 'self help'
              AND (
                SEARCH(short_description, {repr(search_str)})
                OR SEARCH(`text`, {repr(search_str)})
                OR SEARCH(kb_snippet, {repr(search_str)})
                OR SEARCH(kb_steps, {repr(search_str)})
                OR {like_conditions}
              )
            ORDER BY COALESCE(SAFE_CAST(sys_view_count AS INT64), 0) DESC
            LIMIT {max_results}
        """
        logger.info("search_kb_articles SEARCH terms=%s", terms)
        job = client.query(sql)
        rows = [dict(r) for r in job.result()]
        logger.info("search_kb_articles returned %d rows for query: %s", len(rows), query)
        if rows:
            sample = {k: str(v)[:80] for k, v in rows[0].items()}
            logger.info("search_kb_articles sample row: %s", sample)
        return {"articles": rows, "count": len(rows), "error": None}
    except Exception as e:
        logger.error("search_kb_articles failed: %s", e)
        return {"articles": [], "count": 0, "error": str(e)}


def search_confluence(query: str, max_results: int = 15) -> dict[str, Any]:
    """Search OneConfluence pages using BigQuery full-text SEARCH().

    IMPORTANT: Pass expanded keywords/synonyms, NOT a full sentence.
    Good: "wifi wireless vz-connect mobile network"
    Bad:  "how to connect wifi in my mobile"

    Args:
        query: Space-separated keywords and synonyms to search for.
        max_results: Maximum number of pages to return (default 5).

    Returns:
        dict with pages (list of dicts), count, error.
    """
    try:
        client = _bq_client()
        terms = _tokenize(query)
        search_str = " ".join(terms)
        like_conditions = " OR ".join(
            f"LOWER(title) LIKE '%{t}%'"
            for t in terms
        )

        sql = f"""
            SELECT
                title,
                SUBSTR(content, 1, 2000)   AS content_excerpt,
                space,
                web_ui                     AS source_url,
                updated_on,
                updatedby_name,
                page_id
            FROM {CONFLUENCE_TABLE}
            WHERE (
                SEARCH(title, {repr(search_str)})
                OR SEARCH(content, {repr(search_str)})
                OR {like_conditions}
            )
            ORDER BY updated_on DESC
            LIMIT {max_results}
        """
        logger.info("search_confluence SEARCH terms=%s query=%s", terms, query)
        job = client.query(sql)
        rows = [dict(r) for r in job.result()]
        logger.info("search_confluence returned %d rows for query: %s", len(rows), query)
        if rows:
            sample = {"title": str(rows[0].get("title", ""))[:100], "space": rows[0].get("space", "")}
            logger.info("search_confluence sample row: %s", sample)
        return {"pages": rows, "count": len(rows), "error": None}
    except Exception as e:
        logger.error("search_confluence failed: %s", e)
        return {"pages": [], "count": 0, "error": str(e)}
