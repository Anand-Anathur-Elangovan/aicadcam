"""ADK tool functions for the Meeting AI Assistant."""
import asyncio
import logging
import re
from typing import Any

from app import session_store
from app.meeting_manager import get_manager
from app.notification_tools import (
    send_email_notification,
    send_slack_notification,
)

logger = logging.getLogger(__name__)


def feed_meeting_context(topic: str, context_data: str) -> dict[str, Any]:
    """Feed context/data that the bot should know for the meeting.

    Call this before or during a meeting so the bot can use this
    information when auto-responding on your behalf.

    Args:
        topic: Short topic label (e.g. 'project_budget', 'timeline', 'team_size').
        context_data: The actual data or talking point (e.g. 'Budget is $2M for Q3').

    Returns:
        dict with status, topic, error.
    """
    try:
        active = session_store.get_active_session()
        if active:
            session_store.add_context(active.meeting_id, topic, context_data)
        else:
            session_store.store_pending_context(topic, context_data)
        return {"status": "stored", "topic": topic, "error": None}
    except Exception as exc:
        logger.error("feed_meeting_context failed: %s", exc)
        return {"status": "failed", "topic": topic, "error": str(exc)}


async def join_meeting(
    meet_link: str,
    user_name: str,
    wake_name: str,
    user_email: str = "",
    slack_channel_id: str = "",
) -> dict[str, Any]:
    """Join a Google Meet as an AI assistant bot.

    The bot will transcribe the meeting in real-time, detect when
    your name is called, auto-respond using context you've provided,
    and notify you via Slack/email.

    Args:
        meet_link: Full Google Meet URL (e.g. https://meet.google.com/abc-defg-hij).
        user_name: Your display name (used in responses).
        wake_name: The name to listen for (e.g. your first name).
        user_email: Email for notifications when your name is mentioned.
        slack_channel_id: Slack channel ID for notifications.

    Returns:
        dict with meeting_id, status, mode, error.
    """
    try:
        if not re.search(r"meet\.google\.com/", meet_link):
            return {
                "meeting_id": None,
                "status": "failed",
                "error": "Invalid Meet link. Expected format: https://meet.google.com/xxx-xxxx-xxx",
            }

        manager = get_manager()
        result = await manager.join_meeting(
            meet_link=meet_link,
            user_name=user_name,
            wake_name=wake_name,
            user_email=user_email,
            slack_channel_id=slack_channel_id,
        )
        return result
    except Exception as exc:
        logger.error("join_meeting failed: %s", exc)
        return {"meeting_id": None, "status": "failed", "error": str(exc)}


async def leave_meeting(meeting_id: str) -> dict[str, Any]:
    """Leave an active meeting and stop transcription.

    Args:
        meeting_id: The meeting session ID returned by join_meeting.

    Returns:
        dict with status, transcript_length, name_mentions count, error.
    """
    try:
        manager = get_manager()
        result = await manager.leave_meeting(meeting_id)
        return result
    except Exception as exc:
        logger.error("leave_meeting failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


def get_transcript(meeting_id: str) -> dict[str, Any]:
    """Get the current transcript of a meeting.

    Args:
        meeting_id: The meeting session ID.

    Returns:
        dict with transcript list, length, error.
    """
    try:
        manager = get_manager()
        return manager.get_transcript(meeting_id)
    except Exception as exc:
        logger.error("get_transcript failed: %s", exc)
        return {"transcript": [], "length": 0, "error": str(exc)}


async def get_meeting_summary(meeting_id: str) -> dict[str, Any]:
    """Generate a summary of the meeting with action items.

    Best called after the meeting has ended or has enough transcript data.

    Args:
        meeting_id: The meeting session ID.

    Returns:
        dict with summary text, action_items list, duration, error.
    """
    try:
        manager = get_manager()
        result = await manager.generate_summary(meeting_id)
        return result
    except Exception as exc:
        logger.error("get_meeting_summary failed: %s", exc)
        return {"summary": "", "action_items": [], "error": str(exc)}


def set_wake_name(meeting_id: str, wake_name: str) -> dict[str, Any]:
    """Change the name the bot listens for in an active meeting.

    Args:
        meeting_id: The meeting session ID.
        wake_name: New name to listen for.

    Returns:
        dict with status, wake_name, error.
    """
    try:
        manager = get_manager()
        return manager.update_wake_name(meeting_id, wake_name)
    except Exception as exc:
        logger.error("set_wake_name failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


def send_meeting_notification(
    user_email: str,
    message: str,
    slack_channel_id: str = "",
) -> dict[str, Any]:
    """Send a notification about the meeting to the user.

    Args:
        user_email: Recipient email address.
        message: Notification message.
        slack_channel_id: Optional Slack channel for notification.

    Returns:
        dict with status, channels sent, error.
    """
    try:
        channels = []
        errors = []

        if user_email:
            result = send_email_notification(
                user_email, "Meeting AI Assistant Notification", message
            )
            if result["status"] == "sent":
                channels.append("email")
            else:
                errors.append(f"email: {result.get('error')}")

        if slack_channel_id:
            result = send_slack_notification(slack_channel_id, message)
            if result["status"] == "sent":
                channels.append("slack")
            else:
                errors.append(f"slack: {result.get('error')}")

        return {
            "status": "sent" if channels else "failed",
            "channels": channels,
            "error": "; ".join(errors) if errors else None,
        }
    except Exception as exc:
        logger.error("send_meeting_notification failed: %s", exc)
        return {"status": "failed", "channels": [], "error": str(exc)}
