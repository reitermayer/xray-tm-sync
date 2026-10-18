"""xr sync: Xray Cloud → UiPath Test Manager (TM), production sync.

Intake of Xray tests as TM test cases, Xray test sets as TM test sets, and results of real TM test
executions back to Xray as Xray test executions. Never deletes TM test cases, TM test sets or anything in Xray.

Vocabulary (see references/terminology.md): an *Xray test* (key CALC-123) becomes a
*TM test case* (key CALC:12). Code uses xray_* / tm_* prefixes; bare "test", "key",
"id", "folder", "step" are avoided on purpose.

Xray: Cloud GraphQL (read) and the Xray JSON results import (`report --yes`), authenticated with
XRAY_CLIENT_ID / XRAY_CLIENT_SECRET. TM: only the `uip tm` CLI with the user's `uip login` session.

Subcommands: check | pull | plan | apply | testsets | report | status
The CLI is `xr sync …` (xr/cli.py); the skills xray-tm-sync and xray-tm-demo only document `xr` commands.
`xr demo` (xr/demo.py) imports this module and adds seed, mock execution and reset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# --------------------------------------------------------------------------- constants

# Origin marker on the TM test case. XRAY_ID_FIELD is the same override the Avalon Test Manager MCP server
# reads, so both can share one marker (e.g. XRAY_ID_FIELD=XRAY_ID).
TM_FIELD_XRAY_KEY = os.environ.get("XRAY_ID_FIELD", "Xray Key")
TM_FIELD_INTAKE = "Xray Intake"  # JSON: lastModified, xrayHash, tmHash
LABEL_PREFIX = "XR_"
TM_NAME_MAX = 250
XRAY_PAGE = 100  # GraphQL connection limit (max 100)
XRAY_PRECONDITION_PAGE = 10  # nested items per page: 100 × (10 + 20) stays far below the 10k cap
XRAY_TEST_SET_PAGE = 20
MOCK_TAG = "[MOCK]"  # written by xray-tm-demo; production `report` refuses such TM test executions
TM_TO_XRAY_STATUS = {"Passed": "PASSED", "Failed": "FAILED", "None": "TODO", "Restricted": "ABORTED"}
JIRA_FIELDS = ["key", "summary", "description", "labels", "status", "updated"]
WORKERS = int(os.environ.get("XRAY_TM_WORKERS", "4"))
RETRIES = 3
SETTLE_SECONDS = float(os.environ.get("XRAY_TM_SETTLE_SECONDS", "0.3"))  # TM custom field provisioning race
REPORT_MARKER = "reported by xray-tm-sync"  # in the description of every Xray test execution `report` creates

XRAY_STEP_FIELDS = "id action data result"
XRAY_TEST_FIELDS = f"""
  issueId projectId lastModified
  testType {{ name kind }}
  folder {{ path }}
  gherkin unstructured
  preconditions(limit: {XRAY_PRECONDITION_PAGE}) {{
    total
    results {{ issueId definition preconditionType {{ name kind }} jira(fields: ["key", "summary"]) }}
  }}
  testSets(limit: {XRAY_TEST_SET_PAGE}) {{ total results {{ issueId jira(fields: ["key", "summary"]) }} }}
  jira(fields: {json.dumps(JIRA_FIELDS)})
"""
XRAY_QUERY_EXPANDED = f"""
query($jql: String, $since: String, $limit: Int!, $start: Int) {{
  page: getExpandedTests(jql: $jql, modifiedSince: $since, limit: $limit, start: $start) {{
    total start limit
    results {{ {XRAY_TEST_FIELDS} steps {{ {XRAY_STEP_FIELDS} calledTestIssueId }} warnings }}
  }}
}}"""
XRAY_QUERY_PLAIN = f"""
query($jql: String, $since: String, $limit: Int!, $start: Int) {{
  page: getTests(jql: $jql, modifiedSince: $since, limit: $limit, start: $start) {{
    total start limit
    results {{ {XRAY_TEST_FIELDS} steps {{ {XRAY_STEP_FIELDS} }} }}
  }}
}}"""


class IntakeError(Exception):
    """A failure the agent must report to the user instead of working around."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def local_timestamp() -> str:
    # User convention: timestamps come from this exact command.
    return subprocess.run(["date", "+%d.%m.%Y %H:%M"], capture_output=True, text=True).stdout.strip()


# --------------------------------------------------------------------------- command trail
# Every uip call and Xray request of a run goes to <workdir>/evidence/<run>.jsonl, and the uip calls also go
# to <run>.sh. The trail is the evidence that the agent only ran ordinary, repeatable commands. It never holds
# secrets: Xray requests are recorded by operation name and variables, without headers or tokens.

_TRAIL: dict[str, Path] = {}


def start_trail(workdir: Path, tool: str, subcommand: str, argv: list[str]) -> None:
    folder = workdir / "evidence"
    folder.mkdir(parents=True, exist_ok=True)
    run = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{subcommand}"
    n = 1
    while (folder / f"{run}.jsonl").exists():
        n += 1
        run = f"{run.rsplit('~', 1)[0]}~{n}"
    _TRAIL["jsonl"], _TRAIL["sh"] = folder / f"{run}.jsonl", folder / f"{run}.sh"
    rerun = shlex.join([*tool.split(), *argv])
    _TRAIL["sh"].write_text(
        "#!/bin/sh\n"
        f"# uip calls made by: {rerun}  ({local_timestamp()})\n"
        "# This is an audit record. To repeat the run, call the line above again: it is idempotent and\n"
        "# plans before it writes, whereas replaying these lines blindly would create duplicates.\n"
    )
    trail({"tool": tool, "argv": argv, "time": local_timestamp()})


def trail(entry: dict) -> None:
    if "jsonl" not in _TRAIL:
        return
    with _TRAIL["jsonl"].open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def trail_uip(cmd: list[str], ok: bool, ms: int) -> None:
    trail({"tool": "uip", "argv": cmd[1:], "ok": ok, "ms": ms})
    if "sh" in _TRAIL:
        with _TRAIL["sh"].open("a") as f:
            f.write(("" if ok else "# failed: ") + shlex.join(["uip", *cmd[1:]]) + "\n")


def trail_paths() -> dict[str, str]:
    return {k: str(v) for k, v in _TRAIL.items()}


def short_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def norm_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):  # ExpandedStep fields may come back as lists
        value = "\n".join(str(v) for v in value if v)
    return str(value).replace("\r\n", "\n").strip()


# --------------------------------------------------------------------------- Xray source


def xray_base_url() -> str:
    region = os.environ.get("XRAY_REGION", "").strip().lower()
    host = f"{region}.xray.cloud.getxray.app" if region and region != "global" else "xray.cloud.getxray.app"
    return f"https://{host}"


def xray_secrets_file() -> Path:
    return Path(os.environ.get("XRAY_TM_SYNC_ENV", Path.home() / ".config" / "xray-tm-sync" / "xray.env")).expanduser()


def load_xray_secrets() -> None:
    """Fill missing XRAY_* / JIRA_* variables from the secrets file. Environment variables always win."""
    path = xray_secrets_file()
    if not path.is_file():
        return
    if path.stat().st_mode & 0o077:
        raise IntakeError(f"{path} is readable by other users. Run: chmod 600 '{path}'")
    for line in path.read_text().splitlines():
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        name = name.strip()
        if sep and name.startswith(("XRAY_", "JIRA_")) and not os.environ.get(name):
            os.environ[name] = value.strip().strip("'\"")


class XrayClient:
    """Xray Cloud API v2: client-credential JWT (24 h, memory only) + paged GraphQL."""

    def __init__(self, transport: Any = None) -> None:
        import httpx  # imported lazily so offline subcommands don't need it

        load_xray_secrets()
        self.client_id = os.environ.get("XRAY_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("XRAY_CLIENT_SECRET", "").strip()
        if not self.client_id or not self.client_secret:
            raise IntakeError(
                "XRAY_CLIENT_ID / XRAY_CLIENT_SECRET are not set. Create an API key in "
                f"Jira → Apps → Xray → API Keys and put both in {xray_secrets_file()} (chmod 600), "
                "or export them as environment variables."
            )
        self.base = xray_base_url()
        self.http = httpx.Client(timeout=60, transport=transport)
        self.token: str | None = None

    def authenticate(self) -> None:
        r = self.http.post(
            f"{self.base}/api/v2/authenticate",
            json={"client_id": self.client_id, "client_secret": self.client_secret},
        )
        if r.status_code != 200:
            raise IntakeError(f"Xray authentication failed: HTTP {r.status_code} {r.text[:200]}")
        self.token = r.text.strip().strip('"')

    def graphql(self, query: str, variables: dict) -> dict:
        if not self.token:
            self.authenticate()
        delay = 5.0
        for attempt in range(1, 6):
            r = self.http.post(
                f"{self.base}/api/v2/graphql",
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if r.status_code == 429 and attempt < 5:
                wait = float(r.headers.get("Retry-After", delay))
                log(f"Xray rate limit hit, waiting {wait:.0f}s")
                time.sleep(wait)
                delay *= 2
                continue
            if r.status_code == 401 and attempt == 1:
                self.authenticate()
                continue
            op = re.search(r"\b([a-z]\w*)\s*\(", query.split("{", 1)[-1])
            trail({"tool": "xray", "graphql": op.group(1) if op else "?", "variables": variables,
                   "http": r.status_code})
            if r.status_code != 200:
                raise IntakeError(f"Xray GraphQL HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            if body.get("errors"):
                raise IntakeError(f"Xray GraphQL error: {json.dumps(body['errors'])[:500]}")
            return body["data"]
        raise IntakeError("Xray GraphQL: retries exhausted")

    def import_execution(self, xray_json: dict) -> dict:
        """Xray JSON results import → creates an Xray test execution (Jira issue)."""
        if not self.token:
            self.authenticate()
        for attempt in (1, 2):
            r = self.http.post(
                f"{self.base}/api/v2/import/execution",
                json=xray_json,
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if r.status_code == 401 and attempt == 1:
                self.authenticate()
                continue
            trail({"tool": "xray", "rest": "POST /api/v2/import/execution",
                   "xray_tests": [t.get("testKey") for t in xray_json.get("tests", [])], "http": r.status_code})
            if r.status_code != 200:
                raise IntakeError(f"Xray results import HTTP {r.status_code}: {r.text[:500]}")
            return r.json()
        raise IntakeError("Xray results import: authentication failed twice")

    def iter_tests(self, jql: str, since: str | None, expand: bool) -> Iterator[dict]:
        query = XRAY_QUERY_EXPANDED if expand else XRAY_QUERY_PLAIN
        start = 0
        while True:
            page = self.graphql(query, {"jql": jql, "since": since, "limit": XRAY_PAGE, "start": start})["page"]
            results = page.get("results") or []
            yield from results
            start += len(results)
            log(f"Xray: pulled {start}/{page.get('total', '?')}")
            if not results or start >= (page.get("total") or 0):
                return

    # Writes and generic listings, used by `xr tests|testsets|testexecutions` and by `xr demo`.

    def iter_issues(self, getter: str, jql: str, fields: str = 'issueId jira(fields: ["key", "summary", "description"])'):
        query = f"""
query($jql: String, $limit: Int!, $start: Int) {{
  page: {getter}(jql: $jql, limit: $limit, start: $start) {{
    total start limit
    results {{ {fields} }}
  }}
}}"""
        start = 0
        while True:
            page = self.graphql(query, {"jql": jql, "limit": XRAY_PAGE, "start": start})["page"]
            results = page.get("results") or []
            yield from results
            start += len(results)
            if not results or start >= (page.get("total") or 0):
                return

    def delete(self, mutation: str, xray_issue_id: str) -> None:
        self.graphql(f"mutation($id: String!) {{ {mutation}(issueId: $id) }}", {"id": xray_issue_id})

    def create_test(self, jira_project: str, summary: str, description: str, labels: list[str]) -> dict:
        """Manual Xray test without Xray steps (the steps live in the description)."""
        data = self.graphql(
            """mutation($jira: JSON!) {
  createTest(testType: { name: "Manual" }, jira: $jira) { test { issueId jira(fields: ["key"]) } warnings }
}""",
            {"jira": {"fields": {"project": {"key": jira_project}, "summary": summary,
                                 "description": description, "labels": labels}}},
        )["createTest"]
        return {"xray_issue_id": data["test"]["issueId"], "xray_key": data["test"]["jira"]["key"]}

    def create_test_set(self, jira_project: str, summary: str, description: str, xray_issue_ids: list[str]) -> dict:
        data = self.graphql(
            """mutation($jira: JSON!, $ids: [String]) {
  createTestSet(testIssueIds: $ids, jira: $jira) { testSet { issueId jira(fields: ["key"]) } warnings }
}""",
            {"jira": {"fields": {"project": {"key": jira_project}, "summary": summary, "description": description}},
             "ids": xray_issue_ids},
        )["createTestSet"]
        return {"xray_issue_id": data["testSet"]["issueId"], "xray_key": data["testSet"]["jira"]["key"]}


def adf_to_text(node: Any) -> str:
    """Atlassian Document Format → plain text. Strings (wiki markup) pass through."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    kind = node.get("type")
    content = node.get("content") or []
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji", "inlineCard"):
        attrs = node.get("attrs") or {}
        return attrs.get("text") or attrs.get("shortName") or attrs.get("url") or ""
    if kind in ("bulletList", "orderedList"):
        items = []
        for i, item in enumerate(content, 1):
            bullet = f"{i}." if kind == "orderedList" else "-"
            items.append(f"{bullet} {adf_to_text(item.get('content') or []).strip()}")
        return "\n".join(items) + "\n\n"
    if kind == "tableRow":
        return " | ".join(adf_to_text(c.get("content") or []).strip() for c in content) + "\n"
    inner = "".join(adf_to_text(c) for c in content)
    if kind in ("paragraph", "heading", "codeBlock", "blockquote", "panel", "table"):
        return inner.rstrip("\n") + "\n\n"
    return inner


def normalize_xray_test(raw: dict) -> dict:
    """Raw GraphQL Xray test → neutral dict. Everything downstream works on this shape."""
    jira = raw.get("jira") or {}
    if isinstance(jira, str):
        jira = json.loads(jira)
    test_type = raw.get("testType") or {}
    preconditions = []
    pre_page = raw.get("preconditions") or {}
    for p in pre_page.get("results") or []:
        pj = p.get("jira") or {}
        preconditions.append(
            {
                "xray_key": pj.get("key") or p.get("issueId"),
                "summary": norm_text(pj.get("summary")),
                "definition": norm_text(p.get("definition")),
            }
        )
    set_page = raw.get("testSets") or {}
    xray_test_sets = [
        {"xray_key": (t.get("jira") or {}).get("key") or t.get("issueId"), "summary": norm_text((t.get("jira") or {}).get("summary"))}
        for t in set_page.get("results") or []
    ]
    warnings = list(raw.get("warnings") or [])
    if (pre_page.get("total") or 0) > len(preconditions):
        warnings.append(f"only the first {len(preconditions)} of {pre_page['total']} Xray preconditions were read")
    if (set_page.get("total") or 0) > len(xray_test_sets):
        warnings.append(f"only the first {len(xray_test_sets)} of {set_page['total']} Xray test sets were read")
    return {
        "xray_key": jira.get("key") or raw.get("issueId"),
        "xray_issue_id": str(raw.get("issueId")),
        "jira_project_id": raw.get("projectId"),
        "xray_test_type": test_type.get("name") or "Unknown",
        "xray_test_kind": test_type.get("kind") or "",
        "summary": norm_text(jira.get("summary")),
        "description": norm_text(adf_to_text(jira.get("description"))),
        "jira_labels": sorted(jira.get("labels") or []),
        "xray_repo_path": (raw.get("folder") or {}).get("path") or "",
        "gherkin": norm_text(raw.get("gherkin")),
        "unstructured": norm_text(raw.get("unstructured")),
        "preconditions": preconditions,
        "xray_test_sets": xray_test_sets,
        "xray_has_called_steps": any(s.get("calledTestIssueId") for s in raw.get("steps") or []),
        "steps": [
            {"action": norm_text(s.get("action")), "data": norm_text(s.get("data")), "result": norm_text(s.get("result"))}
            for s in raw.get("steps") or []
        ],
        "last_modified": raw.get("lastModified"),
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- Xray → TM mapping


def sanitize_label(text: str) -> str:
    return "_".join(text.strip().split())


def xray_set_label(xray_test_set_key: str) -> str:
    return f"{LABEL_PREFIX}Set_{xray_test_set_key}"


def render_tm_case(x: dict, jira_base: str | None) -> dict:
    """Desired TM test case content for one normalized Xray test."""
    name = x["summary"] or x["xray_key"]
    if len(name) > TM_NAME_MAX:
        name = name[: TM_NAME_MAX - 1] + "…"

    parts = [x["description"]] if x["description"] else []
    if x["xray_test_kind"] == "Gherkin" and x["gherkin"]:
        parts.append(f"Xray Cucumber scenario:\n{x['gherkin']}")
    elif x["xray_test_kind"] == "Unstructured" and x["unstructured"]:
        parts.append(f"Xray generic definition:\n{x['unstructured']}")
    origin = f"Imported from Xray test {x['xray_key']}"
    if jira_base:
        origin += f" ({jira_base.rstrip('/')}/browse/{x['xray_key']})"
    parts.append(f"— {origin}")
    description = "\n\n".join(parts)

    pre_condition = "\n\n".join(
        f"[{p['xray_key']}] {p['summary']}".rstrip() + (f"\n{p['definition']}" if p["definition"] else "")
        for p in x["preconditions"]
    )

    tm_steps = [
        {"description": s["action"] or "(no action)", "clipboardData": s["data"], "expectedResult": s["result"]}
        for s in x["steps"]
    ]

    labels = {f"{LABEL_PREFIX}Imported", f"{LABEL_PREFIX}Type_{sanitize_label(x['xray_test_type'])}"}
    if x["xray_repo_path"] and x["xray_repo_path"] != "/":
        labels.add(f"{LABEL_PREFIX}Folder_{sanitize_label(x['xray_repo_path'])}")
    labels.update(f"{LABEL_PREFIX}Label_{sanitize_label(lbl)}" for lbl in x["jira_labels"])
    labels.update(xray_set_label(t["xray_key"]) for t in x.get("xray_test_sets", []))

    return {
        "name": name,
        "description": description,
        "pre_condition": pre_condition,
        "steps": tm_steps,
        # No Xray steps → TM owns the TM steps (e.g. generated from the description in TM):
        # intake never touches them and drift detection ignores them.
        "manage_steps": bool(tm_steps),
        "labels": sorted(labels),
    }


def tm_content_hash(name: str, description: str, steps: list[dict]) -> str:
    """Hash of what is readable back from TM. Drift = this changed since the last intake.

    The TM precondition is not part of it: `uip tm testcases list` does not return it.
    """
    return short_hash(
        {
            "name": norm_text(name),
            "description": norm_text(description),
            "steps": [
                [norm_text(s.get("description")), norm_text(s.get("clipboardData")), norm_text(s.get("expectedResult"))]
                for s in steps
            ],
        }
    )


# --------------------------------------------------------------------------- TM target (uip CLI)


class UipError(IntakeError):
    def __init__(self, args: list[str], envelope: dict | str):
        self.envelope = envelope
        msg = envelope.get("Message") if isinstance(envelope, dict) else envelope
        super().__init__(f"uip {' '.join(args[:3])} failed: {msg}")

    @property
    def retryable(self) -> bool:
        return not (isinstance(self.envelope, dict) and self.envelope.get("Retry") == "RetryWillNotFix")


def _parse_envelope(stdout: str) -> dict | None:
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(stdout[start:])
        return obj
    except json.JSONDecodeError:
        return None


class TmCli:
    """Thin wrapper over `uip tm … --output json`; returns the envelope's Data."""

    def __init__(self, tm_project_key: str):
        self.bin = os.environ.get("UIP_BIN", "uip")
        if not shutil.which(self.bin):
            raise IntakeError(f"`{self.bin}` CLI not found on PATH")
        self.project = tm_project_key
        self.calls = 0

    def raw(self, *args: str) -> Any:
        cmd = [self.bin, *args, "--output", "json"]
        last: UipError | None = None
        for attempt in range(1, RETRIES + 1):
            self.calls += 1
            started = time.monotonic()
            proc = subprocess.run(cmd, capture_output=True, text=True)
            env = _parse_envelope(proc.stdout) or _parse_envelope(proc.stderr)
            ok = bool(env and env.get("Result") == "Success")
            trail_uip(cmd, ok, int((time.monotonic() - started) * 1000))
            if ok:
                return env.get("Data")
            last = UipError(list(args), env or (proc.stderr or proc.stdout).strip()[:300])
            if not last.retryable:
                break
            time.sleep(attempt * 2)
        assert last is not None
        raise last

    def tm(self, *args: str) -> Any:
        return self.raw("tm", *args, "--project-key", self.project)

    # --- reads
    def login_status(self) -> dict:
        return self.raw("login", "status")

    def project_exists(self) -> bool:
        data = self.raw("tm", "project", "list", "--filter", self.project) or []
        return any(p.get("ProjectKey") == self.project for p in data)

    def list_cases(self) -> list[dict]:
        return self.tm("testcases", "list") or []

    def list_steps(self, tm_case_id: str) -> list[dict]:
        rows = self.tm("testcases", "steps", "list", "--test-case-id", tm_case_id) or []
        rows = sorted(rows, key=lambda s: s.get("OrderNo", s.get("orderNo", 0)))
        return [
            {
                "id": s.get("Id"),
                "description": s.get("Description"),
                "clipboardData": s.get("ClipboardData"),
                "expectedResult": s.get("ExpectedResult"),
            }
            for s in rows
        ]

    def text_values(self, object_id: str, object_type: str = "TestCase") -> dict[str, dict]:
        rows = self.tm("customfield", "value", "list", "--object-type", object_type, "--object-id", object_id) or []
        return {r.get("Name"): r for r in rows}

    def field_definitions(self, object_type: str = "TestCase") -> set[str]:
        rows = self.tm("customfield", "list", "--object-types", object_type, "--data-types", "Text", "--limit", "200") or []
        return {r.get("Name") for r in rows}

    def xr_labels(self, tm_case_id: str) -> set[str]:
        rows = self.tm(
            "objectlabel", "list", "--object-type", "TestCase", "--object-ids", tm_case_id,
            "--filter", LABEL_PREFIX, "--limit", "200",
        ) or []
        return {r.get("Name") for r in rows if r.get("Name", "").startswith(LABEL_PREFIX)}

    # --- writes
    def ensure_field(self, name: str, description: str, object_type: str = "TestCase") -> None:
        self.tm(
            "customfield", "create", "--name", name, "--data-type", "Text",
            "--object-type", object_type, "--description", description,
        )

    def create_case(self, name: str, description: str) -> dict:
        return self.tm("testcases", "create", "--name", name, "--description", description)

    def update_case(self, tm_case_key: str, **fields: str) -> None:
        flags = {"name": "--name", "description": "--description", "pre_condition": "--pre-condition"}
        args = ["testcases", "update", "--test-case-key", tm_case_key]
        for field, value in fields.items():
            args += [flags[field], value]
        self.tm(*args)

    def add_steps(self, tm_case_id: str, steps: list[dict]) -> None:
        args = ["testcases", "steps", "add", "--test-case-id", tm_case_id]
        for i, s in enumerate(steps):
            step = {k: v for k, v in s.items() if v} | {"orderNo": i}
            args += ["--step", json.dumps(step, ensure_ascii=False)]
        self.tm(*args)

    def delete_step(self, step_id: str) -> None:
        self.tm("testcases", "steps", "delete", "--step-id", step_id, "--yes")

    def add_labels(self, tm_case_id: str, labels: list[str]) -> None:
        self.tm("objectlabel", "add", "--object-type", "TestCase", "--object-ids", tm_case_id, "--labels", *labels)

    def remove_labels(self, tm_case_id: str, labels: list[str]) -> None:
        self.tm(
            "objectlabel", "remove", "--object-type", "TestCase", "--object-ids", tm_case_id,
            "--labels", *labels, "--yes",
        )

    def set_text_value(self, object_id: str, name: str, value: str, object_type: str = "TestCase") -> None:
        """Write a Text custom field value and verify it.

        Right after a TM test case is created, TM asynchronously provisions an empty value row for every
        custom field definition (~100 ms later), which clobbers a value written in that window; a
        `value create` on an already provisioned row returns Success without writing. So: update first,
        create only when no row exists, wait, read back, and rewrite up to RETRIES times.
        (Same root cause and fix as the Avalon Test Manager MCP server, 3.4.1.)
        """
        target = ["--object-type", object_type, "--name", name, "--object-id", object_id]
        stored = None
        for _ in range(RETRIES):
            try:
                self.tm("customfield", "value", "update", *target, "--data-type", "Text", "--value", value)
            except UipError:
                self.tm("customfield", "value", "create", *target, "--data-type", "Text", "--value", value)
            time.sleep(SETTLE_SECONDS)
            try:
                stored = (self.tm("customfield", "value", "get", *target) or {}).get("Value")
            except UipError:
                stored = None
            if stored == value:
                return
        raise IntakeError(f"TM custom field {name!r} on {object_id} did not persist (read back {stored!r})")


# --------------------------------------------------------------------------- workspace


@dataclass
class Workspace:
    root: Path

    def path(self, name: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root / name

    def read(self, name: str, default: Any = None) -> Any:
        p = self.root / name
        return json.loads(p.read_text()) if p.exists() else default

    def write(self, name: str, data: Any) -> Path:
        p = self.path(name)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return p


def history_log(entry: dict) -> None:
    log_dir = Path.home() / ".claude" / "logs" / "xray-tm-sync"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "history.log").open("a") as f:
        f.write(json.dumps({"time": local_timestamp(), **entry}, ensure_ascii=False) + "\n")


EMIT_HOOK: Any = None  # set by xr/cli.py: wraps the output in the uip-style envelope


def emit(data: Any) -> None:
    if isinstance(data, dict) and _TRAIL:
        data = data | {"evidence": trail_paths()}
    if EMIT_HOOK:
        EMIT_HOOK(data)
        return
    print(json.dumps(data, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- check / pull


def cmd_check(args: argparse.Namespace) -> int:
    """Report every precondition separately so the user sees all gaps at once."""
    report: dict[str, Any] = {}
    problems: list[str] = []
    tm = TmCli(args.tm_project or "-")
    try:
        status = tm.login_status() or {}
        report["uip"] = {k: status.get(k) for k in ("Status", "Identity", "AuthFlow", "Organization", "Tenant", "ExpirationDate")}
        if status.get("Status") != "Logged in":
            problems.append("uip is not logged in. Ask the user to run `! uip login`.")
        elif args.tm_project:
            if tm.project_exists():
                report["tm_project"] = args.tm_project
                report["tm_fields_present"] = sorted({TM_FIELD_XRAY_KEY, TM_FIELD_INTAKE} & tm.field_definitions())
            else:
                problems.append(f"TM project {args.tm_project} not found (uip tm project list --filter {args.tm_project}).")
    except IntakeError as exc:
        problems.append(str(exc))
    try:
        xray = XrayClient()
        xray.authenticate()
        report["xray"] = {"base_url": xray.base, "authenticated": True}
    except IntakeError as exc:
        problems.append(str(exc))
    report["ok"] = not problems
    report["problems"] = problems
    emit(report)
    return 0 if not problems else 2


def cmd_pull(args: argparse.Namespace) -> int:
    ws = Workspace(Path(args.workdir))
    if args.fixture:
        pages = json.loads(Path(args.fixture).read_text())
        raw_tests = [t for page in pages for t in page["data"]["page"]["results"]]
    else:
        raw_tests = list(XrayClient().iter_tests(args.jql, args.since, expand=not args.no_expand))
    xray_tests = [normalize_xray_test(r) for r in raw_tests]
    snapshot = {
        "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jql": args.jql,
        "since": args.since,
        "incremental": bool(args.since),
        "xray_tests": xray_tests,
    }
    path = ws.write("snapshot.json", snapshot)
    kinds: dict[str, int] = {}
    for x in xray_tests:
        kinds[x["xray_test_type"]] = kinds.get(x["xray_test_type"], 0) + 1
    emit({"snapshot": str(path), "xray_tests": len(xray_tests), "by_xray_test_type": kinds,
          "warnings": {x["xray_key"]: x["warnings"] for x in xray_tests if x["warnings"]}})
    return 0


# --------------------------------------------------------------------------- plan


def build_index(tm: TmCli, ws: Workspace, tm_cases: list[dict], fields_present: bool) -> dict[str, list[dict]]:
    """xray_key → TM test cases carrying that origin marker.

    TM cannot list one custom field across all TM test cases, so the marker is read per
    TM test case once and cached in index-<TM project>.json (negatives included).
    """
    cache_name = f"index-{tm.project}.json"
    cache: dict[str, str | None] = (ws.read(cache_name) or {}).get("tm_cases", {})
    live_ids = {c["Id"] for c in tm_cases}
    cache = {k: v for k, v in cache.items() if k in live_ids}
    unknown = [i for i in live_ids if i not in cache] if fields_present else []
    if unknown:
        log(f"TM: reading origin markers of {len(unknown)} TM test cases not in the local index")
        with ThreadPoolExecutor(WORKERS) as pool:
            for tm_case_id, values in zip(unknown, pool.map(tm.text_values, unknown)):
                cache[tm_case_id] = (values.get(TM_FIELD_XRAY_KEY) or {}).get("Value") or None
    ws.write(cache_name, {"tm_project": tm.project, "tm_cases": cache})

    by_id = {c["Id"]: c for c in tm_cases}
    index: dict[str, list[dict]] = {}
    for tm_case_id, xray_key in cache.items():
        if xray_key:
            index.setdefault(xray_key, []).append(by_id[tm_case_id])
    return index


def classify(tm: TmCli, x: dict, desired: dict, matches: list[dict]) -> dict:
    item: dict[str, Any] = {
        "xray_key": x["xray_key"],
        "xray_hash": short_hash(x),
        "last_modified": x["last_modified"],
        "desired": desired,
        "warnings": x["warnings"],
    }
    if x["xray_test_kind"] not in ("Steps", "Gherkin", "Unstructured"):
        return item | {"action": "unsupported", "reason": f"Xray test kind {x['xray_test_kind']!r}"}
    if not matches:
        return item | {"action": "create", "reason": "no TM test case carries this Xray key"}
    if len(matches) > 1:
        keys = ", ".join(m["TestCaseKey"] for m in matches)
        return item | {"action": "conflict", "reason": f"several TM test cases carry this Xray key: {keys}"}

    tm_case = matches[0]
    item |= {"tm_case_id": tm_case["Id"], "tm_case_key": tm_case["TestCaseKey"]}
    values = tm.text_values(tm_case["Id"])
    intake_row = values.get(TM_FIELD_INTAKE)
    try:
        intake = json.loads(intake_row["Value"]) if intake_row else None
    except (TypeError, json.JSONDecodeError):
        intake = None
    if not intake:
        return item | {"action": "update", "reason": "TM test case has no intake record (partial earlier intake)"}
    if intake.get("xrayHash") == item["xray_hash"]:
        return item | {"action": "unchanged", "reason": "Xray test unchanged since last intake"}

    tm_steps = tm.list_steps(tm_case["Id"]) if intake.get("stepsManaged", desired["manage_steps"]) else []
    current = tm_content_hash(tm_case.get("Name"), tm_case.get("Description"), tm_steps)
    if current != intake.get("tmHash"):
        return item | {"action": "drift", "reason": "TM test case was edited after the last intake; skipped"}
    return item | {"action": "update", "reason": "Xray test changed since last intake"}


def cmd_plan(args: argparse.Namespace) -> int:
    ws = Workspace(Path(args.workdir))
    snapshot = ws.read("snapshot.json")
    if not snapshot:
        raise IntakeError("No snapshot.json — run `pull` first.")
    tm = TmCli(args.tm_project)
    if not tm.project_exists():
        raise IntakeError(f"TM project {args.tm_project} not found.")
    fields = tm.field_definitions()
    fields_present = TM_FIELD_XRAY_KEY in fields
    tm_cases = tm.list_cases()
    index = build_index(tm, ws, tm_cases, fields_present)
    jira_base = args.jira_base_url or os.environ.get("XRAY_JIRA_BASE_URL")

    xray_tests = snapshot["xray_tests"]
    with ThreadPoolExecutor(WORKERS) as pool:
        items = list(
            pool.map(lambda x: classify(tm, x, render_tm_case(x, jira_base), index.get(x["xray_key"], [])), xray_tests)
        )
    accepted = set(args.accept_drift or [])
    for it in items:
        if it["action"] == "drift" and it["xray_key"] in accepted:
            it.update(action="update", reason="TM drift accepted by the user; Xray overwrites the TM test case")

    orphans: list[str] = []
    if not snapshot["incremental"]:
        pulled = {x["xray_key"] for x in xray_tests}
        pulled_projects = {k.rsplit("-", 1)[0] for k in pulled}
        orphans = sorted(k for k in index if k not in pulled and k.rsplit("-", 1)[0] in pulled_projects)

    summary: dict[str, int] = {}
    for it in items:
        summary[it["action"]] = summary.get(it["action"], 0) + 1
    plan = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tm_project": tm.project,
        "jql": snapshot["jql"],
        "tm_fields_missing": sorted({TM_FIELD_XRAY_KEY, TM_FIELD_INTAKE} - fields),
        "summary": summary,
        "orphans_not_in_pull": orphans,
        "items": items,
    }
    path = ws.write("plan.json", plan)
    emit({
        "plan": str(path),
        "tm_project": tm.project,
        "summary": summary,
        "tm_fields_to_create": plan["tm_fields_missing"],
        "orphans_not_in_pull": orphans,
        "by_action": {
            a: [f"{i['xray_key']}" + (f" → {i['tm_case_key']}" if i.get("tm_case_key") else "") + f": {i['reason']}"
                for i in items if i["action"] == a]
            for a in summary
        },
        "uip_calls": tm.calls,
    })
    return 0


# --------------------------------------------------------------------------- apply


def readback_hash(tm: TmCli, tm_case_key: str, tm_case_id: str, manage_steps: bool) -> str:
    rows = tm.tm("testcases", "list", "--filter", tm_case_key) or []
    row = next((r for r in rows if r.get("TestCaseKey") == tm_case_key), None)
    if not row:
        raise IntakeError(f"read-back of {tm_case_key} failed")
    return tm_content_hash(row.get("Name"), row.get("Description"), tm.list_steps(tm_case_id) if manage_steps else [])


def write_intake(tm: TmCli, item: dict, tm_case_id: str, tm_case_key: str) -> None:
    record = {
        "xrayKey": item["xray_key"],
        "lastModified": item["last_modified"],
        "xrayHash": item["xray_hash"],
        "tmHash": readback_hash(tm, tm_case_key, tm_case_id, item["desired"]["manage_steps"]),
        "stepsManaged": item["desired"]["manage_steps"],
        "intakeAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tm.set_text_value(tm_case_id, TM_FIELD_INTAKE, json.dumps(record, separators=(",", ":")))


def apply_create(tm: TmCli, item: dict) -> dict:
    d = item["desired"]
    created = tm.create_case(d["name"], d["description"])
    tm_case_id, tm_case_key = created["Id"], created["TestCaseKey"]
    # Origin marker first: a later failure leaves a findable, repairable TM test case.
    tm.set_text_value(tm_case_id, TM_FIELD_XRAY_KEY, item["xray_key"])
    if d["pre_condition"]:
        tm.update_case(tm_case_key, pre_condition=d["pre_condition"])
    if d["steps"]:
        tm.add_steps(tm_case_id, d["steps"])
    tm.add_labels(tm_case_id, d["labels"])
    write_intake(tm, item, tm_case_id, tm_case_key)
    return {"tm_case_id": tm_case_id, "tm_case_key": tm_case_key}


def apply_update(tm: TmCli, item: dict) -> dict:
    d = item["desired"]
    tm_case_id, tm_case_key = item["tm_case_id"], item["tm_case_key"]
    tm.update_case(tm_case_key, name=d["name"], description=d["description"], pre_condition=d["pre_condition"])
    if d["manage_steps"]:
        current = tm.list_steps(tm_case_id)
        wanted = [[s["description"], s["clipboardData"], s["expectedResult"]] for s in d["steps"]]
        have = [[norm_text(s["description"]), norm_text(s["clipboardData"]), norm_text(s["expectedResult"])] for s in current]
        if wanted != have:
            for s in current:
                tm.delete_step(s["id"])
            tm.add_steps(tm_case_id, d["steps"])
    existing = tm.xr_labels(tm_case_id)
    stale = sorted(existing - set(d["labels"]))
    if stale:
        tm.remove_labels(tm_case_id, stale)
    missing = sorted(set(d["labels"]) - existing)
    if missing:
        tm.add_labels(tm_case_id, missing)
    write_intake(tm, item, tm_case_id, tm_case_key)
    return {"tm_case_id": tm_case_id, "tm_case_key": tm_case_key}


def cmd_apply(args: argparse.Namespace) -> int:
    ws = Workspace(Path(args.workdir))
    plan = ws.read("plan.json")
    if not plan:
        raise IntakeError("No plan.json — run `plan` first.")
    if not args.yes:
        raise IntakeError("apply writes to Test Manager. Show the plan to the user, then re-run with --yes.")
    age = datetime.now(timezone.utc) - datetime.fromisoformat(plan["created_at"])
    if age.total_seconds() > 3600 and not args.force:
        raise IntakeError("plan.json is older than 1 hour — re-run `plan` (or pass --force).")

    tm = TmCli(plan["tm_project"])
    for field in plan["tm_fields_missing"]:
        tm.ensure_field(field, "Written by xray-tm-sync. Do not edit.")
        log(f"TM: created custom field {field!r}")

    todo = [i for i in plan["items"] if i["action"] in ("create", "update")]
    if args.only:
        wanted = set(args.only)
        todo = [i for i in todo if i["xray_key"] in wanted]

    def run(item: dict) -> dict:
        try:
            fn = apply_create if item["action"] == "create" else apply_update
            result = fn(tm, item)
            log(f"{item['action']:>7} {item['xray_key']} → {result['tm_case_key']}")
            return {"xray_key": item["xray_key"], "action": item["action"], "status": "done", **result}
        except IntakeError as exc:
            log(f" FAILED {item['xray_key']}: {exc}")
            return {"xray_key": item["xray_key"], "action": item["action"], "status": "failed", "error": str(exc),
                    "tm_case_key": item.get("tm_case_key")}

    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(run, todo))

    # Index cache: newly created TM test cases are known Xray-origin cases.
    cache_name = f"index-{tm.project}.json"
    cache = ws.read(cache_name) or {"tm_project": tm.project, "tm_cases": {}}
    for r in results:
        if r.get("tm_case_id"):
            cache["tm_cases"][r["tm_case_id"]] = r["xray_key"]
    ws.write(cache_name, cache)

    skipped = [{"xray_key": i["xray_key"], "action": i["action"], "reason": i["reason"]}
               for i in plan["items"] if i["action"] not in ("create", "update", "unchanged")]
    counts: dict[str, int] = {}
    for r in results:
        k = f"{r['action']}_{r['status']}"
        counts[k] = counts.get(k, 0) + 1
    report = {
        "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tm_project": tm.project,
        "counts": counts,
        "unchanged": plan["summary"].get("unchanged", 0),
        "skipped": skipped,
        "results": results,
        "uip_calls": tm.calls,
    }
    ws.write("report.json", report)
    history_log({"tm_project": tm.project, "jql": plan["jql"], "counts": counts, "skipped": len(skipped)})
    emit(report)
    return 1 if any(r["status"] == "failed" for r in results) else 0


# --------------------------------------------------------------------------- TM test sets


def tm_case_key_by_xray_key(tm: TmCli, ws: Workspace) -> dict[str, str]:
    """Xray key → TM case key, from the local index (run `plan` first to refresh it)."""
    cache = (ws.read(f"index-{tm.project}.json") or {}).get("tm_cases", {})
    if not cache:
        raise IntakeError("No local index of TM test cases — run `plan` (and `apply`) first.")
    tm_cases = {c["Id"]: c for c in tm.list_cases()}
    return {xk: tm_cases[cid]["TestCaseKey"] for cid, xk in cache.items() if xk and cid in tm_cases}


def cmd_testsets(args: argparse.Namespace) -> int:
    """Mirror the Xray test sets seen in snapshot.json as TM test sets (dry run without --yes)."""
    ws = Workspace(Path(args.workdir))
    snapshot = ws.read("snapshot.json")
    if not snapshot:
        raise IntakeError("No snapshot.json — run `pull` first.")
    tm = TmCli(args.tm_project)

    desired: dict[str, dict] = {}
    for x in snapshot["xray_tests"]:
        for t in x.get("xray_test_sets", []):
            d = desired.setdefault(t["xray_key"], {"summary": t["summary"], "members": set()})
            d["members"].add(x["xray_key"])
    if args.only:
        desired = {k: v for k, v in desired.items() if k in set(args.only)}
    if not desired:
        emit({"message": "No Xray test sets in snapshot.json (or none matched --only)."})
        return 0

    field_present = TM_FIELD_XRAY_KEY in tm.field_definitions("TestSet")
    tm_sets = tm.tm("testsets", "list") or []
    by_xray: dict[str, list[dict]] = {}
    if field_present and tm_sets:
        with ThreadPoolExecutor(WORKERS) as pool:
            values = list(pool.map(lambda ts: tm.text_values(ts["Id"], "TestSet"), tm_sets))
        for ts, v in zip(tm_sets, values):
            xray_key = (v.get(TM_FIELD_XRAY_KEY) or {}).get("Value")
            if xray_key:
                by_xray.setdefault(xray_key, []).append(ts)

    case_keys = tm_case_key_by_xray_key(tm, ws)
    xray_by_case_key = {v: k for k, v in case_keys.items()}
    pulled = {x["xray_key"] for x in snapshot["xray_tests"]}
    items = []
    for set_key, d in sorted(desired.items()):
        name = (d["summary"] or set_key)[:TM_NAME_MAX]
        wanted = {case_keys[k] for k in d["members"] if k in case_keys}
        item = {
            "xray_test_set_key": set_key,
            "name": name,
            "description": f"Mirrors Xray test set {set_key}. Membership is managed by xray-tm-sync.",
            "add": [], "remove": [],
            "xray_tests_not_in_tm": sorted(k for k in d["members"] if k not in case_keys),
        }
        matches = by_xray.get(set_key, [])
        if len(matches) > 1:
            items.append(item | {"action": "conflict", "reason": "several TM test sets carry this Xray key: "
                                 + ", ".join(m["TestSetKey"] for m in matches)})
            continue
        if not matches:
            items.append(item | {"action": "create", "add": sorted(wanted)})
            continue
        ts = matches[0]
        current = {m["TestCaseKey"] for m in tm.tm("testsets", "list-testcases", "--test-set-key", ts["TestSetKey"]) or []}
        # Remove only Xray-origin TM test cases whose Xray test was pulled and is no longer in the Xray test set;
        # TM test cases added by hand stay.
        remove = sorted(c for c in current - wanted if xray_by_case_key.get(c) in pulled)
        add = sorted(wanted - current)
        changed = bool(add or remove or ts.get("Name") != name)
        items.append(item | {"action": "update" if changed else "unchanged", "tm_test_set_key": ts["TestSetKey"],
                             "tm_test_set_id": ts["Id"], "add": add, "remove": remove})

    if args.yes:
        if not field_present:
            tm.ensure_field(TM_FIELD_XRAY_KEY, "Written by xray-tm-sync. Do not edit.", "TestSet")
        for it in items:
            try:
                if it["action"] == "create":
                    created = tm.tm("testsets", "create", "--name", it["name"], "--description", it["description"])
                    it["tm_test_set_key"] = created["TestSetKey"]
                    listed = tm.tm("testsets", "list", "--filter", created["TestSetKey"]) or []
                    it["tm_test_set_id"] = next(t["Id"] for t in listed if t["TestSetKey"] == created["TestSetKey"])
                    tm.set_text_value(it["tm_test_set_id"], TM_FIELD_XRAY_KEY, it["xray_test_set_key"], "TestSet")
                elif it["action"] == "update":
                    tm.raw("tm", "testsets", "update", "--test-set-key", it["tm_test_set_key"], "--name", it["name"])
                if it["action"] in ("create", "update"):
                    if it["add"]:
                        tm.raw("tm", "testcases", "add", "--test-set-key", it["tm_test_set_key"], "--test-case-keys", *it["add"])
                    if it["remove"]:
                        tm.raw("tm", "testcases", "remove", "--test-set-key", it["tm_test_set_key"],
                               "--test-case-keys", ",".join(it["remove"]))
                    it["status"] = "done"
            except (IntakeError, StopIteration) as exc:
                it["status"], it["error"] = "failed", str(exc)
        history_log({"tm_project": tm.project, "testsets": {i["xray_test_set_key"]: i.get("status", i["action"]) for i in items}})

    emit({"tm_project": tm.project, "applied": args.yes, "tm_field_to_create": None if field_present else "Xray Key (TestSet)",
          "test_sets": items, "uip_calls": tm.calls})
    return 1 if any(i.get("status") == "failed" for i in items) else 0


# --------------------------------------------------------------------------- results back to Xray


def xray_date(value: str | None) -> str | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(timespec="seconds")


def collect_tm_results(tm: TmCli, ws: Workspace, execution_id: str) -> dict:
    """Read a TM test execution: one Xray JSON `tests` entry per TM test case with an origin marker."""
    snapshot = {x["xray_key"]: x for x in (ws.read("snapshot.json") or {}).get("xray_tests", [])}
    logs = tm.tm("executions", "testcaselogs", "list", "--execution-id", execution_id, "--limit", "500") or []
    if not logs:
        raise IntakeError(f"TM test execution {execution_id} has no TM test case logs.")
    tests, skipped, mock_tagged = [], [], False
    for lg in logs:
        tm_case_id = lg["TestCaseId"]
        tm_case_key = (lg.get("TestCase") or {}).get("ObjKey") or tm_case_id
        xray_key = (tm.text_values(tm_case_id).get(TM_FIELD_XRAY_KEY) or {}).get("Value")
        if not xray_key:
            skipped.append({"tm_case_key": tm_case_key, "reason": "TM test case has no Xray origin marker"})
            continue
        step_rows = sorted(tm.tm("teststeplog", "list", "--test-case-log-id", lg["Id"]) or [],
                           key=lambda r: r.get("OrderNo", 0))
        mock_tagged = mock_tagged or any(str(r.get("Comment") or "").startswith(MOCK_TAG) for r in step_rows)
        entry: dict[str, Any] = {
            "testKey": xray_key,
            "status": TM_TO_XRAY_STATUS.get(lg.get("Result") or "None", "TODO"),
            "comment": f"UiPath Test Manager {tm_case_key}, test case log {lg['Id']}",
        }
        start, finish = xray_date(lg.get("ExecutionStart")), xray_date(lg.get("ExecutionEnd") or lg.get("Updated"))
        if start:
            entry["start"] = start
        if finish:
            entry["finish"] = finish
        x = snapshot.get(xray_key)
        # Xray rejects a steps array whose length differs from the Xray test's own steps.
        if (x and x["xray_test_kind"] == "Steps" and not x.get("xray_has_called_steps")
                and step_rows and len(step_rows) == len(x["steps"])):
            entry["steps"] = [{"status": TM_TO_XRAY_STATUS.get(r.get("Result") or "None", "TODO"),
                               **({"comment": r["Comment"]} if r.get("Comment") else {})} for r in step_rows]
        tests.append(entry)
    if not tests:
        raise IntakeError("No TM test case in this execution carries an Xray origin marker; nothing to report.")
    return {"tests": tests, "skipped": skipped, "mock_tagged": mock_tagged,
            "jira_projects": sorted({t["testKey"].rsplit("-", 1)[0] for t in tests})}


def build_xray_json(tm: TmCli, execution_id: str, tests: list[dict], xray_project: str, label: str, *,
                    summary_prefix: str = "", description_suffix: str = "",
                    test_environments: list[str] | None = None, xray_test_plan: str | None = None) -> dict:
    info: dict[str, Any] = {
        "project": xray_project,
        "summary": f"{summary_prefix}UiPath Test Manager {label} (TM project {tm.project})",
        "description": (f"Results of UiPath Test Manager test execution {execution_id} (TM project {tm.project}), "
                        f"{REPORT_MARKER}.{description_suffix}"),
    }
    starts = [t["start"] for t in tests if t.get("start")]
    finishes = [t["finish"] for t in tests if t.get("finish")]
    if starts:
        info["startDate"] = min(starts)
    if finishes:
        info["finishDate"] = max(finishes)
    if test_environments:
        info["testEnvironments"] = test_environments
    if xray_test_plan:
        info["testPlanKey"] = xray_test_plan
    return {"info": info, "tests": tests}


def publish_results(ws: Workspace, tm: TmCli, execution_id: str, xray_json: dict, results: dict,
                    apply: bool, mock: bool, xray_client: Any = None) -> dict:
    """Write xray-results-<id>.json; with apply, import it and append to the local ledger."""
    path = ws.write(f"xray-results-{execution_id}.json", xray_json)
    tests = xray_json["tests"]
    out: dict[str, Any] = {"applied": apply, "mock": mock, "xray_results_file": str(path),
                           "xray_project": xray_json["info"]["project"], "xray_tests": len(tests),
                           "by_status": {s: sum(t["status"] == s for t in tests) for s in {t["status"] for t in tests}},
                           "skipped": results["skipped"]}
    if apply:
        created = (xray_client or XrayClient()).import_execution(xray_json)
        out["xray_test_execution"] = created
        ledger = ws.read("xray-executions.json") or []
        ledger.append({"key": created.get("key"), "id": created.get("id"), "project": xray_json["info"]["project"],
                       "mock": mock, "tm_project": tm.project, "tm_execution_id": execution_id})
        ws.write("xray-executions.json", ledger)
        history_log({"tm_project": tm.project, "reported_execution": execution_id, "mock": mock,
                     "xray_test_execution": created.get("key")})
    return out


def cmd_report(args: argparse.Namespace) -> int:
    """Real TM test execution → new Xray test execution (dry run without --yes)."""
    ws = Workspace(Path(args.workdir))
    tm = TmCli(args.tm_project)
    record = ws.read(f"execution-{args.execution_id}.json") or {}
    results = collect_tm_results(tm, ws, args.execution_id)
    if record.get("mock") or results["mock_tagged"]:
        raise IntakeError(f"TM test execution {args.execution_id} contains {MOCK_TAG} results. xray-tm-sync only "
                          "reports real results; mock runs belong to the xray-tm-demo skill.")
    projects = results["jira_projects"]
    xray_project = args.xray_project or (projects[0] if len(projects) == 1 else None)
    if not xray_project:
        raise IntakeError(f"Xray tests from several Jira projects {projects}; pass --xray-project.")
    label = f"test set {record['tm_test_set']}" if record.get("tm_test_set") else f"test execution {args.execution_id}"
    xray_json = build_xray_json(tm, args.execution_id, results["tests"], xray_project, label,
                                test_environments=args.test_environment, xray_test_plan=args.xray_test_plan)
    emit(publish_results(ws, tm, args.execution_id, xray_json, results, args.yes, mock=False))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    ws = Workspace(Path(args.workdir))
    snap, plan, report = ws.read("snapshot.json"), ws.read("plan.json"), ws.read("report.json")
    emit({
        "snapshot": snap and {"pulled_at": snap["pulled_at"], "jql": snap["jql"], "xray_tests": len(snap["xray_tests"])},
        "plan": plan and {"created_at": plan["created_at"], "tm_project": plan["tm_project"], "summary": plan["summary"]},
        "report": report and {"applied_at": report["applied_at"], "counts": report["counts"], "skipped": len(report["skipped"])},
    })
    return 0


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="xr sync", description=__doc__.split("\n\n")[0])
    p.add_argument("--workdir", default=".xray-tm-sync", help="state folder (default: ./.xray-tm-sync)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="verify uip login, TM project and Xray credentials")
    c.add_argument("--tm-project")

    c = sub.add_parser("pull", help="read Xray tests via GraphQL into snapshot.json")
    c.add_argument("--jql", required=True, help='e.g. "project = CALC AND issuetype = Test"')
    c.add_argument("--since", help="only Xray tests modified after this ISO date (incremental)")
    c.add_argument("--no-expand", action="store_true", help="use getTests instead of getExpandedTests")
    c.add_argument("--fixture", help=argparse.SUPPRESS)  # offline: recorded GraphQL pages

    c = sub.add_parser("plan", help="diff snapshot against TM; writes plan.json, no TM writes")
    c.add_argument("--tm-project", required=True)
    c.add_argument("--jira-base-url", help="e.g. https://acme.atlassian.net (for links in TM descriptions)")
    c.add_argument("--accept-drift", nargs="+", metavar="XRAY_KEY",
                   help="overwrite these drifted TM test cases from Xray (only after the user agreed)")

    c = sub.add_parser("apply", help="execute plan.json against TM (requires --yes)")
    c.add_argument("--yes", action="store_true")
    c.add_argument("--force", action="store_true", help="accept a plan older than 1 hour")
    c.add_argument("--only", nargs="+", metavar="XRAY_KEY", help="restrict to these Xray keys")

    c = sub.add_parser("testsets", help="mirror Xray test sets as TM test sets (dry run without --yes)")
    c.add_argument("--tm-project", required=True)
    c.add_argument("--only", nargs="+", metavar="XRAY_TEST_SET_KEY")
    c.add_argument("--yes", action="store_true")

    c = sub.add_parser("report", help="real TM test execution → Xray test execution (dry run without --yes)")
    c.add_argument("--tm-project", required=True)
    c.add_argument("--execution-id", required=True, metavar="TM_EXECUTION_ID")
    c.add_argument("--xray-project", metavar="JIRA_PROJECT_KEY", help="Jira project of the new Xray test execution")
    c.add_argument("--xray-test-plan", metavar="XRAY_KEY")
    c.add_argument("--test-environment", nargs="+", metavar="NAME",
                   help="Xray test environments that already exist in the Jira project, e.g. staging")
    c.add_argument("--yes", action="store_true")

    sub.add_parser("status", help="summarize the last snapshot, plan and report")

    args = p.parse_args(argv)
    start_trail(Path(args.workdir), "xr sync", args.cmd, sys.argv[1:] if argv is None else argv)
    handler = {"check": cmd_check, "pull": cmd_pull, "plan": cmd_plan, "apply": cmd_apply, "testsets": cmd_testsets,
               "report": cmd_report, "status": cmd_status}[args.cmd]
    try:
        return handler(args)
    except IntakeError as exc:
        emit({"error": str(exc)})
        return 2
    except Exception as exc:
        if not type(exc).__module__.startswith(("httpx", "httpcore")):
            raise
        emit({"error": f"network error talking to Xray or TM: {type(exc).__name__}: {exc}. Safe to re-run."})
        return 2


if __name__ == "__main__":
    sys.exit(main())
