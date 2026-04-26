# Retraining the "Hey Rex" Wake Word — Field Notes

This is the war-tested, no-bullshit version of the training procedure for when you (or anyone) needs to retrain `hey_rex` with more samples or different settings. Every step here represents a problem that was either solved or worked around during the original 9-hour training run on 2026-04-25/26.

If this is your **first time** training a wake word, read [TRAINING_HEY_REX.md](TRAINING_HEY_REX.md) for the conceptual walkthrough first. Come back here for the actual commands.

---

## TL;DR — Speed run

For a re-run on Windows + RTX 3070 Ti, with the same dep stack we proved works:

```bash
# In WSL (Ubuntu 22.04 or 24.04)
cd ~/oww-train
source .venv-train/bin/activate
code .                              # opens VS Code Remote-WSL
# Then in VS Code: open notebooks/automatic_model_training.ipynb,
# pick the .venv-train kernel, run cells 1, 5, 8, 9, 10, 13, 14 (modified), 17.
# Stop after 17.
cp /mnt/c/Users/danto/.rex/wake_training/recordings/*/*.wav my_custom_model/positive_train/
# Run 18, 19. Done. ~30-40 min training on 3070 Ti.
cp my_custom_model/hey_rex.{onnx,tflite} /mnt/c/Users/danto/.rex/wake_models/
```

Total time on a re-run: **~1 hour** end-to-end (vs. 9 hours first time learning the gotchas).

---

## Critical pre-flight facts

These four facts will save you hours of debugging:

### 1. Python 3.10 specifically — not "3.10+"

The training pipeline pins `tensorflow-cpu==2.8.1` and uses `tensorflow-addons` (archived in 2024). Neither has Python 3.11+ wheels. **Python 3.12 is what Colab + Ubuntu 24.04 + Windows ship with by default**, and none of the training deps will resolve there.

You need Python 3.10 specifically. On Ubuntu, install via deadsnakes PPA. On native Windows, install from python.org (but you should be in WSL anyway — see #2).

### 2. WSL2 on Windows, not native Windows

`piper-phonemize` has **zero Windows wheels** anywhere — not on PyPI, not in the GitHub releases. Same problem on native macOS to a lesser degree. The training pipeline only works on Linux.

WSL2 with CUDA passthrough gives you a working Linux environment with full access to your NVIDIA GPU. Native Windows is a dead end for openWakeWord training.

### 3. Don't use Google Colab's notebook as-is

Colab now runs Python 3.12. The exact same blocker as native Windows — `piper-phonemize`, `tensorflow-cpu==2.8.1`, and `tensorflow-addons` won't install. Don't waste time on Colab unless you can pin Python 3.10 (which is itself painful).

### 4. The notebook's Cell 4 (Environment setup) will break your venv

The notebook was written for Colab's volatile environment where it can `pip install` from scratch every time. If you run Cell 4 in your own venv, it'll attempt to reinstall every dep, clobbering the carefully-pinned versions you set up. **Replace Cell 4 entirely** (the exact replacement is below).

---

## Phase 1 — One-time WSL setup

**Skip this phase if you already have `~/oww-train` set up from a previous run.**

```bash
# 1. Install Python 3.10 alongside whatever else you have
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3.10-dev build-essential

# 2. Clone openWakeWord
cd ~
git clone https://github.com/dscripka/openWakeWord.git oww-train
cd oww-train

# 3. Create the Python 3.10 venv
python3.10 -m venv .venv-train
source .venv-train/bin/activate
python --version    # confirm: Python 3.10.x

# 4. Install training deps in this exact order (with these exact pins)
pip install --upgrade pip
pip install -e .
pip install piper-phonemize webrtcvad
pip install mutagen==1.47.0 torchinfo==1.8.0 torchmetrics==1.2.0
pip install speechbrain==0.5.14
pip install audiomentations==0.33.0 torch-audiomentations==0.11.0
pip install acoustics==0.2.6
pip install --no-cache-dir tensorflow-cpu==2.8.1 tensorflow_probability==0.16.0
pip install onnx_tf==1.10.0 pronouncing==0.2.0
pip install datasets==2.14.6 deep-phonemizer==0.0.19
pip install jupyter notebook ipykernel

# 5. The dep-cascade fixes (CRITICAL — without these, nothing imports)
pip install protobuf==3.20.3      # TF 2.8 needs <3.20.x; pip pulls newer otherwise
pip install "numpy<2"             # TF 2.8 was compiled against numpy 1.x
pip install "onnx<1.15"           # newer onnx needs protobuf 4+, conflicts with TF 2.8
pip install "pyarrow<14"          # datasets 2.14.6 uses pa.PyExtensionType (removed in 14+)
```

The five cascade fixes (`protobuf`, `numpy`, `onnx`, `pyarrow`) are not in the original notebook. Pip's resolver pulls newer versions because they're transitively required, then TensorFlow 2.8 fails to import. Pin them yourself.

### Verify the venv

```bash
python -c "
import torch, tensorflow, piper_phonemize, audiomentations, speechbrain, onnx
print('torch:', torch.__version__, '| CUDA:', torch.cuda.is_available(),
      '| device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu')
print('tf:', tensorflow.__version__, '| numpy:', __import__('numpy').__version__, '| onnx:', onnx.__version__)
print('all imports: OK')
"
```

Expected output (the order/exact versions matter):

```
torch: 2.x.x+cu1xx | CUDA: True | device: NVIDIA GeForce RTX ...
tf: 2.8.1 | numpy: 1.26.4 | onnx: 1.14.1
all imports: OK
```

If `CUDA: False` here, force the CUDA torch build:

```bash
pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu124
```

---

## Phase 2 — Open the notebook in VS Code

**Don't use Jupyter via `localhost:8888`.** WSL2 localhost forwarding is flaky on some Windows configs and can hang for minutes. Use VS Code Remote-WSL instead, which talks to WSL through its own IPC channel.

```bash
# Still in (.venv-train) at ~/oww-train
code .
```

First time only: VS Code installs its server inside WSL (~30 sec). Then a Windows VS Code window opens, attached to WSL.

Inside VS Code:

1. **Install required extensions in WSL** (the prompt will ask): Python, Jupyter, Pylance, Python Environment. All from Microsoft. Make sure they install **"in WSL: Ubuntu"** not Windows-side.

2. **Open `notebooks/automatic_model_training.ipynb`**.

3. **Pick the kernel**: top-right kernel picker → "Select Another Kernel..." → "Python Environments..." → `~/oww-train/.venv-train/bin/python`.

4. **Run Cell 1** (`!nvidia-smi`). Should print your GPU. Confirms the kernel works.

If the kernel hangs at "Connecting to kernel..." for >30 seconds:
- Make sure the **Python extension** is installed in WSL (not just Jupyter — it can't drive a kernel alone).
- `Ctrl+Shift+P` → "Developer: Reload Window".

If your Ubuntu shell startup is slow (>2 sec):
- Check if `~/.bashrc` sources NVM. NVM init can hang for minutes on a freshly-OOM'd VM. Comment out the NVM lines.

---

## Phase 3 — Run the cells

### Cell 4 — Replace contents entirely

The original cell tries to `pip install` everything, breaking your pinned venv. Replace **the entire cell** with:

```python
import os, sys

if not os.path.exists("piper-sample-generator"):
    !git clone https://github.com/rhasspy/piper-sample-generator
    !wget -O piper-sample-generator/models/en_US-libritts_r-medium.pt 'https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt'
else:
    print("piper-sample-generator already cloned")

# Notebook later cells reference paths under './openwakeword/openwakeword/...'
# but we installed openwakeword from this directory directly. Symlink so paths resolve.
if not os.path.exists("openwakeword"):
    os.symlink(".", "openwakeword")
    print("symlinked ./openwakeword -> .")

# Resource models
os.makedirs("openwakeword/resources/models", exist_ok=True)
for fname in ["embedding_model.onnx", "embedding_model.tflite",
              "melspectrogram.onnx", "melspectrogram.tflite"]:
    target = f"openwakeword/resources/models/{fname}"
    if not os.path.exists(target):
        !wget -q https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/{fname} -O {target}
```

Run it. ~1-2 min (mostly the 200 MB Piper voice model download).

### Cells 5, 8, 9, 10 — Run as-is

Big downloads. Cell 9 will probably 404 on `bal_train09.tar` (HuggingFace pruned that file). **It's fine** — the cell continues to FMA, which carries the negative training data alongside Cell 10's ACAV100M features. AudioSet would slightly improve quality but it's not blocking.

Cell 10 downloads a **16 GB** ACAV100M precomputed-features file. Plan for ~10 min on a fast connection. This is the bulk of the negative training signal.

### Cell 14 — Replace contents (this is the config)

```python
config["target_phrase"] = ["hey rex"]
config["model_name"] = "hey_rex"
config["n_samples"] = 2000        # bump to 5000+ if you want a stronger model
config["n_samples_val"] = 500
config["steps"] = 10000
config["target_accuracy"] = 0.6
config["target_recall"] = 0.25

# Background paths — only fma since audioset 404'd. Training still works.
config["background_paths"] = ['./fma']
import os
if os.path.exists('./audioset_16k') and len(os.listdir('./audioset_16k')) > 0:
    config["background_paths"].insert(0, './audioset_16k')

config["false_positive_validation_data_path"] = "validation_set_features.npy"
config["feature_data_files"] = {"ACAV100M_sample": "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"}

with open('my_model.yaml', 'w') as file:
    yaml.dump(config, file)
print("Config saved. target_phrase:", config['target_phrase'])
```

### Cell 17 — Synthetic generation. Run as-is. ~10 min on 3070 Ti

Generates 2000 synthetic "hey rex" positives via Piper TTS. They land at `notebooks/my_model_data/positive_train/` (path may vary slightly — look at the cell output).

### **STOP AFTER CELL 17.** Merge real recordings before continuing.

```bash
# In a WSL terminal (outside VS Code is fine — this is just file copy)
# Adjust the destination path to match what Cell 17 actually printed.
cp /mnt/c/Users/danto/.rex/wake_training/recordings/*/*.wav \
   ~/oww-train/notebooks/my_model_data/positive_train/

# Verify
ls ~/oww-train/notebooks/my_model_data/positive_train/ | wc -l
# Should be ~2000 + however many real recordings you had (~329 = ~2329 total)
```

The training pipeline globs the folder, so dropping your real WAVs alongside the synthetic ones is enough. They'll go through the same RIR + background-noise augmentation as the synthetic ones in Cell 18.

### Cell 18 — Augmentation. Run as-is. ~5 min

Adds room-impulse-response and background noise to the positives. CPU-bound.

### Cell 19 — Train. Run as-is. ~25-40 min on 3070 Ti

The actual training. nvidia-smi will show ~1.5 GB VRAM use (training is small — most VRAM was used during Cell 17's Piper synthesis).

### Cell 20 — Optional .tflite fix

Skip unless training somehow fails to produce the .tflite alongside the .onnx.

### Output

```
~/oww-train/notebooks/my_custom_model/hey_rex.onnx     (~200 KB)
~/oww-train/notebooks/my_custom_model/hey_rex.tflite   (~200 KB)
```

---

## Phase 4 — Deploy to REX

```bash
# Copy to ~/.rex/wake_models/ on Windows side (REX picks them up there)
mkdir -p /mnt/c/Users/danto/.rex/wake_models
cp ~/oww-train/notebooks/my_custom_model/hey_rex.onnx \
   /mnt/c/Users/danto/.rex/wake_models/hey_rex.onnx
cp ~/oww-train/notebooks/my_custom_model/hey_rex.tflite \
   /mnt/c/Users/danto/.rex/wake_models/hey_rex.tflite
```

REX 1.0+ auto-resolves `model: hey_rex` in config — but if `~/.rex/wake_models/hey_rex.onnx` exists locally, it'll use the local file (overrides the HF auto-download). So this `cp` immediately swaps in your freshly-trained model.

To re-test:

```powershell
rex --wake-word
```

Should log `Loaded custom wake-word model: ...\hey_rex.onnx`.

### Pushing to Hugging Face (optional)

If the new model is better than the public one and you want the world to get it via REX's auto-download:

```bash
# In WSL or Windows, with huggingface_hub installed
python /mnt/c/Users/danto/OneDrive/Documents/python/rex_voice_assistant/_local/training/upload_to_hf.py
```

The upload script asks for a token at runtime; it overwrites the existing files in `GetToasted/rex-wake-words`. Existing REX users get the new model on their next run via HF's normal cache invalidation.

---

## Comprehensive troubleshooting

### Cell 5: `AttributeError: module 'pyarrow' has no attribute 'PyExtensionType'`

`datasets==2.14.6` was written against the old pyarrow API. Fix:

```bash
pip install "pyarrow<14"
```

Then **restart the kernel** in VS Code and re-run Cell 5.

### TF import: `Descriptors cannot be created directly` (protobuf error)

```bash
pip install protobuf==3.20.3
```

Restart kernel, retry.

### TF import: `module 'numpy' has no attribute '_ARRAY_API'`

NumPy 2.x is incompatible with TF 2.8.1 (compiled against 1.x). Fix:

```bash
pip install "numpy<2"
```

Restart kernel.

### TF + onnx conflict: `onnx 1.21.0 requires protobuf>=4.25.1, but you have protobuf 3.20.3`

```bash
pip install "onnx<1.15"
```

That's the last onnx compatible with the older protobuf. `onnx_tf` (which we need for the final ONNX export) still works.

### Cell 9: 404 on `bal_train09.tar`

HuggingFace pruned that file. **Ignore it.** The cell continues to FMA (which downloads fine) and Cell 10's ACAV100M features carry the bulk of negative signal. You'll have a slightly less diverse negative set; expect <1% accuracy delta.

If you want AudioSet anyway, try other tars (08, 07, 06, etc.) — at least one usually still exists.

### TF install gets killed mid-download

WSL VM OOM (Windows squeezed the 50% RAM share too aggressively). Fix permanently with `~/.wslconfig`:

```ini
[wsl2]
memory=12GB
swap=8GB
```

Then `wsl --shutdown` and reopen.

### WSL Ubuntu terminal won't open / hangs for minutes

Stale shell startup choking. Most likely cause: NVM in `.bashrc`. Open WSL via `wsl --` from PowerShell (which doesn't run interactive bashrc), then comment out the NVM block:

```bash
sed -i '/NVM/s/^/# /' ~/.bashrc
sed -i '/nvm.sh/s/^/# /' ~/.bashrc
```

Reopen Ubuntu — should start instantly.

### `localhost:8888` won't open in browser (Jupyter)

WSL2 localhost forwarding can be flaky. **Use VS Code Remote-WSL instead.** It bypasses localhost entirely. See Phase 2 above.

If you really want browser Jupyter, bind to the WSL IP:

```bash
jupyter notebook --ip=0.0.0.0 --no-browser
# then open http://<wsl-ip>:8888 — find the WSL IP via: hostname -I
```

### VS Code kernel hangs at "Connecting to kernel"

The Jupyter extension can't manage a kernel by itself. **Install the Python extension in WSL** (Ctrl+Shift+X → search "Python" by Microsoft → Install in WSL: Ubuntu). Reload window.

### `piper-phonemize` won't install on Windows

It can't. There are no Windows wheels for it, ever, anywhere. **Use WSL.** This is the entire reason WSL is required.

### `tensorflow-cpu==2.8.1` won't install

You're on Python 3.11+. There are no `tensorflow-cpu==2.8.1` wheels for 3.11 or 3.12. **Use Python 3.10 specifically** via deadsnakes PPA.

### Training stalls or errors mid-run

Probably an OOM in Piper TTS during synthetic generation. Reduce `n_samples` from 2000 to 1000 in Cell 14 and re-run from Cell 17.

### Model produces too many false fires

Either:
1. Raise threshold from 0.5 → 0.6 → 0.7 in your REX config (no retraining needed)
2. Retrain with `n_samples` bumped to 5000+ and/or another batch of real "hey rex" samples (the more contributors, the better generalization)
3. Add false-positive validation data: collect ~30 min of audio that includes the false-fire sources (TV, podcasts, gaming) and point `false_positive_validation_data_path` at it before retraining

### Model never fires on real "hey rex"

Threshold too high (drop to 0.4), or the trained model is genuinely bad (need more positive samples). The model card metrics (`target_accuracy: 0.6`, `target_recall: 0.25`) are deliberately modest — real-world threshold is empirical.

---

## Hardware notes

Tested working configs:

- **WSL2 Ubuntu 24.04 + RTX 3070 Ti (8 GB VRAM)** ← reference setup
- Should also work on Ubuntu native, WSL2 Ubuntu 22.04, any NVIDIA GPU 6GB+
- **Will NOT work** on: Native Windows, Native macOS, AMD GPUs, Python 3.11+, Colab default runtime

Time on a 3070 Ti via WSL2:

| Phase | Time |
|-------|------|
| Cell 4 (env setup) | ~1 min |
| Cell 5-10 (downloads, ~22 GB) | 15-25 min |
| Cell 17 (synthetic gen, 2000 clips) | ~10 min |
| Recording merge | <1 min |
| Cell 18 (augmentation) | ~5 min |
| Cell 19 (training, 10000 steps) | 25-40 min |
| Cell 20 + export | <1 min |
| **Total** | **~60-80 min** |

Most of that is downloads on the first run. Subsequent runs (with deps already pinned and datasets cached) are ~40-50 min for everything from Cell 13 to deploy.

---

## What we knew going in vs. what we learned

| What we thought | What was actually true |
|-----------------|------------------------|
| "Python 3.10+" works | Python 3.10 specifically; 3.11+ breaks |
| "Just install pinned deps" | Five additional cascade pins are needed (protobuf, numpy, onnx, pyarrow, sometimes torch CUDA) |
| Native Windows is fine | Native Windows is dead; WSL2 is required |
| Colab is the easy backup | Colab moved to Python 3.12; same blockers |
| Jupyter localhost forwards from WSL2 | Sometimes; VS Code Remote-WSL bypasses it cleanly |
| Cell 4 just sets up the env | Cell 4 will clobber your venv if not replaced |
| `bal_train09.tar` is the seed dataset | It 404'd; FMA + ACAV100M carry training fine |
| Recordings go in some special folder | They go alongside the synthetic positives, the pipeline globs |
| Training takes ~30 min | First run takes ~9 hours (mostly fighting deps); reruns ~1 hour |
