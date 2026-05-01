"""settings.py
SettingsDialog — tabbed form mirroring default_config.yaml.

Music-service changes apply live via configure_from_config().
Other changes write to disk and prompt for a restart.
"""

from __future__ import annotations

import copy
import logging
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rex_main.config import save_config
from rex_main.ui.icons import make_app_icon

logger = logging.getLogger(__name__)


_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_DEVICES = ("auto", "cuda", "cpu")
_MODELS = ("tiny.en", "base.en", "small.en", "medium.en", "large-v3")
_SERVICES = ("none", "ytmd", "spotify")


class SettingsDialog(QDialog):
    """Modal-ish dialog. Closes without affecting the runtime unless Save is clicked."""

    def __init__(
        self,
        config: dict,
        on_save: Callable[[dict, bool], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rex Settings")
        self.setWindowIcon(make_app_icon())
        self.setMinimumWidth(480)

        self._original = copy.deepcopy(config)
        self._draft = copy.deepcopy(config)
        self._on_save = on_save

        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._build_audio_tab()
        self._build_model_tab()
        self._build_music_tab()
        self._build_wake_tab()
        self._build_perf_tab()
        self._build_logging_tab()

        preset_row = QHBoxLayout()
        gaming_btn = QPushButton("Apply gaming preset")
        gaming_btn.setToolTip(
            "Sets tiny.en + CPU + hey_rex wake word + low-latency mode.\n"
            "Frees the GPU for games. You still need to click Save."
        )
        gaming_btn.clicked.connect(self._apply_gaming_preset)
        default_btn = QPushButton("Apply default preset")
        default_btn.setToolTip(
            "Sets small.en + auto device + hey_rex wake word + low-latency mode.\n"
            "Balanced quality for normal desktop use. You still need to click Save."
        )
        default_btn.clicked.connect(self._apply_default_preset)
        wizard_btn = QPushButton("Run setup wizard…")
        wizard_btn.setToolTip("Re-run the interactive console setup (OAuth, API keys).")
        wizard_btn.clicked.connect(self._run_wizard)
        preset_row.addWidget(gaming_btn)
        preset_row.addWidget(default_btn)
        preset_row.addStretch(1)
        preset_row.addWidget(wizard_btn)
        layout.addLayout(preset_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # Tab builders

    def _build_audio_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        audio = self._draft.setdefault("audio", {})

        self._audio_sample_rate = QSpinBox()
        self._audio_sample_rate.setRange(8000, 48000)
        self._audio_sample_rate.setSingleStep(1000)
        self._audio_sample_rate.setValue(int(audio.get("sample_rate", 16000)))
        form.addRow("Sample rate (Hz)", self._audio_sample_rate)

        self._audio_frame_ms = QSpinBox()
        self._audio_frame_ms.setRange(10, 200)
        self._audio_frame_ms.setValue(int(audio.get("frame_ms", 32)))
        form.addRow("Frame size (ms)", self._audio_frame_ms)

        self._audio_device = QLineEdit(str(audio.get("device", "")) if audio.get("device") else "")
        self._audio_device.setPlaceholderText("(system default)")
        form.addRow("Input device", self._audio_device)

        self._tabs.addTab(page, "Audio")

    def _build_model_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        model = self._draft.setdefault("model", {})

        self._model_name = QComboBox()
        self._model_name.setEditable(True)
        self._model_name.addItems(_MODELS)
        self._model_name.setCurrentText(str(model.get("name", "small.en")))
        form.addRow("Whisper model", self._model_name)

        self._model_device = QComboBox()
        self._model_device.addItems(_DEVICES)
        current_device = str(model.get("device", "auto"))
        if current_device not in _DEVICES:
            self._model_device.addItem(current_device)
        self._model_device.setCurrentText(current_device)
        form.addRow("Device", self._model_device)

        self._model_beam = QSpinBox()
        self._model_beam.setRange(1, 10)
        self._model_beam.setValue(int(model.get("beam_size", 1)))
        form.addRow("Beam size", self._model_beam)

        self._tabs.addTab(page, "Model")

    def _build_music_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        services = self._draft.setdefault("services", {})

        self._music_active = QComboBox()
        self._music_active.addItems(_SERVICES)
        self._music_active.setCurrentText(str(services.get("active", "none")))
        form.addRow("Active service", self._music_active)

        ytmd = services.setdefault("ytmd", {})
        self._ytmd_host = QLineEdit(str(ytmd.get("host", "127.0.0.1")))
        form.addRow("YTMD host", self._ytmd_host)
        self._ytmd_port = QSpinBox()
        self._ytmd_port.setRange(1, 65535)
        self._ytmd_port.setValue(int(ytmd.get("port", 9863)))
        form.addRow("YTMD port", self._ytmd_port)

        spotify = services.setdefault("spotify", {})
        self._spotify_redirect = QLineEdit(str(spotify.get("redirect_uri", "")))
        form.addRow("Spotify redirect URI", self._spotify_redirect)

        hint = QLabel("Switching service applies live without restart.")
        hint.setStyleSheet("color: gray;")
        form.addRow(hint)

        self._tabs.addTab(page, "Music")

    def _build_wake_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        wake = self._draft.setdefault("wake_word", {})

        self._wake_enabled = QCheckBox()
        self._wake_enabled.setChecked(bool(wake.get("enabled", True)))
        form.addRow("Enabled", self._wake_enabled)

        self._wake_model = QLineEdit(str(wake.get("model", "hey_rex")))
        form.addRow("Model", self._wake_model)

        self._wake_threshold = QDoubleSpinBox()
        self._wake_threshold.setRange(0.0, 1.0)
        self._wake_threshold.setSingleStep(0.05)
        self._wake_threshold.setValue(float(wake.get("threshold", 0.5)))
        form.addRow("Threshold", self._wake_threshold)

        self._wake_window = QDoubleSpinBox()
        self._wake_window.setRange(1.0, 60.0)
        self._wake_window.setSingleStep(1.0)
        self._wake_window.setValue(float(wake.get("listening_window_seconds", 6)))
        form.addRow("Listening window (s)", self._wake_window)

        self._wake_debounce = QDoubleSpinBox()
        self._wake_debounce.setRange(0.0, 10.0)
        self._wake_debounce.setSingleStep(0.1)
        self._wake_debounce.setValue(float(wake.get("debounce_seconds", 1.0)))
        form.addRow("Debounce (s)", self._wake_debounce)

        self._wake_cue = QCheckBox()
        self._wake_cue.setChecked(bool(wake.get("cue_enabled", True)))
        form.addRow("Audio cue on fire", self._wake_cue)

        self._tabs.addTab(page, "Wake Word")

    def _build_perf_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        perf = self._draft.setdefault("performance", {})

        self._perf_low_latency = QCheckBox()
        self._perf_low_latency.setChecked(bool(perf.get("low_latency_mode", True)))
        form.addRow("Low-latency mode", self._perf_low_latency)

        self._perf_silence = QSpinBox()
        self._perf_silence.setRange(50, 2000)
        self._perf_silence.setSingleStep(50)
        self._perf_silence.setValue(int(perf.get("vad_silence_ms", 250)))
        form.addRow("VAD silence (ms)", self._perf_silence)

        self._tabs.addTab(page, "Performance")

    def _build_logging_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        log = self._draft.setdefault("logging", {})

        self._log_level = QComboBox()
        self._log_level.addItems(_LOG_LEVELS)
        self._log_level.setCurrentText(str(log.get("level", "INFO")))
        form.addRow("Log level", self._log_level)

        self._log_file = QLineEdit(str(log.get("file", "~/.rex/logs/rex.log")))
        form.addRow("Log file", self._log_file)

        self._tabs.addTab(page, "Logging")

    # Save / wizard

    def _collect(self) -> dict:
        out = copy.deepcopy(self._draft)

        out.setdefault("audio", {})
        out["audio"]["sample_rate"] = self._audio_sample_rate.value()
        out["audio"]["frame_ms"] = self._audio_frame_ms.value()
        device_text = self._audio_device.text().strip()
        out["audio"]["device"] = device_text or None

        out.setdefault("model", {})
        out["model"]["name"] = self._model_name.currentText().strip()
        out["model"]["device"] = self._model_device.currentText().strip()
        out["model"]["beam_size"] = self._model_beam.value()

        out.setdefault("services", {})
        out["services"]["active"] = self._music_active.currentText()
        out["services"].setdefault("ytmd", {})
        out["services"]["ytmd"]["host"] = self._ytmd_host.text().strip()
        out["services"]["ytmd"]["port"] = self._ytmd_port.value()
        out["services"].setdefault("spotify", {})
        out["services"]["spotify"]["redirect_uri"] = self._spotify_redirect.text().strip()

        out.setdefault("wake_word", {})
        out["wake_word"]["enabled"] = self._wake_enabled.isChecked()
        out["wake_word"]["model"] = self._wake_model.text().strip()
        out["wake_word"]["threshold"] = float(self._wake_threshold.value())
        out["wake_word"]["listening_window_seconds"] = float(self._wake_window.value())
        out["wake_word"]["debounce_seconds"] = float(self._wake_debounce.value())
        out["wake_word"]["cue_enabled"] = self._wake_cue.isChecked()

        out.setdefault("performance", {})
        out["performance"]["low_latency_mode"] = self._perf_low_latency.isChecked()
        out["performance"]["vad_silence_ms"] = self._perf_silence.value()

        out.setdefault("logging", {})
        out["logging"]["level"] = self._log_level.currentText()
        out["logging"]["file"] = self._log_file.text().strip()

        return out

    def _save(self) -> None:
        new_config = self._collect()

        # Determine whether anything outside the music block changed; if so, a
        # restart is needed for changes to take effect. Music-only changes can
        # be hot-applied by configure_from_config().
        restart_needed = _restart_required(self._original, new_config)

        try:
            save_config(new_config)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", f"Could not save config:\n{exc}")
            return

        try:
            self._on_save(new_config, restart_needed)
        except Exception:
            logger.exception("on_save callback raised")

        if restart_needed:
            QMessageBox.information(
                self,
                "Restart required",
                "Some changes will take effect after Rex restarts.\n"
                "The tray menu has a Restart Rex action — or quit and relaunch.",
            )

        self.accept()

    def _apply_gaming_preset(self) -> None:
        """Mirror the --gaming CLI preset into the form fields."""
        self._model_name.setCurrentText("tiny.en")
        self._model_device.setCurrentText("cpu")
        self._model_beam.setValue(1)
        self._wake_enabled.setChecked(True)
        self._wake_model.setText("hey_rex")
        self._perf_low_latency.setChecked(True)
        QMessageBox.information(
            self,
            "Gaming preset applied",
            "Fields updated: tiny.en + CPU + hey_rex + low-latency.\n\n"
            "Click Save to persist, then restart Rex to apply.",
        )

    def _apply_default_preset(self) -> None:
        """Restore the shipped default model/device/wake-word setup."""
        self._model_name.setCurrentText("small.en")
        self._model_device.setCurrentText("auto")
        self._model_beam.setValue(1)
        self._wake_enabled.setChecked(True)
        self._wake_model.setText("hey_rex")
        self._perf_low_latency.setChecked(True)
        QMessageBox.information(
            self,
            "Default preset applied",
            "Fields updated: small.en + auto device + hey_rex + low-latency.\n\n"
            "Click Save to persist, then restart Rex to apply.",
        )

    def _run_wizard(self) -> None:
        # The setup wizard is a Rich-based interactive flow that wants a real
        # console. Spawning it from a Qt thread is awkward; for v1 we tell the
        # user how to run it. Future v1.1 work can wrap it in a Qt console.
        QMessageBox.information(
            self,
            "Run setup wizard",
            "The setup wizard runs in a console.\n\n"
            "Quit Rex from the tray, then run:\n\n    rex setup\n\n"
            "from a terminal to (re-)configure music services.",
        )


def _restart_required(old: dict, new: dict) -> bool:
    """True if any non-music key changed."""
    old_no_music = {k: v for k, v in old.items() if k != "services"}
    new_no_music = {k: v for k, v in new.items() if k != "services"}
    if old_no_music != new_no_music:
        return True
    # services.* other than active also requires restart for hosts/ports.
    old_svc = old.get("services", {})
    new_svc = new.get("services", {})
    for key in set(old_svc) | set(new_svc):
        if key == "active":
            continue
        if old_svc.get(key) != new_svc.get(key):
            return True
    return False
