import argparse
import json

from .http import Client
from .pipeline import Pipeline
from .sites import SITES


def main():
    parser = argparse.ArgumentParser(description="Resumable book discovery and downloads")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--site", choices=sorted(SITES), default="amarboi")
    parser.add_argument("--delay", type=float, default=2.0, help="Minimum seconds between requests to one host")
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
    args = parser.parse_args()
    if args.delay < 0 or (getattr(args, "limit", None) is not None and args.limit < 1) or getattr(args, "max_mb", 1) < 1:
        parser.error("delay must be nonnegative; limit and max-mb must be positive")
    pipeline = Pipeline(args.data_dir, SITES[args.site], Client(args.delay))
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
        print(json.dumps(pipeline.status(), indent=2))
    finally:
        pipeline.db.close()
        pipeline.client.session.close()


if __name__ == "__main__":
    main()
