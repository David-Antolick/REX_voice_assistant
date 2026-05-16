---
name: rex-diagnose-dispatch
description: Diagnose why a REX voice command isn't firing — wake-word said but nothing happens, command works sometimes but not always, new action appears broken on a fresh tray, one-word commands disappear. Walks the wake-gate → Whisper-latency → FastVAD-min-speech → matcher chain in the order that ruled out the most common causes during the YTVD-fullscreen debug session in 2026-05. Use before suspecting the action's regex or handler — those are almost always fine.
---

# REX Dispatch Diagnosis

When a voice command isn't firing, the regex is almost never the problem. The chain that breaks is upstream: the wake-word gate closing before the transcription arrives, Whisper's tail latency eating the listening window, or FastVAD's `min_speech` floor silently dropping the utterance. This skill walks that chain in the order that pays off fastest.

The reference incident: a working `play music` command and a brand-new `fullscreen` command both appeared dead on a freshly restarted tray. Two hours of staring at the matcher table found nothing because the matcher was matching fine — the wake gate had already closed by the time the transcription got there. See `docs/LESSONS.md` "Wake-word listening window vs Whisper latency" for the full writeup.

## When to apply

Trigger on any user report shaped like:
- "I say 'hey rex' then `<command>` but nothing happens"
- "It worked yesterday / it works sometimes / it works for some commands but not others"
- "The new action I just added doesn't fire" (always rule out dispatch *before* assuming the registration is broken)
- "Short / one-word commands don't work, longer ones do"
- "It works in the console but not in the tray" (or vice versa)

Do **not** trigger on: explicit regex / pattern-match questions, transport-layer errors (`Connection refused`, OAuth failures), or commands that fire reliably but produce wrong behavior — those are not dispatch problems.

## The chain, in order

You're walking from the easiest-and-most-common cause to the rarest. Stop as soon as one fires.

### Step 0 — Relaunch with `--debug`

**Do this first, before any other diagnosis.** The matcher's most useful log lines are at DEBUG level:

```
Received text: 'play music.'
No command matched: 'hey, rex.'
Suppressed early match 'ytmd_play_music' from 'play music' (wake word not active)
FastVAD flushing utterance: 20 frames (~0.64 s)
FastVAD flushing 8-frame (~0.26 s) utterance     ← dropped
```

Without `--debug`, you see wake-fire counts and an empty match counter and you have to guess. With it, the suppression and the dropped-frame counts are visible.

```powershell
rex --console --debug
```

(Tray works too — `rex --debug` — but console gives you a live log stream side-by-side with the speech.)

### Step 1 — Check the `Suppressed:` counter and gate behavior

In the metrics printout (or the periodic metrics log), look for the `Suppressed: N` counter. **If `Suppressed` is non-zero while `Commands: 0`, the wake gate is closing before the command transcription arrives.** This is the #1 cause of "command appears broken."

In `--debug` you'll also see the smoking-gun line:

```
Suppressed early match 'ytmd_play_music' from 'play music' (wake word not active)
```

That names the regex that *would* have matched — proving the matcher is healthy and the gate is the problem.

The gate **does not auto-extend on each wake fire** — it extends on each **matched** command. So speak-pause-speak patterns push the second utterance's transcription past the window.

### Step 2 — Check the Whisper model + device

The latency budget is:

```
Whisper(p99) + 1 × command_speech ≤ wake_word.listening_window_seconds
```

If `Whisper(p99)` alone is close to the window, you're cooked. Reference numbers:

| Model | Device | Typical | p99 |
|---|---|---|---|
| `tiny.en` | CPU | 200–500 ms | ~700 ms |
| `tiny.en` | CUDA | 50–100 ms | ~150 ms |
| `medium` | CPU | 3–5 s | ~5 s |
| `medium` | CUDA | 200–400 ms | ~600 ms |

Default `listening_window_seconds` is 6. `medium` on CPU leaves ~1 s of headroom — any speak-pause pattern pushes past. **Switch to `tiny.en` for live command dispatch regardless of device.** Commands are short and constrained; `tiny.en` is accurate enough.

Check the active config:

```powershell
rex --print-config | Select-String -Pattern "model|listening_window"
```

Or look directly at `~/.rex/config.yaml`. The `--gaming` preset is equivalent to:

```yaml
model:
  name: tiny.en
  device: cpu
```

### Step 3 — Check FastVAD's `min_speech` floor for one-word commands

FastVAD's `min_speech=300ms` filter silently drops short utterances. Saying `"fullscreen"` quickly produces ~250 ms of speech, gets flushed as a too-short utterance, and **is never transcribed**. There is no INFO-level log telling you this happened — only the `~0.13 s` frame-count line at DEBUG:

```
FastVAD flushing 4-frame (~0.13 s) utterance
```

Symptoms:
- One-word commands (`"home"`, `"sub"`, `"like"`, `"fullscreen"`) don't fire
- Two-word commands (`"play music"`, `"next track"`) work fine
- Saying the wake word and command in one breath works; saying them with a pause doesn't

Fixes (in order of preference):
1. **Coach the user to say wake + command in one breath.** Don't change the floor — it's there for a reason (filters background noise / cough / single-syllable artifacts).
2. If the action is intentionally one-word and that pattern matters, consider adding pre/post padding to the example phrasing (e.g. accept `"go fullscreen"` as well as `"fullscreen"`).
3. Only drop the floor as a last resort, and only after weighing the false-fire cost.

### Step 4 — Confirm the action is actually registered

Only after Steps 0–3 are ruled out:

```powershell
python -c "from rex_main.actions.registry import all_specs; [print(s.name, s.patterns) for s in all_specs() if 'fullscreen' in s.name]"
```

If the action doesn't appear, the registration didn't run. Check:
- The backend file is imported in `rex_main/actions/__init__.py`
- Import order in `__init__.py` lists backends **before** `service` (see `docs/LESSONS.md` decorator-import-order entry)
- The `@action` decorator name matches what you're searching for

### Step 5 — Confirm the slot is active

If the action is registered but `active_specs()` doesn't include it:

```powershell
python -c "from rex_main.actions.registry import active_specs, all_specs; [print(s.name) for s in all_specs() if s.name not in {a.name for a in active_specs()}]"
```

That prints the inactive ones. If your action is in the list, its slot's backend isn't set. Check `actions/service.py:configure_from_config` and the user's `~/.rex/config.yaml` to confirm the right backend is selected for the slot.

### Step 6 — Now and only now, look at the regex

If you've eliminated 0–5 and the matcher is receiving the transcription inside the window and the action is active, **then** suspect the pattern. Most likely:

- Missing `^` anchor (matcher only matches anchored patterns)
- Missing `_END` tail (Whisper's trailing punctuation breaks the match)
- `args` count mismatched with capture groups (would have failed `test_actions.py` though — re-run it)

Test the pattern in isolation:

```powershell
python -c "import re; print(re.match(r'^\s*(?:toggle\s+)?(?:fullscreen|full\s+screen)[.!?\s]*$', 'fullscreen.', re.I))"
```

## Quick decision tree

```
Is the user reporting "nothing happens"?
├─ Run with --debug. Check the next metrics print.
├─ Suppressed > 0 and Commands == 0?
│   └─ Gate is closing too early. Step 1 / Step 2.
│       └─ Whisper model = medium? → switch to tiny.en. Done.
│       └─ Whisper tiny.en already? → increase wake_word.listening_window_seconds to 10s.
├─ Suppressed == 0 and "No command matched" log line never appears?
│   └─ Transcription isn't reaching the matcher. Step 3.
│       └─ Look for "FastVAD flushing N-frame (~0.Xs) utterance" with N < 10.
│       └─ Yes → command is below min_speech floor. Coach the user, or pad the phrase.
├─ "No command matched" log line appears with the user's text?
│   └─ Matcher saw it but no regex hit. Step 4 / Step 5 / Step 6.
```

## Things this skill exists to prevent

- Spending an hour rewriting a regex that was always fine
- Restarting the tray and re-recording wake samples for a problem that's actually Whisper latency
- "Maybe the new action broke something" when the new action is fine and the user just changed their Whisper model last week
- Raising `min_speech` "to see if it helps" without understanding the false-fire cost
- Claiming a fix worked because the next test happened to land inside the gate

## Related docs

- `docs/LESSONS.md` — "Wake-word listening window vs Whisper latency" (the reference incident), "Microbenchmarks lie when you stub the real I/O" (the same trap shifted left).
- `rex_main/matcher.py` — `dispatch_command`, the DEBUG `Received text` and `No command matched` lines, the `Suppressed early match` log.
- `rex_main/fast_vad.py` — `min_speech` / `silence` knobs.
- `rex_main/metrics.py` — `Suppressed:` counter.
- `rex_main/benchmark.py` + `metrics_printer.py` — the live E2E / VAD / Whisper / Exec breakdown that tells you which stage is slow.
