import time
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser

import requests


class Client:
    """Serial, paced requests. Check robots for each origin and redirect."""
    def __init__(self, delay=2.0, retries=3):
        self.delay = delay
        self.retries = retries
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "BookScrapper/0.1"
        self.robots = {}
        self.last = {}

    def _request(self, url, stream=False):
        origin = urlsplit(url).netloc
        for attempt in range(self.retries + 1):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last.get(origin, 0))))
            self.last[origin] = time.monotonic()
            try:
                r = self.session.get(url, timeout=(15, 60), stream=stream, allow_redirects=False)
                if r.status_code == 429 or r.status_code >= 500:
                    wait = r.headers.get("Retry-After", "")
                    r.close()
                    if attempt == self.retries:
                        raise requests.HTTPError(f"Retry limit reached: {url}")
                    time.sleep(min(60, float(wait) if wait.isdigit() else 2 ** attempt))
                    continue
                return r
            except (requests.ConnectionError, requests.Timeout):
                if attempt == self.retries:
                    raise
                time.sleep(2 ** attempt)

    def allowed(self, url):
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self.robots:
            r = self._request(origin + "/robots.txt")
            with r:
                rules = RobotFileParser()
                if r.status_code in (404, 410):
                    rules.parse([])
                elif r.status_code in (401, 403):
                    rules.parse(["User-agent: *", "Disallow: /"])
                else:
                    r.raise_for_status()
                    if r.is_redirect:
                        raise ValueError(f"Robots redirect requires review: {origin}")
                    rules.parse(r.text.splitlines())
                self.robots[origin] = rules
        rules = self.robots[origin]
        crawl_delay = rules.crawl_delay("BookScrapper") or 0
        if crawl_delay:
            self.delay = max(self.delay, crawl_delay)
        return rules.can_fetch("BookScrapper", url)

    def get(self, url, stream=False):
        for _ in range(10):
            if urlsplit(url).scheme not in {"https", "http"}:
                raise ValueError("Unsupported URL scheme")
            if not self.allowed(url):
                raise PermissionError(f"robots.txt disallows {url}")
            r = self._request(url, stream=stream)
            if r.is_redirect:
                url = urljoin(url, r.headers["Location"])
                r.close()
                continue
            try:
                r.raise_for_status()
            except Exception:
                r.close()
                raise
            return r
        raise ValueError("Too many redirects")
