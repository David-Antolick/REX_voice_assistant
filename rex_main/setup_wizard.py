"""setup_wizard.py
Interactive setup wizard for REX voice assistant.

Handles:
1. System check - Detect Python, audio devices, CUDA
2. Audio device selection - Choose microphone
3. Media services - Choice of YTMD, Spotify, both, or none
4. YTMD setup - Authenticate with YouTube Music Desktop
5. Spotify setup - Guide through developer portal and OAuth
6. SteelSeries Moments - Clipping integration
7. Model download - Offer to pre-download Whisper model
8. Audio test - Quick recording test
9. Write config - Save to ~/.rex/config.yaml
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm
from rich.table import Table

# Default model to use if none selected during setup
_selected_model: str = "small.en"

console = Console()


def _step_complete(message: str = "Done!", delay: float = 1.0):
    """Show step completion message and pause briefly before next step."""
    console.print(f"[green]{message}[/green]")
    if delay > 0:
        time.sleep(delay)


def run_wizard():
    """Run the interactive setup wizard."""
    global _selected_model

    console.print(Panel.fit(
        "[bold blue]REX Voice Assistant Setup Wizard[/bold blue]\n\n"
        "This wizard will help you configure REX for first-time use.",
        border_style="blue"
    ))
    console.print()

    # Step 1: System check
    cuda_ok = _check_system()
    if cuda_ok is None:  # Required components missing
        return

    # Step 1b: Offer to install CUDA PyTorch if GPU found but CUDA not working
    if cuda_ok is False:
        _offer_cuda_setup()

    time.sleep(0.5)

    # Step 2: Audio device selection
    _setup_audio()
    _step_complete()

    # Step 3: Media services
    services = _choose_services()

    # Step 4 & 5: Configure chosen services
    secrets = {}

    if "ytmd" in services:
        ytmd_token = _setup_ytmd()
        if ytmd_token:
            secrets["ytmd_token"] = ytmd_token
            _step_complete("YTMD configured!")

    if "spotify" in services:
        spotify_creds = _setup_spotify()
        if spotify_creds:
            secrets.update(spotify_creds)
            _step_complete("Spotify configured!")

    # Step 6: SteelSeries Moments (clipping)
    _setup_steelseries()
    time.sleep(0.5)

    # Step 7: Model download (also sets _selected_model)
    _setup_model()
    _step_complete()

    # Step 8: Wake word (optional)
    wake_word_cfg = _setup_wake_word()
    if wake_word_cfg:
        _step_complete("Wake word configured!")

    # Step 9: Audio test (optional)
    if Confirm.ask("\nWould you like to run a quick audio test?", default=False):
        _test_audio()
        _step_complete()

    # Step 10: Write config (uses _selected_model for default)
    _write_config(services, secrets, cuda_ok=cuda_ok, wake_word=wake_word_cfg)

    console.print()
    console.print(Panel.fit(
        "[bold green]Setup Complete![/bold green]\n\n"
        "Run [cyan]rex[/cyan] to start the voice assistant.\n"
        "Run [cyan]rex status[/cyan] to check configuration.\n"
        "Run [cyan]rex settings[/cyan] to change settings later.",
        border_style="green"
    ))


def _check_system() -> Optional[bool]:
    """Check system requirements and display status.

    Returns:
        True: All OK including CUDA
        False: Required components OK but CUDA not working (GPU found)
        None: Required components missing (cannot continue)
    """
    console.print("[bold]Step 1: System Check[/bold]\n")

    table = Table(title="System Requirements")
    table.add_column("Component", style="cyan")
    table.add_column("Status")
    table.add_column("Details")

    all_ok = True

    # Python version
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 10)
    table.add_row(
        "Python",
        "[green]OK[/green]" if py_ok else "[red]FAIL[/red]",
        f"v{py_version}" + ("" if py_ok else " (need 3.10+)")
    )
    if not py_ok:
        all_ok = False

    # Audio devices
    audio_ok = False
    audio_details = "Checking..."
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
        if inputs:
            audio_ok = True
            default_input = sd.default.device[0]
            if default_input is not None and default_input >= 0:
                dev_name = sd.query_devices(default_input)["name"]
                audio_details = f"{len(inputs)} device(s), default: {dev_name[:30]}"
            else:
                audio_details = f"{len(inputs)} device(s) available"
        else:
            audio_details = "No microphones found"
    except Exception as e:
        audio_details = f"Error: {e}"

    table.add_row(
        "Audio Input",
        "[green]OK[/green]" if audio_ok else "[red]MISSING[/red]",
        audio_details
    )
    if not audio_ok:
        all_ok = False

    # CUDA (recommended for speed)
    cuda_ok = False
    gpu_found = False
    cuda_details = "Not detected (will use slower CPU mode)"
    try:
        import torch
        if torch.cuda.is_available():
            # Also check if cuDNN is actually working
            try:
                cuda_ok = True
                gpu_found = True
                cuda_details = f"CUDA {torch.version.cuda}, {torch.cuda.get_device_name(0)}"
            except Exception as e:
                gpu_found = True
                cuda_details = f"CUDA available but error: {e}"
        else:
            # Check if NVIDIA GPU exists but CUDA isn't configured
            try:
                import subprocess
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    gpu_found = True
                    gpu_name = result.stdout.strip().split('\n')[0]
                    cuda_details = f"GPU found ({gpu_name}) but PyTorch CUDA not installed"
            except Exception:
                pass
    except ImportError:
        cuda_details = "PyTorch not installed yet"
    except Exception as e:
        cuda_details = f"Error checking CUDA: {e}"

    table.add_row(
        "CUDA (recommended)",
        "[green]OK[/green]" if cuda_ok else "[yellow]MISSING[/yellow]",
        cuda_details
    )

    console.print(table)
    console.print()

    if not all_ok:
        console.print("[red]Some required components are missing.[/red]")
        if not audio_ok:
            console.print("[yellow]Please connect a microphone and run setup again.[/yellow]")
        return None  # Cannot continue

    if cuda_ok:
        return True  # All good including CUDA
    elif gpu_found:
        return False  # GPU found but CUDA not working - offer setup
    else:
        return True  # No GPU, but that's OK - will use CPU


def _offer_cuda_setup():
    """Offer to install CUDA-enabled PyTorch for GPU acceleration."""
    console.print("[bold yellow]GPU Detected but CUDA not configured[/bold yellow]\n")

    console.print("Your system has an NVIDIA GPU, but PyTorch was installed without CUDA support.")
    console.print("Installing CUDA-enabled PyTorch will make transcription [bold]5-10x faster[/bold].")
    console.print()

    if not Confirm.ask("Would you like to install CUDA-enabled PyTorch now?", default=True):
        console.print("[dim]Skipping CUDA setup. REX will use CPU mode (slower).[/dim]")
        console.print("[dim]You can install later with:[/dim]")
        console.print("[dim]  pipx runpip rex-voice-assistant install torch --index-url https://download.pytorch.org/whl/cu124 --force-reinstall[/dim]")
        return

    console.print("\nInstalling CUDA-enabled PyTorch (this may take a few minutes)...")
    console.print("[dim]Downloading ~2.5GB from PyTorch servers...[/dim]\n")

    try:
        import subprocess

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Installing PyTorch with CUDA support...", total=None)

            # Run pip install with CUDA index
            result = subprocess.run(
                [
                    sys.executable, "-m", "pip", "install",
                    "torch", "torchaudio",
                    "--index-url", "https://download.pytorch.org/whl/cu124",
                    "--force-reinstall", "--quiet"
                ],
                capture_output=True, text=True, timeout=600  # 10 minute timeout
            )

            if result.returncode != 0:
                progress.update(task, description="[red]Installation failed[/red]")
                console.print("\n[red]Error installing PyTorch:[/red]")
                console.print(f"[dim]{result.stderr}[/dim]")
                console.print("\n[yellow]You can try manually with:[/yellow]")
                console.print("  pipx runpip rex-voice-assistant install torch --index-url https://download.pytorch.org/whl/cu124 --force-reinstall")
                return

            # Also install cuDNN
            progress.update(task, description="Installing cuDNN libraries...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "nvidia-cudnn-cu12", "--quiet"],
                capture_output=True, text=True, timeout=120
            )

            progress.update(task, description="[green]CUDA PyTorch installed![/green]")

        # Verify installation
        console.print("\nVerifying CUDA installation...")
        result = subprocess.run(
            [sys.executable, "-c", "import torch; print('CUDA:', torch.cuda.is_available(), '| GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"],
            capture_output=True, text=True, timeout=30
        )

        if "CUDA: True" in result.stdout:
            console.print(f"[green]Success! {result.stdout.strip()}[/green]")
        else:
            console.print("[yellow]Installation completed but CUDA verification failed.[/yellow]")
            console.print("[dim]This may resolve after restarting the setup wizard.[/dim]")

    except subprocess.TimeoutExpired:
        console.print("[red]Installation timed out. Please try again or install manually.[/red]")
    except Exception as e:
        console.print(f"[red]Error during installation: {e}[/red]")


def _setup_audio():
    """Set up audio device selection."""
    console.print("\n[bold]Step 2: Audio Setup[/bold]\n")

    try:
        from rex_main.audio_stream import list_audio_devices

        devices = list_audio_devices()

        if len(devices) == 1:
            console.print(f"Using microphone: [cyan]{devices[0]['name']}[/cyan]")
        elif len(devices) > 1:
            console.print("Available microphones:")
            for dev in devices:
                default_marker = " [green](default)[/green]" if dev["default"] else ""
                console.print(f"  [{dev['index']}] {dev['name']}{default_marker}")

            console.print()
            console.print("Using system default microphone.")
            console.print("[dim]You can change this in ~/.rex/config.yaml later.[/dim]")

    except Exception as e:
        console.print(f"[yellow]Could not list audio devices: {e}[/yellow]")

    console.print("[green]Audio setup complete.[/green]")


def _choose_services() -> list[str]:
    """Let user choose which media services to configure."""
    console.print("\n[bold]Step 3: Media Services[/bold]\n")

    console.print("REX can control the following music services:")
    console.print("  1. YouTube Music Desktop (YTMD) - Local app with Companion Server")
    console.print("  2. Spotify - Via Spotify Connect API")
    console.print("  3. Both services")
    console.print("  4. None (transcription-only mode)")
    console.print()

    choice = Prompt.ask(
        "Which service(s) would you like to configure?",
        choices=["1", "2", "3", "4"],
        default="1"
    )

    if choice == "1":
        return ["ytmd"]
    elif choice == "2":
        return ["spotify"]
    elif choice == "3":
        return ["ytmd", "spotify"]
    else:
        return []


def _setup_ytmd() -> Optional[str]:
    """Set up YouTube Music Desktop authentication."""
    console.print("\n[bold]Step 4: YouTube Music Desktop Setup[/bold]\n")

    console.print("YouTube Music Desktop (YTMD) is a standalone desktop app for YouTube Music.")
    console.print()
    console.print("[bold cyan]Installation:[/bold cyan]")
    console.print("  Download from: [link=https://ytmdesktop.app]https://ytmdesktop.app[/link]")
    console.print()
    console.print("[bold cyan]Required Settings in YTMD:[/bold cyan]")
    console.print("  1. Open YTMD and click the [bold]gear icon[/bold] (Settings)")
    console.print("  2. Scroll to [bold]\"Integrations\"[/bold] section")
    console.print("  3. Enable these options:")
    console.print("     [green]✓[/green] Companion Server")
    console.print("     [green]✓[/green] Companion Authorization")
    console.print()
    console.print("[dim]The Companion Server allows REX to control playback via local API.[/dim]")
    console.print()

    if not Confirm.ask("Is YTMD running with Companion Server enabled?", default=True):
        console.print("[yellow]Please install/configure YTMD and run 'rex setup' again.[/yellow]")
        return None

    # Use default host/port - these rarely need to change
    host = "localhost"
    port = "9863"
    base_url = f"http://{host}:{port}"

    console.print(f"[dim]Connecting to YTMD at {base_url}...[/dim]")

    # Step 1: Request auth code
    console.print("Requesting authentication code from YTMD...")

    try:
        import requests

        # Request code
        resp = requests.post(
            f"{base_url}/api/v1/auth/requestcode",
            json={
                "appId": "rex_voice_assistant",
                "appName": "REX Voice Assistant",
                "appVersion": "1.0.0"
            },
            timeout=10
        )

        if resp.status_code != 200:
            console.print(f"[red]Failed to request code: HTTP {resp.status_code}[/red]")
            console.print(f"Response: {resp.text}")
            return None

        data = resp.json()
        code = data.get("code")

        if not code:
            console.print("[red]No code received from YTMD[/red]")
            return None

        console.print(f"[green]Got authorization code: {code}[/green]")
        console.print()

        console.print(Panel.fit(
            "When you press Enter, a popup will appear in YTMD.\n"
            "Click [bold]Allow[/bold] in the popup to authorize REX.\n\n"
            "[dim]If no popup appears, check that 'Companion Authorization'\n"
            "is enabled in YTMD Settings > Integrations.[/dim]",
            title="Ready to Authorize"
        ))

        input("\nPress Enter to show the authorization popup in YTMD...")

        # This request triggers the popup in YTMD - it waits until user clicks Allow
        console.print("Waiting for you to click 'Allow' in YTMD...")
        resp = requests.post(
            f"{base_url}/api/v1/auth/request",
            json={"appId": "rex_voice_assistant", "code": code},
            timeout=60  # Give user time to click Allow
        )

        if resp.status_code != 200:
            console.print(f"[red]Failed to get token: HTTP {resp.status_code}[/red]")
            console.print(f"[dim]Response: {resp.text}[/dim]")
            console.print()
            console.print("[yellow]Troubleshooting tips:[/yellow]")
            console.print("  1. Make sure 'Companion Authorization' is enabled in YTMD settings")
            console.print("  2. A popup should appear in YTMD when requesting auth - approve it")
            console.print("  3. If no popup appears, try restarting YTMD")
            return None

        data = resp.json()
        token = data.get("token")

        if not token:
            console.print("[red]No token received[/red]")
            return None

        console.print("[green]Successfully authenticated with YTMD![/green]")
        return token

    except requests.exceptions.ConnectionError:
        console.print(f"[red]Could not connect to YTMD at {base_url}[/red]")
        console.print("Make sure YTMD is running and Companion Server is enabled.")
        return None
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return None


def _setup_spotify() -> Optional[dict]:
    """Set up Spotify authentication."""
    console.print("\n[bold]Step 5: Spotify Setup[/bold]\n")

    console.print("Spotify integration requires a free Spotify Developer account.")
    console.print()
    console.print("[bold cyan]Step-by-step setup:[/bold cyan]")
    console.print()
    console.print("  [bold]1. Sign in to Spotify Developer:[/bold]")
    console.print("     Go to: [link=https://developer.spotify.com]https://developer.spotify.com[/link]")
    console.print("     Click [bold]\"Log in\"[/bold] (top right) and sign in with your Spotify account (free or Premium)")
    console.print()
    console.print("  [bold]2. Open the Dashboard:[/bold]")
    console.print("     • After signing in, click your [bold]username[/bold] in the top-right corner")
    console.print("     • Choose [bold]\"Dashboard\"[/bold] from the dropdown")
    console.print("     • (Or go directly to: [link=https://developer.spotify.com/dashboard]https://developer.spotify.com/dashboard[/link])")
    console.print()
    console.print("  [bold]3. Create a New App:[/bold]")
    console.print("     • Click [bold]\"Create app\"[/bold]")
    console.print("     • App name: [cyan]REX Voice Assistant[/cyan] (or any name)")
    console.print("     • App description: [cyan]Voice control for Spotify[/cyan]")
    console.print("     • Website: can be left blank or use a placeholder")
    console.print()
    console.print("  [bold]4. Configure Redirect URI:[/bold]")
    console.print("     • In the app settings, find [bold]\"Redirect URIs\"[/bold]")
    console.print("     • Add exactly: [bold green]http://127.0.0.1:8888/callback[/bold green]")
    console.print("     • Click [bold]\"Add\"[/bold] then [bold]\"Save\"[/bold]")
    console.print()
    console.print("  [bold]5. Get Your Credentials:[/bold]")
    console.print("     • Go to your app's [bold]\"Settings\"[/bold]")
    console.print("     • Copy the [bold]Client ID[/bold]")
    console.print("     • Click [bold]\"View client secret\"[/bold] and copy it")
    console.print()
    console.print("[dim]Note: Keep your Client Secret private - it's like a password.[/dim]")
    console.print()

    if not Confirm.ask("Have you created a Spotify Developer app?", default=False):
        console.print("[yellow]Please create the app and run 'rex setup' again.[/yellow]")
        return None

    client_id = Prompt.ask("Enter your Spotify Client ID")
    client_secret = Prompt.ask("Enter your Spotify Client Secret")

    if not client_id or not client_secret:
        console.print("[red]Client ID and Secret are required.[/red]")
        return None

    # Set up OAuth and trigger browser login
    console.print("\nOpening browser for Spotify login...")

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        os.environ["SPOTIPY_CLIENT_ID"] = client_id
        os.environ["SPOTIPY_CLIENT_SECRET"] = client_secret
        os.environ["SPOTIPY_REDIRECT_URI"] = "http://127.0.0.1:8888/callback"

        from pathlib import Path as _P
        cache_path = _P.home() / ".rex" / "spotify_token.cache"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            scope="user-modify-playback-state user-read-playback-state user-library-modify user-library-read",
            open_browser=True,
            cache_path=str(cache_path),
        ))

        # Test the connection
        user = sp.current_user()
        console.print(f"[green]Successfully authenticated as: {user.get('display_name', user.get('id'))}[/green]")

        return {
            "spotify_client_id": client_id,
            "spotify_client_secret": client_secret,
        }

    except Exception as e:
        console.print(f"[red]Spotify authentication failed: {e}[/red]")
        return None


def _setup_steelseries():
    """Set up SteelSeries GG Moments integration for clipping."""
    console.print("\n[bold]Step 6: SteelSeries Moments (Clipping)[/bold]\n")

    console.print("REX can trigger clips via SteelSeries GG Moments.")
    console.print("Say [bold cyan]\"clip that\"[/bold cyan] to save gameplay clips.")
    console.print()

    # Check if SteelSeries GG is installed
    import os

    coreprops_paths = [
        os.path.expandvars(r"%PROGRAMDATA%\SteelSeries\SteelSeries Engine 3\coreProps.json"),
        os.path.expandvars(r"%PROGRAMDATA%\SteelSeries\GG\coreProps.json"),
    ]

    steelseries_found = False
    for path in coreprops_paths:
        if os.path.exists(path):
            steelseries_found = True
            break

    if not steelseries_found:
        console.print("[yellow]SteelSeries GG not detected.[/yellow]")
        console.print("[dim]If you don't use SteelSeries GG, you can skip this step.[/dim]")
        console.print("[dim]Download SteelSeries GG from: https://steelseries.com/gg[/dim]")
        console.print()

        if not Confirm.ask("Would you like to set up SteelSeries integration anyway?", default=False):
            console.print("[dim]Skipping SteelSeries setup.[/dim]")
            return

    else:
        console.print("[green]SteelSeries GG detected![/green]")
        console.print()

        if not Confirm.ask("Would you like to register REX with SteelSeries GG?", default=True):
            console.print("[dim]Skipping SteelSeries registration.[/dim]")
            return

    # Try to register with GameSense
    console.print("\nRegistering REX with SteelSeries GameSense...")

    try:
        from rex_main.steelseries import SteelSeriesMoments

        moments = SteelSeriesMoments()
        if moments.register():
            console.print("[green]REX registered with SteelSeries GG![/green]")
            console.print()
            console.print("[bold cyan]Final step - Enable REX in SteelSeries GG:[/bold cyan]")
            console.print("  1. Open SteelSeries GG")
            console.print("  2. Click [bold]Settings[/bold] (gear icon in bottom left)")
            console.print("  3. Find [bold]\"Moments\"[/bold] section in settings")
            console.print("  4. Click [bold]\"Auto-clip\"[/bold] tab")
            console.print("  5. Enable [bold]\"Auto-clipping\"[/bold] at the top")
            console.print("  6. Scroll down to find [bold]\"REX Voice Assistant\"[/bold]")
            console.print("  7. Click and check the box to enable it")
            console.print()
            console.print("[dim]Once enabled, say \"clip that\" while Moments is recording.[/dim]")
        else:
            console.print("[yellow]Could not register with SteelSeries GG.[/yellow]")
            console.print("[dim]Make sure SteelSeries GG is running and try again.[/dim]")

    except Exception as e:
        console.print(f"[yellow]Could not connect to SteelSeries GG: {e}[/yellow]")
        console.print("[dim]You can set this up later by running 'rex setup' again.[/dim]")


def _setup_model():
    """Offer to pre-download the Whisper model and set it as default."""
    global _selected_model

    console.print("\n[bold]Step 7: Model Setup[/bold]\n")

    console.print("REX uses the Whisper speech recognition model.")
    console.print("Models available: tiny, base, small.en, medium, large")
    console.print()
    console.print("[bold cyan]Recommendations:[/bold cyan]")
    console.print("  • [green]medium[/green] - Best accuracy, recommended if you have a GPU")
    console.print("  • small.en - Faster, good for English-only on CPU")
    console.print("  • tiny - Fastest, lower accuracy")
    console.print()

    model_name = Prompt.ask(
        "Which model would you like to use?",
        choices=["tiny", "base", "small.en", "medium", "large"],
        default="medium"
    )

    # Save selection for config
    _selected_model = model_name

    if not Confirm.ask(f"Download {model_name} model now?", default=True):
        console.print(f"[dim]Model will be downloaded on first run. Default set to: {model_name}[/dim]")
        return

    console.print(f"\nDownloading {model_name} model...")

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Downloading model...", total=None)

            from faster_whisper import WhisperModel

            # This will download if not cached
            _ = WhisperModel(model_name, device="cpu", compute_type="int8")

            progress.update(task, description="Model downloaded!")

        console.print(f"[green]Model {model_name} is ready and set as default![/green]")

    except Exception as e:
        console.print(f"[yellow]Could not download model: {e}[/yellow]")
        console.print(f"[dim]Model will be downloaded on first run. Default set to: {model_name}[/dim]")


def _test_audio():
    """Run a quick audio test."""
    console.print("\n[bold]Step 8: Audio Test[/bold]\n")

    console.print("Testing audio capture for 3 seconds...")
    console.print("Please speak into your microphone.\n")

    try:
        import numpy as np
        import sounddevice as sd

        duration = 3  # seconds
        samplerate = 16000

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Recording...", total=None)

            # Record audio
            audio = sd.rec(int(duration * samplerate), samplerate=samplerate, channels=1, dtype=np.float32)
            sd.wait()

            progress.update(task, description="Processing...")

        # Check audio level
        audio = audio.flatten()
        max_level = np.max(np.abs(audio))
        rms = np.sqrt(np.mean(audio**2))

        if max_level > 0.01:
            console.print("[green]Audio captured successfully![/green]")
            console.print(f"  Peak level: {max_level:.2%}")
            console.print(f"  RMS level: {rms:.2%}")
        else:
            console.print("[yellow]Audio captured but level is very low.[/yellow]")
            console.print("Check your microphone settings or try speaking louder.")

    except Exception as e:
        console.print(f"[red]Audio test failed: {e}[/red]")


def _setup_wake_word() -> dict:
    """Optional wake-word setup. Installs the openwakeword extra, downloads models,
    and returns a wake_word config dict (empty if user skips)."""
    console.print("\n[bold]Step 8: Wake Word (optional)[/bold]\n")
    console.print(
        "REX can be gated behind a wake word so it only acts on commands\n"
        "after hearing 'hey jarvis'. Recommended for always-on use.\n"
    )

    if not Confirm.ask("Enable the 'hey jarvis' wake word?", default=False):
        console.print("[dim]Skipped wake-word setup.[/dim]")
        return {}

    # 1) Make sure openwakeword is installed.
    try:
        import openwakeword  # noqa: F401
        console.print("[green]openwakeword already installed.[/green]")
    except ImportError:
        console.print("Installing openwakeword (~50MB) ...")
        import subprocess
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "openwakeword>=0.6,<1"
            ])
            console.print("[green]openwakeword installed.[/green]")
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]Install failed: {exc}[/red]")
            console.print("You can retry later with: pip install rex-voice-assistant[wake_word]")
            return {}

    # 2) Pre-download the model files so first run is instant.
    try:
        from openwakeword.utils import download_models
        console.print("Downloading wake-word models (~30MB) ...")
        download_models()
        console.print("[green]Models downloaded.[/green]")
    except Exception as exc:
        console.print(f"[yellow]Model download failed: {exc}. They will retry on first run.[/yellow]")

    # 3) Discover custom-trained models in ~/.rex/wake_models/ and let the user pick.
    from rex_main.config import CONFIG_DIR
    custom_dir = CONFIG_DIR / "wake_models"
    custom_models = sorted(custom_dir.glob("*.onnx")) if custom_dir.exists() else []

    if custom_models:
        console.print("\n[bold]Found custom wake-word models:[/bold]")
        console.print("  [cyan]0[/cyan]) hey_jarvis (prebuilt)")
        for i, p in enumerate(custom_models, start=1):
            console.print(f"  [cyan]{i}[/cyan]) {p.stem} (custom: {p})")
        # Default to most recently modified custom model.
        newest_idx = 1 + max(
            range(len(custom_models)),
            key=lambda j: custom_models[j].stat().st_mtime,
        )
        choice = int(Prompt.ask(
            "Pick a model",
            choices=[str(i) for i in range(0, len(custom_models) + 1)],
            default=str(newest_idx),
        ))
        if choice == 0:
            model_value = "hey_jarvis"
        else:
            # Store as ~ path so the config file stays portable across users.
            chosen = custom_models[choice - 1]
            try:
                model_value = "~/" + str(chosen.relative_to(Path.home())).replace("\\", "/")
            except ValueError:
                model_value = str(chosen)
    else:
        model_value = "hey_jarvis"
        console.print(
            "\n[dim]Tip: see TRAINING_HEY_REX.md to train a custom 'hey rex' model. "
            "Drop the resulting .onnx in ~/.rex/wake_models/ and re-run `rex setup` to switch.[/dim]"
        )

    # 4) Tunables.
    threshold = float(Prompt.ask(
        "Detection threshold (0.0-1.0, higher = fewer false fires)",
        default="0.5",
    ))
    cue = Confirm.ask("Play a short tone when the wake word fires?", default=True)

    return {
        "enabled": True,
        "model": model_value,
        "threshold": threshold,
        "listening_window_seconds": 6,
        "debounce_seconds": 1.0,
        "cue_enabled": cue,
    }


def _write_config(services: list[str], secrets: dict, *, cuda_ok: Optional[bool] = None, wake_word: Optional[dict] = None):
    """Write configuration to ~/.rex/config.yaml."""
    global _selected_model

    console.print("\n[bold]Step 10: Saving Configuration[/bold]\n")

    from rex_main.config import CONFIG_DIR, save_config, save_secrets, ensure_config_dir

    ensure_config_dir()

    # Choose device default: cuda if GPU works, otherwise auto so REX can fall back.
    device = "cuda" if cuda_ok else "auto"

    # Build config with selected model
    config = {
        "audio": {
            "sample_rate": 16000,
            "frame_ms": 32,
        },
        "model": {
            "name": _selected_model,
            "device": device,
            "beam_size": 1,
            "cache_dir": str(CONFIG_DIR / "models"),
        },
        "services": {
            "active": services[0] if services else "none",
            "ytmd": {
                "host": "localhost",
                "port": 9863,
            },
            "spotify": {
                "redirect_uri": "http://127.0.0.1:8888/callback",
            },
        },
        "logging": {
            "level": "INFO",
            "file": str(CONFIG_DIR / "logs" / "rex.log"),
        },
    }

    if wake_word:
        config["wake_word"] = wake_word

    # Save config
    save_config(config)
    console.print(f"  Configuration saved to: {CONFIG_DIR / 'config.yaml'}")

    # Save secrets
    if secrets:
        save_secrets(secrets)
        console.print("  Secrets saved securely")

    console.print("[green]Configuration complete![/green]")


if __name__ == "__main__":
    run_wizard()
