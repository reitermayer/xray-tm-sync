"""xray-tm-demo: sandbox guard, seed, mock TM test execution, mock report, reset / reseed."""
import json

from conftest import FIXTURE, TM_PROJECT, core, demo, fake_xray, pull_plan_apply, tm_set

PAIR = ["--xray-project", "CALC", "--tm-project", TM_PROJECT]


def register(run_demo, xray_project="CALC"):
    assert run_demo("sandbox", "add", "--xray-project", xray_project, "--tm-project", TM_PROJECT) == 0


# --------------------------------------------------------------------------- sandbox guard


def test_every_demo_command_needs_a_registered_sandbox(env, run_demo, capsys):
    for argv in (["seed"], ["execute", "--tm-test-set", "X:1"], ["report", "--execution-id", "e"], ["reset"]):
        capsys.readouterr()
        assert run_demo(argv[0], *PAIR, *argv[1:]) == 2
        assert "not a registered demo sandbox" in json.loads(capsys.readouterr().out)["error"]


def test_sandbox_registry_add_list_remove(run_demo, capsys, tmp_path):
    register(run_demo)
    register(run_demo)  # idempotent
    capsys.readouterr()
    assert run_demo("sandbox", "list") == 0
    assert json.loads(capsys.readouterr().out)["sandboxes"] == [{"xray_project": "CALC", "tm_project": TM_PROJECT}]
    assert run_demo("sandbox", "remove", *PAIR) == 0
    assert json.loads((tmp_path / "sandboxes.json").read_text()) == []


# --------------------------------------------------------------------------- seed


def test_seed_creates_eight_xray_tests_and_one_xray_test_set(env, run_demo, capsys, monkeypatch):
    xray = fake_xray(monkeypatch)
    register(run_demo)
    capsys.readouterr()
    assert run_demo("seed", *PAIR) == 0  # dry run
    assert len(json.loads(capsys.readouterr().out)["xray_seed_to_create"]["xray_tests"]) == 8
    assert not xray["issues"]["getTests"]
    assert run_demo("seed", *PAIR, "--yes") == 0
    assert len(xray["issues"]["getTests"]) == 8
    (test_set,) = xray["issues"]["getTestSets"].values()
    assert test_set["jira"]["summary"] == "Stock trading regression" and len(test_set["members"]) == 8


# --------------------------------------------------------------------------- mock execution


def mock_execution(env, run_demo, tmp_path, capsys, outcomes=None):
    sync, tm_state, _, plan = env
    register(run_demo)
    pull_plan_apply(sync, plan)
    sync("testsets", "--tm-project", TM_PROJECT, "--yes")
    login_key = tm_set(tm_state(), "Login regression")["TestSetKey"]
    extra = []
    if outcomes:
        path = tmp_path / "outcomes.json"
        path.write_text(json.dumps(outcomes))
        extra = ["--outcomes", str(path)]
    capsys.readouterr()
    assert run_demo("execute", *PAIR, "--tm-test-set", login_key, *extra, "--yes") == 0
    return json.loads(capsys.readouterr().out)


def test_mock_execution_is_all_passed_by_default(env, run_demo, tmp_path, capsys):
    record = mock_execution(env, run_demo, tmp_path, capsys)
    assert record["mock"] is True
    assert {r["xray_key"]: r["result"] for r in record["results"]} == {
        "CALC-101": "Passed", "CALC-102": "Passed", "CALC-104": "Passed"}


def test_mock_execution_records_step_results(env, run_demo, tmp_path, capsys):
    record = mock_execution(env, run_demo, tmp_path, capsys,
                            {"CALC-101": {"result": "Failed", "failed_step": 2, "comment": "button missing"}})
    results = {r["xray_key"]: r for r in record["results"]}
    assert results["CALC-101"]["result"] == "Failed"
    assert results["CALC-101"]["step_results"] == ["Passed", "Failed", "None"]
    assert results["CALC-104"]["result"] == "Passed"
    steplogs = env[1]()["steplogs"][results["CALC-101"]["tm_case_log_id"]]
    assert [r["Comment"] for r in steplogs][1] == "[MOCK] button missing"


# --------------------------------------------------------------------------- mock report


def test_mock_report_goes_to_the_sandbox_as_mock(env, run_demo, tmp_path, capsys, monkeypatch):
    xray = fake_xray(monkeypatch)
    record = mock_execution(env, run_demo, tmp_path, capsys,
                            {"CALC-101": {"result": "Failed", "failed_step": 2}})
    assert run_demo("report", *PAIR, "--execution-id", record["execution_id"], "--xray-test-plan", "CALC-299",
                    "--test-environment", "TM-mock", "--yes") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["xray_test_execution"]["key"] == "CALC-300" and out["mock"] is True
    body = xray["imports"][0]
    assert body["info"]["summary"].startswith("[MOCK] ")
    assert body["info"]["testEnvironments"] == ["TM-mock"] and body["info"]["testPlanKey"] == "CALC-299"
    tests = {t["testKey"]: t for t in body["tests"]}
    assert tests["CALC-101"]["status"] == "FAILED"
    assert [s["status"] for s in tests["CALC-101"]["steps"]] == ["PASSED", "FAILED", "TODO"]
    assert tests["CALC-101"]["start"] == "2026-10-17T00:00:01+00:00"


def test_mock_report_refuses_xray_tests_outside_the_sandbox(env, run_demo, tmp_path, capsys, monkeypatch):
    xray = fake_xray(monkeypatch)
    record = mock_execution(env, run_demo, tmp_path, capsys)
    register(run_demo, "OTHER")
    capsys.readouterr()
    assert run_demo("report", "--xray-project", "OTHER", "--tm-project", TM_PROJECT,
                    "--execution-id", record["execution_id"], "--yes") == 2
    assert "not in the sandbox Jira project OTHER" in json.loads(capsys.readouterr().out)["error"]
    assert not xray["imports"]


def test_production_report_refuses_the_demo_mock_run(env, run_demo, tmp_path, capsys, monkeypatch):
    xray = fake_xray(monkeypatch)
    record = mock_execution(env, run_demo, tmp_path, capsys)
    sync = env[0]
    assert sync("report", "--tm-project", TM_PROJECT, "--execution-id", record["execution_id"], "--yes") == 2
    assert "mock runs belong to the xray-tm-demo skill" in json.loads(capsys.readouterr().out)["error"]
    assert not xray["imports"]


# --------------------------------------------------------------------------- reset


def test_reset_deletes_only_what_the_demo_and_sync_created(env, run_demo, tmp_path, capsys, monkeypatch):
    sync, tm_state, write_state, plan = env
    xray = fake_xray(monkeypatch, tm_state, write_state)
    record = mock_execution(env, run_demo, tmp_path, capsys)
    assert run_demo("report", *PAIR, "--execution-id", record["execution_id"], "--yes") == 0
    xray["test_executions"]["301"] = {"issueId": "301", "jira": {"key": "CALC-301", "summary": "Nightly (real)",
                                                                 "description": f"… {core.REPORT_MARKER}."}}
    assert sorted(xray["test_executions"]) == ["300", "301", "999"]
    st = tm_state()
    st["cases"]["tm-only"] = {"Id": "tm-only", "TestCaseKey": f"{TM_PROJECT}:99", "Name": "Exploratory",
                              "Description": ""}
    write_state(st)
    capsys.readouterr()

    assert run_demo("reset", *PAIR) == 0  # dry run
    dry = json.loads(capsys.readouterr().out)
    assert dry["counts"] == {"xray_test_executions": 1, "xray_test_sets": 0, "xray_tests": 0,
                             "tm_test_executions": 1, "tm_test_sets": 2, "tm_test_cases": 4}
    assert len(tm_state()["cases"]) == 5

    assert run_demo("reset", *PAIR, "--yes") == 0
    st = tm_state()
    assert list(st["cases"]) == ["tm-only"]  # TM-only test case untouched
    assert not st["sets"] and not st["executions"]
    assert sorted(xray["test_executions"]) == ["301", "999"]  # only the [MOCK] Xray test execution went
    assert not list((tmp_path / "work").glob("execution-*.json"))

    assert sync("pull", "--jql", "x", "--fixture", str(FIXTURE)) == 0  # the demo starts from scratch
    assert sync("plan", "--tm-project", TM_PROJECT) == 0
    assert plan()["summary"] == {"create": 4}


def test_reset_ignores_tm_test_cases_from_other_jira_projects(env, run_demo, capsys, monkeypatch):
    sync, tm_state, write_state, plan = env
    fake_xray(monkeypatch, tm_state, write_state)
    pull_plan_apply(sync, plan)  # CALC-* Xray tests
    register(run_demo, "OTHER")
    assert run_demo("reset", "--xray-project", "OTHER", "--tm-project", TM_PROJECT, "--yes") == 0
    assert len(tm_state()["cases"]) == 4


def test_reset_reseed_recreates_the_demo_in_the_sandbox(env, run_demo, capsys, monkeypatch):
    sync, tm_state, write_state, plan = env
    xray = fake_xray(monkeypatch, tm_state, write_state)
    for summary in ("old test A", "old test B"):
        n = str(700 + len(xray["issues"]["getTests"]))
        xray["issues"]["getTests"][n] = {"issueId": n, "jira": {"key": f"CALC-{n}", "summary": summary}}
    xray["issues"]["getTestSets"]["800"] = {"issueId": "800", "jira": {"key": "CALC-800", "summary": "old set"}}
    register(run_demo)
    pull_plan_apply(sync, plan)
    capsys.readouterr()

    assert run_demo("reset", *PAIR, "--reseed") == 0  # dry run
    dry = json.loads(capsys.readouterr().out)
    assert dry["counts"]["xray_tests"] == 2 and dry["counts"]["xray_test_sets"] == 1
    assert dry["counts"]["xray_test_executions"] == 1  # reseed wipes every Xray test execution in the sandbox
    assert len(dry["xray_seed_to_create"]["xray_tests"]) == 8
    assert len(xray["issues"]["getTests"]) == 2

    assert run_demo("reset", *PAIR, "--reseed", "--yes") == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["seeded"]["xray_tests"]) == 8
    tests = {i["jira"]["summary"] for i in xray["issues"]["getTests"].values()}
    assert len(tests) == 8 and "old test A" not in tests and "Buy UiPath stock with a market order" in tests
    sets = list(xray["issues"]["getTestSets"].values())
    assert [s["jira"]["summary"] for s in sets] == ["Stock trading regression"] and len(sets[0]["members"]) == 8
    assert not xray["test_executions"] and not tm_state()["cases"] and not tm_state()["sets"]


def test_reset_reseed_keeps_old_xray_data_when_seeding_fails(env, run_demo, capsys, monkeypatch):
    _, tm_state, write_state, _ = env

    def broken(self, *a, **k):
        raise core.IntakeError("Field 'description' cannot be set")

    monkeypatch.setattr(demo.DemoXray, "create_test", broken)  # before fake_xray wraps the class
    xray = fake_xray(monkeypatch, tm_state, write_state)
    xray["issues"]["getTests"]["700"] = {"issueId": "700", "jira": {"key": "CALC-700", "summary": "old test"}}
    register(run_demo)
    capsys.readouterr()
    assert run_demo("reset", *PAIR, "--reseed", "--yes") == 1
    out = json.loads(capsys.readouterr().out)
    assert "NOT deleted" in out["failed"][0]
    assert "700" in xray["issues"]["getTests"]
