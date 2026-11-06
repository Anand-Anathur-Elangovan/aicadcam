import datetime
import decimal
import logging
import os
from typing import Any

import pandas as pd
from google.adk.tools import ToolContext
from google.cloud import bigquery

logger = logging.getLogger(__name__)

BQ_PROJECT = os.environ.get("BQ_PROJECT", "vz-it-np-voev-dev-voevdo-0")
BQ_RAW_TABLE = f"{BQ_PROJECT}.bq_learn.sample_gmeetdata"
BQ_SCORES_TABLE = f"{BQ_PROJECT}.bq_learn.gmeet_daily_scores"
BQ_ROOM_SCORES_TABLE = f"{BQ_PROJECT}.bq_learn.gmeet_room_daily_scores"
HR_TABLE = f"{BQ_PROJECT}.empan_dev_tbls.vea_hr_latest_data"

SESSION_EID_KEY = "user_eid"
SESSION_EMAIL_KEY = "user_email"
SESSION_NAME_KEY = "user_name"

CLOUD_RUN_JOB_NAME = "gmeetscoretableupdate"
CLOUD_RUN_REGION = "us-east4"
CLOUD_RUN_PROJECT = BQ_PROJECT


def _bq_client() -> bigquery.Client:
    return bigquery.Client(project=BQ_PROJECT)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _sanitize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _to_jsonable(v) for k, v in row.items()} for row in rows]


def _get_score_label(score: float | None) -> str:
    if score is None:
        return "N/A"
    try:
        if pd.isna(score):
            return "N/A"
    except (TypeError, ValueError):
        pass
    if score >= 9.0:
        return "Excellent"
    elif score >= 7.5:
        return "Good"
    elif score >= 6.0:
        return "Fair"
    elif score >= 4.0:
        return "Poor"
    return "Critical"


# ─── Tool 0: Register / Recall EID ───────────────────────────────────────────


def register_eid(eid: str, tool_context: ToolContext) -> dict[str, Any]:
    """Register the user's Employee ID (EID) so they don't need to repeat it.

    Call this as soon as the user provides their EID. The EID is remembered for
    the entire conversation. Also resolves the EID to an email for display.

    Args:
        eid: Employee ID (e.g. "VZ12345" or "0000012345").
    """
    eid = (eid or "").strip()
    if not eid:
        return {"error": "EID is required.", "status": "error"}

    tool_context.state[SESSION_EID_KEY] = eid

    try:
        client = _bq_client()
        email = _resolve_eid_to_email(client, eid)
        if email:
            tool_context.state[SESSION_EMAIL_KEY] = email

        name = _resolve_eid_to_name(client, eid)
        if name:
            tool_context.state[SESSION_NAME_KEY] = name

        return {
            "status": "registered",
            "eid": eid,
            "email": email,
            "name": name,
            "message": (
                f"EID '{eid}' registered for this session"
                + (f" (name: {name}, email: {email})" if name else "")
                + ". You won't need to provide it again."
            ),
            "error": None,
        }
    except Exception as exc:
        tool_context.state[SESSION_EID_KEY] = eid
        return {
            "status": "registered_without_lookup",
            "eid": eid,
            "email": None,
            "name": None,
            "message": f"EID '{eid}' registered but could not look up details: {exc}",
            "error": None,
        }


def _get_session_eid(tool_context: ToolContext) -> str | None:
    return tool_context.state.get(SESSION_EID_KEY)


# ─── Tool 1: Check Data Freshness ────────────────────────────────────────────


def check_data_freshness() -> dict[str, Any]:
    """Check whether the GMeet daily scores table is up-to-date with the latest
    **completed** raw data.

    Raw data streams in every 5 minutes, so today's data is always incomplete.
    This tool only considers completed days (yesterday and earlier) when
    deciding whether scores need updating. Today's data is excluded from the
    comparison because scoring it mid-day would capture partial results and
    the nightly scheduled job would skip re-processing that day.
    """
    try:
        client = _bq_client()

        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)

        raw_query = f"""
        SELECT MAX(DATE(Date_Time)) as latest_raw_date,
               MAX(CASE WHEN DATE(Date_Time) < @today THEN DATE(Date_Time) END)
                   as latest_completed_raw_date
        FROM `{BQ_RAW_TABLE}`
        """
        raw_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("today", "DATE", today),
            ]
        )

        scores_query = f"""
        SELECT MAX(score_date) as latest_score_date
        FROM `{BQ_SCORES_TABLE}`
        """

        raw_result = list(client.query(raw_query, job_config=raw_config).result())
        latest_raw = raw_result[0].latest_raw_date if raw_result else None
        latest_completed_raw = raw_result[0].latest_completed_raw_date if raw_result else None

        try:
            scores_result = list(client.query(scores_query).result())
            latest_score = scores_result[0].latest_score_date if scores_result else None
        except Exception:
            latest_score = None

        if latest_raw is None:
            return {
                "status": "no_raw_data",
                "message": "No raw GMeet data found in the source table.",
                "latest_raw_date": None,
                "latest_completed_raw_date": None,
                "latest_score_date": _to_jsonable(latest_score),
                "needs_update": False,
                "missing_dates": [],
                "error": None,
            }

        if latest_completed_raw is None:
            return {
                "status": "up_to_date",
                "message": (
                    f"Only today's data ({latest_raw}) exists in raw table, "
                    "which is still streaming. No completed days to score yet."
                ),
                "latest_raw_date": _to_jsonable(latest_raw),
                "latest_completed_raw_date": None,
                "latest_score_date": _to_jsonable(latest_score),
                "needs_update": False,
                "missing_dates": [],
                "error": None,
            }

        needs_update = latest_score is None or latest_score < latest_completed_raw

        missing_dates = []
        if needs_update:
            start = (latest_score + datetime.timedelta(days=1)) if latest_score else latest_completed_raw
            current = start
            while current <= latest_completed_raw:
                missing_dates.append(_to_jsonable(current))
                current += datetime.timedelta(days=1)

            missing_count = len(missing_dates)
            message = (
                f"Scores table is behind by {missing_count} completed day(s). "
                f"Raw data has completed days up to {latest_completed_raw}, "
                f"scores computed up to {latest_score}. "
                f"Missing dates: {missing_dates[0]} to {missing_dates[-1]}. "
                f"Today's data ({today}) is excluded (still streaming). "
                "Run trigger_scoring_job to process the missing days."
                if latest_score
                else f"Scores table is empty. Raw data has completed days up to "
                f"{latest_completed_raw}. Today's data ({today}) is excluded "
                "(still streaming). Run trigger_scoring_job to update."
            )
        else:
            message = (
                f"Scores are up-to-date through {latest_score} "
                f"(all completed days scored). "
                f"Today's raw data ({latest_raw}) is still streaming and will be "
                "processed by the nightly scheduled job after midnight."
            )

        return {
            "status": "needs_update" if needs_update else "up_to_date",
            "message": message,
            "latest_raw_date": _to_jsonable(latest_raw),
            "latest_completed_raw_date": _to_jsonable(latest_completed_raw),
            "latest_score_date": _to_jsonable(latest_score),
            "needs_update": needs_update,
            "missing_dates": missing_dates,
            "error": None,
        }
    except Exception as exc:
        logger.error("check_data_freshness failed: %s", exc)
        return {
            "status": "error",
            "message": str(exc),
            "latest_raw_date": None,
            "latest_completed_raw_date": None,
            "latest_score_date": None,
            "needs_update": False,
            "missing_dates": [],
            "error": str(exc),
        }


# ─── Tool 2: Trigger Scoring Job ─────────────────────────────────────────────


def trigger_scoring_job() -> dict[str, Any]:
    """Trigger the Cloud Run job to compute GMeet daily scores for missing
    completed days only.

    First checks data freshness (excluding today's incomplete data). If scores
    are already up-to-date for all completed days, returns without triggering.
    Otherwise executes the gmeetscoretableupdate Cloud Run job with explicit
    date arguments so it only processes the missing completed days — never
    today's partial data.
    """
    freshness = check_data_freshness()
    if freshness.get("error"):
        return {
            "status": "error",
            "message": f"Could not check freshness: {freshness['error']}",
            "job_execution": None,
            "error": freshness["error"],
        }

    if not freshness["needs_update"]:
        return {
            "status": "already_up_to_date",
            "message": freshness["message"],
            "job_execution": None,
            "error": None,
        }

    missing_dates = freshness.get("missing_dates", [])
    if not missing_dates:
        return {
            "status": "already_up_to_date",
            "message": "No missing completed days to process.",
            "job_execution": None,
            "error": None,
        }

    backfill_from = missing_dates[0]
    backfill_to = missing_dates[-1]

    try:
        from google.cloud import run_v2

        jobs_client = run_v2.JobsClient()
        job_name = (
            f"projects/{CLOUD_RUN_PROJECT}/locations/{CLOUD_RUN_REGION}"
            f"/jobs/{CLOUD_RUN_JOB_NAME}"
        )

        overrides = run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    args=[
                        "python", "cloudjobmain.py",
                        "--backfill-from", str(backfill_from),
                        "--date", str(backfill_to),
                    ],
                ),
            ],
        )

        request = run_v2.RunJobRequest(name=job_name, overrides=overrides)
        operation = jobs_client.run_job(request=request)

        return {
            "status": "triggered",
            "message": (
                f"Cloud Run job '{CLOUD_RUN_JOB_NAME}' triggered successfully. "
                f"Processing {len(missing_dates)} completed day(s): "
                f"{backfill_from} to {backfill_to}. "
                f"Today's incomplete data is excluded. "
                "The job runs in the background and may take several minutes."
            ),
            "job_execution": str(operation.metadata.name) if operation.metadata else "submitted",
            "dates_processing": missing_dates,
            "error": None,
        }
    except Exception as exc:
        logger.error("trigger_scoring_job failed: %s", exc)
        return {
            "status": "error",
            "message": f"Failed to trigger Cloud Run job: {exc}",
            "job_execution": None,
            "error": str(exc),
        }


# ─── Tool 3: Get GMeet Score ─────────────────────────────────────────────────


def get_gmeet_score(
    tool_context: ToolContext,
    eid: str = "",
    employee_name: str = "",
    days: int = 30,
    start_date: str = "",
    end_date: str = "",
    last_n_meetings: int = 0,
) -> dict[str, Any]:
    """Get the GMeet quality score for an employee.

    Args:
        eid: Employee ID (e.g. "VZ12345" or "0000012345"). If empty, uses the
             previously registered EID from this session.
        employee_name: Employee name to look up (e.g. "Sarah Johnson"). Used only
                       if eid is not provided. If multiple matches are found, returns
                       the list for the user to confirm.
        days: Number of past days to analyze (default 30). Ignored if
              start_date is provided.
        start_date: Start date in YYYY-MM-DD format (overrides days).
        end_date: End date in YYYY-MM-DD format (defaults to today).
        last_n_meetings: If > 0, return only the last N meetings instead of a
                         date range. Overrides days/start_date/end_date.
    """
    eid = (eid or "").strip() or _get_session_eid(tool_context) or ""

    logger.info(f"get_gmeet_score called with eid={eid}, employee_name={employee_name}, days={days}, last_n_meetings={last_n_meetings}")

    if not eid and employee_name:
        try:
            client = _bq_client()
            matches = _resolve_name_to_eid(client, employee_name)
            if not matches:
                return {"status": "error",
                        "error": f"No employee found with name '{employee_name}'."}
            if len(matches) > 1:
                return {
                    "status": "multiple_matches",
                    "matches": [
                        {"eid": m["eid"], "name": m["name"], "email": m.get("email", "")}
                        for m in matches
                    ],
                    "error": f"Multiple employees found matching '{employee_name}'. "
                             f"Please specify the EID to confirm. "
                             f"Matches: " + ", ".join(
                                 f"{m['name']} (EID: {m['eid']})" for m in matches
                             ),
                }
            eid = matches[0]["eid"]
        except Exception as exc:
            return {"status": "error", "error": f"Name lookup failed: {exc}"}

    if not eid:
        return {
            "error": "EID or employee name is required. Please provide an Employee ID (e.g. VZ12345) or a name.",
            "status": "needs_eid",
        }

    try:
        client = _bq_client()

        eid_email = _resolve_eid_to_email(client, eid)

        if last_n_meetings and last_n_meetings > 0:
            df = _fetch_last_n_meetings_hybrid(client, eid, eid_email, last_n_meetings)
        else:
            if start_date:
                from_date = start_date.strip()
                to_date = (end_date or "").strip() or datetime.datetime.utcnow().strftime("%Y-%m-%d")
            else:
                to_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
                from_date = (
                    datetime.datetime.utcnow() - datetime.timedelta(days=max(1, days))
                ).strftime("%Y-%m-%d")
            df = _fetch_scores_hybrid(client, eid, eid_email, from_date, to_date)

        if df is None or df.empty:
            return {
                "status": "success",
                "eid": eid,
                "overall_score": None,
                "score_label": "No Data",
                "total_meetings": 0,
                "message": f"No GMeet score data found for EID '{eid}'.",
                "meetings": [],
                "category_scores": {},
                "error": None,
            }

        overall_score = _safe_float(df["gmeet_score"].mean())
        total_meetings = len(df)

        audio_avg = _safe_float(df["audio_score"].mean())
        video_valid = df["video_score"].dropna()
        screencast_valid = df["screencast_score"].dropna()

        video_avg = _safe_float(video_valid.mean()) if len(video_valid) else None
        screencast_avg = _safe_float(screencast_valid.mean()) if len(screencast_valid) else None

        category_scores = {
            "audio": {
                "avg": audio_avg,
                "label": _get_score_label(audio_avg),
                "sub_scores": {
                    "packet_loss": _safe_float(df["audio_pkt_loss_score"].mean()),
                    "jitter": _safe_float(df["audio_jitter_score"].mean()),
                    "latency": _safe_float(df["audio_latency_score"].mean()),
                },
            },
            "video": {
                "avg": video_avg,
                "label": _get_score_label(video_avg),
                "active_pct": round(len(video_valid) / total_meetings * 100, 1) if total_meetings else 0,
                "sub_scores": {
                    "packet_loss": _safe_float(df["video_pkt_loss_score"].dropna().mean()) if df["video_pkt_loss_score"].dropna().any() else None,
                    "resolution": _safe_float(df["video_resolution_score"].dropna().mean()) if df["video_resolution_score"].dropna().any() else None,
                    "fps": _safe_float(df["video_fps_score"].dropna().mean()) if df["video_fps_score"].dropna().any() else None,
                },
            },
            "screencast": {
                "avg": screencast_avg,
                "label": _get_score_label(screencast_avg),
                "active_pct": round(len(screencast_valid) / total_meetings * 100, 1) if total_meetings else 0,
                "sub_scores": {
                    "packet_loss": _safe_float(df["screencast_loss_score"].dropna().mean()) if df["screencast_loss_score"].dropna().any() else None,
                    "fps": _safe_float(df["screencast_fps_score"].dropna().mean()) if df["screencast_fps_score"].dropna().any() else None,
                },
            },
        }

        dt_col = "date_time" if "date_time" in df.columns else "Date_Time"
        df_sorted = df.sort_values(dt_col, ascending=False)

        date_range = {
            "start": _to_jsonable(df_sorted[dt_col].min()),
            "end": _to_jsonable(df_sorted[dt_col].max()),
        }

        # Limit to 5 meetings to avoid overwhelming the response
        meetings = _build_meetings_list(df_sorted.head(5))

        issues = _detect_issues(category_scores, df)

        result = {
            "status": "success",
            "eid": eid,
            "overall_score": overall_score,
            "score_label": _get_score_label(overall_score),
            "total_meetings": total_meetings,
            "date_range": date_range,
            "category_scores": category_scores,
            "issues_detected": issues,
            "meetings": meetings,
            "error": None,
        }

        logger.info(f"get_gmeet_score returning successfully for eid={eid}, total_meetings={total_meetings}, overall_score={overall_score}")
        return result
    except Exception as exc:
        logger.error("get_gmeet_score failed for eid=%s: %s", eid, exc, exc_info=True)
        return {"status": "error", "error": str(exc), "eid": eid}


# ─── Tool 4: Build Score Chart ───────────────────────────────────────────────


def build_score_chart(
    tool_context: ToolContext,
    eid: str = "",
    days: int = 30,
    start_date: str = "",
    end_date: str = "",
    chart_type: str = "line",
    metric: str = "gmeet_score",
) -> dict[str, Any]:
    """Build a chart showing GMeet score trends over time for an employee.

    Args:
        eid: Employee ID. If empty, uses the previously registered EID from
             this session.
        days: Number of past days (default 30). Ignored if start_date set.
        start_date: Start date YYYY-MM-DD (overrides days).
        end_date: End date YYYY-MM-DD (defaults to today).
        chart_type: One of "line", "bar", "area", "point" (default "line").
        metric: Score metric to chart. Options: "gmeet_score", "audio_score",
                "video_score", "screencast_score", "all_categories" (default
                "gmeet_score"). "all_categories" overlays audio/video/screencast.
    """
    eid = (eid or "").strip() or _get_session_eid(tool_context) or ""
    if not eid:
        return {
            "error": "EID is required. Please provide your Employee ID (e.g. VZ12345).",
            "vega_lite_spec": None,
        }

    try:
        client = _bq_client()
        eid_email = _resolve_eid_to_email(client, eid)

        if start_date:
            from_date = start_date.strip()
            to_date = (end_date or "").strip() or datetime.datetime.utcnow().strftime("%Y-%m-%d")
        else:
            to_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            from_date = (
                datetime.datetime.utcnow() - datetime.timedelta(days=max(1, days))
            ).strftime("%Y-%m-%d")

        df = _fetch_scores_hybrid(client, eid, eid_email, from_date, to_date)

        if df is None or df.empty:
            return {
                "error": None,
                "message": f"No score data found for EID '{eid}' in the given date range.",
                "rows": [],
                "summary": {},
                "vega_lite_spec": None,
            }

        dt_col = "date_time" if "date_time" in df.columns else "Date_Time"
        df = df.sort_values(dt_col, ascending=True)

        mark = chart_type.lower().strip()
        if mark not in {"line", "bar", "area", "point"}:
            mark = "line"

        if metric == "all_categories":
            chart_rows = []
            for _, row in df.iterrows():
                dt_val = _to_jsonable(row[dt_col])
                chart_rows.append({"date": dt_val, "score": _safe_float(row.get("audio_score")), "category": "Audio"})
                if pd.notna(row.get("video_score")):
                    chart_rows.append({"date": dt_val, "score": _safe_float(row["video_score"]), "category": "Video"})
                if pd.notna(row.get("screencast_score")):
                    chart_rows.append({"date": dt_val, "score": _safe_float(row["screencast_score"]), "category": "Screencast"})

            vega_lite_spec = {
                "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                "description": f"GMeet category scores over time for EID {eid}",
                "mark": {"type": mark, "point": True} if mark == "line" else mark,
                "data": {"values": chart_rows},
                "encoding": {
                    "x": {"field": "date", "type": "temporal", "title": "Date"},
                    "y": {"field": "score", "type": "quantitative", "title": "Score (0-10)", "scale": {"domain": [0, 10]}},
                    "color": {"field": "category", "type": "nominal", "title": "Category"},
                },
            }
        else:
            score_col = metric if metric in df.columns else "gmeet_score"
            chart_rows = []
            for _, row in df.iterrows():
                val = row.get(score_col)
                if pd.notna(val):
                    chart_rows.append({
                        "date": _to_jsonable(row[dt_col]),
                        "score": round(float(val), 2),
                    })

            vega_lite_spec = {
                "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                "description": f"GMeet {score_col} over time for EID {eid}",
                "mark": {"type": mark, "point": True} if mark == "line" else mark,
                "data": {"values": chart_rows},
                "encoding": {
                    "x": {"field": "date", "type": "temporal", "title": "Date"},
                    "y": {"field": "score", "type": "quantitative", "title": "Score (0-10)", "scale": {"domain": [0, 10]}},
                },
            }

        y_values = [r["score"] for r in chart_rows if r.get("score") is not None]
        summary = {
            "total_data_points": len(chart_rows),
            "y_min": min(y_values) if y_values else None,
            "y_max": max(y_values) if y_values else None,
            "y_avg": round(sum(y_values) / len(y_values), 2) if y_values else None,
        }

        return {
            "error": None,
            "rows": chart_rows,
            "summary": summary,
            "vega_lite_spec": vega_lite_spec,
        }
    except Exception as exc:
        logger.error("build_score_chart failed: %s", exc)
        return {"error": str(exc), "rows": [], "summary": {}, "vega_lite_spec": None}


# ─── Tool 5: Get Verizon Overall Stats ───────────────────────────────────────


def get_verizon_overall_stats(
    days: int = 30,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Get Verizon-wide GMeet score statistics for benchmarking.

    Args:
        days: Number of past days (default 30). Ignored if start_date set.
        start_date: Start date YYYY-MM-DD.
        end_date: End date YYYY-MM-DD (defaults to today).

    Returns overall averages, score distribution, and top issue areas
    across all Verizon employees for the given period.
    """
    try:
        if start_date:
            from_date = start_date.strip()
            to_date = (end_date or "").strip() or datetime.datetime.utcnow().strftime("%Y-%m-%d")
        else:
            to_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            from_date = (
                datetime.datetime.utcnow() - datetime.timedelta(days=max(1, days))
            ).strftime("%Y-%m-%d")

        client = _bq_client()

        query = f"""
        SELECT
            ROUND(AVG(gmeet_score), 2) as avg_gmeet_score,
            ROUND(AVG(audio_score), 2) as avg_audio_score,
            ROUND(AVG(CASE WHEN video_score IS NOT NULL THEN video_score END), 2) as avg_video_score,
            ROUND(AVG(CASE WHEN screencast_score IS NOT NULL THEN screencast_score END), 2) as avg_screencast_score,
            ROUND(AVG(audio_pkt_loss_score), 2) as avg_audio_pkt_loss,
            ROUND(AVG(audio_jitter_score), 2) as avg_audio_jitter,
            ROUND(AVG(audio_latency_score), 2) as avg_audio_latency,
            COUNT(*) as total_meetings,
            COUNT(DISTINCT user_email) as total_employees,
            COUNTIF(gmeet_score >= 9.0) as excellent_count,
            COUNTIF(gmeet_score >= 7.5 AND gmeet_score < 9.0) as good_count,
            COUNTIF(gmeet_score >= 6.0 AND gmeet_score < 7.5) as fair_count,
            COUNTIF(gmeet_score >= 4.0 AND gmeet_score < 6.0) as poor_count,
            COUNTIF(gmeet_score < 4.0) as critical_count
        FROM `{BQ_SCORES_TABLE}`
        WHERE score_date >= @from_date
          AND score_date <= @to_date
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("from_date", "STRING", from_date),
                bigquery.ScalarQueryParameter("to_date", "STRING", to_date),
            ]
        )

        rows = list(client.query(query, job_config=job_config).result())
        if not rows or rows[0].total_meetings == 0:
            return {
                "status": "no_data",
                "message": f"No Verizon-wide score data found for {from_date} to {to_date}.",
                "error": None,
            }

        row = rows[0]
        total = row.total_meetings

        return {
            "status": "success",
            "date_range": {"start": from_date, "end": to_date},
            "total_meetings": total,
            "total_employees": row.total_employees,
            "overall_score": float(row.avg_gmeet_score) if row.avg_gmeet_score else None,
            "score_label": _get_score_label(float(row.avg_gmeet_score)) if row.avg_gmeet_score else "N/A",
            "category_averages": {
                "audio": float(row.avg_audio_score) if row.avg_audio_score else None,
                "video": float(row.avg_video_score) if row.avg_video_score else None,
                "screencast": float(row.avg_screencast_score) if row.avg_screencast_score else None,
            },
            "audio_sub_scores": {
                "packet_loss": float(row.avg_audio_pkt_loss) if row.avg_audio_pkt_loss else None,
                "jitter": float(row.avg_audio_jitter) if row.avg_audio_jitter else None,
                "latency": float(row.avg_audio_latency) if row.avg_audio_latency else None,
            },
            "score_distribution": {
                "excellent": row.excellent_count,
                "good": row.good_count,
                "fair": row.fair_count,
                "poor": row.poor_count,
                "critical": row.critical_count,
            },
            "score_distribution_pct": {
                "excellent": round(row.excellent_count / total * 100, 1),
                "good": round(row.good_count / total * 100, 1),
                "fair": round(row.fair_count / total * 100, 1),
                "poor": round(row.poor_count / total * 100, 1),
                "critical": round(row.critical_count / total * 100, 1),
            },
            "error": None,
        }
    except Exception as exc:
        logger.error("get_verizon_overall_stats failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ─── Tool 6: Get Team GMeet Scores ─────────────────────────────────────────


def get_team_gmeet_scores(
    lookup_type: str,
    identifier: str,
    tool_context: ToolContext,
    days: int = 30,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Get GMeet quality scores for a team of employees by hierarchy or department.

    Use this when the user asks about scores for a group: a supervisor's direct
    reports, a manager/director/AD's organization, or everyone in a department.

    Args:
        lookup_type: How to look up the team. One of:
            - "supervisor": direct reports of the given supervisor
            - "manager": all people under the given manager in the hierarchy
            - "director": all people under the given director
            - "ad": all people under the given associate director
            - "department": all people in the given department
        identifier: The EID (e.g. "VZ12345") or full name of the leader, or
                    the department name for department lookups.
        days: Number of past days to analyze (default 30). Ignored if
              start_date is provided.
        start_date: Start date in YYYY-MM-DD format (overrides days).
        end_date: End date in YYYY-MM-DD format (defaults to today).
    """
    lookup_type = (lookup_type or "").strip().lower()
    if lookup_type not in _VALID_LOOKUP_TYPES:
        return {
            "status": "error",
            "error": f"Invalid lookup_type '{lookup_type}'. "
                     f"Must be one of: {', '.join(sorted(_VALID_LOOKUP_TYPES))}.",
        }

    try:
        client = _bq_client()

        team = _resolve_team_members(client, lookup_type, identifier)
        if team.get("error"):
            return {"status": "error", "error": team["error"],
                    "lookup_type": lookup_type, "identifier": identifier}

        members = team["members"]
        if not members:
            return {
                "status": "success",
                "lookup_type": lookup_type,
                "leader_name": team.get("leader_name"),
                "leader_eid": team.get("leader_eid"),
                "team_member_count": 0,
                "message": f"No team members found for {lookup_type} '{identifier}'.",
            }

        if start_date:
            from_date = start_date.strip()
            to_date = (end_date or "").strip() or datetime.datetime.utcnow().strftime("%Y-%m-%d")
        else:
            to_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            from_date = (
                datetime.datetime.utcnow() - datetime.timedelta(days=max(1, days))
            ).strftime("%Y-%m-%d")

        df = _fetch_bulk_scores(client, members, from_date, to_date)

        member_lookup = {m["eid"].strip().zfill(10): m for m in members if m.get("eid")}
        email_lookup = {m["email"].strip().lower(): m for m in members if m.get("email")}

        member_scores = []
        members_with_data = set()

        if df is not None and not df.empty:
            grouped = df.groupby("eid") if "eid" in df.columns else df.groupby("user_email")
            for key, grp in grouped:
                avg_gmeet = _safe_float(grp["gmeet_score"].mean())
                avg_audio = _safe_float(grp["audio_score"].mean())
                video_valid = grp["video_score"].dropna()
                sc_valid = grp["screencast_score"].dropna()
                avg_video = _safe_float(video_valid.mean()) if len(video_valid) else None
                avg_sc = _safe_float(sc_valid.mean()) if len(sc_valid) else None

                padded_key = str(key).strip().zfill(10)
                m = member_lookup.get(padded_key)
                if not m:
                    m = email_lookup.get(str(key).strip().lower())
                if not m:
                    m = {"eid": str(key), "name": str(key), "email": ""}

                members_with_data.add(m.get("eid", "").strip().zfill(10))
                member_scores.append({
                    "eid": m.get("eid", ""),
                    "name": m.get("name", ""),
                    "email": m.get("email", ""),
                    "jobtitle": m.get("jobtitle", ""),
                    "avg_gmeet_score": avg_gmeet,
                    "score_label": _get_score_label(avg_gmeet),
                    "meeting_count": len(grp),
                    "avg_audio_score": avg_audio,
                    "avg_video_score": avg_video,
                    "avg_screencast_score": avg_sc,
                })

        member_scores.sort(key=lambda x: x["avg_gmeet_score"] or 0, reverse=True)

        members_without_data = []
        for m in members:
            eid_padded = (m.get("eid") or "").strip().zfill(10)
            if eid_padded not in members_with_data:
                members_without_data.append({
                    "eid": m.get("eid", ""),
                    "name": m.get("name", ""),
                    "email": m.get("email", ""),
                    "jobtitle": m.get("jobtitle", ""),
                })

        scored_values = [ms["avg_gmeet_score"] for ms in member_scores if ms["avg_gmeet_score"] is not None]
        team_avg = round(sum(scored_values) / len(scored_values), 2) if scored_values else None
        total_meetings = sum(ms["meeting_count"] for ms in member_scores)

        audio_vals = [ms["avg_audio_score"] for ms in member_scores if ms["avg_audio_score"] is not None]
        video_vals = [ms["avg_video_score"] for ms in member_scores if ms["avg_video_score"] is not None]
        sc_vals = [ms["avg_screencast_score"] for ms in member_scores if ms["avg_screencast_score"] is not None]

        score_distribution = {
            "excellent": sum(1 for v in scored_values if v >= 9.0),
            "good": sum(1 for v in scored_values if 7.5 <= v < 9.0),
            "fair": sum(1 for v in scored_values if 6.0 <= v < 7.5),
            "poor": sum(1 for v in scored_values if 4.0 <= v < 6.0),
            "critical": sum(1 for v in scored_values if v < 4.0),
        }

        bottom_performers = [ms for ms in member_scores if (ms["avg_gmeet_score"] or 10) < 6.0]
        bottom_performers.sort(key=lambda x: x["avg_gmeet_score"] or 0)
        top_performers = [ms for ms in member_scores if (ms["avg_gmeet_score"] or 0) >= 9.0]

        # Limit member details to top 10 and bottom 5 to reduce payload
        limited_member_scores = member_scores[:15] if len(member_scores) > 15 else member_scores

        return {
            "status": "success",
            "lookup_type": lookup_type,
            "leader_name": team.get("leader_name"),
            "leader_eid": team.get("leader_eid"),
            "department": team.get("department"),
            "date_range": {"start": from_date, "end": to_date},
            "team_member_count": len(members),
            "team_summary": {
                "avg_gmeet_score": team_avg,
                "score_label": _get_score_label(team_avg),
                "avg_audio_score": round(sum(audio_vals) / len(audio_vals), 2) if audio_vals else None,
                "avg_video_score": round(sum(video_vals) / len(video_vals), 2) if video_vals else None,
                "avg_screencast_score": round(sum(sc_vals) / len(sc_vals), 2) if sc_vals else None,
                "total_meetings": total_meetings,
                "members_with_data": len(members_with_data),
                "members_without_data": len(members_without_data),
            },
            "score_distribution": score_distribution,
            "member_scores": limited_member_scores,
            "members_without_score_data": members_without_data[:5] if len(members_without_data) > 5 else members_without_data,
            "bottom_performers": bottom_performers[:5],
            "top_performers": top_performers[:10],
            "error": None,
        }
    except Exception as exc:
        logger.error("get_team_gmeet_scores failed: %s", exc)
        return {"status": "error", "error": str(exc),
                "lookup_type": lookup_type, "identifier": identifier}


# ─── Tool 7: Get Room Score ────────────────────────────────────────────────


def _fetch_room_scores(
    client: bigquery.Client,
    from_date: str,
    to_date: str,
    room_name: str = "",
    location: str = "",
    city: str = "",
) -> pd.DataFrame | None:
    conditions = [
        "score_date >= @from_date",
        "score_date <= @to_date",
    ]
    params: list = [
        bigquery.ScalarQueryParameter("from_date", "STRING", from_date),
        bigquery.ScalarQueryParameter("to_date", "STRING", to_date),
    ]

    if room_name:
        conditions.append("LOWER(room_name) LIKE CONCAT('%', LOWER(@room_name), '%')")
        params.append(bigquery.ScalarQueryParameter("room_name", "STRING", room_name.strip()))
    if location:
        loc = location.strip()
        conditions.append(
            "(LOWER(location_code) = LOWER(@location) OR LOWER(location_name) LIKE CONCAT('%', LOWER(@location), '%'))"
        )
        params.append(bigquery.ScalarQueryParameter("location", "STRING", loc))
    if city:
        conditions.append("LOWER(city) LIKE CONCAT('%', LOWER(@city), '%')")
        params.append(bigquery.ScalarQueryParameter("city", "STRING", city.strip()))

    where = " AND ".join(conditions)
    query = f"""
    SELECT room_name, location_code, location_name, city, building_info,
           gmeet_score, audio_score, video_score, screencast_score,
           audio_pkt_loss_score, audio_jitter_score, audio_latency_score,
           video_pkt_loss_score, video_resolution_score, video_fps_score,
           screencast_loss_score, screencast_fps_score,
           score_date, date_time, conference_id, duration_seconds, user_role
    FROM `{BQ_ROOM_SCORES_TABLE}`
    WHERE {where}
    ORDER BY date_time DESC
    LIMIT 5000
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        df = client.query(query, job_config=job_config).to_dataframe()
        return df if not df.empty else None
    except Exception as exc:
        logger.error("Room score fetch failed: %s", exc)
        return None


def _detect_room_issues(category_scores: dict, df: pd.DataFrame) -> list[dict]:
    issues = []
    audio = category_scores.get("audio", {})
    video = category_scores.get("video", {})
    screencast = category_scores.get("screencast", {})

    if audio.get("avg") is not None and audio["avg"] < 7.5:
        issues.append({"area": "audio", "severity": "Poor" if audio["avg"] < 6.0 else "Fair",
                        "detail": f"Room audio score {audio['avg']}/10 is below acceptable threshold.",
                        "recommendation": "Check room speakerphone/mic equipment, verify network QoS settings, inspect room ethernet connection."})
    sub = audio.get("sub_scores", {})
    if sub.get("packet_loss") is not None and sub["packet_loss"] < 8.0:
        issues.append({"area": "audio_packet_loss", "severity": "Warning",
                        "detail": f"Audio packet loss score {sub['packet_loss']}/10 — participants may hear dropouts.",
                        "recommendation": "Check room network switch port, verify dedicated VLAN for AV equipment, inspect ethernet cable."})
    if sub.get("jitter") is not None and sub["jitter"] < 8.0:
        issues.append({"area": "audio_jitter", "severity": "Warning",
                        "detail": f"Audio jitter score {sub['jitter']}/10 — audio may sound choppy.",
                        "recommendation": "Enable QoS prioritization for the room network port, check for network congestion."})
    if sub.get("latency") is not None and sub["latency"] < 8.0:
        issues.append({"area": "audio_latency", "severity": "Warning",
                        "detail": f"Audio latency score {sub['latency']}/10 — noticeable conversation delay.",
                        "recommendation": "Check room network routing, verify no proxy/VPN on room device, consider wired connection."})
    if video.get("avg") is not None and video["avg"] < 7.5:
        issues.append({"area": "video", "severity": "Poor" if video["avg"] < 6.0 else "Fair",
                        "detail": f"Room video score {video['avg']}/10 — video quality is degraded.",
                        "recommendation": "Check room camera hardware and drivers, verify sufficient upload bandwidth (>2 Mbps), update firmware."})
    if screencast.get("avg") is not None and screencast["avg"] < 7.5:
        issues.append({"area": "screencast", "severity": "Poor" if screencast["avg"] < 6.0 else "Fair",
                        "detail": f"Room screencast score {screencast['avg']}/10 — screen sharing quality is low.",
                        "recommendation": "Check room display hardware, reduce shared resolution, verify HDMI/USB-C connection to room device."})
    return issues


def get_room_score(
    tool_context: ToolContext,
    room_name: str = "",
    location: str = "",
    city: str = "",
    days: int = 30,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Get GMeet quality scores for conference rooms.

    Use this when the user asks about meeting room quality, room scores,
    or room performance. Can filter by room name, location/country, or city.

    Args:
        room_name: Full or partial conference room name (e.g. "IN-Chennai-TP-7F-Agni").
        location: Location code (e.g. "IN", "NJ") or full name (e.g. "India", "New Jersey").
        city: City name (e.g. "Chennai", "BaskingRidge").
        days: Number of past days (default 30). Ignored if start_date provided.
        start_date: Start date YYYY-MM-DD (overrides days).
        end_date: End date YYYY-MM-DD (defaults to today).
    """
    if not room_name and not location and not city:
        return {"status": "error", "error": "Provide at least one of: room_name, location, or city."}

    logger.info(f"get_room_score called with room_name={room_name}, location={location}, city={city}, days={days}")

    try:
        client = _bq_client()

        if start_date:
            from_date = start_date.strip()
            to_date = (end_date or "").strip() or datetime.datetime.utcnow().strftime("%Y-%m-%d")
        else:
            to_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            from_date = (datetime.datetime.utcnow() - datetime.timedelta(days=max(1, days))).strftime("%Y-%m-%d")

        df = _fetch_room_scores(client, from_date, to_date, room_name, location, city)

        if df is None or df.empty:
            filters = ", ".join(f for f in [room_name, location, city] if f)
            return {"status": "success", "total_meetings": 0,
                    "message": f"No room score data found for filters: {filters}.",
                    "rooms": [], "error": None}

        rooms_grouped = df.groupby("room_name")
        room_results = []

        for rname, grp in rooms_grouped:
            avg_gmeet = _safe_float(grp["gmeet_score"].mean())
            avg_audio = _safe_float(grp["audio_score"].mean())
            video_valid = grp["video_score"].dropna()
            sc_valid = grp["screencast_score"].dropna()
            avg_video = _safe_float(video_valid.mean()) if len(video_valid) else None
            avg_sc = _safe_float(sc_valid.mean()) if len(sc_valid) else None

            category_scores = {
                "audio": {
                    "avg": avg_audio, "label": _get_score_label(avg_audio),
                    "sub_scores": {
                        "packet_loss": _safe_float(grp["audio_pkt_loss_score"].mean()),
                        "jitter": _safe_float(grp["audio_jitter_score"].mean()),
                        "latency": _safe_float(grp["audio_latency_score"].mean()),
                    },
                },
                "video": {
                    "avg": avg_video, "label": _get_score_label(avg_video),
                    "sub_scores": {
                        "packet_loss": _safe_float(grp["video_pkt_loss_score"].dropna().mean()) if grp["video_pkt_loss_score"].dropna().any() else None,
                        "resolution": _safe_float(grp["video_resolution_score"].dropna().mean()) if grp["video_resolution_score"].dropna().any() else None,
                        "fps": _safe_float(grp["video_fps_score"].dropna().mean()) if grp["video_fps_score"].dropna().any() else None,
                    },
                },
                "screencast": {
                    "avg": avg_sc, "label": _get_score_label(avg_sc),
                    "sub_scores": {
                        "packet_loss": _safe_float(grp["screencast_loss_score"].dropna().mean()) if grp["screencast_loss_score"].dropna().any() else None,
                        "fps": _safe_float(grp["screencast_fps_score"].dropna().mean()) if grp["screencast_fps_score"].dropna().any() else None,
                    },
                },
            }

            issues = _detect_room_issues(category_scores, grp)

            first_row = grp.iloc[0]

            # Create simplified room info for summary
            room_info = {
                "room_name": str(rname),
                "location_code": str(first_row.get("location_code", "")),
                "location_name": str(first_row.get("location_name", "")),
                "city": str(first_row.get("city", "")),
                "building_info": str(first_row.get("building_info", "")),
                "avg_gmeet_score": avg_gmeet,
                "score_label": _get_score_label(avg_gmeet),
                "total_meetings": len(grp),
                "avg_audio_score": avg_audio,
                "avg_video_score": avg_video,
                "avg_screencast_score": avg_sc,
                "issues_count": len(issues),
                "top_issue": issues[0] if issues else None,
            }

            room_results.append(room_info)

        room_results.sort(key=lambda x: x["avg_gmeet_score"] or 0, reverse=True)

        # Calculate summary statistics
        scores = [r["avg_gmeet_score"] for r in room_results if r["avg_gmeet_score"] is not None]
        summary_stats = {
            "total_rooms": len(room_results),
            "total_meetings": len(df),
            "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
            "best_room_name": room_results[0]["room_name"] if room_results else None,
            "best_room_score": room_results[0]["avg_gmeet_score"] if room_results else None,
            "worst_room_name": room_results[-1]["room_name"] if room_results else None,
            "worst_room_score": room_results[-1]["avg_gmeet_score"] if room_results else None,
            "excellent_count": sum(1 for s in scores if s >= 9.0),
            "good_count": sum(1 for s in scores if 7.5 <= s < 9.0),
            "fair_count": sum(1 for s in scores if 6.0 <= s < 7.5),
            "poor_count": sum(1 for s in scores if s < 6.0),
        }

        # Limit detailed room data to top 10 and bottom 5 to reduce payload size
        top_rooms = room_results[:10]
        bottom_rooms = room_results[-5:] if len(room_results) > 10 else []

        result = {
            "status": "success",
            "date_range": {"start": from_date, "end": to_date},
            "summary": summary_stats,
            "top_rooms": top_rooms,
            "bottom_rooms": bottom_rooms,
            "error": None,
        }

        logger.info(f"get_room_score returning successfully: {summary_stats['total_rooms']} rooms, avg_score={summary_stats['avg_score']}")
        return result
    except Exception as exc:
        logger.error("get_room_score failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}


# ─── Tool 8: Compare Rooms ────────────────────────────────────────────────


def compare_rooms(
    room_names: str,
    tool_context: ToolContext,
    days: int = 30,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Compare GMeet quality scores between two or more conference rooms.

    Use this when the user wants to compare specific rooms side by side.

    Args:
        room_names: Comma-separated list of room names to compare
                    (e.g. "IN-Chennai-TP-7F-Agni, NJ-BaskingRidge-Tower1-3").
        days: Number of past days (default 30). Ignored if start_date provided.
        start_date: Start date YYYY-MM-DD.
        end_date: End date YYYY-MM-DD (defaults to today).
    """
    names = [n.strip() for n in (room_names or "").split(",") if n.strip()]
    if len(names) < 2:
        return {"status": "error", "error": "Provide at least 2 room names separated by commas."}

    try:
        client = _bq_client()

        if start_date:
            from_date = start_date.strip()
            to_date = (end_date or "").strip() or datetime.datetime.utcnow().strftime("%Y-%m-%d")
        else:
            to_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            from_date = (datetime.datetime.utcnow() - datetime.timedelta(days=max(1, days))).strftime("%Y-%m-%d")

        comparisons = []
        for name in names:
            df = _fetch_room_scores(client, from_date, to_date, room_name=name)
            if df is None or df.empty:
                comparisons.append({"room_name": name, "avg_gmeet_score": None,
                                     "score_label": "No Data", "total_meetings": 0,
                                     "avg_audio_score": None, "avg_video_score": None,
                                     "avg_screencast_score": None})
                continue

            avg_gmeet = _safe_float(df["gmeet_score"].mean())
            avg_audio = _safe_float(df["audio_score"].mean())
            video_valid = df["video_score"].dropna()
            sc_valid = df["screencast_score"].dropna()

            comparisons.append({
                "room_name": name,
                "location_code": str(df.iloc[0].get("location_code", "")),
                "location_name": str(df.iloc[0].get("location_name", "")),
                "city": str(df.iloc[0].get("city", "")),
                "avg_gmeet_score": avg_gmeet,
                "score_label": _get_score_label(avg_gmeet),
                "total_meetings": len(df),
                "avg_audio_score": avg_audio,
                "avg_video_score": _safe_float(video_valid.mean()) if len(video_valid) else None,
                "avg_screencast_score": _safe_float(sc_valid.mean()) if len(sc_valid) else None,
            })

        return {
            "status": "success",
            "date_range": {"start": from_date, "end": to_date},
            "comparisons": comparisons,
            "error": None,
        }
    except Exception as exc:
        logger.error("compare_rooms failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ─── Tool 9: Get Location Room Stats ──────────────────────────────────────


def get_location_room_stats(
    tool_context: ToolContext,
    location: str = "",
    city: str = "",
    days: int = 30,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Get aggregated room statistics by location or city.

    Use this when the user asks about room quality across a location, country,
    city, or wants to find the best/worst rooms in an area.

    Args:
        location: Location code (e.g. "IN") or full name (e.g. "India"). If empty with
                  city also empty, returns stats for all locations.
        city: City name (e.g. "Chennai"). Narrows results within a location.
        days: Number of past days (default 30). Ignored if start_date provided.
        start_date: Start date YYYY-MM-DD.
        end_date: End date YYYY-MM-DD (defaults to today).
    """
    try:
        client = _bq_client()

        if start_date:
            from_date = start_date.strip()
            to_date = (end_date or "").strip() or datetime.datetime.utcnow().strftime("%Y-%m-%d")
        else:
            to_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            from_date = (datetime.datetime.utcnow() - datetime.timedelta(days=max(1, days))).strftime("%Y-%m-%d")

        df = _fetch_room_scores(client, from_date, to_date, location=location, city=city)

        if df is None or df.empty:
            filters = ", ".join(f for f in [location, city] if f) or "all locations"
            return {"status": "success", "total_rooms": 0,
                    "message": f"No room data found for {filters}.",
                    "locations": [], "error": None}

        location_groups = df.groupby(["location_code", "location_name"])
        location_stats = []

        for (loc_code, loc_name), loc_df in location_groups:
            city_groups = loc_df.groupby("city")
            city_stats = []

            for city_name, city_df in city_groups:
                room_groups = city_df.groupby("room_name")
                room_avgs = []
                for rname, rdf in room_groups:
                    room_avgs.append({
                        "room_name": str(rname),
                        "avg_gmeet_score": _safe_float(rdf["gmeet_score"].mean()),
                        "total_meetings": len(rdf),
                    })
                room_avgs.sort(key=lambda x: x["avg_gmeet_score"] or 0, reverse=True)

                # Limit room details to top 5 and bottom 3 per city
                city_stats.append({
                    "city": str(city_name),
                    "total_rooms": len(room_avgs),
                    "total_meetings": len(city_df),
                    "avg_gmeet_score": _safe_float(city_df["gmeet_score"].mean()),
                    "score_label": _get_score_label(_safe_float(city_df["gmeet_score"].mean())),
                    "best_room_name": room_avgs[0]["room_name"] if room_avgs else None,
                    "best_room_score": room_avgs[0]["avg_gmeet_score"] if room_avgs else None,
                    "worst_room_name": room_avgs[-1]["room_name"] if room_avgs else None,
                    "worst_room_score": room_avgs[-1]["avg_gmeet_score"] if room_avgs else None,
                    "top_rooms": room_avgs[:5],
                    "bottom_rooms": room_avgs[-3:] if len(room_avgs) > 5 else [],
                })

            city_stats.sort(key=lambda x: x["avg_gmeet_score"] or 0, reverse=True)

            # Calculate score distribution from all rooms in this location
            all_room_scores = []
            for cs in city_stats:
                all_room_scores.extend([r["avg_gmeet_score"] for r in cs["top_rooms"] if r["avg_gmeet_score"] is not None])
                all_room_scores.extend([r["avg_gmeet_score"] for r in cs["bottom_rooms"] if r["avg_gmeet_score"] is not None])

            score_dist = {
                "excellent": sum(1 for s in all_room_scores if s >= 9.0),
                "good": sum(1 for s in all_room_scores if 7.5 <= s < 9.0),
                "fair": sum(1 for s in all_room_scores if 6.0 <= s < 7.5),
                "poor": sum(1 for s in all_room_scores if 4.0 <= s < 6.0),
                "critical": sum(1 for s in all_room_scores if s < 4.0),
            }

            location_stats.append({
                "location_code": str(loc_code),
                "location_name": str(loc_name),
                "total_rooms": sum(cs["total_rooms"] for cs in city_stats),
                "total_meetings": len(loc_df),
                "avg_gmeet_score": _safe_float(loc_df["gmeet_score"].mean()),
                "score_label": _get_score_label(_safe_float(loc_df["gmeet_score"].mean())),
                "score_distribution": score_dist,
                "cities": city_stats,
            })

        location_stats.sort(key=lambda x: x["avg_gmeet_score"] or 0, reverse=True)

        return {
            "status": "success",
            "date_range": {"start": from_date, "end": to_date},
            "total_locations": len(location_stats),
            "total_rooms": sum(ls["total_rooms"] for ls in location_stats),
            "total_meetings": len(df),
            "locations": location_stats,
            "error": None,
        }
    except Exception as exc:
        logger.error("get_location_room_stats failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ─── Helpers: team / hierarchy lookups ──────────────────────────────────────


_VALID_LOOKUP_TYPES = {"supervisor", "manager", "director", "ad", "department"}


def _is_eid_format(identifier: str) -> bool:
    s = identifier.strip()
    return s.upper().startswith("VZ") or s.isdigit()


def _resolve_eid_to_full_name(client: bigquery.Client, eid: str) -> str | None:
    padded_eid = str(eid).strip().zfill(10)
    query = f"""
    SELECT name
    FROM `{HR_TABLE}`
    WHERE b_eid = @eid
    LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("eid", "STRING", padded_eid)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
        if rows and rows[0].name:
            return str(rows[0].name).strip()
    except Exception as exc:
        logger.warning("Full name resolution failed for %s: %s", eid, exc)
    return None


def _resolve_name_to_eid(client: bigquery.Client, name: str) -> list[dict[str, str]]:
    clean = name.strip()
    query = f"""
    SELECT b_eid, name, work_email_addr
    FROM `{HR_TABLE}`
    WHERE LOWER(name) = LOWER(@name)
       OR LOWER(name) = LOWER(@name_reversed)
       OR (LOWER(first_name) LIKE CONCAT('%', LOWER(@part1), '%')
           AND LOWER(last_name) LIKE CONCAT('%', LOWER(@part2), '%'))
       OR (LOWER(first_name) LIKE CONCAT('%', LOWER(@part2), '%')
           AND LOWER(last_name) LIKE CONCAT('%', LOWER(@part1), '%'))
    LIMIT 10
    """
    parts = [p.strip() for p in clean.replace(",", " ").split() if p.strip()]
    part1 = parts[0] if parts else clean
    part2 = parts[1] if len(parts) > 1 else part1
    if "," in clean:
        reversed_name = clean
    else:
        reversed_name = f"{part2},{part1}" if len(parts) > 1 else clean

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("name", "STRING", clean),
            bigquery.ScalarQueryParameter("name_reversed", "STRING", reversed_name),
            bigquery.ScalarQueryParameter("part1", "STRING", part1),
            bigquery.ScalarQueryParameter("part2", "STRING", part2),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
        return [
            {"eid": r.b_eid, "name": r.name, "email": r.work_email_addr}
            for r in rows
        ]
    except Exception as exc:
        logger.warning("Name-to-EID lookup failed for %s: %s", name, exc)
        return []


def _resolve_team_members(
    client: bigquery.Client,
    lookup_type: str,
    identifier: str,
) -> dict[str, Any]:
    identifier = (identifier or "").strip()
    if not identifier:
        return {"leader_name": None, "leader_eid": None, "lookup_type": lookup_type,
                "members": [], "member_count": 0, "error": "Identifier is required."}

    if lookup_type == "department":
        return _resolve_department_members(client, identifier)

    if _is_eid_format(identifier):
        padded = identifier.strip().zfill(10)
        leader_eid = padded
        leader_name = _resolve_eid_to_full_name(client, padded)
        if not leader_name:
            return {"leader_name": None, "leader_eid": padded, "lookup_type": lookup_type,
                    "members": [], "member_count": 0,
                    "error": f"Could not find employee with EID '{identifier}'."}
    else:
        matches = _resolve_name_to_eid(client, identifier)
        if not matches:
            return {"leader_name": identifier, "leader_eid": None, "lookup_type": lookup_type,
                    "members": [], "member_count": 0,
                    "error": f"No employee found with name '{identifier}'."}
        if len(matches) > 1:
            return {
                "leader_name": None, "leader_eid": None, "lookup_type": lookup_type,
                "members": [], "member_count": 0,
                "status": "multiple_matches",
                "matches": [
                    {"eid": m["eid"], "name": m["name"], "email": m.get("email", "")}
                    for m in matches
                ],
                "error": f"Multiple employees found matching '{identifier}'. "
                         f"Please specify the EID to confirm. "
                         f"Matches: " + ", ".join(
                             f"{m['name']} (EID: {m['eid']})" for m in matches
                         ),
            }
        leader_name = matches[0]["name"]
        leader_eid = matches[0]["eid"]

    if lookup_type == "supervisor":
        return _resolve_supervisor_reports(client, leader_name, leader_eid)

    return _resolve_hierarchy_reports(client, leader_name, leader_eid, lookup_type)


def _resolve_supervisor_reports(
    client: bigquery.Client, leader_name: str, leader_eid: str | None
) -> dict[str, Any]:
    query = f"""
    SELECT b_eid, name, work_email_addr, jobtitle, band
    FROM `{HR_TABLE}`
    WHERE LOWER(supervisor_name) = LOWER(@supervisor_name)
    ORDER BY last_name, first_name
    LIMIT 500
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("supervisor_name", "STRING", leader_name),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
        members = [
            {"eid": r.b_eid, "name": r.name, "email": r.work_email_addr,
             "jobtitle": r.jobtitle, "band": r.band}
            for r in rows
        ]
        return {"leader_name": leader_name, "leader_eid": leader_eid,
                "lookup_type": "supervisor", "members": members,
                "member_count": len(members), "error": None}
    except Exception as exc:
        logger.error("Supervisor report lookup failed: %s", exc)
        return {"leader_name": leader_name, "leader_eid": leader_eid,
                "lookup_type": "supervisor", "members": [], "member_count": 0,
                "error": str(exc)}


def _resolve_hierarchy_reports(
    client: bigquery.Client, leader_name: str, leader_eid: str | None, lookup_type: str
) -> dict[str, Any]:
    padded = (leader_eid or "").strip().zfill(10) if leader_eid else None

    level_cols = ", ".join(f"level_{i}_name" for i in range(1, 11))

    if padded:
        find_query = f"""
        SELECT name, {level_cols}
        FROM `{HR_TABLE}`
        WHERE b_eid = @eid
        LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("eid", "STRING", padded)]
        )
    else:
        find_query = f"""
        SELECT name, {level_cols}
        FROM `{HR_TABLE}`
        WHERE LOWER(name) = LOWER(@leader_name)
        LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("leader_name", "STRING", leader_name),
            ]
        )

    try:
        rows = list(client.query(find_query, job_config=job_config).result())
    except Exception as exc:
        logger.error("Hierarchy leader lookup failed: %s", exc)
        return {"leader_name": leader_name, "leader_eid": leader_eid,
                "lookup_type": lookup_type, "members": [], "member_count": 0,
                "error": str(exc)}

    if not rows:
        return {"leader_name": leader_name, "leader_eid": leader_eid,
                "lookup_type": lookup_type, "members": [], "member_count": 0,
                "error": f"No HR record found for '{leader_name}'."}

    row = rows[0]
    resolved_name = row.name or leader_name
    leader_level = None
    for i in range(1, 11):
        col_val = getattr(row, f"level_{i}_name", None)
        if col_val and col_val.strip().lower() == resolved_name.strip().lower():
            leader_level = i
            break

    if leader_level is None:
        return {"leader_name": resolved_name, "leader_eid": leader_eid,
                "lookup_type": lookup_type, "members": [], "member_count": 0,
                "error": f"Could not determine hierarchy level for '{resolved_name}'."}

    level_col = f"level_{leader_level}_name"
    team_query = f"""
    SELECT b_eid, name, work_email_addr, jobtitle, band
    FROM `{HR_TABLE}`
    WHERE LOWER({level_col}) = LOWER(@leader_name)
    ORDER BY last_name, first_name
    LIMIT 500
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("leader_name", "STRING", resolved_name),
        ]
    )
    try:
        rows = list(client.query(team_query, job_config=job_config).result())
        members = [
            {"eid": r.b_eid, "name": r.name, "email": r.work_email_addr,
             "jobtitle": r.jobtitle, "band": r.band}
            for r in rows
        ]
        return {"leader_name": resolved_name, "leader_eid": leader_eid,
                "lookup_type": lookup_type, "members": members,
                "member_count": len(members), "error": None}
    except Exception as exc:
        logger.error("Hierarchy team lookup failed: %s", exc)
        return {"leader_name": resolved_name, "leader_eid": leader_eid,
                "lookup_type": lookup_type, "members": [], "member_count": 0,
                "error": str(exc)}


def _resolve_department_members(
    client: bigquery.Client, department: str
) -> dict[str, Any]:
    query = f"""
    SELECT b_eid, name, work_email_addr, jobtitle, band
    FROM `{HR_TABLE}`
    WHERE LOWER(b_dept_descr) LIKE CONCAT('%', LOWER(@dept_name), '%')
       OR department_id = @dept_id
    ORDER BY last_name, first_name
    LIMIT 500
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("dept_name", "STRING", department.strip()),
            bigquery.ScalarQueryParameter("dept_id", "STRING", department.strip()),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
        members = [
            {"eid": r.b_eid, "name": r.name, "email": r.work_email_addr,
             "jobtitle": r.jobtitle, "band": r.band}
            for r in rows
        ]
        return {"leader_name": None, "leader_eid": None,
                "lookup_type": "department", "department": department.strip(),
                "members": members, "member_count": len(members), "error": None}
    except Exception as exc:
        logger.error("Department lookup failed: %s", exc)
        return {"leader_name": None, "leader_eid": None,
                "lookup_type": "department", "department": department.strip(),
                "members": [], "member_count": 0, "error": str(exc)}


def _fetch_bulk_scores(
    client: bigquery.Client,
    members: list[dict],
    from_date: str,
    to_date: str,
) -> pd.DataFrame | None:
    eid_list = [m["eid"].strip().zfill(10) for m in members if m.get("eid")]
    email_list = [m["email"].strip().lower() for m in members if m.get("email")]

    if not eid_list and not email_list:
        return None

    query = f"""
    SELECT eid, user_email, gmeet_score, audio_score, video_score,
           screencast_score, score_date, date_time
    FROM `{BQ_SCORES_TABLE}`
    WHERE score_date >= @from_date
      AND score_date <= @to_date
      AND (
        eid IN UNNEST(@eid_list)
        OR LOWER(user_email) IN UNNEST(@email_list)
      )
    ORDER BY eid, date_time DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("from_date", "STRING", from_date),
            bigquery.ScalarQueryParameter("to_date", "STRING", to_date),
            bigquery.ArrayQueryParameter("eid_list", "STRING", eid_list),
            bigquery.ArrayQueryParameter("email_list", "STRING", email_list),
        ]
    )
    try:
        df = client.query(query, job_config=job_config).to_dataframe()
        return df if not df.empty else None
    except Exception as exc:
        logger.error("Bulk score fetch failed: %s", exc)
        return None


# ─── Real-time scoring pipeline (for today's unscored meetings) ──────────────

import numpy as np


def _audio_packet_loss_score(row):
    avg = (row["audio_send_packet_loss_max"] + row["audio_recv_packet_loss_max"]) / 2
    return max(0, min(10, 10 - (avg / 5) * 10))


def _audio_jitter_score(row):
    return max(0, min(10, 10 - (row["network_recv_jitter_msec_max"] / 100) * 10))


def _audio_latency_score(row):
    return max(0, min(10, 10 - (row["network_rtt_msec_mean"] / 400) * 10))


def _video_packet_loss_score(row):
    if row["video_send_seconds"] == 0:
        return np.nan
    avg = (row["video_send_packet_loss_max"] + row["video_recv_packet_loss_max"]) / 2
    return max(0, min(10, 10 - (avg / 5) * 10))


def _video_resolution_score(row):
    if row["video_send_seconds"] == 0:
        return np.nan
    px = row["video_send_short_side_median_pixels"]
    if pd.isna(px):
        return np.nan
    if px >= 720:
        return 10
    elif px >= 360:
        return 7
    elif px >= 180:
        return 4
    return 2


def _video_fps_score(row):
    if row["video_send_seconds"] == 0:
        return np.nan
    fps = row["video_send_fps_mean"]
    if pd.isna(fps):
        return np.nan
    if fps >= 24:
        return 10
    elif fps >= 15:
        return 7
    elif fps >= 10:
        return 5
    return 2


def _screencast_loss_score(row):
    if row["screencast_send_seconds"] == 0:
        return np.nan
    return max(0, min(10, 10 - (row["screencast_send_packet_loss_max"] / 5) * 10))


def _screencast_fps_score(row):
    if row["screencast_send_seconds"] == 0:
        return np.nan
    fps = row["screencast_send_fps_mean"]
    if pd.isna(fps):
        return np.nan
    if fps >= 20:
        return 10
    elif fps >= 10:
        return 7
    elif fps >= 5:
        return 4
    return 2


def _compute_video_score(row):
    scores = {
        "resolution": (row["video_resolution_score"], 0.50),
        "pkt_loss": (row["video_pkt_loss_score"], 0.30),
        "fps": (row["video_fps_score"], 0.20),
    }
    valid = {k: v for k, v in scores.items() if pd.notna(v[0])}
    if not valid:
        return np.nan
    total_weight = sum(w for _, w in valid.values())
    return sum(s * (w / total_weight) for s, w in valid.values())


def _compute_screencast_score(row):
    scores = {
        "loss": (row["screencast_loss_score"], 0.60),
        "fps": (row["screencast_fps_score"], 0.40),
    }
    valid = {k: v for k, v in scores.items() if pd.notna(v[0])}
    if not valid:
        return np.nan
    total_weight = sum(w for _, w in valid.values())
    return sum(s * (w / total_weight) for s, w in valid.values())


def _determine_role(row):
    has_video = row["video_send_seconds"] > 0
    has_screen = row["screencast_send_seconds"] > 0
    if has_video and has_screen:
        return "Presenter"
    elif has_video:
        return "Speaker"
    elif has_screen:
        return "Remote Presenter"
    return "Listener"


def _compute_final_score(row):
    role = row["user_role"]
    if role == "Presenter":
        return row["audio_score"] * 0.50 + row["video_score"] * 0.25 + row["screencast_score"] * 0.25
    elif role == "Speaker":
        return row["audio_score"] * 0.75 + row["video_score"] * 0.25
    elif role == "Remote Presenter":
        return row["audio_score"] * 0.70 + row["screencast_score"] * 0.30
    return row["audio_score"] * 1.00


def _process_raw_gmeet_data(df: pd.DataFrame) -> pd.DataFrame:
    """Score raw GMeet data on-the-fly (same logic as the batch cloud job)."""
    if df.empty:
        return df

    if "Date_Time" in df.columns:
        df["Date_Time"] = pd.to_datetime(df["Date_Time"])

    safe_to_fill_zero = [
        "audio_send_packet_loss_max", "audio_recv_packet_loss_max",
        "video_send_packet_loss_max", "video_recv_packet_loss_max",
        "screencast_send_packet_loss_max",
        "network_recv_jitter_msec_max", "network_rtt_msec_mean",
    ]
    keep_nan = [
        "video_send_short_side_median_pixels", "video_send_fps_mean",
        "screencast_send_fps_mean",
    ]
    activity_cols = [
        "video_send_seconds", "video_recv_seconds",
        "screencast_send_seconds", "screencast_recv_seconds",
        "audio_send_seconds", "audio_recv_seconds",
        "duration_seconds",
    ]

    for col in safe_to_fill_zero + activity_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in keep_nan:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[df["duration_seconds"] >= 30].copy()
    if df.empty:
        return df

    df["audio_pkt_loss_score"] = df.apply(_audio_packet_loss_score, axis=1)
    df["audio_jitter_score"] = df.apply(_audio_jitter_score, axis=1)
    df["audio_latency_score"] = df.apply(_audio_latency_score, axis=1)
    df["video_pkt_loss_score"] = df.apply(_video_packet_loss_score, axis=1)
    df["video_resolution_score"] = df.apply(_video_resolution_score, axis=1)
    df["video_fps_score"] = df.apply(_video_fps_score, axis=1)
    df["screencast_loss_score"] = df.apply(_screencast_loss_score, axis=1)
    df["screencast_fps_score"] = df.apply(_screencast_fps_score, axis=1)

    df["audio_score"] = (
        df["audio_pkt_loss_score"] * 0.50
        + df["audio_latency_score"] * 0.35
        + df["audio_jitter_score"] * 0.15
    )
    df["video_score"] = df.apply(_compute_video_score, axis=1)
    df["screencast_score"] = df.apply(_compute_screencast_score, axis=1)
    df["user_role"] = df.apply(_determine_role, axis=1)
    df["gmeet_score"] = df.apply(_compute_final_score, axis=1).round(2)

    # Normalize column name to match scores table convention
    df = df.rename(columns={"Date_Time": "date_time"})

    return df


def _fetch_raw_data_for_eid(
    client: bigquery.Client,
    eid_email: str | None,
    from_date: str,
    to_date: str,
) -> pd.DataFrame | None:
    """Fetch raw meeting data from the source table for real-time scoring.

    Uses < next_day instead of <= to_date because Date_Time is a TIMESTAMP
    and '2026-07-07' would be interpreted as midnight, missing the whole day.
    """
    if not eid_email:
        return None

    to_date_parsed = datetime.datetime.strptime(to_date, "%Y-%m-%d").date()
    next_day = (to_date_parsed + datetime.timedelta(days=1)).isoformat()

    query = f"""
    SELECT *
    FROM `{BQ_RAW_TABLE}`
    WHERE (LOWER(User_Email) = LOWER(@eid_email)
           OR LOWER(identifier) = LOWER(@eid_email)
           OR LOWER(display_name) = LOWER(@eid_email))
      AND Date_Time >= @from_date
      AND Date_Time < @next_day
    ORDER BY Date_Time DESC
    LIMIT 500
    """
    params = [
        bigquery.ScalarQueryParameter("eid_email", "STRING", eid_email),
        bigquery.ScalarQueryParameter("from_date", "STRING", from_date),
        bigquery.ScalarQueryParameter("next_day", "STRING", next_day),
    ]
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        df = client.query(query, job_config=job_config).to_dataframe()
        return df if not df.empty else None
    except Exception as exc:
        logger.warning("Raw data fetch failed: %s", exc)
        return None


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return round(float(val), 2)


def _resolve_eid_to_email(client: bigquery.Client, eid: str) -> str | None:
    padded_eid = str(eid).strip().zfill(10)
    query = f"""
    SELECT work_email_addr
    FROM `{HR_TABLE}`
    WHERE b_eid = @eid
    LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("eid", "STRING", padded_eid)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
        if rows and rows[0].work_email_addr:
            return str(rows[0].work_email_addr).strip()
    except Exception as exc:
        logger.warning("EID resolution failed for %s: %s", eid, exc)
    return None


def _resolve_eid_to_name(client: bigquery.Client, eid: str) -> str | None:
    padded_eid = str(eid).strip().zfill(10)
    query = f"""
    SELECT preferred_first_name, last_name
    FROM `{HR_TABLE}`
    WHERE b_eid = @eid
    LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("eid", "STRING", padded_eid)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
        if rows:
            first = getattr(rows[0], "preferred_first_name", "") or ""
            last = getattr(rows[0], "last_name", "") or ""
            full = f"{first} {last}".strip()
            return full if full else None
    except Exception as exc:
        logger.warning("Name resolution failed for %s: %s", eid, exc)
    return None


def _fetch_scores_for_eid(
    client: bigquery.Client,
    eid: str,
    eid_email: str | None,
    from_date: str,
    to_date: str,
) -> pd.DataFrame | None:
    conditions = []
    params = [
        bigquery.ScalarQueryParameter("from_date", "STRING", from_date),
        bigquery.ScalarQueryParameter("to_date", "STRING", to_date),
    ]

    if eid_email:
        conditions.append(
            "(LOWER(user_email) = LOWER(@eid_email) OR LOWER(eid) = LOWER(@eid_padded))"
        )
        params.append(bigquery.ScalarQueryParameter("eid_email", "STRING", eid_email))
        params.append(bigquery.ScalarQueryParameter("eid_padded", "STRING", eid.strip().zfill(10)))
    else:
        conditions.append(
            "(LOWER(eid) = LOWER(@eid_padded) OR LOWER(user_email) LIKE CONCAT(LOWER(@eid_raw), '%'))"
        )
        params.append(bigquery.ScalarQueryParameter("eid_padded", "STRING", eid.strip().zfill(10)))
        params.append(bigquery.ScalarQueryParameter("eid_raw", "STRING", eid))

    where = " OR ".join(conditions)

    query = f"""
    SELECT *
    FROM `{BQ_SCORES_TABLE}`
    WHERE ({where})
      AND score_date >= @from_date
      AND score_date <= @to_date
    ORDER BY date_time DESC
    LIMIT 1000
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    df = client.query(query, job_config=job_config).to_dataframe()
    return df if not df.empty else None


def _fetch_last_n_meetings(
    client: bigquery.Client,
    eid: str,
    eid_email: str | None,
    n: int,
) -> pd.DataFrame | None:
    conditions = []
    params = [bigquery.ScalarQueryParameter("limit_n", "INT64", min(n, 500))]

    if eid_email:
        conditions.append(
            "(LOWER(user_email) = LOWER(@eid_email) OR LOWER(eid) = LOWER(@eid_padded))"
        )
        params.append(bigquery.ScalarQueryParameter("eid_email", "STRING", eid_email))
        params.append(bigquery.ScalarQueryParameter("eid_padded", "STRING", eid.strip().zfill(10)))
    else:
        conditions.append(
            "(LOWER(eid) = LOWER(@eid_padded) OR LOWER(user_email) LIKE CONCAT(LOWER(@eid_raw), '%'))"
        )
        params.append(bigquery.ScalarQueryParameter("eid_padded", "STRING", eid.strip().zfill(10)))
        params.append(bigquery.ScalarQueryParameter("eid_raw", "STRING", eid))

    where = " OR ".join(conditions)

    query = f"""
    SELECT *
    FROM `{BQ_SCORES_TABLE}`
    WHERE ({where})
    ORDER BY date_time DESC
    LIMIT @limit_n
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    df = client.query(query, job_config=job_config).to_dataframe()
    return df if not df.empty else None


def _fetch_scores_hybrid(
    client: bigquery.Client,
    eid: str,
    eid_email: str | None,
    from_date: str,
    to_date: str,
) -> pd.DataFrame | None:
    """Fetch scores from the pre-computed table, then fill in any unscored
    days (typically today) by scoring raw data on-the-fly."""
    df_scores = _fetch_scores_for_eid(client, eid, eid_email, from_date, to_date)

    today_str = datetime.date.today().isoformat()
    needs_raw = to_date >= today_str

    df_raw_scored = None
    if needs_raw and eid_email:
        raw_from = today_str
        if df_scores is not None and not df_scores.empty:
            dt_col = "date_time" if "date_time" in df_scores.columns else "Date_Time"
            latest_scored = pd.to_datetime(df_scores[dt_col]).max()
            raw_from = latest_scored.strftime("%Y-%m-%d")

        df_raw = _fetch_raw_data_for_eid(client, eid_email, raw_from, to_date)
        if df_raw is not None and not df_raw.empty:
            df_raw_scored = _process_raw_gmeet_data(df_raw)
            if df_raw_scored is not None and not df_raw_scored.empty:
                df_raw_scored["_source"] = "realtime"

    if df_scores is not None and not df_scores.empty:
        df_scores["_source"] = "scored"
        if "Date_Time" in df_scores.columns:
            df_scores = df_scores.rename(columns={"Date_Time": "date_time"})

    parts = []
    if df_scores is not None and not df_scores.empty:
        parts.append(df_scores)
    if df_raw_scored is not None and not df_raw_scored.empty:
        parts.append(df_raw_scored)

    if not parts:
        return None

    combined = pd.concat(parts, ignore_index=True)

    dt_col = "date_time"
    if dt_col in combined.columns:
        combined[dt_col] = pd.to_datetime(combined[dt_col])
        combined = combined.drop_duplicates(subset=[dt_col], keep="first")
        combined = combined.sort_values(dt_col, ascending=False)

    return combined


def _fetch_last_n_meetings_hybrid(
    client: bigquery.Client,
    eid: str,
    eid_email: str | None,
    n: int,
) -> pd.DataFrame | None:
    """Fetch last N meetings, combining scored data with real-time scored raw
    data for today's unscored meetings."""
    df_scores = _fetch_last_n_meetings(client, eid, eid_email, n)

    df_raw_scored = None
    if eid_email:
        today_str = datetime.date.today().isoformat()
        df_raw = _fetch_raw_data_for_eid(client, eid_email, today_str, today_str)
        if df_raw is not None and not df_raw.empty:
            df_raw_scored = _process_raw_gmeet_data(df_raw)

    parts = []
    if df_raw_scored is not None and not df_raw_scored.empty:
        df_raw_scored["_source"] = "realtime"
        parts.append(df_raw_scored)
    if df_scores is not None and not df_scores.empty:
        df_scores["_source"] = "scored"
        if "Date_Time" in df_scores.columns:
            df_scores = df_scores.rename(columns={"Date_Time": "date_time"})
        parts.append(df_scores)

    if not parts:
        return None

    combined = pd.concat(parts, ignore_index=True)
    dt_col = "date_time"
    if dt_col in combined.columns:
        combined[dt_col] = pd.to_datetime(combined[dt_col])
        combined = combined.drop_duplicates(subset=[dt_col], keep="first")
        combined = combined.sort_values(dt_col, ascending=False)

    return combined.head(n)


def _build_meetings_list(df: pd.DataFrame) -> list[dict[str, Any]]:
    dt_col = "date_time" if "date_time" in df.columns else "Date_Time"
    meetings = []
    for _, row in df.iterrows():
        meetings.append({
            "date_time": _to_jsonable(row.get(dt_col)),
            "conference_id": row.get("conference_id"),
            "duration_seconds": _safe_float(row.get("duration_seconds")),
            "user_role": row.get("user_role", "Unknown"),
            "gmeet_score": _safe_float(row.get("gmeet_score")),
            "audio_score": _safe_float(row.get("audio_score")),
            "video_score": _safe_float(row.get("video_score")),
            "screencast_score": _safe_float(row.get("screencast_score")),
            "audio_sub_scores": {
                "packet_loss": _safe_float(row.get("audio_pkt_loss_score")),
                "jitter": _safe_float(row.get("audio_jitter_score")),
                "latency": _safe_float(row.get("audio_latency_score")),
            },
            "video_sub_scores": {
                "resolution": _safe_float(row.get("video_resolution_score")),
                "packet_loss": _safe_float(row.get("video_pkt_loss_score")),
                "fps": _safe_float(row.get("video_fps_score")),
            },
            "screencast_sub_scores": {
                "packet_loss": _safe_float(row.get("screencast_loss_score")),
                "fps": _safe_float(row.get("screencast_fps_score")),
            },
            "raw_metrics": {
                "audio_send_pkt_loss_max": _safe_float(row.get("audio_send_packet_loss_max")),
                "audio_recv_pkt_loss_max": _safe_float(row.get("audio_recv_packet_loss_max")),
                "jitter_msec_max": _safe_float(row.get("network_recv_jitter_msec_max")),
                "rtt_msec_mean": _safe_float(row.get("network_rtt_msec_mean")),
                "video_send_pkt_loss_max": _safe_float(row.get("video_send_packet_loss_max")),
                "video_recv_pkt_loss_max": _safe_float(row.get("video_recv_packet_loss_max")),
                "video_resolution_px": _safe_float(row.get("video_send_short_side_median_pixels")),
                "video_fps": _safe_float(row.get("video_send_fps_mean")),
                "screencast_pkt_loss_max": _safe_float(row.get("screencast_send_packet_loss_max")),
                "screencast_fps": _safe_float(row.get("screencast_send_fps_mean")),
            },
        })
    return meetings


def _detect_issues(
    category_scores: dict[str, Any], df: pd.DataFrame
) -> list[dict[str, str]]:
    issues = []

    audio = category_scores.get("audio", {})
    if audio.get("avg") is not None and audio["avg"] < 7.5:
        issues.append({
            "area": "Audio",
            "severity": _get_score_label(audio["avg"]),
            "detail": f"Average audio score is {audio['avg']}/10.",
        })

    audio_subs = audio.get("sub_scores", {})
    if audio_subs.get("packet_loss") is not None and audio_subs["packet_loss"] < 8.0:
        avg_loss = _safe_float(df[["audio_send_packet_loss_max", "audio_recv_packet_loss_max"]].mean().mean()) or 0
        issues.append({
            "area": "Audio Packet Loss",
            "severity": _get_score_label(audio_subs["packet_loss"]),
            "detail": (
                f"Score {audio_subs['packet_loss']}/10. "
                f"Average packet loss ~{avg_loss:.1f}%. "
                "This causes audio dropouts and garbled speech."
            ),
            "recommendation": "Check network bandwidth, switch to wired connection, close bandwidth-heavy apps.",
        })
    if audio_subs.get("jitter") is not None and audio_subs["jitter"] < 8.0:
        avg_jitter = _safe_float(df["network_recv_jitter_msec_max"].mean()) or 0
        issues.append({
            "area": "Network Jitter",
            "severity": _get_score_label(audio_subs["jitter"]),
            "detail": (
                f"Score {audio_subs['jitter']}/10. "
                f"Average jitter ~{avg_jitter:.0f}ms. "
                "This causes choppy audio and sync issues."
            ),
            "recommendation": "Use QoS settings, avoid WiFi congestion, check for network instability.",
        })
    if audio_subs.get("latency") is not None and audio_subs["latency"] < 8.0:
        avg_rtt = _safe_float(df["network_rtt_msec_mean"].mean()) or 0
        issues.append({
            "area": "Network Latency",
            "severity": _get_score_label(audio_subs["latency"]),
            "detail": (
                f"Score {audio_subs['latency']}/10. "
                f"Average RTT ~{avg_rtt:.0f}ms. "
                "This causes noticeable delay in conversations."
            ),
            "recommendation": "Check VPN routing, try a closer server, reduce network hops.",
        })

    video = category_scores.get("video", {})
    if video.get("avg") is not None and video["avg"] < 7.5:
        issues.append({
            "area": "Video Quality",
            "severity": _get_score_label(video["avg"]),
            "detail": f"Average video score is {video['avg']}/10.",
            "recommendation": "Check camera settings, bandwidth, and ensure sufficient upload speed.",
        })

    screencast = category_scores.get("screencast", {})
    if screencast.get("avg") is not None and screencast["avg"] < 7.5:
        issues.append({
            "area": "Screencast Quality",
            "severity": _get_score_label(screencast["avg"]),
            "detail": f"Average screencast score is {screencast['avg']}/10.",
            "recommendation": "Reduce screen resolution while sharing, close unnecessary applications.",
        })

    if not issues:
        issues.append({
            "area": "None",
            "severity": "Excellent",
            "detail": "No significant quality issues detected. Meeting experience is within acceptable thresholds.",
        })

    return issues
