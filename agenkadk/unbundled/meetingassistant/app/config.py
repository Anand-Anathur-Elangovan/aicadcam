"""Hardcoded configuration for the Meeting AI Assistant."""

import os

# ── GCP ──────────────────────────────────────────────────────────────────────
GCP_PROJECT = "vz-it-np-voev-dev-voevdo-0"
GCP_REGION = "us-east4"
SERVICE_ACCOUNT_EMAIL = (
    "sa-dev-voev-app-voevdo-0@vz-it-np-voev-dev-voevdo-0.iam.gserviceaccount.com"
)

# ── Meet Bot ─────────────────────────────────────────────────────────────────
MEET_BOT_DISPLAY_NAME = "Verizon Meeting Assistant"
MEET_MEDIA_API_ENABLED = True

# ── Speech-to-Text ───────────────────────────────────────────────────────────
STT_LANGUAGE_CODE = "en-US"
STT_SAMPLE_RATE_HZ = 48000
STT_MODEL = "latest_long"
STT_STREAM_LIMIT_SECONDS = 290
STT_MIN_SPEAKER_COUNT = 2
STT_MAX_SPEAKER_COUNT = 10

# ── Text-to-Speech ───────────────────────────────────────────────────────────
TTS_LANGUAGE_CODE = "en-US"
TTS_VOICE_NAME = "en-US-Studio-O"
TTS_SPEAKING_RATE = 1.0
TTS_SAMPLE_RATE_HZ = 48000

# ── Slack ────────────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")

# ── Email ────────────────────────────────────────────────────────────────────
EMAIL_SERVICE_URL = "https://email-service-vdsi.ebiz.verizon.com"
SMTP_HOST = "vzsmtp.verizon.com"
SMTP_PORT = 25
DEFAULT_FROM_EMAIL = "do-not-reply@verizon.com"

# ── Proxy ────────────────────────────────────────────────────────────────────
PROXIES = {
    "http": "http://proxy.ebiz.verizon.com:80/",
    "https": "http://proxy.ebiz.verizon.com:80/",
}

# ── Playwright Fallback ─────────────────────────────────────────────────────
PLAYWRIGHT_HEADLESS = True
CAPTION_POLL_INTERVAL_MS = 500

# ── WebRTC ───────────────────────────────────────────────────────────────────
WEBRTC_AUDIO_TRANSCEIVER_COUNT = 3
WEBRTC_ICE_RECONNECT_MAX_RETRIES = 3
WEBRTC_ICE_RECONNECT_DELAY_SECONDS = 5

# ── Gemini (for auto-responses during meetings) ─────────────────────────────
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_MAX_RESPONSE_TOKENS = 200
