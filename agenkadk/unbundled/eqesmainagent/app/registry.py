"""Agent registry — single source of truth for all remote agents.

To add a new agent:
  1. Add an AgentEntry below.
  2. Add a tool function in tools.py.
  3. Import the tool in agent.py and add it to the tools list.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentEntry:
    name: str
    resource_name: str
    description: str
    capabilities: list[str] = field(default_factory=list)


AGENT_REGISTRY: dict[str, AgentEntry] = {
    "gmeetagent": AgentEntry(
        name="GMeet Quality Score Agent",
        resource_name="projects/1041813482705/locations/us-east4/reasoningEngines/3662209349331189760",
        description="Analyzes Google Meet meeting quality scores for Verizon employees and conference rooms.",
        capabilities=[
            "Look up GMeet quality scores by EID, employee name, or date range",
            "Disambiguate when multiple employees share a name (returns matches for user confirmation)",
            "Show score breakdowns (audio, video, screencast)",
            "Generate score trend charts and visualizations",
            "Check data freshness and trigger score updates",
            "Compare individual scores against Verizon overall averages",
            "Look up team/hierarchy GMeet scores by supervisor, manager, director, AD, or department",
            "Show team score distributions, employee counts, and identify top/bottom performers",
            "Look up conference room GMeet scores by room name, location, or city",
            "Compare meeting quality between rooms or across locations and countries",
            "Provide room-specific AV and network infrastructure recommendations",
        ],
    ),
    "endpointagent": AgentEntry(
        name="Endpoint Score Agent",
        resource_name="projects/vz-it-np-voev-dev-voevdo-0/locations/us-east4/reasoningEngines/3387032375224434688",
        description="Endpoint health scoring sub-agent of the eQes/DEX system. Analyzes endpoint performance scores (Machine, Session, Application) for Verizon employees' devices.",
        capabilities=[
            "Look up endpoint scores by EID, hostname, or employee name with date range support",
            "Show score breakdowns (machine, session, application) with root cause analysis",
            "Analyze team endpoint performance for managers and compare against department averages",
            "Generate endpoint score trend reports with visualizations and email delivery",
            "Find fleet-wide issues by score bands and detect problematic devices",
            "Provide actionable remediation steps for low-performing endpoints",
        ],
    ),
    "frictionscoreagent": AgentEntry(
        name="Friction Score Agent",
        resource_name="projects/1041813482705/locations/us-east4/reasoningEngines/7496742957060456448",
        description="Analyzes service desk friction scores across incident, interaction, hardware, and software domains for Verizon employees.",
        capabilities=[
            "Look up employee details by name or EID from HR data",
            "Query incident scores (resolution time, bounce/reassignment, escalation)",
            "Query interaction scores (call handle time, transfers, call volume)",
            "Query hardware request scores (endpoint, mobile, loaner, accessories fulfillment)",
            "Query software request scores (fulfillment time, SLA compliance)",
            "Retrieve team hierarchy scores by manager, department, or location",
            "Compare individual friction scores against org-wide averages",
            "Verify day-1 scoring status across all friction domains",
            "Generate friction score summaries combining all four sub-domains",
            "Answer natural language questions about service desk data via NL2SQL",
        ],
    ),
    "vpnagent": AgentEntry( 
        name="VPN eQES Diagnostic Agent", 
        resource_name="projects/1041813482705/locations/us-east4/reasoningEngines/6661993729252917248", 
        description="Diagnoses VPN connectivity issues by analyzing device-level telemetry and calculating eQES health scores.",
        capabilities=[
            "Look up daily VPN health scores by EID or Machine Name",
            "Automatically map Employee IDs (EID) to corresponding Machine Names",
            "Map hour-by-hour diagnostic journeys to pinpoint latency spikes and authentication blocks",
            "Distinguish between corporate network issues, local ISP bottlenecks, and client-side SAML/Duo lockouts",
            "Calculate 'Time-in-Pain' metrics including Hours in Frustration and Total Auth Blocks",
            "Compare individual machine performance against company-wide enterprise benchmarks",
            "Provide actionable troubleshooting recommendations targeted at specific root causes (e.g., ISP penalty, Gateway Churn)",
        ],
    ),    
}


def build_capabilities_summary() -> str:
    """Generate a markdown summary of all registered agents for the orchestrator instruction."""
    lines = []
    for key, entry in AGENT_REGISTRY.items():
        lines.append(f"### {entry.name} (`{key}`)")
        lines.append(f"{entry.description}")
        if entry.capabilities:
            for cap in entry.capabilities:
                lines.append(f"- {cap}")
        lines.append("")
    return "\n".join(lines)
