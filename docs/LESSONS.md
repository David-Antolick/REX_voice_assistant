# Lessons

Append-only log of debugging insights, gotchas, and "things future-me will
forget". Newest at the top. One entry per insight, written so it's
useful when you've forgotten the original context.

Format:

```
## Title (be specific — name the symptom or the surprise)
**Symptom:** what looked broken.
**Root cause:** what was actually broken.
**Fix:** what we did about it.
**Lesson:** the generalizable insight.
**See also:** links to DECISIONS.md / ACTIONS.md / code.
```

---

## Chromium tears down its UIA tree when its window isn't foreground — and you can't programmatically force a rebuild

**Symptom:** Discord voice commands (`mute`, `deafen`, `leave channel`) work
fine when Discord is the active window, but the moment Discord is minimized
or fully covered, every UIA call returns `ElementNotFoundError`. Even after
restoring the window, the buttons sometimes don't come back until the user
physically clicks Discord.

**Root cause:** Chromium suspends its accessibility tree as a memory
optimization whenever its window isn't actively the foreground. The trigger
is "this window isn't visible to a user right now" — combining iconic state,
the `CalculateNativeWinOcclusion` feature, and not-foreground detection. The
tree is reconstructed on demand, but **only when Chromium decides
assistive technology is querying the window** — and there's no reliable
programmatic signal that triggers that decision on modern Chromium.

**Fix:** None that's purely in-code. Two real escape hatches:

1. **Launch Discord with `--force-renderer-accessibility`.** Forces the
   accessibility tree to stay alive permanently regardless of window state.
   Cost: user has to edit their Discord shortcut once.
2. **Add explicit `show discord` / `minimize discord` voice commands**
   ([rex_main/actions/discord.py](../rex_main/actions/discord.py)) so the
   user can drive window state, plus accept that mute/deafen require Discord
   to currently be foreground or be launched with the flag above. After
   `show discord`, a single user click on Discord reliably wakes the tree.

**Things we tried that did NOT work** (all confirmed empirically — saving
future-us the time):

- `SW_SHOWNOACTIVATE` to restore without focus theft → window restores
  visually but Chromium leaves the tree torn down (renderer never wakes).
- `SW_RESTORE` (with focus) → Win32 focus rules prevent a non-foreground
  process from genuinely making Discord foreground; renderer often still
  doesn't wake.
- `AttachThreadInput` trick (merge thread input with the foreground thread
  to bypass focus restrictions) → didn't help; Chromium still didn't
  rebuild the tree.
- `BringWindowToTop` + `SetForegroundWindow` combination → same as above.
- Move-window-off-screen pattern (`SetWindowPos` to `(-10000, -10000)` with
  `SWP_NOACTIVATE`) → Chromium's `CalculateNativeWinOcclusion` detects
  out-of-bounds windows as occluded and tears down the tree. `IsWindowVisible`
  returns `True`, but the tree is gone anyway.
- `SendMessage(WM_GETOBJECT, 0, 1)` to the top-level window — the classic
  AutoHotkey-community trick. Does not work on current Chromium because the
  trick required sending the message to a `Chrome_RenderWidgetHostHWND` child
  window that no longer exists; modern Chromium consolidated rendering into
  a single `Intermediate D3D Window` child that doesn't dispatch to the
  renderer for accessibility queries.
- `WM_GETOBJECT` with `OBJID_CLIENT` (-4) and `OBJID_NATIVEOM` (-16) on both
  the top-level and the D3D child — Chromium responds with non-zero values
  for some variants but doesn't rebuild the tree.
- Polling `child_window().wrapper_object()` for the Deafen anchor button up
  to 2 seconds after restore → the tree never rebuilds within the timeout
  on its own.

**The only programmatic signals that DO work:**

- `--force-renderer-accessibility[=basic|complete]` at Chromium/Electron
  launch. Permanent tree, no in-code workaround needed. The Chromium
  accessibility team's documented official path.
- A real user mouse click on the Chromium window. Mouse-input WinEvents
  reach the renderer process and trigger Chromium's "AT might be present"
  heuristic. Synthesized clicks via `SendInput` can theoretically substitute
  but require focus, which loops back to the original problem.

**Lesson:** When automating an Electron / Chromium-based app via UIA,
assume the accessibility tree is **only valid while the window is foreground
or while `--force-renderer-accessibility` is set**. Build the integration
around that constraint from day one, not against it. If the user needs to
control the app while it's minimized or in the background, either ship
explicit window-state voice commands (so the user surfaces the window before
issuing commands) or document the launch flag as a setup step. Don't burn
hours rediscovering that Chromium is unwilling to be tricked.

This applies to: Discord, Slack, VS Code, Microsoft Teams, OBS Studio
(post-Electron transition), and any other Electron app. Same teardown
behavior, same exhausted toolbox of workarounds.

**See also:** [DECISIONS.md "Discord voice control via UIA"](DECISIONS.md#2026-04-27--discord-voice-control-via-uia-not-rpc-not-keystrokes),
[rex_main/actions/discord.py](../rex_main/actions/discord.py) (current
shipping shape — explicit `show` / `minimize` actions, no auto-restore
heuristics), [_local/wm_getobject_experiment.py](../_local/wm_getobject_experiment.py)
and [_local/offscreen_experiment.py](../_local/offscreen_experiment.py)
(reproductions of the failed approaches, kept for future re-verification
if Chromium's behavior ever changes).

---

## Windows `localhost` is IPv6-first → ~2s hang per HTTP call

**Symptom:** Every voice command on YTMD took 2–3 seconds end-to-end on
Windows even with `--device cuda --low-latency`. BENCHMARK lines showed
Whisper at 50–90 ms but `Exec` at ~2000 ms. `restart_track` was 4 s
because it issues two `_send` calls back-to-back.

**Root cause:** Windows resolves `localhost` to `::1` (IPv6) first.
YTMD's Companion-Server only listens on IPv4 `127.0.0.1`. The IPv6
TCP connection attempt has to time out before Windows falls back to
IPv4 — adding ~2 seconds *per HTTP call*. The `host.docker.internal`
default in older configs hits a similar resolution timeout via
LLMNR/NetBIOS fallbacks.

**Fix:** [rex_main/actions/ytmd.py](../rex_main/actions/ytmd.py)
coerces `host == "localhost"` to `"127.0.0.1"` at construction.
Default in [rex_main/default_config.yaml](../rex_main/default_config.yaml)
also flipped. Plus `requests.Session()` for keep-alive so even the
TCP handshake amortizes.

**Lesson:** When you see a constant ~2s in any benchmark on Windows,
suspect IPv6 fallback before you suspect your code. The fix is almost
never to add async or threading — it's to skip the bad resolution. Use
literal IPv4 addresses for localhost services on Windows.

**See also:** [DECISIONS.md "Coerce localhost → 127.0.0.1"](DECISIONS.md#2026-04-27--coerce-localhost--127001-in-the-ytmd-client).

---

## Microbenchmarks lie when you stub the real I/O

**Symptom:** When the user reported 2–3 s sluggishness after the
registry refactor, my first response was a microbench showing dispatch
took ~1.7 µs/match. I confidently told them "the registry didn't make
it slower". Wrong direction — but the 2 s was real.

**Root cause:** The microbench stubbed out `requests`, `spotipy`,
`ytmusicapi` to make it run without those deps installed. So the
handlers it called did nothing. The real cost was in
`requests.post(...)` against `localhost`, which the stub never
exercised. My benchmark proved the dispatcher was fast — which it is
— and accidentally proved nothing about end-to-end latency.

**Fix:** Trust the live `BENCHMARK` log lines instead. They have the
breakdown the user actually cares about (E2E / VAD / Whisper / Exec).
Once we read `Exec: 2035ms` for `next_track`, the diagnosis was
trivial.

**Lesson:** A microbench that stubs the slow thing is a microbench of
the fast thing. If the user is reporting a wall-clock problem, your
first move is to look at wall-clock numbers from a real run — not to
construct a synthetic measurement that excludes whatever they're
hitting. Stubs are useful for unit tests, not for performance.

**See also:** [DECISIONS.md "Hard perf ceilings"](DECISIONS.md#2026-04-27--hard-perf-ceilings-in-the-test-suite).

---

## Lazy client instantiation shifts latency to the first command

**Symptom:** Pre-localhost-fix, the very first voice command after a
fresh start sometimes felt even slower than subsequent ones. Whisper
warmup didn't account for the gap.

**Root cause:** The new registry uses lazy `_get()` for backend
clients (`SpotifyClient` instantiation does an OAuth check + a
`self.sp.devices()` Web API round-trip; can take 500 ms–2 s). Pre-
registry, `configure_service("spotify")` instantiated eagerly during
boot, so the cost was paid behind Whisper warmup. Post-registry, the
cost moved into the dispatch path of the first matched command.

**Fix:** [rex_main/actions/service.py](../rex_main/actions/service.py)
calls `_warm_client(...)` immediately after `set_active_backends(...)`.
First-command latency goes back to where it was: behind boot.

**Lesson:** Lazy is fine; lazy in the user's hot path is not. Whenever
you replace eager init with a lazy singleton, look for whether the
first invocation is on a latency-sensitive path. If it is, pre-warm
on a known-quiet path (boot, config load) instead.

---

## Observability pays off when you need it most

**Symptom:** *(this is the meta-lesson, not a bug)* The 2-second
mystery took ~30 seconds to diagnose because the live `BENCHMARK` log
lines already split E2E into VAD / Whisper / Exec. One look told us
the cost was in `Exec`, which is HTTP, which pointed at the host.
Without that breakdown we'd have started by profiling Whisper or VAD
— guessing.

**Root cause:** N/A — observability was pre-existing.

**Fix:** Keep the BENCHMARK lines and the metrics summary. When
adding a new pipeline stage (e.g. a planner stage between matcher and
handler), include it in the breakdown.

**Lesson:** A timing breakdown that's there before you need it is
worth ten profilers fired up after the fact. Cheap structured logs
beat expensive forensic work.

**See also:** [rex_main/benchmark.py](../rex_main/benchmark.py),
[rex_main/metrics_printer.py](../rex_main/metrics_printer.py).

---

## Module decorator side-effects depend on import order

**Symptom:** During the registry build, an early version of
[`actions/__init__.py`](../rex_main/actions/__init__.py) imported
`service` before `ytmd` / `spotify`. `service.py` imports
`actions.ytmd` and `actions.spotify` at module level — and at that
point `actions/__init__.py` was still mid-execution, so `ytmd` /
`spotify` were not yet attributes of the `actions` package.

**Root cause:** Python's package init runs top-to-bottom. Submodules
imported by a sibling submodule still get loaded fine (Python's
import machinery handles partial-package state), but reading order
matters for clarity and breaks if anything tries to introspect the
package's attribute dict mid-init.

**Fix:** Order the imports in `actions/__init__.py` so all backend
modules load first, *then* `service` (which references them). Now
the registry is fully populated before `service` even runs.

**Lesson:** Decorator-driven registration patterns make import order
load-bearing. When `__init__.py` imports submodules to trigger side
effects, list dependencies before dependents and document why.

---

## YTMD's "v2" API still has `v1` in the URL

**Symptom:** The Companion-Server endpoint is documented as v2 in the
YTMD wiki, but the URL is `/api/v1/command`.

**Root cause:** Versioning ambiguity in the upstream project — they
bumped the API contract version without bumping the URL path.

**Fix:** Note this with a one-line comment at the top of
[rex_main/actions/ytmd.py](../rex_main/actions/ytmd.py) so the next
person doesn't waste time chasing a `/api/v2/...` endpoint.

**Lesson:** Document upstream quirks at the call site. Comments
explaining "why this looks wrong but isn't" are exactly the comments
worth writing.
