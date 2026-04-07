#!/bin/bash
# SSH tunnel count for SketchyBar
TUNNELS=$(lsof -i -nP -sTCP:LISTEN 2>/dev/null | grep '^ssh' | awk '{print $9}' | sort -u | wc -l | tr -d ' ')
sketchybar --set tunnels label="ssh:${TUNNELS}"
