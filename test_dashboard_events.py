"""Tests for the dashboard event fan-out.

Two seams, neither of which needs audio, a model, or a running server:

* ``rex._make_emitter`` — one lifecycle event reaching every observer,
  with a broken observer unable to take the others down.
* ``dashboard.events.EventHub`` — the cross-thread handoff onto a
  dashboard client's loop, and what happens when that client is slow or
  gone. See docs/DASHBOARD_PLAN.md.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from rex_main import rex
from rex_main.dashboard.events import EventHub


@pytest.fixture
def hub() -> EventHub:
    """A private hub, so tests never observe each other through the global."""
    return EventHub()


class Recorder:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def callback(self, event: str, **payload):
        self.events.append((event, payload))


# The fan-out in rex.py

def test_emitter_feeds_both_the_ui_callback_and_the_hub(monkeypatch, hub):
    monkeypatch.setattr(rex, "event_hub", hub)
    rec = Recorder()

    rex._make_emitter(rec.callback)("state.listening", window_s=6.0)

    assert rec.events == [("state.listening", {"window_s": 6.0})]
    assert hub.snapshot()["state"]["event"] == "state.listening"


def test_emitter_feeds_the_hub_with_no_ui_callback(monkeypatch, hub):
    """Console mode has no tray, but --dashboard still needs the events."""
    monkeypatch.setattr(rex, "event_hub", hub)

    rex._make_emitter(None)("no_match", text="banana phone")

    assert [e["payload"]["text"] for e in hub.snapshot()["events"]] == ["banana phone"]


def test_a_raising_sink_does_not_block_the_others(monkeypatch, hub):
    monkeypatch.setattr(rex, "event_hub", hub)

    def boom(event, **payload):
        raise RuntimeError("ui exploded")

    rex._make_emitter(boom)("match", action="skip_song", text="skip song")

    assert hub.snapshot()["events"][0]["payload"]["action"] == "skip_song"


# Recording and the connect snapshot

def test_state_and_feed_events_are_kept_apart(hub):
    hub.publish("state.listening", window_s=6.0)
    hub.publish("match", action="skip_song", text="skip song")
    hub.publish("state.idle")

    snap = hub.snapshot()
    assert snap["state"]["event"] == "state.idle"
    assert [e["event"] for e in snap["events"]] == ["match"]


def test_only_the_latest_state_is_kept(hub):
    hub.publish("state.listening", window_s=6.0)
    hub.publish("state.idle")
    assert hub.snapshot()["state"]["payload"] == {}


def test_feed_backlog_is_bounded():
    small = EventHub(recent_limit=2)
    for i in range(5):
        small.publish("no_match", text=str(i))
    assert [e["payload"]["text"] for e in small.snapshot()["events"]] == ["3", "4"]


def test_payload_is_coerced_to_something_serializable(hub):
    hub.publish("match", action="t", text="t", args=("one", object()))
    args = hub.snapshot()["events"][0]["payload"]["args"]
    assert args[0] == "one"
    assert isinstance(args[1], str)


def test_publish_with_no_subscribers_is_a_no_op(hub):
    hub.publish("state.idle")  # must not raise


# Cross-thread delivery

def test_subscriber_receives_events_in_order(hub):
    async def go():
        sub = hub.subscribe()
        hub.publish("state.listening", window_s=6.0)
        hub.publish("match", action="skip_song", text="skip song")
        first = await asyncio.wait_for(sub.queue.get(), 1)
        second = await asyncio.wait_for(sub.queue.get(), 1)
        return first, second

    first, second = asyncio.run(go())
    assert first["event"] == "state.listening"
    assert first["type"] == "event"
    assert second["payload"]["text"] == "skip song"


def test_publish_from_another_thread_reaches_the_subscriber(hub):
    """The assistant publishes from its own loop's thread, not the server's."""
    async def go():
        sub = hub.subscribe()
        threading.Thread(target=hub.publish, args=("state.idle",)).start()
        return await asyncio.wait_for(sub.queue.get(), 2)

    assert asyncio.run(go())["event"] == "state.idle"


def test_a_stalled_client_drops_oldest_instead_of_blocking(hub):
    """A browser tab that stopped reading must never stall the audio loop."""
    async def go():
        sub = hub.subscribe()
        for i in range(sub.queue.maxsize + 5):
            hub.publish("no_match", text=str(i))
        await asyncio.sleep(0)  # let the queued puts run
        assert sub.queue.full()
        return sub.queue.get_nowait()

    # 5 dropped off the front, so the oldest survivor is #5.
    assert asyncio.run(go())["payload"]["text"] == "5"


def test_a_dead_subscriber_is_dropped_not_raised(hub):
    loop = asyncio.new_event_loop()
    try:
        sub = loop.run_until_complete(_subscribe(hub))
    finally:
        loop.close()

    hub.publish("state.idle")  # loop is closed under the subscription

    assert sub not in hub._subscribers


async def _subscribe(hub: EventHub):
    return hub.subscribe()


# The WebSocket frame contract the dashboard front-end reads

@pytest.fixture
def ws_client():
    """A test client over the real app. Skipped without starlette's httpx dep."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from rex_main.dashboard import server

    server._should_stop.clear()
    with TestClient(server._get_app()) as client:
        yield client


def _receive_until(ws, frame_type: str, limit: int = 5) -> dict:
    for _ in range(limit):
        frame = ws.receive_json()
        if frame["type"] == frame_type:
            return frame
    raise AssertionError(f"no {frame_type!r} frame in {limit} reads")


def test_socket_opens_with_a_snapshot_then_metrics(ws_client):
    with ws_client.websocket_connect("/ws") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert "state" in snap and isinstance(snap["events"], list)

        first = ws.receive_json()
        assert first["type"] == "metrics"
        assert {"stats", "recent", "commands"} <= set(first)


def test_events_are_pushed_without_waiting_for_the_tick(ws_client):
    from rex_main.dashboard.events import event_hub

    with ws_client.websocket_connect("/ws") as ws:
        _receive_until(ws, "metrics")
        event_hub.publish("match", action="skip_song", text="skip song")

        frame = _receive_until(ws, "event")
        assert frame["event"] == "match"
        assert frame["payload"] == {"action": "skip_song", "text": "skip song"}
        assert isinstance(frame["ts"], float)
