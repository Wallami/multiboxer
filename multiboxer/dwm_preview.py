"""
DWM Thumbnail live preview widget.

Uses the Desktop Window Manager (DWM) Thumbnail API to render a live,
GPU-composited preview of another window inside a Qt widget.  Zero CPU
overhead — DWM already has the source window's surface texture.

Usage:
    widget = DwmPreviewWidget(parent)
    widget.set_source(hwnd)     # start live preview
    widget.clear_source()       # stop preview
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from typing import Callable

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QMouseEvent, QMoveEvent, QResizeEvent
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout

from . import error_logger as log

# ---------------------------------------------------------------------------
# DWM ctypes declarations
# ---------------------------------------------------------------------------

_dwmapi = ctypes.windll.dwmapi

# Thumbnail handle is a HANDLE (void *)
HTHUMBNAIL = ctypes.c_void_p


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wt.DWORD),
        ("rcDestination", _RECT),
        ("rcSource", _RECT),
        ("opacity", ctypes.c_byte),
        ("fVisible", wt.BOOL),
        ("fSourceClientAreaOnly", wt.BOOL),
    ]


# Flag constants for DWM_THUMBNAIL_PROPERTIES.dwFlags
DWM_TNP_RECTDESTINATION = 0x00000001
DWM_TNP_RECTSOURCE = 0x00000002
DWM_TNP_OPACITY = 0x00000004
DWM_TNP_VISIBLE = 0x00000008
DWM_TNP_SOURCECLIENTAREAONLY = 0x00000010

# Function signatures
_dwmapi.DwmRegisterThumbnail.argtypes = [wt.HWND, wt.HWND, ctypes.POINTER(HTHUMBNAIL)]
_dwmapi.DwmRegisterThumbnail.restype = ctypes.HRESULT

_dwmapi.DwmUnregisterThumbnail.argtypes = [HTHUMBNAIL]
_dwmapi.DwmUnregisterThumbnail.restype = ctypes.HRESULT

_dwmapi.DwmUpdateThumbnailProperties.argtypes = [HTHUMBNAIL, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
_dwmapi.DwmUpdateThumbnailProperties.restype = ctypes.HRESULT


# ---------------------------------------------------------------------------
# Helper: get source window client size
# ---------------------------------------------------------------------------

def _get_client_size(hwnd: int) -> tuple[int, int] | None:
    """Return (width, height) of *hwnd*'s client area, or None on failure."""
    try:
        import win32gui
        r = win32gui.GetClientRect(hwnd)
        return (r[2] - r[0], r[3] - r[1])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DwmPreviewWidget
# ---------------------------------------------------------------------------

class DwmPreviewWidget(QWidget):
    """A QWidget that shows a live DWM thumbnail of another window.

    Signals:
        clicked  – emitted when the user clicks on the preview.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._thumb: HTHUMBNAIL | None = None
        self._source_hwnd: int | None = None
        self._top_hwnd: int | None = None  # Top-level window HWND for DWM
        self._last_register_hr: int = 0
        self._last_update_hr: int = 0

        # Ensure the widget has a native window handle for DWM
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setMinimumSize(160, 90)

        # Placeholder label shown when no source is set
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._placeholder = QLabel("No preview")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self._placeholder)

        self.setStyleSheet(
            "DwmPreviewWidget { background-color: #1a1a1a; border: 1px solid #444; }"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def source_hwnd(self) -> int | None:
        return self._source_hwnd

    def set_top_level_hwnd(self, hwnd: int) -> None:
        """Set the top-level window HWND used as the DWM thumbnail destination.

        The DWM Thumbnail API *requires* a top-level window; passing a child
        widget HWND silently fails to render.  Call this once after the main
        window has been shown (so that winId() is valid).
        """
        old = self._top_hwnd
        self._top_hwnd = hwnd
        log.log_info(f"DWM preview: top-level HWND set to {hwnd}")
        # Re-register if a source was already active under the old HWND
        if self._source_hwnd is not None and old != hwnd:
            src = self._source_hwnd
            self.clear_source()
            self.set_source(src)

    def set_source(self, hwnd: int) -> bool:
        """Register a live DWM thumbnail for *hwnd*.
        Returns True on success."""
        # If already showing this source, just update layout
        if self._source_hwnd == hwnd and self._thumb is not None:
            self._update_thumb_rect()
            return True

        # Clear any previous thumbnail
        self.clear_source()

        # DWM requires a TOP-LEVEL window as destination
        dest_hwnd = self._top_hwnd or int(self.winId())
        if not dest_hwnd:
            log.log_error("DWM preview: no destination HWND available", include_trace=False)
            return False

        thumb = HTHUMBNAIL()
        hr = _dwmapi.DwmRegisterThumbnail(dest_hwnd, hwnd, ctypes.byref(thumb))
        self._last_register_hr = hr
        if hr != 0:
            log.log_error(
                f"DwmRegisterThumbnail failed: HRESULT=0x{hr & 0xFFFFFFFF:08X} "
                f"dest={dest_hwnd} src={hwnd}",
                include_trace=False,
            )
            return False

        self._thumb = thumb
        self._source_hwnd = hwnd
        self._placeholder.setVisible(False)
        self._update_thumb_rect()
        log.log_info(f"DWM thumbnail registered: src_hwnd={hwnd} dest_hwnd={dest_hwnd}")
        return True

    def clear_source(self) -> None:
        """Unregister the current thumbnail."""
        if self._thumb is not None:
            try:
                _dwmapi.DwmUnregisterThumbnail(self._thumb)
            except Exception as e:
                log.log_warning(f"DwmUnregisterThumbnail error: {e}")
            self._thumb = None
        self._source_hwnd = None
        self._last_register_hr = 0
        self._last_update_hr = 0
        self._placeholder.setVisible(True)

    def get_diagnostic_info(self) -> str:
        """Return a formatted diagnostic string about the preview widget state."""
        lines: list[str] = []
        lines.append("=== DWM Preview Diagnostic ===")
        lines.append(f"Widget visible: {self.isVisible()}")
        lines.append(f"Widget size: {self.width()}x{self.height()}")
        lines.append(f"Widget pos (in parent): ({self.x()}, {self.y()})")

        try:
            wid = int(self.winId()) if self.winId() else 0
        except Exception:
            wid = 0
        lines.append(f"Widget native HWND (winId): {wid}")
        lines.append(f"Top-level HWND: {self._top_hwnd}")
        lines.append(f"Source HWND: {self._source_hwnd}")
        lines.append(f"Thumbnail registered: {self._thumb is not None}")
        if self._thumb is not None:
            lines.append(f"Thumbnail handle: {self._thumb.value if self._thumb else 'None'}")
        lines.append(f"Last register HRESULT: 0x{self._last_register_hr & 0xFFFFFFFF:08X}")
        lines.append(f"Last update HRESULT: 0x{self._last_update_hr & 0xFFFFFFFF:08X}")
        lines.append(f"Placeholder visible: {self._placeholder.isVisible()}")

        # Map position to top-level window
        top = self.window()
        if top:
            pos = self.mapTo(top, QPoint(0, 0))
            lines.append(f"Mapped pos in top-level: ({pos.x()}, {pos.y()})")
            lines.append(f"Top-level window size: {top.width()}x{top.height()}")
            try:
                top_wid = int(top.winId()) if top.winId() else 0
            except Exception:
                top_wid = 0
            lines.append(f"Top-level winId: {top_wid}")

        # Check source window validity
        if self._source_hwnd:
            try:
                import win32gui
                is_valid = win32gui.IsWindow(self._source_hwnd)
                is_visible = win32gui.IsWindowVisible(self._source_hwnd) if is_valid else False
                lines.append(f"Source window valid: {is_valid}")
                lines.append(f"Source window visible: {is_visible}")
                if is_valid:
                    cr = win32gui.GetClientRect(self._source_hwnd)
                    lines.append(f"Source client rect: {cr}")
            except Exception as e:
                lines.append(f"Source window check error: {e}")

        # Computed destination rect info
        if self._thumb and self._source_hwnd:
            base_x, base_y = 0, 0
            if self._top_hwnd and top:
                pos = self.mapTo(top, QPoint(0, 0))
                base_x, base_y = pos.x(), pos.y()
            src_size = _get_client_size(self._source_hwnd)
            lines.append(f"Dest rect base: ({base_x}, {base_y})")
            lines.append(f"Dest rect area: {self.width()}x{self.height()}")
            if src_size:
                lines.append(f"Source client size: {src_size[0]}x{src_size[1]}")

        lines.append("=== END Preview Diagnostic ===")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_thumb_rect(self) -> None:
        """Tell DWM to render the source into our widget rect, aspect-correct.

        When registered with the top-level HWND the destination rectangle must
        be in the top-level window's client-area coordinate space, so we use
        ``mapTo(self.window(), ...)`` to compute the base offset.
        """
        if self._thumb is None or self._source_hwnd is None:
            return

        dest_w = self.width()
        dest_h = self.height()
        if dest_w < 1 or dest_h < 1:
            return

        # Base offset: when using the top-level HWND, translate widget coords
        base_x, base_y = 0, 0
        if self._top_hwnd:
            top = self.window()
            if top:
                pos = self.mapTo(top, QPoint(0, 0))
                base_x = pos.x()
                base_y = pos.y()

        # Get source client size to compute aspect ratio
        src_size = _get_client_size(self._source_hwnd)
        if src_size and src_size[0] > 0 and src_size[1] > 0:
            src_w, src_h = src_size
            src_aspect = src_w / src_h
            dest_aspect = dest_w / dest_h

            if src_aspect > dest_aspect:
                # Source is wider — fit to width, letterbox top/bottom
                fit_w = dest_w
                fit_h = int(dest_w / src_aspect)
            else:
                # Source is taller — fit to height, pillarbox left/right
                fit_h = dest_h
                fit_w = int(dest_h * src_aspect)

            x_off = (dest_w - fit_w) // 2
            y_off = (dest_h - fit_h) // 2
        else:
            # Fallback: stretch to fill
            x_off, y_off, fit_w, fit_h = 0, 0, dest_w, dest_h

        props = DWM_THUMBNAIL_PROPERTIES()
        props.dwFlags = (
            DWM_TNP_RECTDESTINATION
            | DWM_TNP_VISIBLE
            | DWM_TNP_SOURCECLIENTAREAONLY
            | DWM_TNP_OPACITY
        )
        props.rcDestination = _RECT(
            base_x + x_off, base_y + y_off,
            base_x + x_off + fit_w, base_y + y_off + fit_h,
        )
        props.fVisible = True
        props.fSourceClientAreaOnly = True
        props.opacity = 255

        hr = _dwmapi.DwmUpdateThumbnailProperties(self._thumb, ctypes.byref(props))
        self._last_update_hr = hr
        if hr != 0:
            log.log_warning(
                f"DwmUpdateThumbnailProperties failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}"
            )

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_thumb_rect()

    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        self._update_thumb_rect()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def closeEvent(self, event) -> None:
        self.clear_source()
        super().closeEvent(event)
