"""Authority resolution for scraped metadata (Open Library, Google Books, Wikidata).

All lookups go through the polite pipeline ``Client`` (per-host delay,
retries) and are cached in the ``authority_cache`` table so re-runs and
tests never refetch. Every network failure returns ``None`` — the caller
keeps the scraped value and marks confidence low instead of misattributing.
"""

import json
import re
import time
import unicodedata
from urllib.parse import quote

from .site_utils import BookMeta, normalize_isbn

OL_ISBN_URL = "https://openlibrary.org/isbn/{isbn}.json"
OL_SEARCH_URL = (
    "https://openlibrary.org/search.json?title={title}&author={author}"
    "&language={language}&limit=5&fields=key,title,author_name,publisher,"
    "first_publish_year,language,isbn,cover_i"
)
GOOGLE_ISBN_URL = "https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
GOOGLE_SEARCH_URL = (
    "https://www.googleapis.com/books/v1/volumes?q=intitle:{title}+inauthor:{author}"
    "&langRestrict={language}&maxResults=5"
)
WIKIDATA_SEARCH_URL = (
    "https://www.wikidata.org/w/api.php?action=wbsearchentities&search={name}"
    "&language={language}&format=json&limit=5"
)


def _norm(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text)).lower()
    return re.sub(r"[^0-9a-z\u0980-\u09ff]+", " ", text).strip()


def titles_match(a, b):
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # One contains the other (subtitle/series debris on source pages).
    short, long = sorted((na, nb), key=len)
    return len(short) >= 8 and short in long


def authors_match(a, b):
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = sorted((na, nb), key=len)
    return len(short) >= 5 and short in long


def _get_json(client, url):
    try:
        with client.get(url) as response:
            return json.loads(response.text)
    except Exception:
        return None


def _cache_get(db, key):
    try:
        row = db.execute("SELECT payload FROM authority_cache WHERE key=?", (key,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def _cache_set(db, key, payload):
    try:
        db.execute(
            "INSERT OR REPLACE INTO authority_cache(key,payload,fetched_at) VALUES (?,?,?)",
            (key, json.dumps(payload, ensure_ascii=False), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
    except Exception:
        pass


def _cached_json(client, db, key, url):
    if db is not None:
        hit = _cache_get(db, key)
        if hit is not None:
            return hit
    payload = _get_json(client, url)
    if payload is not None and db is not None:
        try:
            with db:
                _cache_set(db, key, payload)
        except Exception:
            pass
    return payload


def lookup_openlibrary_isbn(client, isbn, db=None):
    isbn = normalize_isbn(isbn)
    if not isbn:
        return None
    payload = _cached_json(client, db, f"ol:isbn:{isbn}", OL_ISBN_URL.format(isbn=isbn))
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    fragment = _ol_edition_fragment(payload)
    if fragment is not None and not fragment.get("author"):
        name = _ol_first_author_name(client, db, payload)
        if name:
            fragment["author"] = name
    return fragment


def _ol_edition_fragment(payload):
    authors = []
    for entry in payload.get("authors", []) or []:
        name = (entry.get("name") or "").strip() if isinstance(entry, dict) else ""
        if name:
            authors.append(name)
    publishers = payload.get("publishers") or payload.get("publish_places") or []
    fragment = {"source": "openlibrary"}
    if authors:
        fragment["author"] = authors[0]
    if publishers and isinstance(publishers, list):
        fragment["publisher"] = str(publishers[0])
    date = str(payload.get("publish_date") or payload.get("publish_year") or "").strip()
    if date:
        fragment["published_date"] = date
    languages = payload.get("languages") or []
    if languages and isinstance(languages, list):
        key = (languages[0].get("key") or "") if isinstance(languages[0], dict) else str(languages[0])
        fragment["language"] = key.rsplit("/", 1)[-1] or None
    identifiers = payload.get("identifiers") or {}
    for values in (identifiers.get("isbn_13") or [], identifiers.get("isbn_10") or []):
        isbn = normalize_isbn(values)
        if isbn:
            fragment["isbn"] = isbn
            break
    pages = payload.get("number_of_pages")
    if isinstance(pages, int) and 1 <= pages <= 10000:
        fragment["page_count"] = pages
    return fragment or None


def _name_from_author_entries(client, db, entries):
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("name"):
            return str(entry["name"]).strip()
        ref = entry.get("author") if isinstance(entry.get("author"), dict) else entry
        key = (ref.get("key") or ref.get("url") or "") if isinstance(ref, dict) else ""
        if "/authors/" not in key:
            continue
        olid = key.rstrip("/").rsplit("/", 1)[-1]
        doc = _cached_json(client, db, f"ol:author:{olid}", f"https://openlibrary.org/authors/{olid}.json")
        if isinstance(doc, dict) and doc.get("name"):
            return str(doc["name"]).strip()
    return None


def _ol_first_author_name(client, db, payload):
    """Resolve the edition's first author.

    OL editions carry authors as inline names, key-only refs, or not at
    all — in the last case the names live on the linked work document.
    """
    name = _name_from_author_entries(client, db, payload.get("authors"))
    if name:
        return name
    works = payload.get("works") or []
    if not isinstance(works, list):
        return None
    for work in works[:2]:
        key = work.get("key") if isinstance(work, dict) else None
        if not key:
            continue
        wid = key.rstrip("/").rsplit("/", 1)[-1]
        doc = _cached_json(client, db, f"ol:work:{wid}", f"https://openlibrary.org/works/{wid}.json")
        if not isinstance(doc, dict):
            continue
        name = _name_from_author_entries(client, db, doc.get("authors"))
        if name:
            return name
    return None


def lookup_google_isbn(client, isbn, db=None):
    isbn = normalize_isbn(isbn)
    if not isbn:
        return None
    payload = _cached_json(client, db, f"gb:isbn:{isbn}", GOOGLE_ISBN_URL.format(isbn=isbn))
    items = (payload or {}).get("items") if isinstance(payload, dict) else None
    if not items:
        return None
    return _google_volume_fragment(items[0])


def _google_volume_fragment(item):
    info = (item or {}).get("volumeInfo", {}) if isinstance(item, dict) else {}
    if not isinstance(info, dict):
        return None
    fragment = {"source": "google-books", "google_id": item.get("id")}
    authors = info.get("authors") or []
    if authors:
        fragment["author"] = str(authors[0])
    if info.get("publisher"):
        fragment["publisher"] = str(info["publisher"])
    if info.get("publishedDate"):
        fragment["published_date"] = str(info["publishedDate"])
    if info.get("language"):
        fragment["language"] = str(info["language"])
    for ident in info.get("industryIdentifiers") or []:
        isbn = normalize_isbn((ident or {}).get("identifier"))
        if isbn:
            fragment["isbn"] = isbn
            break
    if isinstance(info.get("pageCount"), int):
        fragment["page_count"] = info["pageCount"]
    return fragment


def search_openlibrary(client, title, author="", language="ben", db=None):
    if not title:
        return None
    url = OL_SEARCH_URL.format(
        title=quote(title), author=quote(author or ""), language=language or "ben",
    )
    payload = _cached_json(client, db, f"ol:search:{title}|{author}|{language}", url)
    docs = (payload or {}).get("docs") if isinstance(payload, dict) else None
    if not docs:
        return None
    for doc in docs:
        names = doc.get("author_name") or []
        candidate = str(names[0]) if names else ""
        if author and candidate and not authors_match(author, candidate):
            continue
        if not titles_match(title, doc.get("title") or ""):
            continue
        fragment = {"source": "openlibrary"}
        if candidate:
            fragment["author"] = candidate
        publishers = doc.get("publisher") or []
        if publishers:
            fragment["publisher"] = str(publishers[0])
        if doc.get("first_publish_year"):
            fragment["published_date"] = str(doc["first_publish_year"])
        languages = doc.get("language") or []
        if languages:
            fragment["language"] = str(languages[0])
        for raw in doc.get("isbn") or []:
            isbn = normalize_isbn(raw)
            if isbn:
                fragment["isbn"] = isbn
                break
        return fragment
    return None


def search_google(client, title, author="", language="bn", db=None):
    if not title:
        return None
    url = GOOGLE_SEARCH_URL.format(
        title=quote(title), author=quote(author or ""), language=language or "bn",
    )
    payload = _cached_json(client, db, f"gb:search:{title}|{author}|{language}", url)
    items = (payload or {}).get("items") if isinstance(payload, dict) else None
    if not items:
        return None
    for item in items:
        fragment = _google_volume_fragment(item)
        if not fragment:
            continue
        info = item.get("volumeInfo", {})
        if not titles_match(title, info.get("title") or ""):
            continue
        if author and fragment.get("author") and not authors_match(author, fragment["author"]):
            continue
        if fragment.get("language") and language and fragment["language"] != language:
            # Google sometimes returns another edition; keep looking.
            continue
        return fragment
    return None


def lookup_wikidata_author(client, name, db=None):
    """Return (qid, label) for an exact-ish author match, else None."""
    if not name or len(name.strip()) < 3:
        return None
    for language in ("bn", "en"):
        url = WIKIDATA_SEARCH_URL.format(name=quote(name), language=language)
        payload = _cached_json(client, db, f"wd:author:{language}:{name}", url)
        entries = (payload or {}).get("search") if isinstance(payload, dict) else None
        for entry in entries or []:
            label = entry.get("label") or ""
            aliases = " ".join(entry.get("aliases") or [])
            if _norm(label) == _norm(name) or _norm(name) in _norm(aliases):
                return entry.get("id"), label
    return None


def enrich_metadata(base, title, client=None, db=None):
    """Fill gaps in scraped metadata via authority APIs (high-confidence only).

    ISBN-exact wins; fuzzy title/author requires Open Library AND Google
    Books to agree on the author. Returns (BookMeta, applied_source).
    Never raises on network failure.
    """
    base = base or BookMeta()
    if client is None:
        return base, base.source or "scraped"
    fragments = []
    try:
        if base.isbn:
            for lookup in (lookup_openlibrary_isbn, lookup_google_isbn):
                fragment = lookup(client, base.isbn, db)
                if fragment:
                    fragments.append(fragment)
        elif title or base.author:
            ol = search_openlibrary(client, title, base.author or "", "ben", db)
            gb = search_google(client, title, base.author or "", "bn", db)
            if ol and gb:
                if (ol.get("author") and gb.get("author")
                        and not authors_match(ol["author"], gb["author"])):
                    pass  # authorities disagree: refuse to assign
                else:
                    fragments.extend([ol, gb])
            else:
                # Single authority is not enough for a fuzzy match.
                pass
    except Exception:
        fragments = []
    if not fragments:
        return base, base.source or "scraped"
    merged = {}
    for fragment in fragments:
        for key in ("author", "publisher", "published_date", "language", "isbn", "page_count"):
            if merged.get(key) is None and fragment.get(key) is not None:
                merged[key] = fragment[key]
    sources = "+".join(sorted({f.get("source", "?") for f in fragments}))
    qid = None
    try:
        if merged.get("author"):
            found = lookup_wikidata_author(client, merged["author"], db)
            if found:
                qid, _label = found
    except Exception:
        qid = None
    raw = dict(base.raw or {})
    raw["authority"] = {"sources": sources, "qid": qid,
                        "fragments": [{k: v for k, v in f.items()} for f in fragments]}
    enriched = BookMeta(
        author=merged.get("author") or base.author,
        publisher=merged.get("publisher") or base.publisher,
        published_date=merged.get("published_date") or base.published_date,
        language=merged.get("language") or base.language,
        isbn=merged.get("isbn") or base.isbn,
        page_count=merged.get("page_count") or base.page_count,
        confidence="high" if base.isbn or len(fragments) >= 2 else base.confidence,
        source=sources,
        raw=raw,
    )
    return enriched, sources
