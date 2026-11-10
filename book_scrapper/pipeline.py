import hashlib
import json
import os
import uuid
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


class Unsupported(ValueError):
    pass


META_COLUMNS = (
    ("author", "TEXT"),
    ("publisher", "TEXT"),
    ("published_date", "TEXT"),
    ("language", "TEXT"),
    ("isbn", "TEXT"),
    ("page_count", "INTEGER"),
    ("meta_confidence", "TEXT"),
    ("meta_source", "TEXT"),
    ("meta_raw", "TEXT"),
)


def _ensure_meta_columns(db):
    """Idempotent migration for catalogues created before metadata landed."""
    try:
        existing = {row[1] for row in db.execute("PRAGMA table_info(pages)").fetchall()}
    except Exception:
        return
    with db:
        for name, ddl in META_COLUMNS:
            if name not in existing:
                default = " DEFAULT 'low'" if name == "meta_confidence" else ""
                db.execute(f"ALTER TABLE pages ADD COLUMN {name} {ddl}{default}")


def resolve(client, url):
    host = urlsplit(url).hostname or ""
    if host in {"drive.google.com", "docs.google.com"}:
        from .gdrive import media_url, parse_drive_file_id, read_api_key
        file_id = parse_drive_file_id(url)
        if not file_id:
            raise Unsupported("Google Drive folder or unrecognized link")
        key = read_api_key()
        if not key:
            raise Unsupported(
                "Google Drive files require an API key "
                "(set GOOGLE_DRIVE_API_KEY or ~/.config/book-scrapper/drive_api_key)"
            )
        return media_url(file_id, key)
    if host in {"mediafire.com", "www.mediafire.com"}:
        with client.get(url) as response:
            soup = BeautifulSoup(response.text, "html.parser")
        tag = soup.select_one("a#downloadButton[href]")
        if not tag:
            raise Unsupported("MediaFire download button missing (removed file or access challenge)")
        from .site_utils import clean_url
        target = clean_url(url, tag["href"])
        if not target:
            raise Unsupported("Invalid MediaFire download URL")
        return target
    # Probe other public links as well: many use extensionless download URLs.
    return url


def _store_response(client, url, temp, max_bytes):
    """Stream one URL to temp, returning (digest, size, first-chunk prefix)."""
    import hashlib

    digest, size, prefix = hashlib.sha256(), 0, b""
    with client.get(url, stream=True) as r:
        length = r.headers.get("Content-Length")
        if length and int(length) > max_bytes:
            raise ValueError("File exceeds size limit")
        with temp.open("wb") as out:
            for chunk in r.iter_content(64 * 1024):
                if not chunk:
                    continue
                if not prefix:
                    prefix = chunk
                    file_extension(prefix)
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("File exceeds size limit")
                digest.update(chunk)
                out.write(chunk)
    return digest, size, prefix


def _is_forbidden(exc):
    response = getattr(exc, "response", None)
    return response is not None and getattr(response, "status_code", None) == 403


def _drive_fallback_url(resolved):
    """Direct uc URL when the Drive API media endpoint 403s on a file."""
    from .gdrive import parse_media_file_id, uc_download_url

    file_id = parse_media_file_id(resolved)
    return uc_download_url(file_id) if file_id else None


def file_extension(prefix):
    if prefix.startswith(b"%PDF-"):
        return ".pdf"
    if prefix.startswith(b"PK\x03\x04"):
        return ".zip"  # EPUB is identified after the complete ZIP is downloaded.
    if prefix.startswith(b"Rar!\x1a\x07"):
        return ".rar"
    if prefix.startswith(b"7z\xbc\xaf\x27\x1c"):
        return ".7z"
    if prefix[60:68] in {b"BOOKMOBI", b"TEXtREAd"}:
        return ".mobi"
    if b"<FictionBook" in prefix[:4096]:
        return ".fb2"
    raise Unsupported("Response is not a recognized book/archive (may be a landing page or login)")


class Pipeline:
    def __init__(self, directory, site, client):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.site, self.client = site, client
        self.db = sqlite3.connect(self.directory / "catalog.sqlite3", timeout=30.0)
        self.db.row_factory = sqlite3.Row
        # Tolerate brief contention (e.g. status/export while downloading).
        self.db.execute("PRAGMA journal_mode=WAL;")
        self.db.execute("PRAGMA busy_timeout=30000;")
        self.db.execute("PRAGMA synchronous=NORMAL;")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS pages (
          site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT,
          status TEXT NOT NULL DEFAULT 'pending', error TEXT,
          author TEXT, publisher TEXT, published_date TEXT, language TEXT,
          isbn TEXT, page_count INTEGER,
          meta_confidence TEXT NOT NULL DEFAULT 'low', meta_source TEXT,
          meta_raw TEXT);
        CREATE TABLE IF NOT EXISTS assets (
          url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
          path TEXT, sha256 TEXT, bytes INTEGER, error TEXT);
        CREATE TABLE IF NOT EXISTS links (
          page_url TEXT, asset_url TEXT, label TEXT,
          PRIMARY KEY(page_url, asset_url));
        CREATE TABLE IF NOT EXISTS metadata_links (
          page_url TEXT, url TEXT, label TEXT,
          PRIMARY KEY(page_url, url));
        CREATE TABLE IF NOT EXISTS authority_cache (
          key TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at TEXT);
        ''')
        _ensure_meta_columns(self.db)

    def discover(self):
        if getattr(self.site, "discovery", "sitemap") == "catalogue":
            return self.site.discover_catalogue(self.client, self.db)
        # Re-enumerate sitemaps on each discovery run to pick up new posts.
        pending, seen = [self.site.root + "sitemap.xml"], set()
        count = 0
        while pending:
            url = pending.pop()
            if url in seen:
                continue
            seen.add(url)
            with self.client.get(url) as r:
                is_index, urls = self.site.sitemap(r.content)
            if is_index:
                pending.extend(urls)
            else:
                with self.db:
                    for post in urls:
                        if self.site.is_post(post):
                            count += self.db.execute("INSERT OR IGNORE INTO pages(site,url) VALUES (?,?)", (self.site.name, post)).rowcount
        return count

    def crawl(self, limit=None, retry=False):
        statuses = ("pending", "failed") if retry else ("pending",)
        rows = self.db.execute("SELECT url FROM pages WHERE site=? AND status IN (%s) ORDER BY url DESC LIMIT ?" % ",".join("?" * len(statuses)), (self.site.name, *statuses, limit if limit is not None else -1)).fetchall()
        for row in rows:
            url = row["url"]
            try:
                with self.client.get(url) as r:
                    page = self.site.parse(url, r.text)
                with self.db:
                    for target, label in page.links:
                        if self.site.is_post(target):
                            self.db.execute("INSERT OR IGNORE INTO pages(site,url) VALUES (?,?)", (self.site.name, target))
                        elif not self.site.owns(target):
                            self.db.execute("INSERT OR IGNORE INTO assets(url) VALUES (?)", (target,))
                            self.db.execute("INSERT OR REPLACE INTO links VALUES (?,?,?)", (url, target, label))
                        elif getattr(self.site, "is_asset", lambda value: urlsplit(value).path.lower().endswith((".pdf", ".epub", ".zip", ".rar", ".mobi", ".fb2", ".7z")))(target):
                            self.db.execute("INSERT OR IGNORE INTO assets(url) VALUES (?)", (target,))
                            self.db.execute("INSERT OR REPLACE INTO links VALUES (?,?,?)", (url, target, label))
                    for target, label in getattr(page, "metadata_links", []):
                        self.db.execute("INSERT OR REPLACE INTO metadata_links VALUES (?,?,?)", (url, target, label))
                    meta = getattr(page, "meta", None)
                    if meta is not None:
                        import json as _json

                        self.db.execute(
                            "UPDATE pages SET title=?,author=?,publisher=?,published_date=?,"
                            "language=?,isbn=?,page_count=?,meta_confidence=?,meta_source=?,"
                            "meta_raw=?,status='done',error=NULL WHERE url=?",
                            (page.title, meta.author, meta.publisher, meta.published_date,
                             meta.language, meta.isbn, meta.page_count, meta.confidence,
                             meta.source, _json.dumps(meta.raw, ensure_ascii=False) if meta.raw else None, url),
                        )
                    else:
                        self.db.execute("UPDATE pages SET title=?,status='done',error=NULL WHERE url=?", (page.title, url))
                print(f"Indexed: {page.title}", flush=True)
            except Exception as exc:
                with self.db:
                    self.db.execute("UPDATE pages SET status='failed',error=? WHERE url=?", (str(exc), url))
                print(f"Failed page: {url}: {exc}", flush=True)
        return len(rows)

    def enrich(self, limit=None, retry=False):
        """Resolve scraped metadata against authority APIs (Open Library etc).

        Only fills gaps: ISBN-exact or dual-authority agreement upgrades a
        page to high confidence; disagreements keep the scraped value and
        stay low. All lookups are cached in ``authority_cache``.
        """
        from .resolve import enrich_metadata
        from .site_utils import BookMeta

        if retry:
            rows = self.db.execute(
                "SELECT url,title,author,publisher,published_date,language,isbn,page_count,"
                "meta_confidence,meta_source,meta_raw FROM pages WHERE site=? AND status='done'"
                " ORDER BY url DESC LIMIT ?",
                (self.site.name, limit if limit is not None else -1),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT url,title,author,publisher,published_date,language,isbn,page_count,"
                "meta_confidence,meta_source,meta_raw FROM pages WHERE site=? AND status='done'"
                " AND (author IS NULL AND isbn IS NULL) ORDER BY url DESC LIMIT ?",
                (self.site.name, limit if limit is not None else -1),
            ).fetchall()
        import json as _json

        done = 0
        for row in rows:
            try:
                raw = _json.loads(row["meta_raw"]) if row["meta_raw"] else {}
            except Exception:
                raw = {}
            base = BookMeta(
                author=row["author"], publisher=row["publisher"],
                published_date=row["published_date"], language=row["language"],
                isbn=row["isbn"], page_count=row["page_count"],
                confidence=row["meta_confidence"] or "low",
                source=row["meta_source"], raw=raw if isinstance(raw, dict) else {},
            )
            try:
                enriched, source = enrich_metadata(base, row["title"] or "", self.client, self.db)
            except Exception as exc:
                print(f"Enrich failed: {row['url']}: {exc}", flush=True)
                continue
            with self.db:
                self.db.execute(
                    "UPDATE pages SET author=?,publisher=?,published_date=?,language=?,isbn=?,"
                    "page_count=?,meta_confidence=?,meta_source=?,meta_raw=? WHERE url=?",
                    (enriched.author, enriched.publisher, enriched.published_date,
                     enriched.language, enriched.isbn, enriched.page_count,
                     enriched.confidence, enriched.source,
                     _json.dumps(enriched.raw, ensure_ascii=False) if enriched.raw else None,
                     row["url"]),
                )
            done += 1
            print(f"Enriched: {row['url']} [{enriched.confidence}/{enriched.source}]", flush=True)
        return done

    def download(self, limit=None, retry=False, max_bytes=512 * 1024 * 1024):
        statuses = ("pending", "failed", "unsupported") if retry else ("pending",)
        rows = self.db.execute("SELECT DISTINCT a.* FROM assets a JOIN links l ON l.asset_url=a.url JOIN pages p ON p.url=l.page_url WHERE p.site=? AND a.status IN (%s) LIMIT ?" % ",".join("?" * len(statuses)), (self.site.name, *statuses, limit if limit is not None else -1)).fetchall()
        folder = self.directory / "books"
        folder.mkdir(exist_ok=True)
        for row in rows:
            url = row["url"]
            # Unique temp per process: the old deterministic "<sha256(url)>.part"
            # made concurrent workers truncate/write the same file, producing
            # interleaved/corrupt content and ".part -> .pdf: No such file"
            # rename races. Separate temps make replace() atomic (last wins).
            url_hash = hashlib.sha256(url.encode()).hexdigest()
            temp = folder / f"{url_hash}.{os.getpid()}.{uuid.uuid4().hex[:8]}.part"
            try:
                # Skip work already finished after our initial snapshot
                # (e.g. a peer worker got there first before locking existed).
                current = self.db.execute("SELECT status, path FROM assets WHERE url=?", (url,)).fetchone()
                if current and current["status"] == "done" and current["path"]:
                    if (self.directory / current["path"]).exists():
                        continue
                # Site-specific resolution (e.g. KindleBangla /download/ links that
                # redirect to Drive folders) falls back to the generic resolver.
                site_resolver = getattr(self.site, "resolve_asset", None)
                if site_resolver is not None:
                    resolved = site_resolver(self.client, url)
                else:
                    resolved = resolve(self.client, url)
                try:
                    digest, size, prefix = _store_response(self.client, resolved, temp, max_bytes)
                except Exception as exc:
                    # Some publicly shared files 403 on the API media endpoint
                    # but download fine through the direct uc endpoint.
                    fallback = _drive_fallback_url(resolved) if _is_forbidden(exc) else None
                    if fallback is None:
                        raise
                    temp.unlink(missing_ok=True)
                    print(f"Drive API 403, retrying direct: {url}", flush=True)
                    digest, size, prefix = _store_response(self.client, fallback, temp, max_bytes)
                extension = file_extension(prefix)
                if extension == ".zip":
                    import zipfile
                    with zipfile.ZipFile(temp) as archive:
                        if "mimetype" in archive.namelist():
                            with archive.open("mimetype") as mime:
                                if mime.read(100).strip() == b"application/epub+zip":
                                    extension = ".epub"
                checksum = digest.hexdigest()
                destination = folder / (checksum + extension)
                temp.replace(destination)
                with self.db:
                    self.db.execute("UPDATE assets SET status='done',path=?,sha256=?,bytes=?,error=NULL WHERE url=?", (str(destination.relative_to(self.directory)), checksum, size, url))
                print(f"Downloaded: {url} ({size} bytes)", flush=True)
            except Exception as exc:
                temp.unlink(missing_ok=True)
                status = "unsupported" if isinstance(exc, Unsupported) else "failed"
                with self.db:
                    self.db.execute("UPDATE assets SET status=?,error=? WHERE url=?", (status, str(exc), url))
                print(f"{status}: {url}: {exc}", flush=True)

    def status(self):
        pages = dict(self.db.execute("SELECT status,count(*) FROM pages WHERE site=? GROUP BY status", (self.site.name,)))
        assets = dict(self.db.execute("SELECT a.status,count(DISTINCT a.url) FROM assets a JOIN links l ON a.url=l.asset_url JOIN pages p ON p.url=l.page_url WHERE p.site=? GROUP BY a.status", (self.site.name,)))
        try:
            meta = dict(self.db.execute("SELECT meta_confidence,count(*) FROM pages WHERE site=? AND status='done' GROUP BY meta_confidence", (self.site.name,)))
        except Exception:
            meta = {}
        result = {"site": self.site.name, "pages": pages, "assets": assets}
        if meta:
            result["meta"] = meta
        return result

    def export(self, path):
        with Path(path).open("w", encoding="utf-8") as out:
            for page in self.db.execute("SELECT * FROM pages WHERE site=? ORDER BY url", (self.site.name,)):
                record = dict(page)
                record["links"] = [dict(r) for r in self.db.execute("SELECT a.*,l.label FROM links l JOIN assets a ON a.url=l.asset_url WHERE l.page_url=?", (page["url"],))]
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
