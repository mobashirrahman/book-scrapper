# book-scrapper

A resumable, polite Python pipeline for cataloguing publicly accessible book links from configured Bengali-language sources. It stores crawl state in SQLite, keeps source metadata separate from direct file candidates, and validates downloaded file signatures before saving them.

## Use responsibly

Run this only on systems where you have permission. Review each site's robots.txt, terms of service, copyright rules, and rate limits before enabling an adapter. The project does not bypass authentication, paywalls, CAPTCHAs, access controls, or anti-bot challenges. It is designed for bounded, attended work on an appropriate machine; do not run unattended jobs on shared teaching workstations. Keep `data/`, OAuth client files, tokens, and downloaded books out of version control.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
book-scrapper --help
book-scrapper --site amarboi --data-dir data/amarboi discover
book-scrapper --site amarboi --data-dir data/amarboi crawl --limit 10
book-scrapper --site amarboi --data-dir data/amarboi status
```

Use one `--data-dir` per site (e.g. `data/<site>`). State lives in
`<data-dir>/catalog.sqlite3` (WAL mode) plus `books/` for downloads, so runs
are resumable and a second writer on the same directory exits with code 2
(use a separate `--data-dir` per worker). `status`/`export` are read-only and
can run alongside a download.

Global flags (apply to every command):

- `--site {site}` — which adapter to run (see table below, default `amarboi`).
- `--data-dir DIR` — private storage directory (default `data`).
- `--delay SECONDS` — minimum seconds between requests to one host (default `2.0`,
  raised automatically if the site's `robots.txt` sets a larger `Crawl-delay`).
- `--ignore-robots` — skip `robots.txt` checks (only for sites you have
  explicit permission to crawl).

## Supported sites (22)

`--site` choices from `book-scrapper --help`:

| `--site` | Root | Discovery (`discover`) | `crawl` records | `download` / `fetch` |
|---|---|---|---|---|
| `amarboi` | https://www.amarboi.com/ | sitemap | catalogue pages + file/access links | public file candidates |
| `bdebooks` | https://bdebooks.com/bn/ | sitemap | catalogue pages + same-host PDFs/EPUBs | direct files; `/dl/`, `/esdl/`, … handlers stay metadata (robots-disallowed) |
| `granthagara` | https://granthagara.com/ | sitemap (`boi-sitemap`) | book pages | direct files (incl. archive.org mirrors) |
| `bengalionline` | https://isid.ac.in/~deepayan/bengalionline.net/ | sitemap | static `.html` pages | public-domain PDF/EPUB/ZIP under the collection path |
| `fid4sa` | https://fid4sa-repository.ub.uni-heidelberg.de/ | sitemap | eprint records | direct files incl. extensionless `/id/eprint/<id>/<n>/<file>` |
| `dpl_elibrary` | https://elibrary.dpl.gov.bd/ | sitemap | public catalogue pages | metadata-only, no download queue |
| `boiprakash` | https://boiprakash.com/ | sitemap (`shop.php` pagination helper) | `product-details.php?id=` pages | metadata-only catalogue |
| `pdfporo` | https://pdfporo.com/ | sitemap (`post-sitemap`) | book posts | direct files; `/getbook/` links stay metadata |
| `boighor` | https://boighorlibrary.com/ | sitemap | `/books/<slug>/` lending pages | metadata-only (lending, no files) |
| `gutenberg_bengali` | https://www.gutenberg.org/ | sitemap (Bengali OPDS feed helper) | `/ebooks/<id>/` pages | official EPUB/PDF/MOBI/ZIP formats |
| `ndli` | https://ndl.gov.in/ | sitemap | catalogue pages | metadata-only catalogue |
| `kindlebangla` | https://www.kindlebangla.com/ | sitemap (`/book/<id-or-slug>`) | book pages + `/download/<slug>` handlers | `/download/` resolves via Drive API folder → single book file; needs `GOOGLE_DRIVE_API_KEY`, otherwise `unsupported` |
| `banglabook` | https://www.banglabook.org/ | sitemap (`post-sitemap`) | book posts | Drive / MediaFire / MEGA / Box / Dropbox links queued as assets |
| `allbanglaboi` | https://allbanglaboi.com/ | catalogue (WordPress REST `wp/v2/posts`) | book posts | metadata-only catalogue |
| `banglabooks_in` | https://www.banglabooks.in/ | sitemap (`post-sitemap`) | two-segment posts | Drive/file-host links queued as assets |
| `banglabookshelf` | https://www.banglabookshelf.com/ | catalogue (seed `index.php`, `Story-Book.php`) | `.php` series/detail pages | same-host PDFs; crawl follows `.php` links |
| `worldmets` | https://www.worldmets.com/ | sitemap (`post-sitemap`) | 2–4 segment posts | Drive/file-host links queued as assets |
| `archive_bengali` | https://archive.org/ | catalogue (archive.org `advancedsearch.php` `language:ben` + metadata API) | one `done` page per item with `/download/<id>/<file>` assets | public book files; `*_jp2.zip` skipped; ids already in `data/granthagara/catalog.sqlite3` are skipped |
| `bn_wikisource` | https://bn.wikisource.org/ | catalogue (MediaWiki `allpages` API) | one `done` page per work + WS-Export ebook asset | EPUB (preferred, fast) via `ws-export.wmcloud.org` |
| `nctb` | https://nctb.cloud/ | catalogue (seed `textbooks/`, year → level → class crawl) | year/level/class shelves + textbook pages | Drive file links; needs `GOOGLE_DRIVE_API_KEY` |
| `bengaliebook` | https://bengaliebook.com/ | sitemap (`post-sitemap`) | single-slug posts | same-host `wp-content/uploads/…pdf` files |
| `liberationwarbangladesh` | https://liberationwarbangladesh.org/ | catalogue (WordPress REST, `?p=<id>`, ~1,865 posts) | posts; Drive file links → assets, FlipHTML5/viewer links → metadata | Drive file candidates via `download`; FlipHTML5 page images via the `fetch` command |

Notes:

- "metadata-only" means `crawl` catalogues the site and keeps viewer/lending/
  catalogue links in `metadata_links`, but `download` has nothing (or almost
  nothing) to fetch. This is intentional for lending libraries and catalogues
  without public files.
- Google Drive file links resolve through the Drive API and need
  `GOOGLE_DRIVE_API_KEY` (env var or `~/.config/book-scrapper/drive_api_key`).
  Without a key they are recorded as `unsupported`, never bypassed. A Drive
  API 403 automatically retries once via the direct `uc?export=download` URL.
- MEGA links need the optional extra: `pip install -e '.[mega]'`.
- `download` validates the file signature before saving (PDF/EPUB/ZIP/RAR/
  MOBI/FB2/7z; EPUB is confirmed after the ZIP download via its `mimetype`
  entry) and enforces a `--max-mb` limit (default 512 MiB).

## Running the pipeline

One site, full run (recommended per-site directory):

```bash
SITE=amarboi
book-scrapper --site $SITE --data-dir data/$SITE discover
book-scrapper --site $SITE --data-dir data/$SITE crawl
book-scrapper --site $SITE --data-dir data/$SITE download
book-scrapper --site $SITE --data-dir data/$SITE status
```

Everything is resumable: re-running a stage picks up where it left off.
`crawl` without `--limit` keeps following newly discovered collection/post
links until the queue is empty; `download` without `--limit` processes every
pending asset. Check progress any time with `status`, and dump the catalogue
with `export`:

```bash
book-scrapper --site $SITE --data-dir data/$SITE status
book-scrapper --site $SITE --data-dir data/$SITE export data/$SITE/export.jsonl
book-scrapper --site $SITE --data-dir data/$SITE enrich --limit 50  # optional: fill author/publisher/ISBN gaps via authority APIs
```

### Run all sites

There is no `--site all`; loop over the site names with a separate
`--data-dir` per site:

```bash
for SITE in allbanglaboi amarboi archive_bengali banglabook banglabooks_in \
    banglabookshelf bdebooks bengaliebook bengalionline bn_wikisource boighor \
    boiprakash dpl_elibrary fid4sa granthagara gutenberg_bengali kindlebangla \
    liberationwarbangladesh nctb ndli pdfporo worldmets; do
  echo "=== $SITE ==="
  book-scrapper --site "$SITE" --data-dir "data/$SITE" discover
  book-scrapper --site "$SITE" --data-dir "data/$SITE" crawl
  book-scrapper --site "$SITE" --data-dir "data/$SITE" download
done
```

Tips for large runs:

- Start with `--limit` smoke tests before the unbounded run, e.g.
  `crawl --limit 10` / `download --limit 5`.
- The FlipHTML5 collection behind `liberationwarbangladesh` is ~35 GB; page
  images go through the `fetch` command, not `download`, so budget storage and
  use `--limit` waves.
- Set `GOOGLE_DRIVE_API_KEY` before running Drive-backed sites
  (`nctb`, `kindlebangla`, `banglabooks_in`, `worldmets`, `banglabook`).
- Keep the polite default `--delay 2.0` (or higher); each origin is paced and
  retried (429/5xx), and `robots.txt` is honoured unless `--ignore-robots` is
  passed for a site you have permission to crawl.

### Only discovery (`discover`)

Enumerates catalogue pages into `pages` (status `pending`). Sitemap adapters
re-walk `sitemap.xml`; catalogue adapters page their API/seed listing.
Safe to re-run — new posts are added, existing rows are kept.

```bash
book-scrapper --site amarboi --data-dir data/amarboi discover
book-scrapper --site liberationwarbangladesh --data-dir data/liberationwarbangladesh discover
book-scrapper --site archive_bengali --data-dir data/archive_bengali discover
book-scrapper --site bn_wikisource --data-dir data/bn_wikisource discover
```

### Only crawling (`crawl`)

Fetches pending pages, extracts title/metadata, and sorts links into
downloadable `assets` vs `metadata_links`. Prints `Indexed:` / `Failed page:`
per URL.

```bash
book-scrapper --site amarboi --data-dir data/amarboi crawl --limit 10   # first 10 only
book-scrapper --site amarboi --data-dir data/amarboi crawl              # everything queued (loops until empty)
book-scrapper --site amarboi --data-dir data/amarboi crawl --retry      # include previously failed pages too
book-scrapper --site amarboi --data-dir data/amarboi crawl --limit 50 --retry
```

### Only downloading (`download`)

Fetches only links classified as public file candidates, streams each to
`books/<sha256>.<ext>`, and records checksum/bytes in `assets`. Viewer,
lending, folder, and challenge links are left as `unsupported`/`failed` with
a reason — they are never bypassed.

```bash
book-scrapper --site amarboi --data-dir data/amarboi download --limit 5
book-scrapper --site amarboi --data-dir data/amarboi download                 # all pending assets
book-scrapper --site amarboi --data-dir data/amarboi download --retry        # include failed + unsupported too
book-scrapper --site amarboi --data-dir data/amarboi download --max-mb 100   # per-file cap (default 512)
```

## Development

```bash
python -m pytest -q
python -m compileall book_scrapper
```

Adapters live in `book_scrapper/sites.py` and `book_scrapper/sources.py`. Add tests with offline fixtures; do not commit scraped data, personal credentials, or downloaded copyrighted material.
