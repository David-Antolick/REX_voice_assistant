# REX Dashboard Plan

Design spec for the second-monitor telemetry panel. Like the other plan
docs, this locks in *what* and *why* before code.

## What this is

A read-only panel that lives open on a second monitor while you work, so
REX is visible without being in the way. Two jobs, in priority order:

1. **Is it hearing me right now?** Live state, the last thing it
   recognized, and a running feed of what it heard.
2. **Is it healthy, and what is it costing?** Match rate, latency, and
   CPU / GPU / VRAM / temperature.

You never click it. It is a readout, not a control surface.

## Relationship to UI_PLAN

[UI_PLAN.md](UI_PLAN.md) says "The web dashboard idea is dead... opening
a browser tab to localhost feels wrong for an always-on background app."
That judgment was about the browser tab as the **control surface** — the
place you'd go to change settings — and it still holds. Settings belong
in the tray dialog.

This is a different artifact. A passive readout you never interact with
doesn't carry the objection that killed the original. The tray remains
REX's home; this is a window onto it.

Recorded in [DECISIONS.md](DECISIONS.md) as superseding that line.

## What already exists

More than the "dead code" label suggested. `rex_main/dashboard/` is
~1,200 lines and the telemetry plumbing is done:

| Endpoint | Serves |
|---|---|
| `/api/stats` | session stats — counts, match rate, latency percentiles |
| `/api/commands` | command frequency |
| `/api/recent` | recent transcriptions |
| `/api/history` | latency history (time series) |
| `/api/benchmark` | CPU per-core, memory, GPU util, VRAM, GPU temperature |
| `/ws` | WebSocket pushing stats + recent + commands + resources every 1 s |

`fastapi`, `uvicorn[standard]` and `websockets` are already base
dependencies. Binds `127.0.0.1` only — keep it that way.

**The revamp is a presentation-layer rewrite**, not a new system. Server
endpoints stay; `dashboard/static/` (967 lines of HTML/CSS/JS) is
replaced.

## The one backend gap

The WebSocket polls `metrics` on a 1 s tick. Good enough for resource
meters, wrong for the "is it hearing me" half: state transitions
(`state.listening` → `state.idle`) and per-utterance `match` / `no_match`
events reach only the Qt bridge via `run_assistant`'s `ui_callback`. On a
1 s poll they arrive late and the transitions are lost entirely.

Fix: fan `ui_callback` out to the dashboard as a second sink alongside
the Qt bridge, and push those events immediately. Keep the 1 s tick for
resources.

Landed. `run_assistant` now builds its emitter with `_make_emitter`,
which fans every lifecycle event to the Qt bridge *and* to
`dashboard/events.py`'s `event_hub` — the dashboard is a sink even in
console mode, where there is no tray. The hub hands each event to the
server's loop with `call_soon_threadsafe` (the cross-thread move
`UiBridge` makes with queued connections), onto a bounded per-client
queue that drops oldest when a browser tab stops reading. A stalled or
dead client cannot block the audio loop.

### Wire contract

Three frame types on `/ws`, discriminated by `type`:

```jsonc
// once, on connect — so the panel isn't blank until the next utterance
{"type": "snapshot",
 "state": {…event record…} | null,      // last state.* seen
 "events": [{…event record…}, …]}       // last ≤16 non-state events, oldest first

// immediately, when it happens
{"type": "event", "event": "state.listening", "ts": 1755300000.12,
 "payload": {"window_s": 6.0}}

// every 1 s — unchanged apart from the added "type"
{"type": "metrics", "stats": {…}, "recent": […], "commands": […],
 "resources": {…}}                      // "resources" absent if benchmark deps are missing
```

Event names and payloads:

| `event` | `payload` |
|---|---|
| `state.idle` | `{}` |
| `state.listening` | `{"window_s": float}` |
| `match` | `{"action": str, "text": str, "args": [str, …]}` |
| `no_match` | `{"text": str}` |

`state.paused` / `state.error` are in the callback vocabulary but nothing
emits them today. Treat unknown event names as ignorable.

**Phase 0 is what makes this feed trustworthy.** Before the dispatch
seam landed, `no_match` was never emitted on the default (low-latency)
path — an ambient "didn't catch that" feed would have been silently,
permanently empty. Same root cause, same fix. See
[PHASE0_DISPATCH.md](PHASE0_DISPATCH.md).

## Layout

Three zones, ordered by how often you need them.

### Zone 1 — NOW (~50% of height)

The reason the panel exists.

- **State**, as a word, not an icon: *Idle* / *Listening* / *Thinking*.
  When the wake gate is open, the remaining window depletes as a bar.
- **Hero: the last recognized command.** ≥48px, same sans as everything
  else, proportional figures (not `tabular-nums` — equal-width digits
  read loose at display sizes).
- **Live feed**, last ~8 utterances, newest on top: glyph + text +
  latency. Matched carries `✓` in the accent; unmatched carries `·` in
  muted ink. The glyph means state is never color-alone.

### Zone 2 — HEALTH

| Metric | Form | Why not |
|---|---|---|
| Match rate | **meter** (single ratio vs limit) | not a 2-slice donut |
| E2E latency | **single-series sparkline**, endpoint label only | not a 3-line breakdown — one series needs no legend, and "is it healthy" is a glance, not a study |
| Wake confidence (last fire) | meter with the 0.5 threshold as a hairline | — |

Latency decomposition (Whisper vs execute) belongs on hover, not on the
face.

### Zone 3 — COST (smallest)

Four linear meters: **CPU / GPU / VRAM / temperature**. One-hue track,
status color only on threshold crossing, each with a text label and
value so status never rests on hue alone.

**Not donut gauges.** Every resource dashboard reaches for them and they
are a 2-slice pie for a single ratio — slower to read and they don't
stack. Linear meters do both jobs better.

## Color

Validated with the data-viz validator against the dark surface
(`#1a1a19`), not eyeballed.

- **Accent** `#3987e5` — passes every check (chroma, contrast ≥3:1).
- **Meter scale is 3-state**: good `#0ca30c` / warning `#fab219` /
  critical `#d03b3b`. Worst adjacent CVD ΔE 11.3, normal-vision ΔE 27.6,
  all ≥3:1 on surface.
- **The 4-state scale was rejected.** Adding `serious` `#ec835a` put
  `warning`↔`serious` at normal-vision ΔE 13.6 — below the 15 floor, so
  two adjacent meters in those states would be hard to separate even with
  full color vision. Three states, more legible.
- Status roles ship with label + value, per the reserved-status rule.
- Command-frequency bars, if shown, use the sequential blue ramp — one
  hue, more-is-darker. Never categorical hues; the bars are magnitude,
  not identity.

Text wears text tokens, never a series color.

## Ambient constraints

These matter more than the chart choices, and they're what separate this
from a generic dashboard.

- **No idle motion.** No spinners, no breathing animations, no
  auto-scroll. Movement means a real event happened — that is what lets
  peripheral vision catch it while you're working on something else.
- **Burn-in safety.** This sits static for hours. No large
  max-brightness blocks; the hero area dims to a resting state after
  idle.
- **Legible at 1.5–2 m.** Hero ≥48px, feed ≥16px, labels ≥14px. This is
  the constraint that rules out a dense chart grid — you are reading it
  from across a desk, not leaning in.
- **Dark-committed**, not a theme toggle bolted on. It runs at night
  beside a game.
- **Degrade quietly.** No data / REX not running is a resting state, not
  an error splash.

## Out of scope

- **Any control.** No buttons, no settings, no pause toggle. The moment
  it takes input it becomes the thing UI_PLAN rejected.
- **Remote access.** Localhost only, forever. No LAN binding, no phone
  view — that breaks the offline promise.
- **Auth.** Unnecessary while it is loopback-only and read-only.
- **Historical persistence across sessions.** `benchmark.py` already
  exports session JSON; this panel is live-only.
- **Replacing the HUD.** The HUD is in your eyeline during a game; this
  is on another screen. Both exist.

## Success criteria

1. You can tell, from across the desk without leaning in, whether REX
   just heard you and whether it understood.
2. Nothing on screen moves unless something actually happened.
3. State and match events appear immediately, not up to a second late.
4. Left open for a full work session, it neither burns in nor pulls your
   attention.
5. It never becomes something you click.
