#!/bin/bash
# External connection count for SketchyBar
EXT=$(lsof -i -nP -sTCP:ESTABLISHED 2>/dev/null | grep -v '127.0.0.1\|::1' | grep -c ESTABLISHED)
LISTEN=$(lsof -i -nP -sTCP:LISTEN 2>/dev/null | wc -l | tr -d ' ')
sketchybar --set network label="ext:${EXT} srv:${LISTEN}"
