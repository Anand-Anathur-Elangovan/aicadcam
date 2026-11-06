"""Cloud Scheduler-based report scheduling tools."""
import json
import logging
import os
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("BQ_PROJECT", "vz-it-np-voev-dev-voevdo-0")
REGION     = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-east4")

try:
    from google.cloud import scheduler_v1
    _HAS_SCHEDULER = True
except ImportError:
    _HAS_SCHEDULER = False
    logger.warning("google-cloud-scheduler not installed")

try:
    from google.cloud import run_v2
    _HAS_RUN = True
except ImportError:
    _HAS_RUN = False
    logger.warning("google-cloud-run not installed")


def _scheduler_client():
    if not _HAS_SCHEDULER:
        raise RuntimeError("google-cloud-scheduler is not installed")
    return scheduler_v1.CloudSchedulerClient()


def _parent() -> str:
    return f"projects/{PROJECT_ID}/locations/{REGION}"


def create_report_schedule(
    job_name: str,
    schedule_cron: str,
    report_config: dict,
    timezone: str = "America/New_York",
    description: str = "",
) -> dict[str, Any]:
    """Create a Cloud Scheduler job to run a report automatically.

    Args:
        job_name: Unique short name for this schedule (e.g. 'weekly-slack-report').
                  Only letters, numbers, hyphens.
        schedule_cron: Cron expression for when to run.
            Examples:
            - "0 9 * * 1"     → Every Monday at 9 AM
            - "0 8 * * 1-5"   → Weekdays at 8 AM
            - "0 7 * * *"     → Every day at 7 AM
            - "0 9 1 * *"     → 1st of every month at 9 AM
        report_config: Dict describing the report to generate:
            {
              "type": "data_query",       # type of report
              "query": "...",             # the question/query
              "recipients": ["a@b.com"],  # email recipients
              "slack_channel": "C0AE1G97QCV",  # optional Slack channel
              "days_back": 7             # how many days of data
            }
        timezone: IANA timezone string (default: America/New_York).
        description: Human-readable description of this schedule.

    Returns:
        dict with job_name, schedule, next_run, status, error.
    """
    try:
        client = _scheduler_client()
        full_job_name = f"{_parent()}/jobs/ds-agent-{job_name}"

        job = scheduler_v1.Job(
            name=full_job_name,
            description=description or f"Data Science Agent: {job_name}",
            schedule=schedule_cron,
            time_zone=timezone,
            pubsub_target=scheduler_v1.PubsubTarget(
                topic_name=f"projects/{PROJECT_ID}/topics/ds-agent-scheduler",
                data=json.dumps(report_config).encode(),
            ),
        )
        result = client.create_job(
            request=scheduler_v1.CreateJobRequest(parent=_parent(), job=job)
        )
        return {
            "status": "created",
            "job_name": result.name,
            "schedule": schedule_cron,
            "timezone": timezone,
            "next_run": str(result.schedule_time),
            "error": None,
        }
    except Exception as e:
        logger.error("create_report_schedule failed: %s", e)
        return {"status": "failed", "job_name": job_name, "error": str(e)}


def list_report_schedules() -> dict[str, Any]:
    """List all active report schedules created by the Data Science Agent.

    Returns:
        dict with jobs (list), count (int), error.
    """
    try:
        client = _scheduler_client()
        jobs = []
        for job in client.list_jobs(request=scheduler_v1.ListJobsRequest(parent=_parent())):
            if "ds-agent-" in job.name:
                jobs.append({
                    "name": job.name.split("/")[-1],
                    "full_name": job.name,
                    "schedule": job.schedule,
                    "timezone": job.time_zone,
                    "state": job.state.name,
                    "next_run": str(job.schedule_time),
                    "description": job.description,
                })
        return {"jobs": jobs, "count": len(jobs), "error": None}
    except Exception as e:
        logger.error("list_report_schedules failed: %s", e)
        return {"jobs": [], "count": 0, "error": str(e)}


def cancel_report_schedule(job_name: str) -> dict[str, Any]:
    """Cancel (delete) a report schedule by its short job name.

    Args:
        job_name: The short job name as returned by list_report_schedules
                  (e.g. 'ds-agent-weekly-slack-report').

    Returns:
        dict with status, job_name, error.
    """
    try:
        client = _scheduler_client()
        if not job_name.startswith("projects/"):
            full_name = f"{_parent()}/jobs/{job_name}"
        else:
            full_name = job_name
        client.delete_job(request=scheduler_v1.DeleteJobRequest(name=full_name))
        return {"status": "cancelled", "job_name": job_name, "error": None}
    except Exception as e:
        logger.error("cancel_report_schedule failed: %s", e)
        return {"status": "failed", "job_name": job_name, "error": str(e)}


def run_schedule_now(job_name: str) -> dict[str, Any]:
    """Trigger a scheduled report to run immediately (one-off execution).

    Args:
        job_name: Short job name (e.g. 'ds-agent-weekly-slack-report')
                  or full resource name.

    Returns:
        dict with status, job_name, error.
    """
    try:
        client = _scheduler_client()
        if not job_name.startswith("projects/"):
            full_name = f"{_parent()}/jobs/{job_name}"
        else:
            full_name = job_name
        client.run_job(request=scheduler_v1.RunJobRequest(name=full_name))
        return {"status": "triggered", "job_name": job_name, "ran_at": datetime.now().isoformat(), "error": None}
    except Exception as e:
        logger.error("run_schedule_now failed: %s", e)
        return {"status": "failed", "job_name": job_name, "error": str(e)}
