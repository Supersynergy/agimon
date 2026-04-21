"""Smart process registry: launchd agents + known dev services.

Provides scan(), categorization, LLM-classify fallback, and control helpers.
"""
from __future__ import annotations
import fnmatch
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from collectors.cache import ttl_cache
from collectors.llm import ollama_quick, minimax_chat

# ── Category rules ────────────────────────────────────────────────────────────

CATEGORY_RULES: list[tuple[str, str]] = [
    (r"^com\.supersynergy\.autopilot\.", "Autopilot"),
    (r"^ai\.zeroclaw\.", "ZeroClaw"),
    (r"^com\.zeroclaw\.", "ZeroClaw"),
    (r"^com\.zeroultimate\.bot", "Agent"),
    (r"^com\.superjarvis\.", "SuperJarvis"),
    (r"^(com\.supersynergy\.synapsed|de\.supersynergy\.telepathy|com\.supersynergy\.agimon)", "Daemon"),
    (r"^(com\.gitea|com\.ollama|io\.qdrant|com\.docker|colima)", "DevService"),
    (r"^ai\.hermes\.|^com\.aquawhisper\.|^com\.anthropic\.", "Agent"),
    (r"^(.*ShipIt|.*updater|.*Updater|gpuautofix|skhd|openssh|ssh-agent|com\.apple\.)", "System"),
]

CATEGORY_ICONS: dict[str, str] = {
    "Autopilot":  "🤖",
    "ZeroClaw":   "🕷",
    "SuperJarvis":"🧠",
    "Daemon":     "📡",
    "DevService": "🛠",
    "Agent":      "💬",
    "System":     "🪪",
    "Other":      "❓",
}

# Known non-launchd processes
KNOWN_PROCS: list[tuple[str, str, str, str, str]] = [
    # (id, display, category, pgrep-pattern, start-cmd)
    ("ollama",  "🦙 Ollama",         "DevService", "ollama serve",
     "ollama serve >/tmp/ollama.log 2>&1 &"),
    ("qdrant",  "🧭 Qdrant",         "DevService", "qdrant",
     "qdrant --config-path ~/.config/qdrant/config.yaml >/tmp/qdrant.log 2>&1 &"),
    ("gitea",   "🌐 Gitea",          "DevService", "gitea web",
     "gitea web >/tmp/gitea.log 2>&1 &"),
    ("colima",  "🐳 Docker/Colima",  "DevService", "colima daemon",
     "colima start"),
]

# Known TCP ports for health checks
KNOWN_PORTS: dict[str, int] = {
    "ollama":  11434,
    "qdrant":  6333,
    "gitea":   3000,
    "com.supersynergy.synapsed": 0,  # socket-based
}

NOTES_FILE = Path("~/.agimon/procs-notes.json").expanduser()


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ProcEntry:
    label: str
    display: str
    category: str
    kind: str               # "launchd" | "process" | "docker"
    plist: Optional[str]
    pid: Optional[int]
    running: bool
    enabled: bool
    pattern: str
    uptime_sec: Optional[int] = None
    cpu: Optional[float] = None
    mem_mb: Optional[float] = None
    port: Optional[int] = None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _launchctl_list() -> list[dict]:
    """Parse launchctl list once; cached 3s."""
    try:
        r = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=5
        )
        entries = []
        for line in r.stdout.splitlines()[1:]:  # skip header
            parts = line.split("\t", 2)
            if len(parts) == 3:
                pid_s, status_s, label = parts
                entries.append({
                    "pid": int(pid_s) if pid_s != "-" else None,
                    "label": label.strip(),
                })
        return entries
    except Exception:
        return []


_launchctl_list_cached = ttl_cache(3.0)(_launchctl_list)


def _pgrep(pattern: str) -> Optional[int]:
    try:
        r = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=3
        )
        pids = [int(x) for x in r.stdout.split() if x.strip().isdigit()]
        return pids[0] if pids else None
    except Exception:
        return None


def _pid_uptime(pid: int) -> Optional[int]:
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etime="],
            capture_output=True, text=True, timeout=3
        )
        t = r.stdout.strip()
        if not t or t == "-":
            return None
        # etime format: [[DD-]HH:]MM:SS
        parts = t.replace("-", ":").split(":")
        parts = [int(x) for x in reversed(parts)]
        secs = parts[0] if len(parts) > 0 else 0
        secs += parts[1] * 60 if len(parts) > 1 else 0
        secs += parts[2] * 3600 if len(parts) > 2 else 0
        secs += parts[3] * 86400 if len(parts) > 3 else 0
        return secs
    except Exception:
        return None


def _pid_stats(pid: int) -> tuple[Optional[float], Optional[float]]:
    """Return (cpu%, mem_mb) via ps."""
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pcpu=,rss="],
            capture_output=True, text=True, timeout=3
        )
        parts = r.stdout.strip().split()
        cpu = float(parts[0]) if parts else None
        mem = float(parts[1]) / 1024.0 if len(parts) > 1 else None
        return cpu, mem
    except Exception:
        return None, None


def _plist_path_for_label(label: str) -> Optional[str]:
    p = Path(f"~/Library/LaunchAgents/{label}.plist").expanduser()
    if p.exists():
        return str(p)
    p2 = Path(f"/Library/LaunchDaemons/{label}.plist")
    if p2.exists():
        return str(p2)
    return None


def _categorize(label: str) -> str:
    for pattern, cat in CATEGORY_RULES:
        if re.search(pattern, label):
            return cat
    return "Other"


def _load_notes() -> dict[str, str]:
    try:
        return json.loads(NOTES_FILE.read_text())
    except Exception:
        return {}


def _save_notes(notes: dict[str, str]) -> None:
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    NOTES_FILE.write_text(json.dumps(notes, indent=2))


# ── LLM classify ─────────────────────────────────────────────────────────────

@ttl_cache(3600.0)
def classify_with_llm(label: str) -> str:
    categories = "Autopilot | ZeroClaw | SuperJarvis | DevService | Daemon | Agent | System | Other"
    prompt = (
        f"Which single category fits this macOS launchd label best?\n"
        f"Label: {label}\n"
        f"Categories: {categories}\n"
        f"Reply with exactly one category name, nothing else."
    )
    result = ollama_quick(prompt, model="gemma3:270m", timeout=6).strip()
    valid = {"Autopilot", "ZeroClaw", "SuperJarvis", "DevService", "Daemon", "Agent", "System", "Other"}
    for word in result.split():
        if word in valid:
            return word
    return "Other"


# ── Core scan ─────────────────────────────────────────────────────────────────

def scan() -> list[ProcEntry]:
    """Full registry: launchd agents + known process-kind entries."""
    lc_entries = _launchctl_list_cached()
    lc_by_label = {e["label"]: e for e in lc_entries}

    results: list[ProcEntry] = []

    # 1. launchd entries
    for lc in lc_entries:
        label = lc["label"]
        if not label or label.startswith("0x"):
            continue  # anonymous
        pid = lc["pid"]
        plist = _plist_path_for_label(label)
        cat = _categorize(label)

        cpu, mem = (None, None)
        uptime = None
        if pid:
            uptime = _pid_uptime(pid)
            cpu, mem = _pid_stats(pid)

        # Human display: last segment of label, title-case
        parts = label.split(".")
        display_raw = parts[-1].replace("-", " ").replace("_", " ").title()
        icon = CATEGORY_ICONS.get(cat, "•")

        results.append(ProcEntry(
            label=label,
            display=f"{icon} {display_raw}",
            category=cat,
            kind="launchd",
            plist=plist,
            pid=pid,
            running=pid is not None,
            enabled=plist is not None,
            pattern=label,
            uptime_sec=uptime,
            cpu=cpu,
            mem_mb=mem,
            port=KNOWN_PORTS.get(label),
        ))

    # 2. known non-launchd processes (deduplicate if already captured via launchd)
    launchd_labels = {e.label for e in results}
    for proc_id, display, cat, pattern, _start in KNOWN_PROCS:
        if proc_id in launchd_labels:
            continue
        pid = _pgrep(pattern)
        cpu, mem, uptime = None, None, None
        if pid:
            uptime = _pid_uptime(pid)
            cpu, mem = _pid_stats(pid)
        results.append(ProcEntry(
            label=proc_id,
            display=display,
            category=cat,
            kind="process",
            plist=None,
            pid=pid,
            running=pid is not None,
            enabled=False,
            pattern=pattern,
            uptime_sec=uptime,
            cpu=cpu,
            mem_mb=mem,
            port=KNOWN_PORTS.get(proc_id),
        ))

    return results


# ── Grouping ──────────────────────────────────────────────────────────────────

def by_category(entries: list[ProcEntry]) -> dict[str, list[ProcEntry]]:
    out: dict[str, list[ProcEntry]] = {}
    for e in entries:
        out.setdefault(e.category, []).append(e)
    return out


# ── Control ───────────────────────────────────────────────────────────────────

def _find_entry(label_or_pattern: str, entries: Optional[list[ProcEntry]] = None) -> Optional[ProcEntry]:
    if entries is None:
        entries = scan()
    for e in entries:
        if e.label == label_or_pattern:
            return e
    return None


def start(label_or_pattern: str) -> str:
    e = _find_entry(label_or_pattern)
    if e is None:
        return f"not found: {label_or_pattern}"
    if e.running:
        return f"already running (pid {e.pid})"
    if e.kind == "launchd" and e.plist:
        r = subprocess.run(["launchctl", "load", "-w", e.plist],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or r.stderr.strip() or "started"
    # process kind
    for proc_id, _disp, _cat, _pat, start_cmd in KNOWN_PROCS:
        if proc_id == e.label:
            subprocess.Popen(["bash", "-c", start_cmd])
            return f"started: {start_cmd}"
    return "no start method"


def stop(label_or_pattern: str) -> str:
    e = _find_entry(label_or_pattern)
    if e is None:
        return f"not found: {label_or_pattern}"
    if not e.running and not e.pid:
        return "not running"
    if e.kind == "launchd" and e.plist:
        subprocess.run(["launchctl", "unload", e.plist],
                       capture_output=True, timeout=10)
    if e.pid:
        subprocess.run(["kill", str(e.pid)], capture_output=True, timeout=5)
        return f"stopped pid {e.pid}"
    if e.pattern:
        subprocess.run(["pkill", "-f", e.pattern], capture_output=True, timeout=5)
        return "stopped"
    return "no stop method"


def toggle(label: str) -> str:
    e = _find_entry(label)
    if e is None:
        return f"not found: {label}"
    return stop(label) if e.running else start(label)


def enable(label: str) -> str:
    e = _find_entry(label)
    if e is None:
        return f"not found: {label}"
    if e.plist:
        r = subprocess.run(["launchctl", "load", "-w", e.plist],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "enabled"
    return "no plist — cannot enable"


def disable(label: str) -> str:
    e = _find_entry(label)
    if e is None:
        return f"not found: {label}"
    if e.plist:
        r = subprocess.run(["launchctl", "unload", "-w", e.plist],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "disabled"
    return "no plist — cannot disable"


def batch(action: str, category_or_glob: str) -> list[tuple[str, str]]:
    """Apply action to all entries matching category name or shell glob on label."""
    entries = scan()
    matched = [
        e for e in entries
        if e.category == category_or_glob
        or fnmatch.fnmatch(e.label, category_or_glob)
    ]
    results = []
    for e in matched:
        try:
            if action == "start":      r = start(e.label)
            elif action == "stop":     r = stop(e.label)
            elif action == "restart":  stop(e.label); time.sleep(0.5); r = start(e.label)
            elif action == "enable":   r = enable(e.label)
            elif action == "disable":  r = disable(e.label)
            elif action == "toggle":   r = toggle(e.label)
            else:                      r = f"unknown action: {action}"
        except Exception as ex:
            r = str(ex)
        results.append((e.label, r))
    return results


# ── Health check ──────────────────────────────────────────────────────────────

def _tcp_alive(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def heal() -> list[tuple[str, str]]:
    """Restart Daemon/DevService entries that have a PID but unresponsive TCP port."""
    entries = scan()
    results = []
    for e in entries:
        if e.category not in ("Daemon", "DevService"):
            continue
        if not e.running:
            continue
        if e.port and not _tcp_alive(e.port):
            r = stop(e.label)
            time.sleep(1.0)
            r2 = start(e.label)
            results.append((e.label, f"healed: stop={r} start={r2}"))
    return results


# ── MiniMax summary ───────────────────────────────────────────────────────────

def summary() -> str:
    entries = scan()
    cats = by_category(entries)
    lines = []
    for cat, elist in sorted(cats.items()):
        running = sum(1 for e in elist if e.running)
        lines.append(f"{cat}: {running}/{len(elist)} running")
    overview = "\n".join(lines)
    prompt = (
        f"Summarize this macOS process registry in one short paragraph "
        f"(max 80 words). Highlight health issues:\n\n{overview}"
    )
    result = minimax_chat(prompt, system="You are a concise system status assistant.", timeout=15)
    return result or overview


# ── Annotations ──────────────────────────────────────────────────────────────

def get_note(label: str) -> str:
    return _load_notes().get(label, "")


def set_note(label: str, note: str) -> None:
    notes = _load_notes()
    notes[label] = note
    _save_notes(notes)
