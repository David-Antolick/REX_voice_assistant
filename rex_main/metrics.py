"""metrics.py
Metrics collection and aggregation for REX Voice Assistant.

Provides a singleton MetricsCollector that tracks:
- Command match rates (matched vs unmatched)
- Per-stage latencies (VAD, Whisper, command execution)
- Command frequency
- End-to-end latency from speech start to command execution

Usage:
    from rex_main.metrics import metrics

    metrics.record_speech_start()
    metrics.record_vad_emit(duration_ms=1200)
    metrics.record_transcription("play music", latency_ms=150)
    metrics.record_command_match("play_music", matched=True)
    metrics.record_command_execute("play_music", latency_ms=85)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List, Any
import logging

logger = logging.getLogger(__name__)

__all__ = ["metrics", "MetricsCollector", "MetricEvent", "EventType"]


class EventType(Enum):
    """Types of metric events tracked."""
    SPEECH_START = "speech_start"
    VAD_EMIT = "vad_emit"
    TRANSCRIPTION = "transcription"
    COMMAND_MATCH = "command_match"
    COMMAND_EXECUTE = "command_execute"
    WAKE_WORD_DETECTED = "wake_word_detected"
    COMMAND_SUPPRESSED = "command_suppressed"


@dataclass
class MetricEvent:
    """A single metric event."""
    timestamp: float  # time.time()
    event_type: EventType
    latency_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandStats:
    """Aggregated stats for a single command."""
    name: str
    match_count: int = 0
    total_execute_ms: float = 0.0

    @property
    def avg_execute_ms(self) -> float:
        return self.total_execute_ms / self.match_count if self.match_count > 0 else 0.0


class MetricsCollector:
    """Thread-safe metrics collector with circular buffer for history.

    Tracks the full pipeline: speech_start -> vad_emit -> transcription -> match -> execute
    """

    def __init__(self, max_events: int = 10000, max_recent: int = 100):
        """
        Args:
            max_events: Maximum events to retain in history
            max_recent: Maximum recent transcriptions to show in dashboard
        """
        self._lock = threading.RLock()

        # Event history (circular buffer)
        self._events: deque[MetricEvent] = deque(maxlen=max_events)

        # Recent transcriptions for dashboard display
        self._recent_transcriptions: deque[Dict[str, Any]] = deque(maxlen=max_recent)

        # Aggregated counters
        self._total_transcriptions = 0
        self._total_matched = 0
        self._total_unmatched = 0
        self._total_wake_words = 0
        self._total_suppressed = 0

        # Per-command stats
        self._command_stats: Dict[str, CommandStats] = {}

        # Latency accumulators
        self._vad_latencies: deque[float] = deque(maxlen=1000)
        self._whisper_latencies: deque[float] = deque(maxlen=1000)
        self._execute_latencies: deque[float] = deque(maxlen=1000)
        self._e2e_latencies: deque[float] = deque(maxlen=1000)  # end-to-end

        # Current pipeline tracking (for end-to-end calculation)
        self._current_speech_start: Optional[float] = None
        self._current_vad_emit: Optional[float] = None
        self._current_transcription_done: Optional[float] = None

        # Session start time
        self._session_start = time.time()

        logger.debug("MetricsCollector initialized")

    def record_speech_start(self) -> None:
        """Record when VAD first detects speech in an utterance."""
        with self._lock:
            now = time.time()
            self._current_speech_start = now
            self._events.append(MetricEvent(
                timestamp=now,
                event_type=EventType.SPEECH_START
            ))

    def record_vad_emit(self, duration_ms: float) -> None:
        """Record when VAD emits a complete utterance.

        Args:
            duration_ms: Duration of the utterance in milliseconds
        """
        with self._lock:
            now = time.time()
            self._current_vad_emit = now

            # Calculate VAD processing latency (silence detection overhead)
            vad_latency = None
            if self._current_speech_start:
                # This is the time from speech start to utterance emission
                # Includes: speech duration + silence timeout
                vad_latency = (now - self._current_speech_start) * 1000
                self._vad_latencies.append(vad_latency)

            self._events.append(MetricEvent(
                timestamp=now,
                event_type=EventType.VAD_EMIT,
                latency_ms=vad_latency,
                metadata={"duration_ms": duration_ms}
            ))

    def record_transcription(self, text: str, latency_ms: float) -> None:
        """Record a completed transcription.

        Args:
            text: The transcribed text
            latency_ms: Time taken by Whisper to transcribe
        """
        with self._lock:
            now = time.time()
            self._current_transcription_done = now
            self._total_transcriptions += 1
            self._whisper_latencies.append(latency_ms)

            self._events.append(MetricEvent(
                timestamp=now,
                event_type=EventType.TRANSCRIPTION,
                latency_ms=latency_ms,
                metadata={"text": text}
            ))

            # Add to recent transcriptions (will be updated with match info)
            self._recent_transcriptions.append({
                "timestamp": now,
                "text": text,
                "whisper_ms": latency_ms,
                "matched": None,
                "command": None,
                "execute_ms": None,
                "e2e_ms": None
            })

    def record_wake_word(self, score: float, model: str = "") -> None:
        """Record a wake-word detection event."""
        with self._lock:
            self._total_wake_words += 1
            self._events.append(MetricEvent(
                timestamp=time.time(),
                event_type=EventType.WAKE_WORD_DETECTED,
                metadata={"score": float(score), "model": model},
            ))

    def record_command_suppressed(self, command_name: str) -> None:
        """Record a command that matched but was gated off (no active listening window)."""
        with self._lock:
            self._total_suppressed += 1
            self._events.append(MetricEvent(
                timestamp=time.time(),
                event_type=EventType.COMMAND_SUPPRESSED,
                metadata={"command": command_name},
            ))

    def record_command_match(self, command_name: Optional[str], matched: bool) -> None:
        """Record whether a transcription matched a command.

        Args:
            command_name: Name of matched command (or None if no match)
            matched: Whether a command was matched
        """
        with self._lock:
            now = time.time()

            if matched:
                self._total_matched += 1
                if command_name not in self._command_stats:
                    self._command_stats[command_name] = CommandStats(name=command_name)
                self._command_stats[command_name].match_count += 1
            else:
                self._total_unmatched += 1

            self._events.append(MetricEvent(
                timestamp=now,
                event_type=EventType.COMMAND_MATCH,
                metadata={"command": command_name, "matched": matched}
            ))

            # Update recent transcription
            if self._recent_transcriptions:
                self._recent_transcriptions[-1]["matched"] = matched
                self._recent_transcriptions[-1]["command"] = command_name

    def record_command_execute(self, command_name: str, latency_ms: float) -> None:
        """Record command execution completion.

        Args:
            command_name: Name of the executed command
            latency_ms: Time taken to execute the command
        """
        with self._lock:
            now = time.time()
            self._execute_latencies.append(latency_ms)

            # Update command stats
            if command_name in self._command_stats:
                self._command_stats[command_name].total_execute_ms += latency_ms

            # Calculate end-to-end latency
            e2e_ms = None
            if self._current_speech_start:
                e2e_ms = (now - self._current_speech_start) * 1000
                self._e2e_latencies.append(e2e_ms)

            self._events.append(MetricEvent(
                timestamp=now,
                event_type=EventType.COMMAND_EXECUTE,
                latency_ms=latency_ms,
                metadata={"command": command_name, "e2e_ms": e2e_ms}
            ))

            # Update recent transcription
            if self._recent_transcriptions:
                self._recent_transcriptions[-1]["execute_ms"] = latency_ms
                self._recent_transcriptions[-1]["e2e_ms"] = e2e_ms

            # Reset pipeline tracking
            self._current_speech_start = None
            self._current_vad_emit = None
            self._current_transcription_done = None

    def get_session_stats(self) -> Dict[str, Any]:
        """Get aggregated statistics for the current session.

        When wake words have fired, match_rate is computed as
        matched_commands / wake_words (i.e., "did each wake produce a command?").
        Otherwise it falls back to matched / (matched + unmatched).
        """
        with self._lock:
            if self._total_wake_words > 0:
                match_rate = self._total_matched / self._total_wake_words * 100
                match_rate_basis = "wake_word"
            else:
                total = self._total_matched + self._total_unmatched
                match_rate = (self._total_matched / total * 100) if total > 0 else 0.0
                match_rate_basis = "transcription"

            return {
                "session_duration_s": time.time() - self._session_start,
                "total_transcriptions": self._total_transcriptions,
                "total_matched": self._total_matched,
                "total_unmatched": self._total_unmatched,
                "total_wake_words": self._total_wake_words,
                "total_suppressed": self._total_suppressed,
                "match_rate_percent": round(match_rate, 1),
                "match_rate_basis": match_rate_basis,
                "avg_vad_ms": self._avg(self._vad_latencies),
                "avg_whisper_ms": self._avg(self._whisper_latencies),
                "avg_execute_ms": self._avg(self._execute_latencies),
                "avg_e2e_ms": self._avg(self._e2e_latencies),
                "p95_e2e_ms": self._percentile(self._e2e_latencies, 95),
            }

    def get_command_frequency(self) -> List[Dict[str, Any]]:
        """Get command usage frequency, sorted by count."""
        with self._lock:
            return sorted(
                [
                    {
                        "command": stats.name,
                        "count": stats.match_count,
                        "avg_execute_ms": round(stats.avg_execute_ms, 1)
                    }
                    for stats in self._command_stats.values()
                ],
                key=lambda x: x["count"],
                reverse=True
            )

    def get_recent_transcriptions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent transcriptions for dashboard display."""
        with self._lock:
            items = list(self._recent_transcriptions)[-limit:]
            # Format for display
            return [
                {
                    "time": time.strftime("%H:%M:%S", time.localtime(item["timestamp"])),
                    "text": item["text"][:50] + "..." if len(item["text"]) > 50 else item["text"],
                    "matched": item["matched"],
                    "command": item["command"],
                    "whisper_ms": round(item["whisper_ms"], 0) if item["whisper_ms"] else None,
                    "execute_ms": round(item["execute_ms"], 0) if item["execute_ms"] else None,
                    "e2e_ms": round(item["e2e_ms"], 0) if item["e2e_ms"] else None,
                }
                for item in reversed(items)  # Most recent first
            ]

    def get_latency_history(self, minutes: int = 60) -> Dict[str, List[Dict[str, Any]]]:
        """Get latency time-series data for charts.

        Args:
            minutes: How many minutes of history to return
        """
        with self._lock:
            cutoff = time.time() - (minutes * 60)

            # Filter events by time and type
            vad_points = []
            whisper_points = []
            e2e_points = []

            for event in self._events:
                if event.timestamp < cutoff:
                    continue

                ts = event.timestamp * 1000  # JavaScript timestamp

                if event.event_type == EventType.VAD_EMIT and event.latency_ms:
                    vad_points.append({"x": ts, "y": event.latency_ms})
                elif event.event_type == EventType.TRANSCRIPTION and event.latency_ms:
                    whisper_points.append({"x": ts, "y": event.latency_ms})
                elif event.event_type == EventType.COMMAND_EXECUTE:
                    if event.metadata.get("e2e_ms"):
                        e2e_points.append({"x": ts, "y": event.metadata["e2e_ms"]})

            return {
                "vad": vad_points,
                "whisper": whisper_points,
                "e2e": e2e_points
            }

    def reset(self) -> None:
        """Reset all metrics (for new session)."""
        with self._lock:
            self._events.clear()
            self._recent_transcriptions.clear()
            self._total_transcriptions = 0
            self._total_matched = 0
            self._total_unmatched = 0
            self._total_wake_words = 0
            self._total_suppressed = 0
            self._command_stats.clear()
            self._vad_latencies.clear()
            self._whisper_latencies.clear()
            self._execute_latencies.clear()
            self._e2e_latencies.clear()
            self._current_speech_start = None
            self._current_vad_emit = None
            self._current_transcription_done = None
            self._session_start = time.time()
            logger.info("Metrics reset")

    @staticmethod
    def _avg(values: deque) -> Optional[float]:
        """Calculate average, returns None if empty."""
        if not values:
            return None
        return round(sum(values) / len(values), 1)

    @staticmethod
    def _percentile(values: deque, p: int) -> Optional[float]:
        """Calculate percentile, returns None if empty."""
        if not values:
            return None
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * p / 100)
        idx = min(idx, len(sorted_vals) - 1)
        return round(sorted_vals[idx], 1)


# Global singleton instance
metrics = MetricsCollector()
