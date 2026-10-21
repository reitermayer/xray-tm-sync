---
name: xray-tm-sync
description: Sync Xray Cloud (Jira) tests into UiPath Test Manager with `xr sync` (the xr CLI) and the `uip tm` CLI. Intake of Xray tests as TM test cases (steps, precondition text, XR_* labels, origin marker in a TM custom field, re-sync with drift protection), Xray test sets mirrored as TM test sets, and results of real TM test executions sent back to Xray as an Xray test execution. Every write is a dry run until --yes, and every run leaves a command trail. Never deletes anything. Use for "retrieve the Xray tests from Jira project X and sync them to TM project Y", "import Xray tests into Test Manager", "create TM test sets from Xray", "send the TM results to Xray", "give me the commands to run this without you". Single Xray objects (list or create Xray tests, Xray test sets, Xray test executions)→xray. Demo seeding, mock runs and resets→xray-tm-demo. General Test Manager work→uipath-test. Xray Server/Data Center is not supported.
---

# Xray Cloud → UiPath Test Manager (production sync)

| Direction | Subcommands | Writes to |
|---|---|---|
| Xray → TM | `pull`, `plan`, `apply` (TM test cases); `testsets` (TM test sets) | TM |
| TM → Xray | `report`, for real TM test executions only | **Xray / Jira** (new Xray test execution) |
| Read only | `check`, `status` | — |

**Nothing is ever deleted**, neither in TM nor in Xray.

## Words: read this first

Xray and TM use the same words for different things. In everything you say or write, **never use these words on their own**: test, case, step, set, key, id, project, folder, precondition, requirement, label, run, execution, type. Always say **Xray …** or **TM …**.

- An **Xray test** (`CALC-123`, hyphen) becomes a **TM test case** (`CALC:12`, colon).
- An **Xray test set** (`CALC-200`, a Jira issue) becomes a **TM test set** (`CALC:40`, which can be executed).
- An **Xray test execution** (`CALC-300`, a Jira issue) is created from a **TM test execution** (a UUID).
- An **Xray repository folder** is *not* a TM folder. In TM, "folder" means an **Orchestrator folder**.
- An **Xray precondition** is a separate, reusable Jira issue. The **TM precondition** is a text field.

Further references:
- Full word table: [references/terminology.md](references/terminology.md)
- Field rules: [references/field-mapping.md](references/field-mapping.md)
- TM test sets, results and the command trail: [references/test-sets-and-results.md](references/test-sets-and-results.md)
- Xray API: [references/xray-graphql.md](references/xray-graphql.md)

## Why this design: tell the user when it helps

- **This skill has no code of its own.** It only tells you which `xr sync` commands to run. A person, a script or a CI pipeline runs exactly the same commands, with the same output and the same command trail, without any AI.
- **The agent picks the parameters; `xr` and `uip` do the work.** Xray test content goes from Xray to `xr` to `uip`; it never passes through you. Don't retype or rewrite Xray content yourself.
- **When asked for "the commands"**, print the exact `xr sync …` lines you ran (with `--yes` where the user approved) and point to the trail files in `evidence`.
- **Every write gets checked twice:** before, as a dry-run plan the user confirms, and after, with a read-back from TM and an idempotent re-run that shows `unchanged`.

## Auth

- **TM:** the user's `uip login` session: browser OAuth, or in CI `uip login --client-id env.X --client-secret env.Y -t <tenant>`.
  - `xr sync` calls only `uip tm … --output json`, never TM REST.
  - If `uip` is logged out, ask the user to run `! uip login --authority <host> --organization <org> --tenant <tenant>` (it opens the browser sign-in). Never run an interactive login yourself.
- **Xray Cloud:** Xray has no OAuth. A Jira admin creates an API key under Jira → Apps → Xray → API Keys.
  - The user stores it with `! xr login` (prompts for the client id and secret, verifies them, writes `~/.config/xray-tm-sync/xray.env` with mode 600). `xr login status` shows whether it works. Environment variables `XRAY_CLIENT_ID` / `XRAY_CLIENT_SECRET` override the file. `xr` refuses the file if others can read it.
  - Optional setting: `XRAY_REGION` (eu, us or au; the default is global).
  - `xr` exchanges the key for a 24 h token that it keeps in memory only.
  - **Never** ask the user to paste the secret into the chat, and never echo, log or write it.
  - `report --yes` needs a key whose Jira user may create issues. Intake needs read access only.
- **Origin marker field.** It defaults to `Xray Key`. Set `XRAY_ID_FIELD=XRAY_ID` to share markers with the Avalon Test Manager MCP server.

## Workflow

**Preflight:** `xr --version`. If `xr` is missing, the user installs it once (see the `xray` skill): `uv tool install git+https://github.com/reitermayer/xray-tm-sync`. It also needs `uip`.
- State lives in `./.xray-tm-sync/`; `xr sync --workdir <dir> …` overrides that.
- Every subcommand prints the `uip`-style envelope `{Result, Code, Data}`; `Data` includes an `evidence` entry that names the trail files. `--output-filter '<JMESPath>'` narrows `Data`, e.g. `--output-filter summary`.

### A. Xray tests → TM test cases

1. **Check.** Run `xr sync check --tm-project <TM_PROJECT_KEY>` and report every entry in `problems`.
   - Ask for the TM project key if it is missing. Resolve names with `uip tm project list --filter <text> --output json`.
   - Never create a TM project unasked.
2. **Pull.** Run `xr sync pull --jql '<JQL>'`.
   - Ask for the Jira scope first, e.g. `project = CALC AND issuetype = Test`.
   - For re-syncs of large Jira projects, add `--since <ISO date>`.
   - Report the counts per Xray test type and every warning.
3. **Plan** (no TM writes). Run `xr sync plan --tm-project <KEY>` and show `summary` and `by_action`:

   | Action | Meaning |
   |---|---|
   | `create` | New TM test case. |
   | `update` | Xray changed and TM is untouched, so Xray wins. TM steps of Xray tests without Xray steps are TM-owned (e.g. generated with GenAI in TM) and are never touched. |
   | `unchanged` | Nothing to do. |
   | `drift` | The TM test case was edited by hand. **Skipped.** |
   | `conflict` | Several TM test cases carry the same Xray key. Skipped. |
   | `unsupported` | Cannot be synced. |

   Also show `tm_fields_to_create` and `orphans_not_in_pull`. Orphans are reported, never deleted.
4. **Confirm** the plan with the user. For each drifted TM test case, ask whether Xray may overwrite it. If yes, re-run `plan` with `--accept-drift <XRAY_KEY> …`.
5. **Apply.** Run `xr sync apply --yes`, optionally with `--only <XRAY_KEY> …`.
   - Plans older than one hour are refused. Re-plan rather than passing `--force`.
6. **Report** `counts`, `skipped` and any failed items.
   - A failed item is safe to retry with the next `plan` + `apply`, because its origin marker is written first.
   - Offer a re-run of `plan` as proof that everything is now `unchanged`.

### B. Xray test sets → TM test sets

Run this after A.
1. `xr sync testsets --tm-project <KEY>` is a dry run. Per Xray test set it shows:
   - the action: `create`, `update` or `unchanged`
   - the TM case keys to `add` and to `remove`
   - `xray_tests_not_in_tm`: members outside the pulled JQL
2. Confirm with the user, then run `xr sync testsets --tm-project <KEY> --yes`, optionally with `--only <XRAY_TEST_SET_KEY> …`.

Only Xray-origin members are removed; TM test cases added by hand stay.

### C. TM results → Xray

`xr sync report --tm-project <KEY> --execution-id <TM_EXECUTION_ID>` builds `xray-results-<id>.json` (dry run).
- Optional: `[--xray-test-plan <XRAY_KEY>] [--test-environment <NAME> …] [--xray-project <JIRA_PROJECT_KEY>]`.
- Show `by_status`, `skipped` and the target `xray_project`, and confirm the Jira project with the user.
- Then add `--yes` and report the new Xray test execution key.
- Mock results are refused. If the user wants a mock or demo run, that is the `xray-tm-demo` skill.

### D. Running without the agent

When the user asks for automation (CI, cron, "without you"), give them the exact lines. For example:

```bash
xr sync pull --jql 'project = CALC AND issuetype = Test' && xr sync plan --tm-project CALC && xr sync apply --yes
xr sync testsets --tm-project CALC --yes
```

Explain the safety. `apply` only writes what `plan` decided. Drifted TM test cases stay skipped unless they are named with `--accept-drift`. Exit code 1 means some items failed, and they are retried on the next run.

## Rules

1. Every write is a dry run first. Show it, get the user's confirmation, then re-run with `--yes`. A CI run the user set up counts as standing confirmation for that pipeline only.
2. Never delete anything, never create or delete TM projects unasked, and never fabricate results.
3. Follow the uipath-test conventions (the same ones `xr` follows):
   - `--output json` on every direct `uip` call.
   - Check `uip login` first.
   - Stop and ask on any unexpected `uip` error.
   - Do not fall back to REST.
4. Do not hand-edit `.xray-tm-sync/*.json` to steer results; use the flags.
5. Exit codes: 0 ok; 1 finished with failed items; 2 stopped before doing anything (`Result: Failure` with a `Message`, or `problems` in `Data`).

## Not supported

- Requirements linking (Jira requirement → TM requirement)
- Attachments
- Xray test plans as TM objects
- Robot executions started by this skill
- Xray Server/DC

Say so plainly; don't improvise.
