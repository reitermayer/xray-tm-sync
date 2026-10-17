---
name: jre-xray-uipath
description: Xray Cloud (Jira) ↔ UiPath Test Manager via Xray GraphQL and the `uip tm` CLI. Intake of Xray tests as TM test cases (steps, precondition text, XR_* labels, origin marker in TM custom fields, re-sync with drift protection); Xray test sets mirrored as TM test sets; mock (robot-free) manual TM executions from an outcomes file; TM execution results reported back to Xray as an Xray test execution (Xray JSON import, mock results only into a sandbox Jira project). Every TM or Xray write is a dry run until --yes. Use for "import/sync Xray tests into Test Manager", "create TM test sets from Xray", "mock-run the Xray test set in TM", "send TM results to Xray", "Xray to UiPath". Xray Server/Data Center is not supported. General Test Manager work→uipath-test.
---

# Xray Cloud ↔ UiPath Test Manager

| Direction | Subcommands | Writes to |
|---|---|---|
| Xray → TM | `pull`, `plan`, `apply` (TM test cases); `testsets` (TM test sets) | TM |
| TM only | `execute --mock` (manual TM test execution, robot-free, results from a file) | TM |
| TM → Xray | `report` (Xray test execution with the results) | **Xray / Jira** |
| Cleanup | `reset` (demo reset: deletes what the skill created) | TM and Xray |

Only `reset` deletes: objects the skill created (origin markers / report marker), and with `--reseed`
everything Xray in a named sandbox Jira project, which is then re-seeded with demo Xray tests.

## Words — read this first

Xray and TM use the same words for different things. In everything you say or write, **never use these
words bare**: test, case, step, set, key, id, project, folder, precondition, requirement, label, run,
execution, type. Always say **Xray …** or **TM …**.

- An **Xray test** (`CALC-123`, hyphen) becomes a **TM test case** (`CALC:12`, colon).
- An **Xray test set** (`CALC-200`, a Jira issue) becomes a **TM test set** (`CALC:40`, executable).
- An **Xray test execution** (`CALC-300`, a Jira issue) is created from a **TM test execution** (a UUID).
- An **Xray repository folder** is *not* a TM folder. In TM, "folder" means an **Orchestrator folder**.
- An **Xray precondition** is a separate reusable Jira issue; the **TM precondition** is a text field.

Full table: [references/terminology.md](references/terminology.md). Field rules:
[references/field-mapping.md](references/field-mapping.md). Test sets, mock execution and results:
[references/execution-and-results.md](references/execution-and-results.md). Xray API:
[references/xray-graphql.md](references/xray-graphql.md).

## Auth

- **TM:** the user's `uip login` session (browser OAuth; CI: `uip login --client-id … --client-secret …`).
  The script only calls `uip tm … --output json`, with one documented exception: `uip tm` cannot delete
  TM test executions, so `reset` calls the public TM API v2 `DELETE …/testexecutions/{id}` with the token
  from `uip login refresh`. Never call TM REST for anything else.
  If `uip` is logged out, ask the user to run `! uip login` — never run an interactive login yourself.
- **Xray Cloud:** no OAuth exists. A Jira admin creates an API key (Jira → Apps → Xray → Settings → API
  Keys). The user exports `XRAY_CLIENT_ID` and `XRAY_CLIENT_SECRET`, optionally `XRAY_REGION`
  (`eu`, `us`, `au`; default global) and `XRAY_JIRA_BASE_URL` (e.g. `https://acme.atlassian.net`, for
  links). The script trades them for a 24 h token held in memory only. Never echo, log or write the
  secret. `report --yes` needs a key whose Jira user may create Xray test execution issues; intake only
  needs read access.

## Workflow

`S=~/.claude/skills/jre-xray-uipath/scripts/xray_uipath.py` (uv inline script; needs `uv` and `uip`).
All state is in `./.xray-uipath/` (override with `--workdir`). Every subcommand prints JSON.

### A. Intake: Xray tests → TM test cases

1. **Check**: `$S check --tm-project <TM_PROJECT_KEY>`. Report every entry in `problems`. Ask for the
   TM project key if missing; resolve names with `uip tm project list --filter <text> --output json`.
   Never create a TM project unasked.
2. **Pull**: `$S pull --jql '<JQL>'`. Ask for the Jira scope first (e.g.
   `project = CALC AND issuetype = Test`). Re-syncs of large Jira projects: add `--since <ISO date>`.
   Report the counts per Xray test type and every warning.
3. **Plan** (no TM writes): `$S plan --tm-project <KEY>`. Show `summary` and `by_action`:
   `create`, `update` (Xray changed, TM untouched → Xray wins; TM steps of Xray tests without Xray steps are
   TM-owned, e.g. generated with GenAI in TM, and are never touched), `unchanged`, `drift` (TM test case edited
   by hand → **skipped**), `conflict` (several TM test cases carry one Xray key → skipped), `unsupported`.
   Also show `tm_fields_to_create` and `orphans_not_in_pull` (reported, never deleted).
4. **Confirm** the plan with the user. For each drifted TM test case ask whether Xray may overwrite it;
   if yes, re-run `plan` with `--accept-drift <XRAY_KEY> …`.
5. **Apply**: `$S apply --yes` (optionally `--only <XRAY_KEY> …`). Plans older than one hour are refused;
   re-plan rather than passing `--force`.
6. **Report** `counts`, `skipped` and failed items. A failed item is safe to retry with the next
   `plan` + `apply` (its origin marker is written first).

### B. Xray test sets → TM test sets

Run after A. `$S testsets --tm-project <KEY>` is a dry run: per Xray test set it shows `create` /
`update` / `unchanged`, the TM case keys to `add` / `remove`, and `xray_tests_not_in_tm` (members outside
the pulled JQL). Confirm, then `$S testsets --tm-project <KEY> --yes` (`--only <XRAY_TEST_SET_KEY> …`).
Only Xray-origin members are removed; TM test cases a user added by hand stay.

### C. Mock TM execution (pipe test, robot-free)

Only when the user asks for a mock or demo run.
1. Write an outcomes file; default for every TM test case is Passed:
   `{"CALC-101": {"result": "Failed", "failed_step": 2, "comment": "button missing"}, "CALC-102": "Passed"}`
   (results: Passed, Failed, None, Restricted).
2. `$S execute --tm-project <KEY> --tm-test-set <TM_TEST_SET_KEY> --mock --outcomes <file>` — dry run,
   shows the planned outcome per TM test case. Confirm, then add `--yes`. It starts a manual TM test
   execution and records the results; every TM step log is tagged `[MOCK]`. Note the `execution_id`.

### D. TM results → Xray

`$S report --tm-project <KEY> --execution-id <TM_EXECUTION_ID> [--xray-test-plan <XRAY_KEY>]` builds
`xray-results-<id>.json` (dry run). Show `by_status`, `skipped` and the target `xray_project`, then add
`--yes` to import; report the new Xray test execution key.
- **Mock results** (from `execute --mock`, or any `[MOCK]` TM step log) are refused unless
  `--sandbox-project <JIRA_PROJECT_KEY>` is given **and** every Xray test belongs to that Jira project.
  They are labelled `[MOCK]` and use the Xray test environment `TM-mock`. Never suggest a production Jira
  project as sandbox; ask the user which Jira project is the sandbox.
- Real TM executions (testers or robots in TM) need no sandbox flag; confirm the Jira project with the user.

### E. Reset (between demo runs)

`$S reset --tm-project <KEY> [--xray-project <JIRA_PROJECT_KEY>] [--scope all|tm|xray]` is a dry run
listing, in deletion order:
1. Xray test executions created by `report` (found by the description marker; only `[MOCK]` ones unless
   `--include-real-results`),
2. TM test executions of Xray-origin TM test sets (plus mock runs recorded locally),
3. Xray-origin TM test sets,
4. Xray-origin TM test cases.
TM test cases without an origin marker and hand-made Xray test executions are never candidates.
Show the list, get an explicit yes, then add `--yes`. `--xray-project` limits everything to objects
from that Jira project. After a reset, `pull` → `plan` shows everything as `create` again.

**Demo reseed** — `$S reset --tm-project <KEY> --xray-project <SANDBOX> --reseed` additionally treats the
Jira project as a demo sandbox: it deletes **every** Xray test, Xray test set and Xray test execution in
it and creates the seed from `assets/seed-stock-trading.json` (8 Manual stock-trading Xray tests for
UiPath/PATH, Oracle/ORCL, Amazon/AMZN, steps as text in the description, plus the Xray test set "Stock
trading regression"). Seed first, delete second: if Jira rejects the seed, nothing in Xray is deleted.
Only ever use it on a Jira project the user explicitly called a sandbox; always show the dry run first.

## Rules

1. Every write is a dry run first; show it, get the user's confirmation, then re-run with `--yes`.
2. Delete only through `reset`, and only after the user approved its dry-run list. Xray tests and Xray test
   sets are deleted only by `reset --reseed` in a sandbox Jira project the user named. Never create or delete
   TM projects unasked.
3. Never send mock results outside a sandbox Jira project the user named. Never fabricate "real" results.
4. Follow the uipath-test conventions: `--output json` on every direct `uip` call, check the uip login
   first, stop and ask on any unexpected `uip` error; do not fall back to REST.
5. Do not hand-edit `.xray-uipath/*.json` to steer results; use the flags.
6. Exit codes: 0 ok, 1 finished with failed items, 2 stopped before doing anything (`error`/`problems`).

## Not supported

Requirements linking (Jira requirement → TM requirement), attachments, Xray test plans as TM objects,
robot (automated) executions started by this skill, Xray Server/DC. Say so plainly; don't improvise.
