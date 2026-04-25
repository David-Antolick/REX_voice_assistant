"""audio_stream.py
A non-blocking microphone capture helper for the REX voice-assistant.

Usage example (inside your main asyncio program):

    audio_q = asyncio.Queue()
    async with AudioStream(audio_q):
          # other coroutines (VAD, Whisper, etc.)

Each item placed on *audio_q* is a 1-D NumPy array of float32 PCM samples
(normalised to -1.0…1.0) exactly *frame_ms* milliseconds long. (default 32ms, 512 samples at 16kHz)

This implementation uses sounddevice for direct Windows/Mac/Linux audio capture.
No FFmpeg or PulseAudio required.
"""

from __future__ import annotations

import asyncio
from typing import Optional
import numpy as np
import sounddevice as sd

import logging
logger = logging.getLogger(__name__)

__all__ = ["AudioStream"]


class AudioStream:
    """Asynchronously push frames from the default microphone into a queue.

    Parameters
    ----------
    queue : asyncio.Queue[np.ndarray]
        Primary destination queue that will receive PCM blocks.
    samplerate : int, default 16_000
        Target sampling rate (Hz). Make sure downstream models agree.
    frame_ms : int, default 32
        Duration of each frame in milliseconds. Must be consistent with VAD framework.
    device : int or str, optional
        Input device index or name. None uses system default.
    tap_queues : list[asyncio.Queue], optional
        Additional queues that receive a copy of each frame (fan-out). Each tap is
        independent — slow consumers drop frames without affecting the primary queue.
    """

    def __init__(
        self,
        queue: asyncio.Queue,
        *,
        samplerate: int = 16_000,
        frame_ms: int = 32,
        device: Optional[int | str] = None,
        tap_queues: Optional[list[asyncio.Queue]] = None,
        # Keep pulse_server for backwards compatibility but ignore it
        pulse_server: Optional[str] = None,
    ):
        self.queue = queue
        self.samplerate = samplerate
        self.frame_len = int(samplerate * frame_ms / 1000)
        self.frame_ms = frame_ms
        self.device = device
        self.tap_queues = tap_queues or []
        self._stream: Optional[sd.InputStream] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        if pulse_server:
            logger.warning("pulse_server parameter is deprecated and ignored. Using native audio capture.")

    async def __aenter__(self):
        self._loop = asyncio.get_running_loop()
        self._start_stream()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Called by sounddevice for each audio block."""
        if status:
            logger.warning("Audio callback status: %s", status)

        # indata is (frames, channels) - we want mono float32
        # sounddevice gives us float32 in range [-1, 1] already
        audio = indata[:, 0].copy()  # Take first channel, copy to avoid memory issues

        # Put into queue (non-blocking from callback thread)
        # Use a helper that catches QueueFull to avoid spamming errors
        def _enqueue(data):
            try:
                self.queue.put_nowait(data)
            except asyncio.QueueFull:
                pass  # Silently drop frame - this happens during slow transcription
            for tap in self.tap_queues:
                try:
                    tap.put_nowait(data)
                except asyncio.QueueFull:
                    pass  # Independent drop policy per tap

        self._loop.call_soon_threadsafe(lambda: _enqueue(audio))

    def _start_stream(self):
        """Start the sounddevice input stream."""
        # Log configuration
        logger.debug(
            "AudioStream starting: samplerate=%d Hz, frame_ms=%d ms, frame_len=%d samples",
            self.samplerate, self.frame_ms, self.frame_len,
        )

        # List available input devices
        try:
            devices = sd.query_devices()
            default_input = sd.default.device[0]

            if default_input is not None and default_input >= 0:
                dev_info = sd.query_devices(default_input, kind="input")
                logger.info("Using audio input: [%d] %s", default_input, dev_info.get("name", "Unknown"))
            else:
                # Find first available input device
                inputs = [
                    (i, d["name"])
                    for i, d in enumerate(devices)
                    if d.get("max_input_channels", 0) > 0
                ]
                if inputs:
                    logger.info("Available input devices: %s", inputs)
                else:
                    raise RuntimeError("No audio input devices found")

        except Exception as e:
            logger.error("Error querying audio devices: %s", e)
            raise

        # Create and start the input stream
        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                blocksize=self.frame_len,
                device=self.device,
                channels=1,
                dtype=np.float32,
                callback=self._audio_callback,
            )
            self._stream.start()
            logger.debug("Audio stream started successfully")

        except sd.PortAudioError as e:
            logger.error("Failed to start audio stream: %s", e)
            logger.error("Make sure a microphone is connected and accessible")
            raise RuntimeError(f"Audio capture failed: {e}") from e


def list_audio_devices() -> list[dict]:
    """List all available audio input devices.

    Returns:
        List of dicts with 'index', 'name', 'channels', 'default' keys
    """
    devices = []
    default_input = sd.default.device[0]

    for i, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            devices.append({
                "index": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "default": i == default_input,
            })

    return devices
