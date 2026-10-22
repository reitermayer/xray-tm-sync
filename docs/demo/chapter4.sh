#!/usr/bin/env bash
# Chapter 4 of the demo: send the results of the mock TM test execution back to Xray.
# First without AI: the Xray test executions before, the production report refusing
# the [MOCK] results, the demo report as a dry run and with --yes, the list after.
# Then Claude Code in evidence mode: it changes nothing, shows the dry run and
# lists the xr commands - the same ones the script just ran.
#
# Needs chapter 3 first: a mock TM test execution recorded in .xray-tm-sync.
# Off camera, the script takes the id of the newest one for this sandbox pair.
#
# Usage, from anywhere:   docs/demo/chapter4.sh
# Each step: the command is typed by itself, press Enter to run it.
# Self-running:           AUTO=1 docs/demo/chapter4.sh   (no Enter needed)
#
# Options (environment variables):
#   XRAY_PROJECT=UIP   sandbox Jira project
#   TM_PROJECT=XCA     sandbox TM project
#   EXECUTION_ID=...   use this TM test execution instead of the newest mock one
#   TYPE_DELAY=0.03    seconds per typed character
#   AUTO=1             also run each command by itself, PAUSE seconds after typing
#   PAUSE=2            seconds to pause before typing (and, with AUTO=1, before running)

set -e
cd "$(dirname "$0")/../.."

PROJECT="${XRAY_PROJECT:-UIP}"
TM="${TM_PROJECT:-XCA}"
TYPE_DELAY="${TYPE_DELAY:-0.03}"
AUTO="${AUTO:-0}"
PAUSE="${PAUSE:-2}"

if [ ! -f .venv/bin/activate ]; then
  echo "No .venv in $(pwd). Run 'uv sync' first." >&2
  exit 1
fi
for tool in claude python3; do
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
# to run it. AUTO=1 also runs it by itself. A second argument "may-fail" keeps
# the script going when the command is expected to refuse.
# Enter is read with echo on: hidden input (read -s) would make macOS Terminal
# show its password key icon.
step() {
  local cmd="$1" mode="${2:-}"
  show_prompt
  sleep "$PAUSE"
  type_out "$cmd"
  if [ "$AUTO" = "1" ]; then
    sleep "$PAUSE"
    echo
  else
    read -r _                    # Enter: run it (the Enter ends the line)
  fi
  if [ "$mode" = "may-fail" ]; then
    eval "$cmd" || true
  else
    eval "$cmd"
  fi
}

# 0. Off camera: the newest mock TM test execution of this sandbox pair
EXEC_ID="${EXECUTION_ID:-$(XP="$PROJECT" TP="$TM" python3 -c '
import glob, json, os
best = None
for f in glob.glob(".xray-tm-sync/execution-*.json"):
    d = json.load(open(f))
    if d.get("mock") and d.get("tm_project") == os.environ["TP"] and d.get("xray_project") == os.environ["XP"]:
        if best is None or os.path.getmtime(f) > best[0]:
            best = (os.path.getmtime(f), d["execution_id"])
print(best[1] if best else "")
')}"
if [ -z "$EXEC_ID" ]; then
  echo "No mock TM test execution for $PROJECT + $TM in .xray-tm-sync. Run chapter 3 first." >&2
  exit 1
fi

clear

# 1. The environment, so xr is the version in this folder
step ". .venv/bin/activate"

# 2. Before: the Xray test executions in the Jira project
step "xr testexecutions list --project $PROJECT --output table --output-filter \"[].{Key: Key, Summary: Summary, Runs: Runs}\""

# 3. Safety: the production report refuses [MOCK] results
step "xr sync report --tm-project $TM --xray-project $PROJECT --execution-id $EXEC_ID" may-fail

# 4. Without AI: the demo report as a dry run - nothing is sent yet
step "xr demo report --xray-project $PROJECT --tm-project $TM --execution-id $EXEC_ID"

# 5. Without AI: send it - a new [MOCK] Xray test execution in the Jira project
step "xr demo report --xray-project $PROJECT --tm-project $TM --execution-id $EXEC_ID --yes"

# 6. After: the new [MOCK] Xray test execution is in the list
step "xr testexecutions list --project $PROJECT --output table --output-filter \"[].{Key: Key, Summary: Summary, Runs: Runs}\""

# 7. Evidence mode with Claude Code: it changes nothing, it shows the dry run
#    and the xr commands to run this without it
step "claude \"Send the results of the last mock TM test execution in TM project $TM back to Xray, Jira project $PROJECT. Do not change anything: show me the dry run, and give me the xr commands to run this without you.\" --verbose"
