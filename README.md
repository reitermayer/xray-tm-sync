# xray-tm-sync + xray-tm-demo

Two Claude Code skills for the UiPath coding agent that connect **Xray Cloud** (Jira) to **UiPath Test Manager** (TM).

| Skill | Purpose | Commands |
|---|---|---|
| **`xray-tm-sync`** | **Production sync**, ready to hand to UiPath customers. Never deletes TM test cases, TM test sets or anything in Xray. | `check`, `pull`, `plan`, `apply`, `testsets`, `report` (real TM results only), `status` |
| **`xray-tm-demo`** | **UiPath MVP demo.** Loads `xray-tm-sync` as a library and adds what production must never do. | `sandbox`, `seed`, `execute` (mock run), `report` (mock results), `reset [--reseed]` |

Every write is a dry run until you pass `--yes`.

Every demo command needs `--xray-project` and `--tm-project`, and that pair must first be registered as a sandbox with `sandbox add`. The registry lives in `~/.config/xray-tm-demo/sandboxes.json`.

## Status

Both skills are tested (`uv run pytest`) and ran live end to end against Xray Cloud and UiPath Test Manager; see [docs/REHEARSAL.md](docs/REHEARSAL.md). Open items and wishes for `uip` are in [docs/ROADMAP.md](docs/ROADMAP.md).

**Docs**
- [docs/INSTALL.md](docs/INSTALL.md): setup for students and customers, each with their own Xray and TM accounts.
- [docs/DEMO.md](docs/DEMO.md): the demo chapters, each with its prompt, commands and evidence.
- [docs/REHEARSAL.md](docs/REHEARSAL.md): real numbers from the live run.
- [docs/ROADMAP.md](docs/ROADMAP.md): open items.

**Related:** [reitermayer/trade-demo](https://github.com/reitermayer/trade-demo), the stock-trading web app used as the automation target in demo chapter 3b.

## Demo flow (target)

In this section, `DEMO` is your sandbox Jira project and TM project.

1. **Show Xray → sync → show TM.**
   - Show the Xray tests and their descriptions in Jira project DEMO.
   - Sync: `xray-tm-sync pull` → `plan` → `apply --yes` → `testsets --yes`.
   - Show the TM test cases and the TM test set in TM project DEMO.
2. **Mock run → results to Xray.**
   - `xray-tm-demo execute --yes` (all Passed by default).
   - Show "Passed" in TM.
   - `xray-tm-demo report --yes`, then show the new `[MOCK]` Xray Test Execution in Jira.
3. **Between rehearsals:** `xray-tm-demo reset --xray-project DEMO --tm-project DEMO --reseed --yes`.

## Design decisions

| Topic | Decision | Why |
|---|---|---|
| Skill split | Production `xray-tm-sync` and demo `xray-tm-demo` | Customers get a skill with no delete, seed or mock code paths. The demo can be destructive inside a registered sandbox only. |
| Xray API | GraphQL (`getExpandedTests`), Xray Cloud only | No Xray Cloud REST endpoint returns manual step definitions. One paged query also returns the steps, preconditions, test sets, repository folder and Jira fields. Xray Server/DC has no GraphQL. |
| TM API | `uip tm … --output json` only (production) | Reuses your `uip login` OAuth session (browser login, or client credentials in CI). The demo makes one documented exception: TM API v2 `DELETE …/testexecutions/{id}`, because `uip` cannot delete TM test executions. |
| Xray auth | `XRAY_CLIENT_ID` / `XRAY_CLIENT_SECRET` env vars → 24 h JWT, kept in memory only | Xray Cloud has no OAuth or user-delegated login. |
| Origin marker | TM custom field `Xray Key` (on TM test cases and TM test sets) plus `Xray Intake` (JSON with hashes) | Keeps TM names clean. Makes re-sync, drift detection and a safe demo reset possible. |
| Re-sync | Xray wins, but TM test cases edited by hand are reported as `drift` and skipped (`--accept-drift` overrides) | One-way sync that doesn't silently overwrite edits. |
| TM-owned steps | An Xray test without Xray steps leaves its TM steps alone, and those steps are left out of the drift hash | Steps are generated from the description with GenAI in TM. |
| Results to Xray | Xray JSON import using `testKey` from the origin marker, not JUnit | JUnit matches tests by name, so Xray would create duplicate Generic tests. |
| Mock results | Only in the demo skill: tagged `[MOCK]` (optional test environment `TM-mock` if it exists in Xray), sandbox Jira project only | Fake results would distort Xray coverage in a real Jira project. |
| Reseed | Seed first, delete second | If Jira rejects the new tests, nothing in Xray is deleted. |
| Naming | Words like test, step, key or folder are never used without an Xray/TM qualifier ([terminology](xray-tm-sync/references/terminology.md)) | Xray and TM use the same words for different things. For example, an Xray repository folder is not an Orchestrator folder. |

## `uip tm` quirks found and handled (uip 1.203, 17.10.2026)

- **Custom field values can be lost right after creation.** About 100 ms after a TM test case is created, TM provisions empty custom field rows asynchronously. These can overwrite a value written in that window, and `customfield value create` on an existing row returns Success but writes nothing. The skill uses `update`, waits, reads the value back and writes it again if needed. The Avalon Test Manager MCP server 3.4.1 found the same race.
- **No project-wide lookup of custom field values.** Markers are read one TM test case at a time and cached in a local index.
- **Step logs need `testcaselog start` first.** Without it, `testcaselog finish --step-logs` fails with HTTP 400.
- **Missing test case ids.** `testsets list-testcases` rows have no TM case id; the skill resolves them through `testcases list`.
- **No way to delete executions in the CLI.** `uip tm` cannot delete TM test executions, and deleting a TM test set leaves its executions behind.
- **Manual runs need no default folder.** `testsets run --execution-type manual` works without a robot or a default Orchestrator folder.

## Verified live (17.10.2026)

- **Before the split:** intake create, idempotent re-run, update, drift, test set mirroring, and the TM REST delete of a TM test execution.
- **After the split, as installed:** the full demo, chapters 0–4:
  - `reset --reseed` with the first live Xray `createTest`, `createTestSet` and `deleteTest`
  - sync of 8 Xray tests
  - TM test set mirroring
  - the mock run
  - the production `report` refusing the mock run
  - the `[MOCK]` Xray test execution UIP-17

  Details are in [docs/REHEARSAL.md](docs/REHEARSAL.md).
- **Not yet run live:** a production `report --yes` of a *real* TM test execution. It shares its import code with the demo report, which did run.

## Layout

```
xray-tm-sync/                       production skill
  SKILL.md
  references/                       terminology, field mapping, test sets & results & evidence, Xray GraphQL
  scripts/xray_tm_sync.py           check | pull | plan | apply | testsets | report | status
xray-tm-demo/                       demo skill (needs xray-tm-sync installed next to it)
  SKILL.md
  references/demo-operations.md     seed, mock run, mock report, reset
  assets/seed-stock-trading.json    8 demo Xray tests + 1 Xray test set
  scripts/xray_tm_demo.py           sandbox | seed | execute | report | reset
docs/                               INSTALL (students/customers), DEMO (chapters), REHEARSAL (live evidence)
tests/                              pytest + fake `uip` (in-memory TM) + recorded GraphQL pages
```

## Use

```bash
# Xray API key in ~/.config/xray-tm-sync/xray.env (chmod 600) or as env vars — see docs/INSTALL.md
S=~/.claude/skills/xray-tm-sync/scripts/xray_tm_sync.py
D=~/.claude/skills/xray-tm-demo/scripts/xray_tm_demo.py

# production
$S check    --tm-project DEMO
$S pull     --jql 'project = DEMO AND issuetype = Test'
$S plan     --tm-project DEMO && $S apply --yes
$S testsets --tm-project DEMO --yes
$S report   --tm-project DEMO --execution-id <uuid> --yes          # real TM results only

# demo (sandbox pair registered once)
$D sandbox add --xray-project DEMO --tm-project DEMO
$D execute --xray-project DEMO --tm-project DEMO --tm-test-set DEMO:21 --yes
$D report  --xray-project DEMO --tm-project DEMO --execution-id <uuid> --yes
$D reset   --xray-project DEMO --tm-project DEMO --reseed         # dry run; add --yes
```

## Develop

```bash
uv run pytest -q
```

## Not supported

- Requirements linking
- Attachments
- Xray test plans as TM objects
- Robot executions started by the skill
- Xray Server/DC
