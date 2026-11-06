"""Email and Slack notification tools for meeting alerts."""
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests as http_requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app.config import (
    DEFAULT_FROM_EMAIL,
    EMAIL_SERVICE_URL,
    PROXIES,
    SLACK_BOT_TOKEN,
    SMTP_HOST,
    SMTP_PORT,
)

logger = logging.getLogger(__name__)


def send_slack_notification(
    channel_id: str, message: str, title: str = "Meeting Alert"
) -> dict[str, Any]:
    """Send a Slack notification about meeting activity.

    Args:
        channel_id: Slack channel ID.
        message: Notification message.
        title: Bold header above the message.

    Returns:
        dict with status, error.
    """
    if not SLACK_BOT_TOKEN:
        return {"status": "failed", "error": "SLACK_BOT_TOKEN not configured"}
    try:
        client = WebClient(token=SLACK_BOT_TOKEN)
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message}},
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Meeting AI Assistant | {datetime.now().strftime('%Y-%m-%d %H:%M')}_"}]},
        ]
        resp = client.chat_postMessage(channel=channel_id, blocks=blocks, text=message)
        return {"status": "sent", "ts": resp["ts"], "error": None}
    except SlackApiError as e:
        logger.error("Slack notification failed: %s", e.response["error"])
        return {"status": "failed", "error": e.response["error"]}
    except Exception as e:
        logger.error("Slack error: %s", e)
        return {"status": "failed", "error": str(e)}


def send_email_notification(
    to_email: str, subject: str, body: str, is_html: bool = False
) -> dict[str, Any]:
    """Send an email notification.

    Args:
        to_email: Recipient email address.
        subject: Email subject.
        body: Email body.
        is_html: Whether body is HTML.

    Returns:
        dict with status, error.
    """
    if EMAIL_SERVICE_URL:
        try:
            payload = {
                "to": [to_email],
                "subject": subject,
                "from": DEFAULT_FROM_EMAIL,
                "send_all_at_once": True,
            }
            if is_html:
                payload["html_content"] = body
            else:
                payload["body"] = body
            resp = http_requests.post(
                f"{EMAIL_SERVICE_URL}/send-email-dont-reply",
                json=payload,
                timeout=10,
                proxies=PROXIES,
            )
            resp.raise_for_status()
            return {"status": "sent", "method": "email_service", "error": None}
        except Exception as svc_err:
            logger.warning("Email service failed (%s), trying SMTP", str(svc_err)[:120])

    if not SMTP_HOST:
        return {"status": "failed", "error": "No email transport available"}
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = DEFAULT_FROM_EMAIL
        msg["To"] = to_email
        msg.attach(MIMEText(body, "html" if is_html else "plain"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.sendmail(DEFAULT_FROM_EMAIL, [to_email], msg.as_string())
        return {"status": "sent", "method": "smtp", "error": None}
    except Exception as smtp_err:
        logger.error("SMTP failed: %s", smtp_err)
        return {"status": "failed", "error": str(smtp_err)}


def notify_name_mention(
    meeting_id: str,
    mention_text: str,
    question: str,
    response_given: str,
    user_email: str = "",
    slack_channel_id: str = "",
) -> dict[str, Any]:
    """Send notifications when the user's name is mentioned in a meeting.

    Args:
        meeting_id: Meeting session ID.
        mention_text: The text surrounding the name mention.
        question: The extracted question/context.
        response_given: The auto-generated response.
        user_email: Email for notification.
        slack_channel_id: Slack channel for notification.

    Returns:
        dict with channels notified, error.
    """
    channels_sent = []
    errors = []

    formatted_msg = (
        f"*Your name was mentioned in meeting `{meeting_id}`*\n\n"
        f"> _{mention_text}_\n\n"
    )
    if question:
        formatted_msg += f"*Question/context:* {question}\n\n"
    if response_given:
        formatted_msg += f"*Auto-response given:* {response_given}\n"

    if slack_channel_id:
        result = send_slack_notification(slack_channel_id, formatted_msg)
        if result["status"] == "sent":
            channels_sent.append("slack")
        else:
            errors.append(f"slack: {result.get('error')}")

    if user_email:
        email_body = formatted_msg.replace("*", "").replace("_", "").replace(">", "")
        result = send_email_notification(
            user_email,
            f"Meeting Alert — Your name was mentioned ({meeting_id})",
            email_body,
        )
        if result["status"] == "sent":
            channels_sent.append("email")
        else:
            errors.append(f"email: {result.get('error')}")

    return {
        "status": "sent" if channels_sent else "failed",
        "channels": channels_sent,
        "error": "; ".join(errors) if errors else None,
    }
