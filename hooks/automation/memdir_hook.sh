#!/bin/sh
# Function: Launch the memdir Python hook dispatcher on POSIX systems.
# Purpose: Try common Python launchers without requiring hook manifest shell fallback operators.

case "$0" in
    */*) script_dir=${0%/*} ;;
    *) script_dir=. ;;
esac

SCRIPT_DIR=$(CDPATH= cd "$script_dir" 2>/dev/null && pwd -P)
HOOK_SCRIPT="$SCRIPT_DIR/memdir_hook.py"

run_launcher() {
    launcher=$1
    shift
    if ! command -v "$launcher" >/dev/null 2>&1; then
        return 127
    fi
    PYTHONUTF8=1 "$launcher" "$HOOK_SCRIPT" "$@"
}

run_launcher python3 "$@"
status=$?
if [ "$status" -eq 0 ]; then
    exit 0
fi
if [ "$1" = "stop" ] && [ "$status" -ne 127 ]; then
    exit "$status"
fi

run_launcher python "$@"
status=$?
if [ "$status" -eq 0 ]; then
    exit 0
fi
if [ "$1" = "stop" ] && [ "$status" -ne 127 ]; then
    exit "$status"
fi

echo "[memdir_hook] skipped: Python 3.11+ launcher failed; install python3 or python on PATH." >&2
case "$1" in
    session-start|user-prompt-submit)
        printf '%s\n' '{"continue":true,"suppressOutput":true}'
        ;;
esac
if [ "$1" = "stop" ]; then
    exit 1
fi
exit 0
