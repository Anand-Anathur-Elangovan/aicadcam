"""Report generation, email delivery, Slack posting, and scheduling tools."""
import logging
import os
import smtplib
import json
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests as http_requests
from jinja2 import Template
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

# ── Environment config ─────────────────────────────────────────────────────────
BQ_PROJECT        = "vz-it-np-voev-dev-voevdo-0"
EMAIL_SERVICE_URL = "https://email-service-vdsi.ebiz.verizon.com"
SMTP_HOST         = "vzsmtp.verizon.com"
SMTP_PORT         = 25
SLACK_BOT_TOKEN   = os.environ.get("SLACK_BOT_TOKEN", "")
DEFAULT_FROM      = "do-not-reply@verizon.com"

# Corporate proxy (hardcoded for POC)
_PROXIES = {
    "http":  "http://proxy.ebiz.verizon.com:80/",
    "https": "http://proxy.ebiz.verizon.com:80/",
}

# ── HTML report template ───────────────────────────────────────────────────────
_REPORT_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body { font-family: Arial, sans-serif; margin: 0; padding: 0; background: #f4f6f9; }
  .container { max-width: 900px; margin: 24px auto; background: #fff;
               border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden; }
  .header { background: #cc0000; color: #fff; padding: 24px 32px; }
  .header h1 { margin: 0; font-size: 22px; }
  .header p  { margin: 6px 0 0; opacity: 0.85; font-size: 13px; }
  .body  { padding: 24px 32px; }
  .section { margin-bottom: 28px; }
  .section h2 { color: #cc0000; font-size: 16px; border-bottom: 2px solid #f0f0f0;
                padding-bottom: 6px; margin-bottom: 12px; }
  table  { width: 100%; border-collapse: collapse; font-size: 13px; }
  th     { background: #f5f5f5; text-align: left; padding: 8px 10px; border-bottom: 2px solid #ddd; }
  td     { padding: 7px 10px; border-bottom: 1px solid #eee; }
  tr:nth-child(even) td { background: #fafafa; }
  .kpi-row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; }
  .kpi { background: #f8f9fa; border-left: 4px solid #cc0000; padding: 12px 16px;
         border-radius: 4px; flex: 1; min-width: 140px; }
  .kpi .val { font-size: 26px; font-weight: bold; color: #cc0000; }
  .kpi .lbl { font-size: 12px; color: #666; margin-top: 2px; }
  .footer { background: #f5f5f5; padding: 14px 32px; font-size: 11px; color: #999; }
  .chart img { max-width: 100%; border-radius: 6px; margin-top: 8px; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{{ title }}</h1>
    <p>{{ subtitle }} &nbsp;|&nbsp; Generated: {{ generated_at }}</p>
  </div>
  <div class="body">
    {% if kpis %}
    <div class="kpi-row">
      {% for kpi in kpis %}
      <div class="kpi"><div class="val">{{ kpi.value }}</div><div class="lbl">{{ kpi.label }}</div></div>
      {% endfor %}
    </div>
    {% endif %}

    {% for section in sections %}
    <div class="section">
      <h2>{{ section.title }}</h2>
      {% if section.text %}<p>{{ section.text }}</p>{% endif %}
      {% if section.table_rows %}
      <table>
        <tr>{% for h in section.table_headers %}<th>{{ h }}</th>{% endfor %}</tr>
        {% for row in section.table_rows %}
        <tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>
        {% endfor %}
      </table>
      {% endif %}
      {% if section.chart_b64 %}
      <div class="chart"><img src="data:image/png;base64,{{ section.chart_b64 }}" /></div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
  <div class="footer">Verizon Data Science Assistant &nbsp;|&nbsp; {{ generated_at }}</div>
</div>
</body>
</html>"""


def generate_data_report(
    title: str,
    subtitle: str,
    kpis: list[dict] | None = None,
    sections: list[dict] | None = None,
) -> dict[str, Any]:
    """Generate an HTML report from structured data sections.

    Args:
        title: Report title (e.g. 'Slack Channel Weekly Report').
        subtitle: Subtitle / date range (e.g. 'Channel: help-gemini | Jun 15–22 2026').
        kpis: List of KPI dicts: [{"label": "Total Messages", "value": "1,234"}, ...].
        sections: List of section dicts, each with:
            - title (str): Section heading
            - text (str, optional): Narrative paragraph
            - table_headers (list[str], optional): Table column headers
            - table_rows (list[list], optional): Table row data
            - chart_b64 (str, optional): Base64 PNG from create_chart tool

    Returns:
        dict with html (str), title, generated_at, error (str or None).
    """
    try:
        tmpl = Template(_REPORT_TEMPLATE)
        html = tmpl.render(
            title=title,
            subtitle=subtitle,
            kpis=kpis or [],
            sections=sections or [],
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
        )
        return {"html": html, "title": title, "generated_at": datetime.now().isoformat(), "error": None}
    except Exception as e:
        logger.error("generate_data_report failed: %s", e)
        return {"html": "", "title": title, "error": str(e)}


def send_report_email(
    html_content: str,
    subject: str,
    to_emails: list[str],
    from_email: str = DEFAULT_FROM,
) -> dict[str, Any]:
    """Send an HTML report by email.

    Tries the internal Verizon email service first, then falls back to SMTP.
    NOTE: Both endpoints may be unreachable from GCP Agent Runtime if they
    use internal Verizon DNS. Contact the platform team for a VPC connector
    or external SMTP relay if delivery fails.

    Args:
        html_content: Full HTML string of the report.
        subject: Email subject line.
        to_emails: List of recipient email addresses.
        from_email: Sender address (defaults to do-not-reply@verizon.com).

    Returns:
        dict with status ('sent'/'failed'), method, recipients, error (str or None).
    """
    # ── Attempt 1: internal HTTP email service ─────────────────────────────────
    if EMAIL_SERVICE_URL:
        try:
            resp = http_requests.post(
                f"{EMAIL_SERVICE_URL}/send-email-dont-reply",
                json={
                    "to": to_emails,
                    "subject": subject,
                    "html_content": html_content,
                    "from": from_email,
                    "send_all_at_once": True,
                },
                timeout=10,
                proxies=_PROXIES,
            )
            resp.raise_for_status()
            return {"status": "sent", "method": "email_service", "recipients": to_emails, "error": None}
        except Exception as svc_err:
            _svc_err_msg = str(svc_err)
            logger.warning("Email service unreachable (%s), trying SMTP", _svc_err_msg)
    else:
        _svc_err_msg = "EMAIL_SERVICE_URL not configured"

    # ── Attempt 2: direct SMTP ─────────────────────────────────────────────────
    if not SMTP_HOST:
        return {
            "status": "failed",
            "error": (
                f"Email service: {_svc_err_msg}. SMTP_HOST is not configured. "
                "The agent cannot send email from GCP without a reachable SMTP relay. "
                "Contact the platform team to set up a VPC connector or external SMTP."
            ),
            "recipients": to_emails,
        }
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = ", ".join(to_emails)
        msg.attach(MIMEText(html_content, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            if SMTP_PORT in (587, 465):
                server.starttls()
            smtp_pass = os.environ.get("SMTP_PASS", "")
            if smtp_pass:
                server.login(from_email, smtp_pass)
            server.sendmail(from_email, to_emails, msg.as_string())
        return {"status": "sent", "method": "smtp", "recipients": to_emails, "error": None}
    except Exception as smtp_err:
        logger.error("SMTP also failed: %s", smtp_err)
        return {
            "status": "failed",
            "error": f"Email service: {_svc_err_msg}. SMTP: {smtp_err}",
            "recipients": to_emails,
        }


def post_report_to_slack(
    channel_id: str,
    message: str,
    report_title: str | None = None,
) -> dict[str, Any]:
    """Post a report summary to a Slack channel.

    Args:
        channel_id: Slack channel ID (e.g. C0AE1G97QCV).
        message: The text/summary to post. Supports Slack mrkdwn formatting.
        report_title: Optional title shown as a bold header above the message.

    Returns:
        dict with status ('sent'/'failed'), channel_id, ts (message timestamp), error.
    """
    if not SLACK_BOT_TOKEN:
        return {"status": "failed", "error": "SLACK_BOT_TOKEN not configured", "channel_id": channel_id}
    try:
        client = WebClient(token=SLACK_BOT_TOKEN)
        blocks = []
        if report_title:
            blocks.append({
                "type": "header",
                "text": {"type": "plain_text", "text": report_title, "emoji": True},
            })
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": message}})
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_Verizon Data Science Assistant · {datetime.now().strftime('%Y-%m-%d %H:%M')}_"}],
        })
        resp = client.chat_postMessage(channel=channel_id, blocks=blocks, text=message)
        return {"status": "sent", "channel_id": channel_id, "ts": resp["ts"], "error": None}
    except SlackApiError as e:
        logger.error("Slack post failed: %s", e.response["error"])
        return {"status": "failed", "channel_id": channel_id, "error": e.response["error"]}
    except Exception as e:
        logger.error("Slack post unexpected error: %s", e)
        return {"status": "failed", "channel_id": channel_id, "error": str(e)}
