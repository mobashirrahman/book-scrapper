"""Shared site parsing primitives used by the built-in adapters."""

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
class Page:
    title: str
    links: list[tuple[str, str]]
    # Access, viewer, lending, catalogue and source-record links are retained
    # for export but are never put in the download queue.
    metadata_links: list[tuple[str, str]] = field(default_factory=list)

    @property
    def asset_links(self):
        """Explicit name for the legacy ``links`` asset channel."""
        return self.links

    @property
    def access_links(self):
        return self.metadata_links
