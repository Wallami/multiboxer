"""
Diagnostic capture tools for troubleshooting window resize and squish issues.

Two independent diagnostic captures:
- SquishCapture: Monitors client area, aspect ratio, and style changes to detect
  UI element squishing when windows swap.
- ResizeCapture: Monitors window rect changes to detect unexpected size growth
  (e.g. 100% doubling) when windows swap.

Both actively poll window state at high frequency and log ALL observed changes
in real-time to the error log, then produce a clipboard-ready report.
"""

import ctypes
import time
from dataclasses import dataclass, field
from datetime import datetime

import win32con
import win32gui
import win32process
import psutil

from . import error_logger as log


# ---------------------------------------------------------------------------
# Snapshot data
# ---------------------------------------------------------------------------

@dataclass
class WindowSnapshot:
    """A single point-in-time capture of window state."""
    timestamp: float
    hwnd: int
    slot_name: str  # "main" or "preview"
    window_rect: tuple[int, int, int, int] | None  # x, y, w, h
    client_rect: tuple[int, int, int, int] | None  # screen x, y, w, h
    client_raw: tuple[int, int, int, int] | None   # raw GetClientRect
    style: int | None
    exstyle: int | None
    dpi: int | None
    placement: tuple | None
    is_visible: bool
    is_minimized: bool
    is_maximized: bool
    title: str


@dataclass
class CaptureSession:
    """A collection of snapshots for a diagnostic session."""
    session_type: str  # "squish" or "resize"
    start_time: float = 0.0
    snapshots: list[WindowSnapshot] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    def add_event(self, msg: str) -> None:
        elapsed = time.time() - self.start_time if self.start_time else 0
        entry = f"[+{elapsed:.3f}s] {msg}"
        self.events.append(entry)
        log.log_info(f"[DIAG-{self.session_type.upper()}] {entry}")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _get_dpi_for_window(hwnd: int) -> int | None:
    """Get the DPI value Windows is using for *hwnd*."""
    try:
        return ctypes.windll.user32.GetDpiForWindow(hwnd)
    except Exception:
        return None


def capture_snapshot(hwnd: int, slot_name: str) -> WindowSnapshot | None:
    """Capture a complete window-state snapshot for *hwnd*."""
    try:
        if not win32gui.IsWindow(hwnd):
            return None

        # Window rect ----------------------------------------------------------
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            window_rect: tuple[int, int, int, int] | None = (left, top, right - left, bottom - top)
        except Exception:
            window_rect = None

        # Client rect (raw) ---------------------------------------------------
        try:
            cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
            client_raw: tuple[int, int, int, int] | None = (cl, ct, cr - cl, cb - ct)
        except Exception:
            client_raw = None

        # Client rect (screen coords) -----------------------------------------
        try:
            cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
            sl, st = win32gui.ClientToScreen(hwnd, (0, 0))
            client_rect: tuple[int, int, int, int] | None = (sl, st, cr - cl, cb - ct)
        except Exception:
            client_rect = None

        # Style ----------------------------------------------------------------
        try:
            style: int | None = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            exstyle: int | None = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        except Exception:
            style = None
            exstyle = None

        # DPI ------------------------------------------------------------------
        dpi = _get_dpi_for_window(hwnd)

        # Placement ------------------------------------------------------------
        try:
            placement = win32gui.GetWindowPlacement(hwnd)
        except Exception:
            placement = None

        # Flags ----------------------------------------------------------------
        is_visible = bool(win32gui.IsWindowVisible(hwnd))
        is_minimized = bool(style & win32con.WS_MINIMIZE) if style else False
        is_maximized = bool(style & win32con.WS_MAXIMIZE) if style else False

        # Title ----------------------------------------------------------------
        try:
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            title = ""

        return WindowSnapshot(
            timestamp=time.time(),
            hwnd=hwnd,
            slot_name=slot_name,
            window_rect=window_rect,
            client_rect=client_rect,
            client_raw=client_raw,
            style=style,
            exstyle=exstyle,
            dpi=dpi,
            placement=placement,
            is_visible=is_visible,
            is_minimized=is_minimized,
            is_maximized=is_maximized,
            title=title,
        )
    except Exception as e:
        log.log_error(f"capture_snapshot failed for hwnd={hwnd}: {e}")
        return None


# ---------------------------------------------------------------------------
# Style flag decoder
# ---------------------------------------------------------------------------

def _format_style_flags(style: int | None) -> str:
    """Decode WS_ style flags into a human-readable string."""
    if style is None:
        return "N/A"
    flags: list[str] = []
    checks = [
        (win32con.WS_POPUP, "POPUP"),
        (win32con.WS_VISIBLE, "VISIBLE"),
        (win32con.WS_CAPTION, "CAPTION"),
        (win32con.WS_THICKFRAME, "THICKFRAME"),
        (win32con.WS_BORDER, "BORDER"),
        (win32con.WS_CHILD, "CHILD"),
        (win32con.WS_MINIMIZE, "MINIMIZED"),
        (win32con.WS_MAXIMIZE, "MAXIMIZED"),
        (win32con.WS_SYSMENU, "SYSMENU"),
        (win32con.WS_MINIMIZEBOX, "MINIMIZEBOX"),
        (win32con.WS_MAXIMIZEBOX, "MAXIMIZEBOX"),
    ]
    for flag, name in checks:
        if style & flag:
            flags.append(name)
    return ", ".join(flags) if flags else "NONE"


def _format_exstyle_flags(exstyle: int | None) -> str:
    """Decode WS_EX_ extended style flags into a human-readable string."""
    if exstyle is None:
        return "N/A"
    flags: list[str] = []
    checks = [
        (win32con.WS_EX_TOPMOST, "TOPMOST"),
        (win32con.WS_EX_TOOLWINDOW, "TOOLWINDOW"),
        (win32con.WS_EX_APPWINDOW, "APPWINDOW"),
        (win32con.WS_EX_LAYERED, "LAYERED"),
        (win32con.WS_EX_COMPOSITED, "COMPOSITED"),
        (win32con.WS_EX_CLIENTEDGE, "CLIENTEDGE"),
        (win32con.WS_EX_WINDOWEDGE, "WINDOWEDGE"),
    ]
    for flag, name in checks:
        if exstyle & flag:
            flags.append(name)
    return ", ".join(flags) if flags else "NONE"


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def format_squish_report(session: CaptureSession,
                         main_hwnd: int | None,
                         preview_hwnd: int | None) -> str:
    """Format the squish-capture session into a clipboard-ready report."""
    lines: list[str] = []
    lines.append("=== SQUISH DIAGNOSTIC REPORT ===")
    lines.append(f"Captured : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Duration : {time.time() - session.start_time:.1f}s")
    lines.append(f"Snapshots: {len(session.snapshots)}")
    lines.append(f"Main HWND: {main_hwnd}   Preview HWND: {preview_hwnd}")
    lines.append("")

    # Event log
    lines.append("--- EVENT LOG ---")
    for event in session.events:
        lines.append(f"  {event}")
    lines.append("")

    # ---- Per-window analysis ------------------------------------------------
    lines.append("--- CLIENT AREA ANALYSIS (SQUISH DETECTION) ---")
    by_hwnd: dict[int, list[WindowSnapshot]] = {}
    for snap in session.snapshots:
        by_hwnd.setdefault(snap.hwnd, []).append(snap)

    for hwnd, snaps in by_hwnd.items():
        lines.append("")
        lines.append(f"  Window HWND={hwnd} (role at start: {snaps[0].slot_name}):")

        if len(snaps) < 2:
            lines.append(f"    Only {len(snaps)} snapshot – cannot compare")
            continue

        first = snaps[0]
        lines.append(f"    Initial window rect : {first.window_rect}")
        lines.append(f"    Initial client      : {first.client_raw}")
        lines.append(f"    Initial DPI         : {first.dpi}")
        lines.append(f"    Initial style       : {_format_style_flags(first.style)}")
        lines.append(f"    Initial exstyle     : {_format_exstyle_flags(first.exstyle)}")
        if first.client_raw and first.client_raw[2] > 0 and first.client_raw[3] > 0:
            lines.append(f"    Initial aspect ratio: {first.client_raw[2] / first.client_raw[3]:.4f}")

        prev = first
        changes_detected = 0

        for snap in snaps[1:]:
            change_parts: list[str] = []

            # Client rect change (primary squish indicator)
            if snap.client_raw != prev.client_raw:
                change_parts.append(f"client: {prev.client_raw} -> {snap.client_raw}")
                # Aspect ratio shift
                if (prev.client_raw and snap.client_raw
                        and prev.client_raw[2] > 0 and prev.client_raw[3] > 0
                        and snap.client_raw[3] > 0):
                    old_ar = prev.client_raw[2] / prev.client_raw[3]
                    new_ar = snap.client_raw[2] / snap.client_raw[3]
                    if abs(old_ar - new_ar) > 0.01:
                        change_parts.append(
                            f"ASPECT RATIO SHIFT: {old_ar:.4f} -> {new_ar:.4f}"
                        )

            # Style change
            if snap.style != prev.style:
                change_parts.append(
                    f"style: {_format_style_flags(prev.style)} -> {_format_style_flags(snap.style)}"
                )

            # Extended style change
            if snap.exstyle != prev.exstyle:
                change_parts.append(
                    f"exstyle: {_format_exstyle_flags(prev.exstyle)} -> {_format_exstyle_flags(snap.exstyle)}"
                )

            # DPI change
            if snap.dpi != prev.dpi:
                change_parts.append(f"DPI: {prev.dpi} -> {snap.dpi}")

            # Window rect change (may go hand-in-hand with client change)
            if snap.window_rect != prev.window_rect:
                change_parts.append(f"window rect: {prev.window_rect} -> {snap.window_rect}")

            if change_parts:
                changes_detected += 1
                elapsed = snap.timestamp - session.start_time
                lines.append(f"    [+{elapsed:.3f}s] CHANGE: {'; '.join(change_parts)}")

            prev = snap

        last = snaps[-1]
        lines.append(f"    Final window rect : {last.window_rect}")
        lines.append(f"    Final client      : {last.client_raw}")
        lines.append(f"    Final DPI         : {last.dpi}")
        lines.append(f"    Final style       : {_format_style_flags(last.style)}")
        lines.append(f"    Final exstyle     : {_format_exstyle_flags(last.exstyle)}")
        if last.client_raw and last.client_raw[2] > 0 and last.client_raw[3] > 0:
            lines.append(f"    Final aspect ratio: {last.client_raw[2] / last.client_raw[3]:.4f}")
        lines.append(f"    Total changes detected: {changes_detected}")

        # Squish summary
        if first.client_raw and last.client_raw:
            w_change = last.client_raw[2] - first.client_raw[2]
            h_change = last.client_raw[3] - first.client_raw[3]
            if abs(w_change) > 5 or abs(h_change) > 5:
                lines.append(
                    f"    *** SQUISH DETECTED: client area shifted by "
                    f"w={w_change:+d}  h={h_change:+d} pixels ***"
                )
            else:
                lines.append("    Client area stable (no squish detected)")

    lines.append("")
    lines.append("=== END SQUISH REPORT ===")
    return "\n".join(lines)


def format_resize_report(session: CaptureSession,
                         main_hwnd: int | None,
                         preview_hwnd: int | None) -> str:
    """Format the resize-capture session into a clipboard-ready report."""
    lines: list[str] = []
    lines.append("=== RESIZE DIAGNOSTIC REPORT ===")
    lines.append(f"Captured : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Duration : {time.time() - session.start_time:.1f}s")
    lines.append(f"Snapshots: {len(session.snapshots)}")
    lines.append(f"Main HWND: {main_hwnd}   Preview HWND: {preview_hwnd}")
    lines.append("")

    # Event log
    lines.append("--- EVENT LOG ---")
    for event in session.events:
        lines.append(f"  {event}")
    lines.append("")

    # ---- Per-window analysis ------------------------------------------------
    lines.append("--- WINDOW RECT ANALYSIS (RESIZE / 100% GROWTH DETECTION) ---")
    by_hwnd: dict[int, list[WindowSnapshot]] = {}
    for snap in session.snapshots:
        by_hwnd.setdefault(snap.hwnd, []).append(snap)

    for hwnd, snaps in by_hwnd.items():
        lines.append("")
        lines.append(f"  Window HWND={hwnd} (role at start: {snaps[0].slot_name}):")

        if len(snaps) < 2:
            lines.append(f"    Only {len(snaps)} snapshot – cannot compare")
            continue

        first = snaps[0]
        lines.append(f"    Initial window rect : {first.window_rect}")
        lines.append(f"    Initial client area : {first.client_raw}")
        lines.append(f"    Initial DPI         : {first.dpi}")
        lines.append(f"    Initial placement   : {first.placement}")
        lines.append(f"    Initial visibility  : visible={first.is_visible} "
                     f"min={first.is_minimized} max={first.is_maximized}")

        prev = first
        changes_detected = 0
        max_w = first.window_rect[2] if first.window_rect else 0
        max_h = first.window_rect[3] if first.window_rect else 0

        for snap in snaps[1:]:
            change_parts: list[str] = []

            # Window rect change (primary resize indicator)
            if snap.window_rect != prev.window_rect:
                change_parts.append(f"rect: {prev.window_rect} -> {snap.window_rect}")
                if snap.window_rect:
                    max_w = max(max_w, snap.window_rect[2])
                    max_h = max(max_h, snap.window_rect[3])
                # Check for doubling / big jump
                if prev.window_rect and snap.window_rect:
                    pw, ph = prev.window_rect[2], prev.window_rect[3]
                    nw, nh = snap.window_rect[2], snap.window_rect[3]
                    if pw > 0 and ph > 0:
                        w_ratio = nw / pw
                        h_ratio = nh / ph
                        if w_ratio > 1.5 or h_ratio > 1.5:
                            change_parts.append(
                                f"!!! SIZE JUMP: {w_ratio:.2f}x width, "
                                f"{h_ratio:.2f}x height !!!"
                            )

            # Client rect change
            if snap.client_raw != prev.client_raw:
                change_parts.append(f"client: {prev.client_raw} -> {snap.client_raw}")

            # Visibility / minimise / maximise toggles
            if snap.is_visible != prev.is_visible:
                change_parts.append(f"visible: {prev.is_visible} -> {snap.is_visible}")
            if snap.is_minimized != prev.is_minimized:
                change_parts.append(f"minimized: {prev.is_minimized} -> {snap.is_minimized}")
            if snap.is_maximized != prev.is_maximized:
                change_parts.append(f"maximized: {prev.is_maximized} -> {snap.is_maximized}")

            # DPI change
            if snap.dpi != prev.dpi:
                change_parts.append(f"DPI: {prev.dpi} -> {snap.dpi}")

            # Placement change
            if snap.placement != prev.placement:
                change_parts.append(f"placement: {prev.placement} -> {snap.placement}")

            if change_parts:
                changes_detected += 1
                elapsed = snap.timestamp - session.start_time
                lines.append(f"    [+{elapsed:.3f}s] CHANGE: {'; '.join(change_parts)}")

            prev = snap

        last = snaps[-1]
        lines.append(f"    Final window rect : {last.window_rect}")
        lines.append(f"    Final client area : {last.client_raw}")
        lines.append(f"    Final DPI         : {last.dpi}")
        lines.append(f"    Final visibility  : visible={last.is_visible} "
                     f"min={last.is_minimized} max={last.is_maximized}")
        lines.append(f"    Total changes detected: {changes_detected}")
        lines.append(f"    Peak observed size: w={max_w}  h={max_h}")

        # Growth summary
        if first.window_rect and last.window_rect:
            fw, fh = first.window_rect[2], first.window_rect[3]
            lw, lh = last.window_rect[2], last.window_rect[3]
            w_growth = lw - fw
            h_growth = lh - fh
            w_pct = (w_growth / fw * 100) if fw > 0 else 0
            h_pct = (h_growth / fh * 100) if fh > 0 else 0
            lines.append(f"    Net growth: w={w_growth:+d} ({w_pct:+.1f}%)  "
                         f"h={h_growth:+d} ({h_pct:+.1f}%)")

            if abs(w_pct) > 50 or abs(h_pct) > 50:
                lines.append(
                    f"    *** MAJOR RESIZE: window grew "
                    f"{w_pct:+.0f}% / {h_pct:+.0f}% ***"
                )

            if fw > 0 and fh > 0:
                if max_w > fw * 1.5 or max_h > fh * 1.5:
                    lines.append(
                        f"    *** PEAK SIZE EXCEEDED 150%: peak {max_w}x{max_h} "
                        f"vs initial {fw}x{fh} ***"
                    )

    lines.append("")
    lines.append("=== END RESIZE REPORT ===")
    return "\n".join(lines)
