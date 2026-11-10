"""Offline tests for the EPUB curator."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

from book_scrapper.curate import curate_directory, curate_epub


def make_epub(path: Path) -> None:
    files = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": b"""<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        "OEBPS/content.opf": b"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book - \xe0\xa6\x9f\xe0\xa7\x87\xe0\xa6\xb2\xe0\xa6\xbf \xe0\xa6\xac\xe0\xa6\x87</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:publisher>\xe0\xa6\x9f\xe0\xa7\x87\xe0\xa6\xb2\xe0\xa6\xbf \xe0\xa6\xac\xe0\xa6\x87</dc:publisher>
    <dc:contributor>t.me/bongboi</dc:contributor>
    <dc:description>A project to scrape websites into epub</dc:description>
    <dc:rights>All copyrights reserved</dc:rights>
    <dc:language>en</dc:language>
    <dc:identifier id="bookid">%(uniqueid)s</dc:identifier>
    <dc:date>2020-04-17T12:20:29Z</dc:date>
    <meta name="calibre:timestamp" content="2020-04-17T12:20:29Z"/>
    <meta property="dcterms:modified">2020-04-17T12:20:29Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="promo" href="promo.xhtml" media-type="application/xhtml+xml"/>
    <item id="tail" href="tail.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="cover"/><itemref idref="nav"/><itemref idref="c1"/>
    <itemref idref="c2"/><itemref idref="promo"/><itemref idref="tail"/>
  </spine>
</package>""",
        "OEBPS/style.css": b"body { font-family: serif; }",
        "OEBPS/nav.xhtml": b"""<html xmlns="http://www.w3.org/1999/xhtml"><body><nav>
<a href="ch1.xhtml">One</a><a href="promo.xhtml">Promo</a><a href="ch2.xhtml">Two</a>
</nav></body></html>""",
        "OEBPS/cover.xhtml": b"""<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Test Book</h1><h2>Test Author</h2>
<p>Made with love by \xe0\xa6\x9f\xe0\xa7\x87\xe0\xa6\xb2\xe0\xa6\xbf \xe0\xa6\xac\xe0\xa6\x87</p>
<p><a href="https://t.me/bongboi">@bongboi</a></p>
</body></html>""",
        "OEBPS/ch1.xhtml": b"""<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>One</h1><p>Real chapter text keep this.</p>
<p><a href="https://example.com/x">source link</a> more text.</p>
</body></html>""",
        "OEBPS/ch2.xhtml": b"""<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Two</h1><p>Second chapter with \xe0\xa6\x9f\xe0\xa7\x87\xe0\xa6\xb2\xe0\xa6\xbf\xe0\xa6\x97\xe0\xa7\x8d\xe0\xa6\xb0\xe0\xa6\xbe\xe0\xa6\xae in prose.</p>
</body></html>""",
        "OEBPS/promo.xhtml": b"""<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Acknowledgment</h1><p>About Contributor</p>
<p><a href="https://t.me/bongboi">Tele Boi</a></p>
<p>This Book is scraped from public domain maintained by eBanglaLibrary.</p>
</body></html>""",
        "OEBPS/tail.xhtml": b"""<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>\xe0\xa6\xb6\xe0\xa7\x87\xe0\xa6\xb7 \xe0\xa6\xaa\xe0\xa7\x83\xe0\xa6\xb7\xe0\xa7\x8d\xe0\xa6\xa0\xe0\xa6\xbe</h1>
<p>\xe0\xa6\x86\xe0\xa6\xb0\xe0\xa6\x93 \xe0\xa6\xac\xe0\xa6\x87</p>
<p><a href="https://t.me/bongboi">\xe0\xa6\x9f\xe0\xa7\x87\xe0\xa6\xb2\xe0\xa6\xbf \xe0\xa6\xac\xe0\xa6\x87</a></p>
</body></html>""",
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def test_curate_strips_branding_links_and_junk_metadata(tmp_path):
    source = tmp_path / "book.epub"
    make_epub(source)

    data, report = curate_epub(source)
    archive = zipfile.ZipFile(io.BytesIO(data))
    opf = archive.read("OEBPS/content.opf").decode()
    text = "".join(
        archive.read(name).decode("utf-8", "replace")
        for name in archive.namelist()
        if name.endswith((".xhtml", ".ncx", ".opf"))
    )

    assert "OpenBanglaBooks" in opf
    assert "t.me/bongboi" not in text
    assert "eBanglaLibrary" not in text
    assert "A project to scrape" not in text
    assert "<a " not in text
    assert "Real chapter text keep this." in text
    assert "promo.xhtml" not in text
    assert "tail.xhtml" not in text
    assert report.title == "Test Book"
    assert report.author == "Test Author"
    assert report.removed_links >= 3
    # cover.xhtml stays (title/author) after its promo paragraphs are removed
    assert "OEBPS/cover.xhtml" in archive.namelist()
    # zip layout stays valid
    assert archive.namelist()[0] == "mimetype"
    assert archive.read("mimetype") == b"application/epub+zip"


def test_curate_is_deterministic(tmp_path):
    source = tmp_path / "book.epub"
    make_epub(source)
    first, _ = curate_epub(source)
    second, _ = curate_epub(source)
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_curate_directory_writes_sidecar_and_resumes(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    make_epub(books / "1_test.epub")
    out = tmp_path / "curated"

    reports = curate_directory(books, out)
    assert len(reports) == 1
    sidecars = list(out.glob("*.json"))
    assert len(sidecars) == 1
    payload = json.loads(sidecars[0].read_text())
    assert payload["publisher"] == "OpenBanglaBooks"
    assert payload["source"] == "1_test.epub"

    # A second pass skips the already-curated book.
    curate_directory(books, out)
    assert len(list(out.glob("*.json"))) == 1


def test_curate_renames_branded_filenames():
    from book_scrapper.curate import Report, _sanitize_filenames

    files = {
        "OEBPS/bongboi.xhtml": b'<html><body><a href="bongboi.xhtml">x</a></body></html>',
        "OEBPS/banner-telegram.png": b"png",
    }
    report = Report(source="x", source_sha256="0" * 64)
    _sanitize_filenames(files, report)
    assert sorted(files) == ["OEBPS/banner-book.png", "OEBPS/book.xhtml"]
    assert b"book.xhtml" in files["OEBPS/book.xhtml"]
