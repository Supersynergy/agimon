"""Claude Self-Orchestrator — monitors, optimizes, and delegates across instances.

Core capabilities:
1. Read all Ghostty terminals + Claude sessions
2. Send commands to terminals via AppleScript
3. Track results in Qdrant for learning
4. Auto-delegate to cheaper models (MLX/local) when possible
5. Generate optimized prompts from success patterns
6. Self-monitoring loop with context-mode integration
"""
from __future__ import annotations
import json
import subprocess
import time
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from collectors.ghostty import get_all_terminals_flat, focus_terminal
from collectors.processes import get_system_summary
from collectors.sessions import load_recent_sessions, get_active_session_ids
from collectors.network import get_network_summary
from collectors.qdrant_store import index_session, search_sessions

# ── Model routing ────────────────────────────────────────────────────

MODEL_TIERS = {
    "opus": {
        "use_for": ["architecture", "complex-debug", "multi-file-refactor", "security-review"],
        "cost_per_1k": 0.075,
    },
    "sonnet": {
        "use_for": ["coding", "analysis", "moderate-tasks", "tool-use"],
        "cost_per_1k": 0.015,
    },
    "haiku": {
        "use_for": ["simple-search", "formatting", "grep", "file-listing", "summarization"],
        "cost_per_1k": 0.001,
    },
    "local_mlx": {
        "use_for": ["research-draft", "brainstorm", "translation", "data-processing"],
        "cost_per_1k": 0.0,
        "endpoint": "http://localhost:11434/v1",
    },
}


@dataclass
class TaskResult:
    task_id: str = ""
    task_type: str = ""
    model_used: str = ""
    prompt: str = ""
    success: bool = False
    tokens_used: int = 0
    duration_s: float = 0
    quality_score: float = 0  # 0-1, from self-eval or user feedback
    timestamp: str = ""
    output_preview: str = ""


def classify_task(prompt: str) -> str:
    """Classify task complexity to route to optimal model tier."""
    prompt_lower = prompt.lower()

    # Opus-level
    opus_signals = [
        "architect", "security", "refactor entire", "redesign",
        "complex bug", "race condition", "memory leak", "performance",
    ]
    if any(s in prompt_lower for s in opus_signals):
        return "opus"

    # Haiku-level
    haiku_signals = [
        "list files", "find", "grep", "search for", "format",
        "rename", "simple", "typo", "readme", "summarize",
        "translate", "convert",
    ]
    if any(s in prompt_lower for s in haiku_signals):
        return "haiku"

    # Local MLX
    local_signals = [
        "research", "brainstorm", "ideas", "draft", "explore options",
        "what are", "compare", "pros and cons",
    ]
    if any(s in prompt_lower for s in local_signals):
        return "local_mlx"

    return "sonnet"  # default


# ── Terminal Command Execution ───────────────────────────────────────

def send_to_terminal(window_index: int, tab_index: int,
                     command: str, terminal_index: int = 1) -> bool:
    """Send a command to a specific Ghostty terminal via AppleScript."""
    # Escape for AppleScript
    safe_cmd = command.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "Ghostty"
        set t to terminal {terminal_index} of tab {tab_index} of window {window_index}
        perform action "write_to_terminal:\\"{safe_cmd}\\n\\"" on t
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def send_to_idle_terminal(command: str) -> dict | None:
    """Find an idle terminal and send a command to it."""
    terms = get_all_terminals_flat()
    for t in terms:
        title = t.get("terminal_title", "").lower()
        # Skip terminals running claude
        if "claude" in title:
            continue
        # Prefer terminals showing just a shell prompt
        if any(shell in title for shell in ["zsh", "bash", "fish", "$", "~"]):
            wi = t.get("window_index", 0)
            ti = t.get("tab_index", 0)
            if send_to_terminal(wi, ti, command):
                return t
    return None


# ── Qdrant Learning Loop ────────────────────────────────────────────

def track_result(result: TaskResult) -> bool:
    """Index a task result into Qdrant for learning."""
    text = (
        f"task:{result.task_type} model:{result.model_used} "
        f"success:{result.success} quality:{result.quality_score:.1f} "
        f"prompt:{result.prompt[:200]}"
    )
    metadata = {
        "task_type": result.task_type,
        "model_used": result.model_used,
        "success": result.success,
        "tokens_used": result.tokens_used,
        "duration_s": result.duration_s,
        "quality_score": result.quality_score,
        "output_preview": result.output_preview[:500],
    }
    return index_session(result.task_id, text, metadata)


def find_best_approach(task_description: str, limit: int = 5) -> list[dict]:
    """Search Qdrant for similar past tasks to learn from."""
    results = search_sessions(task_description, limit=limit)
    # Filter to successful results with high quality
    return [
        r for r in results
        if r.get("success", False) and r.get("quality_score", 0) > 0.7
    ]


def generate_optimized_prompt(task: str, past_results: list[dict]) -> str:
    """Generate an optimized prompt based on past successes."""
    if not past_results:
        return task

    best = past_results[0]
    model_hint = best.get("model_used", "sonnet")
    past_approach = best.get("output_preview", "")[:200]

    return (
        f"{task}\n\n"
        f"[Optimization hint: Similar task succeeded with {model_hint}. "
        f"Prior approach: {past_approach}]"
    )


# ── System Snapshot ──────────────────────────────────────────────────

def take_snapshot() -> dict:
    """Complete system snapshot for monitoring."""
    sys_info = get_system_summary()
    sessions = load_recent_sessions(10)
    active_ids = get_active_session_ids()
    net = get_network_summary()
    terms = get_all_terminals_flat()

    active_sessions = []
    for s in sessions:
        if s.session_id in active_ids:
            active_sessions.append({
                "id": s.session_id[:12],
                "task": s.first_user_message[:80],
                "agents": len(s.subagents),
                "tools": list(s.tools_used.keys())[:5],
                "tokens": s.input_tokens + s.output_tokens,
            })

    claude_terminals = []
    for t in terms:
        if "claude" in t.get("terminal_title", "").lower():
            claude_terminals.append({
                "window": t.get("window_index"),
                "tab": t.get("tab_index"),
                "title": t.get("terminal_title", "")[:40],
                "cwd": t.get("working_dir", ""),
            })

    return {
        "timestamp": datetime.now(tz=None).isoformat(),
        "processes": {
            "total": sys_info["total"],
            "active": sys_info["active"],
            "idle": sys_info["idle"],
            "cpu": sys_info["total_cpu"],
            "mem_mb": sys_info["total_mem_mb"],
        },
        "active_sessions": active_sessions,
        "claude_terminals": claude_terminals,
        "terminal_count": len(terms),
        "network": {
            "tunnels": net["total_tunnels"],
            "listeners": net["total_listeners"],
            "external": net["total_external"],
        },
    }


# ── Smart Delegation ────────────────────────────────────────────────

def delegate_task(task: str, force_model: str | None = None) -> dict:
    """
    Intelligently delegate a task:
    1. Classify complexity
    2. Check Qdrant for similar past successes
    3. Generate optimized prompt
    4. Route to best model
    5. Track result
    """
    # 1. Classify
    tier = force_model or classify_task(task)

    # 2. Learn from past
    past = find_best_approach(task)
    if past:
        # If past success used cheaper model, downgrade
        past_model = past[0].get("model_used", tier)
        if past_model in ("haiku", "local_mlx") and tier in ("sonnet", "opus"):
            tier = past_model

    # 3. Optimize prompt
    optimized = generate_optimized_prompt(task, past)

    # 4. Build command
    if tier == "local_mlx":
        cmd = (
            f'curl -s http://localhost:11434/api/generate '
            f'-d \'{{"model":"llama3","prompt":"{task[:200]}"}}\''
        )
    else:
        model_flag = {
            "opus": "--model claude-opus-4-6",
            "sonnet": "--model claude-sonnet-4-6",
            "haiku": "--model claude-haiku-4-5-20251001",
        }.get(tier, "")
        cmd = f'claude {model_flag} --print "{optimized[:300]}"'

    return {
        "task": task,
        "tier": tier,
        "optimized_prompt": optimized[:500],
        "command": cmd,
        "past_successes": len(past),
        "estimated_cost": MODEL_TIERS.get(tier, {}).get("cost_per_1k", 0),
    }


# ── CLI Interface ───────────────────────────────────────────────────

def main() -> None:
    import sys
    if len(sys.argv) < 2:
        print("Usage: orchestrator.py <command> [args]")
        print("  snapshot    — Full system snapshot")
        print("  delegate    — Smart task delegation")
        print("  classify    — Classify task complexity")
        print("  history     — Search past task results")
        print("  send        — Send command to terminal")
        return

    cmd = sys.argv[1]

    if cmd == "snapshot":
        snap = take_snapshot()
        print(json.dumps(snap, indent=2, ensure_ascii=False))

    elif cmd == "classify" and len(sys.argv) > 2:
        task = " ".join(sys.argv[2:])
        tier = classify_task(task)
        cost = MODEL_TIERS.get(tier, {}).get("cost_per_1k", 0)
        print(f"Task: {task[:60]}")
        print(f"Tier: {tier}")
        print(f"Cost/1K tokens: ${cost}")
        print(f"Use for: {MODEL_TIERS.get(tier, {}).get('use_for', [])}")

    elif cmd == "delegate" and len(sys.argv) > 2:
        task = " ".join(sys.argv[2:])
        result = delegate_task(task)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif cmd == "history" and len(sys.argv) > 2:
        query = " ".join(sys.argv[2:])
        results = find_best_approach(query, limit=5)
        for r in results:
            print(f"  [{r.get('score', 0):.3f}] {r.get('model_used', '?')} "
                  f"{'OK' if r.get('success') else 'FAIL'} — {r.get('text', '')[:60]}")

    elif cmd == "send" and len(sys.argv) > 4:
        wi, ti = int(sys.argv[2]), int(sys.argv[3])
        command = " ".join(sys.argv[4:])
        ok = send_to_terminal(wi, ti, command)
        print(f"{'OK' if ok else 'FAILED'}: W{wi} T{ti} <- {command[:60]}")

    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
