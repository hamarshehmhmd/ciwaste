"""Offline checks on workflow YAML for patterns that burn Actions minutes."""
import glob
import os
import re

import yaml

RULES = {
    "CW101": ("no-cancel-in-progress", "warning",
              "PR/branch pushes start a new run without cancelling the old one; superseded runs are billed to the end"),
    "CW102": ("no-timeout", "warning",
              "job has no timeout-minutes, so a hang bills up to 360 minutes"),
    "CW103": ("push-and-pull-request", "warning",
              "`push` has no branch filter and `pull_request` is also on: every PR commit runs twice"),
    "CW104": ("frequent-schedule", "notice",
              "cron fires more than once an hour"),
    "CW105": ("macos-on-pull-request", "notice",
              "macOS runners cost ~10x Linux per minute; running them on every PR adds up"),
}


class Issue:
    def __init__(self, rule, path, line, msg, job=None):
        self.rule, self.path, self.line, self.msg, self.job = rule, path, line, msg, job

    @property
    def name(self):
        return RULES[self.rule][0]

    @property
    def level(self):
        return RULES[self.rule][1]

    def as_dict(self):
        return {"rule": self.rule, "name": self.name, "level": self.level, "path": self.path,
                "line": self.line, "job": self.job, "message": self.msg}


def _line_of(text, pattern, default=1):
    m = re.search(pattern, text, re.M)
    return text.count("\n", 0, m.start()) + 1 if m else default


def _triggers(doc):
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {t: None for t in on}
    return on if isinstance(on, dict) else {}


def _has_cancel(conc):
    if isinstance(conc, dict):
        c = conc.get("cancel-in-progress")
        return bool(c) and c is not False and str(c).lower() != "false"
    return False


def _runs_on_labels(job):
    ro = job.get("runs-on")
    vals = []
    if isinstance(ro, str):
        vals = [ro]
    elif isinstance(ro, list):
        vals = [str(x) for x in ro]
    elif isinstance(ro, dict):
        lab = ro.get("labels")
        vals = [lab] if isinstance(lab, str) else [str(x) for x in (lab or [])]
    matrix = ((job.get("strategy") or {}).get("matrix") or {}) if isinstance(job.get("strategy"), dict) else {}
    if isinstance(matrix, dict):
        for v in matrix.values():
            if isinstance(v, list):
                vals += [str(x) for x in v if isinstance(x, (str, int, float))]
        for inc in matrix.get("include") or []:
            if isinstance(inc, dict):
                vals += [str(x) for x in inc.values() if isinstance(x, str)]
    return vals


def lint_text(text, path="workflow.yml"):
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ValueError("%s: could not parse YAML: %s" % (path, str(e).splitlines()[0]))
    if not isinstance(doc, dict):
        return []
    issues = []
    trig = _triggers(doc)
    jobs = doc.get("jobs") or {}
    on_line = _line_of(text, r"^(on|\"on\"|'on'|true)\s*:")
    has_pr = "pull_request" in trig
    push = trig.get("push", 0) if "push" in trig else 0
    push_on = "push" in trig
    push_filter = isinstance(push, dict) and any(k in push for k in ("branches", "branches-ignore"))
    push_tags_only = isinstance(push, dict) and ("tags" in push or "tags-ignore" in push) and not push_filter
    branch_push = push_on and not push_tags_only

    if (has_pr or branch_push) and jobs:
        top = _has_cancel(doc.get("concurrency"))
        job_level = all(_has_cancel((j or {}).get("concurrency")) for j in jobs.values() if isinstance(j, dict))
        if not top and not job_level:
            issues.append(Issue("CW101", path, _line_of(text, r"^concurrency\s*:", on_line),
                                RULES["CW101"][2] + "; add a concurrency group with cancel-in-progress: true"))
    if has_pr and push_on and not push_filter and not push_tags_only:
        issues.append(Issue("CW103", path, on_line, RULES["CW103"][2] + "; add `branches: [%s]` under push"
                            % "main"))
    for cron in (trig.get("schedule") or []) if isinstance(trig.get("schedule"), list) else []:
        expr = (cron or {}).get("cron") if isinstance(cron, dict) else None
        if expr:
            parts = str(expr).split()
            if len(parts) >= 5 and parts[1] in ("*", "*/1") and (parts[0] == "*" or parts[0].startswith("*/") or "," in parts[0]):
                issues.append(Issue("CW104", path, _line_of(text, re.escape(str(expr)), on_line),
                                    "cron `%s` fires more than once an hour" % expr))
    for jid, job in jobs.items():
        if not isinstance(job, dict) or "uses" in job:  # reusable workflow calls carry their own timeouts
            continue
        jl = _line_of(text, r"^\s+%s\s*:" % re.escape(str(jid)), 1)
        if "timeout-minutes" not in job:
            issues.append(Issue("CW102", path, jl, "job `%s` has no timeout-minutes (default 360)" % jid, jid))
        if has_pr and any("macos" in str(lab).lower() for lab in _runs_on_labels(job)):
            issues.append(Issue("CW105", path, jl, "job `%s` runs on macOS for pull_request: %s" % (jid, RULES["CW105"][2]), jid))
    return issues


def find_workflows(root):
    if os.path.isfile(root):
        return [root]
    wf = os.path.join(root, ".github", "workflows")
    base = wf if os.path.isdir(wf) else root
    files = sorted(glob.glob(os.path.join(base, "*.yml")) + glob.glob(os.path.join(base, "*.yaml")))
    return files


def lint_paths(root):
    out = []
    for f in find_workflows(root):
        with open(f, encoding="utf-8") as fh:
            try:
                shown = os.path.relpath(f)
            except ValueError:  # different drive on Windows
                shown = f
            out += lint_text(fh.read(), shown)
    return out
