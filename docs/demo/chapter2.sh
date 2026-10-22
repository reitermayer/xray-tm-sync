#!/usr/bin/env bash
# Chapter 2 of the demo: sync the Xray tests of Jira project UIP into TM project XCA.
# First without AI: TM is empty, xr sync copies the Xray tests, TM shows them.
# Then Claude Code in evidence mode: it changes nothing, runs the plan (nothing left
# to do) and lists the xr commands - the same ones the script just ran.
#
# Off camera, first: the sandbox reset (chapter 0), so the plan shows 8 creates.
# It runs without asking, then the recording part starts by itself. Skip it with RESET=0.
# The reset deletes only Xray-origin TM test cases and TM test sets (and their TM test
# executions) of this sandbox pair. The Xray tests stay, with the same keys as in
# chapter 1. RESEED=1 also replaces all Xray data in the sandbox Jira project with the
# 8 seed tests (new Xray keys - then record chapter 1 again).
#
# Usage, from anywhere:   docs/demo/chapter2.sh
# Each step: the command is typed by itself, press Enter to run it.
# Self-running:           AUTO=1 docs/demo/chapter2.sh   (no Enter needed)
#
# Options (environment variables):
#   XRAY_PROJECT=UIP   Jira project with the Xray tests
#   TM_PROJECT=XCA     TM project key
#   TYPE_DELAY=0.03    seconds per typed character
#   AUTO=1             also run each command by itself, PAUSE seconds after typing
#   PAUSE=2            seconds to pause before typing (and, with AUTO=1, before running)
#   RESET=0            skip the sandbox reset
#   RESEED=1           reset with --reseed (new Xray tests and keys)

set -e
cd "$(dirname "$0")/../.."

PROJECT="${XRAY_PROJECT:-UIP}"
TM="${TM_PROJECT:-XCA}"
TYPE_DELAY="${TYPE_DELAY:-0.03}"
AUTO="${AUTO:-0}"
PAUSE="${PAUSE:-2}"
RESET="${RESET:-1}"
RESEED="${RESEED:-0}"

# The TM test case list, readable on screen: two columns, no info log lines.
TM_LIST="uip tm testcases list --project-key $TM --output table --log-level warn --output-filter \"[].{Key: TestCaseKey, Name: Name}\""

if [ ! -f .venv/bin/activate ]; then
  echo "No .venv in $(pwd). Run 'uv sync' first." >&2
  exit 1
fi
for tool in claude uip; do
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

# Default: types the next command by itself after PAUSE seconds, then waits for
# Enter to run it. AUTO=1 also runs it by itself.
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

# 0. Off camera: reset the sandbox, no questions. xr demo itself refuses any
#    pair that is not registered as a sandbox, and then the script stops here.
if [ "$RESET" != "0" ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
  reset_cmd="xr demo reset --xray-project $PROJECT --tm-project $TM"
  if [ "$RESEED" = "1" ]; then reset_cmd="$reset_cmd --reseed"; fi
  echo "Sandbox reset: $reset_cmd --yes"
  if ! reset_out=$($reset_cmd --yes 2>&1); then
    echo "$reset_out" >&2
    exit 1
  fi
fi

clear

# 1. The environment, so xr is the version in this folder
step ". .venv/bin/activate"

# 2. Before: TM project has none of the Xray tests yet (key and name only)
step "$TM_LIST"

# 3. Without AI: is everything ready? (uip login, TM project, TM custom fields)
step "xr sync check --tm-project $TM --output table"

# 4. Without AI: read the Xray tests from Jira
step "xr sync pull --jql 'project = $PROJECT AND issuetype = Test'"

# 5. Without AI: the plan, a dry run - 8 creates, nothing written yet
step "xr sync plan --tm-project $TM"

# 6. Without AI: write exactly what the plan decided
step "xr sync apply --yes"

# 7. Without AI: mirror the Xray test set as a TM test set
step "xr sync testsets --tm-project $TM --yes"

# 8. After: the Xray tests are now TM test cases
step "$TM_LIST"

# 9. Evidence mode with Claude Code: it changes nothing, it shows the plan
#    (a dry run: nothing left to do) and the xr commands to run this without it
step "claude \"Sync the Xray tests from Jira project $PROJECT to TM project $TM. Do not change anything: show me the plan, and give me the xr commands to run this without you.\" --verbose"
