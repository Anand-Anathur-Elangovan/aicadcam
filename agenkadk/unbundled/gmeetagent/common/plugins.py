"""
plugins.py — Shared ADK plugins for all agents.

Includes:
  CountInvocationPlugin  — tracks agent/model/tool call counts per container lifetime
  AgentLoggingPlugin     — structured lifecycle logging for any named agent
"""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

logger = logging.getLogger(__name__)


class CountInvocationPlugin(BasePlugin):
    """Tracks cumulative agent/model/tool invocation counts in-memory.

    Counts reset on container restart. For durability, persist
    metrics:* state keys to Cloud Firestore or BigQuery.
    """

    def __init__(self):
        super().__init__(name="count_invocation_plugin")
        self._agent_count: int = 0
        self._model_count: int = 0
        self._tool_count: int = 0

    async def before_agent_callback(
        self, *, agent, callback_context: CallbackContext, **kwargs
    ) -> Optional[types.Content]:
        self._agent_count += 1
        logger.info(
            "[CountPlugin] Agent invocation #%d | agent='%s' | invocation_id=%s",
            self._agent_count,
            agent.name,
            callback_context.invocation_id,
        )
        callback_context.state["metrics:agent_invocations"] = self._agent_count
        callback_context.state["metrics:model_invocations"] = self._model_count
        callback_context.state["metrics:tool_invocations"] = self._tool_count
        return None

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request, **kwargs
    ) -> Optional[object]:
        self._model_count += 1
        logger.info("[CountPlugin] Model call #%d", self._model_count)
        return None

    async def before_tool_callback(
        self, *, tool, tool_args: dict, tool_context=None, **kwargs
    ) -> Optional[dict]:
        self._tool_count += 1
        logger.info(
            "[CountPlugin] Tool call #%d | tool='%s' | args=%s",
            self._tool_count,
            tool.name,
            tool_args,
        )
        return None


class AgentLoggingPlugin(BasePlugin):
    """Structured lifecycle logging plugin for a named agent."""

    def __init__(self, agent_name: str):
        super().__init__(name=f"{agent_name}_logging_plugin")
        self._agent_name = agent_name

    async def before_agent_callback(
        self, *, agent, callback_context: CallbackContext, **kwargs
    ) -> Optional[types.Content]:
        logger.info(
            "[%s] Agent '%s' starting | invocation_id=%s",
            self._agent_name,
            agent.name,
            callback_context.invocation_id,
        )
        return None

    async def after_agent_callback(
        self, *, agent, callback_context: CallbackContext, **kwargs
    ) -> Optional[types.Content]:
        logger.info(
            "[%s] Agent '%s' completed | invocation_id=%s",
            self._agent_name,
            agent.name,
            callback_context.invocation_id,
        )
        return None

    async def before_tool_callback(
        self, *, tool, tool_args: dict, tool_context=None, **kwargs
    ) -> Optional[dict]:
        logger.info("[%s] Tool '%s' called | args=%s", self._agent_name, tool.name, tool_args)
        return None

    async def after_tool_callback(
        self, *, tool, tool_args: dict, tool_context=None, result: dict = None, **kwargs
    ) -> Optional[dict]:
        result_keys = list(result.keys()) if isinstance(result, dict) else result
        logger.info("[%s] Tool '%s' done | result_keys=%s", self._agent_name, tool.name, result_keys)
        return None

    async def on_event_callback(self, *, event, **kwargs):
        actions = getattr(event, "actions", None) or {}
        state_delta = (
            actions.get("state_delta") if isinstance(actions, dict)
            else getattr(actions, "state_delta", None)
        )
        if state_delta:
            logger.debug("[%s] State delta: %s", self._agent_name, state_delta)

        turn_complete = (
            actions.get("turn_complete") if isinstance(actions, dict)
            else getattr(actions, "turn_complete", False)
        )
        if turn_complete:
            logger.info(
                "[%s] Final response emitted | author=%s",
                self._agent_name,
                getattr(event, "author", "unknown"),
            )
        return None

    async def after_run_callback(
        self,
        callback_context: CallbackContext | None = None,
        **kwargs,
    ) -> None:
        callback_context = callback_context or kwargs.get("callback_context")
        if not callback_context:
            return
        metrics = {k: v for k, v in callback_context.state.items() if k.startswith("metrics:")}
        if metrics:
            logger.info("[%s] Session metrics: %s", self._agent_name, metrics)
