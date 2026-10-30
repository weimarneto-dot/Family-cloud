#!/usr/bin/env python3
"""A signed-in person who was not given Cloud: 403 not_member with the family's message (the same answer Forms, Mail, Photos and Writer give), never a login form.
A signed-out visitor still gets the sign-in (401). The admin is always let in.

  python3 test_not_shared.py
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

CSRF = "family-cloud"
MESSAGE = "Your account does not include fMily Cloud. Ask the family admin to tick it for you under People."
PEOPLE = {   # email: (password, name, admin, apps the account was given: None = all)
    "anna@example.org": ("pw-anna-1234", "Anna", True, None),
    "ben@example.org": ("pw-ben-12345", "Ben", False, ["songs", "explorer"]),          # not given Cloud
    "cat@example.org": ("pw-cat-12345", "Cat", False, ["songs", "cloud"]),       # given it
}


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
        email = self.sessions.get(self.token())
        if self.path == "/api/me" and email:
            pw, name, admin, apps = PEOPLE[email]
            return self._json({"email": email, "name": name, "admin": admin, "access": {"apps": apps}})
        self._json({"error": "sign in"}, 401)

    def do_POST(self):
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


def sign_in(email):
    t = secrets.token_urlsafe(24)
    FakeDW.sessions[t] = email
    return "dlw_session=" + t


def get(port, path, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Cookie": cookie} if cookie else {})
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, raw


def post(port, path, cookie, csrf, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", path, json.dumps(body or {}), {"Cookie": cookie, "X-Requested-With": csrf, "Content-Type": "application/json"})
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, raw


class NotShared(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dw = ThreadingHTTPServer(("127.0.0.1", 0), FakeDW)
        threading.Thread(target=cls.dw.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        up = f"http://127.0.0.1:{cls.dw.server_address[1]}"
        cls.httpd = app.make_server("127.0.0.1", 0, Path(cls.tmp.name) / "data", sso_upstream=up)
        cls.httpd.daemon_threads = True
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.dw.shutdown()
        cls.dw.server_close()
        cls.tmp.cleanup()

    PATH = "/api/me"

    def test_a_signed_out_visitor_is_asked_to_sign_in(self):
        status, raw = get(self.port, self.PATH)
        self.assertEqual(status, 401)
        self.assertNotIn(b"not_member", raw)

    def test_a_person_who_was_not_given_the_app_gets_403_not_member_with_the_family_message(self):
        status, raw = get(self.port, self.PATH, sign_in("ben@example.org"))
        data = json.loads(raw)
        self.assertEqual((status, data.get("error")), (403, "not_member"))
        self.assertEqual(data.get("message"), MESSAGE)
        self.assertEqual(data.get("email"), "ben@example.org")

    def test_a_person_who_was_given_the_app_gets_in(self):
        self.assertEqual(get(self.port, self.PATH, sign_in("cat@example.org"))[0], 200)

    def test_the_admin_is_always_let_in(self):
        self.assertEqual(get(self.port, self.PATH, sign_in("anna@example.org"))[0], 200)

    def test_sign_out_works_for_the_person_who_was_not_given_the_app(self):
        """The message page offers Sign out: it must really sign the person out of the family login (one sign-out for every app), not answer 403 again."""
        cookie = sign_in("ben@example.org")
        status, raw = post(self.port, "/api/logout", cookie, CSRF)
        self.assertEqual(status, 200, raw)
        self.assertNotIn(cookie.split("=", 1)[1], FakeDW.sessions)
        self.assertEqual(get(self.port, self.PATH, cookie)[0], 401)


if __name__ == "__main__":
    unittest.main()
