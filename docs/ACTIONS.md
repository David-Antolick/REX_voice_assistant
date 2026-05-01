# REX Action Registry

Single source of truth for every voice-triggered action.
Every command REX can perform is declared here and in `rex_main/actions/`.

> **Maintenance rule:** when you add, remove, or rename an action, update both
> the code (`@action` decorator) **and** the inventory at the bottom of this
> document, in the same change. Out-of-date entries here defeat the whole point
> of the registry.

---

## Why the registry exists

1. **Discoverability.** One place to see every command, its phrases, its backend,
   what it changes, and what it depends on. No more scattering regex tables across
   modules.
2. **Backend isolation.** Each app (YTMD, Spotify, SteelSeries, future:
   Discord/Steam/Audio) gets one file under `rex_main/actions/`. Adding an app
   means adding a file — never editing `matcher.py` or `commands.py`.
3. **Slot-based routing.** Multiple backends can implement the same capability
   (e.g. YTMD and Spotify both `play_music`). At runtime exactly one backend
   "owns" each slot (`music`, `clipping`, etc.); the matcher only compiles the
   patterns of active backends. No `global` rebinding, no string-name `getattr`.
4. **Planner-ready metadata.** Each `ActionSpec` carries `summary`, `args`,
   `preconditions`, `side_effects`, `examples` — enough for a future LLM layer
   to ask *"to accomplish X, which actions do I need and in what order?"*.

---

## How an action is declared

Every action lives in a backend module under `rex_main/actions/<backend>.py`
and is registered with the `@action` decorator. Minimal example:

```python
from rex_main.actions.registry import ArgSpec, action

@action(
    name="ytmd_play_music",          # globally unique, snake_case
    capability="play_music",         # abstract verb shared across backends
    backend="ytmd",                  # which app implements this entry
    slot="music",                    # which service slot it owns; None = always-on
    transport="http_local",          # http_local | oauth_cloud | os_native | gamesense | local
    summary="Resume YouTube Music playback.",
    patterns=[r"^\s*play\s+music\s*[.!?\s]*$"],
    args=(),                                 # ArgSpec entries if the regex captures
    preconditions=("YTMD desktop app running",),
    side_effects=("playback_state",),
    examples=("play music",),
    no_early_match=False,                    # True = wait for the full utterance
)
def play_music() -> None:
    _get().play_music()
```

### Field reference

| Field | Required | Purpose |
|------|---|---|
| `name` | yes | Unique stable id. Convention: `<backend>_<capability>`. Used in logs, metrics, and registry lookups. |
| `capability` | yes | Abstract verb. Multiple backends can share a capability (Spotify and YTMD both `play_music`). |
| `backend` | yes | The app providing this entry: `ytmd`, `spotify`, `steelseries`, `discord`, `steam`, `windows_audio`, … Use `rex` for REX-internal actions. |
| `slot` | no | Which mutually-exclusive service slot this backend owns. `None` = always-on. Current slots: `music`. Future: `voice_chat`, `system_audio`, `game_platform`. |
| `transport` | yes | How the action talks to the world. Documents the trust/failure model. Allowed: `http_local`, `oauth_cloud`, `os_native`, `gamesense`, `local`. |
| `summary` | yes | One-line natural-language description. Read by humans and (eventually) by the planner. |
| `patterns` | yes | List of raw regex source strings. Compiled by the matcher with `re.I`. Anchor with `^` and the shared `_END` fragment. |
| `args` | no | Tuple of `ArgSpec(name, type, description)`. One per regex capture group, in order. Types: `"str"`, `"int"`, `"enum:a\|b\|c"`. |
| `preconditions` | no | Human-readable prerequisites (used in docs and error messages). |
| `side_effects` | no | What state changes when this fires (e.g. `playback_state`, `volume`, `library`, `clip_saved`). Used by the planner to chain actions. |
| `examples` | no | Natural utterances. Powers docs and any future few-shot prompting. |
| `no_early_match` | no | `True` if the FastVAD path must wait for the full utterance (variable args, ambiguous prefixes). |

### Authoring rules

1. **One file per backend.** `rex_main/actions/<backend>.py`. The file owns the
   client class (if any), a lazy singleton accessor, and the `@action` wrappers.
2. **Wrappers are thin.** The `@action`-decorated function is a one-liner that
   delegates to the singleton. Real logic lives on the client class.
3. **Lazy clients.** Use the `_client / _get() / reset_client()` pattern (see
   `actions/ytmd.py`). Do not instantiate at import time — REX imports the
   registry before the user's secrets are loaded.
4. **Network calls go through `safe_call`.** Defined per-backend; mirror the
   pattern in `actions/ytmd.py`.
5. **Patterns are regex source strings, not compiled.** The matcher compiles
   them after slot resolution. Use the shared `_END` and `_W` fragments at the
   top of each backend file for consistency.
6. **Validate at boundaries only.** User voice input, config, and HTTP
   responses are untrusted. Internal calls between actions are trusted —
   no defensive checks.
7. **Never log secrets.** Tokens / OAuth codes belong in `keyring`, never in
   log lines or error messages.
8. **Update `ACTIONS.md`.** Add or remove the row(s) in the inventory below in
   the same commit.

---

## Slots

A slot is a mutually-exclusive service category. At any time, at most one
backend owns each slot, set via `set_active_backend(slot, backend)` (driven
by config or by `switch_to_*` voice commands).

| Slot | Meaning | Current backends |
|---|---|---|
| `music` | Audio playback / queue / library | `ytmd`, `spotify` |
| *(future)* `voice_chat` | Voice mute/deafen for Spacebar/Fermi (planned) | `spacebar` |
| *(future)* `system_audio` | Per-app and system volume | `windows_audio` |
| *(future)* `game_platform` | Launching games / library queries | `steam` |
| *(future)* `clipping` | Currently always-on (one backend) | `steelseries` |

If your action has no competing alternative, set `slot=None`.

### Avoiding phrase collisions across slots

The user does *not* say the backend name. If two slots can plausibly match the
same phrase, disambiguate by phrasing, not by prefixing:

- `mute me`     → voice chat (Discord)
- `mute audio`  → system audio
- `mute music`  → music backend

Pick distinct phrasing per-collision when it shows up. Don't preemptively
prefix everything with the app name.

---

## Inventory

Generated by hand — keep it in sync with `@action` decorators. Sorted by backend.
Phrases column shows the user-facing utterance, not the regex.

### `ytmd` — YouTube Music Desktop (slot: `music`, transport: `http_local`)

| Action | Capability | Phrases | Args | Side effects |
|---|---|---|---|---|
| `ytmd_play_music` | `play_music` | "play music" | — | `playback_state` |
| `ytmd_stop_music` | `stop_music` | "stop music" | — | `playback_state` |
| `ytmd_next_track` | `next_track` | "next", "skip" | — | `current_track` |
| `ytmd_previous_track` | `previous_track` | "last", "previous" | — | `current_track` |
| `ytmd_restart_track` | `restart_track` | "restart" | — | `track_position` |
| `ytmd_search_song` | `search_song` | "search X", "search X by Y" | `title:str`, `artist:str?` | `playback_state`, `current_track` |
| `ytmd_volume_up` | `volume_up` | "volume up" | — | `volume` |
| `ytmd_volume_down` | `volume_down` | "volume down" | — | `volume` |
| `ytmd_set_volume` | `set_volume` | "volume 50" | `level:int` | `volume` |
| `ytmd_like` | `like` | "like" | — | `track_rating` |
| `ytmd_dislike` | `dislike` | "dislike" | — | `track_rating` |
| `ytmd_so_sad` | `so_sad` | "this is so sad" | — | `playback_state`, `current_track` |

Preconditions for all: YTMD desktop app running with Companion-Server enabled.

### `spotify` — Spotify Web API + Connect (slot: `music`, transport: `oauth_cloud`)

| Action | Capability | Phrases | Args | Side effects |
|---|---|---|---|---|
| `spotify_play_music` | `play_music` | "play music" | — | `playback_state` |
| `spotify_stop_music` | `stop_music` | "stop music" | — | `playback_state` |
| `spotify_next_track` | `next_track` | "next", "skip" | — | `current_track` |
| `spotify_previous_track` | `previous_track` | "last", "previous" | — | `current_track` |
| `spotify_restart_track` | `restart_track` | "restart" | — | `track_position` |
| `spotify_search_song` | `search_song` | "search X", "search X by Y" | `title:str`, `artist:str?` | `playback_state`, `current_track` |
| `spotify_volume_up` | `volume_up` | "volume up" | — | `volume` |
| `spotify_volume_down` | `volume_down` | "volume down" | — | `volume` |
| `spotify_set_volume` | `set_volume` | "volume 50" | `level:int` | `volume` |
| `spotify_like` | `like` | "like" | — | `library` |
| `spotify_dislike` | `dislike` | "dislike" | — | `library` |
| `spotify_shuffle_on` | `shuffle_on` | "shuffle on" | — | `shuffle_state` |
| `spotify_shuffle_off` | `shuffle_off` | "shuffle off" | — | `shuffle_state` |
| `spotify_set_repeat` | `set_repeat` | "repeat off / context / track" | `mode:enum` | `repeat_state` |
| `spotify_queue_track` | `queue_track` | "next track X" | `query:str` | `queue` |
| `spotify_current_track_info` | `current_track_info` | "what's playing", "track info", "current track info" | — | — |
| `spotify_so_sad` | `so_sad` | "this is so sad" | — | `playback_state`, `current_track` |

Preconditions for all: Spotify Connect device available; OAuth credentials configured.

### `steelseries` — SteelSeries GG Moments (slot: `None`, transport: `gamesense`)

| Action | Capability | Phrases | Args | Side effects |
|---|---|---|---|---|
| `steelseries_clip_that` | `clip_that` | "clip that / it", "save that / clip", "capture that / it", "record that / clip" | — | `clip_saved` |

Preconditions: SteelSeries GG running; Moments enabled and recording; REX
autoclipping enabled in GG → Settings → Moments → Apps.

### `rex` — REX-internal (slot: `None`, transport: `local`)

| Action | Capability | Phrases | Args | Side effects |
|---|---|---|---|---|
| `switch_to_spotify` | `switch_music_backend` | "switch to spotify" | — | `active_music_backend` |
| `switch_to_ytmd` | `switch_music_backend` | "switch to youtube music" | — | `active_music_backend` |

### `apps` — Application launch / close (slot: `None`, transport: `os_native`)

| Action | Capability | Phrases | Args | Side effects |
|---|---|---|---|---|
| `apps_open_youtube_music` | `open_app` | "open / launch / start youtube music" | — | `ytmd_process_running` |
| `apps_close_youtube_music` | `close_app` | "close / quit / exit / kill youtube music" | — | `ytmd_process_running` |
| `apps_open_spotify` | `open_app` | "open / launch / start spotify" | — | `spotify_process_running` |
| `apps_close_spotify` | `close_app` | "close / quit / exit / kill spotify" | — | `spotify_process_running` |

Launch resolves the first existing path from a small candidate list per
app (LocalAppData → Program Files → Microsoft Store WindowsApps). Close
shells out to `taskkill /F` against the app's image name. If the app
isn't installed in any known location, the open command logs a warning
and does nothing.

---

## Adding a new backend (the `discord` / `steam` / `windows_audio` recipe)

1. Create `rex_main/actions/<backend>.py`.
2. Add the client class (or import it from a vendored SDK), a lazy `_get()`
   accessor, and a `reset_client()` for service-switch flows.
3. Decorate one wrapper function per voice command with `@action`. Use a
   dedicated `slot` if the backend competes for the same surface as another
   (e.g. `voice_chat` for Discord vs a future TeamSpeak), or `slot=None` if
   it's the only implementer.
4. Add `from rex_main.actions import <backend>  # noqa: F401` to
   `rex_main/actions/__init__.py` (after registry, before `service`).
5. If the backend has a slot, teach `actions/service.py:configure_from_config`
   how to read the user's config and call `set_active_backends({slot: name})`.
6. Add a section to the inventory above.
7. Update [README.md](../README.md) commands table for any user-visible phrases.
8. Run `pytest test_actions.py -v` and confirm both correctness checks
   (name unique, regex compiles, examples match, ArgSpec ↔ capture
   groups, no phrase collisions inside the backend) and the perf
   ceilings still pass. The suite is the gate — if a ceiling fails,
   investigate the regression before raising the ceiling.

---

## Related docs

- [DECISIONS.md](DECISIONS.md) — why the registry exists, why slots, why
  phrase-based disambiguation, why the perf ceilings.
- [LESSONS.md](LESSONS.md) — gotchas hit while building this (Windows IPv6
  fallback, microbench-vs-reality, etc.).
