"""metrics_printer.py
Optional async task to print metrics to console periodically.
"""

from __future__ import annotations

import asyncio
import logging
from rex_main.metrics import metrics

logger = logging.getLogger(__name__)


async def print_metrics_loop(interval_seconds: int = 30):
    """Periodically print metrics summary to console.

    Args:
        interval_seconds: How often to print (default: 30 seconds)
    """
    await asyncio.sleep(interval_seconds)  # Wait before first print

    while True:
        try:
            stats = metrics.get_session_stats()
            commands = metrics.get_command_frequency()

            # Only print if we have data
            if stats['total_transcriptions'] > 0 or stats.get('total_wake_words', 0) > 0:
                logger.info("=" * 50)
                logger.info("METRICS SUMMARY")
                basis = stats.get("match_rate_basis", "transcription")
                if basis == "wake_word":
                    logger.info(
                        f"  Session: {stats['session_duration_s']:.0f}s | "
                        f"Wake: {stats['total_wake_words']} | "
                        f"Commands: {stats['total_matched']} | "
                        f"Suppressed: {stats['total_suppressed']} | "
                        f"Match Rate: {stats['match_rate_percent']:.0f}% (cmd/wake)"
                    )
                else:
                    logger.info(
                        f"  Session: {stats['session_duration_s']:.0f}s | "
                        f"Commands: {stats['total_matched']} | "
                        f"Match Rate: {stats['match_rate_percent']:.0f}%"
                    )
                logger.info(f"  Latency - E2E: {stats['avg_e2e_ms'] or 0:.0f}ms | Whisper: {stats['avg_whisper_ms'] or 0:.0f}ms | Execute: {stats['avg_execute_ms'] or 0:.0f}ms")

                if commands:
                    top_3 = commands[:3]
                    cmd_str = " | ".join([f"{c['command']}: {c['count']}" for c in top_3])
                    logger.info(f"  Top Commands: {cmd_str}")

                logger.info("=" * 50)
        except Exception as e:
            logger.debug("Error printing metrics: %s", e)

        await asyncio.sleep(interval_seconds)
