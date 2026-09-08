import sqlite3

import pytest

from book_scrapper.pipeline import Pipeline
from book_scrapper.sites import (
    BDeBooks,
    BengaliOnline,
    Boighor,
    Boiprakash,
    DPLELibrary,
    FID4SA,
    Granthagara,
    NDLI,
    PdfPoro,
    ProjectGutenberg,
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
])
def test_all_new_sources_are_registered(name):
    assert name in SITES
    assert SITES[name].download_capability
    assert SITES[name].adapter_status


@pytest.mark.parametrize("site,post,asset", [
    (BDeBooks(), "https://bdebooks.com/bn/books/sample-book/", "https://cdn.example/sample.epub"),
    (Granthagara(), "https://granthagara.com/boi/300196-sample/", "https://archive.org/download/item/sample.pdf"),
    (BengaliOnline(), "https://isid.ac.in/~deepayan/bengalionline.net/book.html", "https://isid.ac.in/~deepayan/bengalionline.net/files/book.pdf"),
    (FID4SA(), "https://fid4sa-repository.ub.uni-heidelberg.de/id/eprint/123", "https://fid4sa-repository.ub.uni-heidelberg.de/id/eprint/123/1/file"),
    (DPLELibrary(), "https://elibrary.dpl.gov.bd/book/123", None),
    (Boiprakash(), "https://boiprakash.com/product-details.php?id=12", None),
    (PdfPoro(), "https://pdfporo.com/sample-book/", "https://cdn.example/sample.pdf"),
    (Boighor(), "https://boighorlibrary.com/books/sample-book-abc/", None),
    (ProjectGutenberg(), "https://www.gutenberg.org/ebooks/123", "https://www.gutenberg.org/cache/epub/123/sample.epub"),
    (NDLI(), "https://ndl.gov.in/item/123", None),
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
