# Decisions

Append-only log of architectural / design decisions and their rationale.
One entry per decision. Newest at the top. Don't edit old entries — if a
decision is later reversed, add a new entry that supersedes it and link
back.

Format:

```
## YYYY-MM-DD — Short title
**Context:** why this came up.
**Decision:** what we picked.
**Alternatives considered:** what we didn't pick and why.
**Consequences:** what this implies for future work.
**See also:** links to ACTIONS.md / LESSONS.md / code where relevant.
```

---

## 2026-04-28 — Desktop UI v1: PySide6 tray + HUD + settings, in-process with asyncio in a QThread

**Context:** REX has lived in the terminal since launch. The vision
doc at [UI_PLAN.md](UI_PLAN.md) commits us to a real desktop surface as
the default — a tray icon, a transient recognition HUD that flashes the
recognized command, and a small settings dialog. The web-dashboard
direction is dead (it's still in-tree but no longer the long-term home).
Independent research (Wispr Flow, WillowVoice, SuperWhisper, Dragon,
Voice Access, Talon) shows this three-piece pattern is the convergence
point for desktop voice assistants.

**Decision:**

- **Toolkit: PySide6** (LGPL, Qt 6.7+). Only Python toolkit that gives
  native Win11 look, first-class system tray (`QSystemTrayIcon`), an
  easy frameless translucent overlay (HUD), good HiDPI, and a mature
  PyInstaller story in one stack. Added as a required base dependency
  in [pyproject.toml](../pyproject.toml) — UI is the default surface,
  not an opt-in.
- **Process model: single process.** Qt main thread owns the UI; a
  worker `QThread` (`AssistantThread`) owns its own asyncio loop and
  runs the unmodified `run_assistant()` coroutine. The runtime invokes
  a plain Python callback at lifecycle events; the callback is bound
  to a `UiBridge(QObject)` whose Qt signals marshal events back to the
  main thread via auto-queued connections. No `qasync` dep needed.
- **Runtime hooks are additive and optional.** Both
  `run_assistant(opts, config, ui_callback=None, paused=None)` and
  `dispatch_command(text_q, listening_state=None, ui_callback=None, paused=None)`
  no-op when the UI args are absent, so console mode is byte-identical
  to today.
- **CLI shape:** `rex` defaults to the tray app. `rex --console`
  preserves the existing console-loop behavior for debugging and
  headless runs.

**Alternatives considered:**

- **tkinter / customtkinter.** Looks dated on Win11, flickers on the
  frameless-overlay show/hide pattern, no native tray. Would have
  required `pystray` plus manual translucency hacks.
- **wxPython, Toga, Flet, DearPyGui.** Each fails on at least one of:
  native Win11 look, tray support, frameless click-through overlay,
  HiDPI, packaging maturity.
- **Subprocess split** (UI as a separate process speaking to a runtime
  subprocess). Cleaner thread story but doubles the operational
  surface for a single-user passion project. Single-process with a
  worker QThread is the right fit.
- **Hybrid tray + browser dashboard.** Rejected: we're explicitly
  retiring the web UI direction, and a browser tab is the wrong shape
  for an always-on background app.

**Consequences:**

- Adds ~60 MB to the install (PySide6). Acceptable next to torch +
  faster-whisper, which already dominate footprint.
- The runtime still depends on nothing UI-specific; it can run
  headless via `rex --console` exactly as before.
- Future expansions (command history, mic test, push-to-talk capture,
  per-app profiles) all fit naturally into the same Qt surface
  without reopening this decision.
- The FastAPI metrics dashboard is left in place for now (off by
  default). Removing it is a separate decision tracked in the vision
  doc as out-of-scope for v1.

**See also:** [UI_PLAN.md](UI_PLAN.md) for the v1/v1.x/v2 vision, the
v1 build plan at `~/.claude/plans/stateless-gathering-lake.md`, and
the new `rex_main/ui/` package.

---

## 2026-04-28 — Removed Discord integration; future voice chat will target Spacebar/Fermi

**Context:** Two prior decisions in this log stood up the Discord
integration: [Discord voice control via UIA](#2026-04-27--discord-voice-control-via-uia-not-rpc-not-keystrokes)
(why we used UIA at all) and [Ship `show discord` / `minimize discord`
instead of auto-restore](#2026-04-28--ship-show-discord--minimize-discord-instead-of-auto-restore)
(why we couldn't make it work cleanly while Discord is minimized). The
core constraint — Chromium tears down its accessibility tree whenever
its window isn't foreground, and no documented programmatic technique
forces a rebuild — was confirmed empirically against every workaround
in [LESSONS.md](LESSONS.md#chromium-tears-down-its-uia-tree-when-its-window-isnt-foreground--and-you-cant-programmatically-force-a-rebuild).

The user's group is migrating to a self-hosted **Spacebar** instance
(via the **Fermi** web client). Spacebar is open-source and
Discord-API-compatible; it can be self-hosted, scopes are not
whitelist-gated, and the web-app delivery model means none of the
Chromium-renderer-suspension issues apply (REX would talk to the
Spacebar HTTP / Gateway / RPC surfaces directly, not to a window).

**Decision:** Rip the Discord integration out of REX. Keep the lessons
and decisions in the docs (they remain valuable for any future Electron
/ Chromium app integration). The voice-chat slot stays in the planned
slots table but is now reserved for a future `spacebar` backend that
will be designed in a separate workstream against the user's
self-hosted Spacebar server.

**Removed:**
- `rex_main/actions/discord.py`
- `pywinauto>=0.6.8,<1` from `pyproject.toml` dependencies (it was added
  solely for the Discord UIA backend and has no other consumer in REX)
- The Discord rows in `README.md` voice commands table
- The Discord section in `ACTIONS.md` inventory
- The `discord_module.warm()` call in `actions/service.py`
- The `from rex_main.actions import discord` line in `actions/__init__.py`

**Kept (intentionally):**
- The two prior Discord-related entries in this DECISIONS log. They
  record real engineering work and the negative-result writeup is
  exactly the value future-us needs.
- [LESSONS.md](LESSONS.md#chromium-tears-down-its-uia-tree-when-its-window-isnt-foreground--and-you-cant-programmatically-force-a-rebuild) —
  the empirical writeup applies to any future Electron-app integration
  (Slack, OBS, Teams, etc.), not just Discord.
- `_local/wm_getobject_experiment.py`, `_local/offscreen_experiment.py`,
  `_local/discord_uia_dump.py` — kept as gitignored reproductions in
  case the situation re-emerges with another Chromium app.
- The published 1.1.0 wheel on PyPI, which still contains the Discord
  integration, untouched. Users who want it can pin
  `rex-voice-assistant==1.1.0`. The next published release will drop
  the integration and bump accordingly.

**Alternatives considered:**
- *Keep the Discord integration as a documented best-effort feature.*
  Rejected: the "works only when Discord is foreground" caveat is
  enough of a foot-gun that shipping it implies more reliability than
  the constraint allows. The user's group migrating away from Discord
  removes the incentive to maintain it.
- *Document `--force-renderer-accessibility` as the official setup step
  and ship as-is.* Rejected: the friction of asking every user to edit
  their Discord shortcut for an integration we're about to abandon
  isn't worth it.

**Consequences:**
- REX is back to YTMD / Spotify / SteelSeries as its supported
  integrations until the Spacebar backend lands.
- The action registry's `voice_chat` slot is reserved for the future
  `spacebar` backend rather than being introduced for one specific
  client.
- Any further work on voice-chat control happens in a separate plan
  against the user's self-hosted Spacebar server, where the API is
  open and the constraints are different.

**See also:** [LESSONS.md](LESSONS.md#chromium-tears-down-its-uia-tree-when-its-window-isnt-foreground--and-you-cant-programmatically-force-a-rebuild),
[`pyproject.toml`](../pyproject.toml).

---

## 2026-04-28 — Ship `show discord` / `minimize discord` instead of auto-restore

**Context:** The 1.1.0 Discord integration silently fails when Discord is
minimized — Chromium tears down the UIA accessibility tree. Spent a session
attempting every documented workaround to programmatically force the tree
to rebuild after a programmatic restore: `SW_SHOWNOACTIVATE`, `SW_RESTORE`,
`AttachThreadInput` focus-merge trick, `BringWindowToTop` + `SetForegroundWindow`
combos, off-screen positioning, `WM_GETOBJECT` to top-level window and to the
`Intermediate D3D Window` child with three different lParam values
(custom=1, `OBJID_CLIENT=-4`, `OBJID_NATIVEOM=-16`). All confirmed by direct
experiment to either restore the window without rebuilding the tree, or fail
to restore at all. Full empirical write-up in
[LESSONS.md](LESSONS.md#chromium-tears-down-its-uia-tree-when-its-window-isnt-foreground--and-you-cant-programmatically-force-a-rebuild).

**Decision:** Don't try to auto-restore. Ship two new voice commands —
`discord_show` ("show discord") and `discord_minimize` ("minimize discord") —
that handle window state explicitly. Drop the auto-restore-on-invoke path
that was added experimentally. Document the
`--force-renderer-accessibility` shortcut tweak in
[ACTIONS.md](ACTIONS.md#discord--discord-voice-control-slot-none-transport-os_native)
as the canonical fix for users who want voice commands to work
unconditionally regardless of window state.

**Alternatives considered:**
- *Auto-restore with focus theft on every invoke.* Rejected: even with
  `SW_RESTORE` + AttachThreadInput, Chromium frequently leaves the tree
  empty post-restore. The user pays the focus blink for an unreliable
  result.
- *Ship the auto-restore as best-effort and silently fail.* Rejected: the
  failure mode is worse than current — user says "mute me", nothing happens,
  no clear feedback on why.
- *Bundle a launcher that adds `--force-renderer-accessibility` to Discord's
  shortcut on REX install.* Rejected: too invasive (modifies user's app
  shortcuts), brittle to Discord auto-update overwriting it.

**Consequences:**
- Adds two voice commands (`show discord`, `minimize discord`) as a
  general-purpose primitive that's useful on its own — voice-controlled
  window state for a frequently-tucked-away app.
- Discord-with-mute-while-minimized requires the
  `--force-renderer-accessibility` setup step. Documented; one-time edit;
  works permanently.
- Pattern generalizes: future Electron-app integrations (Slack, OBS, Teams)
  inherit the same constraint and the same `show`/`minimize` recipe.

**See also:** [LESSONS.md](LESSONS.md#chromium-tears-down-its-uia-tree-when-its-window-isnt-foreground--and-you-cant-programmatically-force-a-rebuild),
[rex_main/actions/discord.py](../rex_main/actions/discord.py),
[`_local/wm_getobject_experiment.py`](../_local/wm_getobject_experiment.py),
[`_local/offscreen_experiment.py`](../_local/offscreen_experiment.py).

---

## 2026-04-27 — Discord voice control via UIA (not RPC, not keystrokes)

**Context:** Wanted voice commands for Discord mute/deafen/disconnect. Three
obvious paths each had a hard blocker:
- **Discord RPC `SET_VOICE_SETTINGS`**: behind `rpc.voice.write`, which is a
  vendor whitelist Discord doesn't grant to indie/OSS apps (Stream Deck,
  Loupedeck, etc. are partners; we wouldn't be).
- **Global keystroke injection**: tried before, broke other apps that share
  Discord's hotkey combos.
- **`PostMessage(WM_KEYDOWN)` to Discord's window**: confirmed via current
  research that Chromium silently drops synthesized input when unfocused.

**Decision:** Drive Discord's UI via Windows UI Automation (`pywinauto`).
Discord's Electron renderer exposes the bottom voice-panel buttons (`Mute`,
`Deafen`, `Disconnect`) with full `Invoke` and `Toggle` patterns. The spike
in `_local/discord_uia_dump.py` confirmed `.invoke()` flips state cleanly on
an unfocused window in ~400ms cold and ~30ms steady-state. The implementation
([rex_main/actions/discord.py](../rex_main/actions/discord.py)) holds one
COM pointer per button; runtime per-call is one `IUIAutomationInvokePattern::Invoke`
call. Total UIA queries per REX session: at most 4 (one window + three buttons).

**Alternatives considered:**
- *Playwright-driven Discord web client.* Rejected: would be a second Discord
  session (so muting "yourself" doesn't mute your real client), heavy (full
  Chromium + WebRTC), and a textbook self-bot from Discord's ToS perspective.
- *LLM tool-calling layer over UIA.* Rejected for now. Adds prompt-injection
  surface (server names / channel names / message text are all attacker-
  controllable strings on the tree) and slows a "rex mute me" command. The
  three Discord actions are well-served by the existing regex matcher — same
  shape as YTMD/Spotify/SteelSeries.
- *Window-scoped `PostMessage(WM_KEYDOWN)`.* Confirmed-blocked by Chromium's
  input handling. Works for Win32 apps, not for Electron.

**Consequences:**
- New runtime dep: `pywinauto` (pulls `comtypes` and `pywin32`). ~500ms one-time
  REX startup cost; negligible per-call.
- Button names are hardcoded English constants (`"Mute"`, `"Deafen"`,
  `"Disconnect"`). If Discord renames them in a future client update, the
  integration silently no-ops until the constants are updated. Same class of
  breakage as a YTMD HTTP API rename — patch release.
- Non-English Discord clients are not supported. Acceptable for an OSS personal
  project; documented in [ACTIONS.md](ACTIONS.md#discord--discord-voice-control-slot-voice_chat-transport-os_native).
- Scope is intentionally narrow (toggle mic / toggle deafen / leave channel).
  No content-generating actions (sending messages, joining channels) — those
  cross into self-bot territory regardless of mechanism.

**See also:** [ACTIONS.md](ACTIONS.md#discord--discord-voice-control-slot-voice_chat-transport-os_native),
[rex_main/actions/discord.py](../rex_main/actions/discord.py),
[`_local/discord_uia_dump.py`](../_local/discord_uia_dump.py) (the live spike,
kept around for debugging future UI-rename breakage).

---

## 2026-04-27 — Hard perf ceilings in the test suite

**Context:** REX dispatch is on the user's spoken-command hot path. A
silent regression (e.g. someone adding an O(N) lookup or a per-match log
call) could push end-to-end latency back over a second without anyone
noticing for weeks.

**Decision:** [test_actions.py](../test_actions.py) asserts hard µs/ms
ceilings on the dispatch loop, registry lookup, and matcher rebuild.
Failures are CI-blocking. Ceilings are set with ~30× headroom over
measured cost — a failure means a real regression, not noise.

**Alternatives considered:**
- *No perf gate, rely on the live `rex_main/benchmark.py` telemetry.*
  Rejected: telemetry catches regressions only after merge, and only if
  someone is looking at the dashboard. We want a pre-merge gate.
- *pytest-benchmark plugin.* Rejected for now: extra dev dep, and the
  built-in `time.perf_counter` measurements are already <5% noise at
  current iteration counts. Revisit if we want richer stats over time.

**Consequences:** Adding an action is cheap; adding latency is not.
Anyone who wants to raise a ceiling has to justify the regression in
the PR. See [LESSONS.md](LESSONS.md#microbenchmarks-lie-when-you-stub-the-real-io)
for why the live benchmark telemetry is also kept — they cover
different cases.

---

## 2026-04-27 — Coerce `localhost` → `127.0.0.1` in the YTMD client

**Context:** Users on native Windows reported 2–3 second per-command
latency despite Whisper running in 50–90 ms on GPU. BENCHMARK lines
showed `Exec: ~2000 ms` per HTTP call to YTMD on `localhost`.

**Decision:** [rex_main/actions/ytmd.py](../rex_main/actions/ytmd.py)
coerces a configured host of `"localhost"` to `"127.0.0.1"` at client
construction. Default also changed to `127.0.0.1`. Default in
[rex_main/default_config.yaml](../rex_main/default_config.yaml)
similarly switched.

**Alternatives considered:**
- *Tell users to edit their config.* Rejected: every existing install
  ships with `localhost`, and the symptom is unreported until someone
  benchmarks. Silent fix at runtime is the right move.
- *Move HTTP off the asyncio loop with `asyncio.to_thread`.*
  Defers the symptom (event loop unblocked) but doesn't fix the
  per-call latency. Worth doing later anyway, but not the priority.

**Consequences:** Docker users who actually need `host.docker.internal`
must set `YTMD_HOST` explicitly — the new default no longer works for
them. Reasonable trade because Docker users are a small minority and
will read setup docs.

**See also:** [LESSONS.md](LESSONS.md#windows-localhost-is-ipv6-first--2s-hang-per-http-call).

---

## 2026-04-27 — Phrase-based disambiguation, not prefix-based

**Context:** With Discord, Steam, and system-audio backends coming, we
have to decide whether the user says `"discord mute me"` or just
`"mute me"`. The first is unambiguous but verbose; the second is
natural but can collide across slots.

**Decision:** Users say the natural phrase. Cross-slot collisions are
solved per-collision by choosing distinct phrasings (`"mute me"` →
voice chat, `"mute audio"` → system audio, `"mute music"` → music).
Prefixing with the app name is reserved for cases where no clean
phrasing exists.

**Alternatives considered:**
- *Prefix every phrase with the app name.* Rejected: most actions
  don't collide, and the prefix burdens 100% of utterances to handle a
  ~5% case. Optimizes for the wrong frequency.
- *Per-slot active-listening modes.* Adds modal complexity. Rejected.

**Consequences:** Each new backend has to scan its action set for
collisions with already-active backends. The
`test_no_phrase_collision_within_a_backend` check in
[test_actions.py](../test_actions.py) catches intra-backend collisions;
inter-backend collisions are caught by the slot-routing model itself
(only one backend per slot is active at once).

**See also:** [ACTIONS.md "Avoiding phrase collisions across slots"](ACTIONS.md#avoiding-phrase-collisions-across-slots).

---

## 2026-04-27 — Slot-based routing, no module-level rebinding

**Context:** Pre-registry, [`commands.configure_service`](../rex_main/commands.py)
swapped backends by reassigning module-level names via `global` after
a switch (`play_music = client.play_music`, etc.). This worked for two
backends but did not generalize to N slots, broke import-time analysis,
and required `getattr(commands, name)` lookups at the matcher.

**Decision:** Each `ActionSpec` declares its `backend` and (optionally)
its `slot`. The registry tracks one active backend per slot. The
matcher recompiles its dispatch table from `actions.active_specs()`
whenever a slot's backend changes. Service-switch voice commands
(`switch_to_spotify`, `switch_to_ytmd`) just call
`set_active_backend("music", X)`.

**Alternatives considered:**
- *Keep `global` rebinding, scale by adding slots.* Rejected: every
  new slot would need its own `configure_*` function and the matcher
  would still do `getattr` against multiple modules.
- *One always-loaded handler per capability that branches internally
  on active backend.* Rejected: pushes routing into every handler,
  fights the introspection goal.

**Consequences:** Adding a backend is one new file under
`rex_main/actions/`; matcher and dispatcher don't change. Slot model
also gives a future LLM planner a clear "which backend do I have
right now" signal.

**See also:** [ACTIONS.md "Slots"](ACTIONS.md#slots),
[rex_main/actions/registry.py](../rex_main/actions/registry.py).

---

## 2026-04-27 — Action registry with planner-ready metadata

**Context:** Voice-command surface was scattered: regex table in
`matcher.py`, handler implementations in `commands.py` and
`steelseries.py`, no central place to introspect what REX can do.
Forecast was 100+ commands across 6+ apps within a year, plus
eventual LLM planning ("to do X, run actions A then B").

**Decision:** Single `ActionSpec` dataclass per command, registered
via `@action` decorator at module import. Each spec carries the regex,
the handler, *and* planning metadata (`summary`, `args`,
`preconditions`, `side_effects`, `examples`). One file per backend
under `rex_main/actions/`. The matcher pulls live from the registry —
no separate regex table to maintain.

**Alternatives considered:**
- *Folder split by transport (HTTP / OAuth / OS-native).* Rejected:
  doesn't match how users or future-us reach for things. Transport
  is metadata, not navigation.
- *Folder split by capability (`music/`, `clipping/`).* Rejected as
  premature: 2 music backends doesn't justify a subfolder yet.
- *Keep regex table in `matcher.py`, just add metadata sidecar.*
  Rejected: two sources of truth invites drift; the registry test
  suite would have to validate the join.

**Consequences:** `matcher.py` becomes mechanical (compile + dispatch).
New backends are pure additions, no edits to existing files except
`actions/__init__.py`. Metadata fields are unused today but consumed
by the eventual planner.

**See also:** [ACTIONS.md](ACTIONS.md) (the contract),
[test_actions.py](../test_actions.py) (the gate),
[LESSONS.md](LESSONS.md#observability-pays-off-when-you-need-it-most).
