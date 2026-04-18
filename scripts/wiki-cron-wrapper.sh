#!/bin/bash
# wiki-cron-wrapper.sh - Cron wrapper with Telegram notification on failure
# Usage: ./wiki-cron-wrapper.sh '<command>' '<job_name>'

set -euo pipefail

COMMAND="${1:-}"
JOB_NAME="${2:-}"

if [[ -z "$COMMAND" || -z "$JOB_NAME" ]]; then
    echo "Usage: $0 '<command>' '<job_name>'" >&2
    exit 1
fi

LOCK_FILE="/tmp/wiki-cron-wrapper.lock"
TIMEOUT_SECONDS=300
LOG_FILE="/vol1/1000/wiki/logs/cron.log"
CONTAINER="wiki-brain"

# Telegram config
TELEGRAM_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-702399976}"

# Load from .env if not set
if [[ -z "$TELEGRAM_TOKEN" && -f /vol1/1000/wiki/.env ]]; then
    TELEGRAM_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' /vol1/1000/wiki/.env | cut -d= -f2)
fi

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [${JOB_NAME}] SKIPPED - locked" >> "$LOG_FILE"
    exit 0
fi
trap 'flock -u 200' EXIT

send_telegram() {
    local exit_code="$1"
    if [[ -z "$TELEGRAM_TOKEN" ]]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [${JOB_NAME}] TELEGRAM_BOT_TOKEN not set" >> "$LOG_FILE"
        return 1
    fi
    local msg="⚠️ Wiki-Brain [${JOB_NAME}] FAILED (exit ${exit_code})"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d text="$msg" >> "$LOG_FILE" 2>&1
}

echo "$(date '+%Y-%m-%d %H:%M:%S') [${JOB_NAME}] STARTED" >> "$LOG_FILE"

set +e
timeout "$TIMEOUT_SECONDS" docker exec "$CONTAINER" python3 "/app/scripts/${COMMAND}" 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}
set -e

echo "$(date '+%Y-%m-%d %H:%M:%S') [${JOB_NAME}] FINISHED exit=${EXIT_CODE}" >> "$LOG_FILE"

if [[ "$EXIT_CODE" -ne 0 ]]; then
    send_telegram "$EXIT_CODE"
fi

exit "$EXIT_CODE"
