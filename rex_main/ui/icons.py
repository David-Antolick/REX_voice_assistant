"""icons.py
Tray and launcher icons for REX.

Primary icon source is ``rex_main/ui/assets/rex_icon.png`` — the
hand-made REX artwork. Per-state tray glyphs are produced by tinting
or badging that base PNG; if the asset is missing we fall back to a
drawn T-rex silhouette so the app never starts without an icon.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# State color palette. Picked for legibility in both light and dark trays.
_STATE_COLORS: dict[str, QColor] = {
    "idle": QColor("#4a90e2"),        # calm blue
    "listening": QColor("#27ae60"),   # active green
    "processing": QColor("#f5a623"),  # working amber
    "paused": QColor("#7f8c8d"),      # muted grey
    "error": QColor("#e74c3c"),       # red
}

_SIZES = (16, 24, 32, 48, 64, 128, 256)
_ASSET_PATH = Path(__file__).parent / "assets" / "rex_icon.png"
# How much of the icon canvas the REX artwork should fill. >1 zooms past
# the bounds (cropping any transparent margin) so the tray shows the
# artwork closer to edge-to-edge instead of a tiny dot in the middle.
_FILL_FACTOR = 2.0


def _load_base_pixmap() -> QPixmap | None:
    if not _ASSET_PATH.is_file():
        return None
    pm = QPixmap(str(_ASSET_PATH))
    return pm if not pm.isNull() else None


def _badge_pixmap(base: QPixmap, size: int, state: str) -> QPixmap:
    """Scale the base icon to ``size`` and overlay a small state-colored dot."""
    target = max(1, int(round(size * _FILL_FACTOR)))
    scaled = base.scaled(target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # Center the over-scaled artwork; overflow is clipped by the canvas.
        x = (size - scaled.width()) // 2
        y = (size - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)

        # Idle stays clean — no badge — so the tray reads as plain "REX".
        if state == "idle":
            return pm

        dot_d = max(4, int(size * 0.42))
        dot_x = size - dot_d
        dot_y = size - dot_d
        p.setPen(QPen(QColor(255, 255, 255), max(1.0, size / 32.0)))
        p.setBrush(QBrush(_STATE_COLORS.get(state, _STATE_COLORS["idle"])))
        p.drawEllipse(QRectF(dot_x, dot_y, dot_d, dot_d))
    finally:
        p.end()
    return pm


def _draw_icon(size: int, color: QColor) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.Antialiasing, True)

        # Filled disc background.
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(color))
        p.drawEllipse(0, 0, size, size)

        # T-rex head silhouette in white, facing right.
        # Coordinates are normalized to the icon size; the path traces a
        # stylized profile: forehead, snout with open jaw, throat, neck.
        white = QColor(255, 255, 255)
        s = float(size)

        path = QPainterPath()
        # Start at back of head/neck top.
        path.moveTo(s * 0.18, s * 0.42)
        # Forehead bump up over the eye.
        path.cubicTo(s * 0.22, s * 0.30, s * 0.40, s * 0.24, s * 0.55, s * 0.30)
        # Top of snout sloping forward to the nose tip.
        path.lineTo(s * 0.86, s * 0.42)
        path.lineTo(s * 0.92, s * 0.46)
        # Nose tip.
        path.lineTo(s * 0.88, s * 0.50)
        # Upper lip back toward mouth corner (with a small tooth notch).
        path.lineTo(s * 0.70, s * 0.54)
        path.lineTo(s * 0.66, s * 0.60)  # tooth point
        path.lineTo(s * 0.62, s * 0.54)
        # Open mouth gap (jaw line).
        path.lineTo(s * 0.50, s * 0.58)
        # Lower jaw forward and back.
        path.lineTo(s * 0.78, s * 0.62)
        path.lineTo(s * 0.74, s * 0.66)  # lower tooth
        path.lineTo(s * 0.70, s * 0.62)
        path.lineTo(s * 0.46, s * 0.66)
        # Throat curve down to neck.
        path.cubicTo(s * 0.34, s * 0.70, s * 0.26, s * 0.74, s * 0.22, s * 0.80)
        # Bottom of neck.
        path.lineTo(s * 0.16, s * 0.80)
        # Back of neck up to start.
        path.cubicTo(s * 0.14, s * 0.62, s * 0.14, s * 0.50, s * 0.18, s * 0.42)
        path.closeSubpath()

        p.setPen(QPen(white, max(1.0, s / 36.0)))
        p.setBrush(QBrush(white))
        p.drawPath(path)

        # Eye — small disc in the head color so it reads as a hole.
        eye_r = max(0.8, s * 0.045)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(color))
        p.drawEllipse(QPointF(s * 0.42, s * 0.38), eye_r, eye_r)
    finally:
        p.end()
    return pm


def make_icon(state: str) -> QIcon:
    """Build a multi-resolution QIcon for the given state name.

    Uses the bundled rex_icon.png with a small state-colored badge
    overlay (idle stays unbadged). Falls back to a drawn silhouette
    if the asset file is missing.
    """
    base = _load_base_pixmap()
    icon = QIcon()
    if base is not None:
        for s in _SIZES:
            icon.addPixmap(_badge_pixmap(base, s, state), QIcon.Normal, QIcon.Off)
    else:
        color = _STATE_COLORS.get(state, _STATE_COLORS["idle"])
        for s in _SIZES:
            icon.addPixmap(_draw_icon(s, color), QIcon.Normal, QIcon.Off)
    return icon


def make_app_icon() -> QIcon:
    """Icon used for windows (settings dialog, about box, taskbar)."""
    return make_icon("idle")


def available_states() -> tuple[str, ...]:
    return tuple(_STATE_COLORS.keys())


__all__ = ["make_icon", "make_app_icon", "available_states"]
