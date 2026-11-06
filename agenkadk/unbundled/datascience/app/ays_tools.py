"""AYS (At Your Service) ServiceNow ticket tools.

All AYS API calls are relayed through the Verizon email service (/ays-proxy endpoint)
which runs on internal Cloud Run with proxy egress to atyourservice.verizon.com.
This avoids the GCP Agent Runtime DNS limitation where proxy.ebiz.verizon.com
cannot be resolved from the managed VPC.
"""
import base64
import logging
import os
import re
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── AYS API config ─────────────────────────────────────────────────────────────
AYS_INCIDENT_URL   = "https://atyourservice.verizon.com/api/verw2/v1/standard/incidents"
AYS_BASIC_AUTH = os.environ.get(
    "CREATE_INCIDENT_BASIC_AUTH",
    "_svc-VOE:+Ia(f#{8.agn!HfB3*7T$,B>((+oSAr4PnOoQ#yN",  # POC fallback
)

# EUS Automation queue
AYS_EUS_ASSIGNMENT_GROUP    = "1c181d9697a72d90c1f83ac11153af2d"
# Citrix manual queue — using same EUS group for now, update with Citrix sys_id when available
AYS_CITRIX_ASSIGNMENT_GROUP = "1c181d9697a72d90c1f83ac11153af2d"
AYS_CMDB_CI = "69abcebddbbdb608ee90feb5ae9619c5"

# ── Relay config ───────────────────────────────────────────────────────────────
# The email service (on internal Cloud Run) exposes /ays-proxy which forwards
# AYS API calls using the Verizon corporate proxy — solving Agent Runtime DNS issue.
AYS_RELAY_URL = os.environ.get(
    "AYS_RELAY_URL",
    "https://email-service-vdsi.ebiz.verizon.com/ays-proxy",
)
# Optional bearer token for relay auth (set AYS_PROXY_TOKEN in both services)
AYS_RELAY_TOKEN = os.environ.get("AYS_PROXY_TOKEN", "")

# ── Corporate proxy (fallback path when relay is also unreachable) ─────────────
_PROXIES = {
    "http":  "http://proxy.ebiz.verizon.com:80/",
    "https": "http://proxy.ebiz.verizon.com:80/",
}

# ── Startup connectivity check ─────────────────────────────────────────────────
# Runs once at module load (Agent Runtime container start).
# Hits email-service-vdsi.ebiz.verizon.com/health via direct and proxy paths
# so you can immediately confirm reachability from Cloud Run logs.
def _startup_connectivity_check() -> None:
    _health_url = "https://email-service-vdsi.ebiz.verizon.com/health"
    _proxy_cfg = {
        "http":  os.environ.get("HTTP_PROXY",  "http://proxy.ebiz.verizon.com:80/"),
        "https": os.environ.get("HTTPS_PROXY", "http://proxy.ebiz.verizon.com:80/"),
    }
    logger.info("=" * 60)
    logger.info("STARTUP CONNECTIVITY CHECK: %s", _health_url)

    # Attempt 1: direct (no env proxy)
    try:
        _sess = requests.Session()
        _sess.trust_env = False
        _r = _sess.get(_health_url, timeout=8)
        logger.info("[DIRECT]  status=%d  body=%s", _r.status_code, _r.text[:200])
    except Exception as _e:
        logger.warning("[DIRECT]  FAILED — %s", str(_e)[:200])

    # Attempt 2: via corporate proxy
    try:
        _r2 = requests.get(_health_url, proxies=_proxy_cfg, timeout=8)
        logger.info("[PROXY]   status=%d  body=%s", _r2.status_code, _r2.text[:200])
    except Exception as _e2:
        logger.warning("[PROXY]   FAILED — %s", str(_e2)[:200])

    logger.info("=" * 60)


_startup_connectivity_check()


def _ays_request(method: str, url: str, **kwargs) -> requests.Response:
    """Forward an AYS API request through the email-service relay (/ays-proxy).

    Flow:
      1. POST relay URL with {method, url, headers, json, timeout} — relay uses
         internal Verizon proxy egress that Agent Runtime cannot access directly.
      2. If relay unreachable (e.g. running locally on Workbench), fall back to
         direct call with corporate proxy.
    """
    relay_headers_out = {"Content-Type": "application/json"}
    if AYS_RELAY_TOKEN:
        relay_headers_out["Authorization"] = f"Bearer {AYS_RELAY_TOKEN}"

    # Build relay payload — pass original AYS headers + body inside the relay envelope
    relay_payload: dict = {
        "method": method,
        "url": url,
        "timeout": kwargs.get("timeout", 30),
    }
    if "headers" in kwargs:
        relay_payload["headers"] = kwargs["headers"]
    if "json" in kwargs:
        relay_payload["json"] = kwargs["json"]
    elif "data" in kwargs:
        relay_payload["data"] = kwargs["data"]

    try:
        session = requests.Session()
        session.trust_env = False  # ignore HTTP_PROXY/HTTPS_PROXY env vars on Agent Runtime
        relay_resp = session.post(
            AYS_RELAY_URL,
            json=relay_payload,
            headers=relay_headers_out,
            timeout=kwargs.get("timeout", 30) + 5,  # slightly longer than AYS timeout
        )
        logger.info("AYS relay response: status=%d url=%s", relay_resp.status_code, url)
        return relay_resp
    except Exception as relay_err:
        relay_err_str = str(relay_err)
        logger.warning(
            "AYS relay unreachable (%s), falling back to direct call with proxy",
            relay_err_str[:120],
        )
        # Fallback: direct call via corporate proxy (works on Workbench / internal)
        return requests.request(method, url, proxies=_PROXIES, **kwargs)


def _ays_headers() -> dict:
    if not AYS_BASIC_AUTH:
        raise RuntimeError("CREATE_INCIDENT_BASIC_AUTH env var is not set.")
    encoded = base64.b64encode(AYS_BASIC_AUTH.encode()).decode()
    return {
        "Content-Type": "application/json",
        "Authorization": f"Basic {encoded}",
    }


def _clean_symptom(text: str) -> str:
    """AYS only accepts short alphanumeric symptom values."""
    clean = text.strip()
    if clean and len(clean) <= 80 and re.fullmatch(r"[A-Za-z0-9 _\-()/]+", clean):
        return clean.replace("_", " ")
    return "Other"


def create_ays_ticket(
    short_description: str,
    user_eid: str,
    is_citrix: bool = False,
    symptom: str = "Other",
) -> dict[str, Any]:
    """Create an AYS (ServiceNow) incident ticket.

    Routes to:
    - EUS Automation queue  (default, for general IT / KB-related issues)
    - Citrix manual queue   (when is_citrix=True)

    Requires CREATE_INCIDENT_BASIC_AUTH env var to be set.
    All calls go through the Verizon corporate proxy.

    Args:
        short_description: Brief description of the issue (max 160 chars).
        user_eid: 10-digit Verizon employee EID of the affected user.
        is_citrix: If True, routes to the Citrix manual support queue.
        symptom: Short symptom label for categorization (e.g. 'Login Issue').
                 Falls back to 'Other' if value is invalid.

    Returns:
        dict with incident_number, incident_url, status, error.
    """
    try:
        assignment_group = AYS_CITRIX_ASSIGNMENT_GROUP if is_citrix else AYS_EUS_ASSIGNMENT_GROUP
        queue_label = "Citrix manual queue" if is_citrix else "EUS automation queue"

        payload = {
            "short_description": short_description[:160],
            "symptom": _clean_symptom(symptom),
            "cmdb_ci": AYS_CMDB_CI,
            "assignment_group": assignment_group,
            "affected_user": user_eid,
            "caller_id": user_eid,
        }
        logger.info("Creating AYS ticket | queue=%s | eid=%s", queue_label, user_eid)

        resp = _ays_request(
            "POST", AYS_INCIDENT_URL,
            headers=_ays_headers(),
            json=payload,
            timeout=30,
        )

        # Retry once with symptom=Other if AYS rejects the symptom value
        if resp.status_code == 400:
            try:
                detail = str(resp.json().get("error", {}).get("detail", ""))
            except Exception:
                detail = resp.text[:300]
            if "Not a valid Symptom" in detail and payload["symptom"] != "Other":
                logger.warning("AYS rejected symptom '%s', retrying with 'Other'", payload["symptom"])
                payload["symptom"] = "Other"
                resp = _ays_request(
                    "POST", AYS_INCIDENT_URL,
                    headers=_ays_headers(),
                    json=payload,
                    timeout=30,
                )

        resp.raise_for_status()
        data = resp.json()
        logger.info("AYS raw response: %s", str(data)[:500])

        # AYS returns: {'status': 'success', 'incident': {'number': 'INC...', 'sys_id': '...', ...}}
        result = data.get("incident") or data.get("result", data)
        if isinstance(result, list):
            result = result[0]
        incident_number = result.get("number", "")
        sys_id = result.get("sys_id", "")
        incident_url = (
            f"https://atyourservice.verizon.com/ays?id=ticket&table=incident&sys_id={sys_id}"
            if sys_id else "https://atyourservice.verizon.com"
        )

        logger.info("AYS ticket created: %s → %s", incident_number, queue_label)
        return {
            "status": "created",
            "incident_number": incident_number,
            "incident_url": incident_url,
            "queue": queue_label,
            "error": None,
        }
    except Exception as e:
        logger.error("create_ays_ticket failed: %s", e)
        return {"status": "failed", "incident_number": None, "incident_url": None, "error": str(e)}


def get_ticket_status(incident_number: str) -> dict[str, Any]:
    """Get the current status of an AYS incident ticket.

    Args:
        incident_number: The INC number (e.g. 'INC0012345').

    Returns:
        dict with incident_number, state, short_description, assigned_to,
        updated_on, resolved_at, error.
    """
    try:
        url = f"https://atyourservice.verizon.com/api/now/table/incident?sysparm_query=number={incident_number}&sysparm_fields=number,state,short_description,assigned_to,sys_updated_on,resolved_at,close_notes&sysparm_limit=1"
        resp = _ays_request("GET", url, headers=_ays_headers(), timeout=20)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("result", [])
        if not records:
            return {"incident_number": incident_number, "state": "not_found", "error": "Ticket not found"}
        rec = records[0]

        # Map numeric state codes to human labels
        state_map = {
            "1": "New", "2": "In Progress", "3": "On Hold",
            "4": "Awaiting User Info", "6": "Resolved", "7": "Closed",
        }
        state_raw = str(rec.get("state", ""))
        state_label = state_map.get(state_raw, state_raw)
        is_closed = state_raw in ("6", "7")

        return {
            "incident_number": rec.get("number", incident_number),
            "state": state_label,
            "state_code": state_raw,
            "is_closed": is_closed,
            "short_description": rec.get("short_description", ""),
            "assigned_to": rec.get("assigned_to", {}).get("display_value", "Unassigned"),
            "updated_on": rec.get("sys_updated_on", ""),
            "resolved_at": rec.get("resolved_at", ""),
            "close_notes": rec.get("close_notes", ""),
            "error": None,
        }
    except Exception as e:
        logger.error("get_ticket_status failed: %s", e)
        return {"incident_number": incident_number, "state": "unknown", "error": str(e)}


def create_ticket_monitor(
    incident_number: str,
    user_email: str,
    check_interval_hours: int = 4,
) -> dict[str, Any]:
    """Schedule automatic ticket status monitoring with email notifications.

    Creates a Cloud Scheduler job that checks the ticket status every
    N hours and sends an email update to the user until the ticket is closed.

    Args:
        incident_number: The INC number to monitor (e.g. 'INC0012345').
        user_email: Email address to send status updates to.
        check_interval_hours: How often to check — 2, 4, or 24 hours (default 4).

    Returns:
        dict with status, job_name, schedule, next_check, error.
    """
    try:
        import json
        from google.cloud import scheduler_v1

        project = os.environ.get("BQ_PROJECT", "vz-it-np-voev-dev-voevdo-0")
        region  = "us-east4"
        parent  = f"projects/{project}/locations/{region}"

        # Build cron: every N hours
        interval = max(1, min(check_interval_hours, 24))
        cron = f"0 */{interval} * * *" if interval < 24 else "0 9 * * *"

        job_name = f"{parent}/jobs/ticket-monitor-{incident_number.lower()}"
        monitor_payload = {
            "incident_number": incident_number,
            "user_email": user_email,
            "action": "check_and_notify",
        }

        client = scheduler_v1.CloudSchedulerClient()
        job = scheduler_v1.Job(
            name=job_name,
            description=f"Monitor AYS ticket {incident_number} for {user_email}",
            schedule=cron,
            time_zone="America/New_York",
            pubsub_target=scheduler_v1.PubsubTarget(
                topic_name=f"projects/{project}/topics/ds-agent-scheduler",
                data=json.dumps(monitor_payload).encode(),
            ),
        )
        result = client.create_job(
            request=scheduler_v1.CreateJobRequest(parent=parent, job=job)
        )
        return {
            "status": "monitoring_started",
            "incident_number": incident_number,
            "job_name": result.name,
            "schedule": f"Every {interval} hour(s)",
            "cron": cron,
            "notify_email": user_email,
            "next_check": str(result.schedule_time),
            "error": None,
        }
    except Exception as e:
        logger.error("create_ticket_monitor failed: %s", e)
        return {"status": "failed", "incident_number": incident_number, "error": str(e)}


def get_my_tickets(user_eid: str, include_closed: bool = False) -> dict[str, Any]:
    """Fetch all open AYS tickets for a given Verizon employee EID.

    Returns a list of incidents raised by or affecting the user.
    By default only returns open tickets (excludes Resolved/Closed).

    Args:
        user_eid: 10-digit Verizon employee EID.
        include_closed: If True, also include Resolved and Closed tickets.

    Returns:
        dict with tickets (list), count, error.
        Each ticket has: incident_number, state, short_description,
        assigned_to, updated_on, incident_url.
    """
    try:
        state_filter = "" if include_closed else "^stateNOT IN6,7"
        url = (
            f"https://atyourservice.verizon.com/api/now/table/incident"
            f"?sysparm_query=caller_id={user_eid}{state_filter}"
            f"&sysparm_fields=number,state,short_description,assigned_to,sys_updated_on,resolved_at,sys_id"
            f"&sysparm_limit=20"
            f"&sysparm_order_by=sys_updated_on&sysparm_order_by_direction=desc"
        )
        resp = _ays_request("GET", url, headers=_ays_headers(), timeout=20)
        resp.raise_for_status()
        records = resp.json().get("result", [])

        state_map = {
            "1": "New", "2": "In Progress", "3": "On Hold",
            "4": "Awaiting User Info", "6": "Resolved", "7": "Closed",
        }
        tickets = []
        for rec in records:
            state_raw = str(rec.get("state", ""))
            sys_id = rec.get("sys_id", "")
            tickets.append({
                "incident_number": rec.get("number", ""),
                "state": state_map.get(state_raw, state_raw),
                "short_description": rec.get("short_description", ""),
                "assigned_to": rec.get("assigned_to", {}).get("display_value", "Unassigned"),
                "updated_on": rec.get("sys_updated_on", ""),
                "incident_url": (
                    f"https://atyourservice.verizon.com/ays?id=ticket&table=incident&sys_id={sys_id}"
                    if sys_id else "https://atyourservice.verizon.com"
                ),
            })

        logger.info("get_my_tickets: found %d tickets for EID %s", len(tickets), user_eid)
        return {"tickets": tickets, "count": len(tickets), "error": None}
    except Exception as e:
        logger.error("get_my_tickets failed: %s", e)
        return {"tickets": [], "count": 0, "error": str(e)}


def check_ticket_escalations(user_eid: str, user_email: str, stale_hours: int = 48) -> dict[str, Any]:
    """Check if any open tickets for this user have been stale (no update) for too long.

    A ticket is considered stale if it has been 'In Progress' with no update
    for more than `stale_hours` hours. Sends a proactive email alert for each
    stale ticket via the email service.

    Args:
        user_eid: 10-digit Verizon employee EID.
        user_email: Email address to send escalation alerts to.
        stale_hours: Hours without update before flagging as stale (default 48).

    Returns:
        dict with stale_tickets (list), alerts_sent (int), error.
    """
    try:
        from datetime import datetime, timezone
        import json as _json
        import requests as _req

        EMAIL_SERVICE_URL = os.environ.get(
            "EMAIL_SERVICE_URL", "https://email-service-vdsi.ebiz.verizon.com"
        )
        PROXIES_EMAIL = {
            "http":  "http://proxy.ebiz.verizon.com:80/",
            "https": "http://proxy.ebiz.verizon.com:80/",
        }

        # Fetch open In Progress tickets for user
        url = (
            f"https://atyourservice.verizon.com/api/now/table/incident"
            f"?sysparm_query=caller_id={user_eid}^state=2"
            f"&sysparm_fields=number,state,short_description,assigned_to,sys_updated_on,sys_id"
            f"&sysparm_limit=20"
        )
        resp = _ays_request("GET", url, headers=_ays_headers(), timeout=20)
        resp.raise_for_status()
        records = resp.json().get("result", [])

        now_utc = datetime.now(timezone.utc)
        stale_tickets = []

        for rec in records:
            updated_str = rec.get("sys_updated_on", "")
            try:
                # AYS timestamps: "2026-06-20 14:32:00"
                updated_dt = datetime.strptime(updated_str, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                hours_stale = (now_utc - updated_dt).total_seconds() / 3600
            except ValueError:
                hours_stale = 0

            if hours_stale >= stale_hours:
                sys_id = rec.get("sys_id", "")
                stale_tickets.append({
                    "incident_number": rec.get("number", ""),
                    "short_description": rec.get("short_description", ""),
                    "assigned_to": rec.get("assigned_to", {}).get("display_value", "Unassigned"),
                    "hours_stale": round(hours_stale, 1),
                    "last_updated": updated_str,
                    "incident_url": (
                        f"https://atyourservice.verizon.com/ays?id=ticket&table=incident&sys_id={sys_id}"
                        if sys_id else "https://atyourservice.verizon.com"
                    ),
                })

        alerts_sent = 0
        for ticket in stale_tickets:
            # Build alert email
            subject = f"Action Needed: Ticket {ticket['incident_number']} has had no update for {ticket['hours_stale']:.0f} hours"
            body = (
                f"<p>Hi,</p>"
                f"<p>Your AYS ticket <strong>{ticket['incident_number']}</strong> has been "
                f"<strong>In Progress</strong> with no update for <strong>{ticket['hours_stale']:.0f} hours</strong>.</p>"
                f"<table style='border-collapse:collapse;'>"
                f"<tr><td style='padding:4px 12px;'><strong>Issue:</strong></td><td>{ticket['short_description']}</td></tr>"
                f"<tr><td style='padding:4px 12px;'><strong>Assigned to:</strong></td><td>{ticket['assigned_to']}</td></tr>"
                f"<tr><td style='padding:4px 12px;'><strong>Last updated:</strong></td><td>{ticket['last_updated']}</td></tr>"
                f"</table>"
                f"<p><a href='{ticket['incident_url']}'>View ticket →</a></p>"
                f"<p>Would you like the Data Science Assistant to follow up on your behalf?</p>"
            )
            try:
                _req.post(
                    f"{EMAIL_SERVICE_URL}/send-email-dont-reply",
                    json={"to": user_email, "subject": subject, "body": body, "is_html": True},
                    timeout=10,
                    proxies=PROXIES_EMAIL,
                )
                alerts_sent += 1
            except Exception as mail_err:
                logger.warning("Escalation email failed for %s: %s", ticket["incident_number"], mail_err)

        logger.info(
            "check_ticket_escalations: %d stale tickets found, %d alerts sent for EID %s",
            len(stale_tickets), alerts_sent, user_eid,
        )
        return {
            "stale_tickets": stale_tickets,
            "stale_count": len(stale_tickets),
            "alerts_sent": alerts_sent,
            "stale_threshold_hours": stale_hours,
            "error": None,
        }
    except Exception as e:
        logger.error("check_ticket_escalations failed: %s", e)
        return {"stale_tickets": [], "stale_count": 0, "alerts_sent": 0, "error": str(e)}

