"""Fetch workflow runs + jobs from the GitHub REST API into a snapshot dict."""
import datetime as dt
import http.client
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")

RUN_KEYS = ("id", "name", "path", "event", "head_branch", "head_sha", "status", "conclusion",
            "run_attempt", "run_number", "created_at", "run_started_at", "updated_at", "html_url")
JOB_KEYS = ("id", "name", "labels", "runner_name", "runner_group_name", "status", "conclusion",
            "started_at", "completed_at", "run_attempt", "created_at")


class FetchError(RuntimeError):
    pass


def get_token():
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


class Client:
    def __init__(self, token=None, api=API, opener=None):
        self.token = token
        self.api = api.rstrip("/")
        self.opener = opener or urllib.request.urlopen
        self.calls = 0

    def get(self, path, params=None):
        url = path if path.startswith("http") else self.api + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ciwaste/" + __version__,
        })
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        for attempt in range(4):
            try:
                self.calls += 1
                with self.opener(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                if e.code in (403, 429) and e.headers.get("x-ratelimit-remaining") == "0":
                    raise FetchError("GitHub API rate limit hit; set GITHUB_TOKEN or wait until it resets")
                if e.code == 404:
                    raise FetchError("not found (or no access): " + url)
                raise FetchError("GitHub API error %s for %s" % (e.code, url))
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
                if attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise FetchError("network error for %s: %s" % (url, getattr(e, "reason", e)))
        raise FetchError("giving up on " + url)


def _pick(d, keys):
    return {k: d.get(k) for k in keys}


def fetch_snapshot(repo, days=30, max_runs=1000, client=None, progress=None, branch=None, workflow=None, workers=8):
    """Return a snapshot dict for owner/repo covering the last `days` days."""
    if "/" not in repo:
        raise FetchError("repo must look like OWNER/NAME")
    client = client or Client(get_token())
    info = client.get("/repos/" + repo)
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"per_page": 100, "created": ">=" + since, "status": "completed"}
    if branch:
        params["branch"] = branch
    base = "/repos/%s/actions/runs" % repo
    if workflow:
        base = "/repos/%s/actions/workflows/%s/runs" % (repo, urllib.parse.quote(workflow, safe=""))
    runs, page = [], 1
    while len(runs) < max_runs:
        data = client.get(base, dict(params, page=page))
        batch = data.get("workflow_runs") or []
        if not batch:
            break
        runs.extend(_pick(r, RUN_KEYS) for r in batch)
        if len(batch) < 100 or page >= 10:  # the runs list API stops at 1,000 results
            break
        page += 1
    truncated = len(runs) >= max_runs
    runs = runs[:max_runs]
    def jobs_for(run):
        jobs, jpage, per = [], 1, 100
        while True:
            try:
                data = client.get("/repos/%s/actions/runs/%s/jobs" % (repo, run["id"]),
                                  {"filter": "all", "per_page": per, "page": jpage})
            except FetchError:
                if per == 100:  # big job pages sometimes 502; retry this run with small pages
                    jobs, jpage, per = [], 1, 30
                    continue
                raise
            batch = data.get("jobs") or []
            jobs.extend(_pick(j, JOB_KEYS) for j in batch)
            if len(batch) < per:
                return jobs
            jpage += 1

    failed = []
    done = [0]

    def work(run):
        try:
            run["jobs"] = jobs_for(run)
        except FetchError as e:
            run["jobs"] = []
            run["jobs_error"] = str(e)
            failed.append(run["id"])
        done[0] += 1
        if progress:
            progress(done[0], len(runs))

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        list(pool.map(work, runs))
    return {
        "tool": "ciwaste " + __version__,
        "repo": repo,
        "private": bool(info.get("private")),
        "default_branch": info.get("default_branch") or "main",
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": since,
        "days": days,
        "truncated": truncated,
        "runs_missing_jobs": failed,
        "runs": runs,
    }


def stderr_progress(i, n):
    if sys.stderr.isatty() or os.environ.get("CIWASTE_PROGRESS"):
        sys.stderr.write("\rciwaste: fetched jobs for %d/%d runs" % (i, n))
        if i == n:
            sys.stderr.write("\n")
        sys.stderr.flush()
