"""Response validator to prevent 'NoneType' object is not subscriptable errors.

This module adds defensive checks for ADK event responses where parts can be None.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def validate_event_parts(event: Any) -> bool:
    """Validate that an event has properly structured parts.

    Args:
        event: ADK event object or dict

    Returns:
        True if event has valid parts, False otherwise
    """
    try:
        # Handle dict-style events
        if isinstance(event, dict):
            content = event.get("content")
            if not content:
                return False

            if not isinstance(content, dict):
                logger.warning("Event content is not a dict: %s", type(content))
                return False

            parts = content.get("parts")
            if parts is None:
                logger.warning("Event content.parts is None")
                return False

            if not isinstance(parts, list):
                logger.warning("Event content.parts is not a list: %s", type(parts))
                return False

            return True

        # Handle object-style events
        content = getattr(event, "content", None)
        if not content:
            return False

        parts = getattr(content, "parts", None)
        if parts is None:
            logger.warning("Event content.parts is None (object)")
            return False

        return True

    except Exception as exc:
        logger.exception("Error validating event parts: %s", exc)
        return False


def extract_text_from_event(event: Any) -> str:
    """Safely extract text from an ADK event.

    Args:
        event: ADK event object or dict

    Returns:
        Extracted text, or empty string if extraction fails
    """
    try:
        # Handle dict-style events
        if isinstance(event, dict):
            content = event.get("content")
            if not content or not isinstance(content, dict):
                return ""

            parts = content.get("parts")
            if parts is None:
                logger.warning("Event content.parts is None, skipping text extraction")
                return ""

            if not isinstance(parts, list):
                logger.warning("Event content.parts is not a list: %s", type(parts))
                return ""

            texts = []
            for part in parts:
                if not isinstance(part, dict):
                    continue

                # Skip function calls/responses
                if any(key in part for key in ["functionCall", "functionResponse",
                                               "function_call", "function_response"]):
                    continue

                val = part.get("text")
                if val and isinstance(val, str) and val.strip():
                    texts.append(val.strip())

            return "\n".join(texts)

        # Handle object-style events
        content = getattr(event, "content", None)
        if not content:
            return ""

        parts = getattr(content, "parts", None)
        if parts is None:
            logger.warning("Event content.parts is None (object), skipping text extraction")
            return ""

        texts = []
        try:
            for part in parts:
                # Skip function calls/responses
                if hasattr(part, "function_call") or hasattr(part, "function_response"):
                    continue

                val = getattr(part, "text", None)
                if val and isinstance(val, str) and val.strip():
                    texts.append(val.strip())
        except TypeError as exc:
            logger.warning("Error iterating parts (not iterable): %s", exc)
            return ""

        return "\n".join(texts)

    except Exception as exc:
        logger.exception("Error extracting text from event: %s", exc)
        return ""


def sanitize_event(event: Any) -> Any:
    """Sanitize an event to ensure it has valid parts structure.

    Args:
        event: ADK event object or dict

    Returns:
        Sanitized event with parts guaranteed to be a list
    """
    try:
        # Handle dict-style events
        if isinstance(event, dict):
            if "content" in event and isinstance(event["content"], dict):
                if "parts" not in event["content"] or event["content"]["parts"] is None:
                    logger.warning("Sanitizing event: setting parts to empty list")
                    event["content"]["parts"] = []
            return event

        # Handle object-style events (harder to modify, just log)
        content = getattr(event, "content", None)
        if content:
            parts = getattr(content, "parts", None)
            if parts is None:
                logger.warning("Event has None parts but cannot modify object-style event")

        return event

    except Exception as exc:
        logger.exception("Error sanitizing event: %s", exc)
        return event
