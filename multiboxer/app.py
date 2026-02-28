from __future__ import annotations

import ctypes
import os
import psutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

try:
    import win32con
    import win32gui
except ImportError:
    win32con = None
    win32gui = None

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from multiboxer.config_store import AppConfig, ConfigStore
from multiboxer.eq_locator import common_search_roots, find_everquest_exe
from multiboxer.window_embedder import WindowEmbedder


def _configure_windows_dpi_awareness() -> None:
    if os.name != "nt":
        return

    try:
        user32 = ctypes.windll.user32
        dpi_context_per_monitor_v2 = ctypes.c_void_p(-4)
        if user32.SetProcessDpiAwarenessContext(dpi_context_per_monitor_v2):
            return
    except Exception:
        pass

    try:
        shcore = ctypes.windll.shcore
        process_per_monitor_dpi_aware = 2
        shcore.SetProcessDpiAwareness(process_per_monitor_dpi_aware)
        return
    except Exception:
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


@dataclass
class GameInstance:
    process: subprocess.Popen | None
    hwnd: int | None
    session_label: str
    parent_created_at: float = 0.0
    child_pid: int | None = None
    child_hwnd: int | None = None
    owns_process: bool = True
    last_managed_rect: tuple[int, int, int, int] | None = None


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget, config: AppConfig) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)

        self.path_edit = QLineEdit(config.game_exe_path)
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self._browse_game_path)

        path_row = QWidget(self)
        path_row_layout = QHBoxLayout(path_row)
        path_row_layout.setContentsMargins(0, 0, 0, 0)
        path_row_layout.addWidget(self.path_edit)
        path_row_layout.addWidget(browse_button)

        self.session_1 = QLineEdit(config.session_1_id)
        self.session_2 = QLineEdit(config.session_2_id)
        self.session_3 = QLineEdit(config.session_3_id)

        form = QFormLayout(self)
        form.addRow("EverQuest EXE", path_row)
        form.addRow("Session 1 ID", self.session_1)
        form.addRow("Session 2 ID", self.session_2)
        form.addRow("Session 3 ID", self.session_3)

        buttons = QWidget(self)
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 6, 0, 0)

        save_button = QPushButton("Save")
        cancel_button = QPushButton("Cancel")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)

        button_layout.addStretch(1)
        button_layout.addWidget(save_button)
        button_layout.addWidget(cancel_button)
        form.addRow(buttons)

    def _browse_game_path(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select everquest.exe", "", "Executable (*.exe)")
        if path:
            self.path_edit.setText(path)

    def values(self) -> dict[str, str]:
        return {
            "game_exe_path": self.path_edit.text().strip(),
            "session_1_id": self.session_1.text().strip(),
            "session_2_id": self.session_2.text().strip(),
            "session_3_id": self.session_3.text().strip(),
        }


class SlotFrame(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setFrameShape(QFrame.Box)
        self.setLineWidth(1)
        self.setObjectName("slotFrame")

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(6, 6, 6, 6)
        self.layout.setSpacing(6)

        self.placeholder = QWidget(self)
        self.placeholder_layout = QVBoxLayout(self.placeholder)
        self.placeholder_layout.setAlignment(Qt.AlignCenter)

        self.layout.addWidget(self.placeholder)

        self.status_label = QLabel("", self)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #8f9398;")
        self.layout.addWidget(self.status_label)

        self._lock_enabled = False
        self._lock_labels: list[QLabel] = []
        for _ in range(2):
            lock_label = QLabel("🔓", self)
            lock_label.setAlignment(Qt.AlignCenter)
            lock_label.setFixedSize(22, 18)
            lock_label.setStyleSheet(
                "background-color: #202327;"
                "border: 1px solid #575d66;"
                "border-radius: 3px;"
                "color: #8f9398;"
            )
            lock_label.hide()
            self._lock_labels.append(lock_label)

    def set_placeholder_widgets(self, widgets: list[QWidget]) -> None:
        while self.placeholder_layout.count():
            child = self.placeholder_layout.takeAt(0)
            child_widget = child.widget()
            if child_widget is not None:
                child_widget.setParent(None)

        for widget in widgets:
            self.placeholder_layout.addWidget(widget)

    def show_embed(self, title: str) -> None:
        self.placeholder.hide()
        self.status_label.hide()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.status_label.setText(title)

    def show_placeholder(self) -> None:
        self.layout.setContentsMargins(6, 6, 6, 6)
        self.layout.setSpacing(6)
        self.placeholder.show()
        self.status_label.show()
        self.status_label.setText("")

    def enable_lock_indicators(self, enabled: bool) -> None:
        self._lock_enabled = enabled
        if not enabled:
            for label in self._lock_labels:
                label.hide()
            return
        self.set_lock_state(False, visible=False)

    def set_lock_state(self, locked: bool, visible: bool = True) -> None:
        if not self._lock_enabled:
            return

        icon = "🔒" if locked else "🔓"
        color = "#d5d9de" if locked else "#8f9398"
        for label in self._lock_labels:
            label.setText(icon)
            label.setStyleSheet(
                "background-color: #202327;"
                "border: 1px solid #575d66;"
                "border-radius: 3px;"
                f"color: {color};"
            )
            label.setVisible(visible)

        self._position_lock_indicators()

    def _position_lock_indicators(self) -> None:
        if not self._lock_enabled:
            return

        y = 2
        ratios = (0.25, 0.75)
        for label, ratio in zip(self._lock_labels, ratios):
            x = int(self.width() * ratio - (label.width() / 2))
            label.move(max(0, x), y)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._position_lock_indicators()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if hasattr(event, "button") and event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    RESOLUTIONS = ["1280x720", "1600x900", "1920x1080", "2560x1440"]

    def __init__(self) -> None:
        super().__init__()
        self.config_store = ConfigStore()
        self.config = self.config_store.load()
        self.embedder = WindowEmbedder()

        self.main_slot = SlotFrame()
        self.small_slot = SlotFrame()
        self.small_slot.clicked.connect(self._on_small_slot_clicked)
        self.main_slot.enable_lock_indicators(True)
        self._main_slot_lock_state: bool | None = None
        self._main_slot_lock_tolerance = 50  # Increased to handle residual frame overhead
        self._main_slot_control_strip_height = 22
        
        # Preview slot watchdog system
        self._preview_slot_lock_state: bool | None = None
        self._preview_slot_lock_tolerance = 20  # Increased for consistency
        self._preview_min_target_width = 80
        self._preview_min_target_height = 50
        
        self._main_attach_throttle_seconds = 0.3  # Reduced to allow faster initial grab
        self._last_main_attach_at = 0.0
        self._main_lock_correction_cooldown_seconds = 2.5
        self._last_main_lock_correction_at = 0.0
        self._main_min_target_width = 640
        self._main_min_target_height = 360
        self._last_stable_main_target_rect: tuple[int, int, int, int] | None = None
        
        # Cooldown tracking for viewport refresh prevention
        self._last_attachment_times: dict[str, float] = {}
        self._pixel_perfect_fit_enabled = False
        self._is_custom_maximized = False
        self._restore_geometry = None
        self._swap_shortcut_latched = False
        self._was_minimized = False

        self.main_instance: GameInstance | None = None
        self.small_instance: GameInstance | None = None
        self._watchdog_last_signature: dict[str, tuple] = {}
        self._watchdog_errors: deque[str] = deque(maxlen=25)
        self._verbose_watchdog_logs = False
        self._safe_mode_enabled = False

        # Delayed decoration removal system
        self._decoration_check_timer = QTimer(self)
        self._decoration_check_timer.setSingleShot(True)
        self._decoration_check_timer.timeout.connect(self._check_and_strip_delayed_decorations)
        self._pending_decoration_check_hwnd: int | None = None

        self.launch_button = QPushButton("Launch EverQuest")
        self.launch_button.clicked.connect(self.launch_main_instance)

        self.grab_main_button = QPushButton("Grab External eqgame.exe (Main)")
        self.grab_main_button.clicked.connect(self.grab_main_external_eqgame)

        self.find_button = QPushButton("Find everquest.exe")
        self.find_button.clicked.connect(self.find_executable)

        self.change_exe_button = QPushButton("Change everquest.exe")
        self.change_exe_button.clicked.connect(self.choose_executable)

        self.preview_plus_button = QPushButton("+")
        self.preview_plus_button.setFixedSize(22, 22)
        self.preview_plus_button.setToolTip("Move Preview To Main")
        self.preview_plus_button.clicked.connect(self._on_swap_back_clicked)
        self.preview_plus_button.hide()

        self.move_to_preview_button = QPushButton("To Preview")
        self.move_to_preview_button.setFixedHeight(22)
        self.move_to_preview_button.clicked.connect(self._on_plus_clicked)
        self.move_to_preview_button.hide()

        self.preview_slot_plus_button = QPushButton("+")
        self.preview_slot_plus_button.setFixedSize(40, 40)
        self.preview_slot_plus_button.setToolTip("Move Main To Preview")
        self.preview_slot_plus_button.clicked.connect(self._on_plus_clicked)

        self.small_slot_swap_button = QPushButton("↩", self.small_slot)
        self.small_slot_swap_button.setObjectName("smallSlotSwapButton")
        self.small_slot_swap_button.setToolTip("Swap Preview Back To Main")
        self.small_slot_swap_button.setFixedSize(18, 18)
        self.small_slot_swap_button.clicked.connect(self._on_swap_back_clicked)
        self.small_slot_swap_button.hide()

        self._small_slot_control_strip_height = 20

        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setMinimumHeight(120)
        self.log_console.setPlaceholderText("Session log output...")

        self.log_dialog = QDialog(self)
        self.log_dialog.setWindowTitle("Session Console")
        self.log_dialog.setModal(False)
        self.log_dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.log_dialog.resize(900, 320)
        log_layout = QVBoxLayout(self.log_dialog)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.addWidget(self.log_console)

        self.log_file_path = Path.home() / ".multiboxer" / "session.log"
        self.log_file_path.parent.mkdir(parents=True, exist_ok=True)

        self.session_selector = QComboBox()
        self.session_selector.addItems(["Session 1", "Session 2", "Session 3"])
        self.session_selector.setFixedWidth(120)

        self.main_status_box = QLabel("Main: Empty")
        self.main_status_box.setObjectName("sessionStatusBox")
        self.preview_status_box = QLabel("Preview: Empty")
        self.preview_status_box.setObjectName("sessionStatusBox")
        self.error_indicator = QLabel("", self)
        self.error_indicator.setObjectName("errorIndicator")
        self.error_indicator.hide()
        self._error_flash_on = False

        self.minimize_button = QPushButton("_")
        self.minimize_button.setObjectName("windowControlButton")
        self.minimize_button.setFixedSize(20, 18)
        self.minimize_button.clicked.connect(self.showMinimized)

        self.maximize_button = QPushButton("□")
        self.maximize_button.setObjectName("windowControlButton")
        self.maximize_button.setFixedSize(20, 18)
        self.maximize_button.clicked.connect(self._toggle_maximize)

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("windowControlButtonClose")
        self.close_button.setFixedSize(20, 18)
        self.close_button.clicked.connect(self.close)

        self._build_window()
        self._apply_resolution(self.config.resolution)
        self._refresh_slot_placeholders()

        if self.config.game_exe_path and not Path(self.config.game_exe_path).exists():
            self.config.game_exe_path = ""
            self.config_store.save(self.config)
        
        # Start child process monitoring
        self._start_child_process_monitor()
        self._start_window_state_watchdog()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if self._handle_swap_shortcut(event, True):
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._handle_swap_shortcut(event, False):
            return
        super().keyReleaseEvent(event)

    def _swap_shortcut_name(self, event) -> str | None:
        key = event.key() if hasattr(event, "key") else 0
        modifiers = event.modifiers() if hasattr(event, "modifiers") else Qt.NoModifier

        if key in (Qt.Key_Tab, Qt.Key_Backtab) and bool(modifiers & Qt.ControlModifier):
            return "Ctrl+Tab"

        if key in (Qt.Key_QuoteLeft, Qt.Key_AsciiTilde) and bool(modifiers & Qt.AltModifier):
            return "Alt+`"

        return None

    def _handle_swap_shortcut(self, event, is_down: bool) -> bool:
        shortcut_name = self._swap_shortcut_name(event)

        if shortcut_name is not None:
            if is_down and not self._swap_shortcut_latched:
                self._swap_shortcut_latched = True
                self._log_capture(f"Keyboard shortcut: {shortcut_name} intercepted -> swapping sessions.")
                self.swap_slots()
            if not is_down:
                self._swap_shortcut_latched = False
            event.accept()
            return True

        key = event.key() if hasattr(event, "key") else 0
        if key in (Qt.Key_Control, Qt.Key_Alt) and not is_down:
            self._swap_shortcut_latched = False

        return False

    def _build_window(self) -> None:
        self.setWindowTitle("")
        self.setObjectName("mainWindow")
        self.setWindowFlag(Qt.FramelessWindowHint, True)

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self.small_slot.setFixedSize(95, 58)
        self.main_slot.setMinimumHeight(580)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(2)
        top_row.addWidget(self.small_slot, 0, Qt.AlignLeft | Qt.AlignTop)
        top_row.addStretch(1)

        layout.addLayout(top_row, 0)
        layout.addWidget(self.main_slot, 1)

        self.setCentralWidget(central)
        self._build_menu()

        menu_corner = QWidget(self)
        menu_corner_layout = QHBoxLayout(menu_corner)
        menu_corner_layout.setContentsMargins(4, 0, 0, 0)
        menu_corner_layout.setSpacing(4)
        menu_corner_layout.addWidget(self.error_indicator)
        menu_corner_layout.addWidget(self.main_status_box)
        menu_corner_layout.addWidget(self.preview_status_box)
        menu_corner_layout.addWidget(self.session_selector)
        menu_corner_layout.addWidget(self.move_to_preview_button)
        menu_corner_layout.addWidget(self.preview_plus_button)
        menu_corner_layout.addWidget(self.minimize_button)
        menu_corner_layout.addWidget(self.maximize_button)
        menu_corner_layout.addWidget(self.close_button)
        self.menuBar().setCornerWidget(menu_corner, Qt.TopRightCorner)

        self.menuBar().setFixedHeight(22)
        self._apply_styles()
        self._position_small_slot_swap_button()

        resize_timer = QTimer(self)
        resize_timer.timeout.connect(self._refresh_managed_windows)
        resize_timer.start(500)
        self._resize_timer = resize_timer

        geometry_refresh_timer = QTimer(self)
        geometry_refresh_timer.setSingleShot(True)
        geometry_refresh_timer.timeout.connect(self._refresh_managed_windows)
        self._geometry_refresh_timer = geometry_refresh_timer

        error_flash_timer = QTimer(self)
        error_flash_timer.timeout.connect(self._tick_error_indicator)
        error_flash_timer.start(500)
        self._error_flash_timer = error_flash_timer

        self._update_error_indicator()

    def _start_child_process_monitor(self) -> None:
        """Start a timer to monitor for child processes (eqgame.exe launched by LaunchPad)."""
        monitor_timer = QTimer(self)
        monitor_timer.timeout.connect(self._monitor_child_processes)
        monitor_timer.start(250)
        self._monitor_timer = monitor_timer

    def _start_window_state_watchdog(self) -> None:
        watchdog_timer = QTimer(self)
        watchdog_timer.timeout.connect(self._emit_window_state_watchdog)
        watchdog_timer.start(500)
        self._watchdog_timer = watchdog_timer

    def _emit_window_state_watchdog(self) -> None:
        if not self.embedder.supported:
            return

        slot_instances = [
            ("Main", self.main_instance),
            ("Preview", self.small_instance),
        ]

        for slot_name, instance in slot_instances:
            if instance is None:
                self._watchdog_last_signature.pop(slot_name, None)
                continue

            hwnd = instance.child_hwnd if instance.child_hwnd is not None else instance.hwnd
            if hwnd is None or not self.embedder.is_window(hwnd):
                signature = ("missing", hwnd)
            else:
                pid, title = self.embedder.get_window_info(hwnd)
                is_visible, is_enabled, parent_hwnd, style, exstyle = self.embedder.get_window_state(hwnd)
                signature = (hwnd, pid, is_visible, is_enabled, parent_hwnd, style, exstyle, title)

            if self._watchdog_last_signature.get(slot_name) == signature:
                continue

            self._watchdog_last_signature[slot_name] = signature
            if signature and signature[0] == "missing":
                self._record_watchdog_error(f"Watchdog {slot_name}: missing/invalid window handle ({signature[1]}).")
                continue

            if self._verbose_watchdog_logs:
                _, pid, is_visible, is_enabled, parent_hwnd, style, exstyle, title = signature
                self._log_capture(
                    f"Watchdog {slot_name}: hwnd={hwnd} pid={pid} visible={is_visible} enabled={is_enabled} "
                    f"parent={parent_hwnd} style={style} exstyle={exstyle} title='{title}'."
                )

    def _record_watchdog_error(self, message: str) -> None:
        if message in self._watchdog_errors:
            return
        self._watchdog_errors.append(message)
        self._log_capture(message)
        self._update_error_indicator()

    def _tick_error_indicator(self) -> None:
        if not self._watchdog_errors:
            self.error_indicator.hide()
            return

        self._error_flash_on = not self._error_flash_on
        color = "#ff4b4b" if self._error_flash_on else "#ffffff"
        self.error_indicator.setStyleSheet(
            "background-color: #202327;"
            f"color: {color};"
            "border: 1px solid #575d66;"
            "border-radius: 3px;"
            "padding: 1px 6px;"
        )

    def _update_error_indicator(self) -> None:
        count = len(self._watchdog_errors)
        if count <= 0:
            self.error_indicator.hide()
            return

        self.error_indicator.setText(f"application throwing errors ({count})")
        self.error_indicator.show()
        self._tick_error_indicator()

    def _monitor_child_processes(self) -> None:
        """Monitor for child processes launched by the main or small instances."""
        slot_map: list[tuple[GameInstance | None, SlotFrame]] = [
            (self.main_instance, self.main_slot),
            (self.small_instance, self.small_slot),
        ]

        for instance, slot in slot_map:
            if instance is None:
                continue

            if instance.process is None:
                self._refresh_instance_window(instance, slot)
                continue

            if instance.process.poll() is None and instance.child_pid is None:
                try:
                    parent = psutil.Process(instance.process.pid)
                    for child in parent.children(recursive=True):
                        child_name = child.name().lower()
                        if "eqgame" in child_name or "eqgrame" in child_name:
                            self._bind_child_to_instance(instance, child.pid)
                            break
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            if self._safe_mode_enabled:
                continue

            self._refresh_instance_window(instance, slot)

    def _is_eqgame_process_name(self, name: str) -> bool:
        lowered = name.lower()
        return "eqgame" in lowered or "eqgrame" in lowered

    def _claim_external_eqgame_for_slot(self, instance: GameInstance, slot: SlotFrame) -> tuple[bool, str]:
        slot_name = "Main" if slot == self.main_slot else "Preview"
        claimed = self._claimed_game_pids()
        if instance.process is not None:
            claimed.discard(instance.process.pid)
        if instance.child_pid is not None:
            claimed.discard(instance.child_pid)

        self._log_capture(f"{slot_name} grab scan: checking foreground window first.")

        active_hwnd, active_pid = self.embedder.get_foreground_window_pid()
        if active_hwnd and active_pid and active_pid not in claimed:
            try:
                active_proc = psutil.Process(active_pid)
                if self._is_eqgame_process_name(active_proc.name()) and not self._is_managed_in_any_slot(active_hwnd):
                    instance.child_pid = active_pid
                    instance.child_hwnd = active_hwnd
                    self._attach_to_slot(instance, slot)
                    return (True, f"Captured active window (PID {active_pid}, HWND {active_hwnd}).")
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        candidates: list[tuple[float, int]] = []
        for proc in psutil.process_iter(["pid", "name", "create_time"]):
            try:
                name = proc.info.get("name") or ""
                if not self._is_eqgame_process_name(name):
                    continue

                pid = proc.info["pid"]
                if pid in claimed:
                    continue

                created_at = float(proc.info.get("create_time") or 0.0)
                candidates.append((created_at, pid))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if not candidates:
            self._log_capture(f"{slot_name} grab scan: no eqgame-like process candidates found.")

        candidates.sort(reverse=True)
        self._log_capture(f"{slot_name} grab scan: found {len(candidates)} eqgame candidate process(es).")
        for _, pid in candidates:
            try:
                hwnds = self.embedder.find_top_windows_for_pid(pid)
                self._log_capture(f"{slot_name} grab scan: PID {pid} visible window count = {len(hwnds)}.")
                if not hwnds:
                    continue

                for hwnd in hwnds:
                    if self._is_managed_in_any_slot(hwnd):
                        self._log_capture(f"{slot_name} grab scan: HWND {hwnd} already managed; skipping.")
                        continue

                    instance.child_pid = pid
                    instance.child_hwnd = hwnd
                    self._attach_to_slot(instance, slot)
                    return (True, f"Captured external window (PID {pid}, HWND {hwnd}).")
            except Exception as error:
                self._log_capture(f"{slot_name} grab scan: PID {pid} window enumeration error: {error}")

        return (False, "No unmanaged eqgame window was found.")

    def _find_external_eqgame_window(self, excluded_pids: set[int]) -> tuple[int, int] | None:
        self._log_capture("Grab scan: searching for external eqgame process/window.")
        active_hwnd, active_pid = self.embedder.get_foreground_window_pid()
        if active_hwnd and active_pid and active_pid not in excluded_pids:
            try:
                active_proc = psutil.Process(active_pid)
                if self._is_eqgame_process_name(active_proc.name()) and not self._is_managed_in_any_slot(active_hwnd):
                    self._log_capture(f"Grab scan: foreground eqgame candidate PID {active_pid}, HWND {active_hwnd}.")
                    return (active_pid, active_hwnd)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        candidates: list[tuple[float, int]] = []
        for proc in psutil.process_iter(["pid", "name", "create_time"]):
            try:
                name = proc.info.get("name") or ""
                if not self._is_eqgame_process_name(name):
                    continue

                pid = proc.info["pid"]
                if pid in excluded_pids:
                    continue

                created_at = float(proc.info.get("create_time") or 0.0)
                candidates.append((created_at, pid))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        self._log_capture(f"Grab scan: process candidates found = {len(candidates)}.")

        candidates.sort(reverse=True)
        for _, pid in candidates:
            try:
                hwnds = self.embedder.find_top_windows_for_pid(pid)
                self._log_capture(f"Grab scan: PID {pid} visible window count = {len(hwnds)}.")
                if not hwnds:
                    continue

                for hwnd in hwnds:
                    if self._is_managed_in_any_slot(hwnd):
                        self._log_capture(f"Grab scan: PID {pid}, HWND {hwnd} already managed; skipping.")
                        continue

                    self._log_capture(f"Grab scan: selected PID {pid}, HWND {hwnd}.")
                    return (pid, hwnd)
            except Exception as error:
                self._log_capture(f"Grab scan: PID {pid} window enumeration error: {error}")

        self._log_capture("Grab scan: no eligible external eqgame window selected.")
        return None

    def grab_main_external_eqgame(self) -> None:
        self._log_capture("Main grab clicked.")
        if self.main_instance is None:
            candidate = self._find_external_eqgame_window(self._claimed_game_pids())
            if candidate is None:
                self._log_capture("Main grab failed: no external eqgame window found.")
                QMessageBox.information(self, "Not Found", "No external eqgame.exe window is available for main slot.")
                return

            pid, hwnd = candidate
            session_index = 1 if self.small_instance is None else self.session_selector.currentIndex() + 1
            session_label = self._session_label_for_index(session_index)
            self.main_instance = GameInstance(
                process=None,
                hwnd=hwnd,
                session_label=session_label,
                parent_created_at=time.time(),
                child_pid=pid,
                child_hwnd=hwnd,
                owns_process=False,
            )
            self._attach_to_slot(self.main_instance, self.main_slot)
            self._refresh_slot_placeholders()
            self._log_capture(f"Main grab success: PID {pid}, HWND {hwnd}.")
            return

        ok, message = self._claim_external_eqgame_for_slot(self.main_instance, self.main_slot)
        self._log_capture(f"Main grab {'success' if ok else 'failed'}: {message}")
        if not ok:
            QMessageBox.information(self, "Not Found", "No external eqgame.exe window is available for main slot.")

    def grab_small_external_eqgame(self) -> None:
        self._log_capture("Preview grab clicked.")
        if self.small_instance is None:
            candidate = self._find_external_eqgame_window(self._claimed_game_pids())
            if candidate is None:
                self._log_capture("Preview grab failed: no external eqgame window found.")
                QMessageBox.information(self, "Not Found", "No external eqgame.exe window is available for preview slot.")
                return

            pid, hwnd = candidate
            session_label = self._selected_session_label()
            self.small_instance = GameInstance(
                process=None,
                hwnd=hwnd,
                session_label=session_label,
                parent_created_at=time.time(),
                child_pid=pid,
                child_hwnd=hwnd,
                owns_process=False,
            )
            self._attach_to_slot(self.small_instance, self.small_slot)
            self._refresh_slot_placeholders()
            self._log_capture(f"Preview grab success: PID {pid}, HWND {hwnd}.")
            return

        ok, message = self._claim_external_eqgame_for_slot(self.small_instance, self.small_slot)
        self._log_capture(f"Preview grab {'success' if ok else 'failed'}: {message}")
        if not ok:
            QMessageBox.information(self, "Not Found", "No external eqgame.exe window is available for preview slot.")

    def _log_capture(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log_console.append(line)
        try:
            with self.log_file_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def _is_managed_in_any_slot(self, hwnd: int) -> bool:
        for instance in (self.main_instance, self.small_instance):
            if instance is None:
                continue
            if hwnd in (instance.hwnd, instance.child_hwnd):
                return True
        return False

    def _slot_global_rect(self, slot: SlotFrame) -> tuple[int, int, int, int]:
        top_left = slot.mapToGlobal(slot.rect().topLeft())
        return (
            int(top_left.x()),
            int(top_left.y()),
            max(1, int(slot.width())),
            max(1, int(slot.height())),
        )

    def _main_slot_target_rect(self) -> tuple[int, int, int, int]:
        x, y, width, height = self._slot_global_rect(self.main_slot)
        inset_top = self._main_slot_control_strip_height
        candidate = (
            x,
            y + inset_top,
            width,
            max(1, height - inset_top),
        )

        if self._pixel_perfect_fit_enabled:
            candidate = self._fit_rect_to_resolution(candidate)

        if (
            candidate[2] >= self._main_min_target_width
            and candidate[3] >= self._main_min_target_height
        ):
            self._last_stable_main_target_rect = candidate
            return candidate

        if self._last_stable_main_target_rect is not None:
            return self._last_stable_main_target_rect

        return candidate

    def _resolution_size(self) -> tuple[int, int]:
        try:
            width_str, height_str = self.config.resolution.split("x", maxsplit=1)
            width, height = int(width_str), int(height_str)
            if width > 0 and height > 0:
                return (width, height)
        except Exception:
            pass
        return (1920, 1080)

    def _fit_rect_to_resolution(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x, y, width, height = rect
        render_width, render_height = self._resolution_size()

        scale = min(width / render_width, height / render_height)
        if scale <= 0:
            return rect

        fitted_width = max(1, int(render_width * scale))
        fitted_height = max(1, int(render_height * scale))
        offset_x = (width - fitted_width) // 2
        offset_y = (height - fitted_height) // 2
        return (x + offset_x, y + offset_y, fitted_width, fitted_height)

    def _preview_embed_insets(self) -> tuple[int, int, int, int]:
        top_inset = self._small_slot_control_strip_height
        available_width = max(1, self.small_slot.width())
        available_height = max(1, self.small_slot.height() - top_inset)

        render_width, render_height = self._resolution_size()
        scale = min(available_width / render_width, available_height / render_height)
        if scale <= 0:
            return (0, top_inset, 0, 0)

        fitted_width = max(1, int(render_width * scale))
        fitted_height = max(1, int(render_height * scale))
        left_inset = max(0, (available_width - fitted_width) // 2)
        right_inset = max(0, available_width - fitted_width - left_inset)
        bottom_inset = max(0, available_height - fitted_height)
        return (left_inset, top_inset, right_inset, bottom_inset)

    def _schedule_geometry_refresh(self, delay_ms: int = 160) -> None:
        if hasattr(self, "_geometry_refresh_timer"):
            self._geometry_refresh_timer.start(max(0, int(delay_ms)))

    def _constrain_to_available_geometry(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        current = self.geometry()
        width = min(current.width(), available.width())
        height = min(current.height(), available.height())
        x = min(max(current.x(), available.x()), available.x() + available.width() - width)
        y = min(max(current.y(), available.y()), available.y() + available.height() - height)

        if (
            x != current.x()
            or y != current.y()
            or width != current.width()
            or height != current.height()
        ):
            self.setGeometry(x, y, width, height)

    def _set_main_lock_state(self, locked: bool, reason: str, force_log: bool = False) -> None:
        self.main_slot.set_lock_state(locked, visible=self.main_instance is not None)
        if force_log or (self._main_slot_lock_state != locked and locked):
            self._log_capture(f"Main lock {'LOCKED' if locked else 'UNLOCKED'}: {reason}")
        lowered_reason = reason.lower()
        if (
            not locked
            and (
                "failed" in lowered_reason
                or "mismatch" in lowered_reason
                or "unable" in lowered_reason
                or "expected" in lowered_reason
            )
        ):
            self._record_watchdog_error(f"Main lock error: {reason}")
        self._main_slot_lock_state = locked

    def _hide_main_lock_state(self) -> None:
        self.main_slot.set_lock_state(False, visible=False)
        self._main_slot_lock_state = None

    def _hide_preview_lock_state(self) -> None:
        """Hide preview slot lock state."""
        self._preview_slot_lock_state = None

    def _set_preview_lock_state(self, locked: bool, reason: str, force_log: bool = False) -> None:
        """Set preview slot lock state and record errors."""
        if force_log or (self._preview_slot_lock_state != locked):
            self._log_capture(f"Preview lock {'LOCKED' if locked else 'UNLOCKED'}: {reason}")
        lowered_reason = reason.lower()
        if (
            not locked
            and (
                "failed" in lowered_reason
                or "unable" in lowered_reason
                or "invalid" in lowered_reason
                or "not embedded" in lowered_reason
                or "not visible" in lowered_reason
                or "not enabled" in lowered_reason
                or "below minimum" in lowered_reason
            )
        ):
            self._record_watchdog_error(f"Preview lock error: {reason}")
        self._preview_slot_lock_state = locked

    def _is_rect_aligned(self, expected: tuple[int, int, int, int], actual: tuple[int, int, int, int], tolerance: int) -> bool:
        return (
            abs(expected[0] - actual[0]) <= tolerance
            and abs(expected[1] - actual[1]) <= tolerance
            and abs(expected[2] - actual[2]) <= tolerance
            and abs(expected[3] - actual[3]) <= tolerance
        )

    def _rect_error(self, expected: tuple[int, int, int, int], actual: tuple[int, int, int, int]) -> int:
        return (
            abs(expected[0] - actual[0])
            + abs(expected[1] - actual[1])
            + abs(expected[2] - actual[2])
            + abs(expected[3] - actual[3])
        )

    def _rect_changed(self, previous: tuple[int, int, int, int] | None, current: tuple[int, int, int, int], tolerance: int = 2) -> bool:
        if previous is None:
            return True
        return not self._is_rect_aligned(previous, current, tolerance)

    def _confirm_main_lock_alignment(self, hwnd: int, target_rect: tuple[int, int, int, int], context: str) -> bool:
        # Check if window is minimized and handle it
        if self.embedder.is_window_minimized(hwnd):
            # If the window is minimized, try to restore it for inactive sessions
            if "inactive" in context.lower() or "container" in context.lower():
                self._log_capture(f"Detected minimized window in {context}, attempting restore")
                if self.embedder.restore_window(hwnd):
                    # Give the window a moment to restore before checking alignment
                    time.sleep(0.1)
                else:
                    self._set_main_lock_state(False, f"{context}: failed to restore minimized window HWND {hwnd}.", force_log=True)
                    return False
            else:
                self._set_main_lock_state(False, f"{context}: window is minimized HWND {hwnd}.", force_log=True)
                return False

        client_rect = self.embedder.get_client_rect_on_screen(hwnd)
        window_rect = self.embedder.get_window_rect(hwnd)

        if client_rect is None and window_rect is None:
            self._set_main_lock_state(False, f"{context}: unable to read window rect for HWND {hwnd}.", force_log=True)
            return False

        # Check for minimized coordinates (typically -32000, -32000 on Windows)
        if window_rect is not None and (window_rect[0] <= -30000 or window_rect[1] <= -30000):
            self._log_capture(f"Detected off-screen coordinates in {context}, attempting to reposition")
            # Try to restore and reposition the window using the target coordinates
            target_x, target_y, target_width, target_height = target_rect
            if self.embedder.force_restore_and_reposition(hwnd, target_x, target_y, target_width, target_height):
                # Force a refresh after restoration
                self._schedule_geometry_refresh(30)
                time.sleep(0.1)
                # Re-read the rectangles after restoration
                client_rect = self.embedder.get_client_rect_on_screen(hwnd)
                window_rect = self.embedder.get_window_rect(hwnd)
                self._log_capture(f"Window repositioned to: client={client_rect}, window={window_rect}")
            else:
                self._set_main_lock_state(False, f"{context}: failed to reposition off-screen window HWND {hwnd}.", force_log=True)
                return False

        if client_rect is not None and self._is_rect_aligned(target_rect, client_rect, self._main_slot_lock_tolerance):
            self._set_main_lock_state(True, f"{context}: aligned (client) at {client_rect}.")
            return True

        if window_rect is not None and self._is_rect_aligned(target_rect, window_rect, self._main_slot_lock_tolerance):
            self._set_main_lock_state(True, f"{context}: aligned (window) at {window_rect}.")
            return True
        
        # Check if position is correct even with frame overhead (for borderless windows after decoration stripping)
        if client_rect is not None and window_rect is not None:
            # Position should be very close even with frame overhead
            position_aligned = (abs(target_rect[0] - client_rect[0]) <= 10 and 
                              abs(target_rect[1] - client_rect[1]) <= 10)
            # Size might differ due to frame overhead, but should be reasonably close
            size_reasonable = (abs(target_rect[2] - client_rect[2]) <= 60 and 
                             abs(target_rect[3] - client_rect[3]) <= 60)
            
            if position_aligned and size_reasonable:
                self._set_main_lock_state(True, f"{context}: acceptable alignment with frame overhead - client at {client_rect}.")
                return True

        if client_rect is not None and window_rect is not None:
            client_error = self._rect_error(target_rect, client_rect)
            window_error = self._rect_error(target_rect, window_rect)
            if client_error <= window_error:
                actual_rect = client_rect
                source = "client"
            else:
                actual_rect = window_rect
                source = "window"
        elif client_rect is not None:
            actual_rect = client_rect
            source = "client"
        else:
            actual_rect = window_rect
            source = "window"

        self._set_main_lock_state(
            False,
            f"{context}: expected {target_rect}, got {actual_rect} ({source}).",
        )
        return False

    def _confirm_preview_lock_alignment(self, hwnd: int, target_rect: tuple[int, int, int, int], context: str) -> bool:
        """Check preview slot alignment for floating window (no embedding)."""
        if not self.embedder.is_window(hwnd):
            self._set_preview_lock_state(False, f"{context}: window HWND {hwnd} is invalid.", force_log=True)
            return False

        # Check window visibility and enabled state
        is_visible, is_enabled, parent_hwnd, style, exstyle = self.embedder.get_window_state(hwnd)
        if not is_visible:
            self._set_preview_lock_state(False, f"{context}: window HWND {hwnd} is not visible.", force_log=True)
            return False
        if not is_enabled:
            self._set_preview_lock_state(False, f"{context}: window HWND {hwnd} is not enabled.", force_log=True)
            return False

        # Get client rect for position/size validation
        client_rect = self.embedder.get_client_rect_on_screen(hwnd)
        if client_rect is None:
            self._set_preview_lock_state(False, f"{context}: unable to read client rect for HWND {hwnd}.", force_log=True)
            return False

        # For floating windows, check position alignment within tolerance
        target_x, target_y, target_width, target_height = target_rect
        actual_x, actual_y, actual_width, actual_height = client_rect
        
        tolerance = self._preview_slot_lock_tolerance
        x_ok = abs(actual_x - target_x) <= tolerance
        y_ok = abs(actual_y - target_y) <= tolerance
        # Size tolerance more lenient since game window maintains its original size
        size_ok = actual_width >= 50 and actual_height >= 50
        
        if not (x_ok and y_ok):
            self._set_preview_lock_state(False, f"{context}: position mismatch - expected ({target_x},{target_y}), got ({actual_x},{actual_y}).", force_log=True)
            return False

        if not size_ok:
            self._set_preview_lock_state(False, f"{context}: size {actual_width}x{actual_height} below minimum.", force_log=True)
            return False

        # All checks passed
        self._set_preview_lock_state(True, f"{context}: preview floating aligned successfully.")
        return True

    def _ensure_main_window_foreground(self) -> None:
        if self.main_instance is None:
            return

        hwnd = self.main_instance.child_hwnd if self.main_instance.child_hwnd is not None else self.main_instance.hwnd
        if hwnd is None:
            return

        def make_attempt(delay_ms: int):
            def _attempt() -> None:
                active_hwnd = None if self.main_instance is None else (self.main_instance.child_hwnd or self.main_instance.hwnd)
                if active_hwnd != hwnd:
                    return

                raised = self.embedder.raise_floating_window(hwnd)
                activated = self.embedder.activate_window(hwnd)
                if not raised and not activated:
                    self._record_watchdog_error(f"Main foreground restore failed at +{delay_ms}ms for HWND {hwnd}.")

            return _attempt

        for delay in (0, 120, 280):
            QTimer.singleShot(delay, make_attempt(delay))

    def _retry_main_lock_alignment(self, hwnd: int, target_rect: tuple[int, int, int, int], context: str, max_attempts: int = 2) -> bool:
        x, y, width, height = target_rect
        for attempt in range(1, max_attempts + 1):
            aligned = self._confirm_main_lock_alignment(hwnd, target_rect, f"{context} attempt {attempt}")
            if aligned:
                # Reset refresh cycle counter when successfully locked
                self._refresh_cycle_count = 0
                return True

            if self._verbose_watchdog_logs:
                self._log_capture(f"Main lock retry: attempt {attempt}/{max_attempts} for HWND {hwnd}.")
            
            # Small delay between attempts to let window settle
            if attempt < max_attempts:
                time.sleep(0.05)
            
            # DON'T call attach_floating again - it causes size accumulation!
            # The first attach already positioned the window. Only re-enforce styles.
            try:
                clean_style = 0x94000000  # WS_POPUP | WS_VISIBLE | WS_CLIPSIBLINGS
                win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, clean_style)
                win32gui.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED
                )
            except:
                pass

        # Even if alignment check failed, if we've tried multiple times, accept current state
        # to prevent infinite accumulation loops
        self._refresh_cycle_count = 0  # Reset to prevent further attempts
        self._log_capture(f"Main lock accepting current state after {max_attempts} attempts to prevent accumulation.")
        return True  # Return True to stop further retry attempts

    def _refresh_instance_window(self, instance: GameInstance, slot: SlotFrame) -> None:
        hwnd = instance.child_hwnd if instance.child_hwnd is not None else instance.hwnd
        if hwnd is None:
            return

        # Check and validate window state before proceeding
        if not self.embedder.is_window(hwnd):
            self._log_capture(f"Window HWND {hwnd} is no longer valid, skipping refresh")
            return

        if slot == self.main_slot:
            target_rect = self._main_slot_target_rect()
        else:
            target_rect = self._slot_global_rect(slot)
        x, y, width, height = target_rect
        is_visible, is_enabled, parent_hwnd, style, exstyle = self.embedder.get_window_state(hwnd)
        
        # Log window style for debugging if it has problematic decorations
        if style and win32con and (
            (style & win32con.WS_CAPTION) or 
            (style & win32con.WS_THICKFRAME) or 
            (style & win32con.WS_SYSMENU) or 
            (style & win32con.WS_MINIMIZEBOX) or 
            (style & win32con.WS_MAXIMIZEBOX)
        ):
            self._log_capture(f"Window {hwnd} still has decorations: style={style}, deferring to attach cycle")
            # Avoid redundant decoration strip during refresh - let attach cycle handle it
            
        if slot == self.small_slot:
            # Preview slot uses floating attach - calculate target rect with control strip offset
            preview_x = x
            preview_y = y + self._small_slot_control_strip_height
            preview_width = width
            preview_height = max(1, height - self._small_slot_control_strip_height)
            preview_target_rect = (preview_x, preview_y, preview_width, preview_height)
            
            # Cooldown-based refresh prevention
            current_time = time.time()
            last_attach_key = f"small_slot_attach_{hwnd}"
            
            # Check if we recently attached this window (within 2 seconds)
            if hasattr(self, '_last_attachment_times'):
                last_attach_time = self._last_attachment_times.get(last_attach_key, 0)
                if current_time - last_attach_time < 2.0:
                    # Too recent - just ensure visibility and don't resize
                    if not is_visible and win32gui:
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                    instance.last_managed_rect = preview_target_rect
                    self.small_slot_swap_button.raise_()
                    return
            else:
                self._last_attachment_times = {}
            
            # For floating windows, check position alignment (NOT parent)
            client_rect = self.embedder.get_client_rect_on_screen(hwnd)
            if client_rect:
                actual_x, actual_y, actual_w, actual_h = client_rect
                tolerance = self._preview_slot_lock_tolerance
                position_ok = abs(actual_x - preview_x) <= tolerance and abs(actual_y - preview_y) <= tolerance
                
                if position_ok and is_visible:
                    # Already positioned correctly - no re-attach needed
                    instance.last_managed_rect = preview_target_rect
                    self._confirm_preview_lock_alignment(hwnd, preview_target_rect, "refresh alignment check")
                    self.small_slot_swap_button.raise_()
                    return
            
            # Need to attach - record the time to prevent immediate loops
            self._last_attachment_times[last_attach_key] = current_time
            self._attach_to_slot(instance, slot)
            return

        main_rect_tolerance = 48 if slot == self.main_slot else 2
        
        # Stability check for main slot - if recently attached successfully, don't re-attach
        # BUT always allow attach if decorations are present (need to strip them)
        has_decorations = (style and win32con and (
            (style & win32con.WS_CAPTION) or 
            (style & win32con.WS_THICKFRAME) or 
            (style & win32con.WS_SYSMENU)
        ))
        
        # DISABLED stability check - it was blocking initial grab
        # if slot == self.main_slot and self._main_slot_lock_state is True and not has_decorations:
        #     current_time = time.time()
        #     if (current_time - self._last_main_attach_at) < 3.0:
        #         return
        
        needs_attach = (
            self._rect_changed(instance.last_managed_rect, target_rect, tolerance=main_rect_tolerance)
            or parent_hwnd not in (0, None)
            or not is_visible
            or not is_enabled
            or has_decorations  # Force attach if decorations present
        )

        if needs_attach:
            if slot == self.main_slot:
                if width < self._main_min_target_width or height < self._main_min_target_height:
                    self._schedule_geometry_refresh(120)
                    return

                now = time.time()
                if (now - self._last_main_attach_at) < self._main_attach_throttle_seconds:
                    return
                self._last_main_attach_at = now
                
                # Track refresh cycles for debugging
                if not hasattr(self, '_refresh_cycle_count'):
                    self._refresh_cycle_count = 0
                self._refresh_cycle_count += 1

            attached = self.embedder.attach_floating(hwnd, x, y, width, height)
            if attached:
                instance.last_managed_rect = target_rect
                if slot == self.main_slot:
                    if not self.embedder.raise_floating_window(hwnd):
                        self._record_watchdog_error(f"Main z-order raise failed after attach for HWND {hwnd}.")
                    # Give more time for styles to apply before checking alignment
                    time.sleep(0.1)
                    self._confirm_main_lock_alignment(hwnd, target_rect, "refresh attach")
                    # Mark as locked regardless of alignment check result to prevent re-attach loops
                    self._set_main_lock_state(True, "attach completed, accepting current position.")
            elif slot == self.main_slot:
                self._set_main_lock_state(False, "refresh attach failed.", force_log=True)
            return

        if slot == self.main_slot:
            # Only check alignment if we actually attempted an operation that might have changed positioning
            # This prevents noise from mathematical accumulation in refresh cycles
            if needs_attach:
                # Provide more context about the window state for better debugging
                context = "refresh alignment check"
                if parent_hwnd not in (0, None):
                    context += " (embedded)"
                if not is_visible:
                    context += " (invisible)"
                if not is_enabled:
                    context += " (disabled)"
                
                self._confirm_main_lock_alignment(hwnd, target_rect, context)
            else:
                # Window is stable - set lock state if not already set
                if self._main_slot_lock_state != True:
                    self._set_main_lock_state(True, "refresh: window stable, no repositioning needed")
                    # Reset refresh cycle count when successfully locked
                    if hasattr(self, '_refresh_cycle_count'):
                        self._refresh_cycle_count = 0

    def _bind_child_to_instance(self, instance: GameInstance, child_pid: int) -> None:
        instance.child_pid = child_pid
        child_hwnd = self.embedder.find_top_window_for_pid(child_pid, timeout_seconds=5.0)
        if not child_hwnd:
            return

        instance.child_hwnd = child_hwnd
        if instance == self.main_instance:
            self._attach_to_slot(instance, self.main_slot)
        elif instance == self.small_instance:
            self._attach_to_slot(instance, self.small_slot)

    def _claimed_game_pids(self) -> set[int]:
        claimed: set[int] = set()
        for instance in [self.main_instance, self.small_instance]:
            if instance is None:
                continue
            if instance.process is not None:
                claimed.add(instance.process.pid)
            if instance.child_pid is not None:
                claimed.add(instance.child_pid)
        return claimed

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu: QMenu = menu.addMenu("File")
        reset_action = QAction("Reset", self)
        reset_action.triggered.connect(self.reset_to_initial_state)
        file_menu.addAction(reset_action)

        move_main_to_preview_action = QAction("Move Main To Preview", self)
        move_main_to_preview_action.triggered.connect(self._on_plus_clicked)
        file_menu.addAction(move_main_to_preview_action)

        move_preview_to_main_action = QAction("Move Preview To Main", self)
        move_preview_to_main_action.triggered.connect(self._on_swap_back_clicked)
        file_menu.addAction(move_preview_to_main_action)

        session_console_action = QAction("Show Session Console", self)
        session_console_action.triggered.connect(self.show_session_console)
        file_menu.addAction(session_console_action)

        copy_watchdog_action = QAction("Copy Watchdog Snapshot", self)
        copy_watchdog_action.triggered.connect(self.copy_watchdog_snapshot)
        file_menu.addAction(copy_watchdog_action)

        clear_watchdog_errors_action = QAction("Clear Watchdog Errors", self)
        clear_watchdog_errors_action.triggered.connect(self.clear_watchdog_errors)
        file_menu.addAction(clear_watchdog_errors_action)

        verbose_watchdog_action = QAction("Verbose Watchdog Logs", self)
        verbose_watchdog_action.setCheckable(True)
        verbose_watchdog_action.setChecked(self._verbose_watchdog_logs)
        verbose_watchdog_action.toggled.connect(self.toggle_verbose_watchdog_logs)
        file_menu.addAction(verbose_watchdog_action)

        safe_mode_action = QAction("Safe Mode (Pause Auto Manage)", self)
        safe_mode_action.setCheckable(True)
        safe_mode_action.setChecked(self._safe_mode_enabled)
        safe_mode_action.toggled.connect(self.toggle_safe_mode)
        file_menu.addAction(safe_mode_action)

        pixel_perfect_action = QAction("Pixel-Perfect Fit (Experimental)", self)
        pixel_perfect_action.setCheckable(True)
        pixel_perfect_action.setChecked(self._pixel_perfect_fit_enabled)
        pixel_perfect_action.toggled.connect(self.toggle_pixel_perfect_fit)
        file_menu.addAction(pixel_perfect_action)

        grab_main_action = QAction("Grab External eqgame to Main", self)
        grab_main_action.triggered.connect(self.grab_main_external_eqgame)
        file_menu.addAction(grab_main_action)

        grab_preview_action = QAction("Grab External eqgame to Preview", self)
        grab_preview_action.triggered.connect(self.grab_small_external_eqgame)
        file_menu.addAction(grab_preview_action)

        settings_menu: QMenu = menu.addMenu("Settings")
        open_settings_action = QAction("Saved Game Info", self)
        open_settings_action.triggered.connect(self.open_settings)
        settings_menu.addAction(open_settings_action)

        view_menu: QMenu = menu.addMenu("View")
        for resolution in self.RESOLUTIONS:
            action = QAction(resolution, self)
            action.triggered.connect(lambda _, r=resolution: self.set_resolution(r))
            view_menu.addAction(action)

    def show_session_console(self) -> None:
        self.log_dialog.show()
        self.log_dialog.raise_()
        self.log_dialog.activateWindow()

    def copy_watchdog_snapshot(self) -> None:
        self._emit_window_state_watchdog()

        lines: list[str] = []
        lines.append(f"Watchdog Snapshot {time.strftime('%Y-%m-%d %H:%M:%S')}")
        x, y, width, height = self._slot_global_rect(self.main_slot)
        lines.append(f"Main slot rect: x={x} y={y} w={width} h={height}")
        px, py, pwidth, pheight = self._slot_global_rect(self.small_slot)
        lines.append(f"Preview slot rect: x={px} y={py} w={pwidth} h={pheight}")
        
        # Add lock state information for debugging
        main_lock = "LOCKED" if self._main_slot_lock_state else ("UNLOCKED" if self._main_slot_lock_state is False else "UNKNOWN")
        preview_lock = "LOCKED" if self._preview_slot_lock_state else ("UNLOCKED" if self._preview_slot_lock_state is False else "UNKNOWN")
        lines.append(f"Lock states: Main={main_lock}, Preview={preview_lock}")
        
        # Add detailed window style analysis
        lines.append("Window style analysis:")
        if self.main_instance:
            hwnd = self.main_instance.child_hwnd or self.main_instance.hwnd
            if hwnd and self.embedder.is_window(hwnd):
                is_visible, is_enabled, parent_hwnd, style, exstyle = self.embedder.get_window_state(hwnd)
                lines.append(f"  Main window styles: style={style} exstyle={exstyle}")
                
                # Expected clean style for comparison - using signed representation to match GetWindowLong
                expected_clean_style = -1811939328  # 0x94000000 as signed 32-bit int
                lines.append(f"  Expected clean style: {expected_clean_style}")
                lines.append(f"  Style matches expected: {style == expected_clean_style}")
                
                # Detailed style breakdown for better debugging
                current_flags = []
                decoration_flags = []
                
                # Positive style flags (should be present)
                if style & 0x80000000: current_flags.append("WS_POPUP")
                if style & 0x10000000: current_flags.append("WS_VISIBLE") 
                if style & 0x04000000: current_flags.append("WS_CLIPSIBLINGS")
                if style & 0x20000000: current_flags.append("WS_MINIMIZE")
                if style & 0x01000000: current_flags.append("WS_MAXIMIZE")
                
                # Decoration flags (should be absent for borderless window)
                if style & 0x00C00000: decoration_flags.append("WS_CAPTION")
                if style & 0x00040000: decoration_flags.append("WS_THICKFRAME") 
                if style & 0x00080000: decoration_flags.append("WS_SYSMENU")
                if style & 0x00800000: decoration_flags.append("WS_BORDER")
                if style & 0x00400000: decoration_flags.append("WS_DLGFRAME")
                if style & 0x00010000: decoration_flags.append("WS_MAXIMIZEBOX")
                if style & 0x00020000: decoration_flags.append("WS_MINIMIZEBOX")
                
                lines.append(f"  Current style flags: {', '.join(current_flags) if current_flags else 'none'}")
                lines.append(f"  Decoration flags: {', '.join(decoration_flags) if decoration_flags else 'none'}")
                
                window_rect = self.embedder.get_window_rect(hwnd)
                client_rect = self.embedder.get_client_rect_on_screen(hwnd)
                if window_rect and client_rect:
                    frame_width = (window_rect[2] - window_rect[0]) - (client_rect[2] - client_rect[0])
                    frame_height = (window_rect[3] - window_rect[1]) - (client_rect[3] - client_rect[1])
                    lines.append(f"  Frame overhead: width={frame_width} height={frame_height}")
                    
                    # Allow minimal frame overhead (up to 2 pixels) for borderless
                    is_borderless = len(decoration_flags) == 0 and frame_width <= 2 and frame_height <= 2
                    lines.append(f"  Borderless achieved: {is_borderless}")
        
        # Add refresh cycle tracking
        if hasattr(self, '_refresh_cycle_count'):
            lines.append(f"Refresh cycles: {self._refresh_cycle_count}")
        else:
            self._refresh_cycle_count = 0

        for slot_name in ("Main", "Preview"):
            signature = self._watchdog_last_signature.get(slot_name)
            if signature is None:
                lines.append(f"{slot_name}: no instance")
                continue

            if signature[0] == "missing":
                lines.append(f"{slot_name}: missing/invalid hwnd={signature[1]}")
                continue

            hwnd, pid, is_visible, is_enabled, parent_hwnd, style, exstyle, title = signature
            lines.append(
                f"{slot_name}: hwnd={hwnd} pid={pid} visible={is_visible} enabled={is_enabled} "
                f"parent={parent_hwnd} style={style} exstyle={exstyle} title='{title}'"
            )

        lines.append("Watchdog errors (newest last):")
        if self._watchdog_errors:
            for error_line in self._watchdog_errors:
                lines.append(f"- {error_line}")
        else:
            lines.append("- none")

        snapshot = "\n".join(lines)
        clipboard = QApplication.clipboard()
        clipboard.setText(snapshot)
        self._log_capture("Watchdog snapshot copied to clipboard.")

    def clear_watchdog_errors(self) -> None:
        previous_count = len(self._watchdog_errors)
        self._watchdog_errors.clear()
        self._log_capture(f"Watchdog errors cleared (removed {previous_count} entr{'y' if previous_count == 1 else 'ies'}).")
        self._update_error_indicator()

    def toggle_verbose_watchdog_logs(self, enabled: bool) -> None:
        self._verbose_watchdog_logs = bool(enabled)
        self._log_capture(f"Verbose watchdog logs {'enabled' if enabled else 'disabled'}.")

    def toggle_safe_mode(self, enabled: bool) -> None:
        self._safe_mode_enabled = bool(enabled)
        self._log_capture(f"Safe mode {'enabled' if enabled else 'disabled'}.")
        if not self._safe_mode_enabled:
            self._schedule_geometry_refresh(40)

    def toggle_pixel_perfect_fit(self, enabled: bool) -> None:
        self._pixel_perfect_fit_enabled = bool(enabled)
        self._log_capture(f"Pixel-perfect fit {'enabled' if enabled else 'disabled'}.")
        self._schedule_geometry_refresh(40)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow#mainWindow { background-color: #2a2c2f; }
            QMenuBar { background-color: #242629; color: #c8ccd1; }
            QMenuBar::item { padding: 2px 6px; margin: 0px; }
            QMenuBar::item:selected { background: #35383d; }
            QMenu { background-color: #2f3237; color: #d3d7dc; }
            QMenu::item:selected { background-color: #3a3f45; }
            QFrame#slotFrame {
                border: 1px solid #5a5f66;
                background-color: #1f2124;
                border-radius: 4px;
            }
            QPushButton {
                background-color: #3a3f45;
                color: #e6ebf0;
                border: 1px solid #5c626a;
                border-radius: 4px;
                padding: 8px 12px;
            }
            QPushButton:hover { background-color: #454b53; }
            QLabel#sessionStatusBox {
                background-color: #202327;
                color: #d5d9de;
                border: 1px solid #575d66;
                border-radius: 3px;
                padding: 1px 6px;
                min-width: 170px;
            }
            QPushButton#windowControlButton {
                background-color: #2f3237;
                color: #d5d9de;
                border: 1px solid #575d66;
                border-radius: 3px;
                padding: 0px;
            }
            QPushButton#windowControlButton:hover { background-color: #3a3f45; }
            QPushButton#windowControlButtonClose {
                background-color: #2f3237;
                color: #d5d9de;
                border: 1px solid #575d66;
                border-radius: 3px;
                padding: 0px;
            }
            QPushButton#windowControlButtonClose:hover {
                background-color: #7c3a3a;
                border-color: #9a4b4b;
            }
            QPushButton#smallSlotSwapButton {
                background-color: #2f3237;
                color: #d5d9de;
                border: 1px solid #575d66;
                border-radius: 3px;
                padding: 0px;
            }
            QPushButton#smallSlotSwapButton:hover { background-color: #3a3f45; }
            QLabel#errorIndicator {
                background-color: #202327;
                color: #ff4b4b;
                border: 1px solid #575d66;
                border-radius: 3px;
                padding: 1px 6px;
                min-width: 260px;
            }
            QLineEdit, QComboBox {
                background-color: #202327;
                color: #e6ebf0;
                border: 1px solid #575d66;
                border-radius: 3px;
                padding: 6px;
            }
            QLabel { color: #d5d9de; }
            """
        )

    def _refresh_slot_placeholders(self) -> None:
        if self.main_instance is None:
            if self.config.game_exe_path:
                self.main_slot.set_placeholder_widgets([
                    self.launch_button,
                    self.grab_main_button,
                    self.change_exe_button,
                ])
            else:
                self.main_slot.set_placeholder_widgets([self.find_button])
            self.main_slot.show_placeholder()
            self.main_status_box.setText("Main: Empty")
            self._hide_main_lock_state()
        else:
            self.main_slot.show_embed(f"Active: {self.main_instance.session_label}")
            self.main_status_box.setText(f"Main: {self.main_instance.session_label}")
            if self._main_slot_lock_state is None:
                self._set_main_lock_state(False, "Main session active; awaiting alignment confirmation.")

        if self.small_instance is None:
            self.small_slot.set_placeholder_widgets([self.preview_slot_plus_button])
            self.small_slot.show_placeholder()
            self.preview_status_box.setText("Preview: Empty")
            self.preview_plus_button.hide()
            self.move_to_preview_button.setVisible(self.main_instance is not None)
            self.small_slot_swap_button.hide()
            self._hide_preview_lock_state()
        else:
            self.small_slot.show_embed(f"Preview: {self.small_instance.session_label}")
            self.preview_status_box.setText(f"Preview: {self.small_instance.session_label}")
            self.preview_plus_button.show()
            self.move_to_preview_button.hide()
            self._position_small_slot_swap_button()
            self.small_slot_swap_button.show()
            self.small_slot_swap_button.raise_()
            if self._preview_slot_lock_state is None:
                self._set_preview_lock_state(False, "Preview session active; awaiting alignment confirmation.")

    def _on_plus_clicked(self) -> None:
        if self.main_instance is not None and self.small_instance is None:
            self._log_capture("UI action: '+' clicked (move main to preview request).")
            self.add_instance_from_small()
            return

        if self.main_instance is None and self.small_instance is not None:
            self._log_capture("UI action: '+' clicked (move preview to main request).")
            self._on_swap_back_clicked()
            return

        self._log_capture("UI action: '+' clicked but no valid move action available.")

    def _on_small_slot_clicked(self) -> None:
        if self.small_instance is None:
            return
        self._log_capture("UI action: preview slot clicked -> swap request.")
        self.swap_slots()

    def _on_swap_back_clicked(self) -> None:
        self._log_capture("UI action: 'Swap To Main' clicked.")
        self.swap_slots()

    def _selected_session_label(self) -> str:
        index = self.session_selector.currentIndex() + 1
        return self._session_label_for_index(index)

    def _session_label_for_index(self, index: int) -> str:
        session_map = {
            1: self.config.session_1_id,
            2: self.config.session_2_id,
            3: self.config.session_3_id,
        }
        identifier = session_map.get(index) or "(unsaved ID)"
        return f"Session {index} - {identifier}"

    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.config)
        if dialog.exec() != QDialog.Accepted:
            return

        values = dialog.values()
        self.config.game_exe_path = values["game_exe_path"]
        self.config.session_1_id = values["session_1_id"]
        self.config.session_2_id = values["session_2_id"]
        self.config.session_3_id = values["session_3_id"]
        self.config_store.save(self.config)
        self._refresh_slot_placeholders()

    def set_resolution(self, resolution: str) -> None:
        self.config.resolution = resolution
        self.config_store.save(self.config)
        self._apply_resolution(resolution)

    def _apply_resolution(self, resolution: str) -> None:
        try:
            width_str, height_str = resolution.split("x", maxsplit=1)
            width, height = int(width_str), int(height_str)
        except ValueError:
            width, height = 1600, 900

        self.resize(width, height)
        self._constrain_to_available_geometry()
        self._schedule_geometry_refresh(40)

    def find_executable(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Locate everquest.exe", "", "Executable (*.exe)")
        path = selected
        if not path:
            hits = find_everquest_exe(common_search_roots(), max_hits=1)
            path = str(hits[0]) if hits else ""

        if not path:
            QMessageBox.warning(self, "Not Found", "Could not find everquest.exe.")
            return

        self._set_game_executable(path)

    def choose_executable(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Select everquest.exe", "", "Executable (*.exe)")
        if not selected:
            return

        self._set_game_executable(selected)

    def _set_game_executable(self, path: str) -> None:
        self.config.game_exe_path = path
        self.config_store.save(self.config)
        self._refresh_slot_placeholders()

    def _get_launcher_exe(self, eqgame_exe_path: str) -> tuple[str, bool]:
        """Get the LaunchPad.exe path if it exists in the same directory as eqgame.exe.
        Returns (exe_path, is_launchpad) tuple.
        """
        game_dir = Path(eqgame_exe_path).parent
        launchpad = game_dir / "LaunchPad.exe"
        if launchpad.exists():
            return (str(launchpad), True)
        return (eqgame_exe_path, False)

    def _launch_process(self, session_label: str) -> GameInstance | None:
        exe_path = self.config.game_exe_path
        if not exe_path:
            QMessageBox.warning(self, "Missing Game Path", "Set everquest.exe first.")
            return None

        if not Path(exe_path).exists():
            QMessageBox.warning(self, "Invalid Path", "Saved everquest.exe path does not exist.")
            self.config.game_exe_path = ""
            self.config_store.save(self.config)
            self._refresh_slot_placeholders()
            return None

        # Use LaunchPad.exe if available, otherwise use eqgame.exe directly
        launcher_path, is_launchpad = self._get_launcher_exe(exe_path)
        
        # Build launch arguments
        launch_args = [launcher_path]
        
        # If launching eqgame directly (no LaunchPad), try to bypass launcher check
        # by setting environment variable that Wine/Proton might use
        env = os.environ.copy()
        if not is_launchpad:
            # Try setting a variable that might satisfy the launcher check
            env["WINE_CPU_TOPOLOGY"] = "4:2"  # Might help Wine compatibility
            launch_args.append("+launcher")  # Some games recognize this parameter

        try:
            process = subprocess.Popen(launch_args, env=env)
        except OSError as error:
            QMessageBox.critical(self, "Launch Failed", f"Could not start EverQuest: {error}")
            return None

        hwnd = self.embedder.find_top_window_for_pid(process.pid)
        return GameInstance(
            process=process,
            hwnd=hwnd,
            session_label=session_label,
            parent_created_at=time.time(),
        )

    def launch_main_instance(self) -> None:
        if self.main_instance is not None:
            QMessageBox.information(self, "In Use", "Main slot is already running a session.")
            return

        session_index = 1 if self.small_instance is None else self.session_selector.currentIndex() + 1
        session_label = self._session_label_for_index(session_index)
        game = self._launch_process(session_label)
        if game is None:
            return

        self.main_instance = game
        self._attach_to_slot(game, self.main_slot)
        self._refresh_slot_placeholders()

    def add_instance_from_small(self) -> None:
        if self.main_instance is None:
            self._log_capture("Move to preview ignored: no main instance is running.")
            return

        if self.small_instance is not None:
            self._log_capture("Move to preview ignored: preview slot is already occupied.")
            return

        self.swap_slots()

    def swap_slots(self) -> None:
        main_hwnd = None if self.main_instance is None else (self.main_instance.child_hwnd or self.main_instance.hwnd)
        small_hwnd = None if self.small_instance is None else (self.small_instance.child_hwnd or self.small_instance.hwnd)
        self._log_capture(f"Swap start: main_hwnd={main_hwnd}, preview_hwnd={small_hwnd}.")
        
        if self.main_instance is not None or self.small_instance is not None:
            self._set_main_lock_state(False, "Swap requested: unlocking before session move.", force_log=True)

        if self.main_instance is None and self.small_instance is None:
            self._log_capture("Swap ignored: both slots are empty.")
            return

        try:
            if self.main_instance is not None and self.small_instance is None:
                self.small_instance = self.main_instance
                self.main_instance = None
                self._log_capture("Swap mode: moved main instance to preview slot.")
            elif self.main_instance is None and self.small_instance is not None:
                self.main_instance = self.small_instance
                self.small_instance = None
                self._log_capture("Swap mode: moved preview instance back to main slot.")
            else:
                self.main_instance, self.small_instance = self.small_instance, self.main_instance
                self._log_capture("Swap mode: exchanged main and preview instances.")

            if self.main_instance is not None:
                self._attach_to_slot(self.main_instance, self.main_slot)
            if self.small_instance is not None:
                self._attach_to_slot(self.small_instance, self.small_slot)

            self._ensure_main_window_foreground()

            self._refresh_slot_placeholders()
            new_main_hwnd = self.main_instance.child_hwnd if self.main_instance and self.main_instance.child_hwnd else (self.main_instance.hwnd if self.main_instance else None)
            new_small_hwnd = self.small_instance.child_hwnd if self.small_instance and self.small_instance.child_hwnd else (self.small_instance.hwnd if self.small_instance else None)
            self._log_capture(f"Swap success: main_hwnd={new_main_hwnd}, preview_hwnd={new_small_hwnd}.")
        except Exception as error:
            self._log_capture(f"Swap error: {error}")
            return

    def _attach_to_slot(self, instance: GameInstance, slot: SlotFrame) -> None:
        """Attach a game instance to a slot. Prefers child window (eqgame) over parent (LaunchPad)."""
        hwnd_to_manage = instance.child_hwnd if instance.child_hwnd is not None else instance.hwnd
        
        if hwnd_to_manage is None:
            slot_name = "Main" if slot == self.main_slot else "Preview"
            self._log_capture(f"{slot_name} attach skipped: no HWND available.")
            return

        if not self.embedder.is_window(hwnd_to_manage):
            slot_name = "Main" if slot == self.main_slot else "Preview"
            self._log_capture(f"{slot_name} attach failed: HWND {hwnd_to_manage} is invalid.")
            return

        # Prepare window for seamless transition to prevent squashing
        self.embedder.prepare_window_for_transition(hwnd_to_manage)
        
        # Special preparation for main slot to clear viewport state
        if slot == self.main_slot:
            self.embedder.prepare_window_for_main_slot(hwnd_to_manage)
        
        slot_name = "Main" if slot == self.main_slot else "Preview"
        if slot == self.main_slot:
            x, y, width, height = self._main_slot_target_rect()
        else:
            x, y, width, height = self._slot_global_rect(slot)
            
        if slot == self.small_slot:
            # Use floating attach for preview slot - same approach as main slot
            # Offset for control strip at top of preview slot
            preview_x = x
            preview_y = y + self._small_slot_control_strip_height
            preview_width = width
            preview_height = max(1, height - self._small_slot_control_strip_height)
            
            if self.embedder.attach_floating(hwnd_to_manage, preview_x, preview_y, preview_width, preview_height):
                # Maintain z-order to prevent game windows going behind application
                self.embedder.maintain_window_z_order(hwnd_to_manage, int(self.winId()))
                pid, title = self.embedder.get_window_info(hwnd_to_manage)
                self._log_capture(f"{slot_name} floating attach success: HWND {hwnd_to_manage}, PID {pid}, Title '{title}'.")
                instance.last_managed_rect = (preview_x, preview_y, preview_width, preview_height)
                # Validate preview slot alignment
                self._confirm_preview_lock_alignment(hwnd_to_manage, (preview_x, preview_y, preview_width, preview_height), "floating attach")
                self._set_preview_lock_state(True, "floating attach completed.")
                self._position_small_slot_swap_button()
                self.small_slot_swap_button.raise_()
            else:
                self._log_capture(f"{slot_name} floating attach failed: HWND {hwnd_to_manage} could not be positioned.")
                self._set_preview_lock_state(False, f"floating attach failed for HWND {hwnd_to_manage}.", force_log=True)
            return
        
        # Main slot uses attach_floating() which handles correct positioning directly
        # Window state management handled by our new coordinate alignment system
        
        self._set_main_lock_state(False, f"{slot_name} attach started for HWND {hwnd_to_manage}.", force_log=True)
        if slot == self.main_slot and (width < self._main_min_target_width or height < self._main_min_target_height):
            self._schedule_geometry_refresh(120)
            return

        if self.embedder.attach_floating(hwnd_to_manage, x, y, width, height):
            # Maintain z-order to prevent game windows going behind application
            self.embedder.maintain_window_z_order(hwnd_to_manage, int(self.winId()))
            pid, title = self.embedder.get_window_info(hwnd_to_manage)
            self._log_capture(f"{slot_name} floating attach success: HWND {hwnd_to_manage}, PID {pid}, Title '{title}'.")
            instance.last_managed_rect = (x, y, width, height)
            if not self.embedder.raise_floating_window(hwnd_to_manage):
                self._record_watchdog_error(f"Main z-order raise failed during attach for HWND {hwnd_to_manage}.")
            
            # Give more time for styles to apply before checking alignment
            time.sleep(0.1)
            
            # Check alignment but mark as locked regardless to prevent accumulation loops
            self._confirm_main_lock_alignment(hwnd_to_manage, (x, y, width, height), "attach")
            self._set_main_lock_state(True, "attach completed, accepting current position.")
            self._refresh_cycle_count = 0  # Reset to prevent further attempts
            
            if slot == self.main_slot:
                self._ensure_main_window_foreground()
        else:
            self._log_capture(f"{slot_name} floating attach failed: HWND {hwnd_to_manage} could not be positioned.")
            self._set_main_lock_state(False, "attach failed.", force_log=True)

    def _toggle_maximize(self) -> None:
        if self._is_custom_maximized and self._restore_geometry is not None:
            self.setGeometry(self._restore_geometry)
            self._is_custom_maximized = False
            self._schedule_geometry_refresh(40)
            return

        self._restore_geometry = self.geometry()
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        self.setGeometry(available)
        self._is_custom_maximized = True
        self._schedule_geometry_refresh(40)

    def _schedule_delayed_decoration_check(self, hwnd: int) -> None:
        """Schedule a delayed check to remove decorations that Windows may have restored."""
        if not self.embedder.is_window(hwnd):
            return
            
        self._pending_decoration_check_hwnd = hwnd
        self._decoration_check_timer.start(10000)  # 10 seconds delay
        self._log_capture(f"Scheduled delayed decoration check for HWND {hwnd} in 10 seconds.")
    
    def _check_and_strip_delayed_decorations(self) -> None:
        """Check for and remove decorations that may have been restored by Windows."""
        if self._pending_decoration_check_hwnd is None:
            return
            
        hwnd = self._pending_decoration_check_hwnd
        self._pending_decoration_check_hwnd = None
        
        if not self.embedder.is_window(hwnd):
            self._log_capture(f"Delayed decoration check skipped: HWND {hwnd} no longer valid.")
            return
            
        # Check if window still has problematic decorations
        if self.embedder.has_frame_controls(hwnd):
            self._log_capture(f"Delayed decoration check: Found restored decorations on HWND {hwnd}, re-stripping...")
            
            # Get current style for logging
            _, _, _, style, _ = self.embedder.get_window_state(hwnd)
            self._log_capture(f"Window style before delayed strip: {style}")
            
            # Strip decorations again
            success = self.embedder.strip_window_decorations(hwnd)
            
            if success:
                _, _, _, new_style, _ = self.embedder.get_window_state(hwnd)
                self._log_capture(f"Delayed decoration strip SUCCESS: style {style} -> {new_style}")
            else:
                self._log_capture(f"Delayed decoration strip FAILED for HWND {hwnd}")
        else:
            self._log_capture(f"Delayed decoration check: HWND {hwnd} decorations still clean, no action needed.")

    def _position_small_slot_swap_button(self) -> None:
        margin = 1
        x = max(0, self.small_slot.width() - self.small_slot_swap_button.width() - margin)
        y = max(0, margin)
        self.small_slot_swap_button.move(x, y)

    def _refresh_managed_windows(self) -> None:
        """Refresh positions/sizes of managed windows."""
        if self._safe_mode_enabled:
            return

        if self.main_instance:
            self._refresh_instance_window(self.main_instance, self.main_slot)

        if self.small_instance:
            self._refresh_instance_window(self.small_instance, self.small_slot)

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        self._schedule_geometry_refresh()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._schedule_geometry_refresh()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._constrain_to_available_geometry()
        self._schedule_geometry_refresh(40)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        
        # Handle window state changes (minimize/restore)
        if hasattr(event, 'type') and event.type() == event.Type.WindowStateChange:
            if self.isMinimized():
                # Application is being minimized - minimize game windows
                self._minimize_game_windows()
            elif not self.isMinimized() and hasattr(self, '_was_minimized') and self._was_minimized:
                # Application is being restored - restore game windows
                self._restore_game_windows()
            
            self._was_minimized = self.isMinimized()

    def _minimize_game_windows(self) -> None:
        """Minimize all game windows when the application is minimized."""
        self._log_capture("Application minimized - minimizing game windows")
        
        if self.main_instance:
            hwnd = self.main_instance.child_hwnd or self.main_instance.hwnd
            if hwnd and self.embedder.is_window(hwnd):
                self.embedder.minimize_window(hwnd)
        
        if self.small_instance:
            hwnd = self.small_instance.child_hwnd or self.small_instance.hwnd
            if hwnd and self.embedder.is_window(hwnd):
                self.embedder.minimize_window(hwnd)

    def _restore_game_windows(self) -> None:
        """Restore all game windows when the application is restored."""
        self._log_capture("Application restored - restoring game windows")
        
        if self.main_instance:
            hwnd = self.main_instance.child_hwnd or self.main_instance.hwnd
            if hwnd and self.embedder.is_window(hwnd):
                self.embedder.restore_window(hwnd)
                # Schedule a refresh to re-align the windows
                self._schedule_geometry_refresh(100)
        
        if self.small_instance:
            hwnd = self.small_instance.child_hwnd or self.small_instance.hwnd
            if hwnd and self.embedder.is_window(hwnd):
                self.embedder.restore_window(hwnd)
                # Schedule a refresh to re-align the windows  
                self._schedule_geometry_refresh(100)

    def _stop_instance(self, instance: GameInstance | None) -> None:
        if instance is None:
            return

        # Detach child window first if it exists
        if instance.child_hwnd:
            self.embedder.unembed_to_desktop(instance.child_hwnd)
        
        # Then detach parent window
        if instance.hwnd:
            self.embedder.unembed_to_desktop(instance.hwnd)

        # Stop child process if running (only for processes we launched)
        if instance.owns_process and instance.child_pid is not None:
            try:
                child = psutil.Process(instance.child_pid)
                child.terminate()
                child.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                try:
                    child = psutil.Process(instance.child_pid)
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        # Stop parent process (only for processes we launched)
        if instance.owns_process and instance.process is not None and instance.process.poll() is None:
            instance.process.terminate()
            try:
                instance.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                instance.process.kill()

    def reset_to_initial_state(self) -> None:
        self._stop_instance(self.main_instance)
        self._stop_instance(self.small_instance)

        self.main_instance = None
        self.small_instance = None
        self.session_selector.setCurrentIndex(0)
        self._refresh_slot_placeholders()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_instance(self.main_instance)
        self._stop_instance(self.small_instance)
        super().closeEvent(event)


def run() -> int:
    _configure_windows_dpi_awareness()
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()

    if os.name != "nt":
        QMessageBox.information(
            window,
            "Platform Note",
            "Floating game-window control is fully supported on Windows. This platform runs UI flow mode only.",
        )

    return app.exec()
