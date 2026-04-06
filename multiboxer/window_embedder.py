from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass


IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import win32api
    import win32con
    import win32gui
    import win32process

    USER32 = ctypes.windll.user32
    KERNEL32 = ctypes.windll.kernel32


@dataclass
class EmbeddedWindow:
    hwnd: int


class WindowEmbedder:
    def __init__(self) -> None:
        self._supported = IS_WINDOWS

    @property
    def supported(self) -> bool:
        return self._supported

    def find_top_window_for_pid(self, pid: int, timeout_seconds: float = 15.0) -> int | None:
        if not self._supported:
            return None

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            found_hwnd: int | None = None

            def callback(hwnd: int, _) -> bool:
                nonlocal found_hwnd
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                if window_pid == pid:
                    found_hwnd = hwnd
                    return False
                return True

            win32gui.EnumWindows(callback, None)
            if found_hwnd:
                return found_hwnd
            time.sleep(0.25)

        return None

    def find_top_windows_for_pid(self, pid: int) -> list[int]:
        if not self._supported:
            return []

        found_hwnds: list[int] = []

        def callback(hwnd: int, _) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True

            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if window_pid == pid:
                found_hwnds.append(hwnd)
            return True

        win32gui.EnumWindows(callback, None)
        return found_hwnds

    def is_window(self, hwnd: int) -> bool:
        if not self._supported:
            return False

        try:
            return bool(win32gui.IsWindow(hwnd))
        except Exception:
            return False

    def get_foreground_window_pid(self) -> tuple[int | None, int | None]:
        if not self._supported:
            return (None, None)

        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return (None, None)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            return (hwnd, pid)
        except Exception:
            return (None, None)

    def get_window_info(self, hwnd: int) -> tuple[int | None, str]:
        if not self._supported:
            return (None, "")

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            title = win32gui.GetWindowText(hwnd) or ""
            return (pid, title)
        except Exception:
            return (None, "")

    def get_window_state(self, hwnd: int) -> tuple[bool, bool, int | None, int | None, int | None]:
        if not self._supported:
            return (False, False, None, None, None)

        try:
            is_visible = bool(win32gui.IsWindowVisible(hwnd))
            is_enabled = bool(win32gui.IsWindowEnabled(hwnd))
            parent = win32gui.GetParent(hwnd)
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            return (is_visible, is_enabled, parent, style, exstyle)
        except Exception:
            return (False, False, None, None, None)

    def is_floating_style_locked(self, hwnd: int) -> bool:
        if not self._supported:
            return False

        try:
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            has_popup = bool(style & win32con.WS_POPUP)
            has_child = bool(style & win32con.WS_CHILD)
            has_caption = bool(style & win32con.WS_CAPTION)
            has_thickframe = bool(style & win32con.WS_THICKFRAME)
            has_sysmenu = bool(style & win32con.WS_SYSMENU)
            has_minimize_box = bool(style & win32con.WS_MINIMIZEBOX)
            has_maximize_box = bool(style & win32con.WS_MAXIMIZEBOX)
            is_minimized = bool(style & win32con.WS_MINIMIZE)
            is_maximized = bool(style & win32con.WS_MAXIMIZE)

            return (
                has_popup
                and not has_child
                and not has_caption
                and not has_thickframe
                and not has_sysmenu
                and not has_minimize_box
                and not has_maximize_box
                and not is_minimized
                and not is_maximized
            )
        except Exception:
            return False

    def has_frame_controls(self, hwnd: int) -> bool:
        if not self._supported:
            return False

        try:
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            return bool(
                (style & win32con.WS_CAPTION)
                or (style & win32con.WS_THICKFRAME)
                or (style & win32con.WS_SYSMENU)
                or (style & win32con.WS_MINIMIZEBOX)
                or (style & win32con.WS_MAXIMIZEBOX)
            )
        except Exception:
            return False

    def get_window_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        if not self._supported:
            return None

        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            return (
                int(left),
                int(top),
                max(1, int(right - left)),
                max(1, int(bottom - top)),
            )
        except Exception:
            return None

    def get_client_rect_on_screen(self, hwnd: int) -> tuple[int, int, int, int] | None:
        if not self._supported:
            return None

        try:
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            width = max(1, int(right - left))
            height = max(1, int(bottom - top))
            screen_left, screen_top = win32gui.ClientToScreen(hwnd, (0, 0))
            return (int(screen_left), int(screen_top), width, height)
        except Exception:
            return None

    def find_interactive_child(self, hwnd: int) -> int | None:
        if not self._supported:
            return None

        if not self.is_window(hwnd):
            return None

        found_visible_enabled: int | None = None
        found_enabled: int | None = None
        found_any: int | None = None

        def callback(child_hwnd: int, _) -> bool:
            nonlocal found_visible_enabled, found_enabled, found_any
            try:
                if found_any is None:
                    found_any = child_hwnd
                if found_enabled is None and win32gui.IsWindowEnabled(child_hwnd):
                    found_enabled = child_hwnd
                if win32gui.IsWindowVisible(child_hwnd) and win32gui.IsWindowEnabled(child_hwnd):
                    found_visible_enabled = child_hwnd
                    return False
            except Exception:
                return True
            return True

        try:
            win32gui.EnumChildWindows(hwnd, callback, None)
        except Exception:
            return None
        return found_visible_enabled or found_enabled or found_any

    def list_child_windows(self, hwnd: int) -> list[tuple[int, bool, bool]]:
        if not self._supported:
            return []
        if not self.is_window(hwnd):
            return []

        children: list[tuple[int, bool, bool]] = []

        def callback(child_hwnd: int, _) -> bool:
            try:
                children.append(
                    (
                        child_hwnd,
                        bool(win32gui.IsWindowVisible(child_hwnd)),
                        bool(win32gui.IsWindowEnabled(child_hwnd)),
                    )
                )
            except Exception:
                pass
            return True

        try:
            win32gui.EnumChildWindows(hwnd, callback, None)
        except Exception:
            return []
        return children

    def resolve_input_target_from_screen(self, root_hwnd: int, screen_x: int, screen_y: int) -> int:
        if not self._supported or not self.is_window(root_hwnd):
            return root_hwnd

        try:
            hwnd_at_point = win32gui.WindowFromPoint((int(screen_x), int(screen_y)))
            if hwnd_at_point and self.is_window(hwnd_at_point):
                if hwnd_at_point == root_hwnd or win32gui.IsChild(root_hwnd, hwnd_at_point):
                    return hwnd_at_point
        except Exception:
            pass

        child_fallback = self.find_interactive_child(root_hwnd)
        if child_fallback is not None:
            return child_fallback
        return root_hwnd

    def resolve_input_target(self, root_hwnd: int, x: int, y: int) -> int:
        if not self._supported or not self.is_window(root_hwnd):
            return root_hwnd

        try:
            child = win32gui.ChildWindowFromPointEx(
                root_hwnd,
                (max(0, x), max(0, y)),
                win32con.CWP_SKIPINVISIBLE | win32con.CWP_SKIPDISABLED,
            )
            if child and child != root_hwnd and self.is_window(child):
                return child
        except Exception:
            pass

        child_fallback = self.find_interactive_child(root_hwnd)
        if child_fallback is not None:
            return child_fallback
        return root_hwnd

    def activate_window(self, hwnd: int) -> bool:
        if not self._supported:
            return False

        try:
            if not win32gui.IsWindow(hwnd):
                return False

            fg_hwnd = win32gui.GetForegroundWindow()
            fg_thread = USER32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            target_thread = USER32.GetWindowThreadProcessId(hwnd, None)
            current_thread = KERNEL32.GetCurrentThreadId()
            root_hwnd = USER32.GetAncestor(hwnd, win32con.GA_ROOT)
            root_thread = USER32.GetWindowThreadProcessId(root_hwnd, None) if root_hwnd else 0

            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNA)
            except Exception:
                pass
            attached_fg = False
            attached_cur = False
            try:
                attach_target_thread = root_thread if root_thread else target_thread
                if fg_thread and attach_target_thread and fg_thread != attach_target_thread:
                    USER32.AttachThreadInput(fg_thread, attach_target_thread, True)
                    attached_fg = True
                if current_thread and attach_target_thread and current_thread != attach_target_thread:
                    USER32.AttachThreadInput(current_thread, attach_target_thread, True)
                    attached_cur = True

                if root_hwnd:
                    USER32.BringWindowToTop(root_hwnd)
                    try:
                        win32gui.SetForegroundWindow(root_hwnd)
                    except Exception:
                        pass

                USER32.BringWindowToTop(hwnd)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                try:
                    win32gui.SetActiveWindow(hwnd)
                except Exception:
                    pass
                try:
                    win32gui.SetFocus(hwnd)
                except Exception:
                    pass
            finally:
                if attached_cur:
                    USER32.AttachThreadInput(current_thread, attach_target_thread, False)
                if attached_fg:
                    USER32.AttachThreadInput(fg_thread, attach_target_thread, False)
            return True
        except Exception:
            return False

    def forward_keyboard_event(self, hwnd: int, vk_code: int, key_down: bool) -> bool:
        if not self._supported:
            return False

        try:
            if not win32gui.IsWindow(hwnd):
                return False
            message = win32con.WM_KEYDOWN if key_down else win32con.WM_KEYUP
            win32gui.SendMessage(hwnd, message, int(vk_code), 0)
            return True
        except Exception:
            return False

    def inject_global_mouse(self, kind: str, screen_x: int, screen_y: int, button: str = "left") -> bool:
        if not self._supported:
            return False

        try:
            win32api.SetCursorPos((int(screen_x), int(screen_y)))

            if kind == "move":
                return True

            if button == "right":
                down_flag = win32con.MOUSEEVENTF_RIGHTDOWN
                up_flag = win32con.MOUSEEVENTF_RIGHTUP
            elif button == "middle":
                down_flag = win32con.MOUSEEVENTF_MIDDLEDOWN
                up_flag = win32con.MOUSEEVENTF_MIDDLEUP
            else:
                down_flag = win32con.MOUSEEVENTF_LEFTDOWN
                up_flag = win32con.MOUSEEVENTF_LEFTUP

            if kind == "down":
                win32api.mouse_event(down_flag, 0, 0, 0, 0)
                return True
            if kind == "up":
                win32api.mouse_event(up_flag, 0, 0, 0, 0)
                return True

            return False
        except Exception:
            return False

    def inject_global_keyboard(self, vk_code: int, key_down: bool) -> bool:
        if not self._supported:
            return False

        try:
            flags = 0 if key_down else win32con.KEYEVENTF_KEYUP
            win32api.keybd_event(int(vk_code), 0, flags, 0)
            return True
        except Exception:
            return False

    def forward_mouse_event(self, hwnd: int, kind: str, x: int, y: int, button: str = "left") -> bool:
        if not self._supported:
            return False

        try:
            if not win32gui.IsWindow(hwnd):
                return False

            lparam = win32api.MAKELONG(max(0, x), max(0, y))
            if kind == "move":
                win32gui.SendMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
                return True

            if button == "right":
                down_msg = win32con.WM_RBUTTONDOWN
                up_msg = win32con.WM_RBUTTONUP
                wparam_down = win32con.MK_RBUTTON
            elif button == "middle":
                down_msg = win32con.WM_MBUTTONDOWN
                up_msg = win32con.WM_MBUTTONUP
                wparam_down = win32con.MK_MBUTTON
            else:
                down_msg = win32con.WM_LBUTTONDOWN
                up_msg = win32con.WM_LBUTTONUP
                wparam_down = win32con.MK_LBUTTON

            if kind == "down":
                win32gui.SendMessage(hwnd, down_msg, wparam_down, lparam)
                return True
            if kind == "up":
                win32gui.SendMessage(hwnd, up_msg, 0, lparam)
                return True

            return False
        except Exception:
            return False

    def embed(self, hwnd: int, container_hwnd: int) -> None:
        if not self._supported:
            return

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass

        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        style = style & ~win32con.WS_OVERLAPPEDWINDOW
        style = style & ~win32con.WS_OVERLAPPED
        style = style & ~win32con.WS_POPUP
        style = style | win32con.WS_CHILD
        style = style | win32con.WS_VISIBLE
        style = style & ~win32con.WS_CAPTION
        style = style & ~win32con.WS_THICKFRAME
        style = style & ~win32con.WS_SYSMENU
        style = style & ~win32con.WS_MINIMIZEBOX
        style = style & ~win32con.WS_MAXIMIZEBOX
        style = style & ~win32con.WS_MINIMIZE
        style = style & ~win32con.WS_MAXIMIZE
        style = style & ~win32con.WS_BORDER
        style = style & ~win32con.WS_DLGFRAME
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
        exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        exstyle = exstyle & ~win32con.WS_EX_NOACTIVATE
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, exstyle)
        win32gui.SetParent(hwnd, container_hwnd)
        win32gui.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_NOZORDER
            | win32con.SWP_FRAMECHANGED,
        )
        win32gui.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_NOZORDER
            | win32con.SWP_SHOWWINDOW,
        )
        win32gui.EnableWindow(hwnd, True)
        self.resize_embedded(hwnd, container_hwnd)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    def embed_with_fallback(self, hwnd: int, container_hwnd: int) -> bool:
        if not self._supported:
            return False

        try:
            self.embed(hwnd, container_hwnd)
            if self.is_embedded_in(hwnd, container_hwnd):
                return True

            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass

            win32gui.SetParent(hwnd, container_hwnd)
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            style = style & ~win32con.WS_OVERLAPPEDWINDOW
            style = style & ~win32con.WS_OVERLAPPED
            style = style & ~win32con.WS_POPUP
            style = style | win32con.WS_CHILD
            style = style & ~win32con.WS_CAPTION
            style = style & ~win32con.WS_THICKFRAME
            style = style & ~win32con.WS_SYSMENU
            style = style & ~win32con.WS_MINIMIZEBOX
            style = style & ~win32con.WS_MAXIMIZEBOX
            style = style & ~win32con.WS_MINIMIZE
            style = style & ~win32con.WS_MAXIMIZE
            style = style & ~win32con.WS_BORDER
            style = style & ~win32con.WS_DLGFRAME
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
            exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            exstyle = exstyle & ~win32con.WS_EX_NOACTIVATE
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, exstyle)
            win32gui.SetWindowPos(
                hwnd,
                0,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_NOZORDER
                | win32con.SWP_FRAMECHANGED
                | win32con.SWP_SHOWWINDOW,
            )
            win32gui.EnableWindow(hwnd, True)
            self.resize_embedded(hwnd, container_hwnd)
            return self.is_embedded_in(hwnd, container_hwnd)
        except Exception:
            return False

    def is_embedded_in(self, hwnd: int, container_hwnd: int) -> bool:
        if not self._supported:
            return False

        try:
            return win32gui.GetParent(hwnd) == container_hwnd
        except Exception:
            return False

    def resize_embedded(self, hwnd: int, container_hwnd: int) -> None:
        if not self._supported:
            return

        left, top, right, bottom = win32gui.GetClientRect(container_hwnd)
        width = max(1, right - left)
        height = max(1, bottom - top)
        win32gui.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            width,
            height,
            win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
        )
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    def resize_embedded_with_insets(
        self,
        hwnd: int,
        container_hwnd: int,
        inset_left: int,
        inset_top: int,
        inset_right: int,
        inset_bottom: int,
    ) -> None:
        if not self._supported:
            return

        left, top, right, bottom = win32gui.GetClientRect(container_hwnd)
        total_width = max(1, right - left)
        total_height = max(1, bottom - top)

        x = max(0, int(inset_left))
        y = max(0, int(inset_top))
        width = max(1, total_width - x - max(0, int(inset_right)))
        height = max(1, total_height - y - max(0, int(inset_bottom)))

        win32gui.SetWindowPos(
            hwnd,
            0,
            x,
            y,
            width,
            height,
            win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
        )
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    def position_embedded_no_resize(self, hwnd: int, x: int, y: int) -> None:
        if not self._supported:
            return

        try:
            win32gui.SetWindowPos(
                hwnd,
                0,
                int(x),
                int(y),
                0,
                0,
                win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
            )
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        except Exception:
            return

    def attach_floating(self, hwnd: int, x: int, y: int, width: int, height: int) -> bool:
        if not self._supported:
            return False

        try:
            if not win32gui.IsWindow(hwnd):
                return False

            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass

            win32gui.SetParent(hwnd, 0)

            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            style = style & ~win32con.WS_OVERLAPPEDWINDOW
            style = style & ~win32con.WS_OVERLAPPED
            style = style & ~win32con.WS_CHILD
            style = style & ~win32con.WS_DISABLED
            style = style | win32con.WS_POPUP
            style = style | win32con.WS_VISIBLE
            style = style & ~win32con.WS_CAPTION
            style = style & ~win32con.WS_THICKFRAME
            style = style & ~win32con.WS_SYSMENU
            style = style & ~win32con.WS_MINIMIZEBOX
            style = style & ~win32con.WS_MAXIMIZEBOX
            style = style & ~win32con.WS_MINIMIZE
            style = style & ~win32con.WS_MAXIMIZE
            style = style & ~win32con.WS_BORDER
            style = style & ~win32con.WS_DLGFRAME
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

            exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            exstyle = exstyle & ~win32con.WS_EX_NOACTIVATE
            exstyle = exstyle & ~win32con.WS_EX_TRANSPARENT
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, exstyle)

            win32gui.EnableWindow(hwnd, True)

            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                int(x),
                int(y),
                max(1, int(width)),
                max(1, int(height)),
                win32con.SWP_SHOWWINDOW | win32con.SWP_FRAMECHANGED | win32con.SWP_NOOWNERZORDER,
            )
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_NOTOPMOST,
                int(x),
                int(y),
                max(1, int(width)),
                max(1, int(height)),
                win32con.SWP_SHOWWINDOW | win32con.SWP_NOOWNERZORDER,
            )
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            return True
        except Exception:
            return False

    def raise_floating_window(self, hwnd: int) -> bool:
        if not self._supported:
            return False

        try:
            if not win32gui.IsWindow(hwnd):
                return False

            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW | win32con.SWP_NOOWNERZORDER,
            )
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW | win32con.SWP_NOOWNERZORDER,
            )
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            return True
        except Exception:
            return False

    def show_window(self, hwnd: int) -> None:
        if not self._supported:
            return

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        except Exception:
            return

    def hide_window(self, hwnd: int) -> None:
        if not self._supported:
            return

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        except Exception:
            return

    def unembed_to_desktop(self, hwnd: int) -> None:
        if not self._supported:
            return

        win32gui.SetParent(hwnd, 0)
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        style = style & ~win32con.WS_CHILD
        style = style | win32con.WS_POPUP
        style = style | win32con.WS_CAPTION
        style = style | win32con.WS_THICKFRAME
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
        win32gui.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_NOZORDER
            | win32con.SWP_NOACTIVATE
            | win32con.SWP_FRAMECHANGED,
        )
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
