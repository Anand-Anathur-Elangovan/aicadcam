"""In-memory meeting session store."""
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class MeetingSession:
    meeting_id: str
    meet_link: str
    space_name: str = ""
    user_name: str = ""
    wake_name: str = ""
    user_email: str = ""
    slack_channel_id: str = ""
    status: str = "initializing"
    mode: str = ""
    context: dict[str, str] = field(default_factory=dict)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    name_mentions: list[dict[str, Any]] = field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None


_lock = threading.Lock()
_sessions: dict[str, MeetingSession] = {}
_pending_context: dict[str, str] = {}


def create_session(
    meet_link: str,
    user_name: str,
    wake_name: str,
    user_email: str = "",
    slack_channel_id: str = "",
) -> str:
    meeting_id = uuid.uuid4().hex[:8]
    session = MeetingSession(
        meeting_id=meeting_id,
        meet_link=meet_link,
        user_name=user_name,
        wake_name=wake_name.lower(),
        user_email=user_email,
        slack_channel_id=slack_channel_id,
        started_at=datetime.now(),
    )
    with _lock:
        session.context.update(_pending_context)
        _sessions[meeting_id] = session
    return meeting_id


def get_session(meeting_id: str) -> MeetingSession | None:
    with _lock:
        return _sessions.get(meeting_id)


def get_active_session() -> MeetingSession | None:
    with _lock:
        for s in _sessions.values():
            if s.status in ("initializing", "joining", "active"):
                return s
    return None


def update_status(meeting_id: str, status: str) -> None:
    with _lock:
        session = _sessions.get(meeting_id)
        if session:
            session.status = status
            if status == "ended":
                session.ended_at = datetime.now()


def update_mode(meeting_id: str, mode: str) -> None:
    with _lock:
        session = _sessions.get(meeting_id)
        if session:
            session.mode = mode


def update_space_name(meeting_id: str, space_name: str) -> None:
    with _lock:
        session = _sessions.get(meeting_id)
        if session:
            session.space_name = space_name


def update_wake_name(meeting_id: str, wake_name: str) -> None:
    with _lock:
        session = _sessions.get(meeting_id)
        if session:
            session.wake_name = wake_name.lower()


def add_context(meeting_id: str, topic: str, data: str) -> None:
    with _lock:
        session = _sessions.get(meeting_id)
        if session:
            session.context[topic] = data
        else:
            _pending_context[topic] = data


def store_pending_context(topic: str, data: str) -> None:
    with _lock:
        _pending_context[topic] = data


def get_context(meeting_id: str) -> dict[str, str]:
    with _lock:
        session = _sessions.get(meeting_id)
        return dict(session.context) if session else dict(_pending_context)


def append_transcript(
    meeting_id: str, speaker: str, text: str, timestamp: str
) -> None:
    with _lock:
        session = _sessions.get(meeting_id)
        if session:
            session.transcript.append(
                {"speaker": speaker, "text": text, "timestamp": timestamp}
            )


def get_transcript(meeting_id: str) -> list[dict[str, Any]]:
    with _lock:
        session = _sessions.get(meeting_id)
        return list(session.transcript) if session else []


def record_mention(
    meeting_id: str, timestamp: str, text: str, response: str
) -> None:
    with _lock:
        session = _sessions.get(meeting_id)
        if session:
            session.name_mentions.append(
                {"timestamp": timestamp, "text": text, "response": response}
            )


def get_all_sessions() -> list[dict[str, Any]]:
    with _lock:
        return [
            {
                "meeting_id": s.meeting_id,
                "status": s.status,
                "mode": s.mode,
                "user_name": s.user_name,
                "started_at": str(s.started_at) if s.started_at else None,
            }
            for s in _sessions.values()
        ]
