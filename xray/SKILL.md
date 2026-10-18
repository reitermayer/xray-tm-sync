---
name: xray
description: Xray Cloud (Jira) from the command line with the `xr` CLI, in the style of `uip`. List and read Jira projects with Xray tests, Xray tests (with steps, preconditions, Xray test sets), Xray test sets, Xray test executions (with run statuses) and Xray test plans; create or delete single Xray tests; import Xray JSON results as an Xray test execution; log in with an Xray API key. Use for "list the Xray tests in project X", "show Xray test UIP-8", "which Xray test executions exist", "what did the last Xray test execution report", "create an Xray test", "log in to Xray", "xr …". Syncing Xray tests into UiPath Test Manager→xray-tm-sync. Demo seeding and resets→xray-tm-demo. Xray Server/Data Center is not supported.
---

# Xray Cloud with `xr`

`xr` is a command line tool for Xray Cloud built like `uip`. This skill has no code of its own: you run `xr` exactly as a person or a script would, so everything you do can be repeated without an agent.

| `uip` (Test Manager) | `xr` (Xray) |
|---|---|
| `uip tm project list` | `xr project list` |
| `uip tm testcases list --project-key XCA` | `xr tests list --project UIP` |
| `uip tm testsets list --project-key XCA` | `xr testsets list --project UIP` |
| `uip tm executions list …` | `xr testexecutions list --project UIP` |

## Words

Never say test, set, execution, key or project on its own: say **Xray test** (`UIP-8`, a Jira issue) or **TM test case** (`XCA:13`), **Xray test set** or **TM test set**, **Xray test execution** or **TM test execution**, **Jira project** or **TM project**.

## Preflight

1. `xr --version`. If it is missing, the user installs it once:
   `uv tool install git+https://github.com/reitermayer/xray-tm-sync` (needs `uv`; the repo must be readable for them).
2. `xr login status`. If `Status` is not `Logged in`, ask the user to run `! xr login` themselves.
   - It prompts for the Xray client id and secret (Jira → Apps → Xray → API Keys), verifies them, and stores them in `~/.config/xray-tm-sync/xray.env` (mode 600).
   - CI: `XRAY_CLIENT_ID` / `XRAY_CLIENT_SECRET` as environment variables, or `xr login --client-id <id> --client-secret-stdin`.
   - **Never** ask for the secret in the chat, never put it on a command line, and never print it.

## Commands

| Command | Data |
|---|---|
| `xr project list` | Jira projects. With `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (in the env or the secrets file): all Jira projects. Without: the Jira projects that contain Xray tests, with `XrayTests` counts, because an Xray API key cannot list Jira projects. |
| `xr tests list --project UIP` | Key, Summary, Type, Status, Folder, Labels |
| `xr tests get UIP-8` | the full Xray test: description (plain text), Xray steps, Xray preconditions, Xray test sets, labels |
| `xr tests create --project UIP --summary "…" [--description "…"] [--label L]` | dry run; add `--yes` to create a Manual Xray test |
| `xr tests delete UIP-8` | dry run; add `--yes` to delete |
| `xr testsets list --project UIP` / `get UIP-16` | Key, Summary, number of Xray tests, member keys |
| `xr testexecutions list --project UIP` / `get UIP-17` | Key, Summary, Created, test environments, counts per status; `get` adds every Xray test run |
| `xr testexecutions import results.json` | dry run; add `--yes` to import Xray JSON as a new Xray test execution |
| `xr testplans list --project UIP` | Key, Summary, number of Xray tests |

- `--jql '<JQL>'` replaces `--project` on every `list`. `--limit N` caps the rows (default 100, `0` = all).
- **Output**, as in `uip`: the envelope `{Result, Code, Data}`; on errors `{Result: "Failure", Code, Message}`. `--output table` for people, `--output-filter '<JMESPath>'` to narrow `Data` (e.g. `--output-filter '[].Key'`). These options work anywhere on the line.
- Exit codes: 0 ok; 2 stopped (see `Message`).
- Writes (`create`, `delete`, `import` with `--yes`) leave a command trail in `.xray-tm-sync/evidence/`, named in `Data.Evidence`.

## Rules

1. Every write is a dry run first. Show it, get the user's yes, then repeat with `--yes`.
2. Read with `xr`, not by guessing: quote keys and summaries from `Data`.
3. Prefer `--output-filter` over reading large outputs.
4. For several Xray tests into TM, use xray-tm-sync (`xr sync`), not a loop of single commands.
5. When the user asks for "the commands", give them the exact `xr` lines; they run unchanged in a shell or a pipeline.
