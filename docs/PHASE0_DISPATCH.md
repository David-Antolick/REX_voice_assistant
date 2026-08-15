# Phase 0 — Consolidate the dispatch paths

Implementation plan. Prerequisite for
[PC_CONTROL_PLAN.md](PC_CONTROL_PLAN.md) Phase 1.

Unlike the north-star docs, this one is meant to be executed. Line
references are as of `9c2c819` and will drift — function names are the
durable anchors.

## Goal

**One place in REX matches text to an action and invokes a handler.**

Today there are two, they have drifted, and the one that runs by default
is the worse of the pair.

## The problem

`low_latency_mode: true` is the default in
[default_config.yaml](../rex_main/default_config.yaml), so `FastVAD` is
the path essentially every user runs — console and tray alike.
`matcher.dispatch_command` is the fallback.

But `FastVAD` doesn't call `matcher.dispatch_command`. Dispatch policy is
reimplemented across two places:

- `rex_main/rex.py:246-281` — the closures `transcribe_sync`,
  `match_command`, and `execute_command`, defined inline inside
  `run_assistant`.
- `rex_main/fast_vad.py:164-204` (early match) and `236-252` (final
  match) — gate checks, metrics, and execution, interleaved with VAD
  buffer state.

Four concrete symptoms:

| # | Symptom | Evidence |
|---|---|---|
| 1 | **`no_match` never reaches the UI on the default path.** The HUD's "didn't catch that" is unreachable. | `ui_callback("no_match", …)` appears only at `matcher.py:116`. `fast_vad.py:250` records the *metric* but there is no UI event and no callback plumbed in. |
| 2 | **The HUD gets an empty string on match.** | `rex.py:274` emits `_emit("match", action=func_name, text="", args=args)` — `text` is hardcoded empty because the closure never received it. |
| 3 | **No error boundary.** A throwing handler kills the assistant. | `fast_vad.py` contains zero `try`/`except`. `self.execute(...)` at lines 185 and 245 is unguarded, and `run()` has no outer handler, so an exception escapes the task and `asyncio.gather` in `run_assistant` re-raises it. The standard path wraps handlers in `matcher._invoke`. |
| 4 | **The wake gate is checked twice, in two different places.** | `fast_vad.py:170` calls `self.gate_func()` and records suppression; the `execute_command` closure at `rex.py:265-273` then checks `listening_state.is_active()` and records suppression *again*. Harmless today, but it's duplicated policy that can disagree. |

Symptom 3 is survivable right now only because every handler is wrapped
in `@safe_call`, which swallows everything. That decorator lives in
`actions/ytmd.py` and catches `requests.exceptions.RequestException`
first — it is network-shaped. Phase 1 introduces COM errors, missing
window handles, and access-denied failures. **Phase 1 landing on this
path is how "clunky" becomes "crashes."**

Symptom 1 is likely a real contributor to REX feeling unresponsive:
UI_PLAN.md names "the user always knows whether REX heard them" as a
design principle, and in the default configuration that feedback cannot
fire.

## Design

Extract the match → gate → emit → invoke sequence out of both callers
into one function in `matcher.py`. Both paths call it. It returns enough
information for `FastVAD` to keep making its buffering decisions.

### `DispatchResult`

A small frozen dataclass. `FastVAD` needs the outcome, not just a
boolean:

| Field | Meaning |
|---|---|
| `matched: bool` | A pattern matched the text |
| `action: str \| None` | Action name, when matched |
| `executed: bool` | The handler actually ran |
| `deferred: bool` | Matched, but `no_early_match` — caller should keep buffering |
| `suppressed: bool` | Matched, but the wake gate was closed |
| `exec_ms: float \| None` | Handler wall time, for benchmark records |

### `dispatch_text()`

```python
def dispatch_text(
    text: str,
    *,
    listening_state=None,
    ui_callback=None,
    paused=None,
    early: bool = False,
) -> DispatchResult
```

Owns, in order: the paused check, pattern match, `no_early_match`
deferral, wake-gate check and suppression metric, match metric, the
`"match"` UI event **with the real text**, guarded handler invocation
(the existing `_invoke` body), the execute metric, and — only when
`early=False` — the no-match metric and the `"no_match"` UI event.

**The `early` flag exists for exactly one reason: `no_match` must never
fire on a partial transcription.** FastVAD transcribes every ~200ms
while you speak; emitting no-match per partial would strobe "didn't
catch that" throughout every sentence. Early calls stay silent on
no-match; only the final flush reports it.

### Callers

- **`matcher.dispatch_command`** keeps its queue loop and calls
  `dispatch_text(text, early=False, ...)` per item. It becomes ~15 lines.
- **`FastVAD`** takes a single `dispatch_func(text, early) ->
  DispatchResult` instead of the current `match_func` + `execute_func`
  pair, and drops `gate_func` entirely — gating now lives in
  `dispatch_text`. Its two dispatch sites become result inspections:
  `deferred` → keep buffering; `executed` or `suppressed` → treat as
  handled and clear the buffer.
- **`run_assistant`** deletes the `match_command` and `execute_command`
  closures and passes a single small `dispatch_func` that closes over
  `listening_state`, `ui_callback`, and `paused`. `transcribe_sync`
  stays — transcription is genuinely a VAD concern.

## Work items

Ordered so the suite stays green throughout.

1. **Add `DispatchResult` + `dispatch_text()` to `matcher.py`**, moving
   the body of the existing match loop and `_invoke` into it. No callers
   change yet. Suite still passes.
2. **Add the dispatch unit tests** (below) against the new function
   while both old paths still exist. They should pass immediately —
   this is the safety net for steps 3 and 4.
3. **Rewrite `matcher.dispatch_command`** to call `dispatch_text`.
   Standard mode must be behaviourally identical.
4. **Change the `FastVAD` constructor** to take `dispatch_func`; replace
   both dispatch sites with result inspection; delete `gate_func`.
5. **Delete the `match_command` / `execute_command` closures** in
   `run_assistant` and pass the new `dispatch_func`.
6. **Strip the now-duplicated metrics calls from `fast_vad.py`** — see
   below.
7. **Add an outer `try`/`except` in `FastVAD.run()`** covering the frame
   loop, so a transcription or VAD error logs and continues rather than
   killing the task.
8. **Widen the collision test** to catch phrase conflicts between
   *concurrently active* backends, not just within one. This is
   Phase 1's tripwire and is cheap to add here.
9. **Update `docs/DECISIONS.md`** with a "single dispatch seam" entry,
   and `docs/LESSONS.md` with the two-paths-drifted finding.

## Metrics: avoid double-counting

`dispatch_text` records match / suppressed / execute metrics. These
calls in `fast_vad.py` must be **removed** in step 6 or every command
counts twice:

- `:172` `record_command_suppressed` (early)
- `:181` `record_command_match` (early)
- `:187` `record_command_execute` (early)
- `:241` `record_command_suppressed` (final)
- `:243` `record_command_match` (final)
- `:247` `record_command_execute` (final)
- `:250` `record_command_match(None, matched=False)` (final no-match)

These **stay in `fast_vad.py`** — they are VAD/audio concerns that
`dispatch_text` has no visibility into:

- `record_speech_start`, `record_vad_emit`, `record_transcription`
- all `benchmark.*` calls, which need audio durations and the
  `early_match=` distinction. `benchmark.record_command` takes its
  `exec_dt` from `DispatchResult.exec_ms` instead of local timing.

## Tests to add

The point of the seam is that it is testable without audio. Today all
225 tests are registry / regex / perf; **nothing covers the dispatch
chain.** New file `test_dispatch.py`:

| Test | Asserts |
|---|---|
| matched text runs the handler | handler called once with captured args |
| match event carries real text | `ui_callback("match", …)` gets the actual string, not `""` — regression guard for symptom 2 |
| unmatched + `early=False` | `no_match` emitted with the text |
| unmatched + `early=True` | **no** `no_match` emitted |
| `no_early_match` action + `early=True` | `deferred=True`, handler not called |
| `no_early_match` action + `early=False` | handler called |
| gate closed | `suppressed=True`, handler not called, suppression metric recorded |
| `paused` set | nothing runs, no events |
| handler raises | exception does not propagate; `executed` reflects the attempt — regression guard for symptom 3 |

Registering a throwaway action against the real registry is the
cleanest fixture; the suite already manipulates active backends in
`test_matcher_rebuilds_on_slot_change`.

## Out of scope

Deliberately excluded. Each is real; none is Phase 0.

- **Any change to VAD tuning** — thresholds, `silence_ms`,
  `min_speech_ms`, `early_check_interval_ms`. Behaviour-preserving
  refactor only.
- **Wake-word logic**, the listening-window model, and
  `wake_word.py` generally.
- **New actions, or the Phase 1 phrase-ownership change.** No vocabulary
  moves here.
- **The `listening_state.activate` monkeypatch** (`rex.py:186-196`).
  Genuinely ugly; contained and working. Leave it.
- **`run_assistant`'s length and its inline logging setup.** It shrinks
  as a side effect; no further cleanup.
- **`whisper_worker._transcribe`'s private access** from `transcribe_sync`.
  Keep the diff tight.
- **Merging `metrics.py` and `benchmark.py`.** Two overlapping telemetry
  systems is real debt — a separate decision.
- **Deleting `rex_main/dashboard/`,** and the `commands.py` /
  `steelseries.py` shims. Unrelated cleanups.
- **FastVAD integration tests.** `_lazy_init` pulls Silero via
  `torch.hub`; testing the frame loop needs a fake `_infer` and a
  synthetic queue. Worth doing eventually, too heavy for this phase.
- **Removing standard mode.** It stays as the escape hatch.

## Risks

- **Behaviour drift in standard mode.** Mitigated by ordering: step 3
  lands against tests written in step 2.
- **Early/final asymmetry is subtle.** The `early` flag is the whole
  correctness argument for no-match; two tests pin it.
- **Double-counted metrics** if step 6 is missed. The benchmark session
  summary printed at shutdown is the fastest manual check — matched
  count should equal commands actually spoken.
- **`command_executed` semantics.** FastVAD currently sets it `True` on
  suppression (`:177`) to skip the flush. The result-inspection rewrite
  must preserve that, or suppressed commands get re-transcribed and
  re-dispatched at flush.

## Success criteria

Measurable, in order of what they protect:

1. **Exactly one call site invokes an action handler.**
   `grep -rn "handler(\*\|func(\*args" rex_main/` returns one location,
   inside `matcher.py`.
2. **`no_match` fires in the default configuration.** Automated: the
   `early=False` unmatched test passes. Manual: run
   `rex --console --debug` in default (low-latency) mode, say something
   nonsensical inside the wake window, and confirm the no-match event —
   and in the tray, that the HUD flashes "didn't catch that."
3. **The HUD shows the recognized text**, not an empty string, in
   low-latency mode.
4. **A throwing handler cannot kill the assistant.** Automated by the
   raise test; the assistant continues dispatching afterwards.
5. **All 225 existing tests still pass**, with the perf ceilings
   unchanged — dispatch stays well under 50 µs/match. `dispatch_text`
   is on the hot path, so `test_perf_dispatch_per_match` is the
   regression guard.
6. **~9 new tests**, all passing, none requiring audio hardware or a
   loaded model.
7. **Net negative diff** across `rex.py` + `fast_vad.py` + `matcher.py`.
   Consolidation should delete more than it adds; if it doesn't, the
   abstraction is wrong.
8. **One `record_command_match` per spoken utterance.** Verify against
   the session summary at shutdown.
9. **Standard mode is unchanged.** `rex --console --standard` behaves
   as before.

Criterion 7 is the honest check on the whole exercise. This is a
consolidation, not a rewrite — if the line count grows, stop and
reconsider.

## Outcome

Completed 2026-08-15. All nine work items landed.

| Criterion | Result |
|---|---|
| 1. One handler-invocation site | **Pass** — `matcher.py` `_invoke` only |
| 2. `no_match` fires by default | **Pass** — verified through the real FastVAD loop, once per utterance |
| 3. HUD gets recognized text | **Pass** — `text="skip song"`, not `""` |
| 4. Throwing handler can't kill the assistant | **Pass** — logged, loop continued |
| 5. Existing tests + perf ceilings | **Pass** — dispatch 2.43 µs / 50 µs |
| 6. New tests, no audio needed | **Pass** — 22 added (17 seam + 5 standard-loop) |
| 7. Net negative diff | **Failed as written** — see below |
| 8. One `record_command_match` per utterance | **Pass** — all 7 duplicate metric calls removed |
| 9. Standard mode unchanged | **Pass** — now covered by tests for the first time |

**On criterion 7:** raw line count across the three files grew by 68.
Counting logical statements instead (AST, excluding docstrings and
comments) gives **382 → 361, a net reduction of 21**. The executable
code shrank; the growth is explanatory comments — the `early`-flag
rationale, the error-boundary note, and the `DispatchResult` field
docs. Judgment call: the criterion was a proxy for "is the abstraction
earning its keep", and the statement count answers that better than the
line count. Recorded rather than silently redefined.

**Deviations from plan:**

- Tests swap `matcher._DISPATCH_TABLE` for a fake rather than
  registering throwaway actions in the real registry. Avoids polluting
  the global registry and coupling to `test_actions.py` collection
  order.
- The `FastVAD.run()` error boundary is a supervisor around an extracted
  `_loop()`, not a `try` inside the frame loop — restarting `_loop()`
  reinitialises utterance state, which is the correct recovery, and it
  avoided reindenting 120 lines.
- Suppression logging moved from `debug` to `info`. "Command didn't
  fire" is the most common support question and the wake gate is the
  first thing to check — see `/rex-diagnose-dispatch`.
- Added standard-mode queue-loop tests, which the plan didn't call for.
  `dispatch_command` is the other caller of the seam and had no coverage.

**Noted, not fixed (pre-existing, out of scope):** after an early match
the buffer clears, and the next speech frame re-enters the
"first speech frame" branch, resetting `command_executed = False`. A
sustained utterance that keeps transcribing to the same command can
therefore fire it more than once. This structure is unchanged from
before the refactor. Worth revisiting if repeat-fires show up in
practice.

## Rollback

Single-purpose commits per work item, no behaviour change bundled with
the refactor. If low-latency mode misbehaves after the fact, `--standard`
is an immediate user-side workaround while it's sorted.
