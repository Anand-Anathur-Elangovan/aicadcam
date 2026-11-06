import logging
import os
from typing import Any
from google.cloud import bigquery

logger = logging.getLogger(__name__)
BQ_PROJECT = "vz-it-np-voev-dev-voevdo-0"


def _get_bq_client() -> bigquery.Client:
    return bigquery.Client(project=BQ_PROJECT)


def run_bigquery_query(sql: str, max_rows: int = 1000) -> dict[str, Any]:
    """Execute a BigQuery SQL query and return results as a list of row dicts.

    Args:
        sql: Valid BigQuery Standard SQL query string.
        max_rows: Maximum number of rows to return (default 1000).

    Returns:
        dict with keys: rows (list of dicts), row_count (int), columns (list),
        schema (list of {name, type}), error (str or None).
    """
    try:
        client = _get_bq_client()
        job_config = bigquery.QueryJobConfig(
            use_query_cache=True,
            maximum_bytes_billed=10 * 1024 ** 3,  # 10 GB safety cap
        )
        query_job = client.query(sql, job_config=job_config)
        results = query_job.result()
        schema = [{"name": f.name, "type": f.field_type} for f in results.schema]
        rows = []
        for i, row in enumerate(results):
            if i >= max_rows:
                break
            rows.append(dict(row))
        logger.info("BQ query returned %d rows", len(rows))
        return {
            "rows": rows,
            "row_count": len(rows),
            "columns": [f.name for f in results.schema],
            "schema": schema,
            "error": None,
            "bytes_processed": query_job.total_bytes_processed,
        }
    except Exception as e:
        logger.error("BQ query failed: %s", e)
        return {"rows": [], "row_count": 0, "columns": [], "schema": [], "error": str(e)}


def list_bq_tables(dataset_ref: str) -> dict[str, Any]:
    """List all tables in a BigQuery dataset.

    Args:
        dataset_ref: Dataset reference in format 'project.dataset' or just 'dataset'
                     (project defaults to BQ_PROJECT env var).

    Returns:
        dict with keys: tables (list of table IDs), error (str or None).
    """
    try:
        client = _get_bq_client()
        if "." not in dataset_ref:
            dataset_ref = f"{BQ_PROJECT}.{dataset_ref}"
        tables = list(client.list_tables(dataset_ref))
        return {
            "tables": [f"{t.project}.{t.dataset_id}.{t.table_id}" for t in tables],
            "count": len(tables),
            "error": None,
        }
    except Exception as e:
        logger.error("list_bq_tables failed: %s", e)
        return {"tables": [], "count": 0, "error": str(e)}


def get_bq_table_schema(table_ref: str) -> dict[str, Any]:
    """Get the full schema (columns and types) of a BigQuery table.

    Args:
        table_ref: Fully qualified table reference: 'project.dataset.table'

    Returns:
        dict with keys: schema (list of {name, type, mode, description}),
        num_rows (int), error (str or None).
    """
    try:
        client = _get_bq_client()
        table = client.get_table(table_ref)
        schema = [
            {
                "name": f.name,
                "type": f.field_type,
                "mode": f.mode,
                "description": f.description or "",
            }
            for f in table.schema
        ]
        return {
            "schema": schema,
            "num_rows": table.num_rows,
            "num_bytes": table.num_bytes,
            "description": table.description or "",
            "error": None,
        }
    except Exception as e:
        logger.error("get_bq_table_schema failed: %s", e)
        return {"schema": [], "num_rows": 0, "error": str(e)}
