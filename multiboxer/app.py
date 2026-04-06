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
        self._main_slot_lock_tolerance = 6
        self._main_slot_control_strip_height = 22
        self._main_attach_throttle_seconds = 1.2
        self._last_main_attach_at = 0.0
        self._main_lock_correction_cooldown_seconds = 2.5
        self._last_main_lock_correction_at = 0.0
        self._main_min_target_width = 640
        self._main_min_target_height = 360
        self._last_stable_main_target_rect: tuple[int, int, int, int] | None = None
        self._pixel_perfect_fit_enabled = False
        self._is_custom_maximized = False
        self._restore_geometry = None
        self._swap_shortcut_latched = False

        self.main_instance: GameInstance | None = None
        self.small_instance: GameInstance | None = None
        self._watchdog_last_signature: dict[str, tuple] = {}
        self._watchdog_errors: deque[str] = deque(maxlen=25)
        self._verbose_watchdog_logs = False
        self._safe_mode_enabled = False

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
        client_rect = self.embedder.get_client_rect_on_screen(hwnd)
        window_rect = self.embedder.get_window_rect(hwnd)

        if client_rect is None and window_rect is None:
            self._set_main_lock_state(False, f"{context}: unable to read window rect for HWND {hwnd}.", force_log=True)
            return False

        if client_rect is not None and self._is_rect_aligned(target_rect, client_rect, self._main_slot_lock_tolerance):
            self._set_main_lock_state(True, f"{context}: aligned (client) at {client_rect}.")
            return True

        if window_rect is not None and self._is_rect_aligned(target_rect, window_rect, self._main_slot_lock_tolerance):
            self._set_main_lock_state(True, f"{context}: aligned (window) at {window_rect}.")
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

    def _retry_main_lock_alignment(self, hwnd: int, target_rect: tuple[int, int, int, int], context: str, max_attempts: int = 3) -> bool:
        x, y, width, height = target_rect
        for attempt in range(1, max_attempts + 1):
            aligned = self._confirm_main_lock_alignment(hwnd, target_rect, f"{context} attempt {attempt}")
            if aligned:
                return True

            if self._verbose_watchdog_logs:
                self._log_capture(f"Main lock retry: attempt {attempt}/{max_attempts} for HWND {hwnd}.")
            attached = self.embedder.attach_floating(hwnd, x, y, width, height)
            if not attached:
                self._record_watchdog_error(
                    f"Main lock retry attach failed: attempt {attempt}/{max_attempts} for HWND {hwnd}."
                )
                continue

        self._record_watchdog_error(
            f"Main lock retry exhausted: HWND {hwnd} not aligned after {max_attempts} attempt(s)."
        )
        return False

    def _refresh_instance_window(self, instance: GameInstance, slot: SlotFrame) -> None:
        hwnd = instance.child_hwnd if instance.child_hwnd is not None else instance.hwnd
        if hwnd is None:
            return

        if slot == self.main_slot:
            target_rect = self._main_slot_target_rect()
        else:
            target_rect = self._slot_global_rect(slot)
        x, y, width, height = target_rect
        is_visible, is_enabled, parent_hwnd, _, _ = self.embedder.get_window_state(hwnd)
        if slot == self.small_slot:
            container_hwnd = int(slot.winId())
            if parent_hwnd != container_hwnd:
                self._attach_to_slot(instance, slot)
                return

            needs_resize = (
                not is_visible
                or not is_enabled
            )
            if needs_resize:
                self.embedder.position_embedded_no_resize(hwnd, 0, self._small_slot_control_strip_height)
            instance.last_managed_rect = target_rect
            self.small_slot_swap_button.raise_()
            return

        main_rect_tolerance = 48 if slot == self.main_slot else 2
        needs_attach = (
            self._rect_changed(instance.last_managed_rect, target_rect, tolerance=main_rect_tolerance)
            or parent_hwnd not in (0, None)
            or not is_visible
            or not is_enabled
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

            attached = self.embedder.attach_floating(hwnd, x, y, width, height)
            if attached:
                instance.last_managed_rect = target_rect
                if slot == self.main_slot:
                    if not self.embedder.raise_floating_window(hwnd):
                        self._record_watchdog_error(f"Main z-order raise failed after attach for HWND {hwnd}.")
                    self._confirm_main_lock_alignment(hwnd, target_rect, "refresh attach")
            elif slot == self.main_slot:
                self._set_main_lock_state(False, "refresh attach failed.", force_log=True)
            return

        if slot == self.main_slot:
            self._confirm_main_lock_alignment(hwnd, target_rect, "refresh alignment check")

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
        else:
            self.small_slot.show_embed(f"Preview: {self.small_instance.session_label}")
            self.preview_status_box.setText(f"Preview: {self.small_instance.session_label}")
            self.preview_plus_button.show()
            self.move_to_preview_button.hide()
            self._position_small_slot_swap_button()
            self.small_slot_swap_button.show()
            self.small_slot_swap_button.raise_()

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

        slot_name = "Main" if slot == self.main_slot else "Preview"
        if slot == self.main_slot:
            x, y, width, height = self._main_slot_target_rect()
        else:
            x, y, width, height = self._slot_global_rect(slot)
        if slot == self.small_slot:
            container_hwnd = int(slot.winId())
            if self.embedder.embed_with_fallback(hwnd_to_manage, container_hwnd):
                self.embedder.position_embedded_no_resize(hwnd_to_manage, 0, self._small_slot_control_strip_height)
                pid, title = self.embedder.get_window_info(hwnd_to_manage)
                self._log_capture(f"{slot_name} embedded attach success: HWND {hwnd_to_manage}, PID {pid}, Title '{title}'.")
                instance.last_managed_rect = (x, y, width, height)
                self._position_small_slot_swap_button()
                self.small_slot_swap_button.raise_()
            else:
                self._log_capture(f"{slot_name} embedded attach failed: HWND {hwnd_to_manage} could not be positioned.")
            return

        self._set_main_lock_state(False, f"{slot_name} attach started for HWND {hwnd_to_manage}.", force_log=True)
        if slot == self.main_slot and (width < self._main_min_target_width or height < self._main_min_target_height):
            self._schedule_geometry_refresh(120)
            return

        if self.embedder.attach_floating(hwnd_to_manage, x, y, width, height):
            pid, title = self.embedder.get_window_info(hwnd_to_manage)
            self._log_capture(f"{slot_name} floating attach success: HWND {hwnd_to_manage}, PID {pid}, Title '{title}'.")
            instance.last_managed_rect = (x, y, width, height)
            if not self.embedder.raise_floating_window(hwnd_to_manage):
                self._record_watchdog_error(f"Main z-order raise failed during attach for HWND {hwnd_to_manage}.")
            self._retry_main_lock_alignment(hwnd_to_manage, (x, y, width, height), "attach")
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
