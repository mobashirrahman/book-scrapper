"""Small, optional adapter for public MEGA file links.

The third-party ``mega.py`` package is intentionally imported only when a
MEGA download is attempted.  This keeps discovery and all other providers
usable in installations that do not install the optional extra.
"""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


MEGA_HOSTS = frozenset({"mega.nz", "mega.co.nz", "www.mega.nz", "www.mega.co.nz"})


class MegaError(ValueError):
    """A safe, normalized MEGA error suitable for queue status and logs."""


class MegaUnavailable(MegaError):
    """The optional MEGA dependency is not installed or cannot be imported."""


@dataclass(frozen=True)
class MegaDescriptor:
    """The validated public file information needed by the downloader."""

    url: str
    file_id: str
    key: str
    provider: str = "mega"


def is_mega_url(url):
    return (urlsplit(url).hostname or "").lower() in MEGA_HOSTS


def redact_mega_url(url):
    """Return a display-safe MEGA URL with its decryption key removed."""
    parts = urlsplit(url)
    if not is_mega_url(url) or not parts.fragment:
        return url
    fragment = parts.fragment
    if fragment.startswith("!"):
        pieces = fragment[1:].split("!", 1)
        fragment = "!" + pieces[0] + "!<redacted>"
    else:
        fragment = "<redacted>"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, fragment))


def parse_mega_url(url):
    """Parse a public MEGA file URL, rejecting folders and account links."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in MEGA_HOSTS:
        raise MegaError("Not a MEGA URL")

    path = parts.path.rstrip("/")
    fragment = parts.fragment
    file_id = None
    key = None
    if path.startswith("/file/"):
        components = path.split("/")
        if len(components) != 3 or not components[2]:
            raise MegaError("MEGA folders and account links are not supported")
        file_id = components[2]
        key = fragment
    elif path in {"", "/"} and fragment.startswith("!"):
        legacy = fragment[1:].split("!", 1)
        if len(legacy) == 2:
            file_id, key = legacy
    elif "/folder/" in path or "/collection/" in path or path:
        raise MegaError("MEGA folders and account links are not supported")

    if not file_id or not key:
        raise MegaError("MEGA file link has no decryption key")
    if "!" in key:
        raise MegaError("Malformed MEGA file link")
    return MegaDescriptor(url=url, file_id=file_id, key=key)


def _load_client():
    try:
        from mega import Mega
    except ImportError as exc:
        raise MegaUnavailable("MEGA support is optional; install with 'pip install -e .[mega]'") from exc
    return Mega().login()


def _safe_exception_message(descriptor, exc):
    message = str(exc) or type(exc).__name__
    message = message.replace(descriptor.url, redact_mega_url(descriptor.url))
    message = message.replace(descriptor.key, "<redacted>")
    return message


def download(descriptor, destination, client_factory=None):
    """Decrypt a public file into ``destination`` using an anonymous session."""
    if not isinstance(descriptor, MegaDescriptor):
        raise MegaError("Invalid MEGA download descriptor")
    destination = Path(destination)
    try:
        client = client_factory() if client_factory else _load_client()
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = client.download_url(
            descriptor.url,
            dest_path=str(destination.parent),
            dest_filename=destination.name,
        )
        # Accept compatible clients that return their output path without
        # honoring dest_filename, while keeping the pipeline's known temp path.
        if not destination.exists() and result:
            returned = Path(result)
            if returned.exists():
                returned.replace(destination)
        if not destination.exists():
            raise MegaError("MEGA downloader produced no output file")
        return destination
    except MegaError:
        raise
    except Exception as exc:
        raise MegaError("MEGA download failed: " + _safe_exception_message(descriptor, exc)) from exc
