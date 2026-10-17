# TM test sets, mock TM executions, results to Xray

## TM test sets (`testsets`)

- Source: the Xray test sets of the pulled Xray tests (`testSets(limit: 20)` in the GraphQL query).
  Members outside the JQL are listed as `xray_tests_not_in_tm`, not created.
- Each TM test case also gets the label `XR_Set_<xray test set key>` during intake, so
  `uip tm testcases add --test-set-key K --labels XR_Set_CALC-200` works for users too.
- Origin marker: TM custom field `Xray Key` with object type **TestSet** (a definition separate from
  the TestCase one; same name is allowed per object type).
- Name = Xray test set summary. Membership is added with explicit TM case keys
  (`uip tm testcases add --test-case-keys`), removed with `uip tm testcases remove`. Only TM test
  cases whose Xray test was pulled and is no longer in the Xray test set are removed.

## Mock TM execution (`execute --mock`)

Verified protocol (uip 1.203, 17.10.2026):

1. `uip tm testsets run --test-set-key K --execution-type manual` → `ExecutionId`. A manual run needs
   no robot and no default Orchestrator folder. It creates one TM test case log per member, result None.
2. `uip tm testcaselog start --execution-id E --test-case-id <uuid>` → test case log `Id`. **This**
   creates the TM step logs; without it `finish --step-logs` fails with `HTTP 400`.
3. `uip tm teststeplog list --test-case-log-id <log id>` → step log rows.
4. `uip tm testcaselog finish … --result Passed|Failed|None|Restricted --has-error … --executed-by <uip
   identity> --step-logs '<json>'`, where the JSON is the step log rows from 3 with camelCase keys and
   `result`, `hasError`, `comment` set.

`uip tm testsets list-testcases` rows carry no TM case id; the script resolves ids through
`uip tm testcases list`. Failed outcome with `failed_step: n`: steps before n Passed, step n Failed, rest
None; without `failed_step` the last step fails. Every TM step log comment starts with `[MOCK]`.

## Results to Xray (`report`)

- Endpoint: `POST /api/v2/import/execution` (Xray JSON). Response `{"id", "key", "self"}`; `key` is the
  new Xray test execution.
- **Not JUnit.** `uip tm result download` + `/import/execution/junit` matches by name, so Xray would
  create duplicate Generic Xray tests. Xray JSON uses `testKey` from the TM origin marker — exact.
- Status map: Passed→PASSED, Failed→FAILED, None→TODO, Restricted→ABORTED.
- Step results are sent only for plain Manual Xray tests whose Xray step count equals the TM step log
  count (Xray rejects a mismatched `steps` array). Cucumber, Generic and modular (called-test) Xray
  tests get the overall status only.
- TM test cases without an Xray origin marker are listed in `skipped`.
- Mock guard: a mock record in `.xray-uipath/execution-<id>.json` or any `[MOCK]` step log makes the run
  a mock. Mock results need `--sandbox-project P`, every Xray key must be `P-…`, the summary starts
  with `[MOCK]` and `info.testEnvironments = ["TM-mock"]`.

```json
{
  "info": {"project": "CALC", "summary": "[MOCK] UiPath Test Manager test set XCA:6 (TM project XCA)",
           "description": "…", "startDate": "…+00:00", "finishDate": "…+00:00",
           "testEnvironments": ["TM-mock"], "testPlanKey": "CALC-299"},
  "tests": [{"testKey": "CALC-101", "status": "FAILED", "start": "…", "finish": "…",
             "comment": "UiPath Test Manager XCA:3, test case log …",
             "steps": [{"status": "PASSED", "comment": "[MOCK]"}, {"status": "FAILED", "comment": "[MOCK] …"},
                       {"status": "TODO", "comment": "[MOCK]"}]}]
}
```

## Reset (`reset`)

| Object | Found by | Deleted with |
|---|---|---|
| Xray test execution | `getTestExecutions(jql: "project = P")`, description contains `reported by jre-xray-uipath`; `[MOCK]` summary unless `--include-real-results` | GraphQL `deleteTestExecution(issueId)` → the Xray tests' status falls back to their previous run, or TODO |
| TM test execution | `uip tm executions list --test-set-id` of each Xray-origin TM test set, plus `execution-*.json` mock records | TM API v2 `DELETE /api/v2/{projectId}/testexecutions/{id}` (no `uip` command exists; deleting a TM test set leaves its executions behind) |
| TM test set | TM custom field `Xray Key` (TestSet) | `uip tm testsets delete --yes` |
| TM test case | TM custom field `Xray Key` (TestCase) | `uip tm testcases delete --yes` |

Xray Jira projects searched: `--xray-project`, else the prefixes of the origin markers plus the local
ledger `xray-executions.json` that `report --yes` appends to. Manual alternative in the Xray UI: open the
Xray test execution → ⋯ → Delete; or reset a single Xray test run inside it.

### Demo reseed (`reset --reseed --xray-project <SANDBOX>`)

1. TM cleanup as above.
2. Create the seed: GraphQL `createTest(testType: {name: "Manual"}, jira: {fields: {project: {key},
   summary, description, labels}})` per seed Xray test, then `createTestSet(testIssueIds, jira)`.
3. Only if 2 succeeded: delete every Xray test execution (`deleteTestExecution`), Xray test set
   (`deleteTestSet`) and Xray test (`deleteTest`) of the sandbox project that existed before step 2.
4. `snapshot.json` and `plan.json` are removed locally; the next `pull` starts from the seed.

The API key's Jira user needs create and delete permissions in the sandbox project.
