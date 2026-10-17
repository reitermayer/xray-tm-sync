# xray-tm-sync + xray-tm-demo

Two Claude Code skills for the UiPath coding agent that connect **Xray Cloud** (Jira) to **UiPath Test Manager** (TM).

| Skill | Purpose | Commands |
|---|---|---|
| **`xray-tm-sync`** | **Production sync**, ready to hand to UiPath customers. Never deletes TM test cases, TM test sets or anything in Xray. | `check`, `pull`, `plan`, `apply`, `testsets`, `report` (real TM results only), `status` |
| **`xray-tm-demo`** | **UiPath MVP demo.** Loads `xray-tm-sync` as a library and adds what production must never do. | `sandbox`, `seed`, `execute` (mock run), `report` (mock results), `reset [--reseed]` |

Every write is a dry run until you pass `--yes`.

Every demo command needs `--xray-project` and `--tm-project`, and that pair must first be registered as a sandbox with `sandbox add`. The registry lives in `~/.config/xray-tm-demo/sandboxes.json`.

## ⚠ Work in progress — resume here

The split from the former single skill `jre-xray-uipath` into these two skills is **half done** (17.10.2026).

**Done**
- **Folders:** `xray-tm-sync/` and `xray-tm-demo/` exist.
- **`xray-tm-sync/scripts/xray_tm_sync.py`** (production core):
  - Demo code removed.
  - `report` split into `collect_tm_results`, `build_xray_json` and `publish_results`. Production `report` refuses mock executions.
  - No TM REST calls remain.
  - Names updated: workdir `.xray-tm-sync`, log folder `~/.claude/logs/xray-tm-sync`, marker `reported by xray-tm-sync`, env var `XRAY_TM_WORKERS`.
- **`xray-tm-demo/scripts/xray_tm_demo.py`** (written, **not yet tested**):
  - Loads the core from the sibling folder `../xray-tm-sync/scripts/xray_tm_sync.py`. Override the path with `XRAY_TM_SYNC_SCRIPT`.
  - Commands `sandbox`, `seed`, `execute`, `report`, `reset --reseed`.
  - Classes `DemoXray`, which adds Xray create/delete, and `DemoTm`, which adds TM deletes and the one TM REST call.
- **Seed data:** `xray-tm-demo/assets/seed-stock-trading.json` holds 8 Manual Xray tests (UiPath/PATH, Oracle/ORCL, Amazon/AMZN) and the Xray test set "Stock trading regression".

**Next steps**
1. **Split the tests.** `tests/test_xray_uipath.py` → `tests/test_sync.py` (intake, test sets, production report incl. the mock refusal) plus `tests/test_demo.py` (sandbox guard, execute, mock report, reset, reseed).
   - Add a shared `tests/conftest.py` that loads the core as `xray_tm_sync`, then the demo.
   - Demo tests register the sandbox through `XRAY_TM_DEMO_SANDBOXES=<tmp file>`.
   - Rename the `--sandbox-project` flags in tests to `--xray-project`.
   - Run `uv run pytest -q`; it had 30 passing tests before the split.
2. **Rewrite SKILL.md files.** `xray-tm-sync/SKILL.md` drops demo content and the TM REST exception. A new `xray-tm-demo/SKILL.md` covers the sandbox registration, the demo runbook and reset/reseed.
   - Move the demo half of `xray-tm-sync/references/execution-and-results.md` to `xray-tm-demo/references/`.
3. **Update `pyproject.toml`.** Rename the dev project from `jre-xray-uipath-dev`.
4. **Reinstall.**
   - Remove `~/.claude/skills/jre-xray-uipath` and install `xray-tm-sync` and `xray-tm-demo`.
   - On Drive, replace `jre-xray-uipath.zip` with two zips and update the README rows.
5. **Migrate live TM data.** XCA's TM custom field descriptions still say "Written by jre-xray-uipath". This is harmless. The local workdir `.xray-uipath` is now `.xray-tm-sync`.
6. **Live demo prep** (needs the user's go):
   - Run `sandbox add --xray-project UIP --tm-project XCA`.
   - Run a `reset --reseed` dry run, then `--yes`. This is the first real run of Xray `createTest`/`deleteTest`.
   - Then run the full demo once.

## Demo flow (target)

1. **Show Xray → sync → show TM.**
   - Show the Xray tests and their descriptions in Jira project UIP.
   - Sync: `xray-tm-sync pull` → `plan` → `apply --yes` → `testsets --yes`.
   - Show the TM test cases and the TM test set in XCA.
2. **Mock run → results to Xray.**
   - `xray-tm-demo execute --yes` (all Passed by default).
   - Show "Passed" in TM.
   - `xray-tm-demo report --yes`, then show the new `[MOCK]` Xray Test Execution in Jira.
3. **Between rehearsals:** `xray-tm-demo reset --xray-project UIP --tm-project XCA --reseed --yes`.

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
| Mock results | Only in the demo skill: tagged `[MOCK]`, test environment `TM-mock`, sandbox Jira project only | Fake results would distort Xray coverage in a real Jira project. |
| Reseed | Seed first, delete second | If Jira rejects the new tests, nothing in Xray is deleted. |
| Naming | Words like test, step, key or folder are never used without an Xray/TM qualifier ([terminology](xray-tm-sync/references/terminology.md)) | Xray and TM use the same words for different things. For example, an Xray repository folder is not an Orchestrator folder. |

## `uip tm` quirks found and handled (uip 1.203, 17.10.2026)

- **Silent no-op on custom field values.** TM creates an empty value row for each custom field when a test case is created. `customfield value create` on such a row returns Success but writes nothing. The skill uses `update` instead and reads the value back. Worth reporting to UiPath.
- **No project-wide lookup of custom field values.** Markers are read one TM test case at a time and cached in a local index.
- **Step logs need `testcaselog start` first.** Without it, `testcaselog finish --step-logs` fails with HTTP 400.
- **Missing test case ids.** `testsets list-testcases` rows have no TM case id; the skill resolves them through `testcases list`.
- **No way to delete executions in the CLI.** `uip tm` cannot delete TM test executions, and deleting a TM test set leaves its executions behind.
- **Manual runs need no default folder.** `testsets run --execution-type manual` works without a robot or a default Orchestrator folder.

## Verified live (17.10.2026, before the split)

- **Staging, tenant AgenticTesting, TM project `XCA`:** intake create, idempotent re-run, update, drift, test set mirroring, a mock execution, and a dry run of the Xray results report.
- **Xray Cloud, global host, Jira project `UIP`:** authentication; `getExpandedTests` pulled all 5 Manual Xray tests; `apply` created TM test cases `XCA:8`–`XCA:12`; re-plan showed all 5 `unchanged`; `getTestExecutions`/`getTests` ran in reset dry runs.
- **TM REST delete of a TM test execution:** worked live.
- **Not yet run live:**
  - `report --yes`, which writes to Jira.
  - Xray `createTest`/`createTestSet`/`deleteTest`.
  - Anything since the split.
- **Leftovers:**
  - In `XCA`: CALC fixture data (4 TM test cases, TM test sets `XCA:6`/`XCA:7`, one mock execution) and UIP test cases `XCA:8`–`XCA:12`.
  - The scratch TM project `XRSYNC`.

## Layout

```
xray-tm-sync/                       production skill
  SKILL.md                          (still describes the pre-split skill — see next steps)
  references/                       terminology, field mapping, execution & results, Xray GraphQL
  scripts/xray_tm_sync.py           check | pull | plan | apply | testsets | report | status
xray-tm-demo/                       demo skill (needs xray-tm-sync installed next to it)
  assets/seed-stock-trading.json    8 demo Xray tests + 1 Xray test set
  scripts/xray_tm_demo.py           sandbox | seed | execute | report | reset
tests/                              pytest + fake `uip` (in-memory TM) + recorded GraphQL pages
```

## Use

```bash
export XRAY_CLIENT_ID=… XRAY_CLIENT_SECRET=…        # Jira → Apps → Xray → API Keys
export XRAY_REGION=eu XRAY_JIRA_BASE_URL=https://acme.atlassian.net   # optional
S=~/.claude/skills/xray-tm-sync/scripts/xray_tm_sync.py
D=~/.claude/skills/xray-tm-demo/scripts/xray_tm_demo.py

# production
$S check    --tm-project XCA
$S pull     --jql 'project = UIP AND issuetype = Test'
$S plan     --tm-project XCA && $S apply --yes
$S testsets --tm-project XCA --yes
$S report   --tm-project XCA --execution-id <uuid> --yes          # real TM results only

# demo (sandbox pair registered once)
$D sandbox add --xray-project UIP --tm-project XCA
$D execute --xray-project UIP --tm-project XCA --tm-test-set XCA:6 --yes
$D report  --xray-project UIP --tm-project XCA --execution-id <uuid> --yes
$D reset   --xray-project UIP --tm-project XCA --reseed         # dry run; add --yes
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
