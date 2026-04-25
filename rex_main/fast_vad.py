"""fast_vad.py
Optimized VAD with early transcription for low-latency command detection.

Key optimization: Instead of waiting for silence to end an utterance,
we periodically send partial audio to Whisper and check for command matches.
If we get a confident match, we execute immediately.

This can reduce latency from ~2-3s to ~500-800ms for short commands.
"""

from __future__ import annotations

import asyncio
from typing import Optional, Callable
from collections import deque
import numpy as np
import torch
import logging
import time

from rex_main.metrics import metrics
from rex_main.benchmark import benchmark

logger = logging.getLogger(__name__)

__all__ = ["FastVAD"]

_REPO = "snakers4/silero-vad"
_MODEL = "silero_vad"


class FastVAD:
    """Optimized VAD with early command detection.

    Instead of waiting for full utterance completion, this VAD:
    1. Starts collecting audio when speech is detected
    2. After min_speech_ms, sends audio for early transcription
    3. If early transcription matches a command, executes immediately
    4. Otherwise, continues collecting until silence timeout

    Parameters
    ----------
    in_queue : asyncio.Queue[np.ndarray]
        Queue delivering fixed-length float32 PCM frames.
    transcribe_func : Callable
        Function that takes audio (np.ndarray) and returns transcribed text.
    match_func : Callable
        Function that takes text and returns (matched: bool, command_name: str | None, args: tuple, allow_early: bool).
    execute_func : Callable
        Function that executes a matched command.
    sample_rate : int
        Audio sample rate (default: 16000).
    frame_ms : int
        Duration of each frame in ms (default: 32).
    speech_threshold : float
        VAD threshold for speech detection (default: 0.65).
    silence_ms : int
        Silence duration to end utterance (default: 400).
    min_speech_ms : int
        Minimum speech before attempting early transcription (default: 300).
    early_check_interval_ms : int
        How often to attempt early transcription (default: 200).
    """

    def __init__(
        self,
        in_queue: asyncio.Queue,
        transcribe_func: Callable[[np.ndarray], str],
        match_func: Callable[[str], tuple[bool, Optional[str], tuple, bool]],
        execute_func: Callable[[str, tuple], None],
        *,
        sample_rate: int = 16_000,
        frame_ms: int = 32,
        speech_threshold: float = 0.65,
        silence_ms: int = 400,
        min_speech_ms: int = 300,
        early_check_interval_ms: int = 200,
        max_utterance_ms: int = 10_000,
        gate_func: Optional[Callable[[], bool]] = None,
    ):
        self.in_q = in_queue
        self.transcribe = transcribe_func
        self.match = match_func
        self.execute = execute_func
        self.gate_func = gate_func or (lambda: True)

        self.sr = sample_rate
        self.frame_ms = frame_ms
        self.speech_th = speech_threshold

        # Convert ms to frame counts
        self.silence_frames = silence_ms // frame_ms
        self.min_speech_frames = min_speech_ms // frame_ms
        self.early_check_frames = early_check_interval_ms // frame_ms
        self.max_frames = max_utterance_ms // frame_ms

        # Pre-speech buffer (capture audio just before speech detected)
        self.pre_speech_frames = 100 // frame_ms  # 100ms
        self._pre_buf: deque = deque(maxlen=self.pre_speech_frames)

        # VAD model
        self._model: Optional[torch.jit.ScriptModule] = None

        logger.info(
            "FastVAD: silence=%dms, min_speech=%dms, early_check=%dms",
            silence_ms, min_speech_ms, early_check_interval_ms
        )

    async def run(self):
        """Main VAD loop with early command detection."""
        self._lazy_init()

        speech_buf: list[np.ndarray] = []
        silence_ctr = 0
        frames_since_check = 0
        last_early_text = ""
        command_executed = False

        while True:
            frame = await self.in_q.get()

            # Always update pre-buffer when not in speech
            if not speech_buf:
                self._pre_buf.append(frame)

            speech_prob = self._infer(frame)

            if speech_prob >= self.speech_th:
                # Speech detected
                if not speech_buf:
                    # First speech frame - prepend pre-buffer
                    speech_buf.extend(self._pre_buf)
                    self._pre_buf.clear()
                    metrics.record_speech_start()
                    benchmark.record_speech_start()
                    command_executed = False
                    last_early_text = ""

                speech_buf.append(frame)
                silence_ctr = 0
                frames_since_check += 1

                # Early transcription check
                if (not command_executed and
                    len(speech_buf) >= self.min_speech_frames and
                    frames_since_check >= self.early_check_frames):

                    frames_since_check = 0
                    audio = np.concatenate(speech_buf, dtype=np.float32)

                    # Run early transcription
                    t0 = time.perf_counter()
                    text = await asyncio.get_running_loop().run_in_executor(
                        None, self.transcribe, audio
                    )
                    dt = (time.perf_counter() - t0) * 1000

                    # Only process if text changed
                    if text and text != last_early_text:
                        last_early_text = text
                        logger.debug("Early transcription (%.0fms): %r", dt, text)

                        # Check for command match
                        matched, cmd_name, args, allow_early = self.match(text)
                        if matched and cmd_name and not allow_early:
                            # Skip early match for commands that need full utterance (e.g., search)
                            logger.debug("Skipping early match for '%s' (requires full utterance)", cmd_name)
                            continue
                        if matched and cmd_name:
                            if not self.gate_func():
                                logger.info("Suppressed early match '%s' from %r (wake word not active)", cmd_name, text)
                                metrics.record_command_suppressed(cmd_name)
                                # Drop the buffer so we don't keep retranscribing the same audio.
                                speech_buf.clear()
                                self._pre_buf.clear()
                                silence_ctr = 0
                                command_executed = True  # treat as handled to skip flush
                                continue
                            logger.info("Early match! Command '%s' from: %r", cmd_name, text)
                            metrics.record_transcription(text, dt)
                            metrics.record_command_match(cmd_name, matched=True)

                            # Execute immediately
                            t1 = time.perf_counter()
                            self.execute(cmd_name, args)
                            exec_dt = (time.perf_counter() - t1) * 1000
                            metrics.record_command_execute(cmd_name, exec_dt)

                            # Record for benchmark
                            duration_ms = len(speech_buf) * self.frame_ms
                            audio_duration_ms = len(speech_buf) * self.frame_ms
                            benchmark.record_vad_complete(duration_ms, audio_duration_ms)
                            benchmark.record_transcription(dt)
                            benchmark.record_command(cmd_name, text, True, exec_dt, early_match=True)

                            # Mark as executed, but keep collecting
                            # (in case user continues speaking)
                            command_executed = True

                            # Clear buffer and reset
                            metrics.record_vad_emit(duration_ms)
                            speech_buf.clear()
                            self._pre_buf.clear()
                            silence_ctr = 0

            else:
                # Silence/non-speech
                if speech_buf:
                    silence_ctr += 1

                    # Check for utterance end
                    if silence_ctr >= self.silence_frames or len(speech_buf) >= self.max_frames:
                        if not command_executed:
                            # No early match - do final transcription
                            audio = np.concatenate(speech_buf, dtype=np.float32)
                            duration_ms = len(speech_buf) * self.frame_ms

                            logger.info(
                                "FastVAD flushing utterance: %d frames (~%.2f s)",
                                len(speech_buf), duration_ms / 1000
                            )
                            metrics.record_vad_emit(duration_ms)

                            # Final transcription
                            t0 = time.perf_counter()
                            text = await asyncio.get_running_loop().run_in_executor(
                                None, self.transcribe, audio
                            )
                            dt = (time.perf_counter() - t0) * 1000

                            if text:
                                metrics.record_transcription(text, dt)
                                benchmark.record_vad_complete(duration_ms, duration_ms)
                                benchmark.record_transcription(dt)

                                matched, cmd_name, args, _ = self.match(text)

                                if matched and cmd_name:
                                    if not self.gate_func():
                                        logger.info("Suppressed final match '%s' from %r (wake word not active)", cmd_name, text)
                                        metrics.record_command_suppressed(cmd_name)
                                    else:
                                        metrics.record_command_match(cmd_name, matched=True)
                                        t1 = time.perf_counter()
                                        self.execute(cmd_name, args)
                                        exec_dt = (time.perf_counter() - t1) * 1000
                                        metrics.record_command_execute(cmd_name, exec_dt)
                                        benchmark.record_command(cmd_name, text, True, exec_dt, early_match=False)
                                else:
                                    metrics.record_command_match(None, matched=False)
                                    benchmark.record_command("none", text, False, 0.0, early_match=False)
                                    logger.debug("No command matched: %r", text)

                        # Reset state
                        speech_buf.clear()
                        self._pre_buf.clear()
                        silence_ctr = 0
                        frames_since_check = 0
                        command_executed = False
                        last_early_text = ""

            self.in_q.task_done()

    def _lazy_init(self):
        if self._model is not None:
            return
        self._model, _ = torch.hub.load(_REPO, _MODEL, trust_repo=True)
        self._model.eval().to("cpu")

    def _infer(self, pcm: np.ndarray) -> float:
        """Return speech probability for one frame."""
        with torch.no_grad():
            wav = torch.from_numpy(pcm).unsqueeze(0)
            logits = self._model(wav, self.sr)
            speech_logit = logits[-1, 0]
            return float(torch.sigmoid(speech_logit).item())
