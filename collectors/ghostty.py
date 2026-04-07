"""Ghostty AppleScript bridge — batched queries, content reading, focus."""
from __future__ import annotations
import subprocess
from dataclasses import dataclass, field


@dataclass
class GhosttyTerminal:
    terminal_id: str = ""
    title: str = ""
    working_dir: str = ""
    window_index: int = 0
    tab_index: int = 0


@dataclass
class GhosttyTab:
    tab_id: str = ""
    title: str = ""
    index: int = 0
    selected: bool = False
    terminals: list[GhosttyTerminal] = field(default_factory=list)


@dataclass
class GhosttyWindow:
    window_id: str = ""
    title: str = ""
    index: int = 0
    tabs: list[GhosttyTab] = field(default_factory=list)


def _run_osascript(script: str, timeout: int = 10) -> str:
    """Execute AppleScript and return stdout."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_windows() -> list[GhosttyWindow]:
    """Get all Ghostty windows with tabs and terminals — single batched call."""
    # One AppleScript to collect everything at once
    script = '''
    tell application "Ghostty"
        set output to ""
        set winCount to count of windows
        repeat with wi from 1 to winCount
            set w to window wi
            set wID to id of w
            set wName to name of w
            set output to output & "WIN|" & wi & "|" & wID & "|" & wName & linefeed
            set tabCount to count of tabs of w
            repeat with ti from 1 to tabCount
                set t to tab ti of w
                set tID to id of t
                set tName to name of t
                set output to output & "TAB|" & wi & "|" & ti & "|" & tID & "|" & tName & linefeed
                set termCount to count of terminals of t
                repeat with tri from 1 to termCount
                    set trm to terminal tri of t
                    set trID to id of trm
                    set trName to name of trm
                    try
                        set trCWD to working directory of trm
                    on error
                        set trCWD to ""
                    end try
                    set output to output & "TERM|" & wi & "|" & ti & "|" & tri & "|" & trID & "|" & trName & "|" & trCWD & linefeed
                end repeat
            end repeat
        end repeat
        return output
    end tell
    '''
    raw = _run_osascript(script, timeout=15)
    if not raw:
        return []

    windows: dict[int, GhosttyWindow] = {}
    tabs: dict[tuple[int, int], GhosttyTab] = {}

    for line in raw.splitlines():
        parts = line.split("|")
        if not parts:
            continue

        if parts[0] == "WIN" and len(parts) >= 4:
            wi = int(parts[1])
            windows[wi] = GhosttyWindow(
                window_id=parts[2], title=parts[3], index=wi
            )

        elif parts[0] == "TAB" and len(parts) >= 5:
            wi, ti = int(parts[1]), int(parts[2])
            tab = GhosttyTab(tab_id=parts[3], title=parts[4], index=ti)
            tabs[(wi, ti)] = tab
            if wi in windows:
                windows[wi].tabs.append(tab)

        elif parts[0] == "TERM" and len(parts) >= 7:
            wi, ti = int(parts[1]), int(parts[2])
            term = GhosttyTerminal(
                terminal_id=parts[4],
                title=parts[5],
                working_dir=parts[6],
                window_index=wi,
                tab_index=ti,
            )
            if (wi, ti) in tabs:
                tabs[(wi, ti)].terminals.append(term)

    return list(windows.values())


def get_all_terminals_flat() -> list[dict]:
    """Get flat list of all terminals with window/tab context."""
    result = []
    for win in get_windows():
        for tab in win.tabs:
            for term in tab.terminals:
                result.append({
                    "window_id": win.window_id,
                    "window_title": win.title,
                    "window_index": win.index,
                    "tab_id": tab.tab_id,
                    "tab_title": tab.title,
                    "tab_index": tab.index,
                    "terminal_id": term.terminal_id,
                    "terminal_title": term.title,
                    "working_dir": term.working_dir,
                })
    return result


def focus_terminal(window_index: int, tab_index: int = 1) -> bool:
    """Focus a Ghostty window and tab, bringing it to front across Spaces."""
    script = f'''
    tell application "Ghostty"
        activate
        set w to window {window_index}
        tell w to activate window w
    end tell
    tell application "System Events"
        tell process "ghostty"
            perform action "AXRaise" of window {window_index}
        end tell
    end tell
    '''
    return bool(_run_osascript(script))


def read_terminal_content(window_index: int, tab_index: int = 1,
                          terminal_index: int = 1) -> str:
    """Read terminal text content via perform action select_all + clipboard."""
    script = f'''
    set oldClip to the clipboard
    tell application "Ghostty"
        set t to terminal {terminal_index} of tab {tab_index} of window {window_index}
        perform action "select_all" on t
        perform action "copy_to_clipboard" on t
        perform action "reset_terminal_soft" on t
    end tell
    delay 0.1
    set termText to the clipboard
    set the clipboard to oldClip
    return termText
    '''
    return _run_osascript(script, timeout=10)
