"""Manage the `dsh web` child process for the JupyterLab extension.

deepseek-harness intentionally refuses wildcard binds (`--host 0.0.0.0` is
rejected at startup), so the GUI always listens on loopback.  This manager
spawns it as a child of the Jupyter server, parses the launch token from the
`dsh web:` readiness line, and exposes the loopback base URL to the proxy
handlers.  All browser-facing safety is preserved: the outside world only ever
reaches JupyterLab, and JupyterLab authenticates every proxied request.
"""

import asyncio
import os
import re
import shlex
import signal
import time
from urllib.parse import urlparse, quote, parse_qs

from tornado import httpclient

_DSH_LINE = re.compile(r"dsh web:\s+(https?://\S+)")


class DshManager:
    """Spawn / watch the dsh web process and remember its entry token."""

    def __init__(self, config, log, cwd=None):
        self.log = log
        self.config = config
        self.cwd = cwd or os.getcwd()
        self.proc = None
        self.base_url = None          # e.g. http://127.0.0.1:41234
        self.ws_base_url = None       # e.g. ws://127.0.0.1:41234
        self.token = None
        self.ready = False
        self.failed = False
        self.last_error = None
        self._log_tail = []
        self._task = None
        self._stopping = False

    # ------------------------------------------------------------------ utils
    def _record(self, line):
        self.log.info("[dsh] %s", line)
        self._log_tail.append(line)
        del self._log_tail[:-40]

    @property
    def port(self):
        if self.base_url is None:
            return None
        return urlparse(self.base_url).port

    def http_client(self):
        return httpclient.AsyncHTTPClient()

    # ------------------------------------------------------------------ spawn
    def start_background(self):
        if self._task is None:
            self._task = asyncio.ensure_future(self._run_forever())

    async def _run_forever(self):
        delay = 1.0
        while not self._stopping:
            ok = await self._spawn_once()
            if self._stopping:
                return
            if ok:
                delay = 1.0
            self.failed = not ok
            if not ok:
                self.last_error = (
                    "dsh exited before becoming ready. Recent output:\n  "
                    + "\n  ".join(self._log_tail[-8:])
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _spawn_once(self):
        """Run one dsh process; return True if it reached readiness."""
        self.ready = False
        self.token = None
        self.base_url = None

        port = int(getattr(self.config, "port", 0) or 0)
        cmd_str = self.config.command.strip()
        if cmd_str:
            cmd = shlex.split(cmd_str)
        else:
            cmd = ["dsh", "web", "--no-open", "--port", str(port)]

        env = dict(os.environ)
        env.setdefault("DSH_NO_ANALYTICS", "1")

        self._record("starting: " + " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.cwd,
                env=env,
                start_new_session=True,
            )
        except Exception as exc:  # dsh missing, node missing, ...
            self._record("spawn failed: %r" % (exc,))
            self.last_error = "could not start dsh: %r" % (exc,)
            await asyncio.sleep(2)
            return False

        self.proc = proc
        got_url = False
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            self._record(text)
            m = _DSH_LINE.search(text)
            if m and not got_url:
                url = m.group(1)
                self._parse_startup_url(url)
                got_url = True

        await proc.wait()
        self._record("dsh process exited with code %s" % proc.returncode)
        self.proc = None
        self.ready = False
        return got_url

    def _parse_startup_url(self, url):
        try:
            q = urlparse(url)
            token = (parse_qs(q.query).get("token") or [None])[0]
        except Exception:
            token = None
        if not token:
            self._record("startup line carried no token: " + url)
            return
        self.token = token
        self.base_url = "http://%s" % q.netloc
        self.ws_base_url = "ws://%s" % q.netloc
        self.ready = True
        self.failed = False
        self.last_error = None
        self._record("dsh ready at %s" % self.base_url)

    # ---------------------------------------------------------------- waiting
    async def wait_ready(self, timeout=60):
        waited = 0.0
        while not self.ready and waited < timeout:
            await asyncio.sleep(0.5)
            waited += 0.5
        return self.ready

    # ------------------------------------------------------------------ stop
    def stop(self):
        self._stopping = True
        proc = self.proc
        if proc is None:
            return
        pid = proc.pid
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(30):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass

    # ------------------------------------------------------------- entry URLs
    def token_url(self):
        if not self.ready:
            return None
        return "%s/?token=%s" % (self.base_url, quote(self.token))
