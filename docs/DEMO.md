# Demo: Xray Cloud ⇄ UiPath Test Manager with a coding agent

Each chapter has three parts:
- **Prompt**: what you type into Claude Code.
- **Under the hood**: the commands the agent runs. These are the same commands a pipeline runs without AI tokens.
- **Evidence**: what you show on screen to prove it worked.

Setup is described in [INSTALL.md](INSTALL.md). In this document, `DEMO` stands for your sandbox Jira project and TM project. The demo is about 15 minutes in total.

## Contents

0. [Before you start: reset the sandbox](#0-before-you-start-reset-the-sandbox)
1. [Xray: populate test data](#1-xray-populate-test-data)
2. [Xray → TM: sync Xray tests into TM test cases](#2-xray--tm-sync-xray-tests-into-tm-test-cases)
3. [TM: run the TM test cases](#3-tm-run-the-tm-test-cases)
   - 3a. [Mock run](#3a-mock-run-manual-tm-test-execution-no-robot)
   - 3b. [Real automation with UiPath Studio for Mac](#3b-real-automation-built-in-uipath-studio-for-mac)
4. [TM → Xray: send the results back](#4-tm--xray-send-the-results-back)
5. [Wrap-up: run it again, then run it without the agent](#5-wrap-up-run-it-again-then-run-it-without-the-agent)

---

## 0. Before you start: reset the sandbox

> Reset the demo sandbox DEMO and seed the stock-trading tests.

- **Under the hood:** `xray_tm_demo.py reset --xray-project DEMO --tm-project DEMO --reseed`, which is a dry run, then the same command with `--yes`.
- **Evidence:** the dry-run list of what would be deleted. Nothing is deleted until you confirm.
- Off camera. Run it between rehearsals.

## 1. Xray: populate test data

> Show me the Xray tests in Jira project DEMO.

- **Under the hood:** `xray_tm_demo.py seed` and Xray GraphQL `createTest`/`createTestSet`. The seed creates 8 Manual Xray tests (buy, sell, limit and stop-loss orders for UiPath, Oracle and Amazon) and the Xray test set "Stock trading regression".
- **Evidence:** open Jira. Show an Xray test with its description; the description is the spec. Show the Xray test set.
- **Message:** this is where testers already work today, and nothing changes for them.

## 2. Xray → TM: sync Xray tests into TM test cases

> Retrieve all Xray tests from Jira project DEMO and sync them to TM project DEMO. Show me the plan first.

- **Under the hood:**
  - `xray_tm_sync.py pull`: Xray GraphQL `getExpandedTests`.
  - `xray_tm_sync.py plan`: a dry run. The table shows 8 × `create`.
  - After you answer **yes**: `apply --yes`, which calls `uip tm testcases create`, `uip tm customfield value update` and `uip tm objectlabel add`.
  - `testsets --yes`, which calls `uip tm testsets create` and `uip tm testcases add`.
- **Evidence:**
  - In TM: 8 TM test cases, each with its description and the custom field `Xray Key`, and the TM test set "Stock trading regression".
  - Optional: generate TM steps from the description with GenAI in TM. The sync leaves those steps alone.
  - In a terminal: `uip tm testcases list --project-key DEMO --output json`, to show the data without the agent.
- **Message:** the agent chose the parameters. The test content went Xray → script → `uip`, never through the model.

## 3. TM: run the TM test cases

Pick one path. 3a always works. 3b shows real automation.

### 3a. Mock run (manual TM test execution, no robot)

> Run the TM test set "Stock trading regression" as a mock run, all passed.

- **Under the hood:**
  - `xray_tm_demo.py execute --tm-test-set DEMO:… --yes`, which calls `uip tm testsets run --execution-type manual`.
  - For each TM test case: `uip tm testcaselog start`, then `uip tm testcaselog finish --result Passed`.
- **Evidence:** the TM test execution with 8 × **Passed**.
- **Honesty:** the results are tagged `[MOCK]`. The production `report` refuses to send them to Xray.

### 3b. Real automation built in UiPath Studio for Mac

> Run the TM test set "Stock trading regression" with the automations linked to it.

- **Prepare:**
  - Build test automations in UiPath Studio (for example one for "Buy UiPath market order").
  - Publish them to Orchestrator and link them to the TM test cases.
- **Under the hood:** `uip tm testsets run` (automated). The robot executes the automations.
- **Evidence:** a real TM test execution with robot logs.
- **Target app:** [reitermayer/trade-demo](https://github.com/reitermayer/trade-demo), a fake stock-trading web app with a market simulator, hosted at `https://www.reitermayer.com/trade/`.
- **Status:** the app is deployed and a Playwright suite for the 8 seeded Xray tests passes against it (`trade-demo/automation/playwright`). Still to settle: the robot setup, and linking the automations to TM test cases.

## 4. TM → Xray: send the results back

> Send the results of this TM test execution back to Xray.

- **Under the hood:**
  - After 3a: `xray_tm_demo.py report --yes`. This makes an Xray Test Execution `[MOCK] …` (add `--test-environment TM-mock` if that Xray test environment exists).
  - After 3b: `xray_tm_sync.py report --yes`, the production path.
  - Both send Xray JSON to `POST /api/v2/import/execution`, matched by the `testKey` from the origin marker, never by name.
- **Evidence:** in Jira, the new Xray Test Execution showing 8 × PASSED. Open one Xray test to show its execution history.
- **Message:** the round trip is closed. Xray stays the system of record, and TM runs the tests.

## 5. Wrap-up: run it again, then run it without the agent

> Run the sync again. Then give me the commands so our pipeline can run this nightly without you.

- **Under the hood:** `plan` reports 8 × `unchanged`, which proves the sync is idempotent. Claude then prints the plain `xray_tm_sync.py` / `uip` commands.
- **Evidence:** paste the commands into a fresh terminal and run them. Same result, no AI tokens.
- **Message:** an agent where it adds value, a script where repeatability matters. MCP is for questions, the CLI for operations.
