"""xr resource commands: one command per Xray object, in the style of `uip tm`.

xr login | logout | login status
xr project list
xr tests list | get | create | delete
xr testsets list | get
xr testexecutions list | get | import
xr testplans list

Every handler returns (Code, Data) for the uip-style envelope, or raises IntakeError. Writes are dry runs until
--yes, and only writes leave a command trail (.xray-tm-sync/evidence/), the same one `xr sync` writes.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from xr import sync

IntakeError = sync.IntakeError

JIRA_LIST_FIELDS = '["key", "summary", "status", "labels", "project"]'
TEST_ROW_FIELDS = f"issueId testType {{ name }} folder {{ path }} jira(fields: {JIRA_LIST_FIELDS})"
TEST_SET_ROW_FIELDS = f'issueId jira(fields: {JIRA_LIST_FIELDS}) tests(limit: 100) {{ total results {{ jira(fields: ["key"]) }} }}'
TEST_EXECUTION_ROW_FIELDS = (
    'issueId testEnvironments jira(fields: ["key", "summary", "status", "created", "labels"]) '
    'testRuns(limit: 100) { total results { status { name } test { jira(fields: ["key"]) } } }'
)
TEST_PLAN_ROW_FIELDS = f"issueId jira(fields: {JIRA_LIST_FIELDS}) tests(limit: 1) {{ total }}"
ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def client() -> sync.XrayClient:
    return sync.XrayClient()


def jira(raw: dict) -> dict:
    value = raw.get("jira") or {}
    return json.loads(value) if isinstance(value, str) else value


def name_of(value: Any) -> Any:
    return value.get("name") if isinstance(value, dict) else value


def pascal(value: Any) -> Any:
    """snake_case keys → PascalCase, so `xr` output reads like `uip` output."""
    if isinstance(value, dict):
        return {"".join(p[:1].upper() + p[1:] for p in k.split("_")): pascal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [pascal(v) for v in value]
    return value


def project_jql(args: argparse.Namespace, issue_type: str) -> str:
    if getattr(args, "jql", None):
        return args.jql
    if not args.project:
        raise IntakeError("Pass --project <JIRA_PROJECT_KEY> or --jql.")
    return f'project = "{args.project}" AND issuetype = "{issue_type}"'


def take(iterator, limit: int) -> list[dict]:
    rows = []
    for row in iterator:
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    return rows


def require_key(key: str) -> str:
    if not ISSUE_KEY.match(key):
        raise IntakeError(f"'{key}' is not a Jira issue key like UIP-12.")
    return key


def find_one(xray: sync.XrayClient, getter: str, key: str, fields: str) -> dict:
    rows = take(xray.iter_issues(getter, f'key = "{require_key(key)}"', fields), 1)
    if not rows:
        raise IntakeError(f"{key} not found by {getter}. Check the key and that it is that kind of Xray issue.")
    return rows[0]


# --------------------------------------------------------------------------- login


def secrets_file() -> Path:
    return sync.xray_secrets_file()


def cmd_login(args: argparse.Namespace) -> tuple[str, Any]:
    if args.action == "status":
        path = secrets_file()
        sync.load_xray_secrets()
        client_id = os.environ.get("XRAY_CLIENT_ID", "")
        status: dict[str, Any] = {
            "SecretsFile": str(path),
            "SecretsFilePresent": path.is_file(),
            "ClientId": f"{client_id[:4]}…{client_id[-4:]}" if len(client_id) > 8 else ("set" if client_id else None),
            "BaseUrl": sync.xray_base_url(),
            "JiraBaseUrl": os.environ.get("JIRA_BASE_URL") or None,
        }
        try:
            client().authenticate()
            status["Status"] = "Logged in"
        except IntakeError as exc:
            status["Status"] = "Logged out"
            status["Problem"] = str(exc)
        return "LoginStatus", status

    client_id = args.client_id or input("Xray client id: ").strip()
    secret = sys.stdin.readline().strip() if args.client_secret_stdin else (
        os.environ.get("XRAY_CLIENT_SECRET") or getpass.getpass("Xray client secret: ").strip())
    if not client_id or not secret:
        raise IntakeError("Both the Xray client id and the client secret are needed (Jira → Apps → Xray → API Keys).")
    os.environ.update({"XRAY_CLIENT_ID": client_id, "XRAY_CLIENT_SECRET": secret})
    if args.region:
        os.environ["XRAY_REGION"] = args.region
    client().authenticate()  # verify before anything is written
    path = secrets_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = [f"XRAY_CLIENT_ID={client_id}", f"XRAY_CLIENT_SECRET={secret}"]
    if args.region:
        lines.append(f"XRAY_REGION={args.region}")
    path.touch(mode=0o600)
    path.chmod(0o600)
    path.write_text("\n".join(lines) + "\n")
    return "Login", {"Status": "Logged in", "SecretsFile": str(path), "BaseUrl": sync.xray_base_url()}


def cmd_logout(args: argparse.Namespace) -> tuple[str, Any]:
    path = secrets_file()
    existed = path.is_file()
    if existed:
        path.unlink()
    return "Logout", {"SecretsFile": str(path), "Removed": existed}


# --------------------------------------------------------------------------- project


def cmd_project(args: argparse.Namespace) -> tuple[str, Any]:
    sync.load_xray_secrets()
    base, email, token = (os.environ.get(n, "").strip() for n in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))
    if base and email and token:
        import httpx

        rows, start = [], 0
        with httpx.Client(timeout=60, auth=(email, token)) as http:
            while True:
                r = http.get(f"{base.rstrip('/')}/rest/api/3/project/search", params={"startAt": start, "maxResults": 50})
                if r.status_code != 200:
                    raise IntakeError(f"Jira project search HTTP {r.status_code}: {r.text[:200]}")
                page = r.json()
                rows += [{"Key": p["key"], "Name": p["name"], "Id": p["id"]} for p in page.get("values") or []]
                start += len(page.get("values") or [])
                if page.get("isLast", True) or not page.get("values"):
                    break
        return "ProjectList", rows
    # Without Jira credentials: the Jira projects that contain Xray tests (the Xray API key can't list projects).
    counts: dict[str, dict] = {}
    for raw in take(client().iter_issues("getTests", args.jql, 'issueId jira(fields: ["project"])'), args.max_tests):
        project = jira(raw).get("project") or {}
        row = counts.setdefault(project.get("key", "?"), {"Key": project.get("key"), "Name": project.get("name"),
                                                          "Id": project.get("id"), "XrayTests": 0})
        row["XrayTests"] += 1
    return "ProjectList", sorted(counts.values(), key=lambda r: r["Key"] or "")


# --------------------------------------------------------------------------- tests


def test_row(raw: dict) -> dict:
    j = jira(raw)
    return {"Key": j.get("key"), "Summary": j.get("summary"), "Type": name_of(raw.get("testType")),
            "Status": name_of(j.get("status")), "Folder": (raw.get("folder") or {}).get("path"),
            "Labels": j.get("labels") or [], "IssueId": raw.get("issueId")}


def cmd_tests(args: argparse.Namespace) -> tuple[str, Any]:
    xray = client()
    if args.action == "list":
        rows = take(xray.iter_issues("getTests", project_jql(args, "Test"), TEST_ROW_FIELDS), args.limit)
        return "TestsList", [test_row(r) for r in rows]
    if args.action == "get":
        raws = take(xray.iter_tests(f'key = "{require_key(args.key)}"', None, expand=True), 1)
        if not raws:
            raise IntakeError(f"Xray test {args.key} not found.")
        return "TestGet", pascal(sync.normalize_xray_test(raws[0]))
    if args.action == "create":
        if not (args.project and args.summary):
            raise IntakeError("xr tests create needs --project and --summary.")
        request = {"Project": args.project, "Summary": args.summary, "Type": "Manual",
                   "Description": args.description or "", "Labels": args.label or []}
        if not args.yes:
            return "TestCreate", {"DryRun": True, "WouldCreate": request}
        start_write_trail(args, "tests-create")
        created = xray.create_test(args.project, args.summary, args.description or "", args.label or [])
        return "TestCreate", pascal(created)
    if args.action == "delete":
        row = test_row(find_one(xray, "getTests", args.key, TEST_ROW_FIELDS))
        if not args.yes:
            return "TestDelete", {"DryRun": True, "WouldDelete": row}
        start_write_trail(args, "tests-delete")
        xray.delete("deleteTest", row["IssueId"])
        return "TestDelete", {"Deleted": row}
    raise IntakeError(f"unknown action {args.action}")


# --------------------------------------------------------------------------- test sets, executions, plans


def test_set_row(raw: dict) -> dict:
    j = jira(raw)
    tests = raw.get("tests") or {}
    return {"Key": j.get("key"), "Summary": j.get("summary"), "Status": name_of(j.get("status")),
            "Tests": tests.get("total"), "TestKeys": [jira(t).get("key") for t in tests.get("results") or []],
            "IssueId": raw.get("issueId")}


def cmd_testsets(args: argparse.Namespace) -> tuple[str, Any]:
    xray = client()
    if args.action == "list":
        rows = take(xray.iter_issues("getTestSets", project_jql(args, "Test Set"), TEST_SET_ROW_FIELDS), args.limit)
        return "TestSetsList", [test_set_row(r) for r in rows]
    return "TestSetGet", test_set_row(find_one(xray, "getTestSets", args.key, TEST_SET_ROW_FIELDS))


def test_execution_row(raw: dict) -> dict:
    j = jira(raw)
    runs = (raw.get("testRuns") or {}).get("results") or []
    statuses: dict[str, int] = {}
    for run in runs:
        name = name_of(run.get("status")) or "?"
        statuses[name] = statuses.get(name, 0) + 1
    return {"Key": j.get("key"), "Summary": j.get("summary"), "Created": j.get("created"),
            "TestEnvironments": raw.get("testEnvironments") or [], "Runs": (raw.get("testRuns") or {}).get("total"),
            "Statuses": statuses, "IssueId": raw.get("issueId")}


def cmd_testexecutions(args: argparse.Namespace) -> tuple[str, Any]:
    if args.action == "import":
        payload = json.loads(Path(args.file).read_text())
        tests = payload.get("tests") or []
        summary = {"Summary": (payload.get("info") or {}).get("summary"), "Project": (payload.get("info") or {}).get("project"),
                   "Tests": len(tests), "TestKeys": [t.get("testKey") for t in tests]}
        if not args.yes:
            return "TestExecutionImport", {"DryRun": True, "WouldImport": summary}
        start_write_trail(args, "testexecutions-import")
        return "TestExecutionImport", pascal(client().import_execution(payload)) | {"Imported": summary}
    xray = client()
    if args.action == "list":
        rows = take(xray.iter_issues("getTestExecutions", project_jql(args, "Test Execution"),
                                     TEST_EXECUTION_ROW_FIELDS), args.limit)
        return "TestExecutionsList", [test_execution_row(r) for r in rows]
    raw = find_one(xray, "getTestExecutions", args.key, TEST_EXECUTION_ROW_FIELDS)
    row = test_execution_row(raw)
    row["TestRuns"] = [{"TestKey": jira(r.get("test") or {}).get("key"), "Status": name_of(r.get("status"))}
                       for r in (raw.get("testRuns") or {}).get("results") or []]
    return "TestExecutionGet", row


def cmd_testplans(args: argparse.Namespace) -> tuple[str, Any]:
    rows = take(client().iter_issues("getTestPlans", project_jql(args, "Test Plan"), TEST_PLAN_ROW_FIELDS), args.limit)
    return "TestPlansList", [{"Key": jira(r).get("key"), "Summary": jira(r).get("summary"),
                              "Status": name_of(jira(r).get("status")), "Tests": (r.get("tests") or {}).get("total"),
                              "IssueId": r.get("issueId")} for r in rows]


def start_write_trail(args: argparse.Namespace, name: str) -> None:
    sync.start_trail(Path(args.workdir), "xr", name, args.argv)


# --------------------------------------------------------------------------- parser


def add_parsers(sub: argparse._SubParsersAction) -> dict[str, Any]:
    def listing(c: argparse.ArgumentParser) -> None:
        c.add_argument("--project", metavar="JIRA_PROJECT_KEY")
        c.add_argument("--jql", help="instead of --project, e.g. 'project = UIP AND labels = smoke'")
        c.add_argument("--limit", type=int, default=100, help="at most this many rows (0 = all; default 100)")

    def write(c: argparse.ArgumentParser) -> None:
        c.add_argument("--yes", action="store_true", help="really write (otherwise a dry run)")
        c.add_argument("--workdir", default=".xray-tm-sync", help="where the command trail goes")

    c = sub.add_parser("login", help="store and verify the Xray API key (or: xr login status)")
    c.add_argument("action", nargs="?", choices=["status"], help="status: show who is logged in")
    c.add_argument("--client-id")
    c.add_argument("--client-secret-stdin", action="store_true", help="read the secret from stdin (CI)")
    c.add_argument("--region", choices=["global", "us", "eu", "au"])

    sub.add_parser("logout", help="remove the stored Xray API key")

    c = sub.add_parser("project", help="Jira projects (all, with JIRA_* credentials; otherwise those with Xray tests)")
    c.add_argument("action", choices=["list"])
    c.add_argument("--jql", default='issuetype = "Test"', help=argparse.SUPPRESS)
    c.add_argument("--max-tests", type=int, default=2000, help="Xray-only mode: scan at most this many Xray tests")

    c = sub.add_parser("tests", help="Xray tests: list | get | create | delete")
    c.add_argument("action", choices=["list", "get", "create", "delete"])
    c.add_argument("key", nargs="?", metavar="XRAY_KEY", help="for get / delete")
    listing(c)
    c.add_argument("--summary")
    c.add_argument("--description")
    c.add_argument("--label", action="append", metavar="JIRA_LABEL")
    write(c)

    c = sub.add_parser("testsets", help="Xray test sets: list | get")
    c.add_argument("action", choices=["list", "get"])
    c.add_argument("key", nargs="?", metavar="XRAY_KEY")
    listing(c)

    c = sub.add_parser("testexecutions", help="Xray test executions: list | get | import")
    c.add_argument("action", choices=["list", "get", "import"])
    c.add_argument("key", nargs="?", metavar="XRAY_KEY | FILE", help="XRAY_KEY for get, an Xray JSON file for import")
    listing(c)
    write(c)

    c = sub.add_parser("testplans", help="Xray test plans: list")
    c.add_argument("action", choices=["list"])
    listing(c)

    return {"login": cmd_login, "logout": cmd_logout, "project": cmd_project, "tests": cmd_tests,
            "testsets": cmd_testsets, "testexecutions": cmd_testexecutions, "testplans": cmd_testplans}


def check_args(args: argparse.Namespace) -> None:
    needs_key = {"tests": ("get", "delete"), "testsets": ("get",), "testexecutions": ("get", "import")}
    if args.action in needs_key.get(args.group, ()) and not args.key:
        raise IntakeError(f"xr {args.group} {args.action} needs a {'file' if args.action == 'import' else 'Xray key'}.")
    if args.group == "testexecutions" and args.action == "import":
        args.file = args.key
