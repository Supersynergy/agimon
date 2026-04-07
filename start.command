#!/bin/bash
# Double-click to start Claude Monitor menubar
cd "$(dirname "$0")"
source .venv/bin/activate
exec python3 menubar.py
