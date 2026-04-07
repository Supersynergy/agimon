"""Parse Claude Code cost/usage data from multiple sources."""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field

COSTS_FILE = Path.home() / ".claude" / "metrics" / "costs.jsonl"
SESSION_META_DIR = Path.home() / ".claude" / "usage-data" / "session-meta"
USAGE_FACETS = Path.home() / ".claude" / "usage-data" / "facets"


@dataclass
class DayStats:
    date: str = ""
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    sessions: int = 0
    messages: int = 0


@dataclass
class SessionMeta:
    session_id: str = ""
    model: str = ""
    start_time: str = ""
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    messages: int = 0
    tools_used: list = field(default_factory=list)


def load_costs_by_day(days: int = 14) -> list[DayStats]:
    """Load cost data aggregated by day for the last N days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    by_day: dict[str, DayStats] = defaultdict(DayStats)

    if COSTS_FILE.exists():
        for line in COSTS_FILE.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("timestamp", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.replace(tzinfo=None) < cutoff:
                continue
            day_key = dt.strftime("%Y-%m-%d")
            ds = by_day[day_key]
            ds.date = day_key
            ds.cost += rec.get("estimated_cost_usd", 0)
            ds.input_tokens += rec.get("input_tokens", 0)
            ds.output_tokens += rec.get("output_tokens", 0)
            ds.total_tokens += rec.get("input_tokens", 0) + rec.get("output_tokens", 0)
            ds.messages += 1
            if rec.get("session_id", "default") != "default":
                ds.sessions += 1

    # Enrich from session-meta JSONs
    if SESSION_META_DIR.exists():
        session_ids_seen: set[str] = set()
        for meta_file in SESSION_META_DIR.iterdir():
            if not meta_file.suffix == ".json":
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            ts = meta.get("startTime", meta.get("timestamp", ""))
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.replace(tzinfo=None) < cutoff:
                continue
            day_key = dt.strftime("%Y-%m-%d")
            ds = by_day[day_key]
            ds.date = day_key
            sid = meta.get("sessionId", meta_file.stem)
            if sid not in session_ids_seen:
                session_ids_seen.add(sid)
                ds.sessions += 1
            ds.cost += meta.get("cost", meta.get("totalCost", 0))
            ds.input_tokens += meta.get("inputTokens", meta.get("input_tokens", 0))
            ds.output_tokens += meta.get("outputTokens", meta.get("output_tokens", 0))
            ds.total_tokens += (
                meta.get("inputTokens", meta.get("input_tokens", 0))
                + meta.get("outputTokens", meta.get("output_tokens", 0))
            )
            ds.messages += meta.get("messageCount", meta.get("messages", 0))

    result = sorted(by_day.values(), key=lambda d: d.date, reverse=True)
    return result


def load_session_metas() -> list[SessionMeta]:
    """Load individual session metadata."""
    metas = []
    if not SESSION_META_DIR.exists():
        return metas
    for meta_file in sorted(SESSION_META_DIR.iterdir(), reverse=True):
        if not meta_file.suffix == ".json":
            continue
        try:
            data = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        sm = SessionMeta(
            session_id=data.get("sessionId", meta_file.stem),
            model=data.get("model", "unknown"),
            start_time=data.get("startTime", data.get("timestamp", "")),
            cost=data.get("cost", data.get("totalCost", 0)),
            input_tokens=data.get("inputTokens", data.get("input_tokens", 0)),
            output_tokens=data.get("outputTokens", data.get("output_tokens", 0)),
            messages=data.get("messageCount", data.get("messages", 0)),
            tools_used=data.get("toolsUsed", []),
        )
        metas.append(sm)
    return metas[:50]


def total_summary(days: int = 14) -> dict:
    """Aggregate summary stats for the period."""
    stats = load_costs_by_day(days)
    return {
        "days": days,
        "total_cost": sum(d.cost for d in stats),
        "total_tokens": sum(d.total_tokens for d in stats),
        "total_sessions": sum(d.sessions for d in stats),
        "total_messages": sum(d.messages for d in stats),
    }
