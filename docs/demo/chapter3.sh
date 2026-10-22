#!/usr/bin/env bash
# Chapter 3 of the demo: run the TM test set in TM project XCA as a mock run (3a).
# No robot and no app: every TM test case is logged as Passed and tagged [MOCK].
# First without AI: the TM test set, the dry run, then the run with --yes.
# Then Claude Code in evidence mode: it changes nothing, shows the dry run and
# lists the xr commands - the same ones the script just ran.
#
# Needs chapter 2 first: the TM test set mirrored from Xray must exist.
# Off camera, the script looks up the key of that TM test set by its name.
#
# Usage, from anywhere:   docs/demo/chapter3.sh
# Each step: the command is typed by itself, press Enter to run it.
# Self-running:           AUTO=1 docs/demo/chapter3.sh   (no Enter needed)
#
# Options (environment variables):
#   XRAY_PROJECT=UIP                        sandbox Jira project
#   TM_PROJECT=XCA                          sandbox TM project
#   TEST_SET_NAME="Stock trading regression" name of the TM test set
#   TYPE_DELAY=0.03                         seconds per typed character
#   AUTO=1                                  also run each command by itself, PAUSE seconds after typing
#   PAUSE=2                                 seconds to pause before typing (and, with AUTO=1, before running)

set -e
cd "$(dirname "$0")/../.."

PROJECT="${XRAY_PROJECT:-UIP}"
TM="${TM_PROJECT:-XCA}"
SET_NAME="${TEST_SET_NAME:-Stock trading regression}"
TYPE_DELAY="${TYPE_DELAY:-0.03}"
AUTO="${AUTO:-0}"
PAUSE="${PAUSE:-2}"

if [ ! -f .venv/bin/activate ]; then
  echo "No .venv in $(pwd). Run 'uv sync' first." >&2
  exit 1
fi
for tool in claude uip python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "'$tool' is not on the PATH." >&2
    exit 1
  fi
done

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

# Types the next command by itself after PAUSE seconds, then waits for Enter
# to run it. AUTO=1 also runs it by itself.
# Enter is read with echo on: hidden input (read -s) would make macOS Terminal
# show its password key icon.
step() {
  local cmd="$1"
  show_prompt
  sleep "$PAUSE"
  type_out "$cmd"
  if [ "$AUTO" = "1" ]; then
    sleep "$PAUSE"
    echo
  else
    read -r _                    # Enter: run it (the Enter ends the line)
  fi
  eval "$cmd"
}

# 0. Off camera: find the key of the TM test set by its name
echo "Looking up TM test set \"$SET_NAME\" in TM project $TM ..."
SET_KEY=$(uip tm testsets list --project-key "$TM" --output json --log-level warn \
  | SET_NAME="$SET_NAME" python3 -c '
import json, os, sys
data = json.load(sys.stdin).get("Data") or []
keys = [t["TestSetKey"] for t in data if t.get("Name") == os.environ["SET_NAME"]]
print(keys[-1] if keys else "")
')
if [ -z "$SET_KEY" ]; then
  echo "No TM test set named \"$SET_NAME\" in TM project $TM. Run chapter 2 first." >&2
  exit 1
fi

clear

# 1. The environment, so xr is the version in this folder
step ". .venv/bin/activate"

# 2. The TM test sets in the TM project (key and name only)
step "uip tm testsets list --project-key $TM --output table --log-level warn --output-filter \"[].{Key: TestSetKey, Name: Name}\""

# 3. Without AI: the mock run as a dry run - nothing is executed yet
step "xr demo execute --xray-project $PROJECT --tm-project $TM --tm-test-set $SET_KEY"

# 4. Without AI: the mock run - 8 TM test cases logged as Passed, tagged [MOCK]
step "xr demo execute --xray-project $PROJECT --tm-project $TM --tm-test-set $SET_KEY --yes"

# 5. Evidence mode with Claude Code: it changes nothing, it shows the dry run
#    and the xr commands to run this without it
step "claude \"Run the TM test set $SET_NAME in TM project $TM as a mock run, all passed. Do not change anything: show me the dry run, and give me the xr commands to run this without you.\" --verbose"
