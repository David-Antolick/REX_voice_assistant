# REX Voice Assistant - Changelog

## [1.2.0] - 2026-04-30

REX gets a real desktop UI. The default `rex` command now opens a system-tray app — the CLI is no longer the only surface. Settings live in a window, recognized commands flash a transient HUD, and Rex can launch and close YouTube Music / Spotify by voice. The console mode is preserved as `rex --console` for debugging and headless runs.

### New Features

#### Desktop UI (system tray + recognition HUD + settings dialog)
- New `rex_main/ui/` package built on **PySide6**. Three surfaces: a state-driven system-tray icon, a transient frameless HUD that flashes the recognized command (✓ skip song / "didn't catch that"), and a tabbed settings window that mirrors `default_config.yaml`.
- The HUD is click-through on Windows (sets `WS_EX_TRANSPARENT` via ctypes after first show) so it can't eat clicks during gameplay.
- Tray icon glyph encodes runtime state — idle / listening / thinking / paused / error — with a small badge over your `rex_icon.png` artwork. Tooltip uses the assistant's name ("Rex — listening", not "REX — listening").
- Right-click tray menu: **Pause / Resume Listening**, **Settings…**, **Restart Rex**, **Open logs folder**, **About**, **Quit**.
- "Restart Rex" (and the auto-prompt that fires when settings save a restart-required field) tear down the assistant runtime thread and spawn a fresh one without ever taking the tray icon down. No more quit-and-relaunch cycle to apply config changes.
- Settings dialog ships **Apply gaming preset** and **Apply default preset** buttons that mirror the `--gaming` CLI flag in form-field form. Music-service changes (`services.active`) apply live via `configure_from_config`; everything else prompts for a restart.

#### Windowed launcher (`rex-gui`)
- New `[project.gui-scripts]` entry point produces `rex-gui.exe` linked against `pythonw.exe`. Pin it to Start menu / Taskbar / Startup to launch Rex into the tray with **no terminal window**. The console-style `rex.exe` still exists for terminal use.

#### `--console` flag
- `rex` now defaults to launching the tray app. Add `--console` to get the previous in-terminal behavior (useful for debugging, headless servers, or scripts piping to logs).
- Falls through to console mode automatically if PySide6 fails to import or the OS reports no system-tray support.

#### App launch / close voice commands
- New `apps` action backend ([rex_main/actions/apps.py](rex_main/actions/apps.py)) with four commands: open / close YouTube Music, open / close Spotify.
- Three-tier launcher resolver: hardcoded paths → recursive Start menu shortcut search (resolved via PowerShell + `WScript.Shell`) → Windows' authoritative installed-apps catalog (`Get-StartApps` + `shell:AppsFolder\\<AppID>`). Result is cached per app for the session, so discovery is paid at most once. Catches Microsoft Store apps, Squirrel/Electron installers, and standard installs without configuration.
- Close uses `taskkill /IM <name> /F /T` against a candidate list of image names — no extra runtime dependency.

### Runtime hooks
- `run_assistant(opts, config)` and `dispatch_command(text_q, listening_state)` gained two optional parameters: `ui_callback` (event sink for state / match / no_match) and `paused` (a `threading.Event`-like). Both no-op when absent, so console-mode behavior is byte-identical to 1.1.x. The tray runs the assistant on a worker `QThread` whose asyncio loop owns these callbacks; signals marshal back to the Qt main thread via auto-queued connections.

### New runtime dependency
- Added **`PySide6>=6.7,<7`** to base dependencies. Adds ~60 MB to the install. Acceptable next to torch + faster-whisper. LGPL.

### Removed (carry-over from 1.1.0 → 1.2.0 unreleased work)

#### Discord voice-control integration
- The Discord backend (`rex_main/actions/discord.py`) and all five voice commands shipped in 1.1.0 (`mute`, `unmute`, `deafen`, `undeafen`, `leave channel`, plus the never-released `show discord` / `minimize discord`) are removed.
- Reason: the user's group is migrating to a self-hosted Spacebar instance via the Fermi web client. Spacebar is open-source and Discord-API-compatible, scopes aren't whitelist-gated, and the web-app delivery model bypasses the Chromium accessibility-tree teardown that made the Discord integration unreliable when minimized. A future Spacebar backend will be designed in a separate workstream. See [docs/DECISIONS.md](docs/DECISIONS.md) entry "Removed Discord integration; future voice chat will target Spacebar/Fermi" for the full rationale.
- `pywinauto>=0.6.8,<1` removed from base dependencies — it was added solely for the Discord UIA backend and has no other consumer in REX.
- The published 1.1.0 wheel on PyPI is unchanged; users who want the Discord integration can pin `rex-voice-assistant==1.1.0`.

### Documentation
- New [docs/UI_PLAN.md](docs/UI_PLAN.md) — vision document for the desktop UI: v1 scope, deferred features (command history, mic test, push-to-talk capture), v1.x and v2 sketches.
- [docs/DECISIONS.md](docs/DECISIONS.md): new entry "Desktop UI v1: PySide6 tray + HUD + settings, in-process with asyncio in a QThread" recording the toolkit choice and integration model. The two prior Discord-related entries are **kept** — the negative-result trail still applies to any future Electron-app integration.
- [docs/LESSONS.md](docs/LESSONS.md) "Chromium tears down its UIA tree when its window isn't foreground" entry is also **kept** for the same reason.
- [docs/ACTIONS.md](docs/ACTIONS.md): new `apps` backend section in the inventory.

### Tests
- New `test_ui_bridge.py` covers the runtime → Qt-signal adapter using `QSignalSpy`. Skips cleanly on environments without PySide6 so the existing CI matrix doesn't have to install Qt.
- The action-registry test gate (`test_actions.py`) still passes 174 assertions including the four new app-launch actions and the additive `dispatch_command` signature change.

## [Unreleased]

## [1.1.0] - 2026-04-28

Discord voice control. REX can now mute, unmute, deafen, undeafen, and disconnect from a Discord voice channel via voice command — without keystrokes, without Discord's whitelisted RPC scopes, and without disrupting other apps. The integration drives Discord's UI through the OS accessibility surface.

### New Features

#### Discord voice commands
- New phrases: `"mute"`, `"unmute"`, `"deafen"`, `"undeafen"`, `"leave channel"`. Same shape as the existing music / clipping commands — wake-word gated, registered in the action registry, no config required.
- `mute_toggle` and `deafen_toggle` map either of their two phrases (mute/unmute, deafen/undeafen) to the same toggle handler — Discord's Mute and Unmute buttons are the same DOM node renamed when state flips, so a held COM pointer survives the rename.
- Patterns are tolerant of common tiny.en mishearings observed in the field: `"on mute"`, `"an mute"`, `"daffin"`, `"deafin"`, `"deaf"`, `"on deafen"`, `"on deafened"` all match. Adds zero false-positive risk in practice because the wake-word gate scopes everything to a 6-second window.

#### How it works ([rex_main/actions/discord.py](rex_main/actions/discord.py))
- Drives Discord's bottom voice-panel buttons via Windows UI Automation (`pywinauto`). One UIA `FindFirst` per button, ever — the resulting COM pointer is held forever and `.invoke()` is a direct method call on it. Steady-state per-command cost: ~10-30 ms.
- **Cache is pre-warmed at REX startup** (mute + deafen buttons resolved) so the first voice command is fast. Cold-call cost (~1.5–4 s) happens during startup where it's invisible. Disconnect resolves lazily on first use since the button only exists when in a voice channel.
- **Sibling-of-Deafen anchor** disambiguates the duplicate Mute / Disconnect buttons that Discord exposes in the channel-header banner when in voice. Deafen is globally unique in Discord's accessibility tree (the header banner has no Deafen button), so we resolve Deafen directly and find Mute / Disconnect as its immediate siblings — scoping the lookup to the bottom voice panel container without enumerating the whole tree.
- Held wrappers are independent of window position, size, monitor, or DPI — they identify UIA elements, not screen coordinates. Move Discord anywhere; the commands keep working.

#### New runtime dependency
- Added `pywinauto>=0.6.8,<1` to base dependencies. Pulls `comtypes` and `pywin32` transitively. ~500 ms one-time REX startup cost; negligible per-call. `pip-audit` clean for the new packages.

#### Action registry
- New `discord` backend in [rex_main/actions/discord.py](rex_main/actions/discord.py) following the recipe in [docs/ACTIONS.md](docs/ACTIONS.md).
- Three actions: `discord_mute_toggle`, `discord_deafen_toggle`, `discord_disconnect`. Currently registered with `slot=None` (always-on, like SteelSeries) since Discord is the only `voice_chat` implementer. The slot can be reintroduced when a second backend (TeamSpeak, Mumble) competes for it.

### Documentation
- [docs/DECISIONS.md](docs/DECISIONS.md): new entry "Discord voice control via UIA (not RPC, not keystrokes)" — records the three blocked alternatives (Discord RPC `SET_VOICE_SETTINGS` whitelist, global keystrokes, `PostMessage(WM_KEYDOWN)` to Chromium's window) and why each was rejected.
- [docs/ACTIONS.md](docs/ACTIONS.md): inventory updated with the discord section; voice_chat slot still listed as `*(future)*` with an explanatory note.
- [README.md](README.md): voice commands table extended with the three Discord rows.
- [_local/discord_uia_dump.py](_local/discord_uia_dump.py): kept around as the canonical UIA spike — re-run if Discord ever renames its accessibility labels.

### Known Limitations
- **English Discord client only.** Button-name lookup is exact-match on `"Mute"` / `"Deafen"` / `"Disconnect"`; localized clients will silently no-op until those constants are updated.
- **Doesn't work when Discord is minimized.** Chromium tears down the accessibility tree on minimize, so UIA can't see the window. Restoring Discord (even just clicking its taskbar icon) recovers; planned for a future release with an auto-restore path that doesn't steal focus.
- **Disconnect requires being in a voice channel** (button only exists when active). Logs a warning and no-ops otherwise.
- **No content-generating actions** (sending messages, joining channels). Scope is intentionally narrow to keep the integration defensible against Discord's self-bot policy.

## [1.0.1] - 2026-04-26

PyPI re-cut. The 1.0.0 wheel partially uploaded then PyPI registered the filename, blocking the re-publish; 1.0.1 is the first usable PyPI release containing the full 1.0 feature set described below.

## [1.0.0] - 2026-04-26

REX hits 1.0. Originally built as a music-control voice assistant, it now ships a custom-trained wake word ("hey rex") auto-downloaded on first run, a guided pipeline for collecting voice samples from contributors, full dashboard, low-latency early-match transcription, and a gaming preset that frees the GPU. The core voice-assistant loop is feature-complete for the project's original scope.

### New Features

#### Custom "Hey Rex" wake word is the default
- A custom-trained `hey_rex` model is now the default wake word, **automatically downloaded** from [GetToasted/rex-wake-words](https://huggingface.co/GetToasted/rex-wake-words) on first run (~200 KB, cached at `~/.rex/wake_models/hey_rex.onnx`).
- Trained on 2000 Piper TTS synthetic positives + 329 real recordings from 3 contributors (david, big_d, zach), with the openWakeWord automatic training pipeline. All contributors gave explicit consent for distribution including commercial use; model is MIT-licensed.
- New `KNOWN_HF_MODELS` registry in [wake_word.py](rex_main/wake_word.py) — config values that match a registered alias auto-resolve via `huggingface_hub.hf_hub_download()`. File paths and openWakeWord prebuilt names (`hey_jarvis` etc.) still work as before.

### Behavior Changes

#### Wake-word gating is on by default
- `wake_word.enabled` defaults to `true` in the package config. New users get the wake-word experience out of the box.
- To disable: pass `--no-wake-word` on the CLI, or set `wake_word.enabled: false` in `~/.rex/config.yaml`.
- Existing users with a `~/.rex/config.yaml` that was set up before this version are unchanged — their explicit `enabled: false` (or absence of the key) still wins via deep-merge.

#### `openwakeword` moved into base dependencies
- Previously a `[wake_word]` optional extra; now bundled in the core install. Adds ~10 MB of transitive deps (mostly already present via faster-whisper). The `[wake_word]` extra still exists as a no-op alias for backward compatibility — `pip install rex-voice-assistant[wake_word]` continues to work.

#### `--gaming` now forces hey_rex
- The gaming preset (`rex --gaming`) explicitly forces `wake_model = "hey_rex"` regardless of what the user's `~/.rex/config.yaml` says. Ensures gaming-mode users always get the auto-downloaded REX-trained wake word, not a stale `hey_jarvis` carried over from an older config.
- New `--wake-model` CLI flag exposes the model override generally — works alongside `--wake-word` to point at any model identifier (REX alias, openWakeWord prebuilt, or local file path).

#### Setup wizard step simplified
- Single prompt to enable/disable, then a numbered model picker. Default selection is the auto-downloading `hey_rex`; `hey_jarvis` is option 2; any custom `.onnx` files in `~/.rex/wake_models/` round out the list.
- No more separate "install openwakeword?" or "download wake-word models?" prompts since both are bundled now.

## [0.3.1] - 2026-04-25

PyPI re-cut. The 0.3.0 version slot was registered before all of the planned 0.3.0 work landed in the wheel, so 0.3.1 is the first release on PyPI that contains the full feature set listed under 0.3.0 below.

## [0.3.0] - 2026-04-25

### New Features

#### Wake Word ("Hey Jarvis")
- **Optional wake-word gate** via `openWakeWord` (prebuilt `hey_jarvis` model). When enabled, REX only acts on commands inside a 6-second listening window after detecting the wake phrase.
- **Audio cue** (short two-tone "ding") plays on wake-word fire so you know REX is listening.
- **Listening window auto-extends** on each successful command, so multi-turn flows ("hey jarvis" → "play music" → "volume up") work without re-waking.
- **Suppressed-command logging**: commands matched outside the listening window are explicitly logged as `Suppressed early/final match '...' (wake word not active)` instead of misleading "Early match!" lines.
- **Optional install** to keep the base footprint small: `pip install rex-voice-assistant[wake_word]`. Models auto-download (~30MB) on first run.
- **CLI flag**: `--wake-word` / `--no-wake-word` (overrides config).
- **Config knobs** (`~/.rex/config.yaml` → `wake_word:`): `enabled`, `model`, `threshold`, `listening_window_seconds`, `debounce_seconds`, `cue_enabled`.
- **Custom "hey rex" model** training is on the roadmap for a future release.

#### Custom wake-word support
- New CLI command **`rex record-wake-samples`** — guided recorder that captures ~100 clean 16 kHz mono WAVs (with peak/clipping validation, retry-on-bad, and a session notes file) ready to feed straight into the openWakeWord training pipeline.
- **Custom `.onnx` models load by file path**: set `wake_word.model: ~/.rex/wake_models/hey_rex.onnx` in your config and REX will use it instead of the prebuilt `hey_jarvis`. Underlying `Model()` already supports paths; we just expand `~` and log a differentiated startup message.
- **Setup wizard auto-discovery**: `rex setup` scans `~/.rex/wake_models/` and presents any custom `.onnx` files alongside `hey_jarvis`, defaulting to the most recently modified one.
- New full walkthrough: [TRAINING_HEY_REX.md](docs/specifics/TRAINING_HEY_REX.md) — covers recording, environment setup, synthetic-positive generation, mixing in user recordings, training on a single GPU (~1 hr on a 3070 Ti), validation, and threshold tuning. Targets the "Option B" approach (synthetic + your voice) for best per-user accuracy.
- **Multi-speaker contribution flow** for training a single model across multiple voices:
  - `rex record-wake-samples --contributor <name>` (or interactive prompt) namespaces recordings under `~/.rex/wake_training/recordings/<name>/` so multiple people's samples can be merged without filename clashes.
  - New `rex package-wake-samples` command zips a contributor's WAVs plus a `manifest.json` (sample count, peak/RMS distribution, microphone, OS) for submission.
  - New non-coder-friendly contribution guide [CONTRIBUTING_VOICE_SAMPLES.md](docs/specifics/CONTRIBUTING_VOICE_SAMPLES.md) — walks a friend through installing REX via `pipx`, recording 100 samples, and producing a labeled `.zip` to send back, in plain English with no code knowledge assumed.
  - [TRAINING_HEY_REX.md](docs/specifics/TRAINING_HEY_REX.md) Phase 4 expanded with PowerShell merge scripts and per-contributor manifest spot-check guidance.
- **Quality-control commands for recordings**:
  - `rex review-wake-samples` — interactive playback with keep / reject / replay / quit. Rejected files move to `_rejected/` (recoverable, not deleted).
  - `rex retrim-wake-samples` — re-trims existing recordings with the asymmetric trim policy (lower 0.005 threshold, 150 ms lead pad, 400 ms tail pad). Originals back up to `_untrimmed/` so the operation is reversible. Includes a `--dry-run` mode that reports duration distribution and flags suspect clips ("barely changed" likely means cut-off; "<0.6 s" likely means fast utterance or fragment).
  - **Trim-policy fix on the recorder itself**: the previous symmetric 100 ms / 0.01-threshold trim was clipping the trailing 'x' sibilant in "hey rex" because its decay falls below 0.01. New recordings now use the same asymmetric trim as the retrim command.

#### Gaming preset (`--gaming`)
- One-flag shortcut for low-overhead operation: `tiny.en` model + CPU device + wake word + low-latency. Frees up GPU/VRAM for games while keeping REX responsive (tiny.en transcribes in ~80ms on CPU, well under the early-match deadline). Individual flags (`--model`, `--device`, etc.) still take precedence when passed alongside `--gaming`.

#### Audio Pipeline Fan-Out
- `AudioStream` now accepts optional `tap_queues` so multiple consumers (VAD + wake-word, future TTS interrupt detector, etc.) can each receive the same audio frames with independent backpressure.

### Behavior Changes

#### Dashboard ships in the base install
- `fastapi`, `uvicorn`, and `websockets` moved from the `[dashboard]` optional extra into the core `dependencies` list. `rex --dashboard` and `rex dashboard` now work out of the box without any `pip install rex-voice-assistant[dashboard]` step. The misleading `Dashboard dependencies not installed` error path has been removed since it's unreachable.

#### Low-latency mode is now the default
- `--low-latency` was opt-in; now the default. Use `--standard` (or `low_latency_mode: false` in config) to opt back into the original 400ms-VAD pipeline.

#### Volume commands no longer use early-match
- `volume_up`, `volume_down`, and `set_volume` now wait for the full utterance before firing, so phrases like "volume 50" don't prematurely trigger on "volume" + a partial number. Joins existing entries (`search_song`, `queue_track`) in `NO_EARLY_MATCH_COMMANDS`.

#### Match-rate metric is wake-aware
- When wake-word gating is active, "Match Rate" is computed as `commands / wake_words` (did each wake produce a successful command?) instead of `matched / total_transcriptions`. The metrics summary line now also shows `Wake:` and `Suppressed:` counters.

### Bug Fixes

#### Click flag defaults shadowed user config
- `--model`, `--device`, and `--beam` had hard-coded Click defaults (`small.en`, `auto`, `1`) that always evaluated truthy, so `ctx.obj.get(key) or config.get(...)` never reached the config branch. User-config values were silently ignored — most visible as `device: cuda` being set but the assistant still running on CPU. Defaults are now `None`, so the config wins unless the flag is explicitly passed.

### Internal / Tooling

- New module `rex_main/wake_word.py` with `ListeningState`, `WakeWordDetector`, and `play_wake_cue()` helper.
- New metric event types `WAKE_WORD_DETECTED` and `COMMAND_SUPPRESSED`, plus `record_wake_word()` / `record_command_suppressed()` methods on the singleton collector.
- `FastVAD` accepts an optional `gate_func` so it can suppress + log accurately without relying on the executor as the only enforcement point.
- New optional dependency extra: `[wake_word]` → `openwakeword>=0.6,<1`.

### Roadmap

`ASSISTANT_ROADMAP.md` added — captures the longer-term plan to evolve REX from a music controller into a personal intelligence-layer dispatcher (regex fast-path + small local LM for unknown intents → built-in actions, system actions, or delegated agents like Claude Code).

---

## [0.2.2] - 2026-04-25

### Bug Fixes

#### Spotify: popup on every command
- Removed `show_dialog=True` from `SpotifyOAuth`, which was forcing the consent UI even when a valid token was already cached.
- Added an explicit `cache_path` of `~/.rex/spotify_token.cache` so the OAuth token persists in a stable location regardless of the working directory `rex` is launched from.
- Same fix applied to the setup wizard so the token cached during `rex setup` is found on subsequent runs.

### UX Improvements

#### Spotify setup wizard
- Updated to match the current Spotify Developer portal flow: sign in at developer.spotify.com → click username (top right) → Dashboard → Create app.

### Security & Hygiene
- **Dependency pins**: every direct dep in `pyproject.toml` now has a major-version upper bound and a CVE-aware floor (e.g. `requests>=2.32.2,<3`, `aiohttp>=3.10.11,<4`, `pyyaml>=6.0.1,<7`).
- **`requirements.txt` simplified** to `-e .` plus install instructions; `pyproject.toml` is the single source of truth.
- **`pip-audit` added** to the `[dev]` extra and as a CI job — every push runs an audit against the resolved dep tree.
- **Dependabot configured** for weekly grouped pip + GitHub Actions updates.
- **`.venv*/` ignored** in `.gitignore`.

---

## [0.2.0] - 2025-12-30

### New Features

#### Metrics Dashboard
- **Real-time metrics dashboard** at `http://localhost:8080` (enable with `rex --dashboard`)
- Tracks command match rates, per-stage latencies, command frequency
- WebSocket-powered live updates every second
- Charts for latency breakdown (VAD → Whisper → Execute)
- Recent activity table with timing information
- Standalone mode: `rex dashboard`

#### Latency Optimization
- **Reduced VAD silence timeout** from 750ms to 400ms (350ms faster response)
- **Low-latency mode** (`rex --low-latency`): 250ms timeout for gaming scenarios
- **Whisper model pre-warming**: Eliminates ~500ms cold-start on first command
- End-to-end latency now ~500-800ms (down from ~1500-2000ms)

#### New CLI Options
- `--dashboard` - Enable metrics dashboard
- `--dashboard-port` - Custom port for dashboard (default: 8080)
- `--low-latency` - Enable aggressive latency optimization

### New Files
- `rex_main/metrics.py` - Thread-safe metrics collection
- `rex_main/dashboard/__init__.py` - Dashboard package
- `rex_main/dashboard/server.py` - FastAPI backend with WebSocket
- `rex_main/dashboard/static/index.html` - Dashboard UI
- `rex_main/dashboard/static/dashboard.js` - Real-time chart updates
- `rex_main/dashboard/static/dashboard.css` - Modern dark theme styling

### Dependencies
New optional dependencies added to `pyproject.toml`:
- `[dashboard]`: fastapi, uvicorn, websockets
- `[integrations]`: pypresence, obsws-python, aiohttp (for future Discord/OBS support)
- `[streamer]`: All of the above combined

Install dashboard: `pip install rex-voice-assistant[dashboard]`

---

## [0.1.0] - 2025-12-30

### Major Changes - Session Summary

This was a comprehensive modernization and bug-fix session that transformed REX from a dev container script into a production-ready Python package.

### ✨ New Features

#### Setup Wizard Enhancements
- **Automatic CUDA detection and installation**: Wizard now detects NVIDIA GPUs and offers to install CUDA-enabled PyTorch automatically
- **Detailed service setup instructions**:
  - YTMD: Step-by-step guide with download link, required settings (Companion Server, Companion Authorization)
  - Spotify: Complete developer portal walkthrough with redirect URI setup
- **Smart model recommendation**: Defaults to `medium` model for GPU users, `small.en` for CPU
- **Removed confusing prompts**: YTMD host/port no longer prompted (uses localhost:9863 by default)

#### CUDA Auto-Detection
- `--device auto` now actually auto-detects GPU availability (was hardcoded to CPU)
- Added `_detect_device()` method in WhisperWorker using `torch.cuda.is_available()`
- Logs clearly indicate device selection: "CUDA detected, using GPU acceleration" or "CUDA not available, using CPU"

### 🐛 Bug Fixes

#### Critical CUDA/Windows DLL Loading Fix
**Problem**: PyTorch installed without CUDA support, cuDNN DLLs not in PATH, causing:
```
Could not locate cudnn_ops64_9.dll
```

**Solution** (3-part fix):
1. **DLL Path Setup in cli.py**: Added Windows-specific code to scan `nvidia.*` namespace packages and add DLL directories to PATH before any imports
2. **Lazy WhisperModel Import**: Moved `from faster_whisper import WhisperModel` inside `_lazy_init()` to ensure DLL paths are set first
3. **Setup Wizard CUDA Installation**: Automated PyTorch+CUDA installation with progress feedback

**Files Changed**:
- [cli.py](rex_main/cli.py:15-30) - Module-level CUDA DLL path setup
- [whisper_worker.py](rex_main/whisper_worker.py:123-136) - Lazy import + device detection
- [setup_wizard.py](rex_main/setup_wizard.py:189-260) - `_offer_cuda_setup()` function

#### YTMD Authentication Flow
**Problem**: Confusing instructions about when the authorization popup appears

**Fix**:
- Clarified messaging: "Press Enter to show the authorization popup in YTMD"
- Increased timeout from 10s to 60s to give users time to click Allow
- Added troubleshooting tips if popup doesn't appear
- Removed unnecessary host/port prompts

**File Changed**: [setup_wizard.py](rex_main/setup_wizard.py:294-323)

#### QueueFull Error Spam
**Problem**: During CPU transcription, audio queue fills up and logs spammed with:
```
Exception in callback AudioStream._audio_callback.<locals>.<lambda>()
asyncio.queues.QueueFull
```

**Fix**: Modified callback to catch `QueueFull` inside the lambda and silently drop frames
- Changed from `try/except` around `call_soon_threadsafe()` to exception handling inside callback
- Added helper function `_enqueue()` that catches exception before it bubbles up

**File Changed**: [audio_stream.py](rex_main/audio_stream.py:88-94)

#### YTMD API Compatibility
**Problem**: YTMD `/api/v1/auth/requestcode` returning 400 Bad Request

**Fix**: Added missing `appVersion` field to auth request payload:
```python
json={
    "appId": "rex_voice_assistant",
    "appName": "REX Voice Assistant",
    "appVersion": "1.0.0"  # Was missing
}
```

**File Changed**: [setup_wizard.py](rex_main/setup_wizard.py:272-278)

### 📚 Documentation

#### New Files
- **DEVELOPMENT.md**: Comprehensive technical documentation including:
  - Architecture overview with diagrams
  - Component details for each module
  - Complete CUDA setup explanation
  - Known issues and workarounds
  - TODOs and future work
  - Testing and debugging guide

#### Updated Files
- **README.md**: Updated CUDA prerequisites and troubleshooting sections

### 🔧 Technical Improvements

#### Package Installation
- Fixed PyTorch dependency to properly detect when CUDA installation is needed
- Setup wizard now handles CUDA installation automatically via subprocess calls
- Added verification step after CUDA install

#### CLI Improvements
- Updated help text: `--device auto` → "auto=detect GPU, fallback to CPU"
- Better error messages for CUDA-related issues

#### Code Quality
- Improved type hints in `_check_system()` return type: `Optional[bool]` with clear meaning:
  - `True`: All OK including CUDA
  - `False`: GPU found but CUDA not working
  - `None`: Required components missing
- Better separation of concerns in setup wizard (system check vs CUDA setup)

### 🔄 Migration Notes

If upgrading from a version without CUDA auto-detection:

```bash
# Reinstall to get CUDA support
pipx install rex-voice-assistant --force

# Run setup wizard to install CUDA PyTorch
rex setup
```

Or manually install CUDA PyTorch:
```bash
pipx runpip rex-voice-assistant install torch torchaudio \
  --index-url https://download.pytorch.org/whl/cu124 --force-reinstall
```

### 📊 Performance Impact

- **CUDA mode**: 5-10x faster transcription on NVIDIA GPUs
- **Auto-detection**: Zero user configuration needed - just run `rex` and it works
- **Setup time**: Fresh install now ~5 minutes including CUDA PyTorch download

### 🙏 Key Problem-Solving Moments

1. Discovering PyTorch was CPU-only by checking `torch.cuda.is_available()` in pipx venv
2. Realizing DLL paths must be set BEFORE importing CTranslate2 (lazy import pattern)
3. Understanding YTMD API requires `appVersion` field (found via API error messages)
4. Fixing QueueFull by moving exception handling inside the callback lambda

---

## Version History

### [0.0.1] - Pre-packaging
- Initial development in dev container
- PulseAudio/FFmpeg dependencies
- .env file configuration
- Single-file script architecture

### [0.1.0] - 2025-12-30
- Modern Python packaging with pyproject.toml
- Click CLI framework
- Setup wizard
- Keyring secret management
- CUDA auto-detection and installation
- Windows DLL path fixes
- Comprehensive documentation

---

**Versioning**: This project follows [Semantic Versioning](https://semver.org/).
