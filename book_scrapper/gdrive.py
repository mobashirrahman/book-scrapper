"""Optional Google Drive folder support for publicly shared folders.

KindleBangla's per-book ``/download/`` endpoints redirect to Google Drive
*folders*, which have no direct file URL. This module resolves such a folder
to a single book file through the official Drive API v3.

Deliberate limits (mirroring ``mega.py``):

* Only the official API is used — Drive HTML/sharing pages are never
  scraped, and private or restricted items are never attempted.
* Public folders need only a browser API key (Drive API enabled): export
  ``GOOGLE_DRIVE_API_KEY``. No OAuth credentials are handled here.
* A folder's EPUB is picked first (covers/images are never candidates);
  other book files are only a fallback when no EPUB is present.
  Empty folders and folders with several same-priority files raise
  ``Unsupported`` so the pipeline records them instead of guessing —
  those need a human pick.
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


def _default_service_factory(api_key):
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise Unsupported(
            "Google Drive support needs the google client libraries "
            "(pip install 'google-api-python-client google-auth-httplib2 google-auth-oauthlib')"
        ) from exc
    return build("drive", "v3", developerKey=api_key)


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
        response = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="files(id, name, mimeType)",
                pageSize=100,
            )
            .execute()
        )
    except Unsupported:
        raise
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in (403, 404):
            raise Unsupported(f"Drive folder is not publicly shared: {folder_id}") from exc
        raise DriveError(f"Drive API error for folder {folder_id}: {exc}") from exc
    entries = response.get("files", [])
    # Images/covers (jpg/png/...) and anything else non-book is never a
    # candidate. EPUB wins; other book files are only a fallback.
    epubs = [e for e in entries if (e.get("name") or "").lower().endswith(".epub")]
    others = [
        e for e in entries
        if (e.get("name") or "").lower().endswith(BOOK_EXTENSIONS)
        and not (e.get("name") or "").lower().endswith(".epub")
    ]
    if len(epubs) > 1:
        raise Unsupported(
            f"Drive folder has {len(epubs)} EPUB files and needs a human pick: {folder_id}"
        )
    if len(epubs) == 1:
        return media_url(epubs[0]["id"], key)
    if not others:
        raise Unsupported(f"Drive folder has no public book files: {folder_id}")
    if len(others) > 1:
        raise Unsupported(
            f"Drive folder has {len(others)} book files (no EPUB) and needs a human pick: {folder_id}"
        )
    return media_url(others[0]["id"], key)
