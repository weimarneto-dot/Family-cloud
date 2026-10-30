"""One login for the family apps: Downloader Web's accounts are the identity, this app just asks it who you are.

Downloader Web (DW) hands out the cookie `dlw_session`. Browsers send a cookie to every port of the same host name, so
a person signed in to DW (or to any app that uses this module) already carries it when they open this app. The app
then asks DW `GET /api/me` with that cookie and maps the answer (an e-mail address) to its own user row. Passwords are
only ever checked by DW; this app never stores or sees the DW password beyond forwarding it once, on sign-in.

Standard library only. The same file is used by fMily Chat, fMily Cloud and fMily Maps.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request

DW_COOKIE = "dlw_session"
DW_HEADER = ("X-Requested-With", "downloader-web")      # what DW wants on every state-changing request
USERNAME_OK = re.compile(r"^[a-z0-9_.-]{2,32}$")


def username_from_email(email: str, taken) -> str:
    """A username a-z 0-9 _ . - (2-32 characters) from the part of the e-mail before the @; `taken(name)` says if it is used."""
    base = re.sub(r"[^a-z0-9_.-]", "", email.split("@")[0].lower())[:28] or "user"
    if len(base) < 2:
        base += "_"
    name, n = base, 1
    while taken(name):
        n += 1
        name = f"{base}{n}"
    return name


class Sso:
    # a "yes" is kept 5 s too: an account removed or signed out in Downloader Web stops working here within 5 s, not 30
    def __init__(self, upstream: str, ttl: float = 5.0, timeout: float = 4.0):
        self.upstream = upstream.rstrip("/")
        self.ttl, self.timeout = ttl, timeout
        self._cache: dict = {}
        self._lock = threading.Lock()

    def _request(self, method, path, token=None, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.upstream + path, data=data, method=method)
        req.add_header(*DW_HEADER)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Cookie", f"{DW_COOKIE}={token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}"), resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, {}, exc.headers
        except (OSError, ValueError):
            return 0, {}, {}

    def whoami(self, token: "str | None") -> "dict | None":
        """{"email", "name", "admin"} for a valid DW session cookie, else None. Answers are kept for `ttl` seconds."""
        if not token or len(token) > 200:
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        status, data, _ = self._request("GET", "/api/me", token)
        acc = data.get("access") if isinstance(data.get("access"), dict) else {}
        apps = acc.get("apps")
        who = {"email": str(data["email"]).strip().lower(), "name": str(data.get("name") or ""), "admin": bool(data.get("admin")),
               "apps": [str(a) for a in apps] if isinstance(apps, list) else None} \
            if status == 200 and data.get("email") else None
        with self._lock:
            if len(self._cache) > 500:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            # a "no" is kept shorter: a person who has just signed in should not wait long
            self._cache[key] = (now + (self.ttl if who else min(self.ttl, 5.0)), who)
        return who

    def directory(self, token: "str | None") -> list:
        """[{"email", "name"}] of the family's accounts, from Downloader Web, for the person with this session (empty when it does not answer: an older Downloader Web)."""
        if not token:
            return []
        status, data, _ = self._request("GET", "/api/people", token)
        return [p for p in data if isinstance(p, dict) and p.get("email")] if status == 200 and isinstance(data, list) else []

    def login(self, email: str, password: str) -> "tuple[str, int] | tuple[None, int]":
        """(session token, DW's max-age) when DW accepts the e-mail and password, else (None, status)."""
        status, _, headers = self._request("POST", "/api/login", None, {"email": email, "password": password})
        if status != 200:
            return None, status
        cookie = headers.get("Set-Cookie", "") if headers else ""
        m = re.search(DW_COOKIE + r"=([^;]+)", cookie)
        age = re.search(r"Max-Age=(\d+)", cookie)
        return (m.group(1), int(age.group(1)) if age else 30 * 86400) if m else (None, 502)

    def logout(self, token: "str | None") -> None:
        if token:
            self._request("POST", "/api/logout", token, {})
            with self._lock:
                self._cache.pop(hashlib.sha256(token.encode()).hexdigest(), None)


def cookie_value(cookie_header: str, name: str) -> "str | None":
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def is_funnel(headers) -> bool:
    """Tailscale Funnel marks requests that come from the public internet with this header."""
    return bool(headers.get("Tailscale-Funnel-Request"))


def allows(who, app_id: str) -> bool:
    """False when the account was not given this app (the admin chooses on the People page of fMily Home); the admin account itself is always let in."""
    apps = who.get("apps")
    return bool(who.get("admin")) or apps is None or app_id in apps


# ---- behind a reverse proxy on this machine (tailscale serve): which host and scheme did the browser use? --------------------------------------------------
# The Host header may arrive as the tailnet name or rewritten to 127.0.0.1:<port>, and the X-Forwarded-* headers depend on the Tailscale version, so nothing may rely on one variant.
# The trust boundary: a request is "from a local proxy" when its TCP peer is THIS machine and it carries X-Forwarded-For (a proxy that forwards sets it). Only then are
# X-Forwarded-Host and X-Forwarded-Proto believed. The services listen on this machine or the home network; a peer on the network is never believed, whatever it sends.
LOOPBACK_PEERS = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
_HOSTPORT = __import__("re").compile(r"[a-z0-9.-]{1,253}(:[0-9]{1,5})?")


def from_local_proxy(headers, peer: str) -> bool:
    return peer in LOOPBACK_PEERS and bool(headers.get("X-Forwarded-For"))


def effective_host(headers, peer: str) -> str:
    """The host[:port] the browser used: X-Forwarded-Host from a proxy on this machine (the first value, lower case, only if it looks like a host), else the Host header."""
    if from_local_proxy(headers, peer):
        fwd = (headers.get("X-Forwarded-Host") or "").split(",")[0].strip().lower()
        if fwd and _HOSTPORT.fullmatch(fwd):
            return fwd
    return headers.get("Host", "") or ""


def effective_https(headers, peer: str) -> bool:
    """True when a proxy on this machine says the browser used https (X-Forwarded-Proto), or forwarded a request for a *.ts.net name without saying (older tailscale serve)."""
    if not from_local_proxy(headers, peer):
        return False
    proto = (headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    return proto == "https" or (not proto and effective_host(headers, peer).split(":")[0].endswith(".ts.net"))
