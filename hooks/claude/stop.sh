#!/bin/sh
# Function: Launch project-memdir from Claude Code Stop hooks.
# Purpose: Reuse the Codex hook dispatcher for Claude Code project and plugin hooks.

case "$0" in
    */*) script_dir=${0%/*} ;;
    *) script_dir=. ;;
esac

SCRIPT_DIR=$(CDPATH= cd "$script_dir" 2>/dev/null && pwd -P)
PLUGIN_ROOT=$(CDPATH= cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd -P)

exec sh "$PLUGIN_ROOT/hooks/automation/memdir_hook.sh" stop
