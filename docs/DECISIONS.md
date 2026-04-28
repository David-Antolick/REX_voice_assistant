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
