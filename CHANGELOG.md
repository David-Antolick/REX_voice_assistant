# REX Voice Assistant - Changelog

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
- New full walkthrough: [TRAINING_HEY_REX.md](TRAINING_HEY_REX.md) — covers recording, environment setup, synthetic-positive generation, mixing in user recordings, training on a single GPU (~1 hr on a 3070 Ti), validation, and threshold tuning. Targets the "Option B" approach (synthetic + your voice) for best per-user accuracy.
- **Multi-speaker contribution flow** for training a single model across multiple voices:
  - `rex record-wake-samples --contributor <name>` (or interactive prompt) namespaces recordings under `~/.rex/wake_training/recordings/<name>/` so multiple people's samples can be merged without filename clashes.
  - New `rex package-wake-samples` command zips a contributor's WAVs plus a `manifest.json` (sample count, peak/RMS distribution, microphone, OS) for submission.
  - New non-coder-friendly contribution guide [CONTRIBUTING_VOICE_SAMPLES.md](CONTRIBUTING_VOICE_SAMPLES.md) — walks a friend through installing REX via `pipx`, recording 100 samples, and producing a labeled `.zip` to send back, in plain English with no code knowledge assumed.
  - [TRAINING_HEY_REX.md](TRAINING_HEY_REX.md) Phase 4 expanded with PowerShell merge scripts and per-contributor manifest spot-check guidance.
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
