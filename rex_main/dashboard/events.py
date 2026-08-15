"""dashboard/events.py
EventHub — fans assistant lifecycle events out to dashboard WebSocket clients.

The assistant owns one thread and asyncio loop (its own in console mode, a
Qt worker thread under the tray); the dashboard server owns another. So
publishing crosses threads — the same problem ``UiBridge`` solves with Qt
queued connections. Here the handoff is ``loop.call_soon_threadsafe`` onto
a queue owned by the subscriber's loop.

Two properties this module exists to guarantee:

* ``publish`` sits on the audio path. It never blocks and never raises —
  a stalled browser tab loses events, it does not slow REX down.
* Events reach the socket immediately. The 1 s tick in ``server.py`` stays
  for resource meters, which don't need to be event-driven.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Per-client backlog. Small on purpose: a client this far behind wants the
# current state, not a minute of history it will render and immediately
# scroll away.
QUEUE_LIMIT = 64

# How many non-state events a freshly connected client is handed, so the
# live feed isn't empty until the next utterance.
RECENT_LIMIT = 16


def _jsonable(value: Any) -> Any:
    """Coerce a payload to something ``send_json`` can't choke on.

    Payloads carry regex capture groups and handler args, so an exotic
    value is possible. Stringifying it costs a nicer render; letting it
    reach ``json.dumps`` costs the client its connection.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


class Subscription:
    """One dashboard client's inbox, bound to the loop that created it."""

    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int = QUEUE_LIMIT):
        self._loop = loop
        self.queue: "asyncio.Queue[dict]" = asyncio.Queue(maxsize=maxsize)

    def offer(self, record: dict) -> None:
        """Hand a record to the subscriber's loop. Called from any thread."""
        self._loop.call_soon_threadsafe(self._put, record)

    def _put(self, record: dict) -> None:  # runs on the subscriber's loop
        if self.queue.full():
            # Drop oldest rather than block: the panel is a live readout,
            # and the newest event is the one worth keeping.
            self.queue.get_nowait()
        self.queue.put_nowait(record)


class EventHub:
    """Broadcasts ``(event, payload)`` from the assistant to subscribers."""

    def __init__(self, recent_limit: int = RECENT_LIMIT):
        self._lock = threading.Lock()
        self._subscribers: set[Subscription] = set()
        self._recent: deque[dict] = deque(maxlen=recent_limit)
        self._last_state: Optional[dict] = None

    def publish(self, event: str, **payload: Any) -> None:
        """Record an event and push it to every subscriber. Never raises."""
        record = {
            "type": "event",
            "event": event,
            "ts": time.time(),
            "payload": _jsonable(payload),
        }

        with self._lock:
            if event.startswith("state."):
                self._last_state = record
            else:
                self._recent.append(record)
            subscribers = list(self._subscribers)

        for sub in subscribers:
            try:
                sub.offer(record)
            except Exception:
                # Loop closed under us (dashboard stopped mid-publish), or
                # the subscriber is otherwise gone. Never the audio loop's problem.
                logger.debug("dropping dashboard subscriber: offer failed")
                self.unsubscribe(sub)

    def subscribe(self) -> Subscription:
        """Register the calling loop as a subscriber. Call from that loop."""
        sub = Subscription(asyncio.get_running_loop())
        with self._lock:
            self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    def snapshot(self) -> dict:
        """Current state plus the recent feed, for a client that just connected."""
        with self._lock:
            return {"state": self._last_state, "events": list(self._recent)}


event_hub = EventHub()
