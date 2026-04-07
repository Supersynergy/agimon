"""Monitor running processes — Claude Code + all dev tools."""
from __future__ import annotations
import subprocess
from dataclasses import dataclass

# Known process labels for identification
KNOWN_PROCESSES: dict[str, str] = {
    "claude": "Claude Code",
    "httpx": "HTTPX Scanner",
    "ollama": "Ollama LLM",
    "python": "Python",
    "node": "Node.js",
    "dolt": "Dolt DB",
    "gitea": "Gitea Git",
    "docker": "Docker",
    "colima": "Colima VM",
    "uvicorn": "Uvicorn ASGI",
    "gunicorn": "Gunicorn WSGI",
    "flask": "Flask Dev",
    "fastapi": "FastAPI",
    "windsurf": "Windsurf IDE",
    "code": "VS Code",
    "sublime": "Sublime Text",
    "postgres": "PostgreSQL",
    "redis": "Redis",
    "nginx": "Nginx",
    "ssh": "SSH Tunnel",
    "lima": "Lima VM",
}


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
    category: str = ""  # "claude", "dev-tool", "service", "ide"


def _categorize(cmd: str) -> tuple[str, str]:
    """Return (label, category) for a process command."""
    cmd_lower = cmd.lower()
    if "claude" in cmd_lower:
        if "chrome-native" in cmd_lower or "helper" in cmd_lower:
            return "Claude Helper", "helper"
        return "Claude Code", "claude"
    for key, label in KNOWN_PROCESSES.items():
        if key in cmd_lower:
            if key in ("docker", "colima", "lima"):
                return label, "infra"
            if key in ("windsurf", "code", "sublime"):
                return label, "ide"
            if key in ("node", "python"):
                return label, "runtime"
            return label, "dev-tool"
    return "", "other"


def get_claude_processes() -> list[ProcessInfo]:
    """Get all running Claude CLI processes."""
    return [p for p in get_all_dev_processes() if p.category == "claude"]


def get_all_dev_processes() -> list[ProcessInfo]:
    """Get all developer-relevant processes."""
    procs: list[ProcessInfo] = []
    try:
        out = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return procs

    for line in out.splitlines()[1:]:  # skip header
        cols = line.split(None, 10)
        if len(cols) < 11:
            continue
        cmd = cols[10]
        label, category = _categorize(cmd)
        if not label:
            continue
        if category == "helper":
            continue

        cpu = float(cols[2])
        procs.append(ProcessInfo(
            pid=int(cols[1]),
            cpu_percent=cpu,
            mem_mb=round(int(cols[5]) / 1024, 1) if cols[5].isdigit() else 0,
            tty=cols[6],
            started=cols[8],
            status="active" if cpu > 1.0 else "idle",
            command=cmd.strip()[:80],
            label=label,
            category=category,
        ))

    procs.sort(key=lambda p: -p.cpu_percent)
    return procs


def get_system_summary() -> dict:
    """Get system metrics for Claude and all dev processes."""
    all_procs = get_all_dev_processes()
    claude_procs = [p for p in all_procs if p.category == "claude"]
    active = sum(1 for p in claude_procs if p.status == "active")
    idle = sum(1 for p in claude_procs if p.status == "idle")
    total_cpu = sum(p.cpu_percent for p in all_procs)
    total_mem = sum(p.mem_mb for p in all_procs)

    # Group by category
    by_category: dict[str, list[ProcessInfo]] = {}
    for p in all_procs:
        by_category.setdefault(p.category, []).append(p)

    return {
        "total": len(claude_procs),
        "active": active,
        "idle": idle,
        "total_cpu": round(total_cpu, 1),
        "total_mem_mb": round(total_mem, 1),
        "processes": claude_procs,
        "all_processes": all_procs,
        "by_category": by_category,
    }
