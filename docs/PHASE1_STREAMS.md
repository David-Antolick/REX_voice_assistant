# Phase 1 — parallel work streams

How Phase 1 of [PC_CONTROL_PLAN.md](PC_CONTROL_PLAN.md) is split so
several agents can work at once without colliding.

**If you are an agent: find your stream, read only what it points at, and
respect the file-ownership table.** The ownership table is the contract —
everything else here is context.

## The constraint that shapes the split

REX enforces **one file per backend** ([ACTIONS.md](ACTIONS.md)), and
three files are touched by nearly every stream. So the split is drawn
along backend-file boundaries, and shared files get explicit rules.

That is also why **"system" is two backends, not one**:
`system_audio` (Core Audio COM) and `system_window` (`user32`). Different
Windows APIs, different failure modes, and the split is what lets two
agents work at once. Both are `slot=None`, transport `os_native`.

## Wave 0 — serial, do before Wave 1

| # | Task | Blocks |
|---|---|---|
| **0a** | `uv tool install --force .` so the global `rex` runs current code | nothing, but do it first |
| **0b** | ~~Volume API spike~~ — **DONE**, see below | ~~Stream A~~ unblocked |

**0a matters more than it looks.** Bare `rex` resolves to the uv tool
install, which was three months stale and had a dead wake gate. A full
debugging session was lost to testing the wrong build.

### 0b result — `ctypes` works, no dependency needed

Spiked and verified on 2026-08-15. Working reference implementation:
**[specifics/spike_system_volume.py](specifics/spike_system_volume.py)** —
run it directly to see it work. Stream A should lift the binding from it.

The chain is `CoCreateInstance(MMDeviceEnumerator)` →
`GetDefaultAudioEndpoint(eRender, eConsole)` →
`Activate(IID_IAudioEndpointVolume)`, then the vtable slots. Measured on
the dev machine (8-channel SteelSeries Sonar endpoint):

| Capability | Result |
|---|---|
| Absolute set (`SetMasterVolumeLevelScalar`) | 40 / 75 / 10 % all read back exact |
| Relative step (`VolumeStepUp` / `Down`) | uses Windows' own 2 % increment |
| Mute / unmute (`SetMute` / `GetMute`) | works |

So `pycaw` is not needed. That matters under the supply-chain rule —
pycaw is a small single-maintainer wrapper, and there was already an
in-repo precedent for raw COM/`user32` binding in
`rex_main/ui/hud.py:105-121`.

**Use `VolumeStepUp`/`VolumeStepDown` for "volume up/down"** rather than
a hand-picked delta, so REX's steps match what the volume keys do.

**Two gotchas that cost time in the spike — don't rediscover them:**

1. **`SUCCEEDED(hr)` is `hr >= 0`, not `hr == 0`.** `SetMute` returns
   `S_FALSE` (1) when the endpoint is *already* in the requested state.
   A `hr != S_OK` check raises on a perfectly successful call — so
   "unmute when not muted" would look like a hard failure.
2. **`IUnknown::Release` returns a refcount (ULONG), not an HRESULT.**
   Routing it through an HRESULT-checking helper fails on the normal
   return of 1.

## Wave 1 — parallel streams

| Stream | Owns (exclusive) | Depends on | Use skill |
|---|---|---|---|
| **A** system_audio | new `actions/system_audio.py`; pattern edits in `actions/ytmd.py` + `actions/spotify.py` | 0b | `/rex-action` |
| **B** system_window | new `actions/system_window.py` | — | `/rex-action` |
| **C** app control | `actions/apps.py` | — | `/rex-action` |
| **D** discovery panel | `rex_main/ui/` (new dialog) | — | — |
| **E1** dashboard backend | `rex_main/dashboard/server.py`, `rex_main/rex.py` (callback fan-out) | — | — |
| **E2** dashboard frontend | `rex_main/dashboard/static/` | **E1** | — |

**E1 → E2 is the only ordering dependency inside Wave 1.** Everything
else is genuinely concurrent.

## Shared files — the collision rules

Three files attract every stream. Rules, not suggestions:

- **`docs/ACTIONS.md`** (A, B, C) — append your backend's section at the
  end of the inventory. Do not reorganize, do not touch another
  backend's rows.
- **`README.md`** (A, B, C, D) — **do not touch it.** One integration
  pass updates the command table after the wave lands.
- **`test_actions.py`** (A, B, C) — you *run* it, you don't edit it. The
  one exception is if your backend legitimately needs a new invariant;
  say so in the PR rather than editing quietly.

## Cross-stream phrase collisions

These produce work that is individually correct and breaks on merge.
Arbitrated here, up front:

| Phrase | Owner | Rule for everyone else |
|---|---|---|
| `close window` | **B** | C must not match a bare "window" as an app name |
| `open` / `close` / `switch to` **+ app name** | **C** | match against the cached app catalog, never a greedy `.+` |
| `volume up/down`, `mute`, `play`, `pause`, `next`, `previous`, `stop` | **A** | these are the phrase-ownership inversion; nobody else claims them |
| `minimize`, `maximize`, `snap left/right`, `lock` | **B** | — |

`test_no_phrase_collision_between_concurrently_active_backends` in
`test_actions.py` is the tripwire — it was added in Phase 0 precisely for
this wave. If it goes red, two streams claimed the same phrase.

## Merge order

**A first.** It is the only stream that changes existing behaviour (the
phrase-ownership inversion re-phrases ytmd/spotify), so landing it first
means everyone else rebases once onto a settled matcher rather than
repeatedly.

Then B, C in either order. D, E1, E2 whenever they're ready — they don't
touch `actions/`.

## Stream briefs

### A — system_audio

Implement the `system_audio` backend per PC_CONTROL_PLAN Phase 1: volume
up/down, absolute set, mute/unmute, and global media keys (play/pause,
next, previous, stop) via `VK_MEDIA_*` so they work in browsers, VLC and
calls where the music APIs can't reach.

**This stream carries the phrase-ownership inversion** — generic phrases
become system-level, and `ytmd` / `spotify` get re-phrased to require
naming the app ("music volume up", "pause music"). Both halves must ship
in **one PR**; split them and the collision test goes red on an
intermediate commit.

Add a DECISIONS.md entry. Don't touch README.

### B — system_window

Implement the `system_window` backend: minimize, maximize, restore,
close window, snap left/right, show desktop, next/last desktop, lock.

Use `ctypes` against `user32` — see `rex_main/ui/hud.py:105-121` for the
established binding pattern in this codebase.

`close window` sends a close **request** the app can respond to (offering
to save), never a forced process kill. Destructive-adjacent phrases must
be `no_early_match` so FastVAD can't fire on a prefix. No shutdown,
restart, sleep or log-off — see the plan's safety rails.

You own the exact phrase `close window`. Don't touch README.

### C — app control

Generalize `rex_main/actions/apps.py` from its two-app hardcoded `_APPS`
dict to any installed app. The three-tier resolver already exists
(`_resolve_exe_paths` → `_resolve_via_start_menu` → `_resolve_via_start_apps`);
this is about feeding it a real catalog.

- Enumerate `Get-StartApps` **once at startup** and cache it. Discovery
  currently caches per app; widen it.
- **Fuzzy-match against that catalog, never a bare `.+`.** Whisper will
  return "blunder" for "blender"; exact matching will feel broken.
- Patterns capturing an app name must be `no_early_match=True`.
- Add "switch to \<app\>" (focus an existing window) — **spike
  `SetForegroundWindow` first.** Windows restricts foreground changes
  from background processes; if it proves unreliable, ship launch-only
  and say so.
- The four existing `apps_*` phrases must keep working.

Don't claim `close window`. Don't touch README.

### D — action discovery panel

Build the read-only "what can I say?" dialog, generated from the registry
(`actions.all_specs()`), opened from the tray menu. Group by backend,
show phrases and a one-line summary per action.

This gets more valuable as the wave lands — past ~60 actions nobody can
remember the surface, and "what can I say?" becomes the thing blocking
daily use.

UI only. No changes under `actions/`.

### E1 — dashboard backend

Read [DASHBOARD_PLAN.md](DASHBOARD_PLAN.md).

Fan `run_assistant`'s `ui_callback` out to the dashboard as a second sink
alongside the Qt bridge, so `state.*`, `match` and `no_match` push over
the WebSocket immediately instead of waiting for the 1 s poll. Keep the
1 s tick for resource meters.

Don't touch the frontend.

### E2 — dashboard frontend

Read [DASHBOARD_PLAN.md](DASHBOARD_PLAN.md) — it carries the full design
including the validated palette and the ambient constraints.

Rewrite `rex_main/dashboard/static/` against the existing endpoints.
Dark-committed, no idle motion, legible at 1.5–2 m, linear meters rather
than donut gauges.

Depends on E1 for live state.

## Not in this wave

- **Dictation** (Phase 3) — needs its own design doc, and its
  mode-isolation requirement touches the dispatch seam.
- **Per-app profiles** (Phase 4) — needs its own doc.
- **README integration pass** — one job, after the wave.
