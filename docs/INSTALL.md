# Installation and first run

This guide takes you from zero to a first sync from Xray Cloud to UiPath Test Manager (TM). Claude Code acts as the coding agent.

Everyone uses **their own accounts**. That is the same position a corporate customer is in: your own Jira with Xray Cloud, your own UiPath tenant with TM, and your own secrets. Nothing in this repository belongs to a shared tenant.

Time needed: about 45 minutes. Most of it is creating the accounts and the API key.

---

## 1. Prerequisites

### Accounts

| What | Where to get it | You need |
|---|---|---|
| **Jira Cloud with Xray Cloud** | [atlassian.com](https://www.atlassian.com/software/jira) (free plan), then install *Xray Test Management* from the Atlassian Marketplace (free trial) | Permission to create issues in your Jira project |
| **UiPath Automation Cloud with Test Manager** | [cloud.uipath.com](https://cloud.uipath.com) (Community or trial) | Access to Test Manager in your tenant |
| **Claude Code** | [claude.com/claude-code](https://claude.com/claude-code) | A Claude subscription or API key |

### Projects you create yourself

| Project | Example key | Notes |
|---|---|---|
| **Jira project** with Xray enabled | `SHOP` | It must offer the Xray issue types *Test* and *Test Set*. A project created after installing Xray usually has them. |
| **TM project** | `SHOP` | Create it in Test Manager → *Projects* → *Create*. The TM project key and the Jira project key can be the same text, but they are different objects. |

On the **first** sync, `xr sync` creates two custom fields in your TM project, `Xray Key` and `Xray Intake`. Your TM role therefore needs permission to manage custom fields in that project. A project administrator has this permission.

### Tools on your machine

| Tool | Install | Check |
|---|---|---|
| **Node.js** (LTS) | [nodejs.org](https://nodejs.org), `brew install node` (macOS) or `winget install OpenJS.NodeJS.LTS` (Windows) | `node --version` |
| **uv** (installs `xr`) | `brew install uv` (macOS), `winget install astral-sh.uv` (Windows) or [docs.astral.sh/uv](https://docs.astral.sh/uv/) | `uv --version` |
| **git** | preinstalled on macOS, `winget install Git.Git` (Windows) or [git-scm.com](https://git-scm.com) | `git --version` |
| **UiPath CLI `uip`** | `npm install -g @uipath/cli` | `uip --version` (this guide was tested with 1.203) |

You don't need to install Python yourself, because uv fetches it when it is needed.

---

## 2. Log in to UiPath with `uip` (OAuth)

```bash
uip login --authority https://cloud.uipath.com --organization <org> --tenant <tenant>
uip login status               # shows "Logged in", your organization and your tenant
```

For example, on the UiPath staging cloud:

```bash
uip login --authority https://staging.uipath.com --organization <organization> --tenant <tenant>
```

- `uip` stores the OAuth tokens and refreshes them on its own. Neither `xr` nor the skills ever see a UiPath password.
- In CI pipelines, use an External Application instead:

  ```bash
  uip login --client-id env.UIP_CLIENT_ID --client-secret env.UIP_CLIENT_SECRET -t <tenant>
  ```

  The `env.` prefix makes `uip` read the value from an environment variable, so the secret never appears in the command line.

### UiPath skills for your coding agent

These are UiPath's own skills. They teach Claude Code the `uip` commands, including `uip tm`.

```bash
uip skills install --agent claude
```

---

## 3. Create the Xray API key

Xray Cloud has no OAuth. It uses an API key made of a **Client ID** and a **Client Secret**.

1. In Jira, open **Apps → Xray → API Keys** (or *Settings → Apps → Xray → API Keys*). Only Jira admins see this page.
2. Click **Create API Key** and pick the Jira user the key acts as. The key has that user's permissions:
   - **Read Xray tests** (sync) needs *Browse projects*.
   - **Send results back** (`report`) also needs *Create issues*, because Xray creates a *Test Execution* issue.
   - **Demo reset and seed** also needs *Delete issues*.
3. Copy the Client ID and the Client Secret. The secret is shown only once.

Corporate tip: give the production sync a key whose user can only browse. Give `report` and the demo a separate key.

### Store the Xray secret

Install `xr` first (step 4), then:

```bash
xr login              # prompts for the Client ID and the Client Secret, verifies them, stores them
xr login status       # "Status": "Logged in"
```

- `xr login` writes `~/.config/xray-tm-sync/xray.env` with mode 600 and refuses to use it if other users can read it. On Windows the file is `%USERPROFILE%\.config\xray-tm-sync\xray.env`; Windows has no mode bits, so `xr` skips that check there and the folder's normal user permissions protect the file.
- The file is plain `KEY=value` lines. You normally never write it by hand, but this is what `xr login` produces, and what you create yourself (then `chmod 600`) if you can't run `xr login`:

  ```
  XRAY_CLIENT_ID=<client id>
  XRAY_CLIENT_SECRET=<client secret>
  # XRAY_REGION=eu
  # XRAY_JIRA_BASE_URL=https://yoursite.atlassian.net
  ```

- Optional lines: `XRAY_REGION` (global is the default; us, eu, au) picks the Xray host (`eu.xray.cloud.getxray.app`, and so on), so set it to the region your Xray app runs in. `XRAY_JIRA_BASE_URL` makes links in TM descriptions. `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` let `xr project list` list every Jira project (an Xray API key can't).
- Environment variables with the same names override the file. That is the way to go in CI, where the pipeline's secret store sets them, or use `xr login --client-id <id> --client-secret-stdin`.
- If you need a different location, set `XRAY_TM_SYNC_ENV=/path/to/file`.
- **Never** put the key into the repository, a command line or a prompt. If you paste it into a chat with an agent, it is in that transcript, so rotate it in Jira.

---

## 4. Install `xr` and the skills

One codebase serves both ways of working: the `xr` command for people and scripts, and three skills that teach a coding agent to run the very same `xr` commands. The skills contain no code.

```bash
uv tool install git+https://github.com/reitermayer/xray-tm-sync    # puts `xr` on your PATH
xr --version

# the skills, for the coding agent you use (the same installer as UiPath's own skills):
uip skills install --repo reitermayer/xray-tm-sync --agent claude
```

`--agent` takes any coding agent `uip skills` supports: `claude`, `cursor`, `copilot`, `codex`, `opencode`, `autopilot`, `kiro`, `windsurf`, `antigravity`, `devin`. Add `--local` to install into the current project only (or `--path <dir>` for another project folder). Without `uip`: copy the folders under `skills/` into your agent's skills folder (for Claude Code `~/.claude/skills/`).

**Project-only install.** Run this inside your project folder:

```bash
uip skills install --repo reitermayer/xray-tm-sync --agent claude --local
```

For Claude Code it puts the three skills in `<project>/.claude/skills/{xray,xray-tm-sync,xray-tm-demo}`, so they apply only in that project. `uip` also keeps a cached copy of the repository under `<project>/.uipath/.skill-repos/` and adds that folder to the project's `.gitignore`. Commit `.claude/skills/` if you want teammates to get the skills with the project; leave `.uipath/` out.

What stays outside the project, whichever way you install the skills:

- `xr` itself is installed once with `uv tool install` and must be on your PATH. The skills only teach the agent to call it.
- The Xray key stays in `~/.config/xray-tm-sync/xray.env` (or environment variables), so a project install never carries credentials.
- The sync's own state goes to `.xray-tm-sync/` in the folder where you run `xr`.

| Skill | Teaches the agent |
|---|---|
| `xray` | `xr project`, `xr tests`, `xr testsets`, `xr testexecutions`, `xr testplans`, `xr login` |
| `xray-tm-sync` | `xr sync`: Xray tests → TM test cases, Xray test sets → TM test sets, TM results → Xray |
| `xray-tm-demo` | `xr demo`: seed, mock run and reset in a registered sandbox |

Update later with `uv tool upgrade xr`.

**Windows.** Everything above works in PowerShell. Open a new terminal after installing Node.js, uv and git so they are on the PATH, and after `uv tool install` run `uv tool update-shell` once if `xr` isn't found. `~` in this guide means `%USERPROFILE%`. The demo recording scripts under `docs/demo/*.sh` need Git Bash or WSL; `xr` itself doesn't.

**Working inside a clone of this repository.** The repository ignores its own `.claude/` folder, so it contains no ready-made skill setup. Install the skills as above (`--local` for this clone, or globally). If you see a `.claude/settings.local.json` in your clone, it is your own Claude Code setting for that folder and is never committed. The maintainer's, for example, holds `{"skillOverrides": {"xray": "name-only"}}`, which makes Claude Code list the `xray` skill by name only, without its description. It is optional and has nothing to do with `xr` or the sync.

---

## 5. Check the setup

Run this in a working folder of your choice. The skill writes its local state into `.xray-tm-sync/` there.

```bash
xr login status
xr tests list --project SHOP --output table      # your Xray tests, straight from Xray
xr sync check --tm-project SHOP
```

Expected output of `xr sync check`: `"Result": "Success"` with `"ok": true`, your UiPath tenant, and `"xray": {"authenticated": true}`.

| Problem shown | Fix |
|---|---|
| `uip` not logged in | `uip login --authority <host> --organization <org> --tenant <tenant>` (see section 2) |
| TM project not found | check the TM project key, and that you logged in to the right tenant |
| `XRAY_CLIENT_ID / XRAY_CLIENT_SECRET are not set` | `xr login` |
| `readable by other users` | `chmod 600 ~/.config/xray-tm-sync/xray.env` |
| `Xray authentication failed: HTTP 401` | the key is wrong or was revoked. Check `XRAY_REGION` too. |

---

## 6. First sync: dry run first

### With Claude Code

Start `claude` in your working folder and say:

> Retrieve all Xray tests from Jira project SHOP and sync them to TM project SHOP. Show me the plan first.

Claude runs `check`, `pull` and `plan`, shows a table of what it would create or update, and waits. Answer *yes* to apply. Then ask:

> Mirror the Xray test sets as TM test sets.
>
> Give me the commands so our pipeline can run this without you.

### Without an agent (same commands, no AI tokens)

```bash
xr sync pull     --jql 'project = SHOP AND issuetype = Test'
xr sync plan     --tm-project SHOP            # dry run: writes nothing to TM
xr sync apply    --yes                        # creates and updates TM test cases
xr sync testsets --tm-project SHOP --yes      # mirrors Xray test sets as TM test sets
xr sync plan     --tm-project SHOP --output-filter summary   # run it again: everything is "unchanged"
```

Then check both sides yourself, with the two CLIs:

```bash
xr  tests list     --project SHOP     --output table
uip tm testcases list --project-key SHOP --output table
```

---

## 7. Optional: the demo sandbox

The demo skill can **delete** things: Xray tests, TM test cases, TM test sets and TM test executions. Use it only with a Jira project and a TM project that exist just for the demo. It refuses to run against any pair you haven't registered.

```bash
xr demo sandbox add --xray-project DEMO --tm-project DEMO
xr demo reset --xray-project DEMO --tm-project DEMO --reseed          # dry run
xr demo reset --xray-project DEMO --tm-project DEMO --reseed --yes    # 8 stock-trading Xray tests
```

`sandbox add` creates `~/.config/xray-tm-demo/sandboxes.json` (and the folder) on first use. The file is a list of registered pairs:

```json
[
  {"xray_project": "DEMO", "tm_project": "DEMO"}
]
```

`xr demo sandbox` lists the pairs, `xr demo sandbox remove --xray-project ... --tm-project ...` unregisters one, and `XRAY_TM_DEMO_SANDBOXES=/path/to/file` uses another location. If you run `xr demo` before registering, it stops with `... is not a registered demo sandbox`. That is the guard working. Register the pair, but only if both projects really are sandboxes. The sync and `report` commands never need this file.

---

## Where things are stored

| What | Location | Secret? | Who manages it |
|---|---|---|---|
| Xray Client ID and Client Secret (optional Jira API token) | `~/.config/xray-tm-sync/xray.env` (mode 600), or environment variables | **yes** | `xr login` / you |
| Xray access token (JWT, valid 24 h) | in memory only, for one run | yes | `xr` |
| UiPath OAuth tokens | `~/.uipath/.auth` (`uip login which` shows the file in use) | **yes** | `uip` |
| Demo sandbox list | `~/.config/xray-tm-demo/sandboxes.json` | no | `xr demo sandbox` |
| Xray snapshot, plan, local index, reports | `.xray-tm-sync/` in your working folder | no, but it contains your test content | `xr`; add it to `.gitignore` |
| Run history | `~/.claude/logs/xray-tm-sync/history.log` | no | `xr sync` |
| Origin markers | TM custom fields `Xray Key` and `Xray Intake` on each TM test case and TM test set | no | `xr sync` |

## Uninstall

```bash
xr logout                                                # removes the Xray secret
uv tool uninstall xr
uip skills uninstall --repo reitermayer/xray-tm-sync --agent claude   # only these skills, not UiPath's
rm -rf ~/.config/xray-tm-sync ~/.config/xray-tm-demo
uip logout
```

Then revoke the API key in Jira under **Apps → Xray → API Keys**.
