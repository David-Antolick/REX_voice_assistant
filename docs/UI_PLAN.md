# REX UI Plan

A vision document for moving REX off the command line into a real desktop
UI. Intentionally non-technical — the goal is to lock in *what* we're
building and *why* before any code goes in. The v1 technical plan lives
elsewhere; this doc is the north star.

## Why we're doing this

REX has outgrown the CLI. As a voice assistant, it lives in the
background — the right place for it isn't a terminal window but a system
tray icon that's always there and gets out of the way. A real UI also
unlocks the things the CLI can't do well: live feedback when a command
lands, settings the user can poke at without editing YAML, and a sense
that REX is a *product* rather than a script you launch.

The web dashboard idea is dead. REX is local-first and offline; opening
a browser tab to localhost feels wrong for an always-on background app.
Native desktop is the right shape.

## Design principles

- **Quiet by default.** REX should be invisible when nothing is
  happening. No floating windows, no taskbar entry, no focus stealing.
  Just a tray icon.
- **Confident feedback.** When REX hears something and acts, the user
  should *see* that — briefly, unobtrusively, in the corner of their
  screen. No "did it hear me?" anxiety.
- **Settings, not a control panel.** The settings window is for
  configuration the user actually changes (mic, wake-word toggle, music
  service). It is not a dashboard, a metrics view, or a profile editor.
- **Clean over fancy.** Native Windows look. No custom skinning, no
  animation flourishes, no marketing chrome. The Windows 11 system
  style is fine; we don't need to out-design Microsoft.
- **The CLI still works.** `rex --console` keeps the old
  console-loop behavior for debugging. The UI is the default; the CLI
  is the escape hatch.

## v1 — the three pieces

Every modern voice/dictation app on Windows (Wispr Flow, WillowVoice,
SuperWhisper, Dragon, Voice Access, Talon) converged on the same three
UI surfaces. v1 ships exactly those three, nothing more.

### 1. Tray icon

The primary surface. Lives in the Windows system tray. The icon glyph
reflects REX's state at a glance:

- **Idle** — REX is running but not actively listening for a command
  (e.g. waiting for the wake word).
- **Listening** — wake word fired or push-to-talk active; REX is
  capturing audio.
- **Processing** — Whisper is transcribing or an action is executing.
- **Error / paused** — something is wrong, or the user has paused REX.

Right-click menu:

- Pause / Resume listening
- Open Settings…
- Open logs folder
- About REX
- Quit

Left-click (or double-click) opens Settings. No main window — the tray
*is* the app's home.

### 2. Recognition HUD

The headline UX element. A small, frameless, always-on-top floater that
appears for ~1.5 seconds after REX recognizes a command, showing what
it heard and what it did:

> ✓ skip song

Lives near the bottom-right of the active monitor. Semi-transparent,
rounded, click-through (so it doesn't eat clicks during gaming —
which is half of why REX exists). Auto-fades. No interaction; it is
purely a confirmation surface.

When recognition *fails* (no match, low confidence), the HUD shows a
muted "didn't catch that" — same shape, different glyph. The point is
the user always knows whether REX heard them.

### 3. Settings window

A normal Windows dialog opened from the tray. Form-style: labeled
fields grouped into sections that mirror the structure of
[default_config.yaml](../rex_main/default_config.yaml) — Audio, Model,
Music Service, Wake Word, Performance, Logging.

Saving writes to `~/.rex/config.yaml` and hot-reloads what can be
hot-reloaded; the rest prompts for a restart. Closing without saving
discards changes. There is a "Reset to defaults" button per section
and a "Run setup wizard…" button that re-launches the existing
[setup_wizard.py](../rex_main/setup_wizard.py) flow for the OAuth /
Spotify pieces that don't fit a form.

That's v1. Tray + HUD + Settings.

## What v1 explicitly does *not* include

These are deliberately deferred. Each is a real feature, but none is
needed to make REX feel like a real app, and shipping them all at once
turns a passion project into a slog.

- **Command history viewer.** A scrolling log of recent recognitions
  and actions. Useful but not table-stakes.
- **Live mic level meter.** Cute, low value once the user trusts the
  HUD.
- **Per-app profiles.** "Different commands when Rocket League is
  focused." Big feature, separate design.
- **Onboarding tour.** A first-run tutorial. Worth doing eventually;
  not a v1 problem.
- **Theming / dark mode toggle.** Inherit the Windows system theme.
  Don't add a switch.
- **Full main window.** The VoiceAttack pattern (big window with
  tabs). We're not that app.
- **Auto-update UI.** Updates still happen via `pip install -U`. A
  "check for updates" button is fine eventually but not in v1.

## Future expansions (rough sketch, not commitments)

These are ideas worth keeping in mind so v1 doesn't paint us into a
corner. None of them are scheduled.

### v1.1 — quality of life

- **Command history panel.** Opened from the tray. Last N
  recognitions with timestamp, what was heard, what action ran, and
  whether it succeeded. Doubles as a debugging tool.
- **Microphone test panel** in Settings. Live waveform + VAD trigger
  indicator so the user can verify their mic before going live.
- **Push-to-talk binding UI.** Currently config-only; promote to a
  Settings field with a "press a key to bind" capture.

### v1.2 — onboarding & polish

- **First-run experience.** When REX starts and no config exists,
  open Settings on the "welcome" tab with a 3-step walkthrough
  (pick mic, pick music service, test wake word).
- **Action discovery panel.** A read-only list of every voice command
  REX understands, generated from the action registry. Useful as a
  cheat sheet; eliminates the "what can I say?" question.
- **In-HUD suggestions.** When recognition fails, show the closest
  matching command(s) ("did you mean: skip song?").

### v2 — power-user features

- **Per-app profiles.** Detect foreground window; load a profile that
  enables/disables sets of commands. Big design surface — needs its
  own doc.
- **Custom command editor.** GUI for users to add their own phrase →
  action mappings (likely keystroke macros or shell commands) without
  writing Python.
- **Statistics.** "You said 'skip song' 47 times this week." Local
  only, opt-in. Charm feature, not core.

### Speculative / probably-never

- **Mobile companion app.** No.
- **Cloud sync of settings.** Violates the offline promise.
- **Plugin marketplace.** Way out of scope for a passion project.

## Open questions

- **Tray-only vs. taskbar option.** Some users want a taskbar entry
  for muscle memory. Decide after living with tray-only for a while.
- **What happens on update?** When `rex` is upgraded via pip while
  the tray app is running, do we prompt to restart, or auto-restart,
  or ignore? Punt to v1.1.
- **Headless / server mode.** If someone runs REX on a Windows
  machine without a logged-in session (rare but possible), the tray
  app is meaningless. `rex --console` covers this; document it.

## Success criteria for v1

We'll know v1 is good when:

1. After installing REX, the user double-clicks the Start Menu entry,
   sees the tray icon appear, says "hey REX, skip song," sees the
   HUD confirm it, and never opens a terminal.
2. Changing a setting (e.g. switching from YTMD to Spotify) is
   doable without reading docs.
3. The tray + HUD + settings window together add less than 100 MB to
   the install footprint.
4. Existing CLI users can still run `rex --console` and get the
   exact behavior they have today.

If we hit those four, v1 is done and we can think about v1.1.
