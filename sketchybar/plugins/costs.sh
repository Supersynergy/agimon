#!/bin/bash
# 14-day cost summary for SketchyBar
COSTS_FILE="$HOME/.claude/metrics/costs.jsonl"
if [ -f "$COSTS_FILE" ]; then
  CUTOFF=$(date -v-14d +%Y-%m-%d)
  TOTAL=$(awk -F'"estimated_cost_usd":' "/$CUTOFF|$(date +%Y-%m-)/" "$COSTS_FILE" \
    | awk -F'[,}]' '{sum+=$1} END {printf "%.2f", sum}' 2>/dev/null)
  sketchybar --set costs label="\$${TOTAL:-0}"
else
  sketchybar --set costs label="\$?"
fi
