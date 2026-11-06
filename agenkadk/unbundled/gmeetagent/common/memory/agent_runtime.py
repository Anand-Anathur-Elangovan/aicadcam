"""
common/memory/agent_runtime.py � Built-in ADK memory strategy (MemoryMode.AGENT_RUNTIME).

Long-term memory backed by ADK's ``VertexAiMemoryBankService``, which uses
the agent's own Reasoning Engine resource as the memory store.

In BYOC deployments (custom container / FastAPI), the ADK runner is NOT active,
so memory ingestion and retrieval are handled manually � the same pattern as
DecoupledMemoryService, but using the ADK-native service object instead of the
raw Vertex AI client.

Required env vars:
  GCP_PROJECT_ID            � GCP project ID.
  GCP_LOCATION              � GCP region (default: us-central1).
  AGENT_ENGINE_RESOURCE_ID  � Numeric ID of the deployed agent's Reasoning Engine,
                               e.g. "8945776137166913536".
                               Available after first deployment.  Set this in .env
                               and redeploy to enable long-term memory.

How memory_service differs from App():
  google.adk.apps.App has extra="forbid" and declares NO memory_service field in
  the installed ADK version.  Do NOT pass memory_service to App().  In managed
  (non-BYOC) deployments the ADK runner wires memory separately; in BYOC we
  handle ingest/retrieve explicitly in main.py.

Ingestion  (called via asyncio.to_thread in main.py \u2014 safe, no event loop in thread):
  ingest_turn_sync \u2192 asyncio.run(add_memory(...))

Retrieval  (called via asyncio.to_thread from main.py \u2014 safe in thread):
  build_context_prompt \u2192 asyncio.run(search_memory(...))
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from common.memory.base import BaseMemoryService, MemoryMode

logger = logging.getLogger(__name__)

# Back-reference phrases that signal the user wants long-term context even
# when a short-term session already exists (same policy as decoupled mode).
_BACK_REF_PHRASES = (
    "as i said", "as mentioned", "earlier", "previous", "before",
    "last time", "you know my", "my preference", "my constraints",
    "based on what i told", "what did i say", "remind me",
)


class AgentRuntimeMemoryService(BaseMemoryService):
    """
    Long-term memory via ADK's ``VertexAiMemoryBankService``.

    The Reasoning Engine resource that runs the agent also serves as its Memory
    Bank.  ``AGENT_ENGINE_RESOURCE_ID`` must be set to the agent's own resource
    ID (available after first deployment).

    In BYOC mode, this class performs manual ingest/retrieve because the ADK
    runner is not active.  All async ADK methods are wrapped with
    ``asyncio.run()`` and must be called from a thread (via
    ``asyncio.to_thread``), never directly from the async event loop.

    When to use
    -----------
    - ``memory_bank.mode = "agent_runtime"`` in agent_runtime_config.json.
    - Zero separate infrastructure: the running agent IS the memory bank.
    - Acceptable that memory is tied to this agent's deployment lifecycle.

    Args:
        app_name  : ADK app name used as memory scope (default: "research_agent2").
        project   : GCP project ID (falls back to ``GCP_PROJECT_ID``).
        location  : GCP region (falls back to ``GCP_LOCATION``).
    """

    def __init__(
        self,
        app_name: str = "research_agent2",
        project: Optional[str] = None,
        location: Optional[str] = None,
    ) -> None:
        self.project = project or os.environ.get("GCP_PROJECT_ID")
        self.location = location or os.environ.get("GCP_LOCATION", "us-central1")
        self._app_name = app_name
        self._initialized = False
        self.adk_memory_service: Optional[Any] = None
        self._init_service()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_service(self) -> None:
        """
        Instantiate VertexAiMemoryBankService.

        Agent Runtime automatically injects ``GOOGLE_CLOUD_AGENT_ENGINE_ID``
        into the container environment — no manual configuration needed.
        See: https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/setup
        """
        # Agent Runtime injects this automatically — no deploy-time patching needed.
        agent_engine_id = (
            os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID", "")
            or os.environ.get("AGENT_ENGINE_RESOURCE_ID", "")  # local dev fallback
        ).strip()
        if not agent_engine_id:
            logger.warning(
                "[AgentRuntimeMemory] GOOGLE_CLOUD_AGENT_ENGINE_ID not set — "
                "this is injected automatically by Agent Runtime. "
                "For local dev, set AGENT_ENGINE_RESOURCE_ID manually. "
                "Long-term memory is disabled."
            )
            return

        try:
            from google.adk.memory import VertexAiMemoryBankService
            self.adk_memory_service = VertexAiMemoryBankService(
                project=self.project,
                location=self.location,
                agent_engine_id=agent_engine_id,
            )
            self._initialized = True
            logger.info(
                "[AgentRuntimeMemory] Ready | agent_engine_id=%s app=%s",
                agent_engine_id, self._app_name,
            )
        except ImportError:
            logger.error(
                "[AgentRuntimeMemory] google.adk.memory.VertexAiMemoryBankService "
                "not found. Long-term memory is disabled."
            )
        except Exception as exc:
            logger.error(
                "[AgentRuntimeMemory] Failed to initialise "
                "VertexAiMemoryBankService: %s. Long-term memory is disabled.", exc,
            )

    # ------------------------------------------------------------------
    # BaseMemoryService interface
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return MemoryMode.AGENT_RUNTIME

    @property
    def is_available(self) -> bool:
        return self._initialized and self.adk_memory_service is not None

    def ingest_turn_sync(
        self,
        user_id: str,
        session_id: str,
        text: str,
        event_id: Optional[str] = None,
    ) -> None:
        """
        Ingest a user turn into memory (blocking).

        MUST be called via ``asyncio.to_thread()`` \u2014 uses ``asyncio.run()``
        internally, which requires a thread without a running event loop.
        """
        if not self.is_available or not user_id or not text:
            return
        try:
            from google.adk.memory.memory_entry import MemoryEntry
            from google.genai import types as genai_types

            entry = MemoryEntry(
                author="user",
                content=genai_types.Content(
                    parts=[genai_types.Part(text=text)],
                    role="user",
                ),
            )
            asyncio.run(
                self.adk_memory_service.add_memory(
                    app_name=self._app_name,
                    user_id=user_id,
                    memories=[entry],
                )
            )
            logger.debug(
                "[AgentRuntimeMemory] Ingested | user=%s session=%s",
                user_id, session_id,
            )
        except Exception as exc:
            logger.warning("[AgentRuntimeMemory] ingest_turn_sync failed: %s", exc)

    def should_consult_memory(
        self,
        user_id: str,
        session_id: Optional[str],
        query_text: str,
    ) -> bool:
        """
        Session-first retrieval policy (same as DecoupledMemoryService).

        - No active session \u2192 always consult long-term memory.
        - Active session + back-reference phrase \u2192 consult long-term memory.
        - Active session + fresh query \u2192 rely on session context only.
        """
        if not self.is_available or not user_id:
            return False

        query = (query_text or "").strip().lower()
        if not query:
            return False

        if not session_id:
            return True

        return any(phrase in query for phrase in _BACK_REF_PHRASES)

    def retrieve_memories_sync(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        No-op in direct sync calls \u2014 use ``build_context_prompt`` instead.

        ``VertexAiMemoryBankService.search_memory`` is async.  Retrieval is done
        inside ``build_context_prompt`` via ``asyncio.run()``, which requires
        being called from a thread (via ``asyncio.to_thread``).
        """
        return []

    def build_context_prompt(
        self,
        user_query: str,
        user_id: str,
        include_memories: bool = True,
        top_k: int = 3,
    ) -> str:
        """
        Retrieve memories and prepend to user_query (blocking).

        MUST be called via ``asyncio.to_thread()`` \u2014 uses ``asyncio.run()``
        internally, which requires a thread without a running event loop.

        In main.py both ingest and retrieval are already wrapped in
        ``asyncio.to_thread``, so this is safe.
        """
        if not include_memories or not self.is_available:
            return user_query

        try:
            response = asyncio.run(
                self.adk_memory_service.search_memory(
                    app_name=self._app_name,
                    user_id=user_id,
                    query=user_query,
                )
            )
            memories = [
                m for m in (response.memories or [])[:top_k]
                if m.content and m.content.parts and m.content.parts[0].text
            ]
            if not memories:
                return user_query

            header = "## Your Previous Context\n" + "".join(
                f"- {m.content.parts[0].text}\n" for m in memories
            )
            logger.info(
                "[AgentRuntimeMemory] Injected %d memories for user=%s",
                len(memories), user_id,
            )
            return f"{header}\n## Current Question\n{user_query}"
        except Exception as exc:
            logger.debug("[AgentRuntimeMemory] build_context_prompt failed: %s", exc)
            return user_query
