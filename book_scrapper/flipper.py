"""FlipHTML5 page fetching for the Muktijuddho e-Archive book collection.

The archive presents its books through the FlipHTML5 viewer
(account ``lzrut``, vanity domain ``doc.liberationwarbangladesh.net/books/``).
The publisher has not enabled the native download feature, but every rendered
page is served as a public image at
``https://online.fliphtml5.com/lzrut/<code>/files/large/<n>.webp``.  This
module enumerates those pages from each book's ``config.js`` and fetches them
into per-book folders, optionally assembling a PDF when ``img2pdf`` is
available.  Transfers go through the shared, robots-aware ``http.Client`` so
pacing and site policy still apply.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

BOOK_ROOT = "https://online.fliphtml5.com/lzrut/{code}"
VIEWER_RE = re.compile(
    r"(?:doc\.liberationwarbangladesh\.net/books/([a-zA-Z0-9]+)"
    r"|online\.fliphtml5\.com/[a-zA-Z0-9]+/([a-zA-Z0-9]+))"
)
_WEBP = b"RIFF"
_MIN_PAGE_BYTES = 1024


def extract_book_codes(text):
    """Return the set of distinct FlipHTML5 book codes mentioned in ``text``."""
    codes = set()
    for match in VIEWER_RE.finditer(text):
        code = match.group(1) or match.group(2)
        if code:
            codes.add(code)
    return codes


def book_info(client, code):
    """Fetch ``config.js`` and return ``(title, page_count)`` for one book."""
    response = client.get(f"{BOOK_ROOT.format(code=code)}/javascript/config.js")
    if hasattr(response, "encoding"):
        response.encoding = "utf-8"
    raw = response.text
    match = re.search(r'var htmlConfig = (\{.*\})', raw, re.S)
    if not match:
        raise ValueError(f"Looked like FlipHTML5 config without htmlConfig: {code}")
    config = json.loads(match.group(1))
    meta = config.get("meta", {})
    page_count = int(meta.get("pageCount") or 0)
    if page_count < 1:
        raise ValueError(f"No page count available for {code}")
    return meta.get("title", code), page_count


def page_url(code, page_number):
    return f"{BOOK_ROOT.format(code=code)}/files/large/{page_number}.webp"


def catalogued_codes(db, site_name):
    """Collect book codes from the site's FlipHTML5 metadata links."""
    rows = db.execute(
        """SELECT DISTINCT m.url FROM metadata_links m
        JOIN pages p ON p.url = m.page_url
        WHERE p.site = ?""",
        (site_name,),
    ).fetchall()
    codes = set()
    for row in rows:
        url = row[0] if not hasattr(row, "keys") else row["url"]
        codes.update(extract_book_codes(url or ""))
    return codes


class FlipBookFetcher:
    """Resumable page-image downloader for one flipped book."""

    def __init__(self, client, code, out_dir, title="", page_count=0,
                 min_bytes=_MIN_PAGE_BYTES):
        self.client = client
        self.code = code
        self.out_dir = Path(out_dir)
        self.title = title
        self.page_count = page_count
        self.min_bytes = min_bytes
        self.progress = self._load()

    def _load(self):
        state = self.out_dir / "progress.json"
        if state.exists():
            try:
                return json.loads(state.read_text(encoding="utf-8")).get("done") or {}
            except ValueError:
                return {}
        return {}

    def _save(self):
        state = self.out_dir / "progress.json"
        state.write_text(
            json.dumps({"code": self.code, "title": self.title,
                        "page_count": self.page_count, "done": self.progress},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    def _page_name(self, page_number):
        return f"{page_number:04d}.webp"

    def download(self):
        if not self.page_count:
            self.title, self.page_count = book_info(self.client, self.code)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        for number in range(1, self.page_count + 1):
            name = self._page_name(number)
            target = self.out_dir / name
            if self.progress.get(name) == "ok" and target.exists():
                continue
            url = page_url(self.code, number)
            with self.client.get(url, stream=True) as response:
                if hasattr(response, "iter_content"):
                    content = b"".join(chunk for chunk in response.iter_content(64 * 1024) if chunk)
                else:
                    content = response.content
            if not content.startswith(_WEBP) or len(content) < self.min_bytes:
                raise ValueError(f"Page {number} ({url}) is not a valid page image")
            temp = target.with_suffix(".part")
            temp.write_bytes(content)
            temp.replace(target)
            self.progress[name] = "ok"
            downloaded += 1
            self._save()
        return downloaded

    def assemble_pdf(self):
        try:
            import img2pdf
        except ImportError:
            return None
        pages = sorted(self.out_dir.glob("[0-9][0-9][0-9][0-9].webp"))
        if not pages:
            return None
        pdf = self.out_dir.parent / f"{self.code}.pdf"
        with pdf.open("wb") as out:
            out.write(img2pdf.convert([str(p) for p in pages]))
        return pdf