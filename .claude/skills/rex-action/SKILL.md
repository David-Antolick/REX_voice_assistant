---
name: rex-action
description: Add, modify, or remove a REX voice-triggered action. Use when wiring a new voice command, renaming or rephrasing patterns, changing args, adding a new backend file under rex_main/actions/, or touching anything decorated with @action. Enforces the ActionSpec contract, the one-file-per-backend rule, lazy-client + safe_call patterns, the docs/ACTIONS.md inventory sync, and the test_actions.py gate.
---

# REX Action Authoring

The action registry is the single source of truth for every voice command REX can perform. The contract is documented in `docs/ACTIONS.md`; this skill is the operational checklist for following it without drift. The #1 failure mode is shipping a working `@action` decorator while forgetting to update the inventory table at the bottom of `docs/ACTIONS.md` — which defeats the entire reason the registry exists.

## When to apply

Trigger on any of:
- Adding a new voice command to an existing backend (most common)
- Scaffolding a new backend file under `rex_main/actions/<backend>.py`
- Renaming, rephrasing, or removing a pattern on an existing action
- Changing an action's `args`, `slot`, `transport`, or `no_early_match`
- Any edit that touches files matching `rex_main/actions/**/*.py` other than `registry.py`

If the user says "add a voice command for X" or "wire up Y," this is the skill.

## Read these before editing

1. `docs/ACTIONS.md` — top half is the contract (ActionSpec fields, authoring rules, slot/collision policy). Bottom half is the inventory table you will be updating.
2. `rex_main/actions/registry.py` — the `ActionSpec` / `ArgSpec` / `@action` source. Confirms field names and defaults haven't drifted from the docs.
3. The most recently edited sibling backend file (e.g. `rex_main/actions/ytvd.py`) — current template for `safe_call`, lazy `_get()` / `reset_client()`, the `_END` / `_W` regex fragments, and the post-class `setattr(... safe_call(...))` wrap loop.

## Adding a single action to an existing backend

### Step 1 — Decide capability, slot, transport

- **`capability`**: abstract verb. If another backend already does the same thing (`play_music`, `next_track`), reuse that capability string. New verb → new string.
- **`slot`**: `None` (always-on) unless this competes with another backend for the same surface. Music slot is owned by `ytmd` xor `spotify`; YTVD video controls are `slot=None` because they don't compete.
- **`transport`**: one of `http_local` | `oauth_cloud` | `os_native` | `gamesense` | `local`. Documents the trust/failure model. Pick the one that matches how the backend talks to the world.

### Step 2 — Write the regex pattern

- Anchor with `^` and use the shared `_END` fragment (`[.!?\s]*$`) at the tail so trailing punctuation/whitespace from Whisper doesn't break the match.
- Use the shared `_W` (`\s*`) for tolerant leading whitespace.
- Capture groups become `args`. **The number of capture groups must equal `len(args)`** — `test_actions.py` enforces this and the suite will fail loudly if you miscount.
- Patterns are raw regex source strings, not compiled. The matcher compiles them with `re.I` after slot resolution.

### Step 3 — Write the wrapper + client method

The decorated function is a one-liner that delegates to the lazy singleton. Real logic lives on the client class:

```python
@action(
    name="ytvd_toggle_fullscreen",
    capability="toggle_fullscreen",
    backend=_BACKEND,
    transport=_TRANSPORT,
    summary="Toggle YouTube video fullscreen.",
    patterns=[rf"^{_W}(?:toggle\s+)?(?:fullscreen|full\s+screen){_END}"],
    preconditions=_PRECONDS,
    side_effects=("video_fullscreen",),
    examples=("fullscreen", "full screen", "toggle fullscreen"),
)
def toggle_fullscreen() -> None:
    _get().toggle_fullscreen()
```

The client method does the real work. If it touches the network, it must be wrapped with `safe_call` (either as a decorator on the method, or appended to the post-class `setattr` loop at the bottom of the backend file — match what's already there).

### Step 4 — Set `no_early_match=True` if the pattern takes args or has ambiguous prefixes

The FastVAD path can fire on partial transcriptions. For variable-length args (`"search …"`, `"speed 1.5"`, `"skip ahead 10"`), the partial would match before the user finishes speaking — `no_early_match=True` forces the matcher to wait for the full utterance. Defaults to `False`.

Rule of thumb: any pattern with `(…+)` or `(\d+)` should have `no_early_match=True`.

### Step 5 — Update the inventory in `docs/ACTIONS.md`

This is the step everyone forgets. Find the backend's table in the inventory section and add a row. Keep the columns aligned. If you removed an action, delete its row in the same edit. If you renamed, update the row.

The user-visible phrase in the "Phrases" column is what the user actually says — not the regex. Mirror the `examples=` tuple in plain words.

### Step 6 — Update `README.md` if the phrase is user-visible

The commands table in `README.md` is authoritative for end users. New phrases, removed phrases, or significantly changed phrasings need a README edit in the same change. Same-meaning regex tightening doesn't.

### Step 7 — Run the test gate

```powershell
pytest test_actions.py -v
```

This is non-negotiable. The suite checks:
- Every `@action` name is unique
- Every regex compiles
- Every `examples=` entry actually matches its own patterns
- `len(args)` equals the number of capture groups in the patterns
- No intra-backend phrase collisions
- Dispatch hot-path latency stays under hard µs/ms ceilings

If a perf ceiling fails, **investigate the regression — don't raise the ceiling.** The ceilings have ~30× headroom; a failure means real slowdown.

## Scaffolding a new backend (the `discord` / `steam` / `windows_audio` recipe)

When you're not adding to an existing file but creating a new one:

1. Create `rex_main/actions/<backend>.py`. Copy the structure from the most recent sibling (`ytvd.py` is the freshest reference). Keep the module docstring; replace the body.
2. Add the client class (or import a vendored SDK), a module-level `_client` / `_get()` accessor, and `reset_client()` for service-switch flows. **Do not instantiate the client at import time** — the registry imports before user secrets are loaded.
3. Define `safe_call` at the top of the file (copy the pattern from `ytvd.py:34`). All network/IPC methods get wrapped, either inline or via the post-class `setattr` loop.
4. Define module-level `_BACKEND`, `_TRANSPORT`, `_PRECONDS`, `_END`, `_W` constants. These keep the `@action` decorators DRY.
5. Decorate one wrapper function per voice command with `@action`. One-liner bodies that call `_get().method(...)`.
6. Add `from rex_main.actions import <backend>  # noqa: F401` to `rex_main/actions/__init__.py`. Order matters: list new backend imports **before** `service`, since `service.py` reads the registry at import time. See the import-order lesson in `docs/LESSONS.md` if you hit a registration-order bug.
7. If the backend has a `slot`, teach `actions/service.py:configure_from_config` how to read the user's config and call `set_active_backends({slot: backend_name})`. Also add a `warm()` hook if first-call latency is non-trivial (OAuth round-trip, COM pointer acquisition) — see the lazy-init lesson in `docs/LESSONS.md`.
8. Add a new section to the inventory in `docs/ACTIONS.md` following the existing format (header line with slot + transport, then a Markdown table, then a preconditions footnote).
9. Add a row (or rows) to the commands table in `README.md` for user-visible phrases.
10. Run `pytest test_actions.py -v`.

## Hard rules (the docs/ACTIONS.md authoring contract, condensed)

- **One file per backend.** Don't shove a new backend's actions into a sibling file.
- **Wrappers stay thin.** No business logic in the `@action` function body. One delegation line.
- **Lazy clients.** No instantiation at import time. Always `_get()`.
- **`safe_call` at network boundaries.** Internal calls between REX modules are trusted; HTTP / OAuth / OS / COM calls are not.
- **Never log secrets.** Tokens belong in `keyring`, never in error messages or `logger.error(..., token)`.
- **Patterns are raw strings.** Don't pre-compile. The matcher compiles after slot resolution.
- **Update `docs/ACTIONS.md` inventory in the same change.** Out-of-date inventory defeats the registry.
- **Phrase-based disambiguation, not prefix-based.** If a phrase collides with another active backend, pick a distinct phrasing (`mute me` vs `mute audio` vs `mute music`) rather than prefixing with the app name.

## Common mistakes

- **Forgot the `docs/ACTIONS.md` inventory row.** The most common drift. Treat the inventory edit as part of the code change, not a follow-up.
- **`args` count ≠ capture groups.** `test_actions.py` catches this but the error is confusing if you forget the rule. Count `(` in the pattern; that's your `len(args)`.
- **No `no_early_match=True` on a variable-arg action.** The FastVAD path will fire on partial transcriptions. Symptom: `"search the beatles"` triggers as `"search the"` mid-utterance.
- **Adding a `print()` for debugging and forgetting to remove it.** Use `logger = logging.getLogger(__name__)` at the top of the file; never `print()`.
- **Wrapping internal calls with `safe_call`.** Only wrap calls that leave the process. Internal REX-to-REX calls are trusted; defensive wrapping adds noise.
- **Hand-editing the regex table in `matcher.py`.** There is no regex table in `matcher.py` anymore — it pulls from the registry. If you find yourself opening `matcher.py` to add an action, stop and re-read `docs/ACTIONS.md`.
- **Eager client instantiation.** Instantiating at module top-level breaks the import order and pushes latency onto the user's first command. Always lazy.

## Quick verification checklist

Before declaring an action change done:

1. `@action` decorator declares `name`, `capability`, `backend`, `transport`, `summary`, `patterns`, `examples` at minimum.
2. `len(args)` equals the number of capture groups in the patterns.
3. Variable-arg patterns have `no_early_match=True`.
4. Wrapper body is one delegation line; client method does the work.
5. Network calls go through `safe_call`.
6. New row added (or existing row updated/removed) in `docs/ACTIONS.md` inventory.
7. `README.md` commands table updated if user-visible.
8. `pytest test_actions.py -v` passes — both correctness and perf ceilings.

## Related docs

- `docs/ACTIONS.md` — full contract and inventory.
- `docs/DECISIONS.md` — entries on action-registry design, slot routing, phrase-based disambiguation, and the perf-ceiling decision.
- `docs/LESSONS.md` — `localhost`-IPv6 coercion, lazy-init hot-path, decorator-import-order, microbench-vs-reality.
- `rex_main/actions/registry.py` — ActionSpec source.
- `test_actions.py` — the gate.
