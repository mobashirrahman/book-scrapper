# book-scrapper

A resumable, polite Python pipeline for cataloguing publicly accessible book links from configured Bengali-language sources. It stores crawl state in SQLite, keeps source metadata separate from direct file candidates, and validates downloaded file signatures before saving them.

## Use responsibly

Run this only on systems where you have permission. Review each site's robots.txt, terms of service, copyright rules, and rate limits before enabling an adapter. The project does not bypass authentication, paywalls, CAPTCHAs, access controls, or anti-bot challenges. It is designed for bounded, attended work on an appropriate machine; do not run unattended jobs on shared teaching workstations. Keep `data/`, OAuth client files, tokens, and downloaded books out of version control.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
book-scrapper --site amarboi discover
book-scrapper --site amarboi crawl --limit 10
book-scrapper --site amarboi status
```

Use `--data-dir` to choose a private storage directory. `crawl` records catalogue and access links; `download` handles only links classified as public file candidates and enforces a 512 MiB default limit. Optional Google Drive and MEGA integrations are isolated behind their extra dependencies. No credentials are included in this repository.

## Development

```bash
python -m pytest -q
python -m compileall book_scrapper
```

Adapters live in `book_scrapper/sites.py` and `book_scrapper/sources.py`. Add tests with offline fixtures; do not commit scraped data, personal credentials, or downloaded copyrighted material.
