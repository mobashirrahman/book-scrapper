"""Bounded adapters for the additional public Bengali book sources.

The adapters deliberately keep catalogue/access records separate from direct
book files.  A link is an asset only when its URL identifies a public file (or
the adapter has a source-specific, non-authenticated file rule); viewer,
lending, metadata and download-handler links remain metadata.
"""

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .site_utils import Page, clean_url


BOOK_EXTENSIONS = (".pdf", ".epub", ".zip", ".rar", ".mobi", ".fb2", ".7z")
_LINK_TEXT = re.compile(
    r"download|read|online|viewer|borrow|lend|catalog|catalogue|record|source|access|"
    r"ebook|pdf|epub|বই|ডাউনলোড|পড়|পড়|অনলাইন|ধার",
    re.I,
)


def _locs(xml):
    root = ET.fromstring(xml)
    kind = root.tag.rsplit("}", 1)[-1]
    if kind not in {"sitemapindex", "urlset"}:
        raise ValueError("Unexpected sitemap document")
    return kind, [e.text.strip() for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "loc" and e.text]


def _title(soup, url):
    element = soup.select_one("h1.entry-title, h1.page-header-title, h1, title")
    return element.get_text(" ", strip=True) if element else url


def _content(soup):
    return soup.select_one("article, .entry-content, .post-content, main, #content") or soup


class SitemapAdapter:
    """Common sitemap/detail behavior used by public catalogue sites."""

    capability = "direct-assets"
    status = "enabled"
    _SITEMAP_INDEX = None
    sitemap_marker = None
    post_pattern = None
    excluded_prefixes = ()

    def owns(self, url):
        return (urlsplit(url).hostname or "").lower() in self.hosts

    def is_post(self, url):
        if not self.owns(url):
            return False
        path = urlsplit(url).path
        if any(path.startswith(prefix) for prefix in self.excluded_prefixes):
            return False
        return bool(self.post_pattern and self.post_pattern.fullmatch(path))

    def sitemap(self, xml):
        kind, urls = _locs(xml)
        if kind == "sitemapindex":
            if self.sitemap_marker:
                urls = [url for url in urls if self.sitemap_marker in url]
            return True, [url for url in urls if self.owns(url)]
        return False, [url for url in urls if self.is_post(url)]

    def is_asset(self, url):
        return self.owns(url) and urlsplit(url).path.lower().endswith(BOOK_EXTENSIONS)

    def _metadata_link(self, target, label):
        text = f"{label} {target}"
        host = urlsplit(target).hostname or ""
        return bool(_LINK_TEXT.search(text) or host in {"archive.org", "www.archive.org"})

    def parse(self, url, html):
        soup = BeautifulSoup(html, "html.parser")
        title = _title(soup, url)
        assets, metadata, seen = [], [], set()
        for anchor in _content(soup).select("a[href]"):
            target = clean_url(url, anchor.get("href", ""))
            if not target or target == url or target in seen:
                continue
            seen.add(target)
            label = anchor.get_text(" ", strip=True)
            if self.is_asset(target):
                assets.append((target, label))
            elif self._metadata_link(target, label) or self.capability == "metadata-only":
                metadata.append((target, label))
        return Page(title, assets, metadata)


class BDeBooks(SitemapAdapter):
    name = "bdebooks"
    root = "https://bdebooks.com/bn/"
    hosts = {"bdebooks.com", "www.bdebooks.com"}
    capability = "direct-assets-and-catalogue"
    post_pattern = re.compile(r"/[^/]+/books/[^/]+/")
    excluded_prefixes = ("/wp-admin", "/dl/", "/esdl/", "/frdl/", "/ptdl/", "/dedl/", "/bndl/", "/audiodl/")

    def is_post(self, url):
        if not self.owns(url):
            return False
        return bool(re.fullmatch(r"/(?:bn|en|es|fr|pt|de)/books/[^/]+/", urlsplit(url).path))

    def sitemap(self, xml):
        # BDeBooks currently publishes catalogue pages without a stable XML
        # sitemap; accepting a future standard sitemap keeps the adapter useful.
        return super().sitemap(xml)

    def is_asset(self, url):
        path = urlsplit(url).path.lower()
        # These handler paths are explicitly disallowed by robots.txt.  Keep
        # them as metadata instead of attempting to reconstruct or bypass them.
        if any(path.startswith(prefix) for prefix in self.excluded_prefixes):
            return False
        return path.endswith(BOOK_EXTENSIONS)

    def discover_catalogue(self, client, db, limit=None):
        seeds = (self.root, self.root + "ebook-collection/")
        return _discover_catalogue(self, client, db, seeds, limit)


class Granthagara(SitemapAdapter):
    name = "granthagara"
    root = "https://granthagara.com/"
    hosts = {"granthagara.com", "www.granthagara.com"}
    _SITEMAP_INDEX = root + "sitemap_index.xml"
    sitemap_marker = "boi-sitemap"
    post_pattern = re.compile(r"/boi/[^/]+/")

    def is_asset(self, url):
        return urlsplit(url).path.lower().endswith(BOOK_EXTENSIONS)


class BengaliOnline(SitemapAdapter):
    name = "bengalionline"
    root = "https://isid.ac.in/~deepayan/bengalionline.net/"
    hosts = {"isid.ac.in", "www.isid.ac.in"}
    capability = "public-domain-static-files"
    post_pattern = re.compile(r"/.+\.html?", re.I)

    def is_post(self, url):
        if not self.owns(url) or self.is_asset(url):
            return False
        path = urlsplit(url).path
        return path.startswith("/~deepayan/bengalionline.net/") and bool(re.search(r"\.html?$", path, re.I))

    def is_asset(self, url):
        path = urlsplit(url).path.lower()
        return path.endswith((".pdf", ".epub", ".zip")) and "/~deepayan/bengalionline.net/" in path

    def discover_catalogue(self, client, db, limit=None):
        return _discover_catalogue(self, client, db, (self.root,), limit)


class FID4SA(SitemapAdapter):
    name = "fid4sa"
    root = "https://fid4sa-repository.ub.uni-heidelberg.de/"
    hosts = {"fid4sa-repository.ub.uni-heidelberg.de"}
    _SITEMAP_INDEX = root + "sitemap.xml"
    post_pattern = re.compile(r"/(?:id/(?:eprint/)?\d+|eprint/\d+)/?", re.I)

    def is_post(self, url):
        return self.owns(url) and bool(self.post_pattern.fullmatch(urlsplit(url).path))

    def is_asset(self, url):
        path = urlsplit(url).path.lower()
        return self.owns(url) and (path.endswith(BOOK_EXTENSIONS) or bool(re.fullmatch(r"/id/eprint/\d+/\d+/[^/]+", path)))


class DPLELibrary(SitemapAdapter):
    name = "dpl_elibrary"
    root = "https://elibrary.dpl.gov.bd/"
    hosts = {"elibrary.dpl.gov.bd", "www.elibrary.dpl.gov.bd"}
    capability = "metadata-only"
    status = "metadata-only-public-catalogue"
    post_pattern = re.compile(r"/(?:book|books|item|items|details|catalogue)/[^/?#]+/?", re.I)

    def is_asset(self, url):
        return False

    def discover_catalogue(self, client, db, limit=None):
        seeds = (self.root, self.root + "books/", self.root + "catalogue/")
        return _discover_catalogue(self, client, db, seeds, limit)


class Boiprakash(SitemapAdapter):
    name = "boiprakash"
    root = "https://boiprakash.com/"
    hosts = {"boiprakash.com", "www.boiprakash.com"}
    capability = "metadata-only"
    status = "metadata-only-catalogue"
    post_pattern = re.compile(r"/product-details\.php", re.I)

    def is_post(self, url):
        return self.owns(url) and urlsplit(url).path.lower().endswith("/product-details.php") and "id=" in urlsplit(url).query

    def is_asset(self, url):
        return False

    def discover_catalogue(self, client, db, limit=None):
        count = processed = 0
        for page_number in range(1, 101):
            page_url = self.root + "shop.php" + (f"?page={page_number}" if page_number > 1 else "")
            try:
                with client.get(page_url) as response:
                    soup = BeautifulSoup(response.text, "html.parser")
            except Exception:
                break
            found = 0
            for anchor in soup.select("a[href]"):
                target = clean_url(page_url, anchor.get("href", ""))
                if not target or not self.is_post(target):
                    continue
                found += 1
                processed += 1
                with db:
                    count += db.execute("INSERT OR IGNORE INTO pages(site,url) VALUES (?,?)", (self.name, target)).rowcount
                if limit is not None and processed >= limit:
                    return count
            if not found:
                break
        return count


class PdfPoro(SitemapAdapter):
    name = "pdfporo"
    root = "https://pdfporo.com/"
    hosts = {"pdfporo.com", "www.pdfporo.com"}
    _SITEMAP_INDEX = root + "sitemap_index.xml"
    sitemap_marker = "post-sitemap"
    post_pattern = re.compile(r"/[^/?#]+/?")

    def is_post(self, url):
        if not self.owns(url):
            return False
        path = urlsplit(url).path
        if path in {"/", "/page/"} or path.startswith(("/getbook/", "/page/", "/author/", "/search/")):
            return False
        return bool(re.fullmatch(r"/[a-z0-9][a-z0-9\-]*/?", path, re.I))

    def is_asset(self, url):
        path = urlsplit(url).path.lower()
        return not path.startswith("/getbook/") and path.endswith(BOOK_EXTENSIONS)


class Boighor(SitemapAdapter):
    name = "boighor"
    root = "https://boighorlibrary.com/"
    hosts = {"boighorlibrary.com", "www.boighorlibrary.com"}
    capability = "metadata-only"
    status = "metadata-only-lending"
    post_pattern = re.compile(r"/books/[^/?#]+/?")

    def is_post(self, url):
        return self.owns(url) and bool(re.fullmatch(r"/books/[^/?#]+/?", urlsplit(url).path))

    def is_asset(self, url):
        return False

    def discover_catalogue(self, client, db, limit=None):
        return _discover_catalogue(self, client, db, (self.root, self.root + "books/"), limit)


class ProjectGutenberg(SitemapAdapter):
    name = "gutenberg_bengali"
    root = "https://www.gutenberg.org/"
    hosts = {"gutenberg.org", "www.gutenberg.org", "central.gutenberg.org"}
    capability = "official-catalogue-formats"
    post_pattern = re.compile(r"/ebooks/\d+/?")

    def is_post(self, url):
        return (urlsplit(url).hostname or "").lower() in {"gutenberg.org", "www.gutenberg.org"} and bool(self.post_pattern.fullmatch(urlsplit(url).path))

    def is_asset(self, url):
        host = (urlsplit(url).hostname or "").lower()
        path = urlsplit(url).path.lower()
        return host in {"gutenberg.org", "www.gutenberg.org", "central.gutenberg.org"} and path.endswith((".pdf", ".epub", ".zip", ".mobi"))

    def discover_feed(self, client, db, limit=None):
        count = processed = 0
        start = 1
        while start <= 10000:
            feed_url = f"https://www.gutenberg.org/ebooks/search.opds/?query=bengali&start_index={start}"
            try:
                with client.get(feed_url) as response:
                    soup = BeautifulSoup(response.text, "xml")
            except Exception:
                break
            urls = []
            for link in soup.select("entry link[href]"):
                target = clean_url(feed_url, link.get("href", ""))
                if target and self.is_post(target) and target not in urls:
                    urls.append(target)
            if not urls:
                break
            for target in urls:
                processed += 1
                with db:
                    count += db.execute("INSERT OR IGNORE INTO pages(site,url) VALUES (?,?)", (self.name, target)).rowcount
                if limit is not None and processed >= limit:
                    return count
            if len(urls) < 25:
                break
            start += 25
        return count


class NDLI(SitemapAdapter):
    name = "ndli"
    root = "https://ndl.gov.in/"
    hosts = {"ndl.gov.in", "www.ndl.gov.in"}
    capability = "metadata-only"
    status = "metadata-only-catalogue"
    post_pattern = re.compile(r"/(?:item|items|content|record|records|search)/[^/?#]+/?", re.I)

    def is_asset(self, url):
        return False

    def discover_catalogue(self, client, db, limit=None):
        seeds = (self.root, self.root + "content/", self.root + "search/")
        return _discover_catalogue(self, client, db, seeds, limit)


class KindleBangla(SitemapAdapter):
    """Catalogue adapter for KindleBangla with Drive-folder downloads.

    Book pages live at /book/<hex-id> (as listed in sitemap.xml) and also
    resolve by Bengali slug (/book/<slug>). The per-book /download/<slug>
    endpoint 302-redirects to a Google Drive *folder*; ``resolve_asset``
    follows that redirect and resolves the folder to its single book file
    through the official Drive API (see ``gdrive`` — needs
    ``GOOGLE_DRIVE_API_KEY`` for public folders). Folders that are empty,
    multi-file, private, or keyless stay ``unsupported`` instead of guessing.
    """

    name = "kindlebangla"
    root = "https://www.kindlebangla.com/"
    hosts = {"kindlebangla.com", "www.kindlebangla.com"}
    capability = "metadata-only"
    status = "metadata-only-catalogue"
    post_pattern = re.compile(r"/book/[^/?#]+/?")

    def is_asset(self, url):
        return self.owns(url) and urlsplit(url).path.startswith("/download/")

    def resolve_asset(self, client, url, drive_service_factory=None):
        from .gdrive import (
            media_url,
            parse_drive_file_id,
            parse_drive_folder_id,
            read_api_key,
            resolve_folder_file,
        )
        from .pipeline import Unsupported

        with client.get(url) as response:
            final = response.url
        folder_id = parse_drive_folder_id(final)
        if folder_id is not None:
            return resolve_folder_file(folder_id, service_factory=drive_service_factory)
        file_id = parse_drive_file_id(final)
        if file_id is not None:
            key = read_api_key()
            if not key:
                raise Unsupported(
                    "KindleBangla landed on a Drive file but no API key is set "
                    "(set GOOGLE_DRIVE_API_KEY or ~/.config/book-scrapper/drive_api_key)"
                )
            return media_url(file_id, key)
        raise Unsupported(f"KindleBangla download did not land on a Drive folder or file: {final}")


class WordPressBookSite(SitemapAdapter):
    """WordPress book catalogue whose downloads live on third-party file hosts.

    Download links point to Google Drive, MediaFire, MEGA, Box etc. instead of
    files served by the site itself, so those hosts are queued as assets while
    viewer/lending metadata links stay out of the download queue.
    """

    _download_hosts = {
        "drive.google.com", "docs.google.com", "drive.usercontent.google.com",
        "mediafire.com", "www.mediafire.com",
        "mega.nz", "mega.co.nz", "www.mega.nz", "www.mega.co.nz",
        "app.box.com", "box.com", "www.box.com",
        "dropbox.com", "www.dropbox.com", "dl.dropboxusercontent.com",
        "drive.googleusercontent.com",
    }

    def is_asset(self, url):
        host = (urlsplit(url).hostname or "").lower()
        if host in self._download_hosts:
            return True
        return self.owns(url) and urlsplit(url).path.lower().endswith(BOOK_EXTENSIONS)


class BanglaBook(WordPressBookSite):
    name = "banglabook"
    root = "https://www.banglabook.org/"
    hosts = {"banglabook.org", "www.banglabook.org"}
    _SITEMAP_INDEX = root + "wp-sitemap.xml"
    sitemap_marker = "post-sitemap"
    post_pattern = re.compile(r"/[a-z0-9][a-z0-9\-]+/?$", re.I)

    def is_post(self, url):
        if not self.owns(url):
            return False
        path = urlsplit(url).path
        return bool(re.fullmatch(r"/[a-z0-9][a-z0-9\-]+/?", path, re.I))


class AllBanglaBoi(SitemapAdapter):
    name = "allbanglaboi"
    root = "https://allbanglaboi.com/"
    hosts = {"allbanglaboi.com", "www.allbanglaboi.com"}
    capability = "metadata-only"
    status = "metadata-only-catalogue"
    discovery = "catalogue"
    post_pattern = re.compile(r"/(?:\d+/|[a-z0-9][a-z0-9\-]+/?$)", re.I)

    def is_post(self, url):
        if not self.owns(url):
            return False
        path = urlsplit(url).path
        if path in {"/", ""}:
            return False
        return bool(re.fullmatch(r"/(?:\d+/|[a-z0-9][a-z0-9\-]+/?$)", path, re.I))

    def discover_catalogue(self, client, db, limit=None):
        count = processed = page = 0
        while True:
            page += 1
            url = self.root + f"wp-json/wp/v2/posts?per_page=100&page={page}"
            try:
                with client.get(url) as response:
                    posts = json.loads(response.text)
            except Exception:
                break
            if not isinstance(posts, list) or not posts:
                break
            for post in posts:
                post_id = post.get("id")
                if not post_id:
                    continue
                link = post.get("link", "")
                processed += 1
                with db:
                    count += db.execute(
                        "INSERT OR IGNORE INTO pages(site,url) VALUES (?,?)",
                        (self.name, link if link else self.root + str(post_id)),
                    ).rowcount
                if limit is not None and processed >= limit:
                    return count
        return count


class BanglaBooksIn(WordPressBookSite):
    name = "banglabooks_in"
    root = "https://www.banglabooks.in/"
    hosts = {"banglabooks.in", "www.banglabooks.in"}
    _SITEMAP_INDEX = root + "wp-sitemap.xml"
    sitemap_marker = "post-sitemap"
    post_pattern = re.compile(r"/[^/?#]+/[^/?#]+/?$")

    def is_post(self, url):
        if not self.owns(url):
            return False
        parts = urlsplit(url)
        # Ignore share/utm variants that repeat the same post.
        if parts.query:
            return False
        path = parts.path
        return bool(re.fullmatch(r"/[a-z0-9][a-z0-9\-]+/[a-z0-9][a-z0-9\-]+/?", path, re.I))


class LiberationWarBangladesh(SitemapAdapter):
    """Catalogue adapter for the Muktijuddho e-Archive (liberationwarbangladesh.org).

    The archive runs WordPress with ``?p=<id>`` post URLs. Full-text posts and
    newspaper series are published publicly. Books are presented through the
    FlipHTML5 viewer on the ``doc.liberationwarbangladesh.net/books/<code>``
    vanity domain; the underlying page images live on
    ``online.fliphtml5.com/lzrut/<code>/files/``. The viewer and other lending
    hosts stay metadata; only Google Drive file links (and direct book files)
    are queued as download candidates, so restricted Drive items are resolved
    through the generic resolver and reported instead of being bypassed.
    """

    name = "liberationwarbangladesh"
    root = "https://liberationwarbangladesh.org/"
    hosts = {
        "liberationwarbangladesh.org", "www.liberationwarbangladesh.org",
        "liberationwarbangladesh.com", "www.liberationwarbangladesh.com",
        "liberationwarbangladesh.net", "www.liberationwarbangladesh.net",
        "doc.liberationwarbangladesh.net",
    }
    capability = "rest-catalogue-and-public-file-candidates"
    status = "enabled"
    discovery = "catalogue"
    _drive_hosts = {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}

    def is_post(self, url):
        if not self.owns(url):
            return False
        parts = urlsplit(url)
        return bool(re.fullmatch(r"p=\d+", parts.query))

    def is_asset(self, url):
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host in self._drive_hosts:
            return "/file/d/" in parts.path or "export=download" in parts.query or "id=" in parts.query
        return parts.path.lower().endswith(BOOK_EXTENSIONS)

    def discover_catalogue(self, client, db, limit=None):
        count = processed = page = 0
        while True:
            page += 1
            url = self.root + f"index.php?rest_route=/wp/v2/posts&per_page=100&page={page}"
            try:
                with client.get(url) as response:
                    posts = json.loads(response.text)
            except Exception:
                break
            if not isinstance(posts, list) or not posts:
                break
            for post in posts:
                post_id = post.get("id")
                if not post_id:
                    continue
                processed += 1
                with db:
                    count += db.execute(
                        "INSERT OR IGNORE INTO pages(site,url) VALUES (?,?)",
                        (self.name, f"{self.root}?p={post_id}"),
                    ).rowcount
                if limit is not None and processed >= limit:
                    return count
        return count

    def parse(self, url, html):
        soup = BeautifulSoup(html, "html.parser")
        title = _title(soup, url)
        assets, metadata, seen = [], [], set()
        for anchor in _content(soup).select("a[href]"):
            target = clean_url(url, anchor.get("href", ""))
            if not target or target == url or target in seen:
                continue
            parts = urlsplit(target)
            if not parts.query and parts.path in ("/", ""):
                continue
            seen.add(target)
            label = anchor.get_text(" ", strip=True)
            if self.is_asset(target):
                assets.append((target, label))
            else:
                metadata.append((target, label))
        return Page(title, assets, metadata)


def _discover_catalogue(site, client, db, seeds, limit=None):
    """Collect only first-level detail links from a bounded seed set."""
    count = processed = 0
    seen = set()
    for seed in seeds:
        if seed in seen:
            continue
        seen.add(seed)
        try:
            with client.get(seed) as response:
                soup = BeautifulSoup(response.text, "html.parser")
        except Exception:
            continue
        for anchor in soup.select("a[href]"):
            target = clean_url(seed, anchor.get("href", ""))
            if not target or target in seen or not site.is_post(target):
                continue
            seen.add(target)
            processed += 1
            with db:
                count += db.execute("INSERT OR IGNORE INTO pages(site,url) VALUES (?,?)", (site.name, target)).rowcount
            if limit is not None and processed >= limit:
                return count
    return count


class BanglaBookshelf(SitemapAdapter):
    """Static catalogue adapter for BanglaBookshelf (no sitemap).

    Series/listing pages (``*-books.php``) and detail pages are plain ``.php``
    documents that link directly to same-host PDFs, so discovery starts from a
    bounded seed set and the crawl follows ``.php`` links while ``.pdf`` links
    are queued as assets.
    """

    name = "banglabookshelf"
    root = "https://www.banglabookshelf.com/"
    hosts = {"banglabookshelf.com", "www.banglabookshelf.com"}
    capability = "direct-assets"
    status = "enabled"
    discovery = "catalogue"
    post_pattern = re.compile(r"/.+\.php", re.I)
    excluded_prefixes = ("/about-us.php", "/contact-us.php", "/privacy-policy.php", "/index.php",
                           "/terms-of-use.php", "/terms.php", "/disclaimer.php", "/dmca.php")

    def is_post(self, url):
        if not self.owns(url):
            return False
        path = urlsplit(url).path
        if path in {"/", ""} or path.lower() in self.excluded_prefixes:
            return False
        return bool(re.fullmatch(r"/.+\.php", path, re.I))

    def parse(self, url, html):
        # Listing pages link to other .php pages with plain labels (series and
        # author names), so post links must stay in the queue channel instead
        # of being dropped as non-metadata navigation.
        soup = BeautifulSoup(html, "html.parser")
        title = _title(soup, url)
        assets, metadata, seen = [], [], set()
        for anchor in _content(soup).select("a[href]"):
            target = clean_url(url, anchor.get("href", ""))
            if not target or target == url or target in seen:
                continue
            seen.add(target)
            label = anchor.get_text(" ", strip=True)
            if self.is_asset(target) or self.is_post(target):
                assets.append((target, label))
            elif self._metadata_link(target, label):
                metadata.append((target, label))
        return Page(title, assets, metadata)

    def discover_catalogue(self, client, db, limit=None):
        seeds = (self.root, self.root + "index.php", self.root + "Story-Book.php")
        return _discover_catalogue(self, client, db, seeds, limit)


class WorldMets(WordPressBookSite):
    name = "worldmets"
    root = "https://www.worldmets.com/"
    hosts = {"worldmets.com", "www.worldmets.com"}
    _SITEMAP_INDEX = root + "sitemap.xml"
    sitemap_marker = "post-sitemap"
    post_pattern = re.compile(r"/[^/?#]+/[^/?#]+/?$")

    def is_post(self, url):
        if not self.owns(url):
            return False
        parts = urlsplit(url)
        # Ignore share/utm variants that repeat the same post.
        if parts.query:
            return False
        segments = [seg for seg in parts.path.split("/") if seg]
        if not 2 <= len(segments) <= 4 or segments[0] in {"wp-content", "category", "tag", "author"}:
            return False
        if any(seg.lower() in {"feed", "page", "comments"} for seg in segments):
            return False
        return all(re.fullmatch(r"[a-z0-9][a-z0-9\-.]*", seg, re.I) for seg in segments)


def _archive_identifier(url):
    """Return the archive.org item identifier for a details/stream/download URL."""
    from urllib.parse import unquote

    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in {"archive.org", "www.archive.org"}:
        return None
    segments = [seg for seg in parts.path.split("/") if seg]
    if len(segments) >= 2 and segments[0] in {"details", "stream", "download"}:
        return unquote(segments[1]) or None
    return None


def _archive_ids_in_catalog(db_path):
    """Collect archive.org identifiers already recorded in another catalogue DB."""
    import sqlite3

    known = set()
    try:
        other = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    except Exception:
        return known
    try:
        tables = {row[0] for row in other.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "pages" in tables:
            for row in other.execute("SELECT url FROM pages"):
                identifier = _archive_identifier(row[0] or "")
                if identifier:
                    known.add(identifier)
        for table, column in (("assets", "url"), ("links", "asset_url"), ("metadata_links", "url")):
            if table in tables:
                for row in other.execute(f"SELECT {column} FROM {table}"):
                    identifier = _archive_identifier(row[0] or "")
                    if identifier:
                        known.add(identifier)
    except Exception:
        pass
    finally:
        try:
            other.close()
        except Exception:
            pass
    return known


class ArchiveBengali(SitemapAdapter):
    """API catalogue adapter for public Bengali texts on archive.org.

    Discovery pages through ``advancedsearch.php`` (``language:ben``,
    ``mediatype:texts``) and resolves each item's book files through the
    public metadata API, recording one done page per item
    (``https://archive.org/details/<identifier>/``) with its
    ``/download/<identifier>/<file>`` assets.  (Item detail HTML is
    JS-rendered, so file discovery uses the metadata API instead of parsing.)
    Derivative bundles (``*_jp2.zip``) stay out of the download queue.

    Identifiers already present in the sibling Granthagara catalogue
    (``<data-dir>/../granthagara/catalog.sqlite3``) are skipped at discovery
    so the two runs never download the same books twice.
    """

    name = "archive_bengali"
    root = "https://archive.org/"
    hosts = {"archive.org", "www.archive.org"}
    capability = "public-domain-api-files"
    status = "enabled"
    discovery = "catalogue"
    dedupe_sites = ("granthagara",)
    post_pattern = re.compile(r"/details/[^/?#]+/?")
    _search_template = (
        "https://archive.org/advancedsearch.php?q=language%3Aben+AND+mediatype%3Atexts"
        "&fl%5B%5D=identifier&rows=200&page={page}&output=json"
    )

    def _page_url(self, identifier):
        return f"{self.root}details/{identifier}/"

    def is_post(self, url):
        if not self.owns(url):
            return False
        return bool(re.fullmatch(r"/details/[^/?#]+/?", urlsplit(url).path))

    def is_asset(self, url):
        if not self.owns(url):
            return False
        path = urlsplit(url).path.lower()
        if not path.startswith("/download/"):
            return False
        if path.endswith("_jp2.zip"):
            return False
        return path.endswith(BOOK_EXTENSIONS)

    def _known_identifiers(self, db):
        from pathlib import Path

        known = set()
        try:
            rows = db.execute("SELECT url FROM pages WHERE site=?", (self.name,)).fetchall()
        except Exception:
            rows = []
        for row in rows:
            identifier = _archive_identifier(row[0] or "")
            if identifier:
                known.add(identifier)
        try:
            main = db.execute("PRAGMA database_list").fetchall()
            db_file = next((Path(entry[2]) for entry in main if entry[1] == "main" and entry[2]), None)
        except Exception:
            db_file = None
        if db_file:
            for site in self.dedupe_sites:
                known |= _archive_ids_in_catalog(db_file.parent.parent / site / "catalog.sqlite3")
        return known

    def _book_files(self, identifier, payload):
        from urllib.parse import quote

        files = []
        if not isinstance(payload, dict):
            return files
        for entry in payload.get("files", []) or []:
            name = (entry.get("name", "") or "").strip()
            if not name:
                continue
            url = f"{self.root}download/{identifier}/{quote(name)}"
            if self.is_asset(url):
                files.append((url, name))
        return files

    def discover_catalogue(self, client, db, limit=None):
        known = self._known_identifiers(db)
        count = processed = page = 0
        while True:
            page += 1
            try:
                with client.get(self._search_template.format(page=page)) as response:
                    payload = json.loads(response.text)
            except Exception:
                break
            docs = (payload.get("response", {}) if isinstance(payload, dict) else {}).get("docs", [])
            if not docs:
                break
            for doc in docs:
                identifier = (doc.get("identifier", "") or "").strip()
                if not identifier or identifier in known:
                    continue
                try:
                    with client.get(f"{self.root}metadata/{identifier}") as response:
                        meta = json.loads(response.text)
                except Exception:
                    continue
                title = ((meta.get("metadata", {}) or {}).get("title", "") or "").strip() or identifier
                if isinstance(title, list):
                    title = (title[0] if title else identifier).strip() or identifier
                files = self._book_files(identifier, meta)
                if not files:
                    continue
                page_url = self._page_url(identifier)
                known.add(identifier)
                processed += 1
                with db:
                    count += db.execute(
                        "INSERT OR IGNORE INTO pages(site,url,title,status) VALUES (?,?,?,?)",
                        (self.name, page_url, title, "done"),
                    ).rowcount
                    for target, label in files:
                        db.execute("INSERT OR IGNORE INTO assets(url) VALUES (?)", (target,))
                        db.execute("INSERT OR REPLACE INTO links VALUES (?,?,?)", (page_url, target, label))
                if limit is not None and processed >= limit:
                    return count
        return count


class BnWikisource(SitemapAdapter):
    """API catalogue adapter for Bengali Wikisource (public-domain works).

    Discovery pages main-namespace works through the MediaWiki API
    (``list=allpages``) and records one done page per work with its
    WS-Export ebook asset.  EPUB is preferred: it renders in under a second
    while first-time PDF renders can take minutes.  Export URLs are generated
    per work, so they never overlap the archive.org/granthagara holdings.
    """

    name = "bn_wikisource"
    root = "https://bn.wikisource.org/"
    hosts = {"bn.wikisource.org", "ws-export.wmcloud.org"}
    capability = "official-api-ebooks"
    status = "enabled"
    discovery = "catalogue"
    post_pattern = re.compile(r"/wiki/[^:?#]+$")
    _api_template = (
        "https://bn.wikisource.org/w/api.php?action=query&list=allpages"
        "&apnamespace=0&aplimit=500&format=json{cont}"
    )
    _export_template = "https://ws-export.wmcloud.org/?format=epub&lang=bn&page={title}"
    # Sort-order debris at the top of the main namespace, not books.
    _skip_titles = frozenset({"Main Page", "Main page", "Lh"})

    @classmethod
    def _is_book_title(cls, title):
        if not title or len(title) < 3 or title in cls._skip_titles:
            return False
        return not title.startswith("H:")

    def _page_url(self, title):
        from urllib.parse import quote

        return f"{self.root}wiki/{quote(title.replace(' ', '_'), safe='/:')}"

    def _export_url(self, title):
        from urllib.parse import quote

        return self._export_template.format(title=quote(title, safe=""))

    def is_post(self, url):
        if not self.owns(url):
            return False
        parts = urlsplit(url)
        if parts.hostname == "ws-export.wmcloud.org":
            return False
        return bool(re.fullmatch(r"/wiki/[^:?#]+", parts.path))

    def is_asset(self, url):
        parts = urlsplit(url)
        if (parts.hostname or "").lower() != "ws-export.wmcloud.org":
            return False
        query = parts.query.lower()
        return "lang=bn" in query and any(f"format={fmt}" in query for fmt in ("epub", "pdf", "mobi"))

    def discover_catalogue(self, client, db, limit=None):
        count = processed = 0
        cont = ""
        while True:
            try:
                with client.get(self._api_template.format(cont=cont)) as response:
                    payload = json.loads(response.text)
            except Exception:
                break
            query = payload.get("query", {}) if isinstance(payload, dict) else {}
            members = query.get("allpages", []) or []
            if not members:
                break
            for member in members:
                title = (member.get("title", "") or "").strip()
                if not self._is_book_title(title):
                    continue
                page_url = self._page_url(title)
                target = self._export_url(title)
                processed += 1
                with db:
                    count += db.execute(
                        "INSERT OR IGNORE INTO pages(site,url,title,status) VALUES (?,?,?,?)",
                        (self.name, page_url, title, "done"),
                    ).rowcount
                    db.execute("INSERT OR IGNORE INTO assets(url) VALUES (?)", (target,))
                    db.execute("INSERT OR REPLACE INTO links VALUES (?,?,?)", (page_url, target, title))
                if limit is not None and processed >= limit:
                    return count
            block = payload.get("continue") or {}
            cont = ""
            if isinstance(block, dict) and block.get("apcontinue"):
                from urllib.parse import quote

                cont = f"&apcontinue={quote(block['apcontinue'], safe='')}"
            if not cont:
                break
        return count


class Nctb(WordPressBookSite):
    """Catalogue adapter for NCTB textbooks (official Bangladesh textbooks).

    The library is browsed as year → level → class pages
    (``/textbooks/<year>/<level>/<class>/``); class pages embed Google Drive
    file links for each textbook, which are queued as assets through the
    shared third-party-host rule and resolved with the Drive API key.
    """

    name = "nctb"
    root = "https://nctb.cloud/"
    hosts = {"nctb.cloud", "www.nctb.cloud"}
    capability = "official-textbooks-drive-files"
    status = "enabled"
    discovery = "catalogue"
    post_pattern = re.compile(r"/textbooks/[^/?#]+/?$")

    def is_post(self, url):
        if not self.owns(url):
            return False
        parts = urlsplit(url)
        if parts.query:
            return False
        segments = [seg for seg in parts.path.split("/") if seg]
        if not segments or segments[0] != "textbooks":
            return False
        if len(segments) > 4 or any(seg.lower() in {"feed", "page"} for seg in segments):
            return False
        return all(re.fullmatch(r"[a-z0-9][a-z0-9\-]*", seg, re.I) for seg in segments[1:])

    def parse(self, url, html):
        # Shelf pages link to sub-shelves with plain labels (years, levels),
        # so post links must stay in the queue channel instead of being
        # dropped as non-metadata navigation.
        soup = BeautifulSoup(html, "html.parser")
        title = _title(soup, url)
        assets, metadata, seen = [], [], set()
        for anchor in _content(soup).select("a[href]"):
            target = clean_url(url, anchor.get("href", ""))
            if not target or target == url or target in seen:
                continue
            seen.add(target)
            label = anchor.get_text(" ", strip=True)
            if self.is_asset(target) or self.is_post(target):
                assets.append((target, label))
            elif self._metadata_link(target, label):
                metadata.append((target, label))
        return Page(title, assets, metadata)

    def discover_catalogue(self, client, db, limit=None):
        return _discover_catalogue(self, client, db, (self.root, self.root + "textbooks/"), limit)


# Friendly aliases for callers that use the source's displayed name.
Fid4SA = FID4SA
DplElibrary = DPLELibrary
Gutenberg = ProjectGutenberg
