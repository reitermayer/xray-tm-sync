---
name: xray-tm-demo
description: Demo toolkit for the xray-tm-sync skill (Xray Cloud ↔ UiPath Test Manager) in a registered sandbox pair of Jira project and TM project. Seeds 8 stock-trading Xray tests and an Xray test set, runs a mock (robot-free) manual TM test execution of a TM test set, sends the mock results to Xray as a [MOCK] Xray test execution, and resets or reseeds the sandbox (deletes TM test cases, TM test sets, TM test executions, and Xray data in the sandbox only). Use for "reset the demo", "seed the stock trading tests", "mock-run the TM test set", "send the mock results to Xray", "prepare the Xray TM demo". The sync itself (pull, plan, apply, testsets)→xray-tm-sync. Never for production projects.
---

# Xray ⇄ TM demo toolkit

This skill needs **xray-tm-sync** installed next to it, because it imports `../xray-tm-sync/scripts/xray_tm_sync.py`. The sync itself (Xray → TM) always goes through xray-tm-sync. This skill adds only what a demo needs and production must never do.

`D=~/.claude/skills/xray-tm-demo/scripts/xray_tm_demo.py` and `S=~/.claude/skills/xray-tm-sync/scripts/xray_tm_sync.py`. Both share `./.xray-tm-sync/` and both write a command trail to `.xray-tm-sync/evidence/`.

Use the same words as xray-tm-sync: **Xray test** ≠ **TM test case**, **Xray test set** ≠ **TM test set**, **Xray test execution** ≠ **TM test execution**. Never use those words on their own.

## Sandbox guard

Every command needs `--xray-project <JIRA_PROJECT_KEY> --tm-project <TM_PROJECT_KEY>`, and that exact pair must be registered:

```bash
$D sandbox list
$D sandbox add --xray-project DEMO --tm-project DEMO     # only after the user confirms both are sandboxes
```

Register a pair only when the user has said in this conversation that both projects are demo sandboxes. `reset --reseed` deletes **every** Xray test in that Jira project. Never suggest a project that looks like production.

## Demo runbook (chapters of docs/DEMO.md)

| Chapter | The user says | You run |
|---|---|---|
| 0 Reset | "Reset the demo and seed the stock trading tests" | `$D reset … --reseed` (dry run, show it) → `--yes` |
| 1 Xray | "Show me the Xray tests in DEMO" | the seed output, or `$S pull --jql 'project = DEMO AND issuetype = Test'` and list summaries |
| 2 Sync | "Retrieve all Xray tests from Jira project DEMO and sync them to TM project DEMO" | xray-tm-sync: `pull` → `plan` → confirm → `apply --yes` → `testsets` → confirm → `testsets --yes` |
| 3a Mock run | "Run the stock trading TM test set as a mock run, all passed" | `$D execute … --tm-test-set <KEY>` (dry run) → `--yes` |
| 4 Results | "Send the results back to Xray" | `$D report … --execution-id <id>` (dry run) → `--yes` |
| 5 Proof | "Run the sync again" / "give me the commands" | `$S plan` (all `unchanged`), then print the exact `$S`/`$D` lines and the `evidence` trail files |

After each chapter, offer one plain `uip` command the user can run themselves as independent proof. For example: `uip tm testcases list --project-key DEMO --output json`, or `uip tm testsets list --project-key DEMO --output json`.

## Commands

- `$D seed …`: dry run, then `--yes`. Creates the 8 Xray tests and the Xray test set. It deletes nothing.
- `$D execute … --tm-test-set <TM_TEST_SET_KEY> [--outcomes file.json]`: dry run, then `--yes`.
  - Runs a manual TM test execution with no robot. Results come from the outcomes file and default to Passed.
  - Every TM step log is tagged `[MOCK]`. Note the `execution_id`.
- `$D report … --execution-id <TM_EXECUTION_ID> [--xray-test-plan <XRAY_KEY>]`: dry run, then `--yes`.
  - Creates a `[MOCK] …` Xray test execution in the sandbox Jira project.
  - Optional: `--test-environment TM-mock`. It works only if that Xray test environment already exists in the Jira project, because the Xray import rejects unknown ones.
- `$D reset … [--reseed]`: the dry run lists everything it would delete. Show it in full and get an explicit yes.
  - **Without `--reseed`:** Xray-origin TM test cases and TM test sets of the sandbox Jira project, their TM test executions, and `[MOCK]` Xray test executions.
  - **With `--reseed`:** additionally **every** Xray test, Xray test set and Xray test execution in the sandbox Jira project, which are then replaced by the seed. The seed is created first and the old data deleted second, so nothing is lost if the seed fails.

Details: [references/demo-operations.md](references/demo-operations.md).

## Rules

1. Dry run first for every command. Show it, get the user's yes, then `--yes`. Show reset lists in full.
2. Only registered sandbox pairs. Never edit `sandboxes.json` by hand to get past the guard.
3. Mock results are always labelled `[MOCK]` (plus `TM-mock` if that test environment exists), and only go into the sandbox Jira project. Never present them as real test outcomes.
4. The one TM REST call (deleting a TM test execution during `reset`) is documented and limited to that. Don't add others.
5. Exit codes: 0 ok; 1 finished with failed items (listed in `failed`); 2 stopped before doing anything.
