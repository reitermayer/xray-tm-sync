# Terminology contract: Xray ↔ Test Manager

**Rule:** in prose, code, logs and output, never use these words without a qualifier:
test, case, step, set, key, id, project, folder, precondition, requirement, label, run, execution, type.
Prose says **Xray …** / **TM …**; code uses `xray_` / `tm_` prefixes.

| Concept | Xray (source) — say | TM (target) — say | Code | Trap |
|---|---|---|---|---|
| Test definition | **Xray test** (Jira issue of type Test) | **TM test case** | `xray_test` / `tm_case` | `uip tm` writes "testcase"; never say bare "test" |
| Human identifier | **Xray key** `CALC-123` (hyphen) | **TM case key** `CALC:12` (colon) | `xray_key` / `tm_case_key` | Both systems may use the same project prefix; the separator is the only tell |
| Machine identifier | **Xray issue id** (numeric string) | **TM case id** (UUID) | `xray_issue_id` / `tm_case_id` | `uip tm testcases steps …` needs the UUID; `update`/`delete` need the key |
| Container | **Jira project** | **TM project** | `jira_project` / `tm_project_key` | Same key string, two different objects |
| Step | **Xray step**: action / data / expected result | **TM step**: description / clipboard data / expected result | `xray_step` / `tm_step` | |
| Folder | **Xray repository folder** `/A/B` | — (TM "folder" = **Orchestrator folder**) | `xray_repo_path` | Never write it into a TM/Orchestrator folder; it becomes the label `XR_Folder_/A/B` |
| Precondition | **Xray precondition**: own Jira issue, reusable, many-to-many | **TM precondition**: free text on the TM test case | `xray_precondition` / `tm_precondition_text` | Joined into the text, each prefixed with its Xray key |
| Requirement | **Jira requirement** (Story/Epic covered by the Xray test) | **TM requirement** (native object) | `jira_req_key` / `tm_requirement_id` | Not mapped in v1 |
| Kind | **Xray test type** Manual / Cucumber / Generic (kind Steps / Gherkin / Unstructured) | — | `xray_test_type` / `xray_test_kind` | Label `XR_Type_<name>` |
| Tags | **Jira labels** | **TM labels** (objectlabel) | `jira_labels` / `tm_labels` | Prefixed `XR_Label_`; user TM labels are never touched |
| Grouping | **Xray test set** `CALC-200` (Jira issue) | **TM test set** `CALC:40` (executable) | `xray_test_set_key` / `tm_test_set_key` | Mirrored by `testsets`; marker = TM custom field `Xray Key` on the TM test set |
| Execution | **Xray test execution** `CALC-300` (Jira issue) with **Xray test runs** | **TM test execution** (UUID) with **TM test case logs** and **TM step logs** | `execution_id` / `tm_case_log_id` | `report` turns one TM test execution into one new Xray test execution |
| Plan | **Xray test plan** | — | `--xray-test-plan` | Only as the target of `report` |

## Sync vocabulary

- **Intake** — the one-way copy Xray → TM. Not "sync" in both directions, never "import back".
- **Source / target** — Xray / TM.
- **Origin marker** — TM custom field `Xray Key` on a TM test case: the Xray key it came from.
- **Intake record** — TM custom field `Xray Intake` (JSON): `xrayHash`, `tmHash`, `lastModified`, `intakeAt`.
- **Drift** — the TM test case's name, description or steps changed since the last intake.
- **Mock** — a TM test execution whose results came from an outcomes file, tagged `[MOCK]`; may only reach a sandbox Jira project.
- **Orphan** — a TM test case whose Xray test was not in the latest full pull.
