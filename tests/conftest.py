"""Shared fixtures: `xr sync` as `core` and `xr demo` as `demo`, a fake `uip`, a fake Xray."""
import json
import os
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "xray_pages.json"
FAKE_UIP = ROOT / "tests" / "fake_uip.py"
TM_PROJECT = "XRSYNC"


os.environ["XRAY_TM_SETTLE_SECONDS"] = "0"
from xr import demo, sync as core  # noqa: E402  (after the env var: sync reads it at import)


def raw_tests():
    return [t for page in json.loads(FIXTURE.read_text()) for t in page["data"]["page"]["results"]]


def by_key():
    return {x["xray_key"]: x for x in map(core.normalize_xray_test, raw_tests())}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the real Xray secrets file, sandbox registry or run history."""
    monkeypatch.setenv("XRAY_TM_SYNC_ENV", str(tmp_path / "no-secrets.env"))
    monkeypatch.setenv("XRAY_TM_DEMO_SANDBOXES", str(tmp_path / "sandboxes.json"))
    for name in ("XRAY_CLIENT_ID", "XRAY_CLIENT_SECRET", "XRAY_REGION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(core, "history_log", lambda entry: None)
    monkeypatch.setattr(core, "WORKERS", 1)
    monkeypatch.setattr(core, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(core, "_TRAIL", {})


@pytest.fixture
def env(tmp_path, monkeypatch):
    """(sync, tm_state, write_state, plan): `sync` runs xray_tm_sync.main against the fake uip."""
    state = tmp_path / "uip-state.json"
    monkeypatch.setenv("FAKE_UIP_STATE", str(state))
    monkeypatch.setenv("UIP_BIN", str(FAKE_UIP))
    work = tmp_path / "work"

    def sync(*argv):
        return core.main(["--workdir", str(work), *argv])

    def tm_state():
        return json.loads(state.read_text())

    def write_state(st):
        state.write_text(json.dumps(st))

    def plan():
        return json.loads((work / "plan.json").read_text())

    return sync, tm_state, write_state, plan


@pytest.fixture
def run_demo(tmp_path):
    def run(*argv):
        return demo.main(["--workdir", str(tmp_path / "work"), *argv])

    return run


def pull_plan_apply(sync, plan, fixture=FIXTURE):
    assert sync("pull", "--jql", "project = CALC", "--fixture", str(fixture)) == 0
    assert sync("plan", "--tm-project", TM_PROJECT) == 0
    summary = plan()["summary"]
    assert sync("apply", "--yes") == 0
    return summary


def changed_fixture(tmp_path, mutate) -> Path:
    pages = json.loads(FIXTURE.read_text())
    mutate(pages)
    path = tmp_path / f"changed-{len(list(tmp_path.glob('changed-*')))}.json"
    path.write_text(json.dumps(pages))
    return path


def tm_set(st, name):
    return next(s for s in st["sets"].values() if s["Name"] == name)


def xray_key_of(st, object_id):
    return st["values"][object_id][core.TM_FIELD_XRAY_KEY]["Value"]


def xray_credentials(monkeypatch):
    monkeypatch.setenv("XRAY_CLIENT_ID", "client-id-value")
    monkeypatch.setenv("XRAY_CLIENT_SECRET", "s3cr3t-value")


def fake_xray(monkeypatch, tm_state=None, write_state=None):
    """Fake Xray Cloud (GraphQL + results import) for core and demo, plus the demo's TM REST delete."""
    xray_credentials(monkeypatch)
    xray_tes: dict[str, dict] = {"999": {"issueId": "999", "jira": {"key": "CALC-999", "summary": "Sprint 12 regression",
                                                                     "description": "by hand"}}}
    xray_issues: dict[str, dict] = {"getTests": {}, "getTestSets": {}}
    imports: list[dict] = []

    def new_issue(bucket, summary):
        n = str(500 + sum(len(v) for v in xray_issues.values()))
        xray_issues[bucket][n] = {"issueId": n, "jira": {"key": f"CALC-{n}", "summary": summary, "description": ""}}
        return xray_issues[bucket][n]

    def handler(request):
        if request.url.path.endswith("authenticate"):
            return httpx.Response(200, text='"jwt"')
        body = json.loads(request.content)
        if request.url.path.endswith("/import/execution"):
            imports.append(body)
            n = str(300 + len(imports) - 1)
            xray_tes[n] = {"issueId": n, "jira": {"key": f"CALC-{n}", "summary": body["info"]["summary"],
                                                  "description": body["info"]["description"]}}
            return httpx.Response(200, json={"id": n, "key": f"CALC-{n}", "self": "https://x"})
        q, v = body.get("query", ""), body.get("variables", {})
        if "createTest(" in q:
            issue = new_issue("getTests", v["jira"]["fields"]["summary"])
            return httpx.Response(200, json={"data": {"createTest": {"test": issue, "warnings": []}}})
        if "createTestSet(" in q:
            issue = new_issue("getTestSets", v["jira"]["fields"]["summary"])
            issue["members"] = v["ids"]
            return httpx.Response(200, json={"data": {"createTestSet": {"testSet": issue, "warnings": []}}})
        for mutation, bucket in (("deleteTestExecution(", xray_tes), ("deleteTestSet(", xray_issues["getTestSets"]),
                                 ("deleteTest(", xray_issues["getTests"])):
            if mutation in q:
                del bucket[v["id"]]
                return httpx.Response(200, json={"data": {mutation[:-1]: "ok"}})
        for getter, bucket in (("getTestExecutions(", xray_tes), ("getTestSets(", xray_issues["getTestSets"]),
                               ("getTests(", xray_issues["getTests"])):
            if getter in q:
                results = list(bucket.values())
                return httpx.Response(200, json={"data": {"page": {"total": len(results), "start": 0, "limit": 100,
                                                                   "results": results}}})
        raise AssertionError(f"unexpected Xray request: {q[:80]}")

    transport = httpx.MockTransport(handler)
    real_core, real_demo = core.XrayClient, demo.DemoXray
    monkeypatch.setattr(core, "XrayClient", lambda: real_core(transport=transport))
    monkeypatch.setattr(demo, "DemoXray", lambda: real_demo(transport=transport))

    if tm_state is not None:
        def tm_rest(request):
            assert request.method == "DELETE" and request.headers["Authorization"] == "Bearer tok"
            assert request.url.path.startswith(f"/org/ten/testmanager_/api/v2/pid-{TM_PROJECT}/testexecutions/")
            st = tm_state()
            del st["executions"][request.url.path.rsplit("/", 1)[1]]
            write_state(st)
            return httpx.Response(204)

        monkeypatch.setattr(demo, "TM_HTTP_TRANSPORT", httpx.MockTransport(tm_rest))
    return {"test_executions": xray_tes, "issues": xray_issues, "imports": imports}
