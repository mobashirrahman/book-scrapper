"""Resumable Telegram channel book downloader.

Telegram documents are not reachable over plain HTTP: the public ``t.me/s/``
preview renders only recent posts and never exposes file URLs.  Books are
therefore fetched with the MTProto API through Telethon (an optional extra,
imported lazily like ``mega.py``), using the operator's own Telegram account.
The first run prompts for the phone number and the login code Telegram sends
to the account's app; the session file in the channel directory keeps later
runs logged in with no interaction.

Like the HTTP pipeline, runs are resumable.  A JSON manifest records every
book document seen (message id, caption, size, status, path) so a re-run
skips completed downloads and, with ``--retry``, retries failed ones.
Filenames keep the uploader's name, falling back to the caption (through
``naming.book_filename``) and finally the message id; collisions with a
different message are de-duplicated with a message-id prefix.

Run standalone: ``python -m book_scrapper.telegram_dl --channel bongboi``.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

from .naming import book_filename

DEFAULT_EXTENSIONS = ("epub", "pdf", "mobi", "azw3")


class TelegramUnavailable(RuntimeError):
    """The optional Telethon dependency is not installed or importable."""


def _import_telethon():
    try:
        from telethon import TelegramClient
    except ImportError as exc:  # pragma: no cover - trivial import guard
        raise TelegramUnavailable(
            "Telethon is required for Telegram downloads: pip install -e '.[telegram]'"
        ) from exc
    return TelegramClient


def channel_slug(channel):
    """Stable directory/session name for a @handle or full t.me URL."""
    name = channel.rstrip("/")
    if "t.me/" in name:
        name = name.split("t.me/", 1)[1]
    name = name.lstrip("@")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return name or "channel"


def sanitize_filename(name):
    """Portability-safe filename, mirroring naming.book_filename's limits."""
    name = unicodedata.normalize("NFC", name or "")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    while len(name.encode("utf-8")) > 220:
        name = name[:-1]
    return name.rstrip(" .")


def caption_title(caption):
    """First meaningful caption line, with light Telegram markdown stripped."""
    for line in (caption or "").splitlines():
        text = re.sub(r"[*_`\[\]]+", "", line).strip(" \t#>-–—•·")
        if text:
            return text
    return ""


def document_extension(file_obj):
    """Lowercase extension of a Telethon ``msg.file`` (name wins over mime)."""
    name = getattr(file_obj, "name", None) or ""
    ext = str(getattr(file_obj, "ext", None) or "").lstrip(".").lower()
    if not ext and "." in name:
        ext = name.rsplit(".", 1)[-1].lower()
    return ext


def choose_filename(file_name, caption, message_id, ext):
    """Filename for a channel document: Telegram name > caption > message id."""
    ext = ext.lstrip(".").lower()
    base = sanitize_filename(file_name or "")
    if base:
        if not re.search(r"\.[A-Za-z0-9]{1,5}$", base):
            base = f"{base}.{ext}" if ext else base
        return base
    title = caption_title(caption)
    if title:
        return book_filename(title, f".{ext}" if ext else "")
    return f"{message_id}.{ext}" if ext else str(message_id)


class Manifest:
    """Crash-safe JSON record of every book document seen in a channel."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                # A truncated manifest after a hard kill is safe to replace:
                # partial downloads have no 'done' entry and are re-fetched.
                self.data = {}

    def entry(self, message_id):
        return self.data.get(str(message_id))

    def record(self, entry):
        self.data[str(entry["message_id"])] = entry

    def save(self):
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def counts(self):
        tally = {}
        for item in self.data.values():
            status = item.get("status", "?")
            tally[status] = tally.get(status, 0) + 1
        return tally


def _credential(args_value, env_name, file_name):
    """CLI flag > env var > ~/.config/book-scrapper/<file>, like the Drive key."""
    if args_value:
        return str(args_value).strip()
    value = os.environ.get(env_name)
    if value:
        return value.strip()
    path = Path.home() / ".config" / "book-scrapper" / file_name
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


async def scan_and_download(client, entity, manifest, channel_dir, args, total=0):
    """One resumable pass over the channel history (newest first)."""
    books_dir = channel_dir / "books"
    books_dir.mkdir(parents=True, exist_ok=True)
    extensions = {e.lstrip(".").lower() for e in args.ext}
    max_bytes = args.max_mb * 1024 * 1024
    scanned = found = downloads = skipped = failed = recorded = 0

    async for msg in client.iter_messages(entity):
        scanned += 1
        if scanned % 500 == 0:
            known = f"{total}" if total else "?"
            print(f"  scanned {scanned}/{known} messages, found {found} book files…", flush=True)
        file_obj = msg.file
        if file_obj is None:
            continue
        ext = document_extension(file_obj)
        if ext not in extensions:
            continue
        found += 1

        message_id = msg.id
        caption = msg.message or ""
        relative = None
        existing = manifest.entry(message_id)
        if existing and existing.get("status") == "done":
            path = existing.get("path")
            if path and (channel_dir / path).exists():
                skipped += 1
                continue
            existing = None  # recorded file was deleted on disk; download again

        filename = choose_filename(file_obj.name, caption, message_id, ext)
        if (channel_dir / "books" / filename).exists() and not (
            existing and existing.get("path") == f"books/{filename}"
        ):
            # A different message already claims this name.
            filename = f"{message_id}_{filename}"

        entry = dict(existing or {})
        entry.update({
            "message_id": message_id,
            "size": file_obj.size or 0,
            "date": msg.date.isoformat() if msg.date else None,
            "caption": caption or None,
        })

        if entry["size"] and entry["size"] > max_bytes:
            entry.update({"status": "too_large", "path": None,
                          "error": f"{entry['size']} bytes exceeds --max-mb {args.max_mb}"})
            manifest.record(entry)
            manifest.save()
            skipped += 1
            print(f"  {message_id}: too_large: {filename}", flush=True)
            continue
        if entry.get("status") == "failed" and not args.retry:
            skipped += 1
            continue
        if args.dry_run:
            entry.update({"status": entry.get("status") or "pending", "path": None})
            manifest.record(entry)
            recorded += 1
            if recorded % 500 == 0:
                manifest.save()
            continue

        entry.update({"status": "downloading", "path": f"books/{filename}"})
        try:
            saved = await client.download_media(msg, file=str(books_dir / filename))
            entry["status"] = "done"
            entry["size"] = Path(saved).stat().st_size if saved else entry["size"]
            downloads += 1
            print(f"  {message_id}: done: {filename}", flush=True)
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
            failed += 1
            print(f"  {message_id}: failed: {filename}: {exc}", flush=True)
        manifest.record(entry)
        manifest.save()
        if args.limit is not None and downloads >= args.limit:
            print(f"Reached --limit {args.limit} downloads; stopping this run.", flush=True)
            break

    manifest.save()
    print(f"Done: scanned {scanned} messages, {found} book files, "
          f"downloaded {downloads}, skipped {skipped}, failed {failed}", flush=True)
    return {"scanned": scanned, "found": found, "downloaded": downloads,
            "skipped": skipped, "failed": failed}


async def run(args):
    TelegramClient = _import_telethon()
    slug = channel_slug(args.channel)
    channel_dir = Path(args.data_dir) / slug
    (channel_dir / "books").mkdir(parents=True, exist_ok=True)
    manifest = Manifest(channel_dir / "manifest.json")

    client = TelegramClient(str(channel_dir / f"{slug}.session"), int(args.api_id), args.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.start(phone=args.phone)  # prompts once for phone/code/2FA
    try:
        entity = await client.get_entity(args.channel)
        label = getattr(entity, "title", None) or args.channel
        total = 0
        try:
            total = (await client.get_messages(entity, limit=0)).total or 0
        except Exception:
            pass
        print(f"Channel: {label} — scanning history "
              f"({total} messages known)…", flush=True)
        if args.dry_run:
            print("Dry run: recording book files without downloading.", flush=True)
        await scan_and_download(client, entity, manifest, channel_dir, args, total)
        counts = ", ".join(f"{k}={v}" for k, v in sorted(manifest.counts().items()))
        print(f"Manifest: {counts}")
        print(f"Books directory: {channel_dir / 'books'}")
    finally:
        await client.disconnect()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Resumable Telegram channel book downloader (Telethon, user login)")
    parser.add_argument("--channel", default="bongboi",
                        help="channel @username or full https://t.me/… URL (default: bongboi)")
    parser.add_argument("--data-dir", default="data/telegram",
                        help="parent directory; state goes to <data-dir>/<channel>/ (default: data/telegram)")
    parser.add_argument("--api-id", default=None,
                        help="Telegram api_id (else TG_API_ID or ~/.config/book-scrapper/telegram_api_id)")
    parser.add_argument("--api-hash", default=None,
                        help="Telegram api_hash (else TG_API_HASH or ~/.config/book-scrapper/telegram_api_hash)")
    parser.add_argument("--phone", default=os.environ.get("TG_PHONE"),
                        help="login phone number (else TG_PHONE; prompted on first run if omitted)")
    parser.add_argument("--ext", nargs="+", default=list(DEFAULT_EXTENSIONS),
                        help="book file extensions to keep (default: epub pdf mobi azw3)")
    parser.add_argument("--limit", type=int, help="maximum downloads this run")
    parser.add_argument("--retry", action="store_true", help="retry previously failed downloads")
    parser.add_argument("--max-mb", type=int, default=512, help="per-file size cap in MiB (default 512)")
    parser.add_argument("--dry-run", action="store_true",
                        help="scan and catalogue only; record pending entries without downloading")
    args = parser.parse_args(argv)

    if not args.ext or args.max_mb < 1 or (args.limit is not None and args.limit < 1):
        parser.error("ext must be nonempty; limit and max-mb must be positive")

    args.api_id = _credential(args.api_id, "TG_API_ID", "telegram_api_id")
    args.api_hash = _credential(args.api_hash, "TG_API_HASH", "telegram_api_hash")
    if not args.api_id or not args.api_hash:
        parser.error(
            "Telegram API credentials are required. Get them free at "
            "https://my.telegram.org (API development tools → create an app), then "
            "export TG_API_ID=<id> TG_API_HASH=<hash>, or pass --api-id/--api-hash")

    try:
        asyncio.run(run(args))
    except TelegramUnavailable as exc:
        parser.exit(2, f"telegram_dl: {exc}\n")
    except KeyboardInterrupt:
        print("\nInterrupted — progress is saved; re-run the same command to resume.",
              file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
