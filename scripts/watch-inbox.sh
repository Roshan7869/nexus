#!/bin/bash
# ~/nexus/scripts/watch-inbox.sh

INBOX="$HOME/nexus/inbox"
SCRIPT="$HOME/nexus/scripts/embed-single.py"
LOG="$HOME/nexus/logs/watcher.log"

mkdir -p "$INBOX" "$(dirname "$LOG")"

echo "[$(date -Is)] Watcher started on $INBOX" >> "$LOG"

inotifywait -m "$INBOX" -e close_write -e moved_to --format '%f' | while read FILE
do
    FILEPATH="$INBOX/$FILE"
    if [ -f "$FILEPATH" ]; then
        echo "[$(date -Is)] Indexing: $FILE" >> "$LOG"
        python3 "$SCRIPT" "$FILEPATH" >> "$LOG" 2>&1
        mv "$FILEPATH" "$INBOX/.done/$FILE" 2>/dev/null || true
    fi
done