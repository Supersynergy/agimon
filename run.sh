#!/bin/bash
# Claude Monitor — Launch Script
cd "$(dirname "$0")"
source .venv/bin/activate
python3 app.py "$@"
