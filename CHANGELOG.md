# REX Voice Assistant - Changelog

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
