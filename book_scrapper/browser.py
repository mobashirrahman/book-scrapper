"""Same-session browser fallback with a loopback web remote control."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import json
import queue
import secrets
import threading
import time

from .captcha import Solver, widget


HTML = '''<!doctype html><meta charset="utf-8"><title>Book download verification</title>
<style>body{font:16px system-ui;max-width:1120px;margin:24px auto;padding:0 12px;background:#f5f6fa;color:#17223b}button,input{font:inherit;padding:8px;margin:4px}img{width:100%;border:1px solid #aaa;background:white}#status{white-space:pre-wrap}small{display:block;margin:12px 0}</style>
<h1>Complete download verification</h1>
<p>Click the live page below to solve the CAPTCHA, then click the site's download button. The file will be saved automatically.</p>
<p id="status">Connecting…</p><small id="url"></small>
<button onclick="act({op:'press',key:'Tab'})">Tab</button>
<button onclick="act({op:'press',key:'Enter'})">Enter</button>
<button onclick="act({op:'press',key:'Backspace'})">Backspace</button>
<button onclick="act({op:'scroll',delta:-500})">Scroll up</button>
<button onclick="act({op:'scroll',delta:500})">Scroll down</button>
<input id="text" placeholder="Text for the selected field" autocomplete="off">
<button onclick="act({op:'text',text:document.querySelector('#text').value});document.querySelector('#text').value=''">Type text</button>
<button onclick="act({op:'skip'})">Save for later</button>
<small>This view controls the actual browser session. Solving a challenge in a separate browser tab does not update this session.</small>
<img id="view" alt="Live download page">
<script>
const root=location.pathname.replace(/\/$/,'');
async function act(data){await fetch(root+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})}
const view=document.querySelector('#view');
view.onclick=e=>{const r=view.getBoundingClientRect();act({op:'click',x:(e.clientX-r.left)*1100/r.width,y:(e.clientY-r.top)*800/r.height})};
async function poll(){try{const s=await (await fetch(root+'/state')).json();document.querySelector('#status').textContent=s.message;document.querySelector('#url').textContent=s.url||'';if(s.image)view.src='data:image/jpeg;base64,'+s.image;if(s.finished)return;}catch(e){document.querySelector('#status').textContent='Browser session ended. Run book-scrapper solve to reopen pending jobs.';}setTimeout(poll,1000)}poll();
</script>'''


class Dashboard:
    def __init__(self):
        self.actions = queue.Queue(maxsize=100)
        self.state = {'message': 'Opening browser…'}
        token = secrets.token_urlsafe(32)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send(self, content, content_type, status=200):
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Frame-Options', 'DENY')
                self.end_headers()
                self.wfile.write(content)

            def do_GET(self):
                if self.path == '/' + token:
                    self.send(HTML.encode(), 'text/html; charset=utf-8')
                elif self.path == '/' + token + '/state':
                    self.send(json.dumps(outer.state).encode(), 'application/json')
                else:
                    self.send(b'Not found', 'text/plain', 404)

            def do_POST(self):
                if self.path != '/' + token + '/action':
                    self.send(b'Not found', 'text/plain', 404)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 8192:
                        raise ValueError('Invalid size')
                    if self.headers.get('Content-Type') != 'application/json':
                        raise ValueError('Expected JSON')
                    action = json.loads(self.rfile.read(length))
                    if not isinstance(action, dict):
                        raise ValueError('Expected object')
                    outer.actions.put_nowait(action)
                    self.send(b'{}', 'application/json')
                except (ValueError, queue.Full):
                    self.send(b'Invalid action', 'text/plain', 400)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}/{token}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


INJECT = '''token => {
 for(const el of document.querySelectorAll('[name="g-recaptcha-response"],[name="h-captcha-response"],[name="cf-turnstile-response"]')) {
   el.value=token;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
 }
 for(const el of document.querySelectorAll('[data-callback]')) {
   const name=el.getAttribute('data-callback');
   const fn=name.split('.').reduce((o,k)=>o&&o[k],window);
   if(typeof fn==='function')fn(token);
 }
}'''


class BrowserFallback:
    def __init__(self, pipeline, max_bytes):
        self.pipeline, self.max_bytes = pipeline, max_bytes

    def run(self, asset_url, page_url, timeout):
        from playwright.sync_api import sync_playwright
        completed = []
        errors = []
        executor = ThreadPoolExecutor(max_workers=1)
        future = None
        try:
            with sync_playwright() as pw, Dashboard() as dashboard:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(accept_downloads=True, viewport={'width': 1100, 'height': 800})
                # Carry initial HTTP cookies into the browser; all further challenge
                # actions and downloads remain in this one browser context.
                cookies = []
                for cookie in self.pipeline.client.session.cookies:
                    cookies.append({'name': cookie.name, 'value': cookie.value,
                                    'domain': cookie.domain, 'path': cookie.path or '/',
                                    'secure': cookie.secure})
                if cookies:
                    context.add_cookies(cookies)
                current = [None]
                download_events = []

                def attach(page):
                    current[0] = page
                    page.on('download', download_events.append)
                context.on('page', attach)
                page = context.new_page()
                message = 'Solve the challenge below, then click the download button.'
                print(f'Manual verification: {dashboard.url}', flush=True)
                print('On a remote server, forward the URL port with SSH to access this page.', flush=True)
                try:
                    page.goto(page_url, wait_until='domcontentloaded', timeout=45000)
                except Exception as exc:
                    # Navigating straight to an attachment also throws ERR_ABORTED.
                    message = 'Waiting for page or download: ' + type(exc).__name__
                deadline = time.monotonic() + timeout
                auto_attempted = False
                auto_clicked = False
                last_image = 0
                while time.monotonic() < deadline and not completed:
                    page = current[0]
                    if page.is_closed():
                        break
                    page.wait_for_timeout(100)
                    if download_events:
                        download = download_events.pop(0)
                        try:
                            failure = download.failure()
                            if failure:
                                raise ValueError(failure)
                            path = download.path()
                            destination = self.pipeline.import_browser_download(asset_url, path, self.max_bytes)
                            completed.append(destination)
                            message = 'Download saved: ' + destination.name
                        except Exception as exc:
                            errors.append(str(exc))
                            message = 'Download rejected: ' + str(exc)
                    if not auto_attempted:
                        found = widget(page.content())
                        if found:
                            auto_attempted = True
                            try:
                                solver = Solver.configured()
                                if solver:
                                    future = executor.submit(solver.solve, *found, page.url)
                                    message = f'Trying {solver.provider}; you can also solve manually.'
                            except Exception as exc:
                                message = str(exc) + '. Solve manually below.'
                    if future and future.done():
                        try:
                            token = future.result()
                            page.evaluate(INJECT, token)
                            message = 'Solver token submitted. Waiting for download; use the page if needed.'
                            # Known download control only; never submit arbitrary forms.
                            button = page.locator('a#downloadButton').first
                            if button.count() and button.is_visible():
                                button.click(timeout=5000)
                        except Exception as exc:
                            message = f'Automatic solving did not complete ({type(exc).__name__}). Solve manually below.'
                        future = None
                    # A solved interstitial may reveal the regular MediaFire button.
                    if not auto_clicked and not widget(page.content()):
                        button = page.locator('a#downloadButton').first
                        if button.count() and button.is_visible():
                            auto_clicked = True
                            try:
                                button.click(timeout=5000)
                            except Exception:
                                pass
                    try:
                        action = dashboard.actions.get_nowait()
                    except queue.Empty:
                        action = None
                    if action:
                        deadline = time.monotonic() + timeout
                        op = action.get('op')
                        if op == 'skip':
                            break
                        try:
                            if op == 'click':
                                x, y = float(action['x']), float(action['y'])
                                if 0 <= x <= 1100 and 0 <= y <= 800:
                                    page.mouse.click(x, y)
                            elif op == 'text':
                                page.keyboard.insert_text(str(action.get('text', ''))[:2000])
                            elif op == 'press' and action.get('key') in {'Tab', 'Enter', 'Backspace'}:
                                page.keyboard.press(action['key'])
                            elif op == 'scroll':
                                page.mouse.wheel(0, max(-800, min(800, int(action['delta']))))
                        except Exception as exc:
                            message = 'Interaction failed: ' + type(exc).__name__
                    if time.monotonic() - last_image > 0.8:
                        try:
                            screenshot = base64.b64encode(page.screenshot(type='jpeg', quality=75, timeout=5000)).decode()
                            dashboard.state = {'message': message, 'url': page.url, 'image': screenshot, 'finished': bool(completed)}
                            last_image = time.monotonic()
                        except Exception:
                            pass
                context.close()
                browser.close()
                if completed:
                    print(f'Browser download saved: {completed[0]}', flush=True)
                else:
                    raise TimeoutError('Saved for later. Run book-scrapper solve to reopen this job.')
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
