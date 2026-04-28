"""cli.py
Unified CLI entry point for REX voice assistant.

Commands:
    rex           - Run the voice assistant (default)
    rex setup     - Interactive setup wizard
    rex settings  - Change model, services, and integrations
    rex status    - Show configuration and service connectivity
    rex test      - Test a specific service (ytmd/spotify)
    rex migrate   - Import existing .env to new config
"""

from __future__ import annotations

# Setup CUDA DLL paths BEFORE importing anything that might load ctranslate2
import os
import sys

if sys.platform == "win32":
    # Add NVIDIA CUDA library paths for cuDNN and cuBLAS
    # This must happen before ctranslate2/faster-whisper are imported
    _cuda_paths = []

    # Try to find nvidia package paths
    try:
        import nvidia
        if hasattr(nvidia, "__path__"):
            for _nvidia_path in nvidia.__path__:
                for _lib in ["cudnn", "cublas"]:
                    _bin_path = os.path.join(_nvidia_path, _lib, "bin")
                    if os.path.isdir(_bin_path) and _bin_path not in _cuda_paths:
                        _cuda_paths.append(_bin_path)
    except ImportError:
        pass

    for _p in _cuda_paths:
        if _p not in os.environ.get("PATH", ""):
            os.environ["PATH"] = _p + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(_p)

import asyncio
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@click.group(invoke_without_command=True)
@click.option("--model", default=None, help="Whisper model size (tiny|base|small|medium|large) - overrides config")
@click.option("--device", default=None, type=click.Choice(["cuda", "cpu", "auto"]), help="Device for inference (overrides config; auto=detect GPU, fallback to CPU)")
@click.option("--beam", default=None, type=int, help="Beam size for Whisper decoding (overrides config)")
@click.option("--log-file", default=None, help="Path to write rotating logs")
@click.option("--debug", is_flag=True, help="Enable verbose logging")
@click.option("--dashboard", is_flag=True, help="Enable metrics dashboard at http://localhost:9876")
@click.option("--dashboard-port", default=9876, type=int, help="Port for metrics dashboard")
@click.option("--low-latency/--standard", "low_latency", default=None, help="Low-latency mode (default) vs standard mode (slower, more forgiving VAD)")
@click.option("--wake-word/--no-wake-word", "wake_word", default=None, help="Enable wake-word gating (overrides config)")
@click.option("--wake-model", default=None, help="Wake-word model name or path (e.g. 'hey_rex', 'hey_jarvis', or a local .onnx). Overrides config.")
@click.option("--gaming", is_flag=True, help="Gaming preset: tiny.en + CPU + hey_rex wake word + low latency. Frees the GPU.")
@click.pass_context
def cli(ctx: click.Context, model: str, device: str, beam: int, log_file: Optional[str], debug: bool, dashboard: bool, dashboard_port: int, low_latency: bool, wake_word: Optional[bool], wake_model: Optional[str], gaming: bool):
    """REX - Voice-controlled music assistant.

    Run without a subcommand to start the voice assistant.
    """
    if gaming:
        # Gaming preset: lightweight + GPU-free. Individual flags still win
        # if explicitly passed alongside --gaming.
        if model is None:
            model = "tiny.en"
        if device is None:
            device = "cpu"
        if low_latency is None:
            low_latency = True
        if wake_word is None:
            wake_word = True
        if wake_model is None:
            # Force the custom REX model regardless of what user config says.
            # The auto-download path in wake_word.py grabs it from Hugging Face on first use.
            wake_model = "hey_rex"
        console.print("[cyan]Gaming preset: tiny.en + CPU + hey_rex wake word + low-latency[/cyan]")

    ctx.ensure_object(dict)
    ctx.obj["model"] = model
    ctx.obj["device"] = device
    ctx.obj["beam"] = beam
    ctx.obj["log_file"] = log_file
    ctx.obj["debug"] = debug
    ctx.obj["dashboard"] = dashboard
    ctx.obj["dashboard_port"] = dashboard_port
    ctx.obj["low_latency"] = low_latency
    ctx.obj["wake_word"] = wake_word
    ctx.obj["wake_model"] = wake_model

    # If no subcommand, run the assistant
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@cli.command()
@click.pass_context
def run(ctx: click.Context):
    """Run the REX voice assistant."""
    from rex_main.config import load_config, get_log_file_path

    config = load_config()

    # Build options namespace-like object for compatibility
    class Options:
        pass

    opts = Options()
    opts.model = ctx.obj.get("model") or config.get("model", {}).get("name", "small.en")
    opts.device = ctx.obj.get("device") or config.get("model", {}).get("device", "auto")
    opts.beam = ctx.obj.get("beam") or config.get("model", {}).get("beam_size", 1)
    opts.log_file = ctx.obj.get("log_file") or get_log_file_path(config)
    opts.debug = ctx.obj.get("debug", False)
    opts.dashboard = ctx.obj.get("dashboard", False)
    opts.dashboard_port = ctx.obj.get("dashboard_port", 9876)
    cli_low_latency = ctx.obj.get("low_latency")
    if cli_low_latency is None:
        opts.low_latency = config.get("performance", {}).get("low_latency_mode", True)
    else:
        opts.low_latency = cli_low_latency
    opts.wake_word = ctx.obj.get("wake_word")
    opts.wake_model = ctx.obj.get("wake_model")

    # Configure services from config
    from rex_main.commands import configure_from_config
    configure_from_config(config)

    # Start dashboard if enabled
    if opts.dashboard:
        from rex_main.dashboard import start_dashboard
        if start_dashboard(port=opts.dashboard_port):
            console.print(f"[green]Dashboard started at http://localhost:{opts.dashboard_port}[/green]")
        else:
            console.print("[yellow]Dashboard failed to start (may already be running)[/yellow]")

    # Import and run main
    from rex_main.rex import run_assistant

    try:
        asyncio.run(run_assistant(opts, config))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        # Stop dashboard if running
        if opts.dashboard:
            try:
                from rex_main.dashboard import stop_dashboard
                stop_dashboard()
            except Exception:
                pass
        sys.exit(0)


@cli.command()
def setup():
    """Interactive setup wizard for REX."""
    from rex_main.setup_wizard import run_wizard
    run_wizard()


@cli.command(name="record-wake-samples")
@click.option("--count", default=100, type=int, help="Number of samples to record")
@click.option("--phrase", default="hey rex", help="Wake-word phrase (used for prompts and filename prefix)")
@click.option("--contributor", default=None, help="Your name (used to label your batch). Prompted if not given.")
@click.option("--output-dir", default=None, type=click.Path(), help="Override save directory (default: ~/.rex/wake_training/recordings/<contributor>)")
@click.option("--device", default=None, help="Audio input device (name or index)")
def record_wake_samples_cmd(count: int, phrase: str, contributor: Optional[str], output_dir: Optional[str], device: Optional[str]):
    """Record samples for training a custom wake-word model.

    See docs/specifics/TRAINING_HEY_REX.md for the full training walkthrough,
    or docs/specifics/CONTRIBUTING_VOICE_SAMPLES.md if you're contributing to someone else's model.
    """
    from rex_main.recorder import record_wake_samples
    record_wake_samples(
        count=count,
        phrase=phrase,
        output_dir=output_dir,
        device=device,
        contributor=contributor,
    )


@cli.command(name="retrim-wake-samples")
@click.option("--contributor", default=None, help="Contributor folder to retrim (prompted if multiple)")
@click.option("--dry-run", is_flag=True, help="Just report stats; don't modify any files")
def retrim_wake_samples_cmd(contributor: Optional[str], dry_run: bool):
    """Re-trim existing recordings using the asymmetric trim policy.

    Useful for cleaning up samples recorded before the trim was tuned. Originals
    are backed up to `_untrimmed/` so the operation is reversible.
    """
    from rex_main.recorder import retrim_wake_samples
    retrim_wake_samples(contributor=contributor, dry_run=dry_run)


@cli.command(name="review-wake-samples")
@click.option("--contributor", default=None, help="Contributor folder to review (prompted if you have multiple)")
@click.option("--start-at", default=1, type=int, help="Sample number to start from (resume from where you left off)")
def review_wake_samples_cmd(contributor: Optional[str], start_at: int):
    """Listen to recorded samples and keep or reject each.

    Rejected files move to a `_rejected/` subfolder (recoverable). Re-run
    `rex record-wake-samples` to fill the gaps.
    """
    from rex_main.recorder import review_wake_samples
    review_wake_samples(contributor=contributor, start_at=start_at)


@cli.command(name="package-wake-samples")
@click.option("--contributor", default=None, help="Contributor folder to package (prompted if you have multiple)")
@click.option("--phrase", default="hey rex", help="Phrase tag stored in the manifest")
@click.option("--output", default=None, type=click.Path(), help="Override output zip path")
def package_wake_samples_cmd(contributor: Optional[str], phrase: str, output: Optional[str]):
    """Bundle your recordings into a single .zip to send to someone training a model.

    The zip contains your WAVs plus a manifest.json (sample count, level stats,
    microphone name, OS) so the trainer can verify quality before merging.
    """
    from rex_main.recorder import package_wake_samples
    package_wake_samples(
        contributor=contributor,
        phrase=phrase,
        output_zip=output,
    )


@cli.command()
def settings():
    """Change REX settings (model, services, integrations)."""
    from rex_main.config import load_config, save_config, save_secrets

    config = load_config()

    console.print(Panel.fit("[bold blue]REX Settings[/bold blue]"))
    console.print()

    # Show menu
    console.print("[bold]What would you like to change?[/bold]")
    console.print("  1. Change Whisper model")
    console.print("  2. Change active music service")
    console.print("  3. Reconfigure YouTube Music Desktop")
    console.print("  4. Reconfigure Spotify")
    console.print("  5. Reconfigure SteelSeries Moments")
    console.print("  6. Back to main menu")
    console.print()

    from rich.prompt import Prompt, Confirm

    choice = Prompt.ask("Select option", choices=["1", "2", "3", "4", "5", "6"], default="6")

    if choice == "1":
        # Change model
        current = config.get("model", {}).get("name", "small.en")
        console.print(f"\nCurrent model: [cyan]{current}[/cyan]")
        console.print()
        console.print("Available models:")
        console.print("  • tiny - Fastest, lower accuracy")
        console.print("  • base - Fast, decent accuracy")
        console.print("  • small.en - Good balance for English")
        console.print("  • medium - Best accuracy (recommended with GPU)")
        console.print("  • large - Highest accuracy, slowest")
        console.print()

        new_model = Prompt.ask(
            "New model",
            choices=["tiny", "base", "small.en", "medium", "large"],
            default=current
        )

        if new_model != current:
            config.setdefault("model", {})["name"] = new_model
            save_config(config)
            console.print(f"[green]Model changed to: {new_model}[/green]")

            if Confirm.ask("Download model now?", default=True):
                console.print(f"Downloading {new_model}...")
                try:
                    from faster_whisper import WhisperModel
                    _ = WhisperModel(new_model, device="cpu", compute_type="int8")
                    console.print("[green]Model downloaded![/green]")
                except Exception as e:
                    console.print(f"[yellow]Download failed: {e}[/yellow]")
                    console.print("Model will download on next run.")
        else:
            console.print("[dim]No change made.[/dim]")

    elif choice == "2":
        # Change active service
        current = config.get("services", {}).get("active", "none")
        console.print(f"\nCurrent active service: [cyan]{current}[/cyan]")
        console.print()
        console.print("Available services:")
        console.print("  • ytmd - YouTube Music Desktop")
        console.print("  • spotify - Spotify")
        console.print("  • none - Transcription only (no music control)")
        console.print()

        new_service = Prompt.ask(
            "Active service",
            choices=["ytmd", "spotify", "none"],
            default=current
        )

        if new_service != current:
            config.setdefault("services", {})["active"] = new_service
            save_config(config)
            console.print(f"[green]Active service changed to: {new_service}[/green]")
        else:
            console.print("[dim]No change made.[/dim]")

    elif choice == "3":
        # Reconfigure YTMD
        from rex_main.setup_wizard import _setup_ytmd
        token = _setup_ytmd()
        if token:
            save_secrets({"ytmd_token": token})
            console.print("[green]YTMD reconfigured![/green]")

    elif choice == "4":
        # Reconfigure Spotify
        from rex_main.setup_wizard import _setup_spotify
        creds = _setup_spotify()
        if creds:
            save_secrets(creds)
            console.print("[green]Spotify reconfigured![/green]")

    elif choice == "5":
        # Reconfigure SteelSeries
        from rex_main.setup_wizard import _setup_steelseries
        _setup_steelseries()

    else:
        console.print("[dim]No changes made.[/dim]")


@cli.command()
@click.option("--port", default=9876, type=int, help="Port for dashboard server")
def dashboard(port: int):
    """Run the metrics dashboard standalone (for viewing past sessions)."""
    from rex_main.dashboard import start_dashboard
    import time

    console.print("[bold blue]REX Metrics Dashboard[/bold blue]")
    console.print(f"Starting dashboard at http://localhost:{port}")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    if start_dashboard(port=port):
        console.print(f"[green]Dashboard running at http://localhost:{port}[/green]")
        try:
            # Keep running until interrupted
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping dashboard...[/yellow]")
            from rex_main.dashboard import stop_dashboard
            stop_dashboard()
    else:
        console.print("[red]Failed to start dashboard[/red]")


@cli.command()
def status():
    """Show current configuration and service connectivity."""
    from rex_main.config import load_config, get_secrets, CONFIG_DIR

    config = load_config()
    secrets = get_secrets(config)

    console.print(Panel.fit("[bold blue]REX Status[/bold blue]"))

    # Config file location
    config_file = CONFIG_DIR / "config.yaml"
    console.print("\n[bold]Configuration:[/bold]")
    console.print(f"  Config file: {config_file} ({'exists' if config_file.exists() else '[red]not found[/red]'})")

    # Active service
    active = config.get("services", {}).get("active", "none")
    console.print(f"  Active service: [cyan]{active}[/cyan]")

    # Model settings
    model_cfg = config.get("model", {})
    console.print("\n[bold]Model:[/bold]")
    console.print(f"  Name: {model_cfg.get('name', 'small.en')}")
    console.print(f"  Device: {model_cfg.get('device', 'auto')}")

    # Service status table
    table = Table(title="\nService Connectivity")
    table.add_column("Service", style="cyan")
    table.add_column("Configured", style="green")
    table.add_column("Status")

    # YTMD status
    ytmd_token = secrets.get("ytmd_token")
    ytmd_configured = "Yes" if ytmd_token else "No"
    ytmd_status = "[green]Ready[/green]" if ytmd_token else "[yellow]Not configured[/yellow]"
    if ytmd_token:
        # Try to connect
        try:
            import requests
            host = config.get("services", {}).get("ytmd", {}).get("host", "localhost")
            port = config.get("services", {}).get("ytmd", {}).get("port", 9863)
            resp = requests.get(f"http://{host}:{port}/api/v1/state",
                              headers={"Authorization": ytmd_token}, timeout=2)
            if resp.status_code == 200:
                ytmd_status = "[green]Connected[/green]"
            else:
                ytmd_status = f"[red]Error ({resp.status_code})[/red]"
        except Exception:
            ytmd_status = "[red]Offline[/red]"
    table.add_row("YouTube Music Desktop", ytmd_configured, ytmd_status)

    # Spotify status
    spotify_id = secrets.get("spotify_client_id")
    spotify_secret = secrets.get("spotify_client_secret")
    spotify_configured = "Yes" if (spotify_id and spotify_secret) else "No"
    spotify_status = "[green]Ready[/green]" if (spotify_id and spotify_secret) else "[yellow]Not configured[/yellow]"
    table.add_row("Spotify", spotify_configured, spotify_status)

    console.print(table)

    # Audio settings
    audio_cfg = config.get("audio", {})
    console.print("\n[bold]Audio:[/bold]")
    console.print(f"  Sample rate: {audio_cfg.get('sample_rate', 16000)} Hz")

    # Show audio device info
    try:
        import sounddevice as sd
        default_input = sd.default.device[0]
        if default_input is not None and default_input >= 0:
            dev_info = sd.query_devices(default_input)
            console.print(f"  Input device: {dev_info['name']}")
        else:
            console.print("  Input device: [yellow]No default set[/yellow]")
    except Exception:
        console.print("  Input device: [yellow]Could not detect[/yellow]")


@cli.command()
@click.argument("service", type=click.Choice(["ytmd", "spotify"]))
def test(service: str):
    """Test connectivity to a specific service."""
    from rex_main.config import load_config, get_secrets

    config = load_config()
    secrets = get_secrets(config)

    if service == "ytmd":
        console.print("[bold]Testing YouTube Music Desktop connection...[/bold]")
        token = secrets.get("ytmd_token")
        if not token:
            console.print("[red]YTMD token not configured. Run 'rex setup' first.[/red]")
            return

        try:
            import requests
            host = config.get("services", {}).get("ytmd", {}).get("host", "localhost")
            port = config.get("services", {}).get("ytmd", {}).get("port", 9863)

            # Test state endpoint
            resp = requests.get(f"http://{host}:{port}/api/v1/state",
                              headers={"Authorization": token}, timeout=5)
            if resp.status_code == 200:
                state = resp.json()
                console.print("[green]Connected successfully![/green]")
                player = state.get("player", {})
                if player.get("trackState") == 1:
                    track = player.get("videoDetails", {})
                    console.print(f"  Now playing: {track.get('title', 'Unknown')} by {track.get('author', 'Unknown')}")
                else:
                    console.print("  Player is paused/stopped")
            else:
                console.print(f"[red]Connection failed: HTTP {resp.status_code}[/red]")
        except requests.exceptions.ConnectionError:
            console.print("[red]Could not connect. Is YouTube Music Desktop running?[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    elif service == "spotify":
        console.print("[bold]Testing Spotify connection...[/bold]")
        client_id = secrets.get("spotify_client_id")
        client_secret = secrets.get("spotify_client_secret")

        if not client_id or not client_secret:
            console.print("[red]Spotify credentials not configured. Run 'rex setup' first.[/red]")
            return

        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth

            redirect_uri = config.get("services", {}).get("spotify", {}).get(
                "redirect_uri", "http://127.0.0.1:8888/callback"
            )

            sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope="user-modify-playback-state user-read-playback-state user-library-modify"
            ))

            # Try to get current user
            user = sp.current_user()
            console.print("[green]Connected successfully![/green]")
            console.print(f"  Logged in as: {user.get('display_name', user.get('id'))}")

            # Check for active devices
            devices = sp.devices()
            if devices.get("devices"):
                console.print(f"  Active devices: {len(devices['devices'])}")
                for dev in devices["devices"]:
                    active = " [active]" if dev.get("is_active") else ""
                    console.print(f"    - {dev['name']} ({dev['type']}){active}")
            else:
                console.print("  [yellow]No active devices found[/yellow]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


@cli.command()
@click.option("--from-env", "from_env", is_flag=True, help="Import from existing .env file")
def migrate(from_env: bool):
    """Migrate configuration from legacy formats."""
    from rex_main.config import CONFIG_DIR, save_secrets

    if from_env:
        console.print("[bold]Migrating from .env file...[/bold]")

        # Look for .env in common locations
        env_paths = [
            Path.cwd() / ".env",
            Path.cwd() / "rex_main" / ".env",
            Path(__file__).parent / ".env",
        ]

        env_file = None
        for path in env_paths:
            if path.exists():
                env_file = path
                break

        if not env_file:
            console.print("[red]No .env file found[/red]")
            return

        console.print(f"  Found: {env_file}")

        # Parse .env file
        from dotenv import dotenv_values
        env_vars = dotenv_values(env_file)

        # Extract and save secrets
        secrets_to_save = {}

        if env_vars.get("YTMD_TOKEN"):
            secrets_to_save["ytmd_token"] = env_vars["YTMD_TOKEN"]
            console.print("  [green]Found YTMD token[/green]")

        if env_vars.get("SPOTIPY_CLIENT_ID"):
            secrets_to_save["spotify_client_id"] = env_vars["SPOTIPY_CLIENT_ID"]
            console.print("  [green]Found Spotify client ID[/green]")

        if env_vars.get("SPOTIPY_CLIENT_SECRET"):
            secrets_to_save["spotify_client_secret"] = env_vars["SPOTIPY_CLIENT_SECRET"]
            console.print("  [green]Found Spotify client secret[/green]")

        if secrets_to_save:
            save_secrets(secrets_to_save)
            console.print(f"\n[green]Migrated {len(secrets_to_save)} secrets to {CONFIG_DIR / 'secrets.yaml'}[/green]")

            # Update config with service settings
            from rex_main.config import load_config, save_config
            config = load_config()

            if env_vars.get("YTMD_HOST"):
                config.setdefault("services", {}).setdefault("ytmd", {})["host"] = env_vars["YTMD_HOST"]
            if env_vars.get("YTMD_PORT"):
                config.setdefault("services", {}).setdefault("ytmd", {})["port"] = int(env_vars["YTMD_PORT"])
            if env_vars.get("SPOTIPY_REDIRECT_URI"):
                config.setdefault("services", {}).setdefault("spotify", {})["redirect_uri"] = env_vars["SPOTIPY_REDIRECT_URI"]

            # Set active service based on what's configured
            if "ytmd_token" in secrets_to_save:
                config.setdefault("services", {})["active"] = "ytmd"
            elif "spotify_client_id" in secrets_to_save:
                config.setdefault("services", {})["active"] = "spotify"

            save_config(config)
            console.print("[green]Updated config.yaml with service settings[/green]")
        else:
            console.print("[yellow]No secrets found to migrate[/yellow]")
    else:
        console.print("Use --from-env to migrate from a .env file")


def main():
    """Entry point for the rex command."""
    cli()


if __name__ == "__main__":
    main()
