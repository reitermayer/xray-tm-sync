#!/usr/bin/env bash
# Chapter 1 of the demo: the Xray tests in Jira project UIP.
# First without AI (xr only), then the same with Claude Code.
#
# Usage, from anywhere:   docs/demo/chapter1.sh
# Each step: press Enter to type the command, press Enter again to run it.
#
# Options (environment variables):
#   XRAY_PROJECT=UIP   Jira project with the Xray tests
#   TYPE_DELAY=0.03    seconds per typed character

set -e
cd "$(dirname "$0")/../.."

PROJECT="${XRAY_PROJECT:-UIP}"
TYPE_DELAY="${TYPE_DELAY:-0.03}"

if [ ! -f .venv/bin/activate ]; then
  echo "No .venv in $(pwd). Run 'uv sync' first." >&2
  exit 1
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code ('claude') is not on the PATH." >&2
  exit 1
fi

show_prompt() {
  local venv="${VIRTUAL_ENV_PROMPT:-}"
  venv="${venv#(}"; venv="${venv%% }"; venv="${venv%)}"
  if [ -n "$venv" ]; then printf '(%s) ' "$venv"; fi
  printf '\033[1;32m%s\033[0m:\033[1;34m%s\033[0m$ ' "$(whoami)@$(hostname -s)" "${PWD/#$HOME/~}"
}

type_out() {
  local text="$1" i
  for ((i = 0; i < ${#text}; i++)); do
    printf '%s' "${text:i:1}"
    sleep "$TYPE_DELAY"
  done
}

# Waits for Enter with echo on. Hidden input (read -s) would make
# macOS Terminal show its password key icon.
step() {
  local cmd="$1"
  show_prompt
  read -r _                    # Enter: start typing
  printf '\033[1A\033[2K\r'     # back to the prompt line
  show_prompt
  type_out "$cmd"
  read -r _                    # Enter: run it (the Enter ends the line)
  eval "$cmd"
}

clear

# 1. The environment, so xr is the version in this folder
step ". .venv/bin/activate"

# 2. Without AI: the command a script or pipeline runs
step "xr tests list --project $PROJECT --output table --limit 0"

# 3. With Claude Code: one plain sentence, the same command underneath
step "claude \"Show me the Xray tests in Jira project $PROJECT.\" --verbose"
