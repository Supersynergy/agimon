#!/bin/bash
# Claude Code process status for SketchyBar
ACTIVE=$(ps aux | grep -c '[c]laude.*--dangerously')
CPU=$(ps aux | grep '[c]laude.*--dangerously' | awk '{sum+=$3} END {printf "%.0f", sum}')
MEM=$(ps aux | grep '[c]laude.*--dangerously' | awk '{sum+=$6} END {printf "%.0f", sum/1024}')

if [ "$ACTIVE" -gt 0 ]; then
  sketchybar --set claude label="CC:${ACTIVE} CPU:${CPU}% ${MEM}MB" icon.color=0xffff9500
else
  sketchybar --set claude label="CC:idle" icon.color=0xff666666
fi
