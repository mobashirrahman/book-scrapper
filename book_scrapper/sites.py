import re
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from .site_utils import Page, clean_url


class Amarboi:
    name = "amarboi"
    root = "https://www.amarboi.com/"

    def owns(self, url):
        return urlsplit(url).hostname in {"amarboi.com", "www.amarboi.com"}

    def is_post(self, url):
        return self.owns(url) and bool(re.fullmatch(r"/\d{4}/\d{2}/[^/]+\.html", urlsplit(url).path))

    def sitemap(self, xml):
        root = ET.fromstring(xml)
        kind = root.tag.rsplit("}", 1)[-1]
        if kind not in {"sitemapindex", "urlset"}:
            raise ValueError("Unexpected sitemap document")
        urls = [e.text.strip() for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "loc" and e.text]
        return kind == "sitemapindex", [u for u in urls if self.owns(u)]

    def parse(self, url, html):
        soup = BeautifulSoup(html, "html.parser")
        body = soup.select_one(".post-body, .entry-content")
        if body is None:
            raise ValueError("Post body missing; site layout may have changed")
        title = soup.select_one("h1.post-title, h3.post-title, h1.entry-title, h1")
        links = {}
        for tag in body.select("a[href], iframe[src], embed[src], object[data]"):
            raw = tag.get("href") or tag.get("src") or tag.get("data")
            target = clean_url(url, raw)
            if target and target != url:
                links[target] = tag.get_text(" ", strip=True)
        return Page(title.get_text(" ", strip=True) if title else url, list(links.items()))


from .sources import (
    BDeBooks, Granthagara, BengaliOnline, FID4SA, DPLELibrary,
    Boiprakash, PdfPoro, Boighor, ProjectGutenberg, NDLI,
)
Bdebooks = BDeBooks
GranthagaraAdapter = Granthagara
BengaliOnlineNet = BengaliOnline
Fid4SA = Fid4sa = FID4SA
DplElibrary = DPLELibrary
Pdfporo = PdfPoro
Boighorlibrary = Boighor
ProjectGutenbergBengali = Gutenberg = ProjectGutenberg
Ndli = NDLI

for _adapter in (Amarboi, BDeBooks, Granthagara, BengaliOnline, FID4SA,
                  DPLELibrary, Boiprakash, PdfPoro, Boighor,
                  ProjectGutenberg, NDLI):
    _adapter.download_capability = property(lambda self: self.capability)
    _adapter.adapter_status = property(lambda self: getattr(self, "status", "enabled"))

SITES = {adapter().name: adapter() for adapter in (
    Amarboi, BDeBooks, Granthagara, BengaliOnline, FID4SA, DPLELibrary,
    Boiprakash, PdfPoro, Boighor, ProjectGutenberg, NDLI,
)}
