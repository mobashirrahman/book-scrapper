"""Shared site parsing primitives used by the built-in adapters."""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit


def clean_url(base, value):
    parts = urlsplit(urljoin(base, value.strip()))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    fragment = parts.fragment if (parts.hostname or "").lower() in {
        "mega.nz", "mega.co.nz", "www.mega.nz", "www.mega.co.nz",
    } else ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, fragment))


@dataclass
class BookMeta:
    """Typed bibliographic metadata for one catalogue page.

    ``confidence`` is high only when two independent signals agree;
    medium for one structured signal (JSON-LD/meta/API); low otherwise.
    ``source`` names the winning provenance (e.g. json-ld+meta,
    openlibrary, google-books, archive.org, visible).
    ``raw`` keeps the evidence trail (selector -> raw string) for audits.
    """

    author: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    language: str | None = None
    isbn: str | None = None
    page_count: int | None = None
    confidence: str = "low"
    source: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Page:
    title: str
    links: list[tuple[str, str]]
    # Access, viewer, lending, catalogue and source-record links are retained
    # for export but are never put in the download queue.
    metadata_links: list[tuple[str, str]] = field(default_factory=list)
    meta: BookMeta | None = None

    @property
    def asset_links(self):
        """Explicit name for the legacy ``links`` asset channel."""
        return self.links

    @property
    def access_links(self):
        return self.metadata_links


_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

_AUTHOR_DENY = {
    "admin", "administrator", "editor", "staff", "team", "support",
    "kindlebangla", "banglabook", "bdebooks", "granthagara", "amarboi",
    "unknown", "unknown author", "n/a", "none",
}

_LABEL_MAP = {
    # normalized label -> canonical field
    "লেখক": "author", "রচয়িতা": "author", "রচয়িতা": "author",
    "author": "author", "authors": "author", "writer": "author",
    "writers": "author", "by": "author", "সম্পাদক": "author",
    "editor": "author",
    "প্রকাশক": "publisher", "প্রকাশনী": "publisher",
    "publisher": "publisher", "publication": "publisher",
    "প্রকাশকাল": "published_date", "প্রকাশ": "publisher",
    "সাল": "published_date", "তারিখ": "published_date",
    "published": "published_date", "publication date": "published_date",
    "publish date": "published_date", "year": "published_date",
    "date": "published_date",
    "আইএসবিএন": "isbn", "isbn": "isbn",
    "ভাষা": "language", "language": "language",
    "পৃষ্ঠা": "page_count", "পাতা": "page_count",
    "pages": "page_count", "page count": "page_count",
    "number of pages": "page_count",
}

_META_SELECTORS = {
    # (tag attr, name) -> field, source tag
    ("name", "citation_author"): ("author", "meta"),
    ("name", "citation_authors"): ("author", "meta"),
    ("name", "DC.creator"): ("author", "meta"),
    ("name", "DC.Creator"): ("author", "meta"),
    ("name", "author"): ("author", "meta"),
    ("property", "og:book:author"): ("author", "meta"),
    ("property", "books:author"): ("author", "meta"),
    ("name", "citation_publisher"): ("publisher", "meta"),
    ("name", "DC.publisher"): ("publisher", "meta"),
    ("name", "DC.Publisher"): ("publisher", "meta"),
    ("name", "publisher"): ("publisher", "meta"),
    ("property", "og:book:publisher"): ("publisher", "meta"),
    ("name", "citation_publication_date"): ("published_date", "meta"),
    ("name", "citation_date"): ("published_date", "meta"),
    ("name", "DC.date"): ("published_date", "meta"),
    ("name", "DC.Date"): ("published_date", "meta"),
    ("property", "article:published_time"): ("published_date", "meta"),
    ("property", "og:book:release_date"): ("published_date", "meta"),
    ("property", "book:release_date"): ("published_date", "meta"),
    ("name", "citation_isbn"): ("isbn", "meta"),
    ("name", "DC.identifier"): ("isbn", "meta"),
    ("property", "og:book:isbn"): ("isbn", "meta"),
    ("name", "citation_language"): ("language", "meta"),
    ("name", "DC.language"): ("language", "meta"),
    ("name", "DC.Language"): ("language", "meta"),
}


def _clean_text(value):
    if value is None:
        return None
    text = unicodedata.normalize("NFC", str(value)).translate(_BN_DIGITS)
    text = re.sub(r"\s+", " ", text).strip(" \t\n\r\f\v:;-|–—")
    return text or None


def _valid_author(name):
    cleaned = _clean_text(name)
    if not cleaned or len(cleaned) < 3 or len(cleaned) > 120:
        return None
    if cleaned.lower() in _AUTHOR_DENY:
        return None
    if re.fullmatch(r"[\d\W_]+", cleaned):
        return None
    return cleaned


def normalize_language(value):
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    low = cleaned.lower()
    if low in {"bn", "ben", "bengali", "bangla", "বাংলা"}:
        return "ben"
    if low in {"en", "eng", "english", "ইংরেজি"}:
        return "eng"
    if re.fullmatch(r"[a-z]{2,3}", low):
        return low
    return cleaned


def normalize_isbn(value):
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    compact = re.sub(r"[^0-9Xx]", "", cleaned).upper()
    if len(compact) == 10:
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(compact[:9]))
        check = "X" if (11 - total % 11) % 11 == 10 else str((11 - total % 11) % 11)
        if compact[9] != check or not re.fullmatch(r"\d{9}[\dX]", compact):
            return None
        return compact
    if len(compact) == 13 and compact.isdigit():
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(compact[:12]))
        if str((10 - total % 10) % 10) != compact[12]:
            return None
        return compact
    return None


def normalize_date(value):
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    match = re.search(r"(19|20)\d{2}(?:-(0?[1-9]|1[0-2])(?:-([0-2]?\d|3[01]))?)?", cleaned)
    if match:
        year, month, day = match.group(0).split("-")[:1], None, None
        parts = match.group(0).split("-")
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return f"{parts[0]}-{int(parts[1]):02d}"
        return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    if re.fullmatch(r"(19|20)\d{2}", cleaned):
        return cleaned
    return None


def normalize_page_count(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 10000 else None
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    match = re.search(r"\d{1,5}", cleaned.replace(",", ""))
    if not match:
        return None
    count = int(match.group(0))
    return count if 1 <= count <= 10000 else None


def _person_name(value):
    if isinstance(value, dict):
        for key in ("name", "@id"):
            if value.get(key):
                return _clean_text(str(value[key]).rsplit("/", 1)[-1].replace("_", " "))
        return None
    return _clean_text(value)


def _split_people(value):
    if isinstance(value, list):
        names = []
        for item in value:
            name = _person_name(item)
            if _valid_author(name):
                names.append(name)
        return names
    name = _person_name(value)
    if not _valid_author(name):
        return []
    # Respect multi-author strings joined by common separators.
    parts = re.split(r"\s*(?:,|;|&| and | এবং | ও )\s*", name)
    return [p for p in (_valid_author(p) for p in parts) if p]


def _evidence():
    return {}


def _record(evidence, field, value, source):
    if value is None:
        return
    evidence.setdefault(field, []).append((value, source))


def _jsonld_candidates(soup):
    for tag in soup.select('script[type="application/ld+json"]'):
        text = tag.string or tag.get_text()
        if not text or not text.strip():
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node and isinstance(node["@graph"], list):
                    stack.extend(node["@graph"])
                node_type = node.get("@type")
                types = {node_type} if isinstance(node_type, str) else set(node_type or [])
                if types & {"Book", "CreativeWork", "PublicationVolume", "TextBook"}:
                    yield node
                for key in ("hasPart", "mainEntity", "itemListElement"):
                    if isinstance(node.get(key), (dict, list)):
                        stack.append(node[key])


def _extract_jsonld(soup, evidence):
    for node in _jsonld_candidates(soup):
        for raw in node.get("author", []) if isinstance(node.get("author"), list) else [node.get("author")]:
            for name in _split_people(raw):
                _record(evidence, "author", name, "json-ld")
        pub = node.get("publisher")
        pub_name = _person_name(pub) if not isinstance(pub, list) else None
        if pub_name:
            _record(evidence, "publisher", pub_name, "json-ld")
        date = normalize_date(node.get("datePublished") or node.get("dateCreated"))
        if date:
            _record(evidence, "published_date", date, "json-ld")
        lang = normalize_language(node.get("inLanguage"))
        if lang:
            _record(evidence, "language", lang, "json-ld")
        isbn = node.get("isbn")
        if isinstance(isbn, list):
            isbn = isbn[0] if isbn else None
        isbn = normalize_isbn(isbn)
        if isbn:
            _record(evidence, "isbn", isbn, "json-ld")
        count = normalize_page_count(node.get("numberOfPages"))
        if count:
            _record(evidence, "page_count", count, "json-ld")


def _extract_meta_tags(soup, evidence):
    for tag in soup.select("meta[name], meta[property]"):
        for attr in ("name", "property"):
            key = (attr, (tag.get(attr) or "").strip())
            if key not in _META_SELECTORS:
                continue
            field, _ = _META_SELECTORS[key]
            content = (tag.get("content") or "").strip()
            if not content:
                continue
            if field == "author":
                for name in _split_people(content):
                    _record(evidence, "author", name, "meta")
            elif field == "publisher":
                cleaned = _clean_text(content)
                if cleaned:
                    _record(evidence, "publisher", cleaned, "meta")
            elif field == "published_date":
                date = normalize_date(content)
                if date:
                    _record(evidence, "published_date", date, "meta")
            elif field == "isbn":
                isbn = normalize_isbn(content)
                if isbn:
                    _record(evidence, "isbn", isbn, "meta")
            elif field == "language":
                lang = normalize_language(content)
                if lang:
                    _record(evidence, "language", lang, "meta")


def _extract_visible(soup, evidence):
    seen_pairs = set()
    for row in soup.select("table tr, dl, ul, ol, div"):
        cells = row.select("th, td, dt, dd, li, span, div, p")
        if len(cells) < 2:
            continue
        # Only consider compact label/value neighbours, not whole articles.
        for label_tag, value_tag in zip(cells, cells[1:]):
            label = _clean_text(label_tag.get_text(" ", strip=True))
            if not label or len(label) > 30:
                continue
            field = _LABEL_MAP.get(label.lower())
            if not field:
                continue
            # Avoid treating a container row that repeats the whole article.
            value_text = _clean_text(value_tag.get_text(" ", strip=True))
            if not value_text or len(value_text) > 200:
                continue
            pair = (field, value_text)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if field == "author":
                for name in _split_people(value_text):
                    _record(evidence, "author", name, "visible")
            elif field == "publisher":
                _record(evidence, "publisher", value_text, "visible")
            elif field == "published_date":
                date = normalize_date(value_text)
                if date:
                    _record(evidence, "published_date", date, "visible")
            elif field == "isbn":
                isbn = normalize_isbn(value_text)
                if isbn:
                    _record(evidence, "isbn", isbn, "visible")
            elif field == "language":
                lang = normalize_language(value_text)
                if lang:
                    _record(evidence, "language", lang, "visible")
            elif field == "page_count":
                count = normalize_page_count(value_text)
                if count:
                    _record(evidence, "page_count", count, "visible")


def _decide(evidence):
    """Pick a value per field; high confidence needs two sources to agree."""
    result, raw, per_field_source = {}, {}, {}
    for field, votes in evidence.items():
        counts = {}
        for value, source in votes:
            counts.setdefault(value, set()).add(source)
        # Most-agreed value wins; ties prefer structured sources.
        best = sorted(
            counts.items(),
            key=lambda item: (
                -len(item[1]),
                -sum(1 for v, s in votes if v == item[0] and s in {"json-ld", "meta", "api"}),
                str(item[0]),
            ),
        )[0]
        value, sources = best
        structured = len([s for s in sources if s in {"json-ld", "meta", "api"}]) >= 1
        if len(sources) >= 2:
            confidence = "high"
        elif structured:
            confidence = "medium"
        else:
            confidence = "low"
        result[field] = (value, confidence, "+".join(sorted(sources)))
        raw[field] = [{"value": str(v), "source": s} for v, s in votes]
    return result, raw


def extract_meta(html, url=""):
    """Extract typed metadata from detail HTML with per-field provenance."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return BookMeta()
    evidence = _evidence()
    _extract_jsonld(soup, evidence)
    _extract_meta_tags(soup, evidence)
    _extract_visible(soup, evidence)
    if not evidence:
        return BookMeta()
    decided, raw = _decide(evidence)
    kwargs = {}
    confidences = []
    sources = []
    for field in ("author", "publisher", "published_date", "language", "isbn", "page_count"):
        if field in decided:
            value, confidence, source = decided[field]
            kwargs[field] = value
            confidences.append(confidence)
            sources.append(source)
    overall = "low"
    if confidences:
        if any(c == "high" for c in confidences) and sum(1 for c in confidences if c in {"high", "medium"}) >= 2:
            overall = "high"
        elif any(c in {"high", "medium"} for c in confidences):
            overall = "medium"
    # A validated ISBN plus one structured field is strong corroboration.
    if kwargs.get("isbn") and sum(1 for c in confidences if c in {"high", "medium"}) >= 2:
        overall = "high"
    return BookMeta(
        confidence=overall,
        source="+".join(sorted(set(sources))) or None,
        raw=raw,
        **kwargs,
    )
