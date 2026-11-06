"""
common/memory/null.py — No-op memory service (MemoryMode.DISABLED).

Used when long-term memory is intentionally turned off.  All methods are
zero-cost no-ops — no imports, no network calls, no cloud credentials needed.
Safe to use in unit tests and local development without any GCP setup.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common.memory.base import BaseMemoryService, MemoryMode


class NullMemoryService(BaseMemoryService):
    """
    No-op implementation — long-term memory is disabled.

    All interface methods return safe empty values instantly.
    ``adk_memory_service`` is always ``None``.

    When to use
    -----------
    - ``memory_bank.mode = "disabled"`` in agent_runtime_config.json.
    - Unit / integration tests that should not touch the cloud.
    - Local dev when no Memory Bank resource is available.
    """

    adk_memory_service = None

    @property
    def mode(self) -> str:
        return MemoryMode.DISABLED

    @property
    def is_available(self) -> bool:
        return False

    def ingest_turn_sync(
        self,
        user_id: str,
        session_id: str,
        text: str,
        event_id: Optional[str] = None,
    ) -> None:
        pass  # intentional no-op

    def should_consult_memory(
        self,
        user_id: str,
        session_id: Optional[str],
        query_text: str,
    ) -> bool:
        return False

    def retrieve_memories_sync(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        return []

    def build_context_prompt(
        self,
        user_query: str,
        user_id: str,
        include_memories: bool = True,
        top_k: int = 3,
    ) -> str:
        return user_query
