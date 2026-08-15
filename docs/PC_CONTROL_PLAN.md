# REX PC Control Plan

The north star for turning REX from a music remote into a general PC
controller. Like [UI_PLAN.md](UI_PLAN.md), this is deliberately light on
implementation — the goal is to lock in *what* we're building, *what we
refuse to build*, and the handful of decisions that are expensive to
reverse later.

Covers Phase 1 onward. Phase 0 (consolidating the two dispatch paths) is
a prerequisite with its own implementation plan; see the note at the end.

## Why we're doing this

REX gets used when gaming and almost never otherwise. The reason isn't
polish — it's vocabulary. Of 44 registered actions, **43 touch music
playback.** Only `clip that` doesn't.

That has a direct consequence: "volume up" doesn't change the PC's
volume, it changes YouTube Music's. So REX is only useful in the one
situation where music is the thing making noise — which is when you're
gaming. Watching a video in a browser, in a voice call, editing footage,
reading with something else playing: REX does nothing, and there's no
obvious reason from the outside why it should be that way.

The fix isn't a better UI. It's giving REX things to say that apply to
the whole machine.

Two capabilities are already paid for and unused:

- **`apps.py` has a fully generic app resolver** — hardcoded paths →
  Start-menu `.lnk` scan → Windows' `Get-StartApps` catalog. It can find
  any installed app. It is driven by a two-entry hardcoded dict.
- **Whisper transcribes continuously**, and the text feeds only a regex
  table. Dictation is nearly free in capability terms.

The architecture is also already pointed this way. `docs/ACTIONS.md`
lists `os_native` as an allowed transport and `system_audio` as a
planned slot. Both are currently unused.

## Design principles

- **Generic phrases mean the machine, not the app.** When you say
  "volume up" you mean the computer. This inverts today's behavior and
  is the single most important decision in this document.
- **Everyday over impressive.** A command earns its place by being said
  often, not by being clever. Ten commands used daily beat forty used
  once.
- **Never surprise destructively.** A misheard "skip song" costs
  nothing. A misheard "shut down" costs an afternoon. Commands are
  gated by how bad a false positive is, not by how hard they are to
  build.
- **Fail visibly.** If REX doesn't understand, the user must see that.
  Silence is what makes a voice assistant feel broken.
- **Windows-native, no daemons.** No helper services, no background
  agents, no elevated privileges. If it needs admin, it's out of scope.

## The decision to lock in first: who owns the generic phrases

Today `ytmd` and `spotify` both declare `"volume up"`. That is safe
only because they are mutually exclusive — the `music` slot guarantees
one is active at a time.

An always-on `system` backend breaks that guarantee. `system` and
`ytmd` would both be active, both matching `"volume up"`, and the
winner would be decided by **module import order in
`rex_main/actions/__init__.py`** — incidental, silent, and not covered
by tests. `test_no_phrase_collision_within_a_backend` only checks
within a single backend; its docstring explicitly assumes cross-backend
collisions are between slot-exclusive peers.

**Decision: generic phrases belong to `system`. App-specific control
requires naming the app.**

| Phrase | Before | After |
|---|---|---|
| "volume up" | YTMD app volume | **System volume** |
| "music volume up" | — | YTMD / Spotify app volume |
| "pause" | YTMD only | **Whatever is playing** (media key) |
| "pause music" | — | YTMD / Spotify explicitly |
| "mute" | — | **System mute** |

This is the right default on the merits — you usually want the whole
machine quieter, and media keys work in browsers, VLC, Netflix, and
calls where the music APIs don't reach.

Two consequences worth stating plainly:

1. **Phase 1 is not purely additive.** It requires re-phrasing existing
   `ytmd` / `spotify` patterns, not just adding a file. That's a larger
   change than "drop in a new backend" and needs the `/rex-action`
   contract followed on the existing backends too.
2. **The collision test must be widened** to catch conflicts between
   *concurrently active* backends, not just within one. Without that,
   this class of bug returns the next time a slotless backend is added.

A `system_audio` slot is **not** needed. Slots exist to arbitrate
between interchangeable backends; there is only one Windows audio
endpoint. `system` is `slot=None`, like `apps`.

## Phase 1 — the `system` backend

One new file, `rex_main/actions/system.py`. Transport `os_native`,
`slot=None`, no network, no auth, no new services. This is the phase
that changes what REX *is*.

### System audio

| Intent | Phrases |
|---|---|
| Volume up / down | "volume up", "louder", "volume down", "quieter" |
| Set volume | "volume 40", "set volume to 40" |
| Mute / unmute | "mute", "unmute" |

Absolute volume ("volume 40") is the one that needs real Core Audio
access rather than volume keys — see Open Questions.

### Global media control

Media keys reach any app that registers for them, which is the whole
point: these work where the music-backend APIs don't.

| Intent | Phrases |
|---|---|
| Play / pause | "play", "pause", "play pause" |
| Next / previous | "next", "previous" |
| Stop | "stop" |

### Window management

| Intent | Phrases |
|---|---|
| Minimize / maximize | "minimize", "maximize", "restore" |
| Close window | "close window" |
| Snap left / right | "snap left", "snap right" |
| Show desktop | "show desktop", "minimize everything" |
| Switch desktop | "next desktop", "last desktop" |

"close window" sends a close *request* the app can respond to (offering
to save), never a forced process kill. `apps.py` already uses
`taskkill /F` for "close spotify" — that stays scoped to named apps
where it's unambiguous, and is not how generic window close behaves.

### Screen / session

| Intent | Phrases |
|---|---|
| Lock | "lock", "lock the screen" |

Sleep, restart, shut down, and log off are **deliberately excluded** —
see Safety Rails.

Roughly 15 actions. Together with Phase 0, this is the milestone where
REX stops being a music remote.

## Phase 2 — generalized app control

The resolver already exists; this phase gives it a voice.

| Intent | Phrases |
|---|---|
| Launch anything | "open Blender", "launch Discord", "start Steam" |
| Focus a running window | "switch to Chrome", "go to Discord" |
| Close by name | "close Blender" |

**Focus is probably used more than launch.** Most of the day the app is
already running and buried — "switch to Discord" replaces an alt-tab
hunt, which is a genuinely everyday action in a way that launching
isn't.

Design notes:

- The Start-menu / `Get-StartApps` catalog should be enumerated **once
  at startup and cached**, not per command. Discovery already caches
  per-app for the session; this widens it to the full catalog.
- Matching must be fuzzy. Whisper will return "blender", "blunder",
  "blend her". Exact matching will feel broken.
- These patterns capture free text and must be `no_early_match=True`,
  or FastVAD will fire on a prefix before the app name arrives.
- Greedy `open (.+)` patterns are the main risk to the rest of the
  command surface. `docs/DECISIONS.md` — "Phrase-based disambiguation,
  not prefix-based" — is the governing precedent.
- The four existing hardcoded `apps_*` actions get replaced by the
  general form. Their phrases must keep working.

## Phase 3 — dictation

The always-on unlock, and the reason tools like Wispr Flow and
SuperWhisper get left running all day. REX already has the hard part:
a warm Whisper model listening continuously.

The shape: a mode you enter deliberately ("take a note", "start
dictation"), where transcribed text goes to the focused text field
instead of the matcher, until you leave it.

This is the largest UX surface in the document and deserves its own
design pass. The hard questions:

- **How do you end it?** A phrase can be dictated by accident. A
  timeout truncates real thoughts.
- **Command isolation is a safety requirement, not a nicety.** While
  dictating, "close window" must be *text*, never an action. The two
  modes must not share a dispatch path.
- **Injection method.** Synthetic keystrokes vs clipboard-and-paste —
  different failure modes around focus, formatting, and clobbering
  clipboard contents.
- **Corrections.** "Scratch that", punctuation commands, capitalization.
  Easy to start, endless to finish. Scope it hard.

Explicitly *not* in scope: formatting, dictation into non-text targets,
or any always-on dictation mode. You enter it on purpose.

## Phase 4 — context awareness

Already sketched as v2 in [UI_PLAN.md](UI_PLAN.md) under "per-app
profiles". Detect the foreground window; enable or disable sets of
commands based on it.

Once Phases 1–3 land, the command surface is large enough that
context becomes a real quality lever rather than a gimmick — "next"
should mean something different in Premiere than in Spotify. Needs its
own document. Listed here so earlier phases don't design it out.

## Safety rails

A voice assistant that controls the machine has failure modes a music
remote doesn't. These are constraints, not preferences.

- **No shutdown, restart, sleep, or log off by voice.** The misfire
  cost is unbounded — unsaved work across every open app — and the
  convenience saved is a keystroke. Not worth it at any recognition
  accuracy.
- **No file operations. Ever.** No delete, move, rename, or empty
  recycle bin. Out of scope permanently, not deferred.
- **No elevation.** Nothing that needs admin rights.
- **Close requests, not kills**, for generic window commands.
- **Destructive-adjacent commands are `no_early_match`.** FastVAD must
  never act on a prefix of something like "close window".
- **Dictation must not be able to dispatch commands.** Mode isolation
  is a hard boundary.

The general rule: **gate a command on the cost of a false positive.**
Recognition will misfire. Design for when it does.

## What this plan explicitly does not include

- **A scripting or macro language.** "Custom command editor" is in
  UI_PLAN v2; keep it there.
- **Remote or network control.** REX is local-first and offline. No
  phone app, no web endpoint. This is also why the dead
  `rex_main/dashboard/` HTTP server should go.
- **Cross-platform support.** Windows-only, as the project already is.
- **An LLM in the dispatch loop.** The registry carries planner-ready
  metadata for a reason, but regex dispatch is fast, offline, and
  predictable. Not this plan.
- **Replacing the wake word with always-on command listening.** The
  gate is what makes false positives survivable.

## Open questions

1. **How do we control absolute system volume?** Volume keys give
   coarse relative steps and no "set to 40". Real Core Audio access
   means either a new dependency (`pycaw`) or hand-rolled COM via
   `ctypes` — the latter matching how the HUD already does
   click-through. Per the project's supply-chain rules a new dependency
   needs justifying. **Unresolved; blocks Phase 1 start.**
2. **Does focusing a window work reliably?** Windows restricts
   foreground changes from background processes. Needs a spike before
   Phase 2 commits to "switch to X".
3. **Do the music backends keep bare phrases at all?** Once "pause"
   means the media key, is `ytmd_stop_music` still worth its slot, or
   do the music backends shrink to what only they can do (search, like,
   shuffle, queue)?
4. **Does `clip that` suggest a wider capture family?** Screenshot,
   record last 30s — adjacent, and `steelseries` already proves the
   shape.
5. **When does the action discovery panel land?** Past ~60 actions,
   "what can I say?" becomes the thing blocking daily use. Probably
   right after Phase 1.

## Success criteria

Phase 1 is done when:

1. "volume up" changes the **system** volume, and "pause" pauses
   whatever is actually making noise — including a browser tab.
2. REX is useful in a session where no music app is running at all.
3. A system action that throws cannot take the assistant down.
4. Every recognition attempt produces visible feedback — success or
   "didn't catch that."

The whole plan is working when REX gets used on a day nobody launches
a game.

## Prerequisite: Phase 0

Phases 1+ assume a single dispatch path with one error boundary and
working no-match feedback. Today there are two paths — `FastVAD`
(the default, via `low_latency_mode: true`) reimplements match and
execute inline in `rex_main/rex.py`, while `matcher.dispatch_command`
holds the better-behaved version that most users never run.

Consequences that block this plan: `no_match` is never emitted on the
default path, so the HUD's "didn't catch that" is unreachable; the
match event carries an empty string instead of the recognized text; and
`fast_vad.py` has no error boundary at all, so a throwing handler kills
the assistant. That last one is survivable today only because every
existing handler is wrapped in `@safe_call` — which catches
network-shaped errors, not the COM and window-handle failures Phase 1
introduces.

Phase 0 consolidates the two paths and adds the first tests that touch
the audio pipeline. It is a prerequisite, not a nice-to-have: landing
15 new action types on the current default path is how "clunky" becomes
"broken". Implementation plan to follow separately.
