#!/bin/bash
# Auto-index Claude sessions into Qdrant after each session.
# Hook: PostToolUse or session-end
cd /Users/master/claude-monitor
.venv/bin/python3 -c "
from collectors.sessions import load_recent_sessions
from collectors.qdrant_store import index_session
import sys

for s in load_recent_sessions(3):
    if not s.first_user_message:
        continue
    text = f'{s.first_user_message} | tools: {list(s.tools_used.keys())} | {len(s.subagents)} agents'
    ok = index_session(s.session_id, text, {
        'project': s.project,
        'tools': list(s.tools_used.keys()),
        'subagent_count': len(s.subagents),
        'input_tokens': s.input_tokens,
        'output_tokens': s.output_tokens,
    })
    if ok:
        print(f'Indexed: {s.session_id[:12]}', file=sys.stderr)
" 2>/dev/null
