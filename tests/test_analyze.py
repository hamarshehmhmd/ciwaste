import pytest
from ciwaste.analyze import analyze
from helpers import job, run, snap


def kinds(rep):
    return {k["kind"]: k for k in rep["waste_by_kind"]}


def test_costs_and_rounding():
    s = snap([run("ci.yml", "push", "main", "a", 0, [
        job("lint", 0, 1), job("mac", 0, 10, ["macos-latest"]),
    ])])
    s["runs"][0]["jobs"][0]["completed_at"] = "2026-11-01T10:00:30Z"  # 30 s -> 1 billed minute
    rep = analyze(s)
    assert rep["billed_minutes"] == 11
    assert rep["list_cost"] == pytest.approx(0.006 + 10 * 0.062)
    assert rep["by_runner"][0]["key"] == "actions_macos"
    assert rep["waste_cost"] == 0


def test_superseded_on_pr_branch():
    runs = [
        run("ci.yml", "pull_request", "feat", "a", 0, [job("test", 1, 21)]),
        run("ci.yml", "pull_request", "feat", "b", 5, [job("test", 6, 26)]),
    ]
    rep = analyze(snap(runs))
    k = kinds(rep)["superseded"]
    assert k["runs"] == 1
    assert k["wasted_minutes"] == pytest.approx(16)  # ran 10:05 -> 10:21 after b was pushed
    assert k["wasted_cost"] == pytest.approx(16 * 0.006)


def test_superseded_ignores_same_sha_and_finished_runs():
    runs = [
        run("ci.yml", "pull_request", "feat", "a", 0, [job("test", 1, 4)]),
        run("ci.yml", "pull_request", "feat", "b", 10, [job("test", 11, 14)]),  # a had finished
        run("ci.yml", "pull_request", "feat", "b", 12, [job("test", 12, 15)]),  # same sha (re-trigger)
    ]
    assert "superseded" not in kinds(analyze(snap(runs)))


def test_cancelled_quickly_is_not_waste():
    runs = [
        run("ci.yml", "pull_request", "feat", "a", 0, [job("test", 1, 5, conclusion="cancelled")], conclusion="cancelled"),
        run("ci.yml", "pull_request", "feat", "b", 5, [job("test", 5, 15)]),
    ]
    s = snap(runs)
    s["runs"][0]["jobs"][0]["completed_at"] = "2026-11-01T10:05:10Z"
    assert "superseded" not in kinds(analyze(s))


def test_default_branch_superseded_reported_not_counted():
    runs = [
        run("ci.yml", "push", "main", "a", 0, [job("test", 1, 21)]),
        run("ci.yml", "push", "main", "b", 5, [job("test", 6, 26)]),
    ]
    rep = analyze(snap(runs))
    assert rep["waste_cost"] == 0
    assert rep["default_branch_superseded"]["runs"] == 1
    rep2 = analyze(snap(runs), count_default_branch=True)
    assert kinds(rep2)["superseded"]["wasted_minutes"] == pytest.approx(16)


def test_duplicate_push_and_pr():
    runs = [
        run("ci.yml", "push", "feat", "a", 0, [job("test", 0, 10)]),
        run("ci.yml", "pull_request", "feat", "a", 0, [job("test", 0, 10)]),
        run("ci.yml", "push", "main", "c", 30, [job("test", 30, 40)]),
    ]
    k = kinds(analyze(snap(runs)))
    assert k["duplicate"]["runs"] == 1
    assert k["duplicate"]["wasted_minutes"] == 10


def test_duplicate_not_counted_twice_as_superseded():
    runs = [
        run("ci.yml", "push", "feat", "a", 0, [job("test", 0, 20)]),
        run("ci.yml", "pull_request", "feat", "a", 0, [job("test", 0, 20)]),
        run("ci.yml", "push", "feat", "b", 5, [job("test", 5, 25)]),
    ]
    rep = analyze(snap(runs))
    k = kinds(rep)
    assert k["duplicate"]["wasted_minutes"] == 20
    assert "superseded" not in k  # the push run for a is already fully claimed
    assert rep["waste_cost"] == pytest.approx(20 * 0.006)


def test_flaky_rerun():
    r = run("ci.yml", "pull_request", "feat", "a", 0, [
        job("test", 0, 8, conclusion="failure", attempt=1),
        job("test", 20, 28, attempt=2),
        job("lint", 0, 2, conclusion="failure", attempt=1),  # never passed: a real failure, not flaky
    ], attempt=2)
    k = kinds(analyze(snap([r])))
    assert k["flaky-rerun"]["wasted_minutes"] == 8


def test_hung_job():
    runs = [run("ci.yml", "push", "main", c * 40, i * 60, [job("test", i * 60, i * 60 + 10)])
            for i, c in enumerate("abcdef")]
    runs.append(run("ci.yml", "push", "main", "g" * 40, 400, [job("test", 400, 760, conclusion="cancelled")],
                    conclusion="cancelled"))
    rep = analyze(snap(runs))
    k = kinds(rep)["hung"]
    assert k["wasted_minutes"] == pytest.approx(350)
    f = [f for f in rep["findings"] if f["kind"] == "hung"][0]
    assert "6-hour" in f["detail"] and f["normal_minutes"] == 10


def test_hung_needs_history():
    runs = [run("ci.yml", "push", "main", "a", 0, [job("test", 0, 10)]),
            run("ci.yml", "push", "main", "b", 60, [job("test", 60, 400)])]
    assert "hung" not in kinds(analyze(snap(runs)))


def test_schedule_no_change():
    runs = [run("nightly.yml", "schedule", "main", "a", d * 60 * 24 // 24, [job("e2e", d * 60, d * 60 + 30)])
            for d in range(3)]
    runs.append(run("nightly.yml", "schedule", "main", "b", 200, [job("e2e", 200, 230)]))
    k = kinds(analyze(snap(runs)))
    assert k["schedule-no-change"]["runs"] == 2


def test_public_repo_paid_cost():
    s = snap([run("ci.yml", "push", "main", "a", 0, [job("t", 0, 10), job("big", 0, 10, ["linux_8_core"])])],
             private=False)
    rep = analyze(s)
    assert rep["list_cost"] == pytest.approx(10 * 0.006 + 10 * 0.022)
    assert rep["paid_cost"] == pytest.approx(10 * 0.022)


def test_monthly_factor_and_unpriced():
    s = snap([run("ci.yml", "push", "main", "a", 0, [job("t", 0, 10, ["weird-label"])])], days=7)
    rep = analyze(s)
    assert rep["monthly_factor"] == pytest.approx(30 / 7, rel=1e-3)
    assert rep["unpriced_labels"] == ["weird-label"]
    assert rep["list_cost"] == 0


def test_skipped_jobs_cost_nothing():
    j = job("skipped", 0, 0, conclusion="skipped")
    rep = analyze(snap([run("ci.yml", "push", "main", "a", 0, [j])]))
    assert rep["billed_minutes"] == 0
