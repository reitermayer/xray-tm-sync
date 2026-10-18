# Live rehearsal: 17.10.2026

This was a full run of [DEMO.md](DEMO.md) chapters 0–4 against **Jira project UIP** (Xray Cloud, global host) and **TM project XCA** (a UiPath Automation Cloud tenant). The skills ran from `~/.claude/skills`, as installed.

The rehearsal ran the skill scripts that `xr` 0.2 replaced; the same commands are now `xr demo …` and `xr sync …` (same code, moved into the `xr` package).

Every number below comes from the command trail in `.xray-tm-sync/evidence/`, which is not committed because it contains tenant data.

| Chapter | Command | Result | `uip` calls | Xray requests |
|---|---|---|---|---|
| 0 | `xray_tm_demo.py reset --reseed` (dry run) | It would delete 5 Xray tests (UIP-3…7) and 5 TM test cases (XCA:8–12), then seed 8 new Xray tests. The CALC leftovers in XCA are not touched, because they belong to another Jira project. | 14 | 3 |
| 0 | `… reset --reseed --yes` | 5 TM test cases deleted. Seeded **UIP-8…UIP-15** and the Xray test set **UIP-16**, then deleted 5 old Xray tests. This was the first live run of `createTest`, `createTestSet` and `deleteTest`. | 19 | 17 |
| 2 | `xray_tm_sync.py pull --jql 'project = UIP AND issuetype = Test'` | 8 Xray tests, all Manual, in one GraphQL page | 0 | 1 |
| 2 | `plan --tm-project XCA` | 8 × `create` | 7 | 0 |
| 2 | `apply --yes` | **XCA:13–XCA:20** created, 0 failed, 19 s | 56 | 0 |
| 2 | `testsets --tm-project XCA --yes` | TM test set **XCA:21** "Stock trading regression" with 8 members | 10 | 0 |
| 2 | `plan --tm-project XCA` (again) | 8 × `unchanged`, no writes | 11 | 0 |
| 3a | `xray_tm_demo.py execute --tm-test-set XCA:21 --yes` | TM test execution `c640e50d-fa6e-0f00-9972-0b4a4144dbef`, 8/8 Passed | 36 | 0 |
| 4 | `xray_tm_sync.py report …` (production) | **Refused**: "contains [MOCK] results" | 17 | 0 |
| 4 | `xray_tm_demo.py report … --yes` | Xray test execution **UIP-17** "[MOCK] UiPath Test Manager test set XCA:21 (TM project XCA)", 8 × PASSED | 17 | 1 |

Over the whole run, from reset to report and without the dry runs, there were about 145 `uip` calls, and none failed.

## Independent checks with plain `uip`, no skill involved

```bash
uip tm testcases list --project-key XCA --output json      # XCA:13–20 listed with the seed summaries
uip tm testsets list  --project-key XCA --output json      # XCA:21 Stock trading regression
uip tm customfield value get --project-key XCA --object-type TestCase \
    --object-id <XCA:13 id> --name "Xray Key" --output json # "UIP-15"
```

## Found and fixed during the rehearsal

- **Xray test environment.** The first `report --yes` failed with HTTP 400: "environments dont exist … 'TM-mock'". The Xray import only accepts test environments that already exist in the Jira project. The mock test environment is now optional (`--test-environment`). The failure was harmless: the dry run had shown exactly what would be sent, and nothing was half-written.

## Applied from the Avalon Test Manager MCP server

- **TM custom field race.** About 100 ms after a TM test case is created, TM provisions empty custom field rows, which can overwrite a value just written. Avalon 3.4.1 found this; the same fix is used here: write, wait, read back, rewrite. All 8 origin markers stuck on the first live run.
- **`XRAY_ID_FIELD`.** Set `XRAY_ID_FIELD=XRAY_ID` to use the same marker field as the Avalon MCP server.
- **`foreignReference`.** Avalon uses it as its machine join. `uip tm` 1.203 cannot set or read it, so the skill can't use it yet. That is worth asking UiPath for: `--foreign-reference` on `uip tm testcases create`/`update`, and the field in `testcases list`.

## State after the rehearsal

- **UIP:**
  - 8 Xray tests and the Xray test set UIP-16
  - UIP-17 (`[MOCK]`)
- **XCA:**
  - XCA:13–21 and the mock TM test execution
  - the CALC fixture leftovers (XCA:1–4, 6, 7)

Before a recording: `xr demo reset --xray-project UIP --tm-project XCA --reseed --yes` brings this back to the seed alone.
