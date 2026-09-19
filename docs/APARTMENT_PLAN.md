# REX Apartment Plan — from loose plan to running by Christmas

Sequenced execution plan for turning REX into the apartment controller
described in [SMART_APT_FEASIBILITY.md](SMART_APT_FEASIBILITY.md).
Written 2026-09-19. Target: **working in the apartment by 2026-12-25**,
which is 14 weeks. Every milestone is independently useful, so slipping
one does not zero the ones before it.

Like [PHASE0_DISPATCH.md](PHASE0_DISPATCH.md) this is meant to be
executed, not admired. Function names are the durable anchors.

## What already exists (and why the split is cheaper than it looks)

- **One dispatch seam.** `matcher.dispatch_text(text, ...)` owns match,
  gate, UI events, and the error boundary (Phase 0, landed 2026-08-15).
  The hub's text endpoint is a thin wrapper around it.
- **Transcription is a plain function.** `WhisperWorker._transcribe(pcm)`
  takes 16 kHz float PCM and returns text. The hub's audio endpoint is
  decode → `_transcribe` → `dispatch_text`.
- **The web stack is already a dependency.** `rex_main/dashboard/server.py`
  runs FastAPI + uvicorn + websockets in a thread. The hub is a second
  FastAPI app on the same stack, not a new dependency.
- **Wake word is already hub-shaped.** `openwakeword` with the custom
  `hey_rex` model runs on raw audio frames in `WakeWordDetector`. It
  does not care whether the frames came from a local mic or a socket.
- **The registry already carries planner metadata** (`summary`, `args`,
  `side_effects`). The LLM layer, when it comes, reads that. Nothing to
  redo.

## Decisions to lock in now

These are the expensive-to-reverse ones. Everything else can be
changed mid-flight.

1. **Two brains, one package.** The PC keeps its local mic → Whisper →
   dispatch pipeline for gaming-grade latency; the hub runs the same
   package headless on the server as `rex hub` for the phone and the
   room mics. Routing PC-targeted actions from the hub to the PC is
   **post-Christmas**. Cutting that is what makes the date real.
2. **Actions declare where they execute.** `ActionSpec` gains
   `executor: "local" | "hub"`. `smartrent` is `hub`-capable; `apps`,
   `system_*`, `steelseries`, `ytmd` are `local`. The hub loads only
   hub-capable backends. Until routing exists, a `local` action asked
   of the hub is a visible "that only works on the PC" and nothing else.
3. **Unlock never fires from a room mic.** Ever. Lock, lights, and
   thermostat can. Unlock is allowed only from the phone client with a
   per-device token, and only if enabled in settings. This is a
   registry-level property (`requires_trusted_client=True`), enforced
   in `dispatch_text`, not a per-handler convention.
4. **Room satellites speak Wyoming.** No custom firmware, no custom
   audio protocol. The hub implements the server side of Wyoming so
   any ESP32-S3 satellite (Home Assistant Voice PE, ESP32-S3-BOX-3,
   Atom Echo) plugs in. Which satellite heard you is the room context.
5. **The hub is Linux, headless, on Tailscale, never on the public
   internet.** HTTPS via `tailscale cert` or Caddy. Phone reaches it
   from anywhere on the tailnet; nobody else reaches it at all.
6. **No LLM in the fast path.** Regex commands stay instant. A local
   model, if added, is a fallback for utterances the regex table
   rejects. It is a stretch goal, not on the Christmas path.
7. **Heavy inference is a remote endpoint, not a hub job.** The
   existing GPU box (currently serving GLM at 2–10 ms) hosts Whisper
   `large-v3-turbo` behind an OpenAI-compatible transcription endpoint
   (speaches / faster-whisper-server) and, later, the LLM planner. The
   hub keeps wake word, VAD, Wyoming, SmartRent, and the phone API
   local, sends only post-wake utterances to the endpoint, and falls
   back to CPU `small.en` if the endpoint is unreachable. SmartRent is
   cloud anyway, so a remote endpoint costs no resilience the apartment
   half ever had. The hub therefore needs no GPU.

## The sequence

| # | Milestone | Weeks | Done when |
|---|---|---|---|
| 0 | Spikes and orders | 1 | SmartRent script lists your devices; hardware ordered |
| 1 | `smartrent` backend on today's REX | 1–3 | "lights off" works from the gaming chair, a week without a reconnect bug |
| 2 | `rex hub` on the server | 3–6 | `curl` text to the hub locks the door; runs as a systemd service |
| 3 | Phone client | 6–8 | Lock the door from bed, from the car, on a home-screen icon |
| 4 | First room satellite | 8–12 | "hey rex, lights off" in the living room with no PC involved |
| 5 | Harden and spread | 12–14 | Second satellite, reconnects survive, Christmas |

Weeks are elapsed calendar weeks from 2026-09-19 at weekend-project
pace, judged against the August burst (Phase 0 plus docs plus the
discovery panel in three days). If you get a burst like that, 1–3
compress to a week.

### Milestone 0 — spikes and orders (week 1)

No REX code. Everything here de-risks or has lead time.

- **SmartRent spike.** `pip install smartrent.py`, twenty lines:
  login (with 2FA if the account has it), print every device with its
  type and attributes, lock the door, toggle one light. Save the output
  to `docs/specifics/spike_smartrent.md`. This answers: does the Fusion
  expose the lights and sensors as Z-Wave devices; is 2FA on; does the
  websocket stay up overnight. If this fails, everything downstream
  changes, so it goes first.
- **Order the server** if not already, and **one satellite**. Voice PE
  is the safe pick (on-device wake word, decent far-field mics, speaker
  for cues); Atom Echo is the cheap one to learn Wyoming on. Lead time
  is why this is week 1 and not week 8.
- **Tailscale** on the server and phone. Confirm the phone reaches the
  server from cellular.
- **Decide the GPU question.** `faster-whisper` is CUDA or CPU int8,
  nothing in between. `small.en` on a modern CPU is roughly one to two
  seconds for a short command: fine for the phone and the rooms, not
  for gaming. If the server can take a cheap NVIDIA card, Whisper gets
  it. Measure before buying; `rex benchmark` exists.

### Milestone 1 — `smartrent` backend on the PC (weeks 1–3)

Pure additive backend under the existing contract. Use the
`/rex-action` skill; it enforces everything below.

- `rex_main/actions/smartrent.py`: client class owning a background
  asyncio loop thread with the `smartrent-py` websocket kept open;
  `_get()` lazy singleton; `reset_client()`; `safe_call` that catches
  the library's and `websockets`' exceptions and logs, never raises.
- Actions: `smartrent_lock_door`, `smartrent_door_status`,
  `smartrent_lights_on` / `_off` with a `room` arg, `smartrent_dim`,
  `smartrent_set_temp`. **No unlock in this milestone.**
- Device naming: `~/.rex/config.yaml` gets a `smartrent.rooms` map from
  spoken name → device id, filled by a `rex setup` step that lists
  what discovery found. Names go into the regex as an `enum:` arg.
- Secrets: email, password, and optional TOTP seed in keyring
  (`rex`, `smartrent_*`), same as Spotify. Persist the refresh token so
  2FA is entered once, not per launch.
- Failure is visible: a cloud timeout emits a `no_match`-style HUD
  event ("couldn't reach SmartRent"), not silence. That is the
  `safe_call` returning a sentinel the wrapper turns into a UI event.
- Tests: `test_actions.py` gate as usual; add a fake client so the
  actions test without credentials.
- Docs: `ACTIONS.md` inventory; README gets the honest line that home
  integrations are opt-in cloud.

Why before the split: it is useful the day it lands, it proves the
protocol over real uptime, and it is the only milestone that needs
nothing but the PC.

### Milestone 2 — `rex hub` on the server (weeks 3–6)

The split. The hub is the same package with a different entry point.

- `rex_main/hub/` package: `app.py` (FastAPI), `pipeline.py` (owns one
  `WhisperWorker` and one `ListeningState`, no audio device), `auth.py`
  (bearer tokens per client, stored in config).
- Endpoints:
  - `POST /v1/text` → `dispatch_text(...)` → `DispatchResult` as JSON.
  - `POST /v1/audio` → decode (wav or opus; `soundfile` handles wav,
    add `av` or shell out to ffmpeg for opus) → `_transcribe` →
    `dispatch_text`.
  - `GET /v1/actions` → the active registry (feeds the phone UI's
    "what can I say" the same way `ui/discovery.py` feeds the tray).
- `rex hub` CLI command; `--host`, `--port`, `--model`.
- `Transcriber` protocol with two implementations: `LocalWhisper`
  (wraps `WhisperWorker._transcribe`) and `RemoteWhisper` (POSTs wav to
  the endpoint's `/v1/audio/transcriptions`, bearer token, 3 s
  timeout). Hub prefers remote, falls back to local on error, and
  logs which one answered. The PC keeps `LocalWhisper` only.
- **Linux headless port.** This is the unknown-cost item. Known work:
  `whisper_worker._setup_cuda_paths` is Windows-shaped; `rex.py`
  imports must not drag in PySide6; `keyring` on headless Linux needs
  the `secrets.yaml` fallback (already exists); `sounddevice` must not
  be opened when there is no mic. Budget a weekend for this alone.
- Registry: add `executor` per decision 2; hub loads hub-capable
  backends only.
- Ops: systemd unit, restart on failure, logs to journald, the existing
  dashboard mounted on the hub so you can see it working.

Done when `curl -H "Authorization: Bearer …" -d '{"text":"lock the
door"}'` from the phone's terminal locks the door, and the service has
survived a reboot.

### Milestone 3 — phone client (weeks 6–8)

Served by the hub. One static page, no framework.

- Text box + hold-to-talk button. Browser `MediaRecorder` records on
  press, POSTs the blob to `/v1/audio` on release. Push-to-talk, not
  streaming, not wake word: mobile browsers will not listen in the
  background and that is the right constraint for the door.
- PWA manifest so it lives on the home screen with an icon.
- Shows the `DispatchResult`: matched action and args, or "didn't
  catch that", or "SmartRent unreachable". Same states as the HUD.
- **Unlock lands here**, behind decision 3: `smartrent_unlock_door`
  with `requires_trusted_client=True`, off by default in settings,
  confirm tap in the UI before it sends.
- HTTPS on the tailnet. `tailscale cert` gives a trusted cert with no
  DNS games.

This is the Christmas demo even if 4 slips: lock the door from bed.

### Milestone 4 — first room satellite (weeks 8–12)

- Hub implements Wyoming server side: satellite connects, streams
  16 kHz PCM, hub runs `WakeWordDetector` (reuse, it consumes frames
  from a queue) then VAD then Whisper then `dispatch_text`. Or, on
  Voice PE, let the satellite's on-device wake word gate the stream
  and skip hub-side wake detection. Start with hub-side: it uses the
  `hey_rex` model you already trained.
- Satellite identity → room in config. `smartrent_lights_off` with no
  `room` arg means the room that heard you. Small change in the
  handler, large change in how it feels.
- Feedback: play the wake cue and a done/fail tone back through
  Wyoming (`play_wake_cue` already exists). No TTS yet.
- Living room first. One satellite for the whole milestone.

### Milestone 5 — harden and spread (weeks 12–14)

- Second satellite. Now two rooms can hear one utterance; pick the
  stream with the higher wake-word score, drop the other for the
  listening window.
- Reconnect logic everywhere: SmartRent websocket, satellite sockets,
  Whisper worker crash. The August lesson was that the default path had
  no error boundary; do not rediscover it in December.
- Echo: if music is playing in the room, expect wake-word misses. The
  fix is on the satellite (Voice PE has AEC), not in REX. Note it, do
  not build for it.

## Explicitly not on the Christmas path

- Hub → PC action routing (phone says "pause" and the PC pauses).
- Local LLM planner. Stretch, after the regex table has proved it
  covers the daily commands.
- Brivo. Phone bridge or nothing; revisit in January if "open the
  gate" turns out to be a daily want.
- PC Control Phase 1 (`system_audio`, `system_window`). Separate track.
  Do not try to land both by Christmas; pick, and the apartment is the
  one with the date.
- Text to speech.

## Hardware

Sized cautiously: bigger model than needed, headroom on the hub.

### Where things run

| Workload | Runs on | Why |
|---|---|---|
| Wake word (`hey_rex`, openWakeWord), VAD | hub | cheap on CPU, must be local so only post-wake audio leaves the apartment |
| Wyoming server, SmartRent socket, phone API | hub | always-on, on the LAN the satellites can reach |
| Whisper `large-v3-turbo` (fp16, ~1.6 GB VRAM) | GPU box | Whisper pads every clip to 30 s, so a short command costs the same as a long one: ~150–300 ms on any RTX, 1.5–3 s on a good CPU. Large models belong on the GPU |
| Whisper `small.en` int8 | hub, fallback only | what the PC runs today; ~0.5–1 s on an 8-core CPU |
| LLM planner (stretch) | GPU box | already there, OpenAI-compatible API |

### Hub (apartment, always-on)

| Pick | Spec | Rough cost |
|---|---|---|
| **Recommended** | Ryzen 7 7840HS / 8845HS-class mini PC, 8 cores, 32 GB (64 GB if it also does ZFS backup duty), 1 TB NVMe + backup drives | $500–700 |
| Minimum | Intel N100 mini PC, 16 GB | $150–200; fine for wake/VAD for 4 satellites, but the CPU Whisper fallback is `base.en` at best |
| If you want inference in-apartment anyway | small tower + used RTX 3060 12 GB or 4060 Ti 16 GB | +$200–400; not needed while the GPU box exists |

Wake word plus VAD for one satellite stream is a few percent of one
core. Four satellites is nothing. The Ryzen pick is about fallback
headroom and the backup role, not the voice work.

### GPU box (already exists)

Add a Whisper endpoint next to GLM. `large-v3-turbo` fp16 is ~1.6 GB
VRAM; full `large-v3` is ~3 GB and is the "bigger than needed" option
if VRAM is free, though turbo is within noise of it on English
commands. Either is invisible next to a GLM deployment.

### Satellites (mics)

| Pick | What you get | Cost |
|---|---|---|
| **Home Assistant Voice Preview Edition** | ESP32-S3 + XMOS XU316 DSP: echo cancellation, noise suppression, dual far-field mics, on-device wake word (microWakeWord), speaker + 3.5 mm out, hardware mute, cased. Wyoming/ESPHome native | $59–69 each |
| ReSpeaker Lite kit (XIAO ESP32-S3) | Same XMOS chip, 2 mics, ~3 m far-field, small speaker, acrylic case; needs ESPHome flashing | ~$34 each |
| Atom Echo | single mic, poor far-field; bedside/desk only | ~$13 |

Recommendation: Voice PE, one per room you actually talk in. For a
typical apartment that is living room, bedroom, and kitchen if it is
its own room; the office/gaming room already has the PC. Order one
now, two more at milestone 5.

Wake word placement: the `hey_rex` model is openWakeWord format and
runs on the hub, so satellites stream continuously (16 kHz mono, ~256
kbps each, trivial on LAN). Voice PE's on-device models ("okay nabu",
"hey jarvis") are a fallback if hub-side detection misbehaves; a
custom microWakeWord for "hey rex" is a separate training pipeline
and is not on the Christmas path.

### Budget

| Item | Qty | Cost |
|---|---|---|
| Hub mini PC (Ryzen, 32 GB) | 1 | $500–700 |
| Voice PE satellites | 3 | ~$180–210 |
| GPU box Whisper endpoint | 0 | already owned |
| **Total** | | **~$700–900** |

The N100 hub and ReSpeaker Lite path lands the same system for about
$300, with less fallback and more flashing.

## The one question that reorders everything

If the milestone 0 spike shows the Fusion's lights and sensors are
**not** exposed as separate SmartRent devices, milestone 1 is lock and
thermostat only, and "lights" moves to a Z-Wave question (does the
Fusion let you pair your own switches, or does the property control
pairing). Find that out in week 1, not week 6.
