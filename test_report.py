import datetime as dt

import report


def alert(check, match, line=1, severity="warning"):
    return {"Check": "NoSlop." + check, "Match": match, "Line": line, "Severity": severity,
            "Message": f"Flagged '{match}'."}


def entry(repo, stars, words):
    return {"repo": repo, "url": "https://github.com/" + repo, "stars": stars,
            "description": "", "readme": "word " * words}


def test_word_count_skips_code_html_and_urls():
    text = "Install it now.\n\n```bash\nnpm install a b c d e\n```\n<img src=x> see https://a.io/b `x y z`\n"
    assert report.word_count(text) == 4


def test_mostly_english():
    assert report.mostly_english("A plain English README about a tool.")
    assert not report.mostly_english("这是一个用于处理数据的工具，支持多种格式。")


def test_score_counts_warnings_and_errors_per_thousand_words():
    pool = [entry("a/one", 10, 500)]
    pool[0]["readme"] = "This robust tool is seamless.\n" + "word " * 495
    alerts = {"a/one": [alert("AIWordsSoft", "robust"), alert("AIWordsSoft", "seamless"),
                        alert("AIWords", "delve", severity="error"),
                        alert("HeadingCase", "Title", severity="suggestion")]}
    e = report.score(pool, alerts)[0]
    assert e["alerts"] == 3
    assert e["score"] == round(3 * 1000 / e["words"], 1)
    assert [t["rule"] for t in e["top"]] == ["AIWordsSoft", "AIWords"]
    assert e["top"][0]["count"] == 2
    assert "<mark>robust</mark>" in e["top"][0]["snippet"]


def test_pick_keeps_the_two_lists_apart_and_breaks_ties_on_stars():
    pool = [dict(entry(f"o/r{i}", stars=i, words=200), score=s) for i, s in enumerate([0, 0, 1, 5, 9, 9, 2])]
    clean, sloppy = report.pick(pool)
    assert [e["repo"] for e in clean] == ["o/r1", "o/r0", "o/r2", "o/r6", "o/r3"]
    assert [e["repo"] for e in sloppy] == ["o/r5", "o/r4"]


def test_render_escapes_readme_text():
    e = dict(entry("o/<script>", 3, 200), score=1.0, words=200, alerts=0,
             description="<b>hi</b>", top=[])
    page = report.render(dt.date(2026, 9, 28), [e], [e], [], ["2026-09-28"], "")
    assert "<script>" not in page
    assert "o/&lt;script&gt;" in page
    assert "&lt;b&gt;hi&lt;/b&gt;" in page
    assert "Week of Sep 28, 2026" in page


def test_snippet_marks_the_match_and_escapes():
    s = report.snippet("Use <this> robust thing", "robust")
    assert s == "Use &lt;this&gt; <mark>robust</mark> thing"


def test_is_bait():
    assert report.is_bait({"full_name": "x/CapCut-Pro-macOS-Windows", "description": "Capcut Pro Unlocked v2026.7 - Full Premium"})
    assert report.is_bait({"full_name": "x/Roblox-Executor", "description": None})
    assert not report.is_bait({"full_name": "tobi/disktree", "description": "A treemap for finding what fills your disk"})
    assert not report.is_bait({"full_name": "x/hackathon-starter", "description": "Starter kit"})
