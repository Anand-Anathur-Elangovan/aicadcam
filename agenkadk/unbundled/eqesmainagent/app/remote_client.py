"""Generic client for querying remote agents deployed on Vertex AI Agent Runtime.

Uses the REST API :streamQuery endpoint directly and parses the response body
ourselves, bypassing the SDK's stream_query JSON parser which fails in deployed
containers with "Can only parse array of JSON objects, instead got {".
"""

import json
import logging
import threading
import time

import google.auth
import google.auth.transport.requests as google_auth_requests
import requests as http_requests
import vertexai
from vertexai import agent_engines

from app.config import GCP_PROJECT, GCP_REGION

logger = logging.getLogger(__name__)

_SESSION_TTL_SECONDS = 3600  # 1 hour


def _extract_text_from_event(event: dict) -> str:
    """Extract text from a single ADK event dict, skipping function parts."""
    try:
        content = event.get("content")
        if not content or not isinstance(content, dict):
            logger.debug("Event has no valid content: %s", event)
            return ""

        parts = content.get("parts")
        if parts is None:
            logger.warning("Event content.parts is None: %s", event)
            return ""

        if not isinstance(parts, list):
            logger.warning("Event content.parts is not a list (type=%s): %s", type(parts), event)
            return ""

        texts = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if "functionCall" in part or "functionResponse" in part:
                continue
            if "function_call" in part or "function_response" in part:
                continue
            val = part.get("text")
            if val and isinstance(val, str) and val.strip():
                texts.append(val.strip())

        return "\n".join(texts)
    except Exception as exc:
        logger.exception("Error extracting text from event: %s", exc)
        return ""


def _parse_stream_response(body: str) -> list[dict]:
    """Parse a streamQuery HTTP response body into a list of event dicts.

    Handles multiple formats:
    - JSON array: [{event1}, {event2}]
    - Single JSON object: {event1}
    - NDJSON: {event1}\\n{event2}
    - Comma-separated in array: [{event1},\\n{event2}\\n]
    """
    body = body.strip()
    if not body:
        return []

    # Format 1: JSON array
    if body.startswith("["):
        try:
            parsed = json.loads(body)
            if isinstance(parsed, list):
                return [e for e in parsed if isinstance(e, dict)]
        except json.JSONDecodeError:
            pass

    # Format 2: single JSON object
    if body.startswith("{") and not "\n" in body:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                return [parsed]
        except json.JSONDecodeError:
            pass

    # Format 3: NDJSON or comma-separated lines (with or without [ ] wrapper)
    events = []
    for line in body.split("\n"):
        line = line.strip()
        if not line or line in ("[", "]", ","):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line.endswith(","):
            line = line[:-1].strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
            elif isinstance(obj, list):
                events.extend(e for e in obj if isinstance(e, dict))
        except json.JSONDecodeError:
            continue

    return events


class RemoteAgentClient:
    """Sends queries to a deployed Vertex AI agent and returns the text response.

    Primary: REST API :streamQuery with manual response parsing.
    Fallback: SDK stream_query (works locally).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], tuple[str, float]] = {}
        self._remote_apps: dict[str, object] = {}
        self._credentials = None

    def _get_credentials(self):
        if self._credentials is None or not self._credentials.valid or self._credentials.expired:
            self._credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        if not self._credentials.valid or self._credentials.expired:
            self._credentials.refresh(google_auth_requests.Request())
        return self._credentials

    def _get_remote_app(self, resource_name: str):
        if resource_name not in self._remote_apps:
            vertexai.init(project=GCP_PROJECT, location=GCP_REGION)
            self._remote_apps[resource_name] = agent_engines.get(resource_name)
        return self._remote_apps[resource_name]

    def _get_or_create_session(
        self, remote_app, resource_name: str, user_id: str
    ) -> str:
        key = (resource_name, user_id)
        now = time.time()

        with self._lock:
            if key in self._sessions:
                session_id, created = self._sessions[key]
                if now - created < _SESSION_TTL_SECONDS:
                    return session_id
                logger.info("Session expired for %s / %s, creating new one", resource_name, user_id)

        session = remote_app.create_session(user_id=user_id)
        session_id = session.id if hasattr(session, "id") else session["id"]

        with self._lock:
            self._sessions[key] = (session_id, now)

        return session_id

    def query(self, resource_name: str, user_id: str, message: str) -> str:
        """Send a message to a remote agent and return the text response."""
        remote_app = self._get_remote_app(resource_name)
        session_id = self._get_or_create_session(remote_app, resource_name, user_id)

        logger.info(
            "Querying remote agent %s (session=%s, user=%s)",
            resource_name, session_id, user_id,
        )

        # Primary: REST API :streamQuery with manual parsing
        try:
            text = self._query_stream_rest(resource_name, user_id, session_id, message)
            if text:
                logger.info("REST streamQuery returned %d chars", len(text))
                return text
            logger.warning("REST streamQuery returned no text")
        except Exception as exc:
            logger.warning("REST streamQuery failed: %s, trying SDK fallback", exc)

        # Fallback: SDK stream_query (works locally)
        return self._query_stream_sdk(remote_app, user_id, session_id, message)

    def _query_stream_rest(
        self, resource_name: str, user_id: str, session_id: str, message: str
    ) -> str:
        """Query via REST API :streamQuery endpoint with manual response parsing."""
        credentials = self._get_credentials()

        url = (
            f"https://{GCP_REGION}-aiplatform.googleapis.com"
            f"/v1beta1/{resource_name}:streamQuery"
        )

        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        }

        payload = {
            "input": {
                "user_id": user_id,
                "session_id": session_id,
                "message": message,
            }
        }

        logger.info("REST streamQuery to %s", url)
        resp = http_requests.post(url, headers=headers, json=payload, timeout=120)

        if resp.status_code != 200:
            logger.warning(
                "REST streamQuery HTTP %d: %s", resp.status_code, resp.text[:500]
            )
            resp.raise_for_status()

        body = resp.text
        logger.info("REST streamQuery response length: %d chars", len(body))
        logger.info("REST streamQuery response start: %.200s", body[:200])

        events = _parse_stream_response(body)
        logger.info("Parsed %d events from response", len(events))

        # Find the last event with actual text (the agent's final response)
        for event in reversed(events):
            text = _extract_text_from_event(event)
            if text:
                return text

        # If structured extraction failed, log all events for debugging
        if events:
            logger.warning(
                "No text found in %d events. First event keys: %s",
                len(events),
                list(events[0].keys()) if events else "none",
            )

        return ""

    def _query_stream_sdk(
        self, remote_app, user_id: str, session_id: str, message: str
    ) -> str:
        """Fallback: query via SDK stream_query (works locally)."""
        text_parts: list[str] = []

        try:
            for event in remote_app.stream_query(
                user_id=user_id,
                session_id=session_id,
                message=message,
            ):
                if isinstance(event, dict):
                    text = _extract_text_from_event(event)
                else:
                    text = ""
                    try:
                        content = getattr(event, "content", None)
                        if content and hasattr(content, "parts"):
                            parts = content.parts
                            if parts is not None:
                                for part in parts:
                                    val = getattr(part, "text", None)
                                    if val and isinstance(val, str) and val.strip():
                                        text = val.strip()
                            else:
                                logger.warning("SDK event content.parts is None")
                    except Exception as part_exc:
                        logger.warning("Error extracting text from SDK event: %s", part_exc)

                if text:
                    text_parts.append(text)
        except Exception as exc:
            logger.exception("SDK stream_query error after %d parts: %s", len(text_parts), exc)

        if text_parts:
            return "\n".join(text_parts).strip()

        return "The remote agent processed the request but the response could not be read. Please try again."


remote_client = RemoteAgentClient()
