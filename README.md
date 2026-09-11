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

### Muktijuddho e-Archive (liberationwarbangladesh)

The archive's 1,865 posts are discovered through the WordPress REST API, and
books are presented in the FlipHTML5 viewer without a native download option.
The publisher still serves every page as a public image, so use the `fetch`
command to pull them:

```bash
book-scrapper --site liberationwarbangladesh discover   # REST catalogue → 1,865 posts
book-scrapper --site liberationwarbangladesh crawl      # parses posts, records viewer links
book-scrapper --site liberationwarbangladesh fetch      # downloads FlipHTML5 page images
book-scrapper --site liberationwarbangladesh fetch --code avdg        # one book
book-scrapper --site liberationwarbangladesh fetch --limit 5 --pdf   # subset, PDF when img2pdf is installed
```

`fetch` stores `0001.webp …` per book under `<data-dir>/fliphtml5/<code>/`,
writes a resumable `progress.json`, and a `summary.json` manifest at the root.
Pass `--pdf` to assemble a PDF per book (requires optional `img2pdf`); without
it the pages are kept as images. Books uploaded via PDF.js rendering (a handful
of recent English titles) have no page-image store and are reported as failed
without stopping the run.

A couple of operational notes: the archive has no `robots.txt`, so discovery
uses the `robots_redirect="follow"` client mode; and the full flipbook
collection is roughly 35 GB, far larger than a typical workstation budget, so
download in waves with `--limit` and a target directory on storage you can
spare.

## Development

```bash
python -m pytest -q
python -m compileall book_scrapper
```

Adapters live in `book_scrapper/sites.py` and `book_scrapper/sources.py`. Add tests with offline fixtures; do not commit scraped data, personal credentials, or downloaded copyrighted material.
