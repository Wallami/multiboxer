"""
Tiled Multiboxer - Simple tiled desktop window manager for EverQuest.
Uses desktop positioning instead of embedding for stability.

"""

import atexit
import signal
import sys
import win32con
import win32gui
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QAction, QFont, QCloseEvent, QClipboard, QMouseEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QMessageBox, QMenu, QMenuBar,
    QComboBox, QGroupBox, QCheckBox
)

from .tiled_manager import TiledWindowManager
from . import error_logger as log
from .diagnostics import (
    CaptureSession, capture_snapshot,
    format_squish_report, format_resize_report,
)
from .dwm_preview import DwmPreviewWidget


class StatusIndicator(QFrame):
    """Visual status indicator for a slot."""
    
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(1)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        
        self.title_label = QLabel(title)
        self.title_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        layout.addWidget(self.title_label)
        
        self.status_label = QLabel("Empty")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)
        
        self.hwnd_label = QLabel("")
        self.hwnd_label.setStyleSheet("color: #666; font-size: 9px;")
        layout.addWidget(self.hwnd_label)
        
        self.set_empty()
        
    def set_empty(self):
        self.status_label.setText("Empty - Click 'Grab' to assign")
        self.status_label.setStyleSheet("color: #888;")
        self.hwnd_label.setText("")
        self.setStyleSheet("background-color: #2a2a2a;")
        
    def set_active(self, title: str, hwnd: int):
        display_title = title if title else "Game Window"
        self.status_label.setText(display_title[:40])
        self.status_label.setStyleSheet("color: #4CAF50;")
        self.hwnd_label.setText(f"HWND: {hwnd}")
        self.setStyleSheet("background-color: #1a3a1a;")
        
    def set_error(self, message: str):
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #f44336;")
        self.setStyleSheet("background-color: #3a1a1a;")


class LayoutPreview(QFrame):
    """Visual preview of the tiled layout."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 120)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Sunken)
        self.setStyleSheet("background-color: #1a1a1a;")
        
        # Main area (large)
        self.main_rect = QFrame(self)
        self.main_rect.setGeometry(5, 5, 140, 110)
        self.main_rect.setStyleSheet("background-color: #2196F3; border: 1px solid #1976D2;")
        self.main_label = QLabel("MAIN", self.main_rect)
        self.main_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_label.setGeometry(0, 0, 140, 110)
        self.main_label.setStyleSheet("color: white; font-weight: bold;")
        
        # Preview area (small, top-right corner)
        self.preview_rect = QFrame(self)
        self.preview_rect.setGeometry(150, 5, 45, 35)
        self.preview_rect.setStyleSheet("background-color: #FF9800; border: 1px solid #F57C00;")
        self.preview_label = QLabel("PV", self.preview_rect)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setGeometry(0, 0, 45, 35)
        self.preview_label.setStyleSheet("color: white; font-size: 10px;")


class TiledMultiboxerApp(QMainWindow):
    """Main application window for tiled multiboxer."""
    
    def __init__(self):
        super().__init__()
        
        # Log startup (init_log already called in main())
        log.log_info("TiledMultiboxerApp.__init__ starting")
        
        self.manager = TiledWindowManager()
        
        # Mouse drag tracking for frameless window
        self._drag_pos: QPoint | None = None
        
        self.setWindowTitle("EQ Tiled Multiboxer")
        self.setMinimumSize(320, 700)
        self.resize(320, 780)
        
        # Make window frameless but keep it on top
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint
        )
        
        # Global stylesheet: borders, group box titles, button defaults
        self.setStyleSheet("""
            QMainWindow {
                border: 2px solid #444;
                background-color: #2b2b2b;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 4px;
                margin-top: 14px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                padding: 5px 10px;
                border-radius: 3px;
            }
        """)
        
        # Diagnostic capture state
        self._squish_session: CaptureSession | None = None
        self._resize_session: CaptureSession | None = None
        self._squish_timer: QTimer | None = None
        self._resize_timer: QTimer | None = None

        self._setup_ui()
        self._setup_layout()
        self._setup_timers()
        
    def _setup_ui(self):
        """Setup the user interface."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(10, 6, 10, 10)
        
        # Custom title bar (for frameless window)
        title_bar = QWidget()
        title_bar_layout = QHBoxLayout(title_bar)
        title_bar_layout.setContentsMargins(0, 0, 0, 0)
        title_bar_layout.setSpacing(4)
        
        # Title
        title = QLabel("Tiled Multiboxer")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title_bar_layout.addWidget(title, 1)
        
        # Minimize button
        minimize_btn = QPushButton("—")
        minimize_btn.setFixedSize(28, 28)
        minimize_btn.setStyleSheet("""
            QPushButton {
                background-color: #444;
                border: none;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)
        minimize_btn.clicked.connect(self.showMinimized)
        title_bar_layout.addWidget(minimize_btn)
        
        # Close button
        close_btn = QPushButton("X")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #c42b1c;
                border: none;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e81123;
            }
        """)
        close_btn.clicked.connect(self.close)
        title_bar_layout.addWidget(close_btn)
        
        main_layout.addWidget(title_bar)
        
        # Layout preview
        layout_group = QGroupBox("Layout Preview")
        layout_vbox = QVBoxLayout(layout_group)
        layout_vbox.setContentsMargins(8, 16, 8, 8)
        self.layout_preview = LayoutPreview()
        layout_vbox.addWidget(self.layout_preview, alignment=Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(layout_group)
        
        # Slot status indicators
        slots_group = QGroupBox("Window Slots")
        slots_layout = QVBoxLayout(slots_group)
        slots_layout.setSpacing(6)
        slots_layout.setContentsMargins(8, 16, 8, 8)
        
        # Main slot
        main_row = QHBoxLayout()
        main_row.setSpacing(6)
        self.main_indicator = StatusIndicator("Main Window")
        main_row.addWidget(self.main_indicator, 1)
        self.grab_main_btn = QPushButton("Grab")
        self.grab_main_btn.setFixedWidth(60)
        self.grab_main_btn.setFixedHeight(30)
        self.grab_main_btn.clicked.connect(self._grab_to_main)
        main_row.addWidget(self.grab_main_btn)
        slots_layout.addLayout(main_row)
        
        # Preview slot
        preview_row = QHBoxLayout()
        preview_row.setSpacing(6)
        self.preview_indicator = StatusIndicator("Preview Window")
        preview_row.addWidget(self.preview_indicator, 1)
        self.grab_preview_btn = QPushButton("Grab")
        self.grab_preview_btn.setFixedWidth(60)
        self.grab_preview_btn.setFixedHeight(30)
        self.grab_preview_btn.clicked.connect(self._grab_to_preview)
        preview_row.addWidget(self.grab_preview_btn)
        slots_layout.addLayout(preview_row)
        
        main_layout.addWidget(slots_group)
        
        # Controls
        controls_group = QGroupBox("Controls")
        controls_layout = QVBoxLayout(controls_group)
        controls_layout.setSpacing(8)
        controls_layout.setContentsMargins(8, 16, 8, 8)

        # Preserve-size toggle
        self.preserve_size_cb = QCheckBox("Preserve window size")
        self.preserve_size_cb.setChecked(self.manager.preserve_size)
        self.preserve_size_cb.setToolTip(
            "When checked, swapping only changes window position — each game\n"
            "keeps its original resolution so UI elements don't move."
        )
        self.preserve_size_cb.toggled.connect(self._on_preserve_size_toggled)
        controls_layout.addWidget(self.preserve_size_cb)
        
        # Swap button
        self.swap_btn = QPushButton("Swap Windows")
        self.swap_btn.setFixedHeight(36)
        self.swap_btn.clicked.connect(self._do_swap)
        controls_layout.addWidget(self.swap_btn)
        
        # Release button
        self.release_btn = QPushButton("Release All")
        self.release_btn.setFixedHeight(30)
        self.release_btn.clicked.connect(self._release_all)
        controls_layout.addWidget(self.release_btn)
        
        main_layout.addWidget(controls_group)

        # Live preview of the inactive (preview-slot) game window
        preview_group = QGroupBox("Live Preview (click to swap)")
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(6, 16, 6, 6)
        self.live_preview = DwmPreviewWidget()
        self.live_preview.setMinimumHeight(140)
        self.live_preview.clicked.connect(self._do_swap)
        preview_layout.addWidget(self.live_preview, 1)  # stretch to fill
        main_layout.addWidget(preview_group, 1)  # give live preview the extra space
        
        # Status bar
        self.status_label = QLabel("Click Swap to switch windows")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #888; font-style: italic;")
        main_layout.addWidget(self.status_label)
        
        # Menu bar
        self._setup_menu()
        
    def _setup_menu(self):
        """Setup menu bar."""
        menubar = self.menuBar()
        
        file_menu = menubar.addMenu("File")
        
        refresh_action = QAction("Refresh Layout", self)
        refresh_action.triggered.connect(self._refresh_layout)
        file_menu.addAction(refresh_action)
        
        file_menu.addSeparator()
        
        watchdog_action = QAction("Copy Watchdog Snapshot", self)
        watchdog_action.triggered.connect(self._copy_watchdog_snapshot)
        file_menu.addAction(watchdog_action)

        file_menu.addSeparator()

        # --- Diagnostic captures ---
        self._squish_action = QAction("Start Squish Capture", self)
        self._squish_action.triggered.connect(self._toggle_squish_capture)
        file_menu.addAction(self._squish_action)

        self._resize_action = QAction("Start Resize Capture", self)
        self._resize_action.triggered.connect(self._toggle_resize_capture)
        file_menu.addAction(self._resize_action)

        self._preview_diag_action = QAction("Copy Preview Diagnostic", self)
        self._preview_diag_action.triggered.connect(self._copy_preview_diagnostic)
        file_menu.addAction(self._preview_diag_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        help_menu = menubar.addMenu("Help")
        
        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
        
    def _setup_layout(self):
        """Calculate and set the tiled layout based on screen."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
            
        geom = screen.availableGeometry()
        
        # Main takes most of the screen
        main_width = int(geom.width() * 0.75)
        main_height = geom.height()
        main_x = geom.x()
        main_y = geom.y()
        
        # Preview in top-right corner
        preview_width = geom.width() - main_width
        preview_height = int(geom.height() * 0.35)
        preview_x = geom.x() + main_width
        preview_y = geom.y()
        
        self.manager.set_layout(
            (main_x, main_y, main_width, main_height),
            (preview_x, preview_y, preview_width, preview_height)
        )
        
        # Position this control window below preview
        self.move(preview_x, preview_y + preview_height + 10)
        
    def _setup_timers(self):
        """Setup refresh timers."""
        # Periodic refresh to catch drifted windows (only if moved)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._periodic_refresh)
        self.refresh_timer.start(5000)  # Every 5 seconds - only repositions if drifted
        
        # UI update timer
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._update_ui)
        self.ui_timer.start(500)
        
    def _do_swap(self):
        """Perform window swap."""
        log.log_info("_do_swap triggered")

        # Notify active diagnostic captures about the swap event
        for session in [self._squish_session, self._resize_session]:
            if session is not None:
                main_h, prev_h = self._current_hwnds()
                session.add_event(
                    f"SWAP TRIGGERED  main_hwnd={main_h}  preview_hwnd={prev_h}"
                )

        try:
            if self.manager.swap_windows():
                # Post-swap diagnostic snapshots
                for session in [self._squish_session, self._resize_session]:
                    if session is not None:
                        new_main, new_prev = self._current_hwnds()
                        session.add_event(
                            f"SWAP COMPLETE   new_main={new_main}  new_preview={new_prev}"
                        )
                        self._capture_tick(session)

                self.status_label.setText("Windows swapped!")
                self.status_label.setStyleSheet("color: #4CAF50;")
                self._update_ui()

                # Update live preview in a deferred call so the Qt event
                # loop can process any pending events first.  This avoids
                # DWM API calls running while the win32 message queue is
                # still settling from the swap.
                QTimer.singleShot(100, self._update_live_preview)

                log.log_info("_do_swap completed successfully")
            else:
                self.status_label.setText("Cannot swap - need 2 windows")
                self.status_label.setStyleSheet("color: #FF9800;")
                log.log_warning("_do_swap failed - swap_windows returned False")
        except Exception as e:
            log.log_exception("_do_swap")
            self.status_label.setText(f"Swap error: {e}")
            self.status_label.setStyleSheet("color: #f44336;")
            
    def _grab_to_main(self):
        """Grab first available eqgame window to main slot."""
        windows = self.manager.find_eqgame_windows()
        log.log_info(f"_grab_to_main: found {len(windows)} EQ windows: {windows}")
        
        # Filter out windows already in slots
        preview_hwnd = self.manager.preview_slot.hwnd if self.manager.preview_slot else None
        available = [w for w in windows if w != preview_hwnd]
        
        if not available:
            log.log_warning("_grab_to_main: no available windows after filtering")
            QMessageBox.information(self, "No Windows", 
                "No available eqgame.exe windows found.")
            return
            
        # Use first available
        hwnd = available[0]
        log.log_info(f"_grab_to_main: attempting to assign hwnd={hwnd}")
        if self.manager.assign_to_main(hwnd):
            log.log_info(f"_grab_to_main: SUCCESS assigned hwnd={hwnd}")
            self.status_label.setText(f"Grabbed to main: HWND {hwnd}")
            self._update_ui()
            self._update_live_preview()
        else:
            log.log_error(f"_grab_to_main: FAILED to assign hwnd={hwnd}", include_trace=False)
            self.main_indicator.set_error("Failed to grab window")
            
    def _grab_to_preview(self):
        """Grab first available eqgame window to preview slot."""
        windows = self.manager.find_eqgame_windows()
        log.log_info(f"_grab_to_preview: found {len(windows)} EQ windows: {windows}")
        
        # Filter out windows already in main
        main_hwnd = self.manager.main_slot.hwnd if self.manager.main_slot else None
        available = [w for w in windows if w != main_hwnd]
        
        if not available:
            log.log_warning("_grab_to_preview: no available windows after filtering")
            QMessageBox.information(self, "No Windows",
                "No available eqgame.exe windows found.")
            return
            
        # Use first available
        hwnd = available[0]
        log.log_info(f"_grab_to_preview: attempting to assign hwnd={hwnd}")
        if self.manager.assign_to_preview(hwnd):
            log.log_info(f"_grab_to_preview: SUCCESS assigned hwnd={hwnd}")
            self.status_label.setText(f"Grabbed to preview: HWND {hwnd}")
            self._update_ui()
            self._update_live_preview()
        else:
            log.log_error(f"_grab_to_preview: FAILED to assign hwnd={hwnd}", include_trace=False)
            self.preview_indicator.set_error("Failed to grab window")
            
    def _release_all(self):
        """Release all managed windows after user confirmation."""
        reply = QMessageBox.question(
            self, "Confirm Release",
            "Release all managed windows?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.manager.release_all()
        self.main_indicator.set_empty()
        self.preview_indicator.set_empty()
        self.live_preview.clear_source()
        self.status_label.setText("All windows released")

    def _on_preserve_size_toggled(self, checked: bool) -> None:
        """Handle preserve-size checkbox toggle."""
        self.manager.preserve_size = checked
        mode = "position-only (prevents squish)" if checked else "full resize to slot"
        log.log_info(f"preserve_size toggled: {checked} — mode: {mode}")
        self.status_label.setText(f"Swap mode: {mode}")
        self.status_label.setStyleSheet("color: #2196F3;")
        
    def _refresh_layout(self):
        """Refresh the layout and reposition windows."""
        self._setup_layout()
        self.manager.refresh_positions()
        self._update_ui()
        self.status_label.setText("Layout refreshed")
        
    def _periodic_refresh(self):
        """Periodic refresh to maintain window positions."""
        try:
            self.manager.refresh_positions()
            self._log_periodic_diagnostic()
        except Exception as e:
            log.log_exception("_periodic_refresh")

    def _log_periodic_diagnostic(self):
        """Log a periodic diagnostic if slots are empty but EQ windows exist."""
        try:
            main_hwnd = self.manager.main_slot.hwnd if self.manager.main_slot else None
            preview_hwnd = self.manager.preview_slot.hwnd if self.manager.preview_slot else None
            
            # Only log if at least one slot is empty
            if main_hwnd and preview_hwnd:
                return
            
            eq_windows = self.manager.find_eqgame_windows()
            if not eq_windows:
                return
            
            # There are EQ windows but at least one slot is unassigned
            assigned = [h for h in [main_hwnd, preview_hwnd] if h is not None]
            unassigned = [w for w in eq_windows if w not in assigned]
            if unassigned:
                slot_status = f"main_hwnd={main_hwnd}, preview_hwnd={preview_hwnd}"
                window_list = ", ".join(str(w) for w in eq_windows)
                log.log_warning(
                    f"DIAGNOSTIC: {len(unassigned)} EQ window(s) unassigned. "
                    f"Slots: [{slot_status}]. EQ windows found: [{window_list}]"
                )
        except Exception:
            pass
        
    def _update_ui(self):
        """Update UI to reflect current state."""
        # Main slot
        if self.manager.main_slot and self.manager.main_slot.hwnd:
            hwnd = self.manager.main_slot.hwnd
            if self.manager.is_valid_window(hwnd):
                title = self.manager.get_window_title(hwnd)
                self.main_indicator.set_active(title, hwnd)
            else:
                log.log_warning(f"_update_ui: main slot hwnd={hwnd} failed is_valid_window check - clearing assignment")
                self.manager.main_slot.hwnd = None
                self.main_indicator.set_empty()
        else:
            self.main_indicator.set_empty()
            
        # Preview slot
        if self.manager.preview_slot and self.manager.preview_slot.hwnd:
            hwnd = self.manager.preview_slot.hwnd
            if self.manager.is_valid_window(hwnd):
                title = self.manager.get_window_title(hwnd)
                self.preview_indicator.set_active(title, hwnd)
            else:
                log.log_warning(f"_update_ui: preview slot hwnd={hwnd} failed is_valid_window check - clearing assignment")
                self.manager.preview_slot.hwnd = None
                self.preview_indicator.set_empty()
        else:
            self.preview_indicator.set_empty()
            
        # Update swap button state
        both_filled = bool(
            self.manager.main_slot and self.manager.main_slot.hwnd and
            self.manager.preview_slot and self.manager.preview_slot.hwnd
        )
        self.swap_btn.setEnabled(both_filled)

    def _update_live_preview(self) -> None:
        """Point the DWM live preview at the current preview-slot window."""
        try:
            preview_hwnd = (
                self.manager.preview_slot.hwnd
                if self.manager.preview_slot
                else None
            )
            if preview_hwnd and self.manager.is_valid_window(preview_hwnd):
                # Skip DWM re-registration if the source hasn't changed
                if self.live_preview.source_hwnd != preview_hwnd:
                    self.live_preview.set_source(preview_hwnd)
            else:
                self.live_preview.clear_source()
        except Exception as e:
            log.log_warning(f"_update_live_preview error: {e}")
            try:
                self.live_preview.clear_source()
            except Exception:
                pass
    
    def _copy_preview_diagnostic(self):
        """Copy DWM preview diagnostic info to clipboard."""
        report = self.live_preview.get_diagnostic_info()
        QApplication.clipboard().setText(report)
        self.status_label.setText("Preview diagnostic copied!")
        self.status_label.setStyleSheet("color: #4CAF50;")

    def _copy_watchdog_snapshot(self):
        """Copy comprehensive watchdog snapshot to clipboard."""
        lines = []
        lines.append(f"Watchdog Snapshot {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 60)
        
        # Screen info
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            lines.append(f"Screen: {geom.width()}x{geom.height()} at ({geom.x()}, {geom.y()})")
        
        # Slot configurations
        lines.append("")
        lines.append("=== SLOT CONFIGURATION ===")
        if self.manager.main_slot:
            ms = self.manager.main_slot
            lines.append(f"Main Slot Target: x={ms.x}, y={ms.y}, w={ms.width}, h={ms.height}")
            lines.append(f"Main Slot HWND: {ms.hwnd}")
        else:
            lines.append("Main Slot: Not configured")
            
        if self.manager.preview_slot:
            ps = self.manager.preview_slot
            lines.append(f"Preview Slot Target: x={ps.x}, y={ps.y}, w={ps.width}, h={ps.height}")
            lines.append(f"Preview Slot HWND: {ps.hwnd}")
        else:
            lines.append("Preview Slot: Not configured")
        
        # Actual window states
        lines.append("")
        lines.append("=== ACTUAL WINDOW STATES ===")
        
        for slot_name, slot in [("Main", self.manager.main_slot), ("Preview", self.manager.preview_slot)]:
            if slot and slot.hwnd:
                hwnd = slot.hwnd
                lines.append(f"")
                lines.append(f"--- {slot_name} Window (HWND {hwnd}) ---")
                
                if self.manager.is_valid_window(hwnd):
                    # Get actual window rect
                    try:
                        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                        actual_w = right - left
                        actual_h = bottom - top
                        lines.append(f"  Actual Position: x={left}, y={top}")
                        lines.append(f"  Actual Size: w={actual_w}, h={actual_h}")
                        
                        # Compare to target
                        target_w = slot.width
                        target_h = slot.height
                        lines.append(f"  Target Size: w={target_w}, h={target_h}")
                        lines.append(f"  Size Diff: w={actual_w - target_w}, h={actual_h - target_h}")
                        lines.append(f"  Position Diff: x={left - slot.x}, y={top - slot.y}")
                    except Exception as e:
                        lines.append(f"  Error reading rect: {e}")
                    
                    # Get client rect (inner area)
                    try:
                        client_left, client_top, client_right, client_bottom = win32gui.GetClientRect(hwnd)
                        client_w = client_right - client_left
                        client_h = client_bottom - client_top
                        lines.append(f"  Client Area: w={client_w}, h={client_h}")
                    except Exception as e:
                        lines.append(f"  Error reading client rect: {e}")
                    
                    # Get window style
                    try:
                        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
                        exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                        lines.append(f"  Style: {style} (0x{style & 0xFFFFFFFF:08X})")
                        lines.append(f"  ExStyle: {exstyle} (0x{exstyle & 0xFFFFFFFF:08X})")
                        
                        # Decode common flags
                        flags = []
                        if style & win32con.WS_POPUP: flags.append("WS_POPUP")
                        if style & win32con.WS_VISIBLE: flags.append("WS_VISIBLE")
                        if style & win32con.WS_CAPTION: flags.append("WS_CAPTION")
                        if style & win32con.WS_THICKFRAME: flags.append("WS_THICKFRAME")
                        if style & win32con.WS_BORDER: flags.append("WS_BORDER")
                        if style & win32con.WS_CHILD: flags.append("WS_CHILD")
                        lines.append(f"  Flags: {', '.join(flags) if flags else 'none'}")
                    except Exception as e:
                        lines.append(f"  Error reading style: {e}")
                    
                    # Get window title
                    try:
                        title = win32gui.GetWindowText(hwnd)
                        lines.append(f"  Title: '{title}'")
                    except Exception:
                        pass
                else:
                    lines.append(f"  Window is INVALID")
            else:
                lines.append(f"")
                lines.append(f"--- {slot_name} Window ---")
                lines.append(f"  No window assigned")
        
        # Find other EQ windows
        lines.append("")
        lines.append("=== OTHER EQ WINDOWS ===")
        other_windows = self.manager.find_eqgame_windows()
        main_hwnd = self.manager.main_slot.hwnd if self.manager.main_slot else None
        preview_hwnd = self.manager.preview_slot.hwnd if self.manager.preview_slot else None
        unassigned = [w for w in other_windows if w != main_hwnd and w != preview_hwnd]
        
        if unassigned:
            for hwnd in unassigned:
                try:
                    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                    title = win32gui.GetWindowText(hwnd)
                    lines.append(f"  HWND {hwnd}: '{title}' at ({left}, {top}) size {right-left}x{bottom-top}")
                except Exception:
                    lines.append(f"  HWND {hwnd}: unable to read")
        else:
            lines.append("  None")
        
        lines.append("")
        lines.append("=== END SNAPSHOT ===")
        
        # Copy to clipboard
        snapshot = "\n".join(lines)
        clipboard = QApplication.clipboard()
        clipboard.setText(snapshot)
        
        self.status_label.setText("Watchdog snapshot copied!")
        self.status_label.setStyleSheet("color: #4CAF50;")
        
    def _show_about(self):
        """Show about dialog."""
        QMessageBox.about(self, "About Tiled Multiboxer",
            "Tiled Multiboxer v2.0\n\n"
            "Simple tiled desktop manager for EverQuest multiboxing.\n\n"
            "Features:\n"
            "- Positions game windows on desktop\n"
            "- Swap button to switch between main and preview\n"
            "- No window embedding - stable and reliable\n\n"
            "Usage:\n"
            "1. Launch two EQ clients\n"
            "2. Click 'Grab' to assign windows\n"
            "3. Click 'Swap Windows' to switch focus"
        )
        
    # ------------------------------------------------------------------
    # Diagnostic capture helpers
    # ------------------------------------------------------------------

    def _current_hwnds(self) -> tuple[int | None, int | None]:
        """Return (main_hwnd, preview_hwnd) for the current slot state."""
        main_hwnd = self.manager.main_slot.hwnd if self.manager.main_slot else None
        preview_hwnd = self.manager.preview_slot.hwnd if self.manager.preview_slot else None
        return main_hwnd, preview_hwnd

    def _capture_tick(self, session: CaptureSession) -> None:
        """Take one snapshot of every managed window and append to *session*."""
        main_hwnd, preview_hwnd = self._current_hwnds()
        for hwnd, role in [(main_hwnd, "main"), (preview_hwnd, "preview")]:
            if hwnd is not None:
                snap = capture_snapshot(hwnd, role)
                if snap is not None:
                    session.snapshots.append(snap)

    # ---- Squish capture -------------------------------------------------

    def _toggle_squish_capture(self) -> None:
        """Start or stop the squish diagnostic capture."""
        if self._squish_session is not None:
            self._stop_squish_capture()
        else:
            self._start_squish_capture()

    def _start_squish_capture(self) -> None:
        self._squish_session = CaptureSession(session_type="squish", start_time=time.time())
        self._squish_session.add_event("Capture started")

        main_hwnd, preview_hwnd = self._current_hwnds()
        self._squish_session.add_event(f"main_hwnd={main_hwnd}  preview_hwnd={preview_hwnd}")

        # Initial snapshot
        self._capture_tick(self._squish_session)

        # High-frequency polling timer (50 ms)
        self._squish_timer = QTimer(self)
        self._squish_timer.timeout.connect(self._squish_poll)
        self._squish_timer.start(50)

        # Auto-stop after 30 s
        QTimer.singleShot(30000, self._auto_stop_squish)

        self._squish_action.setText("Stop Squish Capture (recording…)")
        self.status_label.setText("Squish capture ACTIVE – perform a swap then stop")
        self.status_label.setStyleSheet("color: #FF9800; font-weight: bold;")
        log.log_info("[DIAG-SQUISH] Capture started")

    def _squish_poll(self) -> None:
        if self._squish_session is None:
            return
        self._capture_tick(self._squish_session)

    def _auto_stop_squish(self) -> None:
        if self._squish_session is not None:
            self._squish_session.add_event("Auto-stopped after 30 s timeout")
            self._stop_squish_capture()

    def _stop_squish_capture(self) -> None:
        if self._squish_timer is not None:
            self._squish_timer.stop()
            self._squish_timer = None

        session = self._squish_session
        self._squish_session = None
        self._squish_action.setText("Start Squish Capture")

        if session is None:
            return

        session.add_event("Capture stopped")
        # Final snapshot
        main_hwnd, preview_hwnd = self._current_hwnds()
        for hwnd, role in [(main_hwnd, "main"), (preview_hwnd, "preview")]:
            if hwnd is not None:
                snap = capture_snapshot(hwnd, role)
                if snap is not None:
                    session.snapshots.append(snap)

        report = format_squish_report(session, main_hwnd, preview_hwnd)
        QApplication.clipboard().setText(report)
        log.log_info(f"[DIAG-SQUISH] Capture stopped – {len(session.snapshots)} snapshots, report copied")
        self.status_label.setText(f"Squish report copied ({len(session.snapshots)} snapshots)")
        self.status_label.setStyleSheet("color: #4CAF50;")

    # ---- Resize capture -------------------------------------------------

    def _toggle_resize_capture(self) -> None:
        """Start or stop the resize diagnostic capture."""
        if self._resize_session is not None:
            self._stop_resize_capture()
        else:
            self._start_resize_capture()

    def _start_resize_capture(self) -> None:
        self._resize_session = CaptureSession(session_type="resize", start_time=time.time())
        self._resize_session.add_event("Capture started")

        main_hwnd, preview_hwnd = self._current_hwnds()
        self._resize_session.add_event(f"main_hwnd={main_hwnd}  preview_hwnd={preview_hwnd}")

        # Initial snapshot
        self._capture_tick(self._resize_session)

        # High-frequency polling timer (50 ms)
        self._resize_timer = QTimer(self)
        self._resize_timer.timeout.connect(self._resize_poll)
        self._resize_timer.start(50)

        # Auto-stop after 30 s
        QTimer.singleShot(30000, self._auto_stop_resize)

        self._resize_action.setText("Stop Resize Capture (recording…)")
        self.status_label.setText("Resize capture ACTIVE – perform a swap then stop")
        self.status_label.setStyleSheet("color: #FF9800; font-weight: bold;")
        log.log_info("[DIAG-RESIZE] Capture started")

    def _resize_poll(self) -> None:
        if self._resize_session is None:
            return
        self._capture_tick(self._resize_session)

    def _auto_stop_resize(self) -> None:
        if self._resize_session is not None:
            self._resize_session.add_event("Auto-stopped after 30 s timeout")
            self._stop_resize_capture()

    def _stop_resize_capture(self) -> None:
        if self._resize_timer is not None:
            self._resize_timer.stop()
            self._resize_timer = None

        session = self._resize_session
        self._resize_session = None
        self._resize_action.setText("Start Resize Capture")

        if session is None:
            return

        session.add_event("Capture stopped")
        # Final snapshot
        main_hwnd, preview_hwnd = self._current_hwnds()
        for hwnd, role in [(main_hwnd, "main"), (preview_hwnd, "preview")]:
            if hwnd is not None:
                snap = capture_snapshot(hwnd, role)
                if snap is not None:
                    session.snapshots.append(snap)

        report = format_resize_report(session, main_hwnd, preview_hwnd)
        QApplication.clipboard().setText(report)
        log.log_info(f"[DIAG-RESIZE] Capture stopped – {len(session.snapshots)} snapshots, report copied")
        self.status_label.setText(f"Resize report copied ({len(session.snapshots)} snapshots)")
        self.status_label.setStyleSheet("color: #4CAF50;")

    # ------------------------------------------------------------------
    # Existing lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent):
        """Handle window close."""
        log.log_info("Application closing - cleaning up")
        try:
            self.live_preview.clear_source()
            self.manager.release_all()
            log.log_info("Cleanup complete")
        except Exception as e:
            log.log_exception("closeEvent")
        event.accept()
    
    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse press for window dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
    
    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse move for window dragging."""
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release for window dragging."""
        self._drag_pos = None
        event.accept()


# Global reference for emergency cleanup
_app_instance: TiledMultiboxerApp | None = None


def _emergency_cleanup():
    """Emergency cleanup handler for crashes or abnormal termination."""
    global _app_instance
    if _app_instance is not None:
        try:
            _app_instance.manager.release_all()
        except Exception:
            pass


def _signal_handler(signum, frame):
    """Handle termination signals gracefully."""
    _emergency_cleanup()
    sys.exit(0)


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """Global exception handler to catch and log any unhandled exceptions."""
    import traceback
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.log_error(f"UNHANDLED EXCEPTION:\n{error_msg}", include_trace=False)
    # Also call the original exception hook
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    """Main entry point."""
    global _app_instance
    
    # Install global exception handler FIRST
    sys.excepthook = _global_exception_handler
    
    # Initialize logging 
    log.init_log()
    log.log_info("Application starting - main()")
    
    # Register cleanup handlers BEFORE creating any hooks
    atexit.register(_emergency_cleanup)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # Dark theme
    from PySide6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Button, QColor(55, 55, 55))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)
    
    log.log_info("Creating TiledMultiboxerApp window")
    window = TiledMultiboxerApp()
    _app_instance = window  # Store reference for emergency cleanup
    window.show()
    
    # Set top-level HWND for DWM preview (DWM API requires top-level window;
    # must be done after show() so the native HWND is realised).
    top_hwnd = int(window.winId())
    window.live_preview.set_top_level_hwnd(top_hwnd)
    log.log_info(f"Top-level HWND for DWM preview: {top_hwnd}")
    
    log.log_info("Entering Qt event loop")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
