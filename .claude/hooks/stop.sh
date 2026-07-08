#!/bin/sh
# Function: Run the project-local memdir Stop hook for Claude Code.
# Purpose: Keep visible project hooks under .claude while delegating to shared wrappers.

case "$0" in
    */*) script_dir=${0%/*} ;;
    *) script_dir=. ;;
esac

SCRIPT_DIR=$(CDPATH= cd "$script_dir" 2>/dev/null && pwd -P)
PROJECT_ROOT=$(CDPATH= cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd -P)

exec sh "$PROJECT_ROOT/hooks/claude/stop.sh"
