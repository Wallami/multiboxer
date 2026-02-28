"""
Tiled Desktop Manager - positions game windows on desktop without embedding.
Simple, stable approach using SetWindowPos for positioning only.
Does NOT modify window styles/decorations - leaves windows in their native state.
"""

import time
from dataclasses import dataclass
from typing import Callable

import win32con
import win32gui
import win32process
import ctypes
import psutil

from . import error_logger as log


# Extra flags not in win32con
SWP_NOSENDCHANGING = 0x0400
SWP_ASYNCWINDOWPOS = 0x4000
SWP_NOREDRAW = 0x0008


@dataclass
class WindowSlot:
    """Represents a screen position slot for a window."""
    x: int
    y: int
    width: int
    height: int
    hwnd: int | None = None
    original_width: int | None = None   # Window's native width when first grabbed
    original_height: int | None = None  # Window's native height when first grabbed
    

class TiledWindowManager:
    """Manages game windows in tiled desktop layout without embedding.
    
    Simple approach: just position windows, don't modify their styles.
    This avoids all the issues with decoration stripping causing keyboard/sizing bugs.
    """
    
    def __init__(self):
        self.main_slot: WindowSlot | None = None
        self.preview_slot: WindowSlot | None = None
        self._swap_callback: Callable[[], None] | None = None
        self.preserve_size: bool = True  # When True, swap only changes position, not size
        
    def set_layout(self, main_rect: tuple[int, int, int, int], 
                   preview_rect: tuple[int, int, int, int]) -> None:
        """Set the main and preview slot positions."""
        self.main_slot = WindowSlot(*main_rect)
        self.preview_slot = WindowSlot(*preview_rect)
        
    def find_eqgame_windows(self) -> list[int]:
        """Find all eqgame.exe windows."""
        windows = []
        
        def enum_callback(hwnd, _):
            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc = psutil.Process(pid)
                if proc.name().lower() == "eqgame.exe":
                    windows.append(hwnd)
            except Exception:
                pass
            return True
        
        win32gui.EnumWindows(enum_callback, None)
        return windows
    
    def is_hung_window(self, hwnd: int) -> bool:
        """Check if a window's owning thread is not responding.
        Uses IsHungAppWindow which returns True if the application has
        stopped processing messages for ~5 seconds."""
        try:
            return bool(ctypes.windll.user32.IsHungAppWindow(hwnd))
        except Exception:
            return False

    def is_valid_window(self, hwnd: int) -> bool:
        """Check if window handle is still valid."""
        try:
            is_window = win32gui.IsWindow(hwnd)
            is_visible = win32gui.IsWindowVisible(hwnd) if is_window else False
            result = bool(is_window and is_visible)
            if not result:
                log.log_debug(f"is_valid_window({hwnd}): IsWindow={is_window}, IsWindowVisible={is_visible}")
            return result
        except Exception as e:
            log.log_warning(f"is_valid_window({hwnd}): exception - {e}")
            return False
    
    def is_eqgame_window(self, hwnd: int) -> bool:
        """Check if window belongs to eqgame.exe - CRITICAL safety check."""
        if not self.is_valid_window(hwnd):
            return False
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            return proc.name().lower() == "eqgame.exe"
        except Exception:
            return False
    
    def get_window_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        """Get window position and size. Returns (x, y, width, height) or None."""
        if not self.is_valid_window(hwnd):
            return None
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            return (left, top, right - left, bottom - top)
        except Exception:
            return None
    
    def position_window(self, hwnd: int, x: int, y: int, width: int, height: int,
                         *, move_only: bool = False,
                         skip_validation: bool = False) -> bool:
        """Position a window at the specified location and size.
        ONLY affects eqgame.exe windows. Does NOT modify window styles.

        If *move_only* is True the window is repositioned with SWP_NOSIZE so
        that the game's DirectX renderer is never asked to reallocate its
        backbuffer — this prevents the freeze/crash that EverQuest exhibits
        when it receives unexpected WM_SIZE messages.

        If *skip_validation* is True, the eqgame.exe ownership check is
        skipped (use only during swap when we've already validated)."""

        if move_only:
            # ============================================================
            # FAST PATH — absolutely zero synchronous messages to the game
            # ============================================================
            # Every win32 call that "queries" a window (GetWindowPlacement,
            # GetWindowLong, IsWindowVisible, GetWindowRect, etc.) internally
            # sends a synchronous message to the target window's thread.
            # If that thread is busy rendering a frame, the call blocks our
            # Qt GUI thread until the game responds — which causes the freeze.
            #
            # The only safe call is SetWindowPos with SWP_ASYNCWINDOWPOS,
            # which *posts* instead of *sends*.  Everything else is skipped.
            # ============================================================
            try:
                flags = (win32con.SWP_NOSIZE
                         | win32con.SWP_NOZORDER
                         | win32con.SWP_NOACTIVATE
                         | SWP_NOSENDCHANGING
                         | SWP_ASYNCWINDOWPOS
                         | SWP_NOREDRAW)
                win32gui.SetWindowPos(hwnd, 0, x, y, 0, 0, flags)
                return True
            except Exception as e:
                log.log_error(f"position_window move_only FAILED hwnd={hwnd}: {e}")
                return False

        # ================================================================
        # FULL PATH — used for initial assign and non-preserve-size swaps
        # ================================================================
        if not skip_validation and not self.is_eqgame_window(hwnd):
            log.log_warning(f"position_window rejected non-EQ window: {hwnd}")
            return False
        
        # Capture state before positioning
        before_rect = self.get_window_rect(hwnd)
        target_rect = (x, y, width, height)
        log.log_debug(f"position_window START hwnd={hwnd} before={before_rect} target={target_rect}")
            
        try:
            # CRITICAL: Restore window if minimized or maximized BEFORE repositioning
            # SetWindowPos fails with E_INVALIDARG on maximized windows,
            # and repositioning a minimized window causes internal client area corruption
            placement = win32gui.GetWindowPlacement(hwnd)
            show_cmd = placement[1]
            if show_cmd == win32con.SW_SHOWMINIMIZED:
                log.log_info(f"Restoring minimized window hwnd={hwnd}")
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.05)
            elif show_cmd == win32con.SW_SHOWMAXIMIZED:
                log.log_info(f"Restoring maximized window hwnd={hwnd}")
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.05)
            
            # Also clear maximized style flag if still present
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            if style & win32con.WS_MAXIMIZE:
                log.log_info(f"Clearing WS_MAXIMIZE flag on hwnd={hwnd}")
                win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style & ~win32con.WS_MAXIMIZE)
            
            # Full move + resize
                win32gui.SetWindowPos(
                    hwnd,
                    0,  # ignored when SWP_NOZORDER is set
                    x, y, width, height,
                    win32con.SWP_SHOWWINDOW | win32con.SWP_NOZORDER
                )

                # Verify positioning worked (only for full resize — move_only
                # intentionally skips size changes so drift is expected)
                time.sleep(0.02)
                actual = self.get_window_rect(hwnd)
                if actual:
                    actual_x, actual_y, actual_w, actual_h = actual
                    if (abs(actual_x - x) > 10 or abs(actual_y - y) > 10 or
                        abs(actual_w - width) > 10 or abs(actual_h - height) > 10):
                        log.log_warning(f"SetWindowPos drift detected, using MoveWindow fallback. actual={actual} target={target_rect}")
                        win32gui.MoveWindow(hwnd, x, y, width, height, True)

                # Send WM_SIZE ONLY when the window was actually resized, so
                # the game can update its internal viewport.
                try:
                    client_rect = win32gui.GetClientRect(hwnd)
                    client_w = client_rect[2] - client_rect[0]
                    client_h = client_rect[3] - client_rect[1]
                    lparam = (client_h << 16) | (client_w & 0xFFFF)
                    win32gui.PostMessage(hwnd, win32con.WM_SIZE, win32con.SIZE_RESTORED, lparam)
                    log.log_debug(f"Sent WM_SIZE client=({client_w}, {client_h})")
                except Exception as e:
                    log.log_warning(f"WM_SIZE failed: {e}")
            
            # Log final state
            after_rect = self.get_window_rect(hwnd)
            log.log_window_operation("position_window", hwnd, before_rect, after_rect, target_rect, True)
            
            return True
        except Exception as e:
            log.log_error(f"position_window FAILED hwnd={hwnd}: {e}")
            after_rect = self.get_window_rect(hwnd)
            log.log_window_operation("position_window", hwnd, before_rect, after_rect, target_rect, False)
            return False
    
    def assign_to_main(self, hwnd: int) -> bool:
        """Assign a window to the main slot."""
        if self.main_slot is None:
            return False

        # Record the window's native size before repositioning
        current = self.get_window_rect(hwnd)
        if current:
            self.main_slot.original_width = current[2]
            self.main_slot.original_height = current[3]
            log.log_info(f"assign_to_main: recorded native size {current[2]}x{current[3]} for hwnd={hwnd}")

        if self.preserve_size and current:
            # Position-only: keep window's native size
            if self.position_window(hwnd, self.main_slot.x, self.main_slot.y,
                                    current[2], current[3]):
                self.main_slot.hwnd = hwnd
                self._bring_to_front(hwnd)
                return True
        else:
            # Full resize to slot dimensions
            if self.position_window(hwnd, self.main_slot.x, self.main_slot.y,
                                    self.main_slot.width, self.main_slot.height):
                self.main_slot.hwnd = hwnd
                self._bring_to_front(hwnd)
                return True
        return False
    
    def assign_to_preview(self, hwnd: int) -> bool:
        """Assign a window to the preview slot."""
        if self.preview_slot is None:
            return False

        # Record the window's native size before repositioning
        current = self.get_window_rect(hwnd)
        if current:
            self.preview_slot.original_width = current[2]
            self.preview_slot.original_height = current[3]
            log.log_info(f"assign_to_preview: recorded native size {current[2]}x{current[3]} for hwnd={hwnd}")

        if self.preserve_size and current:
            # Position-only: keep window's native size
            if self.position_window(hwnd, self.preview_slot.x, self.preview_slot.y,
                                    current[2], current[3]):
                self.preview_slot.hwnd = hwnd
                return True
        else:
            # Full resize to slot dimensions
            if self.position_window(hwnd, self.preview_slot.x, self.preview_slot.y,
                                    self.preview_slot.width, self.preview_slot.height):
                self.preview_slot.hwnd = hwnd
                return True
        return False
    
    def _bring_to_front(self, hwnd: int) -> None:
        """Bring window to front and give it focus.
        Uses only SetForegroundWindow — BringWindowToTop sends extra
        WM messages that can destabilise DirectX games.
        Skips hung windows to avoid blocking."""
        try:
            if self.is_hung_window(hwnd):
                log.log_warning(f"_bring_to_front: skipping hung window hwnd={hwnd}")
                return
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            log.log_warning(f"_bring_to_front failed hwnd={hwnd}: {e}")
    
    def swap_windows(self) -> bool:
        """Swap main and preview windows."""
        if self.main_slot is None or self.preview_slot is None:
            log.log_warning("swap_windows: slots not configured")
            return False
            
        main_hwnd = self.main_slot.hwnd
        preview_hwnd = self.preview_slot.hwnd
        
        # Both slots must have windows to swap
        if main_hwnd is None or preview_hwnd is None:
            log.log_warning(f"swap_windows: missing window - main={main_hwnd} preview={preview_hwnd}")
            return False

        use_move_only = self.preserve_size

        if use_move_only:
            # ==============================================================
            # FAST SWAP — zero synchronous queries to the game windows.
            #
            # Validation, rect logging, and size checks are all skipped
            # because every win32 "get" call sends a synchronous message to
            # the target window's thread. If that thread is busy (rendering
            # a frame, loading a zone, etc.) our Qt GUI thread blocks until
            # the game responds — which is the freeze.
            #
            # IsWindow() is the ONE safe call — it doesn't send a message.
            # ==============================================================
            if not win32gui.IsWindow(main_hwnd) or not win32gui.IsWindow(preview_hwnd):
                log.log_warning(f"swap_windows: window handle invalid - main={main_hwnd} preview={preview_hwnd}")
                return False

            log.log_info(f"swap_windows FAST: main={main_hwnd} -> ({self.preview_slot.x},{self.preview_slot.y}), "
                         f"preview={preview_hwnd} -> ({self.main_slot.x},{self.main_slot.y})")

            result1 = self.position_window(main_hwnd, self.preview_slot.x, self.preview_slot.y,
                                           0, 0, move_only=True)

            result2 = self.position_window(preview_hwnd, self.main_slot.x, self.main_slot.y,
                                           0, 0, move_only=True)

            # Update slot assignments and swap original sizes
            self.main_slot.hwnd = preview_hwnd
            self.preview_slot.hwnd = main_hwnd
            self.main_slot.original_width, self.preview_slot.original_width = (
                self.preview_slot.original_width, self.main_slot.original_width
            )
            self.main_slot.original_height, self.preview_slot.original_height = (
                self.preview_slot.original_height, self.main_slot.original_height
            )

            log.log_info(f"swap_windows FAST completed: result1={result1} result2={result2}")

            # SetForegroundWindow is also synchronous (sends WM_ACTIVATE).
            # Use a tiny ctypes call via PostMessage to request activation
            # without blocking.  If the game's thread is busy it will
            # process the focus change when it's ready.
            try:
                # WM_ACTIVATE: wParam=WA_ACTIVE(1), lParam=0
                win32gui.PostMessage(preview_hwnd, win32con.WM_ACTIVATE,
                                     win32con.WA_ACTIVE, 0)
            except Exception:
                pass
            # Also try the standard call — if the game is responsive this
            # is the reliable way to actually get foreground status.
            try:
                if not self.is_hung_window(preview_hwnd):
                    win32gui.SetForegroundWindow(preview_hwnd)
            except Exception:
                pass

            if self._swap_callback:
                self._swap_callback()

            return True

        # ==============================================================
        # FULL SWAP — used when preserve_size is off (resize mode)
        # ==============================================================
            
        # Validate both windows still exist
        if not self.is_valid_window(main_hwnd) or not self.is_valid_window(preview_hwnd):
            log.log_warning(f"swap_windows: invalid window - main_valid={self.is_valid_window(main_hwnd)} preview_valid={self.is_valid_window(preview_hwnd)}")
            return False

        # Determine sizes: each window takes the target slot's size
        main_ow = self.preview_slot.width
        main_oh = self.preview_slot.height
        prev_ow = self.main_slot.width
        prev_oh = self.main_slot.height

        # Log state before swap
        main_rect_before = self.get_window_rect(main_hwnd)
        preview_rect_before = self.get_window_rect(preview_hwnd)
        main_target = (self.preview_slot.x, self.preview_slot.y, main_ow, main_oh)
        preview_target = (self.main_slot.x, self.main_slot.y, prev_ow, prev_oh)
        
        log.log_swap_state("BEFORE", main_hwnd, preview_hwnd, 
                           main_rect_before, preview_rect_before,
                           main_target, preview_target)

        result1 = self.position_window(main_hwnd, self.preview_slot.x, self.preview_slot.y,
                             main_ow, main_oh, skip_validation=True)

        time.sleep(0.05)

        result2 = self.position_window(preview_hwnd, self.main_slot.x, self.main_slot.y,
                             prev_ow, prev_oh, skip_validation=True)
        
        # Update slot assignments AND swap original sizes to follow the window
        self.main_slot.hwnd = preview_hwnd
        self.preview_slot.hwnd = main_hwnd
        self.main_slot.original_width, self.preview_slot.original_width = (
            self.preview_slot.original_width, self.main_slot.original_width
        )
        self.main_slot.original_height, self.preview_slot.original_height = (
            self.preview_slot.original_height, self.main_slot.original_height
        )
        
        # Log state after swap
        main_rect_after = self.get_window_rect(main_hwnd)
        preview_rect_after = self.get_window_rect(preview_hwnd)
        log.log_swap_state("AFTER", preview_hwnd, main_hwnd,
                           preview_rect_after, main_rect_after,
                           preview_target, main_target)
        
        log.log_info(f"swap_windows completed: result1={result1} result2={result2}")
        
        time.sleep(0.05)
        self._bring_to_front(preview_hwnd)
        
        if self._swap_callback:
            self._swap_callback()
            
        return True
    
    def set_swap_callback(self, callback: Callable[[], None]) -> None:
        """Set callback to be called after swap."""
        self._swap_callback = callback
    
    def _effective_size(self, slot: WindowSlot) -> tuple[int, int]:
        """Return (width, height) to use for *slot*, respecting preserve_size."""
        if self.preserve_size and slot.original_width and slot.original_height:
            return (slot.original_width, slot.original_height)
        return (slot.width, slot.height)

    def refresh_positions(self) -> None:
        """Refresh window positions if they've drifted from target."""
        tolerance = 10  # pixels of drift allowed before repositioning
        
        for slot in [self.main_slot, self.preview_slot]:
            if slot and slot.hwnd:
                if self.is_valid_window(slot.hwnd):
                    current = self.get_window_rect(slot.hwnd)
                    if current:
                        x, y, w, h = current
                        if self.preserve_size:
                            # Only check *position* drift; size is the game's own
                            if (abs(x - slot.x) > tolerance or
                                abs(y - slot.y) > tolerance):
                                self.position_window(slot.hwnd, slot.x, slot.y,
                                                     w, h, move_only=True)
                        else:
                            target_w, target_h = self._effective_size(slot)
                            if (abs(x - slot.x) > tolerance or
                                abs(y - slot.y) > tolerance or
                                abs(w - target_w) > tolerance or
                                abs(h - target_h) > tolerance):
                                self.position_window(slot.hwnd, slot.x, slot.y,
                                                     target_w, target_h)
                else:
                    log.log_warning(f"refresh_positions: clearing slot hwnd={slot.hwnd} - window no longer valid")
                    slot.hwnd = None
    
    def validate_and_correct_all_windows(self) -> bool:
        """Check all managed windows and correct any that are out of position.
        Returns True if all windows are correct."""
        all_correct = True
        
        for slot in [self.main_slot, self.preview_slot]:
            if slot and slot.hwnd:
                if self.is_valid_window(slot.hwnd):
                    current = self.get_window_rect(slot.hwnd)
                    if current:
                        x, y, w, h = current
                        if self.preserve_size:
                            if (abs(x - slot.x) > 5 or abs(y - slot.y) > 5):
                                all_correct = False
                                self.position_window(slot.hwnd, slot.x, slot.y,
                                                     w, h, move_only=True)
                        else:
                            target_w, target_h = self._effective_size(slot)
                            if (abs(x - slot.x) > 5 or abs(y - slot.y) > 5 or
                                abs(w - target_w) > 5 or abs(h - target_h) > 5):
                                all_correct = False
                                self.position_window(slot.hwnd, slot.x, slot.y,
                                                     target_w, target_h)
                else:
                    log.log_warning(f"validate_and_correct: clearing slot hwnd={slot.hwnd} - window no longer valid")
                    slot.hwnd = None
        
        return all_correct
    
    def get_window_title(self, hwnd: int) -> str:
        """Get window title."""
        try:
            return win32gui.GetWindowText(hwnd)
        except Exception:
            return ""
    
    def release_all(self) -> None:
        """Release all managed windows (clear slot assignments)."""
        for slot in [self.main_slot, self.preview_slot]:
            if slot:
                slot.hwnd = None
                slot.original_width = None
                slot.original_height = None
