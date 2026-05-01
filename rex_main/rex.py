"""rex.py
Entry-point for the REX voice assistant.

Run with the CLI:
    rex           # Start the assistant
    rex setup     # Interactive setup wizard
    rex status    # Show configuration status

Or directly:
    python -m rex_main.rex

Press **Ctrl-C** to exit cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from typing import Optional, Any, Callable
import numpy as np

from rex_main.audio_stream import AudioStream
from rex_main.vad_stream import SileroVAD
from rex_main.whisper_worker import WhisperWorker
from rex_main.matcher import dispatch_command, COMMAND_PATTERNS, NO_EARLY_MATCH_COMMANDS
from rex_main.metrics_printer import print_metrics_loop
from rex_main.benchmark import benchmark
from rex_main import actions

import logging
logger = logging.getLogger("rex")


# CLI

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the REX voice assistant")
    p.add_argument(
        "--model",
        default="medium",
        help="Whisper model size (tiny|base|small|medium|large or distil-*)",
    )
    p.add_argument(
        "--device",
        default="auto",
        choices=["cuda", "cpu", "auto"],
        help="Device for Whisper model (auto will use CPU unless --device cuda is explicit)",
    )
    p.add_argument(
        "--beam",
        type=int,
        default=1,
        help="Beam size for Whisper decoding (1 is fastest)",
    )
    p.add_argument(
        "--log_file",
        type=str,
        default="rex_main/logs/rex_log.log",
        help="Path to write rotating logs",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Verbose logging",
    )
    return p.parse_args(argv)


# Main orchestration

async def run_assistant(
    opts: Any,
    config: Optional[dict] = None,
    ui_callback: Optional[Callable[..., None]] = None,
    paused: Optional[Any] = None,
):
    """Main entry-point coroutine for the voice assistant.

    Args:
        opts: Options object with model, device, beam, log_file, debug attributes
        config: Optional configuration dictionary (used for pulse_server, etc.)
        ui_callback: Optional callable invoked with lifecycle events. Called as
            ``ui_callback(event: str, **payload)``. Events:
            ``"state.idle"``, ``"state.listening"``, ``"state.paused"``,
            ``"state.error"``, ``"match"`` (action, text, args),
            ``"no_match"`` (text). Exceptions are caught and logged.
        paused: Optional ``threading.Event``-like object. When set,
            ``dispatch_command`` drops incoming utterances silently.
    """

    def _emit(event: str, **payload):
        if ui_callback is None:
            return
        try:
            ui_callback(event, **payload)
        except Exception:
            logger.exception("ui_callback raised on %s event", event)

   # ___ Logging setup ___
    root = logging.getLogger()
    # Remove any existing handlers
    for h in root.handlers[:]:
        root.removeHandler(h)
    level = logging.DEBUG if opts.debug else logging.INFO
    root.setLevel(level)

    # Create a common formatter
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    # Console handler (always)
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating file handler
    if opts.log_file:
        from logging.handlers import RotatingFileHandler
        fileh = RotatingFileHandler(
            opts.log_file,
            maxBytes=2_000_000,
            backupCount=2,
        )
        fileh.setLevel(level)
        fileh.setFormatter(formatter)
        root.addHandler(fileh)

    # Suppress other loggers to warning level
    logging.getLogger("torio._extension.utils").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    # ___ End logging setup ___

    # Check service configuration and show startup info
    active_service = "none"
    if config:
        active_service = config.get("services", {}).get("active", "none")

    logger.info("=" * 50)
    logger.info("REX Voice Assistant starting...")
    logger.info("Model: %s | Device: %s | Beam: %d", opts.model, opts.device, opts.beam)
    logger.info("Active service: %s", active_service)

    if active_service == "none":
        logger.warning("No music service configured - running in transcription-only mode")
        logger.warning("Run 'rex setup' to configure YTMD or Spotify")

    logger.info("=" * 50)
    logger.info("Listening... (Press Ctrl+C to exit)")

    # Queues
    audio_q: "asyncio.Queue[np.ndarray]" = asyncio.Queue(maxsize=50)
    speech_q: "asyncio.Queue[np.ndarray]" = asyncio.Queue(maxsize=10)
    text_q: "asyncio.Queue[str]" = asyncio.Queue(maxsize=10)

    # Get pulse_server from config if available
    pulse_server = None
    if config:
        pulse_server = config.get("audio", {}).get("pulse_server")

    # Wake-word configuration. CLI flags override config: --wake-word/--no-wake-word
    # for the on/off gate, --wake-model for the model identifier (used by --gaming).
    wake_cfg = (config or {}).get("wake_word", {}) if config else {}
    cli_wake = getattr(opts, "wake_word", None)
    wake_enabled = cli_wake if cli_wake is not None else bool(wake_cfg.get("enabled", False))

    cli_wake_model = getattr(opts, "wake_model", None)
    wake_model_id = cli_wake_model or wake_cfg.get("model", "hey_rex")

    from rex_main.wake_word import ListeningState, WakeWordDetector
    listening_state = ListeningState(
        gate_enabled=wake_enabled,
        default_window_s=float(wake_cfg.get("listening_window_seconds", 6)),
    )

    # Wrap activate/deactivate so the UI hears about wake-word fires and
    # listening-window expirations. No-op when ui_callback is None.
    if ui_callback is not None:
        _orig_activate = listening_state.activate
        _orig_deactivate = listening_state.deactivate
        _listening_window = float(wake_cfg.get("listening_window_seconds", 6))

        def _activate(window_s: Optional[float] = None) -> None:
            _orig_activate(window_s)
            _emit("state.listening", window_s=window_s or _listening_window)

        def _deactivate() -> None:
            _orig_deactivate()
            _emit("state.idle")

        listening_state.activate = _activate  # type: ignore[method-assign]
        listening_state.deactivate = _deactivate  # type: ignore[method-assign]

    _emit("state.idle")

    wake_audio_q: Optional["asyncio.Queue[np.ndarray]"] = None
    wake_detector: Optional[WakeWordDetector] = None
    if wake_enabled:
        wake_audio_q = asyncio.Queue(maxsize=50)
        wake_detector = WakeWordDetector(
            wake_audio_q,
            listening_state,
            model=wake_model_id,
            threshold=float(wake_cfg.get("threshold", 0.5)),
            debounce_seconds=float(wake_cfg.get("debounce_seconds", 1.0)),
            cue_enabled=bool(wake_cfg.get("cue_enabled", True)),
        )
        logger.info("Wake-word gating enabled (model=%s, threshold=%.2f)",
                    wake_detector.model_name, wake_detector.threshold)

    # Determine VAD mode based on low-latency setting
    low_latency = getattr(opts, 'low_latency', False)

    # Initialize benchmark tracking
    mode = "low-latency" if low_latency else "standard"
    benchmark.set_session_info(mode=mode, model=opts.model)
    benchmark.start_monitoring(interval_seconds=1.0)
    logger.info("Benchmark monitoring started (data saved to ~/.rex/benchmarks/)")

    audio_taps = [wake_audio_q] if wake_audio_q is not None else []
    async with AudioStream(audio_q, pulse_server=pulse_server, tap_queues=audio_taps):
        whisper = WhisperWorker(
            speech_q,
            text_q,
            model_name=opts.model,
            device=opts.device,
            beam_size=opts.beam,
        )

        # Pre-warm Whisper model to eliminate cold-start latency
        whisper.warmup()

        if low_latency:
            # Use FastVAD with early command detection for lowest latency
            from rex_main.fast_vad import FastVAD
            logger.info("Low-latency mode: Using FastVAD with early command detection")

            # Create helper functions for FastVAD
            def transcribe_sync(audio: np.ndarray) -> str:
                return whisper._transcribe(audio)

            def match_command(text: str) -> tuple[bool, Optional[str], tuple, bool]:
                """Check if text matches any command pattern.

                Returns: (matched, func_name, args, allow_early_match)
                """
                text = text.strip()
                for pattern, func_name in COMMAND_PATTERNS:
                    m = pattern.match(text)
                    if m:
                        allow_early = func_name not in NO_EARLY_MATCH_COMMANDS
                        return (True, func_name, m.groups(), allow_early)
                return (False, None, (), True)

            def execute_command(func_name: str, args: tuple) -> None:
                """Execute a matched command, respecting the wake-word gate."""
                if paused is not None and paused.is_set():
                    return
                if not listening_state.is_active():
                    logger.debug("Command '%s' suppressed - wake word not active", func_name)
                    try:
                        from rex_main.metrics import metrics as _metrics
                        _metrics.record_command_suppressed(func_name)
                    except Exception:
                        pass
                    return
                # Refresh window so multi-step interactions work without re-waking.
                listening_state.activate()
                func = actions.resolve_handler(func_name)
                _emit("match", action=func_name, text="", args=args)
                if callable(func):
                    func(*args)

            fast_vad = FastVAD(
                audio_q,
                transcribe_func=transcribe_sync,
                match_func=match_command,
                execute_func=execute_command,
                silence_ms=250,
                min_speech_ms=300,
                early_check_interval_ms=200,
                gate_func=listening_state.is_active,
            )

            tasks = [
                asyncio.create_task(fast_vad.run(), name="fast_vad"),
                asyncio.create_task(print_metrics_loop(30), name="metrics_printer"),
            ]
        else:
            # Standard mode with separate VAD -> Whisper -> Matcher pipeline
            vad = SileroVAD(audio_q, speech_q, silence_ms=400)

            tasks = [
                asyncio.create_task(vad.run(), name="vad"),
                asyncio.create_task(whisper.run(), name="whisper"),
                asyncio.create_task(
                    dispatch_command(
                        text_q,
                        listening_state=listening_state,
                        ui_callback=ui_callback,
                        paused=paused,
                    ),
                    name="matcher",
                ),
                asyncio.create_task(print_metrics_loop(30), name="metrics_printer"),
            ]

        if wake_detector is not None:
            tasks.append(asyncio.create_task(wake_detector.run(), name="wake_word"))

        # Handle Ctrl-C for graceful shutdown
        # Note: add_signal_handler doesn't work on Windows, but KeyboardInterrupt is caught
        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                asyncio.get_running_loop().add_signal_handler(sig, _cancel_tasks, tasks)

        # Wait until the first task raises (ideally never) or is cancelled
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Shutdown requested - waiting for tasks to finish...")
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            # Stop benchmark monitoring and export session data
            benchmark.stop_monitoring()
            try:
                filepath = benchmark.export_session()
                summary = benchmark.get_session_summary()
                logger.info("=" * 50)
                logger.info("SESSION SUMMARY")
                logger.info("  Mode: %s | Model: %s", summary.mode, summary.model)
                logger.info("  Commands: %d matched / %d total (%.1f%%)",
                           summary.matched_commands, summary.total_commands, summary.match_rate)
                logger.info("  Avg E2E: %.0fms | P95: %.0fms", summary.avg_e2e_ms, summary.p95_e2e_ms)
                logger.info("  Avg CPU: %.1f%% | Avg GPU: %.1f%%", summary.avg_cpu_percent, summary.avg_gpu_percent)
                logger.info("  Benchmark saved: %s", filepath)
                logger.info("=" * 50)
            except Exception as e:
                logger.warning("Failed to export benchmark: %s", e)


def _cancel_tasks(tasks: list[asyncio.Task]):
    logger.info("Cancelling %d tasks…", len(tasks))
    for t in tasks:
        t.cancel()


# Legacy alias for backwards compatibility
main = run_assistant


# Entry-point
if __name__ == "__main__":
    asyncio.run(run_assistant(parse_args()))
