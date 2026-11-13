import textwrap
import pytest
from ciwaste.lint import lint_text, lint_paths


def rules(text):
    return sorted({i.rule for i in lint_text(textwrap.dedent(text))})


def test_wasteful_workflow():
    r = rules("""
        on: [push, pull_request]
        jobs:
          test:
            runs-on: macos-latest
            steps: [{run: make}]
    """)
    assert r == ["CW101", "CW102", "CW103", "CW105"]


def test_clean_workflow():
    r = rules("""
        on:
          push:
            branches: [main]
          pull_request:
        concurrency:
          group: ${{ github.workflow }}-${{ github.ref }}
          cancel-in-progress: true
        jobs:
          test:
            runs-on: ubuntu-latest
            timeout-minutes: 15
            steps: [{run: make}]
    """)
    assert r == []


def test_cancel_false_is_flagged():
    r = rules("""
        on: pull_request
        concurrency: {group: x, cancel-in-progress: false}
        jobs:
          t: {runs-on: ubuntu-latest, timeout-minutes: 5, steps: [{run: x}]}
    """)
    assert r == ["CW101"]


def test_tag_only_push_and_reusable_jobs():
    r = rules("""
        on:
          push:
            tags: ['v*']
        jobs:
          release:
            uses: ./.github/workflows/release.yml
    """)
    assert r == []


def test_frequent_cron():
    r = rules("""
        on:
          schedule:
            - cron: '*/5 * * * *'
            - cron: '0 3 * * *'
        jobs:
          t: {runs-on: ubuntu-latest, timeout-minutes: 5, steps: [{run: x}]}
    """)
    assert r == ["CW104"]


def test_matrix_macos_detected():
    r = rules("""
        on: pull_request
        concurrency: {group: x, cancel-in-progress: true}
        jobs:
          t:
            runs-on: ${{ matrix.os }}
            timeout-minutes: 5
            strategy: {matrix: {os: [ubuntu-latest, macos-14]}}
            steps: [{run: x}]
    """)
    assert r == ["CW105"]


def test_line_numbers_and_paths(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: pull_request\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps: [{run: x}]\n")
    issues = lint_paths(str(tmp_path))
    cw102 = [i for i in issues if i.rule == "CW102"][0]
    assert cw102.line == 3 and cw102.job == "build"


def test_bad_yaml():
    with pytest.raises(ValueError):
        lint_text("on: [\n", "x.yml")
