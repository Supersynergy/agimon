"""Synapse Telepathy integration — list + jump to cross-session activity.

Pulls live cross-session events from Synapse (`syn search telepathy`),
parses the `[sid8][cwd]` tags, resolves each to a full Claude Code
session-id (via ~/.claude/projects/**/*.jsonl) and, if possible, to a
live Ghostty window (by cwd match). One agimon command then jumps
directly to the right window or resumes the session.
"""
from __future__ import annotations
import os
import re
import glob
import subprocess
from dataclasses import dataclass
from pathlib import Path

from collectors.ghostty import get_windows as _raw_get_windows, focus_terminal, _run_osascript
from collectors.cache import ttl_cache

get_windows = ttl_cache(3.0)(_raw_get_windows)


SYN_BIN = os.environ.get("SYN_BIN", "syn")
CLAUDE_BIN = os.environ.get(
    "CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))
PROJECTS = Path.home() / ".claude/projects"

TAG_RE = re.compile(
    r"\[telepathy\]\[(?P<sid>[a-f0-9]{8})\]\[(?P<cwd>[^\]]+)\]"
    r"\[(?P<kind>prompt|reply|tools)\]\s*(?P<body>.*)"
)


@dataclass
class TelepathyEvent:
    sid8: str
    cwd: str
    kind: str
    body: str
    score: float
    doc_id: str


@ttl_cache(3.0)
def fetch_events(limit: int = 30) -> list[TelepathyEvent]:
    """Run `syn search telepathy` and parse into structured events."""
    try:
        r = subprocess.run([SYN_BIN, "search", "telepathy"],
                           capture_output=True, text=True, timeout=6)
    except Exception:
        return []
    out: list[TelepathyEvent] = []
    seen = set()
    for line in r.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        try:
            score = float(parts[0])
        except ValueError:
            continue
        doc_id, text = parts[1], parts[2]
        m = TAG_RE.search(text)
        if not m:
            continue
        sig = (m.group("sid"), m.group("body"))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(TelepathyEvent(
            sid8=m.group("sid"),
            cwd=m.group("cwd"),
            kind=m.group("kind"),
            body=m.group("body").strip(),
            score=score,
            doc_id=doc_id,
        ))
        if len(out) >= limit:
            break
    return out


def resolve_session_id(sid8: str) -> str | None:
    """sid8 (8 hex chars) → full session-id by matching jsonl basenames."""
    for f in glob.glob(str(PROJECTS / "**/*.jsonl"), recursive=True):
        name = os.path.basename(f)
        if name.startswith(sid8):
            return name[:-len(".jsonl")]
    return None


def session_cwd(sid8: str) -> str | None:
    """Resolve cwd recorded in the jsonl (first line with cwd field)."""
    for f in glob.glob(str(PROJECTS / "**/*.jsonl"), recursive=True):
        if not os.path.basename(f).startswith(sid8):
            continue
        try:
            with open(f, "r") as fh:
                import json
                for line in fh:
                    try:
                        ev = json.loads(line)
                        if ev.get("cwd"):
                            return ev["cwd"]
                    except Exception:
                        pass
        except Exception:
            return None
    return None


def find_ghostty_window(cwd: str) -> tuple[int, int] | None:
    """Return (window_index, tab_index) for a Ghostty terminal whose
    working_dir matches cwd basename. None if no match."""
    target = os.path.basename(cwd.rstrip("/")) or cwd
    for win in get_windows():
        for tab in win.tabs:
            for term in tab.terminals:
                wd = term.working_dir or ""
                if not wd:
                    continue
                if wd == cwd or os.path.basename(wd.rstrip("/")) == target:
                    return (win.index, tab.index or 1)
    return None


def jump(sid8: str) -> str:
    """Focus live Ghostty window for sid8, else resume in a new window.
    Returns a human-readable status line."""
    full = resolve_session_id(sid8)
    cwd = session_cwd(sid8)

    # 1. Try live-window focus
    if cwd:
        hit = find_ghostty_window(cwd)
        if hit:
            w, t = hit
            focus_terminal(w, t)
            return f"⚡ focused live window {w}:{t} ({cwd})"

    # 2. Resume in new Ghostty window
    if full:
        cwd_arg = cwd or os.path.expanduser("~")
        cmd = (f"{CLAUDE_BIN} --resume {full} "
               f"--dangerously-skip-permissions")
        script = f'''tell application "Ghostty"
    activate
    set cfg to new surface configuration
    set initial working directory of cfg to "{cwd_arg}"
    set command of cfg to "{cmd}"
    new window with configuration cfg
end tell'''
        _run_osascript(script)
        return f"⚡ resumed {full[:12]}… in new window ({cwd_arg})"

    return f"⚠ unknown session {sid8}"


def format_feed(events: list[TelepathyEvent],
                current_sid8: str | None = None) -> str:
    """Pretty-print feed with color + live-window indicator."""
    if not events:
        return "\033[0;90m(no cross-session activity — is the daemon running?)\033[0m"
    # Precompute live-ness
    live_cwds = set()
    for win in get_windows():
        for tab in win.tabs:
            for term in tab.terminals:
                if term.working_dir:
                    live_cwds.add(os.path.basename(term.working_dir.rstrip('/')))
    lines = ["\033[1;35m📡 Telepathy — cross-session feed\033[0m", ""]
    for i, ev in enumerate(events, 1):
        if ev.sid8 == current_sid8:
            continue
        live = "\033[0;32m●\033[0m" if ev.cwd in live_cwds else "\033[0;90m○\033[0m"
        kind_col = {"prompt":"\033[0;36m","reply":"\033[0;33m","tools":"\033[0;90m"}[ev.kind]
        body = ev.body[:80]
        lines.append(
            f"{live} \033[1;37m[{i:2}]\033[0m "
            f"\033[0;35m{ev.sid8}\033[0m "
            f"\033[0;34m{ev.cwd:<14}\033[0m "
            f"{kind_col}{ev.kind:<6}\033[0m {body}"
        )
    lines.append("")
    lines.append("\033[0;90mjump: agimon telepathy <sid8>   |   agimon telepathy jump <N>\033[0m")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print(format_feed(fetch_events(20)))
        return 0
    cmd = argv[0]
    if cmd == "jump" and len(argv) >= 2:
        events = fetch_events(20)
        try:
            idx = int(argv[1]) - 1
            ev = events[idx]
        except (ValueError, IndexError):
            print("\033[0;31mbad index\033[0m")
            return 2
        print(jump(ev.sid8))
        return 0
    # assume it's a sid8
    if re.fullmatch(r"[a-f0-9]{8}", cmd):
        print(jump(cmd))
        return 0
    # prefix match against feed
    events = fetch_events(20)
    for ev in events:
        if ev.sid8.startswith(cmd):
            print(jump(ev.sid8))
            return 0
    print(f"\033[0;31mno match for: {cmd}\033[0m")
    return 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
