# Slop report design

Approved 2026-09-27.

## Goal

A weekly page that scores the READMEs of the week's most-starred new GitHub repos with slop-linter, with no work from the owner once it's running. It shows off slop-linter by using it on real text, and each report links back to it.

## Decisions

- GitHub READMEs only. LinkedIn is out: it has no API for reading posts, and scraping breaks its terms and names private people. It may come back later as a paste-your-own-text page.
- The page lists both ends: the five cleanest READMEs and the five most AI-sounding, so it's a report, not a call-out list.
- It lives in its own public repo, `thrash-d/slop-report`, served by GitHub Pages from `docs/`.

## Pipeline

A GitHub Action runs every Monday at 14:00 UTC, and on demand.

1. Fetch. Search for repos created in the last 7 days, sorted by stars, and take the top 100. Download each README. Skip archived repos, READMEs under 150 words (not counting code), and READMEs where under 90% of the letters are ASCII. Keep the top 50 left, by stars.
2. Lint. Run Vale 3.22.0 with slop-linter's `.vale.ini` and styles, checked out at a pinned commit. Vale skips code blocks, quoted text, and blockquotes.
3. Score. Score is warning and error alerts per 1,000 words. Suggestions don't count.
4. Pick. Cleanest five: lowest score, ties to more stars. Most AI-sounding five: highest score, from the rest. Each entry shows its three most frequent rules, each with one flagged phrase in context and a count.
5. Publish. Write `docs/index.html` (this week), `docs/archive/<date>.html`, and `docs/data/<date>.json` with every repo's score and alerts. Commit and push as Thrash'd with the built-in token.

## Failure handling

- Fewer than 10 READMEs in the pool: the run fails and publishes nothing, so last week's page stays up. GitHub emails the owner about a failed scheduled run.
- Network errors and 5xx responses retry three times.
- A README that 404s or won't decode is skipped.
- Vale exiting with a runtime error fails the run.

## Testing

`test_report.py` covers word counting, the language check, scoring, picking, and HTML escaping, with no network. The weekly job runs it before the report. The first real run is started by hand and its page checked.

## Out of scope for now

LinkedIn, bot accounts that post the report, and a GitHub Marketplace action for slop-linter.
