#!/bin/bash
# Claude Monitor — Menubar App (rumps)
cd "$(dirname "$0")"
source .venv/bin/activate
python3 menubar.py &
echo "Claude Monitor Menubar gestartet (PID: $!)"
