# TM test sets, results to Xray, evidence

## TM test sets (`testsets`)

- **Source.** The Xray test sets of the pulled Xray tests (`testSets(limit: 20)` in the GraphQL query). Members outside the JQL are listed as `xray_tests_not_in_tm`; they are not created.
- **Label.** During intake each TM test case also gets the label `XR_Set_<xray test set key>`. That lets users run `uip tm testcases add --test-set-key K --labels XR_Set_CALC-200` themselves.
- **Origin marker.** The TM custom field `Xray Key` on object type **TestSet**. This is a separate definition from the TestCase one; TM allows the same name once per object type.
- **Name and membership.**
  - The TM test set name is the Xray test set summary.
  - Members are added with explicit TM case keys (`uip tm testcases add --test-case-keys`) and removed with `uip tm testcases remove`.
  - A TM test case is removed only if its Xray test was pulled and is no longer in the Xray test set. TM test cases a user added by hand stay.

## Results to Xray (`report`)

Input is a TM test execution that testers or robots ran in TM. `uip tm executions list --test-set-id <TM test set id>` shows the TM execution ids.

- **Endpoint.** `POST /api/v2/import/execution` (Xray JSON). The response is `{"id", "key", "self"}`, where `key` is the new Xray test execution.
- **Not JUnit.** `uip tm result download` + `/import/execution/junit` matches tests by name, so Xray would create duplicate Generic Xray tests. Xray JSON uses the `testKey` from the TM origin marker, which is exact.
- **Status map.** Passed→PASSED, Failed→FAILED, None→TODO, Restricted→ABORTED.
- **Step results.** They are sent only for plain Manual Xray tests whose Xray step count equals the TM step log count, because Xray rejects a mismatched `steps` array. Cucumber, Generic and modular (called-test) Xray tests get the overall status only.
- **Skipped.** TM test cases without an Xray origin marker are listed in `skipped`.
- **Description.** Every Xray test execution it creates contains `reported by xray-tm-sync` in its description.
- **Optional.** `--xray-test-plan <XRAY_KEY>`, `--test-environment <name> …`, and `--xray-project` (required when the Xray tests come from several Jira projects).
- **Refused.** Any TM test execution with mock results: a local mock record, or a TM step log comment starting with `[MOCK]`. Mock runs belong to the `xray-tm-demo` skill.
- **Ledger.** `report --yes` appends the new Xray key to `.xray-tm-sync/xray-executions.json`.

```json
{
  "info": {"project": "CALC", "summary": "UiPath Test Manager test set CALC:6 (TM project CALC)",
           "description": "… reported by xray-tm-sync.", "startDate": "…+00:00", "finishDate": "…+00:00",
           "testEnvironments": ["staging"], "testPlanKey": "CALC-299"},
  "tests": [{"testKey": "CALC-101", "status": "PASSED", "start": "…", "finish": "…",
             "comment": "UiPath Test Manager CALC:3, test case log …",
             "steps": [{"status": "PASSED"}, {"status": "PASSED"}, {"status": "PASSED"}]}]
}
```

## Evidence: the command trail

Every run writes to `.xray-tm-sync/evidence/`. The run name is `<YYYYmmdd-HHMMSS>-<subcommand>`.

| File | Content |
|---|---|
| `<run>.jsonl` | One line per call: the script invocation, then every `uip` call (`argv`, `ok`, `ms`) and every Xray request. Xray requests are logged by GraphQL operation and variables, or as the REST import with the Xray keys sent, plus the HTTP status. |
| `<run>.sh` | The `uip` calls as shell lines (failed calls are commented out). The header names the script invocation that produced them. |

- **No secrets.** The trail never contains secrets: no headers, tokens or client secret.
- **How to repeat.** The `.sh` file is an audit record, not a replay script. Replaying `testcases create` lines would create duplicates. To repeat a run, call the script invocation from the header again; it plans before it writes.
- **Output.** Every subcommand's JSON output names its trail files under `evidence`.
