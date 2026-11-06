"""Central orchestrator: wires bot + STT + TTS + name detection + notifications."""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import google.auth

from app import session_store
from app.config import (
    CAPTION_POLL_INTERVAL_MS,
    GCP_PROJECT,
    GCP_REGION,
    GEMINI_MODEL,
    GEMINI_MAX_RESPONSE_TOKENS,
    MEET_BOT_DISPLAY_NAME,
    MEET_MEDIA_API_ENABLED,
)
from app.meet_bot import MeetMediaBot, parse_meet_link, resolve_space_name
from app.meet_bot_fallback import PlaywrightMeetBot
from app.name_detector import NameDetector
from app.notification_tools import notify_name_mention
from app.stt_streamer import SttStreamer
from app.tts_speaker import pcm_to_frames, synthesize_speech

logger = logging.getLogger(__name__)


class MeetingManager:
    """Singleton that manages one active meeting at a time."""

    def __init__(self):
        self._media_bot: MeetMediaBot | None = None
        self._fallback_bot: PlaywrightMeetBot | None = None
        self._stt: SttStreamer | None = None
        self._name_detector: NameDetector | None = None
        self._active_meeting_id: str | None = None
        self._caption_task: asyncio.Task | None = None
        self._credentials = None

    def _get_credentials(self):
        if self._credentials is None:
            self._credentials, _ = google.auth.default()
        return self._credentials

    async def join_meeting(
        self,
        meet_link: str,
        user_name: str,
        wake_name: str,
        user_email: str = "",
        slack_channel_id: str = "",
    ) -> dict[str, Any]:
        active = session_store.get_active_session()
        if active:
            return {
                "meeting_id": active.meeting_id,
                "status": "already_active",
                "error": "Already in a meeting. Leave the current meeting first.",
            }

        meeting_id = session_store.create_session(
            meet_link=meet_link,
            user_name=user_name,
            wake_name=wake_name,
            user_email=user_email,
            slack_channel_id=slack_channel_id,
        )
        self._active_meeting_id = meeting_id
        self._name_detector = NameDetector(wake_name)

        session_store.update_status(meeting_id, "joining")

        if MEET_MEDIA_API_ENABLED:
            try:
                result = await self._join_via_media_api(meeting_id, meet_link)
                return result
            except Exception as exc:
                logger.warning("Meet Media API failed: %s, trying fallback", exc)

        try:
            result = await self._join_via_fallback(meeting_id, meet_link)
            return result
        except Exception as exc:
            session_store.update_status(meeting_id, "failed")
            self._active_meeting_id = None
            return {
                "meeting_id": meeting_id,
                "status": "failed",
                "mode": "none",
                "error": f"Both Media API and browser fallback failed: {exc}",
            }

    async def _join_via_media_api(
        self, meeting_id: str, meet_link: str
    ) -> dict[str, Any]:
        meeting_code = parse_meet_link(meet_link)
        credentials = self._get_credentials()

        space_name = await resolve_space_name(meeting_code, credentials)
        session_store.update_space_name(meeting_id, space_name)

        async def on_audio(pcm_bytes: bytes) -> None:
            if self._stt:
                await self._stt.feed_audio(pcm_bytes)

        self._media_bot = MeetMediaBot(space_name, on_audio_frame=on_audio)

        def on_transcript(speaker: str, text: str, is_final: bool) -> None:
            ts = datetime.now().strftime("%H:%M:%S")
            session_store.append_transcript(meeting_id, speaker, text, ts)
            if is_final and self._name_detector:
                mention = self._name_detector.check(text)
                if mention:
                    asyncio.create_task(
                        self._handle_name_mention(meeting_id, mention, text)
                    )

        self._stt = SttStreamer(on_transcript=on_transcript)
        await self._stt.start()
        await self._media_bot.connect(credentials)

        session_store.update_status(meeting_id, "active")
        session_store.update_mode(meeting_id, "media_api")

        return {
            "meeting_id": meeting_id,
            "status": "active",
            "mode": "media_api",
            "error": None,
        }

    async def _join_via_fallback(
        self, meeting_id: str, meet_link: str
    ) -> dict[str, Any]:
        self._fallback_bot = PlaywrightMeetBot()
        await self._fallback_bot.join(meet_link, MEET_BOT_DISPLAY_NAME)

        session_store.update_status(meeting_id, "active")
        session_store.update_mode(meeting_id, "browser_fallback")

        self._caption_task = asyncio.create_task(
            self._caption_poll_loop(meeting_id)
        )

        return {
            "meeting_id": meeting_id,
            "status": "active",
            "mode": "browser_fallback",
            "note": "Browser mode: captions for transcript, chat for responses (no audio injection)",
            "error": None,
        }

    async def _caption_poll_loop(self, meeting_id: str) -> None:
        while self._fallback_bot and self._fallback_bot.connected:
            try:
                entries = await self._fallback_bot.poll_captions()
                for entry in entries:
                    ts = datetime.now().strftime("%H:%M:%S")
                    session_store.append_transcript(
                        meeting_id, entry["speaker"], entry["text"], ts
                    )
                    if self._name_detector:
                        mention = self._name_detector.check(entry["text"])
                        if mention:
                            asyncio.create_task(
                                self._handle_name_mention(
                                    meeting_id, mention, entry["text"]
                                )
                            )
            except Exception as exc:
                logger.debug("Caption poll error: %s", exc)
            await asyncio.sleep(CAPTION_POLL_INTERVAL_MS / 1000)

    async def _handle_name_mention(self, meeting_id: str, mention, full_text: str) -> None:
        session = session_store.get_session(meeting_id)
        if not session:
            return

        logger.info(
            "Name '%s' detected in meeting %s: %s",
            mention.name_matched,
            meeting_id,
            mention.context_text[:100],
        )

        response_text = await self._generate_response(session, mention.question)

        session_store.record_mention(
            meeting_id,
            datetime.now().strftime("%H:%M:%S"),
            mention.context_text,
            response_text,
        )

        if session.mode == "media_api" and self._media_bot and self._media_bot.connected:
            try:
                pcm = synthesize_speech(response_text)
                frames = pcm_to_frames(pcm)
                await self._media_bot.inject_audio(frames)
            except Exception as exc:
                logger.error("TTS injection failed: %s", exc)
        elif session.mode == "browser_fallback" and self._fallback_bot:
            try:
                await self._fallback_bot.send_chat_message(
                    f"[{session.user_name}]: {response_text}"
                )
            except Exception as exc:
                logger.error("Chat message failed: %s", exc)

        try:
            notify_name_mention(
                meeting_id=meeting_id,
                mention_text=mention.context_text,
                question=mention.question,
                response_given=response_text,
                user_email=session.user_email,
                slack_channel_id=session.slack_channel_id,
            )
        except Exception as exc:
            logger.error("Notification failed: %s", exc)

    async def _generate_response(self, session, question: str) -> str:
        try:
            from google import genai

            client = genai.Client(
                vertexai=True, project=GCP_PROJECT, location=GCP_REGION
            )

            context_str = "\n".join(
                f"- {k}: {v}" for k, v in session.context.items()
            )
            if not context_str:
                context_str = "(No specific context provided)"

            prompt = (
                f"You are answering on behalf of {session.user_name} in a meeting. "
                f"Be concise (2-3 sentences max), professional, and natural.\n\n"
                f"Context provided by {session.user_name}:\n{context_str}\n\n"
                f"Someone in the meeting said something directed at {session.user_name}: "
                f'"{question}"\n\n'
                f"Provide a response as if you are {session.user_name}. "
                f"If the context doesn't have enough information, say: "
                f'"I\'ll need to check on that and get back to you."'
            )

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"max_output_tokens": GEMINI_MAX_RESPONSE_TOKENS},
            )
            return response.text.strip()

        except Exception as exc:
            logger.error("Gemini response generation failed: %s", exc)
            return "I'll need to check on that and get back to you."

    async def leave_meeting(self, meeting_id: str) -> dict[str, Any]:
        session = session_store.get_session(meeting_id)
        if not session:
            return {"status": "not_found", "error": f"Meeting {meeting_id} not found"}

        if self._stt:
            await self._stt.stop()
            self._stt = None

        if self._media_bot:
            await self._media_bot.disconnect()
            self._media_bot = None

        if self._caption_task:
            self._caption_task.cancel()
            self._caption_task = None

        if self._fallback_bot:
            await self._fallback_bot.leave()
            self._fallback_bot = None

        session_store.update_status(meeting_id, "ended")
        self._active_meeting_id = None
        self._name_detector = None

        transcript = session_store.get_transcript(meeting_id)
        return {
            "status": "ended",
            "meeting_id": meeting_id,
            "transcript_length": len(transcript),
            "name_mentions": len(session.name_mentions),
            "error": None,
        }

    def get_transcript(self, meeting_id: str) -> dict[str, Any]:
        session = session_store.get_session(meeting_id)
        if not session:
            return {"transcript": [], "length": 0, "error": f"Meeting {meeting_id} not found"}
        transcript = session_store.get_transcript(meeting_id)
        return {"transcript": transcript, "length": len(transcript), "error": None}

    async def generate_summary(self, meeting_id: str) -> dict[str, Any]:
        session = session_store.get_session(meeting_id)
        if not session:
            return {"summary": "", "error": f"Meeting {meeting_id} not found"}

        transcript = session_store.get_transcript(meeting_id)
        if not transcript:
            return {"summary": "No transcript available", "action_items": [], "error": None}

        transcript_text = "\n".join(
            f"[{t['timestamp']}] {t['speaker']}: {t['text']}" for t in transcript
        )

        try:
            from google import genai

            client = genai.Client(
                vertexai=True, project=GCP_PROJECT, location=GCP_REGION
            )

            prompt = (
                "Summarize this meeting transcript. Provide:\n"
                "1. **Key Discussion Points** (3-5 bullets)\n"
                "2. **Decisions Made** (if any)\n"
                "3. **Action Items** (with owner if identifiable)\n"
                "4. **Follow-up Items** (things to revisit)\n\n"
                f"Meeting transcript:\n{transcript_text[:10000]}"
            )

            response = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt
            )

            action_items = []
            for line in response.text.split("\n"):
                line = line.strip()
                if any(
                    marker in line.lower()
                    for marker in ["action:", "todo:", "follow up:", "- [ ]"]
                ) or (line.startswith("- ") and "should" in line.lower()):
                    action_items.append(line.lstrip("- ").strip())

            return {
                "summary": response.text.strip(),
                "action_items": action_items,
                "transcript_length": len(transcript),
                "name_mentions": len(session.name_mentions),
                "duration": (
                    str(session.ended_at - session.started_at)
                    if session.ended_at and session.started_at
                    else "ongoing"
                ),
                "error": None,
            }

        except Exception as exc:
            logger.error("Summary generation failed: %s", exc)
            return {"summary": "", "action_items": [], "error": str(exc)}

    def update_wake_name(self, meeting_id: str, wake_name: str) -> dict[str, Any]:
        session = session_store.get_session(meeting_id)
        if not session:
            return {"status": "not_found", "error": f"Meeting {meeting_id} not found"}
        session_store.update_wake_name(meeting_id, wake_name)
        if self._name_detector:
            self._name_detector.wake_name = wake_name
        return {"status": "updated", "wake_name": wake_name, "error": None}


_manager = MeetingManager()


def get_manager() -> MeetingManager:
    return _manager
