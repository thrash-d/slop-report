# slop-report

Every Monday, this repo scores the READMEs of the week's most-starred new GitHub repos for AI writing tells, and publishes the five cleanest and the five most AI-sounding at [thrash-d.github.io/slop-report](https://thrash-d.github.io/slop-report/).

The scoring is [slop-linter](https://github.com/thrash-d/slop-linter), a set of Vale rules for the patterns AI writing leans on: stock words like "delve" and "seamless", em dashes, bold-header lists, emoji headings. A README's score is its warnings per 1,000 words. It measures the writing, not the code.

## How a week's report is built

1. The GitHub search API returns the 100 most-starred repos created in the past 7 days.
2. Archived repos, cracked-software and cheat repos, READMEs under 150 words once code is removed, and READMEs that aren't mostly English are dropped. The top 50 left make the pool.
3. Vale runs slop-linter's rules over each README. Code blocks, quoted text, and blockquotes aren't linted, and suggestion-level hits don't count.
4. The page lists the five lowest scores and the five highest, each with its three most frequent rules and an example line.

Every week's full scores are in `docs/data/`, one JSON file per week.

## Run it yourself

You need Python 3.10 or later, [Vale](https://vale.sh) 3.22 or later, a checkout of slop-linter, and a GitHub token.

```sh
git clone https://github.com/thrash-d/slop-linter
GITHUB_TOKEN=<your token> SLOP_LINTER=slop-linter python report.py
```

The page lands in `docs/index.html`. The tests run with `python -m pytest test_report.py`.

## License

MIT.
