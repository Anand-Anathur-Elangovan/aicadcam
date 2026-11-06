"""Tool functions for the EQES orchestrator agent.

Each remote agent gets one tool function. To add a new agent:
  1. Add its AgentEntry in registry.py
  2. Add a tool function here following the pattern below
  3. Import and register it in agent.py
"""

import logging

from google.adk.tools import ToolContext

from app.registry import AGENT_REGISTRY
from app.remote_client import remote_client

logger = logging.getLogger(__name__)


def _query_remote_agent(
    agent_key: str, question: str, tool_context: ToolContext
) -> dict:
    """Shared helper that routes a question to a registered remote agent."""
    entry = AGENT_REGISTRY.get(agent_key)
    if not entry:
        return {"status": "error", "message": f"Unknown agent: {agent_key}"}

    user_id = tool_context.state.get("user_id", "anonymous")
    eid = tool_context.state.get("eid")

    message = question
    if eid:
        message = f"[User EID: {eid}] {question}"

    try:
        response = remote_client.query(
            resource_name=entry.resource_name,
            user_id=user_id,
            message=message,
        )

        # Handle empty or None responses
        if not response or not response.strip():
            logger.warning("Empty response from remote agent %s", agent_key)
            return {
                "status": "error",
                "agent": entry.name,
                "message": f"{entry.name} returned an empty response. The agent may have encountered an internal error. Please try again.",
            }

        return {"status": "success", "agent": entry.name, "response": response}
    except Exception as exc:
        logger.exception("Error querying remote agent %s", agent_key)
        return {
            "status": "error",
            "agent": entry.name,
            "message": f"Failed to reach {entry.name}: {exc}",
        }


# ── Tool: register user identity ────────────────────────────────────────────────


def register_user(
    eid: str,
    name: str,
    email: str,
    tool_context: ToolContext,
) -> dict:
    """Register the user's identity for the session.

    Args:
        eid: Employee ID (e.g. VZ12345).
        name: Employee full name.
        email: Employee email address.
        tool_context: ADK tool context (injected automatically).

    Returns:
        Confirmation of registered identity.
    """
    tool_context.state["eid"] = eid.strip()
    tool_context.state["user_name"] = name.strip()
    tool_context.state["user_email"] = email.strip()
    tool_context.state["user_id"] = eid.strip()
    return {
        "status": "success",
        "message": f"Registered user {name} (EID: {eid}, email: {email}). "
        "All subsequent queries will use this identity.",
    }


# ── Tool: GMeet Agent ───────────────────────────────────────────────────────────


def query_gmeet_agent(question: str, tool_context: ToolContext) -> dict:
    """Query the GMeet Quality Score Agent for meeting quality information.

    Use this tool when the user asks about Google Meet quality scores,
    meeting quality analysis, audio/video/screencast metrics, score trends,
    charts, data freshness, or anything related to GMeet quality.

    Args:
        question: The user's question about GMeet quality scores.
        tool_context: ADK tool context (injected automatically).

    Returns:
        Response from the GMeet agent with score data, analysis, or charts.
    """
    return _query_remote_agent("gmeetagent", question, tool_context)


# ── Tool: Endpoint Score Agent ─────────────────────────────────────────────────


def query_endpoint_agent(question: str, tool_context: ToolContext) -> dict:
    """Query the Endpoint Score Agent for endpoint health and performance information.

    Use this tool when the user asks about endpoint scores, endpoint health,
    device performance, machine/session/application score breakdowns,
    team endpoint performance, fleet-wide issues, or remediation steps.

    Args:
        question: The user's question about endpoint scores or device health.
        tool_context: ADK tool context (injected automatically).

    Returns:
        Response from the Endpoint Score agent with score data, analysis, or reports.
    """
    return _query_remote_agent("endpointagent", question, tool_context)


def friction_score_agent(question: str, tool_context: ToolContext) -> dict:
    """Query the Friction Score Agent for service desk friction scores and employee experience data.

    Use this tool when the user asks about friction scores across incident, interaction, 
    hardware, and software domains. This includes queries about resolution/handle times, 
    bounce rates, request fulfillment SLAs, team/manager hierarchy scores, org-wide 
    comparisons, and day-1 scoring status for Verizon employees.

    Args:
        question: The user's question about friction scores, incidents, interactions, or hardware/software requests.
        tool_context: ADK tool context (injected automatically).

    Returns:
        Response from the Friction Score agent with score data, summaries, or NL2SQL analysis.
    """
    # Assuming you are delegating to a base agent router here:
    return _query_remote_agent("frictionscoreagent", question, tool_context)

def vpn_agent(question: str, tool_context: ToolContext) -> dict:
    """Query the VPN eQES Diagnostic Agent for VPN connectivity telemetry and health scores.

    Use this tool when the user asks about VPN performance, daily VPN health scores by EID 
    or Machine Name, or hour-by-hour diagnostic journeys (e.g., latency spikes, auth blocks). 
    This includes queries about distinguishing between corporate network issues, local ISP 
    bottlenecks, and SAML/Duo lockouts, as well as calculating 'Time-in-Pain' metrics or 
    getting troubleshooting recommendations for Gateway Churn and ISP penalties.

    Args:
        question: The user's question about VPN health, connectivity issues, or telemetry.
        tool_context: ADK tool context (injected automatically).

    Returns:
        Response from the VPN agent with diagnostic data, telemetry maps, and troubleshooting recommendations.
    """
    return _query_remote_agent("vpnagent", question, tool_context)