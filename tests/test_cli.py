"""`xr`: the uip-style envelope, global options, resource commands, and sync/demo through the same entry point."""
import json

from conftest import FIXTURE, TM_PROJECT, core, fake_xray
from xr import cli


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, out


def envelope(capsys, *argv):
    code, out = run(capsys, *argv)
    return code, json.loads(out)


def test_tests_list_is_a_uip_style_envelope(monkeypatch, capsys):
    xray = fake_xray(monkeypatch)
    xray["issues"]["getTests"]["1"] = {"issueId": "1", "testType": {"name": "Manual"}, "folder": {"path": "/Trading"},
                                       "jira": {"key": "UIP-8", "summary": "Buy", "status": {"name": "To Do"},
                                                "labels": ["smoke"]}}
    code, env = envelope(capsys, "tests", "list", "--project", "UIP")
    assert code == 0 and env["Result"] == "Success" and env["Code"] == "TestsList"
    assert env["Data"] == [{"Key": "UIP-8", "Summary": "Buy", "Type": "Manual", "Status": "To Do", "Folder": "/Trading",
                            "Labels": ["smoke"], "IssueId": "1"}]


def test_output_filter_and_table_work_anywhere_on_the_line(monkeypatch, capsys):
    xray = fake_xray(monkeypatch)
    for n in ("1", "2"):
        xray["issues"]["getTests"][n] = {"issueId": n, "jira": {"key": f"UIP-{n}", "summary": f"T{n}"}}
    code, env = envelope(capsys, "--output-filter", "[].Key", "tests", "list", "--project", "UIP")
    assert env["Data"] == ["UIP-1", "UIP-2"]
    code, out = run(capsys, "tests", "list", "--project", "UIP", "--output", "table")
    assert out.splitlines()[0].split()[:2] == ["Key", "Summary"] and "UIP-2" in out


def test_tests_create_is_a_dry_run_until_yes_and_then_leaves_a_trail(monkeypatch, capsys, tmp_path):
    xray = fake_xray(monkeypatch)
    work = str(tmp_path / "work")
    code, env = envelope(capsys, "tests", "create", "--project", "UIP", "--summary", "New", "--workdir", work)
    assert env["Data"]["DryRun"] is True and not xray["issues"]["getTests"]
    code, env = envelope(capsys, "tests", "create", "--project", "UIP", "--summary", "New", "--workdir", work, "--yes")
    assert code == 0 and env["Data"]["XrayKey"].startswith("CALC-") and len(xray["issues"]["getTests"]) == 1
    assert env["Data"]["Evidence"]["jsonl"].endswith("-tests-create.jsonl")


def test_failures_use_the_envelope_and_exit_2(monkeypatch, capsys):
    code, env = envelope(capsys, "tests", "list", "--project", "UIP")  # no Xray credentials in the test env
    assert code == 2 and env["Result"] == "Failure" and "XRAY_CLIENT_ID" in env["Message"]
    fake_xray(monkeypatch)
    code, env = envelope(capsys, "tests", "get", "not-a-key")
    assert code == 2 and "not a Jira issue key" in env["Message"]


def test_sync_runs_through_xr_with_the_same_code_and_an_envelope(env, capsys, tmp_path, monkeypatch):
    sync, *_ = env
    work = str(tmp_path / "work")
    code, out = run(capsys, "sync", "--workdir", work, "pull", "--jql", "project = CALC", "--fixture", str(FIXTURE))
    assert code == 0 and json.loads(out)["Code"] == "SyncPull"
    code, out = run(capsys, "sync", "--workdir", work, "plan", "--tm-project", TM_PROJECT,
                    "--output-filter", "summary")
    assert code == 0 and json.loads(out)["Data"]["create"] > 0
    assert core.EMIT_HOOK is None  # the envelope is only installed while xr runs
    trail = next((tmp_path / "work" / "evidence").glob("*-plan.sh")).read_text()
    assert "# uip calls made by: xr sync --workdir" in trail


def test_demo_refusal_comes_back_as_a_failure_envelope(capsys, tmp_path):
    code, out = run(capsys, "demo", "--workdir", str(tmp_path), "seed", "--xray-project", "PROD", "--tm-project", "PROD")
    env = json.loads(out)
    assert code == 2 and env["Result"] == "Failure" and env["Code"] == "DemoSeed" and "not a registered demo sandbox" in env["Message"]
