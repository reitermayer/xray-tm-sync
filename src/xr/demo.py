"""xr demo: demo tooling on top of xr sync (Xray Cloud ↔ UiPath Test Manager).

Not for production data. Every command needs --xray-project and --tm-project, and the pair must be
registered as a sandbox first (`sandbox add`). Adds what a demo needs and production must never do:
seed demo Xray tests, mock (robot-free) TM test executions, report mock results, and reset/reseed.

Subcommands: sandbox | seed | execute | report | reset
The intake itself (pull, plan, apply, testsets) is `xr sync`'s job.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from xr import sync as core
IntakeError, Workspace, emit, log = core.IntakeError, core.Workspace, core.emit, core.log

MOCK_TAG = core.MOCK_TAG
MOCK_TEST_ENVIRONMENT = "TM-mock"  # suggested name; Xray only accepts test environments that exist in the project
DEFAULT_SEED = Path(__file__).resolve().parent / "data" / "seed-stock-trading.json"
TM_HTTP_TRANSPORT: Any = None  # tests inject an httpx transport for the one TM REST call


def sandbox_file() -> Path:
    return Path(os.environ.get("XRAY_TM_DEMO_SANDBOXES", Path.home() / ".config" / "xray-tm-demo" / "sandboxes.json"))


def require_sandbox(args: argparse.Namespace) -> None:
    pairs = json.loads(sandbox_file().read_text()) if sandbox_file().exists() else []
    if {"xray_project": args.xray_project, "tm_project": args.tm_project} not in pairs:
        raise IntakeError(
            f"Jira project {args.xray_project} + TM project {args.tm_project} is not a registered demo sandbox "
            f"({sandbox_file()}). Only after the user confirms both are sandboxes: "
            f"`sandbox add --xray-project {args.xray_project} --tm-project {args.tm_project}`."
        )


# --------------------------------------------------------------------------- demo-only API calls


class DemoXray(core.XrayClient):
    """The Xray client the demo uses. The create and delete calls live in xr sync's XrayClient, shared with `xr tests`."""


class DemoTm(core.TmCli):
    """TM deletes a demo reset needs."""

    def project_id(self) -> str:
        data = self.raw("tm", "project", "list", "--filter", self.project) or []
        match = next((p for p in data if p.get("ProjectKey") == self.project), None)
        if not match:
            raise IntakeError(f"TM project {self.project} not found.")
        return match["Id"]

    def list_executions(self, tm_test_set_id: str) -> list[dict]:
        return self.tm("executions", "list", "--test-set-id", tm_test_set_id, "--limit", "200") or []

    def delete_case(self, tm_case_key: str) -> None:
        self.tm("testcases", "delete", "--test-case-key", tm_case_key, "--yes")

    def delete_test_set(self, tm_test_set_key: str) -> None:
        self.raw("tm", "testsets", "delete", "--test-set-key", tm_test_set_key, "--yes")

    def delete_execution(self, project_id: str, execution_id: str) -> None:
        """The only TM REST call in either skill: `uip tm` cannot delete a TM test execution.

        Public TM API v2 (swagger), authenticated with the uip login session's token.
        """
        import httpx

        session = self.raw("login", "refresh") or {}
        url = (f"{session['BaseUrl'].rstrip('/')}/{session['OrganizationName']}/{session['TenantName']}"
               f"/testmanager_/api/v2/{project_id}/testexecutions/{execution_id}")
        with httpx.Client(timeout=60, transport=TM_HTTP_TRANSPORT) as http:
            r = http.delete(url, headers={"Authorization": f"Bearer {session['AccessToken']}"})
        if r.status_code not in (200, 204):
            raise IntakeError(f"TM REST delete of test execution {execution_id}: HTTP {r.status_code} {r.text[:200]}")


# --------------------------------------------------------------------------- sandbox


def cmd_sandbox(args: argparse.Namespace) -> int:
    path = sandbox_file()
    pairs = json.loads(path.read_text()) if path.exists() else []
    if args.action == "add":
        if not (args.xray_project and args.tm_project):
            raise IntakeError("sandbox add needs --xray-project and --tm-project.")
        pair = {"xray_project": args.xray_project, "tm_project": args.tm_project}
        if pair not in pairs:
            pairs.append(pair)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(pairs, indent=2))
    elif args.action == "remove":
        pairs = [p for p in pairs if p != {"xray_project": args.xray_project, "tm_project": args.tm_project}]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pairs, indent=2))
    emit({"sandbox_file": str(path), "sandboxes": pairs})
    return 0


# --------------------------------------------------------------------------- seed


def load_seed(path: str) -> dict:
    return json.loads(Path(path).read_text())


def create_seed(xray: DemoXray, jira_project: str, seed: dict) -> dict:
    created = [xray.create_test(jira_project, t["summary"], t["description"], t.get("labels", []))
               for t in seed["xray_tests"]]
    ts = seed["xray_test_set"]
    created_set = xray.create_test_set(jira_project, ts["summary"], ts["description"],
                                       [c["xray_issue_id"] for c in created])
    log(f"created Xray test set {created_set['xray_key']} with {len(created)} Xray tests")
    return {"xray_test_set": created_set["xray_key"], "xray_tests": [c["xray_key"] for c in created]}


def seed_preview(seed: dict) -> dict:
    return {"xray_test_set": seed["xray_test_set"]["summary"], "xray_tests": [t["summary"] for t in seed["xray_tests"]]}


def cmd_seed(args: argparse.Namespace) -> int:
    """Create the demo Xray tests and Xray test set in the sandbox Jira project (no deletes)."""
    require_sandbox(args)
    seed = load_seed(args.seed)
    if not args.yes:
        emit({"applied": False, "xray_project": args.xray_project, "xray_seed_to_create": seed_preview(seed)})
        return 0
    emit({"applied": True, "xray_project": args.xray_project, "seeded": create_seed(DemoXray(), args.xray_project, seed)})
    return 0


# --------------------------------------------------------------------------- mock execution


def resolve_outcome(spec: Any) -> dict:
    """Outcome file entry → {"result", "failed_step" (1-based or None), "comment"}."""
    if spec is None:
        return {"result": "Passed", "failed_step": None, "comment": ""}
    if isinstance(spec, str):
        spec = {"result": spec}
    result = spec.get("result", "Passed")
    if result not in core.TM_TO_XRAY_STATUS:
        raise IntakeError(f"outcome {result!r} is not one of {sorted(core.TM_TO_XRAY_STATUS)}")
    return {"result": result, "failed_step": spec.get("failed_step"), "comment": spec.get("comment", "")}


def mock_step_results(outcome: dict, n_steps: int) -> list[str]:
    if outcome["result"] != "Failed":
        return [outcome["result"]] * n_steps
    failed = outcome["failed_step"] or n_steps
    return ["Passed" if i < failed else "Failed" if i == failed else "None" for i in range(1, n_steps + 1)]


def camel(row: dict) -> dict:
    return {k[:1].lower() + k[1:]: v for k, v in row.items()}


def cmd_execute(args: argparse.Namespace) -> int:
    """Mock TM execution of a TM test set: manual execution, results from an outcomes file, no robot."""
    require_sandbox(args)
    ws = Workspace(Path(args.workdir))
    tm = DemoTm(args.tm_project)
    outcomes = json.loads(Path(args.outcomes).read_text()) if args.outcomes else {}

    members = tm.tm("testsets", "list-testcases", "--test-set-key", args.tm_test_set) or []
    if not members:
        raise IntakeError(f"TM test set {args.tm_test_set} has no TM test cases.")
    # list-testcases rows carry no TM case id; resolve it from the TM project's test case list.
    tm_case_ids = {c["TestCaseKey"]: c["Id"] for c in tm.list_cases()}
    members = [m | {"Id": tm_case_ids[m["TestCaseKey"]]} for m in members]
    with ThreadPoolExecutor(core.WORKERS) as pool:
        markers = list(pool.map(lambda m: (tm.text_values(m["Id"]).get(core.TM_FIELD_XRAY_KEY) or {}).get("Value"), members))
    planned = [
        {"tm_case_key": m["TestCaseKey"], "tm_case_id": m["Id"], "xray_key": xk or None,
         **resolve_outcome(outcomes.get(xk) if xk else None)}
        for m, xk in zip(members, markers)
    ]
    unknown = sorted(set(outcomes) - {p["xray_key"] for p in planned})
    if not args.yes:
        emit({"applied": False, "tm_test_set": args.tm_test_set, "planned": planned,
              "outcomes_for_unknown_xray_keys": unknown})
        return 0

    identity = (tm.login_status() or {}).get("Identity") or ""
    run = tm.raw("tm", "testsets", "run", "--test-set-key", args.tm_test_set, "--execution-type", "manual")
    execution_id = run["ExecutionId"]
    log(f"TM: mock execution {execution_id} started for {args.tm_test_set}")

    results = []
    for p in planned:
        try:
            started = tm.tm("testcaselog", "start", "--execution-id", execution_id, "--test-case-id", p["tm_case_id"])
            step_rows = sorted(tm.tm("teststeplog", "list", "--test-case-log-id", started["Id"]) or [],
                               key=lambda r: r.get("OrderNo", 0))
            step_results = mock_step_results(p, len(step_rows))
            step_logs = [
                camel(r) | {"result": res, "hasError": res == "Failed",
                            "comment": f"{MOCK_TAG} {p['comment']}".strip() if res == "Failed" else MOCK_TAG}
                for r, res in zip(step_rows, step_results)
            ]
            finish = ["testcaselog", "finish", "--execution-id", execution_id, "--test-case-id", p["tm_case_id"],
                      "--result", p["result"], "--has-error", str(p["result"] == "Failed").lower(),
                      "--executed-by", identity]
            if step_logs:
                finish += ["--step-logs", json.dumps(step_logs, ensure_ascii=False)]
            tm.tm(*finish)
            results.append(p | {"status": "done", "tm_case_log_id": started["Id"], "step_results": step_results})
            log(f"  {p['result']:>8} {p['tm_case_key']} ({p['xray_key'] or 'no Xray origin'})")
        except IntakeError as exc:
            results.append(p | {"status": "failed", "error": str(exc)})

    record = {"execution_id": execution_id, "tm_project": tm.project, "tm_test_set": args.tm_test_set,
              "xray_project": args.xray_project, "mock": True, "started_at": run.get("StartTime"), "results": results}
    ws.write(f"execution-{execution_id}.json", record)
    core.history_log({"tm_project": tm.project, "mock_execution": execution_id, "tm_test_set": args.tm_test_set,
                      "results": {r["tm_case_key"]: r["result"] for r in results}})
    emit(record | {"uip_calls": tm.calls})
    return 1 if any(r["status"] == "failed" for r in results) else 0


# --------------------------------------------------------------------------- mock results → Xray


def cmd_report(args: argparse.Namespace) -> int:
    """Mock TM test execution → [MOCK] Xray test execution in the sandbox (dry run without --yes)."""
    require_sandbox(args)
    ws = Workspace(Path(args.workdir))
    tm = DemoTm(args.tm_project)
    record = ws.read(f"execution-{args.execution_id}.json") or {}
    results = core.collect_tm_results(tm, ws, args.execution_id)
    if not (record.get("mock") or results["mock_tagged"]):
        raise IntakeError("This TM test execution has no mock results; report real results with xray-tm-sync.")
    outside = [p for p in results["jira_projects"] if p != args.xray_project]
    if outside:
        raise IntakeError(f"{MOCK_TAG} results refused: Xray tests of Jira project(s) {outside} are not in the "
                          f"sandbox Jira project {args.xray_project}.")
    label = f"test set {record['tm_test_set']}" if record.get("tm_test_set") else f"test execution {args.execution_id}"
    xray_json = core.build_xray_json(
        tm, args.execution_id, results["tests"], args.xray_project, label,
        summary_prefix=f"{MOCK_TAG} ", description_suffix=" Mock results — not real test outcomes.",
        test_environments=args.test_environment, xray_test_plan=args.xray_test_plan,
    )
    emit(core.publish_results(ws, tm, args.execution_id, xray_json, results, args.yes, mock=True))
    return 0


# --------------------------------------------------------------------------- reset


def cmd_reset(args: argparse.Namespace) -> int:
    """Demo reset in the sandbox pair.

    TM: TM test executions of Xray-origin TM test sets (plus locally recorded mock runs), Xray-origin
    TM test sets and TM test cases from the sandbox Jira project. Xray: [MOCK] Xray test executions.
    With --reseed: every Xray test, Xray test set and Xray test execution in the sandbox Jira project,
    replaced by the seed. Seed first, delete second.
    """
    require_sandbox(args)
    ws = Workspace(Path(args.workdir))
    tm = DemoTm(args.tm_project)
    seed = load_seed(args.seed) if args.reseed else None
    from_sandbox = lambda xray_key: xray_key.rsplit("-", 1)[0] == args.xray_project  # noqa: E731
    plan: dict[str, list] = {"xray_test_executions": [], "xray_test_sets": [], "xray_tests": [],
                             "tm_test_executions": [], "tm_test_sets": [], "tm_test_cases": []}

    cases = tm.list_cases()
    with ThreadPoolExecutor(core.WORKERS) as pool:
        markers = list(pool.map(lambda c: (tm.text_values(c["Id"]).get(core.TM_FIELD_XRAY_KEY) or {}).get("Value"), cases))
    plan["tm_test_cases"] = [{"tm_case_key": c["TestCaseKey"], "xray_key": xk, "name": c["Name"]}
                             for c, xk in zip(cases, markers) if xk and from_sandbox(xk)]
    if core.TM_FIELD_XRAY_KEY in tm.field_definitions("TestSet"):
        tm_sets = tm.tm("testsets", "list") or []
        with ThreadPoolExecutor(core.WORKERS) as pool:
            set_markers = list(pool.map(
                lambda ts: (tm.text_values(ts["Id"], "TestSet").get(core.TM_FIELD_XRAY_KEY) or {}).get("Value"), tm_sets))
        for ts, xk in zip(tm_sets, set_markers):
            if xk and from_sandbox(xk):
                plan["tm_test_sets"].append({"tm_test_set_key": ts["TestSetKey"], "xray_test_set_key": xk, "name": ts["Name"]})
                plan["tm_test_executions"] += [{"execution_id": e["Id"], "name": e.get("Name"), "status": e.get("Status")}
                                               for e in tm.list_executions(ts["Id"])]
    known = {e["execution_id"] for e in plan["tm_test_executions"]}
    for f in sorted(ws.root.glob("execution-*.json")) if ws.root.exists() else []:
        rec = json.loads(f.read_text())
        if rec.get("tm_project") == tm.project and rec.get("xray_project") == args.xray_project \
                and rec["execution_id"] not in known:
            plan["tm_test_executions"].append({"execution_id": rec["execution_id"],
                                               "name": f"mock run of {rec['tm_test_set']}", "status": "recorded locally"})

    xray = DemoXray()
    for te in xray.iter_issues("getTestExecutions", f'project = "{args.xray_project}"'):
        jira = te.get("jira") or {}
        if args.reseed or str(jira.get("summary") or "").startswith(MOCK_TAG):
            plan["xray_test_executions"].append({"xray_key": jira.get("key"), "xray_issue_id": te["issueId"],
                                                 "summary": jira.get("summary")})
    if args.reseed:
        for getter, bucket in (("getTestSets", "xray_test_sets"), ("getTests", "xray_tests")):
            plan[bucket] = [{"xray_key": (i.get("jira") or {}).get("key"), "xray_issue_id": i["issueId"],
                             "summary": (i.get("jira") or {}).get("summary")}
                            for i in xray.iter_issues(getter, f'project = "{args.xray_project}"')]

    counts = {k: len(v) for k, v in plan.items()}
    if not args.yes:
        emit({"applied": False, "xray_project": args.xray_project, "tm_project": tm.project, "counts": counts,
              **plan, "xray_seed_to_create": seed and seed_preview(seed)})
        return 0

    failed: list[str] = []

    def attempt(label: str, fn, *fn_args) -> None:
        try:
            fn(*fn_args)
            log(f"deleted {label}")
        except IntakeError as exc:
            failed.append(f"{label}: {exc}")
            log(f" FAILED {label}: {exc}")

    if plan["tm_test_executions"]:
        project_id = tm.project_id()
        for e in plan["tm_test_executions"]:
            attempt(f"TM test execution {e['execution_id']}", tm.delete_execution, project_id, e["execution_id"])
    for ts in plan["tm_test_sets"]:
        attempt(f"TM test set {ts['tm_test_set_key']}", tm.delete_test_set, ts["tm_test_set_key"])
    for c in plan["tm_test_cases"]:
        attempt(f"TM test case {c['tm_case_key']} ({c['xray_key']})", tm.delete_case, c["tm_case_key"])

    seeded: dict[str, Any] = {}
    xray_deletes_allowed = True
    if seed:
        # Seed first, delete second: if Jira rejects the seed, the old Xray data is still there.
        try:
            seeded = create_seed(xray, args.xray_project, seed)
        except IntakeError as exc:
            xray_deletes_allowed = False
            failed.append(f"Xray seed: {exc} — old Xray data was NOT deleted; fix and re-run")
            log(f" FAILED Xray seed: {exc}")
    if xray_deletes_allowed:
        for mutation, bucket, label in (("deleteTestExecution", "xray_test_executions", "Xray test execution"),
                                        ("deleteTestSet", "xray_test_sets", "Xray test set"),
                                        ("deleteTest", "xray_tests", "Xray test")):
            for e in plan[bucket]:
                attempt(f"{label} {e['xray_key']}", xray.delete, mutation, e["xray_issue_id"])

    # Local state that now points at deleted objects.
    if ws.root.exists():
        deleted_runs = {e["execution_id"] for e in plan["tm_test_executions"]}
        for f in ws.root.glob("execution-*.json"):
            if json.loads(f.read_text())["execution_id"] in deleted_runs:
                f.unlink()
        deleted_xray = {e["xray_key"] for e in plan["xray_test_executions"]} if xray_deletes_allowed else set()
        ws.write("xray-executions.json", [e for e in (ws.read("xray-executions.json") or [])
                                          if e.get("key") not in deleted_xray])
        (ws.root / "plan.json").unlink(missing_ok=True)
        if seeded:
            (ws.root / "snapshot.json").unlink(missing_ok=True)  # describes Xray tests that no longer exist

    core.history_log({"demo_reset": f"{args.xray_project}/{tm.project}", "counts": counts, "seeded": seeded,
                      "failed": len(failed)})
    emit({"applied": True, "xray_project": args.xray_project, "tm_project": tm.project, "counts": counts,
          "seeded": seeded, "failed": failed, "uip_calls": tm.calls})
    return 1 if failed else 0


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="xr demo", description=__doc__.split("\n\n")[0])
    p.add_argument("--workdir", default=".xray-tm-sync", help="state folder shared with xray-tm-sync")
    sub = p.add_subparsers(dest="cmd", required=True)

    def pair(c: argparse.ArgumentParser, required: bool = True) -> None:
        c.add_argument("--xray-project", required=required, metavar="JIRA_PROJECT_KEY", help="sandbox Jira project")
        c.add_argument("--tm-project", required=required, metavar="TM_PROJECT_KEY", help="sandbox TM project")

    c = sub.add_parser("sandbox", help="register / list / remove demo sandbox pairs")
    c.add_argument("action", choices=["list", "add", "remove"])
    pair(c, required=False)

    c = sub.add_parser("seed", help="create the demo Xray tests + Xray test set (dry run without --yes)")
    pair(c)
    c.add_argument("--seed", default=str(DEFAULT_SEED))
    c.add_argument("--yes", action="store_true")

    c = sub.add_parser("execute", help="mock manual TM execution of a TM test set (dry run without --yes)")
    pair(c)
    c.add_argument("--tm-test-set", required=True, metavar="TM_TEST_SET_KEY")
    c.add_argument("--outcomes", help='JSON {"UIP-3": "Failed", "UIP-4": {"result": "Failed", "failed_step": 2}}')
    c.add_argument("--yes", action="store_true")

    c = sub.add_parser("report", help="mock TM execution → [MOCK] Xray test execution (dry run without --yes)")
    pair(c)
    c.add_argument("--execution-id", required=True, metavar="TM_EXECUTION_ID")
    c.add_argument("--xray-test-plan", metavar="XRAY_KEY")
    c.add_argument("--test-environment", nargs="+", metavar="NAME",
                   help=f"Xray test environments that exist in the sandbox Jira project, e.g. {MOCK_TEST_ENVIRONMENT}")
    c.add_argument("--yes", action="store_true")

    c = sub.add_parser("reset", help="demo reset; --reseed also replaces all Xray data in the sandbox (dry run without --yes)")
    pair(c)
    c.add_argument("--reseed", action="store_true")
    c.add_argument("--seed", default=str(DEFAULT_SEED))
    c.add_argument("--yes", action="store_true")

    args = p.parse_args(argv)
    core.start_trail(Path(args.workdir), "xr demo", args.cmd, sys.argv[1:] if argv is None else argv)
    handler = {"sandbox": cmd_sandbox, "seed": cmd_seed, "execute": cmd_execute, "report": cmd_report,
               "reset": cmd_reset}[args.cmd]
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
