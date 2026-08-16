#!/bin/bash
# ~/nexus/scripts/watch-inbox.sh — inbox watcher with exit instrumentation (Week1 card 2.2)
# Every exit (crash, signal, supervisor stop) lands in logs/watcher.jsonl so the
# daily-restart cause is diagnosable instead of silent.

INBOX="$HOME/nexus/inbox"
SCRIPT="$HOME/nexus/scripts/embed-single.py"
LOG="$HOME/nexus/logs/watcher.log"
JSONL="$HOME/nexus/logs/watcher.jsonl"
PY="$HOME/nexus/venv/bin/python3"   # venv python — system python3 lacks embed deps

mkdir -p "$INBOX" "$INBOX/.done" "$(dirname "$LOG")"

trap 'echo "{\"ts\":\"$(date -Is)\",\"event\":\"exit\",\"code\":$?}" >> "$JSONL"' EXIT

log_event() {  # log_event <event> <detail>
    echo "{\"ts\":\"$(date -Is)\",\"event\":\"$1\",\"detail\":\"$2\"}" >> "$JSONL"
}

if ! command -v inotifywait >/dev/null 2>&1; then
    echo "FATAL: inotifywait missing (apt install inotify-tools)" >&2
    log_event "fatal" "inotifywait missing"
    exit 77
fi
if [ ! -x "$PY" ]; then
    echo "FATAL: venv python missing at $PY" >&2
    log_event "fatal" "venv python missing"
    exit 78
fi

echo "[$(date -Is)] Watcher started on $INBOX" >> "$LOG"
log_event "started" "$INBOX"

inotifywait -m "$INBOX" -e close_write -e moved_to --format '%f' | while read FILE
do
    FILEPATH="$INBOX/$FILE"
    if [ -f "$FILEPATH" ]; then
        echo "[$(date -Is)] Indexing: $FILE" >> "$LOG"
        "$PY" "$SCRIPT" "$FILEPATH" >> "$LOG" 2>&1
        rc=$?
        log_event "ingested" "file=$FILE rc=$rc"
        mv "$FILEPATH" "$INBOX/.done/$FILE" 2>/dev/null || true
    fi
done
