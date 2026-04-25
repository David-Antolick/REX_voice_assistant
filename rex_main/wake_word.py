"""wake_word.py
Wake-word detection for REX using openWakeWord.

Components:
- ListeningState: shared gate state between detector and command handlers.
- WakeWordDetector: async worker that consumes a tap from the audio queue,
  runs openWakeWord inference, and activates the listening window on detection.
- play_wake_cue(): non-blocking audio cue played when the wake word fires.

When `enabled=False` the gate is a no-op (`is_active()` returns True), preserving
legacy behavior. When openwakeword is not installed, the worker logs an install
hint and disables the gate so REX continues to function.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

__all__ = ["ListeningState", "WakeWordDetector", "play_wake_cue"]


class ListeningState:
    """Tracks whether REX is currently within an active listening window.

    When `gate_enabled` is False, `is_active()` always returns True.
    """

    def __init__(self, *, gate_enabled: bool, default_window_s: float):
        self._gate_enabled = gate_enabled
        self._default_window = default_window_s
        self._active_until: float = 0.0

    @property
    def gate_enabled(self) -> bool:
        return self._gate_enabled

    def is_active(self) -> bool:
        if not self._gate_enabled:
            return True
        return time.monotonic() < self._active_until

    def activate(self, window_s: Optional[float] = None) -> None:
        self._active_until = time.monotonic() + (window_s or self._default_window)

    def deactivate(self) -> None:
        self._active_until = 0.0


def play_wake_cue(samplerate: int = 16_000) -> None:
    """Play a short two-tone 'ding' to confirm wake-word detection.

    Non-blocking. Failures are swallowed - the cue is non-essential.
    """
    try:
        import sounddevice as sd

        duration_s = 0.08
        t = np.arange(0, duration_s, 1 / samplerate, dtype=np.float32)
        tone1 = 0.25 * np.sin(2 * np.pi * 880 * t).astype(np.float32)
        tone2 = 0.25 * np.sin(2 * np.pi * 1320 * t).astype(np.float32)
        envelope = np.linspace(1.0, 0.0, len(t), dtype=np.float32) ** 2
        cue = np.concatenate([tone1 * envelope, tone2 * envelope])
        sd.play(cue, samplerate=samplerate, blocking=False)
    except Exception as exc:
        logger.debug("Wake cue playback failed: %s", exc)


class WakeWordDetector:
    """Async worker that runs openWakeWord on streamed audio frames.

    Reads `float32` frames from `audio_q` (typically a tap of the main audio
    queue). On detection above `threshold`, activates `listening_state` and
    optionally plays a cue. Debounces consecutive fires.

    If openwakeword is not importable, the worker logs an install hint and
    disables the gate (sets listening_state to always-on) so REX still works.
    """

    def __init__(
        self,
        audio_q: "asyncio.Queue[np.ndarray]",
        listening_state: ListeningState,
        *,
        model: str = "hey_jarvis",
        threshold: float = 0.5,
        debounce_seconds: float = 1.0,
        cue_enabled: bool = True,
        samplerate: int = 16_000,
    ):
        self.audio_q = audio_q
        self.listening_state = listening_state
        self.model_name = model
        self.threshold = threshold
        self.debounce_seconds = debounce_seconds
        self.cue_enabled = cue_enabled
        self.samplerate = samplerate

        self._model = None
        self._last_fire: float = 0.0
        self._disabled = False

    def _lazy_init(self) -> None:
        if self._model is not None or self._disabled:
            return
        try:
            from openwakeword.model import Model
            from openwakeword.utils import download_models
        except ImportError:
            logger.error(
                "openwakeword not installed. Install with: "
                "pip install rex-voice-assistant[wake_word]. "
                "Wake-word gate disabled - all commands will fire."
            )
            self.listening_state._gate_enabled = False
            self._disabled = True
            return

        # Expand ~ so users can write `model: ~/.rex/wake_models/hey_rex.onnx`.
        model_arg = os.path.expanduser(self.model_name)
        is_custom = os.path.exists(model_arg)

        def _try_load() -> bool:
            try:
                self._model = Model(wakeword_models=[model_arg], inference_framework="onnx")
                return True
            except Exception as exc:
                logger.warning("Wake-word model load attempt failed: %s", exc)
                return False

        if not _try_load():
            if is_custom:
                # No point downloading prebuilt models; the user pointed at a missing file.
                logger.error("Custom wake-word model not loadable: %s. Gate disabled.", model_arg)
                self.listening_state._gate_enabled = False
                self._disabled = True
                return
            logger.info("Downloading openWakeWord models (one-time, ~30MB)...")
            try:
                download_models()
            except Exception as exc:
                logger.error("Failed to download wake-word models: %s. Gate disabled.", exc)
                self.listening_state._gate_enabled = False
                self._disabled = True
                return
            if not _try_load():
                logger.error("Wake-word model '%s' still unavailable after download. Gate disabled.", self.model_name)
                self.listening_state._gate_enabled = False
                self._disabled = True
                return

        if is_custom:
            logger.info("Loaded custom wake-word model: %s (threshold=%.2f)", model_arg, self.threshold)
        else:
            logger.info("WakeWord model loaded: %s (threshold=%.2f)", self.model_name, self.threshold)

    def _predict(self, frame: np.ndarray) -> Optional[float]:
        """Run inference on one frame. Returns top score or None."""
        if self._model is None:
            return None
        # openWakeWord expects int16 PCM
        pcm_int16 = (frame * 32767.0).astype(np.int16)
        scores = self._model.predict(pcm_int16)
        if not scores:
            return None
        return max(scores.values())

    async def run(self) -> None:
        self._lazy_init()
        if self._disabled:
            # Drain the queue forever so it doesn't fill up.
            while True:
                try:
                    self.audio_q.get_nowait()
                    self.audio_q.task_done()
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.05)

        loop = asyncio.get_running_loop()
        logger.info("WakeWordDetector started (window=%.1fs, debounce=%.1fs)",
                    self.listening_state._default_window, self.debounce_seconds)

        while True:
            frame = await self.audio_q.get()
            try:
                score = await loop.run_in_executor(None, self._predict, frame)
                if score is None:
                    continue

                now = time.monotonic()
                if score >= self.threshold and (now - self._last_fire) >= self.debounce_seconds:
                    self._last_fire = now
                    self.listening_state.activate()
                    logger.info("WakeWord fired: %s (score=%.3f)", self.model_name, score)

                    # Lazy import to avoid circular dependency at module load time.
                    try:
                        from rex_main.metrics import metrics
                        metrics.record_wake_word(score=score, model=self.model_name)
                    except Exception:
                        pass

                    if self.cue_enabled:
                        play_wake_cue(samplerate=self.samplerate)
            finally:
                self.audio_q.task_done()
