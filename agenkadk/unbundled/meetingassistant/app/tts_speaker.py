"""Google Cloud Text-to-Speech wrapper."""
import logging
import struct

from google.cloud import texttospeech

from app.config import (
    TTS_LANGUAGE_CODE,
    TTS_SAMPLE_RATE_HZ,
    TTS_SPEAKING_RATE,
    TTS_VOICE_NAME,
)

logger = logging.getLogger(__name__)

_client: texttospeech.TextToSpeechClient | None = None


def _get_client() -> texttospeech.TextToSpeechClient:
    global _client
    if _client is None:
        _client = texttospeech.TextToSpeechClient()
    return _client


def synthesize_speech(text: str) -> bytes:
    """Convert text to raw PCM audio (LINEAR16, 48 kHz, mono).

    Args:
        text: The text to synthesize.

    Returns:
        Raw PCM bytes (16-bit signed little-endian).
    """
    client = _get_client()

    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code=TTS_LANGUAGE_CODE,
        name=TTS_VOICE_NAME,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=TTS_SAMPLE_RATE_HZ,
        speaking_rate=TTS_SPEAKING_RATE,
    )

    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )

    audio = response.audio_content
    if len(audio) > 44 and audio[:4] == b"RIFF":
        audio = audio[44:]

    logger.info("TTS synthesized %d bytes for %d chars", len(audio), len(text))
    return audio


def pcm_to_frames(pcm_bytes: bytes, frame_duration_ms: int = 20) -> list[bytes]:
    """Split raw PCM into fixed-duration frames for WebRTC.

    Args:
        pcm_bytes: Raw PCM audio (16-bit, 48kHz, mono).
        frame_duration_ms: Duration of each frame in milliseconds.

    Returns:
        List of PCM byte frames.
    """
    samples_per_frame = (TTS_SAMPLE_RATE_HZ * frame_duration_ms) // 1000
    bytes_per_frame = samples_per_frame * 2  # 16-bit = 2 bytes per sample
    frames = []
    for i in range(0, len(pcm_bytes), bytes_per_frame):
        frame = pcm_bytes[i : i + bytes_per_frame]
        if len(frame) < bytes_per_frame:
            frame += b"\x00" * (bytes_per_frame - len(frame))
        frames.append(frame)
    return frames


def generate_silence(duration_ms: int = 20) -> bytes:
    """Generate a silent PCM frame.

    Args:
        duration_ms: Duration in milliseconds.

    Returns:
        Silent PCM bytes.
    """
    samples = (TTS_SAMPLE_RATE_HZ * duration_ms) // 1000
    return b"\x00\x00" * samples
