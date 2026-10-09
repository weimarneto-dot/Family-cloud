#!/usr/bin/env python3
"""Family Cloud: single sign-on through a (fake) Downloader Web.

  python3 test_sso.py
"""
import http.client
import json
import secrets
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import server as app

PEOPLE = {"anna@example.org": ("pw-anna-1234", "Anna Silva", True), "bob@example.org": ("pw-bob-1234", "Bob", False)}


class FakeDW(BaseHTTPRequestHandler):
    sessions = {}

    def log_message(self, *a):
        pass

    def _json(self, data, status=200, cookie=None):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "dlw_session":
                return v

    def do_GET(self):
        if self.headers.get("X-Requested-With") != "downloader-web":
            return self._json({}, 403)
        email = self.sessions.get(self.token())
        if self.path == "/api/me" and email:
            pw, name, admin = PEOPLE[email]
            return self._json({"email": email, "name": name, "admin": admin})
        self._json({"error": "sign in"}, 401)

    def do_POST(self):
        if self.headers.get("X-Requested-With") != "downloader-web":
            return self._json({}, 403)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/login":
            p = PEOPLE.get(body.get("email"))
            if not p or p[0] != body.get("password"):
                return self._json({"error": "wrong"}, 401)
            t = secrets.token_urlsafe(24)
            self.sessions[t] = body["email"]
            return self._json({"ok": True}, cookie=f"dlw_session={t}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
        if self.path == "/api/logout":
            self.sessions.pop(self.token(), None)
            return self._json({"ok": True})
        self._json({}, 404)


def call(port, method, path, body=None, cookie=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"X-Requested-With": "family-cloud", "Content-Type": "application/json", **(headers or {})}
    if cookie:
        h["Cookie"] = cookie
    conn.request(method, path, json.dumps(body) if body is not None else None, h)
    r = conn.getresponse()
    raw = r.read()
    out = (r.status, json.loads(raw or b"{}"), r.getheader("Set-Cookie"))
    conn.close()
    return out


class SsoCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dw = ThreadingHTTPServer(("127.0.0.1", 0), FakeDW)
        threading.Thread(target=cls.dw.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.httpd = app.make_server("127.0.0.1", 0, Path(cls.tmp.name) / "data",
                                    sso_upstream=f"http://127.0.0.1:{cls.dw.server_address[1]}")
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.port = cls.httpd.server_address[1]
        cls.store = cls.httpd.RequestHandlerClass.store

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.dw.shutdown()
        cls.tmp.cleanup()

    def test_1_not_signed_in(self):
        self.assertEqual(call(self.port, "GET", "/api/me")[0], 401)
        self.assertEqual(call(self.port, "GET", "/api/me", cookie="dlw_session=nonsense")[0], 401)

    def test_2_email_login_makes_user_and_sets_family_cookie(self):
        status, data, cookie = call(self.port, "POST", "/api/login", {"username": "Anna@Example.org", "password": "pw-anna-1234"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["username"], "anna")
        self.assertEqual(data["display_name"], "Anna Silva")
        self.assertTrue(cookie.startswith("dlw_session="))
        self.assertIn("HttpOnly", cookie)
        jar = cookie.split(";")[0]
        status, me, _ = call(self.port, "GET", "/api/me", cookie=jar)
        self.assertEqual((status, me["username"]), (200, "anna"))

    def test_3_cookie_from_another_app_is_enough(self):
        # signed in at Downloader Web (or Maps) only; opening Chat needs no second login
        token = secrets.token_urlsafe(24)
        FakeDW.sessions[token] = "bob@example.org"
        status, me, _ = call(self.port, "GET", "/api/me", cookie=f"dlw_session={token}")
        self.assertEqual((status, me["username"]), (200, "bob"))
        again = call(self.port, "GET", "/api/me", cookie=f"dlw_session={token}")[1]
        self.assertEqual(again["id"], me["id"])                 # the same user row, not a new one each time

    def test_4_wrong_password(self):
        status, data, cookie = call(self.port, "POST", "/api/login", {"username": "anna@example.org", "password": "nope"})
        self.assertEqual(status, 401)
        self.assertIsNone(cookie)

    def test_5_funnel_visitors_cannot_try_family_passwords(self):
        status, _, cookie = call(self.port, "POST", "/api/login", {"username": "anna@example.org", "password": "pw-anna-1234"},
                                 headers={"Tailscale-Funnel-Request": "?1"})
        self.assertEqual(status, 403)
        self.assertIsNone(cookie)

    def test_6_local_account_still_works(self):
        self.store.add_user("carol", "Carol", "correct horse")
        status, data, cookie = call(self.port, "POST", "/api/login", {"username": "carol", "password": "correct horse"},
                                    headers={"Tailscale-Funnel-Request": "?1"})
        self.assertEqual(status, 200)
        self.assertTrue(cookie.startswith("cloud_session="))

    def test_7_link_existing_user_to_family_login(self):
        self.store.add_user("dora", "Dora", "correct horse")
        FakeDW.sessions["t-dora"] = "dora@example.org"
        PEOPLE["dora@example.org"] = ("pw-dora-1234", "Dora D", False)
        self.store.link_sso("dora", "dora@example.org")
        status, me, _ = call(self.port, "GET", "/api/me", cookie="dlw_session=t-dora")
        self.assertEqual((status, me["username"]), (200, "dora"))     # her old chats, not a second account
        with self.assertRaises(ValueError):
            self.store.link_sso("carol", "dora@example.org")          # one address, one user

    def test_8_username_collision_gets_a_number(self):
        self.store.add_user("eve", "Eve (local)", "correct horse")
        PEOPLE["eve@example.org"] = ("pw-eve-12345", "Eve", False)
        status, data, _ = call(self.port, "POST", "/api/login", {"username": "eve@example.org", "password": "pw-eve-12345"})
        self.assertEqual((status, data["username"]), (200, "eve2"))   # never takes over the local "eve"

    def test_9_logout_ends_the_family_session(self):
        status, data, cookie = call(self.port, "POST", "/api/login", {"username": "bob@example.org", "password": "pw-bob-1234"})
        jar = cookie.split(";")[0]
        status, _, out = call(self.port, "POST", "/api/logout", {}, cookie=jar)
        self.assertEqual(status, 200)
        self.assertIn("dlw_session=;", out)
        self.assertEqual(call(self.port, "GET", "/api/me", cookie=jar)[0], 401)   # DW forgot it too

    def test_10_without_sso_nothing_changes(self):
        tmp = tempfile.TemporaryDirectory()
        httpd = app.make_server("127.0.0.1", 0, Path(tmp.name) / "data")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            self.assertEqual(call(httpd.server_address[1], "GET", "/api/me", cookie="dlw_session=x")[0], 401)
            status, _, _ = call(httpd.server_address[1], "POST", "/api/login", {"username": "a@b.c", "password": "x"})
            self.assertEqual(status, 401)
        finally:
            httpd.shutdown()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
