"""Challenge detection and bounded, optional paid token solving."""
import os
import time
from urllib.parse import urlsplit, parse_qs

from bs4 import BeautifulSoup
import requests


class Challenge(Exception):
    def __init__(self, url, message="CAPTCHA or browser verification required"):
        self.url = url
        super().__init__(message)


def is_challenge(html):
    text = html.lower()
    return any(marker in text for marker in (
        'g-recaptcha', 'h-captcha', 'cf-turnstile', 'challenges.cloudflare.com',
        '/recaptcha/api', 'hcaptcha.com/1/api', 'cf-chl-',
        'verify you are human', 'verify that you are human',
    ))


def widget(html):
    soup = BeautifulSoup(html, 'html.parser')
    for kind, selector in [('recaptcha', '.g-recaptcha[data-sitekey]'),
                           ('hcaptcha', '.h-captcha[data-sitekey]'),
                           ('turnstile', '.cf-turnstile[data-sitekey]')]:
        tag = soup.select_one(selector)
        if tag:
            return kind, tag['data-sitekey']
    for frame in soup.select('iframe[src]'):
        url = frame['src']
        if '/recaptcha/' in url and '/anchor' in url:
            key = parse_qs(urlsplit(url).query).get('k', [None])[0]
            if key:
                return 'recaptcha', key
    return None


class Solver:
    def __init__(self, provider, key, timeout=120, session=None):
        self.provider, self.key, self.timeout = provider, key, timeout
        self.session = session or requests.Session()
        self.endpoint = {'2captcha': 'https://api.2captcha.com',
                         'anticaptcha': 'https://api.anti-captcha.com'}[provider]

    @classmethod
    def configured(cls):
        provider = os.getenv('CAPTCHA_PROVIDER', '2captcha')
        env = {'2captcha': 'TWOCAPTCHA_API_KEY', 'anticaptcha': 'ANTICAPTCHA_API_KEY'}
        if provider not in env:
            raise ValueError('CAPTCHA_PROVIDER must be 2captcha or anticaptcha')
        key = os.getenv(env[provider])
        return cls(provider, key) if key else None

    def request(self, method, payload):
        with self.session.post(self.endpoint + '/' + method,
                               json={'clientKey': self.key, **payload}, timeout=20) as r:
            r.raise_for_status()
            result = r.json()
        if result.get('errorId'):
            # Error codes only: never log API payloads, keys, cookies or tokens.
            raise RuntimeError('Solver error: ' + str(result.get('errorCode', 'unknown')))
        return result

    def solve(self, kind, key, url):
        types = {'recaptcha': 'RecaptchaV2TaskProxyless',
                 'hcaptcha': 'HCaptchaTaskProxyless',
                 'turnstile': 'TurnstileTaskProxyless'}
        task = {'type': types[kind], 'websiteURL': url, 'websiteKey': key}
        created = self.request('createTask', {'task': task})
        task_id = created['taskId']
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(5)
            result = self.request('getTaskResult', {'taskId': task_id})
            if result.get('status') == 'ready':
                solution = result.get('solution', {})
                token = solution.get('gRecaptchaResponse') or solution.get('token')
                if not token:
                    raise RuntimeError('Solver returned no token')
                return token
        raise TimeoutError('Solver timed out; switching to manual solving')
