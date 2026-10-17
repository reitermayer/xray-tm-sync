# Roadmap and open items

## Demo chapter 3b: real automation instead of the mock run

- **Target app:** [reitermayer/trade-demo](https://github.com/reitermayer/trade-demo). Deployed at https://www.reitermayer.com/trade/ (`./deploy.sh` in that repo).
- **Automations:** one per seeded Xray test, titled with the Xray test summary, so they pair with the TM test cases by name (see `trade-demo/docs/AUTOMATION.md`).
- **Link:** `trade-demo/automation/link-automations.sh` links each TM test case to its automation, without AI.
- **Run and report:** `uip tm testsets run --execution-type automated --wait`, then `xray_tm_sync.py report --yes`. This is the first production `report` of a real TM test execution.
- **Open question:** which robot type runs the package (serverless, VM or unattended).

## Wishes for `uip tm` (uip 1.203)

- `uip tm testcases create/update --foreign-reference`, and the field returned by `testcases list`. It would replace the per-case custom field lookup with one list call, as the Avalon Test Manager MCP server does with the TM API.
- `uip tm executions delete`. Today the demo `reset` needs one TM REST call for this.
- A project-wide lookup of custom field values.

## Before the repo goes public

- Rotate the Xray API key that was used during development.
- The git history mentions the UiPath tenant name in older README versions; no secrets were ever committed (`.env` is gitignored). Squash or accept that before publishing.
