"""Offline tests for metadata extraction, storage and authority enrichment."""

import json
import sqlite3

from book_scrapper.pipeline import Pipeline
from book_scrapper.resolve import (
    GOOGLE_ISBN_URL,
    OL_ISBN_URL,
    enrich_metadata,
)
from book_scrapper.sites import SITES
from book_scrapper.site_utils import (
    BookMeta,
    extract_meta,
    normalize_date,
    normalize_isbn,
    normalize_language,
)
from book_scrapper.sources import ArchiveBengali


VALID_ISBN13 = "9780136042594"  # Artificial Intelligence (Russell & Norvig)


class Response:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url not in self.mapping:
            raise KeyError(url)
        return Response(self.mapping[url])


def test_isbn_validation_accepts_valid_rejects_bad():
    assert normalize_isbn(VALID_ISBN13) == VALID_ISBN13
    assert normalize_isbn("978-0-13-604259-4") == VALID_ISBN13
    assert normalize_isbn("not-an-isbn") is None
    assert normalize_isbn("9780136042595") is None  # bad check digit


def test_language_and_date_normalization():
    assert normalize_language("বাংলা") == "ben"
    assert normalize_language("bn") == "ben"
    assert normalize_language("English") == "eng"
    assert normalize_date("প্রকাশ: ২০২১-০৩-০৫") == "2021-03-05"
    assert normalize_date("2019") == "2019"
    assert normalize_date("no date here") is None


def test_extract_meta_dual_signal_is_high():
    payload = json.dumps({
        "@context": "https://schema.org", "@type": "Book",
        "author": {"@type": "Person", "name": "Humayun Ahmed"},
        "inLanguage": "bn", "isbn": VALID_ISBN13})
    html = ("<html><head><script type=\"application/ld+json\">" + payload + "</script>"
            "<meta name=\"citation_author\" content=\"Humayun Ahmed\">"
            "</head><body><article><h1>Sample</h1></article></body></html>")
    meta = extract_meta(html)
    assert meta.author == "Humayun Ahmed"
    assert meta.isbn == VALID_ISBN13
    assert meta.confidence == "high"
    assert "json-ld" in (meta.source or "") and "meta" in (meta.source or "")


def test_extract_meta_single_structured_signal_is_medium():
    html = ('<html><head><meta name="citation_author" content="Sarat Chandra">'
            "</head><body><article><h1>T</h1></article></body></html>")
    meta = extract_meta(html)
    assert meta.author == "Sarat Chandra"
    assert meta.confidence == "medium"


def test_extract_meta_visible_only_is_low_and_denylist_rejects_admin():
    html = ("<html><body><article><h1>T</h1>"
            "<table><tr><th>Author</th><td>Admin</td></tr></table>"
            "</article></body></html>")
    assert extract_meta(html).author is None
    html2 = ("<html><body><article><h1>T</h1>"
             "<table><tr><th>লেখক</th><td>Bankim Chandra</td></tr></table>"
             "</article></body></html>")
    meta2 = extract_meta(html2)
    assert meta2.author == "Bankim Chandra"
    assert meta2.confidence == "low"


def test_crawl_stores_typed_metadata_columns(tmp_path):
    site = SITES["granthagara"]
    post = "https://granthagara.com/boi/1-sample/"
    html = ("<article><h1>Sample</h1><script type=\"application/ld+json\">" + json.dumps(
        {"@type": "Book", "author": "Author One", "publisher": "Pub House"}) + "</script>"
        "<meta name=\"citation_author\" content=\"Author One\">"
        "<a href=\"https://archive.org/download/x/x.pdf\">PDF</a></article>")
    pipeline = Pipeline(tmp_path, site, FakeClient({post: html}))
    with pipeline.db:
        pipeline.db.execute("INSERT INTO pages(site,url) VALUES (?,?)", (site.name, post))
    assert pipeline.crawl() == 1
    row = pipeline.db.execute(
        "SELECT title,author,publisher,meta_confidence,meta_source FROM pages WHERE url=?", (post,)
    ).fetchone()
    assert row["author"] == "Author One"
    assert row["publisher"] == "Pub House"
    assert row["meta_confidence"] in {"high", "medium"}
    assert row["meta_source"]
    status = pipeline.status()
    assert "meta" in status
    pipeline.db.close()


def _ol_isbn_payload():
    return json.dumps({
        "title": "Artificial Intelligence",
        "authors": [{"name": "Stuart Russell"}],
        "publishers": ["Prentice Hall"],
        "publish_date": "2010",
        "languages": [{"key": "/languages/eng"}],
        "number_of_pages": 1152,
    })


def _google_isbn_payload():
    return json.dumps({"items": [{
        "id": "kqsaBQAAQBAJ",
        "volumeInfo": {
            "title": "Artificial Intelligence",
            "authors": ["Stuart Russell"],
            "publisher": "Prentice Hall",
            "publishedDate": "2010",
            "language": "en",
            "pageCount": 1152,
            "industryIdentifiers": [{"type": "ISBN_13", "identifier": VALID_ISBN13}],
        },
    }]})


def test_enrich_isbn_exact_upgrades_to_high():
    client = FakeClient({
        OL_ISBN_URL.format(isbn=VALID_ISBN13): _ol_isbn_payload(),
        GOOGLE_ISBN_URL.format(isbn=VALID_ISBN13): _google_isbn_payload(),
    })
    enriched, source = enrich_metadata(BookMeta(isbn=VALID_ISBN13), "Artificial Intelligence", client)
    assert enriched.author == "Stuart Russell"
    assert enriched.publisher == "Prentice Hall"
    assert enriched.page_count == 1152
    assert enriched.confidence == "high"
    assert "openlibrary" in source and "google-books" in source


def test_enrich_resolves_openlibrary_key_only_author():
    ol_edition = json.dumps({
        "title": "Artificial Intelligence",
        "authors": [{"key": "/authors/OL123A"}],
        "publishers": ["Prentice Hall"],
    })
    ol_author = json.dumps({"key": "/authors/OL123A", "name": "Stuart Russell"})
    client = FakeClient({
        OL_ISBN_URL.format(isbn=VALID_ISBN13): ol_edition,
        "https://openlibrary.org/authors/OL123A.json": ol_author,
    })
    enriched, source = enrich_metadata(BookMeta(isbn=VALID_ISBN13), "Artificial Intelligence", client)
    assert enriched.author == "Stuart Russell"
    assert enriched.confidence == "high"
    assert "openlibrary" in source


def test_enrich_resolves_openlibrary_work_level_author():
    ol_edition = json.dumps({
        "title": "Artificial Intelligence",
        "works": [{"key": "/works/OL2896994W"}],
        "publishers": ["Prentice Hall"],
    })
    ol_work = json.dumps({
        "title": "Artificial intelligence",
        "authors": [{"type": {"key": "/type/author_role"},
                     "author": {"key": "/authors/OL456A"}}],
    })
    ol_author = json.dumps({"key": "/authors/OL456A", "name": "Stuart Russell"})
    client = FakeClient({
        OL_ISBN_URL.format(isbn=VALID_ISBN13): ol_edition,
        "https://openlibrary.org/works/OL2896994W.json": ol_work,
        "https://openlibrary.org/authors/OL456A.json": ol_author,
    })
    enriched, _source = enrich_metadata(BookMeta(isbn=VALID_ISBN13), "Artificial Intelligence", client)
    assert enriched.author == "Stuart Russell"
    assert enriched.confidence == "high"


def test_enrich_disagreement_refuses_fuzzy_assignment():
    from book_scrapper import resolve as R

    ol_payload = json.dumps({"docs": [{
        "title": "Common Title", "author_name": ["Author Alpha"],
        "publisher": ["Pub A"], "first_publish_year": 2001,
        "language": ["ben"], "isbn": [VALID_ISBN13],
    }]})
    gb_payload = json.dumps({"items": [{
        "id": "x",
        "volumeInfo": {"title": "Common Title", "authors": ["Author Beta"],
                       "publisher": "Pub B", "publishedDate": "2002",
                       "language": "bn", "pageCount": 100},
    }]})
    mapping = {
        R.OL_SEARCH_URL.format(title="Common%20Title", author="",
                               language="ben"): ol_payload,
        R.GOOGLE_SEARCH_URL.format(title="Common%20Title", author="",
                                   language="bn"): gb_payload,
    }
    enriched, _source = enrich_metadata(BookMeta(), "Common Title", FakeClient(mapping))
    assert enriched.author is None
    assert enriched.confidence == "low"


def test_archive_discovery_stores_authority_metadata(tmp_path):
    site = ArchiveBengali()
    db_path = tmp_path / "catalog.sqlite3"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE pages (site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT)")
    db.execute("CREATE TABLE assets (url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending', path TEXT, sha256 TEXT, bytes INTEGER, error TEXT)")
    db.execute("CREATE TABLE links (page_url TEXT, asset_url TEXT, label TEXT, PRIMARY KEY(page_url, asset_url))")
    db.commit()
    template = ArchiveBengali._search_template
    meta = json.dumps({
        "metadata": {"identifier": "x", "title": "Bangla Book",
                     "creator": "Kazi Nazrul", "date": "2020",
                     "language": "ben"},
        "files": [{"name": "x.pdf"}],
    })
    client = FakeClient({
        template.format(page=1): json.dumps({"response": {"docs": [{"identifier": "x"}]}}),
        template.format(page=2): json.dumps({"response": {"docs": []}}),
        "https://archive.org/metadata/x": meta,
    })
    assert site.discover_catalogue(client, db, limit=10) == 1
    row = db.execute("SELECT author,published_date,language,meta_confidence,meta_source FROM pages").fetchone()
    assert row[0] == "Kazi Nazrul"
    assert row[1] == "2020"
    assert row[2] == "ben"
    assert row[3] == "high"
    assert row[4] == "archive.org"
    db.close()


def test_old_catalogue_migrates_and_crawls(tmp_path):
    db_path = tmp_path / "catalog.sqlite3"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE pages (site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT)")
    db.commit()
    db.close()
    site = SITES["bengaliebook"]
    post = "https://bengaliebook.com/sample-book/"
    html = ('<article><h1>Sample</h1><meta name="citation_author" content="Jane Doe">'
            '<a href="https://bengaliebook.com/wp-content/uploads/x.pdf">PDF</a></article>')
    pipeline = Pipeline(tmp_path, site, FakeClient({post: html}))
    with pipeline.db:
        pipeline.db.execute("INSERT OR IGNORE INTO pages(site,url) VALUES (?,?)", (site.name, post))
    assert pipeline.crawl() == 1
    row = pipeline.db.execute("SELECT author,meta_confidence FROM pages WHERE url=?", (post,)).fetchone()
    assert row["author"] == "Jane Doe"
    pipeline.db.close()
