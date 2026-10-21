# xr: Xray Cloud CLI + Xray ⇄ UiPath Test Manager sync

**One codebase, two ways to use it.** `xr` is a command line tool for Xray Cloud built like UiPath's `uip`. People and CI pipelines run it directly; a coding agent (Claude Code) runs exactly the same commands through three skills that contain instructions only, no code. Whatever the agent did, a sceptic can re-run by hand, with the same output and the same command trail.

```
person / script / CI ──────────────┐
                                    ├──▶  xr  ──▶ Xray Cloud (GraphQL, results import)
coding agent ─▶ skills (SKILL.md) ──┘     │
                                          └──▶ uip tm ──▶ UiPath Test Manager
```

| `xr` (Xray) | `uip` (Test Manager) |
|---|---|
| `xr project list` | `uip tm project list --filter "Xray CodingAgent"` |
| `xr tests list --project UIP` | `uip tm testcases list --project-key XCA` |
| `xr testsets list --project UIP` | `uip tm testsets list --project-key XCA` |
| `xr testexecutions list --project UIP` | `uip tm executions list --project-key XCA` |

Both print the envelope `{Result, Code, Data}`, take `--output json|table` and `--output-filter <JMESPath>`, and use the same exit codes. Every write is a dry run until `--yes`. (`uip xr …` itself is not possible: `uip` loads only tools from a fixed list of UiPath packages; see [docs/ROADMAP.md](docs/ROADMAP.md).)

| Command group | Purpose | Skill |
|---|---|---|
| `xr login`, `project`, `tests`, `testsets`, `testexecutions`, `testplans` | Xray objects, one command each | **`xray`** |
| `xr sync check \| pull \| plan \| apply \| testsets \| report \| status` | **Production sync** Xray → TM, results TM → Xray. Never deletes TM test cases, TM test sets or anything in Xray. | **`xray-tm-sync`** |
| `xr demo sandbox \| seed \| execute \| report \| reset` | **UiPath MVP demo**: seed, mock run, mock report, reset. Only in a registered sandbox pair (`~/.config/xray-tm-demo/sandboxes.json`). | **`xray-tm-demo`** |

Install: [docs/INSTALL.md](docs/INSTALL.md): `uv tool install git+https://github.com/reitermayer/xray-tm-sync`, then `uip skills install --repo reitermayer/xray-tm-sync --agent <agent>` for any coding agent `uip skills` supports (claude, cursor, copilot, codex, opencode, autopilot, kiro, windsurf, antigravity, devin).

## Status

`xr` and the skills are tested (`uv run pytest`) and ran live end to end against Xray Cloud and UiPath Test Manager; see [docs/REHEARSAL.md](docs/REHEARSAL.md). Open items and wishes for `uip` are in [docs/ROADMAP.md](docs/ROADMAP.md).

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
   - `xr tests list --project DEMO --output table`.
   - Sync: `xr sync pull` → `plan` → `apply --yes` → `testsets --yes`.
   - Show the TM test cases and the TM test set in TM project DEMO.
2. **Mock run → results to Xray.**
   - `xr demo execute --yes` (all Passed by default).
   - Show "Passed" in TM.
   - `xr demo report --yes`, then show the new `[MOCK]` Xray Test Execution in Jira.
3. **Between rehearsals:** `xr demo reset --xray-project DEMO --tm-project DEMO --reseed --yes`.

## Design decisions

| Topic | Decision | Why |
|---|---|---|
| One codebase | All code in the `xr` package; the skills hold instructions only | The agent has no hidden code path: it runs the commands a person or a pipeline runs, with the same output and command trail. |
| Command style | `xr` copies `uip` (envelope, `--output`, `--output-filter`, exit codes, dry run until `--yes`) | Teams already scripting `uip` can script Xray the same way, and `xr` could become a `uip` tool later. |
| Skill split | `xray` (objects), `xray-tm-sync` (production sync), `xray-tm-demo` (demo) | The production sync never deletes. `xr demo` ships in the same package but refuses any Jira/TM project pair that isn't registered as a sandbox, and the production skill never calls it. `xr tests delete` exists for single Xray tests, dry run until `--yes`, as `uip tm testcases delete` does. |
| Xray API | GraphQL (`getExpandedTests`), Xray Cloud only | No Xray Cloud REST endpoint returns manual step definitions. One paged query also returns the steps, preconditions, test sets, repository folder and Jira fields. Xray Server/DC has no GraphQL. |
| TM API | `uip tm … --output json` only (production) | Reuses your `uip login` OAuth session (browser login, or client credentials in CI). The demo makes one documented exception: TM API v2 `DELETE …/testexecutions/{id}`, because `uip` cannot delete TM test executions. |
| Xray auth | `XRAY_CLIENT_ID` / `XRAY_CLIENT_SECRET` env vars → 24 h JWT, kept in memory only | Xray Cloud has no OAuth or user-delegated login. |
| Origin marker | TM custom field `Xray Key` (on TM test cases and TM test sets) plus `Xray Intake` (JSON with hashes) | Keeps TM names clean. Makes re-sync, drift detection and a safe demo reset possible. |
| Re-sync | Xray wins, but TM test cases edited by hand are reported as `drift` and skipped (`--accept-drift` overrides) | One-way sync that doesn't silently overwrite edits. |
| TM-owned steps | An Xray test without Xray steps leaves its TM steps alone, and those steps are left out of the drift hash | Steps are generated from the description with GenAI in TM. |
| Results to Xray | Xray JSON import using `testKey` from the origin marker, not JUnit | JUnit matches tests by name, so Xray would create duplicate Generic tests. |
| Mock results | Only in the demo skill: tagged `[MOCK]` (optional test environment `TM-mock` if it exists in Xray), sandbox Jira project only | Fake results would distort Xray coverage in a real Jira project. |
| Reseed | Seed first, delete second | If Jira rejects the new tests, nothing in Xray is deleted. |
| Naming | Words like test, step, key or folder are never used without an Xray/TM qualifier ([terminology](skills/xray-tm-sync/references/terminology.md)) | Xray and TM use the same words for different things. For example, an Xray repository folder is not an Orchestrator folder. |

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
src/xr/                             the only code
  cli.py                            `xr` entry point, global --output / --output-filter
  output.py                         uip-style envelope and table output
  resources.py                      login | project | tests | testsets | testexecutions | testplans
  sync.py                           xr sync: check | pull | plan | apply | testsets | report | status
  demo.py                           xr demo: sandbox | seed | execute | report | reset
  data/seed-stock-trading.json      8 demo Xray tests + 1 Xray test set
skills/                             installed with `uip skills install --repo …` for any supported coding agent
  xray/SKILL.md                     xr object commands
  xray-tm-sync/                     xr sync (SKILL.md + references: terminology, field mapping, results, GraphQL)
  xray-tm-demo/                     xr demo (SKILL.md + references/demo-operations.md)
docs/                               INSTALL, DEMO (chapters), REHEARSAL (live evidence), ROADMAP
tests/                              pytest + fake `uip` (in-memory TM) + fake Xray + recorded GraphQL pages
```

## Use

```bash
xr login                                          # once; stores the Xray API key (mode 600)
xr project list --output table
xr tests list --project DEMO --output-filter '[].{Key: Key, Summary: Summary}'
xr testexecutions get DEMO-17 --output table

# production sync
xr sync check    --tm-project DEMO
xr sync pull     --jql 'project = DEMO AND issuetype = Test'
xr sync plan     --tm-project DEMO && xr sync apply --yes
xr sync testsets --tm-project DEMO --yes
xr sync report   --tm-project DEMO --execution-id <uuid> --yes     # real TM results only

# demo (sandbox pair registered once)
xr demo sandbox add --xray-project DEMO --tm-project DEMO
xr demo execute --xray-project DEMO --tm-project DEMO --tm-test-set DEMO:21 --yes
xr demo report  --xray-project DEMO --tm-project DEMO --execution-id <uuid> --yes
xr demo reset   --xray-project DEMO --tm-project DEMO --reseed     # dry run; add --yes
```

## Develop

```bash
uv run pytest -q
uv tool install --editable .        # `xr` on your PATH, running this checkout
```

## Not supported

- Requirements linking
- Attachments
- Xray test plans as TM objects
- Robot executions started by the skill
- Xray Server/DC
