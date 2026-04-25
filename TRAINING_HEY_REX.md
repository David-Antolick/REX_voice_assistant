# Training a Custom "Hey Rex" Wake-Word Model

**Audience:** REX users who want to replace the prebuilt `hey_jarvis` wake word with a custom-trained `hey rex` model fine-tuned on their own voice.
**Hardware target:** NVIDIA GPU with ≥6 GB VRAM (this guide is written for an RTX 3070 Ti, 8 GB).
**Time budget:** ~60–90 minutes end-to-end (recording + training).

---

## 0. Overview

This guide produces a `hey_rex.onnx` file that REX loads in place of `hey_jarvis`. The training pipeline (provided by [openWakeWord](https://github.com/dscripka/openWakeWord)) does most of the work: it generates ~2000 synthetic positive samples using Piper TTS, augments them with reverb and noise, and trains a small DNN on top of a frozen audio-embedding model.

We're doing the **"Option B"** variant: in addition to the synthetic positives, we mix in **~100 recordings of your own voice** saying "hey rex". This dramatically reduces false-fire rate on your specific microphone and acoustic environment compared to a synthetic-only model.

### What you'll have at the end

- `~/.rex/wake_models/hey_rex.onnx` — the trained model (~1 MB)
- `~/.rex/config.yaml` updated to point REX at it
- A short verification run to confirm it fires on real "hey rex" and not on conversation

### Time breakdown (rough, on a 3070 Ti)

| Phase | Time |
|-------|------|
| 1. Record your samples | 15 min |
| 2. Set up training env | 10 min |
| 3. Generate synthetic positives | 8–12 min |
| 4. Mix in your recordings | 1 min |
| 5. Train | 25–40 min |
| 6. Validate | 5 min |
| 7. Install into REX | 1 min |

---

## 1. Phase 1 — Record your samples (15 min)

REX has a built-in recorder that saves clean 16 kHz mono WAVs in the format the training pipeline expects.

```powershell
rex record-wake-samples --count 100
```

It will ask for your name (used as a folder label) — type your first name and continue. The tool prompts you 100 times, plays a tone, records ~2 seconds, validates the level (rejects silence and clipping), and saves to:

```
~/.rex/wake_training/recordings/<your_name>/hey_rex_001.wav
~/.rex/wake_training/recordings/<your_name>/hey_rex_002.wav
...
```

### Multi-speaker model (recommended if you have friends willing to help)

A model trained on 3–5 voices is dramatically more robust than a single-speaker model — including for *you*. To collect samples from friends:

1. Send them [CONTRIBUTING_VOICE_SAMPLES.md](CONTRIBUTING_VOICE_SAMPLES.md). It walks them through installing REX (one `pipx` command), recording 100 samples, and producing a single `.zip` to send back.
2. Collect the `.zip` files (Discord, email, Drive — whatever).
3. Phase 4 below has the merge instructions.

You don't have to do this — your own 100 samples are enough for a working model. But if you can recruit 3–5 people, the result will generalize much better.

### Variation matters more than perfection

The whole point of recording your own samples is to teach the model your *real* voice — not a single clean studio take repeated 100 times. **Vary**:

- **Distance**: close to mic, normal sitting position, leaning back, across the room
- **Volume**: whispered, normal, slightly raised, half-shouted
- **Pace**: slow, normal, fast, rushed
- **Tone**: bored, excited, tired, annoyed, neutral
- **Environment**: with your fan running, with music playing softly, with the TV on, in silence
- **Pronunciation**: emphasize "hey", emphasize "rex", run them together, slight pauses

A few low-quality samples (slightly muffled, slightly off-mic) help the model generalize — they don't ruin training. Don't try to make every sample identical.

### Resuming a session

The tool auto-numbers from where it left off. If you record 50 today and 50 tomorrow, you'll end up with `hey_rex_001.wav` through `hey_rex_100.wav`.

### Don't have a 2nd environment? That's fine.

You can do all 100 in one sitting. Just vary your delivery as much as you can. The synthetic positives cover the diversity you can't, so don't stress about it.

---

## 2. Phase 2 — Set up the training environment (10 min)

The openWakeWord training pipeline has heavy dependencies (TensorFlow, SpeechBrain, audiomentations, Piper TTS) that conflict with REX's pins. Use a **separate venv**.

### 2.1 Clone openWakeWord

```powershell
cd C:\Users\danto\OneDrive\Documents\python
git clone https://github.com/dscripka/openWakeWord.git oww-train
cd oww-train
```

### 2.2 Create an isolated venv

```powershell
python -m venv .venv-train
.\.venv-train\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

> **Make sure the prompt now shows `(.venv-train)`** before continuing. Every command in Phases 2–6 must run in this venv.

### 2.3 Install training dependencies

These are the exact pins from the openWakeWord training notebook:

```powershell
pip install -e .
pip install piper-phonemize webrtcvad
pip install mutagen==1.47.0 torchinfo==1.8.0 torchmetrics==1.2.0
pip install speechbrain==0.5.14
pip install audiomentations==0.33.0 torch-audiomentations==0.11.0
pip install acoustics==0.2.6
pip install tensorflow-cpu==2.8.1 tensorflow_probability==0.16.0
pip install onnx_tf==1.10.0 pronouncing==0.2.0
pip install datasets==2.14.6 deep-phonemizer==0.0.19
pip install jupyter notebook
```

### 2.4 Verify GPU is visible to torch

```powershell
python -c "import torch; print('CUDA:', torch.cuda.is_available(), '| device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu')"
```

You should see `CUDA: True | device: NVIDIA GeForce RTX 3070 Ti`. If not, torch was installed CPU-only — fix with:

```powershell
pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu124
```

> Note: we use `tensorflow-cpu` on purpose. The training step is small enough that CPU TF is fine, and the CUDA TF wheel is huge and prone to driver mismatch. Torch is the one that actually needs the GPU.

---

## 3. Phase 3 — Generate synthetic positives (8–12 min)

```powershell
jupyter notebook notebooks/automatic_model_training.ipynb
```

A browser tab will open. Find the **configuration cell** near the top of the notebook and set:

```python
config = {
    "model_name": "hey_rex",
    "target_phrase": ["hey rex"],
    "n_samples": 2000,
    "n_samples_val": 500,
    "steps": 10000,
    "target_accuracy": 0.6,
    "target_recall": 0.25,
    # ... leave the rest of the keys at their defaults
}
```

Run cells **top-to-bottom** until you reach the cell that calls `generate_adversarial_texts(...)` and the cells that produce the synthetic audio with Piper TTS. **Stop after the synthetic generation completes** — don't run the training cell yet.

### What's happening

Piper TTS runs ~6–8 different voice models against the phrase "hey rex" with random pitch/speed/volume jitter, producing 2000 positives + 500 validation samples. They land in:

```
oww-train/my_custom_model/positive_train/
oww-train/my_custom_model/positive_test/
```

(exact path depends on the notebook config; check the cell output for "saving to ...")

### Watching VRAM

In a separate terminal:

```powershell
nvidia-smi -l 2
```

Expect ~3–4 GB during synthetic generation (Piper TTS uses ONNX runtime on GPU). If you OOM, drop `n_samples` to 1000 in the config.

---

## 4. Phase 4 — Mix in real recordings (1–5 min)

This is the "Option B" step that makes your model dramatically better than synthetic-only. You can do this with just your own recordings, or — even better — by merging in `.zip` submissions from friends who followed [CONTRIBUTING_VOICE_SAMPLES.md](CONTRIBUTING_VOICE_SAMPLES.md).

### 4.1 Find the synthetic positives folder

In the notebook, the previous cells should have printed something like `Saved 2000 samples to my_custom_model/positive_train/`. Note that path.

### 4.2 Copy your own recordings in

In a new PowerShell window (outside the venv is fine — this is just file copy):

```powershell
$src = "$env:USERPROFILE\.rex\wake_training\recordings"
$dst = "C:\Users\danto\OneDrive\Documents\python\oww-train\my_custom_model\positive_train"

# Copy WAVs from every contributor folder under ~/.rex/wake_training/recordings/<name>/
Get-ChildItem "$src" -Directory | ForEach-Object {
    Copy-Item "$($_.FullName)\*.wav" $dst -Force
}
```

The training pipeline globs the folder, so all of your `*.wav` files become part of the positive set. With 100 of your own samples, real recordings make up ~5% of the positives by count — but their per-sample weight during training is comparable to the synthetic ones.

### 4.3 Merge contributions from friends (multi-speaker model)

Each friend should send you a `.zip` produced by `rex package-wake-samples`. Each zip contains:

```
alex/
  hey_rex_001.wav
  hey_rex_002.wav
  ...
  notes.md
  manifest.json
```

To merge:

```powershell
# Where you've collected the .zips - adjust as needed
$inbox = "$env:USERPROFILE\Downloads\rex_contributions"

# Extract each zip into ~/.rex/wake_training/recordings/<name>/ (zips are already namespaced)
Get-ChildItem "$inbox\*.zip" | ForEach-Object {
    Expand-Archive $_.FullName -DestinationPath "$env:USERPROFILE\.rex\wake_training\recordings" -Force
}

# Then re-run the copy from 4.2 to pick up the new contributors.
```

Before merging, **spot-check each contributor**:

```powershell
$src = "$env:USERPROFILE\.rex\wake_training\recordings"
Get-ChildItem $src -Directory | ForEach-Object {
    $count = (Get-ChildItem "$($_.FullName)\*.wav").Count
    "{0,-15} {1} samples" -f $_.Name, $count
}
```

You can also open each contributor's `manifest.json` to see their level stats and microphone — peak averages between 0.2 and 0.8 are fine, anything way outside that range is suspect (clipped or near-silent recordings). If a batch looks bad, ask them for another 30 samples or just skip that folder during the copy.

### 4.4 Sanity check the final positive set

```powershell
$dst = "C:\Users\danto\OneDrive\Documents\python\oww-train\my_custom_model\positive_train"
(Get-ChildItem "$dst\*.wav").Count
```

Should be ~2000 (synthetic) + however many real samples you merged. With 4 contributors at 100 each, expect ~2400 total — roughly 17% real, 83% synthetic. That ratio works well; the model learns the synthetic-voice diversity *and* anchors on real human acoustics.

### 4.5 Multi-speaker tip

If you have 5+ contributors, your model will generalize across most voices that sound like the contributor pool. If a contributor has a noticeably different accent or speech pattern from the rest, that's a feature — it helps the model handle out-of-distribution speakers better, not worse.

Should report 100.

---

## 5. Phase 5 — Train the model (25–40 min)

Back in the notebook, run the remaining cells from the top of the **Model Training** section through the end of the notebook. The key cell is the one that calls `train_model(...)`.

### What you should see

- A progress bar over `steps` (default 10000)
- Periodic accuracy / recall printouts every ~500 steps
- VRAM usage drops to ~1.5 GB once training starts (the embedding model is small)
- Training accuracy climbing past 0.95 within the first ~3000 steps

### When to stop early

If accuracy plateaus at <0.85 for 2000+ steps, something's wrong with the data — usually too few unique synthetic voices or recordings that are mostly silence. Re-check the recordings (`ls -l ~/.rex/wake_training/recordings`) and the synthetic-positives folder (should have ~2100 files after the copy).

### Output

The final notebook cell converts the trained model to ONNX + TFLite and saves them to:

```
oww-train/my_custom_model/hey_rex.onnx
oww-train/my_custom_model/hey_rex.tflite
```

REX uses the `.onnx` version.

---

## 6. Phase 6 — Validate (5 min)

Before installing into REX, sanity-check the model in openWakeWord's microphone demo.

```powershell
# Still in the oww-train venv
python examples/detect_from_microphone.py --model_path my_custom_model/hey_rex.onnx --threshold 0.5
```

Then:

1. Say "hey rex" five times — all five should fire (look for `hey_rex` score > 0.5 in the live output).
2. Read a paragraph from a book or talk to yourself for a minute — should fire 0 times.
3. Watch a 30-second YouTube video — should fire 0 times.

**Tuning thresholds**:
- Fires reliably on "hey rex", but also on ambient conversation? → raise threshold to 0.6 or 0.7.
- Misses "hey rex" sometimes? → lower threshold to 0.4. If that doesn't fix it, you need more/better recordings — go back to Phase 1 with another 50 in different conditions and re-train.

---

## 7. Phase 7 — Install into REX (1 min)

### 7.1 Copy the model

```powershell
mkdir "$env:USERPROFILE\.rex\wake_models" -ErrorAction SilentlyContinue
Copy-Item "C:\Users\danto\OneDrive\Documents\python\oww-train\my_custom_model\hey_rex.onnx" `
          "$env:USERPROFILE\.rex\wake_models\hey_rex.onnx"
```

### 7.2 Update REX config

Either re-run `rex setup` (it will auto-discover models in `~/.rex/wake_models/` and offer to switch), or hand-edit `~/.rex/config.yaml`:

```yaml
wake_word:
  enabled: true
  model: ~/.rex/wake_models/hey_rex.onnx
  threshold: 0.5            # whatever worked in Phase 6
  listening_window_seconds: 6
  debounce_seconds: 1.0
  cue_enabled: true
```

### 7.3 Run REX

```powershell
rex --wake-word
```

Look for `Loaded custom wake-word model: C:\Users\danto\.rex\wake_models\hey_rex.onnx` in the startup logs.

Then say "hey rex" — you should hear the cue tone and `WakeWord fired: hey_rex (score=0.xx)`. Within 6 seconds, any normal command ("play music", "next") will execute.

---

## 8. Troubleshooting

### `piper-phonemize` install fails on Windows

Known Windows wheel issue. Workaround: use WSL2 for the training venv only (REX itself stays on native Windows). Or install from the prebuilt wheel:

```powershell
pip install https://github.com/rhasspy/piper-phonemize/releases/download/v1.1.0/piper_phonemize-1.1.0-cp310-cp310-win_amd64.whl
```

Match the cp310 to your Python version (cp311 / cp312).

### `tensorflow-cpu` won't import

You probably have CUDA TF lingering. Force-clean:

```powershell
pip uninstall -y tensorflow tensorflow-gpu tensorflow-cpu
pip install tensorflow-cpu==2.8.1
```

### CUDA out of memory during synthetic generation

Drop `n_samples` to 1000 (still plenty for a personal model). If it still OOMs, set `os.environ["CUDA_VISIBLE_DEVICES"] = ""` in the first notebook cell to force Piper TTS to CPU — slower (~25 min) but works on any hardware.

### Model fires on TV / podcasts / family conversations

Two fixes, in order:

1. Raise threshold from `0.5` → `0.6` → `0.7` in `~/.rex/config.yaml`. Often enough.
2. If still bad: retrain with more negative data. The notebook has a `false_positive_validation_data_path` config — point it at a folder of 30 minutes of audio that includes the false-fire sources, and re-run training. The model will learn to specifically avoid those.

### Model never fires on real "hey rex"

Threshold too high, or not enough variation in your recordings. Lower threshold to 0.4, then if still bad, record another 50 samples with deliberate variation (different pace, different distance) and retrain.

---

## 9. What "good" looks like

After 1–2 days of normal use:

| Metric | Target |
|--------|--------|
| Accuracy on "hey rex" | >95% — fires almost every time you say it |
| False-fire rate during normal use | <1 per hour |
| False-fire rate during gaming/streaming with audio playing | <3 per hour |

If you're not hitting these after threshold tuning, the answer is almost always *more recordings with more variation* — not different hyperparameters.

---

## 10. Next steps

Once your custom model is solid, consider:

- **Bumping the threshold** for fewer false fires now that REX knows your voice well
- **Sharing the recordings folder** as backup — `~/.rex/wake_training/recordings/` is your training corpus and will be useful again if you re-train later
- **Training a second wake word** for a different action (e.g., "hey rex stop" as a panic-cancel) — same pipeline, different `target_phrase`
