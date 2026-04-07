"""Process monitoring — delegates to Rust core via IPC for speed."""
from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

CORE_BIN = Path.home() / ".local" / "bin" / "agimon-core"


@dataclass
class ProcessInfo:
    pid: int = 0
    tty: str = ""
    cpu_percent: float = 0.0
    mem_mb: float = 0.0
    started: str = ""
    status: str = "running"
    command: str = ""
    session_id: str = ""
    label: str = ""
    category: str = ""


def _call_core(subcommand: str) -> dict | None:
    """Call agimon-core binary and parse JSON output."""
    if not CORE_BIN.exists():
        return None
    try:
        result = subprocess.run(
            [str(CORE_BIN), subcommand],
            capture_output=True, text=True, timeout=5,
        )
        return json.loads(result.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_all_dev_processes() -> list[ProcessInfo]:
    """Get all dev processes via Rust core (100x faster than ps aux)."""
    data = _call_core("ipc")
    if data is None:
        return []
    procs = []
    for p in data.get("procs", []):
        procs.append(ProcessInfo(
            pid=p.get("pid", 0),
            cpu_percent=p.get("cpu", 0.0),
            mem_mb=p.get("mem", 0),
            status=p.get("s", "idle"),
            label=p.get("label", ""),
            category=p.get("cat", ""),
        ))
    return procs


def get_claude_processes() -> list[ProcessInfo]:
    """Get only Claude Code processes."""
    return [p for p in get_all_dev_processes() if p.category == "claude"]


def get_system_summary() -> dict:
    """System metrics via Rust core."""
    data = _call_core("ipc")
    if data is None:
        return {
            "total": 0, "active": 0, "idle": 0,
            "total_cpu": 0.0, "total_mem_mb": 0.0,
            "processes": [], "all_processes": [], "by_category": {},
        }

    all_procs = []
    by_category: dict[str, list[ProcessInfo]] = {}
    for p in data.get("procs", []):
        pi = ProcessInfo(
            pid=p.get("pid", 0),
            cpu_percent=p.get("cpu", 0.0),
            mem_mb=p.get("mem", 0),
            status=p.get("s", "idle"),
            label=p.get("label", ""),
            category=p.get("cat", ""),
        )
        all_procs.append(pi)
        by_category.setdefault(pi.category, []).append(pi)

    claude_procs = [p for p in all_procs if p.category == "claude"]
    return {
        "total": len(claude_procs),
        "active": data.get("active", 0),
        "idle": data.get("idle", 0),
        "total_cpu": round(data.get("cpu", 0.0), 1),
        "total_mem_mb": round(data.get("mem_mb", 0), 1),
        "processes": claude_procs,
        "all_processes": all_procs,
        "by_category": by_category,
    }
