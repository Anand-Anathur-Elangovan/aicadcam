"""
common/memory — Pluggable long-term memory for ADK agents.

Three strategies, selected via ``memory_bank.mode`` in
``config/agent_runtime_config.json`` or the ``MEMORY_BANK_MODE`` env var:

  disabled       — No long-term memory.  Zero cloud dependencies.
                   Safe for local dev and unit tests.

  decoupled      — Standalone Vertex AI Memory Bank resource, managed
                   independently of Agent Runtime.  Persists across
                   redeployments; shareable across multiple agents.
                   Requires ``MEMORY_BANK_RESOURCE_NAME`` env var and a
                   pre-created resource (``deploy/create_memory_bank.py``).

  agent_runtime  — Built-in ADK ``VertexAiMemoryBankService``, managed by
                   Agent Runtime / AdkApp.  Zero ops — pass
                   ``svc.adk_memory_service`` to ``App()`` and ADK handles
                   everything.  No pre-created resource needed.

Typical usage
-------------
    from common.memory import create_memory_service

    # Reads mode from config/env automatically
    memory = create_memory_service()

    # Wire into ADK App (None-safe for non-agent_runtime modes)
    app = App(..., memory_service=memory.adk_memory_service)

    # In request handler (decoupled mode only — no-op in other modes)
    import asyncio
    asyncio.create_task(
        asyncio.to_thread(memory.ingest_turn_sync, user_id, session_id, text)
    )

    # Optional manual prompt augmentation (decoupled mode only)
    enriched = memory.build_context_prompt(user_query, user_id)

Public API
----------
"""
from __future__ import annotations

from common.memory.base import BaseMemoryService, MemoryMode
from common.memory.null import NullMemoryService
from common.memory.decoupled import DecoupledMemoryService
from common.memory.agent_runtime import AgentRuntimeMemoryService
from common.memory.factory import create_memory_service

__all__ = [
    # Core interface & constants
    "BaseMemoryService",
    "MemoryMode",
    # Concrete strategies (for type hints / isinstance checks / DI)
    "NullMemoryService",
    "DecoupledMemoryService",
    "AgentRuntimeMemoryService",
    # Primary entry point
    "create_memory_service",
]
