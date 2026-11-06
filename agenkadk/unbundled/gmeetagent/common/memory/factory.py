"""
common/memory/factory.py — Factory for creating memory services.

Single entry point for all agent code.  Reads mode from (highest priority first):
  1. Explicit ``mode`` argument passed to ``create_memory_service()``.
  2. ``MEMORY_BANK_MODE`` environment variable.
  3. ``memory_bank.mode`` in agent_runtime_config.json (via ``common.config``).
  4. Hard default: ``"disabled"``.

Usage
-----
Minimal (reads everything from config / env):

    from common.memory import create_memory_service

    memory = create_memory_service()
    app = App(..., memory_service=memory.adk_memory_service)

With explicit overrides (useful in tests or scripts):

    from common.memory import create_memory_service, MemoryMode

    memory = create_memory_service(mode=MemoryMode.DECOUPLED, project="my-proj")
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from common.memory.base import BaseMemoryService, MemoryMode

logger = logging.getLogger(__name__)


def create_memory_service(
    mode: Optional[str] = None,
    project: Optional[str] = None,
    location: Optional[str] = None,
    idle_trigger_seconds: Optional[int] = None,
) -> BaseMemoryService:
    """
    Create and return the appropriate memory service for the resolved *mode*.

    Mode resolution order (first non-empty value wins):

    1. ``mode`` argument.
    2. ``MEMORY_BANK_MODE`` env var.
    3. ``memory_bank.mode`` in ``agent_runtime_config.json``.
    4. Default: ``"disabled"``.

    Args:
        mode                 : Explicit mode override (``MemoryMode.*`` constant).
        project              : GCP project ID. Falls back to ``GCP_PROJECT_ID``.
        location             : GCP region.    Falls back to ``GCP_LOCATION``.
        idle_trigger_seconds : Idle-duration consolidation trigger for decoupled
                               mode.  Falls back to config / env / 300 s.

    Returns:
        A :class:`~common.memory.base.BaseMemoryService` instance.
        Always returns a valid object — never raises.

    Examples:
        >>> svc = create_memory_service()
        >>> app = App(..., memory_service=svc.adk_memory_service)
    """
    # ---- resolve mode -------------------------------------------------------
    resolved_mode = (mode or "").strip().lower()

    if not resolved_mode:
        resolved_mode = os.environ.get("MEMORY_BANK_MODE", "").strip().lower()

    cfg_idle: int = 300
    if not resolved_mode:
        try:
            from common.config import cfg
            resolved_mode = cfg.memory_bank.mode.strip().lower()
            cfg_idle = cfg.memory_bank.idle_trigger_seconds
        except Exception as exc:
            logger.debug("[MemoryFactory] Could not read common.config: %s", exc)

    resolved_mode = resolved_mode or MemoryMode.DISABLED

    # ---- resolve idle trigger (decoupled only) -------------------------------
    effective_idle = idle_trigger_seconds or cfg_idle

    logger.info("[MemoryFactory] Initialising memory service | mode=%s", resolved_mode)

    # ---- dispatch -----------------------------------------------------------
    if resolved_mode == MemoryMode.DECOUPLED:
        from common.memory.decoupled import DecoupledMemoryService
        return DecoupledMemoryService(
            project=project,
            location=location,
            idle_trigger_seconds=effective_idle,
        )

    if resolved_mode == MemoryMode.AGENT_RUNTIME:
        from common.memory.agent_runtime import AgentRuntimeMemoryService
        return AgentRuntimeMemoryService(project=project, location=location)

    if resolved_mode != MemoryMode.DISABLED:
        logger.warning(
            "[MemoryFactory] Unknown mode '%s' — falling back to 'disabled'.",
            resolved_mode,
        )

    from common.memory.null import NullMemoryService
    return NullMemoryService()
