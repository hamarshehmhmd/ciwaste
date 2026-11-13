"""Text / markdown / JSON output."""
import json
import os
import sys

from .rates import RATES_AS_OF


def use_color(stream=None):
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    return hasattr(stream, "isatty") and stream.isatty()


class C:
    def __init__(self, on):
        self.on = on

    def __call__(self, code, s):
        return "\x1b[%sm%s\x1b[0m" % (code, s) if self.on else s


def usd(x):
    return "$%s" % format(x, ",.2f")


def mins(x):
    return format(int(round(x)), ",")


def _table(rows, headers, aligns):
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    def fmt(r):
        return "  ".join((str(v).rjust(w) if a == "r" else str(v).ljust(w)) for v, w, a in zip(r, widths, aligns))
    return [fmt(headers)] + [fmt(r) for r in rows]


def text_report(rep, top=8, color=None):
    c = C(use_color() if color is None else color)
    m = rep["monthly_factor"]
    out = []
    vis = "private repo" if rep["private"] else "public repo: standard runners are free here, $ is the private-repo list price"
    out.append(c("1", "ciwaste  %s" % rep["repo"]) + c("2", "  last %g days  (%s)" % (rep["days"], vis)))
    out.append("%s runs · %s jobs · %s billed min · %s list price%s" % (
        format(rep["runs"], ","), format(rep["jobs"], ","), mins(rep["billed_minutes"]), c("1", usd(rep["list_cost"])),
        ("  (~%s / 30 days)" % usd(rep["list_cost"] * m)) if abs(m - 1) > 0.01 else ""))
    if rep["private"] is False and rep["paid_cost"]:
        out.append("you actually pay %s (larger runners are billed even in public repos)" % usd(rep["paid_cost"]))
    if rep["truncated"]:
        out.append(c("33", "note: hit --max-runs; figures cover the newest runs only and are scaled to 30 days"))
    if rep.get("runs_missing_jobs"):
        out.append(c("33", "note: %d run(s) had no job data (API errors); their cost is not included" % rep["runs_missing_jobs"]))
    out.append("")
    out.append(c("1", "Where the minutes go"))
    rows = [(b["key"], b["runs"], mins(b["billed_minutes"]), usd(b["cost"]), "%d%%" % round(100 * b["share"]))
            for b in rep["by_workflow"][:top]]
    out += ["  " + l for l in _table(rows, ("workflow", "runs", "billed min", "cost", "share"), "lrrrr")]
    out.append("")
    rows = [(b["key"], b["jobs"], mins(b["billed_minutes"]), usd(b["cost"]), "%d%%" % round(100 * b["share"]))
            for b in rep["by_runner"][:top]]
    out += ["  " + l for l in _table(rows, ("runner (billing SKU)", "jobs", "billed min", "cost", "share"), "lrrrr")]
    mac = rep["macos"]
    if mac["cost"] and rep["list_cost"] and mac["cost"] / rep["list_cost"] >= 0.25:
        out.append(c("33", "  macOS is %d%% of the bill: the same %s minutes on Linux would cost %s"
                     % (round(100 * mac["cost"] / rep["list_cost"]), mins(mac["billed_minutes"]), usd(mac["as_linux"]))))
    out.append("")
    out.append(c("1", "Most expensive jobs"))
    rows = [(b["key"][:70], b["jobs"], mins(b["billed_minutes"]), usd(b["cost"])) for b in rep["by_job"][:top]]
    out += ["  " + l for l in _table(rows, ("workflow / job", "runs", "billed min", "cost"), "lrrr")]
    out.append("")
    w = rep["waste_cost"]
    if rep["waste_by_kind"]:
        out.append(c("1;31", "Waste: %s (%d%% of the bill)" % (usd(w), round(100 * rep["waste_share"])))
                   + ("  ~%s / 30 days" % usd(w * m) if abs(m - 1) > 0.01 else ""))
        for k in rep["waste_by_kind"]:
            out.append("  %s  %s  %s min  %d run%s  in %s" % (
                c("31", usd(k["wasted_cost"]).rjust(9)), c("1", k["title"]), mins(k["wasted_minutes"]), k["runs"],
                "" if k["runs"] == 1 else "s", ", ".join(k["workflows"][:3])))
            out.append(c("2", "             fix: " + k["fix"]))
    else:
        out.append(c("32", "No waste patterns found."))
    d = rep["default_branch_superseded"]
    if d["runs"]:
        out.append(c("2", "  (not counted: %s on %d `%s` push runs that a newer commit overtook; cancel those only if "
                          "you don't need every commit on %s tested)" % (usd(d["wasted_cost"]), d["runs"],
                                                                        rep["default_branch"], rep["default_branch"])))
    if rep["findings"]:
        out.append("")
        out.append(c("1", "Biggest single items"))
        for f in rep["findings"][:top]:
            out.append("  %s  %-18s %s" % (c("31", usd(f["wasted_cost"]).rjust(8)), f["kind"], f["detail"]))
            if f.get("run_url"):
                out.append(c("2", "            " + f["run_url"]))
    if rep["unpriced_labels"]:
        out.append("")
        out.append(c("33", "unpriced runner labels (counted as $0): %s  -> use --rate LABEL=USD_PER_MIN"
                     % ", ".join(rep["unpriced_labels"])))
    out.append(c("2", "rates: GitHub-hosted list prices as of %s, each job rounded up to a whole minute; "
                      "self-hosted = $0 unless --self-hosted-rate" % RATES_AS_OF))
    return "\n".join(out)


def markdown_report(rep, top=8):
    m = rep["monthly_factor"]
    L = ["## ciwaste: GitHub Actions spend for `%s` (last %g days)" % (rep["repo"], rep["days"]), ""]
    L.append("**%s** list price over %s billed minutes in %s runs. **Waste: %s (%d%%)**, about %s per 30 days." % (
        usd(rep["list_cost"]), mins(rep["billed_minutes"]), rep["runs"], usd(rep["waste_cost"]),
        round(100 * rep["waste_share"]), usd(rep["waste_cost"] * m)))
    if not rep["private"]:
        L.append("")
        L.append("_Public repo: standard runners are free, $ is what the same usage would cost in a private repo._")
    L += ["", "| waste | cost | minutes | runs | fix |", "|---|---:|---:|---:|---|"]
    for k in rep["waste_by_kind"]:
        L.append("| %s | %s | %s | %d | %s |" % (k["title"], usd(k["wasted_cost"]), mins(k["wasted_minutes"]), k["runs"], k["fix"]))
    L += ["", "| workflow | runs | billed min | cost | share |", "|---|---:|---:|---:|---:|"]
    for b in rep["by_workflow"][:top]:
        L.append("| %s | %d | %s | %s | %d%% |" % (b["key"], b["runs"], mins(b["billed_minutes"]), usd(b["cost"]), round(100 * b["share"])))
    return "\n".join(L) + "\n"


def json_report(rep):
    return json.dumps(rep, indent=2, sort_keys=False)


def lint_text(issues, color=None):
    c = C(use_color() if color is None else color)
    if not issues:
        return c("32", "ciwaste lint: no minute-burning patterns found")
    out = []
    for i in issues:
        lvl = c("33", i.level) if i.level == "warning" else c("36", i.level)
        out.append("%s:%d: %s %s %s" % (i.path, i.line, lvl, c("1", "%s/%s" % (i.rule, i.name)), i.msg))
    n = sum(1 for i in issues if i.level == "warning")
    out.append(c("2", "%d warning(s), %d notice(s)" % (n, len(issues) - n)))
    return "\n".join(out)


def lint_github(issues):
    lvl = {"warning": "warning", "notice": "notice"}
    return "\n".join("::%s file=%s,line=%d,title=%s %s::%s" % (lvl[i.level], i.path, i.line, i.rule, i.name, i.msg)
                     for i in issues)
