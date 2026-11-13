import itertools

_ids = itertools.count(1000)


def T(minute, hour=10, day=1):
    """Timestamp helper: minutes after 10:00 on 2026-11-<day>."""
    hour += minute // 60
    minute %= 60
    return "2026-11-%02dT%02d:%02d:00Z" % (day, hour, minute)


def job(name, start, end, labels=("ubuntu-latest",), conclusion="success", attempt=1):
    return {"id": next(_ids), "name": name, "labels": list(labels), "status": "completed",
            "conclusion": conclusion, "started_at": T(start), "completed_at": T(end), "run_attempt": attempt}


def run(path, event, branch, sha, created, jobs, conclusion="success", attempt=1):
    return {"id": next(_ids), "name": path.split("/")[-1], "path": ".github/workflows/" + path, "event": event,
            "head_branch": branch, "head_sha": sha * 40 if len(sha) == 1 else sha, "status": "completed",
            "conclusion": conclusion, "run_attempt": attempt, "created_at": T(created),
            "html_url": "https://github.com/o/r/actions/runs/1", "jobs": jobs}


def snap(runs, private=True, days=30, default="main"):
    return {"repo": "o/r", "private": private, "default_branch": default, "days": days,
            "fetched_at": "2026-11-10T00:00:00Z", "since": "2026-10-11T00:00:00Z", "runs": runs}
