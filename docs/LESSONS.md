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

## `from __future__ import annotations` silently broke the dashboard WebSocket

**Symptom:** `/ws` refused every connection with close code 1008. The
dashboard fell back to nothing, and the only trace was a `logger.debug`
whose comment blamed browsers: "expected in some browsers (403)".

**Root cause:** `dashboard/server.py` deferred its fastapi imports into
`_get_app()` so `rex` without `--dashboard` wouldn't pay for them — but
the module also had `from __future__ import annotations`. That makes
every annotation a string, and FastAPI resolves endpoint annotations
against the function's **module** globals, where `WebSocket` doesn't
exist. The unresolvable annotation didn't raise; it degraded into a
required query parameter named `websocket`, so every handshake failed
validation.

**Fix:** Drop the future import from that module, so `websocket:
WebSocket` is evaluated at `def` time against `_get_app`'s locals. The
lazy fastapi import is preserved, with a comment saying why the future
import must not come back.

**Lesson:** Deferred imports and postponed annotations don't mix in any
framework that introspects signatures — FastAPI, pydantic, typer,
dataclasses. If a decorator reads your annotations, they must resolve
from module scope. And a broad `except Exception: logger.debug(...)`
around connection setup will hide this class of bug for months; the
misleading comment was written by someone looking straight at it.

**See also:** [DASHBOARD_PLAN.md](DASHBOARD_PLAN.md),
[rex_main/dashboard/server.py](../rex_main/dashboard/server.py).

---

## Two code paths for the same job — and the default config ran the worse one

**Symptom:** REX felt unresponsive in a way that didn't match the code.
The HUD's "didn't catch that" — the thing that's supposed to tell you
REX heard you but didn't understand — never appeared, no matter how
much nonsense was spoken into an open wake window. Reading
`matcher.py` showed the event being emitted correctly, right there at
line 116.

**Root cause:** It was emitted correctly in the path that wasn't
running. `low_latency_mode: true` is the default in
`default_config.yaml`, so `FastVAD` handles dispatch for essentially
every user. `FastVAD` never called `matcher.dispatch_command` — the
matching and execution logic was reimplemented as closures inside
`run_assistant`, with gate checks and metrics spread through
`fast_vad.py`. Over time the two copies drifted, and every difference
landed on the default path:

- `ui_callback("no_match", …)` existed *only* in `matcher.py`, so the
  HUD event was unreachable in the shipped configuration.
- The match event passed `text=""` — the closure never received the
  recognized text, so even successful matches showed blank.
- `fast_vad.py` contained zero `try`/`except`. A throwing handler would
  escape the task and `asyncio.gather` would tear the assistant down.
  This never fired only because every handler carries `@safe_call`.
- The wake gate was checked twice, in two different modules, each
  recording its own suppression metric.

**Fix:** One seam — `matcher.dispatch_text()` — called by both paths,
returning a `DispatchResult` rich enough for FastVAD to keep making its
buffering decisions. See
[DECISIONS.md](DECISIONS.md) "One dispatch seam" and
[PHASE0_DISPATCH.md](PHASE0_DISPATCH.md).

**Lesson:** When a feature is provably present in the code but never
observed at runtime, check *which* branch of a mode switch you're
reading before you debug the feature. The tell here was a config
default: `low_latency_mode: true` meant the well-structured code path
was the fallback, not the norm — so every code review looked at the
wrong file. Two implementations of the same behaviour will always
drift; the one guarded by a non-default flag is the one that stays
correct, because it's the one people read. If you must have two paths,
give them one shared core and let the paths differ only in how they
*feed* it.

Corollary: a decorator that's "just" defensive convenience can quietly
become load-bearing. `@safe_call` was documented as swallowing
transient network errors, but it was the only thing keeping an
exception from killing the process on the default path. Nothing said
so, and nothing tested it.

**See also:** [rex_main/matcher.py](../rex_main/matcher.py)
`dispatch_text`; [rex_main/fast_vad.py](../rex_main/fast_vad.py);
`test_dispatch.py`.

---

## openWakeWord ships without its mel/embedding pre-processors — the load error names *your* model, not the missing bundled file

**Symptom:** Fresh `uv` venv. `uv run rex --gaming` boots cleanly through Whisper warmup, then on wake-word init:

```
WARNING rex_main.wake_word: Wake-word model load attempt failed: [ONNXRuntimeError] : 3 : NO_SUCHFILE :
  Load model from C:\...\.venv\Lib\site-packages\openwakeword\resources\models\melspectrogram.onnx failed:
  ... File doesn't exist
ERROR rex_main.wake_word: Custom wake-word model not loadable: C:\Users\danto\.rex\wake_models\hey_rex.onnx. Gate disabled.
```

The error message blames `hey_rex.onnx` — the wake model that's actually fine. Felt like the uv install had a missing dep or the cached HF model was corrupt.

**Root cause:** Two layers.

1. **openWakeWord ships incomplete.** The PyPI wheel installs the Python code but *not* the `melspectrogram.onnx` / `embedding_model.onnx` pre-processor ONNX files those models rely on. Those live in `<site-packages>/openwakeword/resources/models/` and are fetched on first use by `openwakeword.utils.download_models()`. The directory doesn't even exist until that call runs. Every wake model — custom or prebuilt — depends on these pre-processors.
2. **`wake_word.py` mis-blamed the wrong file.** Old `_lazy_init` flow: try to load the wake model; if it fails *and* `is_custom` (i.e. the resolved path exists on disk), early-return with "custom model not loadable" and disable the gate. The early-return assumed `is_custom` ⇒ "the custom file is missing/broken." But `is_custom` was True (the cached `hey_rex.onnx` *was* there); the actual failure was the missing pre-processor inside openwakeword's site-packages. The `download_models()` fallback that would have fixed it was skipped because the early-return ran first.

**Fix:** Proactive ensure in [rex_main/wake_word.py](../rex_main/wake_word.py) `_lazy_init`. Before any load attempt, check whether `melspectrogram.onnx` and `embedding_model.onnx` exist under `<openwakeword pkg>/resources/models/`. If not, call `download_models(model_names=["_rex_preproc_only_"])` — the sentinel matches no prebuilt wake-word name, so the function's `always download feature + VAD` blocks run and the ~30 MB prebuilt-wakeword set is skipped. Only the ~5 MB of pre-processors REX actually needs gets fetched. The `is_custom` early-return stays in the post-load failure path; now it correctly means "your `.onnx` itself is broken" instead of mis-blaming.

**Lesson:** "Library is installed" ≠ "library is ready to run." Some packages bundle code but lazily fetch supporting data files on first use, and the install-time check (`import openwakeword` succeeds, `openwakeword.__file__` resolves) won't tell you the runtime payload is missing. When wrapping that kind of library, never let your *load-failure* classification logic short-circuit before you've ensured the library's own first-run dependencies are in place — or your error message will confidently name the wrong culprit. Also: this bites *both* venvs equally. Same fix path applies whether REX is launched via `uv run rex` (project `.venv`) or bare `rex` (the `uv tool` install at `~\.local\bin\`). The auto-ensure runs the first time each venv hits `_lazy_init`.

**See also:** [rex_main/wake_word.py](../rex_main/wake_word.py) `_lazy_init`; openwakeword's `download_models()` in `openwakeword/utils.py`.

---

## Wake-word listening window vs Whisper latency — `medium` on CPU eats the entire 6s gate before the command arrives

**Symptom:** New `ytvd_*` voice actions appeared "broken" in the tray:
say `"hey rex"` then `"fullscreen"`, nothing happens. Same with
`"play music"` — a long-known-working action. Tray was a fresh
process. Restart didn't help.

**Root cause:** Not the actions. The chain was working; the wake-word
gate was closing before the command's transcription arrived. Detailed
trace from `--debug` logs:

```
00:37:11  WakeWord fired
00:37:20  FastVAD flushing utterance: 20 frames (~0.64 s)   ← 'hey, rex.'
00:37:25  No command matched: 'hey, rex.'                   ← only the wake phrase
00:37:38  WakeWord fired again
00:37:45  FastVAD flushing utterance: 23 frames (~0.74 s)   ← 'hey, rex.' again
00:37:49  FastVAD flushing 8-frame  (~0.26 s) utterance     ← dropped (<min_speech)
00:37:54  FastVAD flushing 4-frame  (~0.13 s) utterance     ← dropped
…
00:41:41  Early transcription: 'play music.'
00:41:41  Suppressed early match 'ytmd_play_music' (wake word not active)
```

Two compounding things were happening:

1. **Whisper `medium` on CPU returns transcriptions in ~4.5 s.** With
   `wake_word.listening_window_seconds: 6`, that left ~1.5 s of usable
   headroom — and any speak-pause-speak pattern pushed the command's
   transcription past the window. The `suppressed` log line is the
   smoking gun: matcher found `ytmd_play_music` for text `'play
   music.'`, but the wake-word gate had already closed (the gate
   doesn't auto-extend on each wake fire — only on each *matched*
   command).
2. **FastVAD's `min_speech=300ms` filter silently drops short
   utterances.** Saying `"fullscreen"` quickly produces ~250 ms of
   speech, gets flushed as a too-short utterance, never transcribed.
   Saying `"hey rex"` slowly and then `"fullscreen"` produces two
   utterances — the wake one transcribes, the command one is below
   the floor and disappears. There is no log line at INFO level
   telling you this happened — only the `~0.13 s` frame-count line
   at DEBUG.

**Fix:** Switch the user's config to the equivalent of `--gaming` mode
(`model.name: tiny.en`, `model.device: cpu`). `tiny.en` on CPU returns
in ~200–500 ms, so the existing 6 s window has order-of-magnitude
slack and chained commands work without re-waking.

```yaml
model:
  name: tiny.en     # was: medium
  device: cpu       # was: auto
```

Whisper `medium` is the wrong model for live command dispatch
regardless of device — its tail latency on CPU is what bites, but
even on GPU the cold-load and warm cycle is heavier than the task
needs. Commands are short and constrained; `tiny.en` is accurate
enough.

**Lesson:**

- **A wake-gated voice assistant has a Whisper-latency budget**:
  `Whisper(p99) + 1×command_speech ≤ listening_window_seconds` or
  commands get suppressed silently. With `medium` on CPU, p99 is
  ~5 s — the gate must be ≥10 s, or the model must change.
- **When commands don't match, the first question is "did the
  transcription reach the matcher inside the window?"**, not "is the
  regex right?". The `Suppressed: N` counter in the metrics printout
  is the cheapest signal that the gate closed too early — if it's
  non-zero while `Commands: 0`, the gate is the problem. `--debug`
  also adds the `Suppressed early match '<name>' from '<text>' (wake
  word not active)` line which names the regex that *would* have
  matched.
- **FastVAD's `min_speech=300ms` is a silent killer for one-word
  commands** said in isolation. Any future short-command additions
  (`"home"`, `"sub"`, `"like"`) need to either be said with
  enough pre/post padding to clear 300 ms, or the floor needs to
  drop. Keep the floor; coach users / docs to say wake + command in
  one breath.
- **The matcher logging is DEBUG-level.** "No command matched" and
  "Received text" are both invisible at INFO. When diagnosing dispatch,
  always relaunch with `--debug` first. Without it you're looking at
  wake counts and an empty match counter and inventing theories.

**See also:** [DECISIONS.md "New `ytvd` backend"](DECISIONS.md#2026-05-16--new-ytvd-backend-separate-file-for-youtube-video--music-desktop-fork)
(where this lesson surfaced),
[rex_main/fast_vad.py](../rex_main/fast_vad.py) (the `min_speech` /
`silence` knobs),
[rex_main/matcher.py](../rex_main/matcher.py) (`dispatch_command` —
DEBUG-level "Received text" and "No command matched" lines),
[rex_main/metrics.py](../rex_main/metrics.py) (`Suppressed:` counter).

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
