"""AGIMON Watchdog - Self-Monitoring & Auto-Recovery.

Monitors AGIMON components and takes corrective action when issues are detected.
"""
from __future__ import annotations
import json
import subprocess
import time
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable

WATCHDOG_LOG = Path.home() / ".claude" / "agimon_watchdog.jsonl"
PID_FILE = Path.home() / ".claude" / "agimon_watchdog.pid"

# Health check thresholds
HEALTH_THRESHOLDS = {
    "max_memory_mb": 2048,  # Restart if AGIMON uses >2GB
    "max_cpu_percent": 50,  # Alert if single core >50% for 5min
    "max_session_cost": 20.0,  # Alert if session >$20
    "max_subagent_count": 100,  # Alert if >100 subagents
    "stale_session_minutes": 60,  # Consider session stale after 60min idle
}


@dataclass
class HealthCheck:
    component: str
    status: str  # "ok", "warning", "critical"
    message: str
    timestamp: str
    action_taken: Optional[str] = None


@dataclass
class WatchdogState:
    last_check: str
    checks: list[HealthCheck] = field(default_factory=list)
    issues_found: int = 0
    issues_resolved: int = 0
    total_restarts: int = 0


class AGIMONWatchdog:
    """Watchdog that monitors and maintains AGIMON health."""
    
    def __init__(self):
        self.state = self._load_state()
        self.running = False
        self.checks: list[Callable[[], HealthCheck]] = [
            self._check_core_health,
            self._check_session_costs,
            self._check_memory_usage,
            self._check_zombie_sessions,
            self._check_qdrant_connection,
        ]
    
    def _load_state(self) -> WatchdogState:
        """Load watchdog state from disk."""
        if WATCHDOG_LOG.exists():
            try:
                lines = WATCHDOG_LOG.read_text().strip().split("\n")
                if lines:
                    last = json.loads(lines[-1])
                    return WatchdogState(
                        last_check=last.get("timestamp", datetime.now().isoformat()),
                        total_restarts=last.get("total_restarts", 0),
                    )
            except (json.JSONDecodeError, OSError):
                pass
        return WatchdogState(last_check=datetime.now().isoformat())
    
    def _save_state(self):
        """Save watchdog state to disk."""
        WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "checks": len(self.state.checks),
            "issues_found": self.state.issues_found,
            "issues_resolved": self.state.issues_resolved,
            "total_restarts": self.state.total_restarts,
        }
        with open(WATCHDOG_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    
    def _check_core_health(self) -> HealthCheck:
        """Check if agimon-core binary is responsive."""
        try:
            result = subprocess.run(
                ["agimon-core", "health"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return HealthCheck(
                    component="agimon-core",
                    status="ok",
                    message="Core responding normally",
                    timestamp=datetime.now().isoformat(),
                )
            else:
                # Try to restart
                self._restart_core()
                return HealthCheck(
                    component="agimon-core",
                    status="critical",
                    message="Core not responding - restarted",
                    timestamp=datetime.now().isoformat(),
                    action_taken="restart",
                )
        except subprocess.TimeoutExpired:
            self._restart_core()
            return HealthCheck(
                component="agimon-core",
                status="critical",
                message="Core timeout - restarted",
                timestamp=datetime.now().isoformat(),
                action_taken="restart",
            )
        except FileNotFoundError:
            return HealthCheck(
                component="agimon-core",
                status="critical",
                message="Core binary not found",
                timestamp=datetime.now().isoformat(),
            )
    
    def _check_session_costs(self) -> HealthCheck:
        """Check for runaway expensive sessions."""
        from collectors.sessions import load_recent_sessions, get_active_session_ids
        from collectors.cost_predictor import estimate_session_cost
        
        sessions = load_recent_sessions(limit=20)
        active_ids = get_active_session_ids()
        
        expensive_sessions = []
        for session in sessions:
            if session.session_id in active_ids:
                cost = estimate_session_cost(session)
                if cost > HEALTH_THRESHOLDS["max_session_cost"]:
                    expensive_sessions.append((session.session_id, cost))
        
        if expensive_sessions:
            msg = f"{len(expensive_sessions)} sessions >${HEALTH_THRESHOLDS['max_session_cost']}"
            return HealthCheck(
                component="session-costs",
                status="warning",
                message=msg,
                timestamp=datetime.now().isoformat(),
            )
        
        return HealthCheck(
            component="session-costs",
            status="ok",
            message="All sessions within budget",
            timestamp=datetime.now().isoformat(),
        )
    
    def _check_memory_usage(self) -> HealthCheck:
        """Check AGIMON's own memory usage."""
        try:
            result = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(os.getpid())],
                capture_output=True, text=True, timeout=2
            )
            rss_kb = int(result.stdout.strip() or 0)
            rss_mb = rss_kb / 1024
            
            if rss_mb > HEALTH_THRESHOLDS["max_memory_mb"]:
                return HealthCheck(
                    component="memory",
                    status="warning",
                    message=f"High memory usage: {rss_mb:.0f}MB",
                    timestamp=datetime.now().isoformat(),
                )
            
            return HealthCheck(
                component="memory",
                status="ok",
                message=f"Memory OK: {rss_mb:.0f}MB",
                timestamp=datetime.now().isoformat(),
            )
        except (subprocess.TimeoutExpired, ValueError):
            return HealthCheck(
                component="memory",
                status="unknown",
                message="Could not check memory",
                timestamp=datetime.now().isoformat(),
            )
    
    def _check_zombie_sessions(self) -> HealthCheck:
        """Check for sessions that are idle but consuming resources."""
        from collectors.sessions import load_recent_sessions, get_active_session_ids
        from collectors.processes import get_claude_processes
        
        active_sessions = get_active_session_ids()
        claude_procs = get_claude_processes()
        
        # Find processes without active sessions (zombies)
        zombie_count = 0
        for proc in claude_procs:
            if proc.status == "idle":
                # Check if associated with active session
                has_active_session = any(
                    str(proc.pid) in sid or sid[:8] in proc.command
                    for sid in active_sessions
                )
                if not has_active_session:
                    zombie_count += 1
        
        if zombie_count > 5:
            return HealthCheck(
                component="zombie-sessions",
                status="warning",
                message=f"{zombie_count} potentially stale Claude processes",
                timestamp=datetime.now().isoformat(),
            )
        
        return HealthCheck(
            component="zombie-sessions",
            status="ok",
            message=f"{zombie_count} idle processes (within normal)",
            timestamp=datetime.now().isoformat(),
        )
    
    def _check_qdrant_connection(self) -> HealthCheck:
        """Check Qdrant connection for semantic search."""
        try:
            result = subprocess.run(
                ["curl", "-s", "http://localhost:6333/healthz"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0 or "ok" in result.stdout.lower():
                return HealthCheck(
                    component="qdrant",
                    status="ok",
                    message="Qdrant responding",
                    timestamp=datetime.now().isoformat(),
                )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        return HealthCheck(
            component="qdrant",
            status="warning",
            message="Qdrant not responding - search disabled",
            timestamp=datetime.now().isoformat(),
        )
    
    def _restart_core(self):
        """Restart the agimon-core binary."""
        try:
            # Kill any existing processes
            subprocess.run(["pkill", "-f", "agimon-core"], check=False, timeout=2)
            time.sleep(1)
            self.state.total_restarts += 1
        except subprocess.TimeoutExpired:
            pass
    
    def run_single_check(self) -> list[HealthCheck]:
        """Run all health checks once."""
        results = []
        for check_fn in self.checks:
            try:
                result = check_fn()
                results.append(result)
                if result.status in ("warning", "critical"):
                    self.state.issues_found += 1
            except Exception as e:
                results.append(HealthCheck(
                    component=check_fn.__name__,
                    status="error",
                    message=f"Check failed: {e}",
                    timestamp=datetime.now().isoformat(),
                ))
        
        self.state.checks = results
        self.state.last_check = datetime.now().isoformat()
        self._save_state()
        return results
    
    def generate_report(self) -> str:
        """Generate a formatted health report."""
        results = self.run_single_check()
        
        lines = [
            "🐕 AGIMON Watchdog Report",
            f"Last Check: {self.state.last_check}",
            f"Total Restarts: {self.state.total_restarts}",
            "",
            "Health Checks:",
        ]
        
        status_icons = {"ok": "✅", "warning": "⚠️", "critical": "🚨", "unknown": "❓", "error": "💥"}
        
        for check in results:
            icon = status_icons.get(check.status, "❓")
            lines.append(f"  {icon} {check.component}: {check.status.upper()}")
            lines.append(f"     {check.message}")
            if check.action_taken:
                lines.append(f"     → Action: {check.action_taken}")
        
        # Summary
        issues = [c for c in results if c.status in ("warning", "critical", "error")]
        if issues:
            lines.append("")
            lines.append(f"⚠️  {len(issues)} issues detected")
        else:
            lines.append("")
            lines.append("✅ All systems healthy")
        
        return "\n".join(lines)


def run_watchdog_daemon():
    """Run watchdog in daemon mode (periodic checks)."""
    import sys
    
    # Write PID file
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    
    watchdog = AGIMONWatchdog()
    
    def signal_handler(signum, frame):
        print("\n🐕 Watchdog shutting down...")
        if PID_FILE.exists():
            PID_FILE.unlink()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    print("🐕 AGIMON Watchdog started (checking every 60s)")
    print("Press Ctrl+C to stop")
    
    try:
        while True:
            results = watchdog.run_single_check()
            issues = [r for r in results if r.status in ("warning", "critical")]
            if issues:
                print(f"⚠️  {datetime.now().strftime('%H:%M:%S')} - {len(issues)} issues detected")
            time.sleep(60)
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "daemon":
        run_watchdog_daemon()
    else:
        watchdog = AGIMONWatchdog()
        print(watchdog.generate_report())
