#!/bin/bash
# Wrapper for launchd — logs startup state before handing off to Python
set -e
LOG="/Users/xvy/Library/Logs/vr-obsidian-start.log"

echo "=== $(date) ===" >> "$LOG"
echo "PID=$$  PPID=$PPID  USER=$USER" >> "$LOG"
echo "CWD=$(pwd)" >> "$LOG"
echo "PATH=$PATH" >> "$LOG"

# Test Desktop access
if ls /Users/xvy/Desktop/elektro-brain/_SW/jski7/vr-obsidian/serve.py >> "$LOG" 2>&1; then
    echo "Desktop: ACCESSIBLE" >> "$LOG"
else
    echo "Desktop: BLOCKED" >> "$LOG"
    exit 1
fi

echo "Starting serve.py..." >> "$LOG"
exec /opt/homebrew/bin/python3.12 -u \
    /Users/xvy/Desktop/elektro-brain/_SW/jski7/vr-obsidian/serve.py \
    --ngrok
