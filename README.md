## REX - Offline Voice-Controlled Music Assistant

[![PyPI version](https://img.shields.io/pypi/v/rex-voice-assistant.svg)](https://pypi.org/project/rex-voice-assistant/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

REX is a lightweight, streaming voice assistant that runs **100% locally** - no cloud APIs, no subscriptions. Control your music with your voice and capture gaming moments hands-free.

**Features:**
- **Music Control** - Play, pause, skip, search, and volume for YouTube Music Desktop or Spotify
- **Screen Clipping** - Voice-activated clip saving with SteelSeries GG Moments ("clip that!")
- **Fast & Private** - Whisper speech recognition runs locally on CPU or GPU
- **Low Latency** - Early command detection means near-instant response

---

### Quick Start

```powershell
# 1. Install REX (choose one)
pipx install rex-voice-assistant    # Recommended: isolated environment
pip install rex-voice-assistant     # Or use pip directly

# 2. Run the setup wizard
rex setup

# 3. Start REX
rex
```

The setup wizard will guide you through configuring your music service and GPU acceleration.

---

### Tech Stack

| Stage               | Tech                                      | What it does                                   |
| ------------------- | ----------------------------------------- | ---------------------------------------------- |
| Audio capture       | `sounddevice` (PortAudio)                  | Streams 16 kHz mono PCM from the default mic   |
| Voice activity      | Silero VAD (PyTorch, TorchScript)          | Groups frames into utterances                  |
| Transcription       | Faster-Whisper (CTranslate2 backend)       | Speech to text on CPU or CUDA                   |
| Command routing     | Regex matcher (`rex_main/matcher.py`)      | Maps recognized text to handlers               |
| Media control       | YTMusic Desktop Companion API / Spotipy    | Sends actions to YTMD or Spotify               |
| Config & secrets    | `~/.rex/config.yaml` + keyring             | Configuration and secure secret storage        |

---

### CLI Commands

```bash
rex              # Start the voice assistant
rex setup        # Interactive setup wizard
rex settings     # Change model, services, and integrations
rex status       # Show configuration and service connectivity
rex test ytmd    # Test YouTube Music Desktop connection
rex test spotify # Test Spotify connection
rex dashboard    # Run metrics dashboard standalone
rex migrate --from-env  # Import settings from .env file
```

**Options for `rex` command:**
```
--model         Whisper model (tiny|base|small|medium|large, default: small.en)
--device        Force device (cuda|cpu, default: auto)
--beam          Beam size for decoding (default: 1)
--log-file      Path to log file
--debug         Enable verbose logging
--dashboard     Enable metrics dashboard at http://localhost:8080
--low-latency   Low-latency mode (default, 250ms VAD timeout)
--standard      Standard mode (400ms VAD timeout, more forgiving for slower speech)
--wake-word     Require wake word ("hey jarvis") before commands fire
--no-wake-word  Disable the wake-word gate (default)
--gaming        Preset: tiny.en + CPU + wake word + low latency (frees the GPU)
```

---

### Prerequisites

#### Windows 10/11

1. **Python 3.10+** (tested with 3.12)
   ```powershell
   winget install Python.Python.3.12
   ```

2. **A microphone** - Any USB or built-in microphone will work

3. **Optional: NVIDIA GPU** for 5-10x faster transcription
   - Recent NVIDIA driver (no manual CUDA installation needed)
   - The setup wizard will offer to install CUDA PyTorch automatically

---

### Media Service Setup

#### YouTube Music Desktop (YTMD)

1. Install YTMD: https://ytmdesktop.app
2. In YTMD Settings, enable:
   - "Companion server"
   - "Allow browser communication"
   - "Enable companion authorization"
3. Run `rex setup` and follow the prompts to authenticate

#### Spotify

1. Create an app at https://developer.spotify.com/dashboard
2. Set Redirect URI to `http://127.0.0.1:8888/callback`
3. Run `rex setup` and enter your Client ID and Secret

---

### Voice Commands

| Phrase (examples)                 | Action                      |
| --------------------------------- | --------------------------- |
| "play music", "stop music"        | Play/pause                  |
| "next", "last/previous", "restart"| Track navigation            |
| "volume up/down", "volume N"      | Volume control              |
| "search <song> by <artist>"       | Play first search hit       |
| "switch to spotify"               | Switch backend to Spotify   |
| "switch to youtube music"         | Switch backend to YTMD      |
| "like", "dislike"                 | Thumbs up/down current track|
| "clip that", "save clip"          | Save clip (SteelSeries GG)  |

Add custom commands by editing `rex_main/matcher.py` and `rex_main/commands.py`.

#### SteelSeries Moments (Screen Clipping)

REX integrates with [SteelSeries GG Moments](https://steelseries.com/gg) for hands-free clip saving during gameplay. Just say "clip that" and REX triggers a clip save via the GameSense SDK.

**Setup:**
1. Install SteelSeries GG and enable Moments screen recording
2. Run `rex setup` - it will auto-detect and register REX with GameSense
3. Enable REX in SteelSeries GG:
   - Open GG → Settings (gear icon, bottom left)
   - Find "Moments" section → "Auto-clip" tab
   - Enable "Auto-clipping" at the top
   - Scroll down and check "REX Voice Assistant"

**Voice triggers:** "clip that", "capture that", "record that", "save clip"

---

### Wake Word ("Hey Jarvis") — Optional

REX can be gated behind a wake word so it only acts on commands within a short window after hearing "hey jarvis". When the gate is off (default), every recognized command fires immediately.

**Setup:**

```powershell
# Install the optional wake-word dependency
pip install rex-voice-assistant[wake_word]

# Enable in config (~/.rex/config.yaml) or via CLI flag
rex --wake-word
```

**Config knobs** (`~/.rex/config.yaml`):

```yaml
wake_word:
  enabled: true                  # Master switch
  model: "hey_jarvis"            # Prebuilt openWakeWord model
  threshold: 0.5                 # 0.0-1.0; raise to reduce false fires
  listening_window_seconds: 6    # Commands accepted for N seconds after wake
  debounce_seconds: 1.0          # Min gap between consecutive fires
  cue_enabled: true              # Play a short tone on wake
```

The listening window auto-extends each time a command fires, so multi-step interactions ("hey jarvis" → "play music" → "volume up") work without re-waking.

This is currently a proof of concept using the prebuilt `hey_jarvis` model. Custom "hey rex" training is on the roadmap.

---

### Gaming Mode

For gaming, REX has a one-flag preset that frees up the GPU and minimizes overhead while keeping you responsive:

```powershell
rex --gaming
```

This is equivalent to:

```powershell
rex --model tiny.en --device cpu --wake-word --low-latency
```

**Why these defaults:**

| Setting | Why |
|---------|-----|
| `tiny.en` model | ~80 ms CPU transcription — fast enough for early-match to fire before the silence-flush deadline. small.en/medium.en on CPU run 300 ms–4 s, which causes early matches to be missed. |
| `cpu` device | Frees ~2.5 GB VRAM and 100% of GPU compute for the game. |
| `--wake-word` | Prevents in-game chat from triggering REX. |
| `--low-latency` | 250 ms VAD silence cutoff (the default already, but pinned for clarity). |

**Tradeoff:** tiny.en is less accurate than small.en. If you find it misrecognizing your commands, bump the model:

```powershell
rex --gaming --model base.en   # ~150 ms on CPU, more accurate
```

Individual flags always win over `--gaming`, so you can override any single piece.

**Mode comparison:**

| Mode | Model | Device | Wake | E2E latency | VRAM |
|------|-------|--------|------|-------------|------|
| Default | small.en | cuda (auto) | off | ~250 ms | ~2.5 GB |
| `--gaming` | tiny.en | cpu | on | ~150 ms | 0 |
| `--standard` | small.en | cuda | off | ~400 ms | ~2.5 GB |

---

### Configuration

REX stores configuration in `~/.rex/`:

```
~/.rex/
  config.yaml     # Main configuration
  secrets.yaml    # Fallback secret storage (if keyring unavailable)
  logs/           # Log files
  models/         # Cached Whisper models
```

#### Environment Variable Overrides

| Variable              | Description                              |
| --------------------- | ---------------------------------------- |
| `REX_MODEL`           | Override Whisper model                   |
| `REX_DEVICE`          | Force CPU/GPU (`cpu`/`cuda`)             |
| `REX_SERVICE`         | Active service (`ytmd`/`spotify`/`none`) |
| `YTMD_TOKEN`          | YTMD authorization token                 |
| `YTMD_HOST`           | YTMD host (default: localhost)           |
| `YTMD_PORT`           | YTMD port (default: 9863)                |
| `SPOTIPY_CLIENT_ID`   | Spotify client ID                        |
| `SPOTIPY_CLIENT_SECRET`| Spotify client secret                   |
| `SPOTIPY_REDIRECT_URI`| Spotify OAuth redirect URI               |

---

### Troubleshooting

**No audio input detected:**
- Check Windows sound settings for default microphone
- Run `rex status` to see detected audio device
- Try running `rex setup` and use the audio test

**YTMD connection errors:**
- Run `rex test ytmd` to check connectivity
- Verify Companion Server is enabled in YTMD settings
- Re-run `rex setup` to get a new token

**Spotify device not found:**
- Open the Spotify desktop app before running REX
- Run `rex test spotify` to check connection
- Re-authenticate if needed

**CUDA not being used:**
- Run `rex setup` - it will detect your GPU and offer to install CUDA PyTorch
- Or manually install: `pipx runpip rex-voice-assistant install torch --index-url https://download.pytorch.org/whl/cu124 --force-reinstall`
- Verify: `rex` should auto-detect and log "CUDA detected, using GPU acceleration"

---

### Development

```bash
# Clone and install in development mode
git clone https://github.com/David-Antolick/rex_voice_assistant.git
cd rex_voice_assistant
pip install -e ".[dev]"

# Run tests
pytest

# Run directly
python -m rex_main.rex --debug
```

---

### Roadmap

- Custom "Hey Rex" wake-word model (prebuilt "hey jarvis" already supported via `[wake_word]` extra)
- Discord integration (waiting for RPC API access)
- Application controls (open/close apps)
- Performance optimizations

---

### Contributing

PRs welcome. Please keep changes small and document new config flags in this README. For larger features, open an issue to discuss design.
