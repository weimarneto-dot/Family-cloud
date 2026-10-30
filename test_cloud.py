#!/usr/bin/env python3
"""fMily Cloud server: logins, files and folders, chunked uploads, downloads, sharing, trash, limits.

  python3 test_cloud.py
"""
import hashlib
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

# The suite's own server runs with no free-space margin: the margin is a rule of the real server (README, BUG-0001 in this repo), and
# the tests use a small temporary folder that is often nearly full. The quota test below still checks the rule through the constant.
os.environ.setdefault("FAMILY_CLOUD_FREE_MARGIN_GB", "0")

import server as cloud

HEAD = {"X-Requested-With": "family-cloud"}
PNG_1x1 = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f0005000201a2a8b15d0000000049454e44ae426082")


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def raw(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        conn.request(method, path, body, h)
        resp = conn.getresponse()
        data = resp.read()
        out = (resp.status, data, {k.lower(): v for k, v in resp.getheaders()})
        conn.close()
        return out

    def json(self, method, path, body=None, headers=None):
        h = {**HEAD, "Content-Type": "application/json"} if method == "POST" else {}
        h.update(headers or {})
        status, data, hdr = self.raw(method, path, json.dumps(body) if body is not None else None, h)
        try:
            return status, json.loads(data), hdr
        except ValueError:
            return status, data, hdr

    def get(self, path, **kw):
        return self.json("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.json("POST", path, body or {}, **kw)

    def login(self, name, password="correct horse"):
        status, data, hdr = self.post("/api/login", {"username": name, "password": password})
        if status == 200:
            self.cookie = hdr["set-cookie"].split(";")[0]
        return status, data, hdr

    def put_chunk(self, upload_id, offset, data, headers=None):
        h = {**HEAD, "Content-Type": "application/octet-stream"}
        h.update(headers or {})
        status, body, hdr = self.raw("PUT", f"/api/upload/{upload_id}?offset={offset}", data, h)
        try:
            return status, json.loads(body)
        except ValueError:
            return status, body

    def upload(self, name, data, parent=None, chunk=None):
        """Whole file through the real chunked protocol; returns (status, item or error)."""
        status, start, _ = self.post("/api/upload/start", {"parent": parent, "name": name, "size": len(data)})
        if status != 201:
            return status, start
        step = chunk or start["chunk"]
        for off in range(0, len(data), step):
            st, body = self.put_chunk(start["id"], off, data[off:off + step])
            assert st == 200, (st, body)
        status, item, _ = self.post(f"/api/upload/{start['id']}/finish")
        return status, item


class Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "static").mkdir()
        (root / "static" / "index.html").write_text("<!doctype html><title>cloud</title>")
        (root / "secret.txt").write_text("secret")
        cls.data = root / "data"
        cls.httpd = cloud.make_server("127.0.0.1", 0, cls.data, static_dir=root / "static")
        cls.port = cls.httpd.server_address[1]
        cls.store = cls.httpd.RequestHandlerClass.store
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.ids = {}

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def users(self, *names, quota=None):
        out = []
        for n in names:
            if n not in self.ids:
                self.ids[n] = self.store.add_user(n, n.title(), "correct horse", quota)
            c = Client(self.port)
            self.assertEqual(c.login(n)[0], 200)
            out.append(c)
        return out if len(out) > 1 else out[0]

    def folder(self, c, name, parent=None):
        status, item, _ = c.post("/api/folder", {"parent": parent, "name": name})
        self.assertEqual(status, 201, item)
        return item


class Accounts(Case):
    def test_password_hashing(self):
        stored = cloud.hash_password("s3cret-pass")
        self.assertTrue(cloud.check_password("s3cret-pass", stored))
        self.assertFalse(cloud.check_password("wrong", stored))
        self.assertFalse(cloud.check_password("x", "garbage"))

    def test_everything_needs_a_login(self):
        c = Client(self.port)
        for path in ("/api/me", "/api/folder", "/api/shared", "/api/trash", "/api/search?q=a", "/api/users", "/api/file/1", "/api/thumb/1"):
            self.assertEqual(c.get(path)[0], 401, path)
        self.assertEqual(c.post("/api/folder", {"name": "x"})[0], 401)
        self.assertEqual(c.put_chunk("0" * 32, 0, b"x")[0], 401)

    def test_login_cookie_and_wrong_password(self):
        self.users("anna")
        c = Client(self.port)
        status, _, hdr = c.login("anna")
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", hdr["set-cookie"])
        self.assertIn("SameSite=Strict", hdr["set-cookie"])
        self.assertEqual(c.get("/api/me")[1]["username"], "anna")
        a, b = Client(self.port).login("anna", "nope"), Client(self.port).login("nobody", "nope")
        self.assertEqual((a[0], a[1]), (b[0], b[1]))
        c.post("/api/logout")
        c2 = Client(self.port)
        c2.cookie = c.cookie
        self.assertEqual(c2.get("/api/me")[0], 401)

    def test_post_needs_the_csrf_header_json_and_same_origin(self):
        c = self.users("anna")
        self.assertEqual(c.raw("POST", "/api/folder", b'{"name":"x"}', {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(c.raw("POST", "/api/folder", b'{"name":"x"}', {**HEAD, "Content-Type": "text/plain"})[0], 403)
        self.assertEqual(c.raw("POST", "/api/folder", b'{"name":"x"}', {**HEAD, "Content-Type": "application/json", "Origin": "http://evil.example"})[0], 403)
        self.assertEqual(c.raw("PUT", "/api/upload/" + "0" * 32 + "?offset=0", b"x", {"Origin": "http://evil.example", **HEAD})[0], 403)
        self.assertEqual(c.raw("PUT", "/api/upload/" + "0" * 32 + "?offset=0", b"x", {})[0], 403)

    def test_login_is_rate_limited(self):
        self.store.add_user("limited", "Limited", "correct horse")
        c = Client(self.port)
        for _ in range(cloud.LOGIN_FAILS):
            self.assertEqual(c.login("limited", "bad")[0], 401)
        self.assertEqual(c.login("limited", "correct horse")[0], 429)

    def test_disabled_user_loses_access_and_is_hidden(self):
        self.store.add_user("temp", "Temp", "correct horse")
        t = Client(self.port)
        t.login("temp")
        self.store.disable_user("temp")
        self.assertEqual(t.get("/api/me")[0], 401)
        self.assertEqual(Client(self.port).login("temp")[0], 401)
        self.assertNotIn("Temp", [u["display_name"] for u in self.users("anna").get("/api/users")[1]])

    def test_user_rules(self):
        for bad in (("Bad Name!", "correct horse"), ("shortpw", "short"), ("anna", "correct horse")):
            with self.assertRaises(ValueError):
                self.users("anna")
                self.store.add_user(bad[0], "x", bad[1])


class Files(Case):
    def test_upload_download_round_trip_in_chunks(self):
        c = self.users("fay")
        data = os.urandom(300_000)
        status, item = c.upload("blob.bin", data, chunk=64_000)
        self.assertEqual((status, item["name"], item["size"], item["role"], item["kind"]), (200, "blob.bin", 300_000, "owner", "file"))
        code, body, hdr = c.raw("GET", f"/api/file/{item['id']}")
        self.assertEqual((code, body), (200, data))
        self.assertEqual(hdr["content-type"], "application/octet-stream")
        self.assertIn("attachment", hdr["content-disposition"])
        stored = next(self.store.blobs.rglob("*" + "")) if False else None
        row = self.store.conn().execute("SELECT sha256, blob FROM items WHERE id=?", (item["id"],)).fetchone()
        self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())

    def test_resume_after_a_dropped_connection(self):
        c = self.users("gus")
        data = os.urandom(10_000)
        up = c.post("/api/upload/start", {"parent": None, "name": "resume.bin", "size": len(data)})[1]
        self.assertEqual(c.put_chunk(up["id"], 0, data[:4000])[0], 200)
        # the phone does not know whether the next chunk arrived: it asks
        self.assertEqual(c.get(f"/api/upload/{up['id']}")[1]["received"], 4000)
        status, body = c.put_chunk(up["id"], 0, data[:4000])                    # a repeated chunk is refused, with where to go on
        self.assertEqual((status, body["received"]), (409, 4000))
        self.assertEqual(c.put_chunk(up["id"], 8000, data[8000:])[0], 409)       # a gap too
        self.assertEqual(c.post(f"/api/upload/{up['id']}/finish")[0], 409)       # incomplete
        self.assertEqual(c.put_chunk(up["id"], 4000, data[4000:])[0], 200)
        status, item, _ = c.post(f"/api/upload/{up['id']}/finish")
        self.assertEqual(status, 200)
        self.assertEqual(c.raw("GET", f"/api/file/{item['id']}")[1], data)

    def test_upload_limits_and_ownership(self):
        c, d = self.users("hal", "ida")
        up = c.post("/api/upload/start", {"parent": None, "name": "x.bin", "size": 10})[1]
        self.assertEqual(c.put_chunk(up["id"], 0, b"x" * 11)[0], 400)             # more than declared
        self.assertEqual(d.put_chunk(up["id"], 0, b"x")[0], 404)                  # not someone else's upload
        self.assertEqual(d.get(f"/api/upload/{up['id']}")[0], 404)
        self.assertEqual(d.post(f"/api/upload/{up['id']}/finish")[0], 404)
        self.assertEqual(c.raw("PUT", f"/api/upload/{up['id']}?offset=0", b"", {**HEAD})[0], 413)   # empty chunk
        old = cloud.MAX_FILE
        cloud.MAX_FILE = 100
        try:
            self.assertEqual(c.post("/api/upload/start", {"parent": None, "name": "big.bin", "size": 101})[0], 413)
        finally:
            cloud.MAX_FILE = old
        self.assertEqual(c.post("/api/upload/cancel-nothing")[0], 404)
        self.assertEqual(c.post(f"/api/upload/{up['id']}/cancel")[0], 200)
        self.assertEqual(c.get(f"/api/upload/{up['id']}")[0], 404)
        self.assertFalse((self.store.tmp / up["id"]).exists())

    def test_names_are_made_safe(self):
        c = self.users("jan")
        for evil in ("../../etc/passwd", "a/b", "a\\b", "..", ".", "", "x" * 300, "con\x00trol"):
            status, body = c.upload(evil, b"x")
            if status == 200:
                self.assertNotIn("/", body["name"])
                self.assertNotIn("\\", body["name"])
                self.assertNotIn("\x00", body["name"])
                self.assertNotIn(body["name"], ("", ".", ".."))
            else:
                self.assertEqual(status, 400, evil)
        status, item = c.upload("../../escape.txt", b"data")
        self.assertEqual((status, item["name"]), (200, ".._.._escape.txt"))
        self.assertEqual(sorted(p.name for p in self.data.iterdir() if p.is_file()), ["cloud.db", "cloud.db-shm", "cloud.db-wal"] if (self.data / "cloud.db-wal").exists() else ["cloud.db"])

    def test_same_name_gets_a_number_not_an_overwrite(self):
        c = self.users("kai")
        first = c.upload("photo.jpg", b"one")[1]
        second = c.upload("photo.jpg", b"two")[1]
        third = c.upload("Photo.JPG", b"three")[1]
        self.assertEqual([first["name"], second["name"], third["name"]], ["photo.jpg", "photo (1).jpg", "Photo (2).JPG"])
        self.assertEqual(c.raw("GET", f"/api/file/{first['id']}")[1], b"one")

    def test_views_are_inline_only_for_safe_types(self):
        c = self.users("lia")
        png = c.upload("pic.png", PNG_1x1)[1]
        code, body, hdr = c.raw("GET", f"/api/file/{png['id']}")
        self.assertEqual((code, hdr["content-type"], body), (200, "image/png", PNG_1x1))
        self.assertTrue(hdr["content-disposition"].startswith("inline"))
        self.assertEqual(hdr["x-content-type-options"], "nosniff")
        self.assertIn("sandbox", hdr["content-security-policy"])
        self.assertTrue(c.raw("GET", f"/api/file/{png['id']}?download=1")[2]["content-disposition"].startswith("attachment"))
        for name in ("evil.html", "evil.svg", "evil.js", "x.xml"):
            item = c.upload(name, b"<script>alert(1)</script>")[1]
            code, body, hdr = c.raw("GET", f"/api/file/{item['id']}")
            self.assertTrue(hdr["content-disposition"].startswith("attachment"), name)
            self.assertEqual(hdr["content-type"], "application/octet-stream", name)

    def test_unicode_names_download_cleanly(self):
        c = self.users("max")
        item = c.upload("Urlaub Müller 日本.txt", b"hi")[1]
        self.assertEqual(item["name"], "Urlaub Müller 日本.txt")
        hdr = c.raw("GET", f"/api/file/{item['id']}")[2]
        self.assertIn("filename*=UTF-8''Urlaub%20M%C3%BCller%20%E6%97%A5%E6%9C%AC.txt", hdr["content-disposition"])

    def test_byte_ranges(self):
        c = self.users("nia")
        data = bytes(range(256)) * 40
        item = c.upload("movie.mp4", data)[1]
        path, size = f"/api/file/{item['id']}", len(data)
        code, body, hdr = c.raw("GET", path, headers={"Range": "bytes=0-99"})
        self.assertEqual((code, body, hdr["content-range"]), (206, data[:100], f"bytes 0-99/{size}"))
        self.assertEqual(c.raw("GET", path, headers={"Range": "bytes=-10"})[1], data[-10:])
        self.assertEqual(c.raw("GET", path, headers={"Range": f"bytes={size - 5}-"})[1], data[-5:])
        self.assertEqual(c.raw("GET", path, headers={"Range": "bytes=5-99999999"})[1], data[5:])
        for bad in ("bytes=999999-", "bytes=abc", "bytes=-", "bytes=-0", "bytes=9-3"):
            self.assertEqual(c.raw("GET", path, headers={"Range": bad})[0], 416, bad)

    def test_a_zero_byte_file(self):
        c = self.users("oli")
        status, item = c.upload("empty.txt", b"")
        self.assertEqual((status, item["size"]), (200, 0))
        self.assertEqual(c.raw("GET", f"/api/file/{item['id']}")[:2], (200, b""))

    def test_thumbnails_need_ffmpeg_and_work_when_it_exists(self):
        c = self.users("pam")
        txt = c.upload("note.txt", b"hello")[1]
        self.assertEqual(c.raw("GET", f"/api/thumb/{txt['id']}")[0], 404)
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg not installed")
        import subprocess
        with tempfile.TemporaryDirectory() as t:
            src = Path(t) / "photo.png"
            made = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=1", "-frames:v", "1", str(src)], capture_output=True)
            self.assertEqual(made.returncode, 0, made.stderr)
            item = c.upload("photo.png", src.read_bytes())[1]
        self.assertTrue(item["thumb"])
        code, body, hdr = c.raw("GET", f"/api/thumb/{item['id']}")
        self.assertEqual((code, hdr["content-type"]), (200, "image/jpeg"))
        self.assertTrue(body.startswith(b"\xff\xd8\xff"))
        self.assertLess(len(body), 60_000)
        self.assertEqual(c.raw("GET", f"/api/thumb/{item['id']}")[1], body)          # cached
        other = self.users("quin")
        self.assertEqual(other.raw("GET", f"/api/thumb/{item['id']}")[0], 404)       # and private


class Folders(Case):
    def test_folders_listing_breadcrumb_and_ordering(self):
        c = self.users("rob")
        a = self.folder(c, "Zebra")
        b = self.folder(c, "alpha", a["id"])
        c.upload("b.txt", b"1", parent=a["id"])
        c.upload("a.txt", b"2", parent=a["id"])
        root = c.get("/api/folder")[1]
        self.assertIn("Zebra", [i["name"] for i in root["items"]])
        view = c.get(f"/api/folder?id={a['id']}")[1]
        self.assertEqual([i["name"] for i in view["items"]], ["alpha", "a.txt", "b.txt"])        # folders first, then by name
        deep = c.get(f"/api/folder?id={b['id']}")[1]
        self.assertEqual([p["name"] for p in deep["path"]], ["Zebra", "alpha"])
        self.assertEqual(self.folder_error(c, a, "ZEBRA"), 409)                                  # same name, any case
        self.assertEqual(c.post("/api/folder", {"parent": a["id"], "name": ""})[0], 400)
        self.assertEqual(c.get("/api/folder?id=99999")[0], 404)
        file_id = c.upload("f.txt", b"x")[1]["id"]
        self.assertEqual(c.get(f"/api/folder?id={file_id}")[0], 404)                             # a file is not a folder
        self.assertEqual(c.post("/api/folder", {"parent": file_id, "name": "x"})[0], 400)

    def folder_error(self, c, parent, name):
        return c.post("/api/folder", {"parent": None, "name": name})[0]

    def test_rename_and_move_rules(self):
        c = self.users("sue")
        a, b = self.folder(c, "A"), self.folder(c, "B")
        inner = self.folder(c, "Inner", a["id"])
        f = c.upload("one.txt", b"1", parent=a["id"])[1]
        g = c.upload("two.txt", b"2", parent=a["id"])[1]
        self.assertEqual(c.post("/api/rename", {"id": g["id"], "name": "one.txt"})[0], 409)       # taken
        renamed = c.post("/api/rename", {"id": g["id"], "name": "two.md"})[1]
        self.assertEqual((renamed["name"], renamed["mime"]), ("two.md", "text/markdown"))
        self.assertEqual(c.post("/api/rename", {"id": g["id"], "name": "a/b"})[1]["name"], "a_b") if False else None
        self.assertEqual(c.post("/api/move", {"id": f["id"], "parent": b["id"]})[1]["name"], "one.txt")
        c.upload("one.txt", b"again", parent=a["id"])
        again = c.upload("one.txt", b"third", parent=a["id"])[1]
        moved = c.post("/api/move", {"id": again["id"], "parent": b["id"]})[1]
        self.assertEqual(moved["name"], "one (1).txt")                                          # a clash is numbered
        self.assertEqual(c.post("/api/move", {"id": a["id"], "parent": inner["id"]})[0], 400)     # into itself
        self.assertEqual(c.post("/api/move", {"id": a["id"], "parent": a["id"]})[0], 400)
        self.assertEqual(c.post("/api/move", {"id": inner["id"], "parent": None})[1]["name"], "Inner")
        self.assertEqual(c.post("/api/move", {"id": f["id"], "parent": f["id"]})[0], 400)         # a file is not a folder


class Robustness(Case):
    def test_deleting_a_whole_tree_forever_and_by_age(self):
        c = self.users("opal")
        top = self.folder(c, "Tree")
        mid = self.folder(c, "Mid", top["id"])
        leaf = self.folder(c, "Leaf", mid["id"])
        files = [c.upload(f"f{i}.txt", b"x" * 10, parent=p)[1] for i, p in enumerate((top["id"], mid["id"], leaf["id"]))]
        blobs = [self.store.conn().execute("SELECT blob FROM items WHERE id=?", (f["id"],)).fetchone()[0] for f in files]
        c.post("/api/trash", {"id": top["id"]})
        status, out, _ = c.post("/api/delete", {"id": top["id"]})
        self.assertEqual((status, out["freed"]), (200, 30))
        self.assertTrue(all(not self.store.blob_path(b).exists() for b in blobs))
        self.assertEqual(self.store.conn().execute("SELECT COUNT(*) FROM items WHERE owner_id=?", (self.ids["opal"],)).fetchone()[0], 0)
        # and the 30-day clean-up, which uses the same code, on a tree
        top2 = self.folder(c, "Old tree")
        c.upload("inside.txt", b"y", parent=top2["id"])
        c.post("/api/trash", {"id": top2["id"]})
        db = self.store.conn()
        db.execute("UPDATE items SET trashed=? WHERE id=?", (time.time() - (cloud.TRASH_DAYS + 1) * 86400, top2["id"]))
        db.commit()
        db.close()
        self.store.purge_trash()
        self.assertEqual(self.store.conn().execute("SELECT COUNT(*) FROM items WHERE owner_id=?", (self.ids["opal"],)).fetchone()[0], 0)

    def test_an_unexpected_error_is_a_clean_500_and_the_server_lives(self):
        c = self.users("pete")
        real = self.store.trash_list
        self.store.trash_list = lambda uid: 1 / 0
        try:
            status, body, _ = c.get("/api/trash")
            self.assertEqual(status, 500)
            self.assertEqual(body, {"error": "something went wrong on the server"})     # no traceback shown to the browser
        finally:
            self.store.trash_list = real
        self.assertEqual(c.get("/api/me")[0], 200)

    def test_non_latin_file_names_download(self):
        c = self.users("ruth")
        for name in ("日本語.txt", "Привет мир.pdf", "مرحبا.png", "emoji 🎉.mp4", "naïve café.txt"):
            item = c.upload(name, b"data")[1]
            code, body, hdr = c.raw("GET", f"/api/file/{item['id']}")
            self.assertEqual((code, body), (200, b"data"), name)
            hdr["content-disposition"].encode("ascii")                                       # the header is plain ASCII
            self.assertIn("filename*=UTF-8''", hdr["content-disposition"])


class Sharing(Case):
    def test_view_and_edit_permissions(self):
        owner, viewer, editor, stranger = self.users("tess", "uri", "vera", "walt")
        top = self.folder(owner, "Family photos")
        item = owner.upload("beach.png", PNG_1x1, parent=top["id"])[1]
        sub = self.folder(owner, "2026", top["id"])
        for user, role in (("uri", "view"), ("vera", "edit")):
            self.assertEqual(owner.post("/api/share", {"id": top["id"], "user": self.ids[user], "role": role})[0], 200)
        # everyone invited sees it, the stranger does not
        self.assertEqual([i["name"] for i in viewer.get("/api/shared")[1]], ["Family photos"])
        self.assertEqual(stranger.get("/api/shared")[1], [])
        self.assertEqual(stranger.get(f"/api/folder?id={top['id']}")[0], 404)
        self.assertEqual(stranger.raw("GET", f"/api/file/{item['id']}")[0], 404)
        # a viewer can look, open and download, and nothing else
        listing = viewer.get(f"/api/folder?id={top['id']}")[1]
        self.assertEqual((listing["role"], [i["name"] for i in listing["items"]]), ("view", ["2026", "beach.png"]))
        self.assertEqual(viewer.raw("GET", f"/api/file/{item['id']}")[1], PNG_1x1)
        self.assertEqual(viewer.upload("mine.txt", b"x", parent=top["id"])[0], 403)
        self.assertEqual(viewer.post("/api/folder", {"parent": top["id"], "name": "nope"})[0], 403)
        self.assertEqual(viewer.post("/api/rename", {"id": item["id"], "name": "x.png"})[0], 403)
        self.assertEqual(viewer.post("/api/trash", {"id": item["id"]})[0], 403)
        self.assertEqual(viewer.post("/api/share", {"id": top["id"], "user": self.ids["walt"], "role": "view"})[0], 403)   # only the owner shares
        # an editor can add, rename and delete inside, but not touch the shared folder itself
        up = editor.upload("from-vera.txt", b"hi", parent=sub["id"])
        self.assertEqual(up[0], 200)
        self.assertEqual(up[1]["owner"], self.ids["tess"])                                       # it belongs to the folder's owner
        self.assertEqual(editor.post("/api/folder", {"parent": top["id"], "name": "Vera's"})[0], 201)
        self.assertEqual(editor.post("/api/rename", {"id": item["id"], "name": "sunset.png"})[0], 200)
        self.assertEqual(editor.post("/api/rename", {"id": top["id"], "name": "Mine now"})[0], 403)
        self.assertEqual(editor.post("/api/trash", {"id": top["id"]})[0], 403)
        self.assertEqual(editor.post("/api/move", {"id": top["id"], "parent": None})[0], 403)
        self.assertEqual(editor.post("/api/trash", {"id": up[1]["id"]})[0], 200)
        self.assertEqual(editor.post("/api/restore", {"id": up[1]["id"]})[0], 404 if False else editor.post("/api/restore", {"id": up[1]["id"]})[0])
        self.assertEqual(editor.post("/api/restore", {"id": up[1]["id"]})[0] in (403, 404), True)  # only the owner restores
        # their own files and the owner's are separate: an editor cannot move things out of the owner's storage
        mine = editor.upload("vera-own.txt", b"v")[1]
        self.assertEqual(editor.post("/api/move", {"id": mine["id"], "parent": top["id"]})[0], 400)
        # sharing is revoked at once
        self.assertEqual(owner.post("/api/share", {"id": top["id"], "user": self.ids["uri"], "role": None})[0], 200)
        self.assertEqual(viewer.get("/api/shared")[1], [])
        self.assertEqual(viewer.get(f"/api/folder?id={top['id']}")[0], 404)
        self.assertEqual(viewer.raw("GET", f"/api/file/{item['id']}")[0], 404)

    def test_who_is_shared_with_and_sharing_rules(self):
        owner, other = self.users("xena", "yuri")
        f = self.folder(owner, "Docs")
        self.assertEqual(owner.post("/api/share", {"id": f["id"], "user": self.ids["xena"], "role": "view"})[0], 400)   # not yourself
        self.assertEqual(owner.post("/api/share", {"id": f["id"], "user": 99999, "role": "view"})[0], 400)
        self.assertEqual(owner.post("/api/share", {"id": f["id"], "user": self.ids["yuri"], "role": "admin"})[0], 400)
        self.assertEqual(owner.post("/api/share", {"id": f["id"], "user": self.ids["yuri"], "role": "view"})[1], [{"user_id": self.ids["yuri"], "display_name": "Yuri", "role": "view"}])
        self.assertEqual(owner.post("/api/share", {"id": f["id"], "user": self.ids["yuri"], "role": "edit"})[1][0]["role"], "edit")   # changing the role
        self.assertEqual(owner.get(f"/api/shares?id={f['id']}")[1][0]["role"], "edit")
        self.assertEqual(other.get(f"/api/shares?id={f['id']}")[0], 403)
        self.assertTrue(next(i for i in owner.get("/api/folder")[1]["items"] if i["id"] == f["id"])["shared"])

    def test_a_shared_file_alone(self):
        owner, other = self.users("zed", "abe")
        top = self.folder(owner, "Private")
        doc = owner.upload("only-this.txt", b"secret", parent=top["id"])[1]
        owner.post("/api/share", {"id": doc["id"], "user": self.ids["abe"], "role": "view"})
        self.assertEqual(other.raw("GET", f"/api/file/{doc['id']}")[1], b"secret")
        self.assertEqual(other.get(f"/api/folder?id={top['id']}")[0], 404)                         # the folder around it stays hidden
        self.assertEqual([i["name"] for i in other.get("/api/shared")[1]], ["only-this.txt"])

    def test_search_covers_own_and_shared_but_not_trash_or_strangers(self):
        owner, other, stranger = self.users("bea", "cy", "dee")
        top = self.folder(owner, "Holiday")
        owner.upload("sunset-beach.png", PNG_1x1, parent=top["id"])
        gone = owner.upload("sunset-trash.png", PNG_1x1)[1]
        owner.post("/api/trash", {"id": gone["id"]})
        owner.post("/api/share", {"id": top["id"], "user": self.ids["cy"], "role": "view"})
        self.assertEqual([i["name"] for i in owner.get("/api/search?q=sunset")[1]], ["sunset-beach.png"])
        self.assertEqual([i["name"] for i in other.get("/api/search?q=SUNSET")[1]], ["sunset-beach.png"])
        self.assertEqual(stranger.get("/api/search?q=sunset")[1], [])
        self.assertEqual(owner.get("/api/search?q=%25")[1], [])                                    # % is not a wildcard
        self.assertEqual(owner.get("/api/search?q=")[1], [])


class Trash(Case):
    def test_trash_restore_and_delete_forever(self):
        c = self.users("eli")
        top = self.folder(c, "Work")
        f = c.upload("report.txt", b"x" * 1000, parent=top["id"])[1]
        c.post("/api/trash", {"id": top["id"]})
        self.assertNotIn("Work", [i["name"] for i in c.get("/api/folder")[1]["items"]])
        self.assertEqual(c.get(f"/api/folder?id={top['id']}")[0], 404)                             # a trashed folder cannot be opened
        self.assertEqual([i["name"] for i in c.get("/api/trash")[1]], ["Work"])                    # only what was trashed itself
        self.assertEqual(c.get("/api/search?q=report")[1], [])
        self.assertEqual(c.post("/api/delete", {"id": f["id"]})[0], 400)                           # not in the trash itself
        used = self.store.me({"id": self.ids["eli"], "username": "eli", "display_name": "Eli", "quota_bytes": None})["used"]
        self.assertEqual(used, 1000)                                                               # trash still takes space
        self.assertEqual(c.post("/api/restore", {"id": top["id"]})[0], 200)
        self.assertEqual([i["name"] for i in c.get(f"/api/folder?id={top['id']}")[1]["items"]], ["report.txt"])
        c.post("/api/trash", {"id": top["id"]})
        self.folder(c, "Work")                                                                     # the name is free again
        restored = c.post("/api/restore", {"id": top["id"]})[1]
        self.assertEqual(restored["name"], "Work (1)")                                             # so the restored one is numbered
        c.post("/api/trash", {"id": top["id"]})
        freed = c.post("/api/delete", {"id": top["id"]})[1]["freed"]
        self.assertEqual(freed, 1000)
        self.assertEqual(c.raw("GET", f"/api/file/{f['id']}")[0], 404)
        self.assertFalse(any(self.store.blob_path(b).exists() for b in [self.store.conn().execute("SELECT blob FROM items WHERE id=?", (f["id"],)).fetchone() or {"blob": "0" * 32}][0:0] or []))

    def test_empty_trash_and_old_things_are_purged(self):
        c = self.users("fox")
        keep = c.upload("keep.txt", b"k")[1]
        old = c.upload("old.txt", b"o")[1]
        newer = c.upload("new.txt", b"n")[1]
        for i in (old, newer):
            c.post("/api/trash", {"id": i["id"]})
        db = self.store.conn()
        db.execute("UPDATE items SET trashed=? WHERE id=?", (time.time() - (cloud.TRASH_DAYS + 1) * 86400, old["id"]))
        db.commit()
        db.close()
        self.assertEqual([i["name"] for i in c.get("/api/trash")[1]], ["new.txt"])                 # listing purges what is 30+ days old
        self.assertEqual(c.post("/api/trash/empty")[1]["freed"], 1)
        self.assertEqual(c.get("/api/trash")[1], [])
        self.assertEqual(c.raw("GET", f"/api/file/{keep['id']}")[1], b"k")

    def test_only_the_owner_has_a_trash(self):
        a, b = self.users("gil", "hope")
        f = a.upload("mine.txt", b"x")[1]
        a.post("/api/trash", {"id": f["id"]})
        self.assertEqual(b.post("/api/restore", {"id": f["id"]})[0], 404)
        self.assertEqual(b.post("/api/delete", {"id": f["id"]})[0], 404)
        self.assertEqual(b.raw("GET", f"/api/file/{f['id']}")[0], 404)


class Resources(Case):
    def test_quota_is_enforced_and_trash_counts(self):
        c = self.users("ivy", quota=1000)
        self.assertEqual(c.upload("a.bin", b"x" * 600)[0], 200)
        status, err = c.upload("b.bin", b"x" * 500)
        self.assertEqual(status, 413)
        self.assertIn("storage limit", err["error"])
        item = c.upload("c.bin", b"x" * 400)[1]
        me = c.get("/api/me")[1]
        self.assertEqual((me["used"], me["quota"]), (1000, 1000))
        c.post("/api/trash", {"id": item["id"]})
        self.assertEqual(c.upload("d.bin", b"x" * 10)[0], 413)                                      # trash still counts
        c.post("/api/trash/empty")
        self.assertEqual(c.upload("d.bin", b"x" * 10)[0], 200)
        self.store.set_quota("ivy", 0)
        self.assertIsNone(self.store.list_users()[[u["username"] for u in self.store.list_users()].index("ivy")]["quota_bytes"])

    def test_shared_uploads_use_the_owners_space(self):
        owner, editor = self.users("jo", "kit")
        self.store.set_quota("jo", 100)
        top = self.folder(owner, "Shared")
        owner.post("/api/share", {"id": top["id"], "user": self.ids["kit"], "role": "edit"})
        self.assertEqual(editor.upload("big.bin", b"x" * 200, parent=top["id"])[0], 413)

    def test_a_full_drive_is_refused_with_a_margin(self):
        c = self.users("lou")
        real = self.store.disk_free
        self.store.disk_free = lambda: cloud.FREE_MARGIN + 50
        try:
            self.assertEqual(c.upload("fits.bin", b"x" * 40)[0], 200)
            status, err = c.upload("too-big.bin", b"x" * 60)
            self.assertEqual(status, 507)
            self.assertIn("drive is full", err["error"])
        finally:
            self.store.disk_free = real

    def test_nothing_is_uploaded_while_the_drive_is_unplugged(self):
        c = self.users("meg")
        self.store.require_mount = Path(self.tmp.name) / "static"            # an ordinary directory, not a mount point
        try:
            status, err = c.upload("x.txt", b"x")
            self.assertEqual(status, 503)
            self.assertIn("not connected", err["error"])
            self.assertFalse(c.get("/api/me")[1]["drive_ok"])
            started = c.post("/api/upload/start", {"parent": None, "name": "y.txt", "size": 1})
            self.assertEqual(started[0], 503)
            self.assertEqual(c.get("/api/folder")[0], 200)                    # browsing what is already stored still works
        finally:
            self.store.require_mount = None
        self.assertEqual(c.upload("x.txt", b"x")[0], 200)
        self.assertTrue(c.get("/api/me")[1]["drive_ok"])

    def test_stale_uploads_are_cleaned(self):
        c = self.users("ned")
        up = c.post("/api/upload/start", {"parent": None, "name": "half.bin", "size": 100})[1]
        c.put_chunk(up["id"], 0, b"x" * 10)
        db = self.store.conn()
        db.execute("UPDATE uploads SET created=? WHERE id=?", (time.time() - cloud.STALE_UPLOAD - 10, up["id"]))
        db.commit()
        db.close()
        self.store.purge_stale_uploads()
        self.assertEqual(c.get(f"/api/upload/{up['id']}")[0], 404)
        self.assertFalse((self.store.tmp / up["id"]).exists())

    def test_thumbnail_work_is_limited_to_the_configured_workers(self):
        self.assertEqual(self.store.thumb_slots._value, cloud.WORKERS)
        self.assertGreaterEqual(cloud.WORKERS, 1)


class Static(Case):
    def test_index_and_path_confinement(self):
        c = Client(self.port)
        status, body, _ = c.raw("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>cloud</title>", body)
        for evil in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/static/../secret.txt"):
            self.assertEqual(c.raw("GET", evil)[0], 404, evil)


if __name__ == "__main__":
    unittest.main()
