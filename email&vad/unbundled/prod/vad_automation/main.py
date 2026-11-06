import os
import time
import uuid
import logging
import threading
import requests
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Optional
import schedule
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import google.auth
from google.auth.transport.requests import Request
from google.cloud import bigquery
from google.cloud import storage
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import json
import smtplib
import pandas as pd
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
from google.cloud.dialogflowcx_v3 import SessionsClient, types as dfcx_types
from email_validator import EmailValidator
from email_rate_limiter import get_rate_limiter

AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database configuration
# DB_HOST = "100.68.0.92"
# DB_HOST = "63.22.176.48"
DB_HOST = "100.100.0.239"
DB_NAME = "citrix_incident_classification"
DB_USERNAME = "datalake"
DB_PASSWORD = "datalake"

CONNECTION_STRING = f"postgresql+psycopg2://{DB_USERNAME}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"
print("CONNECTION_STRING", CONNECTION_STRING)

# Citrix API configuration
# CITRIX_API_URL = "https://sacwpezkva013.verizon.com:8443/autohealvm"
CITRIX_API_URL = "https://sacwpezkva024.verizon.com:8443/autohealvm"
# CITRIX_API_KEY = "citrixapitest123"
CITRIX_API_KEY = "Ggejeql2ETpOVUw60bhVnHBzNu92O7i6"

# Email/SMTP configuration
SMTP_SERVER = "vzsmtp.verizon.com"
SMTP_PORT = 25
VDSI_FROM_EMAIL = "vzworkforward@verizon.com"
VDSI_FROM_NAME = "Verizon VDSI"
DO_NOT_REPLY_SMTP_SERVER = "tpaapd1kva109.verizon.com"
DO_NOT_REPLY_FROM_EMAIL = "do-not-reply@verizon.com"
EMAIL_RATE_LIMIT = 1  # Seconds between emails to avoid flooding
MAX_EMAILS_PER_BATCH = 100

class CitrixAutomationService:
    def __init__(self):
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        self.db_connection = None
        self.bigquery_client = bigquery.Client()
        
    def get_db_connection(self):
        """Establish database connection"""
        try:
            AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
            CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
            connection = psycopg2.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USERNAME,
                password=DB_PASSWORD
            )
            logger.info("Successfully connected to database")
            return connection
        except Exception as e:
            logger.error(f"Error connecting to database: {e}")
            raise e
    
    def get_employee_number(self, sys_id: str) -> Optional[str]:
        """
        Get employee number from vea_sn_sys_user_all_cols BigQuery table based on sys_id
        """
        if not sys_id:
            logger.warning("No sys_id provided")
            return None
            
        try:
            AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
            CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
            
            # Get the last 32 digits of sys_id
            last_32_digits = sys_id[-32:] if len(sys_id) >= 32 else sys_id
            
            # BigQuery SQL query
            query = """
            SELECT name, employee_number 
            FROM `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols`
            WHERE sys_id LIKE CONCAT('%', @sys_id_pattern, '%')
            LIMIT 1
            """
            
            # Configure query parameters
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("sys_id_pattern", "STRING", last_32_digits)
                ]
            )
            
            # Execute the query
            query_job = self.bigquery_client.query(query, job_config=job_config)
            results = query_job.result()
            
            # Get the first result
            for row in results:
                logger.info(f"Found employee number {row.employee_number} for sys_id {sys_id}")
                return row.employee_number
            
            logger.warning(f"No employee number found for sys_id: {sys_id}")
            return None
                
        except Exception as e:
            logger.error(f"Error getting employee number for sys_id {sys_id}: {e}")
            return None
    
    def get_employee_numbers_batch(self, sys_ids: List[str]) -> Dict[str, Optional[str]]:
        """
        Get employee numbers for multiple sys_ids in a single BigQuery query
        Returns a dictionary mapping sys_id to employee_number
        """
        if not sys_ids:
            return {}
            
        try:
            AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
            CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
            
            # Prepare sys_id patterns (last 32 digits)
            sys_id_patterns = [sys_id[-32:] if len(sys_id) >= 32 else sys_id for sys_id in sys_ids]
            
            # BigQuery SQL query with IN clause for batch processing
            query = """
            SELECT sys_id, employee_number 
            FROM `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols`
            WHERE sys_id IN UNNEST(@sys_id_patterns)
            """
            
            # Configure query parameters
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("sys_id_patterns", "STRING", sys_id_patterns)
                ]
            )
            
            # Execute the query
            query_job = self.bigquery_client.query(query, job_config=job_config)
            results = query_job.result()
            
            # Build result dictionary
            employee_map = {}
            for row in results:
                # Find the original sys_id that matches this result
                for original_sys_id in sys_ids:
                    if row.sys_id in original_sys_id:
                        employee_map[original_sys_id] = row.employee_number
                        break
            
            # Fill in None for sys_ids not found
            for sys_id in sys_ids:
                if sys_id not in employee_map:
                    employee_map[sys_id] = None
            
            logger.info(f"Batch fetched employee numbers for {len(employee_map)} sys_ids")
            return employee_map
                
        except Exception as e:
            logger.error(f"Error getting employee numbers in batch: {e}")
            # Return None for all sys_ids on error
            return {sys_id: None for sys_id in sys_ids}
    
    def get_emails_by_eid(self, eids: List[str]) -> Dict[str, Optional[str]]:
        """
        Get email addresses from BigQuery for given employee IDs (eids)
        Returns a dictionary mapping eid to email address
        """
        if not eids:
            return {}
        
        try:
            AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
            CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
            
            # BigQuery SQL query with IN clause for batch processing
            query = """
            SELECT employee_number, email 
            FROM `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols`
            WHERE employee_number IN UNNEST(@eids)
            """
            
            # Configure query parameters
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("eids", "STRING", eids)
                ]
            )
            
            # Execute the query
            query_job = self.bigquery_client.query(query, job_config=job_config)
            results = query_job.result()
            
            # Build result dictionary
            email_map = {}
            for row in results:
                email_map[row.employee_number] = row.email
            
            logger.info(f"Batch fetched emails for {len(email_map)} eids")
            return email_map
                
        except Exception as e:
            logger.error(f"Error getting emails by eid: {e}")
            return {}
    
    def get_pending_incidents(self) -> List[Dict]:
        """
        Get incidents from classified_incidents table that need automation
        Filters: classification='Launch_Issue' AND (automation IS NULL OR automation != true)
        """
        connection = None
        try:
            AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
            CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
            connection = self.get_db_connection()
            cursor = connection.cursor(cursor_factory=RealDictCursor)
            
            query = """
            SELECT job_id, number, classification, u_affected_user_display_value, sys_id
            FROM classified_incidents 
            WHERE classification = 'Launch_Issue' 
            AND (automation IS NULL OR automation != true)
            AND automation_status != 'failed'
            """
            
            cursor.execute(query)
            results = cursor.fetchall()
            
            logger.info(f"Found {len(results)} incidents pending automation")
            return [dict(row) for row in results]
            
        except Exception as e:
            logger.error(f"Error getting pending incidents: {e}")
            return []
        finally:
            if connection:
                connection.close()
    
    def call_citrix_api(self, user_id: str, incident_number: str, label: str) -> str:
        """
        Call the Citrix Team API with the provided parameters
        Returns: 'success', 'inprogress', or 'failed'
        """
        try:
            AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
            CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
            headers = {
                'X-API-Key': CITRIX_API_KEY,
                'Content-Type': 'application/json'
            }
            
            params = {
                'userId': user_id,
                'incno': incident_number,
                'label': label
            }
            
            logger.info(f"Calling Citrix API for incident {incident_number} with userId {user_id}")
            
            response = requests.get(
                CITRIX_API_URL,
                headers=headers,
                params=params,
                timeout=30
            )
            
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    api_status = response_data.get('status', '').upper()
                    
                    if api_status == 'SUCCESS':
                        logger.info(f"Citrix API returned SUCCESS for incident {incident_number}")
                        return 'success'
                    elif api_status == 'INPROGRESS':
                        logger.info(f"Citrix API returned InProgress for incident {incident_number}")
                        return 'inprogress'
                    else:
                        logger.warning(f"Citrix API returned unexpected status '{api_status}' for incident {incident_number}")
                        return 'failed'
                except Exception as json_error:
                    logger.error(f"Error parsing Citrix API response for incident {incident_number}: {json_error}")
                    return 'failed'
            else:
                logger.error(f"Citrix API call failed for incident {incident_number}. Status: {response.status_code}, Response: {response.text}")
                return 'failed'
                
        except Exception as e:
            logger.error(f"Error calling Citrix API for incident {incident_number}: {e}")
            return 'failed'
    
    def update_incident_automation_status(self, job_id: int, status: str) -> bool:
        """
        Update the automation column and automation_status based on API response
        status: 'success', 'inprogress', or 'failed'
        """
        connection = None
        try:
            AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
            CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
            connection = self.get_db_connection()
            cursor = connection.cursor()
            
            if status == 'success':
                query = """
                UPDATE classified_incidents 
                SET automation = true, automation_status = 'success'
                WHERE job_id = %s
                """
            elif status == 'inprogress':
                query = """
                UPDATE classified_incidents 
                SET automation = false, automation_status = 'inprogress'
                WHERE job_id = %s
                """
            else:  # failed
                query = """
                UPDATE classified_incidents 
                SET automation = false, automation_status = 'failed'
                WHERE job_id = %s
                """
                
            cursor.execute(query, (job_id,))
            connection.commit()
            
            logger.info(f"Updated automation status for job_id {job_id} to '{status}'")
            return True
            
        except Exception as e:
            logger.error(f"Error updating automation status for job_id {job_id}: {e}")
            return False
        finally:
            if connection:
                connection.close()
    
    def send_email_batch(self, recipients: List[Dict]) -> Dict:
        """
        Send emails to multiple recipients with proper SMTP envelope handling

        Args:
            recipients: List of recipient dicts with keys:
                - to: recipient email
                - subject: email subject
                - message: email body (supports HTML)
                - cc: optional CC list
                - bcc: optional BCC list
                - from: optional sender email (defaults to VDSI_FROM_EMAIL)

        Returns:
            Dict with status, success_count, failed_count, and details
        """
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        if not recipients or len(recipients) == 0:
            logger.warning("No recipients provided for email batch")
            return {
                "status": "error",
                "message": "No recipients provided",
                "success_count": 0,
                "failed_count": 0,
                "details": []
            }

        # Enforce batch size limit
        if len(recipients) > MAX_EMAILS_PER_BATCH:
            logger.warning(f"Recipient count {len(recipients)} exceeds max {MAX_EMAILS_PER_BATCH}")
            recipients = recipients[:MAX_EMAILS_PER_BATCH]

        # Check rate limits before processing batch
        rate_limiter = get_rate_limiter()
        can_send, rate_limit_msg = rate_limiter.can_send_email(len(recipients))
        if not can_send:
            logger.error(f"Rate limit exceeded: {rate_limit_msg}")
            rate_limiter.record_rate_limit_block()
            return {
                "status": "error",
                "message": f"Rate limit exceeded: {rate_limit_msg}",
                "success_count": 0,
                "failed_count": len(recipients),
                "details": []
            }

        # Validate all recipient emails before attempting to send
        to_emails = [r.get("to", "").strip() for r in recipients]
        all_valid, validation_results = EmailValidator.validate_emails_batch(to_emails)

        if not all_valid:
            invalid_count = sum(1 for v in validation_results if not v["valid"])
            logger.error(f"Email validation failed: {invalid_count} invalid email(s)")
            return {
                "status": "error",
                "message": f"Validation failed: {invalid_count} invalid recipient email(s)",
                "success_count": 0,
                "failed_count": len(recipients),
                "validation_errors": validation_results,
                "details": [
                    {"to": v["email"], "status": "failed", "error": v["error"]}
                    for v in validation_results if not v["valid"]
                ]
            }

        results = {
            "status": "completed",
            "total_emails": len(recipients),
            "success_count": 0,
            "failed_count": 0,
            "details": []
        }
        
        smtp_server = None
        try:
            # Establish SMTP connection
            logger.info(f"Connecting to SMTP server {SMTP_SERVER}:{SMTP_PORT}")
            smtp_server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
            smtp_server.set_debuglevel(0)
            
            # Process each recipient
            for idx, recipient in enumerate(recipients):
                try:
                    to_email = recipient.get("to", "").strip()
                    subject = recipient.get("subject", "").strip()
                    message_content = recipient.get("message", "").strip()
                    from_email = recipient.get("from", VDSI_FROM_EMAIL).strip()
                    cc_list = recipient.get("cc")
                    bcc_list = recipient.get("bcc")
                    
                    # Validate required fields
                    if not to_email:
                        logger.error(f"Recipient {idx}: Missing 'to' email")
                        results["details"].append({
                            "to": to_email,
                            "status": "failed",
                            "error": "Missing recipient email address"
                        })
                        results["failed_count"] += 1
                        continue
                    
                    if not subject:
                        logger.error(f"Recipient {idx}: Missing subject")
                        results["details"].append({
                            "to": to_email,
                            "status": "failed",
                            "error": "Missing email subject"
                        })
                        results["failed_count"] += 1
                        continue
                    
                    if not message_content:
                        logger.error(f"Recipient {idx}: Missing message")
                        results["details"].append({
                            "to": to_email,
                            "status": "failed",
                            "error": "Missing email message"
                        })
                        results["failed_count"] += 1
                        continue
                    
                    # Build email message
                    message = MIMEMultipart('alternative')
                    message['Subject'] = subject
                    message['From'] = from_email  # Proper envelope sender
                    message['To'] = to_email
                    
                    # Add CC if provided
                    cc_recipients = []
                    if cc_list:
                        if isinstance(cc_list, list):
                            cc_recipients = cc_list
                            message['Cc'] = ", ".join(cc_recipients)
                        elif isinstance(cc_list, str) and cc_list.strip():
                            cc_recipients = [cc_list.strip()]
                            message['Cc'] = cc_list.strip()
                    
                    # Add BCC if provided (not included in headers for privacy)
                    bcc_recipients = []
                    if bcc_list:
                        if isinstance(bcc_list, list):
                            bcc_recipients = bcc_list
                        elif isinstance(bcc_list, str) and bcc_list.strip():
                            bcc_recipients = [bcc_list.strip()]
                    
                    # Determine if HTML or plain text
                    is_html = '<' in message_content and '>' in message_content
                    msg_type = 'html' if is_html else 'plain'
                    msg_part = MIMEText(message_content, msg_type)
                    message.attach(msg_part)
                    
                    # Build all recipients for SMTP envelope
                    all_smtp_recipients = [to_email] + cc_recipients + bcc_recipients
                    
                    # Send the message via SMTP
                    try:
                        smtp_server.send_message(message, from_addr=from_email)
                        logger.info(f"Email sent to {to_email} - Subject: {subject[:50]}")
                        rate_limiter.record_email_sent(1)
                        results["details"].append({
                            "to": to_email,
                            "status": "success",
                            "message": "Email sent successfully"
                        })
                        results["success_count"] += 1
                    except smtplib.SMTPRecipientsRefused as e:
                        logger.error(f"SMTP recipient refused for {to_email}: {e}")
                        results["details"].append({
                            "to": to_email,
                            "status": "failed",
                            "error": f"Recipient rejected: {str(e)}"
                        })
                        results["failed_count"] += 1
                    except smtplib.SMTPSenderRefused as e:
                        logger.error(f"SMTP sender refused for {from_email}: {e}")
                        results["details"].append({
                            "to": to_email,
                            "status": "failed",
                            "error": f"Sender rejected: {str(e)}"
                        })
                        results["failed_count"] += 1
                    
                    # Rate limiting - delay between emails to avoid flooding
                    if idx < len(recipients) - 1:
                        time.sleep(EMAIL_RATE_LIMIT)
                
                except Exception as e:
                    logger.error(f"Error sending email to {recipient.get('to', 'unknown')}: {str(e)}")
                    results["details"].append({
                        "to": recipient.get("to", "unknown"),
                        "status": "failed",
                        "error": str(e)
                    })
                    results["failed_count"] += 1
            
        except smtplib.SMTPException as e:
            logger.error(f"SMTP connection error: {e}")
            results["status"] = "error"
            results["message"] = f"SMTP connection failed: {str(e)}"
            results["failed_count"] = len(recipients)
            results["success_count"] = 0
            results["details"] = [{
                "status": "failed",
                "error": f"SMTP connection error: {str(e)}"
            }]
        except Exception as e:
            logger.error(f"Unexpected error in send_email_batch: {e}")
            results["status"] = "error"
            results["message"] = str(e)
            results["failed_count"] = len(recipients)
            results["success_count"] = 0
        finally:
            # Ensure SMTP connection is closed
            if smtp_server:
                try:
                    smtp_server.quit()
                except Exception as e:
                    logger.warning(f"Error closing SMTP connection: {e}")
        
        return results
    
    def process_incidents(self):
        """
        Main processing function that orchestrates the workflow
        """
        logger.info("Starting incident processing cycle")
        
        try:
            AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
            CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
            # Get pending incidents
            incidents = self.get_pending_incidents()
            
            if not incidents:
                logger.info("No incidents found for processing")
                return
            
            processed_count = 0
            success_count = 0
            
            for incident in incidents:
                try:
                    job_id = incident['job_id']
                    incident_number = incident['number']
                    classification = incident['classification']
                    sys_id = incident['sys_id']
                    
                    logger.info(f"Processing incident {incident_number} (job_id: {job_id})")
                    
                    # Get employee number for the affected user using sys_id
                    employee_number = self.get_employee_number(sys_id)
                    
                    if not employee_number:
                        logger.warning(f"Skipping incident {incident_number} - no employee number found")
                        self.update_incident_automation_status(job_id, 'failed')
                        processed_count += 1
                        continue
                    
                    # Call Citrix API
                    api_status = self.call_citrix_api(
                        user_id=employee_number,
                        incident_number=incident_number,
                        label=classification
                    )
                    
                    # Update automation status
                    self.update_incident_automation_status(job_id, api_status)
                    
                    processed_count += 1
                    if api_status in ['success', 'inprogress']:
                        success_count += 1
                    
                    # Add a small delay between API calls to avoid rate limiting
                    time.sleep(1)
                    
                except Exception as e:
                    logger.error(f"Error processing incident {incident.get('number', 'unknown')}: {e}")
                    processed_count += 1
            
            logger.info(f"Processing cycle complete. Processed: {processed_count}, Successful: {success_count}")
            
        except Exception as e:
            logger.error(f"Error in process_incidents: {e}")

# Initialize the service
AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
citrix_service = CitrixAutomationService()
AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])

def run_automation_cycle():
    """Run the automation cycle"""
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        citrix_service.process_incidents()
    except Exception as e:
        logger.error(f"Error in automation cycle: {e}")

# Flask app for Cloud Run health checks
AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
app = Flask(__name__)
CORS(app)
AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])

@app.route('/')
def health_check():
    AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
    CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
    return {'status': 'healthy', 'timestamp': datetime.now().isoformat()}

@app.route('/email-metrics', methods=['GET'])
def email_metrics():
    """Get current email rate limiting metrics."""
    try:
        rate_limiter = get_rate_limiter()
        metrics = rate_limiter.get_metrics()
        return jsonify({
            "status": "success",
            "metrics": metrics,
            "timestamp": datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Error fetching email metrics: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/run')
def manual_run():
    """Manual trigger endpoint"""
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        run_automation_cycle()
        return {'status': 'completed', 'timestamp': datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"Manual run failed: {e}")
        return {'status': 'error', 'error': str(e)}, 500

@app.route('/send-email-vdsi', methods=['POST'])
def send_email_vdsi():
    """
    API endpoint to send VDSI emails (production-grade implementation)
    Uses vzsmtp.verizon.com SMTP server with proper envelope sender handling
    
    Expected JSON payload:
    {
        "to": "email@verizon.com" or ["email1@verizon.com", "email2@verizon.com"],
        "subject": "Email Subject",
        "message": "Email content (supports HTML)",
        "from": "sender@verizon.com" (optional, defaults to vzworkforward@verizon.com),
        "cc": "cc@verizon.com" or ["cc1@verizon.com", "cc2@verizon.com"] (optional),
        "bcc": "bcc@verizon.com" or ["bcc1@verizon.com", "bcc2@verizon.com"] (optional)
    }
    
    Returns:
        JSON response with status of each email sent
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        
        data = request.get_json()
        
        # Validate required fields
        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON data provided"
            }), 400
        
        to_emails = data.get("to")
        subject = data.get("subject")
        message_content = data.get("message")
        from_email = data.get("from", VDSI_FROM_EMAIL)
        cc_emails = data.get("cc")
        bcc_emails = data.get("bcc")

        # Validate required fields
        if not to_emails:
            return jsonify({
                "status": "error",
                "message": "Missing required field: to"
            }), 400

        if not subject:
            return jsonify({
                "status": "error",
                "message": "Missing required field: subject"
            }), 400

        if not message_content:
            return jsonify({
                "status": "error",
                "message": "Missing required field: message"
            }), 400

        # Validate FROM email
        is_valid_from, from_error = EmailValidator.validate_from_email(from_email)
        if not is_valid_from:
            return jsonify({
                "status": "error",
                "message": f"Invalid FROM email: {from_error}"
            }), 400
        
        # Convert single email to list
        if isinstance(to_emails, str):
            to_emails = [to_emails]
        
        # Validate email list
        if not isinstance(to_emails, list) or len(to_emails) == 0:
            return jsonify({
                "status": "error",
                "message": "Invalid 'to' field - must be email string or array"
            }), 400
        
        # Build recipients list for batch sending
        recipients = []
        for email in to_emails:
            recipients.append({
                "to": email,
                "subject": subject,
                "message": message_content,
                "from": from_email,
                "cc": cc_emails,
                "bcc": bcc_emails
            })
        
        logger.info(f"Processing email batch with {len(recipients)} recipients")
        
        # Send emails using the service's batch email method
        result = citrix_service.send_email_batch(recipients)
        
        status_code = 200 if result["failed_count"] == 0 else 207
        return jsonify(result), status_code
        
    except Exception as e:
        logger.error(f"Error in send_email_vdsi: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/send-email-dont-reply', methods=['POST'])
def send_email_dont_reply():
    """
    API endpoint to send do-not-reply emails (automated/system notifications)
    Uses tpaapd1kva109.verizon.com SMTP server with proper envelope sender handling
    
    Expected JSON payload:
    Option 1 - Using direct email addresses:
    {
        "to": "email@verizon.com" or ["email1@verizon.com", "email2@verizon.com"],
        "subject": "Email Subject",
        "message": "Email content (supports HTML)",
        "from": "do-not-reply@verizon.com" (optional),
        "cc": "cc@verizon.com" or ["cc1@verizon.com", "cc2@verizon.com"] (optional),
        "bcc": "bcc@verizon.com" or ["bcc1@verizon.com", "bcc2@verizon.com"] (optional)
    }
    
    Option 2 - Using EID (employee IDs) from BigQuery:
    {
        "eid": "0123456789" or ["0123456789", "0134326789"],
        "subject": "Email Subject",
        "message": "Email content (supports HTML)",
        "from": "do-not-reply@verizon.com" (optional),
        "cc": "cc@verizon.com" or ["cc1@verizon.com", "cc2@verizon.com"] (optional),
        "bcc": "bcc@verizon.com" or ["bcc1@verizon.com", "bcc2@verizon.com"] (optional)
    }
    
    Returns:
        JSON response with status of each email sent
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        
        data = request.get_json()
        
        # Validate required fields
        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON data provided"
            }), 400
        
        # Get either 'to' or 'eid' parameter
        to_emails = data.get("to")
        eids = data.get("eid")
        subject = data.get("subject")
        message_content = data.get("message")
        from_email = data.get("from", DO_NOT_REPLY_FROM_EMAIL)
        cc_emails = data.get("cc")
        bcc_emails = data.get("bcc")

        # Validate FROM email
        is_valid_from, from_error = EmailValidator.validate_from_email(from_email)
        if not is_valid_from:
            return jsonify({
                "status": "error",
                "message": f"Invalid FROM email: {from_error}"
            }), 400

        # Validate that either 'to' or 'eid' is provided
        if not to_emails and not eids:
            return jsonify({
                "status": "error",
                "message": "Missing required field: either 'to' or 'eid' must be provided"
            }), 400
        
        # If eid is provided, fetch emails from BigQuery
        if eids and not to_emails:
            # Convert single eid to list
            if isinstance(eids, str):
                eids = [eids]
            
            # Validate eid list
            if not isinstance(eids, list) or len(eids) == 0:
                return jsonify({
                    "status": "error",
                    "message": "Invalid 'eid' field - must be string or array"
                }), 400
            
            logger.info(f"Fetching emails for {len(eids)} eids from BigQuery")
            
            # Fetch emails from BigQuery
            email_map = citrix_service.get_emails_by_eid(eids)
            
            if not email_map:
                return jsonify({
                    "status": "error",
                    "message": "No emails found for provided eids"
                }), 404
            
            # Extract emails from the map
            to_emails = list(email_map.values())
            
            logger.info(f"Successfully fetched {len(to_emails)} emails from BigQuery for eids")
        else:
            # Validate 'to' field if provided directly
            if not to_emails:
                return jsonify({
                    "status": "error",
                    "message": "Missing required field: to"
                }), 400
            
            # Convert single email to list
            if isinstance(to_emails, str):
                to_emails = [to_emails]
            
            # Validate email list
            if not isinstance(to_emails, list) or len(to_emails) == 0:
                return jsonify({
                    "status": "error",
                    "message": "Invalid 'to' field - must be email string or array"
                }), 400
        
        if not subject:
            return jsonify({
                "status": "error",
                "message": "Missing required field: subject"
            }), 400
        
        if not message_content:
            return jsonify({
                "status": "error",
                "message": "Missing required field: message"
            }), 400
        
        # Build recipients list for batch sending
        recipients = []
        for email in to_emails:
            recipients.append({
                "to": email,
                "subject": subject,
                "message": message_content,
                "from": from_email,
                "cc": cc_emails,
                "bcc": bcc_emails
            })
        
        logger.info(f"Processing do-not-reply email batch with {len(recipients)} recipients")
        
        # Send emails directly using SMTP
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        results = []
        success_count = 0
        failed_count = 0
        
        try:
            # Setup SMTP connection
            server = smtplib.SMTP(DO_NOT_REPLY_SMTP_SERVER, timeout=30)
            server.set_debuglevel(0)
            
            # Send emails to all recipients
            for idx, email in enumerate(to_emails):
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
                    
                    # Add BCC if provided
                    if bcc_emails:
                        if isinstance(bcc_emails, list):
                            message['Bcc'] = ", ".join(bcc_emails)
                        else:
                            message['Bcc'] = bcc_emails
                    
                    # Determine if message is HTML or plain text
                    is_html = '<' in message_content and '>' in message_content
                    msg_type = 'html' if is_html else 'plain'
                    msg_part = MIMEText(message_content, msg_type)
                    message.attach(msg_part)
                    
                    # Send email with proper envelope sender
                    server.send_message(message, from_addr=from_email)
                    
                    logger.info(f"Do-Not-Reply email sent to {email}")
                    results.append({
                        "to": email,
                        "status": "success",
                        "message": "Email sent successfully"
                    })
                    success_count += 1
                    
                    # Rate limiting - delay between emails
                    if idx < len(to_emails) - 1:
                        time.sleep(EMAIL_RATE_LIMIT)
                    
                except smtplib.SMTPRecipientsRefused as e:
                    logger.error(f"SMTP recipient refused for {email}: {e}")
                    results.append({
                        "to": email,
                        "status": "failed",
                        "error": f"Recipient rejected: {str(e)}"
                    })
                    failed_count += 1
                except smtplib.SMTPSenderRefused as e:
                    logger.error(f"SMTP sender refused for {from_email}: {e}")
                    results.append({
                        "to": email,
                        "status": "failed",
                        "error": f"Sender rejected: {str(e)}"
                    })
                    failed_count += 1
                except Exception as e:
                    logger.error(f"Error sending do-not-reply email to {email}: {str(e)}")
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
            
            status_code = 200 if failed_count == 0 else 207
            
            return jsonify(response), status_code
            
        except smtplib.SMTPException as e:
            logger.error(f"SMTP connection error: {str(e)}")
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

@app.route('/launch-issues', methods=['GET', 'POST'])
def get_launch_issues():
    """Get all Launch_Issue incidents with employee numbers - Optimized with incident_state filtering"""
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        
        # Get incident_state from query params or request body
        incident_states = None
        if request.method == 'POST' and request.is_json:
            incident_states = request.json.get('incident_state')
        elif request.method == 'GET':
            # Try to get from query params
            incident_state_param = request.args.get('incident_state')
            if incident_state_param:
                # Handle comma-separated values or JSON array
                try:
                    import json
                    incident_states = json.loads(incident_state_param)
                except:
                    incident_states = [s.strip() for s in incident_state_param.split(',')]
        
        connection = citrix_service.get_db_connection()
        cursor = connection.cursor(cursor_factory=RealDictCursor)
        
        # Build dynamic query based on incident_state filter
        query = """
        SELECT job_id, number, classification, u_affected_user_display_value, sys_id, 
               automation, automation_status, incident_state, timestamp
        FROM classified_incidents 
        WHERE classification = 'Launch_Issue'
        """
        
        # Add incident_state filter if provided
        if incident_states and isinstance(incident_states, list) and len(incident_states) > 0:
            # Sanitize the states and build the IN clause
            placeholders = ', '.join(['%s'] * len(incident_states))
            query += f" AND incident_state IN ({placeholders})"
            cursor.execute(query, tuple(incident_states))
        else:
            cursor.execute(query)
        
        results = cursor.fetchall()
        connection.close()
        
        # Convert to list of dicts
        incidents = [dict(row) for row in results]
        
        # Extract all sys_ids
        sys_ids = [incident['sys_id'] for incident in incidents]
        
        # Batch fetch all employee numbers in one query
        employee_map = citrix_service.get_employee_numbers_batch(sys_ids)
        
        # Add employee numbers to incidents and format timestamps
        for incident in incidents:
            incident['employee_number'] = employee_map.get(incident['sys_id'])
            # Format timestamp as ISO string if it exists
            if incident.get('timestamp'):
                if isinstance(incident['timestamp'], str):
                    incident['timestamp'] = incident['timestamp']
                else:
                    incident['timestamp'] = incident['timestamp'].isoformat() if hasattr(incident['timestamp'], 'isoformat') else str(incident['timestamp'])
        
        return jsonify({
            'status': 'success',
            'count': len(incidents),
            'data': incidents,
            'filter_applied': {'incident_state': incident_states} if incident_states else None,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error fetching launch issues: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/update-incident-state', methods=['POST'])
def update_incident_state():
    """Update incident_state and/or automation_status for one or more incident numbers
    
    Supports both single and multiple incident updates:
    Single incident:
    {
        "number": "122",
        "incident_state": "closed",
        "automation_status": "success" (optional)
    }
    
    Multiple incidents:
    {
        "number": ["122", "343", "456"],
        "incident_state": "closed",
        "automation_status": "success" (optional)
    }
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        
        # Validate request has JSON data
        if not request.is_json:
            return {'status': 'error', 'error': 'Request must be JSON'}, 400
        
        # Get parameters from request body
        data = request.json
        incident_numbers = data.get('number')
        new_state = data.get('incident_state')
        new_automation_status = data.get('automation_status')
        
        # Validate required fields
        if not incident_numbers:
            return {'status': 'error', 'error': 'Missing required field: number'}, 400
        
        # Convert single incident number to list
        if isinstance(incident_numbers, str):
            incident_numbers = [incident_numbers]
        elif not isinstance(incident_numbers, list):
            return {'status': 'error', 'error': 'Invalid number field - must be string or array'}, 400
        
        # Validate incident_numbers list is not empty
        if len(incident_numbers) == 0:
            return {'status': 'error', 'error': 'number array cannot be empty'}, 400
        
        # At least one of incident_state or automation_status must be provided
        if not new_state and not new_automation_status:
            return {'status': 'error', 'error': 'At least one of incident_state or automation_status must be provided'}, 400
        
        # Connect to database
        connection = citrix_service.get_db_connection()
        cursor = connection.cursor()
        
        # Build dynamic update query based on provided fields
        update_fields = []
        update_values = []
        
        if new_state:
            update_fields.append('incident_state = %s')
            update_values.append(new_state)
        
        if new_automation_status:
            update_fields.append('automation_status = %s')
            update_values.append(new_automation_status)
        
        # Build the IN clause for multiple incident numbers
        placeholders = ', '.join(['%s'] * len(incident_numbers))
        update_values.extend(incident_numbers)  # Add all incident numbers for WHERE clause
        
        update_query = f"""
        UPDATE classified_incidents 
        SET {', '.join(update_fields)}
        WHERE number IN ({placeholders})
        """
        
        cursor.execute(update_query, tuple(update_values))
        connection.commit()
        
        rows_affected = cursor.rowcount
        connection.close()
        
        # Build response
        update_info = []
        if new_state:
            update_info.append(f"incident_state to '{new_state}'")
        if new_automation_status:
            update_info.append(f"automation_status to '{new_automation_status}'")
        
        logger.info(f"Updated {rows_affected} incidents: {', '.join(update_info)}")
        
        response_data = {
            'status': 'success',
            'message': f'Successfully updated {rows_affected} incident(s)',
            'incident_numbers': incident_numbers,
            'rows_affected': rows_affected,
            'timestamp': datetime.now().isoformat()
        }
        
        if new_state:
            response_data['new_state'] = new_state
        if new_automation_status:
            response_data['new_automation_status'] = new_automation_status
        
        return response_data
        
    except Exception as e:
        logger.error(f"Error updating incident: {e}")
        return {'status': 'error', 'error': str(e)}, 500

@app.route('/dialogflow_query', methods=['POST'])
def dialogflow_query():
    """
    Query the Dialogflow CX Agent (YubiKeyAssistantUpdate-Prod) for YubiKey support
    
    Request body:
    {
        "session_id": "user-id-or-session-identifier",
        "text": "User query text",
        "language_code": "en"  # Optional, defaults to "en"
    }
    
    Returns: Full Dialogflow API response with responseMessages
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        
        # Extract parameters from request body
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Missing request body'}), 400
        
        session_id = data.get('session_id')
        text = data.get('text')
        language_code = data.get('language_code', 'en')
        
        # Validate required parameters
        if not session_id:
            return jsonify({'error': 'Missing required parameter: session_id'}), 400
        if not text:
            return jsonify({'error': 'Missing required parameter: text'}), 400
        
        logger.info(f"Dialogflow query - session_id: {session_id}, text: {text[:50]}...")
        
        # Refresh credentials to get valid access token
        CREDENTIALS.refresh(Request())
        
        # Construct Dialogflow CX API endpoint (newyubikeysupport-prod agent)
        dialogflow_api_url = (
            f"https://us-west1-dialogflow.googleapis.com/v3/projects/"
            f"vz-it-pr-voev-voevdo-0/locations/us-west1/agents/"
            f"cf8c6f6d-65ee-4882-b345-8c9a42fbb5ce/sessions/{session_id}:detectIntent"
        )
        
        # Prepare request payload for Dialogflow API
        payload = {
            "queryInput": {
                "text": {
                    "text": text
                },
                "languageCode": language_code
            }
        }
        
        # Prepare headers with authentication
        headers = {
            "Authorization": f"Bearer {CREDENTIALS.token}",
            "Content-Type": "application/json"
        }
        
        logger.info(f"Calling Dialogflow API endpoint: {dialogflow_api_url}")
        
        # Make API request to Dialogflow
        response = requests.post(
            dialogflow_api_url,
            json=payload,
            headers=headers,
            timeout=30
        )
        
        # Handle response
        if response.status_code == 200:
            dialogflow_response = response.json()
            logger.info(f"Dialogflow API returned 200 - session: {session_id}")
            return jsonify(dialogflow_response), 200
        else:
            logger.error(
                f"Dialogflow API error - Status: {response.status_code}, "
                f"Response: {response.text}"
            )
            return jsonify({
                'error': 'Dialogflow API request failed',
                'status_code': response.status_code,
                'details': response.text
            }), response.status_code
        
    except Exception as e:
        logger.error(f"Error in dialogflow_query: {str(e)}")
        return jsonify({
            'error': 'Internal server error',
            'details': str(e)
        }), 500

# def run_scheduler():
#     """Run the scheduler for periodic execution"""
#     # Schedule the task to run every 15 minutes
#     AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
#     CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
#     schedule.every(15).minutes.do(run_automation_cycle)
    
#     logger.info("Scheduler started - running every 15 minutes")
    
#     while True:
#         schedule.run_pending()
#         time.sleep(60)  # Check every minute

@app.route('/user-details', methods=['POST'])
def user_details():
    AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
    CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
    data = request.get_json()
    eid = data.get('eid') if data else None
    if not eid:
        return jsonify({'error': 'Missing eid parameter'}), 400
    
    try:
        PROJECT_ID = "vz-it-pr-voev-voevdo-0"
        client = bigquery.Client(project=PROJECT_ID)
        
        # Check if eid is an array
        if isinstance(eid, list):
            # Handle array of EIDs
            if not eid:
                return jsonify({'error': 'Empty eid array'}), 400
            
            # Create a comma-separated string of quoted EIDs for the IN clause
            eid_list = "', '".join(str(e) for e in eid)
            
            # Query for user details
            user_query = f"""
            SELECT
                user_info.employee_number,
                user_info.u_manager_eid,
                user_info.manager_display_value,
                user_info.city AS location,
                user_info.location_display_value AS odc,
                user_info.u_persona_person_type AS employment,
                hr_data.band,
                manager_info.email AS manager_email,
                hr_data.jobtitle,
                hr_data.first_name,
                hr_data.last_name,
                hr_data.work_email_addr,
                hr_data.level_4_name,
                hr_data.level_5_name,
                google_users.org
            FROM
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS user_info
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_hr_latest_data` AS hr_data
            ON
                hr_data.b_eid = user_info.employee_number
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS manager_info
            ON
                manager_info.employee_number = user_info.u_manager_eid
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.Google_Users_v2` AS google_users
            ON
                google_users.eid = user_info.employee_number
            WHERE
                user_info.employee_number IN ('{eid_list}');
            """
            
            # Query for computer details
            computer_query = f"""
            SELECT
                user_info.employee_number,
                computer.u_discovered_model,
                computer.serial_number,
                computer.os_domain,
                computer.name AS hostname,
                computer.assigned_to_display_value
            FROM
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS user_info
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_cmdb_ci_computer` AS computer
            ON
                user_info.sys_id = SUBSTR(computer.assigned_to_link, -32)
                AND computer.install_status = 'installed'
            WHERE
                user_info.employee_number IN ('{eid_list}');
            """
            
            user_df = client.query(user_query).to_dataframe()
            computer_df = client.query(computer_query).to_dataframe()
            
            if user_df.empty:
                return jsonify([])
            
            # Combine user and computer data
            results = []
            for _, user_row in user_df.iterrows():
                user_eid = user_row['employee_number']
                user_data = user_row.to_dict()
                
                # Get all computers for this user
                user_computers = computer_df[computer_df['employee_number'] == user_eid]
                computers_list = []
                for _, comp_row in user_computers.iterrows():
                    if pd.notna(comp_row['u_discovered_model']):  # Only add if computer exists
                        computers_list.append({
                            'u_discovered_model': comp_row['u_discovered_model'],
                            'serial_number': comp_row['serial_number'],
                            'os_domain': comp_row['os_domain'],
                            'hostname': comp_row['hostname'],
                            'assigned_to_display_value': comp_row['assigned_to_display_value']
                        })
                
                user_data['computers'] = computers_list
                results.append(user_data)
            
            return jsonify(results)
        else:
            # Handle single EID
            # Query for user details
            user_query = f"""
            SELECT
                user_info.employee_number,
                user_info.u_manager_eid,
                user_info.manager_display_value,
                user_info.city AS location,
                user_info.location_display_value AS odc,
                user_info.u_persona_person_type AS employment,
                hr_data.band,
                manager_info.email AS manager_email,
                hr_data.jobtitle,
                hr_data.first_name,
                hr_data.last_name,
                hr_data.work_email_addr,
                hr_data.level_4_name,
                hr_data.level_5_name,
                google_users.org
            FROM
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS user_info
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_hr_latest_data` AS hr_data
            ON
                hr_data.b_eid = user_info.employee_number
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS manager_info
            ON
                manager_info.employee_number = user_info.u_manager_eid
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.Google_Users_v2` AS google_users
            ON
                google_users.eid = user_info.employee_number
            WHERE
                user_info.employee_number = '{eid}';
            """
            
            # Query for computer details
            computer_query = f"""
            SELECT
                computer.u_discovered_model,
                computer.serial_number,
                computer.os_domain,
                computer.name AS hostname,
                computer.assigned_to_display_value
            FROM
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS user_info
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_cmdb_ci_computer` AS computer
            ON
                user_info.sys_id = SUBSTR(computer.assigned_to_link, -32)
                AND computer.install_status = 'installed'
            WHERE
                user_info.employee_number = '{eid}';
            """
            
            user_df = client.query(user_query).to_dataframe()
            computer_df = client.query(computer_query).to_dataframe()
            
            if user_df.empty:
                return jsonify({'error': 'User not found'}), 404
            
            # Get user data
            user_data = user_df.iloc[0].to_dict()
            
            # Get all computers for this user
            computers_list = []
            for _, comp_row in computer_df.iterrows():
                if pd.notna(comp_row['u_discovered_model']):  # Only add if computer exists
                    computers_list.append({
                        'u_discovered_model': comp_row['u_discovered_model'],
                        'serial_number': comp_row['serial_number'],
                        'os_domain': comp_row['os_domain'],
                        'hostname': comp_row['hostname'],
                        'assigned_to_display_value': comp_row['assigned_to_display_value']
                    })
            
            user_data['computers'] = computers_list
            
            return jsonify(user_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/user-details-recent', methods=['POST'])
def user_details_recent():
    AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
    CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
    data = request.get_json()
    eid = data.get('eid') if data else None
    if not eid:
        return jsonify({'error': 'Missing eid parameter'}), 400
    
    try:
        PROJECT_ID = "vz-it-pr-voev-voevdo-0"
        client = bigquery.Client(project=PROJECT_ID)
        
        # Check if eid is an array
        if isinstance(eid, list):
            # Handle array of EIDs
            if not eid:
                return jsonify({'error': 'Empty eid array'}), 400
            
            # Create a comma-separated string of quoted EIDs for the IN clause
            eid_list = "', '".join(str(e) for e in eid)
            
            # Query for user details
            user_query = f"""
            SELECT
                user_info.employee_number,
                user_info.u_manager_eid,
                user_info.manager_display_value,
                user_info.city AS location,
                user_info.location_display_value AS odc,
                user_info.u_persona_person_type AS employment,
                hr_data.band,
                manager_info.email AS manager_email,
                hr_data.jobtitle,
                hr_data.first_name,
                hr_data.last_name,
                hr_data.work_email_addr,
                hr_data.level_4_name,
                hr_data.level_5_name,
                google_users.org
            FROM
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS user_info
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_hr_latest_data` AS hr_data
            ON
                hr_data.b_eid = user_info.employee_number
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS manager_info
            ON
                manager_info.employee_number = user_info.u_manager_eid
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.Google_Users_v2` AS google_users
            ON
                google_users.eid = user_info.employee_number
            WHERE
                user_info.employee_number IN ('{eid_list}');
            """
            
            # Query for computer details with commission date filters
            computer_query = f"""
            SELECT
                user_info.employee_number,
                computer.u_discovered_model,
                computer.serial_number,
                computer.os_domain,
                computer.name AS hostname,
                computer.assigned_to_display_value,
                computer.u_commission_date
            FROM
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS user_info
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_cmdb_ci_computer` AS computer
            ON
                user_info.sys_id = SUBSTR(computer.assigned_to_link, -32)
                AND computer.install_status = 'installed'
                AND computer.u_commission_date IS NOT NULL
                AND computer.u_commission_date != ''
                AND LOWER(computer.model_id_display_value) NOT LIKE '%virtual machine%'
            WHERE
                user_info.employee_number IN ('{eid_list}')
            ORDER BY
                computer.u_commission_date DESC;
            """
            
            user_df = client.query(user_query).to_dataframe()
            computer_df = client.query(computer_query).to_dataframe()
            
            if user_df.empty:
                return jsonify([])
            
            # Combine user and computer data
            results = []
            for _, user_row in user_df.iterrows():
                user_eid = user_row['employee_number']
                user_data = user_row.to_dict()
                
                # Get all computers for this user
                user_computers = computer_df[computer_df['employee_number'] == user_eid]
                computers_list = []
                for _, comp_row in user_computers.iterrows():
                    if pd.notna(comp_row['u_discovered_model']):  # Only add if computer exists
                        computers_list.append({
                            'u_discovered_model': comp_row['u_discovered_model'],
                            'serial_number': comp_row['serial_number'],
                            'os_domain': comp_row['os_domain'],
                            'hostname': comp_row['hostname'],
                            'assigned_to_display_value': comp_row['assigned_to_display_value'],
                            'u_commission_date': comp_row['u_commission_date']
                        })
                
                user_data['computers'] = computers_list
                results.append(user_data)
            
            return jsonify(results)
        else:
            # Handle single EID
            # Query for user details
            user_query = f"""
            SELECT
                user_info.employee_number,
                user_info.u_manager_eid,
                user_info.manager_display_value,
                user_info.city AS location,
                user_info.location_display_value AS odc,
                user_info.u_persona_person_type AS employment,
                hr_data.band,
                manager_info.email AS manager_email,
                hr_data.jobtitle,
                hr_data.first_name,
                hr_data.last_name,
                hr_data.work_email_addr,
                hr_data.level_4_name,
                hr_data.level_5_name,
                google_users.org
            FROM
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS user_info
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_hr_latest_data` AS hr_data
            ON
                hr_data.b_eid = user_info.employee_number
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS manager_info
            ON
                manager_info.employee_number = user_info.u_manager_eid
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.Google_Users_v2` AS google_users
            ON
                google_users.eid = user_info.employee_number
            WHERE
                user_info.employee_number = '{eid}';
            """
            
            # Query for computer details with commission date filters
            computer_query = f"""
            SELECT
                computer.u_discovered_model,
                computer.serial_number,
                computer.os_domain,
                computer.name AS hostname,
                computer.assigned_to_display_value,
                computer.u_commission_date
            FROM
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_sys_user_all_cols` AS user_info
            LEFT JOIN
                `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_sn_cmdb_ci_computer` AS computer
            ON
                user_info.sys_id = SUBSTR(computer.assigned_to_link, -32)
                AND computer.install_status = 'installed'
                AND computer.u_commission_date IS NOT NULL
                AND computer.u_commission_date != ''
                AND LOWER(computer.model_id_display_value) NOT LIKE '%virtual machine%'
            WHERE
                user_info.employee_number = '{eid}'
            ORDER BY
                computer.u_commission_date DESC;
            """
            
            user_df = client.query(user_query).to_dataframe()
            computer_df = client.query(computer_query).to_dataframe()
            
            if user_df.empty:
                return jsonify({'error': 'User not found'}), 404
            
            # Get user data
            user_data = user_df.iloc[0].to_dict()
            
            # Get all computers for this user
            computers_list = []
            for _, comp_row in computer_df.iterrows():
                if pd.notna(comp_row['u_discovered_model']):  # Only add if computer exists
                    computers_list.append({
                        'u_discovered_model': comp_row['u_discovered_model'],
                        'serial_number': comp_row['serial_number'],
                        'os_domain': comp_row['os_domain'],
                        'hostname': comp_row['hostname'],
                        'assigned_to_display_value': comp_row['assigned_to_display_value'],
                        'u_commission_date': comp_row['u_commission_date']
                    })
            
            user_data['computers'] = computers_list
            
            return jsonify(user_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================================
# VIDEO MANAGEMENT APIs
# ============================================================================

# GCS Bucket Configuration for Video Management
VIDEO_BUCKET_NAME = "voev-prod-voevdo-0-usmr-talentstore"
VIDEO_CHUNK_SIZE = 15 * 1024 * 1024  # 15 MB chunks for uploads
VIDEO_STREAM_CHUNK_SIZE = 2 * 1024 * 1024  # 2 MB chunks for streaming
MAX_BULK_DELETE = 100  # Maximum videos to delete in one request

def parse_video_filename(filename: str) -> Dict:
    """
    Parse video filename in flexible formats - handles field order variations gracefully
    
    Supported patterns (field order doesn't matter):
    1. "Text | Text | Text - Timestamp Timezone - Recording" - pipe-separated with trailing suffix
    2. "text (Timestamp)" - extracts timestamp from parentheses
    3. "Text - Timestamp Timezone - Recording" - dash-separated
    4. Any other format - extracts what it can without throwing errors
    
    Examples:
    - "L1_ L2_ HR Discussion with (Name) | CID (C-11111111) | (Job Title) - 2026_02_10 04:58 EST - Recording"
    - "Test Event - 2026_01_21 03:58 EST - Recording"
    - "ycp-xgmu-arg (2026-02-04 07:00 GMT-5)"
    - "L1 Hr Discussion with Pavan | C-11111233 | manager role - 2026/02/10 04:59"
    
    Returns: {title, job_id, role, timestamp, file_type, filename}
    Always returns filename and file_type. Other fields may be None if not detected.
    """
    import re
    try:
        # Get file extension and name
        name_without_ext = os.path.splitext(filename)[0]
        file_type = os.path.splitext(filename)[1].lstrip('.')
        
        title = None
        job_id = None
        role = None
        timestamp = None
        
        working = name_without_ext.strip()
        
        # ── Step 1: Strip trailing " - Recording" suffix (case-insensitive) ──
        rec_match = re.search(r'\s*-\s*Recording\s*$', working, re.IGNORECASE)
        if rec_match:
            working = working[:rec_match.start()].strip()
        
        # ── Step 2: Extract job ID early (before timestamp, so CID parens aren't confused) ──
        # Pattern: "CID (C-11111111)" or "CID C-11111111" or bare "C-11111111" / "INC-12345"
        cid_match = re.search(r'CID\s*\(?([A-Za-z]+-\d+)\)?', working, re.IGNORECASE)
        if cid_match:
            job_id = cid_match.group(1)
        else:
            # Bare job-ID pattern like C-11111233, INC-12345
            bare_id = re.search(r'\b([A-Z]+-\d{5,})\b', working)
            if bare_id:
                job_id = bare_id.group(1)
        
        # ── Step 3: Extract timestamp ──
        # Timestamp pattern: date (YYYY/MM/DD, YYYY_MM_DD, YYYY-MM-DD) + optional time + optional timezone
        ts_pattern = r'(\d{4}[/_-]\d{2}[/_-]\d{2}(?:\s+\d{1,2}:\d{2})?(?:\s+(?:EST|CST|MST|PST|EDT|CDT|MDT|PDT|GMT[+-]?\d*|UTC[+-]?\d*|[A-Z]{2,4}))?)'
        
        # Case A: "text (Timestamp)" — timestamp inside parentheses at end
        # Only match if the content looks like a date (not a name/job-ID)
        paren_ts = re.search(r'\((' + ts_pattern[1:] + r')\)\s*$', working)
        if paren_ts:
            timestamp = paren_ts.group(1).strip()
            working = working[:paren_ts.start()].strip()
        else:
            # Case B: " - Timestamp" at end or after last pipe section
            dash_ts = re.search(r'\s*-\s*' + ts_pattern + r'\s*$', working)
            if dash_ts:
                timestamp = dash_ts.group(1).strip()
                working = working[:dash_ts.start()].strip()
        
        # ── Step 4: Split remaining text on pipes if present ──
        if '|' in working:
            parts = [p.strip() for p in working.split('|')]
            
            title_parts = []
            for part in parts:
                if not part:
                    continue
                # Skip the part that is (or contains) the job ID we already extracted
                if job_id and job_id in part:
                    continue
                title_parts.append(part)
            
            # First non-job-ID part is the title, remaining is the role
            if len(title_parts) >= 1:
                title = title_parts[0]
            if len(title_parts) >= 2:
                role = title_parts[1]
        else:
            # No pipes — everything left (after timestamp/recording removal) is the title
            title = working.strip() if working.strip() else None
        
        # ── Step 5: Fallback — use full name if nothing matched ──
        if not title:
            title = name_without_ext
        
        return {
            "title": title if title else None,
            "job_id": job_id,
            "role": role,
            "timestamp": timestamp,
            "file_type": file_type,
            "filename": filename
        }
    except Exception as e:
        # Even on error, return filename and file_type
        logger.warning(f"Error parsing filename {filename}: {e}, using fallback")
        return {
            "title": os.path.splitext(filename)[0],
            "job_id": None,
            "role": None,
            "timestamp": None,
            "file_type": os.path.splitext(filename)[1].lstrip('.'),
            "filename": filename
        }

@app.route('/videos/list', methods=['GET'])
def list_videos():
    """
    List all video files in the bucket with metadata
    
    Supports flexible filename formats:
    - Format 1: "Title | JobId | Role - Timestamp"
    - Format 2: "Text (Timestamp)"
    
    Returns: Array of objects with {title, job_id, role, timestamp, file_type, filename, size, created, updated}
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        
        storage_client = storage.Client()
        bucket = storage_client.bucket(VIDEO_BUCKET_NAME)
        
        videos = []
        
        # List all blobs in bucket
        blobs = bucket.list_blobs()
        
        for blob in blobs:
            # Skip directory placeholders (trailing slash) and hidden files
            if blob.name.endswith('/') or blob.name.startswith('.'):
                continue

            parsed = parse_video_filename(blob.name)

            if parsed:
                parsed["size"] = blob.size
                parsed["created"] = blob.time_created.isoformat() if blob.time_created else None
                parsed["updated"] = blob.updated.isoformat() if blob.updated else None
                videos.append(parsed)
        
        logger.info(f"Retrieved {len(videos)} videos from bucket")
        
        return jsonify({
            "status": "success",
            "total": len(videos),
            "videos": videos
        }), 200
        
    except Exception as e:
        logger.error(f"Error listing videos: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/videos/stream', methods=['GET'])
def stream_video():
    """
    Stream a video file from GCS with 15MB capped responses.
    Always returns 206 Partial Content so clients request subsequent ranges.
    
    Query params:
        filename: exact filename to stream (required)
    
    Example: GET /videos/stream?filename=L1%20Hr%20Discussion%20with%20Pavan%20%7C%20C-11111233%20%7C%20manager%20role%20-%202026_02_10_04_59.mp4
    """
    filename = request.args.get('filename')
    request_id = request.headers.get('X-Request-ID', os.urandom(8).hex())
    logger.info(f"[{request_id}] Stream request for: {filename}")

    if not filename:
        return jsonify({
            "status": "error",
            "message": "Missing 'filename' parameter"
        }), 400

    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])

        storage_client = storage.Client()
        bucket = storage_client.bucket(VIDEO_BUCKET_NAME)
        blob = bucket.blob(filename)

        if not blob.exists():
            return jsonify({
                "status": "error",
                "message": f"Video file '{filename}' not found"
            }), 404

        blob.reload()
        file_size = blob.size

        if file_size is None:
            return jsonify({
                "status": "error",
                "message": "Could not retrieve file size"
            }), 500

        logger.info(f"[{request_id}] File size: {file_size} bytes ({file_size / (1024*1024):.2f} MB)")

        # Cloud Run buffers the full response before sending (~32MB limit).
        # Cap every response to MAX_CHUNK so Cloud Run never exceeds its limit.
        MAX_CHUNK = 15 * 1024 * 1024  # 15 MB max per response

        range_header = request.headers.get("Range", None)
        start_byte = 0

        if range_header:
            try:
                range_str = range_header.replace("bytes=", "")
                parts = range_str.split("-")
                start_byte = int(parts[0])
                end_str = parts[1].strip() if len(parts) > 1 else ""
                if end_str:
                    requested_end = int(end_str)
                else:
                    requested_end = file_size - 1

                start_byte = max(0, start_byte)
                requested_end = min(requested_end, file_size - 1)

                if start_byte >= file_size or start_byte > requested_end:
                    logger.warning(f"[{request_id}] Range not satisfiable: {range_header}")
                    resp = Response("Requested Range Not Satisfiable", status=416)
                    resp.headers["Content-Range"] = f"bytes */{file_size}"
                    return resp

            except (ValueError, IndexError) as parse_error:
                logger.warning(f"[{request_id}] Invalid Range header '{range_header}': {parse_error}. Starting from 0.")
                start_byte = 0

        # Cap end_byte so response never exceeds MAX_CHUNK
        end_byte = min(start_byte + MAX_CHUNK - 1, file_size - 1)
        content_length = (end_byte - start_byte) + 1

        logger.info(f"[{request_id}] Serving bytes {start_byte}-{end_byte}/{file_size} ({content_length} bytes)")

        # Read the exact byte range into memory (max 15MB) and return it directly
        with blob.open("rb") as f:
            f.seek(start_byte)
            data = f.read(content_length)

        # Always return 206 with Content-Range so clients know to request more
        response = Response(data, status=206, mimetype="video/mp4")
        response.headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{file_size}"
        response.headers["Content-Length"] = str(content_length)
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Cache-Control"] = "no-cache"

        return response

    except Exception as e:
        logger.error(f"[{request_id}] Error streaming video: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/videos/upload', methods=['POST'])
def upload_video():
    """
    Upload video file with chunked upload support
    
    Single upload:
    - Files in request.files: video file
    - Form data: video_id, person_name, title
    
    Bulk upload:
    - Multiple files in request.files
    - Form data for each file or use filename parsing
    
    Returns: Upload status for each file
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        
        # Get files from request
        files = request.files.getlist('video')
        
        if not files or len(files) == 0:
            return jsonify({
                "status": "error",
                "message": "No video files provided"
            }), 400
        
        storage_client = storage.Client()
        bucket = storage_client.bucket(VIDEO_BUCKET_NAME)
        
        upload_results = {
            "status": "completed",
            "total": len(files),
            "successful": 0,
            "failed": 0,
            "details": []
        }
        
        for idx, file in enumerate(files):
            if file.filename == '':
                upload_results["details"].append({
                    "status": "failed",
                    "error": "Empty filename"
                })
                upload_results["failed"] += 1
                continue
            
            try:
                # Get file extension
                file_ext = os.path.splitext(file.filename)[1]
                
                # Build formatted filename: "{title} | {job_id} | {role} - {timestamp}.ext"
                formatted_filename = f"{title} | {job_id} | {role} - {timestamp}{file_ext}"
                
                logger.info(f"Uploading video: {formatted_filename}")
                
                # Upload with chunking
                blob = bucket.blob(formatted_filename)
                
                file.seek(0)
                file.seek(0, 2)  # Seek to end
                file_size = file.tell()
                file.seek(0)  # Reset to beginning
                
                bytes_uploaded = 0
                
                # Chunked upload
                with blob.open("wb", chunk_size=VIDEO_CHUNK_SIZE) as blob_writer:
                    while True:
                        chunk = file.read(VIDEO_CHUNK_SIZE)
                        if not chunk:
                            break
                        blob_writer.write(chunk)
                        bytes_uploaded += len(chunk)
                        progress = (bytes_uploaded / file_size) * 100 if file_size > 0 else 0
                        logger.info(f"Upload progress for {formatted_filename}: {progress:.2f}%")
                
                logger.info(f"Successfully uploaded {formatted_filename} ({bytes_uploaded} bytes)")
                
                upload_results["details"].append({
                    "filename": formatted_filename,
                    "title": title,
                    "job_id": job_id,
                    "role": role,
                    "timestamp": timestamp,
                    "size": bytes_uploaded,
                    "status": "success",
                    "message": "File uploaded successfully"
                })
                upload_results["successful"] += 1
                
            except Exception as e:
                logger.error(f"Error uploading file {file.filename}: {e}")
                upload_results["details"].append({
                    "filename": file.filename,
                    "status": "failed",
                    "error": str(e)
                })
                upload_results["failed"] += 1
        
        return jsonify(upload_results), 200 if upload_results["failed"] == 0 else 207
        
    except Exception as e:
        logger.error(f"Error in upload_video: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/videos/delete', methods=['DELETE', 'POST'])
def delete_videos():
    """
    Delete video file(s) by filename
    
    Single delete:
    {
        "filename": "L1 Hr Discussion with Pavan | C-11111233 | manager role - 2026/02/10 04:59.mp4"
    }
    
    Bulk delete:
    {
        "filenames": ["video1.mp4", "video2.mp4", "video3.mp4"]
    }
    
    Returns: Delete status for each file
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])
        
        data = request.get_json()
        
        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON payload provided"
            }), 400
        
        # Get filenames to delete
        filenames = []
        
        if 'filename' in data and data['filename']:
            filenames = [data['filename']]
        elif 'filenames' in data and isinstance(data['filenames'], list):
            filenames = data['filenames']
        else:
            return jsonify({
                "status": "error",
                "message": "Missing 'filename' or 'filenames' parameter"
            }), 400
        
        # Limit bulk deletes
        if len(filenames) > MAX_BULK_DELETE:
            return jsonify({
                "status": "error",
                "message": f"Maximum {MAX_BULK_DELETE} videos can be deleted at once"
            }), 400
        
        storage_client = storage.Client()
        bucket = storage_client.bucket(VIDEO_BUCKET_NAME)
        
        delete_results = {
            "status": "completed",
            "total": len(filenames),
            "successful": 0,
            "failed": 0,
            "details": []
        }
        
        for filename in filenames:
            try:
                # Check if file exists
                blob = bucket.blob(filename)
                
                if not blob.exists():
                    delete_results["details"].append({
                        "filename": filename,
                        "status": "failed",
                        "error": "File not found"
                    })
                    delete_results["failed"] += 1
                    continue
                
                # Delete the file
                try:
                    bucket.delete_blob(filename)
                    logger.info(f"Deleted file: {filename}")
                    
                    delete_results["details"].append({
                        "filename": filename,
                        "status": "success",
                        "message": "File deleted successfully"
                    })
                    delete_results["successful"] += 1
                    
                except Exception as e:
                    logger.error(f"Error deleting file {filename}: {e}")
                    delete_results["details"].append({
                        "filename": filename,
                        "status": "failed",
                        "error": str(e)
                    })
                    delete_results["failed"] += 1
                
            except Exception as e:
                logger.error(f"Error processing filename {filename}: {e}")
                delete_results["details"].append({
                    "filename": filename,
                    "status": "failed",
                    "error": str(e)
                })
                delete_results["failed"] += 1
        
        return jsonify(delete_results), 200 if delete_results["failed"] == 0 else 207
        
    except Exception as e:
        logger.error(f"Error in delete_videos: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
    
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

CLASSIFICATION_API_URL = "https://citrix-ticket-classifier.verizon.com/classify"


@app.route('/classify-query', methods=['POST'])
def classify_query():
    """
    Classify a Citrix user query using the external ticket classifier API (PROD).

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

        resp = requests.post(
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

    except requests.exceptions.RequestException as e:
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

# ============================================================================
# CITRIX AUTO-HEAL — Real API Integration + DB Tracking + Scheduler (PROD)
# ============================================================================
import json as _json
from datetime import datetime

# ── Configuration ─────────────────────────────────────────────────
AUTOHEAL_API_URL = "https://sacwpezkva024.verizon.com:8443/autohealvm"
AUTOHEAL_API_KEY = "Ggejeql2ETpOVUw60bhVnHBzNu92O7i6"

# Prod DB Configuration for Auto-Heal Tracking
# Uses existing global DB_HOST, DB_NAME, DB_USERNAME, DB_PASSWORD
_TERMINAL_STATUSES = {"Success", "Error", "NA", "ip_fail", "ip_success", "completed"}

def _get_autoheal_db_connection():
    """Get a PostgreSQL connection via psycopg2 using Prod credentials."""
    return psycopg2.connect(
        host=DB_HOST,
        database="citrix_incident_classification", # Forced to user requested DB
        user="datalake",
        password="datalake"
    )

def _normalize_heal_status(raw_status: str) -> str:
    if not raw_status:
        return raw_status
    s = raw_status.strip()
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

def _generate_incno(slack_user_id: str) -> str:
    now = datetime.now()
    return f"{slack_user_id}{now.strftime('%Y%m%d%H%M%S')}"

def _pad_user_id(slack_user_id: str) -> str:
    """Look up 10-digit EID from Prod BigQuery slack user table."""
    try:
        query = """
            SELECT eid
            FROM `vz-it-pr-voev-voevdo-0.slack.vea_slack_userobj_data`
            WHERE slackid = @slack_id
            LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("slack_id", "STRING", slack_user_id),
            ]
        )
        bq_client = bigquery.Client(project="vz-it-pr-voev-voevdo-0")
        rows = list(bq_client.query(query, job_config=job_config).result())

        if rows and rows[0].eid:
            eid = str(rows[0].eid).strip().zfill(10)
            logger.info(f"EID lookup success: {slack_user_id} → {eid}")
            return eid

        logger.warning(f"EID not found for slack_id {slack_user_id} — using fallback")
    except Exception as e:
        logger.error(f"EID lookup failed for {slack_user_id}: {e}")

    raw = slack_user_id.lstrip("U") if slack_user_id.startswith("U") else slack_user_id
    return raw.zfill(10)[:10]

def _call_citrix_autoheal_api(user_id: str, incno: str, label: str) -> dict:
    params = {"userId": user_id, "incno": incno, "label": label}
    headers = {"X-API-Key": AUTOHEAL_API_KEY}

    logger.info(f"Calling Citrix Auto-Heal API: userId={user_id}, incno={incno}, label={label}")
    resp = requests.get(
        AUTOHEAL_API_URL,
        params=params,
        headers=headers,
        timeout=60,
        verify=False,
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
    conn = _get_autoheal_db_connection()
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
    conn = _get_autoheal_db_connection()
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
    conn = _get_autoheal_db_connection()
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
    conn = _get_autoheal_db_connection()
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
    conn = _get_autoheal_db_connection()
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
    conn = _get_autoheal_db_connection()
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

def _format_status_notification(notif: dict) -> dict:
    status = notif["new_status"]
    message = notif.get("message", "")
    user_id = notif["slack_user_id"]
    incno = notif["incno"]

    status_emoji = {
        "InProgress": "⏳", "Success": "✅", "Error": "❌", "NA": "⚠️",
        "ip_success": "✅", "ip_fail": "❌", "completed": "🎉",
    }.get(status, "ℹ️")

    status_label = {
        "InProgress": "In Progress", "Success": "Issue Fixed", "Error": "Error Occurred",
        "NA": "Auto-Heal Not Applicable", "ip_success": "VM Restart Successful",
        "ip_fail": "Auto-Heal Failed", "completed": "Completed — User Connected Successfully",
    }.get(status, status)

    text = f"{status_emoji} *Auto-Heal Status Update*\n\n"
    text += f"<@{user_id}>, here's the latest on your auto-heal request (`{incno}`):\n\n"
    text += f"*Status:* {status_label}\n"
    if message:
        text += f"*Details:* {message}\n"

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]

    if status in ("ip_fail", "Error"):
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "The auto-heal could not resolve your issue. Would you like me to raise an AYS ticket for the support team to assist you?"
            }
        })
        btn_context = _json.dumps({"incno": incno, "s": status})
        blocks.append({
            "type": "actions",
            "block_id": "autoheal_ays_prompt",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "✅ Yes, raise AYS ticket", "emoji": True}, "style": "primary", "action_id": "autoheal_ays_yes", "value": btn_context},
                {"type": "button", "text": {"type": "plain_text", "text": "❌ No, I'll handle it", "emoji": True}, "style": "danger", "action_id": "autoheal_ays_no", "value": btn_context},
            ],
        })

    if status in ("Success", "ip_success", "completed"):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "🎉 _Your Citrix VM issue has been resolved. Happy working!_"}]})

    return {"text": text, "blocks": blocks}

def _send_slack_notifications(bot_token: str, notifications: list):
    slack_api_url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"}
    
    for notif in notifications:
        try:
            msg = _format_status_notification(notif)
            payload = {
                "channel": notif["channel_id"],
                "thread_ts": notif["thread_ts"],
                "text": msg["text"],
                "blocks": msg.get("blocks", []),
            }
            resp = requests.post(slack_api_url, headers=headers, json=payload, timeout=10)
            resp_data = resp.json()
            if not resp_data.get("ok"):
                logger.error(f"Slack notification failed for {notif['incno']}: {resp_data.get('error')}")
            else:
                logger.info(f"Slack notification sent for {notif['incno']} → {notif['new_status']}")
        except Exception as e:
            logger.error(f"Error sending Slack notification for {notif['incno']}: {e}")

@app.route('/auto-heal', methods=['POST'])
def auto_heal():
    try:
        data = request.get_json()
        if not data or not data.get("user_id", "").strip():
            return jsonify({"status": "error", "message": "Missing 'user_id'"}), 400

        slack_user_id = data["user_id"].strip()
        query_text = data.get("query", "").strip()
        classification = data.get("classification", "Launch_Issue")
        channel_id = data.get("channel_id", "")
        thread_ts = data.get("thread_ts", "")
        label = classification

        incno = _generate_incno(slack_user_id)
        eid = _pad_user_id(slack_user_id)

        logger.info(f"Auto-heal triggered: slack_user={slack_user_id}, eid={eid}, incno={incno}")
        api_response = _call_citrix_autoheal_api(eid, incno, label)

        heal_status = _normalize_heal_status(api_response.get("status", "Error"))
        api_message = api_response.get("Message", api_response.get("message", ""))
        vm_name = api_response.get("vmname", "")
        details = api_response.get("details", "")
        immediate = heal_status in _TERMINAL_STATUSES

        try:
            _save_autoheal_request(
                slack_user_id=slack_user_id, user_id=eid, incno=incno, label=label,
                query_text=query_text, classification=classification, channel_id=channel_id,
                thread_ts=thread_ts, status=heal_status, api_message=api_message,
                vm_name=vm_name, details=details,
            )
        except Exception as db_err:
            logger.error(f"Failed to save auto-heal to DB (continuing): {db_err}")

        return jsonify({
            "status": "success", "heal_status": heal_status, "message": api_message,
            "incno": incno, "vm_name": vm_name, "immediate": immediate,
        }), 200

    except requests.exceptions.RequestException as e:
        return jsonify({"status": "error", "message": f"Auto-Heal API unreachable: {e}"}), 502
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/auto-heal-status', methods=['POST'])
def auto_heal_status():
    try:
        data = request.get_json()
        record = None

        channel_id = data.get("channel_id", "").strip()
        thread_ts = data.get("thread_ts", "").strip()
        if channel_id and thread_ts:
            record = _get_autoheal_by_thread(channel_id, thread_ts)

        if not record:
            slack_user_id = data.get("slack_user_id", "").strip()
            if slack_user_id:
                records = _get_autoheal_by_user(slack_user_id, limit=1)
                record = records[0] if records else None

        if not record:
            return jsonify({"status": "success", "found": False, "message": "No auto-heal request found."}), 200

        if record["status"] in ("Pending", "InProgress"):
            try:
                api_response = _call_citrix_autoheal_api(record["user_id"], record["incno"], record["label"])
                new_status = _normalize_heal_status(api_response.get("status", record["status"]))
                new_message = api_response.get("Message", api_response.get("message", ""))
                new_vm = api_response.get("vmname", "")
                new_details = api_response.get("details", "")

                if new_status != record["status"]:
                    _update_autoheal_status(record["incno"], new_status, new_message, new_vm, new_details)
                    record["status"] = new_status
                    record["api_message"] = new_message
            except Exception as poll_err:
                logger.warning(f"Could not re-poll API for {record['incno']}: {poll_err}")

        return jsonify({
            "status": "success", "found": True, "heal_status": record["status"],
            "message": record.get("api_message", ""), "incno": record["incno"],
            "vm_name": record.get("vm_name", ""), "created_at": str(record.get("created_at", "")),
            "updated_at": str(record.get("updated_at", "")),
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/auto-heal-check-pending', methods=['POST'])
def auto_heal_check_pending():
    try:
        data = request.get_json(silent=True) or {}
        slack_bot_token = data.get("slack_bot_token", "")
        pending = _get_pending_autoheal_requests()
        notifications = []

        for req in pending:
            try:
                api_response = _call_citrix_autoheal_api(req["user_id"], req["incno"], req["label"])
                new_status = _normalize_heal_status(api_response.get("status", req["status"]))
                new_message = api_response.get("Message", api_response.get("message", ""))
                new_vm = api_response.get("vmname", "")
                new_details = api_response.get("details", "")

                if new_status != req["status"] or new_status != req.get("last_notified_status"):
                    _update_autoheal_status(req["incno"], new_status, new_message, new_vm, new_details)

                    if new_status != req.get("last_notified_status"):
                        notifications.append({
                            "incno": req["incno"], "slack_user_id": req["slack_user_id"],
                            "channel_id": req["channel_id"], "thread_ts": req["thread_ts"],
                            "old_status": req["status"], "new_status": new_status, "message": new_message,
                        })
                        _mark_notified(req["incno"], new_status)
            except Exception as poll_err:
                logger.error(f"Error polling {req['incno']}: {poll_err}")

        if slack_bot_token and notifications:
            _send_slack_notifications(slack_bot_token, notifications)

        return jsonify({"status": "success", "checked": len(pending), "changed": len(notifications), "notifications": notifications}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/auto-heal-update-status', methods=['POST'])
def auto_heal_update_status():
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
        return jsonify({"status": "success", "message": f"Status updated to '{new_status}' for {incno}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/auto-heal-list', methods=['GET'])
def auto_heal_list():
    try:
        status_filter = request.args.get("status", "").strip()
        user_filter = request.args.get("user_id", "").strip()
        limit = min(int(request.args.get("limit", "50")), 200)
        offset = max(int(request.args.get("offset", "0")), 0)

        conn = _get_autoheal_db_connection()
        try:
            cur = conn.cursor()
            conditions, params = [], []
            if status_filter:
                conditions.append("status = %s")
                params.append(status_filter)
            if user_filter:
                conditions.append("slack_user_id = %s")
                params.append(user_filter)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            cur.execute(f"SELECT COUNT(*) FROM citrix_autoheal_requests {where_clause}", params)
            total = cur.fetchone()[0]

            cur.execute(
                f"""SELECT id, slack_user_id, user_id, incno, label, channel_id, thread_ts,
                           status, api_message, vm_name, details, created_at, updated_at,
                           completed_at, last_notified_status, notification_count
                    FROM citrix_autoheal_requests
                    {where_clause}
                    ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                params + [limit, offset]
            )

            columns = ["id", "slack_user_id", "user_id", "incno", "label", "channel_id", "thread_ts",
                       "status", "api_message", "vm_name", "details", "created_at", "updated_at",
                       "completed_at", "last_notified_status", "notification_count"]
            rows = []
            for row in cur.fetchall():
                record = dict(zip(columns, row))
                for key in ("created_at", "updated_at", "completed_at"):
                    if record.get(key) is not None:
                        record[key] = str(record[key])
                rows.append(record)
        finally:
            conn.close()

        return jsonify({"status": "success", "total": total, "limit": limit, "offset": offset, "requests": rows}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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

        try:
            prompt = _AUTOHEAL_THREAD_REPLY_PROMPT.format(
                incno=incno, status=heal_status, status_meaning=status_meaning,
                api_message=api_message or "N/A", created_at=created_at or "recently",
                updated_at=updated_at or "recently", user_message=user_message,
            )
            # Make sure _gemini_model is accessible here
            response = _gemini_model.generate_content(prompt)
            reply = response.text.strip()
        except Exception:
            fallback = f"Your auto-heal is currently *{heal_status}*."
            if api_message:
                fallback += f" {api_message}"
            return jsonify({"status": "success", "reply": fallback}), 200

        return jsonify({"status": "success", "reply": reply, "heal_status": heal_status}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/create-ays-incident', methods=['POST'])
def create_ays_incident():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        slack_user_id = data.get("user_id", "unknown")
        query_text = data.get("query", "").strip()
        classification = data.get("classification", "")
        short_desc = data.get("short_description", "Citrix issue reported via Slack bot")
        channel_id = data.get("channel_id", "")
        thread_ts = data.get("thread_ts", "")
        autoheal_incno = data.get("autoheal_incno", "")

        incident_number = f"INC{int(time.time())}"
        import uuid
        incident_id = str(uuid.uuid4())
        incident_url = f"https://verizon.service-now.com/nav_to.do?uri=incident.do?sysparm_query=number={incident_number}"

        try:
            autoheal_request_id = None
            if autoheal_incno:
                conn = _get_autoheal_db_connection()
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT id FROM citrix_autoheal_requests WHERE incno = %s", (autoheal_incno,))
                    row = cur.fetchone()
                    autoheal_request_id = row[0] if row else None
                finally:
                    conn.close()

            conn = _get_autoheal_db_connection()
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
        except Exception as db_err:
            logger.error(f"Failed to save AYS incident to DB (continuing): {db_err}")

        return jsonify({
            "status": "success", "incident_id": incident_id, "incident_number": incident_number,
            "incident_url": incident_url, "short_description": short_desc, "assigned_group": "Citrix Support",
            "priority": "3", "message": f"AYS Incident {incident_number} created successfully.",
        }), 201

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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
        bq_client = bigquery.Client(project="vz-it-pr-voev-voevdo-0")
        # Query BigQuery for EID from Slack user object table
        # Table only has: slackid, eid
        query = """
            SELECT eid
            FROM `vz-it-pr-voev-voevdo-0.slack.vea_slack_userobj_data`
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
                FROM `vz-it-pr-voev-voevdo-0.empan_prd_tbls.vea_hr_latest_data`
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


# ============================================================================
# CHANNEL AI BOT — Configuration & Endpoints
# ============================================================================

# --- Prod BigQuery Tables ---
BQ_PROJECT_PROD = "vz-it-pr-voev-voevdo-0"
# SLACK_DATA_TABLE = f"{BQ_PROJECT_PROD}.empan_prd_tbls.vea_slack_data"
SLACK_DATA_TABLE = f"{BQ_PROJECT_PROD}.slack.vea_slack_data"
BQ_KB_TABLE = f"{BQ_PROJECT_PROD}.empan_prd_tbls.vea_sn_kb_knowledge"

# --- Cloud SQL (channelassistantai) for Channel AI metadata ---
CHANNEL_AI_DB_NAME = "channelassistantai"

# --- Dialogflow CX Agent Config (Prod) ---
# Set USE_DFCX = False to skip DFCX entirely and use Gemini + BigQuery directly.
# DFCX requires a configured OpenAPI Tool pointing to /channel-data-webhook.
# Until the tool is set up in the Conversational Agents console, keep this False.
USE_DFCX = False
CHANNEL_DFCX_PROJECT_ID = BQ_PROJECT_PROD
CHANNEL_DFCX_LOCATION = "us-west1"
CHANNEL_DFCX_AGENT_ID = os.environ.get("CHANNEL_DFCX_AGENT_ID", "130754c3-1abd-4f74-8027-0964e1fe981f")

# --- Channel Mapping (test channel → source channel data) ---
# Maps the test channel ID to the source channel whose data we query from BigQuery
CHANNEL_DATA_MAP = {
    "C0B7D0YKQS2": "C05CHPFT57A",  # test channel → digital-workplace-influencers
    "C0BD08SBE85": "C04098B3W8J",
}

# --- Slack Workspace URL for source links ---
SLACK_WORKSPACE_URL = "https://verizon.enterprise.slack.com"

# --- Vertex AI / Gemini Initialization ---
try:
    vertexai.init(project=BQ_PROJECT_PROD, location="us-central1")
    _gemini_model = GenerativeModel("gemini-2.5-flash")
    logger.info("Vertex AI / Gemini initialized for Channel AI")
except Exception as _vai_err:
    logger.warning(f"Could not initialize Vertex AI: {_vai_err}")
    _gemini_model = None

# --- Dialogflow CX Sessions Client ---
try:
    _dfcx_client = SessionsClient(
        client_options={"api_endpoint": f"{CHANNEL_DFCX_LOCATION}-dialogflow.googleapis.com"}
    )
    logger.info("Dialogflow CX SessionsClient initialized")
except Exception as _dfcx_err:
    logger.warning(f"Could not initialize DFCX client: {_dfcx_err}")
    _dfcx_client = None

# --- Channel Data Cache ---
_channel_data_cache: dict = {}
_CHANNEL_CACHE_TTL = 1800  # 30 minutes — data changes slowly with 180-day window

# --- Conversation Memory Store ---
# Keyed by session_id → {"turns": [{"user": ..., "assistant": ...}], "last_active": epoch}
# Auto-expires after 30 min of inactivity. Max 10 turns per session.
_conversation_store: dict = {}
_conversation_lock = threading.Lock()
_CONV_TTL_SECONDS = 1800   # 30 minutes
_CONV_MAX_TURNS   = 10     # max Q&A pairs kept per session


def _get_conversation_history(session_id: str) -> list:
    """Retrieve conversation turns for a session, purging if expired."""
    with _conversation_lock:
        entry = _conversation_store.get(session_id)
        if not entry:
            return []
        if time.time() - entry["last_active"] > _CONV_TTL_SECONDS:
            del _conversation_store[session_id]
            return []
        return list(entry["turns"])


def _save_to_db(session_id: str, channel_id: str, user_text: str, assistant_text: str, response_source: str = "unknown"):
    """Save conversation turn to PostgreSQL database (channelassistantai)."""
    connection = None
    try:
        connection = _get_channel_ai_db()
        cursor = connection.cursor()

        insert_query = """
        INSERT INTO conversation_history
        (session_id, channel_id, user_query, bot_response, response_source, created_at)
        VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        """

        cursor.execute(insert_query, (session_id, channel_id, user_text, assistant_text, response_source))
        connection.commit()

        logger.info(f"Conversation saved to DB - session: {session_id}, channel: {channel_id}")

    except Exception as e:
        logger.error(f"Error saving conversation to DB: {e}")
    finally:
        if connection:
            try:
                connection.close()
            except:
                pass


def _save_conversation_turn(session_id: str, channel_id: str, user_text: str, assistant_text: str, response_source: str = "unknown"):
    """Append a Q&A turn to session history and persist to database."""
    # Save to in-memory store (for follow-ups in same session)
    with _conversation_lock:
        # Purge expired sessions opportunistically (every 100 saves)
        if len(_conversation_store) % 100 == 0:
            now = time.time()
            expired = [k for k, v in _conversation_store.items()
                       if now - v["last_active"] > _CONV_TTL_SECONDS]
            for k in expired:
                del _conversation_store[k]

        entry = _conversation_store.setdefault(session_id, {"turns": [], "last_active": 0})
        entry["turns"].append({"user": user_text, "assistant": assistant_text})
        # Keep only the most recent turns
        if len(entry["turns"]) > _CONV_MAX_TURNS:
            entry["turns"] = entry["turns"][-_CONV_MAX_TURNS:]
        entry["last_active"] = time.time()

    # Persist to database asynchronously to avoid blocking the response
    try:
        thread = threading.Thread(
            target=_save_to_db,
            args=(session_id, channel_id, user_text, assistant_text, response_source),
            daemon=True
        )
        thread.start()
    except Exception as e:
        logger.error(f"Error spawning DB save thread: {e}")


def _resolve_channel_id(channel_id: str) -> str:
    """Resolve channel ID through mapping (test → source). Returns source channel_id."""
    return CHANNEL_DATA_MAP.get(channel_id, channel_id)


# --- Conversation memory expiry helpers ---

_FOLLOWUP_INDICATORS = {
    "yes", "no", "ok", "okay", "sure", "correct", "right", "yep", "nope",
    "tell me more", "continue", "elaborate", "more details", "explain more",
    "go on", "proceed", "noted", "thanks", "thank you", "understood",
    "makes sense", "got it", "what about", "how about", "and then",
    "what else", "anything else", "more", "next", "so", "and",
}


def _estimate_session_thread_age(session_id: str) -> float:
    """Extract thread_ts from session_id and return age in seconds. 0 if unparseable."""
    try:
        # session_id format: "channel-U12345-1234567890.123456"
        ts_str = session_id.rsplit("-", 1)[-1]
        return time.time() - float(ts_str)
    except (ValueError, IndexError):
        return 0.0


def _is_likely_followup(text: str) -> bool:
    """Return True when the query looks like a follow-up that needs prior conversation context."""
    cleaned = text.strip().lower()
    word_only = re.sub(r'[^\w\s]', '', cleaned).strip()
    words = word_only.split()
    # Very short messages (≤4 words) are almost always follow-ups
    if len(words) <= 4:
        return True
    # Longer but all follow-up indicator words
    if all(w in _FOLLOWUP_INDICATORS for w in words):
        return True
    # Contains explicit follow-up phrases
    followup_phrases = ["tell me more", "what about", "go on", "continue from", "as i said", "like i mentioned"]
    if any(p in cleaned for p in followup_phrases):
        return True
    return False


def _build_slack_thread_link(channel_id: str, thread_ts: str) -> str:
    """Build a Slack thread permalink from channel_id and thread_ts."""
    if not thread_ts or not channel_id:
        return ""
    ts_no_dot = thread_ts.replace(".", "")
    return f"{SLACK_WORKSPACE_URL}/archives/{channel_id}/p{ts_no_dot}"


def fetch_channel_messages(channel_id: str, days: int = 180) -> list:
    """
    Fetch meaningful messages from a Slack channel stored in BigQuery (prod).
    Uses a date-based window (default 180 days) instead of a row count limit.
    Filters out system messages.
    """
    bq_client = bigquery.Client(project=BQ_PROJECT_PROD)
    query = f"""
        SELECT text, channel_normalname, ts_time, slackuserid,
               thread_ts, reply_count, subtype, channel_name AS channel_id_col
        FROM `{SLACK_DATA_TABLE}`
        WHERE channel_name = @channel_id
          AND text IS NOT NULL
          AND TRIM(text) != ''
          AND ts_time >= CAST(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY) AS DATETIME)
          AND (subtype IS NULL OR subtype NOT IN (
               'channel_join', 'channel_leave',
               'channel_posting_permissions', 'bot_message'))
        ORDER BY ts_time DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("channel_id", "STRING", channel_id),
            bigquery.ScalarQueryParameter("days", "INT64", days),
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
    logger.info(f"Fetched {len(messages)} messages for channel {channel_id} (last {days} days)")
    return messages


def _get_channel_ai_db():
    """Get a connection to the channelassistantai Cloud SQL database."""
    return psycopg2.connect(
        host=DB_HOST,
        database=CHANNEL_AI_DB_NAME,
        user=DB_USERNAME,
        password=DB_PASSWORD
    )


def fetch_channel_knowledge(channel_id: str) -> list:
    """
    Fetch knowledge for a channel using hybrid Cloud SQL + BQ approach:
    1. Cloud SQL: custom admin content (FAQs, steps, guides) — served directly
    2. Cloud SQL: KB number references → fetch full content from BQ vea_sn_kb_knowledge
    """
    knowledge = []
    kb_numbers_to_fetch = []

    # Step 1: Get all knowledge entries from Cloud SQL
    try:
        conn = _get_channel_ai_db()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, knowledge_type, title, content, source_url, kb_number, updated_at
                FROM knowledge_articles
                WHERE channel_id = %s
                ORDER BY updated_at DESC
            """, (channel_id,))
            rows = cur.fetchall()
        conn.close()

        for row in rows:
            if row["kb_number"]:
                # This is a KB reference — need to fetch full content from BQ
                kb_numbers_to_fetch.append({
                    "id": str(row["id"]),
                    "kb_number": row["kb_number"],
                    "source_url": row.get("source_url", ""),
                })
            else:
                # Custom admin content — use directly from Cloud SQL
                knowledge.append({
                    "id": str(row["id"]),
                    "type": row["knowledge_type"] or "info",
                    "title": row["title"] or "",
                    "content": row["content"] or "",
                    "source_url": row.get("source_url", ""),
                    "updated_at": str(row["updated_at"] or ""),
                })
    except Exception as e:
        logger.warning(f"Could not fetch Cloud SQL knowledge for {channel_id}: {e}")
        return []

    # Step 2: Fetch full KB article details from BQ for any kb_number references
    if kb_numbers_to_fetch:
        try:
            bq_client = bigquery.Client(project=BQ_PROJECT_PROD)
            numbers = [item["kb_number"].lower().strip() for item in kb_numbers_to_fetch]
            query = f"""
                SELECT number, short_description, text, kb_snippet, kb_steps,
                       CONCAT('https://atyourservice.verizon.com/ays?id=ays_kb_article&sysparm_article=', UPPER(number)) as url
                FROM `{BQ_KB_TABLE}`
                WHERE LOWER(number) IN UNNEST(@kb_numbers)
                  AND workflow_state = 'published'
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("kb_numbers", "STRING", numbers),
                ]
            )
            bq_rows = list(bq_client.query(query, job_config=job_config).result())

            # Map BQ results by number
            bq_map = {}
            for bq_row in bq_rows:
                bq_map[bq_row.number.lower()] = bq_row

            for item in kb_numbers_to_fetch:
                bq_row = bq_map.get(item["kb_number"].lower())
                if bq_row:
                    # Combine all content fields — steps first (most actionable), then body, then snippet
                    _parts = []
                    if bq_row.kb_steps:
                        _parts.append(bq_row.kb_steps)
                    if bq_row.text and bq_row.text not in (bq_row.kb_steps or ""):
                        _parts.append(bq_row.text)
                    if bq_row.kb_snippet and bq_row.kb_snippet not in (bq_row.kb_steps or "", bq_row.text or ""):
                        _parts.append(bq_row.kb_snippet)
                    content = "\n\n".join(_parts)
                    if len(content) > 5000:
                        content = content[:5000] + "..."
                    knowledge.append({
                        "id": item["id"],
                        "type": "kb_article",
                        "kb_number": bq_row.number.upper(),
                        "title": f"{bq_row.number}: {bq_row.short_description or ''}",
                        "content": content,
                        "source_url": bq_row.url or item.get("source_url", ""),
                        "updated_at": "",
                    })
                else:
                    # KB number not found in BQ — include stub
                    knowledge.append({
                        "id": item["id"],
                        "type": "kb_article",
                        "kb_number": item["kb_number"].upper(),
                        "title": f"{item['kb_number']} (not found in KB database)",
                        "content": "",
                        "source_url": item.get("source_url", ""),
                        "updated_at": "",
                    })
        except Exception as e:
            logger.warning(f"Could not fetch BQ KB articles for {channel_id}: {e}")

    logger.info(f"Fetched {len(knowledge)} knowledge entries for channel {channel_id}")
    return knowledge


def _get_channel_context(channel_id: str):
    """Get channel messages and knowledge with TTL-based caching."""
    source_channel = _resolve_channel_id(channel_id)
    now = time.time()
    if source_channel in _channel_data_cache:
        cached_at, cached = _channel_data_cache[source_channel]
        if now - cached_at < _CHANNEL_CACHE_TTL:
            return cached

    messages = fetch_channel_messages(source_channel)
    knowledge = fetch_channel_knowledge(source_channel)
    channel_name = messages[0]["channel_name"] if messages else source_channel

    result = (messages, knowledge, channel_name)
    _channel_data_cache[source_channel] = (now, result)
    return result


_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "but", "and", "or", "if", "while", "about", "up",
    "that", "this", "these", "those", "it", "its", "i", "me", "my", "we",
    "our", "you", "your", "he", "him", "his", "she", "her", "they", "them",
    "their", "what", "which", "who", "whom", "get", "got", "please", "help",
    "know", "want", "like", "also", "well", "back", "even", "give", "new",
}


def _filter_messages_by_relevance(messages: list, query_text: str,
                                   top_relevant: int = 100,
                                   top_recent: int = 50) -> list:
    """
    Filter a large message set to the most relevant + most recent messages.
    Scores each message by keyword overlap with the user's query.
    Returns a de-duplicated, timestamp-sorted list.
    """
    if len(messages) <= top_relevant + top_recent:
        return messages

    keywords = {
        w.lower() for w in re.sub(r'[^\w\s]', '', query_text).split()
        if len(w) > 2 and w.lower() not in _STOP_WORDS
    }

    if not keywords:
        return messages[:top_relevant + top_recent]

    scored = []
    for msg in messages:
        text_lower = msg["text"].lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        scored.append((score, msg))

    scored.sort(key=lambda x: x[0], reverse=True)
    relevant = [msg for score, msg in scored[:top_relevant] if score > 0]

    recent = messages[:top_recent]

    seen_ts = set()
    combined = []
    for msg in relevant + recent:
        ts = msg["timestamp"]
        if ts not in seen_ts:
            seen_ts.add(ts)
            combined.append(msg)

    combined.sort(key=lambda m: m["timestamp"])
    return combined


def _rank_knowledge_by_relevance(knowledge: list, query_text: str) -> list:
    """
    Sort knowledge articles by keyword overlap with the query (highest score first).
    Articles with empty content are pushed to the end so Gemini sees useful content first.
    """
    if not knowledge:
        return []
    keywords = {
        w.lower() for w in re.sub(r'[^\w\s]', '', query_text).split()
        if len(w) > 2 and w.lower() not in _STOP_WORDS
    }
    if not keywords:
        return knowledge
    scored = []
    for k in knowledge:
        combined = f"{k.get('title', '')} {k.get('content', '')}".lower()
        score = sum(1 for kw in keywords if kw in combined)
        # Empty-content stubs always score 0 effectively — ensure they sink to bottom
        if not k.get("content", "").strip():
            score = -1
        scored.append((score, k))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [k for _, k in scored]


def _llm_select_relevant_knowledge(knowledge: list, query_text: str) -> list:
    """
    LLM call #1: Ask Gemini to pick the 3-5 most relevant KB articles for the query.
    Returns a reordered, filtered list — most relevant first.
    Falls back to keyword-ranked order if the LLM call fails or returns nothing useful.
    """
    if not _gemini_model or len(knowledge) <= 3:
        return knowledge[:5]

    kb_listing = "\n".join(
        f"{i}: [{k.get('kb_number', 'custom')}] {k.get('title', '')} — {k.get('content', '')[:200].strip()}"
        for i, k in enumerate(knowledge)
    )
    selection_prompt = (
        f"User question: \"{query_text}\"\n\n"
        f"Available knowledge base articles:\n{kb_listing}\n\n"
        "Which articles best answer this question?\n"
        "Return ONLY a JSON array of 0-based indices of the 3-5 most relevant articles, most relevant first.\n"
        "Example: [2, 0, 4]\n"
        "Output ONLY the JSON array — nothing else."
    )
    try:
        resp = _gemini_model.generate_content(selection_prompt)
        m = re.search(r'\[[\d,\s]+\]', resp.text.strip())
        if m:
            indices = json.loads(m.group(0))
            selected = [knowledge[i] for i in indices if isinstance(i, int) and 0 <= i < len(knowledge)]
            if selected:
                logger.info(f"LLM KB selector chose indices {indices} for '{query_text[:50]}'")
                return selected
    except Exception as e:
        logger.warning(f"LLM KB selection failed, using keyword ranking: {e}")
    return knowledge[:5]


def detect_intent_channel(session_id: str, text: str) -> str:
    """
    Call the Channel Dialogflow CX agent using detectIntent API.
    Returns the agent's text response.
    """
    if not _dfcx_client:
        raise RuntimeError("DFCX client not initialized")

    session_path = _dfcx_client.session_path(
        project=CHANNEL_DFCX_PROJECT_ID,
        location=CHANNEL_DFCX_LOCATION,
        agent=CHANNEL_DFCX_AGENT_ID,
        session=session_id,
    )

    text_input = dfcx_types.TextInput(text=text)
    query_input = dfcx_types.QueryInput(
        text=text_input,
        language_code="en",
    )

    response = _dfcx_client.detect_intent(
        request=dfcx_types.DetectIntentRequest(
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

    answer = "\n\n".join(response_texts) if response_texts else ""
    logger.info(
        f"DFCX detectIntent completed: session={session_id}, has_text_response={bool(answer)}"
    )
    return answer


# def generate_channel_ai_response(channel_id: str, query_text: str, session_id: str = "default") -> str:
#     """
#     Generate an AI answer using channel history + knowledge + Gemini directly.
#     Maintains per-session conversation memory (up to 10 turns, 30 min TTL).
#     """
#     if not _gemini_model:
#         return "Gemini fallback is not available. Please configure the Dialogflow CX agent."

#     all_messages, knowledge, channel_name = _get_channel_context(channel_id)
#     messages = _filter_messages_by_relevance(all_messages, query_text)
#     logger.info(f"Keyword filter: {len(all_messages)} total → {len(messages)} relevant for query '{query_text[:60]}'")
#     prior_turns = _get_conversation_history(session_id)

#     # --- Conversational memory expiry check ---
#     # If the session has no history AND the thread is older than the TTL AND the
#     # query looks like a follow-up (not a standalone question), tell the user
#     # gracefully instead of giving a confusing context-free answer.
#     if not prior_turns and session_id.startswith("channel-"):
#         thread_age = _estimate_session_thread_age(session_id)
#         if thread_age > _CONV_TTL_SECONDS and _is_likely_followup(query_text):
#             return (
#                 "⏰ *Conversation memory has expired* — sessions reset after *30 minutes* of inactivity.\n\n"
#                 "I no longer have context from your earlier questions in this thread. "
#                 "To continue, please *re-state your full question* (or paste the key details) "
#                 "and I’ll pick right up from there!"
#             )

#     # Resolve the actual source channel_id for building links
#     source_channel_id = _resolve_channel_id(channel_id)
    
#     parts = []
#     parts.append(
#         f"You are a senior engineer embedded in the *#{channel_name}* Slack channel.\n"
#         "You have read every message and know this channel's history.\n"
#         "Answer like a sharp, helpful teammate — not a chatbot.\n"
#         "If the user's question refers to a previous answer in this conversation, use that context.\n\n"
#         "RULES:\n"
#         "- Lead with the fix, not the problem description.\n"
#         "- Max 3 numbered solutions, most likely first.\n"
#         "- Use *bold* for key actions. No markdown headers.\n"
#         "- Under 150 words.\n"
#         "- End with a clear next step.\n"
#         "- SOURCE ATTRIBUTION (REQUIRED): On its own line at the very end, output exactly:\n"
#         "  Sources: <URL1|Label1> <URL2|Label2>\n"
#         "  Rules for the Sources line:\n"
#         "  1. KB Articles: If your answer uses a Knowledge Base article tagged [KB:N], write:\n"
#         "     <the article URL|KB: the article title>\n"
#         "     Copy the URL exactly from the article's URL field. Do NOT abbreviate or omit URLs.\n"
#         "  2. Channel Messages: If your answer uses channel messages with [ref: URL] tags, write:\n"
#         "     <the ref URL|Thread Link>\n"
#         "     Use the exact [ref: URL] value. Max 3 unique threads.\n"
#         "  3. Mixed: If you used both KB articles and channel messages, include both types.\n"
#         "  4. Channel History (no refs): If based on channel messages that had no [ref:] tag, write:\n"
#         "     Sources: Channel History\n"
#         "  5. General Knowledge: If the answer is from your own training (not this channel), write:\n"
#         "     Sources: Gemini AI\n"
#         "  IMPORTANT: Always copy URLs verbatim from the data provided. Never output just a KB number without its full URL.\n"
#     )

#     # Inject prior conversation turns so Gemini can follow up on previous answers
#     if prior_turns:
#         parts.append("=== CONVERSATION SO FAR ===")
#         for turn in prior_turns:
#             parts.append(f"User: {turn['user']}")
#             parts.append(f"Assistant: {turn['assistant']}")
#         parts.append("")

#     kb_lookup = {}
#     if knowledge:
#         parts.append("=== CHANNEL KNOWLEDGE & FAQs ===")
#         kb_index = 0
#         for k in knowledge[:10]:
#             label = k.get("title") or k.get("type", "info")
#             source_url = k.get("source_url", "")
#             kb_number = k.get("kb_number", "")
#             if k.get("type") == "kb_article" and source_url:
#                 kb_index += 1
#                 tag = f"KB:{kb_index}"
#                 kb_lookup[tag] = (source_url, label, kb_number)
#                 parts.append(
#                     f"[{tag}] Title: {label} | URL: {source_url}\n"
#                     f"  Content: {k['content'][:500]}"
#                 )
#             elif source_url:
#                 parts.append(f"[{label}] (source_url: {source_url}): {k['content'][:500]}")
#             else:
#                 parts.append(f"[{label}]: {k['content'][:500]}")
#         parts.append("")

#     if messages:
#         parts.append(
#             "IMPORTANT: When answering from channel messages below, *quote or paraphrase* "
#             "the specific message(s) that support your answer. Reference the user and timestamp. "
#             "Prefer messages that have a [ref:] tag so you can provide a clickable thread link in Sources."
#         )
#         parts.append(f"=== RECENT CHANNEL MESSAGES ({len(messages)} messages) ===")
#         total_chars = 0
#         max_context_chars = 80000
#         for msg in reversed(messages):
#             thread_link = ""
#             msg_thread_ts = msg.get("thread_ts", "")
#             if msg_thread_ts:
#                 thread_link = _build_slack_thread_link(source_channel_id, msg_thread_ts)

#             if thread_link:
#                 line = f"[{msg['timestamp']}] User {msg['user_id']}: {msg['text']} [ref: {thread_link}]"
#             else:
#                 line = f"[{msg['timestamp']}] User {msg['user_id']}: {msg['text']}"

#             if total_chars + len(line) > max_context_chars:
#                 parts.append("... (older messages truncated)")
#                 break
#             parts.append(line)
#             total_chars += len(line)
#         parts.append("")

#     parts.append(f"=== CURRENT QUESTION ===\n{query_text}")

#     prompt = "\n".join(parts)
#     response = _gemini_model.generate_content(prompt)
#     answer_text = response.text

#     # --- Source link enforcement ---
#     # Gemini may output "Sources: Channel History" when messages lacked [ref:] tags
#     # (BigQuery stores thread_ts as NULL for non-threaded messages).
#     # Collect the first 3 unique thread links from the messages context and
#     # replace the generic fallback with real Slack links.
#     fallback_links: list = []
#     seen_links: set = set()
#     for msg in messages:
#         msg_thread_ts = msg.get("thread_ts", "")
#         if msg_thread_ts:
#             lnk = _build_slack_thread_link(source_channel_id, msg_thread_ts)
#             if lnk and lnk not in seen_links:
#                 seen_links.add(lnk)
#                 fallback_links.append(lnk)
#         if len(fallback_links) >= 3:
#             break

#     if fallback_links:
#         formatted = " ".join(f"<{l}|Thread Link>" for l in fallback_links)
#         real_sources_line = f"Sources: {formatted}"
#         if "Sources: Channel History" in answer_text:
#             # Upgrade generic label to actual clickable Slack thread links
#             answer_text = answer_text.replace("Sources: Channel History", real_sources_line)
#         elif "Sources:" not in answer_text:
#             # Gemini skipped the line entirely — append real links
#             answer_text = answer_text.rstrip() + f"\n\n{real_sources_line}"
#     else:
#         # No thread links (messages had no thread_ts in BigQuery).
#         # Trust Gemini's own label; annotate "Channel History" to explain the missing link.
#         if "Sources: Channel History" in answer_text:
#             answer_text = answer_text.replace(
#                 "Sources: Channel History",
#                 "Sources: Channel History _(thread link unavailable)_"
#             )
#         elif "Sources:" not in answer_text:
#             answer_text = answer_text.rstrip() + "\n\nSources: Channel History _(thread link unavailable)_"

#     # --- KB article source link enforcement ---
#     if kb_lookup:
#         for tag, (url, title, kb_num) in kb_lookup.items():
#             display_title = title
#             if ":" in title:
#                 display_title = title.split(":", 1)[1].strip()
#             proper_link = f"<{url}|KB: {display_title}>"

#             answer_text = answer_text.replace(f"[{tag}]", proper_link)
#             answer_text = answer_text.replace(f"<{tag}>", proper_link)
#             answer_text = answer_text.replace(tag, proper_link)

#         def _fix_kb_ref(match):
#             bracket = match.group(0)
#             kb_num_raw = match.group(1).upper()
#             for _tag, (url, title, kb_num) in kb_lookup.items():
#                 if kb_num and kb_num.upper() == kb_num_raw:
#                     display_title = title
#                     if ":" in title:
#                         display_title = title.split(":", 1)[1].strip()
#                     return f"<{url}|KB: {display_title}>"
#             return bracket

#         answer_text = re.sub(
#             r'[\[<](kb\d{5,10})[^\]>]*[\]>]',
#             _fix_kb_ref,
#             answer_text,
#             flags=re.IGNORECASE,
#         )

#         lines = answer_text.split("\n")
#         for i, line in enumerate(lines):
#             if line.strip().startswith("Sources:"):
#                 for _tag, (url, title, kb_num) in kb_lookup.items():
#                     display_title = title
#                     if ":" in title:
#                         display_title = title.split(":", 1)[1].strip()
#                     bare_ref = f"KB: {display_title}"
#                     proper_link = f"<{url}|KB: {display_title}>"
#                     if bare_ref in line and proper_link not in line:
#                         line = line.replace(bare_ref, proper_link)
#                 lines[i] = line
#         answer_text = "\n".join(lines)

#     # Persist this turn for future follow-ups
#     _save_conversation_turn(session_id, query_text, answer_text)

#     return answer_text

# ── Dynamic System Instruction Management ─────────────────────────────────────

def _get_channel_system_instruction(channel_id: str, has_kb: bool, channel_name: str) -> str:
    """
    Get system_instruction for a channel with priority:
    1. Hardcoded (Citrix in production - never modified)
    2. Custom from database (AI-generated or manually configured)
    3. Default based on has_kb flag

    Args:
        channel_id: Slack channel ID
        has_kb: Whether channel has knowledge base articles
        channel_name: Channel display name

    Returns:
        System instruction text for Gemini
    """
    # PRIORITY 1: HARDCODED CITRIX CHANNEL (PRODUCTION - DO NOT MODIFY)
    # CITRIX_CHANNEL_ID = "C0BHJL3Q5S8"  # Production Citrix channel ID
    CITRIX_CHANNEL_ID = "C0218861E3C"

    if channel_id == CITRIX_CHANNEL_ID:
        # Hardcoded Citrix instruction - KB-ONLY (no Slack history)
        return (
            f"You are a technical support assistant for the *#{channel_name}* Slack channel.\n\n"
            "CRITICAL RULES:\n"
            "- KNOWLEDGE BASE PRIORITY: Use ONLY Knowledge Base articles as your primary and authoritative source. Do NOT reference Slack channel history or past conversations.\n"
            "- EXTRACT RELATED GUIDANCE: Even if a KB article doesn't exactly match the query, extract relevant preventative steps, best practices, or related troubleshooting guidance that helps address the user's issue. For example:\n"
            "  • Data recovery query + KB about data storage best practices → Extract the storage guidance to prevent future data loss\n"
            "  • Performance issue + KB about performance troubleshooting → Extract all relevant performance steps even if some don't apply\n"
            "  • DO NOT say 'KB articles do not contain information' if there's any related guidance available\n"
            "- FOCUS: Answer ONLY what the user's symptoms require. Map symptoms to relevant sections:\n"
            "  • CPU/memory high usage → include CPU and memory steps only\n"
            "  • Connection disruption / disconnection / cannot open URLs → include network latency checks (ping, CQI, router, ISP guidance)\n"
            "  • Freezing/slow + connection disruption → include both resource (CPU/memory) AND network checks\n"
            "  • General slowness with no URL issues → CPU/memory steps only, skip network latency\n"
            "  Do NOT include disk space cleanup, Citrix Workspace App version checks, or application heap size adjustments unless the user explicitly mentions those issues.\n"
            "- NEVER ask the user for additional details. Always provide a complete answer using the Knowledge Base articles provided.\n"
            "- READ each KB article and extract the steps that are directly relevant or preventatively helpful to the user's question. Skip KB sections that cover completely unrelated topics.\n"
            "- MULTIPLE KB ARTICLES FOR SAME SYMPTOM: When multiple KB articles address the same issue with DIFFERENT solutions (e.g., folder mapping can fail due to permissions OR network issues):\n"
            "  1. Analyze the KB article titles and content to identify the PRIMARY/MOST COMMON solution\n"
            "  2. Provide ONLY the most relevant KB article's solution\n"
            "  3. DO NOT combine multiple different troubleshooting approaches in one answer\n"
            "  4. Priority order for common issues:\n"
            "     • Folder/drive mapping → Network/VPN configuration FIRST (check VPN, host files), then permissions (EACR) if network is confirmed working\n"
            "     • Login/authentication → Credentials and VPN connection FIRST, then permissions\n"
            "     • Application launch → Check Citrix Workspace App and session state FIRST, then permissions\n"
            "  5. If unsure which is primary, choose the KB that mentions 'first step' or 'before' or appears simpler\n"
            "- REWRITE the steps in clear, easy-to-follow language that a non-technical user can act on immediately. Do NOT copy-paste raw KB text. Do NOT say 'refer to the article'.\n"
            "- LENGTH: Write at a medium-to-slightly-above-medium level. Keep ALL relevant steps and specific details (file paths, commands, values, URLs) — do NOT remove any information. Combine closely related sequential actions into a single bullet using arrows (→). Avoid repeating the same instruction twice. No long introductory sentences — go straight to the action.\n"
            "  BAD (too long): '• Open Task Manager by going to the Start menu. • Go to the Processes tab. • Identify any application consuming high CPU. • Right-click and select End Task.'\n"
            "  GOOD (concise): '• Open Task Manager → Processes tab → identify high CPU apps → right-click → End Task → relaunch.'\n"
            "  Apply this pattern throughout: collapse sequential same-topic steps into one arrow-chained bullet wherever it makes sense without losing detail.\n"
            "- Structure the answer with numbered sections (1., 2., 3.) with nested sub-bullets (a., b., c. or •) for each step.\n"
            "- If multiple KB articles are relevant, merge only the sections that match the user's question — do NOT include unrelated sections from those articles.\n"
            "- Include every specific detail for the relevant steps: exact file names, folder paths, parameter values, button/menu labels, URLs. Do not generalize or omit specifics.\n"
            "- PRESERVE EXACT CASE for all technical content: commands, JVM flags, file paths, config keys, and parameter values must retain their original casing exactly as they appear in the KB (e.g., AddVMOption -Xms512m -Xmx2048m, eclipse.ini, product.conf). Never lowercase technical terms.\n"
            "- SOURCE FORMAT (REQUIRED): On its own line at the very end, output EXACTLY:\n"
            "  Sources: [KB:1] [KB:2]\n"
            "  Use ONLY the [KB:N] bracket tags for the articles you used. Do NOT output URLs, titles, or bare KB numbers — the system converts [KB:N] to proper links automatically.\n"
        )

    # PRIORITY 2: CUSTOM INSTRUCTION FROM DATABASE
    try:
        conn = _get_channel_ai_db()  # channelassistantai DB
        cur = conn.cursor()
        cur.execute("""
            SELECT system_instruction, is_hardcoded
            FROM channel_system_instructions
            WHERE channel_id = %s
        """, (channel_id,))
        row = cur.fetchone()
        conn.close()

        if row and row[0]:  # Has custom instruction
            logger.info(f"Using custom system_instruction for channel {channel_id}")
            return row[0]
    except Exception as e:
        logger.warning(f"Could not fetch custom system_instruction for {channel_id}: {e}")

    # PRIORITY 3: DEFAULT FALLBACK BASED ON has_kb
    if has_kb:
        logger.info(f"Using default KB-based system_instruction for channel {channel_id}")
        return (
            f"You are a technical support assistant for the *#{channel_name}* Slack channel.\n\n"
            "CRITICAL RULES:\n"
            "- MULTI-SOURCE GROUNDING: Use ALL available sources to answer comprehensively:\n"
            "  1. Slack Channel History (past conversations, real user experiences)\n"
            "  2. Knowledge Base Articles (official troubleshooting guides)\n"
            "  3. Cross-reference both: if Slack users solved an issue, AND there's a KB article, mention BOTH\n"
            "- PRIORITY ORDER: Check Slack history FIRST for recent solutions, then KB articles for official steps\n"
            "- FOCUS: Answer ONLY what the user's symptoms require. Map symptoms to relevant sections:\n"
            "  • CPU/memory high usage → include CPU and memory steps only\n"
            "  • Connection disruption / disconnection / cannot open URLs → include network latency checks (ping, CQI, router, ISP guidance)\n"
            "  • Freezing/slow + connection disruption → include both resource (CPU/memory) AND network checks\n"
            "  • General slowness with no URL issues → CPU/memory steps only, skip network latency\n"
            "  Do NOT include disk space cleanup, Citrix Workspace App version checks, or application heap size adjustments unless the user explicitly mentions those issues.\n"
            "- NEVER ask the user for additional details. Always provide a complete answer using ALL available sources.\n"
            "- READ each KB article and Slack thread and extract ONLY the steps that are directly relevant to the user's question. Skip KB sections that cover topics not mentioned in the query.\n"
            "- MULTIPLE KB ARTICLES FOR SAME SYMPTOM: When multiple KB articles address the same issue with DIFFERENT solutions:\n"
            "  1. Analyze the KB article titles and content to identify the PRIMARY/MOST COMMON solution\n"
            "  2. Provide ONLY the most relevant KB article's solution\n"
            "  3. DO NOT combine multiple different troubleshooting approaches in one answer\n"
            "  4. If unsure which is primary, choose the KB that mentions 'first step' or 'before' or appears simpler\n"
            "- REWRITE the steps in clear, easy-to-follow language that a non-technical user can act on immediately. Do NOT copy-paste raw KB text. Do NOT say 'refer to the article'.\n"
            "- LENGTH: Write at a medium-to-slightly-above-medium level. Keep ALL relevant steps and specific details (file paths, commands, values, URLs) — do NOT remove any information. Combine closely related sequential actions into a single bullet using arrows (→). Avoid repeating the same instruction twice. No long introductory sentences — go straight to the action.\n"
            "  BAD (too long): '• Open Task Manager by going to the Start menu. • Go to the Processes tab. • Identify any application consuming high CPU. • Right-click and select End Task.'\n"
            "  GOOD (concise): '• Open Task Manager → Processes tab → identify high CPU apps → right-click → End Task → relaunch.'\n"
            "  Apply this pattern throughout: collapse sequential same-topic steps into one arrow-chained bullet wherever it makes sense without losing detail.\n"
            "- Structure the answer with numbered sections (1., 2., 3.) with nested sub-bullets (a., b., c. or •) for each step.\n"
            "- If multiple KB articles are relevant, merge only the sections that match the user's question — do NOT include unrelated sections from those articles.\n"
            "- Include every specific detail for the relevant steps: exact file names, folder paths, parameter values, button/menu labels, URLs. Do not generalize or omit specifics.\n"
            "- PRESERVE EXACT CASE for all technical content: commands, JVM flags, file paths, config keys, and parameter values must retain their original casing exactly as they appear in the KB (e.g., AddVMOption -Xms512m -Xmx2048m, eclipse.ini, product.conf). Never lowercase technical terms.\n"
            "- SOURCE FORMAT (REQUIRED): On its own line at the very end, output EXACTLY:\n"
            "  Sources: [KB:1] [KB:2] [Slack:1] [Slack:2]\n"
            "  Use [KB:N] for Knowledge Base articles and [Slack:N] for Slack threads. Do NOT output URLs directly — the system converts these tags to proper links automatically.\n"
        )
    else:
        logger.info(f"Using default general system_instruction for channel {channel_id}")
        return (
            f"You are a senior engineer embedded in the *#{channel_name}* Slack channel.\n"
            "You have read every message and know this channel's history.\n"
            "Answer like a sharp, helpful teammate — not a chatbot.\n\n"
            "RULES:\n"
            "- MULTI-SOURCE: Use ALL available sources (Slack history + KB articles + other sources)\n"
            "- PRIORITY: Check Slack history FIRST, then KB articles\n"
            "- Lead with the fix, not the problem description.\n"
            "- Max 3 numbered solutions, most likely first.\n"
            "- Use *bold* for key actions. No markdown headers. Under 150 words.\n"
            "- SOURCE FORMAT (REQUIRED): On its own line at the very end, output exactly:\n"
            "  Sources: [KB:1] [KB:2] [Slack:1] [Slack:2]\n"
            "  Use [KB:N] for KB articles and [Slack:N] for Slack threads. The system converts these to proper links.\n"
        )

def generate_channel_ai_response(channel_id: str, query_text: str, session_id: str = "default") -> str:
    """
    Generate an AI answer using channel history + knowledge + Gemini directly.
    Maintains per-session conversation memory (up to 10 turns, 30 min TTL).
    """
    if not _gemini_model:
        return "Gemini fallback is not available. Please configure the Dialogflow CX agent."

    all_messages, knowledge, channel_name = _get_channel_context(channel_id)
    messages = _filter_messages_by_relevance(all_messages, query_text)
    logger.info(f"Keyword filter: {len(all_messages)} total → {len(messages)} relevant for query '{query_text[:60]}'")
    prior_turns = _get_conversation_history(session_id)

    # --- Conversational memory expiry check ---
    # If the session has no history AND the thread is older than the TTL AND the
    # query looks like a follow-up (not a standalone question), tell the user
    # gracefully instead of giving a confusing context-free answer.
    if not prior_turns and session_id.startswith("channel-"):
        thread_age = _estimate_session_thread_age(session_id)
        if thread_age > _CONV_TTL_SECONDS and _is_likely_followup(query_text):
            return (
                "⏰ *Conversation memory has expired* — sessions reset after *30 minutes* of inactivity.\n\n"
                "I no longer have context from your earlier questions in this thread. "
                "To continue, please *re-state your full question* (or paste the key details) "
                "and I’ll pick right up from there!"
            )

    # Resolve the actual source channel_id for building links
    source_channel_id = _resolve_channel_id(channel_id)

    # Step 1 — keyword-rank then LLM-select the most relevant KB articles.
    # This is LLM call #1: cheap, fast, no channel-ID hardcoding.
    ranked_knowledge = _rank_knowledge_by_relevance(knowledge, query_text)
    useful_knowledge = [k for k in ranked_knowledge if k.get("content", "").strip()]
    has_kb = bool(useful_knowledge)

    if has_kb and len(useful_knowledge) > 3:
        # LLM selects the best subset from the keyword-ranked list
        selected_knowledge = _llm_select_relevant_knowledge(useful_knowledge, query_text)
    else:
        selected_knowledge = useful_knowledge

    # Step 2 — Get system instruction dynamically (hardcoded > custom DB > default).
    # Uses new helper function that supports AI-generated channel-specific instructions.
    system_instruction = _get_channel_system_instruction(channel_id, has_kb, channel_name)

    # Step 3 — Build the data context prompt.
    parts = []

    if prior_turns:
        parts.append("=== CONVERSATION SO FAR ===")
        for turn in prior_turns:
            parts.append(f"User: {turn['user']}")
            parts.append(f"Assistant: {turn['assistant']}")
        parts.append("")

    # KB section — feed full content of selected articles (no URL truncation risk because
    # Gemini will only output [KB:N] tags for sources, not raw URLs).
    kb_lookup = {}
    if selected_knowledge:
        parts.append("=== KNOWLEDGE BASE ARTICLES (use these as your primary source) ===")
        kb_index = 0
        for k in selected_knowledge[:5]:
            label = k.get("title") or k.get("type", "info")
            source_url = k.get("source_url", "")
            kb_number = k.get("kb_number", "")
            # Full content up to 5000 chars — BQ already caps at this limit
            content_body = k["content"][:5000]
            if k.get("type") == "kb_article":
                kb_index += 1
                tag = f"KB:{kb_index}"
                kb_lookup[tag] = (source_url, label, kb_number)
                parts.append(
                    f"[{tag}] Title: {label}\n"
                    f"  Content:\n{content_body}"
                )
            elif source_url:
                parts.append(f"[custom] Title: {label}\n  Content:\n{content_body}")
            else:
                parts.append(f"[custom] Title: {label}\n  Content:\n{content_body}")
        parts.append("")

    # Channel messages — Skip Slack for Citrix channel (KB-only)
    CITRIX_CHANNEL_ID = "C0218861E3C"
    slack_lookup = {}
    if messages and channel_id != CITRIX_CHANNEL_ID:
        parts.append(
            "IMPORTANT: When answering from Slack messages below, cite them using [Slack:N] tags. "
            "Use BOTH Slack data AND KB articles to provide comprehensive answers."
        )
        parts.append(f"=== SLACK CHANNEL HISTORY ({len(messages)} messages) ===")
        total_chars = 0
        slack_index = 0
        for msg in reversed(messages):
            msg_thread_ts = msg.get("thread_ts", "")
            thread_link = _build_slack_thread_link(source_channel_id, msg_thread_ts) if msg_thread_ts else ""
            if thread_link:
                slack_index += 1
                tag = f"Slack:{slack_index}"
                slack_lookup[tag] = thread_link
                line = f"[{tag}] [{msg['timestamp']}] User {msg['user_id']}: {msg['text']}"
            else:
                line = f"[{msg['timestamp']}] User {msg['user_id']}: {msg['text']}"

            if total_chars + len(line) > 80000:
                parts.append("... (older messages truncated)")
                break
            parts.append(line)
            total_chars += len(line)
        parts.append("")

    parts.append(f"=== CURRENT QUESTION ===\n{query_text}")
    context_prompt = "\n".join(parts)

    # LLM call #2 — response generation with system_instruction as hard rules.
    from vertexai.generative_models import GenerativeModel as _GeminiModel, GenerationConfig as _GConfig
    _responder = _GeminiModel("gemini-2.5-flash", system_instruction=system_instruction)
    response = _responder.generate_content(
        context_prompt,
        generation_config=_GConfig(max_output_tokens=8192),
    )
    # Safe text extraction — response.text raises if the response is blocked by safety filters
    try:
        answer_text = response.text
    except (ValueError, AttributeError) as _rt_err:
        logger.warning(f"response.text unavailable ({_rt_err}), assembling from parts")
        answer_text = ""
        if response.candidates:
            for _part in response.candidates[0].content.parts:
                if hasattr(_part, "text"):
                    answer_text += _part.text

    # --- Reflection Layer: Check if response is grounded ---
    reflection_prompt = f"""Check if this response properly answers the user's question using the provided sources.

User Question: {query_text}

Assistant Response: {answer_text}

Evaluate:
1. Does the response directly address the user's question?
2. Is the response grounded in the provided Slack/KB sources?
3. Are there any hallucinations or unsupported claims?

Respond with JSON only:
{{"is_grounded": true/false, "confidence": 0.0-1.0, "issue": "brief reason if not grounded"}}"""

    try:
        reflection = _gemini_model.generate_content(reflection_prompt)
        reflection_text = reflection.text.strip()
        import json
        import re
        json_match = re.search(r'\{[^{}]*\}', reflection_text, re.DOTALL)
        if json_match:
            reflection_data = json.loads(json_match.group())
            is_grounded = reflection_data.get("is_grounded", True)
            confidence = reflection_data.get("confidence", 1.0)

            logger.info(f"Reflection result: grounded={is_grounded}, confidence={confidence}")

            # If response is not grounded or low confidence, rewrite query and retry
            if not is_grounded or confidence < 0.6:
                logger.warning(f"Response not grounded (confidence={confidence}), rewriting query...")

                rewrite_prompt = f"""The user asked: "{query_text}"

The assistant's response was not well-grounded in the available sources.

Rewrite this question to be more specific and searchable, focusing on:
- Key technical terms
- Specific error messages or symptoms
- Product/feature names

Respond with ONLY the rewritten question, nothing else."""

                rewrite_response = _gemini_model.generate_content(rewrite_prompt)
                rewritten_query = rewrite_response.text.strip().strip('"')
                logger.info(f"Rewritten query: '{rewritten_query}'")

                # Retry with rewritten query
                parts_retry = []
                if prior_turns:
                    parts_retry.append("=== CONVERSATION SO FAR ===")
                    for turn in prior_turns:
                        parts_retry.append(f"User: {turn['user']}")
                        parts_retry.append(f"Assistant: {turn['assistant']}")
                    parts_retry.append("")

                if selected_knowledge:
                    parts_retry.append("=== KNOWLEDGE BASE ARTICLES ===")
                    for k in selected_knowledge[:5]:
                        kb_index_retry = list(kb_lookup.keys()).index(f"KB:{selected_knowledge.index(k)+1}") + 1 if kb_lookup else 0
                        if kb_index_retry > 0:
                            parts_retry.append(f"[KB:{kb_index_retry}] {k.get('title', 'Article')}\n{k['content'][:5000]}")
                    parts_retry.append("")

                if messages:
                    parts_retry.append(f"=== SLACK CHANNEL HISTORY ({len(messages)} messages) ===")
                    for msg in reversed(messages[:20]):
                        parts_retry.append(f"[{msg['timestamp']}] User {msg['user_id']}: {msg['text']}")
                    parts_retry.append("")

                parts_retry.append(f"=== CURRENT QUESTION (REFINED) ===\n{rewritten_query}")
                retry_context = "\n".join(parts_retry)

                retry_response = _responder.generate_content(
                    retry_context,
                    generation_config=_GConfig(max_output_tokens=8192),
                )
                try:
                    answer_text = retry_response.text
                    logger.info("Retry with rewritten query succeeded")
                except (ValueError, AttributeError):
                    logger.warning("Retry failed, keeping original response")
    except Exception as e:
        logger.warning(f"Reflection layer failed: {e}, proceeding with original response")

    # --- Post-processing: convert [KB:N] and [Slack:N] tags → proper links ---
    if kb_lookup:
        for tag, (url, title, kb_num) in kb_lookup.items():
            display_title = title.split(":", 1)[1].strip() if ":" in title else title
            proper_link = f"<{url}|KB: {display_title}>"
            answer_text = answer_text.replace(f"[{tag}]", proper_link)
            answer_text = answer_text.replace(f"<{tag}>", proper_link)

    if slack_lookup:
        for tag, thread_link in slack_lookup.items():
            slack_link = f"<{thread_link}|Slack Thread>"
            answer_text = answer_text.replace(f"[{tag}]", slack_link)
            answer_text = answer_text.replace(f"<{tag}>", slack_link)

    # --- Ensure Sources line exists ---
    if "Sources:" not in answer_text:
        sources_parts = []
        if kb_lookup:
            sources_parts.append("KB Articles")
        if slack_lookup:
            sources_parts.append("Slack Threads")
        if sources_parts:
            answer_text = answer_text.rstrip() + f"\n\nSources: {', '.join(sources_parts)}"

    return answer_text
# ── Channel AI API Endpoints ─────────────────────────────────────────────────

@app.route('/channel-chat', methods=['POST'])
def channel_chat_endpoint():
    """
    API endpoint for the Slack bot to query the Channel AI assistant.
    Routes through Dialogflow CX agent (primary) or direct Gemini (fallback).

    Expected JSON:
    {
        "query": "How do I get access to Gemini Enterprise?",
        "channel_id": "C0AE1G97QCV",
        "session_id": "slack-U12345-ts"  (optional)
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
        response_source = ""

        # DFCX default fallback phrases — treat these as "no useful answer" and skip to Gemini
        DFCX_FALLBACK_PHRASES = {
            "sorry something went wrong, can you repeat?",
            "i didn't understand that. can you rephrase?",
            "i'm not sure i understand. could you try again?",
            "sorry, i didn't get that.",
            "sorry, i didn't understand.",
            "can you rephrase that?",
        }

        def is_dfcx_fallback(text):
            return text.strip().lower() in DFCX_FALLBACK_PHRASES

        # Primary path: Dialogflow CX agent (only if USE_DFCX is True and agent is configured)
        if USE_DFCX and CHANNEL_DFCX_AGENT_ID and CHANNEL_DFCX_AGENT_ID != "REPLACE_WITH_PROD_AGENT_ID":
            try:
                dfcx_session = f"{_resolve_channel_id(channel_id)}-{session_id}"
                answer = detect_intent_channel(dfcx_session, query_text)
                if answer and not is_dfcx_fallback(answer):
                    response_source = "dialogflow_cx"
                elif answer and is_dfcx_fallback(answer):
                    logger.warning(
                        f"DFCX returned fallback phrase '{answer}', treating as empty and falling back to Gemini"
                    )
                    answer = ""  # force Gemini fallback below
            except Exception as dfcx_err:
                err_str = str(dfcx_err)
                if "Token limit exceeded" in err_str or "FAILED_PRECONDITION" in err_str:
                    fresh = f"{dfcx_session}-{uuid.uuid4().hex[:8]}"
                    logger.warning(f"Session hit token limit, retrying with {fresh}")
                    answer = detect_intent_channel(fresh, query_text)
                    if answer and not is_dfcx_fallback(answer):
                        response_source = "dialogflow_cx_retry"
                    elif answer and is_dfcx_fallback(answer):
                        logger.warning(f"DFCX retry also returned fallback phrase, falling back to Gemini")
                        answer = ""
                else:
                    logger.warning(f"DFCX agent failed, falling back to Gemini: {dfcx_err}")
                    answer = generate_channel_ai_response(channel_id, query_text, session_id)
                    if answer:
                        response_source = "gemini_fallback_after_dfcx_error"

            # DFCX returned empty or a fallback phrase — use Gemini
            if not answer:
                logger.warning(
                    "DFCX returned empty/fallback response, falling back to Gemini direct generation"
                )
                answer = generate_channel_ai_response(channel_id, query_text, session_id)
                if answer:
                    response_source = "gemini_fallback_after_empty_dfcx"
        else:
            logger.info("DFCX agent not configured, using direct Gemini fallback")
            answer = generate_channel_ai_response(channel_id, query_text, session_id)
            if answer:
                response_source = "gemini_direct"

        if not answer:
            logger.warning(
                f"Both DFCX and Gemini produced empty response for channel={channel_id}, session={session_id}"
            )
            answer = "I'm sorry, I couldn't generate a response. Please try again."
            response_source = "empty_response"

        logger.info(
            f"Channel chat response generated: source={response_source or 'unknown'}, channel={channel_id}, session={session_id}"
        )

        # Persist conversation turn to both in-memory store and database
        _save_conversation_turn(session_id, channel_id, query_text, answer, response_source)

        return jsonify({"status": "success", "response": answer, "source": response_source}), 200

    except Exception as e:
        logger.error(f"Error in channel_chat: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


def _get_session_conversations_from_db(session_id: str, channel_id: str) -> List[Dict]:
    """Retrieve all conversations for a session from database (channelassistantai)."""
    connection = None
    try:
        connection = _get_channel_ai_db()
        cursor = connection.cursor(cursor_factory=RealDictCursor)

        query = """
        SELECT user_query, bot_response, response_source, created_at
        FROM conversation_history
        WHERE session_id = %s AND channel_id = %s
        ORDER BY created_at ASC
        """

        cursor.execute(query, (session_id, channel_id))
        results = cursor.fetchall()
        connection.close()

        conversations = [
            {
                "user_query": row["user_query"],
                "bot_response": row["bot_response"],
                "response_source": row["response_source"],
                "timestamp": row["created_at"].isoformat() if row["created_at"] else None
            }
            for row in results
        ]

        logger.info(f"Retrieved {len(conversations)} conversations from DB for session: {session_id}")
        return conversations

    except Exception as e:
        logger.error(f"Error retrieving conversations from DB: {e}")
        return []


def _generate_session_summary(conversations: List[Dict], session_id: str, channel_id: str) -> Dict:
    """
    Generate a structured summary of conversations using Gemini.

    Returns:
    {
        "summary": "...",
        "structure": {
            "queries": [...],
            "recommendations": [...],
            "action_items": [...],
            "topics": [...]
        }
    }
    """
    if not conversations:
        return {
            "summary": "No conversations found for this session.",
            "structure": {
                "queries": [],
                "recommendations": [],
                "action_items": [],
                "topics": []
            }
        }

    try:
        if not _gemini_model:
            raise ValueError("Gemini model not configured")

        # Build conversation text
        conversation_text = "=== SESSION CONVERSATIONS ===\n"
        for idx, conv in enumerate(conversations, 1):
            conversation_text += f"\n[Turn {idx}]\n"
            conversation_text += f"User: {conv['user_query']}\n"
            conversation_text += f"Assistant: {conv['bot_response']}\n"
            if conv.get('response_source'):
                conversation_text += f"Source: {conv['response_source']}\n"

        # Create summarization prompt
        summary_prompt = f"""{conversation_text}

Based on the conversation above, provide a comprehensive summary in the following JSON format:

{{
    "summary": "A 2-3 sentence overview of the entire session and what was discussed",
    "structure": {{
        "queries": [
            "List of all unique questions/queries asked by the user"
        ],
        "recommendations": [
            "List of recommendations or solutions provided"
        ],
        "action_items": [
            "List of action items the user should take"
        ],
        "topics": [
            "List of main topics covered in the conversation"
        ]
    }}
}}

Ensure all fields are arrays of strings. Be concise but comprehensive."""

        # Generate summary using Gemini
        response = _gemini_model.generate_content(
            summary_prompt,
            generation_config=GenerationConfig(max_output_tokens=2048)
        )

        try:
            summary_text = response.text
            # Extract JSON from response
            import json
            import re

            json_match = re.search(r'\{.*\}', summary_text, re.DOTALL)
            if json_match:
                summary_data = json.loads(json_match.group())
                logger.info(f"Generated summary for session: {session_id}")
                return summary_data
            else:
                # Fallback: return raw summary
                return {
                    "summary": summary_text,
                    "structure": {
                        "queries": [],
                        "recommendations": [],
                        "action_items": [],
                        "topics": []
                    }
                }
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Could not parse JSON from Gemini response: {e}")
            return {
                "summary": summary_text if response.text else "Summary generation failed",
                "structure": {
                    "queries": [],
                    "recommendations": [],
                    "action_items": [],
                    "topics": []
                }
            }

    except Exception as e:
        logger.error(f"Error generating session summary: {e}")
        raise


@app.route('/session-summary', methods=['POST'])
def session_summary_endpoint():
    """
    Generate a comprehensive summary of a session's conversations.

    Supports two options:

    OPTION 1 - Query from Database:
    {
        "session_id": "test-123",
        "channel_id": "C0C0W29P8GG"
    }

    OPTION 2 - Direct Conversation Data:
    {
        "conversations": [
            {
                "user_query": "What is the 80/20 Rule?",
                "bot_response": "The 80/20 Rule states...",
                "response_source": "gemini_direct",
                "timestamp": "2026-09-09T06:04:58Z"
            },
            ...
        ],
        "session_id": "test-123",
        "channel_id": "C0C0W29P8GG"
    }

    Returns:
    {
        "status": "success",
        "session_id": "test-123",
        "channel_id": "C0C0W29P8GG",
        "conversation_count": 5,
        "summary": "...",
        "structure": {
            "queries": [...],
            "recommendations": [...],
            "action_items": [...],
            "topics": [...]
        },
        "timestamp": "2026-09-09T06:04:58Z"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        session_id = data.get("session_id", "").strip()
        channel_id = data.get("channel_id", "").strip()
        conversations = data.get("conversations")

        # Validate required fields
        if not session_id:
            return jsonify({"status": "error", "message": "Missing required field: 'session_id'"}), 400
        if not channel_id:
            return jsonify({"status": "error", "message": "Missing required field: 'channel_id'"}), 400

        # Determine which option is being used
        if conversations:
            # OPTION 2: Direct conversation data provided
            if not isinstance(conversations, list) or len(conversations) == 0:
                return jsonify({
                    "status": "error",
                    "message": "Invalid 'conversations' field - must be non-empty array"
                }), 400

            logger.info(f"Option 2 - Generating summary from {len(conversations)} conversations provided in payload")
            conversation_list = conversations
        else:
            # OPTION 1: Query from database
            logger.info(f"Option 1 - Querying database for session: {session_id}, channel: {channel_id}")
            conversation_list = _get_session_conversations_from_db(session_id, channel_id)

            if not conversation_list:
                return jsonify({
                    "status": "warning",
                    "message": f"No conversations found in database for session '{session_id}'",
                    "session_id": session_id,
                    "channel_id": channel_id,
                    "conversation_count": 0
                }), 200

        # Generate summary
        summary_data = _generate_session_summary(conversation_list, session_id, channel_id)

        # Build response
        response = {
            "status": "success",
            "session_id": session_id,
            "channel_id": channel_id,
            "conversation_count": len(conversation_list),
            "summary": summary_data.get("summary"),
            "structure": summary_data.get("structure"),
            "timestamp": datetime.now().isoformat()
        }

        logger.info(f"Session summary generated successfully - session: {session_id}, conversations: {len(conversation_list)}")
        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Error in session_summary: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/assess-query-confidence', methods=['POST'])
def assess_query_confidence():
    """
    Score how specific and actionable a user's IT support query is (0-100).
    Queries scoring below 50 are considered too vague for KB retrieval.

    Expected JSON: {"query": "string"}
    Returns: {"score": int, "reason": "string"}
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"score": 75, "reason": "No data provided"}), 200

        query_text = (data.get("query") or "").strip()
        if not query_text:
            return jsonify({"score": 0, "reason": "Empty query"}), 200

        if not _gemini_model:
            logger.warning("assess_query_confidence: Gemini unavailable, returning default score 75")
            return jsonify({"score": 75, "reason": "Gemini unavailable"}), 200

        prompt = (
            "You are evaluating a user's IT support query for Citrix/Virtual Desktop issues.\n"
            "Score how specific and actionable this query is on a scale of 0 to 100.\n\n"
            "HIGH score (>=50): query has enough detail to search for relevant KB articles.\n"
            "Examples of HIGH confidence:\n"
            '- "My Citrix VDI won\'t launch, I see a black screen"\n'
            '- "How do I reset my Citrix workspace app?"\n'
            '- "VDI is slow and lagging when I connect from home"\n'
            '- "Can\'t connect to mydcloud.verizon.com, getting authentication error"\n'
            '- "how to install software on my VDI"\n\n'
            "LOW score (<50): query is too vague, generic, or lacks context to find relevant help.\n"
            "Examples of LOW confidence:\n"
            '- "help"\n'
            '- "it\'s not working"\n'
            '- "I have an issue"\n'
            '- "can you help me"\n'
            '- "problem with my computer"\n'
            '- "something is wrong"\n\n'
            f'User query: "{query_text}"\n\n'
            "Respond ONLY with valid JSON in this exact format (no extra text):\n"
            '{"score": <integer 0-100>, "reason": "<10 words or less>"}'
        )

        response = _gemini_model.generate_content(prompt)
        result_text = response.text.strip()

        json_match = re.search(r'\{[^{}]*\}', result_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            score = max(0, min(100, int(result.get("score", 75))))
            reason = str(result.get("reason", ""))
            logger.info(f"Query confidence: score={score}, reason='{reason}', query='{query_text[:60]}'")
            return jsonify({"score": score, "reason": reason}), 200

        logger.warning(f"Could not parse Gemini confidence response: {result_text[:200]}")
        return jsonify({"score": 75, "reason": "Parse error"}), 200

    except Exception as e:
        logger.error(f"Error in assess_query_confidence: {e}", exc_info=True)
        return jsonify({"score": 75, "reason": "Error, defaulting to pass"}), 200


@app.route('/channel-data-webhook', methods=['POST'])
def channel_data_webhook():
    """
    Dialogflow CX OpenAPI Tool endpoint.
    Called by the Channel AI agent's Tool to fetch channel data from BigQuery.

    Expected JSON (from Dialogflow CX Tool):
    {
        "channel_id": "C0AE1G97QCV",
        "search_query": "access denied error"  (optional)
    }
    """
    logger.info("Channel data webhook called")

    data = request.get_json(silent=True) or {}
    channel_id = data.get("channel_id", "").strip()

    if not channel_id:
        return jsonify({"error": "Missing 'channel_id'", "messages": [], "knowledge": []}), 400

    messages, knowledge, channel_name = _get_channel_context(channel_id)

    # Keyword filtering
    search_query = data.get("search_query", "").strip().lower()
    if search_query:
        keywords = [w for w in search_query.split() if len(w) > 2]
        if keywords:
            filtered = [m for m in messages if any(kw in m["text"].lower() for kw in keywords)]
            messages = filtered if filtered else messages

    # Token budget limits
    MAX_MSG_CHARS = 20000
    MAX_MSGS = 30
    MAX_MSG_TEXT = 400

    truncated_messages = []
    total_chars = 0
    thread_link_scores: dict = {}
    search_keywords = [w for w in search_query.split() if len(w) > 2] if search_query else []

    for msg in messages[:MAX_MSGS]:
        text = msg["text"][:MAX_MSG_TEXT]
        entry = {
            "text": text,
            "timestamp": msg["timestamp"],
            "user_id": msg["user_id"],
            "is_reply": msg.get("is_thread_reply", False),
        }
        msg_channel_id = msg.get("channel_id", channel_id)
        msg_thread_ts = msg.get("thread_ts", "")
        if msg_thread_ts:
            link = _build_slack_thread_link(msg_channel_id, msg_thread_ts)
            if link:
                text_lower = msg["text"].lower()
                matches = sum(1 for kw in search_keywords if kw in text_lower) if search_keywords else 0
                if msg.get("is_thread_reply", False):
                    matches += 1
                thread_link_scores[link] = thread_link_scores.get(link, 0) + max(matches, 1)

        entry_size = len(text) + len(msg["timestamp"]) + len(msg["user_id"]) + 40
        if total_chars + entry_size > MAX_MSG_CHARS:
            break
        truncated_messages.append(entry)
        total_chars += entry_size

    ranked_source_links = sorted(thread_link_scores.keys(),
                                  key=lambda lnk: thread_link_scores[lnk],
                                  reverse=True)[:5]

    truncated_knowledge = []
    for k in knowledge[:10]:
        truncated_knowledge.append({
            "type": k["type"],
            "title": k.get("title", ""),
            "content": k["content"][:500],
            "source_url": k.get("source_url", ""),
        })

    return jsonify({
        "channel_name": channel_name,
        "messages_count": len(truncated_messages),
        "knowledge_count": len(truncated_knowledge),
        "response_instructions": (
            "VOICE: Answer like a senior engineer — confident, direct, no filler. "
            "RESPOND in under 150 words. LEAD with the fix. "
            "Max 3 numbered solutions, most likely first. Use *bold* for key actions. "
            "End with a clear next step. "
            "SOURCE ATTRIBUTION — end with one 'Source:' line: "
            "• Channel messages → 'Source: <most relevant link from source_thread_links>' "
            "• Knowledge base → 'Source: Knowledge Base — <source_url>' "
            "• General knowledge → 'Source: Gemini AI' "
            "IMPORTANT: Always provide a helpful answer. NEVER say 'no data found'."
        ),
        "source_thread_links": ranked_source_links,
        "messages": truncated_messages,
        "knowledge": truncated_knowledge,
    }), 200


@app.route('/channel-check', methods=['POST'])
def channel_check():
    """
    Check if a channel ID exists in the Slack BigQuery table.
    Used by the React UI to verify channel onboarding status.

    Expected JSON:
    {
        "channel_id": "C0AE1G97QCV"
    }
    """
    try:
        data = request.get_json()
        if not data or not data.get("channel_id"):
            return jsonify({"status": "error", "message": "Missing 'channel_id'"}), 400

        channel_id = data["channel_id"].strip()
        bq_client = bigquery.Client(project=BQ_PROJECT_PROD)

        query = f"""
            SELECT channel_normalname, COUNT(*) as msg_count
            FROM `{SLACK_DATA_TABLE}`
            WHERE channel_name = @channel_id
            GROUP BY channel_normalname
            LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("channel_id", "STRING", channel_id),
            ]
        )
        rows = list(bq_client.query(query, job_config=job_config).result())

        if rows:
            return jsonify({
                "status": "success",
                "exists": True,
                "channel_name": rows[0].channel_normalname,
                "message_count": rows[0].msg_count,
                "message": f"Channel found with {rows[0].msg_count} messages. Ready to integrate."
            }), 200
        else:
            return jsonify({
                "status": "success",
                "exists": False,
                "channel_name": None,
                "message_count": 0,
                "message": ("Channel not found in Slack data. "
                           "Please contact anand.anathur.elangovan@verizon.com to fetch "
                           "historical data for this channel.")
            }), 200

    except Exception as e:
        logger.error(f"Error in channel_check: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/channel-onboard', methods=['POST'])
def channel_onboard():
    """
    Onboard a channel for Channel AI Bot.
    Registers channel in the system and optionally sets up mapping.

    Expected JSON:
    {
        "channel_id": "C0AE1G97QCV",
        "channel_name": "help-gemini-enterprise",
        "test_channel_id": "C0TESTCHAN1" (optional - for mapping test→source)
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        channel_id = data.get("channel_id", "").strip()
        channel_name = data.get("channel_name", "").strip()
        test_channel_id = data.get("test_channel_id", "").strip()

        if not channel_id:
            return jsonify({"status": "error", "message": "Missing 'channel_id'"}), 400

        # Save to channels table in channelassistantai database
        try:
            conn = _get_channel_ai_db()
            cur = conn.cursor()

            # Insert into channels table with ON CONFLICT to handle existing channels
            cur.execute("""
                INSERT INTO channels (
                    channel_id, channel_name, is_active,
                    has_slack_data, bot_invited, onboarded_by
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (channel_id)
                DO UPDATE SET
                    channel_name = EXCLUDED.channel_name,
                    is_active = EXCLUDED.is_active,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                channel_id,
                channel_name or channel_id,
                True,  # is_active
                False,  # has_slack_data - will be updated when data is fetched
                True,   # bot_invited - assuming bot is being invited during onboarding
                'api'   # onboarded_by
            ))

            conn.commit()
            conn.close()

            logger.info(f"Channel {channel_id} saved to channels table successfully")

        except Exception as db_error:
            logger.error(f"Error saving to channels table: {db_error}")
            return jsonify({
                "status": "error",
                "message": f"Failed to save channel to database: {str(db_error)}"
            }), 500

        # Add to channel mapping if test channel provided
        if test_channel_id:
            CHANNEL_DATA_MAP[test_channel_id] = channel_id
            logger.info(f"Channel mapping added: {test_channel_id} → {channel_id}")

            # Also save mapping to database
            try:
                conn = _get_channel_ai_db()
                cur = conn.cursor()

                cur.execute("""
                    INSERT INTO channel_mappings (
                        test_channel_id, source_channel_id,
                        test_channel_name, source_channel_name,
                        created_by, is_active
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (test_channel_id)
                    DO UPDATE SET
                        source_channel_id = EXCLUDED.source_channel_id,
                        source_channel_name = EXCLUDED.source_channel_name,
                        created_at = CURRENT_TIMESTAMP
                """, (
                    test_channel_id,
                    channel_id,
                    f"test-{channel_name}" if channel_name else test_channel_id,
                    channel_name or channel_id,
                    'api',
                    True
                ))

                conn.commit()
                conn.close()

                logger.info(f"Channel mapping saved to database: {test_channel_id} → {channel_id}")

            except Exception as mapping_error:
                logger.warning(f"Error saving channel mapping to database: {mapping_error}")
                # Don't fail the request if mapping save fails

        return jsonify({
            "status": "success",
            "message": f"Channel {channel_name or channel_id} onboarded successfully and saved to database.",
            "channel_id": channel_id,
            "mapping": {test_channel_id: channel_id} if test_channel_id else None,
            "next_steps": [
                "Generate system instruction via /generate-system-instruction endpoint",
                "Upload knowledge articles via /channel-knowledge endpoint (if needed)",
                "Test by mentioning the bot in the channel"
            ]
        }), 200

    except Exception as e:
        logger.error(f"Error in channel_onboard: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/channel-mappings', methods=['GET'])
def get_channel_mappings():
    """
    Get complete channel mappings for Slack bot routing.

    Returns a dictionary where:
    - Test channels map to their source channels (e.g., C0B7D0YKQS2 → C05CHPFT57A)
    - Direct channels map to themselves (e.g., C0AE1G97QCV → C0AE1G97QCV)

    Only returns active channels and active mappings.

    Returns:
    {
        "status": "success",
        "mappings": {
            "C0B5KJXMDC3": "C0AE1G97QCV",  // test → source
            "C05CHPFT57A": "C05CHPFT57A",   // direct (self)
            "C0B7D0YKQS2": "C05CHPFT57A",   // test → source
            "C0BT70ZTR27": "C0BT70ZTR27"    // direct (self)
        },
        "total_channels": 4,
        "timestamp": "2026-09-04T03:00:00"
    }
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])

        conn = _get_channel_ai_db()
        cur = conn.cursor()

        # Step 1: Get test channel → source channel mappings
        cur.execute("""
            SELECT test_channel_id, source_channel_id
            FROM channel_mappings
            WHERE is_active = true
        """)
        mapping_rows = cur.fetchall()

        # Step 2: Get all active channels (for direct/self mappings)
        cur.execute("""
            SELECT channel_id
            FROM channels
            WHERE is_active = true AND bot_invited = true
        """)
        channel_rows = cur.fetchall()

        conn.close()

        # Build the complete mapping dictionary
        mappings = {}

        # Add test → source mappings
        for row in mapping_rows:
            test_channel = row[0]
            source_channel = row[1]
            mappings[test_channel] = source_channel
            logger.info(f"Mapping: {test_channel} → {source_channel} (test → source)")

        # Add direct channels (channel → self)
        for row in channel_rows:
            channel_id = row[0]
            # Only add if not already in mappings (test channels already added above)
            if channel_id not in mappings:
                mappings[channel_id] = channel_id
                logger.info(f"Mapping: {channel_id} → {channel_id} (direct/self)")

        logger.info(f"Total channel mappings: {len(mappings)}")

        return jsonify({
            "status": "success",
            "mappings": mappings,
            "total_channels": len(mappings),
            "timestamp": datetime.now().isoformat()
        }), 200

    except Exception as e:
        logger.error(f"Error in get_channel_mappings: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/channel-mapping', methods=['POST', 'PUT', 'DELETE'])
def manage_channel_mapping():
    """
    Manage channel mappings.

    POST/PUT:
    - If only channel_id provided: Direct channel (maps to itself)
    - If channel_id + source_channel_id provided: Test channel (maps to source)

    {
        "channel_id": "C0BHJL3Q5S8",
        "source_channel_id": "C05CHPFT57A",  // optional - if not provided, uses channel_id
        "channel_name": "my-channel"  // optional
    }

    DELETE:
    {
        "channel_id": "C0TESTCHAN"
    }
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])

        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        channel_id = data.get("channel_id", "").strip()
        if not channel_id:
            return jsonify({"status": "error", "message": "Missing 'channel_id'"}), 400

        conn = _get_channel_ai_db()
        cur = conn.cursor()

        # DELETE
        if request.method == 'DELETE':
            # Try to delete from both tables
            cur.execute("DELETE FROM channel_mappings WHERE test_channel_id = %s", (channel_id,))
            cur.execute("DELETE FROM channels WHERE channel_id = %s", (channel_id,))
            conn.commit()
            conn.close()

            return jsonify({
                "status": "success",
                "message": f"Channel {channel_id} removed"
            }), 200

        # POST/PUT
        source_channel_id = data.get("source_channel_id", "").strip()
        channel_name = data.get("channel_name", channel_id).strip()

        # If no source_channel_id or same as channel_id -> Direct channel
        if not source_channel_id or source_channel_id == channel_id:
            cur.execute("""
                INSERT INTO channels (channel_id, channel_name, is_active, has_slack_data, bot_invited, onboarded_by)
                VALUES (%s, %s, true, true, true, 'api')
                ON CONFLICT (channel_id) DO UPDATE SET
                    channel_name = EXCLUDED.channel_name,
                    is_active = true,
                    bot_invited = true,
                    updated_at = CURRENT_TIMESTAMP
            """, (channel_id, channel_name))
            conn.commit()
            conn.close()

            return jsonify({
                "status": "success",
                "message": f"Direct channel added: {channel_id} → {channel_id}",
                "mapping": {"channel_id": channel_id, "source_channel_id": channel_id}
            }), 200

        # Test channel -> source channel mapping
        cur.execute("""
            INSERT INTO channel_mappings (
                test_channel_id, source_channel_id, test_channel_name,
                source_channel_name, created_by, is_active
            ) VALUES (%s, %s, %s, %s, 'api', true)
            ON CONFLICT (test_channel_id) DO UPDATE SET
                source_channel_id = EXCLUDED.source_channel_id,
                test_channel_name = EXCLUDED.test_channel_name,
                is_active = true,
                created_at = CURRENT_TIMESTAMP
        """, (channel_id, source_channel_id, channel_name, f"source-{source_channel_id}"))
        conn.commit()
        conn.close()

        return jsonify({
            "status": "success",
            "message": f"Test channel mapping: {channel_id} → {source_channel_id}",
            "mapping": {"channel_id": channel_id, "source_channel_id": source_channel_id}
        }), 200

    except Exception as e:
        logger.error(f"Error in manage_channel_mapping: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/generate-system-instruction', methods=['POST'])
def generate_system_instruction():
    """
    Generate a perfect system_instruction for a Slack channel using AI.
    Uses Gemini to create channel-specific instructions that align with Slack best practices.

    Expected JSON:
    {
        "channel_id": "C0AE1G97QCV",
        "channel_name": "help-network-issues",
        "channel_description": "Support channel for network connectivity and VPN issues. Users report connection problems, slow speeds, and authentication failures.",
        "channel_instruction_text": "Focus on quick troubleshooting steps. Users are often remote workers needing immediate help. Prioritize common fixes before escalation.",
        "has_kb": false,  (optional - whether channel has KB articles, defaults to false)
        "save_to_db": true  (optional - whether to save to database, defaults to true)
    }

    Returns:
    {
        "status": "success",
        "channel_id": "C0AE1G97QCV",
        "channel_name": "help-network-issues",
        "generated_system_instruction": "You are a...",
        "saved_to_db": true,
        "preview": "First 200 chars of instruction..."
    }
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])

        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        # Extract parameters
        channel_id = data.get("channel_id", "").strip()
        channel_name = data.get("channel_name", "").strip()
        channel_description = data.get("channel_description", "").strip()
        channel_instruction_text = data.get("channel_instruction_text", "").strip()
        has_kb = data.get("has_kb", False)
        save_to_db = data.get("save_to_db", True)

        # Validate required fields
        if not channel_id:
            return jsonify({"status": "error", "message": "Missing required field: 'channel_id'"}), 400
        if not channel_name:
            return jsonify({"status": "error", "message": "Missing required field: 'channel_name'"}), 400
        if not channel_description:
            return jsonify({"status": "error", "message": "Missing required field: 'channel_description'"}), 400

        logger.info(f"Generating system_instruction for channel {channel_id} ({channel_name})")

        # Check if Gemini model is available
        if not _gemini_model:
            return jsonify({
                "status": "error",
                "message": "Gemini AI model not available. Cannot generate system instruction."
            }), 500

        # Build the AI prompt for generating system instruction
        generator_prompt = f"""You are an expert AI prompt engineer specializing in creating system instructions for Slack channel support bots.

Your task: Generate a perfect system_instruction for a Slack channel bot based on the provided context.

CHANNEL CONTEXT:
- Channel Name: #{channel_name}
- Channel ID: {channel_id}
- Description: {channel_description}
- Special Instructions: {channel_instruction_text if channel_instruction_text else "None provided"}
- Has Knowledge Base: {'Yes - channel has KB articles for reference' if has_kb else 'No - general conversation channel'}

REQUIREMENTS FOR THE SYSTEM INSTRUCTION:

1. OPENING LINE:
   - Start with "You are a [role/persona] for the *#{channel_name}* Slack channel."
   - Role should match the channel purpose (e.g., "technical support assistant", "senior engineer", "help desk specialist")

2. STRUCTURE:
   - Use "CRITICAL RULES:" or "RULES:" section header
   - If has_kb=True: Focus on KB-based support with structured, step-by-step answers
   - If has_kb=False: Focus on conversational help using channel history and general knowledge
   - Include 5-8 key rules that define bot behavior

3. TONE & STYLE:
   - Professional but friendly
   - Direct and actionable
   - No corporate jargon or robotic language
   - Match Slack's conversational style

4. CONTENT GUIDELINES:
   {"- Answer using Knowledge Base articles as primary source" if has_kb else "- Answer using channel history and general knowledge"}
   {"- Extract and rewrite KB steps in clear, user-friendly language" if has_kb else "- Be conversational like a helpful teammate"}
   - Never ask for more details - provide complete answers
   - Include specific technical details when relevant
   {"- Structure answers with numbered sections and sub-bullets (8-10 detailed steps)" if has_kb else "- Provide 5-7 actionable tips with practical examples (up to 600 words)"}
   - Use *bold* for key actions and important terms

5. SOURCE ATTRIBUTION (REQUIRED):
   - Must end with source citation rules
   {"- Use [KB:N] tags for Knowledge Base articles" if has_kb else "- Cite channel history or thread links"}
   - Always attribute answers to their source

6. SPECIAL CHANNEL CONTEXT:
   - Incorporate the channel description naturally into the instructions
   - If special instructions are provided, weave them into the rules
   - Make the bot sound like it belongs specifically in THIS channel

EXAMPLE OUTPUT FORMAT (adapt to this channel's needs):
```
You are a [role] for the *#{channel_name}* Slack channel.

CRITICAL RULES:
- [Rule 1 specific to this channel's purpose]
- [Rule 2 about how to answer]
- [Rule 3 about tone/style]
- [Rule 4 about structure]
- [Rule 5 about technical details]
- SOURCE FORMAT (REQUIRED): [citation instructions]
```

Generate ONLY the system_instruction text. No explanations, no meta-commentary. The output should be ready to use directly as a Gemini system_instruction parameter.

Maximum length: {"800 words for detailed KB-structured answers" if has_kb else "600 words for conversational answers with practical examples"}
"""

        # Call Gemini to generate the instruction
        try:
            from vertexai.generative_models import GenerationConfig as _GenConfig
            response = _gemini_model.generate_content(
                generator_prompt,
                generation_config=_GenConfig(
                    max_output_tokens=2048,
                    temperature=0.7,  # Slightly creative but consistent
                )
            )

            # Extract generated instruction
            generated_instruction = response.text.strip()

            # Clean up any markdown code blocks if present
            if generated_instruction.startswith("```"):
                lines = generated_instruction.split("\n")
                # Remove first and last line if they're backticks
                if lines[0].strip().startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                generated_instruction = "\n".join(lines).strip()

            logger.info(f"Generated system_instruction ({len(generated_instruction)} chars) for channel {channel_id}")

        except Exception as gen_error:
            logger.error(f"Error generating system_instruction: {gen_error}")
            return jsonify({
                "status": "error",
                "message": f"Failed to generate instruction: {str(gen_error)}"
            }), 500

        # Save to database if requested
        db_saved = False
        if save_to_db:
            try:
                conn = _get_channel_ai_db()  # channelassistantai DB
                cur = conn.cursor()

                # Upsert into channel_system_instructions table
                # Table schema: channel_id, system_instruction, is_hardcoded, updated_at
                cur.execute("""
                    INSERT INTO channel_system_instructions
                    (channel_id, system_instruction, is_hardcoded)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (channel_id)
                    DO UPDATE SET
                        system_instruction = EXCLUDED.system_instruction,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE channel_system_instructions.is_hardcoded = FALSE
                """, (
                    channel_id,
                    generated_instruction,
                    False  # is_hardcoded
                ))

                conn.commit()
                conn.close()
                db_saved = True
                logger.info(f"Saved system_instruction to database for channel {channel_id}")

            except Exception as db_error:
                logger.error(f"Error saving to database: {db_error}")
                # Don't fail the request if save fails - just report it
                db_saved = False

        # Return response
        preview = generated_instruction[:200] + "..." if len(generated_instruction) > 200 else generated_instruction

        return jsonify({
            "status": "success",
            "channel_id": channel_id,
            "channel_name": channel_name,
            "generated_system_instruction": generated_instruction,
            "instruction_length": len(generated_instruction),
            "saved_to_db": db_saved,
            "preview": preview,
            "next_steps": [
                "Review the generated instruction",
                "Test the bot in the channel",
                "Adjust channel_description or channel_instruction_text if needed",
                "Regenerate if necessary"
            ]
        }), 200

    except Exception as e:
        logger.error(f"Error in generate_system_instruction: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/channel-knowledge', methods=['GET', 'POST', 'DELETE'])
def channel_knowledge():
    """
    Manage knowledge base articles for channels.

    GET: List knowledge for a channel
        ?channel_id=C0AE1G97QCV

    POST: Add knowledge article(s)
    {
        "channel_id": "C0AE1G97QCV",
        "articles": [
            {
                "title": "How to get Gemini access",
                "content": "Step 1: Go to...",
                "type": "faq",           (optional: faq, guide, announcement, kb_article)
                "source_url": "https://..."  (optional)
            }
        ]
    }

    POST with KB numbers:
    {
        "channel_id": "C0AE1G97QCV",
        "kb_numbers": ["kb0026061", "kb0026053"],
        "mother_kb": "kb0025000" (optional - fetches all sub-articles)
    }

    DELETE: Remove knowledge article
    {
        "channel_id": "C0AE1G97QCV",
        "article_id": "uuid-here"
    }
    """

    if request.method == 'GET':
        channel_id = request.args.get("channel_id", "").strip()
        if not channel_id:
            return jsonify({"status": "error", "message": "Missing 'channel_id'"}), 400

        knowledge = fetch_channel_knowledge(channel_id)
        return jsonify({
            "status": "success",
            "channel_id": channel_id,
            "count": len(knowledge),
            "articles": knowledge
        }), 200

    elif request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        channel_id = data.get("channel_id", "").strip()
        if not channel_id:
            return jsonify({"status": "error", "message": "Missing 'channel_id'"}), 400

        inserted_ids = []

        try:
            conn = _get_channel_ai_db()
            cur = conn.cursor()

            # Option 1: Direct articles (admin-written FAQs, steps, guides)
            articles = data.get("articles", [])
            for article in articles:
                title = article.get("title", "").strip()
                content = article.get("content", "").strip()
                kb_type = article.get("type", "info")
                source_url = article.get("source_url", "")

                if not content:
                    continue

                row_id = str(uuid.uuid4())
                cur.execute("""
                    INSERT INTO knowledge_articles
                    (id, channel_id, knowledge_type, title, content, source_url, uploaded_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (row_id, channel_id, kb_type, title, content, source_url, "api"))
                inserted_ids.append(row_id)
                logger.info(f"Inserted KB article {row_id} for channel {channel_id}")

            # Option 2: KB article numbers — store ONLY the number reference in Cloud SQL
            # Full content will be fetched from BQ at query time
            kb_numbers = data.get("kb_numbers", [])
            mother_kb = data.get("mother_kb", "").strip()

            if mother_kb:
                # Fetch all sub-article numbers under the mother KB from BQ
                bq_client = bigquery.Client(project=BQ_PROJECT_PROD)
                kb_query = f"""
                    SELECT number, short_description,
                           CONCAT('https://atyourservice.verizon.com/ays?id=ays_kb_article&sysparm_article=', UPPER(number)) as url
                    FROM `{BQ_KB_TABLE}`
                    WHERE source_display_value = @mother_kb
                      AND workflow_state = 'published'
                """
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("mother_kb", "STRING", mother_kb.lower()),
                    ]
                )
                rows = list(bq_client.query(kb_query, job_config=job_config).result())
                for row in rows:
                    kb_numbers.append(row.number)

            if kb_numbers:
                for kb_num in kb_numbers:
                    kb_num = kb_num.strip()
                    if not kb_num:
                        continue
                    row_id = str(uuid.uuid4())
                    source_url = f"https://atyourservice.verizon.com/ays?id=ays_kb_article&sysparm_article={kb_num.upper()}"
                    cur.execute("""
                        INSERT INTO knowledge_articles
                        (id, channel_id, knowledge_type, title, content, source_url, kb_number, uploaded_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                    """, (row_id, channel_id, "kb_reference", kb_num, "", source_url, kb_num.lower(), "api"))
                    inserted_ids.append(row_id)
                    logger.info(f"Inserted KB reference {kb_num} for channel {channel_id}")

            conn.commit()
            cur.close()
            conn.close()

        except Exception as e:
            logger.error(f"Error inserting knowledge: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({
            "status": "success",
            "message": f"Inserted {len(inserted_ids)} knowledge article(s)",
            "channel_id": channel_id,
            "inserted_ids": inserted_ids
        }), 200

    elif request.method == 'DELETE':
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        channel_id = data.get("channel_id", "").strip()
        article_id = data.get("article_id", "").strip()

        if not channel_id or not article_id:
            return jsonify({"status": "error", "message": "Missing 'channel_id' or 'article_id'"}), 400

        try:
            conn = _get_channel_ai_db()
            cur = conn.cursor()
            cur.execute("""
                DELETE FROM knowledge_articles
                WHERE id = %s AND channel_id = %s
            """, (article_id, channel_id))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"Error deleting knowledge article: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({
            "status": "success",
            "message": f"Deleted article {article_id}"
        }), 200


@app.route('/channel-map', methods=['GET', 'POST', 'DELETE'])
def channel_map():
    """
    Manage channel mappings (test channel → source channel data).

    GET: List all mappings
    POST: Add mapping {"test_channel_id": "C0TEST", "source_channel_id": "C0SOURCE"}
    DELETE: Remove mapping {"test_channel_id": "C0TEST"}
    """
    if request.method == 'GET':
        return jsonify({
            "status": "success",
            "mappings": CHANNEL_DATA_MAP
        }), 200

    elif request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        test_id = data.get("test_channel_id", "").strip()
        source_id = data.get("source_channel_id", "").strip()

        if not test_id or not source_id:
            return jsonify({"status": "error", "message": "Missing 'test_channel_id' or 'source_channel_id'"}), 400

        CHANNEL_DATA_MAP[test_id] = source_id
        logger.info(f"Channel map added: {test_id} → {source_id}")

        return jsonify({
            "status": "success",
            "message": f"Mapped {test_id} → {source_id}",
            "mappings": CHANNEL_DATA_MAP
        }), 200

    elif request.method == 'DELETE':
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400

        test_id = data.get("test_channel_id", "").strip()
        if test_id in CHANNEL_DATA_MAP:
            del CHANNEL_DATA_MAP[test_id]

        return jsonify({
            "status": "success",
            "message": f"Removed mapping for {test_id}",
            "mappings": CHANNEL_DATA_MAP
        }), 200


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
12. INDIVIDUAL EMPLOYEES section contains EVERY employee's full details: Name, Band, Location, Business Group, Leader, Supervisor, Tenure, Gender, Maternity status, Promotion status, and ratings. ALWAYS use this section to answer ANY drill-down question — identify specific employees by name with their business group and leader details. Never say "I cannot identify specific employees" when this section is present.
19. YEAR CONTEXT RULE: A YEAR CONTEXT section at the top of every summary tells you exactly which years to focus on. ALWAYS begin your answer with a one-line year declaration, e.g. "**Analyzing 2024 → 2025**" (or "**Analyzing 2023 → 2024 (as requested)**"). For double-dip / double-hike questions, use ONLY the MULTI-YEAR TRAJECTORIES data — the trajectories in that section have already been filtered to the focus years. If years were auto-defaulted (not specified by the user), say "using last 2 available years — specify different years if needed." NEVER mix up year ranges or answer for the wrong year.
"""


@app.route('/hr-chat', methods=['POST'])
def hr_chat():
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

        # ── Year context from frontend ─────────────────────────────────
        detected_years = data.get("detected_years", [])
        available_years = data.get("available_years", [])
        years_defaulted = data.get("years_defaulted", True)

        # Fallback: extract years from question if frontend didn't send them
        if not detected_years:
            year_matches = re.findall(r'\b(20\d{2})\b', question)
            if year_matches:
                detected_years = sorted(list(set(int(y) for y in year_matches)))
                years_defaulted = False
            elif available_years:
                detected_years = available_years[-2:]
                years_defaulted = True

        parts = [HR_CHAT_SYSTEM_PROMPT]

        if summary:
            parts.append(f"\n=== WORKFORCE DATA SUMMARY ===\n{summary}\n")

        if employee_context:
            parts.append(f"\n=== SPECIFIC EMPLOYEE DATA ===\n{employee_context}\n")

        # ── Inject explicit year instruction before conversation ────────
        if detected_years:
            year_labels = " → ".join(f"Rating {y}" for y in detected_years)
            if years_defaulted:
                year_note = (
                    f"Auto-defaulted to last {len(detected_years)} available year(s) — "
                    f"user did not specify years in this query."
                )
            else:
                year_note = "User explicitly mentioned these years in their query."
            avail_note = (
                f"All available years: {', '.join(str(y) for y in available_years)}"
                if available_years
                else ""
            )
            year_ctx = (
                f"\n=== ACTIVE YEAR INSTRUCTION ===\n"
                f"Focus on: {year_labels}\n"
                f"{year_note}\n"
            )
            if avail_note:
                year_ctx += f"{avail_note}\n"
            year_ctx += (
                "Start your answer with a bold year declaration, e.g. "
                f"\"**Analyzing {detected_years[0] if len(detected_years)==1 else str(detected_years[0])+' → '+str(detected_years[-1])}**\". "
                "For double-dip/hike, ONLY use the MULTI-YEAR TRAJECTORIES section — it is already filtered.\n"
            )
            parts.append(year_ctx)

        if chat_history:
            parts.append("\n=== CONVERSATION HISTORY ===")
            for msg in chat_history[-10:]:
                role = msg.get("role", "user")
                text = msg.get("text", "")
                if role == "user":
                    parts.append(f"User: {text}")
                else:
                    parts.append(f"Assistant: {text}")
            parts.append("")

        parts.append(f"User: {question}")

        prompt = "\n".join(parts)
        logger.info(
            f"HR chat request: question='{question[:80]}', "
            f"years={detected_years}, defaulted={years_defaulted}, prompt_len={len(prompt)}"
        )

        response = _gemini_model.generate_content(prompt)
        answer = response.text.strip()

        logger.info(f"HR chat response: {len(answer)} chars")

        return jsonify({
            "status": "success",
            "response": answer,
            "detected_years": detected_years,
            "years_defaulted": years_defaulted,
        }), 200

    except Exception as e:
        logger.error(f"Error in hr_chat: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"Failed to generate response: {str(e)}"
        }), 500


# ============================================================================
# LEVEL UPS CHATBOT API
# ============================================================================

LEVELUPS_CHAT_SYSTEM_PROMPT = """You are an expert HR Promotions & Nominations Assistant for the Level Ups dashboard.
You help HR leaders analyze promotion eligibility, nomination fairness, and ensure balanced promotion decisions.

VOICE & TONE:
- Speak as a strategic HR partner — analytical, fair-minded, data-driven.
- Flag imbalances and biases (band favoritism, location bias, work mode discrimination).
- Use specific numbers: "Band 6V has 72.7% nomination rate (8/11)" not "most employees".
- Be direct about violations: "This exceeds the 5-6% target by 34 percentage points."
- Keep answers concise (under 300 words) unless detailed breakdown requested.

DATA YOU RECEIVE:
The LEVELUPS DATA SUMMARY contains:
1. OVERVIEW — total employees, eligible count, nominated count, nomination rate vs target
2. ELIGIBILITY BREAKDOWN — eligible vs ineligible with percentages
3. NOMINATION RATE BY BAND — each band's nomination % (6V, 7V, 8V, 7T)
4. NOMINATION RATE BY LOCATION — Chennai, Hyderabad, Bangalore nomination %
5. NOMINATION RATE BY WORK MODE — Same vs Remote nomination %
6. NOMINATION RATE BY LEADER — each leader's nomination rate and success index
7. RATING DISTRIBUTION — performance rating breakdown for nominated vs not nominated
8. TIME IN TITLE DISTRIBUTION — nomination rate by tenure buckets
9. INDIVIDUAL NOMINEES — each nominated employee with name, band, location, leader, rating
10. INELIGIBLE EMPLOYEES — list with reasons why ineligible
11. THRESHOLD VIOLATIONS — groups exceeding target nomination range
12. RATING ALIGNMENT — top-rated employees not nominated (missed opportunities)

TARGET NOMINATION RANGE:
- The organization sets a target nomination range (typically 5-6%).
- Each band/location/work mode should nominate within this range for fairness.
- Violations = groups significantly above/below target.

CAPABILITIES:
- Analyze nomination fairness across bands, locations, work modes, leaders
- Identify bias patterns (e.g., "Remote workers nominated 2x more than on-site")
- Compare nominated vs not nominated employees by rating
- Flag ineligible employees who were nominated (data quality issue)
- Recommend who to add/remove to meet target range
- Drill down to specific employee names when asked
- Calculate nomination gaps and suggest corrective actions
- Leader performance analysis: compare leaders by nomination rate and rating alignment

RULES:
1. ALWAYS ground answers in the provided summary data. Never invent data.
2. Use percentages AND absolute numbers (e.g., "43/109 (39.4%)").
3. Format for readability: bullets, bold key stats. Use HTML <table> for multi-column comparisons (name + band + location + rating).
4. When asked "who should be nominated", prioritize:
   - Top-rated employees (Top Performing+Top Performing, Top Performing+Exceeding)
   - Employees with longer tenure in title (3-5 years ideal)
   - Balance across bands/locations/work modes
5. Flag violations explicitly: "**CRITICAL**: Band 6V at 72.7% — 66.7 points above target."
6. For "fairness" questions, compare each group's nomination % against overall average.
7. When listing employees, include: Name | Band | Location | Leader | Rating | Nominated (Y/N).
8. Never share data externally — remind user this is confidential.
9. For "who to remove" questions, suggest lowest-rated nominees first (Approaching, Achieving).
10. For "who to add" questions, suggest highest-rated eligible but not nominated.
11. INDIVIDUAL NOMINEES and INELIGIBLE EMPLOYEES sections contain full employee details — ALWAYS use these to answer drill-down questions.
12. Highlight if ineligible employees are nominated (data integrity issue).
13. When comparing leaders, show each leader's: nomination count, eligible count, nomination %, and top rating alignment.
14. For band fairness questions, check if nomination rates are proportional to eligible population.
15. Use markdown bold (**text**) for emphasis, bullet lists for breakdowns.
"""

@app.route('/levelups-chat', methods=['POST'])
def levelups_chat():
    """
    Level Ups AI Chatbot - Promotion & Nomination Analysis

    Expected JSON:
    {
        "question": "Which bands have highest nomination rates?",
        "summary": "Pre-computed LevelUps data summary",
        "employee_context": "Specific employee data (optional)",
        "chat_history": [{"role": "user", "text": "..."}, {"role": "assistant", "text": "..."}],
        "target_range": {"lower": 5, "upper": 6}
    }

    Returns:
    {
        "status": "success",
        "response": "AI-generated analysis..."
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
        target_range = data.get("target_range", {"lower": 5, "upper": 6})

        # Build prompt
        parts = [LEVELUPS_CHAT_SYSTEM_PROMPT]

        if summary:
            parts.append(f"\n=== LEVEL UPS DATA SUMMARY ===\n{summary}\n")

        if employee_context:
            parts.append(f"\n=== SPECIFIC EMPLOYEE DATA ===\n{employee_context}\n")

        # Add target range context
        target_ctx = (
            f"\n=== TARGET NOMINATION RANGE ===\n"
            f"Organization Target: {target_range['lower']}-{target_range['upper']}%\n"
            f"Any group with nomination rate outside this range should be flagged.\n"
        )
        parts.append(target_ctx)

        # Add chat history
        if chat_history:
            parts.append("\n=== CONVERSATION HISTORY ===")
            for msg in chat_history[-10:]:
                role = msg.get("role", "user")
                text = msg.get("text", "")
                if role == "user":
                    parts.append(f"User: {text}")
                else:
                    parts.append(f"Assistant: {text}")
            parts.append("")

        parts.append(f"User: {question}")

        prompt = "\n".join(parts)
        logger.info(f"LevelUps chat request: question='{question[:80]}', prompt_len={len(prompt)}")

        # Call Gemini
        response = _gemini_model.generate_content(prompt)
        answer = response.text.strip()

        logger.info(f"LevelUps chat response: {len(answer)} chars")

        return jsonify({
            "status": "success",
            "response": answer
        }), 200

    except Exception as e:
        logger.error(f"Error in levelups_chat: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"Failed to generate response: {str(e)}"
        }), 500


@app.route('/levelups-sync', methods=['POST'])
def levelups_sync():
    """
    Save LevelUps data and audit log to BigQuery

    Expected JSON:
    {
        "data": [...],  // Full LevelUps dataset
        "audit_log": [...],  // Change audit trail
        "session_id": "user-session-id",
        "sync_timestamp": "2026-10-05T10:30:00Z"
    }

    Returns:
    {
        "status": "success",
        "message": "Synced 222 employees and 15 changes",
        "session_id": "...",
        "timestamp": "..."
    }
    """
    try:
        AUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        CREDENTIALS, _ = google.auth.default(scopes=[AUTH_SCOPE])

        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        levelups_data = data.get("data", [])
        audit_log = data.get("audit_log", [])
        session_id = data.get("session_id", str(uuid.uuid4()))
        sync_timestamp = data.get("sync_timestamp", datetime.now().isoformat())

        if not levelups_data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        # BigQuery client
        bq_client = bigquery.Client(project="vz-it-pr-voev-voevdo-0")

        # Table IDs
        DATA_TABLE = "vz-it-pr-voev-voevdo-0.hr_insights.levelups_data"
        AUDIT_TABLE = "vz-it-pr-voev-voevdo-0.hr_insights.levelups_audit_log"

        # 1. Save main data
        df_data = pd.DataFrame(levelups_data)
        df_data['sync_timestamp'] = sync_timestamp
        df_data['session_id'] = session_id

        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION]
        )

        job_data = bq_client.load_table_from_dataframe(
            df_data, DATA_TABLE, job_config=job_config
        )
        job_data.result()  # Wait for completion

        logger.info(f"LevelUps data synced: {len(levelups_data)} rows to {DATA_TABLE}")

        # 2. Save audit log (if exists)
        if audit_log:
            df_audit = pd.DataFrame(audit_log)
            df_audit['sync_timestamp'] = sync_timestamp
            df_audit['session_id'] = session_id

            job_audit = bq_client.load_table_from_dataframe(
                df_audit, AUDIT_TABLE, job_config=job_config
            )
            job_audit.result()

            logger.info(f"LevelUps audit log synced: {len(audit_log)} entries to {AUDIT_TABLE}")

        logger.info(f"LevelUps sync success: session_id={session_id}")

        return jsonify({
            "status": "success",
            "message": f"Synced {len(levelups_data)} employees and {len(audit_log)} changes",
            "session_id": session_id,
            "timestamp": sync_timestamp
        }), 200

    except Exception as e:
        logger.error(f"Error in levelups_sync: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================================
# ATTRITION CHATBOT API
# ============================================================================

ATTRITION_CHAT_SYSTEM_PROMPT = """You are an expert HR Attrition Analytics Assistant for the Attrition Dashboard.
You help HR leaders analyze employee turnover, identify retention risks, and understand why employees leave.

VOICE & TONE:
- Speak as a strategic retention partner — empathetic, data-driven, action-oriented.
- Focus on regrettable attrition (typically 90%+ of leavers) — these are the ones to prevent.
- Use specific numbers: "Chennai lost 17 employees (17.0% attrition rate)" not "some employees left".
- Flag high-risk patterns: "Band 7V has 20.6% attrition vs 11.0% overall — urgent retention needed."
- Keep answers concise (under 300 words) unless detailed breakdown requested.

DATA YOU RECEIVE:
The ATTRITION DATA SUMMARY contains:
1. OVERVIEW — total headcount, total leavers, overall attrition rate, regrettable vs non-regrettable
2. ATTRITION BY BAND — each band's attrition rate and regrettable count
3. ATTRITION BY LOCATION — each location's attrition rate and regrettable count
4. ATTRITION BY BUSINESS UNIT — each business unit's attrition rate
5. ATTRITION BY TENURE CATEGORY — turnover by tenure bucket
6. ATTRITION BY DIRECTOR — directors with highest attrition
7. TOP REASONS FOR LEAVING — exit reasons ranked by frequency
8. REASON BY SEGMENT — why each band/location/business loses people
9. INDIVIDUAL LEAVERS — each leaver with name, band, location, reason, regrettable status, rating
10. MONTH-WISE TREND — attrition by month (identify spikes)
11. RATING CORRELATION — attrition by performance rating (are top performers leaving?)

ATTRITION RATE CALCULATIONS:
- MTD (Month-to-Date) = (Leavers ÷ Headcount) × 100
- YTD (Year-to-Date) = (Leavers ÷ Headcount) × (12 ÷ Month of Calculation) × 100
- QTD (Quarter-to-Date) = (Leavers ÷ Headcount) × 4 × 100
- Industry Benchmark: 10-15% annual attrition is normal; above 15% is concerning.

CAPABILITIES:
- Analyze attrition rates across all segments (band, location, business, tenure, director)
- Identify high-risk groups (e.g., "Band 7V has 2x higher attrition than overall")
- Root cause analysis by exit reason (career opportunity, pay, work-life balance)
- Regrettable vs Non-Regrettable breakdown (focus on regrettable — those you want to keep)
- Compare segments (e.g., "Chennai vs Hyderabad attrition")
- Drill down to individual leavers (names, reasons, dates)
- Recommend retention strategies based on exit patterns
- Flag director/manager retention issues
- Tenure-based vulnerability analysis
- Rating correlation (are high performers leaving?)

RULES:
1. ALWAYS ground answers in the provided summary data. Never invent data.
2. Use percentages AND absolute numbers (e.g., "17 leavers (17.0% attrition)").
3. Format for readability: bullets, bold key stats. Use HTML <table> for multi-column comparisons.
4. When analyzing regrettable attrition, PRIORITIZE it — these are the people you wanted to keep.
5. Flag high-risk groups explicitly: "**URGENT**: Band 7V at 20.6% attrition — 87% above overall rate."
6. For exit reasons, provide actionable insights: "18 left for career opportunities → review career paths."
7. When listing leavers, include: Name | Band | Location | Reason | Regrettable | Rating.
8. Never share data externally — remind user this is confidential.
9. For "what should we do" questions, provide 3-5 specific retention strategies.
10. Compare attrition rates against industry benchmarks (10-15% normal).
11. INDIVIDUAL LEAVERS section contains full employee details — ALWAYS use it for drill-down questions.
12. When asked "why are people leaving", rank reasons by frequency AND by regrettability.
13. For director/manager questions, show: name, total reports, leavers, attrition %, regrettable count.
14. Month-wise trends: flag spikes (e.g., "28 leavers in July — 90% of annual attrition in one month!").
15. Use markdown bold (**text**) for emphasis, bullet lists for breakdowns.
16. When comparing locations/bands/businesses, show attrition % AND regrettable % for each.
17. If high performers are leaving (Top Performing/Exceeding ratings), flag this as CRITICAL flight risk.
"""

@app.route('/attrition-chat', methods=['POST'])
def attrition_chat():
    """
    Attrition AI Chatbot - Turnover Analysis & Retention Strategies

    Expected JSON:
    {
        "question": "Which band has highest attrition?",
        "summary": "Pre-computed attrition data summary (from headcount + attrition files)",
        "employee_context": "Specific employee/leaver data (optional)",
        "chat_history": [{"role": "user", "text": "..."}, {"role": "assistant", "text": "..."}]
    }

    Returns:
    {
        "status": "success",
        "response": "AI-generated analysis with retention strategies..."
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

        # Build prompt
        parts = [ATTRITION_CHAT_SYSTEM_PROMPT]

        if summary:
            parts.append(f"\n=== ATTRITION DATA SUMMARY ===\n{summary}\n")

        if employee_context:
            parts.append(f"\n=== SPECIFIC EMPLOYEE/LEAVER DATA ===\n{employee_context}\n")

        # Add chat history
        if chat_history:
            parts.append("\n=== CONVERSATION HISTORY ===")
            for msg in chat_history[-10:]:
                role = msg.get("role", "user")
                text = msg.get("text", "")
                if role == "user":
                    parts.append(f"User: {text}")
                else:
                    parts.append(f"Assistant: {text}")
            parts.append("")

        parts.append(f"User: {question}")

        prompt = "\n".join(parts)
        logger.info(f"Attrition chat request: question='{question[:80]}', prompt_len={len(prompt)}")

        # Call Gemini
        response = _gemini_model.generate_content(prompt)
        answer = response.text.strip()

        logger.info(f"Attrition chat response: {len(answer)} chars")

        return jsonify({
            "status": "success",
            "response": answer
        }), 200

    except Exception as e:
        logger.error(f"Error in attrition_chat: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"Failed to generate response: {str(e)}"
        }), 500


# ============================================================================
# TALENT DISTRIBUTION CHATBOT — Workforce Planning & Talent Analytics via Gemini
# ============================================================================

TALENT_DISTRIBUTION_CHAT_SYSTEM_PROMPT = """You are an expert Talent Distribution & Workforce Planning Assistant.
You help HR leaders analyze talent distribution across bands, locations, tenure, and time since last promotion.

VOICE & TONE:
- Speak as a strategic HR partner focused on talent optimization and succession planning.
- Use specific numbers and percentages: "Band 7 has 142 employees (31.4%)" not "many employees".
- Highlight imbalances, bottlenecks, and succession risks proactively.
- Keep answers concise (under 300 words) unless detailed analysis is requested.

DATA YOU RECEIVE:
The TALENT DISTRIBUTION DATA SUMMARY contains:
1. OVERVIEW — total headcount, overall talent distribution summary
2. BAND DISTRIBUTION — employee count and percentage by band level (Band 6, 7, 8, 9, etc.)
3. LOCATION/ODC DISTRIBUTION — talent spread across offices/locations
4. TENURE DISTRIBUTION — workforce by years of service (0-1 year, 1-3 years, 3-5 years, etc.)
5. TIME SINCE LAST PROMOTION (LPD) — promotion recency buckets (< 2 years, 2-5 years, 5-10 years, 10+ years, Never Promoted)
6. GENDER DISTRIBUTION — diversity metrics across dimensions
7. CROSS-DIMENSION ANALYSIS — band-by-location, tenure-by-band, LPD-by-band matrices
8. INDIVIDUAL EMPLOYEES — employee details with: Name | Band | Location | Tenure | LPD | Gender | Business

CAPABILITIES:
- Identify talent concentration risks (too many employees in one band/location creates succession gaps)
- Analyze promotion pipeline health by tenure and last promotion date
- Compare talent distribution across locations, bands, and tenure groups
- Identify succession planning gaps (not enough pipeline in lower bands)
- Diversity and inclusion analysis across all dimensions
- Tenure-based retention risk assessment (high tenure + no recent promotion = flight risk)
- Promotion readiness analysis (employees overdue for promotion)
- Organizational balance recommendations

KEY METRICS:
- **Band Concentration**: Ideally 30-40% in mid-bands (7-8), 15-25% in senior bands (9+), rest in junior
- **Promotion Pipeline**: < 30% "Never Promoted" is healthy; > 50% indicates stagnation
- **Tenure Balance**: Mix of tenures ensures knowledge transfer + fresh perspectives
- **Location Balance**: Avoid > 50% concentration in one location (single point of failure)

RULES:
1. ALWAYS ground answers in the provided data — never invent statistics.
2. Use both percentages AND absolute numbers (e.g., "180 (28%) are in Band 7").
3. Format for readability: use bullets, bold key numbers. Use HTML <table> for multi-column comparisons.
4. If data is insufficient, say so clearly — don't guess.
5. When listing employees, use HTML <table> with <thead> and <tbody> (Name | Band | Location | Tenure | LPD).
6. Highlight risks proactively: "**RISK**: 65% of Band 9 employees are in Hyderabad — succession vulnerability."
7. For "what should we do" questions, provide 3-5 specific workforce planning strategies.
8. Compare distributions against healthy benchmarks (mentioned in KEY METRICS).
9. INDIVIDUAL EMPLOYEES section contains full details — ALWAYS use it for drill-down questions.
10. When asked about promotion readiness, focus on employees with: high tenure + long LPD + senior band.
11. For location questions, show: location name, headcount, % of total, top bands.
12. For band questions, show: band level, headcount, % of total, tenure breakdown, LPD breakdown.
13. Use markdown bold (**text**) for emphasis, bullet lists for breakdowns.
14. When comparing segments, show absolute numbers AND percentages for context.
15. Flag diversity imbalances if gender distribution is skewed (> 80% one gender in any band/location).
16. Never share data externally — remind user this data is confidential.
"""

@app.route('/talent-distribution-chat', methods=['POST'])
def talent_distribution_chat():
    """
    Talent Distribution AI Chatbot - Workforce Planning & Talent Analytics

    Expected JSON:
    {
        "question": "Which band has the most employees?",
        "summary": "Pre-computed talent distribution data summary",
        "employee_context": "Specific employee data (optional)",
        "chat_history": [{"role": "user", "text": "..."}, {"role": "assistant", "text": "..."}]
    }

    Returns:
    {
        "status": "success",
        "response": "AI-generated talent distribution analysis..."
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

        # Build prompt
        parts = [TALENT_DISTRIBUTION_CHAT_SYSTEM_PROMPT]

        if summary:
            parts.append(f"\n=== TALENT DISTRIBUTION DATA SUMMARY ===\n{summary}\n")

        if employee_context:
            parts.append(f"\n=== SPECIFIC EMPLOYEE DATA ===\n{employee_context}\n")

        # Add chat history
        if chat_history:
            parts.append("\n=== CONVERSATION HISTORY ===")
            for msg in chat_history[-10:]:
                role = msg.get("role", "user")
                text = msg.get("text", "")
                if role == "user":
                    parts.append(f"User: {text}")
                else:
                    parts.append(f"Assistant: {text}")
            parts.append("")

        parts.append(f"User: {question}")

        prompt = "\n".join(parts)
        logger.info(f"Talent distribution chat request: question='{question[:80]}', prompt_len={len(prompt)}")

        # Call Gemini
        response = _gemini_model.generate_content(prompt)
        answer = response.text.strip()

        logger.info(f"Talent distribution chat response: {len(answer)} chars")

        return jsonify({
            "status": "success",
            "response": answer
        }), 200

    except Exception as e:
        logger.error(f"Error in talent_distribution_chat: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"Failed to generate response: {str(e)}"
        }), 500


if __name__ == '__main__':
    # For local development with scheduler
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting VAD Automation + Channel AI Service on port {port}")
    logger.info("Channel AI endpoints: /channel-chat, /channel-data-webhook, /channel-knowledge, /channel-map, /hr-chat, /levelups-chat, /levelups-sync, /attrition-chat, /talent-distribution-chat")
    app.run(host='0.0.0.0', port=port)

