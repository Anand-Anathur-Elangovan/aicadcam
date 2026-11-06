"""Root-level AgentTool wrappers.

The root agent calls these functions, which delegate to the appropriate sub-agent.
"""
import logging
from google.adk.tools import ToolContext
from google.adk.tools.agent_tool import AgentTool

from app.sub_agents.bigquery.agent import bigquery_agent
from app.sub_agents.analytics.agent import analytics_agent
from app.sub_agents.kb.agent import kb_agent

logger = logging.getLogger(__name__)

# Instantiate AgentTools once at module level — avoids cold-start delay on first call
_bigquery_tool = AgentTool(agent=bigquery_agent)
_analytics_tool = AgentTool(agent=analytics_agent)
_kb_tool = AgentTool(agent=kb_agent)


async def call_bigquery_agent(question: str, tool_context: ToolContext) -> str:
    """Query BigQuery using natural language (NL2SQL).

    Use this for any question that requires fetching data from BigQuery —
    Slack messages, HR employee data, or any other BQ table.

    Args:
        question: Natural language question about the data, including any
                  date ranges, filters, or groupings needed.

    Returns:
        Query results as a structured text summary with key numbers.
    """
    logger.info("call_bigquery_agent: %s", question)
    result = await _bigquery_tool.run_async(
        args={"request": question}, tool_context=tool_context
    )
    tool_context.state["bq_last_result"] = result
    return result


async def call_analytics_agent(question: str, tool_context: ToolContext) -> str:
    """Perform data analysis, visualization, and statistical computation.

    Use this AFTER calling call_bigquery_agent to get the data.
    Handles charts, trends, anomaly detection, correlation, statistics.

    Args:
        question: Description of the analysis needed. Include the data context
                  from the previous BigQuery result.

    Returns:
        Analysis results, statistics, and/or base64-encoded chart PNG.
    """
    logger.info("call_analytics_agent: %s", question)
    result = await _analytics_tool.run_async(
        args={"request": question}, tool_context=tool_context
    )
    tool_context.state["analytics_last_result"] = result
    return result


async def call_kb_agent(question: str, tool_context: ToolContext) -> str:
    """Search KB articles and OneConfluence pages to answer a support question.

    Use this when the user asks a how-to, troubleshooting, or policy question.
    Searches both ServiceNow KB articles and Confluence pages in BigQuery.
    Always returns the source link so the user can read the full article.

    Args:
        question: The user's support question or keywords to search for.

    Returns:
        Answer synthesized from KB/Confluence content, with source links.
        Ends with an offer to raise an AYS ticket if the answer is unhelpful.
    """
    logger.info("call_kb_agent: %s", question)
    result = await _kb_tool.run_async(
        args={"request": question}, tool_context=tool_context
    )
    tool_context.state["kb_last_result"] = result
    return result
