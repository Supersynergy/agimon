"""Global window search — find and focus any window across all apps and Spaces."""
from __future__ import annotations
import subprocess
from dataclasses import dataclass


@dataclass
class AppWindow:
    app_name: str = ""
    window_title: str = ""
    window_index: int = 0
    position: str = ""
    size: str = ""
    focused: bool = False


def _run_osascript(script: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_all_windows() -> list[AppWindow]:
    """Get all visible windows across all apps via System Events."""
    script = '''
    tell application "System Events"
        set output to ""
        set visProcs to every process whose visible is true
        repeat with p in visProcs
            set pName to name of p
            try
                set wins to every window of p
                repeat with i from 1 to count of wins
                    set w to item i of wins
                    set wTitle to ""
                    try
                        set wTitle to name of w
                    end try
                    set wPos to ""
                    try
                        set wPos to position of w as text
                    end try
                    set wSize to ""
                    try
                        set wSize to size of w as text
                    end try
                    set output to output & pName & "|" & i & "|" & wTitle & "|" & wPos & "|" & wSize & linefeed
                end repeat
            end try
        end repeat
        return output
    end tell
    '''
    raw = _run_osascript(script, timeout=15)
    if not raw:
        return []

    windows = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) < 5:
            continue
        windows.append(AppWindow(
            app_name=parts[0],
            window_index=int(parts[1]) if parts[1].isdigit() else 0,
            window_title=parts[2],
            position=parts[3],
            size=parts[4],
        ))
    return windows


def search_windows(query: str) -> list[AppWindow]:
    """Search all windows by title or app name."""
    query_lower = query.lower()
    return [
        w for w in get_all_windows()
        if query_lower in w.window_title.lower()
        or query_lower in w.app_name.lower()
    ]


def focus_window(app_name: str, window_index: int = 1) -> bool:
    """Focus a specific window, bringing it to front across Spaces."""
    script = f'''
    tell application "{app_name}" to activate
    delay 0.3
    tell application "System Events"
        tell process "{app_name}"
            try
                perform action "AXRaise" of window {window_index}
            end try
        end tell
    end tell
    '''
    return bool(_run_osascript(script))


def focus_window_by_title(title_fragment: str) -> bool:
    """Find and focus a window by partial title match."""
    windows = search_windows(title_fragment)
    if not windows:
        return False
    win = windows[0]
    return focus_window(win.app_name, win.window_index)
