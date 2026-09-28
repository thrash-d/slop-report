"""Flag code that redefines something the repo keeps in one place.

Rules live in .devkit/duplicates.json. Each rule is a regex plus the files
allowed to contain it. A match anywhere else is a finding. Exit codes:
0 clean, 1 findings, 3 bad config.

    python .devkit/kit/check_duplicates.py              # whole repo
    python .devkit/kit/check_duplicates.py path/a.py    # just these files

A line containing "devkit-allow: <rule id>" is skipped for that rule.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_EXCLUDE_DIRS = {
    ".git", ".devkit", "node_modules", "dist", "build", "__pycache__",
    ".venv", "venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next",
}
MAX_BYTES = 1_000_000


class ConfigError(Exception):
    pass


def find_root(start: Path) -> Path | None:
    for d in [start, *start.parents]:
        if (d / ".devkit" / "duplicates.json").is_file():
            return d
    return None


def load_rules(config: Path) -> tuple[list[dict], set[str]]:
    try:
        data = json.loads(config.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"{config}: {e}") from e
    exclude = DEFAULT_EXCLUDE_DIRS | set(data.get("exclude_dirs", []))
    rules = []
    for i, r in enumerate(data.get("rules", [])):
        rid = r.get("id") or f"rule-{i + 1}"
        if "pattern" not in r:
            raise ConfigError(f"{config}: rule {rid} has no pattern")
        try:
            rx = re.compile(r["pattern"])
        except re.error as e:
            raise ConfigError(f"{config}: rule {rid} pattern: {e}") from e
        rules.append({
            "id": rid,
            "rx": rx,
            "files": r.get("files", ["*"]),
            "allow": r.get("allow", []),
            "message": r.get("message", ""),
        })
    return rules, exclude


def matches_any(rel: str, globs: list[str]) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g) for g in globs)


def walk(root: Path, exclude: set[str]):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude]
        for f in filenames:
            yield Path(dirpath) / f


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:4096]:
        return None
    return raw.decode("utf-8", errors="replace")


def check_file(path: Path, rel: str, rules: list[dict]) -> list[str]:
    applicable = [r for r in rules if matches_any(rel, r["files"]) and not matches_any(rel, r["allow"])]
    if not applicable:
        return []
    text = read_text(path)
    if text is None:
        return []
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for r in applicable:
            if r["rx"].search(line) and f"devkit-allow: {r['id']}" not in line:
                hits.append(f"{rel}:{n}: [{r['id']}] {r['message']}".rstrip())
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--root", type=Path)
    args = ap.parse_args(argv)

    start = args.root or (args.files[0].resolve().parent if args.files else Path.cwd())
    root = args.root.resolve() if args.root else find_root(start.resolve())
    if root is None or not (root / ".devkit" / "duplicates.json").is_file():
        return 0
    try:
        rules, exclude = load_rules(root / ".devkit" / "duplicates.json")
    except ConfigError as e:
        print(f"check_duplicates: bad config: {e}", file=sys.stderr)
        return 3
    if not rules:
        return 0

    if args.files:
        paths = [p.resolve() for p in args.files]
    else:
        paths = list(walk(root, exclude))

    hits = []
    for p in paths:
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        if any(part in exclude for part in rel.split("/")[:-1]):
            continue
        hits.extend(check_file(p, rel, rules))

    for h in hits:
        print(h)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
