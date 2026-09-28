"""Weekly slop report: score the week's most-starred new GitHub repos' READMEs
with slop-linter and write the Pages site under docs/."""

import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
WORK = ROOT / "work"
# Resolved against ROOT because Vale runs from WORK.
SLOP_LINTER = (ROOT / os.environ.get("SLOP_LINTER", "slop-linter")).resolve()

CANDIDATES = 100
POOL = 50
MIN_POOL = 10
MIN_WORDS = 150
PICK = 5


def gh(path, raw=False):
    """GET from the GitHub API. Returns parsed JSON, or text when raw. None on 404."""
    req = urllib.request.Request("https://api.github.com" + path, headers={
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
        "User-Agent": "slop-report",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
            return body.decode("utf-8") if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code < 500 or attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(5)


def prose(text):
    """README text minus code, HTML tags, and URLs: what a reader reads as prose."""
    text = re.sub(r"(?s)```.*?```|~~~.*?~~~", " ", text)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r"(?s)<!--.*?-->|<[^>]+>", " ", text)
    return re.sub(r"https?://\S+", " ", text)


def word_count(text):
    return len(re.findall(r"[^\W\d_][\w'’-]*", prose(text)))


def mostly_english(text):
    letters = re.findall(r"[^\W\d_]", prose(text))
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) >= 0.9


# Cracked-software and cheat repos are malware bait, and the page shouldn't link
# readers to them.
# ponytail: keyword list, misses bait that avoids these words; add a star-age or report-count check if it slips through.
BAIT = re.compile(
    r"\b(crack(ed)?|keygen|unlocked|full premium|premium free|free download|activator|license key|"
    r"mod apk|cheats?|hack(s|ed)?|executor|injector|spoofer|aimbot)\b", re.I)


def is_bait(repo):
    return bool(BAIT.search(f'{repo["full_name"].replace("-", " ")} {repo["description"] or ""}'))


def fetch_pool(today):
    since = (today - dt.timedelta(days=7)).isoformat()
    q = urllib.parse.quote(f"created:>={since}")
    found = gh(f"/search/repositories?q={q}&sort=stars&order=desc&per_page={CANDIDATES}")["items"]
    pool = []
    for repo in found:
        if repo["archived"] or is_bait(repo):
            continue
        try:
            readme = gh(f"/repos/{repo['full_name']}/readme", raw=True)
        except UnicodeDecodeError:
            continue
        if not readme or not mostly_english(readme) or word_count(readme) < MIN_WORDS:
            continue
        pool.append({
            "repo": repo["full_name"],
            "url": repo["html_url"],
            "stars": repo["stargazers_count"],
            "description": repo["description"] or "",
            "readme": readme,
        })
        if len(pool) == POOL:
            break
    return pool


def lint(pool):
    """Runs Vale once over every README. Returns {repo: [alert, ...]}."""
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    names = {}
    for entry in pool:
        name = entry["repo"].replace("/", "__") + ".md"
        (WORK / name).write_text(entry["readme"], encoding="utf-8")
        names[name] = entry["repo"]
    run = subprocess.run(
        ["vale", "--output=JSON", "--config", str(SLOP_LINTER / ".vale.ini"), "."],
        cwd=WORK, capture_output=True, text=True, encoding="utf-8")
    # Vale exits 1 when it finds errors, 2 when it couldn't run.
    if run.returncode > 1:
        sys.exit("Vale failed: " + run.stderr)
    found = json.loads(run.stdout or "{}")
    return {repo: found.get(name, []) for name, repo in names.items()}


def snippet(line, match, width=60):
    """The flagged phrase with some text either side, as escaped HTML with the phrase marked."""
    i = line.find(match)
    if i < 0:
        return html.escape(match)
    start, end = max(0, i - width), min(len(line), i + len(match) + width)
    before = ("…" if start else "") + line[start:i]
    after = line[i + len(match):end] + ("…" if end < len(line) else "")
    return html.escape(before) + "<mark>" + html.escape(match) + "</mark>" + html.escape(after)


def score(pool, alerts):
    """Adds words, score, and the top three rules to each entry. Score is warning
    and error alerts per 1,000 words."""
    for entry in pool:
        hits = [a for a in alerts[entry["repo"]] if a["Severity"] in ("warning", "error")]
        lines = entry["readme"].splitlines()
        entry["words"] = word_count(entry["readme"])
        entry["alerts"] = len(hits)
        entry["score"] = round(len(hits) * 1000 / entry["words"], 1)
        top = []
        for check, count in Counter(a["Check"] for a in hits).most_common(3):
            first = next(a for a in hits if a["Check"] == check)
            line = lines[first["Line"] - 1] if first["Line"] <= len(lines) else ""
            top.append({
                "rule": check.split(".")[-1],
                "message": first["Message"],
                "count": count,
                "snippet": snippet(line.strip(), first["Match"]),
            })
        entry["top"] = top
    return pool


def pick(pool):
    clean = sorted(pool, key=lambda e: (e["score"], -e["stars"]))[:PICK]
    rest = [e for e in pool if e not in clean]
    sloppy = sorted(rest, key=lambda e: (-e["score"], -e["stars"]))[:PICK]
    return clean, sloppy


CSS = """
:root{--bg:#fafafa;--fg:#141414;--muted:#5c5c5c;--line:#e2e2e2;--card:#fff;--accent:#b8321f;--mark:#fde2dd}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#f0f0f0;--muted:#a0a0a0;--line:#2c2c2c;--card:#1c1c1c;--accent:#e84430;--mark:#5a1f17}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:760px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:1.9rem;margin:0 0 8px;line-height:1.2}
h1 span{color:var(--accent)}
h2{font-size:1.25rem;margin:40px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--accent)}
p.lede,footer{color:var(--muted)}
a{color:var(--accent)}
ol{list-style:none;padding:0;margin:0}
li.repo{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:0 0 12px}
.head{display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap}
.head a{font-weight:600;word-break:break-all}
.score{font-variant-numeric:tabular-nums;white-space:nowrap}
.meta{color:var(--muted);font-size:.9rem;margin:2px 0 8px}
ul.hits{margin:0;padding-left:18px;font-size:.92rem}
ul.hits li{margin:4px 0}
.snip{display:block;color:var(--muted);overflow-wrap:anywhere}
mark{background:var(--mark);color:var(--fg);padding:0 2px;border-radius:2px}
footer{margin-top:48px;font-size:.9rem;border-top:1px solid var(--line);padding-top:16px}
"""


def entry_html(e):
    hits = "".join(
        f'<li><strong>{html.escape(t["rule"])}</strong> ×{t["count"]}: {html.escape(t["message"])}'
        f'<span class="snip">{t["snippet"]}</span></li>' for t in e["top"])
    hits = f'<ul class="hits">{hits}</ul>' if hits else '<p class="meta">Nothing flagged.</p>'
    desc = f' · {html.escape(e["description"])}' if e["description"] else ""
    return (
        f'<li class="repo"><div class="head"><a href="{html.escape(e["url"])}">{html.escape(e["repo"])}</a>'
        f'<span class="score">{e["score"]} per 1,000 words</span></div>'
        f'<div class="meta">★ {e["stars"]:,} · {e["words"]:,} words{desc}</div>{hits}</li>')


def render(today, pool, clean, sloppy, weeks, root):
    """One report page. root is the relative path from the page to docs/."""
    day = f"{today:%b} {today.day}, {today.year}"
    median = sorted(e["score"] for e in pool)[len(pool) // 2]
    past = " · ".join(f'<a href="{root}archive/{w}.html">{w}</a>' for w in weeks)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Slop report: week of {day}</title>
<meta name="description" content="This week's most-starred new GitHub repos, with each README scored for AI writing tells by slop-linter.">
<style>{CSS}</style></head>
<body><main>
<h1>Slop report<span>.</span> Week of {day}</h1>
<p class="lede">The {len(pool)} most-starred GitHub repos created this week, with each README scored by
<a href="https://github.com/thrash-d/slop-linter">slop-linter</a>. The score counts AI writing tells per 1,000 words:
stock phrases, em dashes, stacked hedges. It says nothing about the code. This week's median is {median}.</p>
<h2>Cleanest READMEs</h2>
<ol>{"".join(entry_html(e) for e in clean)}</ol>
<h2>Most AI-sounding READMEs</h2>
<ol>{"".join(entry_html(e) for e in sloppy)}</ol>
<footer>
<p>Every Monday, this page takes the 100 most-starred repos created in the past week and keeps the top {POOL} with an
English README of at least {MIN_WORDS} words, not counting code. Vale runs slop-linter's rules on each one, and suggestions
don't count toward the score. <a href="{root}data/{today}.json">All {len(pool)} scores</a> ·
<a href="https://github.com/thrash-d/slop-report">How it works</a></p>
<p>Past weeks: {past}</p>
</footer>
</main></body></html>
"""


def publish(today, pool, clean, sloppy):
    (DOCS / "archive").mkdir(parents=True, exist_ok=True)
    (DOCS / "data").mkdir(exist_ok=True)
    (DOCS / ".nojekyll").touch()
    data = [{k: v for k, v in e.items() if k != "readme"} for e in pool]
    (DOCS / "data" / f"{today}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    weeks = sorted((p.stem for p in (DOCS / "data").glob("*.json")), reverse=True)
    (DOCS / "archive" / f"{today}.html").write_text(render(today, pool, clean, sloppy, weeks, "../"), encoding="utf-8")
    (DOCS / "index.html").write_text(render(today, pool, clean, sloppy, weeks, ""), encoding="utf-8")


def main():
    today = dt.datetime.now(dt.timezone.utc).date()
    pool = fetch_pool(today)
    if len(pool) < MIN_POOL:
        sys.exit(f"Only {len(pool)} READMEs qualified, under {MIN_POOL}. Nothing published.")
    pool = score(pool, lint(pool))
    clean, sloppy = pick(pool)
    publish(today, pool, clean, sloppy)
    print(f"Published {today}: {len(pool)} READMEs, cleanest {clean[0]['repo']}, sloppiest {sloppy[0]['repo']}.")


if __name__ == "__main__":
    main()
