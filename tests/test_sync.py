"""xray-tm-sync (production): mapping, Xray client, intake, TM test sets, real results → Xray, evidence."""
import json
import os
import stat

import httpx
import pytest
from conftest import (FIXTURE, TM_PROJECT, by_key, changed_fixture, core, fake_xray, pull_plan_apply, raw_tests,
                      tm_set, xray_credentials, xray_key_of)

# --------------------------------------------------------------------------- mapping


def test_adf_description_becomes_plain_text():
    assert by_key()["CALC-101"]["description"] == "Checks the happy path.\n\n- Chrome\n- Edge"


def test_wiki_markup_description_passes_through():
    assert by_key()["CALC-102"]["description"].startswith("h3. Wiki markup")


def test_manual_xray_test_maps_steps_precondition_and_labels():
    tm_case = core.render_tm_case(by_key()["CALC-101"], "https://acme.atlassian.net")
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
    cucumber = core.render_tm_case(xs["CALC-102"], None)
    assert "Xray Cucumber scenario:\nScenario: Locked account" in cucumber["description"]
    assert cucumber["steps"] == []
    generic = core.render_tm_case(xs["CALC-103"], None)
    assert "Xray generic definition:\nRun the nightly" in generic["description"]
    assert "XR_Folder_/" not in " ".join(generic["labels"])  # root repository folder is not a label


def test_modular_xray_test_keeps_expanded_steps_and_warns_on_truncated_preconditions():
    x = by_key()["CALC-104"]
    assert [s["action"] for s in x["steps"]][-1] == "Add an item and check out"
    assert len(x["steps"]) == 3
    assert any("2 of 12 Xray preconditions" in w for w in x["warnings"])


def test_long_summary_is_truncated():
    x = by_key()["CALC-101"] | {"summary": "x" * 400}
    assert len(core.render_tm_case(x, None)["name"]) == core.TM_NAME_MAX


def test_expanded_step_list_values_are_joined():
    raw = raw_tests()[0] | {"steps": [{"action": ["a", "b"], "data": None, "result": ["r"]}]}
    assert core.normalize_xray_test(raw)["steps"] == [{"action": "a\nb", "data": "", "result": "r"}]


def test_tm_content_hash_ignores_whitespace_noise():
    a = core.tm_content_hash("n", "d\r\n", [{"description": "s ", "clipboardData": None, "expectedResult": "e"}])
    b = core.tm_content_hash("n", "d", [{"description": "s", "clipboardData": "", "expectedResult": "e"}])
    assert a == b


def test_xray_test_sets_become_labels():
    assert {"XR_Set_CALC-200", "XR_Set_CALC-201"} <= set(core.render_tm_case(by_key()["CALC-101"], None)["labels"])


# --------------------------------------------------------------------------- Xray client and secrets


def test_xray_client_authenticates_and_pages(monkeypatch):
    xray_credentials(monkeypatch)
    monkeypatch.setenv("XRAY_REGION", "eu")
    pages = json.loads(FIXTURE.read_text())
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        if request.url.path == "/api/v2/authenticate":
            assert json.loads(request.content) == {"client_id": "client-id-value", "client_secret": "s3cr3t-value"}
            return httpx.Response(200, text='"jwt-token"')
        assert request.headers["Authorization"] == "Bearer jwt-token"
        start = json.loads(request.content)["variables"]["start"]
        return httpx.Response(200, json=pages[0] if start == 0 else pages[1])

    client = core.XrayClient(transport=httpx.MockTransport(handler))
    tests = list(client.iter_tests("project = CALC", None, expand=True))
    assert [t["jira"]["key"] for t in tests] == ["CALC-101", "CALC-102", "CALC-103", "CALC-104"]
    assert seen[0] == "https://eu.xray.cloud.getxray.app/api/v2/authenticate"
    assert len(seen) == 3  # auth + 2 pages


def test_xray_graphql_errors_are_raised(monkeypatch):
    xray_credentials(monkeypatch)

    def handler(request):
        if request.url.path.endswith("authenticate"):
            return httpx.Response(200, text='"t"')
        return httpx.Response(200, json={"errors": [{"message": "jql matched too many issues"}]})

    client = core.XrayClient(transport=httpx.MockTransport(handler))
    with pytest.raises(core.IntakeError, match="too many issues"):
        list(client.iter_tests("x", None, expand=True))


def test_missing_xray_credentials_fail_clearly():
    with pytest.raises(core.IntakeError, match="XRAY_CLIENT_ID"):
        core.XrayClient()


def test_xray_secrets_file_is_read_but_env_wins(tmp_path, monkeypatch):
    secrets = tmp_path / "xray.env"
    secrets.write_text("# comment\nXRAY_CLIENT_ID=from-file\nexport XRAY_CLIENT_SECRET='file-secret'\nOTHER=x\n")
    secrets.chmod(0o600)
    monkeypatch.setenv("XRAY_TM_SYNC_ENV", str(secrets))
    monkeypatch.setenv("XRAY_CLIENT_ID", "from-env")
    client = core.XrayClient()
    assert (client.client_id, client.client_secret) == ("from-env", "file-secret")
    assert "OTHER" not in os.environ


def test_xray_secrets_file_readable_by_others_is_refused(tmp_path, monkeypatch):
    secrets = tmp_path / "xray.env"
    secrets.write_text("XRAY_CLIENT_ID=a\nXRAY_CLIENT_SECRET=b\n")
    secrets.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
    monkeypatch.setenv("XRAY_TM_SYNC_ENV", str(secrets))
    with pytest.raises(core.IntakeError, match="chmod 600"):
        core.XrayClient()


# --------------------------------------------------------------------------- intake end to end (fake uip)


def drop_local_index(tmp_path):
    for f in (tmp_path / "work").glob("index-*.json"):
        f.unlink()


def test_first_intake_creates_everything(env):
    sync, tm_state, _, plan = env
    assert pull_plan_apply(sync, plan) == {"create": 4}
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


def test_custom_field_write_survives_the_provisioning_race(env):
    """TM clobbers values written right after creation; the write is retried until it reads back."""
    sync, tm_state, write_state, plan = env
    sync("pull", "--jql", "x", "--fixture", str(FIXTURE))
    sync("plan", "--tm-project", TM_PROJECT)
    st = tm_state()
    st["clobber_writes"] = 2
    write_state(st)
    assert sync("apply", "--yes") == 0
    st = tm_state()
    assert sorted(xray_key_of(st, cid) for cid in st["cases"]) == ["CALC-101", "CALC-102", "CALC-103", "CALC-104"]


def test_origin_marker_field_name_can_match_the_avalon_mcp_server(env, monkeypatch):
    sync, tm_state, _, plan = env
    monkeypatch.setattr(core, "TM_FIELD_XRAY_KEY", "XRAY_ID")
    pull_plan_apply(sync, plan)
    st = tm_state()
    assert "TestCase:XRAY_ID" in st["fields"] and "TestCase:Xray Key" not in st["fields"]
    assert sorted(v["XRAY_ID"]["Value"] for v in st["values"].values()) == ["CALC-101", "CALC-102", "CALC-103",
                                                                           "CALC-104"]


def test_second_run_is_idempotent(env):
    sync, tm_state, _, plan = env
    pull_plan_apply(sync, plan)
    writes_before = len(tm_state()["calls"])
    assert pull_plan_apply(sync, plan) == {"unchanged": 4}
    new_calls = tm_state()["calls"][writes_before:]
    assert not [c for c in new_calls if any(w in c for w in ("create", "update", "add", "delete", "remove"))]


def test_xray_change_updates_and_tm_edit_is_drift(env, tmp_path):
    sync, tm_state, write_state, plan = env
    pull_plan_apply(sync, plan)

    st = tm_state()  # hand-edit CALC-102's TM test case → drift
    c102 = next(c for c in st["cases"].values() if c["Name"].startswith("Locked account"))
    c102["Description"] += "\nedited in TM"
    write_state(st)

    def mutate(pages):
        t101, t102 = pages[0]["data"]["page"]["results"]
        t101["steps"].append({"id": "s4", "action": "Sign out", "data": "", "result": "Login form is shown"})
        t101["jira"]["labels"] = ["smoke"]
        t102["gherkin"] += "\n  And the account stays locked"

    assert sync("pull", "--jql", "project = CALC", "--fixture", str(changed_fixture(tmp_path, mutate))) == 0
    assert sync("plan", "--tm-project", TM_PROJECT) == 0
    actions = {i["xray_key"]: i["action"] for i in plan()["items"]}
    assert actions == {"CALC-101": "update", "CALC-102": "drift", "CALC-103": "unchanged", "CALC-104": "unchanged"}
    assert sync("apply", "--yes") == 0

    st = tm_state()
    c101 = next(c for c in st["cases"].values() if c["Name"] == "Login with valid credentials")
    assert [s["Description"] for s in st["steps"][c101["Id"]]][-1] == "Sign out"
    assert len(st["steps"][c101["Id"]]) == 4
    assert "XR_Label_login_page" not in st["labels"][c101["Id"]]  # stale XR_ label removed
    assert "edited in TM" in st["cases"][c102["Id"]]["Description"]
    assert sync("plan", "--tm-project", TM_PROJECT) == 0
    assert plan()["summary"] == {"unchanged": 3, "drift": 1}


def test_user_labels_survive_update(env, tmp_path):
    sync, tm_state, write_state, plan = env
    pull_plan_apply(sync, plan)
    st = tm_state()
    c101 = next(c for c in st["cases"].values() if c["Name"] == "Login with valid credentials")
    st["labels"][c101["Id"]].append("team-red")
    write_state(st)

    def mutate(pages):
        pages[0]["data"]["page"]["results"][0]["jira"]["summary"] = "Login with valid credentials (v2)"

    sync("pull", "--jql", "x", "--fixture", str(changed_fixture(tmp_path, mutate)))
    sync("plan", "--tm-project", TM_PROJECT)
    sync("apply", "--yes")
    assert "team-red" in tm_state()["labels"][c101["Id"]]


def test_partial_intake_is_repaired_and_lost_index_is_rebuilt(env, tmp_path):
    sync, tm_state, write_state, plan = env
    sync("pull", "--jql", "x", "--fixture", str(FIXTURE))
    sync("plan", "--tm-project", TM_PROJECT)
    st = tm_state()
    st["fail_on"] = "objectlabel add"
    write_state(st)
    assert sync("apply", "--yes") == 1  # every item failed after the origin marker was written
    st = tm_state()
    st["fail_on"] = None
    write_state(st)
    drop_local_index(tmp_path)  # simulate another machine
    sync("plan", "--tm-project", TM_PROJECT)
    assert plan()["summary"] == {"update": 4}
    assert all("no intake record" in i["reason"] for i in plan()["items"])
    assert sync("apply", "--yes") == 0
    sync("plan", "--tm-project", TM_PROJECT)
    assert plan()["summary"] == {"unchanged": 4}
    assert len(tm_state()["cases"]) == 4  # no duplicates


def test_duplicate_origin_marker_is_a_conflict(env, tmp_path):
    sync, tm_state, write_state, plan = env
    pull_plan_apply(sync, plan)
    st = tm_state()
    ids = list(st["cases"])
    st["values"][ids[1]]["Xray Key"]["Value"] = st["values"][ids[0]]["Xray Key"]["Value"]
    write_state(st)
    drop_local_index(tmp_path)
    sync("plan", "--tm-project", TM_PROJECT)
    assert "conflict" in plan()["summary"]


def test_apply_requires_yes(env):
    sync, _, _, _ = env
    sync("pull", "--jql", "x", "--fixture", str(FIXTURE))
    sync("plan", "--tm-project", TM_PROJECT)
    assert sync("apply") == 2


def test_accept_drift_overwrites_only_named_xray_tests(env, tmp_path):
    sync, tm_state, write_state, plan = env
    pull_plan_apply(sync, plan)
    st = tm_state()
    for c in st["cases"].values():
        c["Description"] += " (edited in TM)"
    write_state(st)

    def mutate(pages):
        for t in pages[0]["data"]["page"]["results"]:
            t["jira"]["summary"] += " v2"

    sync("pull", "--jql", "x", "--fixture", str(changed_fixture(tmp_path, mutate)))
    sync("plan", "--tm-project", TM_PROJECT, "--accept-drift", "CALC-101")
    actions = {i["xray_key"]: i["action"] for i in plan()["items"]}
    assert actions["CALC-101"] == "update" and actions["CALC-102"] == "drift"
    assert sync("apply", "--yes") == 0
    names = {c["Name"] for c in tm_state()["cases"].values()}
    assert "Login with valid credentials v2" in names and "Locked account cannot sign in v2" not in names


def test_tm_owned_steps_survive_update_and_are_not_drift(env, tmp_path):
    """Xray test without Xray steps (description only): TM steps generated in TM are TM-owned."""
    sync, tm_state, write_state, plan = env
    pull_plan_apply(sync, plan)
    st = tm_state()
    c103 = next(c for c in st["cases"].values() if c["Name"] == "API contract suite")
    st["steps"][c103["Id"]] = [{"Id": "gen-1", "OrderNo": 0, "Description": "Generated by GenAI in TM",
                                "ExpectedResult": "ok", "ClipboardData": None}]
    write_state(st)

    def mutate(pages):
        pages[1]["data"]["page"]["results"][0]["jira"]["description"] = "Now also checks the error contract."

    sync("pull", "--jql", "x", "--fixture", str(changed_fixture(tmp_path, mutate)))
    sync("plan", "--tm-project", TM_PROJECT)
    assert {i["xray_key"]: i["action"] for i in plan()["items"]}["CALC-103"] == "update"
    assert sync("apply", "--yes") == 0
    st = tm_state()
    assert [s["Description"] for s in st["steps"][c103["Id"]]] == ["Generated by GenAI in TM"]
    assert st["cases"][c103["Id"]]["Description"].startswith("Now also checks the error contract.")
    sync("plan", "--tm-project", TM_PROJECT)
    assert {i["xray_key"]: i["action"] for i in plan()["items"]}["CALC-103"] == "unchanged"


# --------------------------------------------------------------------------- TM test sets


def test_testsets_mirror_xray_test_sets(env, tmp_path, capsys):
    sync, tm_state, write_state, plan = env
    pull_plan_apply(sync, plan)
    capsys.readouterr()
    assert sync("testsets", "--tm-project", TM_PROJECT) == 0  # dry run
    dry = json.loads(capsys.readouterr().out)
    assert [(i["xray_test_set_key"], i["action"], len(i["add"])) for i in dry["test_sets"]] == [
        ("CALC-200", "create", 3), ("CALC-201", "create", 1)]
    assert not tm_state()["sets"]

    assert sync("testsets", "--tm-project", TM_PROJECT, "--yes") == 0
    st = tm_state()
    login = tm_set(st, "Login regression")
    assert sorted(xray_key_of(st, cid) for cid in login["members"]) == ["CALC-101", "CALC-102", "CALC-104"]
    assert xray_key_of(st, login["Id"]) == "CALC-200"

    # A tester adds a TM-only test case by hand; Xray drops CALC-102 from the Xray test set.
    st["cases"]["tm-only"] = {"Id": "tm-only", "TestCaseKey": f"{TM_PROJECT}:99", "Name": "Exploratory",
                              "Description": ""}
    st["values"]["tm-only"] = {"Xray Key": {"Name": "Xray Key", "Value": ""}}
    login["members"].append("tm-only")
    write_state(st)

    def mutate(pages):
        pages[0]["data"]["page"]["results"][1]["testSets"] = {"total": 0, "results": []}

    sync("pull", "--jql", "x", "--fixture", str(changed_fixture(tmp_path, mutate)))
    capsys.readouterr()
    assert sync("testsets", "--tm-project", TM_PROJECT, "--yes") == 0
    items = {i["xray_test_set_key"]: i for i in json.loads(capsys.readouterr().out)["test_sets"]}
    assert items["CALC-200"]["action"] == "update" and len(items["CALC-200"]["remove"]) == 1
    assert items["CALC-201"]["action"] == "unchanged"
    st = tm_state()
    remaining = sorted(xray_key_of(st, cid) or st["cases"][cid]["TestCaseKey"]
                       for cid in tm_set(st, "Login regression")["members"])
    assert remaining == ["CALC-101", "CALC-104", f"{TM_PROJECT}:99"]  # CALC-102 removed, TM-only kept


# --------------------------------------------------------------------------- real results → Xray


def real_execution(sync, tm_state, plan, comment=""):
    """A TM test execution as a tester (not the demo) would record it."""
    pull_plan_apply(sync, plan)
    assert sync("testsets", "--tm-project", TM_PROJECT, "--yes") == 0
    tm = core.TmCli(TM_PROJECT)
    login = tm_set(tm_state(), "Login regression")
    execution_id = tm.tm("testsets", "run", "--test-set-key", login["TestSetKey"], "--execution-type", "manual")[
        "ExecutionId"]
    for cid in login["members"]:
        log = tm.tm("testcaselog", "start", "--execution-id", execution_id, "--test-case-id", cid)
        rows = tm.tm("teststeplog", "list", "--test-case-log-id", log["Id"]) or []
        step_logs = [{"id": r["Id"], "result": "Passed", "comment": comment} for r in rows]
        tm.tm("testcaselog", "finish", "--execution-id", execution_id, "--test-case-id", cid, "--result", "Passed",
              *(["--step-logs", json.dumps(step_logs)] if step_logs else []))
    return execution_id


def test_report_sends_real_results_by_xray_key(env, capsys, monkeypatch):
    sync, tm_state, _, plan = env
    xray = fake_xray(monkeypatch)
    execution_id = real_execution(sync, tm_state, plan)
    capsys.readouterr()
    assert sync("report", "--tm-project", TM_PROJECT, "--execution-id", execution_id) == 0  # dry run
    assert not xray["imports"]
    assert sync("report", "--tm-project", TM_PROJECT, "--execution-id", execution_id,
                "--xray-test-plan", "CALC-299", "--test-environment", "staging", "--yes") == 0
    body = xray["imports"][0]
    assert not body["info"]["summary"].startswith("[MOCK]")
    assert body["info"]["testEnvironments"] == ["staging"] and body["info"]["testPlanKey"] == "CALC-299"
    assert core.REPORT_MARKER in body["info"]["description"]
    tests = {t["testKey"]: t for t in body["tests"]}
    assert sorted(tests) == ["CALC-101", "CALC-102", "CALC-104"]
    assert [s["status"] for s in tests["CALC-101"]["steps"]] == ["PASSED"] * 3
    assert "steps" not in tests["CALC-102"]  # Cucumber: no step results
    assert "steps" not in tests["CALC-104"]  # called Xray test inlined: step counts don't map back


def test_report_refuses_mock_tagged_results(env, capsys, monkeypatch):
    sync, tm_state, _, plan = env
    xray = fake_xray(monkeypatch)
    execution_id = real_execution(sync, tm_state, plan, comment=core.MOCK_TAG)
    capsys.readouterr()
    assert sync("report", "--tm-project", TM_PROJECT, "--execution-id", execution_id, "--yes") == 2
    assert "only reports real results" in json.loads(capsys.readouterr().out)["error"]
    assert not xray["imports"]


# --------------------------------------------------------------------------- evidence (command trail)


def test_every_run_leaves_a_command_trail_without_secrets(env, tmp_path, capsys, monkeypatch):
    sync, tm_state, _, plan = env
    fake_xray(monkeypatch)
    execution_id = real_execution(sync, tm_state, plan)
    capsys.readouterr()
    assert sync("report", "--tm-project", TM_PROJECT, "--execution-id", execution_id, "--yes") == 0
    out = json.loads(capsys.readouterr().out)
    trail_jsonl, trail_sh = out["evidence"]["jsonl"], out["evidence"]["sh"]
    entries = [json.loads(line) for line in open(trail_jsonl)]
    assert entries[0]["tool"] == "xr sync" and "report" in entries[0]["argv"]
    assert any(e.get("rest") == "POST /api/v2/import/execution" and e["http"] == 200 for e in entries)
    assert any(e["tool"] == "uip" and e["argv"][:3] == ["tm", "executions", "testcaselogs"] for e in entries)
    everything = open(trail_jsonl).read() + open(trail_sh).read()
    assert "s3cr3t-value" not in everything and "jwt" not in everything
    sh = open(trail_sh).read()
    assert sh.startswith("#!/bin/sh\n# uip calls made by: xr sync ") and " report " in sh and "\nuip tm executions testcaselogs list" in sh

    apply_runs = list((tmp_path / "work" / "evidence").glob("*-apply.sh"))
    assert apply_runs and "uip tm testcases create --name 'Login with valid credentials'" in apply_runs[0].read_text()
