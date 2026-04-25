"""recorder.py
Guided recorder for collecting wake-word training samples.

Used by `rex record-wake-samples`. Loops `count` times, plays a get-ready cue,
records ~2 seconds, validates levels, and saves int16 mono WAVs at 16 kHz to
the openWakeWord-compatible folder layout.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import time
import zipfile
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

from rex_main.config import CONFIG_DIR
from rex_main.wake_word import play_wake_cue

logger = logging.getLogger(__name__)

__all__ = ["record_wake_samples"]


SAMPLE_RATE = 16_000
RECORD_SECONDS = 2.0
PEAK_MIN = 0.02   # Below = silence/whisper, reject
PEAK_MAX = 0.99   # Above = clipping, reject
PRE_RECORD_PAUSE_S = 0.45


def _record_one(device: Optional[str | int]) -> np.ndarray:
    """Record RECORD_SECONDS of mono float32 audio at SAMPLE_RATE."""
    frames = int(SAMPLE_RATE * RECORD_SECONDS)
    audio = sd.rec(
        frames,
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    return audio[:, 0]


def _trim_silence(audio: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    """Trim leading/trailing silence below threshold, keeping a small head/tail pad."""
    abs_audio = np.abs(audio)
    above = np.where(abs_audio > threshold)[0]
    if len(above) == 0:
        return audio
    pad = int(0.1 * SAMPLE_RATE)  # 100 ms pad
    start = max(0, above[0] - pad)
    end = min(len(audio), above[-1] + pad)
    return audio[start:end]


def _level_check(audio: np.ndarray) -> tuple[bool, str, float, float]:
    """Returns (ok, reason_if_bad, peak, rms)."""
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio**2)))
    if peak < PEAK_MIN:
        return False, f"too quiet (peak {peak:.3f} < {PEAK_MIN})", peak, rms
    if peak > PEAK_MAX:
        return False, f"clipped (peak {peak:.3f} > {PEAK_MAX})", peak, rms
    return True, "", peak, rms


def _save_wav(path: Path, audio: np.ndarray) -> None:
    pcm_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    sf.write(str(path), pcm_int16, SAMPLE_RATE, subtype="PCM_16")


def _next_index(out_dir: Path, prefix: str) -> int:
    existing = sorted(out_dir.glob(f"{prefix}_*.wav"))
    if not existing:
        return 1
    last = existing[-1].stem  # e.g. hey_rex_007
    try:
        return int(last.rsplit("_", 1)[-1]) + 1
    except ValueError:
        return len(existing) + 1


def _safe_contributor(name: str) -> str:
    """Slug a contributor name to a folder-safe identifier."""
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip().lower())
    return slug or "anonymous"


def record_wake_samples(
    count: int = 100,
    phrase: str = "hey rex",
    output_dir: Optional[Path] = None,
    device: Optional[str | int] = None,
    contributor: Optional[str] = None,
) -> int:
    """Run an interactive recording session. Returns number of samples saved.

    When `contributor` is set, samples land in a subfolder named after them,
    so multiple people's recordings can be merged later without filename clashes.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt

    console = Console()

    if contributor is None:
        # Default to OS username, allow override.
        default_name = os.environ.get("USERNAME") or os.environ.get("USER") or "anonymous"
        contributor = Prompt.ask(
            "Your name (used to label your recordings)",
            default=default_name,
        )
    contributor_slug = _safe_contributor(contributor)

    if output_dir is None:
        output_dir = CONFIG_DIR / "wake_training" / "recordings" / contributor_slug
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    prefix = phrase.strip().lower().replace(" ", "_")
    start_idx = _next_index(output_dir, prefix)

    console.print(Panel.fit(
        f"[bold blue]Wake-word recording session[/bold blue]\n\n"
        f"Contributor: [cyan]{contributor}[/cyan]\n"
        f"Phrase: [cyan]'{phrase}'[/cyan]\n"
        f"Target: [cyan]{count}[/cyan] samples\n"
        f"Saving to: [cyan]{output_dir}[/cyan]\n"
        f"Starting at index: [cyan]{start_idx:03d}[/cyan]\n\n"
        f"[bold]Tips for good samples:[/bold]\n"
        f"  - Vary your tone, pace, and volume\n"
        f"  - Move around (close to mic, farther away)\n"
        f"  - Record in different environments if you can\n"
        f"  - A few whispered + a few loud is fine — variety wins\n"
        f"  - The tool will reject silence and clipping; just retry",
        border_style="blue",
    ))

    if not Confirm.ask("\nReady to start?", default=True):
        console.print("[yellow]Cancelled.[/yellow]")
        return 0

    saved = 0
    levels: list[tuple[float, float]] = []  # (peak, rms)

    try:
        for i in range(count):
            idx = start_idx + saved
            console.print(f"\n[bold][ {i + 1:>3} / {count} ][/bold]  next file: [dim]{prefix}_{idx:03d}.wav[/dim]")
            console.print(f"  Get ready... say [cyan]'{phrase}'[/cyan] when you hear the tone")

            time.sleep(PRE_RECORD_PAUSE_S)
            play_wake_cue()
            time.sleep(0.15)  # let the cue finish before recording

            console.print("  [yellow]Recording...[/yellow]")
            audio = _record_one(device=device)
            audio = _trim_silence(audio)

            ok, reason, peak, rms = _level_check(audio)
            if not ok:
                console.print(f"  [red]Rejected:[/red] {reason}. Retrying this slot.")
                # Don't increment saved; retry this index
                continue

            out_path = output_dir / f"{prefix}_{idx:03d}.wav"
            _save_wav(out_path, audio)
            levels.append((peak, rms))
            saved += 1
            console.print(f"  [green]Saved[/green] (peak={peak:.2f}, rms={rms:.3f})")

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")

    # Write session notes
    if saved:
        notes_path = output_dir / "notes.md"
        peaks = [p for p, _ in levels]
        rmss = [r for _, r in levels]
        with open(notes_path, "a", encoding="utf-8") as f:
            f.write(f"\n## Session {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- Contributor: `{contributor}`\n")
            f.write(f"- Phrase: `{phrase}`\n")
            f.write(f"- Saved: {saved} files (indices {start_idx:03d}-{start_idx + saved - 1:03d})\n")
            f.write(f"- Peak levels: min={min(peaks):.2f} avg={np.mean(peaks):.2f} max={max(peaks):.2f}\n")
            f.write(f"- RMS levels: min={min(rmss):.3f} avg={np.mean(rmss):.3f} max={max(rmss):.3f}\n")
            f.write(f"- Output dir: `{output_dir}`\n")

    console.print(Panel.fit(
        f"[bold green]Session complete[/bold green]\n\n"
        f"Saved [cyan]{saved}[/cyan] sample(s) to:\n  [cyan]{output_dir}[/cyan]\n\n"
        f"Total recordings now: [cyan]{len(list(output_dir.glob(f'{prefix}_*.wav')))}[/cyan]\n\n"
        f"Next: run [cyan]rex package-wake-samples[/cyan] to bundle them for sending.",
        border_style="green",
    ))

    return saved


def _detect_microphone_name() -> str:
    """Best-effort guess at the default mic name for the manifest."""
    try:
        default_input = sd.default.device[0]
        if default_input is not None and default_input >= 0:
            return sd.query_devices(default_input).get("name", "unknown")
    except Exception:
        pass
    return "unknown"


def package_wake_samples(
    contributor: Optional[str] = None,
    phrase: str = "hey rex",
    recordings_dir: Optional[Path] = None,
    output_zip: Optional[Path] = None,
) -> Optional[Path]:
    """Zip a contributor's recordings + a manifest for submission.

    Returns the path to the produced zip, or None if nothing was packaged.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt

    console = Console()

    if recordings_dir is None:
        recordings_dir = CONFIG_DIR / "wake_training" / "recordings"
    recordings_dir = Path(recordings_dir).expanduser()

    if not recordings_dir.exists():
        console.print(f"[red]No recordings folder found at {recordings_dir}.[/red]")
        console.print("Run [cyan]rex record-wake-samples[/cyan] first.")
        return None

    # Pick the contributor folder.
    candidates = [p for p in recordings_dir.iterdir() if p.is_dir()]
    if not candidates:
        # Legacy flat layout (pre-contributor support) - treat the whole dir as one batch.
        candidates = [recordings_dir]

    if contributor is None:
        if len(candidates) == 1:
            contributor_folder = candidates[0]
        else:
            console.print("\n[bold]Pick a contributor folder to package:[/bold]")
            for i, p in enumerate(candidates, start=1):
                wav_count = len(list(p.glob("*.wav")))
                console.print(f"  [cyan]{i}[/cyan]) {p.name} ({wav_count} samples)")
            choice = int(Prompt.ask(
                "Select",
                choices=[str(i) for i in range(1, len(candidates) + 1)],
                default="1",
            ))
            contributor_folder = candidates[choice - 1]
    else:
        slug = _safe_contributor(contributor)
        contributor_folder = recordings_dir / slug
        if not contributor_folder.exists():
            console.print(f"[red]No recordings found for contributor '{contributor}' at {contributor_folder}.[/red]")
            return None

    contributor_name = contributor_folder.name
    wavs = sorted(contributor_folder.glob("*.wav"))
    if not wavs:
        console.print(f"[red]No .wav files in {contributor_folder}.[/red]")
        return None

    # Collect level stats for the manifest.
    peaks: list[float] = []
    rmss: list[float] = []
    for wav in wavs:
        try:
            data, sr = sf.read(str(wav), dtype="float32")
            if data.ndim > 1:
                data = data[:, 0]
            peaks.append(float(np.max(np.abs(data))))
            rmss.append(float(np.sqrt(np.mean(data**2))))
        except Exception:
            continue

    try:
        from rex_main import __version__ as rex_version  # if exposed
    except Exception:
        rex_version = "unknown"

    manifest = {
        "contributor": contributor_name,
        "phrase": phrase,
        "sample_count": len(wavs),
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "microphone": _detect_microphone_name(),
        "rex_version": rex_version,
        "level_stats": {
            "peak_min": round(min(peaks), 3) if peaks else None,
            "peak_avg": round(float(np.mean(peaks)), 3) if peaks else None,
            "peak_max": round(max(peaks), 3) if peaks else None,
            "rms_min": round(min(rmss), 4) if rmss else None,
            "rms_avg": round(float(np.mean(rmss)), 4) if rmss else None,
            "rms_max": round(max(rmss), 4) if rmss else None,
        },
    }

    if output_zip is None:
        date = time.strftime("%Y%m%d")
        output_zip = CONFIG_DIR / "wake_training" / f"{contributor_name}_hey_rex_{date}.zip"
    output_zip = Path(output_zip).expanduser()
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for wav in wavs:
            zf.write(wav, arcname=f"{contributor_name}/{wav.name}")
        # Include any session notes if present.
        notes = contributor_folder / "notes.md"
        if notes.exists():
            zf.write(notes, arcname=f"{contributor_name}/notes.md")
        zf.writestr(f"{contributor_name}/manifest.json", json.dumps(manifest, indent=2))

    size_mb = output_zip.stat().st_size / 1_000_000

    console.print(Panel.fit(
        f"[bold green]Package ready[/bold green]\n\n"
        f"Contributor: [cyan]{contributor_name}[/cyan]\n"
        f"Samples: [cyan]{len(wavs)}[/cyan]\n"
        f"Size: [cyan]{size_mb:.1f} MB[/cyan]\n\n"
        f"File:\n  [cyan]{output_zip}[/cyan]\n\n"
        f"Send this single .zip to the person who asked you to record\n"
        f"(Discord, email, Drive — whatever's easiest).",
        border_style="green",
    ))

    return output_zip
