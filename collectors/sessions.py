"""Parse Claude Code session and subagent JSONL files."""
from __future__ import annotations
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"


@dataclass
class ToolUse:
    tool_name: str = ""
    timestamp: str = ""
    status: str = ""


@dataclass
class SubAgent:
    agent_id: str = ""
    model: str = ""
    prompt_preview: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    tool_uses: list[ToolUse] = field(default_factory=list)
    message_count: int = 0


@dataclass
class Session:
    session_id: str = ""
    project: str = ""
    permission_mode: str = ""
    start_time: str = ""
    last_activity: str = ""
    message_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    subagents: list[SubAgent] = field(default_factory=list)
    tools_used: dict[str, int] = field(default_factory=dict)
    first_user_message: str = ""


def _parse_session_jsonl(path: Path) -> Session:
    """Parse a single session JSONL file."""
    session = Session(
        session_id=path.stem,
        project=path.parent.name,
    )

    try:
        lines = path.read_text().splitlines()
    except OSError:
        return session

    timestamps = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        rec_type = rec.get("type", "")

        if rec_type == "permission-mode":
            session.permission_mode = rec.get("permissionMode", "")

        elif rec_type == "file-history-snapshot":
            ts = rec.get("snapshot", {}).get("timestamp", "")
            if ts:
                timestamps.append(ts)

        elif rec_type == "user":
            session.message_count += 1
            msg = rec.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, str) and not session.first_user_message:
                session.first_user_message = content[:120]
            elif isinstance(content, list) and not session.first_user_message:
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        session.first_user_message = block.get("text", "")[:120]
                        break

        elif rec_type == "assistant":
            session.message_count += 1
            msg = rec.get("message", {})
            usage = msg.get("usage", {})
            session.input_tokens += usage.get("input_tokens", 0)
            session.input_tokens += usage.get("cache_read_input_tokens", 0)
            session.input_tokens += usage.get("cache_creation_input_tokens", 0)
            session.output_tokens += usage.get("output_tokens", 0)

            # Extract tool uses
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool = block.get("name", "unknown")
                        session.tools_used[tool] = session.tools_used.get(tool, 0) + 1

    if timestamps:
        timestamps.sort()
        session.start_time = timestamps[0]
        session.last_activity = timestamps[-1]

    # Parse subagents
    subagent_dir = path.parent / path.stem / "subagents"
    if subagent_dir.exists():
        for sa_file in subagent_dir.glob("*.jsonl"):
            sa = _parse_subagent(sa_file)
            session.subagents.append(sa)

    return session


def _parse_subagent(path: Path) -> SubAgent:
    """Parse a subagent JSONL file."""
    sa = SubAgent(agent_id=path.stem)

    try:
        lines = path.read_text().splitlines()
    except OSError:
        return sa

    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        sa.message_count += 1
        agent_id = rec.get("agentId", "")
        if agent_id:
            sa.agent_id = agent_id

        msg = rec.get("message", {})
        if isinstance(msg, dict):
            role = msg.get("role", rec.get("type", ""))
            usage = msg.get("usage", {})
            sa.input_tokens += usage.get("input_tokens", 0)
            sa.input_tokens += usage.get("cache_read_input_tokens", 0)
            sa.input_tokens += usage.get("cache_creation_input_tokens", 0)
            sa.output_tokens += usage.get("output_tokens", 0)

            model = msg.get("model", "")
            if model:
                sa.model = model

            if role == "user" and not sa.prompt_preview:
                content = msg.get("content", "")
                if isinstance(content, str):
                    sa.prompt_preview = content[:100]
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            sa.prompt_preview = block.get("text", "")[:100]
                            break

            # Tool uses from assistant
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        sa.tool_uses.append(ToolUse(
                            tool_name=block.get("name", "unknown")
                        ))

    return sa


def load_recent_sessions(limit: int = 30) -> list[Session]:
    """Load most recent sessions across all projects."""
    sessions = []

    if not PROJECTS_DIR.exists():
        return sessions

    jsonl_files = []
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.glob("*.jsonl"):
            jsonl_files.append(f)

    # Sort by modification time, newest first
    jsonl_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    for f in jsonl_files[:limit]:
        session = _parse_session_jsonl(f)
        sessions.append(session)

    return sessions


def get_active_session_ids() -> set[str]:
    """Return session IDs that appear to be currently active (recent mtime)."""
    active = set()
    if not PROJECTS_DIR.exists():
        return active
    cutoff = datetime.utcnow().timestamp() - 300  # last 5 minutes
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.glob("*.jsonl"):
            if f.stat().st_mtime > cutoff:
                active.add(f.stem)
    return active
