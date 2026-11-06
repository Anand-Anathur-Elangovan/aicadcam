"""
common/memory/decoupled.py — Standalone Memory Bank strategy (MemoryMode.DECOUPLED).

Long-term memory backed by a pre-created Vertex AI Memory Bank resource that
lives *outside* Agent Runtime.  The resource is created once via
``deploy/create_memory_bank.py`` and its name is supplied via the
``MEMORY_BANK_RESOURCE_NAME`` environment variable.

MEMORY CONSOLIDATION STRATEGY — platform-managed, not custom code:
  idle_duration trigger: the Memory Bank server monitors each stream and
  automatically fires memory generation when no new events arrive for
  ``idle_trigger_seconds`` (default 300 s = 5 min).  This covers:
    - Session end        : stream goes idle → auto-consolidate after idle window
    - Orphaned sessions  : same idle-timer mechanism, no client state needed
    - Abandoned streams  : 24-hour platform failsafe (built into Memory Bank)

WORKFLOW (per request):
  1. Pre-query  : retrieve relevant memories → inject as prompt context.
  2. Per-turn   : ingest user message into Memory Bank stream (background task).
  3. Auto-manage: Memory Bank consolidates stream → long-term memories when idle.

Required env vars:
  GCP_PROJECT_ID             — GCP project ID.
  GCP_LOCATION               — GCP region (default: us-central1).
  MEMORY_BANK_RESOURCE_NAME  — Full resource name of an existing Memory Bank,
                                e.g. projects/…/locations/…/reasoningEngines/…
                                Create once with deploy/create_memory_bank.py.

Optional env vars:
  MEMORY_BANK_IDLE_SECONDS   — Idle-duration trigger in seconds (default: 300).
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from common.memory.base import BaseMemoryService, MemoryMode

logger = logging.getLogger(__name__)

# Back-reference phrases that signal the user wants long-term context even
# when a short-term session already exists.
_BACK_REF_PHRASES = (
    "as i said",
    "as mentioned",
    "earlier",
    "previous",
    "before",
    "last time",
    "you know my",
    "my preference",
    "my constraints",
    "based on what i told",
    "what did i say",
    "remind me",
)


class DecoupledMemoryService(BaseMemoryService):
    """
    Long-term memory via a standalone (decoupled) Memory Bank resource.

    The Memory Bank resource is managed independently of Agent Runtime —
    it persists across agent redeployments and can be shared across multiple
    agents or agent versions.

    When to use
    -----------
    - ``memory_bank.mode = "decoupled"`` in agent_runtime_config.json.
    - You need memory to survive agent redeployments.
    - Multiple agents or agent versions should share the same memory store.
    - You want custom retrieval control (top_k, scope, etc.).

    Initialisation
    --------------
    ``adk_memory_service`` is always ``None`` — ingestion and retrieval are
    handled manually by this class via ``ingest_turn_sync`` /
    ``retrieve_memories_sync``.

    Args:
        project              : GCP project ID (falls back to ``GCP_PROJECT_ID``).
        location             : GCP region (falls back to ``GCP_LOCATION``).
        idle_trigger_seconds : Idle-duration trigger for memory consolidation.
                               Falls back to ``MEMORY_BANK_IDLE_SECONDS`` env var,
                               then to 300 s.
    """

    adk_memory_service = None  # manual ingestion; not wired into ADK App

    def __init__(
        self,
        project: Optional[str] = None,
        location: Optional[str] = None,
        idle_trigger_seconds: int = 300,
    ) -> None:
        self.project = project or os.environ.get("GCP_PROJECT_ID")
        self.location = location or os.environ.get("GCP_LOCATION", "us-central1")
        self.idle_trigger_seconds = idle_trigger_seconds or int(
            os.environ.get("MEMORY_BANK_IDLE_SECONDS", "300")
        )
        self._client = None
        self._memory_bank_name: Optional[str] = None
        self._initialized = False

        if self.project and self.location:
            self._init_backend()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_backend(self) -> None:
        """Connect to an existing Memory Bank resource."""
        try:
            import vertexai

            vertexai.init(project=self.project, location=self.location)
            self._client = vertexai.Client(project=self.project, location=self.location)

            resource_name = os.environ.get("MEMORY_BANK_RESOURCE_NAME", "").strip()
            if not resource_name:
                logger.error(
                    "[DecoupledMemory] MEMORY_BANK_RESOURCE_NAME is not set. "
                    "Create a Memory Bank with deploy/create_memory_bank.py and "
                    "set this env var. Long-term memory is disabled."
                )
                return

            try:
                mb = self._client.agent_engines.get(name=resource_name)
                self._memory_bank_name = mb.api_resource.name
            except Exception as exc:
                logger.error(
                    "[DecoupledMemory] Cannot access resource '%s': %s. "
                    "Verify it exists and the agent identity has "
                    "aiplatform.reasoningEngines.get permission. "
                    "Long-term memory is disabled.",
                    resource_name, exc,
                )
                return

            self._initialized = True
            logger.info(
                "[DecoupledMemory] Ready | resource=%s | idle_trigger=%ds",
                self._memory_bank_name, self.idle_trigger_seconds,
            )
        except Exception as exc:
            logger.error("[DecoupledMemory] Unexpected init failure: %s", exc)

    # ------------------------------------------------------------------
    # BaseMemoryService interface
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return MemoryMode.DECOUPLED

    @property
    def is_available(self) -> bool:
        return self._initialized and bool(self._memory_bank_name)

    def ingest_turn_sync(
        self,
        user_id: str,
        session_id: str,
        text: str,
        event_id: Optional[str] = None,
    ) -> None:
        """
        Ingest a user turn into the Memory Bank stream (blocking).

        Always call via ``asyncio.to_thread()`` — never on the hot path.

        Memory Bank's server fires consolidation automatically after
        ``idle_trigger_seconds`` of stream inactivity.
        """
        if not self.is_available or not user_id or not text:
            return

        stream_id = session_id or f"user-{user_id}"
        eid = event_id or f"{stream_id}-{uuid.uuid4().hex[:8]}"

        try:
            self._client.agent_engines.memories.ingest_events(
                name=self._memory_bank_name,
                stream_id=stream_id,
                direct_contents_source={
                    "events": [
                        {
                            "content": {"role": "user", "parts": [{"text": text}]},
                            "event_id": eid,
                        }
                    ]
                },
                generation_trigger_config={
                    "generation_rule": {
                        "idle_duration": f"{self.idle_trigger_seconds}s",
                    }
                },
                scope={"user_id": user_id},
            )
            logger.debug(
                "[DecoupledMemory] Ingested | user=%s session=%s event_id=%s",
                user_id, session_id, eid,
            )
        except Exception as exc:
            logger.warning("[DecoupledMemory] ingest_events failed: %s", exc)

    def should_consult_memory(
        self,
        user_id: str,
        session_id: Optional[str],
        query_text: str,
    ) -> bool:
        """
        Session-first retrieval policy.

        - No active session → always consult long-term memory.
        - Active session + back-reference phrase → consult long-term memory.
        - Active session + fresh query → rely on session context only.
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
        Similarity-search for memories relevant to *query* (blocking).

        Returns ``[]`` silently when no memories exist yet — this is normal
        during early interactions before the first consolidation window.
        """
        if not self.is_available:
            return []

        try:
            logger.info(
                "[DecoupledMemory] Retrieving | user=%s query='%.100s'",
                user_id, query,
            )
            results = self._client.agent_engines.memories.retrieve(
                name=self._memory_bank_name,
                scope={"user_id": user_id},
                similarity_search_params={"search_query": query, "top_k": top_k},
            )
            memories: List[Dict[str, Any]] = [
                {
                    "fact": mem.memory.fact,
                    "distance": mem.distance,
                    "created": (
                        str(mem.memory.create_time)
                        if hasattr(mem.memory, "create_time") else None
                    ),
                }
                for mem in list(results)[:top_k]
            ]
            logger.info(
                "[DecoupledMemory] Retrieved %d memories for user=%s",
                len(memories), user_id,
            )
            return memories
        except Exception as exc:
            logger.debug(
                "[DecoupledMemory] Retrieval not yet available "
                "(expected before first consolidation): %s", exc,
            )
            return []
