"""Optional Google Drive folder support for publicly shared folders.

KindleBangla's per-book ``/download/`` endpoints redirect to Google Drive
*folders*, which have no direct file URL. This module resolves such a folder
to a single book file through the official Drive API v3.

Deliberate limits (mirroring ``mega.py``):

* Only the official API is used — Drive HTML/sharing pages are never
  scraped, and private or restricted items are never attempted.
* Public folders need only a browser API key (Drive API enabled): export
  ``GOOGLE_DRIVE_API_KEY``. No OAuth credentials are handled here.
* KindleBangla folders bundle the book with EPUB support files (``.opf``,
  ``.jpg`` cover, ``.kfx``) and sometimes a PDF/EPUB pair, so nested
  subfolders are walked and only real book files are candidates. The
  highest-priority format wins (EPUB > PDF > MOBI > AZW3 > FB2 > archives);
  within one format the largest file wins, which skips samples and
  duplicate exports. Empty folders (no book file at any depth) still raise
  ``Unsupported``.
* Without an API key (or the optional google client) every folder resolves
  to ``Unsupported`` with a message saying what to set, so plain crawls keep
  working and ``--retry`` picks the folders up once a key is configured.
"""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .pipeline import Unsupported

DRIVE_HOSTS = frozenset({"drive.google.com", "docs.google.com"})

BOOK_EXTENSIONS = (".pdf", ".epub", ".zip", ".rar", ".mobi", ".fb2", ".7z")

# Preferred order when a folder ships several formats of the same book
# (KindleBangla often has EPUB + PDF or EPUB + KFX).
BOOK_PRIORITY = (".epub", ".pdf", ".mobi", ".azw3", ".fb2", ".zip", ".rar", ".7z")

FOLDER_MIME = "application/vnd.google-apps.folder"
MAX_FOLDER_DEPTH = 3

_FOLDER_RE = re.compile(r"/drive/folders/([^/?#]+)")
_FILE_RE = re.compile(r"/d/([^/]+)")


class DriveError(ValueError):
    """A Drive API failure (transport/quota); the pipeline marks it failed."""


def read_api_key():
    """User's Drive API key: env wins, else a 600-perm config file.

    The key lives outside the repo so background jobs (which do not inherit
    ad-hoc ``export``s) and retries can all read it. ``googleapis.com`` has
    no robots.txt (404 = allow), so these media URLs stay robots-clean even
    though drive.google.com now disallows /uc.
    """
    key = os.environ.get("GOOGLE_DRIVE_API_KEY")
    if key:
        return key
    base = os.environ.get("BOOK_SCRAPPER_CONFIG") or str(Path.home() / ".config" / "book-scrapper")
    try:
        return (Path(base) / "drive_api_key").read_text().strip() or None
    except OSError:
        return None


def parse_drive_folder_id(url):
    """Return the folder id for a Drive folder URL, else None."""
    if (urlsplit(url).hostname or "").lower() not in DRIVE_HOSTS:
        return None
    match = _FOLDER_RE.search(urlsplit(url).path)
    return match.group(1) if match else None


def parse_drive_file_id(url):
    """Return the file id for a Drive file URL (/d/<id> or ?id=), else None."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in DRIVE_HOSTS:
        return None
    match = _FILE_RE.search(parts.path)
    if match:
        return match.group(1)
    return parse_qs(parts.query).get("id", [None])[0]


def media_url(file_id, key):
    """Official bytes endpoint for a public file (no virus-scan interstitial)."""
    return f"https://www.googleapis.com/drive/v3/files/{file_id}?key={key}&alt=media"


def parse_media_file_id(url):
    """Return the file id from a Drive API media URL, else None."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != "www.googleapis.com":
        return None
    match = re.search(r"/drive/v3/files/([^/?#]+)", parts.path)
    return match.group(1) if match else None


def uc_download_url(file_id):
    """Direct download endpoint, used when the API endpoint 403s on a file."""
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def _default_service_factory(api_key):
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise Unsupported(
            "Google Drive support needs the google client libraries "
            "(pip install 'google-api-python-client google-auth-httplib2 google-auth-oauthlib')"
        ) from exc
    return build("drive", "v3", developerKey=api_key)


def _list_children(service, folder_id):
    response = (
        service.files()
        .list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="files(id, name, mimeType, size)",
            pageSize=200,
        )
        .execute()
    )
    return response.get("files", [])


def _book_candidates(service, folder_id, depth=0, seen=None):
    """Collect book files in a folder, descending into subfolders.

    Covers, ``.opf``/``.xml`` sidecars and other support files never match
    ``BOOK_PRIORITY``; ``seen`` guards against folder cycles.
    """
    seen = seen if seen is not None else set()
    if folder_id in seen or depth > MAX_FOLDER_DEPTH:
        return []
    seen.add(folder_id)
    candidates = []
    for entry in _list_children(service, folder_id):
        name = entry.get("name") or ""
        if entry.get("mimeType") == FOLDER_MIME:
            candidates.extend(_book_candidates(service, entry["id"], depth + 1, seen))
            continue
        suffix = Path(name).suffix.lower()
        if suffix not in BOOK_PRIORITY:
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        candidates.append((BOOK_PRIORITY.index(suffix), -size, name, entry["id"]))
    return candidates


def resolve_folder_file(folder_id, api_key=None, service_factory=None):
    """Resolve a public Drive folder to one direct download URL.

    ``service_factory`` is injectable for offline tests; it receives the API
    key and must return an object with
    ``files().list(...).execute()`` like the Drive v3 client.
    """
    key = api_key or read_api_key()
    if not key:
        raise Unsupported(
            "Google Drive folder listing needs GOOGLE_DRIVE_API_KEY "
            "(a browser API key with Drive API enabled for public folders)"
        )
    try:
        service = service_factory(key) if service_factory else _default_service_factory(key)
        candidates = _book_candidates(service, folder_id)
    except Unsupported:
        raise
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in (403, 404):
            raise Unsupported(f"Drive folder is not publicly shared: {folder_id}") from exc
        raise DriveError(f"Drive API error for folder {folder_id}: {exc}") from exc
    if not candidates:
        raise Unsupported(f"Drive folder has no public book files: {folder_id}")
    # Priority first (EPUB wins), then largest file (skips samples/dupes).
    _, _, _, file_id = min(candidates)
    return media_url(file_id, key)
