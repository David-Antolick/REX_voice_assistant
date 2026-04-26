"""One-shot uploader for hey_rex wake-word model to Hugging Face.

Run with: python upload_to_hf.py

Will prompt for the HF token (token won't echo as you type).
Creates the repo if it doesn't exist, then uploads the model files + README.
"""

import getpass
import sys
from pathlib import Path

from huggingface_hub import HfApi, login

REPO_ID = "GetToasted/rex-wake-words"
WAKE_MODELS_DIR = Path.home() / ".rex" / "wake_models"

README = """\
# REX Wake-Word Models

Custom wake-word models for the [REX voice assistant](https://github.com/David-Antolick/rex_voice_assistant).

## Models

- **hey_rex.onnx** - openWakeWord model for the phrase "hey rex".
- **hey_rex.tflite** - same model, TFLite runtime version.

Trained with the openWakeWord automatic training pipeline:
- 2000 Piper TTS synthetic positives + 329 real recordings from 3 speakers
- Background data: FMA music + ACAV100M precomputed features (~2000 hrs)
- 10,000 training steps on an RTX 3070 Ti

## Usage

REX 0.3.2+ downloads this model automatically when wake-word gating is enabled.

For direct use with openWakeWord:

```python
from openwakeword.model import Model
oww = Model(wakeword_models=["hey_rex.onnx"], inference_framework="onnx")
score = oww.predict(audio_int16_chunk_1280_samples)["hey_rex"]
```

Audio format: 16 kHz mono int16, fed in chunks of 1280 samples (80 ms).
Default detection threshold: 0.5 (raise to ~0.7 to reduce false fires).

## License

MIT, matching the REX project. Voice samples used in training were collected
with explicit consent from contributors for inclusion in this model and its
distribution (including any commercial distribution, though none is intended).
"""


def main() -> int:
    onnx_path = WAKE_MODELS_DIR / "hey_rex.onnx"
    tflite_path = WAKE_MODELS_DIR / "hey_rex.tflite"

    for p in (onnx_path, tflite_path):
        if not p.exists():
            print(f"ERROR: {p} not found.", file=sys.stderr)
            return 1

    print(f"About to upload to: https://huggingface.co/{REPO_ID}")
    print(f"  - {onnx_path} ({onnx_path.stat().st_size:,} bytes)")
    print(f"  - {tflite_path} ({tflite_path.stat().st_size:,} bytes)")
    print()

    token = getpass.getpass("Paste HF token (won't echo): ").strip()
    if not token.startswith("hf_"):
        print("ERROR: that doesn't look like an HF token (should start with hf_).", file=sys.stderr)
        return 1

    login(token=token)
    api = HfApi()

    print("Creating repo (no-op if it already exists)...")
    api.create_repo(repo_id=REPO_ID, exist_ok=True, repo_type="model", private=False)

    print("Uploading hey_rex.onnx...")
    api.upload_file(
        path_or_fileobj=str(onnx_path),
        path_in_repo="hey_rex.onnx",
        repo_id=REPO_ID,
    )

    print("Uploading hey_rex.tflite...")
    api.upload_file(
        path_or_fileobj=str(tflite_path),
        path_in_repo="hey_rex.tflite",
        repo_id=REPO_ID,
    )

    print("Uploading README.md...")
    api.upload_file(
        path_or_fileobj=README.encode("utf-8"),
        path_in_repo="README.md",
        repo_id=REPO_ID,
    )

    print()
    print(f"Done! Public URL: https://huggingface.co/{REPO_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
