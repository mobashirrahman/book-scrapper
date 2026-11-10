"""Deterministic EPUB curation for scraped/Telegram books.

Removes source branding, injected promo pages and every hyperlink, rewrites
the package metadata to neutral values (publisher/source = OpenBanglaBooks),
and rebuilds the archive deterministically so the same input always produces
the same SHA-256.

    python -m book_scrapper.curate --input data/telegram/bongboi \
        --out data/telegram/bongboi/curated [--limit 50] [--force]

Output is one ``<sha256>.epub`` plus ``<sha256>.json`` per book. The sidecar
records the original path/hash and everything that was changed, so imports and
audits never have to re-open the source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from bs4 import BeautifulSoup, NavigableString, Tag

PUBLISHER = "OpenBanglaBooks"
SOURCE = "OpenBanglaBooks.com"
FIXED_MODIFIED = "2020-01-01T00:00:00Z"
UUID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# Text that only appears because a scraper/channel injected it. Matching is
# case-insensitive and tolerant of spacing.
PROMO_RE = re.compile(
    r"(bongboi|t\.me/|telegram|টেলি\s*বই|টেলিগ্রাম|ebanglalibrary|"
    r"a project to scrape|auto[\s-]*generated|made with|আরও বই|"
    r"শেষ পৃষ্ঠা|contributor|disclaimer|সবাই সুস্থ|করোনার প্রকোপ|"
    r"please buy the hardcopy|hardcopy|recommended ebook reader|ebook reader|teleboi|tele\s*boi)",
    re.IGNORECASE,
)
STRONG_PROMO_RE = re.compile(
    r"(bongboi|t\.me/|টেলি\s*বই|ebanglalibrary|telegram|recommended ebook reader)",
    re.IGNORECASE,
)
PROMO_IMAGE_RE = re.compile(r"(logo|banner|telegram|bongboi|channel|watermark)", re.IGNORECASE)

DROP_METADATA = {
    "dc:contributor",
    "dc:description",
    "dc:date",
    "dc:rights",
    "dc:source",
    "dc:identifier",
    "dc:publisher",
    "dc:language",
    "dc:relation",
}
DROP_META_NAMES = {"calibre:timestamp", "calibre:series", "calibre:series_index", "Sigil version"}
TITLE_NOISE_RE = re.compile(
    r"\s*[-–—|:]\s*(টেলি\s*বই|tele\s*boi|bongboi|ebanglalibrary|openbanglabooks).*$",
    re.IGNORECASE,
)
AUTHOR_PREFIX_RE = re.compile(
    r"^(?:লেখক|অনুবাদক?|সম্পাদক|translated\s+by|translation|by)\s*[:：-]\s*",
    re.IGNORECASE,
)
AUTHOR_DENY = {
    "admin", "administrator", "editor", "staff", "team", "support",
    "bongboi", "tele boi", "টেলি বই", "ebanglalibrary", "unknown", "n/a",
}


@dataclass
class Report:
    source: str
    source_sha256: str
    output: str | None = None
    output_sha256: str | None = None
    title: str | None = None
    author: str | None = None
    removed_files: list[str] = field(default_factory=list)
    removed_elements: int = 0
    removed_links: int = 0
    removed_images: int = 0
    metadata: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "source_sha256": self.source_sha256,
            "output": self.output,
            "output_sha256": self.output_sha256,
            "title": self.title,
            "author": self.author,
            "publisher": PUBLISHER,
            "source_name": SOURCE,
            "language": "bn",
            "removed": {
                "files": self.removed_files,
                "elements": self.removed_elements,
                "links": self.removed_links,
                "images": self.removed_images,
                "metadata": self.metadata,
            },
            "warnings": self.warnings,
        }


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    text = unicodedata.normalize("NFC", str(value))
    text = re.sub(r"\s+", " ", text).strip(" \t\n\r\f\v:;-|–—")
    return text or None


def _looks_bengali(text: str) -> bool:
    return bool(re.search(r"[\u0980-\u09ff]", text or ""))


def _valid_author(name: str | None) -> str | None:
    clean = _clean_text(name)
    if not clean:
        return None
    clean = AUTHOR_PREFIX_RE.sub("", clean).strip()
    # OPF creators sometimes carry the series first: "রবীন্দ্র রচনাবলী – রবীন্দ্রনাথ ঠাকুর".
    if " – " in clean or " — " in clean:
        parts = [part.strip() for part in re.split(r"[–—]", clean) if part.strip()]
        if len(parts) > 1:
            clean = parts[-1]
    if not clean or not 3 <= len(clean) <= 120:
        return None
    if clean.lower() in AUTHOR_DENY or PROMO_RE.search(clean):
        return None
    if re.fullmatch(r"[\d\W_]+", clean):
        return None
    return clean


def _clean_title(value: str | None) -> str | None:
    clean = _clean_text(value)
    if not clean:
        return None
    clean = TITLE_NOISE_RE.sub("", clean)
    clean = re.sub(r"@[\w_]+", "", clean)
    clean = re.sub(r"\b(?:bongboi|teleboi|telegram|ebanglalibrary)\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" -–—|:")
    return clean or None


def _fallback_from_filename(path: Path) -> tuple[str | None, str | None]:
    """Telegram naming is ``<message id>_<author> <title>`` (best effort)."""
    stem = path.stem
    stem = re.sub(r"^\d+[_\-.\s]+", "", stem)
    stem = stem.replace("_", " ").strip()
    if not stem:
        return None, None
    # Authors contain spaces too; this is only a fallback when the OPF is empty.
    return stem, None


def _scrub_opf(soup: BeautifulSoup, digest: str, path: Path, report: Report) -> tuple[str | None, str | None]:
    metadata = soup.find("metadata")
    if metadata is None:
        raise ValueError("EPF has no <metadata> element")

    title = _clean_title(metadata.find("dc:title").get_text() if metadata.find("dc:title") else None)
    authors = [
        name
        for tag in list(metadata.find_all("dc:creator"))
        for name in [_valid_author(tag.get_text())]
        if name
    ]
    if not title:
        title, _ = _fallback_from_filename(path)
        title = _clean_title(title)
    if not title:
        raise ValueError("no usable title")

    for tag in list(metadata.find_all(DROP_METADATA)):
        name = tag.name
        tag.decompose()
        report.metadata.append(f"dropped {name}")

    title_tag = metadata.find("dc:title")
    if title_tag is None:
        title_tag = soup.new_tag("dc:title")
        metadata.append(title_tag)
    title_tag.string = title
    for meta in list(metadata.find_all(lambda tag: tag.name and tag.name.split(":")[-1] == "meta")):
        name = (meta.get("name") or "").strip()
        prop = (meta.get("property") or "").strip()
        if (
            name in DROP_META_NAMES
            or name.startswith(("calibre:", "GBS"))
            or prop == "dcterms:modified"
            or PROMO_RE.search(meta.get_text(" ", strip=True) or "")
        ):
            meta.decompose()
            report.metadata.append(f"dropped meta {name or prop or 'promo'}")

    for tag in list(metadata.find_all("dc:creator")):
        tag.decompose()
    for index, author in enumerate(authors or [None], start=1):
        if not author:
            continue
        tag = soup.new_tag("dc:creator")
        tag.string = author
        if index > 1:
            tag["opf:role"] = "aut"
        metadata.append(tag)

    # Deterministic identifier: same source book always yields the same urn.
    identifier = soup.new_tag("dc:identifier", attrs={"id": "pub-id"})
    identifier.string = f"urn:uuid:{uuid.uuid5(UUID_NAMESPACE, digest)}"
    metadata.append(identifier)
    package = soup.find("package")
    if package is not None:
        package["unique-identifier"] = "pub-id"

    publisher = soup.new_tag("dc:publisher")
    publisher.string = PUBLISHER
    metadata.append(publisher)
    source = soup.new_tag("dc:source")
    source.string = SOURCE
    metadata.append(source)
    rights = soup.new_tag("dc:rights")
    rights.string = SOURCE
    metadata.append(rights)
    language = soup.new_tag("dc:language")
    language.string = "bn"
    metadata.append(language)
    modified = soup.new_tag("meta", attrs={"property": "dcterms:modified"})
    modified.string = FIXED_MODIFIED
    metadata.append(modified)
    report.metadata.extend(["publisher", "source", "rights", "language", "identifier", "modified"])

    return title, (authors[0] if authors else None)


def _text_length(soup: BeautifulSoup) -> int:
    return len(re.sub(r"\s+", " ", soup.get_text(" ", strip=True)))


def _drop_promo_page(soup: BeautifulSoup, name: str = "") -> bool:
    text = soup.get_text(" ", strip=True)
    if not text:
        return True
    # Title pages carry the book title/author as well as promo paragraphs; keep
    # them and let the block scrubber remove only the promo parts.
    lower = name.lower()
    if any(part in lower for part in ("cover", "title", "nav", "toc", ".ncx")):
        return False
    strong = len(STRONG_PROMO_RE.findall(text))
    # A small document that only talks about the channel/scraper is a promo page.
    return (
        (_text_length(soup) < 2000 and strong >= 1 and len(PROMO_RE.findall(text)) >= 2)
        or (strong >= 2 and _text_length(soup) < 2500)
    )


def _scrub_document(soup: BeautifulSoup, report: Report) -> None:
    for anchor in list(soup.find_all("a")):
        anchor.unwrap()
        report.removed_links += 1
    for tag in list(soup.find_all(["script", "iframe", "object", "embed", "form"])):
        tag.decompose()
        report.removed_elements += 1
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower().startswith("on"):
                del tag[attr]
    # Remote or promo images never survive; the cover is local and named cover*.
    for image in list(soup.find_all("img")):
        src = image.get("src") or ""
        if src.lower().startswith(("http://", "https://", "//")) or (
            PROMO_IMAGE_RE.search(src) and "cover" not in src.lower()
        ):
            image.decompose()
            report.removed_images += 1

    # Remove promo blocks, bottom-up so parents see their final size.
    for tag in reversed(list(soup.find_all(["p", "div", "section", "h1", "h2", "h3", "h4", "li", "td", "blockquote"]))):
        if tag.name in {"body", "html"} or not tag.parent:
            continue
        text = _clean_text(tag.get_text(" ", strip=True)) or ""
        if not text or len(text) > 600:
            continue
        if PROMO_RE.search(text):
            tag.decompose()
            report.removed_elements += 1

    # Stray promo-only strings (e.g. an unwrapped link label).
    for node in list(soup.find_all(string=True)):
        value = str(node)
        if not PROMO_RE.search(value):
            continue
        stripped = value.strip()
        if not stripped or len(stripped) > 80:
            continue
        if isinstance(node, NavigableString) and node.parent is not None:
            node.extract()
            report.removed_elements += 1


def _scrub_nav(soup: BeautifulSoup, dropped: set[str], base: str, report: Report) -> None:
    def target(href: str | None) -> str | None:
        if not href:
            return None
        clean = href.split("#", 1)[0]
        if not clean:
            return None
        return posixpath.normpath(posixpath.join(base, clean))

    for anchor in list(soup.find_all("a")):
        href = target(anchor.get("href"))
        if href in dropped or PROMO_RE.search(anchor.get_text(" ", strip=True) or ""):
            parent = anchor.find_parent(["li", "navPoint"]) or anchor
            parent.decompose()
            report.removed_elements += 1
    for nav_point in list(soup.find_all("navPoint")):
        content = nav_point.find("content")
        text = nav_point.find("text")
        label = text.get_text(" ", strip=True) if text is not None else ""
        if (content is not None and target(content.get("src")) in dropped) or PROMO_RE.search(label or ""):
            nav_point.decompose()
            report.removed_elements += 1


def _remove_dropped_from_opf(soup: BeautifulSoup, dropped: set[str], base: str, existing: set[str]) -> None:
    removed_ids = set()
    for item in list(soup.find_all("item")):
        href = item.get("href") or ""
        resolved = posixpath.normpath(posixpath.join(base, href.split("#", 1)[0]))
        # Drop removed files and references the source never shipped (broken
        # fonts/covers are common in channel EPUBs and fail EPUB validation).
        if resolved in dropped or href.startswith(("http://", "https://")) or resolved not in existing:
            removed_ids.add(item.get("id"))
            item.decompose()
    if removed_ids:
        for ref in list(soup.find_all("itemref")):
            if ref.get("idref") in removed_ids:
                ref.decompose()
    for meta in list(soup.find_all("meta")):
        if meta.get("name") == "cover" and meta.get("content") not in {i.get("id") for i in soup.find_all("item")}:
            meta.decompose()




def _sanitize_filenames(files: dict[str, bytes], report: Report) -> None:
    """Rename entries whose basename carries channel branding (bongboi.xhtml)."""
    renames: dict[str, str] = {}
    taken = set(files)
    for name in sorted(files):
        base = posixpath.basename(name)
        if not STRONG_PROMO_RE.search(base):
            continue
        clean = STRONG_PROMO_RE.sub("book", base)
        clean = re.sub(r"[-_ .]{2,}", "-", clean).strip("-_ .")
        if not clean or clean == base:
            continue
        directory = posixpath.dirname(name)
        candidate = posixpath.join(directory, clean)
        counter = 2
        while candidate in taken or candidate in renames.values():
            stem, ext = posixpath.splitext(clean)
            candidate = posixpath.join(directory, f"{stem}-{counter}{ext}")
            counter += 1
        renames[name] = candidate
        taken.add(candidate)
    if not renames:
        return
    for old, new in renames.items():
        files[new] = files.pop(old)
        report.metadata.append(f"renamed {posixpath.basename(old)}")
    for name in list(files):
        if not name.lower().endswith((".opf", ".ncx", ".xhtml", ".html", ".htm", ".css")):
            continue
        text = files[name].decode("utf-8", "replace")
        for old, new in renames.items():
            text = text.replace(posixpath.basename(old), posixpath.basename(new))
        files[name] = text.encode("utf-8")


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        order = ["mimetype"] + sorted(name for name in files if name != "mimetype")
        for name in order:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return buffer.getvalue()


def curate_epub(path: Path) -> tuple[bytes, Report]:
    raw = path.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    report = Report(source=path.name, source_sha256=source_sha)

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        files = {name: archive.read(name) for name in names}

    container = BeautifulSoup(files.get("META-INF/container.xml", b""), "xml")
    rootfile = container.find("rootfile")
    if rootfile is None:
        raise ValueError("EPUB has no rootfile")
    opf_path = posixpath.normpath(rootfile.get("full-path", ""))
    opf_dir = posixpath.dirname(opf_path)
    opf = BeautifulSoup(files[opf_path], "xml")
    title, author = _scrub_opf(opf, source_sha, path, report)
    report.title, report.author = title, author

    manifest = opf.find("manifest")
    manifest_base = opf_dir
    documents = []
    if manifest is not None:
        for item in manifest.find_all("item"):
            media = (item.get("media-type") or "").lower()
            href = item.get("href") or ""
            if media in {"application/xhtml+xml", "text/html"} or href.lower().endswith((".xhtml", ".html", ".htm")):
                documents.append(item)

    dropped: set[str] = set()
    removed_assets: set[str] = set()
    manifest_docs: set[str] = set()
    for item in documents:
        href = item.get("href") or ""
        resolved = posixpath.normpath(posixpath.join(manifest_base, href.split("#", 1)[0]))
        if resolved not in files:
            continue
        manifest_docs.add(resolved)
        doc = BeautifulSoup(files[resolved], "xml")
        if _drop_promo_page(doc, resolved):
            dropped.add(resolved)
            report.removed_files.append(resolved)
            continue
        _scrub_document(doc, report)
        if _text_length(doc) < 20:
            dropped.add(resolved)
            report.removed_files.append(resolved)
            continue
        files[resolved] = doc.encode("utf-8")

    # Orphan XHTML (present in the zip but not in the manifest) is not shown by
    # readers and often carries the channel footer; drop it rather than ship it.
    for name in list(files):
        if name in manifest_docs or name in dropped:
            continue
        if name.lower().endswith((".xhtml", ".html", ".htm")):
            del files[name]
            dropped.add(name)
            report.removed_files.append(name)

    # The cover page was scrubbbed above; keep it.
    for name in list(files):
        if name in dropped:
            del files[name]
            continue
        lower = name.lower()
        if PROMO_IMAGE_RE.search(lower) and "cover" not in lower and lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
            del files[name]
            removed_assets.add(name)
            report.removed_images += 1
            continue
        if lower.endswith((".xhtml", ".html", ".htm")):
            continue

    for name in list(files):
        if name == opf_path or name in dropped:
            continue
        # Navigation files get targeted cleanup.
        if name.lower().endswith((".ncx",)) or "nav" in name.lower() or "toc" in name.lower():
            soup = BeautifulSoup(files[name], "xml")
            _scrub_nav(soup, dropped, posixpath.dirname(name), report)
            files[name] = soup.encode("utf-8")

    _remove_dropped_from_opf(opf, dropped | removed_assets, opf_dir, set(files))
    files[opf_path] = opf.encode("utf-8")

    _sanitize_filenames(files, report)
    output = _zip_bytes(files)
    report.output_sha256 = hashlib.sha256(output).hexdigest()
    return output, report


class _TimedOut(Exception):
    pass


def _curate_one(path_str: str, out_str: str, force: bool) -> dict:
    """Process one EPUB in a worker process (files are written atomically)."""
    import signal

    path, out_dir = Path(path_str), Path(out_str)

    def _alarm(_signum, _frame):
        raise _TimedOut("curation exceeded 120s")

    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(120)
    try:
        data, report = curate_epub(path)
    except Exception as exc:  # noqa: BLE001 - reported per book
        return {"status": "failed", "name": path.name, "error": str(exc)}
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    report.output = f"{report.output_sha256}.epub"
    epub_out = out_dir / report.output
    sidecar = out_dir / f"{report.output_sha256}.json"
    if sidecar.exists() and not force:
        return {"status": "skipped", "name": path.name, "output": report.output, "report": report}
    temp = out_dir / f".{report.output}.{os.getpid()}.{uuid.uuid4().hex[:8]}.part"
    temp.write_bytes(data)
    epub_out.unlink(missing_ok=True)
    temp.replace(epub_out)
    sidecar.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "ok",
        "name": path.name,
        "output": report.output,
        "files": len(report.removed_files),
        "links": report.removed_links,
        "elements": report.removed_elements,
        "report": report,
    }


def curate_directory(
    input_dir: Path,
    out_dir: Path,
    limit: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    jobs: int = 1,
) -> list[Report]:
    books = input_dir / "books" if (input_dir / "books").is_dir() else input_dir
    paths = sorted(books.glob("*.epub"))
    if limit is not None:
        paths = paths[:limit]
    out_dir.mkdir(parents=True, exist_ok=True)
    reports: list[Report] = []

    if dry_run:
        for path in paths:
            try:
                _, report = curate_epub(path)
            except Exception as exc:  # noqa: BLE001
                print(f"failed: {path.name}: {exc}", flush=True)
                continue
            print(f"would curate: {path.name} ({len(report.removed_files)} files, {report.removed_links} links)", flush=True)
            reports.append(report)
        return reports

    def handle(result: dict) -> None:
        status = result["status"]
        if status == "ok":
            print(
                f"curated: {result['name']} -> {result['output'][:12]} "
                f"({result['files']} files, {result['links']} links, {result['elements']} elements)",
                flush=True,
            )
            if result.get("report") is not None:
                reports.append(result["report"])
        elif status == "skipped":
            print(f"skipped (curated): {result['name']}", flush=True)
        else:
            print(f"failed: {result['name']}: {result['error']}", flush=True)

    if jobs <= 1:
        for path in paths:
            handle(_curate_one(str(path), str(out_dir), force))
        return reports

    from concurrent.futures import ProcessPoolExecutor, as_completed

    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(_curate_one, str(path), str(out_dir), force): path for path in paths
        }
        for future in as_completed(futures):
            try:
                handle(future.result())
            except Exception as exc:  # noqa: BLE001
                print(f"failed: {futures[future].name}: {exc}", flush=True)
    return reports


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Curate downloaded EPUBs (strip links, branding and injected metadata)")
    parser.add_argument("--input", required=True, help="Directory with EPUBs, or a data dir containing books/")
    parser.add_argument("--out", required=True, help="Output directory for <sha>.epub + <sha>.json")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true", help="Re-curate even if the output sidecar exists")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--jobs", type=int, default=4, help="Worker processes (default 4)")
    args = parser.parse_args(argv)
    reports = curate_directory(Path(args.input), Path(args.out), args.limit, args.force, args.dry_run, args.jobs)
    print(f"done: {len(reports)} book(s) curated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
