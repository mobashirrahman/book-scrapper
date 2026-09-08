"""Bounded adapters for the additional public Bengali book sources.

The adapters deliberately keep catalogue/access records separate from direct
book files.  A link is an asset only when its URL identifies a public file (or
the adapter has a source-specific, non-authenticated file rule); viewer,
lending, metadata and download-handler links remain metadata.
"""

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

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


# Friendly aliases for callers that use the source's displayed name.
Fid4SA = FID4SA
DplElibrary = DPLELibrary
Gutenberg = ProjectGutenberg
