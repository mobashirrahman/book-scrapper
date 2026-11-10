"""Offline tests for the Telegram downloader's pure helpers (no network)."""

import asyncio
import json

import pytest

from pathlib import Path

from types import SimpleNamespace

from book_scrapper import telegram_dl as t


class FakeClient:
    """Minimal stand-in for a Telethon client inside scan_and_download."""

    def __init__(self, messages, download=None):
        self._messages = messages
        self._download = download

    async def iter_messages(self, entity):
        for message in self._messages:
            yield message

    async def download_media(self, message, file=None):
        if self._download is not None:
            return self._download(message, file)
        Path(file).write_bytes(b"fake")
        return file


def _args(**overrides):
    defaults = {"ext": ["epub"], "max_mb": 512, "retry": False,
                "dry_run": True, "limit": None}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _messages(count=3):
    return [
        SimpleNamespace(id=i, file=SimpleNamespace(ext=".epub", name=f"book{i}.epub", size=1000),
                        message=f"Book {i} - Author {i}", date=None)
        for i in range(1, count + 1)
    ]


def test_channel_slug_accepts_handle_and_urls():
    assert t.channel_slug("bongboi") == "bongboi"
    assert t.channel_slug("@bongboi") == "bongboi"
    assert t.channel_slug("https://t.me/bongboi") == "bongboi"
    assert t.channel_slug("https://t.me/bongboi/") == "bongboi"
    assert t.channel_slug("https://t.me/s/bongboi") == "s_bongboi"


def test_sanitize_filename_is_portable_and_limited():
    assert t.sanitize_filename('a<b>:"/\\|?*c') == "a_b________c"
    assert t.sanitize_filename("ত্রিজাতা  [১৯৬২] ") == "ত্রিজাতা [১৯৬২]"
    assert len(t.sanitize_filename("x" * 400).encode("utf-8")) <= 220


def test_document_extension_prefers_mime_ext_then_name():
    assert t.document_extension(SimpleNamespace(ext=".epub", name="x.epub")) == "epub"
    assert t.document_extension(SimpleNamespace(ext=None, name="Book.PDF")) == "pdf"
    assert t.document_extension(SimpleNamespace(ext="", name=None)) == ""
    assert t.document_extension(SimpleNamespace(ext=".ZIP", name="a.zip")) == "zip"


def test_choose_filename_keeps_uploader_name_and_extension():
    assert t.choose_filename("গিটাঞ্জলি.epub", "caption", 5, "epub") == "গিটাঞ্জলি.epub"
    # Name without an extension gets the document's extension appended.
    assert t.choose_filename("গিটাঞ্জলি", "caption", 5, "epub") == "গিটাঞ্জলি.epub"


def test_choose_filename_falls_back_to_caption_then_message_id():
    name = t.choose_filename(None, "*গিটাঞ্জলি* - রবীন্দ্রনাথ ঠাকুর", 7, "epub")
    assert name == "রবীন্দ্রনাথ ঠাকুর - গিটাঞ্জলি.epub"
    assert t.choose_filename(None, None, 7, "epub") == "7.epub"


def test_manifest_roundtrip_counts_and_crash_safety(tmp_path):
    path = tmp_path / "manifest.json"
    manifest = t.Manifest(path)
    manifest.record({"message_id": 1, "status": "done", "path": "books/a.epub"})
    manifest.record({"message_id": 2, "status": "failed", "path": None})
    manifest.save()
    reloaded = t.Manifest(path)
    assert reloaded.entry(1)["status"] == "done"
    assert reloaded.counts() == {"done": 1, "failed": 1}
    # A truncated manifest is replaced rather than crashing the run.
    path.write_text('{"3": {"status": "d', encoding="utf-8")
    assert t.Manifest(path).data == {}
    assert t.Manifest(tmp_path / "missing.json").data == {}


def test_main_requires_credentials(tmp_path, monkeypatch, capsys):
    for name in ("TG_API_ID", "TG_API_HASH", "TG_PHONE"):
        monkeypatch.delenv(name, raising=False)
    # Ignore any real credentials stored in ~/.config/book-scrapper.
    monkeypatch.setattr(t, "_credential", lambda *_: None)
    with pytest.raises(SystemExit) as exc:
        t.main(["--data-dir", str(tmp_path)])
    assert "my.telegram.org" in capsys.readouterr().err


def test_dry_run_persists_pending_entries(tmp_path):
    manifest = t.Manifest(tmp_path / "manifest.json")
    asyncio.run(t.scan_and_download(FakeClient(_messages()), None, manifest, tmp_path, _args()))
    reloaded = t.Manifest(tmp_path / "manifest.json")
    assert reloaded.counts() == {"pending": 3}
    assert reloaded.entry(2)["path"] is None


def test_download_records_done_and_files(tmp_path):
    manifest = t.Manifest(tmp_path / "manifest.json")
    args = _args(dry_run=False)
    stats = asyncio.run(t.scan_and_download(FakeClient(_messages()), None, manifest, tmp_path, args))
    assert stats["downloaded"] == 3
    assert manifest.counts() == {"done": 3}
    saved = t.Manifest(tmp_path / "manifest.json")
    assert saved.entry(1)["status"] == "done"
    assert (tmp_path / "books" / "book1.epub").exists()


def test_scan_skips_done_and_respects_max_mb(tmp_path):
    manifest = t.Manifest(tmp_path / "manifest.json")
    done = _messages(2)
    manifest.record({"message_id": 1, "status": "done", "path": "books/book1.epub"})
    (tmp_path / "books").mkdir()
    (tmp_path / "books" / "book1.epub").write_bytes(b"fake")
    args = _args(dry_run=False)
    stats = asyncio.run(t.scan_and_download(FakeClient(done), None, manifest, tmp_path, args))
    assert stats["skipped"] == 1 and stats["downloaded"] == 1

    # Oversized files are recorded as too_large, never downloaded.
    big = [SimpleNamespace(id=9, file=SimpleNamespace(ext=".epub", name="big.epub", size=600 * 1024 * 1024),
                           message="Big", date=None)]
    stats = asyncio.run(t.scan_and_download(FakeClient(big), None, manifest, tmp_path, args))
    assert manifest.entry(9)["status"] == "too_large"
    assert not (tmp_path / "books" / "big.epub").exists()
