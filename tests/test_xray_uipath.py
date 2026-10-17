import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "xray_pages.json"
FAKE_UIP = ROOT / "tests" / "fake_uip.py"

spec = importlib.util.spec_from_file_location("xray_uipath", ROOT / "jre-xray-uipath" / "scripts" / "xray_uipath.py")
xi = importlib.util.module_from_spec(spec)
sys.modules["xray_uipath"] = xi
spec.loader.exec_module(xi)


def raw_tests():
    return [t for page in json.loads(FIXTURE.read_text()) for t in page["data"]["page"]["results"]]


def by_key():
    return {x["xray_key"]: x for x in map(xi.normalize_xray_test, raw_tests())}


# --------------------------------------------------------------------------- mapping


def test_adf_description_becomes_plain_text():
    x = by_key()["CALC-101"]
    assert x["description"] == "Checks the happy path.\n\n- Chrome\n- Edge"


def test_wiki_markup_description_passes_through():
    assert by_key()["CALC-102"]["description"].startswith("h3. Wiki markup")


def test_manual_xray_test_maps_steps_precondition_and_labels():
    tm_case = xi.render_tm_case(by_key()["CALC-101"], "https://acme.atlassian.net")
    assert tm_case["name"] == "Login with valid credentials"
    assert tm_case["description"].endswith(
        "— Imported from Xray test CALC-101 (https://acme.atlassian.net/browse/CALC-101)"
    )
    assert tm_case["pre_condition"] == "[CALC-50] Registered user\nA registered user exists"
    assert tm_case["steps"][1] == {
        "description": "Enter user and password",
        "clipboardData": "user=alice\npassword=***",
        "expectedResult": "",
    }
    assert tm_case["labels"] == [
        "XR_Folder_/Login/Smoke",
        "XR_Imported",
        "XR_Label_login_page",
        "XR_Label_smoke",
        "XR_Set_CALC-200",
        "XR_Set_CALC-201",
        "XR_Type_Manual",
    ]


def test_cucumber_and_generic_go_into_description_without_steps():
    xs = by_key()
    cucumber = xi.render_tm_case(xs["CALC-102"], None)
    assert "Xray Cucumber scenario:\nScenario: Locked account" in cucumber["description"]
    assert cucumber["steps"] == []
    generic = xi.render_tm_case(xs["CALC-103"], None)
    assert "Xray generic definition:\nRun the nightly" in generic["description"]
    assert "XR_Folder_/" not in " ".join(generic["labels"])  # root repository folder is not a label


def test_modular_xray_test_keeps_expanded_steps_and_warns_on_truncated_preconditions():
    x = by_key()["CALC-104"]
    assert [s["action"] for s in x["steps"]][-1] == "Add an item and check out"
    assert len(x["steps"]) == 3
    assert any("2 of 12 Xray preconditions" in w for w in x["warnings"])


def test_long_summary_is_truncated():
    x = by_key()["CALC-101"] | {"summary": "x" * 400}
    assert len(xi.render_tm_case(x, None)["name"]) == xi.TM_NAME_MAX


def test_expanded_step_list_values_are_joined():
    raw = raw_tests()[0] | {"steps": [{"action": ["a", "b"], "data": None, "result": ["r"]}]}
    assert xi.normalize_xray_test(raw)["steps"] == [{"action": "a\nb", "data": "", "result": "r"}]


def test_tm_content_hash_ignores_whitespace_noise():
    a = xi.tm_content_hash("n", "d\r\n", [{"description": "s ", "clipboardData": None, "expectedResult": "e"}])
    b = xi.tm_content_hash("n", "d", [{"description": "s", "clipboardData": "", "expectedResult": "e"}])
    assert a == b


# --------------------------------------------------------------------------- Xray client


def test_xray_client_authenticates_and_pages(monkeypatch):
    monkeypatch.setenv("XRAY_CLIENT_ID", "id")
    monkeypatch.setenv("XRAY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("XRAY_REGION", "eu")
    pages = json.loads(FIXTURE.read_text())
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        if request.url.path == "/api/v2/authenticate":
            assert json.loads(request.content) == {"client_id": "id", "client_secret": "secret"}
            return httpx.Response(200, text='"jwt-token"')
        assert request.headers["Authorization"] == "Bearer jwt-token"
        start = json.loads(request.content)["variables"]["start"]
        return httpx.Response(200, json=pages[0] if start == 0 else pages[1])

    client = xi.XrayClient(transport=httpx.MockTransport(handler))
    tests = list(client.iter_tests("project = CALC", None, expand=True))
    assert [t["jira"]["key"] for t in tests] == ["CALC-101", "CALC-102", "CALC-103", "CALC-104"]
    assert seen[0] == "https://eu.xray.cloud.getxray.app/api/v2/authenticate"
    assert len(seen) == 3  # auth + 2 pages


def test_xray_graphql_errors_are_raised(monkeypatch):
    monkeypatch.setenv("XRAY_CLIENT_ID", "id")
    monkeypatch.setenv("XRAY_CLIENT_SECRET", "secret")

    def handler(request):
        if request.url.path.endswith("authenticate"):
            return httpx.Response(200, text='"t"')
        return httpx.Response(200, json={"errors": [{"message": "jql matched too many issues"}]})

    client = xi.XrayClient(transport=httpx.MockTransport(handler))
    with pytest.raises(xi.IntakeError, match="too many issues"):
        list(client.iter_tests("x", None, expand=True))


def test_missing_xray_credentials_fail_clearly(monkeypatch):
    monkeypatch.delenv("XRAY_CLIENT_ID", raising=False)
    with pytest.raises(xi.IntakeError, match="XRAY_CLIENT_ID"):
        xi.XrayClient()


# --------------------------------------------------------------------------- end to end with fake uip


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "uip-state.json"
    monkeypatch.setenv("FAKE_UIP_STATE", str(state))
    monkeypatch.setenv("UIP_BIN", str(FAKE_UIP))
    monkeypatch.setenv("XRAY_UIPATH_WORKERS", "1")
    monkeypatch.setattr(xi, "WORKERS", 1)
    monkeypatch.setattr(xi, "history_log", lambda entry: None)
    work = tmp_path / "work"

    def run(*argv):
        return xi.main(["--workdir", str(work), *argv])

    def tm_state():
        return json.loads(state.read_text())

    def write_state(st):
        state.write_text(json.dumps(st))

    def plan():
        return json.loads((work / "plan.json").read_text())

    return run, tm_state, write_state, plan


def drop_local_index():
    import os

    for f in (Path(os.environ["FAKE_UIP_STATE"]).parent / "work").glob("index-*.json"):
        f.unlink()


def pull_plan_apply(run, plan):
    assert run("pull", "--jql", "project = CALC", "--fixture", str(FIXTURE)) == 0
    assert run("plan", "--tm-project", "XRSYNC") == 0
    summary = plan()["summary"]
    assert run("apply", "--yes") == 0
    return summary


def test_first_intake_creates_everything(env):
    run, tm_state, _, plan = env
    summary = pull_plan_apply(run, plan)
    assert summary == {"create": 4}
    st = tm_state()
    assert sorted(st["fields"]) == ["TestCase:Xray Intake", "TestCase:Xray Key"]
    assert len(st["cases"]) == 4
    calc101 = next(c for c in st["cases"].values() if c["Name"] == "Login with valid credentials")
    assert calc101["PreCondition"].startswith("[CALC-50]")
    assert [s["Description"] for s in st["steps"][calc101["Id"]]][0] == "Open the login page"
    assert st["values"][calc101["Id"]]["Xray Key"]["Value"] == "CALC-101"
    intake = json.loads(st["values"][calc101["Id"]]["Xray Intake"]["Value"])
    assert intake["xrayKey"] == "CALC-101" and len(intake["tmHash"]) == 16
    assert "XR_Type_Manual" in st["labels"][calc101["Id"]]


def test_second_run_is_idempotent(env):
    run, tm_state, _, plan = env
    pull_plan_apply(run, plan)
    writes_before = len(tm_state()["calls"])
    summary = pull_plan_apply(run, plan)
    assert summary == {"unchanged": 4}
    new_calls = tm_state()["calls"][writes_before:]
    assert not [c for c in new_calls if any(w in c for w in ("create", "update", "add", "delete", "remove"))]


def test_xray_change_updates_and_tm_edit_is_drift(env, tmp_path):
    run, tm_state, write_state, plan = env
    pull_plan_apply(run, plan)

    # Hand-edit CALC-102's TM test case in TM → drift.
    st = tm_state()
    c102 = next(c for c in st["cases"].values() if c["Name"].startswith("Locked account"))
    c102["Description"] += "\nedited in TM"
    write_state(st)

    # Change CALC-101 and CALC-102 in Xray.
    pages = json.loads(FIXTURE.read_text())
    t101, t102 = pages[0]["data"]["page"]["results"]
    t101["steps"].append({"id": "s4", "action": "Sign out", "data": "", "result": "Login form is shown"})
    t101["jira"]["labels"] = ["smoke"]
    t102["gherkin"] += "\n  And the account stays locked"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(pages))

    assert run("pull", "--jql", "project = CALC", "--fixture", str(changed)) == 0
    assert run("plan", "--tm-project", "XRSYNC") == 0
    actions = {i["xray_key"]: i["action"] for i in plan()["items"]}
    assert actions == {"CALC-101": "update", "CALC-102": "drift", "CALC-103": "unchanged", "CALC-104": "unchanged"}
    assert run("apply", "--yes") == 0

    st = tm_state()
    c101 = next(c for c in st["cases"].values() if c["Name"] == "Login with valid credentials")
    assert [s["Description"] for s in st["steps"][c101["Id"]]][-1] == "Sign out"
    assert len(st["steps"][c101["Id"]]) == 4
    assert "XR_Label_login_page" not in st["labels"][c101["Id"]]  # stale XR_ label removed
    assert "edited in TM" in next(c for c in st["cases"].values() if c["Id"] == c102["Id"])["Description"]

    # After the update the TM test case is clean again.
    assert run("plan", "--tm-project", "XRSYNC") == 0
    assert plan()["summary"] == {"unchanged": 3, "drift": 1}


def test_user_labels_survive_update(env, tmp_path):
    run, tm_state, write_state, plan = env
    pull_plan_apply(run, plan)
    st = tm_state()
    c101 = next(c for c in st["cases"].values() if c["Name"] == "Login with valid credentials")
    st["labels"][c101["Id"]].append("team-red")
    write_state(st)
    pages = json.loads(FIXTURE.read_text())
    pages[0]["data"]["page"]["results"][0]["jira"]["summary"] = "Login with valid credentials (v2)"
    changed = tmp_path / "c.json"
    changed.write_text(json.dumps(pages))
    run("pull", "--jql", "x", "--fixture", str(changed))
    run("plan", "--tm-project", "XRSYNC")
    run("apply", "--yes")
    assert "team-red" in tm_state()["labels"][c101["Id"]]


def test_partial_intake_is_repaired_and_lost_index_is_rebuilt(env):
    run, tm_state, write_state, plan = env
    run("pull", "--jql", "x", "--fixture", str(FIXTURE))
    run("plan", "--tm-project", "XRSYNC")
    st = tm_state()
    st["fail_on"] = "objectlabel add"
    write_state(st)
    assert run("apply", "--yes") == 1  # every item failed after the origin marker was written
    st = tm_state()
    st["fail_on"] = None
    write_state(st)
    drop_local_index()  # simulate another machine
    run("plan", "--tm-project", "XRSYNC")
    assert plan()["summary"] == {"update": 4}
    assert all("no intake record" in i["reason"] for i in plan()["items"])
    assert run("apply", "--yes") == 0
    run("plan", "--tm-project", "XRSYNC")
    assert plan()["summary"] == {"unchanged": 4}
    assert len(tm_state()["cases"]) == 4  # no duplicates


def test_duplicate_origin_marker_is_a_conflict(env):
    run, tm_state, write_state, plan = env
    pull_plan_apply(run, plan)
    st = tm_state()
    ids = list(st["cases"])
    st["values"][ids[1]]["Xray Key"]["Value"] = st["values"][ids[0]]["Xray Key"]["Value"]
    write_state(st)
    drop_local_index()
    run("plan", "--tm-project", "XRSYNC")
    assert "conflict" in plan()["summary"]


def test_apply_requires_yes(env):
    run, _, _, plan = env
    run("pull", "--jql", "x", "--fixture", str(FIXTURE))
    run("plan", "--tm-project", "XRSYNC")
    assert run("apply") == 2


def test_accept_drift_overwrites_only_named_xray_tests(env, tmp_path):
    run, tm_state, write_state, plan = env
    pull_plan_apply(run, plan)
    st = tm_state()
    for c in st["cases"].values():
        c["Description"] += " (edited in TM)"
    write_state(st)
    pages = json.loads(FIXTURE.read_text())
    for t in pages[0]["data"]["page"]["results"]:
        t["jira"]["summary"] += " v2"
    changed = tmp_path / "c.json"
    changed.write_text(json.dumps(pages))
    run("pull", "--jql", "x", "--fixture", str(changed))
    run("plan", "--tm-project", "XRSYNC", "--accept-drift", "CALC-101")
    actions = {i["xray_key"]: i["action"] for i in plan()["items"]}
    assert actions["CALC-101"] == "update" and actions["CALC-102"] == "drift"
    assert run("apply", "--yes") == 0
    names = {c["Name"] for c in tm_state()["cases"].values()}
    assert "Login with valid credentials v2" in names and "Locked account cannot sign in v2" not in names


# --------------------------------------------------------------------------- test sets, mock execution, report


def test_xray_test_sets_become_labels():
    tm_case = xi.render_tm_case(by_key()["CALC-101"], None)
    assert {"XR_Set_CALC-200", "XR_Set_CALC-201"} <= set(tm_case["labels"])


def tm_set(st, name):
    return next(s for s in st["sets"].values() if s["Name"] == name)


def member_keys(st, ts):
    return sorted(st["cases"][cid]["TestCaseKey"] for cid in ts["members"])


def xray_key_of(st, tm_case_id):
    return st["values"][tm_case_id]["Xray Key"]["Value"]


def test_testsets_mirror_xray_test_sets(env, tmp_path, capsys):
    run, tm_state, write_state, plan = env
    pull_plan_apply(run, plan)
    capsys.readouterr()
    assert run("testsets", "--tm-project", "XRSYNC") == 0  # dry run
    dry = json.loads(capsys.readouterr().out)
    assert [(i["xray_test_set_key"], i["action"], len(i["add"])) for i in dry["test_sets"]] == [
        ("CALC-200", "create", 3), ("CALC-201", "create", 1)]
    assert not tm_state()["sets"]

    assert run("testsets", "--tm-project", "XRSYNC", "--yes") == 0
    st = tm_state()
    login = tm_set(st, "Login regression")
    assert sorted(xray_key_of(st, cid) for cid in login["members"]) == ["CALC-101", "CALC-102", "CALC-104"]
    assert st["values"][login["Id"]]["Xray Key"]["Value"] == "CALC-200"

    # A tester adds a TM-only test case by hand; Xray drops CALC-102 from the Xray test set.
    st["cases"]["tm-only"] = {"Id": "tm-only", "TestCaseKey": "XRSYNC:99", "Name": "Exploratory", "Description": ""}
    st["values"]["tm-only"] = {"Xray Key": {"Name": "Xray Key", "Value": ""}}
    login["members"].append("tm-only")
    write_state(st)
    pages = json.loads(FIXTURE.read_text())
    pages[0]["data"]["page"]["results"][1]["testSets"] = {"total": 0, "results": []}
    changed = tmp_path / "c.json"
    changed.write_text(json.dumps(pages))
    run("pull", "--jql", "x", "--fixture", str(changed))
    capsys.readouterr()
    assert run("testsets", "--tm-project", "XRSYNC", "--yes") == 0
    items = {i["xray_test_set_key"]: i for i in json.loads(capsys.readouterr().out)["test_sets"]}
    assert items["CALC-200"]["action"] == "update" and len(items["CALC-200"]["remove"]) == 1
    assert items["CALC-201"]["action"] == "unchanged"
    st = tm_state()
    remaining = sorted(xray_key_of(st, cid) or st["cases"][cid]["TestCaseKey"]
                       for cid in tm_set(st, "Login regression")["members"])
    assert remaining == ["CALC-101", "CALC-104", "XRSYNC:99"]  # CALC-102 removed, TM-only test case kept


def mock_execution(env, tmp_path, capsys):
    run, tm_state, _, plan = env
    pull_plan_apply(run, plan)
    run("testsets", "--tm-project", "XRSYNC", "--yes")
    login_key = tm_set(tm_state(), "Login regression")["TestSetKey"]
    outcomes = tmp_path / "outcomes.json"
    outcomes.write_text(json.dumps({"CALC-101": {"result": "Failed", "failed_step": 2, "comment": "button missing"}}))
    capsys.readouterr()
    assert run("execute", "--tm-project", "XRSYNC", "--tm-test-set", login_key, "--mock",
               "--outcomes", str(outcomes), "--yes") == 0
    return json.loads(capsys.readouterr().out)


def test_execute_requires_mock_flag(env):
    run, *_ = env
    assert run("execute", "--tm-project", "XRSYNC", "--tm-test-set", "XRSYNC:100", "--yes") == 2


def test_mock_execution_records_step_results(env, tmp_path, capsys):
    record = mock_execution(env, tmp_path, capsys)
    results = {r["xray_key"]: r for r in record["results"]}
    assert results["CALC-101"]["result"] == "Failed"
    assert results["CALC-101"]["step_results"] == ["Passed", "Failed", "None"]
    assert results["CALC-104"]["result"] == "Passed"
    st = env[1]()
    log101 = results["CALC-101"]["tm_case_log_id"]
    assert [r["Comment"] for r in st["steplogs"][log101]][1] == "[MOCK] button missing"


def test_report_refuses_mock_results_outside_sandbox(env, tmp_path, capsys):
    run, *_ = env
    record = mock_execution(env, tmp_path, capsys)
    assert run("report", "--tm-project", "XRSYNC", "--execution-id", record["execution_id"]) == 2
    assert "sandbox" in json.loads(capsys.readouterr().out)["error"]
    assert run("report", "--tm-project", "XRSYNC", "--execution-id", record["execution_id"],
               "--sandbox-project", "OTHER") == 2
    assert "not in the sandbox project OTHER" in json.loads(capsys.readouterr().out)["error"]


def test_report_builds_and_imports_xray_json(env, tmp_path, capsys, monkeypatch):
    run, *_ = env
    record = mock_execution(env, tmp_path, capsys)
    monkeypatch.setenv("XRAY_CLIENT_ID", "id")
    monkeypatch.setenv("XRAY_CLIENT_SECRET", "secret")
    sent = []

    def handler(request):
        if request.url.path.endswith("authenticate"):
            return httpx.Response(200, text='"t"')
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "1", "key": "CALC-300", "self": "https://x"})

    real = xi.XrayClient
    monkeypatch.setattr(xi, "XrayClient", lambda: real(transport=httpx.MockTransport(handler)))
    assert run("report", "--tm-project", "XRSYNC", "--execution-id", record["execution_id"],
               "--sandbox-project", "CALC", "--xray-test-plan", "CALC-299", "--yes") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["xray_test_execution"]["key"] == "CALC-300"
    body = sent[0]
    assert body["info"]["summary"].startswith("[MOCK] ")
    assert body["info"]["testEnvironments"] == ["TM-mock"] and body["info"]["testPlanKey"] == "CALC-299"
    tests = {t["testKey"]: t for t in body["tests"]}
    assert tests["CALC-101"]["status"] == "FAILED"
    assert [s["status"] for s in tests["CALC-101"]["steps"]] == ["PASSED", "FAILED", "TODO"]
    assert "steps" not in tests["CALC-102"]  # Cucumber: no step results
    assert "steps" not in tests["CALC-104"]  # called Xray test inlined: step counts don't map back
    assert tests["CALC-104"]["status"] == "PASSED"
    assert tests["CALC-101"]["start"] == "2026-10-17T00:00:01+00:00"


def test_tm_owned_steps_survive_update_and_are_not_drift(env, tmp_path):
    """Xray test without Xray steps (description only): TM steps generated in TM are TM-owned."""
    run, tm_state, write_state, plan = env
    pull_plan_apply(run, plan)
    st = tm_state()
    c103 = next(c for c in st["cases"].values() if c["Name"] == "API contract suite")
    st["steps"][c103["Id"]] = [{"Id": "gen-1", "OrderNo": 0, "Description": "Generated by GenAI in TM",
                                "ExpectedResult": "ok", "ClipboardData": None}]
    write_state(st)

    pages = json.loads(FIXTURE.read_text())
    pages[1]["data"]["page"]["results"][0]["jira"]["description"] = "Now also checks the error contract."
    changed = tmp_path / "c.json"
    changed.write_text(json.dumps(pages))
    run("pull", "--jql", "x", "--fixture", str(changed))
    run("plan", "--tm-project", "XRSYNC")
    assert {i["xray_key"]: i["action"] for i in plan()["items"]}["CALC-103"] == "update"
    assert run("apply", "--yes") == 0

    st = tm_state()
    assert [s["Description"] for s in st["steps"][c103["Id"]]] == ["Generated by GenAI in TM"]
    assert st["cases"][c103["Id"]]["Description"].startswith("Now also checks the error contract.")
    run("plan", "--tm-project", "XRSYNC")
    assert {i["xray_key"]: i["action"] for i in plan()["items"]}["CALC-103"] == "unchanged"


# --------------------------------------------------------------------------- reset


def xray_and_tm_rest(monkeypatch, tm_state, write_state):
    """Fake Xray (import, list, delete test executions) and the TM REST delete of TM test executions."""
    monkeypatch.setenv("XRAY_CLIENT_ID", "id")
    monkeypatch.setenv("XRAY_CLIENT_SECRET", "secret")
    xray_tes: dict[str, dict] = {}
    xray_issues: dict[str, dict] = {"getTests": {}, "getTestSets": {}}
    xray_tes["999"] = {"issueId": "999", "jira": {"key": "CALC-999", "summary": "Sprint 12 regression",
                                                  "description": "by hand"}}

    def new_issue(bucket, summary):
        n = str(500 + sum(len(v) for v in xray_issues.values()))
        xray_issues[bucket][n] = {"issueId": n, "jira": {"key": f"CALC-{n}", "summary": summary, "description": ""}}
        return xray_issues[bucket][n]

    def xray(request):
        if request.url.path.endswith("authenticate"):
            return httpx.Response(200, text='"t"')
        body = json.loads(request.content)
        q, v = body.get("query", ""), body.get("variables", {})
        if "createTest(" in q:
            issue = new_issue("getTests", v["jira"]["fields"]["summary"])
            return httpx.Response(200, json={"data": {"createTest": {"test": issue, "warnings": []}}})
        if "createTestSet(" in q:
            issue = new_issue("getTestSets", v["jira"]["fields"]["summary"])
            issue["members"] = v["ids"]
            return httpx.Response(200, json={"data": {"createTestSet": {"testSet": issue, "warnings": []}}})
        for mutation, bucket in (("deleteTest(", "getTests"), ("deleteTestSet(", "getTestSets")):
            if mutation in q:
                del xray_issues[bucket][v["id"]]
                return httpx.Response(200, json={"data": {mutation[:-1]: "ok"}})
        for getter in ("getTests", "getTestSets"):
            if f"{getter}(" in q:
                results = list(xray_issues[getter].values())
                return httpx.Response(200, json={"data": {"page": {"total": len(results), "start": 0, "limit": 100,
                                                                   "results": results}}})
        if request.url.path.endswith("/import/execution"):
            n = str(300 + len(xray_tes) - 1)
            xray_tes[n] = {"issueId": n, "jira": {"key": f"CALC-{n}", "summary": body["info"]["summary"],
                                                  "description": body["info"]["description"]}}
            return httpx.Response(200, json={"id": n, "key": f"CALC-{n}"})
        if "deleteTestExecution" in body["query"]:
            del xray_tes[body["variables"]["id"]]
            return httpx.Response(200, json={"data": {"deleteTestExecution": "ok"}})
        results = list(xray_tes.values())
        return httpx.Response(200, json={"data": {"page": {"total": len(results), "start": 0, "limit": 100, "results": results}}})

    def tm_rest(request):
        assert request.method == "DELETE" and request.headers["Authorization"] == "Bearer tok"
        assert request.url.path.startswith("/org/ten/testmanager_/api/v2/pid-XRSYNC/testexecutions/")
        st = tm_state()
        del st["executions"][request.url.path.rsplit("/", 1)[1]]
        write_state(st)
        return httpx.Response(204)

    real = xi.XrayClient
    monkeypatch.setattr(xi, "XrayClient", lambda: real(transport=httpx.MockTransport(xray)))
    monkeypatch.setattr(xi, "TM_HTTP_TRANSPORT", httpx.MockTransport(tm_rest))
    return xray_tes, xray_issues


def test_reset_deletes_only_what_the_skill_created(env, tmp_path, capsys, monkeypatch):
    run, tm_state, write_state, plan = env
    xray_tes, _ = xray_and_tm_rest(monkeypatch, tm_state, write_state)
    record = mock_execution(env, tmp_path, capsys)
    assert run("report", "--tm-project", "XRSYNC", "--execution-id", record["execution_id"],
               "--sandbox-project", "CALC", "--yes") == 0
    assert sorted(xray_tes) == ["300", "999"]
    st = tm_state()
    st["cases"]["tm-only"] = {"Id": "tm-only", "TestCaseKey": "XRSYNC:99", "Name": "Exploratory", "Description": ""}
    write_state(st)
    capsys.readouterr()

    assert run("reset", "--tm-project", "XRSYNC") == 0  # dry run
    dry = json.loads(capsys.readouterr().out)
    assert dry["counts"] == {"xray_test_executions": 1, "xray_test_sets": 0, "xray_tests": 0,
                             "tm_test_executions": 1, "tm_test_sets": 2, "tm_test_cases": 4, "kept": 0}
    assert len(tm_state()["cases"]) == 5

    assert run("reset", "--tm-project", "XRSYNC", "--yes") == 0
    st = tm_state()
    assert list(st["cases"]) == ["tm-only"]  # TM-only test case untouched
    assert not st["sets"] and not st["executions"]
    assert list(xray_tes) == ["999"]  # the hand-made Xray test execution CALC-999 was never a candidate
    assert not list((tmp_path / "work").glob("execution-*.json"))

    # Afterwards the demo starts from scratch.
    assert run("pull", "--jql", "x", "--fixture", str(FIXTURE)) == 0
    assert run("plan", "--tm-project", "XRSYNC") == 0
    assert plan()["summary"] == {"create": 4}


def test_reset_keeps_real_results_and_respects_scope(env, tmp_path, capsys, monkeypatch):
    run, tm_state, write_state, plan = env
    xray_tes, _ = xray_and_tm_rest(monkeypatch, tm_state, write_state)
    pull_plan_apply(run, plan)
    xray_tes["301"] = {"issueId": "301", "jira": {"key": "CALC-301", "summary": "UiPath Test Manager test set XRSYNC:100",
                                                  "description": f"… {xi.REPORT_MARKER}."}}
    capsys.readouterr()
    assert run("reset", "--tm-project", "XRSYNC", "--scope", "xray", "--xray-project", "CALC") == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["counts"]["xray_test_executions"] == 0 and dry["kept"][0]["xray_key"] == "CALC-301"
    assert dry["counts"]["tm_test_cases"] == 0  # scope xray: TM untouched
    assert run("reset", "--tm-project", "XRSYNC", "--xray-project", "OTHER", "--yes") == 0
    assert len(tm_state()["cases"]) == 4  # nothing came from Jira project OTHER


def test_reset_reseed_recreates_the_demo_in_the_sandbox(env, tmp_path, capsys, monkeypatch):
    run, tm_state, write_state, plan = env
    xray_tes, xray_issues = xray_and_tm_rest(monkeypatch, tm_state, write_state)
    for summary in ("old test A", "old test B"):
        n = str(700 + len(xray_issues["getTests"]))
        xray_issues["getTests"][n] = {"issueId": n, "jira": {"key": f"CALC-{n}", "summary": summary}}
    xray_issues["getTestSets"]["800"] = {"issueId": "800", "jira": {"key": "CALC-800", "summary": "old set"}}
    pull_plan_apply(run, plan)
    capsys.readouterr()

    assert run("reset", "--tm-project", "XRSYNC", "--reseed") == 2  # needs the sandbox project
    capsys.readouterr()
    assert run("reset", "--tm-project", "XRSYNC", "--xray-project", "CALC", "--reseed") == 0  # dry run
    dry = json.loads(capsys.readouterr().out)
    assert dry["counts"]["xray_tests"] == 2 and dry["counts"]["xray_test_sets"] == 1
    assert dry["counts"]["xray_test_executions"] == 1  # reseed wipes every Xray test execution in the sandbox
    assert len(dry["xray_seed_to_create"]["xray_tests"]) == 8
    assert len(xray_issues["getTests"]) == 2

    assert run("reset", "--tm-project", "XRSYNC", "--xray-project", "CALC", "--reseed", "--yes") == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["seeded"]["xray_tests"]) == 8
    tests = {i["jira"]["summary"] for i in xray_issues["getTests"].values()}
    assert len(tests) == 8 and "old test A" not in tests and "Buy UiPath stock with a market order" in tests
    sets = list(xray_issues["getTestSets"].values())
    assert [s["jira"]["summary"] for s in sets] == ["Stock trading regression"] and len(sets[0]["members"]) == 8
    assert not xray_tes and not tm_state()["cases"] and not tm_state()["sets"]


def test_reset_reseed_keeps_old_xray_data_when_seeding_fails(env, capsys, monkeypatch):
    run, tm_state, write_state, plan = env

    def broken(self, *a, **k):
        raise xi.IntakeError("Field 'description' cannot be set")

    monkeypatch.setattr(xi.XrayClient, "create_test", broken)
    _, xray_issues = xray_and_tm_rest(monkeypatch, tm_state, write_state)
    xray_issues["getTests"]["700"] = {"issueId": "700", "jira": {"key": "CALC-700", "summary": "old test"}}
    assert run("reset", "--tm-project", "XRSYNC", "--xray-project", "CALC", "--reseed", "--yes") == 1
    out = json.loads(capsys.readouterr().out)
    assert "NOT deleted" in out["failed"][0]
    assert "700" in xray_issues["getTests"]
