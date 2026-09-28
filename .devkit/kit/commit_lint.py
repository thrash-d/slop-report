"""Flag commit messages that describe the diff instead of the reason, or that
carry an AI tool's fingerprints.

Usage:
  python commit_lint.py MSGFILE           commit-msg hook: the message against the staged diff
  python commit_lint.py --commit REV      one existing commit against its own diff
  python commit_lint.py --range A..B      each commit in a range, report only (CI)
  python commit_lint.py --history [N]     the last N commits (default 200), report only

Three tiers:
  hard     AI attribution: co-author and assisted-by trailers naming an AI
           tool, "Generated with ..." lines, the robot emoji, agent trailers,
           and bot authors. Always blocks.
  scored   Weak tells that add up: file lists, bullets that mirror the diff,
           benefit tails, wrap-up sentences, Markdown in the body, no reason
           given. Some human signals subtract. Warns at `warn`, blocks at
           `block`.
  history  Style uniformity and shifts across many commits. Report only.

No one style is enforced. "fixed leak in store (#537)" and "redis-cli: fix
double error message." both pass. Mentioning Claude or an LLM in ordinary
prose passes; only attribution phrasing and trailers count.

Per-repo settings, all optional, in .devkit/commit-lint.json:
  {"warn": 4, "block": 8, "disable": ["rule-id"], "allow_trailers": ["Assisted-by"]}

Exit 1 when a hook or --commit check blocks. Report modes always exit 0.
Standard library only.
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

DEFAULTS = {"warn": 4, "block": 8, "disable": [], "allow_trailers": []}

AI_NAME = (r"claude|copilot|cursor|chatgpt|gpt(?:-?[\d.o]+)?|gemini|codex|devin|aider|windsurf"
           r"|openai|anthropic|llm|ai|jules|amazon q|codewhisperer|tabnine|replit agent")
AI_NAME_RE = re.compile(rf"(?i)\b(?:{AI_NAME})\b")
AI_EMAIL_RE = re.compile(r"(?i)@(?:anthropic\.com|openai\.com)\b|copilot|\bclaude\b|cursoragent|devin-ai")
# Trailer keys that credit someone. An AI name in the value is attribution.
CREDIT_KEYS = {"co-authored-by", "signed-off-by", "reviewed-by", "acked-by", "tested-by", "helped-by",
               "suggested-by", "written-by", "authored-by"}
# Keys that exist only to disclose AI help, so any value counts.
AI_KEYS = {"assisted-by", "generated-by", "ai-assisted", "ai-generated", "ai-tool", "made-with", "x-generated-by"}
AGENT_KEY_RE = re.compile(r"(?i)^replit-[\w-]+:|^x-ai-[\w-]+:")
GENERATED_RE = re.compile(
    rf"(?i)^\W*(?:(?:generated|written|created|authored|produced|drafted|co-?written)\s+(?:with|by|using|via)\b[^,;]{{0,40}}\b(?:{AI_NAME})\b[^,;]{{0,60}}$"
    r"|ai[- ]generated\b|written by (?:an? )?(?:ai|llm)\b)")
BOT_AUTHOR_RE = re.compile(r"(?i)\(aider\)|\b(?:devin-ai[\w-]*|copilot[\w-]*|claude[\w-]*|cursor[\w-]*|codex[\w-]*)\[bot\]")

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⬆↔-↪✅❌♻]")
CONVENTIONAL_RE = re.compile(r"^[a-z]+(?:\([^)]*\))?!?: ")
BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)")
TRAILER_RE = re.compile(r"^[A-Za-z][\w-]*: \S")

INFLATED = ["enhance", "enhanced", "enhances", "enhancement", "enhancements", "streamline", "streamlined",
            "streamlines", "robust", "comprehensive", "seamless", "seamlessly", "leverage", "leverages",
            "leveraging", "utilize", "utilizes", "utilizing", "facilitate", "facilitates", "bolster",
            "elevate", "holistic", "various", "overall", "significantly", "ensure", "ensures", "ensuring",
            "refine", "refines", "refined", "optimal", "improved", "improves", "optimize", "optimized"]
INFLATED_RE = re.compile(r"(?i)\b(" + "|".join(INFLATED) + r")\b")
# "improve" and "optimize" are fine with a number attached.
NEEDS_NUMBER = {"improved", "improves", "optimize", "optimized", "optimal"}
BENEFIT_RE = re.compile(
    r"(?i)\b(?:for|to (?:improve|enhance|ensure|increase|boost))\s+(?:the\s+)?(?:better|improved|enhanced|increased|greater|overall\s+)?\s*"
    r"(?:readability|maintainability|clarity|consistency|reliability|robustness|user experience|ux|"
    r"code quality|developer experience|performance|scalability|extensibility)\b")
WRAPUP_RE = re.compile(
    r"(?i)\b(?:this|these)\s+(?:commit|change|changes|update|updates|pr|patch|refactor)\s+"
    r"(?:introduces?|adds?|improves?|ensures?|enhances?|makes?|provides?|implements?|refactors?|updates?|"
    r"streamlines?|aims?|allows?|enables?|should|will|help)\b|^\s*overall,|^\s*in summary,")
MD_RE = re.compile(r"(?m)^#{1,6} \S|\*\*\S[^*]*\*\*|^ {2,}[-*] \S|^```")
SECTION_RE = re.compile(r"(?im)^(?:summary|changes|key changes|what changed|details|testing|test plan|"
                        r"motivation|overview|description|context|notes?|breaking changes)\s*:?\s*$")
BULLET_VERBS = {"add", "added", "adds", "update", "updated", "updates", "remove", "removed", "removes",
                "refactor", "refactored", "implement", "implemented", "improve", "improved", "fix", "fixed",
                "introduce", "introduced", "create", "created", "enhance", "enhanced", "rename", "renamed",
                "move", "moved", "extract", "extracted", "replace", "replaced", "ensure", "ensured", "clean",
                "cleaned", "convert", "converted", "adjust", "adjusted", "change", "changed", "use", "delete",
                "deleted", "simplify", "simplified", "integrate", "integrated", "optimize", "optimized"}
WHY_RE = re.compile(
    r"(?i)\b(?:because|since|so that|so it|so the|otherwise|caus(?:e|ed|es|ing)|broke|breaks|broken|was|wasn't|"
    r"were|weren't|reported|noticed|turns out|turned out|needed|needs|instead of|fail(?:s|ed|ing|ure)?|crash|"
    r"error|bug|regression|slow|why|without (?:this|it)|prevent|avoid|stopped|stops|missing|wrong|never|"
    r"couldn't|can't|didn't|doesn't|won't|hangs?|leak|timeout|blocked|lost|drops?)\b|#\d+|\d+ ?(?:ms|s|%|x|kb|mb)\b")
CASUAL_RE = re.compile(r"(?i)\b(?:lol|lmao|oops|whoops|ugh|welp|yolo)\b")
VAGUE_SUBJECT_RE = re.compile(r"(?i)^(?:wip|misc|minor (?:fixes|changes|tweaks)|small cleanup|tweaks?|updates?|changes|stuff|cleanup)\.?$")
TEST_CLAIM_RE = re.compile(r"(?i)\b(?:add(?:ed|s)?|write|wrote|new|with)\s+(?:unit\s+|integration\s+|regression\s+)?tests?\b|\btest coverage\b")
FIRST_PERSON_RE = re.compile(r"(?i)\bI(?:'m| am| was| think| thought| tried| noticed| found| couldn't| wasn't| don't| didn't| suspect| guess)\b")
HUMAN_REF_RE = re.compile(r"(?im)^(?:reported-by|reviewed-by|tested-by|fixes|link|bug|closes):|this reverts commit [0-9a-f]{7,}|\breverts? [0-9a-f]{7,}")
QUOTED_ERR_RE = re.compile(r"(?i)[\"'`].{0,80}(?:error|exception|traceback|failed|panic)|^\s{4}\S")
MEASURE_RE = re.compile(r"\b\d+(?:\.\d+)? ?(?:ms|s|sec|MB|KB|GB|%|x)\b|\bfrom \d[\d.,]* to \d")


def git(*args, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        sys.exit(f"commit-lint: git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def load_config():
    top = git("rev-parse", "--show-toplevel", check=False).strip()
    cfg = dict(DEFAULTS)
    p = Path(top or ".") / ".devkit" / "commit-lint.json"
    if p.is_file():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg


def clean_message(text):
    """Drop git's comment lines and everything below the scissors line."""
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if re.match(r"^# -+ >8 -+", line):
            break
        if line.startswith("#"):
            continue
        out.append(line.rstrip())
    return "\n".join(out).strip("\n")


def split_message(msg):
    lines = msg.split("\n")
    subject = next((l for l in lines if l.strip()), "")
    rest = lines[lines.index(subject) + 1:] if subject in lines else []
    paras = "\n".join(rest).strip("\n").split("\n\n")
    trailers = []
    if paras and paras[-1].strip() and all(TRAILER_RE.match(l) for l in paras[-1].strip().split("\n")):
        trailers = paras[-1].strip().split("\n")
        paras = paras[:-1]
    body = "\n\n".join(paras).strip("\n")
    return subject.strip(), body, trailers


def parse_numstat(text):
    files, lines = [], 0
    for row in text.splitlines():
        parts = row.split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], parts[-1]
        files.append(path)
        if a.isdigit() and d.isdigit():
            lines += int(a) + int(d)
    return {"files": files, "lines": lines}


def lint(message, diff=None, author="", cfg=None):
    """Return (hard_findings, score, scored_findings) for one message.

    diff is {"files": [...], "lines": N} or None when unknown. Each finding is
    (rule_id, weight, reason).
    """
    cfg = cfg or DEFAULTS
    off = set(cfg.get("disable", []))
    allow = {k.lower() for k in cfg.get("allow_trailers", [])}
    msg = clean_message(message)
    subject, body, trailers = split_message(msg)
    hard, scored = [], []

    def hit(rule, weight, reason):
        if rule in off:
            return
        (hard if weight is None else scored).append((rule, weight, reason))

    if not subject or re.match(r"^(?:Merge |Revert \"|fixup! |squash! |amend! )", subject):
        return [], 0, []

    # ---- hard tier: attribution ----
    for line in msg.split("\n"):
        s = line.strip()
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.+)$", s)
        if m:
            key, val = m.group(1).lower(), m.group(2)
            if key in allow:
                continue
            if key in AI_KEYS:
                hit("ai-trailer", None, f'"{s[:70]}" discloses AI help in a trailer. Remove it.')
            elif key in CREDIT_KEYS and (AI_NAME_RE.search(val) or AI_EMAIL_RE.search(val)):
                hit("ai-trailer", None, f'"{s[:70]}" credits an AI tool. Remove the trailer.')
            elif AGENT_KEY_RE.match(s):
                hit("agent-trailer", None, f'"{s[:70]}" is an agent metadata trailer. Remove it.')
        if GENERATED_RE.search(s):
            hit("generated-with", None, f'"{s[:70]}" is an AI attribution line. Remove it.')
    if "\U0001F916" in msg:
        hit("robot-emoji", None, "The robot emoji marks AI-generated text. Remove it.")
    if author and BOT_AUTHOR_RE.search(author):
        hit("bot-author", None, f"The author ({author.split('<')[0].strip()}) is an AI agent identity.")

    # ---- scored tier ----
    text = subject + "\n" + body
    body_lines = [l for l in body.split("\n") if l.strip()]
    bullets = [m.group(1) for l in body_lines if (m := BULLET_RE.match(l))]
    files = diff["files"] if diff else []
    size = diff["lines"] if diff else None

    if MD_RE.search(body):
        hit("markdown-body", 3, "Markdown headers, bold, nested bullets, or code fences in the body. Commit bodies are plain text.")
    if SECTION_RE.search(body):
        hit("labeled-sections", 2, 'Labeled sections ("Summary:", "Changes:") read like a PR template, not a commit.')
    if WRAPUP_RE.search(text):
        hit("wrap-up", 2, 'A "This commit/These changes ..." or "Overall," sentence describes the commit instead of the reason.')
    if re.match(r"(?i)^this (?:commit|pr|change|patch)\b", subject):
        hit("this-commit-subject", 1, 'The subject starts with "This commit". Say the change itself.')
    if m := BENEFIT_RE.search(text):
        hit("benefit-tail", 2, f'"{m.group(0)}" is a generic benefit. Say what was wrong before.')
    has_number = bool(re.search(r"\d", text))
    words = {w.lower() for w in INFLATED_RE.findall(text)}
    if has_number:
        words -= NEEDS_NUMBER
    if words:
        hit("inflated-words", min(len(words), 3), f"Inflated words: {', '.join(sorted(words))}.")
    if EMOJI_RE.match(subject) or EMOJI_RE.match(CONVENTIONAL_RE.sub("", subject)):
        hit("emoji-prefix", 2, "An emoji prefix on the subject.")
    rest = CONVENTIONAL_RE.sub("", subject)
    ws = [w for w in re.findall(r"[A-Za-z][\w'-]*", rest)[1:] if len(w) > 3]
    if len(ws) >= 3 and sum(w[0].isupper() for w in ws) / len(ws) >= 0.75:
        hit("title-case", 1, "The Subject Is In Title Case.")
    if len(bullets) >= 3 and all(b.split()[0].lower().rstrip(":") in BULLET_VERBS for b in bullets if b.split()) \
            and all(b[:1].isupper() for b in bullets):
        hit("parallel-bullets", 2, f"{len(bullets)} bullets that each start with a verb like Add or Update, one per change.")
    if files:
        names = {Path(f).name for f in files} | set(files)
        named = sorted({n for n in names if len(n) > 3 and re.search(rf"(?<![\w/.-]){re.escape(n)}(?![\w-])", text)})
        if len(named) >= 3:
            hit("names-files", 2, f"Names {len(named)} changed files ({', '.join(named[:3])}...). The diff already lists them.")
        if len(bullets) >= 3 and len(files) >= 3 and abs(len(bullets) - len(files)) <= 1:
            hit("bullets-mirror-files", 3, f"{len(bullets)} bullets for {len(files)} changed files, one bullet per file.")
        if TEST_CLAIM_RE.search(text) and not any(re.search(r"(?i)test|spec", f) for f in files):
            hit("claims-tests", 4, "Mentions tests, but no test file changed.")
    if size is not None and len(body_lines) > 6 and size < 20:
        hit("long-body-small-diff", 2, f"{len(body_lines)} body lines for a {size}-line diff.")
    if size is not None and VAGUE_SUBJECT_RE.match(rest.strip()) and size > 150 and len(files) >= 5:
        hit("vague-large", 2, f'"{rest.strip()}" for a {size}-line change across {len(files)} files.')
    series = [s for s in re.split(r"(?<=[.!?])\s+", body.replace("\n", " ")) if s.count(",") >= 4]
    if series:
        hit("inventory-sentence", 2, "A sentence lists four or more items. That's usually the diff, restated.")
    elif (n := subject.count(",") + subject.count(";")) >= 2:
        hit("inventory-subject", 1 if n < 4 else 2,
            "The subject lists several changes. One commit per change, or say what ties them together.")
    if len(subject) > 100:
        hit("long-subject", 1 if len(subject) <= 200 else 2, f"A {len(subject)}-character subject. Put the detail in the body.")
    if len(re.findall(r"`[^`\s]+`", body)) >= 2:
        hit("backticks", 1, "Backticked identifiers are a Markdown habit.")
    casual = {w.lower() for w in CASUAL_RE.findall(text)}
    if casual:
        hit("performative-casual", min(len(casual), 2), f"Casual filler ({', '.join(sorted(casual))}) doesn't replace a reason.")
    if len(body_lines) >= 2 and not WHY_RE.search(body):
        hit("no-why", 2, "The body says what changed but never why.")

    # ---- human signals ----
    if FIRST_PERSON_RE.search(text):
        hit("human-first-person", -2, "First person.")
    if HUMAN_REF_RE.search(msg):
        hit("human-reference", -1, "A real reference: a reporter, a Fixes tag, or a revert.")
    # Numbers in an inventory are values copied from the diff, not a measurement.
    if MEASURE_RE.search(text) and not any(r.startswith("inventory") for r, _, _ in scored):
        hit("human-measurement", -1, "A measurement.")
    if any(QUOTED_ERR_RE.search(l) for l in body.split("\n")):
        hit("human-quoted-error", -1, "Quotes an error or output.")

    score = max(0, sum(w for _, w, _ in scored))
    return hard, score, scored


def verdict(hard, score, cfg):
    if hard or score >= cfg["block"]:
        return "block"
    return "warn" if score >= cfg["warn"] else "ok"


def report(label, hard, score, scored, cfg, out=sys.stderr):
    v = verdict(hard, score, cfg)
    if v == "ok":
        return v
    out.write(f"commit-lint: {label}{v} (score {score}{', attribution' if hard else ''})\n")
    for rule, _, reason in hard:
        out.write(f"  hard  {rule}: {reason}\n")
    for rule, w, reason in sorted(scored, key=lambda f: -f[1]):
        if w > 0:
            out.write(f"  {w:+d}    {rule}: {reason}\n")
    if score >= cfg["warn"]:
        out.write("  Say what was wrong or what prompted the change, and leave out what the diff already shows.\n")
    return v


def commit_info(rev):
    msg = git("log", "-1", "--format=%B", rev)
    author = git("log", "-1", "--format=%an <%ae>", rev).strip()
    parents = git("log", "-1", "--format=%P", rev).split()
    diff = parse_numstat(git("show", "--numstat", "--format=", rev)) if len(parents) == 1 else None
    return msg, author, diff, len(parents) > 1


def history(n, cfg):
    revs = git("rev-list", "--no-merges", f"--max-count={n}", "HEAD").split()
    rows = []
    for rev in reversed(revs):
        msg, author, diff, _ = commit_info(rev)
        hard, score, scored = lint(msg, diff, author, cfg)
        subject, body, _ = split_message(clean_message(msg))
        rows.append({"rev": rev[:8], "subject": subject, "score": score, "hard": hard, "scored": scored,
                     "len": len(subject), "body": bool(body), "conv": bool(CONVENTIONAL_RE.match(subject)),
                     "lower": subject[:1].islower(), "period": subject.endswith(".")})
    if not rows:
        print("No commits.")
        return
    print(f"{len(rows)} commits, oldest first.\n")
    flagged = sorted((r for r in rows if r["hard"] or r["score"] >= cfg["warn"]), key=lambda r: (-len(r["hard"]), -r["score"]))
    print(f"Flagged: {len(flagged)} ({sum(1 for r in rows if r['hard'])} with attribution).")
    for r in flagged[:10]:
        why = [f[0] for f in r["hard"]] + [f[0] for f in sorted(r["scored"], key=lambda f: -f[1]) if f[1] > 0]
        print(f"  {r['rev']} score {r['score']:>2}  {r['subject'][:60]}\n           {', '.join(why)}")

    def style(rs):
        return {"conventional": sum(r["conv"] for r in rs) / len(rs), "has body": sum(r["body"] for r in rs) / len(rs),
                "lowercase": sum(r["lower"] for r in rs) / len(rs), "period": sum(r["period"] for r in rs) / len(rs)}

    if len(rows) >= 20:
        lens = [r["len"] for r in rows]
        s = style(rows)
        fixed = [k for k, v in s.items() if v in (0.0, 1.0)]
        print(f"\nSubject length: mean {statistics.mean(lens):.0f}, stdev {statistics.pstdev(lens):.1f}.")
        print("Style rates: " + ", ".join(f"{k} {v:.0%}" for k, v in s.items()) + ".")
        if statistics.pstdev(lens) < 6 and len(fixed) == len(s):
            print("Uniform: every commit has the same shape. Generated or template-forced messages look like this.")
        # Biggest style shift between adjacent windows of 20.
        w = 20
        best = None
        for i in range(w, len(rows) - w + 1, 5):
            a, b = style(rows[i - w:i]), style(rows[i:i + w])
            jump = sum(abs(a[k] - b[k]) for k in a)
            if not best or jump > best[0]:
                best = (jump, i, a, b)
        if best and best[0] >= 1.2:
            _, i, a, b = best
            print(f"\nStyle shift at {rows[i]['rev']} ({rows[i]['subject'][:50]}):")
            for k in a:
                print(f"  {k:<13} {a[k]:>4.0%} -> {b[k]:.0%}")


def main():
    ap = argparse.ArgumentParser(description="Flag AI-looking and low-information commit messages.")
    ap.add_argument("msgfile", nargs="?", help="Commit message file, as git passes to commit-msg.")
    ap.add_argument("--commit", metavar="REV")
    ap.add_argument("--range", metavar="A..B")
    ap.add_argument("--history", nargs="?", const=200, type=int, metavar="N")
    args = ap.parse_args()
    cfg = load_config()

    if args.history is not None:
        history(args.history, cfg)
        return 0
    if args.range:
        gha = os.environ.get("GITHUB_ACTIONS") == "true"
        for rev in git("rev-list", "--reverse", args.range).split():
            msg, author, diff, merge = commit_info(rev)
            if merge:
                continue
            hard, score, scored = lint(msg, diff, author, cfg)
            v = report(f"{rev[:8]} ", hard, score, scored, cfg, sys.stdout)
            if gha and v != "ok":
                subject = split_message(clean_message(msg))[0]
                rules = ", ".join(f[0] for f in hard + [f for f in scored if f[1] > 0])
                print(f"::warning title=commit-lint {rev[:8]}::{subject[:60]} (score {score}): {rules}")
        return 0
    if args.commit:
        msg, author, diff, _ = commit_info(args.commit)
        hard, score, scored = lint(msg, diff, author, cfg)
        return 1 if report("", hard, score, scored, cfg) == "block" else 0
    if not args.msgfile:
        ap.error("give a message file, --commit, --range, or --history")
    msg = Path(args.msgfile).read_text(encoding="utf-8", errors="replace")
    diff = parse_numstat(git("diff", "--cached", "--numstat", check=False))
    author = git("var", "GIT_AUTHOR_IDENT", check=False).rsplit(">", 1)[0] + ">"
    hard, score, scored = lint(msg, diff if diff["files"] else None, author, cfg)
    v = report("", hard, score, scored, cfg)
    if v == "block":
        sys.stderr.write("commit-lint: commit blocked. Reword the message, or set thresholds in .devkit/commit-lint.json.\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
