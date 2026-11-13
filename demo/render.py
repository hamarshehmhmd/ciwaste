"""Render the ciwaste narrated demo: real command output + edge-tts narration.

Usage: python demo/render.py   (needs ffmpeg, edge-tts, Pillow, network)
"""
import asyncio
import os
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(HERE, "build")
OUT = os.path.join(ROOT, "demo.mp4")
SDKBIN = "/tmp/cwvenv/bin"
W, H, FPS = 1280, 720, 24
VOICE = "en-US-AndrewNeural"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BG, FG = (24, 26, 33), (220, 223, 228)
ANSI = {"31": (255, 95, 95), "32": (95, 215, 120), "33": (240, 200, 80),
        "2": (130, 135, 145), "36": (90, 200, 230), "35": (200, 140, 230), "1": FG}
PROMPT = "dev@laptop $ "


DEMO = "/tmp/cwdemo"
P = "dev@laptop ~ $ "

SCENES = [
    {
        "kind": "title",
        "title": "ciwaste",
        "sub": "find the GitHub Actions minutes you pay for\nand don't need",
        "say": "This is C I waste, an open source command line tool and GitHub Action that shows where your GitHub Actions "
               "minutes go, and which of them you are paying for without needing to.",
    },
    {
        "kind": "bullets",
        "title": "The problem",
        "bullets": [
            "\"Pricing Changes for GitHub Actions\": 802 points, 819 comments on HN",
            "r/devops: \"GitHub's billing page is useless for cost attribution\"",
            "Superseded PR runs keep running and are billed to the end",
            "A hung job bills up to 360 minutes (the default timeout)",
            "macOS minutes cost ~10x Linux",
        ],
        "say": ("GitHub's Actions pricing change drew eight hundred points and over eight hundred comments on Hacker News, "
                "and teams on Reddit say the billing page is useless for working out which workflow costs what. "
                "Meanwhile the waste is invisible: old pull request runs keep going after a new push, hung jobs bill up to "
                "six hours, and mac O S minutes cost about ten times Linux."),
    },
    {
        "kind": "term",
        "cmds": ["cat fetch.out"],
        "show": ["ciwaste fetch pydantic/pydantic --days 7 --max-runs 400 -o pydantic.json"],
        "say": ("Let's look at a real, busy public repo: pydantic. ciwaste fetch pulls the workflow runs and every job "
                "attempt from the GitHub A P I into a snapshot file. Four hundred runs, almost seven thousand jobs. "
                "You can also skip the file and run report on the repo directly."),
    },
    {
        "kind": "term",
        "cmds": ["ciwaste report pydantic.json --top 4 | head -n 27"],
        "say": ("The report prices every job at GitHub's list rate, rounded up per job like GitHub bills. Pydantic is public, "
                "so it pays nothing, but in a private repo this would be about two thousand four hundred dollars a month. "
                "Seventy percent of it is mac O S, and the same minutes on Linux would cost twenty eight dollars."),
    },
    {
        "kind": "term",
        "cmds": ["ciwaste report pydantic.json --top 3 | tail -n 20"],
        "say": ("Then the waste. Fourteen runs on pull request branches kept running after a newer commit landed on the same branch, "
                "which a concurrency group with cancel in progress would stop. Flaky jobs that failed and passed on re-run "
                "cost money too. Each item links to the run, and overtaken runs on main are listed but not counted, "
                "because you may want every main commit tested."),
    },
    {
        "kind": "term",
        "cmds": ["ciwaste lint pydantic --ignore CW102"],
        "say": ("ciwaste lint checks the workflow files offline and points at the cause: no cancel in progress in C I dot "
                "yml and codspeed dot yml, and mac O S jobs on every pull request."),
    },
    {
        "kind": "term",
        "cmds": ["ciwaste lint myapp"],
        "say": ("On a small app it also catches push and pull request both firing for every P R commit, jobs without "
                "a timeout, and a cron that runs every fifteen minutes."),
    },
    {
        "kind": "bullets",
        "title": "In CI",
        "bullets": [
            "- uses: hamarshehmhmd/ciwaste@v0.1.0",
            "   with: { days: 30, fail-over: 50 }",
            "Report lands in the job summary (markdown)",
            "--fail-over USD / --fail-over-pct: a budget gate",
            "--format json for dashboards; --rate for custom runners",
        ],
        "say": ("In C I, use the GitHub Action. It writes the report to the job summary, and fail over turns it into a "
                "budget gate when monthly waste passes a dollar amount. There's J S O N output too, and custom runner prices."),
    },
    {
        "kind": "bullets",
        "title": "Limits",
        "bullets": [
            "Estimates from public list prices, not your invoice",
            "Doesn't know your included minutes or discounts",
            "API returns at most 1,000 runs per query",
            "Self-hosted counted as $0 unless --self-hosted-rate",
            "For org totals, also see Insights > Actions Usage Metrics",
        ],
        "say": ("The limits. These are estimates from public list prices, not your invoice, so included minutes and "
                "discounts aren't applied. The A P I returns at most a thousand runs per query, and self hosted minutes "
                "count as zero unless you set a rate."),
    },
    {
        "kind": "bullets",
        "title": "Try it",
        "bullets": [
            "curl -fsSL https://raw.githubusercontent.com/",
            "   hamarshehmhmd/ciwaste/main/install.sh | sh",
            "ciwaste report OWNER/REPO  ·  ciwaste lint",
            "github.com/hamarshehmhmd/ciwaste",
            "GNU AGPL-3.0-or-later (commercial licenses available)",
        ],
        "say": ("Install it with the one line installer and run ciwaste report on your repo. It's licensed under the "
                "GNU Affero General Public License version 3 or later, with commercial licenses available from the author."),
    },
]


async def tts(text, path):
    import edge_tts
    for attempt in range(8):
        try:
            await edge_tts.Communicate(text, VOICE, rate="+4%").save(path)
            return
        except Exception:
            if os.path.exists(path):
                os.remove(path)
            if attempt == 7:
                raise
            await asyncio.sleep(5)


def duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        text=True,
    )
    return float(out.strip())


EXPORTS = {}


def run_cmd(cmd, cwd):
    if cmd.startswith("export ") and "=" in cmd:
        k, v = cmd[7:].split("=", 1)
        EXPORTS[k] = v
        return ""
    env = dict(os.environ, FORCE_COLOR="1", **EXPORTS)
    env.pop("NO_COLOR", None)
    env["PATH"] = SDKBIN + os.pathsep + env["PATH"]
    env["COLUMNS"] = "120"
    p = subprocess.run(
        ["bash", "-c", "shopt -s nullglob; " + cmd],
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    return p.stdout.rstrip("\n")


def parse_ansi(line):
    spans, color, bold = [], FG, False
    for part in re.split(r"(\x1b\[[0-9;]*m)", line):
        m = re.match(r"\x1b\[([0-9;]*)m", part)
        if m:
            for code in (m.group(1) or "0").split(";"):
                if code == "0":
                    color, bold = FG, False
                elif code == "1":
                    bold = True
                elif code in ANSI:
                    color = ANSI[code]
        elif part:
            spans.append((part, color, bold))
    return spans


class Term:
    def __init__(self):
        self.font = ImageFont.truetype(MONO, 14)
        self.bold = ImageFont.truetype(MONO_B, 14)
        self.cw = self.font.getlength("M")
        self.lh = 20
        self.cols = int((W - 80) / self.cw)

    def wrap(self, spans):
        lines, cur, n = [], [], 0
        for text, c, b in spans:
            while text:
                room = self.cols - n
                if room <= 4:
                    lines.append(cur); cur, n = [], 0
                    text = "    " + text.lstrip(" ")
                    room = self.cols
                if len(text) <= room:
                    cur.append((text, c, b)); n += len(text); text = ""
                else:
                    cut = text.rfind(" ", 0, room + 1)
                    cut = cut if cut > 4 else room
                    cur.append((text[:cut], c, b)); lines.append(cur); cur, n = [], 0
                    text = "    " + text[cut:].lstrip(" ")
        lines.append(cur)
        return lines

    def frame(self, lines, cursor=False):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([20, 14, W - 20, H - 14], 12, fill=(30, 33, 41), outline=(60, 64, 76))
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse([40 + i * 24, 30, 54 + i * 24, 44], fill=c)
        d.text((W // 2, 37), "~ — bash", font=ImageFont.truetype(SANS, 15), fill=(150, 155, 165), anchor="mm")
        visible = lines[-30:]
        y = 64
        for ln in visible:
            x = 40
            for text, c, b in ln:
                d.text((x, y), text, font=self.bold if b else self.font, fill=c)
                x += self.cw * len(text)
            y += self.lh
        if cursor:
            last = visible[-1] if visible else []
            x = 40 + self.cw * sum(len(t) for t, _, _ in last)
            d.rectangle([x, y - self.lh + 3, x + self.cw - 2, y - 4], fill=FG)
        return img


def card(scene):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if scene["kind"] == "title":
        d.text((W // 2, 280), scene["title"], font=ImageFont.truetype(MONO_B, 78), fill=(95, 215, 120), anchor="mm")
        d.multiline_text((W // 2, 420), scene["sub"], font=ImageFont.truetype(SANS, 32), fill=FG, anchor="mm",
                         align="center", spacing=12)
        d.text((W // 2, 620), "github.com/hamarshehmhmd/ciwaste  ·  report · fetch · lint · GitHub Action  ·  GNU AGPL-3.0",
               font=ImageFont.truetype(SANS, 20), fill=(150, 155, 165), anchor="mm")
    else:
        d.text((90, 90), scene["title"], font=ImageFont.truetype(SANS_B, 52), fill=(95, 215, 120))
        f = ImageFont.truetype(SANS, 29)
        y = 200
        for b in scene["bullets"]:
            if b and not b.startswith("   "):
                d.text((90, y), "•", font=f, fill=(240, 200, 80))
            d.text((130, y), b.strip(), font=f, fill=FG)
            y += 58 if b.startswith("   ") or not b else 62
    return img


class Writer:
    def __init__(self, path, audio):
        self.p = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", audio,
             "-af", "apad", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
             "-shortest", path],
            stdin=subprocess.PIPE,
        )
        self.n = 0

    def put(self, img, count=1):
        data = img.tobytes()
        for _ in range(count):
            self.p.stdin.write(data)
        self.n += count

    def close(self):
        self.p.stdin.close()
        self.p.wait()
        if self.p.returncode:
            sys.exit("ffmpeg failed")


def _cap_output(out, tail=False):
    lines = out.splitlines()
    if tail and len(lines) > 26:
        return "\n".join(["…"] + lines[-25:])
    if len(lines) > 24:
        lines = lines[:22] + ["…", lines[-1]]
    return "\n".join(lines)


def render_scene(i, scene, workdir):
    audio = os.path.join(BUILD, f"s{i}.mp3")
    if not os.path.exists(audio):
        asyncio.run(tts(scene["say"], audio))
    total = int((duration(audio) + 0.7) * FPS)
    path = os.path.join(BUILD, f"s{i}.mp4")
    w = Writer(path, audio)
    if scene["kind"] != "term":
        w.put(card(scene), total)
        w.close()
        return path
    t = Term()
    shown = scene.get("show", scene["cmds"])
    wd = workdir
    outputs = [_cap_output(run_cmd(c, wd), scene.get("tail")) for c in scene["cmds"]]
    # If show has more lines than cmds (a command displayed as several visual lines
    # that map to one executed command), join show into one typed command per output.
    if len(shown) != len(outputs):
        if len(outputs) == 1:
            shown = [" ".join(s.rstrip("\\").strip() for s in shown)]
        elif len(shown) > len(outputs):
            # first N-1 outputs map 1:1; remaining show lines are the last command
            shown = list(shown[: len(outputs) - 1]) + [
                " ".join(s.rstrip("\\").strip() for s in shown[len(outputs) - 1 :])
            ]
    steps = list(zip(shown, outputs))
    typing = sum(len(s) for s, _ in steps) / 32 * FPS
    pause = max(6, int((total - typing) / max(1, len(steps))) - 4)
    lines = []
    for cmd, out in steps:
        prompt = [(scene.get("prompt", PROMPT), (95, 215, 120), True)]
        for k in range(0, len(cmd) + 1, 2):
            frame = t.frame(lines + t.wrap(prompt + [(cmd[:k], FG, False)]), cursor=True)
            w.put(frame)
        lines += t.wrap(prompt + [(cmd, FG, False)])
        for ol in out.splitlines():
            lines += t.wrap(parse_ansi(ol))
        lines.append([])
        hold = min(pause, max(0, total - w.n))
        w.put(t.frame(lines), hold)
    final = t.frame(lines + t.wrap([(scene.get("prompt", PROMPT), (95, 215, 120), True)]), cursor=True)
    if w.n < total:
        w.put(final, total - w.n)
    w.close()
    return path





def setup():
    assert os.path.exists(os.path.join(DEMO, "pydantic.json")), "run the fetch first (see README)"


def main():
    os.makedirs(BUILD, exist_ok=True)
    setup()
    only = [int(x) for x in sys.argv[1:]]
    parts = [render_scene(i, s, s.get("cwd", DEMO)) if (not only or i in only) else os.path.join(BUILD, "s%d.mp4" % i)
             for i, s in enumerate(SCENES)]
    with open(os.path.join(BUILD, "list.txt"), "w") as fh:
        fh.writelines(f"file '{p}'\n" for p in parts)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
         os.path.join(BUILD, "list.txt"), "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", OUT],
        check=True,
    )
    with open(os.path.join(BUILD, "narration.txt"), "w") as fh:
        fh.write("\n\n".join(s["say"] for s in SCENES) + "\n")
    print(OUT, round(duration(OUT), 1), "s")


if __name__ == "__main__":
    main()
