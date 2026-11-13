"""Turn a runs+jobs snapshot into costs and waste findings."""
import datetime as dt
import statistics
from collections import defaultdict

from .rates import Pricer, billed_minutes

PUSHLIKE = ("push", "pull_request", "pull_request_target")

KINDS = {
    "superseded": "Superseded runs kept running",
    "duplicate": "Same commit tested twice (push + pull_request)",
    "flaky-rerun": "Failed attempts that passed on re-run",
    "hung": "Jobs that ran far past their normal time",
    "schedule-no-change": "Scheduled runs on an unchanged commit",
}

FIXES = {
    "superseded": "add `concurrency: {group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true}` to the workflow",
    "duplicate": "limit `push:` to your main branches (`branches: [main]`) so PR branches only run on `pull_request`",
    "flaky-rerun": "fix or quarantine the flaky job; every red attempt you re-run is billed",
    "hung": "set `timeout-minutes` on the job (default is 360) a bit above its normal time",
    "schedule-no-change": "skip the scheduled run when nothing changed since the last green one, or run it less often",
}


def ts(s):
    if not s:
        return None
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


class Job:
    __slots__ = ("raw", "run", "id", "name", "sku", "rate", "seconds", "billed", "started", "completed",
                 "attempt", "conclusion", "free")

    def __init__(self, raw, run, pricer, public):
        self.raw, self.run = raw, run
        self.id = raw.get("id")
        self.name = raw.get("name") or "?"
        self.sku = pricer.sku(raw.get("labels"))
        self.rate = pricer.rate(self.sku)
        self.started, self.completed = ts(raw.get("started_at")), ts(raw.get("completed_at"))
        if self.started and self.completed and raw.get("status", "completed") == "completed":
            self.seconds = max(0.0, (self.completed - self.started).total_seconds())
        else:
            self.seconds = 0.0
        self.billed = billed_minutes(self.seconds)
        self.attempt = raw.get("run_attempt") or 1
        self.conclusion = raw.get("conclusion")
        self.free = public and pricer.is_free_in_public(self.sku)

    @property
    def cost(self):
        return self.billed * (self.rate or 0.0)

    def cost_after(self, cutoff):
        """Exact (unrounded) minutes and cost of this job after `cutoff`."""
        if not (self.started and self.completed):
            return 0.0, 0.0
        start = max(self.started, cutoff)
        mins = max(0.0, (self.completed - start).total_seconds() / 60.0)
        return mins, mins * (self.rate or 0.0)


class Run:
    def __init__(self, raw, pricer, public):
        self.raw = raw
        self.id = raw.get("id")
        self.path = raw.get("path") or raw.get("name") or "?"
        self.name = raw.get("name") or self.path
        self.event = raw.get("event")
        self.branch = raw.get("head_branch")
        self.sha = raw.get("head_sha")
        self.created = ts(raw.get("created_at"))
        self.conclusion = raw.get("conclusion")
        self.attempt = raw.get("run_attempt") or 1
        self.url = raw.get("html_url") or ""
        self.jobs = [Job(j, self, pricer, public) for j in raw.get("jobs") or []]

    @property
    def cost(self):
        return sum(j.cost for j in self.jobs)

    @property
    def billed(self):
        return sum(j.billed for j in self.jobs)

    @property
    def ended(self):
        ends = [j.completed for j in self.jobs if j.completed]
        return max(ends) if ends else None


def _wf(run):
    p = run.path or ""
    return p.split("/")[-1].split("@")[0] if p else run.name


def analyze(snapshot, pricer=None, count_default_branch=False):
    pricer = pricer or Pricer()
    public = not snapshot.get("private", False)
    default = snapshot.get("default_branch") or "main"
    runs = [Run(r, pricer, public) for r in snapshot.get("runs") or []]
    runs.sort(key=lambda r: (r.created or ts("1970-01-01T00:00:00Z"), r.id or 0))
    jobs = [j for r in runs for j in r.jobs]

    claimed = {}  # job id -> kind
    findings = []

    def add(kind, run, parts, detail, default_branch=False, **extra):
        mins = sum(m for _, m, _ in parts)
        cost = sum(c for _, _, c in parts)
        if mins < 0.5:
            return
        for j, _, _ in parts:
            claimed[j.id] = kind
        findings.append({
            "kind": kind, "workflow": _wf(run), "run_id": run.id, "run_url": run.url,
            "event": run.event, "branch": run.branch, "sha": (run.sha or "")[:7],
            "jobs": sorted({j.name for j, _, _ in parts}),
            "wasted_minutes": round(mins, 2), "wasted_cost": round(cost, 4),
            "detail": detail, "default_branch": default_branch,
        })
        findings[-1].update(extra)

    def whole(run):
        return [(j, float(j.billed), j.cost) for j in run.jobs if j.billed and j.id not in claimed]

    # 1. duplicate: a push run on a PR branch for a commit that also ran via pull_request
    pr_keys = {(r.path, r.sha) for r in runs if r.event == "pull_request"}
    for r in runs:
        if r.event == "push" and r.branch != default and (r.path, r.sha) in pr_keys:
            add("duplicate", r, whole(r), "push run on `%s` duplicates the pull_request run for %s"
                % (r.branch, (r.sha or "")[:7]))

    # 2. scheduled runs on a commit that already passed on the previous schedule
    sched = defaultdict(list)
    for r in runs:
        if r.event == "schedule":
            sched[r.path].append(r)
    for path, lst in sched.items():
        for prev, cur in zip(lst, lst[1:]):
            if cur.sha and cur.sha == prev.sha and prev.conclusion == "success" and cur.conclusion == "success":
                add("schedule-no-change", cur, whole(cur),
                    "same commit %s already passed in the previous scheduled run" % (cur.sha or "")[:7])

    # 3. superseded: a newer commit on the same branch started while this run was still going
    groups = defaultdict(list)
    for r in runs:
        if r.event in PUSHLIKE and r.branch:
            groups[(r.path, r.branch, r.event)].append(r)
    for (path, branch, event), lst in groups.items():
        for i, r in enumerate(lst):
            nxt = next((n for n in lst[i + 1:] if n.sha != r.sha and n.created), None)
            if not nxt or not r.ended or nxt.created >= r.ended:
                continue
            parts = []
            for j in r.jobs:
                if j.id in claimed:
                    continue
                m, c = j.cost_after(nxt.created)
                if m > 0:
                    parts.append((j, m, c))
            is_default = event == "push" and branch == default
            add("superseded", r, parts,
                "%.0f job-minutes still running after %s was pushed to `%s`" %
                (sum(m for _, m, _ in parts), (nxt.sha or "")[:7], branch), default_branch=is_default)

    # 4. flaky re-runs: a job failed in an earlier attempt and the same job passed later
    for r in runs:
        if r.attempt <= 1:
            continue
        passed_later = defaultdict(set)
        for j in r.jobs:
            if j.conclusion == "success":
                passed_later[j.name].add(j.attempt)
        parts = [(j, float(j.billed), j.cost) for j in r.jobs
                 if j.id not in claimed and j.billed and j.conclusion in ("failure", "timed_out", "cancelled")
                 and any(a > j.attempt for a in passed_later.get(j.name, ()))]
        if parts:
            add("flaky-rerun", r, parts, "%d job attempt(s) failed, then passed when re-run: %s"
                % (len(parts), ", ".join(sorted({j.name for j, _, _ in parts}))))

    # 5. hung: far longer than the job's normal (p90 of successful runs) time
    normal = defaultdict(list)
    for j in jobs:
        if j.conclusion == "success" and j.seconds > 0:
            normal[(j.run.path, j.name)].append(j.seconds / 60.0)
    p90 = {}
    for k, v in normal.items():
        if len(v) >= 5:
            v = sorted(v)
            p90[k] = v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))]
    for j in jobs:
        k = (j.run.path, j.name)
        if j.id in claimed or k not in p90:
            continue
        mins, base = j.seconds / 60.0, p90[k]
        if mins >= max(3 * base, base + 15):
            extra = mins - base
            note = " (hit the 6-hour default timeout)" if mins >= 355 else ""
            add("hung", j.run, [(j, extra, extra * (j.rate or 0.0))],
                "`%s` ran %.0f min, normal is ~%.0f min%s" % (j.name, mins, base, note),
                normal_minutes=round(base, 1), suggested_timeout=int(max(10, round(base * 2 + 5))))

    # ---- aggregates
    total_cost = sum(j.cost for j in jobs)
    paid_cost = sum(j.cost for j in jobs if not j.free)
    billed = sum(j.billed for j in jobs)
    days = float(snapshot.get("days") or 30)
    if snapshot.get("truncated") and runs and runs[0].created:
        span = (ts(snapshot.get("fetched_at")) - runs[0].created).total_seconds() / 86400.0
        days = max(span, 1.0)
    month = 30.0 / days if days else 1.0

    def bucket(keyf):
        agg = defaultdict(lambda: {"runs": set(), "jobs": 0, "billed_minutes": 0, "cost": 0.0})
        for j in jobs:
            a = agg[keyf(j)]
            a["runs"].add(j.run.id)
            a["jobs"] += 1
            a["billed_minutes"] += j.billed
            a["cost"] += j.cost
        out = []
        for k, a in agg.items():
            out.append({"key": k, "runs": len(a["runs"]), "jobs": a["jobs"],
                        "billed_minutes": a["billed_minutes"], "cost": round(a["cost"], 4),
                        "share": round(a["cost"] / total_cost, 4) if total_cost else 0.0})
        out = [o for o in out if o["billed_minutes"] or o["cost"]]
        return sorted(out, key=lambda x: (-x["cost"], -x["billed_minutes"], str(x["key"])))

    counted = [f for f in findings if count_default_branch or not f["default_branch"]]
    by_kind = []
    for kind in KINDS:
        fs = [f for f in counted if f["kind"] == kind]
        if not fs:
            continue
        wfs = defaultdict(float)
        for f in fs:
            wfs[f["workflow"]] += f["wasted_cost"]
        by_kind.append({
            "kind": kind, "title": KINDS[kind], "fix": FIXES[kind], "runs": len({f["run_id"] for f in fs}),
            "wasted_minutes": round(sum(f["wasted_minutes"] for f in fs), 1),
            "wasted_cost": round(sum(f["wasted_cost"] for f in fs), 4),
            "workflows": sorted(wfs, key=lambda w: -wfs[w]),
        })
    by_kind.sort(key=lambda k: -k["wasted_cost"])
    waste = sum(k["wasted_cost"] for k in by_kind)
    default_sup = [f for f in findings if f["default_branch"]]

    unknown = sorted({j.sku[8:] for j in jobs if j.sku.startswith("unknown:") and j.billed})
    mac_cost = sum(j.cost for j in jobs if "macos" in j.sku)
    mac_min = sum(j.billed for j in jobs if "macos" in j.sku)
    win_cost = sum(j.cost for j in jobs if "windows" in j.sku)
    win_min = sum(j.billed for j in jobs if "windows" in j.sku)

    return {
        "repo": snapshot.get("repo"),
        "private": not public,
        "default_branch": default,
        "since": snapshot.get("since"),
        "fetched_at": snapshot.get("fetched_at"),
        "days": round(days, 2),
        "truncated": bool(snapshot.get("truncated")),
        "runs_missing_jobs": len(snapshot.get("runs_missing_jobs") or []),
        "runs": len(runs),
        "jobs": len(jobs),
        "billed_minutes": billed,
        "list_cost": round(total_cost, 4),
        "paid_cost": round(paid_cost, 4),
        "monthly_factor": round(month, 4),
        "waste_cost": round(waste, 4),
        "waste_share": round(waste / total_cost, 4) if total_cost else 0.0,
        "waste_by_kind": by_kind,
        "default_branch_superseded": {
            "runs": len(default_sup),
            "wasted_minutes": round(sum(f["wasted_minutes"] for f in default_sup), 1),
            "wasted_cost": round(sum(f["wasted_cost"] for f in default_sup), 4),
        },
        "findings": sorted(counted, key=lambda f: -f["wasted_cost"]),
        "by_workflow": bucket(lambda j: _wf(j.run)),
        "by_runner": bucket(lambda j: j.sku),
        "by_event": bucket(lambda j: j.run.event or "?"),
        "by_job": bucket(lambda j: "%s / %s" % (_wf(j.run), j.name)),
        "macos": {"billed_minutes": mac_min, "cost": round(mac_cost, 4),
                  "as_linux": round(mac_min * 0.006, 4)},
        "windows": {"billed_minutes": win_min, "cost": round(win_cost, 4),
                    "as_linux": round(win_min * 0.006, 4)},
        "unpriced_labels": unknown,
    }
