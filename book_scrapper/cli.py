import argparse
import fcntl
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from .http import Client
from .pipeline import Pipeline
from .sites import SITES


@contextmanager
def data_dir_lock(data_dir, command):
    """Prevent concurrent writers on the same data-dir.

    discover/crawl/download take an exclusive lock; status/export are
    read-only and run lock-free so they work alongside a download
    (SQLite WAL gives them a consistent snapshot).
    """
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if command in ("status", "export"):
        yield
        return
    lock_path = directory / ".lock"
    flags = fcntl.LOCK_EX
    with open(lock_path, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), flags | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                f"Another book-scrapper {command} is already running on {directory.resolve()} "
                f"({lock_path} is locked). Use a separate --data-dir per worker.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        except OSError:
            pass
        try:
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description="Resumable book discovery and downloads")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--site", choices=sorted(SITES), default="amarboi")
    parser.add_argument("--delay", type=float, default=2.0, help="Minimum seconds between requests to one host")
    parser.add_argument("--ignore-robots", action="store_true", help="Do not check robots.txt (user-opted crawl)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="Enumerate all sitemap pages")
    for name in ("crawl", "download"):
        command = sub.add_parser(name)
        command.add_argument("--limit", type=int, help="Maximum items this run; default all queued items")
        command.add_argument("--retry", action="store_true", help="Include previously failed items")
        if name == "download":
            command.add_argument("--max-mb", type=int, default=512)
    sub.add_parser("status")
    export = sub.add_parser("export")
    export.add_argument("path")
    fetch = sub.add_parser("fetch", help="Download FlipHTML5 page images for books recorded by a catalogue adapter")
    fetch.add_argument("--limit", type=int, help="Maximum books this run")
    fetch.add_argument("--code", action="append", help="Fetch only this book code (repeatable)")
    fetch.add_argument("--pdf", action="store_true", help="Assemble a PDF per book when img2pdf is installed")
    args = parser.parse_args()
    if args.delay < 0 or (getattr(args, "limit", None) is not None and args.limit < 1) or getattr(args, "max_mb", 1) < 1:
        parser.error("delay must be nonnegative; limit and max-mb must be positive")

    def _fetch(pipeline, args):
        from . import flipper
        codes = list(args.code) if args.code else sorted(flipper.catalogued_codes(pipeline.db, args.site))
        if not codes:
            print("No FlipHTML5 book links recorded; run `crawl` first." if not args.code
                  else f"Unknown code: {args.code[0]}")
            return
        if args.limit is not None:
            codes = codes[: args.limit]
        root = Path(args.data_dir) / "fliphtml5"
        summary = {}
        for code in codes:
            out_dir = root / code
            try:
                fetcher = flipper.FlipBookFetcher(pipeline.client, code, out_dir)
                downloaded = fetcher.download()
                pdf = fetcher.assemble_pdf() if args.pdf else None
                if args.pdf and not pdf:
                    print(f"{code}: (--pdf requested but img2pdf is not installed; pages kept as webp)")
                summary[code] = {
                    "title": fetcher.title, "pages": fetcher.page_count,
                    "downloaded_this_run": downloaded, "pdf": str(pdf) if pdf else None,
                }
                print(f"{code}: {fetcher.page_count} pages keyed to {out_dir}"
                      + (f", pdf → {pdf}" if pdf else ""), flush=True)
            except Exception as exc:
                print(f"{code}: failed: {exc}", flush=True)
        (root / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    with data_dir_lock(args.data_dir, args.command):
        client = Client(args.delay, robots_redirect="follow" if args.site == "liberationwarbangladesh" else "raise", respect_robots=not args.ignore_robots)
        pipeline = Pipeline(args.data_dir, SITES[args.site], client)
        try:
            if args.command == "discover":
                print(f"New pages: {pipeline.discover()}")
            elif args.command == "crawl":
                # Follow newly discovered collection/post links until queue is empty.
                if args.limit is not None:
                    pipeline.crawl(args.limit, args.retry)
                else:
                    pipeline.crawl(retry=args.retry)
                    while pipeline.crawl():
                        pass
            elif args.command == "download":
                pipeline.download(args.limit, args.retry, args.max_mb * 1024 * 1024)
            elif args.command == "export":
                pipeline.export(args.path)
            elif args.command == "fetch":
                _fetch(pipeline, args)
            print(json.dumps(pipeline.status(), indent=2))
        finally:
            pipeline.db.close()
            pipeline.client.session.close()


if __name__ == "__main__":
    main()
