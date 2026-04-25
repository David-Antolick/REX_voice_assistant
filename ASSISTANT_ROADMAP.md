# REX → Personal Intelligence Layer — Roadmap

Strategy doc for evolving REX from a music voice controller into a local, always-on dispatcher that routes voice intents to the right tool (built-in actions, system commands, or delegated agents like Claude Code).

**Last updated:** 2026-04-25

---

## Vision

REX is a **router**, not a doer.

A small local model classifies voice intent and dispatches to one of:
1. **Built-in regex/fast-path actions** — music control, clip, volume, etc. (current behavior, sub-100ms)
2. **System actions** — launch apps, focus windows, OS-level controls
3. **Delegated agents** — Claude Code with a scoped prompt, plan-mode, and confirm-before-execute

The local model never tries to *do* the hard thing. It picks the right tool and hands off. That keeps REX fast, offline, and private, while letting heavy lifting happen in specialized agents.

**Example flow** — *"make the dashboard color blue for this app"*:
1. Wake word fires → REX listens
2. Whisper transcribes
3. Regex matcher misses (not a known command)
4. Local LM classifies intent: `delegate_to_claude_code`
5. REX spawns `claude --permission-mode plan` in the project dir with the scoped prompt
6. Claude returns a plan via the SDK
7. REX surfaces the plan via TTS / notification: "Claude wants to edit dashboard.py and styles.css. Proceed?"
8. User says "yes" → REX resumes Claude execution
9. REX reports completion

---

## Architectural shifts required

| Today | Tomorrow |
|-------|----------|
| Audio → VAD → Whisper → regex matcher → command function | Audio → **wake word** → VAD → Whisper → **fast-path matcher OR LM classifier** → **action registry** → handler (sync action OR delegated agent) |
| Flat `commands.py` module | Action registry: `{name, description, args_schema, handler, kind}` |
| Silent on success | TTS feedback layer (Piper) for confirmations + status |
| Terminal-only | System tray + always-on |
| Single-PC | Optionally network-addressable (Jetson appliance) |

---

## Upgrade Tiers

### Tier 0 — Done

- [x] Low-latency mode as default ([cli.py:62](rex_main/cli.py#L62), [default_config.yaml:21-22](rex_main/default_config.yaml#L21-L22))
- [x] Volume commands excluded from early-match ([matcher.py:34-40](rex_main/matcher.py#L34-L40))

### Tier 1 — Wake Word (NEXT)

- [ ] Integrate openWakeWord with prebuilt `hey jarvis` model (proof of concept)
- [ ] New `wake_word.py` worker tapping `audio_q` before VAD
- [ ] Listening-window state machine (wake → 5s active window → return to idle)
- [ ] Audio cue on wake-word fire
- [ ] Config knobs: enable/disable, threshold, window duration, model selection
- [ ] Train custom `hey rex` model (deferred, post-PoC)

### Tier 2 — Dispatcher Foundation

- [ ] **Action registry** — refactor `commands.py` into a registry pattern with structured metadata per action
- [ ] **Hybrid intent classifier** — regex-first (current matcher), LM fallback only on miss
- [ ] **Local LM backend** — Qwen2.5-1.5B-Instruct via llama.cpp/ollama, Q4 quantized, GPU-offloaded
- [ ] **LM prompt template** — JSON-schema-style tool selection with few-shot examples

### Tier 3 — Claude Code Dispatcher

- [ ] New action kind: `delegate_to_claude_code(scoped_prompt, project_dir, plan_mode=True)`
- [ ] Use Claude Agent SDK (Python) for clean plan-mode + structured output
- [ ] Plan summarization → TTS surface to user
- [ ] Voice confirmation loop (yes/no) before resuming execution
- [ ] Status updates during long Claude runs

### Tier 4 — Assistant Ergonomics

- [ ] **TTS layer** — Piper TTS for spoken responses
- [ ] **System tray app** — pystray icon with idle/listening/thinking/executing states
- [ ] **Conversation context** — rolling buffer for multi-turn ("open spotify" → "play that jazz playlist")

### Tier 5 — PC Intelligence Layer

- [ ] **OS action library** — pywinauto / pygetwindow for app launch, window focus, system volume, screenshot+OCR
- [ ] **Project alias map** — `~/.rex/projects.yaml` for "open the rex repo in vscode"-style commands
- [ ] **Memory** — durable user preferences ("I like quiet music in the evenings")

---

## Hardware question — Jetson?

**Verdict:** *Don't buy hardware until the software justifies it.*

| Option | Cost | Verdict |
|--------|------|---------|
| Current PC GPU | $0 | **Use this. Plenty of headroom for Whisper + Qwen2.5-1.5B (under 2GB VRAM total).** |
| Jetson Orin Nano 8GB | ~$500 | Possible later, tight on memory for full stack |
| Jetson Orin NX 16GB | ~$900 | Comfortable always-on appliance, *if* you want REX accessible from multiple PCs |
| Jetson AGX Orin | $1500+ | Overkill |

**Reasons to consider Jetson eventually:**
- Always-on appliance, doesn't compete with gaming/work for GPU
- Low idle power (~7–15W vs. desktop GPU's 30W idle)
- Forces clean network architecture (REX-as-LAN-service, every PC has a thin agent)

**Reasons NOT to do it yet:**
- ARM64 wheel pain (ctranslate2, llama.cpp work but it's not friction-free)
- Whisper is 3–5× slower on Orin Nano than a desktop 3060
- Multi-machine agent design is a *much* bigger project than the dispatcher itself
- You'll learn what you actually need by building on existing hardware first

**Recommendation:** Build through Tier 3 on existing PC. Re-evaluate hardware question only once you're hitting a real constraint (e.g., "I want REX in another room").

---

## Local Model Candidates (Tier 2 reference)

| Model | Size (Q4) | VRAM | Best for |
|-------|-----------|------|----------|
| Qwen2.5-0.5B-Instruct | ~400MB | <500MB | Pure intent classification, super fast |
| **Qwen2.5-1.5B-Instruct** | ~1.0GB | ~1.2GB | **Recommended starting point — strong tool calling** |
| Phi-3-mini (3.8B) | ~2.3GB | ~2.5GB | More general reasoning, if classifier feels weak |
| Gemma-2-2B | ~1.6GB | ~1.8GB | Solid all-rounder |
| SmolLM2-1.7B-Instruct | ~1.1GB | ~1.3GB | Tool-use trained, strong alternative |

Backend: **llama-cpp-python** (CUDA build) or **Ollama** daemon.

---

## Sequencing Rationale

Why this order:

1. **Wake word first** — independent, immediately useful, unblocks the always-on story. Builds a real dataset of "did it fire correctly?" that informs everything later.
2. **Claude Code dispatcher before action registry** — building one *real* delegate forces the registry design organically. Don't design abstractly; let one concrete dispatcher pull the abstraction into existence.
3. **Tray + TTS after dispatcher works** — they're polish, not foundation. Ship them once the core flow is real.
4. **OS actions / memory last** — high scope risk, high payoff. Defer until 1–3 are battle-tested.

---

## Out of Scope (for now)

- Custom wake word training (Tier 1 stretch goal, post-PoC)
- Multi-machine architecture / Jetson migration
- Web/mobile interfaces
- Cloud sync of memory
- Discord integration (waiting on RPC API access — see existing roadmap)
