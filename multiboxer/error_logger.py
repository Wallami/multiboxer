"""
Error and diagnostic logging for multiboxer debugging.
Writes to errors.txt in the working directory.
Minimal logging - only errors and size mismatches to keep file small.
"""

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


# Log file path - in the multiboxer directory or cwd
def _get_log_path() -> Path:
    """Get the path to the error log file."""
    # Try to put it next to the main script/exe
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller exe
        base = Path(sys.executable).parent
    else:
        # Running as script
        base = Path(__file__).parent.parent
    return base / "errors.txt"


LOG_PATH = _get_log_path()


def _format_timestamp() -> str:
    """Get formatted timestamp."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write_log(level: str, message: str, include_trace: bool = False) -> None:
    """Write a log entry to the error file."""
    try:
        timestamp = _format_timestamp()
        entry = f"[{timestamp}] [{level}] {message}\n"
        
        if include_trace:
            entry += traceback.format_exc() + "\n"
        
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass  # Silent fail - don't spam stderr


def init_log() -> None:
    """Initialize the log file - clears previous content and starts fresh."""
    try:
        # Clear the log file on each app start
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(f"Session: {_format_timestamp()}\n")
    except Exception:
        pass


def log_info(message: str) -> None:
    """Log an info message."""
    _write_log("INFO", message)


def log_warning(message: str) -> None:
    """Log a warning message."""
    _write_log("WARN", message)


def log_error(message: str, include_trace: bool = True) -> None:
    """Log an error message, optionally with stack trace."""
    _write_log("ERROR", message, include_trace)


def log_debug(message: str) -> None:
    """Log a debug message."""
    _write_log("DEBUG", message)


def log_swap_state(action: str, main_hwnd: int | None, preview_hwnd: int | None,
                   main_rect: tuple | None, preview_rect: tuple | None,
                   main_target: tuple | None, preview_target: tuple | None) -> None:
    """Log swap state only if there's an issue."""
    # Only log if sizes don't match targets (indicates a problem)
    if action == "AFTER":
        issues = []
        if main_rect and main_target:
            w_diff = abs(main_rect[2] - main_target[2])
            h_diff = abs(main_rect[3] - main_target[3])
            if w_diff > 20 or h_diff > 20:
                issues.append(f"Main size mismatch: {main_rect} vs target {main_target}")
        if preview_rect and preview_target:
            w_diff = abs(preview_rect[2] - preview_target[2])
            h_diff = abs(preview_rect[3] - preview_target[3])
            if w_diff > 20 or h_diff > 20:
                issues.append(f"Preview size mismatch: {preview_rect} vs target {preview_target}")
        if issues:
            _write_log("SWAP_ERR", "; ".join(issues))


def log_window_operation(operation: str, hwnd: int, 
                         before_rect: tuple | None, after_rect: tuple | None,
                         target_rect: tuple | None, success: bool) -> None:
    """Log window operation only if there's a size mismatch."""
    if not success:
        _write_log("WINOP_FAIL", f"{operation} hwnd={hwnd} failed")
        return
        
    # Only log if there's a significant size discrepancy
    if after_rect and target_rect:
        w_diff = abs(after_rect[2] - target_rect[2])
        h_diff = abs(after_rect[3] - target_rect[3])
        if w_diff > 20 or h_diff > 20:
            _write_log("SIZE_ERR", f"hwnd={hwnd} after={after_rect} target={target_rect} diff=({w_diff},{h_diff})")


def log_exception(context: str) -> None:
    """Log an exception with full traceback."""
    _write_log("EXCEPTION", f"Exception in {context}", include_trace=True)
