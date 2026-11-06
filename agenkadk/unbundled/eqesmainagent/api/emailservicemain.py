import os

# Route Vertex AI (aiplatform.googleapis.com) calls through corporate proxy
# to satisfy VPC-SC network access level requirements.
# Must be set before vertexai is imported/initialized.
_CORP_PROXY = os.environ.get("HTTPS_PROXY", "http://proxy.ebiz.verizon.com:9290")
os.environ.setdefault("HTTPS_PROXY", _CORP_PROXY)
os.environ.setdefault("https_proxy", _CORP_PROXY)
os.environ.setdefault("NO_PROXY", "verizon.com,atyourservice.verizon.com,localhost,127.0.0.1,.svc.cluster.local")
os.environ.setdefault("no_proxy", "verizon.com,atyourservice.verizon.com,localhost,127.0.0.1,.svc.cluster.local")

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.message import EmailMessage
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.auth
import logging
import base64
import re
import csv
import io
import uuid
import json as _json
import requests as http_requests
from pathlib import Path
import time
import pandas as pd
from datetime import datetime, timedelta
from google.cloud import bigquery
from google.cloud import dialogflowcx_v3 as dialogflowcx
try:
    from google.cloud import run_v2
    _HAS_CLOUD_RUN = True
except ImportError:
    _HAS_CLOUD_RUN = False
    logging.warning("google-cloud-run not installed – Cloud Run Job mode unavailable")
try:
    from google.cloud import scheduler_v1
    _HAS_CLOUD_SCHEDULER = True
except ImportError:
    _HAS_CLOUD_SCHEDULER = False
    logging.warning("google-cloud-scheduler not installed – scheduling unavailable")
try:
    from google.cloud import logging as cloud_logging
    _HAS_CLOUD_LOGGING = True
except ImportError:
    _HAS_CLOUD_LOGGING = False
try:
    import vertexai
    from vertexai import agent_engines as _agent_engines
    from vertexai.generative_models import GenerativeModel, Content, Part, Tool, FunctionDeclaration
    _VERTEXAI_AVAILABLE = True
except ImportError:
    _VERTEXAI_AVAILABLE = False
    vertexai = None
    _agent_engines = None
    GenerativeModel = None
    Content = None
    Part = None
    Tool = None
    FunctionDeclaration = None

# Initialize Flask app
app = Flask(__name__)

# Enable CORS for all routes
CORS(app)

# Register GMeet Scoring API blueprint
from gmeet_scoring_api import gmeet_bp
app.register_blueprint(gmeet_bp)

# Initialize Google Cloud authentication
AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])

# SMTP Server Configuration
SMTP_SERVER = "tpaapd1kva109.verizon.com"
VDSI_SMTP_SERVER = "vzsmtp.verizon.com"
DO_NOT_REPLY_SMTP_SERVER = "tpaapd1kva109.verizon.com"
SMTP_PROXY_URL = "http://proxy.ebiz.verizon.com:9290"


class _ProxySMTP(smtplib.SMTP):
    """SMTP client that tunnels through an HTTP CONNECT proxy."""
    def __init__(self, host, port=25, proxy_url=SMTP_PROXY_URL, timeout=30):
        self._proxy_url = proxy_url
        super().__init__(host, port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        import socket
        from urllib.parse import urlparse
        p = urlparse(self._proxy_url)
        proxy_host = p.hostname
        proxy_port = p.port or 9290
        logging.getLogger(__name__).info(
            f"[ProxySMTP] Resolving proxy {proxy_host}:{proxy_port} ..."
        )
        try:
            infos = socket.getaddrinfo(proxy_host, proxy_port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            resolved_ip = infos[0][4][0] if infos else "?"
            logging.getLogger(__name__).info(f"[ProxySMTP] Proxy resolved to {resolved_ip}")
        except socket.gaierror as dns_err:
            raise OSError(f"[ProxySMTP] DNS failed for {proxy_host}: {dns_err}")
        try:
            sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
        except socket.timeout:
            raise OSError(f"[ProxySMTP] TCP timeout connecting to {proxy_host}:{proxy_port} (port blocked?)")
        except OSError as conn_err:
            raise OSError(f"[ProxySMTP] TCP connect failed to {proxy_host}:{proxy_port}: {conn_err}")
        logging.getLogger(__name__).info(f"[ProxySMTP] Connected to proxy. Sending CONNECT {host}:{port}")
        sock.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp += chunk
        logging.getLogger(__name__).info(f"[ProxySMTP] Proxy CONNECT response: {resp[:200]!r}")
        if b" 200 " not in resp:
            sock.close()
            raise OSError(f"[ProxySMTP] CONNECT rejected: {resp[:300]!r}")
        logging.getLogger(__name__).info("[ProxySMTP] Tunnel established. Starting SMTP session.")
        return sock


DEFAULT_FROM = "vea.dev@verizon.com"
VDSI_FROM = "Persona Device Distribution <vzworkforward@verizon.com>"
DO_NOT_REPLY_FROM = "do-not-reply@verizon.com"

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def embed_images_in_html(html_content, base_path=None):
    """
    Convert image paths to base64-encoded data URIs in HTML content
    
    Args:
        html_content: HTML string
        base_path: Base directory path for resolving relative image paths
    
    Returns:
        HTML string with embedded images
    """
    if not base_path:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    # Find all img src attributes
    img_pattern = r'<img([^>]*?)src=["\']([^"\']+)["\']([^>]*)>'
    
    def replace_img_src(match):
        prefix = match.group(1)
        src = match.group(2)
        suffix = match.group(3)
        
        # Skip data URIs and absolute URLs
        if src.startswith('data:') or src.startswith('http://') or src.startswith('https://'):
            return match.group(0)
        
        # Try to resolve local file
        if not src.startswith('/'):
            img_path = os.path.join(base_path, src)
        else:
            img_path = src
        
        try:
            if os.path.exists(img_path):
                with open(img_path, 'rb') as img_file:
                    img_data = base64.b64encode(img_file.read()).decode()
                    ext = os.path.splitext(img_path)[1].lower().strip('.')
                    mime_type = f'image/{ext}' if ext else 'image/png'
                    data_uri = f'data:{mime_type};base64,{img_data}'
                    return f'<img{prefix}src="{data_uri}"{suffix}>'
            else:
                logger.warning(f"Image file not found: {img_path}")
                return match.group(0)
        except Exception as e:
            logger.error(f"Error embedding image {src}: {str(e)}")
            return match.group(0)
    
    return re.sub(img_pattern, replace_img_src, html_content)


def inline_external_css(html_content):
    """
    Remove or comment out external CSS/script tags for email compatibility
    Keeps inline styles intact
    
    Args:
        html_content: HTML string
    
    Returns:
        HTML string with external resources removed/commented
    """
    # Remove external script tags
    html_content = re.sub(r'<script[^>]*src=["\'][^"\']*["\'][^>]*></script>', '', html_content)
    
    # Remove external link tags (CSS)
    html_content = re.sub(r'<link[^>]*?href=["\']https?://[^"\']*["\'][^>]*/?>', '', html_content)
    
    # Remove @import statements with external URLs
    html_content = re.sub(r'@import\s+url\(["\']https?://[^)]*\);?', '', html_content)
    
    return html_content


def load_html_file(file_path):
    """
    Load HTML file from disk
    
    Args:
        file_path: Path to HTML file (absolute or relative)
    
    Returns:
        HTML content as string
    """
    try:
        # Try the path as-is first
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        
        # If not found and it's a relative path, try relative to script directory
        if not os.path.isabs(file_path):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            alt_path = os.path.join(script_dir, file_path)
            if os.path.exists(alt_path):
                with open(alt_path, 'r', encoding='utf-8') as f:
                    return f.read()
        
        # If still not found, provide detailed error
        raise FileNotFoundError(
            f"HTML file not found: '{file_path}'\n"
            f"Tried:\n"
            f"  1. {file_path} (as provided)\n"
            f"  2. {os.path.join(os.path.dirname(os.path.abspath(__file__)), file_path)} (relative to script)"
        )
    except Exception as e:
        logger.error(f"Error loading HTML file {file_path}: {str(e)}")
        raise


def send_single_email(to_email, subject, message_content, from_email=None, cc=None, bcc=None):
    """
    Send a single email using SMTP
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        message_content: Email body content
        from_email: Sender email (optional, uses default if not provided)
        cc: CC email addresses (optional, list or string)
        bcc: BCC email addresses (optional, list or string)
    
    Returns:
        dict: Status of email sending operation
    """
    try:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = from_email or DEFAULT_FROM
        message["To"] = to_email
        
        # Add CC if provided
        if cc:
            if isinstance(cc, list):
                message["Cc"] = ", ".join(cc)
            else:
                message["Cc"] = cc
        
        # Add BCC if provided
        if bcc:
            if isinstance(bcc, list):
                message["Bcc"] = ", ".join(bcc)
            else:
                message["Bcc"] = bcc
        
        message.set_content(message_content)
        
        server = smtplib.SMTP(SMTP_SERVER)
        server.send_message(message)
        server.quit()
        
        return {
            "to": to_email,
            "status": "success",
            "message": "Email sent successfully"
        }
    except Exception as e:
        return {
            "to": to_email,
            "status": "failed",
            "error": str(e)
        }


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Cloud Run"""
    return jsonify({
        "status": "healthy",
        "service": "email-service"
    }), 200


@app.route('/debug/pwd', methods=['GET'])
def debug_pwd():
    """Debug endpoint to check current working directory"""
    import os
    return jsonify({
        "current_working_directory": os.getcwd(),
        "script_directory": os.path.dirname(os.path.abspath(__file__)),
        "file_exists_absolute": os.path.exists("/Users/ANATAN8/Desktop/project/emailservicenonprod/summary21.html"),
        "file_exists_relative": os.path.exists("summary21.html"),
        "available_files": os.listdir(os.getcwd())
    }), 200


@app.route('/send-email', methods=['POST'])
def send_email():
    """
    API endpoint to send emails
    
    Expected JSON payload:
    {
        "to": "email@example.com" or ["email1@example.com", "email2@example.com"],
        "subject": "Email Subject",
        "message": "Email message content",
        "from": "sender@example.com" (optional),
        "cc": "cc@example.com" or ["cc1@example.com", "cc2@example.com"] (optional),
        "bcc": "bcc@example.com" or ["bcc1@example.com", "bcc2@example.com"] (optional)
    }
    
    Returns:
        JSON response with status of each email sent
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON data provided"
            }), 400
        
        required_fields = ["to", "subject", "message"]
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            return jsonify({
                "status": "error",
                "message": f"Missing required fields: {', '.join(missing_fields)}"
            }), 400
        
        to_emails = data["to"]
        subject = data["subject"]
        message_content = data["message"]
        from_email = data.get("from", None)
        cc_emails = data.get("cc", None)
        bcc_emails = data.get("bcc", None)
        
        # Convert single email to list
        if isinstance(to_emails, str):
            to_emails = [to_emails]
        
        # Send emails to all recipients
        results = []
        for email in to_emails:
            result = send_single_email(email, subject, message_content, from_email, cc_emails, bcc_emails)
            results.append(result)
        
        # Count successes and failures
        success_count = sum(1 for r in results if r["status"] == "success")
        failed_count = len(results) - success_count
        
        response = {
            "status": "completed",
            "total_emails": len(results),
            "successful": success_count,
            "failed": failed_count,
            "details": results
        }
        
        status_code = 200 if failed_count == 0 else 207  # 207 Multi-Status if some failed
        
        return jsonify(response), status_code
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/send-email-vdsi', methods=['POST'])
def send_email_vdsi():
    """
    API endpoint to send VDSI emails (flexible, supports multiple recipients)
    Uses vzsmtp.verizon.com SMTP server and VDSI sender address
    
    Expected JSON payload:
    {
        "to": "email@example.com" or ["email1@example.com", "email2@example.com"],
        "subject": "Email Subject",
        "message": "Email message content (supports HTML)",
        "from": "sender@example.com" (optional, uses VDSI default if not provided),
        "cc": "cc@example.com" or ["cc1@example.com", "cc2@example.com"] (optional),
        "bcc": "bcc@example.com" or ["bcc1@example.com", "bcc2@example.com"] (optional)
    }
    
    Returns:
        JSON response with status of each email sent
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON data provided"
            }), 400
        
        required_fields = ["to", "subject", "message"]
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            return jsonify({
                "status": "error",
                "message": f"Missing required fields: {', '.join(missing_fields)}"
            }), 400
        
        to_emails = data["to"]
        subject = data["subject"]
        message_content = data["message"]
        from_email = data.get("from", VDSI_FROM)
        cc_emails = data.get("cc", None)
        bcc_emails = data.get("bcc", None)
        
        # Convert single email to list
        if isinstance(to_emails, str):
            to_emails = [to_emails]
        
        results = []
        success_count = 0
        failed_count = 0
        
        try:
            # Setup SMTP connection for multiple emails
            server = smtplib.SMTP(VDSI_SMTP_SERVER, 25)
            server.set_debuglevel(0)
            
            # Send emails to all recipients
            for email in to_emails:
                try:
                    # Create message
                    message = MIMEMultipart('alternative')
                    message['Subject'] = subject
                    message['From'] = from_email
                    message['To'] = email
                    
                    # Add CC if provided
                    if cc_emails:
                        if isinstance(cc_emails, list):
                            message['Cc'] = ", ".join(cc_emails)
                        else:
                            message['Cc'] = cc_emails
                    
                    # Add BCC if provided (not typically included in headers, but for reference)
                    if bcc_emails:
                        if isinstance(bcc_emails, list):
                            message['Bcc'] = ", ".join(bcc_emails)
                        else:
                            message['Bcc'] = bcc_emails
                    
                    # Determine if message is HTML or plain text
                    if '<' in message_content and '>' in message_content:
                        # Likely HTML
                        msg_part = MIMEText(message_content, 'html')
                    else:
                        # Plain text
                        msg_part = MIMEText(message_content, 'plain')
                    
                    message.attach(msg_part)
                    
                    # Build all recipients for SMTP (including CC and BCC)
                    all_recipients = [email]
                    if cc_emails:
                        if isinstance(cc_emails, list):
                            all_recipients.extend(cc_emails)
                        else:
                            all_recipients.append(cc_emails)
                    if bcc_emails:
                        if isinstance(bcc_emails, list):
                            all_recipients.extend(bcc_emails)
                        else:
                            all_recipients.append(bcc_emails)
                    
                    # Send email
                    server.send_message(message)
                    
                    logger.info(f"Email sent to {email}")
                    results.append({
                        "to": email,
                        "status": "success",
                        "message": "Email sent successfully"
                    })
                    success_count += 1
                    
                except Exception as e:
                    logger.error(f"Error sending email to {email}: {str(e)}")
                    results.append({
                        "to": email,
                        "status": "failed",
                        "error": str(e)
                    })
                    failed_count += 1
            
            # Close SMTP connection
            server.quit()
            
            response = {
                "status": "completed",
                "total_emails": len(to_emails),
                "successful": success_count,
                "failed": failed_count,
                "details": results
            }
            
            status_code = 200 if failed_count == 0 else 207  # 207 Multi-Status if some failed
            
            return jsonify(response), status_code
            
        except smtplib.SMTPException as e:
            logger.error(f"SMTP Error: {str(e)}")
            return jsonify({
                "status": "error",
                "message": f"SMTP connection error: {str(e)}"
            }), 500
            
    except Exception as e:
        logger.error(f"Error in send_email_vdsi: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/send-email-dont-reply', methods=['POST'])
def send_email_dont_reply():
    """
    API endpoint to send emails from do-not-reply account
    Uses tpaapd1kva109.verizon.com SMTP server with do-not-reply@verizon.com sender
    
    Expected JSON payload:
    {
        "to": "email@verizon.com" or ["email1@verizon.com", "email2@verizon.com"]
              or "email1@verizon.com, email2@verizon.com, email3@verizon.com",
        "subject": "Email Subject",
        "message": "Email content (supports HTML)" OR "html_content": "HTML as string",
        "from": "do-not-reply@verizon.com" (optional, defaults to do-not-reply@verizon.com),
        "send_all_at_once": true (optional, default false — sends one email with all recipients in To),
        "cc": "cc@verizon.com" or ["cc1@verizon.com", "cc2@verizon.com"] (optional),
        "bcc": "bcc@verizon.com" or ["bcc1@verizon.com", "bcc2@verizon.com"] (optional)
    }
    
    OR FormData with file upload:
    - to: email address(es) — supports "Display Name <email>" format
    - subject: email subject
    - html_file: HTML file as email body (optional)
    - message: plain text or HTML message (optional, used if html_file not provided)
    - attachments: single or multiple file attachments (optional)
    - send_all_at_once: "true" to send one email with all recipients in To (optional, default false)
    - cc: (optional)
    - bcc: (optional)
    
    Note: In FormData mode, provide either html_file or message (form field) for the
    email body. The 'attachments' field accepts one or more files that will be sent
    as email attachments. Use the same field name 'attachments' for multiple files.
    
    Returns:
        JSON response with status of each email sent
    """
    try:
        message_content = None
        base_path = os.path.dirname(os.path.abspath(__file__))
        attachment_files = []  # List of (filename, file_data, content_type) tuples
        
        # Check if it's multipart form data (file upload)
        if request.method == 'POST' and (request.files or request.content_type and 'multipart/form-data' in request.content_type):
            # Handle file upload
            data = {}
            
            # Parse form data and strip quotes from values
            for key in request.form:
                # Use getlist to support multiple values for cc/bcc fields
                values = request.form.getlist(key)
                if len(values) > 1:
                    # Multiple form fields with the same name (e.g., bcc=a@x.com&bcc=b@x.com)
                    cleaned = []
                    for v in values:
                        v = v.strip().strip('"').strip("'")
                        # Also split by comma in case each value has multiple emails
                        cleaned.extend([e.strip() for e in v.split(",") if e.strip()])
                    data[key] = cleaned
                else:
                    value = values[0]
                    # Remove surrounding quotes if present
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    # Check if comma-separated (multiple emails in one field)
                    if key in ("bcc", "cc", "to") and "," in value:
                        data[key] = [e.strip() for e in value.split(",") if e.strip()]
                    else:
                        data[key] = value
            
            # Read html_file for email body (optional now)
            if 'html_file' in request.files:
                file = request.files['html_file']
                if file.filename != '':
                    try:
                        message_content = file.read().decode('utf-8')
                        logger.info(f"Loaded HTML from uploaded file: {file.filename} for {data.get('to')}")
                    except Exception as e:
                        logger.error(f"Error reading uploaded file: {str(e)}")
                        return jsonify({
                            "status": "error",
                            "message": f"Failed to read uploaded HTML file: {str(e)}"
                        }), 400
            
            # If no html_file, check for 'message' in form fields
            if message_content is None:
                if "message" in data:
                    message_content = data["message"]
                elif "html_content" in data:
                    message_content = data["html_content"]
            
            # Validate that we have some email body content
            if message_content is None:
                return jsonify({
                    "status": "error",
                    "message": "No email body provided. Include 'html_file', 'message', or 'html_content' in your request."
                }), 400
            
            # Collect file attachments (supports single and multiple files)
            # Field name: 'attachments' (can appear multiple times for multiple files)
            uploaded_attachments = request.files.getlist('attachments')
            for att in uploaded_attachments:
                if att.filename and att.filename != '':
                    try:
                        file_data = att.read()
                        content_type = att.content_type or 'application/octet-stream'
                        attachment_files.append((att.filename, file_data, content_type))
                        logger.info(f"Attachment collected: {att.filename} ({content_type}, {len(file_data)} bytes)")
                    except Exception as e:
                        logger.error(f"Error reading attachment {att.filename}: {str(e)}")
                        return jsonify({
                            "status": "error",
                            "message": f"Failed to read attachment '{att.filename}': {str(e)}"
                        }), 400
            
            # Also support 'attachment' (singular) field name for convenience
            uploaded_single = request.files.getlist('attachment')
            for att in uploaded_single:
                if att.filename and att.filename != '':
                    try:
                        file_data = att.read()
                        content_type = att.content_type or 'application/octet-stream'
                        attachment_files.append((att.filename, file_data, content_type))
                        logger.info(f"Attachment collected: {att.filename} ({content_type}, {len(file_data)} bytes)")
                    except Exception as e:
                        logger.error(f"Error reading attachment {att.filename}: {str(e)}")
                        return jsonify({
                            "status": "error",
                            "message": f"Failed to read attachment '{att.filename}': {str(e)}"
                        }), 400
            
            logger.info(f"Total attachments: {len(attachment_files)}")
        else:
            # Handle JSON request
            data = request.get_json()
            
            if not data:
                return jsonify({
                    "status": "error",
                    "message": "No JSON data provided"
                }), 400
            
            # Check for html_content (direct HTML string)
            if "html_content" in data:
                message_content = data["html_content"]
            elif "message" in data:
                message_content = data["message"]
            else:
                return jsonify({
                    "status": "error",
                    "message": "Missing required field: 'message' or 'html_content'"
                }), 400
        
        # Validate required fields
        to_emails = data.get("to")
        subject = data.get("subject", "Email from System")
        
        if not to_emails:
            return jsonify({
                "status": "error",
                "message": "Missing required field: 'to'"
            }), 400
        
        # Parse send_all_at_once flag
        send_all_at_once = False
        raw_flag = data.get("send_all_at_once", False)
        if isinstance(raw_flag, bool):
            send_all_at_once = raw_flag
        elif isinstance(raw_flag, str):
            send_all_at_once = raw_flag.strip().lower() in ("true", "1", "yes", "on")
        
        # Clean and validate email addresses
        if isinstance(to_emails, str):
            # Strip whitespace and quotes, split by comma
            to_emails = to_emails.strip().strip('"').strip("'")
            to_emails = [e.strip() for e in to_emails.split(",") if e.strip()]
        elif isinstance(to_emails, list):
            to_emails = [email.strip().strip('"').strip("'") if isinstance(email, str) else str(email) for email in to_emails]
        
        # Ensure all items are strings before regex validation
        to_emails = [str(e) for e in to_emails if e is not None]
        
        # Validate email format
        import re
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        invalid_emails = [email for email in to_emails if not re.match(email_pattern, email)]
        
        if invalid_emails:
            logger.error(f"Invalid email addresses detected: {invalid_emails}")
            return jsonify({
                "status": "error",
                "message": f"Invalid email address format: {', '.join(invalid_emails)}",
                "debug": {
                    "received_to": data.get("to"),
                    "parsed_emails": to_emails,
                    "invalid_emails": invalid_emails
                }
            }), 400
        
        logger.info(f"Validated email addresses: {to_emails} (send_all_at_once={send_all_at_once})")
        
        from_email = data.get("from", DO_NOT_REPLY_FROM)
        cc_emails = data.get("cc", None)
        bcc_emails = data.get("bcc", None)
        
        # Clean CC emails if provided
        if cc_emails:
            if isinstance(cc_emails, str):
                cc_emails = cc_emails.strip().strip('"').strip("'")
            elif isinstance(cc_emails, list):
                cc_emails = [email.strip().strip('"').strip("'") if isinstance(email, str) else email for email in cc_emails]
        
        # Clean BCC emails if provided
        if bcc_emails:
            if isinstance(bcc_emails, str):
                bcc_emails = bcc_emails.strip().strip('"').strip("'")
            elif isinstance(bcc_emails, list):
                bcc_emails = [email.strip().strip('"').strip("'") if isinstance(email, str) else email for email in bcc_emails]
        
        # Process HTML content: embed images and remove external resources
        is_html = '<' in message_content and '>' in message_content
        if is_html:
            message_content = inline_external_css(message_content)
            message_content = embed_images_in_html(message_content, base_path)
        
        results = []
        success_count = 0
        failed_count = 0
        
        try:
            # Setup SMTP connection for multiple emails
            server = smtplib.SMTP(VDSI_SMTP_SERVER, 25)
            server.set_debuglevel(1)  # Enable debug output
            
            logger.info(f"SMTP connection established. Sending to: {to_emails} (send_all_at_once={send_all_at_once})")
            
            def _build_message_body():
                """Build the email body (shared between single and batch send)."""
                if is_html:
                    body_part = MIMEMultipart('alternative')
                    plain_part = MIMEText(
                        "This is an HTML email. Please view in an HTML-capable email client.",
                        'plain'
                    )
                    body_part.attach(plain_part)
                    html_part = MIMEText(message_content, 'html')
                    body_part.attach(html_part)
                else:
                    body_part = MIMEMultipart('alternative')
                    msg_part = MIMEText(message_content, 'plain')
                    body_part.attach(msg_part)
                
                if attachment_files:
                    msg = MIMEMultipart('mixed')
                    msg.attach(body_part)
                    for filename, file_data, content_type in attachment_files:
                        maintype, _, subtype = content_type.partition('/')
                        if not subtype:
                            maintype = 'application'
                            subtype = 'octet-stream'
                        att_part = MIMEBase(maintype, subtype)
                        att_part.set_payload(file_data)
                        from email import encoders
                        encoders.encode_base64(att_part)
                        att_part.add_header('Content-Disposition', 'attachment', filename=filename)
                        msg.attach(att_part)
                        logger.info(f"Attached file: {filename} ({content_type})")
                else:
                    msg = body_part
                return msg
            
            if send_all_at_once:
                # ── Send one email with ALL recipients in the To field ──
                try:
                    message = _build_message_body()
                    message['Subject'] = subject
                    message['From'] = from_email
                    message['To'] = ", ".join(to_emails)
                    
                    if cc_emails:
                        if isinstance(cc_emails, list):
                            message['Cc'] = ", ".join(cc_emails)
                        else:
                            message['Cc'] = cc_emails
                    
                    if bcc_emails:
                        if isinstance(bcc_emails, list):
                            message['Bcc'] = ", ".join(bcc_emails)
                        else:
                            message['Bcc'] = bcc_emails
                    
                    logger.info(f"Sending single email to all {len(to_emails)} recipients at once")
                    server.send_message(message)
                    
                    logger.info(f"Do-Not-Reply email sent to all recipients: {to_emails}")
                    results.append({
                        "to": to_emails,
                        "status": "success",
                        "message": f"Email sent to all {len(to_emails)} recipients at once"
                    })
                    success_count = len(to_emails)
                    
                except Exception as e:
                    logger.error(f"Error sending batch email: {str(e)}")
                    results.append({
                        "to": to_emails,
                        "status": "failed",
                        "error": str(e)
                    })
                    failed_count = len(to_emails)
            else:
                # ── Send individual emails to each recipient (existing behavior) ──
                for email_addr in to_emails:
                    try:
                        logger.info(f"Preparing email for: {email_addr}")
                        
                        message = _build_message_body()
                        message['Subject'] = subject
                        message['From'] = from_email
                        message['To'] = email_addr
                        
                        logger.info(f"Message headers - From: {from_email}, To: {email_addr}, Subject: {subject}")
                        
                        if cc_emails:
                            if isinstance(cc_emails, list):
                                message['Cc'] = ", ".join(cc_emails)
                            else:
                                message['Cc'] = cc_emails
                        
                        if bcc_emails:
                            if isinstance(bcc_emails, list):
                                message['Bcc'] = ", ".join(bcc_emails)
                            else:
                                message['Bcc'] = bcc_emails
                        
                        logger.info(f"Attempting to send email to {email_addr} (attachments: {len(attachment_files)})")
                        server.send_message(message)
                        
                        logger.info(f"Do-Not-Reply email sent successfully to {email_addr}")
                        results.append({
                            "to": email_addr,
                            "status": "success",
                            "message": "Email sent successfully"
                        })
                        success_count += 1
                        
                    except Exception as e:
                        logger.error(f"Error sending do-not-reply email to {email_addr}: {str(e)}")
                        results.append({
                            "to": email_addr,
                            "status": "failed",
                            "error": str(e)
                        })
                        failed_count += 1
            
            # Close SMTP connection
            server.quit()
            
            response = {
                "status": "completed",
                "send_all_at_once": send_all_at_once,
                "total_recipients": len(to_emails),
                "successful": success_count,
                "failed": failed_count,
                "details": results
            }
            
            status_code = 200 if failed_count == 0 else 207  # 207 Multi-Status if some failed
            
            return jsonify(response), status_code
            
        except smtplib.SMTPException as e:
            logger.error(f"SMTP Error: {str(e)}")
            return jsonify({
                "status": "error",
                "message": f"SMTP connection error: {str(e)}"
            }), 500
            
    except Exception as e:
        logger.error(f"Error in send_email_dont_reply: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/ays-proxy', methods=['POST'])
def ays_proxy():
    """Relay AYS API requests through this service using Verizon proxy egress.

    Expected JSON payload:
    {
      "method": "POST",
      "url": "https://atyourservice.verizon.com/api/verw2/v1/standard/incidents",
      "headers": {"Authorization": "Basic ...", "Content-Type": "application/json"},
      "json": {...},
      "timeout": 30
    }
    """
    try:
        # Optional protection: set AYS_PROXY_TOKEN in Cloud Run and send bearer token.
        required_token = os.environ.get("AYS_PROXY_TOKEN", "").strip()
        if required_token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {required_token}":
                return jsonify({"status": "error", "message": "Unauthorized"}), 401

        data = request.get_json(silent=True) or {}
        method = str(data.get("method", "POST")).upper().strip()
        url = str(data.get("url", "")).strip()
        headers = data.get("headers", {}) or {}
        timeout = int(data.get("timeout", 30))

        if not url:
            return jsonify({"status": "error", "message": "Missing 'url'"}), 400
        if "atyourservice.verizon.com" not in url:
            return jsonify({"status": "error", "message": "Only atyourservice.verizon.com is allowed"}), 400

        req_kwargs = {
            "headers": headers,
            "timeout": timeout,
            "proxies": {
                "http": os.environ.get("HTTP_PROXY", "http://proxy.ebiz.verizon.com:80/"),
                "https": os.environ.get("HTTPS_PROXY", "http://proxy.ebiz.verizon.com:80/"),
            },
        }
        if "json" in data:
            req_kwargs["json"] = data.get("json")
        elif "data" in data:
            req_kwargs["data"] = data.get("data")

        resp = http_requests.request(method, url, **req_kwargs)
        content_type = resp.headers.get("Content-Type", "application/json")
        return app.response_class(response=resp.content, status=resp.status_code, mimetype=content_type)
    except Exception as e:
        logger.error(f"ays_proxy failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/fpas-explore', methods=['POST'])
def fpas_explore():
    """Explore FPAS BigQuery tables for schema and sample data.

    Payload:
    {
        "action": "schema" | "sample" | "query",
        "table": "tspot_vzt_fact_oneex_v" | "tspot_vzw_fact_one_ex_summary_cv_forecast_v" | "tspot_vzw_fact_one_ex_summary_v" | "all",
        "limit": 10,           // for action=sample (default 10, max 100)
        "sql": "SELECT ..."    // for action=query (custom SQL)
    }

    table aliases accepted: "vzt", "forecast", "summary" (shorthand)
    """
    _FPAS_PROJECT = "vz-nonit-np-k65v-dev-fpardo-0"
    _FPAS_DATASET = "fpas_dm_nonsec"
    _FPAS_TABLES = {
        "tspot_vzt_fact_oneex_v":                       f"{_FPAS_PROJECT}.{_FPAS_DATASET}.tspot_vzt_fact_oneex_v",
        "tspot_vzw_fact_one_ex_summary_cv_forecast_v":  f"{_FPAS_PROJECT}.{_FPAS_DATASET}.tspot_vzw_fact_one_ex_summary_cv_forecast_v",
        "tspot_vzw_fact_one_ex_summary_v":              f"{_FPAS_PROJECT}.{_FPAS_DATASET}.tspot_vzw_fact_one_ex_summary_v",
        # shorthand aliases
        "vzt":      f"{_FPAS_PROJECT}.{_FPAS_DATASET}.tspot_vzt_fact_oneex_v",
        "forecast": f"{_FPAS_PROJECT}.{_FPAS_DATASET}.tspot_vzw_fact_one_ex_summary_cv_forecast_v",
        "summary":  f"{_FPAS_PROJECT}.{_FPAS_DATASET}.tspot_vzw_fact_one_ex_summary_v",
    }

    try:
        data = request.get_json(silent=True) or {}
        action = str(data.get("action", "schema")).lower().strip()
        table_key = str(data.get("table", "all")).strip()
        limit = min(int(data.get("limit", 10)), 100)
        custom_sql = data.get("sql", "").strip()

        fp_client = bigquery.Client(project=_FPAS_PROJECT)

        # ── action=query ──────────────────────────────────────────
        if action == "query":
            if not custom_sql:
                return jsonify({"status": "error", "message": "Provide 'sql' for action=query"}), 400
            job = fp_client.query(custom_sql)
            rows = [dict(r) for r in job.result()]
            # Convert non-serialisable types (date, Decimal, etc.)
            import datetime, decimal
            def _serialize(obj):
                if isinstance(obj, (datetime.date, datetime.datetime)):
                    return obj.isoformat()
                if isinstance(obj, decimal.Decimal):
                    return float(obj)
                return str(obj)
            import json as _json
            rows_clean = _json.loads(_json.dumps(rows, default=_serialize))
            return jsonify({"status": "ok", "action": "query", "row_count": len(rows_clean), "rows": rows_clean}), 200

        # ── resolve tables ────────────────────────────────────────
        if table_key == "all":
            tables_to_process = list({v for k, v in _FPAS_TABLES.items() if k not in ("vzt", "forecast", "summary")})
        elif table_key in _FPAS_TABLES:
            tables_to_process = [_FPAS_TABLES[table_key]]
        else:
            return jsonify({"status": "error",
                            "message": f"Unknown table '{table_key}'. Use: vzt, forecast, summary, or full table name."}), 400

        results = {}
        for full_table in tables_to_process:
            short_name = full_table.split(".")[-1]
            try:
                if action == "schema":
                    tbl_ref = fp_client.get_table(full_table)
                    schema = [
                        {
                            "name": f.name,
                            "type": f.field_type,
                            "mode": f.mode,
                            "description": f.description or "",
                        }
                        for f in tbl_ref.schema
                    ]
                    results[short_name] = {
                        "full_table": full_table,
                        "num_rows": tbl_ref.num_rows,
                        "num_bytes": tbl_ref.num_bytes,
                        "schema": schema,
                    }
                elif action == "sample":
                    job = fp_client.query(f"SELECT * FROM `{full_table}` LIMIT {limit}")
                    rows = [dict(r) for r in job.result()]
                    import datetime, decimal, json as _json
                    def _serialize(obj):
                        if isinstance(obj, (datetime.date, datetime.datetime)):
                            return obj.isoformat()
                        if isinstance(obj, decimal.Decimal):
                            return float(obj)
                        return str(obj)
                    rows_clean = _json.loads(_json.dumps(rows, default=_serialize))
                    results[short_name] = {
                        "full_table": full_table,
                        "row_count": len(rows_clean),
                        "rows": rows_clean,
                    }
                else:
                    return jsonify({"status": "error", "message": f"Unknown action '{action}'. Use: schema, sample, query"}), 400
            except Exception as tbl_err:
                results[short_name] = {"error": str(tbl_err)}

        return jsonify({"status": "ok", "action": action, "results": results}), 200

    except Exception as e:
        logger.error(f"fpas_explore failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/', methods=['GET'])
def root():
    """Root endpoint with API information"""
    return jsonify({
        "service": "Email Service API",
        "version": "1.0",
        "endpoints": {
            "/health": "GET - Health check",
            "/send-email": "POST - Send email(s)",
            "/send-email-vdsi": "POST - Send VDSI nomination emails",
            "/send-email-dont-reply": "POST - Send do-not-reply emails",
            "/ays-proxy": "POST - Relay AYS API requests",
            "/fpas-explore": "POST - Explore FPAS BigQuery tables (schema/sample/query)",
            "/": "GET - API information"
        },
        "usage": {
            "send-email": {
                "endpoint": "/send-email",
                "method": "POST",
                "payload": {
                    "to": "email@example.com or [email1, email2]",
                    "subject": "Your subject",
                    "message": "Your message content",
                    "from": "sender@example.com (optional)",
                    "cc": "cc@example.com or [cc1, cc2] (optional)",
                    "bcc": "bcc@example.com or [bcc1, bcc2] (optional)"
                }
            },
            "send-email-vdsi": {
                "endpoint": "/send-email-vdsi",
                "method": "POST",
                "description": "VDSI email service - supports multiple recipients, CC, BCC with HTML content",
                "payload": {
                    "to": "email@example.com or [email1@example.com, email2@example.com]",
                    "subject": "Email Subject",
                    "message": "Email content (supports HTML)",
                    "from": "sender@example.com (optional, defaults to Persona Device Distribution)",
                    "cc": "cc@example.com or [cc1@example.com, cc2@example.com] (optional)",
                    "bcc": "bcc@example.com or [bcc1@example.com, bcc2@example.com] (optional)"
                }
            },
            "send-email-dont-reply": {
                "endpoint": "/send-email-dont-reply",
                "method": "POST",
                "description": "Do-Not-Reply email service - for automated/system notifications with HTML content support",
                "options": [
                    "Option 1: Send HTML as string in JSON",
                    "Option 2: Upload HTML file as FormData attachment"
                ],
                "json_payload": {
                    "to": "email@verizon.com or [email1@verizon.com, email2@verizon.com]",
                    "subject": "Email Subject",
                    "message": "Plain text email content (optional if using html_content)",
                    "html_content": "Raw HTML string (optional, alternative to message)",
                    "from": "do-not-reply@verizon.com (optional)",
                    "send_all_at_once": "true to send one email with all recipients in To field (optional, default false)",
                    "cc": "cc@verizon.com or [cc1@verizon.com, cc2@verizon.com] (optional)",
                    "bcc": "bcc@verizon.com or [bcc1@verizon.com, bcc2@verizon.com] (optional)"
                },
                "formdata_payload": {
                    "to": "email@verizon.com",
                    "subject": "Email Subject",
                    "html_file": "HTML file as email body (optional, upload as file in FormData)",
                    "message": "Plain text or HTML message (optional, used if html_file not provided)",
                    "attachments": "Single or multiple file attachments (use same field name for multiple files)",
                    "cc": "(optional)",
                    "bcc": "(optional)"
                },
                "features": [
                    "Send plain text or HTML emails",
                    "Send HTML as string in JSON body (html_content)",
                    "Upload HTML file directly (FormData with html_file)",
                    "Single and multiple file attachments via FormData (field: 'attachments' or 'attachment')",
                    "Automatically embeds images as base64",
                    "Removes external CDN resources for email compatibility",
                    "Preserves all formatting and styling"
                ]
            }
        }
    }), 200


# ──────────────────────────────────────────────────────────────────
# DIALOGFLOW CX WEBHOOK — Citrix KB Article Lookup
# ──────────────────────────────────────────────────────────────────
# BigQuery table containing KB articles
BQ_PROJECT = "vz-it-np-voev-dev-voevdo-0"
BQ_TABLE = f"{BQ_PROJECT}.empan_dev_tbls.vea_sn_kb_knowledge"

# The columns we extract from each KB article
KB_COLUMNS = [
    "number",                     # KB article number (e.g., kb0031644)
    "short_description",          # One-line summary — key for LLM matching
    "kb_snippet",                 # Brief snippet for quick display
    "kb_steps",                   # Step-by-step resolution instructions
    "text",                       # Full article body (plain text)
    "u_portal_url",               # Portal URL — clickable link to the KB article
    "kb_category_display_value",  # Category (filtered for 'citrix')
    "kb_knowledge_base_display_value",  # Knowledge base type (e.g., 'Tech KB' = admin-only)
]

# Initialize BigQuery client
bq_client = bigquery.Client(project=BQ_PROJECT)

# ── Landing-page KB scope (Approach A) ─────────────────────────
# KB0020868 is the Citrix landing page whose hyperlink_embedded_text
# field contains links to all curated Citrix-related KB articles.
# We parse those links once at startup, cache the KB numbers, and
# restrict all subsequent searches to that set.
LANDING_PAGE_KB = "KB0020868"
_CITRIX_KB_NUMBERS: list[str] = []          # populated by _load_citrix_kb_scope()


def _load_citrix_kb_scope() -> list[str]:
    """
    Query the landing-page article (KB0020868) and extract every
    KB number referenced in its hyperlink_embedded_text field.
    Returns a de-duplicated, sorted list of KB numbers (upper-case).
    The landing page itself is included in the list.
    """
    try:
        query = f"""
            SELECT hyperlink_embedded_text
            FROM `{BQ_TABLE}`
            WHERE UPPER(number) = @landing_kb
            LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("landing_kb", "STRING", LANDING_PAGE_KB),
            ]
        )
        rows = list(bq_client.query(query, job_config=job_config).result())

        if not rows:
            logger.warning(f"Landing page {LANDING_PAGE_KB} not found in BigQuery — falling back to empty scope")
            return []

        hyperlink_text = str(rows[0].hyperlink_embedded_text or "")
        # Extract all KB numbers (e.g. KB0018249) from the HTML/text
        kb_numbers = re.findall(r"KB\d{7}", hyperlink_text, re.IGNORECASE)
        # Normalise to upper-case and de-duplicate
        kb_set = {kb.upper() for kb in kb_numbers}
        # Always include the landing page itself
        kb_set.add(LANDING_PAGE_KB)
        kb_list = sorted(kb_set)

        logger.info(
            f"Citrix KB scope loaded: {len(kb_list)} articles from {LANDING_PAGE_KB} "
            f"(e.g. {kb_list[:5]})"
        )
        return kb_list

    except Exception as e:
        logger.error(f"Failed to load Citrix KB scope from {LANDING_PAGE_KB}: {e}")
        return []


# Supplemental KB articles not on landing page but required per client feedback.
# These are merged into the scope alongside the landing-page articles.
_SUPPLEMENTAL_KB_NUMBERS: list[str] = [
    "KB0029130",   # How to check latency in Citrix VM
    "KB0011171",   # How to install/remove/manage software on Desktop
    "KB0029924",   # Citrix performance / slowness
    "KB0032449",   # Citrix VM performance troubleshooting
    "KB0014975",   # Self-service / self-help tools
    "KB0012981",   # How to restart/reboot Citrix VM (self-help)
]

# Load the scope once at import / startup
_CITRIX_KB_NUMBERS = _load_citrix_kb_scope()

# Merge supplemental KBs into the scope
for _kb in _SUPPLEMENTAL_KB_NUMBERS:
    if _kb not in _CITRIX_KB_NUMBERS:
        _CITRIX_KB_NUMBERS.append(_kb)
_CITRIX_KB_NUMBERS.sort()
logger.info(f"Final Citrix KB scope: {len(_CITRIX_KB_NUMBERS)} articles (incl. {len(_SUPPLEMENTAL_KB_NUMBERS)} supplemental)")


# ── Synonym expansion & stop words for search ──────────────────
# Maps a user term to additional terms that should also be searched.
# If the user says "onboard", we also search for "add", "create", etc.
SYNONYM_MAP: dict[str, list[str]] = {
    # ── Citrix-domain identity terms ─────────────────────────────
    # The bot is exclusively for Citrix, so "virtual desktop", "VM",
    # "dcloud", "desktop" all imply Citrix context.
    "virtual":    ["citrix", "desktop", "vdi", "dcloud", "workspace", "mydcloud"],
    "desktop":    ["citrix", "virtual", "vdi", "dcloud", "workspace", "machine", "mydcloud"],
    "dcloud":     ["citrix", "virtual", "desktop", "vdi", "workspace", "mydcloud", "odc", "onprem", "on-prem", "remote"],
    "vdi":        ["citrix", "virtual", "desktop", "dcloud", "workspace", "mydcloud"],
    "workspace":  ["citrix", "virtual", "desktop", "vdi", "dcloud", "mydcloud"],
    "mydcloud":   ["citrix", "dcloud", "desktop", "onprem", "on-prem", "odc"],
    # ── User / account management ────────────────────────────────
    "onboard":    ["add", "create", "setup", "provision", "new", "register", "enroll", "request"],
    "onboarding": ["add", "create", "setup", "provision", "new", "register", "enroll", "request"],
    "offboard":   ["remove", "delete", "disable", "deactivate", "decommission"],
    "offboarding":["remove", "delete", "disable", "deactivate", "decommission"],
    "provision":  ["add", "create", "setup", "onboard", "new", "enroll", "request", "frontdoor"],
    "register":   ["add", "create", "enroll", "onboard", "provision"],
    "enroll":     ["add", "create", "register", "onboard", "provision"],
    "user":       ["account", "employee", "member", "access"],
    "project":    ["request", "frontdoor", "provision", "new", "onboard", "setup"],
    # ── Troubleshooting ──────────────────────────────────────────
    "fix":        ["resolve", "repair", "troubleshoot", "solution", "workaround"],
    "broken":     ["not working", "error", "fail", "issue", "problem"],
    "slow":       ["performance", "lag", "latency", "delay", "hang", "freeze", "speed"],
    "performance":["slow", "lag", "latency", "delay", "speed", "optimize"],
    "latency":    ["slow", "lag", "performance", "delay", "speed", "response time"],
    "lag":        ["slow", "latency", "performance", "delay"],
    "crash":      ["freeze", "hang", "stop", "unresponsive", "not responding"],
    "freeze":     ["crash", "hang", "unresponsive", "stuck", "not responding"],
    "error":      ["fail", "issue", "problem", "broken"],
    # ── Access / login ───────────────────────────────────────────
    "login":      ["log in", "sign in", "authenticate", "access", "connect", "remote", "odc", "onprem", "mydcloud"],
    "logon":      ["log on", "sign in", "authenticate", "access", "connect", "remote", "odc", "onprem", "mydcloud"],
    "signin":     ["sign in", "login", "logon", "authenticate", "access"],
    "access":     ["login", "connect", "authenticate", "remote", "odc", "onprem", "on-prem", "mydcloud", "vendor"],
    "password":   ["credential", "passcode", "reset password"],
    "mfa":        ["multi-factor", "two-factor", "2fa", "authentication"],
    "started":    ["start", "begin", "getting started", "launch", "first time", "setup"],
    # ── Location / remote access ─────────────────────────────────
    "vendor":     ["remote", "odc", "off-domain", "external", "contractor", "third-party", "onprem", "on-prem", "mydcloud"],
    "odc":        ["off-domain", "remote", "vendor", "external", "contractor", "mydcloud", "onprem"],
    "onprem":     ["on-prem", "on premises", "mydcloud", "internal", "office", "campus"],
    "remote":     ["odc", "off-domain", "vendor", "external", "vpn", "offsite"],
    "location":   ["remote", "onprem", "on-prem", "odc", "vendor", "office", "campus", "offsite"],
    "external":   ["remote", "odc", "vendor", "off-domain", "contractor"],
    # ── Installation / software ──────────────────────────────────
    "install":    ["setup", "deploy", "download", "configure", "software", "manage"],
    "uninstall":  ["remove", "delete", "cleanup"],
    "setup":      ["install", "configure", "provision", "deploy"],
    "configure":  ["setup", "settings", "config"],
    "software":   ["install", "application", "app", "program", "manage", "remove"],
    "application":["software", "app", "program", "install"],
    # ── Connectivity / VM operations ─────────────────────────────
    "disconnect": ["connection", "drop", "timeout", "lost", "reconnect"],
    "vpn":        ["network", "remote", "connection", "tunnel"],
    "network":    ["connectivity", "connection", "vpn", "internet"],
    "restart":    ["reboot", "power cycle", "self-service", "self service", "self help"],
    "reboot":     ["restart", "power cycle", "self-service", "self service"],
    "reset":      ["restart", "reboot", "power cycle"],
    # ── Printing / peripherals ───────────────────────────────────
    "print":      ["printer", "printing", "cups"],
    "printer":    ["print", "printing", "cups"],
    "drive":      ["disk", "storage", "mapped", "cdrive", "c-drive", "local"],
    "monitor":    ["display", "screen", "resolution", "dual"],
    "display":    ["monitor", "screen", "resolution"],
    # ── General / request / guide ────────────────────────────────
    "request":    ["provision", "new", "order", "submit", "frontdoor"],
    "guide":      ["documentation", "instructions", "steps", "manual", "how-to", "user guide"],
    "documentation": ["guide", "instructions", "steps", "manual", "how-to"],
    "update":     ["upgrade", "patch", "version"],
    "upgrade":    ["update", "patch", "version", "migrate"],
    "launch":     ["start", "open", "run", "execute", "fail"],
    "open":       ["launch", "start", "access"],
    "fails":      ["fail", "error", "crash", "not working", "broken", "issue"],
    "fail":       ["error", "crash", "not working", "broken", "issue"],
}

# Words that carry no meaningful search signal — removed from queries
STOP_WORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it", "they",
    "this", "that", "these", "those",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "into",
    "about", "between", "through", "during", "after", "before",
    "and", "or", "but", "not", "no", "nor", "so", "if", "then",
    "how", "what", "when", "where", "who", "which", "why",
    "am", "get", "got", "like", "just", "also", "very", "too",
    "up", "out", "all", "any", "each", "some",
    "its", "him", "her", "them", "us",
    "please", "help", "want", "trying", "try", "able",
    "tell", "know", "give", "show", "find", "look",
    "thing", "way", "work", "make", "use",
}


def _expand_search_terms(raw_query: str) -> list[str]:
    """
    Turn a user query into an expanded, de-duplicated list of search terms.
    1. Tokenise & lowercase
    2. Remove stop words and very short words (≤ 2 chars)
    3. Expand each token via SYNONYM_MAP
    4. Return unique terms (preserving original-first order)
    """
    tokens = [t.strip() for t in raw_query.lower().split()]
    # Keep meaningful words only
    meaningful = [t for t in tokens if len(t) > 2 and t not in STOP_WORDS]

    if not meaningful:
        # If everything got filtered, keep the raw query as-is
        meaningful = [raw_query.lower().strip()]

    # Expand with synonyms
    expanded: list[str] = []
    seen: set[str] = set()
    for term in meaningful:
        if term not in seen:
            expanded.append(term)
            seen.add(term)
        # Add synonyms for this term
        for syn in SYNONYM_MAP.get(term, []):
            syn_lower = syn.lower()
            if syn_lower not in seen:
                expanded.append(syn_lower)
                seen.add(syn_lower)

    return expanded


def _ai_expand_search_terms(raw_query: str) -> list[str]:
    """
    Use Gemini to intelligently extract search keywords from a user query.
    Returns a list of precise, contextually-relevant search terms.
    Falls back to static synonym expansion if Gemini is unavailable.
    """
    if not _gemini_model:
        return _expand_search_terms(raw_query)

    prompt = (
        "You are a search-term extraction engine for a Citrix Virtual Desktop IT support knowledge base.\n\n"
        "Given the user's query below, extract 5-10 precise search keywords/phrases that should be used "
        "to find the correct KB article in a database. Focus on the ACTUAL intent — do NOT confuse "
        "similar-sounding but different concepts.\n\n"
        "IMPORTANT DISTINCTIONS:\n"
        "- 'restart VM' / 'reboot VM' = power cycling a virtual machine (NOT password reset)\n"
        "- 'reset password' = changing/recovering login credentials (NOT restarting a machine)\n"
        "- 'install software' = adding applications (NOT provisioning a new VM)\n"
        "- 'onboard user' = creating new account/access (NOT troubleshooting)\n\n"
        "Return ONLY a JSON array of lowercase strings. No explanation.\n"
        "Example: [\"restart\", \"reboot\", \"virtual machine\", \"power cycle\", \"citrix vm\"]\n\n"
        f"User query: \"{raw_query}\""
    )

    try:
        response = _gemini_model.generate_content(prompt)
        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        import json as _json
        terms = _json.loads(text)
        if isinstance(terms, list) and all(isinstance(t, str) for t in terms):
            # Merge with original meaningful tokens to ensure we keep the user's words
            tokens = [t.strip().lower() for t in raw_query.lower().split()]
            meaningful = [t for t in tokens if len(t) > 2 and t not in STOP_WORDS]
            seen = set()
            merged = []
            for t in meaningful + terms:
                t_lower = t.lower().strip()
                if t_lower and t_lower not in seen:
                    merged.append(t_lower)
                    seen.add(t_lower)
            logger.info(f"AI-expanded search terms: {merged}")
            return merged
        logger.warning(f"Gemini returned non-list for search terms: {text[:200]}")
    except Exception as e:
        logger.warning(f"AI search term expansion failed (falling back to synonyms): {e}")

    return _expand_search_terms(raw_query)


def fetch_citrix_kb_articles(search_query=None):
    """
    Query BigQuery for Citrix-related KB articles.

    Article scope is determined by the landing page KB0020868: only articles
    whose KB number appears in that page's hyperlink_embedded_text (plus the
    landing page itself) are searched.  If the landing-page scope failed to
    load at startup, falls back to the old category-based filter.

    Search terms are expanded using AI (Gemini) for semantic understanding,
    with a fallback to static synonym expansion if AI is unavailable.

    If a search_query is provided, results are ranked by relevance
    (matches in short_description, kb_snippet, kb_steps, text) and only the
    top 5 most relevant articles are returned — with generous field limits so
    the agent can give a thorough, actionable answer.

    If no search_query is provided, returns the first 5 articles with moderate
    truncation (used as a fallback).
    """
    results = []

    if search_query:
        # Search mode — return top articles ranked by relevance.
        # Note: Dialogflow CX has an 8192 token limit (~32K chars).
        # We return up to 5 articles for the tech KB pre-check;
        # Dialogflow CX only uses the response text, not these results directly.
        FIELD_LIMITS = {
            "kb_steps": 1500,
            "kb_snippet": 200,
            "short_description": 150,
            "text": 1000,
        }
        MAX_ARTICLES = 5

        # Build relevance-ranked query using keyword matching
        # Use AI to generate contextually-accurate search terms (falls back to synonyms)
        search_terms = _ai_expand_search_terms(search_query)
        logger.info(f"Search terms (AI-expanded): {search_terms}")

        # Build CASE expressions that count how many fields each term appears in
        relevance_parts = []
        for term in search_terms:
            safe_term = term.replace("'", "\\'")
            relevance_parts.append(
                f"(CASE WHEN LOWER(short_description) LIKE '%{safe_term}%' THEN 3 ELSE 0 END "
                f"+ CASE WHEN LOWER(kb_snippet) LIKE '%{safe_term}%' THEN 2 ELSE 0 END "
                f"+ CASE WHEN LOWER(kb_steps) LIKE '%{safe_term}%' THEN 2 ELSE 0 END "
                f"+ CASE WHEN LOWER(text) LIKE '%{safe_term}%' THEN 1 ELSE 0 END)"
            )
        relevance_expr = " + ".join(relevance_parts)

        columns_str = ", ".join(KB_COLUMNS)

        if _CITRIX_KB_NUMBERS:
            # Scope to articles linked from the landing page
            kb_list_sql = ", ".join(f"'{kb}'" for kb in _CITRIX_KB_NUMBERS)
            scope_clause = f"UPPER(number) IN ({kb_list_sql})"
        else:
            # Fallback if landing-page load failed
            scope_clause = "LOWER(kb_category_display_value) LIKE '%citrix%'"

        query = f"""
            SELECT {columns_str}, ({relevance_expr}) AS relevance_score
            FROM `{BQ_TABLE}`
            WHERE {scope_clause}
              AND ({relevance_expr}) > 0
            ORDER BY relevance_score DESC
            LIMIT {MAX_ARTICLES}
        """
    else:
        # Fallback — no search query, return a handful of articles with moderate detail
        FIELD_LIMITS = {
            "kb_steps": 600,
            "kb_snippet": 200,
            "short_description": 150,
            "text": 500,
        }
        MAX_ARTICLES = 5
        columns_str = ", ".join(KB_COLUMNS)

        if _CITRIX_KB_NUMBERS:
            kb_list_sql = ", ".join(f"'{kb}'" for kb in _CITRIX_KB_NUMBERS)
            scope_clause = f"UPPER(number) IN ({kb_list_sql})"
        else:
            scope_clause = "LOWER(kb_category_display_value) LIKE '%citrix%'"

        query = f"""
            SELECT {columns_str}
            FROM `{BQ_TABLE}`
            WHERE {scope_clause}
            LIMIT {MAX_ARTICLES}
        """

    try:
        logger.info(f"Querying BigQuery: {BQ_TABLE} (search={search_query})")
        query_job = bq_client.query(query)
        rows = query_job.result()

        for row in rows:
            article = {}
            for col in KB_COLUMNS:
                value = row.get(col, None) if hasattr(row, 'get') else getattr(row, col, None)
                if value is not None:
                    text = str(value)
                    max_len = FIELD_LIMITS.get(col)
                    if max_len and len(text) > max_len:
                        text = text[:max_len] + "..."
                    article[col] = text
                else:
                    article[col] = "N/A"

            # De-duplicate: if kb_steps covers most of the text content,
            # trim the text field to save tokens for the agent's response.
            steps = article.get("kb_steps", "")
            body = article.get("text", "")
            if steps and body and steps != "N/A" and body != "N/A":
                # If >60% of text content overlaps with kb_steps, truncate text
                steps_lower = steps.lower()[:500]
                body_lower = body.lower()[:500]
                overlap_words = set(steps_lower.split()) & set(body_lower.split())
                body_words = set(body_lower.split())
                if body_words and len(overlap_words) / len(body_words) > 0.6:
                    # Keep only the portion of text NOT covered by steps
                    article["text"] = body[:800] + "..." if len(body) > 800 else body

            results.append(article)

        logger.info(f"BigQuery returned {len(results)} Citrix KB articles")
    except Exception as e:
        logger.error(f"Error querying BigQuery: {e}")
    return results


@app.route('/debug/kb-scope', methods=['GET'])
def debug_kb_scope():
    """Debug endpoint to inspect the cached Citrix KB article scope."""
    return jsonify({
        "landing_page": LANDING_PAGE_KB,
        "total_articles_in_scope": len(_CITRIX_KB_NUMBERS),
        "kb_numbers": _CITRIX_KB_NUMBERS,
    }), 200


def format_kb_response(results):
    """Format KB articles into a readable response for Dialogflow."""
    if not results:
        return "I couldn't find any Citrix KB articles. Please try again later or contact support."

    response_text = f"I found {len(results)} Citrix KB article(s):\n\n"
    for i, article in enumerate(results, 1):
        response_text += f"**Article {i}: {article.get('number', 'N/A')}**\n"
        response_text += f"  - **Title:** {article.get('short_description', 'N/A')}\n"
        response_text += f"  - **Category:** {article.get('kb_category_display_value', 'N/A')}\n"
        response_text += f"  - **Summary:** {article.get('kb_snippet', 'N/A')}\n"

        steps = article.get("kb_steps", "N/A")
        if steps and steps != "N/A":
            step_list = [s.strip() for s in steps.split("$") if s.strip()]
            if step_list:
                response_text += "  - **Steps:**\n"
                for step_num, step in enumerate(step_list, 1):
                    response_text += f"    {step_num}. {step}\n"

        link = article.get("u_portal_url", "N/A")
        if link and link != "N/A":
            response_text += f"  - **Link:** {link}\n"
        response_text += "\n"
    return response_text


@app.route('/dialogflow-webhook', methods=['POST'])
def dialogflow_webhook():
    """
    Dialogflow CX OpenAPI Tool endpoint.
    Called by the Dialogflow agent's Tool to fetch Citrix KB articles.

    Accepts an optional 'search_query' in the JSON body so the agent
    can request articles relevant to the user's specific issue.
    Returns a simple JSON response that the LLM can read.
    """
    logger.info("Dialogflow tool endpoint called")

    # Extract optional search query from the request body
    search_query = None
    data = request.get_json(silent=True)
    if data:
        search_query = data.get("search_query") or data.get("query")
    logger.info(f"Search query: {search_query}")

    # Fetch relevant Citrix KB articles from BigQuery
    results = fetch_citrix_kb_articles(search_query=search_query)

    if not results:
        return jsonify({
            "results_count": 0,
            "message": "No Citrix KB articles found. Please try again later or contact IT support.",
            "articles": []
        }), 200

    # Return clean, simple JSON — the LLM will read and interpret this
    return jsonify({
        "results_count": len(results),
        "message": f"Found {len(results)} Citrix KB articles.",
        "articles": results
    }), 200


# ──────────────────────────────────────────────────────────────────
# CITRIX CHAT API — Dialogflow CX detectIntent wrapper
# ──────────────────────────────────────────────────────────────────
# Called by the Slack bot (hosted on AWS) to get Citrix agent responses
# without needing google-cloud-dialogflow-cx installed locally.

CITRIX_DFCX_PROJECT_ID = "vz-it-np-voev-dev-voevdo-0"
CITRIX_DFCX_LOCATION = "global"
CITRIX_DFCX_AGENT_ID = "70f05c86-5e60-40bc-a24f-74e7bda0333d"

# Create the Dialogflow CX SessionsClient once (reuse across requests)
_dfcx_client = dialogflowcx.SessionsClient(
    client_options={"api_endpoint": "dialogflow.googleapis.com"}
)


def detect_intent_citrix(session_id, text):
    """
    Call Citrix Dialogflow CX agent using detectIntent API.
    Returns the agent's text response as a string.
    """
    session_path = _dfcx_client.session_path(
        project=CITRIX_DFCX_PROJECT_ID,
        location=CITRIX_DFCX_LOCATION,
        agent=CITRIX_DFCX_AGENT_ID,
        session=session_id,
    )

    text_input = dialogflowcx.TextInput(text=text)
    query_input = dialogflowcx.QueryInput(
        text=text_input,
        language_code="en",
    )

    response = _dfcx_client.detect_intent(
        request=dialogflowcx.DetectIntentRequest(
            session=session_path,
            query_input=query_input,
        )
    )

    # Extract all text responses from the agent
    response_texts = []
    for message in response.query_result.response_messages:
        if message.text:
            for t in message.text.text:
                if t.strip():
                    response_texts.append(t.strip())

    return "\n\n".join(response_texts) if response_texts else ""


@app.route('/citrix-chat', methods=['POST'])
def citrix_chat():
    """
    API endpoint for the Slack bot to query the Citrix Dialogflow CX agent.
    Wraps detectIntent so the caller doesn't need google-cloud-dialogflow-cx.

    If the top matching KB article is a "Tech KB" (admin-only), returns a
    special flag so the Slack bot can tell the user to raise an AYS ticket
    instead of providing self-service steps.

    Expected JSON payload:
    {
        "query": "I can't see my local C drive in Citrix",
        "session_id": "slack-U12345"  (optional, defaults to 'default')
    }

    Returns:
    {
        "status": "success",
        "response": "Agent's text response...",
        "is_tech_kb": false
    }
    """
    try:
        data = request.get_json()
        if not data or "query" not in data:
            return jsonify({
                "status": "error",
                "message": "Missing required field: 'query'"
            }), 400

        query_text = data["query"]
        session_id = data.get("session_id", "default")

        logger.info(f"Citrix chat request: session={session_id}, query='{query_text[:80]}'")

        # ── Tech KB check: only flag if ALL matching articles are admin-only ──
        # If there's at least one self-help article in the results, do NOT flag
        # as tech KB — the self-help article should be used instead.
        is_tech_kb = False
        tech_kb_article = None
        try:
            kb_results = fetch_citrix_kb_articles(search_query=query_text)
            if kb_results:
                tech_kb_articles = []
                self_help_articles = []
                for article in kb_results:
                    kb_base_value = (article.get("kb_knowledge_base_display_value") or "").strip().lower()
                    if "tech kb" in kb_base_value:
                        tech_kb_articles.append(article)
                    else:
                        self_help_articles.append(article)

                # Only flag as tech KB if there are NO self-help alternatives
                if tech_kb_articles and not self_help_articles:
                    is_tech_kb = True
                    tech_kb_article = tech_kb_articles[0]
                    logger.info(
                        f"Tech KB detected (no self-help alternative): "
                        f"{tech_kb_article.get('number')} — all {len(tech_kb_articles)} "
                        f"matching articles are admin-only"
                    )
                elif tech_kb_articles and self_help_articles:
                    logger.info(
                        f"Tech KB articles found ({[a.get('number') for a in tech_kb_articles]}) "
                        f"but self-help alternatives exist "
                        f"({[a.get('number') for a in self_help_articles]}). "
                        f"Using self-help path."
                    )
        except Exception as kb_err:
            logger.warning(f"Tech KB pre-check failed (non-fatal): {kb_err}")

        # If Tech KB → return early with admin-only flag (no need to call Dialogflow)
        if is_tech_kb and tech_kb_article:
            admin_response = (
                f"This issue is managed by the *Admin/Tech KB* team and requires administrator intervention.\n\n"
                f"*KB Article:* {tech_kb_article.get('number', 'N/A')} — {tech_kb_article.get('short_description', '')}\n\n"
                f"Self-service steps are not available for this type of request. "
                f"Please raise an AYS ticket so the admin team can assist you."
            )
            if tech_kb_article.get("u_portal_url") and tech_kb_article["u_portal_url"] != "N/A":
                admin_response += f"\n\n*Reference:* {tech_kb_article['u_portal_url']}"

            logger.info(f"Returning Tech KB admin-only response for query: '{query_text[:80]}'")
            return jsonify({
                "status": "success",
                "response": admin_response,
                "is_tech_kb": True,
            }), 200

        try:
            answer = detect_intent_citrix(session_id, query_text)
        except Exception as dfcx_err:
            err_str = str(dfcx_err)
            # If the session has accumulated too many tokens, start a fresh session
            if "Token limit exceeded" in err_str or "FAILED_PRECONDITION" in err_str:
                import uuid
                fresh_session = f"{session_id}-{uuid.uuid4().hex[:8]}"
                logger.warning(
                    f"Session {session_id} hit token limit. Retrying with fresh session {fresh_session}"
                )
                answer = detect_intent_citrix(fresh_session, query_text)
            else:
                raise

        if not answer:
            answer = "I'm sorry, I couldn't find relevant information. Please contact IT support."

        logger.info(f"Citrix chat response: {len(answer)} chars")

        return jsonify({
            "status": "success",
            "response": answer,
            "is_tech_kb": False,
        }), 200

    except Exception as e:
        logger.error(f"Error in citrix_chat: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"{type(e).__name__}: {str(e)}"
        }), 500


# ──────────────────────────────────────────────────────────────────
# QUERY ANALYSIS — AI-powered vagueness & Citrix-relevance check
# ──────────────────────────────────────────────────────────────────
# Uses Gemini to determine if a query:
#   (a) is Citrix-related
#   (b) has enough context to proceed, or needs clarification
# This replaces static word-list checks with intelligent understanding.

_ANALYZE_QUERY_PROMPT = """You are a triage assistant for a Citrix Virtual Desktop support bot used by Verizon employees.

Given the user's message (and optionally their previous messages in the conversation), decide:

1. **is_citrix**: Is the query related to Citrix Virtual Desktop, VDI, dCloud, remote desktop, or any IT topic that would be handled through a Citrix desktop environment? Be generous — if there's any reasonable Citrix connection, say true. Only say false for clearly unrelated topics (e.g., "what's the weather", "tell me a joke", "HR policy questions").

2. **needs_clarification**: Does the query have enough context for a support agent to start troubleshooting? Say false (= ready to proceed) if the user describes ANY specific action, symptom, or error — even briefly. Say true ONLY if the message is extremely vague with no actionable detail at all (e.g., just "help", "issue", "problem", single word with no context).

3. **clarifying_question**: If needs_clarification is true, write a short, friendly clarifying question (2-3 sentences max) asking what specific Citrix issue they're experiencing. Give 2-3 example topics they could mention. Do NOT repeat instructions — just ask naturally.

IMPORTANT:
- Queries like "citrix is slow", "can't login", "black screen", "install software" — these have ENOUGH context. Set needs_clarification=false.
- Queries like "error", "help me", "issue", "problem", "not working" with NO other detail — these need clarification.
- When previous messages are provided, consider the FULL conversation context. If earlier messages give enough detail, set needs_clarification=false.

Respond ONLY with valid JSON (no markdown, no explanation):
{"is_citrix": true/false, "needs_clarification": true/false, "clarifying_question": "..." or ""}
"""


@app.route('/analyze-query', methods=['POST'])
def analyze_query():
    """
    Analyze a user query using Gemini to determine:
    - Is it Citrix-related?
    - Does it have enough context, or should the bot ask a clarifying question?

    Expected JSON:
    {
        "query": "help",
        "previous_messages": ["I was trying to launch my desktop"]  (optional)
    }

    Returns:
    {
        "status": "success",
        "is_citrix": true,
        "needs_clarification": true,
        "clarifying_question": "Could you tell me more about..."
    }
    """
    try:
        data = request.get_json()
        if not data or not data.get("query", "").strip():
            return jsonify({"status": "error", "message": "Missing 'query'"}), 400

        query_text = data["query"].strip()
        previous_messages = data.get("previous_messages", [])

        # If Gemini is not available, return a safe default (assume Citrix, proceed)
        if not _gemini_model:
            logger.warning("Gemini not available for analyze-query — defaulting to proceed")
            return jsonify({
                "status": "success",
                "is_citrix": True,
                "needs_clarification": False,
                "clarifying_question": "",
            }), 200

        # Build the prompt with conversation context
        parts = [_ANALYZE_QUERY_PROMPT]
        if previous_messages:
            parts.append("Previous messages in this conversation:")
            for i, msg in enumerate(previous_messages[:5], 1):
                parts.append(f"  {i}. {msg}")
        parts.append(f"\nCurrent user message: {query_text}")

        prompt = "\n".join(parts)
        logger.info(f"Analyzing query: '{query_text[:80]}' (with {len(previous_messages)} prev msgs)")

        response = _gemini_model.generate_content(prompt)
        raw_text = response.text.strip()

        # Parse JSON from Gemini response (strip markdown fences if present)
        clean = raw_text
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()

        import json as _json
        result = _json.loads(clean)

        is_citrix = bool(result.get("is_citrix", True))
        needs_clarification = bool(result.get("needs_clarification", False))
        clarifying_question = str(result.get("clarifying_question", ""))

        logger.info(f"Query analysis: is_citrix={is_citrix}, needs_clarification={needs_clarification}")

        return jsonify({
            "status": "success",
            "is_citrix": is_citrix,
            "needs_clarification": needs_clarification,
            "clarifying_question": clarifying_question,
        }), 200

    except (_json.JSONDecodeError if '_json' in dir() else ValueError) as e:
        logger.error(f"Could not parse Gemini response as JSON: {e}")
        # Safe default: assume Citrix, proceed without clarification
        return jsonify({
            "status": "success",
            "is_citrix": True,
            "needs_clarification": False,
            "clarifying_question": "",
        }), 200
    except Exception as e:
        logger.error(f"Error in analyze_query: {type(e).__name__}: {e}", exc_info=True)
        # Safe default on error: don't block the user
        return jsonify({
            "status": "success",
            "is_citrix": True,
            "needs_clarification": False,
            "clarifying_question": "",
        }), 200


# ──────────────────────────────────────────────────────────────────
# CITRIX QUERY CLASSIFICATION, AUTO-HEALING & AYS INCIDENT CREATION
# ──────────────────────────────────────────────────────────────────
# Flow:
#   1. Slack bot sends user query → POST /classify-query
#      → Calls external Classification API to determine issue type
#   2a. If classification == "Launch_Issue":
#       → Slack bot calls POST /auto-heal to trigger self-healing
#       → User sees result + "Is your issue fixed?" Yes/No buttons
#   2b. If classification != "Launch_Issue":
#       → Slack bot calls existing POST /citrix-chat (KB lookup)
#       → User sees KB response + "Is your issue resolved?" Yes/No buttons
#   3. If user clicks "No":
#       → Slack bot calls POST /create-ays-incident to open a ticket

CLASSIFICATION_API_URL = "https://citrix-ticket-classifier.ebiz.verizon.com/classify"


@app.route('/classify-query', methods=['POST'])
def classify_query():
    """
    Classify a Citrix user query using the external ticket classifier API.

    Expected JSON:
    {
        "query": "I can't launch my Citrix desktop"
    }

    Returns:
    {
        "status": "success",
        "classification": "Launch_Issue",
        "citrix_type": "Citrix",
        "reason": "Matched INC016018620 (73.96%). ..."
    }
    """
    try:
        data = request.get_json()
        if not data or not data.get("query", "").strip():
            return jsonify({"status": "error", "message": "Missing 'query'"}), 400

        query_text = data["query"].strip()
        logger.info(f"Classifying query: '{query_text[:80]}'")

        resp = http_requests.post(
            CLASSIFICATION_API_URL,
            json={"inputs": [query_text]},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()

        logger.info(f"Classification result: {result.get('classification')} / {result.get('citrix_type')}")

        return jsonify({
            "status": "success",
            "classification": result.get("classification", "Unknown"),
            "citrix_type": result.get("citrix_type", "Unknown"),
            "reason": result.get("reason", ""),
        }), 200

    except http_requests.exceptions.RequestException as e:
        logger.error(f"Classification API request failed: {e}")
        return jsonify({
            "status": "error",
            "message": f"Classification API unreachable: {e}"
        }), 502
    except Exception as e:
        logger.error(f"Error in classify_query: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"{type(e).__name__}: {str(e)}"
        }), 500


# ──────────────────────────────────────────────────────────────────
# CITRIX AUTO-HEAL — Real API Integration + DB Tracking + Scheduler
# ──────────────────────────────────────────────────────────────────
# Real Citrix Auto-Heal API: GET https://sacwpezkva024.verizon.com:8443/autohealvm
# Statuses: Pending, InProgress, Success, Error, NA, ip_success, ip_fail, completed
#
# DB: PostgreSQL on Cloud SQL (see autoheal_schema.sql for DDL)
# Scheduler: Cloud Scheduler → POST /auto-heal-check-pending every 5 mins
# Notifications: Backend sends Slack messages via bot token passed from Slack bot

import json as _json
import pg8000
import pg8000.native

# ── Configuration ─────────────────────────────────────────────────
# Citrix Auto-Heal API
AUTOHEAL_API_URL = "https://sacwpezkva024.verizon.com:8443/autohealvm"
AUTOHEAL_API_KEY = os.environ.get("CITRIX_AUTOHEAL_API_KEY", "citrixapitest123")

# Cloud SQL PostgreSQL connection config
# Instance: citrix_bot in same project (vz-it-np-voev-dev-voevdo-0)
DB_HOST = os.environ.get("DB_HOST", "63.21.112.111")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "citrix_bot")
DB_USER = os.environ.get("DB_USER", "datalake")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "datalake")
# Cloud SQL Unix socket for Cloud Run (same project)
DB_UNIX_SOCKET = os.environ.get("DB_UNIX_SOCKET", "")

# Terminal statuses — auto-heal is done, no more polling needed
_TERMINAL_STATUSES = {"Success", "Error", "NA", "ip_fail", "ip_success", "completed"}


def _normalize_heal_status(raw_status: str) -> str:
    """
    Normalize the status string returned by the Citrix Auto-Heal API.
    The API returns mixed-case values (IP_success, SUCCESS, InProgress, etc.)
    but our code uses a canonical form for comparisons and lookups.
    """
    if not raw_status:
        return raw_status
    s = raw_status.strip()
    # Map known API responses to canonical forms used by the bot
    canonical = {
        "ip_success": "ip_success",
        "ip_fail": "ip_fail",
        "inprogress": "InProgress",
        "pending": "Pending",
        "success": "Success",
        "error": "Error",
        "na": "NA",
        "completed": "completed",
    }
    return canonical.get(s.lower(), s)


def _get_db_connection():
    """Get a PostgreSQL connection via pg8000."""
    if DB_UNIX_SOCKET:
        return pg8000.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            unix_sock=f"{DB_UNIX_SOCKET}/.s.PGSQL.5432",
        )
    return pg8000.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
    )


def _generate_incno(slack_user_id: str) -> str:
    """Generate a unique incident number: userId + YYYYMMDD + HHmmss."""
    now = datetime.now()
    return f"{slack_user_id}{now.strftime('%Y%m%d%H%M%S')}"


def _pad_user_id(slack_user_id: str) -> str:
    """
    Look up the 10-digit EID for a Slack user from BigQuery.
    Table: vz-it-np-voev-dev-voevdo-0.slack.vea_slack_userobj_data
    Columns: slackid, eid

    The Citrix auto-heal API requires a 10-digit EID.
    If lookup fails, falls back to padding the slack ID (temporary).
    """
    try:
        query = """
            SELECT eid
            FROM `vz-it-np-voev-dev-voevdo-0.slack.vea_slack_userobj_data`
            WHERE slackid = @slack_id
            LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("slack_id", "STRING", slack_user_id),
            ]
        )
        rows = list(bq_client.query(query, job_config=job_config).result())

        if rows and rows[0].eid:
            eid = str(rows[0].eid).strip()
            # Ensure it's 10 digits (pad with leading zeros if needed)
            eid = eid.zfill(10)
            logger.info(f"EID lookup success: {slack_user_id} → {eid}")
            return eid

        logger.warning(f"EID not found for slack_id {slack_user_id} — using fallback")
    except Exception as e:
        logger.error(f"EID lookup failed for {slack_user_id}: {e}")

    # Fallback: pad slack ID (won't work with real Citrix API, but won't crash)
    raw = slack_user_id.lstrip("U") if slack_user_id.startswith("U") else slack_user_id
    return raw.zfill(10)[:10]


def _call_citrix_autoheal_api(user_id: str, incno: str, label: str) -> dict:
    """
    Call the real Citrix Auto-Heal API.
    GET https://sacwpezkva024.verizon.com:8443/autohealvm
    Params: userId, incno, label
    Header: X-API-Key
    Returns the parsed JSON response dict.
    """
    params = {
        "userId": user_id,
        "incno": incno,
        "label": label,
    }
    headers = {
        "X-API-Key": AUTOHEAL_API_KEY,
    }

    logger.info(f"Calling Citrix Auto-Heal API: userId={user_id}, incno={incno}, label={label}")

    resp = http_requests.get(
        AUTOHEAL_API_URL,
        params=params,
        headers=headers,
        timeout=60,
        verify=False,  # Internal cert may not be in trust store
    )

    logger.info(f"Citrix Auto-Heal API response: status_code={resp.status_code}, body={resp.text[:500]}")

    if resp.status_code == 401:
        return {"status": "Error", "Message": "Unauthorized: Invalid API key"}
    if resp.status_code == 400:
        return {"status": "Error", "Message": resp.text[:500]}

    resp.raise_for_status()
    return resp.json()


def _save_autoheal_request(slack_user_id, user_id, incno, label, query_text,
                           classification, channel_id, thread_ts, status,
                           api_message, vm_name, details):
    """Insert a new auto-heal request record into the database."""
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO citrix_autoheal_requests
               (slack_user_id, user_id, incno, label, query_text, classification,
                channel_id, thread_ts, status, api_message, vm_name, details,
                last_notified_status, notification_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
               RETURNING id""",
            (slack_user_id, user_id, incno, label, query_text, classification,
             channel_id, thread_ts, status, api_message, vm_name, details, status)
        )
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    finally:
        conn.close()


def _update_autoheal_status(incno, new_status, api_message=None, vm_name=None, details=None):
    """Update the status of an existing auto-heal request in the database."""
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        set_parts = ["status = %s"]
        params = [new_status]
        if api_message is not None:
            set_parts.append("api_message = %s")
            params.append(api_message)
        if vm_name is not None:
            set_parts.append("vm_name = %s")
            params.append(vm_name)
        if details is not None:
            set_parts.append("details = %s")
            params.append(details)
        if new_status in _TERMINAL_STATUSES:
            set_parts.append("completed_at = CURRENT_TIMESTAMP")

        params.append(incno)
        cur.execute(
            f"UPDATE citrix_autoheal_requests SET {', '.join(set_parts)} WHERE incno = %s",
            params
        )
        conn.commit()
    finally:
        conn.close()


def _get_pending_autoheal_requests():
    """Fetch all auto-heal requests that are still in progress (need polling)."""
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, slack_user_id, user_id, incno, label, channel_id, thread_ts,
                      status, last_notified_status, notification_count, created_at
               FROM citrix_autoheal_requests
               WHERE status IN ('Pending', 'InProgress')
               ORDER BY created_at ASC"""
        )
        columns = ["id", "slack_user_id", "user_id", "incno", "label",
                   "channel_id", "thread_ts", "status", "last_notified_status",
                   "notification_count", "created_at"]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _get_autoheal_by_thread(channel_id, thread_ts):
    """Find auto-heal request by Slack thread (for in-thread status queries)."""
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, slack_user_id, user_id, incno, label, channel_id, thread_ts,
                      status, api_message, vm_name, details, created_at, updated_at, completed_at
               FROM citrix_autoheal_requests
               WHERE channel_id = %s AND thread_ts = %s
               ORDER BY created_at DESC LIMIT 1""",
            (channel_id, thread_ts)
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = ["id", "slack_user_id", "user_id", "incno", "label",
                   "channel_id", "thread_ts", "status", "api_message", "vm_name",
                   "details", "created_at", "updated_at", "completed_at"]
        return dict(zip(columns, row))
    finally:
        conn.close()


def _get_autoheal_by_user(slack_user_id, limit=5):
    """Fetch recent auto-heal requests for a user."""
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, incno, status, api_message, created_at, updated_at
               FROM citrix_autoheal_requests
               WHERE slack_user_id = %s
               ORDER BY created_at DESC LIMIT %s""",
            (slack_user_id, limit)
        )
        columns = ["id", "incno", "status", "api_message", "created_at", "updated_at"]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _mark_notified(incno, status):
    """Mark a request as having been notified for its current status."""
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE citrix_autoheal_requests
               SET last_notified_status = %s, notification_count = notification_count + 1
               WHERE incno = %s""",
            (status, incno)
        )
        conn.commit()
    finally:
        conn.close()


@app.route('/auto-heal', methods=['POST'])
def auto_heal():
    """
    Trigger auto-healing for a Launch_Issue.
    Calls the real Citrix Auto-Heal API and stores the request in DB.

    Expected JSON:
    {
        "query": "can't launch citrix desktop",
        "user_id": "U08ASQAJJ4D",         (Slack user ID)
        "classification": "Launch_Issue",
        "channel_id": "C0AQESCN24E",
        "thread_ts": "1714500000.123456"
    }

    Returns:
    {
        "status": "success",
        "heal_status": "InProgress" | "Success" | "Error" | "NA",
        "message": "...",
        "incno": "...",
        "immediate": true/false   (true = resolved immediately, false = will poll)
    }
    """
    try:
        data = request.get_json()
        if not data or not data.get("user_id", "").strip():
            return jsonify({"status": "error", "message": "Missing 'user_id'"}), 400

        slack_user_id = data["user_id"].strip()
        query_text = data.get("query", "").strip()
        classification = data.get("classification", "Launch_Issue")
        channel_id = data.get("channel_id", "")
        thread_ts = data.get("thread_ts", "")
        label = classification  # Pass classification as label to Citrix API

        # Generate unique incident number and look up EID from BigQuery
        incno = _generate_incno(slack_user_id)
        eid = _pad_user_id(slack_user_id)

        logger.info(f"Auto-heal triggered: slack_user={slack_user_id}, eid={eid}, incno={incno}")

        # Call the real Citrix Auto-Heal API
        api_response = _call_citrix_autoheal_api(eid, incno, label)

        # Parse API response — normalize status to canonical form
        heal_status = _normalize_heal_status(api_response.get("status", "Error"))
        api_message = api_response.get("Message", api_response.get("message", ""))
        vm_name = api_response.get("vmname", "")
        details = api_response.get("details", "")

        # Determine if this resolved immediately or needs polling
        immediate = heal_status in _TERMINAL_STATUSES

        # Save to database
        try:
            _save_autoheal_request(
                slack_user_id=slack_user_id,
                user_id=eid,
                incno=incno,
                label=label,
                query_text=query_text,
                classification=classification,
                channel_id=channel_id,
                thread_ts=thread_ts,
                status=heal_status,
                api_message=api_message,
                vm_name=vm_name,
                details=details,
            )
            logger.info(f"Auto-heal request saved to DB: incno={incno}, status={heal_status}")
        except Exception as db_err:
            logger.error(f"Failed to save auto-heal to DB (continuing): {db_err}")

        result = {
            "status": "success",
            "heal_status": heal_status,
            "message": api_message,
            "incno": incno,
            "vm_name": vm_name,
            "immediate": immediate,
        }

        logger.info(f"Auto-heal result: incno={incno}, heal_status={heal_status}, immediate={immediate}")
        return jsonify(result), 200

    except http_requests.exceptions.RequestException as e:
        logger.error(f"Citrix Auto-Heal API request failed: {e}")
        return jsonify({
            "status": "error",
            "message": f"Auto-Heal API unreachable: {e}"
        }), 502
    except Exception as e:
        logger.error(f"Error in auto_heal: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"{type(e).__name__}: {str(e)}"
        }), 500


@app.route('/auto-heal-status', methods=['POST'])
def auto_heal_status():
    """
    Check auto-heal status for a specific thread or user.
    Called by Slack bot when user asks "status?" in a thread.

    Expected JSON:
    {
        "channel_id": "C0AQESCN24E",
        "thread_ts": "1714500000.123456"
    }
    OR:
    {
        "slack_user_id": "U08ASQAJJ4D"
    }

    Returns current status from DB (and optionally re-polls the API).
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        record = None

        # Try thread-based lookup first
        channel_id = data.get("channel_id", "").strip()
        thread_ts = data.get("thread_ts", "").strip()
        if channel_id and thread_ts:
            record = _get_autoheal_by_thread(channel_id, thread_ts)

        # Fallback to user-based lookup
        if not record:
            slack_user_id = data.get("slack_user_id", "").strip()
            if slack_user_id:
                records = _get_autoheal_by_user(slack_user_id, limit=1)
                record = records[0] if records else None

        if not record:
            return jsonify({
                "status": "success",
                "found": False,
                "message": "No auto-heal request found for this thread/user."
            }), 200

        # If still in progress, re-poll the Citrix API for latest status
        if record["status"] in ("Pending", "InProgress"):
            try:
                api_response = _call_citrix_autoheal_api(
                    record["user_id"], record["incno"], record["label"]
                )
                new_status = _normalize_heal_status(api_response.get("status", record["status"]))
                new_message = api_response.get("Message", api_response.get("message", ""))
                new_vm = api_response.get("vmname", "")
                new_details = api_response.get("details", "")

                # Update DB if status changed
                if new_status != record["status"]:
                    _update_autoheal_status(record["incno"], new_status, new_message, new_vm, new_details)
                    record["status"] = new_status
                    record["api_message"] = new_message
            except Exception as poll_err:
                logger.warning(f"Could not re-poll API for {record['incno']}: {poll_err}")

        return jsonify({
            "status": "success",
            "found": True,
            "heal_status": record["status"],
            "message": record.get("api_message", ""),
            "incno": record["incno"],
            "vm_name": record.get("vm_name", ""),
            "created_at": str(record.get("created_at", "")),
            "updated_at": str(record.get("updated_at", "")),
        }), 200

    except Exception as e:
        logger.error(f"Error in auto_heal_status: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/auto-heal-check-pending', methods=['POST'])
def auto_heal_check_pending():
    """
    Scheduler endpoint — called every 5 minutes by Cloud Scheduler.
    Polls the Citrix API for all InProgress requests and updates DB.
    Returns list of status changes that need Slack notifications.

    Expected JSON (optional):
    {
        "slack_bot_token": "xoxb-..."   (for sending Slack notifications directly)
    }

    Returns:
    {
        "status": "success",
        "checked": 5,
        "changed": 2,
        "notifications": [
            {"incno": "...", "slack_user_id": "...", "channel_id": "...",
             "thread_ts": "...", "old_status": "...", "new_status": "...", "message": "..."}
        ]
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        slack_bot_token = data.get("slack_bot_token", "")

        pending = _get_pending_autoheal_requests()
        logger.info(f"Scheduler: checking {len(pending)} pending auto-heal requests")

        notifications = []
        for req in pending:
            try:
                api_response = _call_citrix_autoheal_api(
                    req["user_id"], req["incno"], req["label"]
                )
                new_status = _normalize_heal_status(api_response.get("status", req["status"]))
                new_message = api_response.get("Message", api_response.get("message", ""))
                new_vm = api_response.get("vmname", "")
                new_details = api_response.get("details", "")

                if new_status != req["status"] or new_status != req.get("last_notified_status"):
                    # Status changed or not yet notified — update DB and queue notification
                    _update_autoheal_status(req["incno"], new_status, new_message, new_vm, new_details)

                    if new_status != req.get("last_notified_status"):
                        notifications.append({
                            "incno": req["incno"],
                            "slack_user_id": req["slack_user_id"],
                            "channel_id": req["channel_id"],
                            "thread_ts": req["thread_ts"],
                            "old_status": req["status"],
                            "new_status": new_status,
                            "message": new_message,
                        })
                        _mark_notified(req["incno"], new_status)
                else:
                    # No change — just increment notification count for tracking
                    logger.info(f"No change for {req['incno']}: still {req['status']}")

            except Exception as poll_err:
                logger.error(f"Error polling {req['incno']}: {poll_err}")

        # If a Slack bot token was provided, send notifications directly
        if slack_bot_token and notifications:
            _send_slack_notifications(slack_bot_token, notifications)

        return jsonify({
            "status": "success",
            "checked": len(pending),
            "changed": len(notifications),
            "notifications": notifications,
        }), 200

    except Exception as e:
        logger.error(f"Error in auto_heal_check_pending: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


def _send_slack_notifications(bot_token: str, notifications: list):
    """
    Send Slack messages for status changes.
    Uses the Slack Web API chat.postMessage.
    """
    slack_api_url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json",
    }
    # Use proxy for Slack API calls
    proxies = {
        "http": "http://proxy.ebiz.verizon.com:80/",
        "https": "http://proxy.ebiz.verizon.com:80/",
    }

    for notif in notifications:
        try:
            msg = _format_status_notification(notif)
            payload = {
                "channel": notif["channel_id"],
                "thread_ts": notif["thread_ts"],
                "text": msg["text"],
                "blocks": msg.get("blocks", []),
            }
            resp = http_requests.post(
                slack_api_url, headers=headers, json=payload,
                proxies=proxies, timeout=10
            )
            resp_data = resp.json()
            if not resp_data.get("ok"):
                logger.error(f"Slack notification failed for {notif['incno']}: {resp_data.get('error')}")
            else:
                logger.info(f"Slack notification sent for {notif['incno']} → {notif['new_status']}")
        except Exception as e:
            logger.error(f"Error sending Slack notification for {notif['incno']}: {e}")


def _format_status_notification(notif: dict) -> dict:
    """Format a status change into a Slack message with blocks."""
    status = notif["new_status"]
    message = notif.get("message", "")
    user_id = notif["slack_user_id"]
    incno = notif["incno"]

    status_emoji = {
        "InProgress": "⏳",
        "Success": "✅",
        "Error": "❌",
        "NA": "⚠️",
        "ip_success": "✅",
        "ip_fail": "❌",
        "completed": "🎉",
    }.get(status, "ℹ️")

    status_label = {
        "InProgress": "In Progress",
        "Success": "Issue Fixed",
        "Error": "Error Occurred",
        "NA": "Auto-Heal Not Applicable",
        "ip_success": "VM Restart Successful",
        "ip_fail": "Auto-Heal Failed",
        "completed": "Completed — User Connected Successfully",
    }.get(status, status)

    text = f"{status_emoji} *Auto-Heal Status Update*\n\n"
    text += f"<@{user_id}>, here's the latest on your auto-heal request (`{incno}`):\n\n"
    text += f"*Status:* {status_label}\n"
    if message:
        text += f"*Details:* {message}\n"

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text}
        }
    ]

    # If failed, offer AYS ticket creation
    if status in ("ip_fail", "Error"):
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "The auto-heal could not resolve your issue. Would you like me to raise an AYS ticket for the support team to assist you?"
            }
        })
        # Encode context for button
        btn_context = _json.dumps({"incno": incno, "s": status})
        blocks.append({
            "type": "actions",
            "block_id": "autoheal_ays_prompt",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Yes, raise AYS ticket", "emoji": True},
                    "style": "primary",
                    "action_id": "autoheal_ays_yes",
                    "value": btn_context,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ No, I'll handle it", "emoji": True},
                    "style": "danger",
                    "action_id": "autoheal_ays_no",
                    "value": btn_context,
                },
            ],
        })

    # If completed successfully
    if status in ("Success", "ip_success", "completed"):
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "🎉 _Your Citrix VM issue has been resolved. Happy working!_"}]
        })

    return {"text": text, "blocks": blocks}


@app.route('/auto-heal-update-status', methods=['POST'])
def auto_heal_update_status():
    """
    External endpoint for Citrix team to update auto-heal status directly.
    This is called when the Citrix team manually updates the status
    (e.g., ip_success, ip_fail, completed) after monitoring the VM.

    Expected JSON:
    {
        "incno": "U08ASQAJJ4D20260430143022",
        "status": "ip_success",
        "message": "VM restarted successfully and is in registered state",
        "vm_name": "VMNAME123"
    }
    """
    try:
        data = request.get_json()
        if not data or not data.get("incno"):
            return jsonify({"status": "error", "message": "Missing 'incno'"}), 400

        incno = data["incno"].strip()
        new_status = data.get("status", "").strip()
        message = data.get("message", "")
        vm_name = data.get("vm_name", "")
        details = data.get("details", "")

        if not new_status:
            return jsonify({"status": "error", "message": "Missing 'status'"}), 400

        _update_autoheal_status(incno, new_status, message, vm_name, details)
        logger.info(f"Auto-heal status updated externally: incno={incno}, new_status={new_status}")

        return jsonify({
            "status": "success",
            "message": f"Status updated to '{new_status}' for {incno}"
        }), 200

    except Exception as e:
        logger.error(f"Error in auto_heal_update_status: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/auto-heal-list', methods=['GET'])
def auto_heal_list():
    """
    List auto-heal requests from the database with optional filters.
    For use by Citrix team to monitor all requests.

    Query Parameters:
        status   — Filter by status (e.g., InProgress, Success, Error). Optional.
        user_id  — Filter by Slack user ID. Optional.
        limit    — Max records to return (default 50, max 200). Optional.
        offset   — Pagination offset (default 0). Optional.

    Returns:
    {
        "status": "success",
        "total": 123,
        "limit": 50,
        "offset": 0,
        "requests": [ { ... }, ... ]
    }
    """
    try:
        # Parse query parameters
        status_filter = request.args.get("status", "").strip()
        user_filter = request.args.get("user_id", "").strip()
        limit = min(int(request.args.get("limit", "50")), 200)
        offset = max(int(request.args.get("offset", "0")), 0)

        conn = _get_db_connection()
        try:
            cur = conn.cursor()

            # Build WHERE clause
            conditions = []
            params = []
            if status_filter:
                conditions.append("status = %s")
                params.append(status_filter)
            if user_filter:
                conditions.append("slack_user_id = %s")
                params.append(user_filter)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            # Get total count
            cur.execute(f"SELECT COUNT(*) FROM citrix_autoheal_requests {where_clause}", params)
            total = cur.fetchone()[0]

            # Fetch records
            cur.execute(
                f"""SELECT id, slack_user_id, user_id, incno, label, channel_id, thread_ts,
                           status, api_message, vm_name, details, created_at, updated_at,
                           completed_at, last_notified_status, notification_count
                    FROM citrix_autoheal_requests
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s""",
                params + [limit, offset]
            )

            columns = [
                "id", "slack_user_id", "user_id", "incno", "label", "channel_id",
                "thread_ts", "status", "api_message", "vm_name", "details",
                "created_at", "updated_at", "completed_at",
                "last_notified_status", "notification_count",
            ]
            rows = []
            for row in cur.fetchall():
                record = dict(zip(columns, row))
                # Convert datetime objects to strings for JSON serialization
                for key in ("created_at", "updated_at", "completed_at"):
                    if record.get(key) is not None:
                        record[key] = str(record[key])
                rows.append(record)

        finally:
            conn.close()

        logger.info(f"Auto-heal list: total={total}, returned={len(rows)}, status_filter={status_filter or 'all'}")

        return jsonify({
            "status": "success",
            "total": total,
            "limit": limit,
            "offset": offset,
            "requests": rows,
        }), 200

    except Exception as e:
        logger.error(f"Error in auto_heal_list: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ── AI-powered thread reply for auto-heal context ────────────────

_AUTOHEAL_THREAD_REPLY_PROMPT = """You are a friendly Citrix Virtual Desktop support assistant chatting with a Verizon employee on Slack.

CONTEXT: The user previously reported a Citrix launch issue, and an auto-heal process was triggered for their VM.

Here's the current auto-heal status:
- Request ID: {incno}
- Current Status: {status}
- Status Meaning: {status_meaning}
- API Message: {api_message}
- Started At: {created_at}
- Last Updated: {updated_at}

STATUS MEANINGS:
- Pending/InProgress = auto-heal is actively running (typically 5-15 minutes)
- Success = VM issues were fixed, no problems identified
- ip_success = VM restart was successful, VM is back in registered state
- completed = user has successfully connected to VM
- ip_fail = auto-heal failed, VM could not be restored
- Error = server-side error during healing
- NA = auto-heal not applicable (no VM assigned or user not found in Citrix)

The user just sent this message in the thread: "{user_message}"

INSTRUCTIONS:
1. Respond naturally and conversationally (like a helpful teammate, not a robot)
2. Acknowledge what the user said specifically
3. Give them the current status in a natural way
4. If InProgress/Pending: reassure them it's still running, mention typical 5-15 min timeframe
5. If Success/ip_success/completed: congratulate them, confirm it's resolved
6. If ip_fail/Error: empathize, explain it failed, ask if they want an AYS support ticket raised
7. If NA: explain it's not applicable for their account, offer to raise a ticket
8. Keep it SHORT — 2-4 sentences max. This is Slack, not email.
9. Use Slack-friendly formatting: *bold* for emphasis, no markdown headers
10. Don't repeat the request ID unless the user asks for it

Respond with ONLY the message text (no JSON, no labels):"""


@app.route('/auto-heal-thread-reply', methods=['POST'])
def auto_heal_thread_reply():
    """
    Generate an AI-powered contextual reply for user messages in auto-heal threads.
    Uses Gemini to provide natural, conversational responses based on the current status.

    Expected JSON:
    {
        "user_message": "is it done yet?",
        "heal_status": "InProgress",
        "api_message": "INFO : Autoheal in progress",
        "incno": "U08ASQAJJ4D20260430143022",
        "created_at": "2026-04-30 14:30:22",
        "updated_at": "2026-04-30 14:35:00"
    }

    Returns:
    {
        "status": "success",
        "reply": "Hey! Your auto-heal is still running..."
    }
    """
    try:
        data = request.get_json()
        if not data or not data.get("user_message", "").strip():
            return jsonify({"status": "error", "message": "Missing 'user_message'"}), 400

        user_message = data["user_message"].strip()
        heal_status = data.get("heal_status", "Unknown")
        api_message = data.get("api_message", "")
        incno = data.get("incno", "")
        created_at = data.get("created_at", "")
        updated_at = data.get("updated_at", "")

        status_meaning = {
            "InProgress": "Auto-heal is actively running on the VM",
            "Pending": "Queued for processing",
            "Success": "VM issues fixed — no problems identified",
            "Error": "Server-side error during healing",
            "NA": "Auto-heal not applicable (no VM assigned or user not found)",
            "ip_success": "VM restart successful — VM is back in registered state",
            "ip_fail": "Auto-heal failed — VM could not be restored to normal state",
            "completed": "User has successfully connected to the VM",
        }.get(heal_status, heal_status)

        # If Gemini not available, return a simple formatted fallback
        if not _gemini_model:
            fallback = f"Your auto-heal is currently *{heal_status}*."
            if api_message:
                fallback += f" {api_message}"
            return jsonify({"status": "success", "reply": fallback}), 200

        prompt = _AUTOHEAL_THREAD_REPLY_PROMPT.format(
            incno=incno,
            status=heal_status,
            status_meaning=status_meaning,
            api_message=api_message or "N/A",
            created_at=created_at or "recently",
            updated_at=updated_at or "recently",
            user_message=user_message,
        )

        response = _gemini_model.generate_content(prompt)
        reply = response.text.strip()

        logger.info(f"Auto-heal thread reply generated for {incno}: '{reply[:80]}'")

        return jsonify({
            "status": "success",
            "reply": reply,
            "heal_status": heal_status,
        }), 200

    except Exception as e:
        logger.error(f"Error in auto_heal_thread_reply: {type(e).__name__}: {e}", exc_info=True)
        # Fallback to simple status message
        fallback = f"Your auto-heal status is currently *{data.get('heal_status', 'unknown')}*."
        return jsonify({"status": "success", "reply": fallback}), 200


@app.route('/create-ays-incident', methods=['POST'])
def create_ays_incident():
    """
    Create an AYS (At Your Service) incident for unresolved Citrix issues.
    ** DUMMY IMPLEMENTATION ** — AYS API integration coming later.
    Stores the incident record in DB for tracking.

    Expected JSON:
    {
        "user_id": "U08ASQAJJ4D",
        "query": "can't launch citrix desktop",
        "classification": "Launch_Issue",
        "classification_reason": "Matched INC016018620 ...",
        "short_description": "Citrix Launch Issue reported via Slack",
        "channel_id": "C0AQESCN24E",
        "thread_ts": "1714500000.123456",
        "autoheal_incno": "..."  (optional, links to auto-heal request)
    }

    Returns:
    {
        "status": "success",
        "incident_number": "INC...",
        "incident_url": "https://...",
        "message": "AYS Incident created successfully."
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        slack_user_id = data.get("user_id", "unknown")
        query_text = data.get("query", "").strip()
        classification = data.get("classification", "")
        classification_reason = data.get("classification_reason", "")
        short_desc = data.get("short_description", "Citrix issue reported via Slack bot")
        channel_id = data.get("channel_id", "")
        thread_ts = data.get("thread_ts", "")
        autoheal_incno = data.get("autoheal_incno", "")

        logger.info(f"Creating AYS incident for user {slack_user_id}, classification={classification}")

        # ── DUMMY AYS RESPONSE (replace with real API later) ──────
        incident_number = f"INC{int(time.time())}"
        incident_id = str(uuid.uuid4())
        incident_url = f"https://verizon.service-now.com/nav_to.do?uri=incident.do?sysparm_query=number={incident_number}"

        incident_result = {
            "status": "success",
            "incident_id": incident_id,
            "incident_number": incident_number,
            "incident_url": incident_url,
            "short_description": short_desc,
            "assigned_group": "Citrix Support",
            "priority": "3",
            "message": f"AYS Incident {incident_number} created successfully.",
        }
        # ──────────────────────────────────────────────────────────

        # Store in DB for tracking
        try:
            autoheal_request_id = None
            if autoheal_incno:
                conn = _get_db_connection()
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT id FROM citrix_autoheal_requests WHERE incno = %s",
                        (autoheal_incno,)
                    )
                    row = cur.fetchone()
                    autoheal_request_id = row[0] if row else None
                finally:
                    conn.close()

            conn = _get_db_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO citrix_ays_incidents
                       (autoheal_request_id, slack_user_id, incident_number, incident_url,
                        short_description, query_text, classification, channel_id, thread_ts)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (autoheal_request_id, slack_user_id, incident_number, incident_url,
                     short_desc, query_text, classification, channel_id, thread_ts)
                )
                conn.commit()
            finally:
                conn.close()
            logger.info(f"AYS incident saved to DB: {incident_number}")
        except Exception as db_err:
            logger.error(f"Failed to save AYS incident to DB (continuing): {db_err}")

        logger.info(f"AYS incident created: {incident_number} for user {slack_user_id}")
        return jsonify(incident_result), 201

    except Exception as e:
        logger.error(f"Error in create_ays_incident: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"{type(e).__name__}: {str(e)}"
        }), 500


# ── User Lookup (EID + Name from BigQuery) ───────────────────────

@app.route('/lookup-user', methods=['POST'])
def lookup_user():
    """
    Look up a user's EID and display name from BigQuery given their Slack ID.

    Expected JSON:
    {
        "slack_id": "U08ASQAJJ4D"
    }

    Returns:
    {
        "status": "success",
        "slack_id": "U08ASQAJJ4D",
        "eid": "0012345678",
        "user_name": "Prasanna Venkatesh"
    }
    """
    try:
        data = request.get_json()
        if not data or not data.get("slack_id"):
            return jsonify({"status": "error", "message": "slack_id is required"}), 400

        slack_id = data["slack_id"].strip()
        logger.info(f"User lookup request for slack_id={slack_id}")

        # Query BigQuery for EID from Slack user object table
        # Table only has: slackid, eid
        query = """
            SELECT eid
            FROM `vz-it-np-voev-dev-voevdo-0.slack.vea_slack_userobj_data`
            WHERE slackid = @slack_id
            LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("slack_id", "STRING", slack_id),
            ]
        )
        rows = list(bq_client.query(query, job_config=job_config).result())

        if not rows:
            logger.warning(f"No user found for slack_id={slack_id}")
            return jsonify({
                "status": "not_found",
                "slack_id": slack_id,
                "message": "User not found in BigQuery"
            }), 404

        row = rows[0]
        eid = str(row.eid or "").strip().zfill(10) if row.eid else ""

        # Step 2: Get user name from HR table using EID
        user_name = ""
        try:
            hr_query = """
                SELECT name
                FROM `vz-it-np-voev-dev-voevdo-0.empan_dev_tbls.vea_hr_latest_data`
                WHERE b_eid = @eid
                LIMIT 1
            """
            hr_job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("eid", "STRING", eid),
                ]
            )
            hr_rows = list(bq_client.query(hr_query, job_config=hr_job_config).result())
            if hr_rows and hr_rows[0].name:
                user_name = str(hr_rows[0].name).strip().strip('"')
                logger.info(f"HR name lookup success: eid={eid} → name={user_name}")
            else:
                logger.warning(f"No HR record found for eid={eid}")
        except Exception as hr_err:
            logger.error(f"HR name lookup failed for eid={eid}: {hr_err}")

        logger.info(f"User lookup success: slack_id={slack_id} → eid={eid}, name={user_name}")
        return jsonify({
            "status": "success",
            "slack_id": slack_id,
            "eid": eid,
            "user_name": user_name,
        }), 200

    except Exception as e:
        logger.error(f"Error in lookup_user: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"{type(e).__name__}: {str(e)}"
        }), 500


# ──────────────────────────────────────────────────────────────────
# CHANNEL AI BOT — Slack Channel Knowledge Assistant (via Dialogflow CX)
# ──────────────────────────────────────────────────────────────────
# Scalable architecture: any Slack channel can invite the bot.
# Uses Dialogflow CX agent → calls /channel-data-webhook Tool → BigQuery
# Same pattern as the Citrix bot.
#
# Flow:
#   Slack user → slackmain.py → POST /channel-chat
#     → detectIntent (Dialogflow CX Channel Agent)
#       → Agent playbook calls OpenAPI Tool → POST /channel-data-webhook
#         → Fetches channel messages + knowledge from BigQuery
#       → Agent LLM (Gemini) generates answer
#     ← response text
#   ← JSON to Slack bot → threaded reply

# BigQuery table for Slack channel data
SLACK_DATA_TABLE = f"{BQ_PROJECT}.slack.vea_slack_data"

# Slack workspace URL for building source thread links
SLACK_WORKSPACE_URL = "https://verizon.enterprise.slack.com"


def _build_slack_thread_link(channel_id: str, thread_ts: str) -> str:
    """Build a Slack thread permalink from channel_id and thread_ts."""
    if not thread_ts or not channel_id:
        return ""
    # Slack permalink format: /archives/{channel}/p{ts_without_dot}
    ts_no_dot = thread_ts.replace(".", "")
    return f"{SLACK_WORKSPACE_URL}/archives/{channel_id}/p{ts_no_dot}"


# BigQuery table for additional channel knowledge (FAQs, docs, etc.)
# Schema: id STRING, channel_id STRING, knowledge_type STRING,
#         title STRING, content TEXT, source_filename STRING,
#         created_at TIMESTAMP, updated_at TIMESTAMP
CHANNEL_KNOWLEDGE_TABLE = f"{BQ_PROJECT}.slack.channel_bot_knowledge"

# ── Dialogflow CX Agent for Channel Bot ─────────────────────────
# TODO: After creating the agent in Dialogflow CX console, paste the agent ID here.
CHANNEL_DFCX_PROJECT_ID = BQ_PROJECT  # same GCP project
CHANNEL_DFCX_LOCATION = "global"
CHANNEL_DFCX_AGENT_ID = "8b9420ba-7669-45df-aee4-76cc0a367ef9"

# Reuse the existing SessionsClient (shared with Citrix agent)
# _dfcx_client is already created above

# Initialize Vertex AI for direct Gemini fallback (optional — only needed if DFCX agent is not configured)
_gemini_model = None
if _VERTEXAI_AVAILABLE:
    try:
        # vertexai.init(project=BQ_PROJECT, location="us-central1")
        vertexai.init(project=BQ_PROJECT, location="us-east1")
        _gemini_model = GenerativeModel("gemini-2.5-flash")
        logger.info("Vertex AI / Gemini initialized for channel AI fallback")
    except Exception as _vai_err:
        logger.warning(f"Could not initialize Vertex AI: {_vai_err}")
else:
    logger.warning("vertexai not installed — Gemini fallback disabled, DFCX agent required")

# In-memory cache for channel data to avoid hammering BigQuery
_channel_data_cache: dict = {}
_CHANNEL_CACHE_TTL = 300  # seconds


# ── BigQuery data fetch functions ────────────────────────────────

def fetch_channel_messages(channel_id: str, limit: int = 200) -> list[dict]:
    """
    Fetch recent meaningful messages from a Slack channel stored in BigQuery.
    Filters out system messages (joins, leaves, bot messages, permission changes).
    """
    query = f"""
        SELECT text, channel_normalname, ts_time, slackuserid,
               thread_ts, reply_count, subtype, channel_name AS channel_id_col
        FROM `{SLACK_DATA_TABLE}`
        WHERE channel_name = @channel_id
          AND text IS NOT NULL
          AND TRIM(text) != ''
          AND (subtype IS NULL OR subtype NOT IN (
               'channel_join', 'channel_leave',
               'channel_posting_permissions', 'bot_message'))
        ORDER BY ts_time DESC
        LIMIT @msg_limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("channel_id", "STRING", channel_id),
            bigquery.ScalarQueryParameter("msg_limit", "INT64", limit),
        ]
    )
    rows = list(bq_client.query(query, job_config=job_config).result())
    messages = []
    for row in rows:
        raw_thread_ts = str(row.thread_ts or "") if row.thread_ts else ""
        messages.append({
            "text": str(row.text or "").strip(),
            "channel_name": str(row.channel_normalname or ""),
            "timestamp": str(row.ts_time or ""),
            "user_id": str(row.slackuserid or ""),
            "is_thread_reply": bool(row.thread_ts),
            "reply_count": row.reply_count or 0,
            "thread_ts": raw_thread_ts,
            "channel_id": str(row.channel_id_col or channel_id),
        })
    logger.info(f"Fetched {len(messages)} messages for channel {channel_id}")
    return messages


def fetch_channel_knowledge(channel_id: str) -> list[dict]:
    """
    Fetch additional knowledge (FAQs, docs, announcements) for a channel
    from the channel_bot_knowledge BigQuery table.
    Returns empty list gracefully if the table doesn't exist or has no data.
    """
    try:
        query = f"""
            SELECT id, knowledge_type, title, content, source_filename, updated_at
            FROM `{CHANNEL_KNOWLEDGE_TABLE}`
            WHERE channel_id = @channel_id
            ORDER BY updated_at DESC
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("channel_id", "STRING", channel_id),
            ]
        )
        rows = list(bq_client.query(query, job_config=job_config).result())
        knowledge = []
        for row in rows:
            knowledge.append({
                "id": str(row.id or ""),
                "type": str(row.knowledge_type or "info"),
                "title": str(row.title or ""),
                "content": str(row.content or ""),
                "source_filename": str(row.source_filename or ""),
                "updated_at": str(row.updated_at or ""),
            })
        logger.info(f"Fetched {len(knowledge)} knowledge entries for channel {channel_id}")
        return knowledge
    except Exception as e:
        logger.warning(f"Could not fetch channel knowledge for {channel_id}: {e}")
        return []


def _get_channel_context(channel_id: str):
    """Get channel messages and knowledge with TTL-based caching."""
    now = time.time()
    if channel_id in _channel_data_cache:
        cached_at, cached = _channel_data_cache[channel_id]
        if now - cached_at < _CHANNEL_CACHE_TTL:
            return cached

    messages = fetch_channel_messages(channel_id)
    knowledge = fetch_channel_knowledge(channel_id)
    channel_name = messages[0]["channel_name"] if messages else channel_id

    result = (messages, knowledge, channel_name)
    _channel_data_cache[channel_id] = (now, result)
    return result


# ── Dialogflow CX detectIntent for Channel Agent ────────────────

def detect_intent_channel(session_id: str, text: str) -> str:
    """
    Call the Channel Dialogflow CX agent using detectIntent API.
    Returns the agent's text response as a string.
    Same pattern as detect_intent_citrix().
    """
    session_path = _dfcx_client.session_path(
        project=CHANNEL_DFCX_PROJECT_ID,
        location=CHANNEL_DFCX_LOCATION,
        agent=CHANNEL_DFCX_AGENT_ID,
        session=session_id,
    )

    text_input = dialogflowcx.TextInput(text=text)
    query_input = dialogflowcx.QueryInput(
        text=text_input,
        language_code="en",
    )

    response = _dfcx_client.detect_intent(
        request=dialogflowcx.DetectIntentRequest(
            session=session_path,
            query_input=query_input,
        )
    )

    response_texts = []
    for message in response.query_result.response_messages:
        if message.text:
            for t in message.text.text:
                if t.strip():
                    response_texts.append(t.strip())

    return "\n\n".join(response_texts) if response_texts else ""


# ── Direct Gemini fallback (used when DFCX agent is not configured) ──

def generate_channel_ai_response(channel_id: str, query_text: str) -> str:
    """
    Generate an AI answer using channel chat history + knowledge + Gemini directly.
    Used as fallback when Dialogflow CX agent is not yet configured.
    """
    if not _gemini_model:
        return ("Gemini fallback is not available (vertexai not installed). "
                "Please configure the Dialogflow CX agent or install google-cloud-aiplatform.")

    messages, knowledge, channel_name = _get_channel_context(channel_id)

    parts = []
    parts.append(
        f"You are a senior engineer who is deeply embedded in the *#{channel_name}* Slack channel.\n"
        "You have read every message and know this channel's history inside-out.\n"
        "When someone asks a question, answer like a sharp, helpful teammate — not a chatbot.\n\n"
        "VOICE & TONE:\n"
        "- Speak as a confident peer: direct, knowledgeable, no filler.\n"
        "- Use 'you' and 'we' naturally. Say 'Here's what works' not 'I found information suggesting'.\n"
        "- Be opinionated — recommend the best path, don't just list options neutrally.\n"
        "- Match the casual-professional tone of a Slack workspace (no corporate stiffness, no slang).\n\n"
        "STRICT RULES:\n"
        "1. *BE CONCISE*: Keep your entire answer under 150 words. Slack users scan, not read. Include brief 'how' details, not just 'what'.\n"
        "2. *LEAD WITH THE FIX*: Start with the most likely resolution — not a description of the problem.\n"
        "3. *MAX 3 SOLUTIONS*: If multiple fixes exist, list only the top 3, most likely first. Use numbered steps.\n"
        "4. *NO ESSAYS*: Never list every message or user who mentioned the topic. Synthesize into one answer.\n"
        "5. *ALWAYS ANSWER*: If channel history and knowledge base have no relevant data, use your own general\n"
        "   knowledge to answer. NEVER say 'I cannot find any information'.\n"
        "6. *FORMAT FOR SLACK*: Use *bold* for key points. Use numbered lists for steps. No markdown headers.\n"
        "7. *END WITH NEXT STEP*: Always close with a clear next step. If escalation is needed, say the admin/support team will follow up — never tell the user to 'contact your admin'.\n"
        "8. Thread replies (marked [thread reply]) are more likely to contain solutions than original posts.\n"
        "9. If channel knowledge/FAQs are provided, treat them as authoritative.\n"
        "10. *COMBINE SOURCES*: When channel data is available, combine it with your own knowledge for the best answer.\n"
        "11. *SOURCE ATTRIBUTION*: Always end your answer with exactly one source line:\n"
        "    - If you used channel messages → 'Source: <relevant Slack thread link>'\n"
        "    - If you used knowledge base entries → 'Source: Knowledge Base'\n"
        "    - If you used your own general knowledge → 'Source: Gemini AI'\n"
    )

    if knowledge:
        parts.append("=== CHANNEL KNOWLEDGE & FAQs ===")
        for k in knowledge:
            label = k.get("title") or k.get("type", "info")
            parts.append(f"[{label}]: {k['content']}")
        parts.append("")

    if messages:
        parts.append(f"=== RECENT CHANNEL MESSAGES ({len(messages)} messages) ===")
        total_chars = 0
        max_context_chars = 60000
        chronological = list(reversed(messages))
        for msg in chronological:
            line = f"[{msg['timestamp']}] User {msg['user_id']}: {msg['text']}"
            if msg["is_thread_reply"]:
                line += " [thread reply]"
            if total_chars + len(line) > max_context_chars:
                parts.append("... (older messages truncated for brevity)")
                break
            parts.append(line)
            total_chars += len(line)
        parts.append("")

    parts.append(f"=== USER QUESTION ===\n{query_text}")

    prompt = "\n".join(parts)
    logger.info(
        f"Generating channel AI response for #{channel_name} ({channel_id}), "
        f"prompt length: {len(prompt)} chars"
    )

    response = _gemini_model.generate_content(prompt)
    return response.text


# ── API Endpoints ────────────────────────────────────────────────

@app.route('/channel-data-webhook', methods=['POST'])
def channel_data_webhook():
    """
    Dialogflow CX OpenAPI Tool endpoint.
    Called by the Channel AI agent's Tool to fetch channel data from BigQuery.
    Returns channel messages + knowledge as JSON for the agent LLM to process.

    Expected JSON (from Dialogflow CX Tool):
    {
        "channel_id": "C0AE1G97QCV",
        "search_query": "access denied error"  (optional — for future keyword filtering)
    }

    Returns:
    {
        "channel_name": "help-gemini-enterprise",
        "messages_count": 150,
        "knowledge_count": 3,
        "messages": [...],
        "knowledge": [...]
    }
    """
    logger.info("Channel data webhook called")

    data = request.get_json(silent=True) or {}
    channel_id = data.get("channel_id", "").strip()

    if not channel_id:
        return jsonify({
            "error": "Missing required field: 'channel_id'",
            "messages": [],
            "knowledge": []
        }), 400

    messages, knowledge, channel_name = _get_channel_context(channel_id)

    # ── Keyword filtering ────────────────────────────────────────
    # If the agent passes a search_query, filter messages to only
    # those containing at least one keyword.  This keeps the payload
    # small enough for the DFCX 8192-token context window.
    search_query = data.get("search_query", "").strip().lower()
    if search_query:
        keywords = [w for w in search_query.split() if len(w) > 2]
        if keywords:
            filtered = [
                m for m in messages
                if any(kw in m["text"].lower() for kw in keywords)
            ]
            # Fall back to most recent if no keyword matches
            messages = filtered if filtered else messages

    # ── DFCX token budget ────────────────────────────────────────
    # DFCX agent has an 8192 token limit (~4 chars per token).
    # Reserve ~2000 tokens for knowledge + agent instructions,
    # leaving ~6000 tokens (~24000 chars) for messages.
    MAX_MSG_CHARS = 20000
    MAX_MSGS = 30           # hard cap on number of messages
    MAX_MSG_TEXT = 400       # enough to capture resolutions, not just problem snippets

    truncated_messages = []
    total_chars = 0
    # Score each thread link by how many keyword-matching messages it contains
    thread_link_scores: dict[str, int] = {}
    search_keywords = [w for w in search_query.split() if len(w) > 2] if search_query else []
    for msg in messages[:MAX_MSGS]:
        text = msg["text"][:MAX_MSG_TEXT]
        entry = {
            "text": text,
            "timestamp": msg["timestamp"],
            "user_id": msg["user_id"],
            "is_reply": msg.get("is_thread_reply", False),
        }
        # Build source thread link and score by keyword relevance
        msg_channel_id = msg.get("channel_id", channel_id)
        msg_thread_ts = msg.get("thread_ts", "")
        if msg_thread_ts:
            link = _build_slack_thread_link(msg_channel_id, msg_thread_ts)
            if link:
                # Score: count how many search keywords appear in this message
                text_lower = msg["text"].lower()
                matches = sum(1 for kw in search_keywords if kw in text_lower) if search_keywords else 0
                # Boost thread replies (more likely to contain solutions)
                if msg.get("is_thread_reply", False):
                    matches += 1
                thread_link_scores[link] = thread_link_scores.get(link, 0) + max(matches, 1)
        entry_size = len(text) + len(msg["timestamp"]) + len(msg["user_id"]) + 40
        if total_chars + entry_size > MAX_MSG_CHARS:
            break
        truncated_messages.append(entry)
        total_chars += entry_size

    # Sort thread links by relevance score (highest first)
    ranked_source_links = sorted(thread_link_scores.keys(),
                                  key=lambda lnk: thread_link_scores[lnk],
                                  reverse=True)[:5]

    # Truncate knowledge content (keep concise for token budget)
    truncated_knowledge = []
    for k in knowledge[:10]:
        truncated_knowledge.append({
            "type": k["type"],
            "title": k.get("title", ""),
            "content": k["content"][:500],
            "source_url": k.get("source_filename", ""),
        })

    logger.info(
        f"Returning {len(truncated_messages)}/{len(messages)} msgs, "
        f"{len(truncated_knowledge)} knowledge entries for {channel_id} "
        f"(~{total_chars} chars)"
    )

    return jsonify({
        "channel_name": channel_name,
        "messages_count": len(truncated_messages),
        "knowledge_count": len(truncated_knowledge),
        "response_instructions": (
            "VOICE: Answer like a senior engineer and trusted teammate — confident, direct, no filler. "
            "Use 'you' and 'we' naturally. Say 'Here's what works' not 'I found information suggesting'. "
            "Be opinionated — recommend the best path. Match casual-professional Slack tone. "
            "RESPOND in under 150 words. "
            "LEAD with the fix, not the problem description. "
            "For each solution, include a brief HOW (e.g. 'Open Settings > Integrations > Add app'), not just WHAT. "
            "Max 3 numbered solutions, most likely first. "
            "Use *bold* for key actions. No markdown headers. "
            "End with a clear next step. If escalation needed, say the admin/support team will follow up. "
            "Thread replies marked is_reply=true are more likely to contain solutions. "
            "SOURCE ATTRIBUTION — always end your answer with exactly one 'Source:' line: "
            "• If your answer uses information from channel messages → 'Source: <most relevant link from source_thread_links>' "
            "• If your answer uses knowledge base entries → 'Source: Knowledge Base' — and if the entry has a source_url, use 'Source: Knowledge Base — <source_url>' "
            "• If your answer comes from your own general knowledge (no channel or KB data matched) → 'Source: Gemini AI' "
            "• If combining sources, cite the primary one. "
            "IMPORTANT: If the channel messages and knowledge base do NOT contain relevant information "
            "for the user's question, you MUST still provide a helpful answer using your own general knowledge. "
            "NEVER say 'I cannot find any information' or 'no data found' — always provide a useful answer. "
            "When channel data IS available, combine it with your own knowledge to give the most accurate, "
            "complete answer. Synthesize both sources into one clear response."
        ),
        "source_thread_links": ranked_source_links,
        "messages": truncated_messages,
        "knowledge": truncated_knowledge,
    }), 200


@app.route('/channel-chat', methods=['POST'])
def channel_chat_endpoint():
    """
    API endpoint for the Slack bot to query the Channel AI assistant.
    Routes through Dialogflow CX agent (primary) or direct Gemini (fallback).

    Expected JSON payload:
    {
        "query": "What issues have been reported recently?",
        "channel_id": "C0AE1G97QCV",
        "session_id": "slack-U12345-ts"  (optional)
    }

    Returns:
    {
        "status": "success",
        "response": "AI-generated answer..."
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        query_text = data.get("query", "").strip()
        channel_id = data.get("channel_id", "").strip()

        if not query_text:
            return jsonify({"status": "error", "message": "Missing required field: 'query'"}), 400
        if not channel_id:
            return jsonify({"status": "error", "message": "Missing required field: 'channel_id'"}), 400

        session_id = data.get("session_id", "default")
        logger.info(f"Channel chat request: channel={channel_id}, session={session_id}, query='{query_text[:80]}'")

        answer = ""

        # Primary path: Dialogflow CX agent
        if CHANNEL_DFCX_AGENT_ID and CHANNEL_DFCX_AGENT_ID != "REPLACE_WITH_AGENT_ID":
            try:
                # Prefix session with channel_id so the agent knows which channel
                dfcx_session = f"{channel_id}-{session_id}"
                answer = detect_intent_channel(dfcx_session, query_text)
            except Exception as dfcx_err:
                err_str = str(dfcx_err)
                if "Token limit exceeded" in err_str or "FAILED_PRECONDITION" in err_str:
                    fresh = f"{dfcx_session}-{uuid.uuid4().hex[:8]}"
                    logger.warning(f"Session {dfcx_session} hit token limit, retrying with {fresh}")
                    answer = detect_intent_channel(fresh, query_text)
                else:
                    logger.warning(f"DFCX agent failed, falling back to direct Gemini: {dfcx_err}")
                    answer = generate_channel_ai_response(channel_id, query_text)
        else:
            # Fallback: direct Gemini (agent not configured yet)
            logger.info("DFCX agent not configured, using direct Gemini fallback")
            answer = generate_channel_ai_response(channel_id, query_text)

        if not answer:
            answer = "I'm sorry, I couldn't generate a response. Please try again."

        logger.info(f"Channel chat response: {len(answer)} chars")
        return jsonify({"status": "success", "response": answer}), 200

    except Exception as e:
        logger.error(f"Error in channel_chat: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"{type(e).__name__}: {str(e)}"
        }), 500


@app.route('/channel-chat/refresh-cache', methods=['POST'])
def channel_chat_refresh_cache():
    """
    Force-refresh the cached channel data for a given channel_id.
    Useful after adding new knowledge or when fresh data is needed.
    """
    data = request.get_json(silent=True) or {}
    channel_id = data.get("channel_id", "").strip()
    if not channel_id:
        return jsonify({"status": "error", "message": "Missing 'channel_id'"}), 400

    if channel_id in _channel_data_cache:
        del _channel_data_cache[channel_id]

    _get_channel_context(channel_id)
    return jsonify({"status": "success", "message": f"Cache refreshed for {channel_id}"}), 200


# ──────────────────────────────────────────────────────────────────
# CHANNEL KNOWLEDGE MANAGEMENT API — CRUD + File Upload
# ──────────────────────────────────────────────────────────────────
# Allows channel teams to add, update, delete, and upload knowledge
# (FAQs, docs, CSV, text files) that the bot uses as context.
#
# BigQuery table schema (channel_bot_knowledge):
#   id              STRING    (UUID, primary key)
#   channel_id      STRING    (Slack channel ID)
#   knowledge_type  STRING    (faq, document, announcement, csv_data, etc.)
#   title           STRING    (human-readable title)
#   content         STRING    (the actual knowledge text)
#   source_filename STRING    (original filename if uploaded)
#   created_at      TIMESTAMP
#   updated_at      TIMESTAMP

def _insert_knowledge_row(channel_id: str, knowledge_type: str, title: str,
                          content: str, source_filename: str = "") -> str:
    """Insert a knowledge row into BigQuery. Returns the generated ID."""
    row_id = str(uuid.uuid4())
    query = f"""
        INSERT INTO `{CHANNEL_KNOWLEDGE_TABLE}`
        (id, channel_id, knowledge_type, title, content, source_filename, created_at, updated_at)
        VALUES
        (@id, @channel_id, @knowledge_type, @title, @content, @source_filename,
         CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", row_id),
            bigquery.ScalarQueryParameter("channel_id", "STRING", channel_id),
            bigquery.ScalarQueryParameter("knowledge_type", "STRING", knowledge_type),
            bigquery.ScalarQueryParameter("title", "STRING", title),
            bigquery.ScalarQueryParameter("content", "STRING", content),
            bigquery.ScalarQueryParameter("source_filename", "STRING", source_filename),
        ]
    )
    bq_client.query(query, job_config=job_config).result()
    logger.info(f"Inserted knowledge {row_id} for channel {channel_id}")
    return row_id


@app.route('/channel-knowledge', methods=['GET'])
def list_channel_knowledge():
    """
    List all knowledge entries for a channel.

    Query params:
        channel_id (required): Slack channel ID
    """
    channel_id = request.args.get("channel_id", "").strip()
    if not channel_id:
        return jsonify({"status": "error", "message": "Missing query param: 'channel_id'"}), 400

    entries = fetch_channel_knowledge(channel_id)
    return jsonify({
        "status": "success",
        "channel_id": channel_id,
        "count": len(entries),
        "entries": entries
    }), 200


@app.route('/channel-knowledge', methods=['POST'])
def add_channel_knowledge():
    """
    Add a knowledge entry for a channel.

    Expected JSON:
    {
        "channel_id": "C0AE1G97QCV",
        "knowledge_type": "faq",        (optional, defaults to "info")
        "title": "How to get access",   (optional)
        "content": "Submit a request..." (required)
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON data provided"}), 400

    channel_id = data.get("channel_id", "").strip()
    content = data.get("content", "").strip()

    if not channel_id:
        return jsonify({"status": "error", "message": "Missing 'channel_id'"}), 400
    if not content:
        return jsonify({"status": "error", "message": "Missing 'content'"}), 400

    knowledge_type = data.get("knowledge_type", "info").strip()
    title = data.get("title", "").strip()
    source_filename = data.get("source_filename", "").strip()

    try:
        row_id = _insert_knowledge_row(channel_id, knowledge_type, title, content, source_filename)
        # Invalidate cache so next query picks up new knowledge
        _channel_data_cache.pop(channel_id, None)
        return jsonify({"status": "success", "id": row_id, "message": "Knowledge added"}), 201
    except Exception as e:
        logger.error(f"Error adding knowledge: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/channel-knowledge/<knowledge_id>', methods=['PUT'])
def update_channel_knowledge(knowledge_id):
    """
    Update an existing knowledge entry.

    Expected JSON:
    {
        "channel_id": "C0AE1G97QCV",  (required for cache invalidation)
        "title": "Updated title",      (optional)
        "content": "Updated content",  (optional)
        "knowledge_type": "faq"        (optional)
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON data provided"}), 400

    channel_id = data.get("channel_id", "").strip()
    if not channel_id:
        return jsonify({"status": "error", "message": "Missing 'channel_id'"}), 400

    # Build SET clauses dynamically
    set_parts = ["updated_at = CURRENT_TIMESTAMP()"]
    params = [
        bigquery.ScalarQueryParameter("knowledge_id", "STRING", knowledge_id),
        bigquery.ScalarQueryParameter("channel_id", "STRING", channel_id),
    ]

    if "title" in data:
        set_parts.append("title = @title")
        params.append(bigquery.ScalarQueryParameter("title", "STRING", data["title"]))
    if "content" in data:
        set_parts.append("content = @content")
        params.append(bigquery.ScalarQueryParameter("content", "STRING", data["content"]))
    if "knowledge_type" in data:
        set_parts.append("knowledge_type = @knowledge_type")
        params.append(bigquery.ScalarQueryParameter("knowledge_type", "STRING", data["knowledge_type"]))
    if "source_filename" in data:
        set_parts.append("source_filename = @source_filename")
        params.append(bigquery.ScalarQueryParameter("source_filename", "STRING", data["source_filename"]))

    if len(set_parts) == 1:
        return jsonify({"status": "error", "message": "No fields to update"}), 400

    try:
        query = f"""
            UPDATE `{CHANNEL_KNOWLEDGE_TABLE}`
            SET {', '.join(set_parts)}
            WHERE id = @knowledge_id AND channel_id = @channel_id
        """
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        result = bq_client.query(query, job_config=job_config).result()
        _channel_data_cache.pop(channel_id, None)
        return jsonify({"status": "success", "message": f"Knowledge {knowledge_id} updated"}), 200
    except Exception as e:
        logger.error(f"Error updating knowledge {knowledge_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/channel-knowledge/<knowledge_id>', methods=['DELETE'])
def delete_channel_knowledge(knowledge_id):
    """
    Delete a knowledge entry.

    Query params:
        channel_id (required): Slack channel ID (for security — must match)
    """
    channel_id = request.args.get("channel_id", "").strip()
    if not channel_id:
        return jsonify({"status": "error", "message": "Missing query param: 'channel_id'"}), 400

    try:
        query = f"""
            DELETE FROM `{CHANNEL_KNOWLEDGE_TABLE}`
            WHERE id = @knowledge_id AND channel_id = @channel_id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("knowledge_id", "STRING", knowledge_id),
                bigquery.ScalarQueryParameter("channel_id", "STRING", channel_id),
            ]
        )
        bq_client.query(query, job_config=job_config).result()
        _channel_data_cache.pop(channel_id, None)
        return jsonify({"status": "success", "message": f"Knowledge {knowledge_id} deleted"}), 200
    except Exception as e:
        logger.error(f"Error deleting knowledge {knowledge_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/channel-knowledge/upload', methods=['POST'])
def upload_channel_knowledge():
    """
    Upload a file (txt, csv, doc/docx) as channel knowledge.
    The file content is extracted, stored in BigQuery against the channel.

    FormData fields:
        channel_id     (required): Slack channel ID
        file           (required): The file to upload (.txt, .csv, .docx, .doc)
        knowledge_type (optional): Type label (default: 'document')
        title          (optional): Human-readable title (default: filename)

    Supported file types:
        .txt   — stored as-is
        .csv   — parsed and formatted as readable text
        .docx  — plain text extracted (requires python-docx)
        .doc   — not supported (legacy binary format)
    """
    channel_id = request.form.get("channel_id", "").strip()
    if not channel_id:
        return jsonify({"status": "error", "message": "Missing 'channel_id'"}), 400

    if "file" not in request.files:
        return jsonify({"status": "error", "message": "Missing 'file' in upload"}), 400

    uploaded = request.files["file"]
    if not uploaded.filename:
        return jsonify({"status": "error", "message": "Empty filename"}), 400

    filename = uploaded.filename
    knowledge_type = request.form.get("knowledge_type", "document").strip()
    title = request.form.get("title", "").strip() or filename
    ext = os.path.splitext(filename)[1].lower()

    try:
        raw_bytes = uploaded.read()

        if ext == ".txt":
            content = raw_bytes.decode("utf-8", errors="replace")

        elif ext == ".csv":
            text_data = raw_bytes.decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(text_data))
            rows = list(reader)
            if not rows:
                return jsonify({"status": "error", "message": "CSV file is empty"}), 400
            # Format CSV as readable text: header + rows
            header = rows[0]
            formatted_rows = []
            for row in rows[1:]:
                row_text = "; ".join(
                    f"{header[i]}: {row[i]}" if i < len(header) else row[i]
                    for i in range(len(row))
                )
                formatted_rows.append(row_text)
            content = f"CSV Data ({len(formatted_rows)} rows):\n" + "\n".join(formatted_rows)

        elif ext == ".docx":
            try:
                import docx
                doc = docx.Document(io.BytesIO(raw_bytes))
                content = "\n".join(para.text for para in doc.paragraphs if para.text.strip())
            except ImportError:
                return jsonify({
                    "status": "error",
                    "message": "python-docx not installed. Use .txt or .csv instead."
                }), 400

        elif ext == ".doc":
            return jsonify({
                "status": "error",
                "message": ".doc (legacy Word) is not supported. Please convert to .docx or .txt."
            }), 400

        else:
            return jsonify({
                "status": "error",
                "message": f"Unsupported file type: {ext}. Supported: .txt, .csv, .docx"
            }), 400

        if not content.strip():
            return jsonify({"status": "error", "message": "File has no extractable content"}), 400

        # Truncate to 500KB to stay within BigQuery limits
        max_content = 500_000
        if len(content) > max_content:
            content = content[:max_content] + "\n\n... (content truncated)"

        row_id = _insert_knowledge_row(channel_id, knowledge_type, title, content, filename)
        _channel_data_cache.pop(channel_id, None)

        return jsonify({
            "status": "success",
            "id": row_id,
            "message": f"File '{filename}' uploaded as knowledge",
            "content_length": len(content),
        }), 201

    except Exception as e:
        logger.error(f"Error uploading knowledge file: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ──────────────────────────────────────────────────────────────────
# HR INSIGHTS CHATBOT — Conversational workforce analytics via Gemini
# ──────────────────────────────────────────────────────────────────
# The frontend sends a pre-aggregated data summary (no raw PII rows)
# along with the user's question and conversation history.
# Gemini generates a natural-language analytical response.
# Nothing is stored — everything is in-memory per request.

HR_CHAT_SYSTEM_PROMPT = """You are an expert HR Analytics Assistant embedded in a workforce intelligence dashboard.
You help HR leaders analyze employee performance, demographics, and workforce trends.

VOICE & TONE:
- Speak as a sharp, data-savvy HR partner — confident, analytical, no corporate filler.
- Use specific numbers from the summary data provided. Say "42% are Exceeding" not "a large portion".
- Be opinionated when asked for recommendations — suggest actions, flag risks.
- Keep answers concise (under 300 words) unless the user asks for a detailed breakdown.

DATA YOU RECEIVE:
The WORKFORCE DATA SUMMARY contains pre-computed analytics matching what the dashboard shows:
1. OVERVIEW — total employees, gender split
2. RATING DISTRIBUTIONS — current & previous year, with target caps (TP=5%, Ex=15%, Ach=65%, App=10%, NI=2%)
3. YEAR-OVER-YEAR CHANGE — rating shifts and movement classification (Improved/Maintained/Declined)
4. LEADER PERFORMANCE — every leader's rating breakdown with Success Index (0-100 weighted score: TP=100, Ex=80, Ach=60, App=40, NI=20)
5. LEADER YoY MOVEMENT — per-leader improved/maintained/declined counts
6. BUSINESS UNIT, BAND, LOCATION, WORK MODE, TENURE breakdowns — each with rating distribution + Success Index
7. SUPERVISOR PERFORMANCE — direct manager rating breakdowns
8. PROMOTION COHORT — performance of promoted employees by year + PROMOTED EMPLOYEES DETAIL (each employee with name, business, leader, band, rating)
9. MATERNITY STATUS — performance by maternity category + MATERNITY EMPLOYEES DETAIL (each employee with name, business, leader, band, rating)
10. ROLE/TITLE — performance by job title
11. HIRING COHORT (YOJ) — performance by year of joining
12. INDIVIDUAL EMPLOYEES — EVERY employee's row with: Name | Band | Location | Business | Leader | Supervisor | Tenure | Gender | Maternity status | Promotion status | Current Rating | Previous Rating

CRITICAL: Sections 8, 9, and 12 contain INDIVIDUAL employee rows with their Name, Business Group, and Leader. When asked "from which business group" or "who are these employees" — scan these sections to find the exact employees and report their details. NEVER say you cannot identify employees when these sections exist in the data.

The Success Index = weighted average of ratings (excluding New/Not Rated). Higher = better team performance.
Rating Rank: Top Performing(best) > Exceeding > Achieving > Approaching > Needs Improvement(worst).

CAPABILITIES:
- Rank leaders by performance (use Success Index), identify top/bottom performers
- Compare any two leaders, business units, bands, locations, etc. side by side
- Analyze YoY rating trends — who improved, who declined, by how much
- Identify leaders not meeting rating caps (e.g., too many TP exceeding 5% target)
- Break down any dimension: "How does Chennai compare to Hyderabad?", "Band 7V vs 8V?"
- Answer "who" questions about specific employees when individual data is provided
- Calculate gaps, variances, and highlight risks or imbalances
- Promotion effectiveness, tenure impact, maternity equity analysis
- Threshold violation analysis: identify leaders/bands/locations/tenure groups exceeding rating caps (TP>5%, Ex>15%, Ach>65%, App>10%, NI>2%) — pre-computed in THRESHOLD VIOLATIONS sections
- Double dip / double hike detection: employees whose rating improved or declined 2+ consecutive years — pre-computed in MULTI-YEAR TRAJECTORIES section
- Rating transition matrix: exactly how many employees moved between each rating pair year-over-year
- Employee-level drill-down: list specific employee names with their ratings, leaders, bands, business groups — ALWAYS available from INDIVIDUAL EMPLOYEES, MATERNITY EMPLOYEES DETAIL, and PROMOTED EMPLOYEES DETAIL sections

RULES:
1. ALWAYS ground your answer in the provided data summary. Never invent data.
2. Use percentages AND absolute numbers for clarity (e.g., "180 (28%) are Exceeding").
3. Format for readability: use bullet points, bold key numbers, short paragraphs. When presenting employee lists, comparisons, or multi-column data (e.g., name + business + leader + rating), use an HTML <table> with <thead> and <tbody>. Do NOT use tables for simple 1-2 point answers — only when the data has 3+ columns and multiple rows.
4. If the data doesn't contain enough info to answer, say so clearly — but check ALL sections first.
5. Never share data with external parties — remind the user this data is confidential if they ask about sharing.
6. When asked "who" questions with individual data provided, list names concisely.
7. For trend analysis, highlight year-over-year changes explicitly with direction arrows.
8. Use markdown bold (**text**) for emphasis, bullet lists for breakdowns.
9. When ranking leaders, always show the Success Index score and key rating counts.
10. When comparing, use a clear side-by-side format.
11. When asked about threshold violations, use the THRESHOLD VIOLATIONS sections — list every group that exceeds caps with their actual % vs target %.
12. For "double dip" or "double hike" questions, use the MULTI-YEAR TRAJECTORIES section. List affected employees by name with their full rating trajectory.
13. When asked to "drill down" or show employee-level data, use INDIVIDUAL EMPLOYEES and EMPLOYEE RATING TRANSITIONS sections. Always give names, bands, and leaders.
14. For transition/movement questions, reference the RATING TRANSITION MATRIX — show exactly how many employees moved between each rating pair.
15. When the user asks which groups "benefit" from top ratings, compare each group's TP+Exceeding % against the overall average. Rank groups from highest to lowest.
16. For MATERNITY questions — always use the MATERNITY EMPLOYEES DETAIL section to provide specific employee names, business groups, and leaders. Never say you cannot identify specific employees if this section is present.
17. For PROMOTION questions — always use the PROMOTED EMPLOYEES DETAIL section to provide specific employee names, business groups, leaders, and promo year. Cross-reference ratings from this section to answer "promoted + rated X" questions.
18. INDIVIDUAL EMPLOYEES section contains EVERY employee's full details: Name, Band, Location, Business Group, Leader, Supervisor, Tenure, Gender, Maternity status, Promotion status, and ratings. ALWAYS use this section to answer ANY drill-down question — identify specific employees by name with their business group and leader details. Never say "I cannot identify specific employees" when this section is present.
"""


@app.route('/hr-chat', methods=['POST'])
def hr_chat():
    """
    HR Insights Chatbot endpoint.
    Accepts aggregated workforce summary + question + conversation history.
    Returns Gemini-generated analytical response.

    Nothing is stored — completely stateless.

    Expected JSON:
    {
        "question": "How are ratings distributed across bands?",
        "summary": "Total: 6518 employees. Band distribution: ...",
        "chat_history": [
            {"role": "user", "text": "Show rating distribution"},
            {"role": "assistant", "text": "Here's the breakdown..."}
        ],
        "employee_context": "Optional: specific employee rows for targeted queries"
    }

    Returns:
    {
        "status": "success",
        "response": "Based on the data..."
    }
    """
    if not _gemini_model:
        return jsonify({
            "status": "error",
            "message": "Gemini model not available. Ensure google-cloud-aiplatform is installed."
        }), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        question = data.get("question", "").strip()
        if not question:
            return jsonify({"status": "error", "message": "Missing 'question'"}), 400

        summary = data.get("summary", "").strip()
        chat_history = data.get("chat_history", [])
        employee_context = data.get("employee_context", "").strip()

        # Build the prompt
        parts = [HR_CHAT_SYSTEM_PROMPT]

        if summary:
            parts.append(f"\n=== WORKFORCE DATA SUMMARY ===\n{summary}\n")

        if employee_context:
            parts.append(f"\n=== SPECIFIC EMPLOYEE DATA ===\n{employee_context}\n")

        # Add conversation history for multi-turn context
        if chat_history:
            parts.append("\n=== CONVERSATION HISTORY ===")
            for msg in chat_history[-10:]:  # Keep last 10 turns max
                role = msg.get("role", "user")
                text = msg.get("text", "")
                if role == "user":
                    parts.append(f"User: {text}")
                else:
                    parts.append(f"Assistant: {text}")
            parts.append("")

        parts.append(f"User: {question}")

        prompt = "\n".join(parts)
        logger.info(f"HR chat request: question='{question[:80]}', prompt_len={len(prompt)}")

        response = _gemini_model.generate_content(prompt)
        answer = response.text.strip()

        logger.info(f"HR chat response: {len(answer)} chars")

        return jsonify({
            "status": "success",
            "response": answer
        }), 200

    except Exception as e:
        logger.error(f"Error in hr_chat: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"Failed to generate response: {str(e)}"
        }), 500


@app.route('/get-experience-score', methods=['POST'])
def get_experience_score():
    """
    API endpoint to retrieve experience score details from BigQuery by hostname and date.
    Fetches:
      1. Averaged scores from vea_uberagent_experience_score_summary
      2. Per-application detail from vea_uberagent_application_score_detail
      3. AI-powered analysis explaining scores, causes, and resolutions

    Negative values (-1) in application detail are treated as 0.
    NULL values are excluded from mean calculations.

    Expected JSON payload:
    {
        "hostname": "YYTYYHKGD4",
        "date": "2024-04-17"
    }

    Returns:
        JSON response with experience scores, application details, and AI analysis.
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON data provided"
            }), 400

        hostname = (data.get("hostname") or "").strip()
        date_str = (data.get("date") or "").strip()

        if not hostname:
            return jsonify({
                "status": "error",
                "message": "Missing required field: hostname"
            }), 400

        if not date_str:
            return jsonify({
                "status": "error",
                "message": "Missing required field: date (format: YYYY-MM-DD)"
            }), 400

        # Use the exact date provided by the caller
        query_date = date_str

        client = bigquery.Client()

        # ── Query 1: Experience Score Summary (averaged) ─────────
        summary_table = "`vz-it-np-voev-dev-voevdo-0.vea_device_data.vea_uberagent_experience_score_summary`"
        summary_query = f"""
            SELECT
                AVG(machine_score) AS machine_score,
                AVG(session_score) AS session_score,
                AVG(application_score) AS application_score,
                AVG(user_experience_score) AS user_experience_score,
                COUNT(*) AS row_count
            FROM {summary_table}
            WHERE host = @hostname AND date = @query_date
        """
        summary_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("hostname", "STRING", hostname),
                bigquery.ScalarQueryParameter("query_date", "DATE", query_date),
            ]
        )
        summary_results = client.query(summary_query, job_config=summary_config).result()
        summary_row = next(iter(summary_results), None)

        # If no data for computed date, inform the user of the latest available date
        if not summary_row or summary_row.row_count == 0:
            latest_date_query = f"""
                SELECT MAX(date) AS latest_date
                FROM {summary_table}
                WHERE host = @hostname
            """
            latest_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("hostname", "STRING", hostname),
                ]
            )
            latest_result = client.query(latest_date_query, job_config=latest_config).result()
            latest_row = next(iter(latest_result), None)

            if not latest_row or latest_row.latest_date is None:
                return jsonify({
                    "status": "success",
                    "message": f"No records found for hostname: {hostname}. No data available for any date.",
                    "data": {}
                }), 200

            latest_available = latest_row.latest_date.strftime("%Y-%m-%d") if hasattr(latest_row.latest_date, 'strftime') else str(latest_row.latest_date)

            return jsonify({
                "status": "no_data",
                "message": f"No data found for hostname '{hostname}' on computed date {query_date}. Latest available data is on {latest_available}. Please retry with that date.",
                "hostname": hostname,
                "requested_date": date_str,
                "computed_date": query_date,
                "latest_available_date": latest_available,
            }), 200

        experience_scores = {
            "date": query_date,
            "host": hostname,
            "machine_score": round(summary_row.machine_score, 2) if summary_row.machine_score is not None else None,
            "session_score": round(summary_row.session_score, 2) if summary_row.session_score is not None else None,
            "application_score": round(summary_row.application_score, 2) if summary_row.application_score is not None else None,
            "user_experience_score": round(summary_row.user_experience_score, 2) if summary_row.user_experience_score is not None else None,
            "rows_aggregated": summary_row.row_count,
        }

        # ── Query 2: Application Score Detail ────────────────────
        # Negative values (-1) are treated as 0 using GREATEST(..., 0)
        # NULL values are excluded from AVG (BigQuery default behavior)
        detail_table = "`vz-it-np-voev-dev-voevdo-0.vea_device_data.vea_uberagent_application_score_detail`"

        # First check if detail data exists for the same date; if not, use the closest prior date
        detail_date = query_date
        detail_check_query = f"""
            SELECT MAX(date) AS latest_date
            FROM {detail_table}
            WHERE host = @hostname AND date <= @query_date
        """
        detail_check_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("hostname", "STRING", hostname),
                bigquery.ScalarQueryParameter("query_date", "DATE", query_date),
            ]
        )
        detail_check_result = client.query(detail_check_query, job_config=detail_check_config).result()
        detail_check_row = next(iter(detail_check_result), None)
        if detail_check_row and detail_check_row.latest_date is not None:
            detail_date = detail_check_row.latest_date.strftime("%Y-%m-%d") if hasattr(detail_check_row.latest_date, 'strftime') else str(detail_check_row.latest_date)

        detail_query = f"""
            SELECT
                appname,
                appversion,
                COUNT(*) AS entry_count,
                AVG(CASE WHEN application_score IS NOT NULL THEN GREATEST(application_score, 0) END) AS avg_application_score,
                AVG(CASE WHEN ramusage_primary_score IS NOT NULL THEN GREATEST(ramusage_primary_score, 0) END) AS avg_ramusage_score,
                AVG(CASE WHEN cpuusage_primary_score IS NOT NULL THEN GREATEST(cpuusage_primary_score, 0) END) AS avg_cpuusage_score,
                AVG(CASE WHEN appio_primary_score IS NOT NULL THEN GREATEST(appio_primary_score, 0) END) AS avg_appio_score,
                AVG(CASE WHEN networklatency_primary_score IS NOT NULL THEN GREATEST(networklatency_primary_score, 0) END) AS avg_networklatency_score,
                AVG(CASE WHEN appuidelays_primary_score IS NOT NULL THEN GREATEST(appuidelays_primary_score, 0) END) AS avg_appuidelays_score,
                AVG(CASE WHEN apperror_score IS NOT NULL THEN GREATEST(apperror_score, 0) END) AS avg_apperror_score,
                SUM(CASE WHEN total_apperror IS NOT NULL THEN CAST(total_apperror AS INT64) ELSE 0 END) AS total_errors
            FROM {detail_table}
            WHERE host = @hostname AND date = @detail_date
            GROUP BY appname, appversion
            ORDER BY avg_application_score ASC
        """
        detail_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("hostname", "STRING", hostname),
                bigquery.ScalarQueryParameter("detail_date", "DATE", detail_date),
            ]
        )
        detail_results = client.query(detail_query, job_config=detail_config).result()

        app_details = []
        for row in detail_results:
            app_detail = {
                "appname": row.appname or "Unknown",
                "appversion": row.appversion if row.appversion and row.appversion != "null" else None,
                "entry_count": row.entry_count,
                "application_score": round(row.avg_application_score, 2) if row.avg_application_score is not None else None,
                "ram_usage_score": round(row.avg_ramusage_score, 2) if row.avg_ramusage_score is not None else None,
                "cpu_usage_score": round(row.avg_cpuusage_score, 2) if row.avg_cpuusage_score is not None else None,
                "app_io_score": round(row.avg_appio_score, 2) if row.avg_appio_score is not None else None,
                "network_latency_score": round(row.avg_networklatency_score, 2) if row.avg_networklatency_score is not None else None,
                "app_ui_delays_score": round(row.avg_appuidelays_score, 2) if row.avg_appuidelays_score is not None else None,
                "app_error_score": round(row.avg_apperror_score, 2) if row.avg_apperror_score is not None else None,
                "total_errors": row.total_errors or 0,
            }
            app_details.append(app_detail)

        # ── AI Analysis using Gemini ─────────────────────────────
        ai_analysis = None
        if _gemini_model and (app_details or experience_scores):
            try:
                analysis_prompt = _build_experience_score_analysis_prompt(
                    hostname, query_date, experience_scores, app_details
                )
                response = _gemini_model.generate_content(analysis_prompt)
                ai_analysis = response.text.strip()
            except Exception as ai_err:
                logger.warning(f"AI analysis failed for {hostname}: {ai_err}")
                ai_analysis = None

        return jsonify({
            "status": "success",
            "hostname": hostname,
            "date": query_date,
            "experience_scores": experience_scores,
            "application_details_date": detail_date,
            "application_details": app_details,
            "total_applications": len(app_details),
            "ai_analysis": ai_analysis,
        }), 200

    except Exception as e:
        logger.error(f"Error in get_experience_score: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Failed to retrieve experience score: {str(e)}"
        }), 500


def _build_experience_score_analysis_prompt(hostname, query_date, experience_scores, app_details):
    """Build the Gemini prompt for experience score analysis."""
    prompt = f"""You are a Device Performance Analyst for an enterprise IT team. Analyze the following device experience scores and application-level performance data. Provide a clear, actionable summary.

DEVICE: {hostname}
DATE: {query_date}

OVERALL EXPERIENCE SCORES:
- Machine Score: {experience_scores.get('machine_score', 'N/A')}
- Session Score: {experience_scores.get('session_score', 'N/A')}
- Application Score: {experience_scores.get('application_score', 'N/A')}
- User Experience Score: {experience_scores.get('user_experience_score', 'N/A')}

SCORING GUIDE (0-10 scale):
- 8-10: Excellent — no issues
- 6-8: Acceptable — minor issues
- 4-6: Poor — noticeable degradation
- 0-4: Critical — severe performance problems
- 0: App crashed, errored, or all metrics negative

APPLICATION-LEVEL DETAIL ({len(app_details)} apps):
"""
    for app in app_details:
        prompt += f"""
App: {app['appname']} (v{app['appversion'] or 'unknown'})
  - Application Score: {app['application_score']}
  - RAM Usage Score: {app['ram_usage_score']}
  - CPU Usage Score: {app['cpu_usage_score']}
  - App I/O Score: {app['app_io_score']}
  - Network Latency Score: {app['network_latency_score']}
  - UI Delays Score: {app['app_ui_delays_score']}
  - Error Score: {app['app_error_score']}
  - Total Errors: {app['total_errors']}
"""

    prompt += """
PROVIDE YOUR ANALYSIS IN THIS EXACT FORMAT:

## Summary
(2-3 sentence overview of device health)

## Key Findings
(Bullet points of the most important observations)

## Problem Applications
(List apps with score < 5 and explain WHY they scored poorly based on their sub-scores)

## Root Causes
(Based on the sub-scores — high RAM, CPU, network latency, UI delays, errors — explain the likely root causes)

## Recommendations
(Actionable steps to improve the experience score — be specific to the apps and metrics observed)

Keep analysis concise and actionable. Focus on the worst-performing apps and metrics.
If all scores are 0 with negative sub-scores, it likely means the app had no measurable activity during the window — note this.
"""
    return prompt


# ══════════════════════════════════════════════════════════════════
# DWP PULSE REPORT — AI-Powered Weekly Channel Analysis & Reporting
# ══════════════════════════════════════════════════════════════════
# Generates structured feedback reports from Slack channel conversations
# using Vertex AI (Gemini 2.5 Flash) + BigQuery + PostgreSQL.
#
# Flow:
#   1. POST /pulse-report/generate → fetch messages from BQ → Gemini analysis
#   2. Store results in PostgreSQL (dwppulse DB)
#   3. POST /pulse-report/send-email → sends HTML report via /send-email-dont-reply
#   4. Cloud Scheduler → cloudjob/main.py → calls generate + send weekly

# ── DWP Pulse DB Configuration ───────────────────────────────────
PULSE_DB_HOST = os.environ.get("PULSE_DB_HOST", DB_HOST)
PULSE_DB_PORT = int(os.environ.get("PULSE_DB_PORT", DB_PORT))
PULSE_DB_NAME = os.environ.get("PULSE_DB_NAME", "dwppulse")
PULSE_DB_USER = os.environ.get("PULSE_DB_USER", DB_USER)
PULSE_DB_PASSWORD = os.environ.get("PULSE_DB_PASSWORD", DB_PASSWORD)
PULSE_DB_UNIX_SOCKET = os.environ.get("PULSE_DB_UNIX_SOCKET", DB_UNIX_SOCKET)

# ── SkyVault Slack Real-Time API Configuration ───────────────────
# Used to fetch messages newer than what's available in BigQuery (24hr lag)
SKYVAULT_API_USERNAME = os.environ.get("SLACK_API_USERNAME", "slack_nops_vea")
SKYVAULT_API_PASSWORD = os.environ.get("SLACK_API_PASSWORD", "vea_slack_nops")
# Channel-specific SkyVault endpoints (channel_id → URL template)
SKYVAULT_CHANNEL_URLS = {
    "C0AE1G97QCV": os.environ.get(
        "SLACK_API_URL_1",
        "https://skyvaultapi.ebiz.verizon.com/generic_egress/slack_nops_HelpGeminiGoogleWorkspace?start_date={start_date}T00:00:00&end_date={end_date}T23:59:59&offset={offset}"
    ),
    "C0AH7K4QXCY": os.environ.get(
        "SLACK_API_URL_2",
        "https://skyvaultapi.ebiz.verizon.com/generic_egress/slack_nops_HelpNotebooklm?start_date={start_date}T00:00:00&end_date={end_date}T23:59:59&offset={offset}"
    ),
    "C0AHAB2DXFZ": os.environ.get(
        "SLACK_API_URL_3",
        "https://skyvaultapi.ebiz.verizon.com/generic_egress/slack_nops_digital_workplace_influencers?start_date={start_date}T00:00:00&end_date={end_date}T23:59:59&offset={offset}"
    ),
}
# Fallback SkyVault API for channels using second credential set
SKYVAULT_API2_USERNAME = os.environ.get("SLACK_API2_USERNAME", "slack_vea")
SKYVAULT_API2_PASSWORD = os.environ.get("SLACK_API2_PASSWORD", "vea_slack")
SKYVAULT_API2_URL = os.environ.get(
    "SLACK_API2_URL",
    "https://skyvaultapi.ebiz.verizon.com/generic_egress/slack_data?start_date={start_date}&end_date={end_date}&offset={offset}&channel_id={channel_ids}"
)
# Channel IDs that use the second credential set
SKYVAULT_API2_CHANNELS = set(
    os.environ.get("SLACK_API2_CHANNEL_IDS", "C014NGFR1NV,C0173TX4TV0,C0AD4NSFXGW,C0AGH6ZAR4N,C0AKL05F8P7,C0643RAS796,C08QT7UL8TV,C05CHPFT57A,C0AMW13T2FL").split(",")
)


def _get_pulse_db():
    """Get a PostgreSQL connection to the dwppulse database."""
    if PULSE_DB_UNIX_SOCKET:
        return pg8000.connect(
            user=PULSE_DB_USER,
            password=PULSE_DB_PASSWORD,
            database=PULSE_DB_NAME,
            unix_sock=f"{PULSE_DB_UNIX_SOCKET}/.s.PGSQL.5432",
        )
    return pg8000.connect(
        host=PULSE_DB_HOST,
        port=PULSE_DB_PORT,
        user=PULSE_DB_USER,
        password=PULSE_DB_PASSWORD,
        database=PULSE_DB_NAME,
    )


# ── Default Pulse Channels (seeded on first request) ────────────
PULSE_DEFAULT_CHANNELS = [
    {"channel_id": "C0AE1G97QCV", "channel_name": "help-gemini-google-workspace", "description": "Help Gemini Google Workspace"},
    {"channel_id": "C0AH7K4QXCY", "channel_name": "help-notebooklm", "description": "Help NotebookLM"},
    {"channel_id": "C0AHAB2DXFZ", "channel_name": "digital-workplace-influencers", "description": "Digital Workplace Influencers"},
    {"channel_id": "C0643RAS796", "channel_name": "help-gemini-google-workspace", "description": "Help Gemini Google Workspace"},
    {"channel_id": "C08QT7UL8TV", "channel_name": "C08QT7UL8TV", "description": ""},
    {"channel_id": "C05CHPFT57A", "channel_name": "digital-workplace-influencers", "description": "Digital Workplace Influencers"},
    {"channel_id": "C014NGFR1NV", "channel_name": "help-slack", "description": "Help Slack"},
    {"channel_id": "C0173TX4TV0", "channel_name": "help-google-workspace", "description": "Help Google Workspace"},
    {"channel_id": "C0AD4NSFXGW", "channel_name": "help-meet", "description": "Help Meet"},
    {"channel_id": "C0AGH6ZAR4N", "channel_name": "help-frontier-google-migration", "description": "Help Frontier Google Migration"},
    {"channel_id": "C0AKL05F8P7", "channel_name": "C0AKL05F8P7", "description": ""},
]

_pulse_channels_seeded = False


def _seed_pulse_channels():
    """Ensure default channels exist in the pulse_channels table."""
    global _pulse_channels_seeded
    if _pulse_channels_seeded:
        return
    try:
        conn = _get_pulse_db()
        cur = conn.cursor()
        for ch in PULSE_DEFAULT_CHANNELS:
            cur.execute(
                """INSERT INTO pulse_channels (channel_id, channel_name, description, added_by)
                   VALUES (%s, %s, %s, 'system')
                   ON CONFLICT (channel_id) DO NOTHING""",
                (ch["channel_id"], ch["channel_name"], ch["description"])
            )
        conn.commit()
        conn.close()
        _pulse_channels_seeded = True
        logger.info("Pulse: Default channels seeded successfully")
    except Exception as e:
        logger.warning(f"Pulse: Could not seed default channels: {e}")


# ── AI Prompt for Pulse Report Analysis ──────────────────────────
_PULSE_ANALYSIS_PROMPT = """You are a highly specialized Data Extraction and Summarization Engine for the Digital Workplace Strategy team at Verizon, focused on analyzing employee feedback and discussions from Slack channel messages.

### ALLOWED PRODUCTS
For the "product" key, use only one of the following product names (use the exact name as shown):
Google Workspace, Gemini, Gmail, Google Calendar, Google Meet, Google Drive, Google Docs, Google Slides, Google Sheets, Google Sites, Google Vids, NotebookLM, AYS, Poppulo, Generic Mailbox Management, Lucid, Distribution List Management, Proofpoint, External Collaboration Management, Polly, Slack, Cisco Board Pro, Smartsheets, BitWarden, VDI/Citrix, Jabber.
If no product can be identified, set "product" to "Unknown".

### OBJECTIVE
Extract and categorize the most significant feedback, major discussions, recurring issues, and important topics from the messages below.

### EXTRACTION RULES
* Focus on feedback mentioned by multiple people or generating significant discussion.
* Group similar feedback together — do not split minor differences into separate items.
* For each item, categorize as one of:
    - "AI Testimonial/Use Case" (positive AI feedback with specifics on how AI was used)
    - "Pain Point/Bug" (issues, failures, broken behavior)
    - "Feature Request" (new capabilities or enhancements)
    - "Common Question" (recurring how-to or clarification questions)
    - "Other" (relevant feedback that doesn't fit above)

### OUTPUT FORMAT
Return ONLY valid JSON (no markdown, no explanation):
{"results": [
  {
    "feedback_type": "Pain Point/Bug",
    "feedback_summary": "Concise summary of the feedback",
    "proposed_solution": "Actionable solution or N/A - Information Only",
    "product": "Product Name",
    "mention_count": 3,
    "source_message_ids": ["MSG-0001", "MSG-0005"]
  }
]}

Rules for new fields:
- mention_count: integer count of distinct messages that contributed to this feedback item.
- source_message_ids: list of [MSG-XXXX] tags (from the messages above) that contributed to this item. Include every relevant tag. If a message contributed to multiple items, include its tag in each.

If no significant feedback found: {"results": []}
Begin immediately with the opening curly brace.
"""

_PULSE_CONSOLIDATION_PROMPT = """You are a Feedback Consolidation Engine. Merge overlapping feedback across channels so similar ideas appear only once.

### ALLOWED PRODUCTS
Google Workspace, Gemini, Gmail, Google Calendar, Google Meet, Google Drive, Google Docs, Google Slides, Google Sheets, Google Sites, Google Vids, NotebookLM, AYS, Poppulo, Generic Mailbox Management, Lucid, Distribution List Management, Proofpoint, External Collaboration Management, Polly, Slack, Cisco Board Pro, Smartsheets, BitWarden, VDI/Citrix, Jabber.
If none match, use "Unknown".

### RULES
1. Merge items expressing the same core idea across channels
2. Keep unique items separate
3. Assign exactly ONE feedback_type and ONE product per item
4. feedback_type must be one of: "Pain Point/Bug", "Feature Request", "Common Question", "AI Testimonial/Use Case", "Other"
5. When merging items, combine their source_message_ids lists (deduplicated) and sum their mention_counts
6. For unmerged items, preserve mention_count and source_message_ids as-is

### OUTPUT
Return ONLY valid JSON:
{"results": [{"feedback_type": "...", "feedback_summary": "...", "proposed_solution": "...", "product": "...", "mention_count": 3, "source_message_ids": ["MSG-0001"]}]}
If no feedback: {"results": []}
Begin immediately with the opening curly brace.
"""


def _get_channel_data_range(channel_id: str) -> dict:
    """
    Query BigQuery to get the earliest and latest ts_time for a channel.
    Returns dict with 'start_date' and 'end_date' or None if no data.
    """
    try:
        range_query = f"""
            SELECT
                MIN(ts_time) AS earliest,
                MAX(ts_time) AS latest,
                COUNT(*) AS total_rows
            FROM `{SLACK_DATA_TABLE}`
            WHERE channel_name = @channel_id
              AND text IS NOT NULL
              AND TRIM(text) != ''
        """
        range_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("channel_id", "STRING", channel_id),
            ]
        )
        result = list(bq_client.query(range_query, job_config=range_config).result())
        if result and result[0].earliest:
            return {
                "start_date": str(result[0].earliest)[:10],
                "end_date": str(result[0].latest)[:10],
                "total_messages": result[0].total_rows,
            }
    except Exception as e:
        logger.warning(f"Could not fetch data range for channel {channel_id}: {e}")
    return None


def _fetch_realtime_slack_messages(channel_id: str, start_date: str, end_date: str) -> tuple:
    """
    Fetch real-time Slack messages via SkyVault API for channels where BQ has a 24hr lag.
    Returns tuple (messages, error_info):
      - messages: list of dicts with keys: text, channel_normalname, ts_time, slackuserid, thread_ts
      - error_info: None on success, or dict with gap_start, gap_end, reason on failure
    """
    # Determine which credentials and URL template to use
    if channel_id in SKYVAULT_CHANNEL_URLS:
        username = SKYVAULT_API_USERNAME
        password = SKYVAULT_API_PASSWORD
        url_template = SKYVAULT_CHANNEL_URLS[channel_id]
    elif channel_id in SKYVAULT_API2_CHANNELS:
        username = SKYVAULT_API2_USERNAME
        password = SKYVAULT_API2_PASSWORD
        url_template = SKYVAULT_API2_URL
    else:
        logger.info(f"Pulse SkyVault: No API endpoint configured for channel {channel_id}, skipping real-time fetch")
        return [], None

    all_messages = []
    offset = 0
    page_size = 1000
    error_info = None

    try:
        while True:
            url = url_template.format(
                start_date=start_date,
                end_date=end_date,
                offset=offset,
                channel_ids=channel_id,
            )
            resp = http_requests.get(
                url,
                auth=(username, password),
                timeout=60,
            )

            if resp.status_code != 200:
                logger.warning(f"Pulse SkyVault API error: status={resp.status_code}, channel={channel_id}")
                error_info = {
                    "gap_start": start_date,
                    "gap_end": end_date,
                    "reason": f"SkyVault API returned HTTP {resp.status_code}",
                }
                break

            data = resp.json()
            records = data if isinstance(data, list) else data.get("data", data.get("results", []))

            if not records:
                break

            for rec in records:
                text = rec.get("text") or rec.get("message_text") or ""
                if not text or not text.strip():
                    continue
                # Filter out system messages
                subtype = rec.get("subtype") or ""
                if subtype in ("channel_join", "channel_leave", "channel_posting_permissions", "bot_message"):
                    continue

                all_messages.append({
                    "text": text.replace("\n", " "),
                    "channel_normalname": rec.get("channel_normalname") or rec.get("channel_name") or channel_id,
                    "ts_time": rec.get("ts_time") or rec.get("timestamp") or "",
                    "slackuserid": rec.get("slackuserid") or rec.get("user_id") or rec.get("user") or "",
                    "thread_ts": rec.get("thread_ts") or "",
                })

            # Paginate
            if len(records) < page_size:
                break
            offset += page_size

        logger.info(f"Pulse SkyVault: channel={channel_id}, range={start_date} to {end_date}, messages={len(all_messages)}")

    except Exception as e:
        logger.error(f"Pulse SkyVault API exception for channel {channel_id}: {e}")
        error_info = {
            "gap_start": start_date,
            "gap_end": end_date,
            "reason": f"SkyVault API error: {str(e)}",
        }

    return all_messages, error_info


def _fetch_pulse_channel_messages(channel_id: str, start_date: str, end_date: str) -> tuple:
    """
    Fetch Slack messages from BigQuery + SkyVault real-time API for a given channel and date range.
    Strategy:
      1. Query BQ for the full requested date range (covers data older than ~24hrs)
      2. Determine BQ's latest available date from results
      3. If there's a gap between BQ's latest date and the requested end_date,
         call SkyVault API ONLY for that gap period
      4. Merge both results
    Returns (messages_text, message_count, channel_name, available_range, gap_warning, msg_metadata).
    available_range is a dict with start_date/end_date if no data found in requested range.
    gap_warning is None or a dict with gap_start, gap_end, reason when API fails.
    msg_metadata is a dict mapping MSG tag → {slackuserid, ts, channel_id, channel_normalname}.
    """
    # ts_time is a DATETIME column — use CAST to convert string to DATETIME
    start_dt = f"{start_date} 00:00:00"
    end_dt = f"{end_date} 23:59:59"

    # ── Step 1: Fetch from BigQuery (historical data, full range) ─
    query = f"""
        SELECT text, channel_normalname, ts_time, slackuserid, thread_ts, ts, client_msg_id, channel_name
        FROM `{SLACK_DATA_TABLE}`
        WHERE channel_name = @channel_id
          AND text IS NOT NULL
          AND TRIM(text) != ''
          AND (subtype IS NULL OR subtype NOT IN (
               'channel_join', 'channel_leave',
               'channel_posting_permissions', 'bot_message'))
          AND ts_time >= CAST(@start_dt AS DATETIME)
          AND ts_time <= CAST(@end_dt AS DATETIME)
        ORDER BY ts_time ASC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("channel_id", "STRING", channel_id),
            bigquery.ScalarQueryParameter("start_dt", "STRING", start_dt),
            bigquery.ScalarQueryParameter("end_dt", "STRING", end_dt),
        ]
    )
    bq_rows = list(bq_client.query(query, job_config=job_config).result())
    logger.info(f"Pulse BQ query: channel={channel_id}, range={start_dt} to {end_dt}, rows={len(bq_rows)}")

    # ── Step 2: Determine BQ's latest date & check for gap ───────
    # Find the max ts_time in BQ results to know where BQ data ends
    bq_max_date = None
    if bq_rows:
        for row in bq_rows:
            row_date = str(row.ts_time)[:10] if row.ts_time else None
            if row_date and (bq_max_date is None or row_date > bq_max_date):
                bq_max_date = row_date

    # ── Step 3: Call SkyVault API ONLY for the gap ───────────────
    # If BQ has no data → fetch full range from API
    # If BQ's latest date < requested end_date → fetch only the gap
    # If BQ covers the full range → no API call needed
    realtime_messages = []
    gap_warning = None
    if bq_max_date is None:
        # BQ has no data at all for this range — try API for full range
        logger.info(f"Pulse: No BQ data, fetching full range from SkyVault API")
        realtime_messages, error_info = _fetch_realtime_slack_messages(channel_id, start_date, end_date)
        if error_info:
            gap_warning = error_info
    elif bq_max_date < end_date:
        # BQ data ends before requested end_date — fetch gap from API
        # Gap starts day after BQ's last date
        gap_start = (datetime.strptime(bq_max_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        logger.info(f"Pulse: BQ data ends at {bq_max_date}, fetching gap {gap_start} to {end_date} from SkyVault API")
        realtime_messages, error_info = _fetch_realtime_slack_messages(channel_id, gap_start, end_date)
        if error_info:
            gap_warning = error_info
    else:
        logger.info(f"Pulse: BQ covers full range (max={bq_max_date}), no API call needed")

    # ── Step 4: Combine results ──────────────────────────────────
    all_messages = []
    channel_name = channel_id

    # Add BQ rows (carry ts and channel_name for Slack permalink construction)
    for row in bq_rows:
        text = str(row.text or "")
        thread_key = str(row.thread_ts or row.ts_time or "no_thread")
        all_messages.append({
            "text": text,
            "thread_key": thread_key,
            "slackuserid": str(row.slackuserid or ""),
            "ts": str(row.ts or ""),
            "channel_id": str(row.channel_name or channel_id),
        })
        if not channel_name or channel_name == channel_id:
            channel_name = str(row.channel_normalname or channel_id)

    # Add SkyVault API messages (gap data — no dedup needed since date ranges don't overlap)
    for msg in realtime_messages:
        text = msg.get("text", "")
        thread_key = msg.get("thread_ts") or msg.get("ts_time") or "no_thread"
        all_messages.append({
            "text": text,
            "thread_key": thread_key,
            "slackuserid": msg.get("slackuserid", ""),
            "ts": "",
            "channel_id": channel_id,
        })
        if not channel_name or channel_name == channel_id:
            channel_name = msg.get("channel_normalname") or channel_id

    if realtime_messages:
        logger.info(f"Pulse: Added {len(realtime_messages)} messages from SkyVault API (gap fill) for channel {channel_id}")

    total_count = len(all_messages)

    # If no messages from either source
    if total_count == 0:
        available_range = _get_channel_data_range(channel_id)
        return "", 0, channel_id, available_range, gap_warning, {}

    # ── Step 5: Assign MSG tags and build metadata ────────────────
    msg_metadata = {}
    for i, msg in enumerate(all_messages, start=1):
        tag = f"MSG-{i:04d}"
        msg["tag"] = tag
        msg_metadata[tag] = {
            "slackuserid": msg.get("slackuserid", ""),
            "ts": msg.get("ts", ""),
            "channel_id": msg.get("channel_id", channel_id),
            "channel_normalname": channel_name,
        }

    # ── Step 6: Group by thread and format with MSG tags ─────────
    threads = {}
    for msg in all_messages:
        thread_key = msg["thread_key"]
        if thread_key not in threads:
            threads[thread_key] = []
        threads[thread_key].append(f"[{msg['tag']}] {msg['text']}")

    thread_blocks = []
    for thread_id, messages in threads.items():
        if messages:
            block = "Thread:\n" + "\n".join(messages)
            thread_blocks.append(block)

    messages_text = "\n\n".join(thread_blocks)
    return messages_text, total_count, channel_name, None, gap_warning, msg_metadata


def _analyze_messages_with_gemini(messages_text: str, batch_size: int = 50000) -> list:
    """
    Send channel messages to Gemini for AI analysis in batches.
    Returns a consolidated list of feedback items.
    """
    if not _gemini_model:
        logger.error("Gemini model not available for pulse analysis")
        return []

    # Split messages into batches
    batches = [messages_text[i:i + batch_size] for i in range(0, len(messages_text), batch_size)]
    all_feedback = []

    for i, batch in enumerate(batches):
        logger.info(f"Analyzing batch {i+1}/{len(batches)}...")
        prompt = (
            _PULSE_ANALYSIS_PROMPT +
            "\n\n### SLACK_MESSAGES_FOR_ANALYSIS\n[START OF MESSAGES]\n" +
            batch +
            "\n[END OF MESSAGES]\n\n### JSON_OUTPUT\n"
        )

        try:
            response = _gemini_model.generate_content(prompt)
            raw_text = response.text.strip()

            # Clean markdown fences
            clean = raw_text
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
            if clean.startswith("json"):
                clean = clean[4:].strip()

            result = _json.loads(clean)
            feedback = result.get("results", [])
            if feedback:
                all_feedback.extend(feedback)

        except Exception as e:
            logger.error(f"Gemini analysis for batch {i+1} failed: {e}")
            # Continue to next batch
    
    return all_feedback


def _consolidate_feedback(all_feedback: dict) -> list:
    """
    Consolidate feedback from multiple channels using Gemini.
    all_feedback: {channel_name: [feedback_items]}
    """
    if not _gemini_model:
        # Flatten without consolidation
        items = []
        for feedbacks in all_feedback.values():
            items.extend(feedbacks)
        return items

    prompt = (
        _PULSE_CONSOLIDATION_PROMPT +
        "\n\n### FEEDBACK_BY_CHANNEL\n" +
        _json.dumps(all_feedback, indent=2) +
        "\n\n### CONSOLIDATED_JSON_OUTPUT\n"
    )

    try:
        response = _gemini_model.generate_content(prompt)
        raw_text = response.text.strip()
        clean = raw_text
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()
        if clean.startswith("json"):
            clean = clean[4:].strip()
        result = _json.loads(clean)
        return result.get("results", [])
    except Exception as e:
        logger.error(f"Consolidation failed: {e}")
        # Fallback: flatten
        items = []
        for feedbacks in all_feedback.values():
            items.extend(feedbacks)
        return items


def _resolve_slack_eids(slackuserids: list) -> dict:
    """
    Batch-resolve Slack user IDs to Verizon EIDs via BigQuery.
    Returns {slackuserid: eid}. Falls back to slackuserid if no EID found.
    """
    if not slackuserids:
        return {}
    unique_ids = list({uid for uid in slackuserids if uid})
    if not unique_ids:
        return {}
    try:
        query = f"""
            SELECT slackid, eid
            FROM `vz-it-np-voev-dev-voevdo-0.slack.vea_slack_userobj_data`
            WHERE slackid IN UNNEST(@ids)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("ids", "STRING", unique_ids),
            ]
        )
        rows = list(bq_client.query(query, job_config=job_config).result())
        return {row.slackid: (row.eid or row.slackid) for row in rows}
    except Exception as e:
        logger.warning(f"Could not resolve Slack EIDs: {e}")
        return {}


def _generate_pulse_html(report_data: dict) -> str:
    """Generate HTML email content for the pulse report."""
    start_date = report_data["start_date"]
    end_date = report_data["end_date"]
    feedback_items = report_data.get("feedback", [])
    channels = report_data.get("channels", [])
    total_messages = report_data.get("total_messages", 0)

    # Category styles
    card_styles = {
        "Feature Request": {"bg": "#e6f4ea", "color": "#28a745", "icon": "🟢"},
        "Pain Point/Bug": {"bg": "#fdeaea", "color": "#dc3545", "icon": "🔴"},
        "AI Testimonial/Use Case": {"bg": "#fffbe6", "color": "#ffc107", "icon": "🟡"},
        "Common Question": {"bg": "#e6f0fa", "color": "#007bff", "icon": "🔵"},
        "Other": {"bg": "#f4f6fa", "color": "#6c757d", "icon": "⚪️"},
    }
    category_order = ["Pain Point/Bug", "Feature Request", "Common Question", "AI Testimonial/Use Case", "Other"]

    # Build pivot table data
    records = [{"product": fb.get("product", "Unknown"), "category": fb.get("feedback_type", "Other")} for fb in feedback_items]
    df = pd.DataFrame(records) if records else pd.DataFrame(columns=["product", "category"])

    table_html = ""
    if not df.empty:
        pivot = pd.pivot_table(df, index="product", columns="category", aggfunc="size", fill_value=0)
        pivot = pivot.reindex(columns=category_order, fill_value=0)
        pivot["Total"] = pivot.sum(axis=1)
        totals_row = pivot.sum(axis=0)
        pivot = pd.concat([pivot, pd.DataFrame([totals_row], index=["Total"])])

        table_html = '<table style="border-collapse:collapse; width:100%; margin-bottom:24px; font-size:0.95em;"><thead><tr>'
        table_html += '<th style="background:#f4f6fa; padding:8px; border:1px solid #ddd; text-align:left;">Product</th>'
        for cat in category_order:
            table_html += f'<th style="background:#f4f6fa; padding:8px; border:1px solid #ddd; text-align:center;">{cat}</th>'
        table_html += '<th style="background:#ececec; padding:8px; border:1px solid #ddd; text-align:center;">Total</th></tr></thead><tbody>'

        for idx, row in pivot.iterrows():
            is_last = idx == "Total"
            style = 'font-weight:bold;' if is_last else ''
            table_html += f'<tr style="{style}">'
            cell_bg = "background:#ececec;" if is_last else ""
            table_html += f'<td style="padding:8px; border:1px solid #ddd; {cell_bg}">{idx}</td>'
            for cat in category_order:
                val = int(row.get(cat, 0))
                table_html += f'<td style="padding:8px; border:1px solid #ddd; text-align:center; {cell_bg}">{val if val > 0 else ""}</td>'
            table_html += f'<td style="padding:8px; border:1px solid #ddd; text-align:center; background:#ececec; font-weight:bold;">{int(row["Total"])}</td></tr>'
        table_html += '</tbody></table>'
    else:
        table_html = "<p>No feedback data available.</p>"

    # Build feedback detail sections
    feedback_sections = []
    products = sorted(set(fb.get("product", "Unknown") for fb in feedback_items))
    for product in products:
        feedback_sections.append(
            f'<div style="background:#CD040B; color:#fff; font-size:1.1em; font-weight:700; '
            f'border-radius:20px; padding:10px 28px; margin-top:32px; margin-bottom:18px; '
            f'letter-spacing:1px; text-align:center;">{product}</div>'
        )
        for cat in category_order:
            items = [fb for fb in feedback_items if fb.get("product") == product and fb.get("feedback_type") == cat]
            if items:
                style = card_styles.get(cat, card_styles["Other"])
                feedback_sections.append(
                    f'<div style="color:{style["color"]}; font-weight:600; font-size:1.1em; '
                    f'margin-top:24px; margin-bottom:8px;">'
                    f'<span style="font-size:1.2em; margin-right:8px;">{style["icon"]}</span>{cat}</div>'
                )
                for fb in items:
                    mc = fb.get("mention_count", 0) or 0
                    if mc >= 6:
                        heat_badge = '<span style="background:#dc3545; color:#fff; font-size:0.8em; font-weight:700; border-radius:12px; padding:2px 10px; margin-left:8px;">🔥🔥 Very Hot · ' + str(mc) + ' mentions</span>'
                    elif mc >= 3:
                        heat_badge = '<span style="background:#fd7e14; color:#fff; font-size:0.8em; font-weight:700; border-radius:12px; padding:2px 10px; margin-left:8px;">🔥 Hot · ' + str(mc) + ' mentions</span>'
                    elif mc >= 1:
                        heat_badge = '<span style="background:#ffc107; color:#333; font-size:0.8em; font-weight:700; border-radius:12px; padding:2px 10px; margin-left:8px;">● Warm · ' + str(mc) + ' mentions</span>'
                    else:
                        heat_badge = ""
                    feedback_sections.append(f"""
                    <div style="margin: 14px 0; padding: 14px 18px; border-left: 6px solid {style["color"]};
                         border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); background: #f9f9f9;">
                        <p style="margin: 0 0 6px 0;"><strong>Summary:</strong> {fb.get("feedback_summary", "")}{heat_badge}</p>
                        <p style="margin: 0;"><strong>Proposed Solution:</strong> {fb.get("proposed_solution", "N/A")}</p>
                    </div>
                    """)

    channels_html = ", ".join(f"#{ch.get('channel_name', ch.get('channel_id', ''))}" for ch in channels)
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    csv_link_html = ""
    if report_data.get("report_id"):
        csv_link_html = (
            f'<p style="margin-top:16px; font-size:0.95em;">'
            f'📎 <a href="/pulse-report/{report_data["report_id"]}/csv" '
            f'style="color:#CD040B; font-weight:600;">Download Full CSV Report</a>'
            f' (includes contributor IDs &amp; message links)</p>'
        )

    html = f"""
    <html>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6fa; margin: 0; padding: 0 0 24px 0;">
        <div style="background: #CD040B; color: #fff; padding: 24px 0 16px 0; text-align: center; border-radius: 0 0 18px 18px;">
            <h1 style="margin: 0; font-size: 2em; letter-spacing: 1px;">Digital Workplace Pulse Report</h1>
            <p style="margin: 8px 0 0 0; font-size: 1.1em;">{start_date} to {end_date}</p>
        </div>
        <div style="max-width: 700px; margin: 32px auto; background: #fff; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.07); padding: 32px;">
            <div style="background: #f4f6fa; border-radius: 8px; padding: 16px; margin-bottom: 24px;">
                <p style="margin: 0 0 8px 0;"><strong>Channels Analyzed:</strong> {channels_html}</p>
                <p style="margin: 0 0 8px 0;"><strong>Total Messages Processed:</strong> {total_messages}</p>
                <p style="margin: 0;"><strong>Feedback Items Extracted:</strong> {len(feedback_items)}</p>
            </div>
            <h2 style="color: #222; border-bottom: 2px solid #eee; padding-bottom: 8px;">Feedback Distribution</h2>
            {table_html}
            <hr style="margin: 32px 0;">
            <h2 style="color: #222; border-bottom: 2px solid #eee; padding-bottom: 8px;">Feedback by Product</h2>
            {''.join(feedback_sections) if feedback_sections else "<p>No feedback found.</p>"}
            {csv_link_html}
            <p style="margin-top: 40px;">
                <strong>Thanks,</strong><br>
                <strong>DWPS Team</strong>
            </p>
            <p style="font-size: 0.9em; color: #666; margin-top: 28px;">
                <em>Generated on: {current_time}</em>
            </p>
        </div>
    </body>
    </html>
    """
    return html


def _generate_pulse_csv(feedback_items: list) -> bytes:
    """
    Build a CSV from feedback items.
    Reads _contributors and _message_links fields that were embedded at report-generation time.
    Works for both new reports (with contributor info) and old reports (those fields absent → blank).
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "#", "feedback_type", "product", "feedback_summary",
        "proposed_solution", "mention_count", "contributor_ids", "message_links",
    ])
    for i, fb in enumerate(feedback_items, start=1):
        writer.writerow([
            i,
            fb.get("feedback_type", ""),
            fb.get("product", ""),
            fb.get("feedback_summary", ""),
            fb.get("proposed_solution", ""),
            fb.get("mention_count", 0) or 0,
            "; ".join(fb.get("_contributors", [])),
            "; ".join(fb.get("_message_links", [])),
        ])
    return output.getvalue().encode("utf-8")


# ── Pulse Report API Endpoints ───────────────────────────────────

@app.route('/pulse-report/generate', methods=['POST'])
def pulse_report_generate():
    """
    Generate a DWP Pulse Report for given channels and date range.
    Fetches messages from BigQuery, analyzes with Gemini, stores in DB.

    Expected JSON:
    {
        "channel_ids": ["C0AE1G97QCV"],
        "start_date": "2026-04-28",   (optional, defaults to 7 days ago)
        "end_date": "2026-05-05",     (optional, defaults to today)
        "created_by": "user@verizon.com"  (optional)
    }

    Returns:
    {
        "status": "success",
        "report_id": "uuid...",
        "total_messages": 150,
        "total_feedback": 12,
        "feedback": [...],
        "html_preview": "..."
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        channel_ids = data.get("channel_ids", ["C0AE1G97QCV"])
        if isinstance(channel_ids, str):
            channel_ids = [channel_ids]

        # Date range
        end_date = data.get("end_date", datetime.now().strftime("%Y-%m-%d"))
        start_date = data.get("start_date", (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))
        created_by = data.get("created_by", "api")

        logger.info(f"Pulse report generation: channels={channel_ids}, {start_date} to {end_date}")

        # Create report record in DB
        report_id = str(uuid.uuid4())
        try:
            conn = _get_pulse_db()
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO pulse_reports (report_id, title, start_date, end_date, status, created_by)
                   VALUES (%s, %s, %s, %s, 'processing', %s)""",
                (report_id, f"DWP Pulse Report: {start_date} to {end_date}",
                 start_date, end_date, created_by)
            )
            conn.commit()
            conn.close()
        except Exception as db_err:
            logger.warning(f"Could not save to DB (continuing): {db_err}")

        # Fetch and analyze messages per channel
        all_channel_feedback = {}
        channel_details = []
        total_messages = 0
        all_msg_metadata = {}

        data_available_ranges = {}
        gap_warnings = []

        for ch_id in channel_ids:
            messages_text, msg_count, ch_name, available_range, gap_warning, msg_metadata = _fetch_pulse_channel_messages(ch_id, start_date, end_date)
            total_messages += msg_count
            all_msg_metadata.update(msg_metadata)

            if available_range:
                data_available_ranges[ch_id] = available_range

            if gap_warning:
                gap_warnings.append({
                    "channel_id": ch_id,
                    "channel_name": ch_name,
                    "gap_start": gap_warning["gap_start"],
                    "gap_end": gap_warning["gap_end"],
                    "reason": gap_warning["reason"],
                })

            if messages_text:
                feedback = _analyze_messages_with_gemini(messages_text)
            else:
                feedback = []

            all_channel_feedback[ch_name] = feedback
            channel_details.append({
                "channel_id": ch_id,
                "channel_name": ch_name,
                "message_count": msg_count,
                "feedback_count": len(feedback),
            })

        # If no messages found for any channel, return available date ranges
        if total_messages == 0 and data_available_ranges:
            return jsonify({
                "status": "no_data",
                "message": f"No messages found in the requested date range ({start_date} to {end_date}). "
                           f"Please select dates within the available data range.",
                "requested_range": {"start_date": start_date, "end_date": end_date},
                "data_available": data_available_ranges,
            }), 200

        # Consolidate if multiple channels
        if len(all_channel_feedback) > 1:
            consolidated = _consolidate_feedback(all_channel_feedback)
        else:
            consolidated = []
            for feedbacks in all_channel_feedback.values():
                consolidated.extend(feedbacks)

        # Resolve Slack user IDs to EIDs and embed contributor info into each feedback item.
        # Storing this directly in each item means the CSV can always be regenerated from report_json
        # without needing msg_metadata again.
        all_slack_ids = list({m["slackuserid"] for m in all_msg_metadata.values() if m.get("slackuserid")})
        eid_map = _resolve_slack_eids(all_slack_ids)

        for fb in consolidated:
            source_ids = fb.get("source_message_ids") or []
            c_ids, links = [], []
            for tag in source_ids:
                meta = all_msg_metadata.get(tag)
                if not meta:
                    continue
                uid = meta.get("slackuserid", "")
                ts = meta.get("ts", "")
                ch_id = meta.get("channel_id", "")
                eid = eid_map.get(uid, uid) if uid else ""
                if eid and eid not in c_ids:
                    c_ids.append(eid)
                if ts and ch_id:
                    links.append(f"https://verizon.enterprise.slack.com/archives/{ch_id}/p{ts.replace('.', '')}")
            fb["_contributors"] = c_ids
            fb["_message_links"] = links

        # Generate HTML
        report_data = {
            "report_id": report_id,
            "start_date": start_date,
            "end_date": end_date,
            "feedback": consolidated,
            "channels": channel_details,
            "total_messages": total_messages,
        }
        html_content = _generate_pulse_html(report_data)

        # Update DB with results
        try:
            conn = _get_pulse_db()
            cur = conn.cursor()
            cur.execute(
                """UPDATE pulse_reports
                   SET status = 'completed', total_messages = %s, total_feedback = %s,
                       report_json = %s, html_content = %s, completed_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE report_id = %s""",
                (total_messages, len(consolidated), _json.dumps(report_data),
                 html_content, report_id)
            )
            # Save channel details
            for ch in channel_details:
                cur.execute(
                    """INSERT INTO pulse_report_channels (report_id, channel_id, channel_name, message_count, feedback_count)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (report_id, ch["channel_id"], ch["channel_name"], ch["message_count"], ch["feedback_count"])
                )
            # Save individual feedback items
            for fb in consolidated:
                cur.execute(
                    """INSERT INTO pulse_report_feedback (report_id, feedback_type, product, feedback_summary, proposed_solution)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (report_id, fb.get("feedback_type", "Other"), fb.get("product", "Unknown"),
                     fb.get("feedback_summary", ""), fb.get("proposed_solution", ""))
                )
            conn.commit()
            conn.close()
        except Exception as db_err:
            logger.warning(f"Could not update DB with results (continuing): {db_err}")

        return jsonify({
            "status": "success",
            "report_id": report_id,
            "title": f"DWP Pulse Report: {start_date} to {end_date}",
            "start_date": start_date,
            "end_date": end_date,
            "total_messages": total_messages,
            "total_feedback": len(consolidated),
            "channels": channel_details,
            "feedback": consolidated,
            "html_content": html_content,
            "gap_warnings": gap_warnings if gap_warnings else None,
        }), 200

    except Exception as e:
        logger.error(f"Error in pulse_report_generate: {type(e).__name__}: {e}", exc_info=True)
        # Update DB status to failed
        try:
            conn = _get_pulse_db()
            cur = conn.cursor()
            cur.execute(
                "UPDATE pulse_reports SET status = 'failed', error_message = %s WHERE report_id = %s",
                (str(e), report_id)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse-report/send-email', methods=['POST'])
def pulse_report_send_email():
    """
    Send a generated pulse report via email.

    Expected JSON:
    {
        "report_id": "uuid...",      (optional if html_content provided)
        "recipients": ["email1@verizon.com", "email2@verizon.com"],
        "subject": "Custom subject"  (optional),
        "html_content": "..."        (optional, overrides report_id lookup)
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        recipients = data.get("recipients", [])
        if isinstance(recipients, str):
            recipients = [recipients]
        if not recipients:
            return jsonify({"status": "error", "message": "Missing 'recipients'"}), 400

        html_content = data.get("html_content")
        report_id = data.get("report_id")
        subject = data.get("subject")

        csv_b64 = data.get("csv_b64", "")

        # If no html_content, fetch from DB
        if not html_content and report_id:
            try:
                conn = _get_pulse_db()
                cur = conn.cursor()
                cur.execute(
                    "SELECT html_content, title, start_date, end_date, report_json FROM pulse_reports WHERE report_id = %s",
                    (report_id,)
                )
                row = cur.fetchone()
                conn.close()
                if row:
                    html_content = row[0]
                    if not subject:
                        subject = row[1] or "Digital Workplace Pulse Report"
                    if not csv_b64 and row[4]:
                        try:
                            raw4 = row[4]
                            rj = raw4 if isinstance(raw4, dict) else _json.loads(raw4)
                            feedback_items = rj.get("feedback", [])
                            if feedback_items:
                                csv_b64 = base64.b64encode(_generate_pulse_csv(feedback_items)).decode("utf-8")
                        except Exception:
                            pass
                else:
                    return jsonify({"status": "error", "message": "Report not found"}), 404
            except Exception as db_err:
                return jsonify({"status": "error", "message": f"DB error: {db_err}"}), 500

        if not html_content:
            return jsonify({"status": "error", "message": "No html_content or valid report_id provided"}), 400

        if not subject:
            subject = "Digital Workplace Pulse Report"

        # Build email — use mixed/alternative structure to support CSV attachment
        from_email = DO_NOT_REPLY_FROM
        results = []
        try:
            server = smtplib.SMTP(VDSI_SMTP_SERVER, 25)
            message = MIMEMultipart('mixed')
            message['Subject'] = subject
            message['From'] = from_email
            message['To'] = ", ".join(recipients)

            alt_part = MIMEMultipart('alternative')
            alt_part.attach(MIMEText("This is an HTML email. Please view in an HTML-capable email client.", 'plain'))
            alt_part.attach(MIMEText(html_content, 'html'))
            message.attach(alt_part)

            if csv_b64:
                csv_bytes = base64.b64decode(csv_b64)
                csv_attachment = MIMEBase('text', 'csv')
                csv_attachment.set_payload(base64.encodebytes(csv_bytes).decode('ascii'))
                csv_attachment['Content-Transfer-Encoding'] = 'base64'
                fname = f"pulse_report_{report_id}.csv" if report_id else "pulse_report.csv"
                csv_attachment.add_header('Content-Disposition', 'attachment', filename=fname)
                message.attach(csv_attachment)

            server.send_message(message)
            server.quit()

            for r in recipients:
                results.append({"to": r, "status": "sent"})
        except Exception as smtp_err:
            logger.error(f"SMTP error sending pulse report: {smtp_err}")
            for r in recipients:
                results.append({"to": r, "status": "failed", "error": str(smtp_err)})

        # Record in DB
        if report_id:
            try:
                conn = _get_pulse_db()
                cur = conn.cursor()
                for r in results:
                    cur.execute(
                        """INSERT INTO pulse_report_emails (report_id, recipient_email, subject, status, sent_at)
                           VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)""",
                        (report_id, r["to"], subject, r["status"])
                    )
                conn.commit()
                conn.close()
            except Exception as db_err:
                logger.warning(f"Could not save email record: {db_err}")

        success_count = sum(1 for r in results if r["status"] == "sent")
        return jsonify({
            "status": "success" if success_count > 0 else "failed",
            "total_recipients": len(recipients),
            "successful": success_count,
            "failed": len(recipients) - success_count,
            "details": results,
        }), 200

    except Exception as e:
        logger.error(f"Error in pulse_report_send_email: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse-report/<report_id>/csv', methods=['GET'])
def pulse_report_csv_download(report_id):
    """Download the CSV for a generated pulse report."""
    from flask import make_response

    def _html_error(msg, code=500):
        """Return a visible HTML error so the browser shows it instead of blank."""
        body = f"<html><body><h2>CSV Download Error</h2><p>{msg}</p><p>Report ID: {report_id}</p></body></html>"
        return body, code, {"Content-Type": "text/html; charset=utf-8"}

    try:
        logger.info(f"CSV download requested for report_id={report_id}")

        conn = _get_pulse_db()
        cur = conn.cursor()

        # Also fetch html_content as a fallback signal that the report exists
        cur.execute(
            "SELECT report_json, html_content FROM pulse_reports WHERE report_id = %s",
            (report_id,)
        )
        row = cur.fetchone()
        conn.close()

        if not row:
            logger.warning(f"CSV download: report {report_id} not found in DB")
            return _html_error("Report not found in database.", 404)

        raw_json, html_content = row[0], row[1]
        logger.info(f"CSV download: report_json type={type(raw_json).__name__}, is_null={raw_json is None}")

        if raw_json is None:
            # report_json not stored — rebuild basic CSV from pulse_report_feedback table
            logger.info(f"CSV download: report_json is NULL, rebuilding from pulse_report_feedback table")
            conn2 = _get_pulse_db()
            cur2 = conn2.cursor()
            cur2.execute(
                "SELECT feedback_type, product, feedback_summary, proposed_solution FROM pulse_report_feedback WHERE report_id = %s",
                (report_id,)
            )
            fb_rows = cur2.fetchall()
            conn2.close()

            if not fb_rows:
                logger.warning(f"CSV download: no feedback rows found for report {report_id}")
                return _html_error("No feedback data found for this report. The report may still be processing.", 404)

            feedback_items = [
                {"feedback_type": r[0], "product": r[1], "feedback_summary": r[2], "proposed_solution": r[3]}
                for r in fb_rows
            ]
        else:
            # psycopg2 returns JSONB as dict, TEXT as string
            rj = raw_json if isinstance(raw_json, dict) else _json.loads(raw_json)
            feedback_items = rj.get("feedback", [])
            if not feedback_items:
                logger.warning(f"CSV download: report_json exists but feedback list is empty for {report_id}")
                return _html_error("Report has no feedback items to export.", 404)

        logger.info(f"CSV download: generating CSV with {len(feedback_items)} items for report {report_id}")
        csv_bytes = _generate_pulse_csv(feedback_items)
        logger.info(f"CSV download: generated {len(csv_bytes)} bytes")

        resp = make_response(csv_bytes)
        resp.headers["Content-Type"] = "text/csv; charset=utf-8"
        resp.headers["Content-Disposition"] = f'attachment; filename="pulse_report_{report_id}.csv"'
        resp.headers["Content-Length"] = str(len(csv_bytes))
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    except Exception as e:
        logger.error(f"CSV download error for report {report_id}: {type(e).__name__}: {e}", exc_info=True)
        return _html_error(f"{type(e).__name__}: {e}")


@app.route('/pulse-report/list', methods=['GET'])
def pulse_report_list():
    """
    List past pulse reports with optional pagination.
    Query params: limit (default 20), offset (default 0)
    """
    try:
        limit = min(int(request.args.get("limit", "20")), 100)
        offset = max(int(request.args.get("offset", "0")), 0)

        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM pulse_reports")
        total = cur.fetchone()[0]

        cur.execute(
            """SELECT report_id, title, start_date, end_date, status, total_messages,
                      total_feedback, created_by, created_at, completed_at
               FROM pulse_reports ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            (limit, offset)
        )
        columns = ["report_id", "title", "start_date", "end_date", "status",
                   "total_messages", "total_feedback", "created_by", "created_at", "completed_at"]
        reports = []
        for row in cur.fetchall():
            record = dict(zip(columns, row))
            for key in ("start_date", "end_date", "created_at", "completed_at"):
                if record[key]:
                    record[key] = str(record[key])
            reports.append(record)
        conn.close()

        return jsonify({"status": "success", "total": total, "reports": reports}), 200
    except Exception as e:
        logger.error(f"Error in pulse_report_list: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse-report/<report_id>', methods=['GET'])
def pulse_report_detail(report_id):
    """Get a specific pulse report with full details."""
    try:
        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT report_id, title, start_date, end_date, status, total_messages,
                      total_feedback, report_json, html_content, created_by, created_at, completed_at
               FROM pulse_reports WHERE report_id = %s""",
            (report_id,)
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Report not found"}), 404

        columns = ["report_id", "title", "start_date", "end_date", "status",
                   "total_messages", "total_feedback", "report_json", "html_content",
                   "created_by", "created_at", "completed_at"]
        report = dict(zip(columns, row))
        for key in ("start_date", "end_date", "created_at", "completed_at"):
            if report[key]:
                report[key] = str(report[key])
        if report["report_json"] and isinstance(report["report_json"], str):
            report["report_json"] = _json.loads(report["report_json"])

        # Fetch channels
        cur.execute(
            "SELECT channel_id, channel_name, message_count, feedback_count FROM pulse_report_channels WHERE report_id = %s",
            (report_id,)
        )
        report["channels"] = [
            {"channel_id": r[0], "channel_name": r[1], "message_count": r[2], "feedback_count": r[3]}
            for r in cur.fetchall()
        ]

        # Fetch feedback items
        cur.execute(
            """SELECT feedback_type, product, feedback_summary, proposed_solution
               FROM pulse_report_feedback WHERE report_id = %s""",
            (report_id,)
        )
        report["feedback"] = [
            {"feedback_type": r[0], "product": r[1], "feedback_summary": r[2], "proposed_solution": r[3]}
            for r in cur.fetchall()
        ]

        conn.close()
        return jsonify({"status": "success", "report": report}), 200
    except Exception as e:
        logger.error(f"Error in pulse_report_detail: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse-report/channels', methods=['GET'])
def pulse_report_channels():
    """List all onboarded channels for pulse reporting."""
    try:
        _seed_pulse_channels()
        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT channel_id, channel_name, description, is_active, added_by, created_at
               FROM pulse_channels WHERE is_active = TRUE ORDER BY channel_name"""
        )
        columns = ["channel_id", "channel_name", "description", "is_active", "added_by", "created_at"]
        channels = []
        for row in cur.fetchall():
            record = dict(zip(columns, row))
            record["created_at"] = str(record["created_at"]) if record["created_at"] else None
            channels.append(record)
        conn.close()
        return jsonify({"status": "success", "channels": channels}), 200
    except Exception as e:
        logger.error(f"Error in pulse_report_channels: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse-report/channels', methods=['POST'])
def pulse_report_add_channel():
    """
    Add a channel to the pulse report system.
    Expected JSON: {"channel_id": "C0AE1G97QCV", "channel_name": "help-gemini", "description": "..."}
    """
    try:
        data = request.get_json()
        if not data or not data.get("channel_id"):
            return jsonify({"status": "error", "message": "Missing 'channel_id'"}), 400

        channel_id = data["channel_id"].strip()
        channel_name = data.get("channel_name", "").strip()
        description = data.get("description", "").strip()
        added_by = data.get("added_by", "api")

        # Try to get channel name from BQ if not provided
        if not channel_name:
            try:
                query = f"""
                    SELECT channel_normalname FROM `{SLACK_DATA_TABLE}`
                    WHERE channel_name = @channel_id LIMIT 1
                """
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("channel_id", "STRING", channel_id)]
                )
                rows = list(bq_client.query(query, job_config=job_config).result())
                if rows:
                    channel_name = str(rows[0].channel_normalname or channel_id)
            except Exception:
                channel_name = channel_id

        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO pulse_channels (channel_id, channel_name, description, added_by)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (channel_id) DO UPDATE SET
                 channel_name = EXCLUDED.channel_name,
                 description = EXCLUDED.description,
                 is_active = TRUE,
                 updated_at = CURRENT_TIMESTAMP""",
            (channel_id, channel_name, description, added_by)
        )
        conn.commit()
        conn.close()

        return jsonify({
            "status": "success",
            "message": f"Channel {channel_id} ({channel_name}) added",
            "channel_id": channel_id,
            "channel_name": channel_name,
        }), 201
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse-report/channels/<channel_id>', methods=['DELETE'])
def pulse_report_remove_channel(channel_id):
    """Remove (deactivate) a channel from pulse reporting."""
    try:
        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE pulse_channels SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE channel_id = %s",
            (channel_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": f"Channel {channel_id} removed"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# DWP PULSE — USER AUTH, JOB SCHEDULER, ADMIN & HISTORY APIs
# ══════════════════════════════════════════════════════════════════

# Admin VZ IDs (can see all jobs, users, metrics)
PULSE_ADMIN_VZIDS = set(
    os.environ.get("PULSE_ADMIN_VZIDS", "anatan8").split(",")
)


def _ensure_pulse_user(vzid: str) -> dict:
    """Ensure user exists in pulse_users, create if not. Returns user dict."""
    conn = _get_pulse_db()
    cur = conn.cursor()
    cur.execute("SELECT vzid, display_name, email, role, is_active FROM pulse_users WHERE vzid = %s", (vzid,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE pulse_users SET last_login = CURRENT_TIMESTAMP WHERE vzid = %s", (vzid,))
        conn.commit()
        conn.close()
        return {"vzid": row[0], "display_name": row[1], "email": row[2], "role": row[3], "is_active": row[4]}
    else:
        role = "admin" if vzid in PULSE_ADMIN_VZIDS else "user"
        email = f"{vzid}@verizon.com"
        cur.execute(
            """INSERT INTO pulse_users (vzid, display_name, email, role, last_login)
               VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
               ON CONFLICT (vzid) DO UPDATE SET last_login = CURRENT_TIMESTAMP""",
            (vzid, vzid, email, role)
        )
        conn.commit()
        conn.close()
        return {"vzid": vzid, "display_name": vzid, "email": email, "role": role, "is_active": True}


def _is_admin(vzid: str) -> bool:
    """Check if user is admin."""
    return vzid in PULSE_ADMIN_VZIDS


def _get_date_range_for_type(date_range_type: str, custom_start=None, custom_end=None):
    """Calculate actual start_date and end_date from date_range_type."""
    today = datetime.now().date()
    if date_range_type == "last_7_days":
        return (today - timedelta(days=7)).isoformat(), today.isoformat()
    elif date_range_type == "last_14_days":
        return (today - timedelta(days=14)).isoformat(), today.isoformat()
    elif date_range_type == "last_30_days":
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    elif date_range_type == "custom" and custom_start and custom_end:
        return custom_start, custom_end
    else:
        return (today - timedelta(days=7)).isoformat(), today.isoformat()


def _day_to_cron(schedule_day: str, schedule_time: str = "09:00") -> str:
    """Convert schedule_day + time to a cron expression."""
    hour, minute = schedule_time.split(":")[:2]
    day_map = {
        "monday": "1", "tuesday": "2", "wednesday": "3",
        "thursday": "4", "friday": "5", "saturday": "6", "sunday": "0",
    }
    if schedule_day == "daily":
        return f"{minute} {hour} * * *"
    day_num = day_map.get(schedule_day.lower(), "1")
    return f"{minute} {hour} * * {day_num}"


def _add_job_log(job_id: str, message: str, level: str = "INFO", run_id: str = None):
    """Add a log entry for a job."""
    try:
        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pulse_job_logs (job_id, run_id, level, message) VALUES (%s, %s, %s, %s)",
            (job_id, run_id, level, message)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── GCP Cloud Run Jobs Integration ───────────────────────────────

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "vz-it-np-voev-dev-voevdo-0")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-east4")
CLOUD_RUN_SERVICE_URL = os.environ.get(
    "CLOUD_RUN_SERVICE_URL",
    "https://email-service-vdsi.ebiz.verizon.com"
)
CLOUD_RUN_SA = os.environ.get(
    "GCP_SERVICE_ACCOUNT",
    "sa-dev-voev-app-voevdo-0@vz-it-np-voev-dev-voevdo-0.iam.gserviceaccount.com"
)
CLOUD_RUN_IMAGE = os.environ.get(
    "CLOUD_RUN_IMAGE",
    "us-east4-docker.pkg.dev/vz-it-np-voev-dev-voevdo-0/development-artifact/pulse-cloudjob:latest"
)
CLOUD_RUN_VPC_CONNECTOR = os.environ.get(
    "CLOUD_RUN_VPC_CONNECTOR",
    "projects/vz-it-np-exhv-sharedvpc-228116/locations/us-east4/connectors/shared-np-east"
)
CLOUD_RUN_KMS_KEY = os.environ.get(
    "CLOUD_RUN_KMS_KEY",
    "projects/vz-it-np-d0sv-vsadkms-0/locations/us-east4/keyRings/vz-it-np-kr-corp/cryptoKeys/vz-it-np-kms-voev"
)


def _sanitize_cloud_job_name(job_id: str, custom_name: str = None) -> str:
    """Sanitize into a valid Cloud Run Job name (lowercase, alphanumeric + hyphens, max 63 chars)."""
    import re as _re
    if custom_name and custom_name.strip():
        name = custom_name.strip().lower()
    else:
        name = f"pulse-{job_id}".lower()
    name = _re.sub(r'[^a-z0-9-]', '-', name)
    name = _re.sub(r'-+', '-', name).strip('-')
    if not name or not name[0].isalpha():
        name = f"pulse-{name}"
    return name[:63]


def _create_or_update_cloud_run_job(job_id: str, cloud_job_name: str, vzid: str, cloud_config: dict = None) -> dict:
    """Create or update a Cloud Run Job for a pulse scheduled job."""
    if not _HAS_CLOUD_RUN:
        return {"status": "error", "message": "google-cloud-run package not installed"}

    cloud_config = cloud_config or {}
    client = run_v2.JobsClient()
    parent = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}"
    full_name = f"{parent}/jobs/{cloud_job_name}"

    cpu = cloud_config.get("cpu", "2")
    memory = cloud_config.get("memory", "2Gi")
    timeout_sec = int(cloud_config.get("timeout", 3600))
    max_retries = int(cloud_config.get("max_retries", 1))

    env_vars = [
        run_v2.EnvVar(name="PULSE_JOB_ID", value=job_id),
        run_v2.EnvVar(name="PULSE_TRIGGER_VZID", value=vzid),
        run_v2.EnvVar(name="PULSE_BACKEND_URL", value=CLOUD_RUN_SERVICE_URL),
    ]

    container = run_v2.Container(
        image=CLOUD_RUN_IMAGE,
        env=env_vars,
        resources=run_v2.ResourceRequirements(
            limits={"cpu": cpu, "memory": memory},
        ),
    )

    template = run_v2.ExecutionTemplate(
        template=run_v2.TaskTemplate(
            containers=[container],
            timeout=f"{timeout_sec}s",
            max_retries=max_retries,
            service_account=CLOUD_RUN_SA,
            vpc_access=run_v2.VpcAccess(
                connector=CLOUD_RUN_VPC_CONNECTOR,
                egress=run_v2.VpcAccess.VpcEgress.ALL_TRAFFIC,
            ),
            encryption_key=CLOUD_RUN_KMS_KEY,
        ),
    )

    job = run_v2.Job(
        template=template,
        labels={"managed-by": "dwp-pulse", "pulse-job-id": job_id[:63]},
    )

    try:
        existing = client.get_job(name=full_name)
        # Job exists, update it
        job.name = full_name
        update_mask = {"paths": ["template", "labels"]}
        client.update_job(job=job, update_mask=update_mask)
        logger.info(f"Cloud Run Job updated: {cloud_job_name}")
        return {"status": "success", "action": "updated", "name": cloud_job_name}
    except Exception as get_err:
        if "404" in str(get_err) or "NotFound" in str(get_err):
            # Create new job
            req = run_v2.CreateJobRequest(
                parent=parent,
                job=job,
                job_id=cloud_job_name,
            )
            client.create_job(request=req)
            logger.info(f"Cloud Run Job created: {cloud_job_name}")
            return {"status": "success", "action": "created", "name": cloud_job_name}
        logger.error(f"Failed to create/update Cloud Run Job: {get_err}")
        return {"status": "error", "message": str(get_err)}


def _create_or_update_scheduler_trigger(cloud_job_name: str, cron_expression: str, timezone: str = "Asia/Kolkata") -> dict:
    """Create or update a Cloud Scheduler job to trigger the Cloud Run Job on a cron schedule."""
    if not _HAS_CLOUD_SCHEDULER:
        return {"status": "error", "message": "google-cloud-scheduler not installed"}

    try:
        from google.cloud import scheduler_v1
        from google.protobuf import duration_pb2

        client = scheduler_v1.CloudSchedulerClient()
        parent = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}"
        scheduler_name = f"{parent}/jobs/pulse-trigger-{cloud_job_name}"

        # Cloud Run Job run endpoint
        run_url = (
            f"https://{GCP_LOCATION}-run.googleapis.com/v2/"
            f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/jobs/{cloud_job_name}:run"
        )

        job = scheduler_v1.Job(
            name=scheduler_name,
            schedule=cron_expression,
            time_zone=timezone,
            http_target=scheduler_v1.HttpTarget(
                uri=run_url,
                http_method=scheduler_v1.HttpMethod.POST,
                oauth_token=scheduler_v1.OAuthToken(
                    service_account_email=CLOUD_RUN_SA,
                    scope="https://www.googleapis.com/auth/cloud-platform",
                ),
            ),
            retry_config=scheduler_v1.RetryConfig(
                retry_count=2,
                max_retry_duration=duration_pb2.Duration(seconds=300),
            ),
        )

        try:
            # Try to update existing
            client.get_job(name=scheduler_name)
            update_mask = {"paths": ["schedule", "time_zone", "http_target", "retry_config"]}
            client.update_job(job=job, update_mask=update_mask)
            logger.info(f"Cloud Scheduler trigger updated: {scheduler_name}")
            return {"status": "success", "action": "updated", "scheduler_name": scheduler_name}
        except Exception as get_err:
            if "404" in str(get_err) or "NotFound" in str(get_err):
                req = scheduler_v1.CreateJobRequest(parent=parent, job=job)
                client.create_job(request=req)
                logger.info(f"Cloud Scheduler trigger created: {scheduler_name}")
                return {"status": "success", "action": "created", "scheduler_name": scheduler_name}
            raise

    except Exception as e:
        logger.error(f"Failed to create/update scheduler trigger: {e}")
        return {"status": "error", "message": str(e)}


def _delete_scheduler_trigger(cloud_job_name: str) -> dict:
    """Delete the Cloud Scheduler trigger for a Cloud Run Job."""
    if not _HAS_CLOUD_SCHEDULER:
        return {"status": "error", "message": "google-cloud-scheduler not installed"}

    try:
        client = scheduler_v1.CloudSchedulerClient()
        scheduler_name = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/jobs/pulse-trigger-{cloud_job_name}"
        client.delete_job(name=scheduler_name)
        logger.info(f"Cloud Scheduler trigger deleted: {scheduler_name}")
        return {"status": "success"}
    except Exception as e:
        if "404" in str(e) or "NotFound" in str(e):
            return {"status": "success", "message": "Already deleted"}
        logger.error(f"Failed to delete scheduler trigger: {e}")
        return {"status": "error", "message": str(e)}


def _pause_scheduler_trigger(cloud_job_name: str) -> dict:
    """Pause the Cloud Scheduler trigger."""
    if not _HAS_CLOUD_SCHEDULER:
        return {"status": "error", "message": "google-cloud-scheduler not installed"}

    try:
        client = scheduler_v1.CloudSchedulerClient()
        scheduler_name = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/jobs/pulse-trigger-{cloud_job_name}"
        client.pause_job(name=scheduler_name)
        logger.info(f"Cloud Scheduler trigger paused: {scheduler_name}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to pause scheduler trigger: {e}")
        return {"status": "error", "message": str(e)}


def _resume_scheduler_trigger(cloud_job_name: str) -> dict:
    """Resume a paused Cloud Scheduler trigger."""
    if not _HAS_CLOUD_SCHEDULER:
        return {"status": "error", "message": "google-cloud-scheduler not installed"}

    try:
        client = scheduler_v1.CloudSchedulerClient()
        scheduler_name = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/jobs/pulse-trigger-{cloud_job_name}"
        client.resume_job(name=scheduler_name)
        logger.info(f"Cloud Scheduler trigger resumed: {scheduler_name}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to resume scheduler trigger: {e}")
        return {"status": "error", "message": str(e)}


def _execute_cloud_run_job(cloud_job_name: str) -> dict:
    """Trigger execution of a Cloud Run Job."""
    if not _HAS_CLOUD_RUN:
        return {"status": "error", "message": "google-cloud-run package not installed"}

    try:
        client = run_v2.JobsClient()
        full_name = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/jobs/{cloud_job_name}"
        client.run_job(name=full_name)
        logger.info(f"Cloud Run Job execution triggered: {cloud_job_name}")
        return {"status": "success", "name": cloud_job_name, "action": "executed"}
    except Exception as e:
        logger.error(f"Failed to execute Cloud Run Job: {e}")
        return {"status": "error", "message": str(e)}


def _delete_cloud_run_job(cloud_job_name: str) -> dict:
    """Delete a Cloud Run Job."""
    if not _HAS_CLOUD_RUN:
        return {"status": "error", "message": "google-cloud-run package not installed"}

    try:
        client = run_v2.JobsClient()
        full_name = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/jobs/{cloud_job_name}"
        client.delete_job(name=full_name)
        logger.info(f"Cloud Run Job deleted: {cloud_job_name}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to delete Cloud Run Job: {e}")
        return {"status": "error", "message": str(e)}


def _get_cloud_run_job_executions(cloud_job_name: str, limit: int = 20) -> list:
    """Get execution history for a Cloud Run Job."""
    if not _HAS_CLOUD_RUN:
        return []

    try:
        client = run_v2.ExecutionsClient()
        parent = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/jobs/{cloud_job_name}"
        executions = list(client.list_executions(parent=parent))

        exec_list = []
        for ex in executions[:limit]:
            exec_list.append({
                "name": ex.name.split("/")[-1] if ex.name else "",
                "create_time": ex.create_time.isoformat() if ex.create_time else "",
                "completion_time": ex.completion_time.isoformat() if ex.completion_time else "",
                "succeeded_count": ex.succeeded_count,
                "failed_count": ex.failed_count,
                "running_count": ex.running_count,
            })
        return exec_list
    except Exception as e:
        logger.error(f"Failed to get executions for {cloud_job_name}: {e}")
        return []


def _get_cloud_run_job_logs(cloud_job_name: str, execution_name: str = None, limit: int = 200) -> list:
    """Fetch logs for a Cloud Run Job from Cloud Logging."""
    if not _HAS_CLOUD_LOGGING:
        return [{"message": "google-cloud-logging not installed", "severity": "ERROR", "timestamp": ""}]

    try:
        client = cloud_logging.Client(project=GCP_PROJECT_ID)

        log_filter = (
            f'resource.type="cloud_run_job" '
            f'resource.labels.job_name="{cloud_job_name}" '
            f'resource.labels.location="{GCP_LOCATION}"'
        )
        if execution_name:
            log_filter += f' labels."run.googleapis.com/execution_name"="{execution_name}"'

        entries = []
        for entry in client.list_entries(
            filter_=log_filter,
            order_by=cloud_logging.DESCENDING,
            max_results=limit,
        ):
            payload = ""
            if isinstance(entry.payload, dict):
                payload = entry.payload.get("message", str(entry.payload))
            elif isinstance(entry.payload, str):
                payload = entry.payload
            else:
                payload = entry.text_payload or str(entry.payload)

            entries.append({
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else "",
                "severity": entry.severity or "DEFAULT",
                "message": payload,
                "execution": (entry.labels or {}).get("run.googleapis.com/execution_name", ""),
            })

        entries.reverse()
        return entries
    except Exception as e:
        logger.error(f"Failed to get logs for {cloud_job_name}: {e}")
        return [{"message": f"Error fetching logs: {e}", "severity": "ERROR", "timestamp": ""}]


# ── Auth / Login ─────────────────────────────────────────────────

@app.route('/pulse/auth/login', methods=['POST'])
def pulse_auth_login():
    """
    Login with VZ ID. Creates user if first time.
    Expected JSON: {"vzid": "anatan8"}
    """
    try:
        data = request.get_json()
        if not data or not data.get("vzid"):
            return jsonify({"status": "error", "message": "Missing 'vzid'"}), 400

        vzid = data["vzid"].strip().lower()
        if not vzid or len(vzid) < 3:
            return jsonify({"status": "error", "message": "Invalid VZ ID"}), 400

        user = _ensure_pulse_user(vzid)
        if not user["is_active"]:
            return jsonify({"status": "error", "message": "Account is deactivated"}), 403

        logger.info(f"Pulse login: {vzid} (role={user['role']})")
        return jsonify({"status": "success", "user": user}), 200
    except Exception as e:
        logger.error(f"Pulse login error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/auth/profile', methods=['PUT'])
def pulse_auth_update_profile():
    """Update user profile (display_name, email)."""
    try:
        data = request.get_json()
        vzid = data.get("vzid", "").strip().lower()
        if not vzid:
            return jsonify({"status": "error", "message": "Missing vzid"}), 400

        conn = _get_pulse_db()
        cur = conn.cursor()
        updates = []
        params = []
        if "display_name" in data:
            updates.append("display_name = %s")
            params.append(data["display_name"])
        if "email" in data:
            updates.append("email = %s")
            params.append(data["email"])

        if not updates:
            conn.close()
            return jsonify({"status": "error", "message": "Nothing to update"}), 400

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(vzid)
        cur.execute(f"UPDATE pulse_users SET {', '.join(updates)} WHERE vzid = %s", params)
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Profile updated"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Job Scheduler CRUD ───────────────────────────────────────────

@app.route('/pulse/jobs', methods=['POST'])
def pulse_create_job():
    """
    Create a new scheduled pulse report job.
    Expected JSON:
    {
        "vzid": "anatan8",
        "job_name": "Weekly Gemini Report",
        "channel_ids": ["C0AE1G97QCV", "C0AH7K4QXCY"],
        "from_email": "do-not-reply@verizon.com",
        "to_emails": ["team@verizon.com"],
        "date_range_type": "last_7_days",
        "schedule_day": "monday",
        "schedule_time": "09:00"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400

        vzid = data.get("vzid", "").strip().lower()
        job_name = data.get("job_name", "").strip()
        channel_ids = data.get("channel_ids", [])
        from_email = data.get("from_email", "do-not-reply@verizon.com").strip()
        to_emails = data.get("to_emails", [])
        date_range_type = data.get("date_range_type", "last_7_days")
        schedule_day = data.get("schedule_day", "monday").lower()
        schedule_time = data.get("schedule_time", "09:00")
        timezone = data.get("timezone", "Asia/Kolkata").strip()

        if not vzid:
            return jsonify({"status": "error", "message": "Missing vzid"}), 400
        if not job_name:
            return jsonify({"status": "error", "message": "Missing job_name"}), 400
        if not channel_ids:
            return jsonify({"status": "error", "message": "Missing channel_ids"}), 400
        if not to_emails:
            return jsonify({"status": "error", "message": "Missing to_emails"}), 400

        _ensure_pulse_user(vzid)

        cron_expr = _day_to_cron(schedule_day, schedule_time)
        job_id = str(uuid.uuid4())
        cloud_job_name = _sanitize_cloud_job_name(job_id, f"pulse-{vzid}-{job_name[:30]}")

        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO pulse_jobs
               (job_id, job_name, created_by, channel_ids, from_email, to_emails,
                date_range_type, schedule_day, schedule_time, cron_expression, cloud_job_name, timezone, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')""",
            (job_id, job_name, vzid, channel_ids, from_email, to_emails,
             date_range_type, schedule_day, schedule_time, cron_expr, cloud_job_name, timezone)
        )
        conn.commit()
        conn.close()

        # Create actual GCP Cloud Run Job
        cloud_result = _create_or_update_cloud_run_job(job_id, cloud_job_name, vzid)
        if cloud_result["status"] != "success":
            logger.warning(f"Cloud Run Job creation failed for {job_id}: {cloud_result.get('message')}")

        # Create Cloud Scheduler trigger for the cron schedule
        scheduler_result = _create_or_update_scheduler_trigger(cloud_job_name, cron_expr, timezone)
        if scheduler_result["status"] != "success":
            logger.warning(f"Cloud Scheduler trigger failed for {job_id}: {scheduler_result.get('message')}")

        _add_job_log(job_id, f"Job '{job_name}' created by {vzid} (cloud: {cloud_result['status']}, scheduler: {scheduler_result['status']})")
        logger.info(f"Pulse job created: {job_id} by {vzid}")

        return jsonify({
            "status": "success",
            "job_id": job_id,
            "job_name": job_name,
            "cloud_job_name": cloud_job_name,
            "cron_expression": cron_expr,
            "timezone": timezone,
            "cloud_run_status": cloud_result["status"],
            "scheduler_status": scheduler_result["status"],
        }), 201
    except Exception as e:
        logger.error(f"Error creating pulse job: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/jobs', methods=['GET'])
def pulse_list_jobs():
    """List jobs for a user. Admin sees all. Query params: vzid"""
    try:
        vzid = request.args.get("vzid", "").strip().lower()
        if not vzid:
            return jsonify({"status": "error", "message": "Missing vzid"}), 400

        conn = _get_pulse_db()
        cur = conn.cursor()

        if _is_admin(vzid):
            cur.execute(
                """SELECT job_id, job_name, created_by, channel_ids, from_email, to_emails,
                          date_range_type, schedule_day, schedule_time, cron_expression,
                          cloud_job_name, status, last_run_at, next_run_at, run_count, created_at,
                          COALESCE(timezone, 'Asia/Kolkata') AS timezone
                   FROM pulse_jobs WHERE status != 'deleted' ORDER BY created_at DESC"""
            )
        else:
            cur.execute(
                """SELECT job_id, job_name, created_by, channel_ids, from_email, to_emails,
                          date_range_type, schedule_day, schedule_time, cron_expression,
                          cloud_job_name, status, last_run_at, next_run_at, run_count, created_at,
                          COALESCE(timezone, 'Asia/Kolkata') AS timezone
                   FROM pulse_jobs WHERE created_by = %s AND status != 'deleted' ORDER BY created_at DESC""",
                (vzid,)
            )

        columns = ["job_id", "job_name", "created_by", "channel_ids", "from_email", "to_emails",
                   "date_range_type", "schedule_day", "schedule_time", "cron_expression",
                   "cloud_job_name", "status", "last_run_at", "next_run_at", "run_count", "created_at", "timezone"]
        jobs = []
        for row in cur.fetchall():
            job = dict(zip(columns, row))
            job["schedule_time"] = str(job["schedule_time"]) if job["schedule_time"] else None
            job["last_run_at"] = str(job["last_run_at"]) if job["last_run_at"] else None
            job["next_run_at"] = str(job["next_run_at"]) if job["next_run_at"] else None
            job["created_at"] = str(job["created_at"]) if job["created_at"] else None
            jobs.append(job)
        conn.close()
        return jsonify({"status": "success", "jobs": jobs, "total": len(jobs)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/jobs/<job_id>', methods=['GET'])
def pulse_get_job(job_id):
    """Get job details by ID."""
    try:
        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT job_id, job_name, created_by, channel_ids, from_email, to_emails,
                      date_range_type, schedule_day, schedule_time, cron_expression,
                      cloud_job_name, status, last_run_at, next_run_at, run_count, created_at,
                      COALESCE(timezone, 'Asia/Kolkata') AS timezone
               FROM pulse_jobs WHERE job_id = %s""", (job_id,)
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({"status": "error", "message": "Job not found"}), 404

        columns = ["job_id", "job_name", "created_by", "channel_ids", "from_email", "to_emails",
                   "date_range_type", "schedule_day", "schedule_time", "cron_expression",
                   "cloud_job_name", "status", "last_run_at", "next_run_at", "run_count", "created_at", "timezone"]
        job = dict(zip(columns, row))
        job["schedule_time"] = str(job["schedule_time"]) if job["schedule_time"] else None
        job["last_run_at"] = str(job["last_run_at"]) if job["last_run_at"] else None
        job["next_run_at"] = str(job["next_run_at"]) if job["next_run_at"] else None
        job["created_at"] = str(job["created_at"]) if job["created_at"] else None
        return jsonify({"status": "success", "job": job}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/jobs/<job_id>', methods=['PUT'])
def pulse_update_job(job_id):
    """Update a job. Owner or admin can edit. Updates Cloud Scheduler if schedule changes."""
    try:
        data = request.get_json()
        vzid = data.get("vzid", "").strip().lower()
        if not vzid:
            return jsonify({"status": "error", "message": "Missing vzid"}), 400

        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute("SELECT created_by, cloud_job_name FROM pulse_jobs WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Job not found"}), 404
        if row[0] != vzid and not _is_admin(vzid):
            conn.close()
            return jsonify({"status": "error", "message": "Not authorized"}), 403

        cloud_job_name = row[1]
        updates = []
        params = []
        editable = ["job_name", "channel_ids", "from_email", "to_emails",
                    "date_range_type", "schedule_day", "schedule_time", "status", "timezone"]
        for field in editable:
            if field in data:
                updates.append(f"{field} = %s")
                params.append(data[field])

        cron_expr = None
        new_tz = data.get("timezone")
        if "schedule_day" in data or "schedule_time" in data:
            new_day = data.get("schedule_day", "monday")
            new_time = data.get("schedule_time", "09:00")
            cron_expr = _day_to_cron(new_day, new_time)
            updates.append("cron_expression = %s")
            params.append(cron_expr)

        if not updates:
            conn.close()
            return jsonify({"status": "error", "message": "Nothing to update"}), 400

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(job_id)
        cur.execute(f"UPDATE pulse_jobs SET {', '.join(updates)} WHERE job_id = %s", params)
        conn.commit()
        conn.close()

        # Update Cloud Run Job if needed (rebuild with same config)
        if cloud_job_name:
            _create_or_update_cloud_run_job(job_id, cloud_job_name, vzid)
            # Update scheduler trigger if schedule or timezone changed
            if cron_expr or new_tz:
                tz = new_tz or "Asia/Kolkata"
                _create_or_update_scheduler_trigger(cloud_job_name, cron_expr or "", tz)

        _add_job_log(job_id, f"Job updated by {vzid}: {list(data.keys())}")
        return jsonify({"status": "success", "message": "Job updated"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/jobs/<job_id>', methods=['DELETE'])
def pulse_delete_job(job_id):
    """Soft-delete a job. Owner or admin. Also deletes Cloud Scheduler job."""
    try:
        vzid = request.args.get("vzid", "").strip().lower()
        if not vzid:
            return jsonify({"status": "error", "message": "Missing vzid"}), 400

        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute("SELECT created_by, cloud_job_name FROM pulse_jobs WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Job not found"}), 404
        if row[0] != vzid and not _is_admin(vzid):
            conn.close()
            return jsonify({"status": "error", "message": "Not authorized"}), 403

        cloud_job_name = row[1]

        cur.execute(
            "UPDATE pulse_jobs SET status = 'deleted', updated_at = CURRENT_TIMESTAMP WHERE job_id = %s", (job_id,)
        )
        conn.commit()
        conn.close()

        # Delete the Cloud Run Job and its scheduler trigger
        if cloud_job_name:
            _delete_scheduler_trigger(cloud_job_name)
            _delete_cloud_run_job(cloud_job_name)

        _add_job_log(job_id, f"Job deleted by {vzid}")
        return jsonify({"status": "success", "message": "Job deleted"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/jobs/<job_id>/pause', methods=['POST'])
def pulse_pause_job(job_id):
    """Pause/Resume a job. Also pauses/resumes the Cloud Scheduler job."""
    try:
        data = request.get_json() or {}
        vzid = data.get("vzid", "").strip().lower()
        if not vzid:
            return jsonify({"status": "error", "message": "Missing vzid"}), 400

        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute("SELECT created_by, status, cloud_job_name FROM pulse_jobs WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Job not found"}), 404
        if row[0] != vzid and not _is_admin(vzid):
            conn.close()
            return jsonify({"status": "error", "message": "Not authorized"}), 403

        new_status = "active" if row[1] == "paused" else "paused"
        cloud_job_name = row[2]

        cur.execute(
            "UPDATE pulse_jobs SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE job_id = %s",
            (new_status, job_id)
        )
        conn.commit()
        conn.close()

        # Pause/resume the Cloud Scheduler trigger
        if cloud_job_name:
            if new_status == "paused":
                _pause_scheduler_trigger(cloud_job_name)
            else:
                _resume_scheduler_trigger(cloud_job_name)

        _add_job_log(job_id, f"Job {'resumed' if new_status == 'active' else 'paused'} by {vzid}")
        return jsonify({"status": "success", "new_status": new_status}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/jobs/<job_id>/run', methods=['POST'])
def pulse_manual_run_job(job_id):
    """Manually trigger a job run via Cloud Run Job execution (shows in GCP)."""
    try:
        data = request.get_json() or {}
        vzid = data.get("vzid", "").strip().lower()
        if not vzid:
            return jsonify({"status": "error", "message": "Missing vzid"}), 400

        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT created_by, cloud_job_name FROM pulse_jobs WHERE job_id = %s AND status != 'deleted'",
            (job_id,)
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({"status": "error", "message": "Job not found"}), 404
        if row[0] != vzid and not _is_admin(vzid):
            return jsonify({"status": "error", "message": "Not authorized"}), 403

        cloud_job_name = row[1]

        if cloud_job_name:
            # Trigger the Cloud Run Job — it will call /pulse/jobs/<id>/execute
            result = _execute_cloud_run_job(cloud_job_name)
            if result["status"] == "success":
                _add_job_log(job_id, f"Cloud Run Job execution triggered by {vzid} (manual run)")
                return jsonify({
                    "status": "success",
                    "message": "Job execution triggered via Cloud Run. Check Cloud Executions tab for progress.",
                    "cloud_job_name": cloud_job_name,
                    "execution_mode": "cloud_run_job",
                }), 200
            else:
                # Fallback to inline execution if Cloud Run trigger fails
                logger.warning(f"Cloud Run trigger failed, falling back to inline: {result.get('message')}")
                _add_job_log(job_id, f"Cloud Run trigger failed ({result.get('message')}), running inline", level="WARNING")

        # Inline fallback (or no cloud_job_name)
        return _execute_job_inline(job_id, vzid)

    except Exception as e:
        logger.error(f"Manual run error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/jobs/<job_id>/execute', methods=['POST'])
def pulse_execute_job(job_id):
    """
    Internal endpoint called by Cloud Run Job container to execute the actual job logic.
    This does the report generation, email sending, and DB updates.
    """
    try:
        data = request.get_json() or {}
        vzid = data.get("vzid", "").strip().lower()
        # Cloud Run Jobs pass vzid via env var → cloudjob/main.py passes it in body
        if not vzid:
            vzid = "scheduler"

        return _execute_job_inline(job_id, vzid)

    except Exception as e:
        logger.error(f"Execute job error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


def _execute_job_inline(job_id: str, vzid: str):
    """Execute a pulse job inline — generate report, send email, update DB."""
    run_id = None

    try:
        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT job_id, job_name, created_by, channel_ids, from_email, to_emails,
                      date_range_type, custom_start_date, custom_end_date
               FROM pulse_jobs WHERE job_id = %s AND status != 'deleted'""", (job_id,)
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Job not found"}), 404

        job_name = row[1]
        channel_ids = row[3]
        from_email = row[4]
        to_emails = row[5]
        date_range_type = row[6]
        custom_start = str(row[7]) if row[7] else None
        custom_end = str(row[8]) if row[8] else None

        start_date, end_date = _get_date_range_for_type(date_range_type, custom_start, custom_end)

        # Create run record
        run_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO pulse_job_runs (run_id, job_id, triggered_by, status, start_date, end_date) VALUES (%s, %s, %s, 'running', %s, %s)",
            (run_id, job_id, vzid, start_date, end_date)
        )
        conn.commit()
        conn.close()

        _add_job_log(job_id, f"Job execution started by {vzid}", run_id=run_id)

        import time as _time
        run_start = _time.time()
        _add_job_log(job_id, "Starting report generation...", run_id=run_id)

        # Generate report
        all_channel_feedback = {}
        channel_details = []
        total_messages = 0
        fetch_start = _time.time()

        for ch_id in channel_ids:
            messages_text, msg_count, ch_name, available_range, gap_warning = _fetch_pulse_channel_messages(ch_id, start_date, end_date)
            total_messages += msg_count
            
            analyze_start = _time.time()
            if messages_text:
                feedback = _analyze_messages_with_gemini(messages_text)
            else:
                feedback = []
            analyze_duration = _time.time() - analyze_start
            _add_job_log(job_id, f"Channel {ch_name}: AI analysis took {analyze_duration:.2f}s for {msg_count} messages.", run_id=run_id)

            all_channel_feedback[ch_name] = feedback
            channel_details.append({
                "channel_id": ch_id, "channel_name": ch_name,
                "message_count": msg_count, "feedback_count": len(feedback),
            })
        
        fetch_duration = _time.time() - fetch_start
        _add_job_log(job_id, f"Data fetching and initial analysis took {fetch_duration:.2f}s.", run_id=run_id)

        consolidate_start = _time.time()
        if len(all_channel_feedback) > 1:
            consolidated = _consolidate_feedback(all_channel_feedback)
        else:
            consolidated = []
            for feedbacks in all_channel_feedback.values():
                consolidated.extend(feedbacks)
        consolidate_duration = _time.time() - consolidate_start
        _add_job_log(job_id, f"Feedback consolidation took {consolidate_duration:.2f}s.", run_id=run_id)

        report_id = str(uuid.uuid4())
        report_data = {
            "start_date": start_date, "end_date": end_date,
            "feedback": consolidated, "channels": channel_details,
            "total_messages": total_messages,
        }
        
        html_start = _time.time()
        html_content = _generate_pulse_html(report_data)
        html_duration = _time.time() - html_start
        _add_job_log(job_id, f"HTML report generation took {html_duration:.2f}s.", run_id=run_id)

        # Save report
        db_save_start = _time.time()
        try:
            conn2 = _get_pulse_db()
            cur2 = conn2.cursor()
            cur2.execute(
                """INSERT INTO pulse_reports (report_id, title, start_date, end_date, status,
                             total_messages, total_feedback, report_json, html_content, created_by, completed_at)
                   VALUES (%s, %s, %s, %s, 'completed', %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)""",
                (report_id, f"{job_name}: {start_date} to {end_date}",
                 start_date, end_date, total_messages, len(consolidated),
                 _json.dumps(report_data), html_content, vzid)
            )
            conn2.commit()
            conn2.close()
            db_save_duration = _time.time() - db_save_start
            _add_job_log(job_id, f"DB save took {db_save_duration:.2f}s.", run_id=run_id)
        except Exception as db_err:
            logger.warning(f"Job run DB save error: {db_err}")
            _add_job_log(job_id, f"DB save failed: {db_err}", run_id=run_id, level="ERROR")

        # Send email via /send-email-dont-reply API
        email_start = _time.time()
        email_status = "sent"
        email_error = None
        subject = f"DWP Pulse Report: {start_date} to {end_date}"
        try:
            resp = http_requests.post(
                f"{CLOUD_RUN_SERVICE_URL}/send-email-vdsi",
                json={
                    "to": to_emails,
                    "from": DO_NOT_REPLY_FROM,
                    "subject": subject,
                    "message": html_content,
                },
                timeout=120,
            )
            resp.raise_for_status()
            email_duration = _time.time() - email_start
            _add_job_log(job_id, f"Email sending took {email_duration:.2f}s.", run_id=run_id)
        except Exception as email_err:
            email_status = "failed"
            email_error = str(email_err)
            logger.error(f"Job email send failed: {email_err}")
            _add_job_log(job_id, f"Email sending failed: {email_err}", run_id=run_id, level="ERROR")

        # Record email history
        try:
            conn3 = _get_pulse_db()
            cur3 = conn3.cursor()
            cur3.execute(
                """INSERT INTO pulse_email_history (report_id, job_id, sent_by, from_email, to_emails, subject, status, error_message)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (report_id, job_id, vzid, from_email, to_emails, subject, email_status, email_error)
            )
            conn3.commit()
            conn3.close()
        except Exception:
            pass

        # Update run record
        duration = int(_time.time() - run_start)
        try:
            conn4 = _get_pulse_db()
            cur4 = conn4.cursor()
            cur4.execute(
                """UPDATE pulse_job_runs SET status = 'completed', report_id = %s,
                          total_messages = %s, total_feedback = %s, channels_processed = %s,
                          completed_at = CURRENT_TIMESTAMP, duration_seconds = %s
                   WHERE run_id = %s""",
                (report_id, total_messages, len(consolidated), len(channel_ids), duration, run_id)
            )
            cur4.execute(
                "UPDATE pulse_jobs SET last_run_at = CURRENT_TIMESTAMP, run_count = run_count + 1 WHERE job_id = %s",
                (job_id,)
            )
            conn4.commit()
            conn4.close()
        except Exception:
            pass

        _add_job_log(job_id, f"Run completed: {total_messages} msgs, {len(consolidated)} feedback, email={email_status}", run_id=run_id)

        return jsonify({
            "status": "success", "run_id": run_id, "report_id": report_id,
            "total_messages": total_messages, "total_feedback": len(consolidated),
            "email_status": email_status, "duration_seconds": duration,
        }), 200

    except Exception as e:
        logger.error(f"Manual run error: {e}", exc_info=True)
        try:
            conn_err = _get_pulse_db()
            cur_err = conn_err.cursor()
            cur_err.execute(
                "UPDATE pulse_job_runs SET status = 'failed', error_message = %s, completed_at = CURRENT_TIMESTAMP WHERE run_id = %s",
                (str(e), run_id)
            )
            conn_err.commit()
            conn_err.close()
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        # Restore proxy settings
        if original_proxy:
            os.environ["HTTPS_PROXY"] = original_proxy
            os.environ["https_proxy"] = original_proxy


# ── Job History & Logs ───────────────────────────────────────────

@app.route('/pulse/jobs/<job_id>/history', methods=['GET'])
def pulse_job_history(job_id):
    """Get run history for a job."""
    try:
        limit = int(request.args.get("limit", 20))
        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT run_id, job_id, triggered_by, status, report_id, start_date, end_date,
                      total_messages, total_feedback, channels_processed, error_message,
                      started_at, completed_at, duration_seconds
               FROM pulse_job_runs WHERE job_id = %s ORDER BY started_at DESC LIMIT %s""",
            (job_id, limit)
        )
        columns = ["run_id", "job_id", "triggered_by", "status", "report_id", "start_date", "end_date",
                   "total_messages", "total_feedback", "channels_processed", "error_message",
                   "started_at", "completed_at", "duration_seconds"]
        runs = []
        for row in cur.fetchall():
            run = dict(zip(columns, row))
            run["started_at"] = str(run["started_at"]) if run["started_at"] else None
            run["completed_at"] = str(run["completed_at"]) if run["completed_at"] else None
            run["start_date"] = str(run["start_date"]) if run["start_date"] else None
            run["end_date"] = str(run["end_date"]) if run["end_date"] else None
            runs.append(run)
        conn.close()
        return jsonify({"status": "success", "runs": runs}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/jobs/<job_id>/logs', methods=['GET'])
def pulse_job_logs_endpoint(job_id):
    """Get logs for a job."""
    try:
        limit = int(request.args.get("limit", 50))
        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT log_id, job_id, run_id, level, message, created_at
               FROM pulse_job_logs WHERE job_id = %s ORDER BY created_at DESC LIMIT %s""",
            (job_id, limit)
        )
        columns = ["log_id", "job_id", "run_id", "level", "message", "created_at"]
        logs = []
        for row in cur.fetchall():
            log = dict(zip(columns, row))
            log["created_at"] = str(log["created_at"]) if log["created_at"] else None
            logs.append(log)
        conn.close()
        return jsonify({"status": "success", "logs": logs}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Report History ───────────────────────────────────────────────

@app.route('/pulse/reports/history', methods=['GET'])
def pulse_report_history():
    """Get report + email history for a user (last 30 days). Admin sees all."""
    try:
        vzid = request.args.get("vzid", "").strip().lower()
        if not vzid:
            return jsonify({"status": "error", "message": "Missing vzid"}), 400

        conn = _get_pulse_db()
        cur = conn.cursor()

        if _is_admin(vzid):
            cur.execute(
                """SELECT r.report_id, r.title, r.start_date, r.end_date, r.status,
                          r.total_messages, r.total_feedback, r.created_by, r.created_at,
                          e.to_emails, e.status AS email_status, e.sent_at
                   FROM pulse_reports r
                   LEFT JOIN pulse_email_history e ON r.report_id = e.report_id
                   WHERE r.created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
                   ORDER BY r.created_at DESC LIMIT 100"""
            )
        else:
            cur.execute(
                """SELECT r.report_id, r.title, r.start_date, r.end_date, r.status,
                          r.total_messages, r.total_feedback, r.created_by, r.created_at,
                          e.to_emails, e.status AS email_status, e.sent_at
                   FROM pulse_reports r
                   LEFT JOIN pulse_email_history e ON r.report_id = e.report_id
                   WHERE r.created_by = %s AND r.created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
                   ORDER BY r.created_at DESC LIMIT 50""",
                (vzid,)
            )

        columns = ["report_id", "title", "start_date", "end_date", "status",
                   "total_messages", "total_feedback", "created_by", "created_at",
                   "to_emails", "email_status", "sent_at"]
        reports = []
        for row in cur.fetchall():
            rec = dict(zip(columns, row))
            rec["start_date"] = str(rec["start_date"]) if rec["start_date"] else None
            rec["end_date"] = str(rec["end_date"]) if rec["end_date"] else None
            rec["created_at"] = str(rec["created_at"]) if rec["created_at"] else None
            rec["sent_at"] = str(rec["sent_at"]) if rec["sent_at"] else None
            reports.append(rec)
        conn.close()
        return jsonify({"status": "success", "reports": reports, "total": len(reports)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Admin APIs ───────────────────────────────────────────────────

@app.route('/pulse/admin/dashboard', methods=['GET'])
def pulse_admin_dashboard():
    """Admin dashboard metrics."""
    try:
        vzid = request.args.get("vzid", "").strip().lower()
        if not _is_admin(vzid):
            return jsonify({"status": "error", "message": "Admin access required"}), 403

        conn = _get_pulse_db()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM pulse_jobs WHERE status != 'deleted'")
        total_jobs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pulse_jobs WHERE status = 'active'")
        active_jobs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pulse_users WHERE is_active = TRUE")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pulse_reports WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'")
        reports_30d = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pulse_email_history WHERE sent_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'")
        emails_30d = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pulse_job_runs WHERE started_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'")
        runs_7d = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pulse_job_runs WHERE status = 'failed' AND started_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'")
        failed_7d = cur.fetchone()[0]

        cur.execute("SELECT vzid, role, last_login FROM pulse_users WHERE is_active = TRUE ORDER BY last_login DESC NULLS LAST LIMIT 10")
        recent_users = [{"vzid": r[0], "role": r[1], "last_login": str(r[2]) if r[2] else None} for r in cur.fetchall()]

        cur.execute(
            """SELECT created_by, COUNT(*) as job_count, SUM(run_count) as total_runs
               FROM pulse_jobs WHERE status != 'deleted' GROUP BY created_by ORDER BY job_count DESC"""
        )
        jobs_by_user = [{"vzid": r[0], "job_count": r[1], "total_runs": r[2] or 0} for r in cur.fetchall()]

        conn.close()
        return jsonify({
            "status": "success",
            "metrics": {
                "total_jobs": total_jobs, "active_jobs": active_jobs,
                "total_users": total_users, "reports_30d": reports_30d,
                "emails_30d": emails_30d, "runs_7d": runs_7d, "failed_7d": failed_7d,
            },
            "recent_users": recent_users,
            "jobs_by_user": jobs_by_user,
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/admin/users', methods=['GET'])
def pulse_admin_users():
    """List all users (admin only)."""
    try:
        vzid = request.args.get("vzid", "").strip().lower()
        if not _is_admin(vzid):
            return jsonify({"status": "error", "message": "Admin access required"}), 403

        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute("SELECT vzid, display_name, email, role, is_active, last_login, created_at FROM pulse_users ORDER BY created_at DESC")
        columns = ["vzid", "display_name", "email", "role", "is_active", "last_login", "created_at"]
        users = []
        for row in cur.fetchall():
            user = dict(zip(columns, row))
            user["last_login"] = str(user["last_login"]) if user["last_login"] else None
            user["created_at"] = str(user["created_at"]) if user["created_at"] else None
            users.append(user)
        conn.close()
        return jsonify({"status": "success", "users": users}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/admin/all-runs', methods=['GET'])
def pulse_admin_all_runs():
    """List all recent runs across all users (admin only)."""
    try:
        vzid = request.args.get("vzid", "").strip().lower()
        if not _is_admin(vzid):
            return jsonify({"status": "error", "message": "Admin access required"}), 403

        limit = int(request.args.get("limit", 50))
        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT jr.run_id, jr.job_id, j.job_name, j.created_by, jr.triggered_by,
                      jr.status, jr.total_messages, jr.total_feedback, jr.started_at,
                      jr.completed_at, jr.duration_seconds
               FROM pulse_job_runs jr
               JOIN pulse_jobs j ON jr.job_id = j.job_id
               ORDER BY jr.started_at DESC LIMIT %s""", (limit,)
        )
        columns = ["run_id", "job_id", "job_name", "created_by", "triggered_by",
                   "status", "total_messages", "total_feedback", "started_at",
                   "completed_at", "duration_seconds"]
        runs = []
        for row in cur.fetchall():
            run = dict(zip(columns, row))
            run["started_at"] = str(run["started_at"]) if run["started_at"] else None
            run["completed_at"] = str(run["completed_at"]) if run["completed_at"] else None
            runs.append(run)
        conn.close()
        return jsonify({"status": "success", "runs": runs}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Scheduled Job Execution (called by Cloud Scheduler tick) ─────

@app.route('/pulse/scheduler/run', methods=['POST'])
def pulse_scheduler_run():
    """Called by Cloud Scheduler to execute due jobs for the current day.
    Triggers each due job as a Cloud Run Job execution."""
    try:
        today = datetime.now()
        current_day = today.strftime("%A").lower()

        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT job_id, created_by, cloud_job_name
               FROM pulse_jobs
               WHERE status = 'active' AND (schedule_day = %s OR schedule_day = 'daily')""",
            (current_day,)
        )
        jobs_to_run = cur.fetchall()
        conn.close()

        results = []
        for job_row in jobs_to_run:
            j_id, j_owner, cloud_name = job_row[0], job_row[1], job_row[2]
            try:
                if cloud_name:
                    # Trigger Cloud Run Job execution
                    exec_result = _execute_cloud_run_job(cloud_name)
                    results.append({"job_id": j_id, "cloud_job_name": cloud_name,
                                    "status": exec_result.get("status", "unknown")})
                else:
                    # Fallback: call the run endpoint directly
                    with app.test_client() as client:
                        client.post(f'/pulse/jobs/{j_id}/run',
                                    json={"vzid": j_owner},
                                    content_type='application/json')
                    results.append({"job_id": j_id, "status": "triggered"})
                _add_job_log(j_id, f"Scheduler triggered execution")
            except Exception as job_err:
                results.append({"job_id": j_id, "status": "error", "message": str(job_err)})

        logger.info(f"Pulse scheduler: {current_day}, ran {len(results)} jobs")
        return jsonify({"status": "success", "day": current_day, "jobs_executed": results}), 200
    except Exception as e:
        logger.error(f"Scheduler run error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Cloud Run Job Management Endpoints ───────────────────────────

@app.route('/pulse/cloud-jobs', methods=['GET'])
def pulse_list_cloud_jobs():
    """List all Cloud Run Jobs created by DWP Pulse."""
    try:
        vzid = request.args.get("vzid", "").strip().lower()
        if not vzid or not _is_admin(vzid):
            return jsonify({"status": "error", "message": "Admin access required"}), 403

        if not _HAS_CLOUD_RUN:
            return jsonify({"status": "error", "message": "Cloud Run SDK not available"}), 500

        client = run_v2.JobsClient()
        parent = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}"

        jobs = []
        for job in client.list_jobs(parent=parent):
            labels = dict(job.labels) if job.labels else {}
            if labels.get("managed-by") == "dwp-pulse":
                config = {}
                try:
                    tpl = job.template.template
                    if tpl.containers:
                        c = tpl.containers[0]
                        limits = dict(c.resources.limits) if c.resources and c.resources.limits else {}
                        config["cpu"] = limits.get("cpu", "2")
                        config["memory"] = limits.get("memory", "2Gi")
                        config["image"] = c.image or ""
                    config["timeout"] = tpl.timeout.seconds if tpl.timeout else 900
                    config["max_retries"] = tpl.max_retries or 0
                except Exception:
                    pass

                jobs.append({
                    "name": job.name.split("/")[-1] if job.name else "",
                    "full_name": job.name,
                    "pulse_job_id": labels.get("pulse-job-id", ""),
                    "create_time": job.create_time.isoformat() if job.create_time else "",
                    "update_time": job.update_time.isoformat() if job.update_time else "",
                    "execution_count": job.execution_count,
                    "config": config,
                })

        return jsonify({"status": "success", "cloud_jobs": jobs, "total": len(jobs)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/cloud-jobs/<name>/executions', methods=['GET'])
def pulse_cloud_job_executions(name):
    """Get execution history for a Cloud Run Job."""
    try:
        limit = int(request.args.get("limit", 20))
        executions = _get_cloud_run_job_executions(name, limit)
        return jsonify({"status": "success", "executions": executions}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/cloud-jobs/<name>/logs', methods=['GET'])
def pulse_cloud_job_logs(name):
    """Get logs for a Cloud Run Job from Cloud Logging."""
    try:
        execution = request.args.get("execution")
        limit = int(request.args.get("limit", 200))
        logs = _get_cloud_run_job_logs(name, execution_name=execution, limit=limit)
        return jsonify({"status": "success", "logs": logs, "count": len(logs)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/cloud-jobs/<name>/run', methods=['POST'])
def pulse_cloud_job_manual_run(name):
    """Manually trigger a Cloud Run Job execution."""
    try:
        result = _execute_cloud_run_job(name)
        return jsonify(result), 200 if result["status"] == "success" else 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/pulse/jobs/sync-triggers', methods=['POST'])
def pulse_sync_triggers():
    """Sync Cloud Scheduler triggers for all active jobs that may be missing them."""
    try:
        data = request.get_json() or {}
        vzid = data.get("vzid", "").strip().lower()
        if not vzid or not _is_admin(vzid):
            return jsonify({"status": "error", "message": "Admin access required"}), 403

        conn = _get_pulse_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT job_id, cloud_job_name, cron_expression, COALESCE(timezone, 'Asia/Kolkata') FROM pulse_jobs WHERE status = 'active' AND cloud_job_name IS NOT NULL"
        )
        rows = cur.fetchall()
        conn.close()

        results = []
        for row in rows:
            job_id, cloud_name, cron, tz = row
            if cloud_name and cron:
                res = _create_or_update_scheduler_trigger(cloud_name, cron, tz)
                results.append({"job_id": job_id, "cloud_job_name": cloud_name, "timezone": tz, "result": res})

        return jsonify({"status": "success", "synced": len(results), "results": results}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════
# OpenAI-Compatible API for Cline (VS Code AI Copilot)
# ══════════════════════════════════════════════════════════════════════
CLINE_API_KEY = "corp-gemini-sec-8f3b2c9a1d4e7f6b0a9c8b7a6f5e4d3c"  # Change this to your own secret

CLINE_MODEL_MAP = {
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-pro": "gemini-2.5-pro",
}


def _validate_cline_auth(headers):
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != CLINE_API_KEY:
        return False
    return True


def _extract_text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


def _build_tool_id_name_map(messages):
    """Return {tool_call_id: function_name} by scanning assistant messages."""
    mapping = {}
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in (msg.get("tool_calls") or []):
                if tc.get("type") == "function":
                    mapping[tc.get("id", "")] = tc["function"].get("name", "")
    return mapping


def _openai_messages_to_vertex(messages):
    system_parts = []
    contents = []
    tool_id_map = _build_tool_id_name_map(messages)

    for msg in messages:
        role = msg.get("role", "user")

        if role == "system":
            system_parts.append(_extract_text_content(msg.get("content", "")))

        elif role == "tool":
            # Cline tool result → Vertex function response
            tool_call_id = msg.get("tool_call_id", "")
            func_name = tool_id_map.get(tool_call_id, tool_call_id or "tool")
            result_text = _extract_text_content(msg.get("content", ""))
            contents.append(Content(
                role="user",
                parts=[Part.from_function_response(name=func_name, response={"output": result_text})]
            ))

        elif role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            parts = []
            text = _extract_text_content(msg.get("content") or "")
            if text:
                parts.append(Part.from_text(text))
            for tc in tool_calls:
                if tc.get("type") == "function":
                    func = tc.get("function", {})
                    try:
                        args = _json.loads(func.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    try:
                        parts.append(Part.from_function_call(name=func.get("name", ""), args=args))
                    except (AttributeError, TypeError):
                        parts.append(Part.from_text(f"[tool call: {func.get('name')}]"))
            if parts:
                contents.append(Content(role="model", parts=parts))

        else:  # user
            text = _extract_text_content(msg.get("content", ""))
            if text:
                contents.append(Content(role="user", parts=[Part.from_text(text)]))

    system_instruction = "\n\n".join(system_parts) if system_parts else None
    return system_instruction, contents


def _openai_tools_to_vertex(tools):
    """Convert OpenAI tools array → Vertex AI Tool list."""
    if not tools or not FunctionDeclaration:
        return None
    declarations = []
    for tool in tools:
        if tool.get("type") == "function":
            func = tool.get("function", {})
            params = func.get("parameters", {"type": "object", "properties": {}})
            declarations.append(FunctionDeclaration(
                name=func.get("name", ""),
                description=func.get("description", ""),
                parameters=params,
            ))
    return [Tool(function_declarations=declarations)] if declarations else None


def _vertex_candidate_to_openai(candidate, model_id, response_id):
    """Convert a Vertex AI candidate to OpenAI response dict."""
    parts = list(candidate.content.parts) if candidate.content else []
    tool_calls, text_parts = [], []

    for part in parts:
        if hasattr(part, "function_call") and part.function_call and part.function_call.name:
            fc = part.function_call
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": fc.name,
                    "arguments": _json.dumps(dict(fc.args) if fc.args else {}),
                },
            })
        elif hasattr(part, "text") and part.text:
            text_parts.append(part.text)

    if tool_calls:
        return {
            "id": response_id,
            "object": "chat.completion",
            "model": model_id,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(text_parts) or None,
                    "tool_calls": tool_calls,
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    return {
        "id": response_id,
        "object": "chat.completion",
        "model": model_id,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "".join(text_parts)},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.route('/v1/chat/completions', methods=['POST'])
def cline_chat_completions():
    if not _VERTEXAI_AVAILABLE:
        return jsonify({"error": {"message": "Vertex AI not available", "type": "server_error"}}), 503

    if not _validate_cline_auth(request.headers):
        return jsonify({"error": {"message": "Invalid API key", "type": "authentication_error"}}), 401

    data = request.get_json(force=True)
    model_id = data.get("model", "gemini-2.5-flash")
    messages = data.get("messages", [])
    stream = data.get("stream", False)
    temperature = data.get("temperature")
    max_tokens = data.get("max_tokens")
    tools = data.get("tools")

    # target_model = CLINE_MODEL_MAP.get(model_id, "gemini-2.5-flash")
    target_model = CLINE_MODEL_MAP.get(model_id, model_id)

    try:
        system_instruction, contents = _openai_messages_to_vertex(messages)
        vertex_tools = _openai_tools_to_vertex(tools)

        generation_config = {}
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_tokens is not None:
            generation_config["max_output_tokens"] = max_tokens

        if "2.5" in target_model:
            generation_config["thinking_config"] = {"thinking_budget": 24576}
            
        model = GenerativeModel(
            target_model,
            system_instruction=system_instruction,
            tools=vertex_tools,
        )

        response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        if stream:
            def generate_stream():
                try:
                    response = model.generate_content(
                        contents,
                        generation_config=generation_config or None,
                        stream=True,
                    )
                    pending_tool_calls = []

                    for chunk in response:
                        if not chunk.candidates:
                            continue
                        candidate = chunk.candidates[0]
                        if not candidate.content or not candidate.content.parts:
                            continue

                        for part in candidate.content.parts:
                            if hasattr(part, "function_call") and part.function_call and part.function_call.name:
                                fc = part.function_call
                                pending_tool_calls.append({
                                    "id": f"call_{uuid.uuid4().hex[:12]}",
                                    "type": "function",
                                    "function": {
                                        "name": fc.name,
                                        "arguments": _json.dumps(dict(fc.args) if fc.args else {}),
                                    },
                                })

                            elif hasattr(part, "thought") and part.thought:
                                # Wrap Vertex thoughts in tags for Cline
                                text_content = f"<think>\n{part.text}\n</think>\n"
                                sse_data = _json.dumps({
                                    "id": response_id,
                                    "object": "chat.completion.chunk",
                                    "model": model_id,
                                    "choices": [{"index": 0, "delta": {"content": text_content}, "finish_reason": None}],
                                })
                                yield f"data: {sse_data}\n\n"
                                
                            elif hasattr(part, "text") and part.text:
                                sse_data = _json.dumps({
                                    "id": response_id,
                                    "object": "chat.completion.chunk",
                                    "model": model_id,
                                    "choices": [{"index": 0, "delta": {"content": part.text}, "finish_reason": None}],
                                })
                                yield f"data: {sse_data}\n\n"

                    # Emit accumulated tool calls after streaming completes
                    if pending_tool_calls:
                        tc_data = _json.dumps({
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "model": model_id,
                            "choices": [{
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [{**tc, "index": i} for i, tc in enumerate(pending_tool_calls)],
                                },
                                "finish_reason": "tool_calls",
                            }],
                        })
                        yield f"data: {tc_data}\n\n"
                    else:
                        done_chunk = _json.dumps({
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "model": model_id,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        })
                        yield f"data: {done_chunk}\n\n"

                except Exception as e:
                    logger.error(f"Cline streaming error: {e}")
                    err_data = _json.dumps({
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "model": model_id,
                        "choices": [{"index": 0, "delta": {"content": f"\n\n[Error: {e}]"}, "finish_reason": "stop"}],
                    })
                    yield f"data: {err_data}\n\n"

                yield "data: [DONE]\n\n"

            from flask import Response
            return Response(generate_stream(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        else:
            response = model.generate_content(
                contents,
                generation_config=generation_config or None,
            )
            return jsonify(_vertex_candidate_to_openai(response.candidates[0], model_id, response_id))

    except Exception as e:
        logger.error(f"Cline chat completions error: {e}")
        return jsonify({"error": {"message": str(e), "type": "server_error"}}), 500


@app.route('/v1/models', methods=['GET'])
def cline_list_models():
    if not _validate_cline_auth(request.headers):
        return jsonify({"error": {"message": "Invalid API key", "type": "authentication_error"}}), 401

    models = []
    for mid in CLINE_MODEL_MAP:
        models.append({
            "id": mid,
            "object": "model",
            "owned_by": "google",
        })
    return jsonify({"object": "list", "data": models})


# ── Finance Agent API ──────────────────────────────────────────────────────────
# Wraps the FPAS Finance Agent Runtime so any UI can call it via simple HTTP.
# Sessions are auto-managed per user_id with a 1-hour TTL.
# No auth (development). Add auth middleware before production.
#
# Endpoints:
#   POST   /finance/ask          — send message, get full JSON response
#   POST   /finance/ask/stream   — send message, get SSE stream
#   POST   /finance/session/new  — start a fresh conversation session
#   DELETE /finance/session/<id> — remove a session from the registry
#   GET    /finance/health       — check agent connectivity

import threading as _threading
import time as _time

_FINANCE_GCP_PROJECT   = os.environ.get("GCP_PROJECT", "vz-it-np-voev-dev-voevdo-0")
_FINANCE_GCP_REGION    = os.environ.get("GCP_REGION", "us-east4")
_FINANCE_RESOURCE_NAME = os.environ.get(
    "FINANCE_AGENT_RESOURCE_NAME",
    "projects/1041813482705/locations/us-east4/reasoningEngines/200770823231897600",
)
_FINANCE_SESSION_TTL   = int(os.environ.get("SESSION_TTL_SECONDS", "3600"))

_finance_agent = None
_finance_agent_lock = _threading.Lock()

# Maps user_id → {session_id, created_at, last_used}
_finance_sessions: dict = {}
_finance_sessions_lock = _threading.Lock()


def _get_finance_agent():
    global _finance_agent
    if _finance_agent is not None:
        return _finance_agent
    with _finance_agent_lock:
        if _finance_agent is not None:
            return _finance_agent
        if not _FINANCE_RESOURCE_NAME:
            raise RuntimeError(
                "FINANCE_AGENT_RESOURCE_NAME env var is not set. "
                "Deploy the finance agent and set this env var."
            )
        if not _VERTEXAI_AVAILABLE or _agent_engines is None:
            raise RuntimeError("vertexai / agent_engines not available in this environment.")
        vertexai.init(project=_FINANCE_GCP_PROJECT, location=_FINANCE_GCP_REGION)
        _finance_agent = _agent_engines.get(_FINANCE_RESOURCE_NAME)
        logger.info("Finance Agent Runtime client ready: %s", _FINANCE_RESOURCE_NAME)
        return _finance_agent


def _finance_get_or_create_session(user_id: str) -> str:
    with _finance_sessions_lock:
        existing = _finance_sessions.get(user_id)
        if existing and (_time.time() - existing["last_used"]) < _FINANCE_SESSION_TTL:
            existing["last_used"] = _time.time()
            return existing["session_id"]
        agent = _get_finance_agent()
        session = agent.create_session(user_id=user_id)
        session_id = session["id"]
        _finance_sessions[user_id] = {
            "session_id": session_id,
            "created_at": _time.time(),
            "last_used":  _time.time(),
        }
        logger.info("Finance: created session %s for user %s", session_id, user_id)
        return session_id


class _AgentError(Exception):
    """Raised when the remote Agent Runtime returns an error event."""


def _extract_text_from_event(event) -> str:
    """Extract text from an ADK event regardless of how it is serialized.

    ADK events can arrive as:
      - dict with error_code + error_message  → raises _AgentError
      - dict with content.parts[].text  (most common from remote Agent Runtime)
      - object with .content.parts[].text  (in-process / proto object)
      - object with .text shortcut  (ADK 2.x)
      - plain str
    Returns the combined text, or "" if none found.
    """
    # ── error event ───────────────────────────────────────────────────────────
    if isinstance(event, dict) and event.get("error_code"):
        raise _AgentError(
            f"{event.get('error_code')}: {event.get('error_message', 'unknown error')}"
        )
    texts = []

    # ── plain string ─────────────────────────────────────────────────────────
    if isinstance(event, str):
        return event

    # ── ADK 2.x .text shortcut (attribute access) ───────────────────────────
    try:
        t = getattr(event, "text", None)
        if t and isinstance(t, str):
            return t
    except Exception:
        pass

    # ── .content  (object or dict form) ─────────────────────────────────────
    def _parts_from_content(content):
        """Return iterable of Part-like objects from a Content object or dict."""
        if content is None:
            return []
        if isinstance(content, dict):
            return content.get("parts") or []
        return getattr(content, "parts", None) or []

    def _text_from_part(part):
        if isinstance(part, dict):
            return part.get("text") or ""
        return getattr(part, "text", None) or ""

    # Try attribute-style content first
    try:
        content = getattr(event, "content", None)
        if content is not None:
            for part in _parts_from_content(content):
                t = _text_from_part(part)
                if t:
                    texts.append(t)
    except Exception:
        pass

    # Try dict-style content (may overlap with above but harmless)
    if isinstance(event, dict):
        content = event.get("content")
        if isinstance(content, dict):
            for part in _parts_from_content(content):
                t = _text_from_part(part)
                if t and t not in texts:
                    texts.append(t)
        # Also check top-level string fields
        if not texts:
            for key in ("text", "message", "output", "response"):
                val = event.get(key)
                if val and isinstance(val, str):
                    texts.append(val)
                    break

    return "".join(texts)


def _finance_collect_response(user_id: str, session_id: str, message: str) -> str:
    agent = _get_finance_agent()
    chunks = []
    event_count = 0

    try:
        for event in agent.stream_query(user_id=user_id, session_id=session_id, message=message):
            event_count += 1
            # Log first 3 events for diagnosis
            if event_count <= 3:
                logger.info(
                    "finance event[%d] type=%s repr=%.500s",
                    event_count, type(event).__name__, repr(event),
                )
            text = _extract_text_from_event(event)
            if text:
                chunks.append(text)
    except _AgentError as agent_err:
        logger.error("finance agent returned error user=%s: %s", user_id, agent_err)
        return f"The finance agent encountered an error: {agent_err}"
    except Exception as stream_err:
        logger.error("finance stream_query error user=%s: %s", user_id, stream_err, exc_info=True)

    logger.info(
        "finance_collect_response: %d events, %d text chunks for user=%s session=%s",
        event_count, len(chunks), user_id, session_id,
    )

    if chunks:
        return "".join(chunks)

    # ── Fallback: non-streaming query if streaming returned nothing ───────────
    logger.warning(
        "finance_collect_response: stream returned no text, trying non-streaming query "
        "user=%s session=%s", user_id, session_id,
    )
    try:
        result = agent.query(user_id=user_id, session_id=session_id, message=message)
        logger.info("finance query() fallback result type=%s repr=%.500s", type(result).__name__, repr(result))
        # query() may return a dict or an object
        text = _extract_text_from_event(result)
        if text:
            return text
        # Some versions return a list of events
        if isinstance(result, (list, tuple)):
            for ev in result:
                t = _extract_text_from_event(ev)
                if t:
                    chunks.append(t)
            if chunks:
                return "".join(chunks)
    except Exception as query_err:
        logger.error("finance query() fallback also failed user=%s: %s", user_id, query_err, exc_info=True)

    logger.error(
        "finance_collect_response: all methods returned no text for user=%s session=%s",
        user_id, session_id,
    )
    return ""


@app.route('/finance/health', methods=['GET'])
def finance_agent_health():
    try:
        agent = _get_finance_agent()
        return jsonify({
            "status": "healthy",
            "agent_resource": _FINANCE_RESOURCE_NAME,
            "active_sessions": len(_finance_sessions),
        }), 200
    except Exception as e:
        return jsonify({"status": "unavailable", "error": str(e)}), 503


@app.route('/finance/ask', methods=['POST'])
def finance_ask():
    """Send a message to the Finance Agent and receive the full response.

    Request body:
    {
        "message": "What are gross adds by segment in June 2026?",
        "user_id": "john.doe"   // optional, defaults to "default"
    }
    """
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    user_id = (data.get("user_id") or "default").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    try:
        session_id = _finance_get_or_create_session(user_id)
        logger.info("finance/ask user=%s session=%s msg=%.80s", user_id, session_id, message)
        response_text = _finance_collect_response(user_id, session_id, message)
        return jsonify({
            "response":   response_text,
            "user_id":    user_id,
            "session_id": session_id,
        }), 200
    except Exception as e:
        logger.error("finance/ask failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/finance/ask/stream', methods=['POST'])
def finance_ask_stream():
    """Send a message and receive a Server-Sent Events (SSE) streaming response.

    Same request body as /finance/ask.
    Response: text/event-stream — each chunk is: data: {"chunk": "...", "session_id": "..."}
    Final event: data: {"done": true, "session_id": "..."}
    """
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    user_id = (data.get("user_id") or "default").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    try:
        session_id = _finance_get_or_create_session(user_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def _generate():
        import json as _j
        agent = _get_finance_agent()
        try:
            for event in agent.stream_query(user_id=user_id, session_id=session_id, message=message):
                chunk = ""
                # ADK Event object
                try:
                    if hasattr(event, "content") and event.content:
                        parts = getattr(event.content, "parts", None) or []
                        for part in parts:
                            text = getattr(part, "text", None)
                            if text:
                                chunk += text
                except Exception:
                    pass
                # Fallback: dict / string
                if not chunk:
                    if isinstance(event, dict):
                        chunk = event.get("text") or event.get("content") or ""
                    elif isinstance(event, str):
                        chunk = event
                if chunk:
                    yield f"data: {_j.dumps({'chunk': chunk, 'session_id': session_id})}\n\n"
            yield f"data: {_j.dumps({'done': True, 'session_id': session_id})}\n\n"
        except Exception as e:
            logger.error("finance/ask/stream failed: %s", e, exc_info=True)
            yield f"data: {_j.dumps({'error': str(e)})}\n\n"

    from flask import stream_with_context
    return app.response_class(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route('/finance/session/new', methods=['POST'])
def finance_session_new():
    """Force-create a fresh conversation session for a user (clears history).

    Request body:
    {
        "user_id": "john.doe"   // optional, defaults to "default"
    }
    """
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "default").strip()

    try:
        agent = _get_finance_agent()
        session = agent.create_session(user_id=user_id)
        session_id = session["id"]
        with _finance_sessions_lock:
            _finance_sessions[user_id] = {
                "session_id": session_id,
                "created_at": _time.time(),
                "last_used":  _time.time(),
            }
        logger.info("Finance: force-new session %s for user %s", session_id, user_id)
        return jsonify({"session_id": session_id, "user_id": user_id}), 200
    except Exception as e:
        logger.error("finance/session/new failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/finance/session/<session_id>', methods=['DELETE'])
def finance_session_delete(session_id: str):
    """Remove a session from the in-memory registry."""
    with _finance_sessions_lock:
        for uid, info in list(_finance_sessions.items()):
            if info["session_id"] == session_id:
                del _finance_sessions[uid]
                return jsonify({"deleted": True, "session_id": session_id}), 200
    return jsonify({"deleted": False, "message": "Session not found"}), 404


# ── EQES Main Agent API ────────────────────────────────────────────────────────
# Wraps the EQES Main Agent Runtime so any UI can call it via simple HTTP.
# Sessions are auto-managed per user_id with a 1-hour TTL.
# No auth (development). Add auth middleware before production.
#
# Endpoints:
#   POST   /eqes/ask          — send message, get full JSON response
#   POST   /eqes/ask/stream   — send message, get SSE stream
#   POST   /eqes/session/new  — start a fresh conversation session
#   DELETE /eqes/session/<id> — remove a session from the registry
#   GET    /eqes/health       — check agent connectivity

_EQES_GCP_PROJECT   = os.environ.get("GCP_PROJECT", "vz-it-np-voev-dev-voevdo-0")
_EQES_GCP_REGION    = os.environ.get("GCP_REGION", "us-east4")
_EQES_RESOURCE_NAME = os.environ.get(
    "EQES_AGENT_RESOURCE_NAME",
    "projects/1041813482705/locations/us-east4/reasoningEngines/7058204943345254400",
)
_EQES_SESSION_TTL   = int(os.environ.get("SESSION_TTL_SECONDS", "3600"))

_eqes_agent = None
_eqes_agent_lock = _threading.Lock()

# Maps user_id → {session_id, created_at, last_used}
_eqes_sessions: dict = {}
_eqes_sessions_lock = _threading.Lock()


def _get_eqes_agent():
    global _eqes_agent
    if _eqes_agent is not None:
        return _eqes_agent
    with _eqes_agent_lock:
        if _eqes_agent is not None:
            return _eqes_agent
        if not _EQES_RESOURCE_NAME:
            raise RuntimeError(
                "EQES_AGENT_RESOURCE_NAME env var is not set. "
                "Deploy the EQES agent and set this env var."
            )
        if not _VERTEXAI_AVAILABLE or _agent_engines is None:
            raise RuntimeError("vertexai / agent_engines not available in this environment.")
        vertexai.init(project=_EQES_GCP_PROJECT, location=_EQES_GCP_REGION)
        _eqes_agent = _agent_engines.get(_EQES_RESOURCE_NAME)
        logger.info("EQES Agent Runtime client ready: %s", _EQES_RESOURCE_NAME)
        return _eqes_agent


def _eqes_get_or_create_session(user_id: str) -> str:
    with _eqes_sessions_lock:
        existing = _eqes_sessions.get(user_id)
        if existing and (_time.time() - existing["last_used"]) < _EQES_SESSION_TTL:
            existing["last_used"] = _time.time()
            return existing["session_id"]
        agent = _get_eqes_agent()
        session = agent.create_session(user_id=user_id)
        session_id = session["id"]
        _eqes_sessions[user_id] = {
            "session_id": session_id,
            "created_at": _time.time(),
            "last_used":  _time.time(),
        }
        logger.info("EQES: created session %s for user %s", session_id, user_id)
        return session_id


def _eqes_collect_response(user_id: str, session_id: str, message: str) -> str:
    agent = _get_eqes_agent()
    chunks = []
    event_count = 0

    try:
        for event in agent.stream_query(user_id=user_id, session_id=session_id, message=message):
            event_count += 1
            # Log first 3 events for diagnosis
            if event_count <= 3:
                logger.info(
                    "eqes event[%d] type=%s repr=%.500s",
                    event_count, type(event).__name__, repr(event),
                )
            text = _extract_text_from_event(event)
            if text:
                chunks.append(text)
    except _AgentError as agent_err:
        logger.error("eqes agent returned error user=%s: %s", user_id, agent_err)
        return f"The EQES agent encountered an error: {agent_err}"
    except Exception as stream_err:
        logger.error("eqes stream_query error user=%s: %s", user_id, stream_err, exc_info=True)

    logger.info(
        "eqes_collect_response: %d events, %d text chunks for user=%s session=%s",
        event_count, len(chunks), user_id, session_id,
    )

    if chunks:
        return "".join(chunks)

    # ── Fallback: non-streaming query if streaming returned nothing ───────────
    logger.warning(
        "eqes_collect_response: stream returned no text, trying non-streaming query "
        "user=%s session=%s", user_id, session_id,
    )
    try:
        result = agent.query(user_id=user_id, session_id=session_id, message=message)
        logger.info("eqes query() fallback result type=%s repr=%.500s", type(result).__name__, repr(result))
        # query() may return a dict or an object
        text = _extract_text_from_event(result)
        if text:
            return text
        # Some versions return a list of events
        if isinstance(result, (list, tuple)):
            for ev in result:
                t = _extract_text_from_event(ev)
                if t:
                    chunks.append(t)
            if chunks:
                return "".join(chunks)
    except Exception as query_err:
        logger.error("eqes query() fallback also failed user=%s: %s", user_id, query_err, exc_info=True)

    logger.error(
        "eqes_collect_response: all methods returned no text for user=%s session=%s",
        user_id, session_id,
    )
    return ""


@app.route('/eqes/health', methods=['GET'])
def eqes_agent_health():
    try:
        agent = _get_eqes_agent()
        return jsonify({
            "status": "healthy",
            "agent_resource": _EQES_RESOURCE_NAME,
            "active_sessions": len(_eqes_sessions),
        }), 200
    except Exception as e:
        return jsonify({"status": "unavailable", "error": str(e)}), 503


@app.route('/eqes/ask', methods=['POST'])
def eqes_ask():
    """Send a message to the EQES Agent and receive the full response.

    Request body:
    {
        "message": "Process this email support request...",
        "user_id": "john.doe"   // optional, defaults to "default"
    }
    """
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    user_id = (data.get("user_id") or "default").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    try:
        session_id = _eqes_get_or_create_session(user_id)
        logger.info("eqes/ask user=%s session=%s msg=%.80s", user_id, session_id, message)
        response_text = _eqes_collect_response(user_id, session_id, message)
        return jsonify({
            "response":   response_text,
            "user_id":    user_id,
            "session_id": session_id,
        }), 200
    except Exception as e:
        logger.error("eqes/ask failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/eqes/ask/stream', methods=['POST'])
def eqes_ask_stream():
    """Send a message and receive a Server-Sent Events (SSE) streaming response.

    Same request body as /eqes/ask.
    Response: text/event-stream — each chunk is: data: {"chunk": "...", "session_id": "..."}
    Final event: data: {"done": true, "session_id": "..."}
    """
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    user_id = (data.get("user_id") or "default").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    try:
        session_id = _eqes_get_or_create_session(user_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def _generate():
        import json as _j
        agent = _get_eqes_agent()
        try:
            for event in agent.stream_query(user_id=user_id, session_id=session_id, message=message):
                chunk = ""
                # ADK Event object
                try:
                    if hasattr(event, "content") and event.content:
                        parts = getattr(event.content, "parts", None) or []
                        for part in parts:
                            text = getattr(part, "text", None)
                            if text:
                                chunk += text
                except Exception:
                    pass
                # Fallback: dict / string
                if not chunk:
                    if isinstance(event, dict):
                        chunk = event.get("text") or event.get("content") or ""
                    elif isinstance(event, str):
                        chunk = event
                if chunk:
                    yield f"data: {_j.dumps({'chunk': chunk, 'session_id': session_id})}\n\n"
            yield f"data: {_j.dumps({'done': True, 'session_id': session_id})}\n\n"
        except Exception as e:
            logger.error("eqes/ask/stream failed: %s", e, exc_info=True)
            yield f"data: {_j.dumps({'error': str(e)})}\n\n"

    from flask import stream_with_context
    return app.response_class(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route('/eqes/session/new', methods=['POST'])
def eqes_session_new():
    """Force-create a fresh conversation session for a user (clears history).

    Request body:
    {
        "user_id": "john.doe"   // optional, defaults to "default"
    }
    """
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "default").strip()

    try:
        agent = _get_eqes_agent()
        session = agent.create_session(user_id=user_id)
        session_id = session["id"]
        with _eqes_sessions_lock:
            _eqes_sessions[user_id] = {
                "session_id": session_id,
                "created_at": _time.time(),
                "last_used":  _time.time(),
            }
        logger.info("EQES: force-new session %s for user %s", session_id, user_id)
        return jsonify({"session_id": session_id, "user_id": user_id}), 200
    except Exception as e:
        logger.error("eqes/session/new failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/eqes/session/<session_id>', methods=['DELETE'])
def eqes_session_delete(session_id: str):
    """Remove a session from the in-memory registry."""
    with _eqes_sessions_lock:
        for uid, info in list(_eqes_sessions.items()):
            if info["session_id"] == session_id:
                del _eqes_sessions[uid]
                return jsonify({"deleted": True, "session_id": session_id}), 200
    return jsonify({"deleted": False, "message": "Session not found"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
