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
