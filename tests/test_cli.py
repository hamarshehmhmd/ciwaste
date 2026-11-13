import json
import pytest
from ciwaste.cli import main
from ciwaste import fetch
from helpers import job, run, snap


@pytest.fixture
def snapfile(tmp_path):
    runs = [
        run("ci.yml", "pull_request", "feat", "a", 0, [job("test", 1, 21)]),
        run("ci.yml", "pull_request", "feat", "b", 5, [job("test", 6, 26)]),
        run("mac.yml", "push", "main", "c", 0, [job("build", 0, 30, ["macos-latest"])]),
    ]
    p = tmp_path / "s.json"
    p.write_text(json.dumps(snap(runs)))
    return str(p)


def test_report_text(snapfile, capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert main(["report", snapfile]) == 0
    out = capsys.readouterr().out
    assert "Superseded runs kept running" in out
    assert "cancel-in-progress" in out
    assert "macOS is" in out


def test_report_json_and_threshold(snapfile, capsys):
    assert main(["report", snapfile, "--format", "json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["waste_cost"] == pytest.approx(16 * 0.006)
    assert main(["report", snapfile, "--fail-over", "0.01", "--format", "json"]) == 2
    assert main(["report", snapfile, "--fail-over", "100", "--format", "json"]) == 0
    assert main(["report", snapfile, "--fail-over-pct", "1", "--format", "json"]) == 2


def test_step_summary(snapfile, tmp_path, monkeypatch, capsys):
    summ = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summ))
    assert main(["report", snapfile]) == 0
    assert "| Superseded runs kept running |" in summ.read_text()


def test_lint_cli(tmp_path, capsys):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: [push, pull_request]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps: [{run: x}]\n")
    assert main(["lint", str(tmp_path)]) == 0
    assert main(["lint", str(tmp_path), "--strict"]) == 1
    assert main(["lint", str(tmp_path), "--format", "github"]) == 0
    out = capsys.readouterr().out
    assert "::warning file=" in out and "CW103" in out


def test_rates_cli(capsys):
    assert main(["rates"]) == 0
    assert "actions_macos" in capsys.readouterr().out


def test_bad_source(capsys):
    assert main(["report", "not-a-file-or-repo"]) == 1


class FakeClient:
    def __init__(self, runs, jobs):
        self.runs, self.jobs, self.calls = runs, jobs, []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "/repos/o/r":
            return {"private": True, "default_branch": "trunk"}
        if path.endswith("/actions/runs"):
            return {"workflow_runs": self.runs if params["page"] == 1 else []}
        rid = int(path.split("/")[-2])
        return {"jobs": self.jobs.get(rid, [])}


def test_fetch_snapshot_with_fake_api():
    runs = [{"id": 1, "path": ".github/workflows/ci.yml", "event": "push", "head_branch": "trunk",
             "head_sha": "a" * 40, "created_at": "2026-11-01T10:00:00Z", "extra": "dropped"}]
    jobs = {1: [{"id": 9, "name": "t", "labels": ["ubuntu-latest"], "status": "completed",
                 "started_at": "2026-11-01T10:00:00Z", "completed_at": "2026-11-01T10:03:00Z", "steps": []}]}
    c = FakeClient(runs, jobs)
    s = fetch.fetch_snapshot("o/r", days=7, client=c)
    assert s["default_branch"] == "trunk" and s["private"] is True
    assert "extra" not in s["runs"][0] and "steps" not in s["runs"][0]["jobs"][0]
    assert c.calls[1][1]["created"].startswith(">=")
    assert c.calls[2][1]["filter"] == "all"


def test_fetch_requires_owner_repo():
    with pytest.raises(fetch.FetchError):
        fetch.fetch_snapshot("nope", client=FakeClient([], {}))


def test_lint_ignore(tmp_path, capsys):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: [push, pull_request]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps: [{run: x}]\n")
    assert main(["lint", str(tmp_path), "--ignore", "CW102", "--format", "json"]) == 0
    got = {i["rule"] for i in json.loads(capsys.readouterr().out)}
    assert got == {"CW101", "CW103"}
