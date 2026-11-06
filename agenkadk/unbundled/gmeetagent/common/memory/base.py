"""
common/memory/base.py — Abstract base class and mode constants.

Any agent in this project can depend on these types without pulling in
any Vertex AI or ADK SDK imports.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Mode constants
# ---------------------------------------------------------------------------

class MemoryMode:
    """
    Supported memory-backend modes.

    Set via ``memory_bank.mode`` in agent_runtime_config.json
    or the ``MEMORY_BANK_MODE`` environment variable.
    """
    DISABLED      = "disabled"       # no-op; zero dependencies
    DECOUPLED     = "decoupled"      # standalone Memory Bank resource (pre-created)
    AGENT_RUNTIME = "agent_runtime"  # built-in ADK VertexAiMemoryBankService


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseMemoryService(ABC):
    """
    Common interface for all long-term memory strategies.

    Implementations
    ---------------
    NullMemoryService         — MemoryMode.DISABLED
    DecoupledMemoryService    — MemoryMode.DECOUPLED
    AgentRuntimeMemoryService — MemoryMode.AGENT_RUNTIME

    The interface is intentionally thin so that:
    - Agent code never needs ``if mode == ...`` branches.
    - Strategies can be swapped via config with zero agent-code changes.
    - Unit tests can inject NullMemoryService without any cloud credentials.

    ADK wiring
    ----------
    When mode is ``agent_runtime``, set ``App(memory_service=svc.adk_memory_service)``
    in your agent builder.  For all other modes ``adk_memory_service`` is ``None``.
    """

    #: Expose the ADK VertexAiMemoryBankService instance for App() wiring.
    #: Only populated by AgentRuntimeMemoryService; None for all other modes.
    adk_memory_service: Optional[Any] = None

    # ------------------------------------------------------------------
    # Core interface — all strategies must implement these
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def mode(self) -> str:
        """Return the MemoryMode string for this instance."""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """True when the backend is reachable and ready."""

    @abstractmethod
    def ingest_turn_sync(
        self,
        user_id: str,
        session_id: str,
        text: str,
        event_id: Optional[str] = None,
    ) -> None:
        """
        Ingest a single user turn into long-term memory (synchronous / blocking).

        **Always** call this via ``asyncio.to_thread()`` to avoid blocking the
        event loop.  Fire-and-forget via ``asyncio.create_task`` is fine.

        Args:
            user_id    : Scopes memories to a user; required.
            session_id : Stream ID for isolation between sessions.
            text       : User message text.
            event_id   : Optional deduplication ID; auto-generated if omitted.
        """

    @abstractmethod
    def should_consult_memory(
        self,
        user_id: str,
        session_id: Optional[str],
        query_text: str,
    ) -> bool:
        """
        Return True when long-term memory retrieval is warranted.

        Implementations apply a retrieval policy suited to their mode, e.g.:
        - NullMemoryService    → always False
        - DecoupledMemoryService → session-first heuristic
        - AgentRuntimeMemoryService → always False (ADK injects automatically)
        """

    @abstractmethod
    def retrieve_memories_sync(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant memories for *query* (synchronous / blocking).

        Returns a list of dicts with at minimum a ``"fact"`` key.
        Returns ``[]`` when unavailable or no memories exist yet.

        Args:
            user_id : User identifier.
            query   : Search query text (typically the user's current message).
            top_k   : Maximum number of memories to return.
        """

    # ------------------------------------------------------------------
    # Shared helper (override in strategies if behaviour differs)
    # ------------------------------------------------------------------

    def build_context_prompt(
        self,
        user_query: str,
        user_id: str,
        include_memories: bool = True,
        top_k: int = 3,
    ) -> str:
        """
        Prepend retrieved long-term memories to *user_query* (synchronous).

        Returns the original query unchanged when:
        - ``include_memories`` is False, or
        - the backend is unavailable, or
        - no relevant memories exist yet.

        Args:
            user_query       : The raw user message.
            user_id          : User identifier.
            include_memories : Set False to skip retrieval (e.g., in tests).
            top_k            : Max memories to inject.
        """
        if not include_memories or not self.is_available:
            return user_query

        memories = self.retrieve_memories_sync(user_id, user_query, top_k=top_k)
        if not memories:
            return user_query

        header = "## Your Previous Context\n" + "".join(
            f"- {m['fact']}\n" for m in memories
        )
        return f"{header}\n## Current Question\n{user_query}"
