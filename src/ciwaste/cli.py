"""ciwaste command line."""
import argparse
import json
import os
import sys

from . import __version__
from .analyze import analyze
from .fetch import FetchError, fetch_snapshot, stderr_progress, Client, get_token
from .lint import RULES, lint_paths
from .rates import Pricer, SKU_RATES, RATES_AS_OF, RATES_URL, parse_rate_overrides
from . import render


def _load(source, args):
    if os.path.isfile(source):
        with open(source, encoding="utf-8") as fh:
            return json.load(fh)
    if source.count("/") == 1 and not source.endswith(".json"):
        return fetch_snapshot(source, days=args.days, max_runs=args.max_runs, client=Client(get_token()),
                              progress=stderr_progress, branch=args.branch, workflow=args.workflow)
    raise FetchError("%s is neither a snapshot file nor OWNER/REPO" % source)


def _guess_repo():
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    try:
        import subprocess
        url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return None
    for pre in ("git@github.com:", "https://github.com/", "ssh://git@github.com/"):
        if url.startswith(pre):
            r = url[len(pre):]
            return r[:-4] if r.endswith(".git") else r
    return None


def _fetch_args(p):
    p.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    p.add_argument("--max-runs", type=int, default=1000, help="cap on runs fetched (default 1000, the API max)")
    p.add_argument("--branch", help="only runs for this branch")
    p.add_argument("--workflow", help="only runs of this workflow file name, e.g. ci.yml")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ciwaste", description="Find the GitHub Actions minutes you pay for and don't need.")
    ap.add_argument("--version", action="version", version="ciwaste " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    rp = sub.add_parser("report", help="cost + waste report for OWNER/REPO or a saved snapshot")
    rp.add_argument("source", nargs="?", help="OWNER/REPO or snapshot.json (default: this checkout's GitHub repo)")
    _fetch_args(rp)
    rp.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    rp.add_argument("--top", type=int, default=8)
    rp.add_argument("--rate", action="append", metavar="LABEL=USD", help="per-minute price for a custom runner label")
    rp.add_argument("--self-hosted-rate", type=float, default=0.0, help="price self-hosted minutes (default 0)")
    rp.add_argument("--count-default-branch", action="store_true",
                    help="also count overtaken runs on the default branch as waste")
    rp.add_argument("--fail-over", type=float, metavar="USD", help="exit 2 if waste per 30 days exceeds USD")
    rp.add_argument("--fail-over-pct", type=float, metavar="PCT", help="exit 2 if waste is more than PCT%% of spend")
    rp.add_argument("--save", metavar="FILE", help="also save the fetched snapshot")

    fp = sub.add_parser("fetch", help="download runs + jobs into a snapshot file (analyze offline later)")
    fp.add_argument("repo", nargs="?")
    _fetch_args(fp)
    fp.add_argument("-o", "--output", default="ciwaste-snapshot.json")

    lp = sub.add_parser("lint", help="offline check of workflow files for minute-burning patterns")
    lp.add_argument("path", nargs="?", default=".")
    lp.add_argument("--format", choices=("text", "json", "github"), default="text")
    lp.add_argument("--strict", action="store_true", help="exit 1 on warnings")
    lp.add_argument("--ignore", action="append", default=[], metavar="RULE", help="skip a rule, e.g. CW102 (repeatable)")

    sub.add_parser("rates", help="print the per-minute rates used")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "rates":
            print("GitHub-hosted runner list prices, USD/min (as of %s, %s)" % (RATES_AS_OF, RATES_URL))
            for k, v in SKU_RATES.items():
                print("  %-24s %.3f" % (k, v))
            return 0
        if args.cmd == "lint":
            skip = {r.upper() for x in args.ignore for r in x.split(",")}
            issues = [i for i in lint_paths(args.path) if i.rule not in skip]
            if args.format == "json":
                print(json.dumps([i.as_dict() for i in issues], indent=2))
            elif args.format == "github":
                if issues:
                    print(render.lint_github(issues))
            else:
                print(render.lint_text(issues))
            return 1 if args.strict and any(i.level == "warning" for i in issues) else 0
        if args.cmd == "fetch":
            repo = args.repo or _guess_repo()
            if not repo:
                ap.error("give OWNER/REPO")
            snap = fetch_snapshot(repo, days=args.days, max_runs=args.max_runs, progress=stderr_progress,
                                  branch=args.branch, workflow=args.workflow)
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(snap, fh)
            print("saved %d runs (%d jobs) of %s to %s" % (len(snap["runs"]), sum(len(r["jobs"]) for r in snap["runs"]),
                                                          repo, args.output))
            return 0
        if args.cmd == "report":
            source = args.source or _guess_repo()
            if not source:
                ap.error("give OWNER/REPO or a snapshot file")
            snap = _load(source, args)
            if args.save:
                with open(args.save, "w", encoding="utf-8") as fh:
                    json.dump(snap, fh)
            pricer = Pricer(parse_rate_overrides(args.rate), args.self_hosted_rate)
            rep = analyze(snap, pricer, count_default_branch=args.count_default_branch)
            if args.format == "json":
                print(render.json_report(rep))
            elif args.format == "markdown":
                print(render.markdown_report(rep, args.top), end="")
            else:
                print(render.text_report(rep, args.top))
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary and args.format != "markdown":
                with open(summary, "a", encoding="utf-8") as fh:
                    fh.write(render.markdown_report(rep, args.top))
            monthly = rep["waste_cost"] * rep["monthly_factor"]
            if args.fail_over is not None and monthly > args.fail_over:
                sys.stderr.write("ciwaste: waste ~$%.2f / 30 days is over --fail-over $%.2f\n" % (monthly, args.fail_over))
                return 2
            if args.fail_over_pct is not None and 100 * rep["waste_share"] > args.fail_over_pct:
                sys.stderr.write("ciwaste: waste is %.1f%% of spend, over --fail-over-pct %g\n"
                                 % (100 * rep["waste_share"], args.fail_over_pct))
                return 2
            return 0
        ap.print_help()
        return 0
    except (FetchError, ValueError) as e:
        sys.stderr.write("ciwaste: %s\n" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
