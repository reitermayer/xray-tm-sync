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

On the **first** sync, the skill creates two custom fields in your TM project, `Xray Key` and `Xray Intake`. Your TM role therefore needs permission to manage custom fields in that project. A project administrator has this permission.

### Tools on your machine

| Tool | Install | Check |
|---|---|---|
| **Node.js** (LTS) | [nodejs.org](https://nodejs.org) or `brew install node` | `node --version` |
| **uv** (Python runner) | `brew install uv` or [docs.astral.sh/uv](https://docs.astral.sh/uv/) | `uv --version` |
| **git** | preinstalled on macOS, or [git-scm.com](https://git-scm.com) | `git --version` |
| **UiPath CLI `uip`** | `npm install -g @uipath/cli` | `uip --version` (this guide was tested with 1.203) |

You don't need to install Python yourself, because uv fetches it when it is needed.

---

## 2. Log in to UiPath with `uip` (OAuth)

```bash
uip login --interactive        # opens the browser, then pick your tenant
uip login status               # shows "Logged in", your organization and your tenant
```

- `uip` stores the OAuth tokens and refreshes them on its own. The skill never sees a UiPath password.
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

Put the key in a file that only you can read:

```bash
mkdir -p ~/.config/xray-tm-sync && chmod 700 ~/.config/xray-tm-sync
cat > ~/.config/xray-tm-sync/xray.env <<'EOF'
XRAY_CLIENT_ID=paste-client-id-here
XRAY_CLIENT_SECRET=paste-client-secret-here
# XRAY_REGION=eu                                  # optional: global (default), us, eu, au
# XRAY_JIRA_BASE_URL=https://yoursite.atlassian.net  # optional: links in TM descriptions
EOF
chmod 600 ~/.config/xray-tm-sync/xray.env
```

- The script refuses to run if other users can read this file.
- Environment variables with the same names override the file. That is the way to go in CI, where the pipeline's secret store sets them.
- If you need a different location, set `XRAY_TM_SYNC_ENV=/path/to/file`.
- **Never** put the key into the repository or a prompt. If you paste it into a chat with an agent, it is in that transcript, so rotate it in Jira.

---

## 4. Install the skills

```bash
git clone https://github.com/reitermayer/xray-tm-sync.git
cd xray-tm-sync
mkdir -p ~/.claude/skills
cp -R xray-tm-sync xray-tm-demo ~/.claude/skills/      # demo is optional
```

`xray-tm-demo` loads `xray-tm-sync` from the folder next to it, so install both into the same folder.

---

## 5. Check the setup

Run this in a working folder of your choice. The skill writes its local state into `.xray-tm-sync/` there.

```bash
S=~/.claude/skills/xray-tm-sync/scripts/xray_tm_sync.py
$S check --tm-project SHOP
```

Expected output: `"ok": true`, your UiPath tenant, and `"xray": {"authenticated": true}`.

| Problem shown | Fix |
|---|---|
| `uip` not logged in | `uip login --interactive` |
| TM project not found | check the TM project key, and that you logged in to the right tenant |
| `XRAY_CLIENT_ID / XRAY_CLIENT_SECRET are not set` | check `~/.config/xray-tm-sync/xray.env` |
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

### Without an agent (same scripts, no AI tokens)

```bash
$S pull     --jql 'project = SHOP AND issuetype = Test'
$S plan     --tm-project SHOP                 # dry run: writes nothing to TM
$S apply    --yes                             # creates and updates TM test cases
$S testsets --tm-project SHOP --yes           # mirrors Xray test sets as TM test sets
$S plan     --tm-project SHOP                 # run it again: everything is "unchanged"
```

Then check it yourself, without the skill:

```bash
uip tm testcases list --project-key SHOP --output json
```

---

## 7. Optional: the demo sandbox

The demo skill can **delete** things: Xray tests, TM test cases, TM test sets and TM test executions. Use it only with a Jira project and a TM project that exist just for the demo. It refuses to run against any pair you haven't registered.

```bash
D=~/.claude/skills/xray-tm-demo/scripts/xray_tm_demo.py
$D sandbox add --xray-project DEMO --tm-project DEMO
$D reset --xray-project DEMO --tm-project DEMO --reseed          # dry run
$D reset --xray-project DEMO --tm-project DEMO --reseed --yes    # 8 stock-trading Xray tests
```

---

## Where things are stored

| What | Location | Secret? | Who manages it |
|---|---|---|---|
| Xray Client ID and Client Secret | `~/.config/xray-tm-sync/xray.env` (mode 600), or environment variables | **yes** | you |
| Xray access token (JWT, valid 24 h) | in memory only, for one run | yes | the script |
| UiPath OAuth tokens | `~/.uipath/.auth` (`uip login which` shows the file in use) | **yes** | `uip` |
| Demo sandbox list | `~/.config/xray-tm-demo/sandboxes.json` | no | `xray-tm-demo sandbox` |
| Xray snapshot, plan, local index, reports | `.xray-tm-sync/` in your working folder | no, but it contains your test content | the script; it is in `.gitignore` |
| Run history | `~/.claude/logs/xray-tm-sync/history.log` | no | the script |
| Origin markers | TM custom fields `Xray Key` and `Xray Intake` on each TM test case and TM test set | no | the script |

## Uninstall

```bash
rm -rf ~/.claude/skills/xray-tm-sync ~/.claude/skills/xray-tm-demo
rm -rf ~/.config/xray-tm-sync ~/.config/xray-tm-demo     # removes the Xray secret
uip logout
```

Then revoke the API key in Jira under **Apps → Xray → API Keys**.
