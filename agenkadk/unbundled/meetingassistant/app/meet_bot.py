"""Google Meet Media API client using WebRTC (aiortc).

Joins a Google Meet as a bot participant, receives audio streams,
and can inject TTS audio back into the meeting.
"""
import asyncio
import logging
from collections.abc import Callable, Awaitable

import numpy as np
from aiortc import (
    MediaStreamTrack,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.mediastreams import AudioStreamTrack
from av import AudioFrame

from app.config import (
    GCP_PROJECT,
    MEET_BOT_DISPLAY_NAME,
    TTS_SAMPLE_RATE_HZ,
    WEBRTC_AUDIO_TRANSCEIVER_COUNT,
    WEBRTC_ICE_RECONNECT_DELAY_SECONDS,
    WEBRTC_ICE_RECONNECT_MAX_RETRIES,
)

logger = logging.getLogger(__name__)

AudioCallback = Callable[[bytes], Awaitable[None]]


class TtsAudioTrack(MediaStreamTrack):
    """Custom audio track that plays TTS frames then silence."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._queue: asyncio.Queue[list[bytes]] = asyncio.Queue()
        self._current_frames: list[bytes] = []
        self._frame_index = 0
        self._pts = 0
        self._sample_rate = TTS_SAMPLE_RATE_HZ
        self._samples_per_frame = (self._sample_rate * 20) // 1000  # 20ms frames

    async def recv(self) -> AudioFrame:
        if not self._current_frames or self._frame_index >= len(self._current_frames):
            try:
                self._current_frames = self._queue.get_nowait()
                self._frame_index = 0
            except asyncio.QueueEmpty:
                self._current_frames = []
                self._frame_index = 0

        if self._current_frames and self._frame_index < len(self._current_frames):
            pcm_data = self._current_frames[self._frame_index]
            self._frame_index += 1
        else:
            pcm_data = b"\x00\x00" * self._samples_per_frame

        samples = np.frombuffer(pcm_data, dtype=np.int16)
        frame = AudioFrame.from_ndarray(
            samples.reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = self._sample_rate
        frame.pts = self._pts
        self._pts += self._samples_per_frame

        await asyncio.sleep(20 / 1000)
        return frame

    def enqueue_audio(self, pcm_frames: list[bytes]) -> None:
        self._queue.put_nowait(pcm_frames)


class MeetMediaBot:
    """Connects to Google Meet via the Meet Media API (WebRTC)."""

    def __init__(self, space_name: str, on_audio_frame: AudioCallback):
        self._space_name = space_name
        self._on_audio_frame = on_audio_frame
        self._pc: RTCPeerConnection | None = None
        self._tts_track: TtsAudioTrack | None = None
        self._connected = False
        self._reconnect_count = 0
        self._receive_tasks: list[asyncio.Task] = []

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, credentials) -> None:
        """Establish WebRTC connection to Meet via the Media API.

        Args:
            credentials: Google auth credentials for Meet API access.
        """
        self._pc = RTCPeerConnection()

        for _ in range(WEBRTC_AUDIO_TRANSCEIVER_COUNT):
            self._pc.addTransceiver("audio", direction="recvonly")

        self._tts_track = TtsAudioTrack()
        self._pc.addTrack(self._tts_track)

        self._pc.addTransceiver("video", direction="recvonly")

        @self._pc.on("track")
        async def on_track(track: MediaStreamTrack):
            if track.kind == "audio":
                task = asyncio.create_task(self._read_audio_track(track))
                self._receive_tasks.append(task)
            logger.info("Received %s track", track.kind)

        @self._pc.on("iceconnectionstatechange")
        async def on_ice_state():
            state = self._pc.iceConnectionState
            logger.info("ICE connection state: %s", state)
            if state in ("disconnected", "failed"):
                await self._handle_disconnect()

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)

        logger.info("Sending SDP offer to Meet Media API for space: %s", self._space_name)

        try:
            from google.apps.meet_v2beta import SpacesServiceAsyncClient
            from google.apps.meet_v2beta.types import ConnectActiveConferenceRequest

            client = SpacesServiceAsyncClient(credentials=credentials)
            response = await client.connect_active_conference(
                ConnectActiveConferenceRequest(
                    name=self._space_name,
                    offer=self._pc.localDescription.sdp,
                )
            )

            answer = RTCSessionDescription(sdp=response.answer, type="answer")
            await self._pc.setRemoteDescription(answer)
            self._connected = True
            self._reconnect_count = 0
            logger.info("WebRTC connection established to Meet")

        except Exception as exc:
            logger.error("Meet Media API connection failed: %s", exc)
            await self.disconnect()
            raise

    async def _read_audio_track(self, track: MediaStreamTrack) -> None:
        try:
            while True:
                frame = await track.recv()
                pcm = frame.to_ndarray().flatten()
                pcm_bytes = pcm.astype(np.int16).tobytes()
                await self._on_audio_frame(pcm_bytes)
        except Exception as exc:
            logger.debug("Audio track ended: %s", exc)

    async def _handle_disconnect(self) -> None:
        if self._reconnect_count >= WEBRTC_ICE_RECONNECT_MAX_RETRIES:
            logger.error("Max reconnection attempts reached, giving up")
            self._connected = False
            return

        self._reconnect_count += 1
        logger.warning(
            "ICE disconnected, attempt %d/%d in %ds",
            self._reconnect_count,
            WEBRTC_ICE_RECONNECT_MAX_RETRIES,
            WEBRTC_ICE_RECONNECT_DELAY_SECONDS,
        )
        await asyncio.sleep(WEBRTC_ICE_RECONNECT_DELAY_SECONDS)

    async def inject_audio(self, pcm_frames: list[bytes]) -> None:
        """Inject TTS audio frames into the meeting.

        Args:
            pcm_frames: List of raw PCM byte frames (20ms each, 48kHz, mono, 16-bit).
        """
        if self._tts_track and self._connected:
            self._tts_track.enqueue_audio(pcm_frames)
            logger.info("Injected %d audio frames into meeting", len(pcm_frames))

    async def disconnect(self) -> None:
        self._connected = False
        for task in self._receive_tasks:
            task.cancel()
        self._receive_tasks.clear()
        if self._pc:
            await self._pc.close()
            self._pc = None
        logger.info("Disconnected from Meet")


def parse_meet_link(meet_link: str) -> str:
    """Extract the meeting code from a Google Meet link.

    Args:
        meet_link: Full Meet URL (e.g., https://meet.google.com/abc-defg-hij).

    Returns:
        The meeting code (e.g., abc-defg-hij).

    Raises:
        ValueError: If the link format is invalid.
    """
    import re

    patterns = [
        r"meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})",
        r"meet\.google\.com/([a-zA-Z0-9\-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, meet_link)
        if match:
            return match.group(1)
    raise ValueError(f"Invalid Meet link format: {meet_link}")


async def resolve_space_name(meeting_code: str, credentials) -> str:
    """Resolve a meeting code to a Meet API space resource name.

    Args:
        meeting_code: The meeting code (e.g., abc-defg-hij).
        credentials: Google auth credentials.

    Returns:
        Space resource name (e.g., spaces/abc123).
    """
    try:
        from google.apps.meet_v2 import SpacesServiceAsyncClient
        from google.apps.meet_v2.types import GetSpaceRequest

        client = SpacesServiceAsyncClient(credentials=credentials)
        space = await client.get_space(
            GetSpaceRequest(name=f"spaces/{meeting_code}")
        )
        return space.name
    except Exception as exc:
        logger.warning("Could not resolve space name via API: %s", exc)
        return f"spaces/{meeting_code}"
