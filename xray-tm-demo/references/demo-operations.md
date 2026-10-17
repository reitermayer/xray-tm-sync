# Demo operations: seed, mock TM execution, mock results, reset

Every command needs `--xray-project <JIRA_PROJECT_KEY> --tm-project <TM_PROJECT_KEY>`, and that pair must be registered in `~/.config/xray-tm-demo/sandboxes.json` (override with `XRAY_TM_DEMO_SANDBOXES`).

## Seed (`seed`, and step 2 of `reset --reseed`)

`assets/seed-stock-trading.json` contains 8 Manual Xray tests: UiPath/PATH, Oracle/ORCL and Amazon/AMZN orders, with the steps written as text in the Jira description. It also contains the Xray test set "Stock trading regression".

- **Xray tests:** one `createTest(testType: {name: "Manual"}, jira: {fields: {project: {key}, summary, description, labels}})` per seed test.
- **Xray test set:** `createTestSet(testIssueIds, jira)`.
- **No Xray steps.** The seed creates no Xray steps on purpose. In TM, the TM steps are generated from the description with GenAI and stay TM-owned (see xray-tm-sync, field-mapping).

## Mock TM execution (`execute`)

Verified protocol (uip 1.203, 17.10.2026):

1. `uip tm testsets run --test-set-key K --execution-type manual` returns an `ExecutionId`.
   - A manual run needs no robot and no default Orchestrator folder.
   - TM creates one TM test case log per member, with result None.
2. `uip tm testcaselog start --execution-id E --test-case-id <uuid>` returns the test case log `Id`. **This step** creates the TM step logs; without it, `finish --step-logs` fails with `HTTP 400`.
3. `uip tm teststeplog list --test-case-log-id <log id>` returns the step log rows.
4. `uip tm testcaselog finish … --result Passed|Failed|None|Restricted --has-error … --executed-by <uip identity> --step-logs '<json>'`.
   - The JSON is the step log rows from step 3, with camelCase keys and with `result`, `hasError` and `comment` set.

Details:
- **Ids.** `uip tm testsets list-testcases` rows carry no TM case id, so the script resolves ids through `uip tm testcases list`.
- **Outcomes file.** Every result defaults to Passed. Example: `{"UIP-3": "Failed", "UIP-4": {"result": "Failed", "failed_step": 2, "comment": "…"}}`.
  - With `failed_step: n`, the steps before n are Passed, step n is Failed and the rest are None.
  - Without `failed_step`, the last step fails.
- **Tagging.** Every TM step log comment starts with `[MOCK]`. A local record `execution-<id>.json` with `"mock": true` is written as well.

## Mock results to Xray (`report`)

- **Same import as production.** `xray-tm-sync`'s `collect_tm_results` → `build_xray_json` → `publish_results`, i.e. `POST /api/v2/import/execution`.
- **Marked as mock.** The summary starts with `[MOCK] ` and the description adds "Mock results — not real test outcomes."
- **Test environment.** Pass `--test-environment TM-mock` only if that Xray test environment exists in the Jira project. The Xray import rejects unknown ones with HTTP 400 ("environments dont exist"); verified live on 17.10.2026.
- **Refused** when any Xray key does not belong to the sandbox Jira project, or when the TM test execution has no mock results (real results go through xray-tm-sync).

## Reset (`reset`)

The dry run lists everything first. The `--yes` run deletes in this order:

| Order | Object | Found by | Deleted with |
|---|---|---|---|
| 1 | TM test execution | `uip tm executions list --test-set-id` for each Xray-origin TM test set, plus local `execution-*.json` mock records | TM API v2 `DELETE /api/v2/{projectId}/testexecutions/{id}` with the token from `uip login refresh`. **This is the only TM REST call.** `uip` cannot delete TM test executions, and deleting a TM test set leaves its executions behind. |
| 2 | TM test set | TM custom field `Xray Key` (TestSet) with a key from the sandbox Jira project | `uip tm testsets delete --yes` |
| 3 | TM test case | TM custom field `Xray Key` (TestCase) with a key from the sandbox Jira project | `uip tm testcases delete --yes` |
| 4 | Xray test execution | `getTestExecutions(jql: project = P)`. Only `[MOCK]` ones, or **all** with `--reseed`. | GraphQL `deleteTestExecution(issueId)`. The status of each Xray test falls back to its previous run, or to TODO. |
| 5 | Xray test set, Xray test | `getTestSets` / `getTests` (only with `--reseed`) | `deleteTestSet` / `deleteTest` |

Never touched:
- TM test cases without an origin marker
- TM objects whose Xray key comes from another Jira project
- Real Xray test executions (without `--reseed`)

**Reseed order: seed first, delete second.** Before step 4, the seed is created. If Jira rejects it, no Xray data is deleted and the output says so. After a reseed, the local `snapshot.json` and `plan.json` are removed, so the next `pull` starts from the seed.

The API key's Jira user needs *Create issues* and *Delete issues* in the sandbox Jira project.

**Manual alternative in the Xray UI:** open the Xray test execution → ⋯ → Delete, or reset a single Xray test run inside it.
