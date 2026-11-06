"""Conversational memory tools backed by BigQuery.

Stores per-user session history and preferences so the agent can remember
context across conversations (e.g. favourite date ranges, last queries,
frequently used datasets).

BigQuery table (auto-created on first use):
  vz-it-np-voev-dev-voevdo-0.empan_dev_tbls.vea_ds_agent_memory
  - user_eid          STRING   (partition key)
  - session_id        STRING
  - memory_type       STRING   ('preference' | 'last_query' | 'context')
  - key               STRING   (e.g. 'last_dataset', 'preferred_chart_type')
  - value             STRING   (JSON-serialised value)
  - created_at        TIMESTAMP
  - updated_at        TIMESTAMP
"""
import json
import logging
import os
from typing import Any

from google.cloud import bigquery

logger = logging.getLogger(__name__)

BQ_PROJECT   = os.environ.get("BQ_PROJECT", "vz-it-np-voev-dev-voevdo-0")
MEMORY_TABLE = f"`{BQ_PROJECT}.empan_dev_tbls.vea_ds_agent_memory`"


def _bq():
    return bigquery.Client(project=BQ_PROJECT)


# ── Table bootstrap ────────────────────────────────────────────────────────────

def _ensure_memory_table():
    """Create the memory table if it doesn't exist."""
    client = _bq()
    schema = [
        bigquery.SchemaField("user_eid",    "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("session_id",  "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("memory_type", "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("key",         "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("value",       "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("created_at",  "TIMESTAMP", mode="NULLABLE"),
        bigquery.SchemaField("updated_at",  "TIMESTAMP", mode="NULLABLE"),
    ]
    table_ref = bigquery.Table(
        f"{BQ_PROJECT}.empan_dev_tbls.vea_ds_agent_memory", schema=schema
    )
    try:
        client.create_table(table_ref, exists_ok=True)
    except Exception as e:
        logger.warning("Could not ensure memory table: %s", e)


# ── Public tools ───────────────────────────────────────────────────────────────

def save_user_memory(
    user_eid: str,
    key: str,
    value: Any,
    memory_type: str = "context",
    session_id: str = "",
) -> dict[str, Any]:
    """Save or update a memory entry for a user.

    Stores facts the agent should remember across sessions — last dataset
    used, preferred chart type, last query topic, user preferences, etc.

    Args:
        user_eid: 10-digit Verizon employee EID.
        key: Memory key (e.g. 'last_dataset', 'preferred_chart', 'last_query').
        value: Value to store — any JSON-serialisable object.
        memory_type: One of 'preference', 'last_query', 'context' (default 'context').
        session_id: Optional session identifier.

    Returns:
        dict with status, key, error.
    """
    try:
        _ensure_memory_table()
        client = _bq()
        value_str = json.dumps(value) if not isinstance(value, str) else value

        # MERGE (upsert) so we don't duplicate rows for the same user+key
        sql = f"""
            MERGE {MEMORY_TABLE} T
            USING (
                SELECT
                    @user_eid  AS user_eid,
                    @key       AS key,
                    @mem_type  AS memory_type
            ) S
            ON T.user_eid = S.user_eid AND T.key = S.key AND T.memory_type = S.memory_type
            WHEN MATCHED THEN
                UPDATE SET
                    value      = @value,
                    session_id = @session_id,
                    updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN
                INSERT (user_eid, session_id, memory_type, key, value, created_at, updated_at)
                VALUES (@user_eid, @session_id, @mem_type, @key, @value,
                        CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("user_eid",   "STRING", user_eid),
            bigquery.ScalarQueryParameter("key",        "STRING", key),
            bigquery.ScalarQueryParameter("mem_type",   "STRING", memory_type),
            bigquery.ScalarQueryParameter("value",      "STRING", value_str),
            bigquery.ScalarQueryParameter("session_id", "STRING", session_id or ""),
        ])
        client.query(sql, job_config=job_config).result()
        logger.info("Memory saved: user=%s key=%s", user_eid, key)
        return {"status": "saved", "key": key, "error": None}
    except Exception as e:
        logger.error("save_user_memory failed: %s", e)
        return {"status": "failed", "key": key, "error": str(e)}


def get_user_memory(user_eid: str, key: str = "") -> dict[str, Any]:
    """Retrieve memory entries for a user.

    If key is provided, returns that specific entry.
    If key is empty, returns all memory entries for the user (last 20).

    Args:
        user_eid: 10-digit Verizon employee EID.
        key: Specific memory key to retrieve. Empty = return all.

    Returns:
        dict with memories (list of {key, value, memory_type, updated_at}),
        count, error.
    """
    try:
        _ensure_memory_table()
        client = _bq()
        key_filter = "AND key = @key" if key else ""
        sql = f"""
            SELECT key, value, memory_type, updated_at
            FROM {MEMORY_TABLE}
            WHERE user_eid = @user_eid
            {key_filter}
            ORDER BY updated_at DESC
            LIMIT 20
        """
        params = [bigquery.ScalarQueryParameter("user_eid", "STRING", user_eid)]
        if key:
            params.append(bigquery.ScalarQueryParameter("key", "STRING", key))

        rows = list(client.query(sql, job_config=bigquery.QueryJobConfig(
            query_parameters=params)).result())

        memories = []
        for r in rows:
            try:
                val = json.loads(r.value) if r.value else None
            except (json.JSONDecodeError, TypeError):
                val = r.value
            memories.append({
                "key": r.key,
                "value": val,
                "memory_type": r.memory_type,
                "updated_at": str(r.updated_at),
            })

        return {"memories": memories, "count": len(memories), "error": None}
    except Exception as e:
        logger.error("get_user_memory failed: %s", e)
        return {"memories": [], "count": 0, "error": str(e)}


def clear_user_memory(user_eid: str, key: str = "") -> dict[str, Any]:
    """Clear memory entries for a user.

    If key is provided, deletes only that entry.
    If key is empty, deletes ALL memory for this user.

    Args:
        user_eid: 10-digit Verizon employee EID.
        key: Specific key to delete. Empty = clear all memory for user.

    Returns:
        dict with status, deleted_count, error.
    """
    try:
        _ensure_memory_table()
        client = _bq()
        key_filter = "AND key = @key" if key else ""
        sql = f"""
            DELETE FROM {MEMORY_TABLE}
            WHERE user_eid = @user_eid {key_filter}
        """
        params = [bigquery.ScalarQueryParameter("user_eid", "STRING", user_eid)]
        if key:
            params.append(bigquery.ScalarQueryParameter("key", "STRING", key))
        job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
        job.result()
        deleted = job.num_dml_affected_rows or 0
        logger.info("Memory cleared: user=%s key=%s deleted=%d", user_eid, key or "ALL", deleted)
        return {"status": "cleared", "deleted_count": deleted, "error": None}
    except Exception as e:
        logger.error("clear_user_memory failed: %s", e)
        return {"status": "failed", "deleted_count": 0, "error": str(e)}
