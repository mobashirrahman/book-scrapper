import sqlite3

import pytest

from book_scrapper.pipeline import Pipeline
from book_scrapper.sites import (
    AllBanglaBoi,
    ArchiveBengali,
    BDeBooks,
    BanglaBook,
    BanglaBookshelf,
    BanglaBooksIn,
    BengaliOnline,
    BnWikisource,
    Boighor,
    Boiprakash,
    DPLELibrary,
    FID4SA,
    Granthagara,
    KindleBangla,
    LiberationWarBangladesh,
    Nctb,
    NDLI,
    PdfPoro,
    ProjectGutenberg,
    WorldMets,
    SITES,
)


class Response:
    def __init__(self, text):
        self.text = text
        self.content = text.encode()
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Client:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return Response(self.responses[url])


@pytest.mark.parametrize("name", [
    "bdebooks", "granthagara", "bengalionline", "fid4sa", "dpl_elibrary",
    "boiprakash", "pdfporo", "boighor", "gutenberg_bengali", "ndli",
    "kindlebangla", "liberationwarbangladesh", "banglabook", "allbanglaboi",
    "banglabooks_in", "banglabookshelf", "worldmets", "archive_bengali",
    "bn_wikisource", "nctb",
])
def test_all_new_sources_are_registered(name):
    assert name in SITES
    assert SITES[name].download_capability
    assert SITES[name].adapter_status


@pytest.mark.parametrize("site,post,asset", [
    (BDeBooks(), "https://bdebooks.com/bn/books/sample-book/", "https://cdn.example/sample.epub"),
    (BanglaBook(), "https://www.banglabook.org/sample-book/", "https://www.mediafire.com/file/abc/book.pdf"),
    (AllBanglaBoi(), "https://allbanglaboi.com/sample-book-pdf/", None),
    (BanglaBooksIn(), "https://www.banglabooks.in/category/sample-book/", "https://drive.google.com/file/d/abc/view"),
    (Granthagara(), "https://granthagara.com/boi/300196-sample/", "https://archive.org/download/item/sample.pdf"),
    (BengaliOnline(), "https://isid.ac.in/~deepayan/bengalionline.net/book.html", "https://isid.ac.in/~deepayan/bengalionline.net/files/book.pdf"),
    (FID4SA(), "https://fid4sa-repository.ub.uni-heidelberg.de/id/eprint/123", "https://fid4sa-repository.ub.uni-heidelberg.de/id/eprint/123/1/file"),
    (DPLELibrary(), "https://elibrary.dpl.gov.bd/book/123", None),
    (Boiprakash(), "https://boiprakash.com/product-details.php?id=12", None),
    (PdfPoro(), "https://pdfporo.com/sample-book/", "https://cdn.example/sample.pdf"),
    (Boighor(), "https://boighorlibrary.com/books/sample-book-abc/", None),
    (ProjectGutenberg(), "https://www.gutenberg.org/ebooks/123", "https://www.gutenberg.org/cache/epub/123/sample.epub"),
    (NDLI(), "https://ndl.gov.in/item/123", None),
    (KindleBangla(), "https://www.kindlebangla.com/book/6a9593879a248", None),
    (LiberationWarBangladesh(), "https://liberationwarbangladesh.org/?p=1", "https://drive.google.com/file/d/abc123/view"),
    (BanglaBookshelf(), "https://www.banglabookshelf.com/Story%20Books/Himu/himu-books.php", "https://www.banglabookshelf.com/Story%20Books/Himu/Himu.pdf"),
    (WorldMets(), "https://www.worldmets.com/author-slug/book-slug/", "https://drive.google.com/file/d/abc/view"),
    (ArchiveBengali(), "https://archive.org/details/someid/", "https://archive.org/download/someid/someid.pdf"),
    (BnWikisource(), "https://bn.wikisource.org/wiki/Sample_Book", "https://ws-export.wmcloud.org/?format=epub&lang=bn&page=Sample_Book"),
    (Nctb(), "https://nctb.cloud/textbooks/2026/primary/class-1/", "https://drive.google.com/file/d/abc/view"),
])
def test_detail_parsing_separates_assets_and_access_links(site, post, asset):
    asset_markup = f'<a href="{asset}">Download PDF</a>' if asset else ""
    html = f'''<article><h1>Fixture title</h1>{asset_markup}
        <a href="{post}#read">Read online</a>
        <a href="https://viewer.example/record/123">Viewer / catalogue record</a>
    </article>'''
    page = site.parse(post, html)
    assets = [url for url, _ in page.links]
    metadata = [url for url, _ in page.metadata_links]
    if asset and site.capability != "metadata-only":
        assert asset in assets
    else:
        assert asset is None or asset not in assets
    assert post + "#read" not in assets
    assert any("viewer.example" in url for url in metadata)


def test_granthagara_sitemap_ignores_lastmod_and_filters_writer_shards():
    site = Granthagara()
    xml = '''<sitemapindex><sitemap><loc>https://granthagara.com/boi-sitemap1.xml</loc>
        <lastmod>1900-01-01</lastmod></sitemap>
        <sitemap><loc>https://granthagara.com/writer-sitemap1.xml</loc></sitemap></sitemapindex>'''
    assert site.sitemap(xml) == (True, ["https://granthagara.com/boi-sitemap1.xml"])


def test_pdfporo_excludes_forbidden_getbook_path():
    site = PdfPoro()
    page = site.parse(
        "https://pdfporo.com/sample-book/",
        '<article><h1>Sample</h1><a href="/getbook/123">Download</a>'
        '<a href="https://cdn.example/book.pdf">PDF</a></article>',
    )
    assert [url for url, _ in page.links] == ["https://cdn.example/book.pdf"]
    assert [url for url, _ in page.metadata_links] == ["https://pdfporo.com/getbook/123"]


def test_extensionless_fid4sa_file_is_asset():
    site = FID4SA()
    assert site.is_asset("https://fid4sa-repository.ub.uni-heidelberg.de/id/eprint/123/1/file")
    assert not site.is_asset("https://fid4sa-repository.ub.uni-heidelberg.de/id/eprint/123")


def test_metadata_links_are_retained_without_asset_queue(tmp_path):
    site = Boighor()
    post = "https://boighorlibrary.com/books/sample-book-abc/"
    client = Client({post: '<main><h1>Sample</h1><a href="/books/sample-book-abc/read/">Read</a></main>'})
    pipeline = Pipeline(tmp_path, site, client)
    with pipeline.db:
        pipeline.db.execute("INSERT INTO pages(site,url) VALUES (?,?)", (site.name, post))
    assert pipeline.crawl() == 1
    assert pipeline.db.execute("SELECT count(*) FROM assets").fetchone()[0] == 0
    assert pipeline.db.execute("SELECT url FROM metadata_links").fetchone()[0] == "https://boighorlibrary.com/books/sample-book-abc/read/"


def test_boiprakash_discovery_is_bounded_and_deduplicated(tmp_path):
    site = Boiprakash()
    shop = site.root + "shop.php"
    product = site.root + "product-details.php?id=12"
    client = Client({shop: f'<a href="{product}">Book</a><a href="{product}">Book again</a>'})
    db_path = tmp_path / "catalog.sqlite3"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE pages (site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT)")
    db.commit()
    assert site.discover_catalogue(client, db, limit=1) == 1
    assert db.execute("SELECT count(*) FROM pages").fetchone()[0] == 1
    assert client.calls == [shop]


def test_kindlebangla_accepts_hex_and_slug_posts_but_not_listings():
    site = KindleBangla()
    assert site.is_post("https://kindlebangla.com/book/6a9593879a248")
    assert site.is_post("https://www.kindlebangla.com/book/শতধারায়-বয়ে-যায়")
    assert site.is_post("https://www.kindlebangla.com/book/শতধারায়-বয়ে-যায়/")
    assert not site.is_post("https://www.kindlebangla.com/books")
    assert not site.is_post("https://www.kindlebangla.com/book/")
    assert not site.is_post("https://www.kindlebangla.com/download/শতধারায়-বয়ে-যায়")
    assert not site.is_post("https://www.kindlebangla.com/writers")
    assert site.is_asset("https://www.kindlebangla.com/download/শতধারায়-বয়ে-যায়")
    assert not site.is_asset("https://www.kindlebangla.com/book/6a9593879a248")


def test_kindlebangla_sitemap_keeps_only_book_pages():
    site = KindleBangla()
    xml = '''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://kindlebangla.com/book/6a9593879a248</loc></url>
        <url><loc>https://kindlebangla.com/books</loc></url>
        <url><loc>https://kindlebangla.com/writers</loc></url>
        <url><loc>https://kindlebangla.com/category/foo</loc></url>
    </urlset>'''
    assert site.sitemap(xml.encode()) == (False, ["https://kindlebangla.com/book/6a9593879a248"])


def test_kindlebangla_download_handler_is_queued_as_asset():
    site = KindleBangla()
    post = "https://www.kindlebangla.com/book/6a9593879a248"
    page = site.parse(
        post,
        '<article><h1>Fixture title</h1>'
        '<a href="/download/fixture-title">বইটি ডাউনলোড করুন</a>'
        '<a href="https://viewer.example/record/123">Viewer record</a></article>',
    )
    assert [url for url, _ in page.links] == ["https://www.kindlebangla.com/download/fixture-title"]
    assert [url for url, _ in page.metadata_links] == ["https://viewer.example/record/123"]


class _DriveFiles:
    def __init__(self, entries):
        self.entries = entries

    def list(self, **kwargs):
        entries = self.entries

        class _Execute:
            def execute(self):
                return {"files": entries}

        return _Execute()


class _DriveService:
    def __init__(self, entries):
        self._files = _DriveFiles(entries)

    def files(self):
        return self._files


class _Redirect:
    def __init__(self, final_url):
        self.url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _RedirectClient:
    def __init__(self, final_url):
        self.final_url = final_url
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return _Redirect(self.final_url)


def test_gdrive_folder_id_parsing():
    from book_scrapper.gdrive import parse_drive_folder_id

    assert parse_drive_folder_id("https://drive.google.com/drive/folders/114MopUH6cKrh2eVFWDeN6xGxNdYlQgsB?usp=sharing") == "114MopUH6cKrh2eVFWDeN6xGxNdYlQgsB"
    assert parse_drive_folder_id("https://drive.google.com/file/d/abc123/view") is None
    assert parse_drive_folder_id("https://drive.google.com/uc?export=download&id=abc123") is None
    assert parse_drive_folder_id("https://example.com/drive/folders/abc123") is None


def test_gdrive_folder_needs_api_key(monkeypatch, tmp_path):
    from book_scrapper.gdrive import resolve_folder_file
    from book_scrapper.pipeline import Unsupported

    monkeypatch.delenv("GOOGLE_DRIVE_API_KEY", raising=False)
    monkeypatch.setenv("BOOK_SCRAPPER_CONFIG", str(tmp_path))
    with pytest.raises(Unsupported, match="GOOGLE_DRIVE_API_KEY"):
        resolve_folder_file("sOmEfOlDeRiD")


def test_gdrive_single_book_file_resolves(monkeypatch):
    from book_scrapper.gdrive import resolve_folder_file

    monkeypatch.setenv("GOOGLE_DRIVE_API_KEY", "test-key")
    factory = lambda key: _DriveService([{"id": "file123", "name": "book.pdf", "mimeType": "application/pdf"}])
    assert resolve_folder_file("sOmEfOlDeRiD", service_factory=factory) == "https://www.googleapis.com/drive/v3/files/file123?key=test-key&alt=media"


def test_gdrive_epub_wins_over_pdf_and_images(monkeypatch):
    from book_scrapper.gdrive import resolve_folder_file

    monkeypatch.setenv("GOOGLE_DRIVE_API_KEY", "test-key")
    entries = [
        {"id": "cover", "name": "cover.jpg", "mimeType": "image/jpeg"},
        {"id": "pdf", "name": "book.pdf", "mimeType": "application/pdf"},
        {"id": "epub", "name": "book.epub", "mimeType": "application/epub+zip"},
        {"id": "notes", "name": "notes.txt", "mimeType": "text/plain"},
    ]
    factory = lambda key: _DriveService(entries)
    assert resolve_folder_file("folder", service_factory=factory) == "https://www.googleapis.com/drive/v3/files/epub?key=test-key&alt=media"


def test_gdrive_empty_and_ambiguous_folders_are_unsupported(monkeypatch):
    from book_scrapper.gdrive import resolve_folder_file
    from book_scrapper.pipeline import Unsupported

    monkeypatch.setenv("GOOGLE_DRIVE_API_KEY", "test-key")
    with pytest.raises(Unsupported, match="no public book files"):
        resolve_folder_file("empty", service_factory=lambda key: _DriveService([]))
    with pytest.raises(Unsupported, match="no public book files"):
        resolve_folder_file(
            "images-only",
            service_factory=lambda key: _DriveService([{"id": "c", "name": "cover.jpg", "mimeType": "image/jpeg"}]),
        )
    multi_epub = _DriveService([
        {"id": "a", "name": "one.epub", "mimeType": "application/epub+zip"},
        {"id": "b", "name": "two.epub", "mimeType": "application/epub+zip"},
    ])
    with pytest.raises(Unsupported, match="human pick"):
        resolve_folder_file("multi-epub", service_factory=lambda key: multi_epub)
    multi_other = _DriveService([
        {"id": "a", "name": "one.pdf", "mimeType": "application/pdf"},
        {"id": "b", "name": "two.pdf", "mimeType": "application/pdf"},
    ])
    with pytest.raises(Unsupported, match="human pick"):
        resolve_folder_file("multi-pdf", service_factory=lambda key: multi_other)


def test_kindlebangla_resolve_asset_follows_redirect_to_drive_file(monkeypatch):
    monkeypatch.setenv("GOOGLE_DRIVE_API_KEY", "test-key")
    site = KindleBangla()
    client = _RedirectClient("https://drive.google.com/drive/folders/114MopUH6cKrh2eVFWDeN6xGxNdYlQgsB?usp=sharing")
    factory = lambda key: _DriveService([{"id": "file123", "name": "book.pdf", "mimeType": "application/pdf"}])
    resolved = site.resolve_asset(
        client, "https://www.kindlebangla.com/download/fixture-title", drive_service_factory=factory
    )
    assert resolved == "https://www.googleapis.com/drive/v3/files/file123?key=test-key&alt=media"
    assert client.calls == ["https://www.kindlebangla.com/download/fixture-title"]


def test_kindlebangla_resolve_asset_rejects_non_drive_landing():
    from book_scrapper.pipeline import Unsupported

    site = KindleBangla()
    client = _RedirectClient("https://www.kindlebangla.com/some-page")
    with pytest.raises(Unsupported, match="did not land on a Drive folder or file"):
        site.resolve_asset(client, "https://www.kindlebangla.com/download/fixture-title",
                           drive_service_factory=lambda key: _DriveService([]))


def test_kindlebangla_resolve_asset_accepts_drive_file_landing(monkeypatch):
    monkeypatch.setenv("GOOGLE_DRIVE_API_KEY", "test-key")
    site = KindleBangla()
    client = _RedirectClient("https://drive.google.com/file/d/abc123/view?usp=sharing")
    resolved = site.resolve_asset(
        client, "https://www.kindlebangla.com/download/fixture-title",
        drive_service_factory=lambda key: _DriveService([]),
    )
    assert resolved == "https://www.googleapis.com/drive/v3/files/abc123?key=test-key&alt=media"


def test_drive_file_id_parsing():
    from book_scrapper.gdrive import parse_drive_file_id

    assert parse_drive_file_id("https://drive.google.com/file/d/abc123/view?usp=sharing") == "abc123"
    assert parse_drive_file_id("https://drive.google.com/uc?export=download&id=abc123") == "abc123"
    assert parse_drive_file_id("https://drive.google.com/drive/folders/abc123") is None
    assert parse_drive_file_id("https://example.com/file/d/abc123") is None


def test_resolve_drive_file_uses_api_media_url_with_key(monkeypatch):
    from book_scrapper.pipeline import resolve

    monkeypatch.setenv("GOOGLE_DRIVE_API_KEY", "test-key")
    assert resolve(None, "https://drive.google.com/file/d/abc123/view") == "https://www.googleapis.com/drive/v3/files/abc123?key=test-key&alt=media"


def test_resolve_drive_file_falls_back_to_uc_without_key(monkeypatch, tmp_path):
    from book_scrapper.pipeline import resolve, Unsupported

    monkeypatch.delenv("GOOGLE_DRIVE_API_KEY", raising=False)
    monkeypatch.setenv("BOOK_SCRAPPER_CONFIG", str(tmp_path))
    with pytest.raises(Unsupported, match="API key"):
        resolve(None, "https://drive.google.com/file/d/abc123/view")


def test_gdrive_media_url_round_trip():
    from book_scrapper.gdrive import media_url, parse_media_file_id, uc_download_url

    assert parse_media_file_id(media_url("abc123", "key")) == "abc123"
    assert parse_media_file_id("https://drive.google.com/uc?export=download&id=abc123") is None
    assert parse_media_file_id("https://example.com/drive/v3/files/abc123") is None
    assert uc_download_url("abc123") == "https://drive.google.com/uc?export=download&id=abc123"


class _StreamResponse:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, size):
        yield self._payload


class _FallbackClient:
    """403s the Drive API media URL, serves the direct uc URL."""

    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        import requests

        self.calls.append(url)
        if "googleapis.com" in url:
            response = requests.Response()
            response.status_code = 403
            response.url = url
            raise requests.HTTPError("403 Client Error: Forbidden", response=response)
        return _StreamResponse(b"%PDF-1.4 fake")


def test_download_retries_drive_api_403_through_direct_uc(tmp_path, monkeypatch):
    from book_scrapper.pipeline import Pipeline

    monkeypatch.setenv("GOOGLE_DRIVE_API_KEY", "test-key")
    site = Nctb()
    page = site.root + "textbooks/2017/primary/class-1/"
    asset = "https://drive.google.com/file/d/abc123/view"
    client = _FallbackClient()
    pipeline = Pipeline(tmp_path, site, client)
    with pipeline.db:
        pipeline.db.execute("INSERT INTO pages(site,url) VALUES (?,?)", (site.name, page))
        pipeline.db.execute("INSERT INTO assets(url) VALUES (?)", (asset,))
        pipeline.db.execute("INSERT INTO links VALUES (?,?,?)", (page, asset, "book"))
    pipeline.download()
    row = pipeline.db.execute("SELECT status, bytes FROM assets WHERE url=?", (asset,)).fetchone()
    assert tuple(row) == ("done", len(b"%PDF-1.4 fake"))
    assert client.calls[0].startswith("https://www.googleapis.com/drive/v3/files/abc123?")
    assert client.calls[1] == "https://drive.google.com/uc?export=download&id=abc123"


def test_lwb_identifies_wordpress_posts_and_drive_files():
    site = LiberationWarBangladesh()
    assert site.is_post("https://liberationwarbangladesh.org/?p=1234")
    assert site.is_post("https://www.liberationwarbangladesh.org/?p=1234")
    assert not site.is_post("https://liberationwarbangladesh.org/")
    assert not site.is_post("https://liberationwarbangladesh.org/category/news/")
    assert not site.is_post("https://online.fliphtml5.com/lzrut/avdg/")
    assert site.is_asset("https://drive.google.com/file/d/abc123/view")
    assert site.is_asset("https://drive.usercontent.google.com/download?id=abc123")
    assert not site.is_asset("https://drive.google.com/drive/folders/abc123")
    assert not site.is_asset("https://doc.liberationwarbangladesh.net/books/avdg")


def test_lwb_parse_routes_viewer_links_to_metadata_and_drive_to_assets():
    site = LiberationWarBangladesh()
    page = site.parse(
        "https://liberationwarbangladesh.org/?p=1",
        '<article><h1>বই</h1>'
        '<a href="https://doc.liberationwarbangladesh.net/books/avdg">পড়ুন</a>'
        '<a href="https://drive.google.com/file/d/xyz/view">ফাইল</a>'
        '<a href="https://view.publitas.com/liberationwarbangladesh/17-may-1971/">সংবাদ</a>'
        '<a href="/">হোম</a></article>',
    )
    assert [url for url, _ in page.links] == ["https://drive.google.com/file/d/xyz/view"]
    assert [url for url, _ in page.metadata_links] == [
        "https://doc.liberationwarbangladesh.net/books/avdg",
        "https://view.publitas.com/liberationwarbangladesh/17-may-1971/",
    ]


def test_lwb_rest_discovery_respects_limit(tmp_path):
    site = LiberationWarBangladesh()
    posts = [{"id": i} for i in range(1, 101)]
    client = Client({site.root + "index.php?rest_route=/wp/v2/posts&per_page=100&page=1": __import__("json").dumps(posts)})
    db_path = tmp_path / "catalog.sqlite3"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE pages (site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT)")
    db.commit()
    assert site.discover_catalogue(client, db, limit=2) == 2
    assert db.execute("SELECT count(*) FROM pages").fetchone()[0] == 2
    assert db.execute("SELECT url FROM pages ORDER BY url").fetchall() == [
        (site.root + "?p=1",), (site.root + "?p=2",),
    ]


def test_flip_book_code_extraction_and_urls():
    from book_scrapper.flipper import extract_book_codes, page_url

    codes = extract_book_codes(
        "vanity https://doc.liberationwarbangladesh.net/books/avdg and account "
        "https://online.fliphtml5.com/lzrut/qwer and again https://doc.liberationwarbangladesh.net/books/avdg"
    )
    assert codes == {"avdg", "qwer"}
    assert page_url("avdg", 7) == "https://online.fliphtml5.com/lzrut/avdg/files/large/7.webp"


def test_flip_book_info_parses_config_and_rejects_garbage():
    from book_scrapper.flipper import book_info

    client = Client({"https://online.fliphtml5.com/lzrut/avdg/javascript/config.js": 'var htmlConfig = {"meta": {"title": "নতুন বই", "pageCount": 199}};'})
    assert book_info(client, "avdg") == ("নতুন বই", 199)
    client = Client({"https://online.fliphtml5.com/lzrut/avdg/javascript/config.js": "not a config"})
    with pytest.raises(ValueError, match="htmlConfig"):
        book_info(client, "avdg")


def _flip_client(pages):
    from book_scrapper.flipper import page_url

    config = 'var htmlConfig = {"meta": {"title": "Sample", "pageCount": %d}};'
    responses = {
        "https://online.fliphtml5.com/lzrut/avdg/javascript/config.js": config % len(pages),
    }
    for number, payload in enumerate(pages, start=1):
        responses[page_url("avdg", number)] = payload
    return Client(responses)


def test_flip_fetch_downloads_then_resumes(tmp_path):
    from book_scrapper.flipper import FlipBookFetcher

    page_payload = "RIFF" + "x" * 20_000
    client = _flip_client([page_payload, page_payload, page_payload])
    out_dir = tmp_path / "fliphtml5" / "avdg"
    fetcher = FlipBookFetcher(client, "avdg", out_dir)
    assert fetcher.download() == 3
    assert sorted(path.name for path in out_dir.glob("*.webp")) == ["0001.webp", "0002.webp", "0003.webp"]
    assert fetcher.title == "Sample" and fetcher.page_count == 3
    resumed = FlipBookFetcher(client, "avdg", out_dir)
    assert resumed.download() == 0
    assert (out_dir / "progress.json").exists()


def test_flip_fetch_rejects_missing_pages(tmp_path):
    from book_scrapper.flipper import FlipBookFetcher

    client = _flip_client(["RIFF" + "x" * 20_000, "this is not a webp page", "RIFF" + "x" * 20_000])
    with pytest.raises(ValueError, match="not a valid page image"):
        FlipBookFetcher(client, "avdg", tmp_path / "fliphtml5" / "avdg").download()


def test_flip_catalogued_codes_collects_metadata_link_codes(tmp_path):
    from book_scrapper.flipper import catalogued_codes

    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    db.execute("CREATE TABLE pages (site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT)")
    db.execute("CREATE TABLE metadata_links (page_url TEXT, url TEXT, title TEXT)")
    page_url_ = "https://liberationwarbangladesh.org/?p=1"
    db.execute("INSERT INTO pages(site, url) VALUES (?,?)", ("liberationwarbangladesh", page_url_))
    db.execute("INSERT INTO metadata_links(page_url, url) VALUES (?, ?)",
               (page_url_, "https://doc.liberationwarbangladesh.net/books/avdg"))
    db.execute("INSERT INTO metadata_links(page_url, url) VALUES (?, ?)",
               (page_url_, "https://view.publitas.com/liberationwarbangladesh/17-may-1971/"))
    db.commit()
    assert catalogued_codes(db, "liberationwarbangladesh") == {"avdg"}


def test_banglabook_recognizes_single_segment_posts():
    site = BanglaBook()
    assert site.is_post("https://www.banglabook.org/tmtu/")
    assert site.is_post("https://www.banglabook.org/a-b-c-123/")
    assert not site.is_post("https://www.banglabook.org/")
    assert not site.is_post("https://www.banglabook.org/category/news/")
    assert not site.is_post("https://www.banglabook.org/wp-sitemap.xml")
    assert site.is_asset("https://mega.nz/#!abc!key")
    assert site.is_asset("https://www.mediafire.com/file/rnxj/book.pdf/file")
    assert not site.is_asset("https://www.banglabook.org/sample-book/")


def test_banglabook_sitemap_filters_posts():
    site = BanglaBook()
    xml = '''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://www.banglabook.org/tmtu/</loc></url>
        <url><loc>https://www.banglabook.org/contact-us/</loc></url>
        <url><loc>https://www.banglabook.org/</loc></url>
    </urlset>'''
    assert site.sitemap(xml.encode()) == (False, ["https://www.banglabook.org/tmtu/", "https://www.banglabook.org/contact-us/"])


def test_allbanglaboi_rest_discovery_uses_permalink(tmp_path):
    import json
    site = AllBanglaBoi()
    posts = [{"id": 1, "link": "https://allbanglaboi.com/sample-book-pdf/"}]
    client = Client({site.root + "wp-json/wp/v2/posts?per_page=100&page=1": json.dumps(posts)})
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    db.execute("CREATE TABLE pages (site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT)")
    db.commit()
    assert site.discover_catalogue(client, db, limit=5) == 1
    assert db.execute("SELECT url FROM pages").fetchall() == [("https://allbanglaboi.com/sample-book-pdf/",)]


def test_allbanglaboi_posts_and_external_assets():
    site = AllBanglaBoi()
    assert site.is_post("https://allbanglaboi.com/10760/")
    assert site.is_post("https://allbanglaboi.com/sample-book-pdf/")
    assert not site.is_post("https://allbanglaboi.com/")
    # Metadata-only: drive/mediafire download links stay out of the asset queue.
    assert not site.is_asset("https://allbanglaboi.com/sample-book-pdf/")
    page = site.parse(
        "https://allbanglaboi.com/sample-book-pdf/",
        '<article><h1>Sample</h1>'
        '<a href="https://drive.google.com/file/d/abc/view">পিডিএফ ডাউনলোড</a>'
        '<a href="/10900/">আরেকটি বই</a></article>',
    )
    assert [url for url, _ in page.links] == []
    assert [url for url, _ in page.metadata_links] == [
        "https://drive.google.com/file/d/abc/view", "https://allbanglaboi.com/10900/",
    ]


def test_banglabooks_in_requires_two_segments_and_strips_query_noise():
    site = BanglaBooksIn()
    assert site.is_post("https://www.banglabooks.in/category/sample-book/")
    assert not site.is_post("https://www.banglabooks.in/blog/")
    assert not site.is_post("https://www.banglabooks.in/category/sample-book/?share=facebook")
    assert not site.is_post("https://www.banglabooks.in/wp-content/uploads/2023/06/c.jpg")
    assert site.is_asset("https://drive.google.com/file/d/abc/view")
    assert site.is_asset("https://docs.google.com/uc?export=download&id=abc")
    assert not site.is_asset("https://www.banglabooks.in/category/sample-book/")


def test_banglabooks_in_sitemap_keeps_only_post_shards():
    site = BanglaBooksIn()
    xml = '''<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <sitemap><loc>https://www.banglabooks.in/post-sitemap.xml</loc></sitemap>
        <sitemap><loc>https://www.banglabooks.in/page-sitemap.xml</loc></sitemap>
        <sitemap><loc>https://www.banglabooks.in/category-sitemap.xml</loc></sitemap>
    </sitemapindex>'''
    assert site.sitemap(xml.encode()) == (True, ["https://www.banglabooks.in/post-sitemap.xml"])


def test_banglabooks_in_parse_keeps_drive_download_asset_and_reader_metadata():
    site = BanglaBooksIn()
    post = "https://www.banglabooks.in/anugalpo/anugalper-mohona-pdf/"
    page = site.parse(
        post,
        '<article><h1>অণুগল্পের মোহনা</h1>'
        '<a href="https://drive.google.com/file/d/abc/view">পিডিএফ ডাউনলোড</a>'
        '<a href="https://viewer.example/record/1">অনলাইনে পড়ুন</a>'
        '</article>',
    )
    assert [url for url, _ in page.links] == ["https://drive.google.com/file/d/abc/view"]
    assert [url for url, _ in page.metadata_links] == ["https://viewer.example/record/1"]


def test_ignore_robots_skips_robots_txt(tmp_path):
    from book_scrapper.http import Client as RealClient

    client = RealClient(delay=0, respect_robots=False)
    try:
        assert client.allowed("https://example.com/any-path") is True
    finally:
        client.session.close()


def test_banglabookshelf_accepts_php_pages_and_direct_pdfs():
    site = BanglaBookshelf()
    assert site.is_post("https://www.banglabookshelf.com/Story%20Books/Himu/himu-books.php")
    assert site.is_post("https://www.banglabookshelf.com/Story-Book.php")
    assert not site.is_post("https://www.banglabookshelf.com/")
    assert not site.is_post("https://www.banglabookshelf.com/index.php")
    assert not site.is_post("https://www.banglabookshelf.com/about-us.php")
    assert not site.is_post("https://www.banglabookshelf.com/contact-us.php")
    assert not site.is_post("https://www.banglabookshelf.com/privacy-policy.php")
    assert site.is_asset("https://www.banglabookshelf.com/Story%20Books/Himu/Himu-by-Humayun-Ahmed.PDF")
    assert not site.is_asset("https://www.banglabookshelf.com/Story-Book.php")
    assert not site.is_asset("https://cdn.example/book.pdf")


def test_banglabookshelf_parse_queues_pdfs_and_follows_php_links(tmp_path):
    site = BanglaBookshelf()
    listing = site.root + "Story-Book.php"
    series = site.root + "Story%20Books/Himu/himu-books.php"
    client = Client({listing: f'<a href="{series}">Himu</a><a href="/about-us.php">About</a>'})
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    db.execute("CREATE TABLE pages (site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT)")
    db.commit()
    assert site.discover_catalogue(client, db) == 1
    assert db.execute("SELECT url FROM pages").fetchall() == [(series,)]

    pipeline = Pipeline(tmp_path, site, Client({series: (
        '<html><head><title>Himu books</title></head><body>'
        '<a href="/Story%20Books/Himu/Himu-by-Humayun-Ahmed.PDF">Download</a>'
        '<a href="/Story%20Books/Himu/other-books.php">More</a></body></html>'
    )}))
    with pipeline.db:
        pipeline.db.execute("INSERT OR IGNORE INTO pages(site,url) VALUES (?,?)", (site.name, series))
    assert pipeline.crawl() == 1
    assert pipeline.db.execute("SELECT url FROM assets").fetchone()[0] == \
        "https://www.banglabookshelf.com/Story%20Books/Himu/Himu-by-Humayun-Ahmed.PDF"
    assert pipeline.db.execute(
        "SELECT url FROM pages WHERE url=?", (site.root + "Story%20Books/Himu/other-books.php",)
    ).fetchone() is not None


def test_worldmets_sitemap_keeps_only_post_shards():
    site = WorldMets()
    xml = '''<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <sitemap><loc>https://www.worldmets.com/post-sitemap.xml</loc></sitemap>
        <sitemap><loc>https://www.worldmets.com/post-sitemap2.xml</loc></sitemap>
        <sitemap><loc>https://www.worldmets.com/page-sitemap.xml</loc></sitemap>
        <sitemap><loc>https://www.worldmets.com/category-sitemap.xml</loc></sitemap>
        <sitemap><loc>https://www.worldmets.com/attachment-sitemap.xml</loc></sitemap>
    </sitemapindex>'''
    assert site.sitemap(xml.encode()) == (True, [
        "https://www.worldmets.com/post-sitemap.xml",
        "https://www.worldmets.com/post-sitemap2.xml",
    ])


def test_worldmets_posts_require_two_to_four_slug_segments():
    site = WorldMets()
    assert site.is_post("https://www.worldmets.com/abanindranath-tagore/chirakaler-sera-book-pdf/")
    assert site.is_post("https://www.worldmets.com/cat/sub/book-title-ebook-pdf/")
    assert site.is_post("https://www.worldmets.com/a/b/c/d-book-pdf/")
    assert not site.is_post("https://www.worldmets.com/blog/")
    assert not site.is_post("https://www.worldmets.com/a/b/c/d/e-book-pdf/")
    assert not site.is_post("https://www.worldmets.com/wp-content/uploads/2026/09/cover.jpg")
    assert not site.is_post("https://www.worldmets.com/author-slug/book-slug/?share=facebook")
    assert not site.is_post("https://www.worldmets.com/category/some-cat/")
    assert not site.is_post("https://www.worldmets.com/feed/")
    assert site.is_asset("https://drive.google.com/file/d/abc/view?usp=share_link")


def test_worldmets_parse_keeps_drive_asset():
    site = WorldMets()
    post = "https://www.worldmets.com/sarat-chandra-chattopadhyay/devdas-novel/devdas-pdf/"
    page = site.parse(
        post,
        '<article><h1>Devdas PDF</h1>'
        '<a href="https://drive.google.com/file/d/abc/view?usp=share_link">Download</a>'
        '<a href="https://viewer.example/record/1">Read online</a></article>',
    )
    assert [url for url, _ in page.links] == ["https://drive.google.com/file/d/abc/view?usp=share_link"]
    assert [url for url, _ in page.metadata_links] == ["https://viewer.example/record/1"]


def test_archive_bengali_posts_and_file_assets():
    site = ArchiveBengali()
    assert site.is_post("https://archive.org/details/someid/")
    assert site.is_post("https://archive.org/details/someid")
    assert not site.is_post("https://archive.org/download/someid/someid.pdf")
    assert not site.is_post("https://archive.org/search?query=bengali")
    assert not site.is_post("https://archive.org/advancedsearch.php")
    assert site.is_asset("https://archive.org/download/someid/someid.pdf")
    assert site.is_asset("https://archive.org/download/someid/someid_text.pdf")
    assert site.is_asset("https://archive.org/download/someid/book.epub")
    assert not site.is_asset("https://archive.org/download/someid/someid_jp2.zip")
    assert not site.is_asset("https://archive.org/download/someid/someid_archive.torrent")
    assert not site.is_asset("https://archive.org/details/someid/")
    assert not site.is_asset("https://cdn.example/book.pdf")


def test_archive_bengali_parse_keeps_book_files_only():
    site = ArchiveBengali()
    post = "https://archive.org/details/someid/"
    page = site.parse(
        post,
        '<article><h1>Sample book</h1>'
        '<a href="/download/someid/someid_text.pdf">PDF</a>'
        '<a href="/download/someid/someid_jp2.zip">JP2 ZIP</a>'
        '<a href="/download/someid/someid_archive.torrent">Torrent</a>'
        '<a href="/details/otherid/">Related item</a></article>',
    )
    assert [url for url, _ in page.links] == ["https://archive.org/download/someid/someid_text.pdf"]
    assert "https://archive.org/details/otherid/" in [url for url, _ in page.metadata_links]


def _archive_search_payload(identifiers):
    import json

    return json.dumps({"response": {"numFound": len(identifiers), "docs": [{"identifier": i} for i in identifiers]}})


def _archive_meta_payload(title, names):
    import json

    return json.dumps({"metadata": {"identifier": "x", "title": title}, "files": [{"name": n} for n in names]})


def test_archive_bengali_discovery_resolves_files_and_skips_granthagara_ids(tmp_path):
    site = ArchiveBengali()
    archive_db = tmp_path / "archive" / "catalog.sqlite3"
    archive_db.parent.mkdir(parents=True)
    granthagara_db = tmp_path / "granthagara" / "catalog.sqlite3"
    granthagara_db.parent.mkdir(parents=True)
    gdb = sqlite3.connect(granthagara_db)
    gdb.execute("CREATE TABLE assets (url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending', path TEXT, sha256 TEXT, bytes INTEGER, error TEXT)")
    gdb.execute("CREATE TABLE metadata_links (page_url TEXT, url TEXT, label TEXT, PRIMARY KEY(page_url, url))")
    gdb.execute("INSERT INTO assets(url) VALUES (?)", ("https://archive.org/download/sharedid/sharedid.pdf",))
    gdb.execute(
        "INSERT INTO metadata_links VALUES (?,?,?)",
        ("https://granthagara.com/boi/1-x/", "https://archive.org/details/metaid/", "scan"),
    )
    gdb.commit()
    gdb.close()

    db = sqlite3.connect(archive_db)
    db.execute("CREATE TABLE pages (site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT)")
    db.execute("CREATE TABLE assets (url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending', path TEXT, sha256 TEXT, bytes INTEGER, error TEXT)")
    db.execute("CREATE TABLE links (page_url TEXT, asset_url TEXT, label TEXT, PRIMARY KEY(page_url, asset_url))")
    db.commit()
    template = ArchiveBengali._search_template
    client = Client({
        template.format(page=1): _archive_search_payload(["sharedid", "metaid", "freshid", "emptyid"]),
        template.format(page=2): _archive_search_payload([]),
        "https://archive.org/metadata/freshid": _archive_meta_payload(
            "Fresh Book", ["freshid.pdf", "freshid_jp2.zip", "freshid_archive.torrent"]),
        "https://archive.org/metadata/emptyid": _archive_meta_payload("Empty", ["emptyid_jp2.zip"]),
    })
    assert site.discover_catalogue(client, db, limit=10) == 1
    assert db.execute("SELECT url, title, status FROM pages").fetchall() == [
        ("https://archive.org/details/freshid/", "Fresh Book", "done")]
    assert db.execute("SELECT asset_url FROM links").fetchall() == [
        ("https://archive.org/download/freshid/freshid.pdf",)]
    # Second run is stable: own pages are skipped too.
    assert site.discover_catalogue(client, db, limit=10) == 0


def test_bn_wikisource_posts_and_export_assets():
    site = BnWikisource()
    assert site.is_post("https://bn.wikisource.org/wiki/গল্পসল্প/ধ্বংস")
    assert not site.is_post("https://bn.wikisource.org/wiki/বিশেষ:সাম্প্রতিক_পরিবর্তন")
    assert not site.is_post("https://bn.wikisource.org/")
    assert not site.is_post("https://ws-export.wmcloud.org/?format=epub&lang=bn&page=X")
    assert site.is_asset("https://ws-export.wmcloud.org/?format=epub&lang=bn&page=Sample_Book")
    assert site.is_asset("https://ws-export.wmcloud.org/?format=pdf&lang=bn&page=X")
    assert not site.is_asset("https://ws-export.wmcloud.org/?format=epub&lang=en&page=X")
    assert not site.is_asset("https://bn.wikisource.org/wiki/Sample_Book")
    page = site.parse(
        "https://bn.wikisource.org/wiki/Sample_Book",
        '<article><h1>Sample</h1>'
        '<a href="https://ws-export.wmcloud.org/?format=epub&lang=bn&page=Sample_Book">EPUB ডাউনলোড</a>'
        '<a href="https://viewer.example/record/1">Read online</a></article>',
    )
    assert [url for url, _ in page.links] == ["https://ws-export.wmcloud.org/?format=epub&lang=bn&page=Sample_Book"]
    assert [url for url, _ in page.metadata_links] == ["https://viewer.example/record/1"]


def _wikisource_api_payload(titles, apcontinue=""):
    import json

    payload = {"query": {"allpages": [{"pageid": i + 1, "ns": 0, "title": t} for i, t in enumerate(titles)]}}
    if apcontinue:
        payload["continue"] = {"apcontinue": apcontinue, "continue": "-||"}
    return json.dumps(payload)


def test_bn_wikisource_discovery_paginates_and_records_exports(tmp_path):
    site = BnWikisource()
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    db.execute("CREATE TABLE pages (site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT)")
    db.execute("CREATE TABLE assets (url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending', path TEXT, sha256 TEXT, bytes INTEGER, error TEXT)")
    db.execute("CREATE TABLE links (page_url TEXT, asset_url TEXT, label TEXT, PRIMARY KEY(page_url, asset_url))")
    db.commit()
    template = BnWikisource._api_template
    client = Client({
        template.format(cont=""): _wikisource_api_payload(["Main Page", "গল্পসল্প/ধ্বংস", "Sample Book"], "Next"),
        template.format(cont="&apcontinue=Next"): _wikisource_api_payload(["Third"]),
    })
    assert site.discover_catalogue(client, db) == 3
    rows = db.execute("SELECT title, status FROM pages").fetchall()
    assert sorted(rows) == [("Sample Book", "done"), ("Third", "done"), ("গল্পসল্প/ধ্বংস", "done")]
    assert db.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 3
    assert "ws-export.wmcloud.org" in db.execute("SELECT asset_url FROM links LIMIT 1").fetchone()[0]


def test_nctb_posts_follow_year_level_class_shape():
    site = Nctb()
    assert site.is_post("https://nctb.cloud/textbooks/")
    assert site.is_post("https://nctb.cloud/textbooks/2017/")
    assert site.is_post("https://nctb.cloud/textbooks/2017/primary/")
    assert site.is_post("https://nctb.cloud/textbooks/2017/primary/class-1/")
    assert not site.is_post("https://nctb.cloud/")
    assert not site.is_post("https://nctb.cloud/teachers/")
    assert not site.is_post("https://nctb.cloud/textbooks/2017/primary/class-1/?x=1")
    assert not site.is_post("https://nctb.cloud/textbooks/a/b/c/d/")
    assert site.is_asset("https://drive.google.com/file/d/abc/view")
    assert site.is_asset("https://drive.google.com/uc?export=download&id=abc")


def test_nctb_discovery_collects_shelves_and_crawl_expands(tmp_path):
    site = Nctb()
    shelf = site.root + "textbooks/"
    year = site.root + "textbooks/2017/"
    client = Client({
        site.root: '<a href="/textbooks/">Textbooks</a>',
        shelf: f'<a href="{year}">2017</a><a href="/teachers/">Teachers</a>',
    })
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    db.execute("CREATE TABLE pages (site TEXT NOT NULL, url TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT)")
    db.commit()
    # Seeds are first-level only: root yields the shelf; crawl expands the year.
    assert site.discover_catalogue(client, db) == 1
    pipeline = Pipeline(tmp_path, site, client)
    assert pipeline.crawl() == 1
    urls = [row[0] for row in pipeline.db.execute("SELECT url FROM pages ORDER BY url")]
    assert urls == [shelf, year]


def test_nctb_parse_queues_drive_assets():
    site = Nctb()
    post = "https://nctb.cloud/textbooks/2017/primary/class-1/"
    page = site.parse(
        post,
        '<article><h1>Class 1</h1>'
        '<a href="https://drive.google.com/uc?export=download&id=abc">আমার বাংলা বই</a>'
        '<a href="https://viewer.example/record/1">Read online</a></article>',
    )
    assert [url for url, _ in page.links] == ["https://drive.google.com/uc?export=download&id=abc"]
    assert [url for url, _ in page.metadata_links] == ["https://viewer.example/record/1"]
