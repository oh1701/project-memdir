#!/bin/sh
# Function: Launch the memdir CLI on POSIX systems.
# Purpose: Try common Python launchers for manual project-memdir commands.

case "$0" in
    */*) script_dir=${0%/*} ;;
    *) script_dir=. ;;
esac

SCRIPT_DIR=$(CDPATH= cd "$script_dir" 2>/dev/null && pwd -P)
CLI_SCRIPT="$SCRIPT_DIR/memdir_cli.py"

run_launcher() {
    launcher=$1
    shift
    if ! command -v "$launcher" >/dev/null 2>&1; then
        return 127
    fi
    PYTHONUTF8=1 "$launcher" "$CLI_SCRIPT" "$@"
}

run_launcher python3 "$@"
status=$?
if [ "$status" -eq 0 ]; then
    exit 0
fi
if [ "$status" -ne 127 ]; then
    exit "$status"
fi

run_launcher python "$@"
status=$?
if [ "$status" -eq 0 ]; then
    exit 0
fi
if [ "$status" -ne 127 ]; then
    exit "$status"
fi

echo "[memdir_cli] failed: Python 3.11+ launcher not found; install python3 or python on PATH." >&2
exit 1
