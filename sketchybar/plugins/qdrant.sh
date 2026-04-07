#!/bin/bash
# Qdrant stats for SketchyBar
COLS=$(curl -s http://localhost:6333/collections 2>/dev/null | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(len(d['result']['collections']))
except: print('?')
" 2>/dev/null)
sketchybar --set qdrant label="qd:${COLS:-?}col"
