"""Google Cloud Speech-to-Text streaming wrapper with auto-reconnection."""
import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech

from google.api_core.client_options import ClientOptions

from app.config import (
    GCP_PROJECT,
    GCP_REGION,
    STT_LANGUAGE_CODE,
    STT_MODEL,
    STT_SAMPLE_RATE_HZ,
    STT_STREAM_LIMIT_SECONDS,
    STT_MIN_SPEAKER_COUNT,
    STT_MAX_SPEAKER_COUNT,
)

logger = logging.getLogger(__name__)

TranscriptCallback = Callable[[str, str, bool], Any]


class SttStreamer:
    """Streams audio to Cloud STT and fires a callback for each transcript segment.

    Handles the 5-minute stream limit by automatically reconnecting.
    """

    def __init__(self, on_transcript: TranscriptCallback):
        self._on_transcript = on_transcript
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None
        self._client: SpeechAsyncClient | None = None
        self._recognizer = f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/recognizers/_"

    async def start(self) -> None:
        self._running = True
        self._client = SpeechAsyncClient(
            client_options=ClientOptions(
                api_endpoint=f"{GCP_REGION}-speech.googleapis.com",
            ),
        )
        self._task = asyncio.create_task(self._stream_loop())
        logger.info("STT streamer started")

    async def stop(self) -> None:
        self._running = False
        await self._audio_queue.put(None)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("STT streamer stopped")

    async def feed_audio(self, pcm_bytes: bytes) -> None:
        if self._running:
            await self._audio_queue.put(pcm_bytes)

    def _build_streaming_config(self) -> cloud_speech.StreamingRecognitionConfig:
        recognition_config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[STT_LANGUAGE_CODE],
            model=STT_MODEL,
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
                diarization_config=cloud_speech.SpeakerDiarizationConfig(
                    min_speaker_count=STT_MIN_SPEAKER_COUNT,
                    max_speaker_count=STT_MAX_SPEAKER_COUNT,
                ),
            ),
        )
        return cloud_speech.StreamingRecognitionConfig(
            config=recognition_config,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(
                interim_results=True,
            ),
        )

    async def _request_generator(
        self, stream_start: float
    ):
        config_request = cloud_speech.StreamingRecognizeRequest(
            recognizer=self._recognizer,
            streaming_config=self._build_streaming_config(),
        )
        yield config_request

        while self._running:
            elapsed = time.monotonic() - stream_start
            if elapsed >= STT_STREAM_LIMIT_SECONDS:
                logger.info("STT stream limit approaching (%ds), reconnecting", int(elapsed))
                return

            try:
                chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if chunk is None:
                return

            yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

    async def _stream_loop(self) -> None:
        backoff = 1.0
        while self._running:
            stream_start = time.monotonic()
            try:
                responses = await self._client.streaming_recognize(
                    requests=self._request_generator(stream_start)
                )

                async for response in responses:
                    if not self._running:
                        break
                    for result in response.results:
                        if not result.alternatives:
                            continue
                        alt = result.alternatives[0]
                        speaker = "Speaker"
                        if alt.words:
                            last_word = alt.words[-1]
                            if hasattr(last_word, "speaker_label") and last_word.speaker_label:
                                speaker = f"Speaker {last_word.speaker_label}"
                        is_final = result.is_final
                        transcript_text = alt.transcript.strip()
                        if transcript_text:
                            try:
                                await asyncio.coroutine(lambda: None)() if False else None
                                self._on_transcript(speaker, transcript_text, is_final)
                            except Exception:
                                logger.exception("Transcript callback error")

                backoff = 1.0

            except Exception as exc:
                if not self._running:
                    break
                logger.warning("STT stream error: %s, reconnecting in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
