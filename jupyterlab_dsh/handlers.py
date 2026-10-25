"""Same-origin reverse proxy for the dsh web GUI.

Why a proxy at all
------------------
deepseek-harness refuses wildcard binds, mints authority-bound session
cookies, and enforces a Host/Origin fence that 403s any request whose
authority is not loopback / the bind IP / an explicitly trusted host.  A
browser that reaches JupyterLab from an external address (JupyterLab listens
on 0.0.0.0) would present `Origin: http://<external-ip>:8888` and be refused.

The proxy solves every leg of that in one place:

* Requests are forwarded to the loopback listener, so the Host the backend
  sees (`127.0.0.1:<port>`) is always inside the fence - no matter which
  external IP:port the browser used - and cookies minted for that authority
  validate for every subsequent request.
* The `Origin` header is rewritten to the same loopback authority, which is
  exactly what the fence demands ("Origin must name the same authority").
* The launch-token exchange (`GET /?token=...` -> signed cookie -> `./`) is
  performed server-side, so the process credential never reaches the browser.
* `Set-Cookie` paths are rewritten from `/` to the `/dsh/` mount, per the
  upstream deployment contract.
* Jupyter-side XSRF is bypassed for this handler only (requests still require
  an authenticated Jupyter session; the dsh session cookie protects the
  backend) because the dsh frontend cannot know JupyterLab's `_xsrf` token.
* WebSocket upgrades (the `/api/remote.mux` stream) are tunnelled with the
  same Host/Origin/cookie treatment.
"""

import asyncio
import re

from tornado import httpclient, web, websocket
from tornado.httputil import HTTPHeaders

from jupyter_server.base.handlers import JupyterHandler

# Hop-by-hop headers must never be forwarded in either direction.
HOP_HEADERS = frozenset(
    h.lower()
    for h in (
        "Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization",
        "TE", "Trailers", "Transfer-Encoding", "Upgrade", "Proxy-Connection",
        "Host", "Content-Length", "Sec-Websocket-Key", "Sec-Websocket-Version",
        "Sec-Websocket-Extensions", "Sec-Websocket-Protocol", "Sec-Websocket-Accept",
    )
)

# Browser metadata that would leak a mismatched authority or confuse the fence.
STRIP_REQUEST_HEADERS = frozenset(
    h.lower()
    for h in (
        "Origin", "Referer", "Sec-Fetch-Site", "Sec-Fetch-Mode",
        "Sec-Fetch-User", "Sec-Fetch-Dest", "Sec-Fetch-Storage-Access",
        "X-Xsrf-Token", "Xsrftoken",
    )
)

# Response headers that would keep the GUI out of an iframe or split it from
# its JupyterLab parent; the rest of the backend's headers pass through.
STRIP_RESPONSE_HEADERS = frozenset(
    h.lower()
    for h in ("X-Frame-Options", "Content-Security-Policy", "Cross-Origin-Opener-Policy")
)

_COOKIE_PATH_RE = re.compile(r"(?i);\s*Path\s*=\s*(/[^;]*)")


class WebSocketHandlerMixin:
    """Route GET into tornado's websocket upgrade when requested.

    Merged before JupyterHandler so `self` is still a JupyterHandler for
    authentication while gaining WebSocketHandler's protocol handling.
    """

    def check_xsrf_cookie(self):
        # Websockets have no XSRF token dance; dsh's own signed session
        # cookie authenticates the tunnelled stream.
        pass

    async def get(self, path=""):
        if self.request.headers.get("Upgrade", "").lower() != "websocket":
            await self._http_get(path)
            return
        await websocket.WebSocketHandler.get(self, path)


class DshProxyHandler(WebSocketHandlerMixin, JupyterHandler, websocket.WebSocketHandler):
    """HTTP + WebSocket proxy for everything under `<base_url>dsh/`."""

    def check_xsrf_cookie(self):
        # The dsh frontend posts to its own API without JupyterLab's _xsrf.
        # Jupyter session auth (web.authenticated) + dsh's signed cookie
        # remain fully in force.
        pass

    def check_origin(self, origin):
        # Same-origin iframe: origin matches by construction.  Being lenient
        # here only admits the WebSocket handshake; dsh still fences and
        # authenticates every proxied call.
        return True

    # ------------------------------------------------------------- helpers
    @property
    def dsh(self):
        return self.settings["dsh_manager"]

    def _mount(self):
        """External mount prefix, e.g. `/dsh/` or `/user/me/dsh/`."""
        return self.base_url + "dsh/"

    def _strip_mount_path(self, path):
        return path[1:] if path.startswith("/") else path

    def _rewrite_cookie(self, value):
        """Re-scope a backend Set-Cookie onto the /dsh/ mount."""
        m = _COOKIE_PATH_RE.search(value)
        if not m:
            # No Path attribute: browsers default it to the request
            # directory, which is already the mount.  Leave untouched.
            return value
        rest = m.group(1)[1:]  # drop the leading slash
        return value[: m.start(1)] + self._mount() + rest + value[m.end(1):]

    def _rewrite_location(self, location):
        base = self.dsh.base_url
        if location.startswith(base):
            location = location[len(base):] or "/"
        if location.startswith("/"):
            return self._mount() + location[1:]
        return location  # already relative

    def _backend_headers(self):
        h = HTTPHeaders()
        for name in self.request.headers.keys():
            for value in self.request.headers.get_list(name):
                ln = name.lower()
                if ln in HOP_HEADERS or ln in STRIP_REQUEST_HEADERS:
                    continue
                if ln == "cookie":
                    continue  # re-added below
                h.add(name, value)
        cookie = self.request.headers.get("Cookie")
        if cookie:
            h.add("Cookie", cookie)
        # The fence: Origin must name the same authority as Host, and the
        # Host we present is the loopback listener itself.
        h.add("Origin", self.dsh.base_url)
        return h

    def _emit_response(self, resp):
        self.set_status(resp.code, resp.reason)
        skip = HOP_HEADERS | STRIP_RESPONSE_HEADERS | {"content-length"}
        # 204/304 (and 1xx) responses carry no body and must not carry
        # representation metadata (RFC 9110 §9.3.5/§15.4.5).  Tornado's
        # finish() strips those headers for such statuses, and its
        # clear_header() raises KeyError when the header's HTTPHeaders
        # lookup-cache entry is gone -- which is the state left behind by
        # add_header() on a name that already had a value (it pops the
        # cache).  That is exactly how a browser revalidating a cached
        # asset (If-None-Match) turned a proxied response into a 500 and a
        # blank dsh tab after close + reopen.  So: no representation
        # headers, no body write for no-body statuses.
        no_body = resp.code in (204, 304) or 100 <= resp.code < 200
        for name in resp.headers.keys():
            for value in resp.headers.get_list(name):
                ln = name.lower()
                if ln in skip:
                    continue
                if no_body and ln in ("content-type", "content-encoding", "content-language"):
                    continue
                if ln == "set-cookie":
                    self.add_header("Set-Cookie", self._rewrite_cookie(value))
                elif ln == "location":
                    self.add_header("Location", self._rewrite_location(value))
                elif ln == "content-type":
                    # set_header: replaces the "text/html" default from
                    # JupyterHandler.set_default_headers instead of
                    # stacking a second, conflicting value
                    self.set_header(name, value)
                else:
                    self.add_header(name, value)
        if not no_body and self.request.method != "HEAD" and resp.body is not None:
            self.write(resp.body)

    async def _fetch(self, url, method, headers, body=None):
        request = httpclient.HTTPRequest(
            url,
            method=method,
            headers=headers,
            body=body,
            follow_redirects=False,
            request_timeout=620,
            connect_timeout=20,
            decompress_response=False,
            allow_nonstandard_methods=True,
        )
        return await self.dsh.http_client().fetch(request, raise_error=False)

    # ------------------------------------------------------------ GET flows
    async def _http_get(self, path):
        if self._strip_mount_path(path) == "":
            await self._entry_point()
            return
        await self._proxy(path)

    @web.authenticated
    async def get(self, path=""):
        await WebSocketHandlerMixin.get(self, path)

    async def _entry_point(self):
        """Serve the GUI root, performing the token exchange server-side."""
        mgr = self.dsh
        if not mgr.ready:
            await self._not_ready_page()
            return
        try:
            # 1. exchange the launch token for the signed session cookie
            resp = await self._fetch(mgr.token_url(), "GET", {"Accept": "text/html,*/*"})
            set_cookies = resp.headers.get_list("Set-Cookie")
            pairs = []
            for c in set_cookies:
                pair = c.split(";", 1)[0].strip()
                if "=" in pair:
                    pairs.append(pair)
            # 2. fetch the index document with those cookies
            hdrs = HTTPHeaders({"Accept": "text/html,*/*"})
            if pairs:
                hdrs.add("Cookie", "; ".join(pairs))
            doc = await self._fetch(mgr.base_url + "/", "GET", hdrs)
        except Exception as exc:
            self.log.warning("dsh entry exchange failed: %r", exc)
            await self._not_ready_page()
            return
        self.set_status(doc.code if doc.code < 500 else 502, doc.reason)
        self.set_header("Content-Type", doc.headers.get("Content-Type", "text/html"))
        self.set_header("Cache-Control", "no-store")
        for c in set_cookies + doc.headers.get_list("Set-Cookie"):
            self.add_header("Set-Cookie", self._rewrite_cookie(c))
        self.write(doc.body)

    async def _not_ready_page(self):
        mgr = self.dsh
        err = ""
        if mgr.failed and mgr.last_error:
            err = "<pre>%s</pre>" % (mgr.last_error.replace("<", "&lt;"),)
        self.set_status(503 if mgr.failed else 200)
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.set_header("Cache-Control", "no-store")
        page = (
            """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="3">
<title>deepseek-harness</title>
<style>body{font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f6fa;color:#333}
.box{text-align:center}h2{font-weight:500}pre{text-align:left;background:#fff;border:1px solid #ddd;padding:8px;border-radius:6px}
.spin{width:28px;height:28px;border:4px solid #d7dcf5;border-top-color:#4d6bfe;border-radius:50%;margin:0 auto 14px;animation:r 1s linear infinite}@keyframes r{to{transform:rotate(360deg)}}</style></head>
<body><div class="box"><div class="spin"></div><h2>deepseek-harness is starting&hellip;</h2>
<p>This page refreshes automatically.</p>__ERR__</div></body></html>"""
        ).replace("__ERR__", err)
        self.write(page)

    # ------------------------------------------------------- generic proxy
    async def _proxy(self, path, body=None):
        mgr = self.dsh
        if not mgr.ready:
            self.set_status(503)
            self.set_header("Content-Type", "application/json")
            self.write('{"error": "dsh backend not ready"}')
            return
        backend_path = self._strip_mount_path(path)
        url = mgr.base_url + "/" + backend_path
        if self.request.query:
            url += "?" + self.request.query
        headers = self._backend_headers()
        try:
            resp = await self._fetch(url, self.request.method, headers, body=body)
        except Exception as exc:
            self.log.warning("dsh proxy error for %s: %r", url, exc)
            self.set_status(502)
            self.set_header("Content-Type", "application/json")
            self.write('{"error": "dsh backend unreachable"}')
            return
        self._emit_response(resp)

    async def _proxy_method(self, path):
        await self._proxy(path, body=self.request.body or None)

    @web.authenticated
    async def post(self, path=""):
        await self._proxy_method(path)

    @web.authenticated
    async def put(self, path=""):
        await self._proxy_method(path)

    @web.authenticated
    async def delete(self, path=""):
        await self._proxy_method(path)

    @web.authenticated
    async def patch(self, path=""):
        await self._proxy_method(path)

    @web.authenticated
    async def head(self, path=""):
        await self._proxy_method(path)

    @web.authenticated
    async def options(self, path=""):
        await self._proxy_method(path)

    # ------------------------------------------------------ websocket flow
    def select_subprotocol(self, subprotocols):
        return subprotocols[0] if subprotocols else None

    async def open(self, path=""):
        mgr = self.dsh
        if not mgr.ready:
            self.close(code=1013, reason="backend starting")
            return
        backend_path = self._strip_mount_path(path)
        url = mgr.ws_base_url + "/" + backend_path
        if self.request.query:
            url += "?" + self.request.query
        headers = HTTPHeaders({"Origin": mgr.base_url})
        cookie = self.request.headers.get("Cookie")
        if cookie:
            headers.add("Cookie", cookie)
        raw = self.request.headers.get("Sec-WebSocket-Protocol", "")
        subprotocols = [s.strip() for s in raw.split(",") if s.strip()]
        req = httpclient.HTTPRequest(url, headers=headers, connect_timeout=30)
        try:
            self._upstream = await websocket.websocket_connect(
                req, subprotocols=subprotocols or None, max_message_size=1 << 27
            )
        except Exception as exc:
            self.log.warning("dsh websocket upstream failed (%s): %r", url, exc)
            self.close(code=1011, reason="upstream failed")
            return
        self.set_nodelay(True)
        self._pump = asyncio.ensure_future(self._pump_upstream())

    async def _pump_upstream(self):
        try:
            while True:
                msg = await self._upstream.read_message()
                if msg is None:
                    self.close()
                    return
                self.write_message(msg, binary=isinstance(msg, bytes))
        except Exception:
            self.close()

    def on_message(self, message):
        upstream = getattr(self, "_upstream", None)
        if upstream is not None:
            try:
                upstream.write_message(message, binary=isinstance(message, bytes))
            except Exception:
                self.close()

    def on_close(self):
        pump = getattr(self, "_pump", None)
        if pump is not None:
            pump.cancel()
        upstream = getattr(self, "_upstream", None)
        if upstream is not None:
            try:
                upstream.close()
            except Exception:
                pass


class DshStatusHandler(JupyterHandler):
    """Small JSON endpoint (outside the proxied mount) for health checks."""

    @web.authenticated
    async def get(self):
        mgr = self.settings["dsh_manager"]
        self.set_header("Content-Type", "application/json")
        self.write(
            {
                "ready": mgr.ready,
                "failed": mgr.failed,
                "backend": mgr.base_url,
                "entry": self.base_url + "dsh/",
                "error": mgr.last_error,
            }
        )
