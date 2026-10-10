#!/usr/bin/env python3
"""fMily Cloud: private file storage for the family, like Google Drive or iCloud, on the home server.
Standard library only (ffmpeg, if installed, makes thumbnails).

Everyone has their own files and folders behind a login, can share a file or folder with another family
member (view or edit), and gets a trash that keeps deleted things for 30 days. Uploads are chunked and can
resume after the phone loses its connection; downloads support byte ranges, so video plays and seeks.
The web app in static/ is an installable PWA with the same look as fMily Chat.

  python3 server.py add-user anna --name "Anna" [--quota-gb 200]     # asks for a password; no quota = the drive's limit
  python3 server.py list-users | reset-password anna | disable-user anna | set-quota anna 200 (0 = no limit)
  python3 server.py serve [--host 127.0.0.1] [--port 8778] [--secure-cookie] [--data-dir DIR] [--require-mount DIR]

Resources: files go in the data directory (default ~/.local/share/family-cloud, or FAMILY_CLOUD_DATA): point it
at the big drive. --require-mount DIR refuses every upload while DIR is not a mount point, so nothing silently
fills the internal disk when the drive is unplugged. The server always leaves FAMILY_CLOUD_FREE_MARGIN_GB (5)
free on the drive. Thumbnails use up to FAMILY_CLOUD_WORKERS ffmpeg processes (default: all cores) at low priority.

Not yet: sync clients (WebDAV), link sharing outside the family, automatic photo backup from the phone, end-to-end
encryption (the server can read every file).
"""
import argparse
import getpass
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import sso as sso_mod


def frame_ancestors(host_header):
    """Who may show this app in a frame: fMily Home, which is another port of this same machine (nobody else)."""
    host = (host_header or "").rsplit(":", 1)[0].strip("[]").lower()
    if host and re.fullmatch(r"[a-z0-9.-]{1,253}", host):
        return f"frame-ancestors https://{host}:* http://{host}:*"
    return "frame-ancestors 'none'"

HERE = Path(__file__).resolve().parent
DEFAULT_DATA = Path(os.environ.get("FAMILY_CLOUD_DATA", Path.home() / ".local" / "share" / "family-cloud"))
COOKIE = "cloud_session"
CSRF_HEADER = ("X-Requested-With", "family-cloud")
SESSION_DAYS = 30
MAX_JSON = 64 * 1024
CHUNK = 4 * 1024 * 1024                  # what the web app sends per request
MAX_CHUNK = 8 * 1024 * 1024              # what the server accepts per request
GB = 1024 ** 3
MAX_FILE = int(float(os.environ.get("FAMILY_CLOUD_MAX_FILE_GB", "50")) * GB)
FREE_MARGIN = int(float(os.environ.get("FAMILY_CLOUD_FREE_MARGIN_GB", "5")) * GB)
WORKERS = max(1, int(os.environ.get("FAMILY_CLOUD_WORKERS", os.cpu_count() or 2)))
TRASH_DAYS = 30
STALE_UPLOAD = 24 * 3600
LOGIN_FAILS = 5
USERNAME = re.compile(r"^[a-z0-9_.-]{2,32}$")
# shown in the browser; everything else is downloaded, never rendered (no HTML or SVG from other people)
INLINE = {"image/jpeg", "image/png", "image/gif", "image/webp", "video/mp4", "video/webm", "video/quicktime",
          "audio/mpeg", "audio/mp4", "audio/ogg", "audio/webm", "audio/wav", "audio/x-wav", "application/pdf"}
THUMBABLE = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heif", "video/mp4", "video/webm", "video/quicktime")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, pw_hash TEXT NOT NULL,
  quota_bytes INTEGER, disabled INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), created REAL NOT NULL, last_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), parent_id INTEGER REFERENCES items(id),
  name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('file','folder')), size INTEGER NOT NULL DEFAULT 0,
  mime TEXT, blob TEXT, sha256 TEXT, created REAL NOT NULL, modified REAL NOT NULL, trashed REAL);
CREATE UNIQUE INDEX IF NOT EXISTS items_name ON items(owner_id, COALESCE(parent_id, 0), name COLLATE NOCASE) WHERE trashed IS NULL;
CREATE INDEX IF NOT EXISTS items_parent ON items(parent_id);
CREATE TABLE IF NOT EXISTS shares(
  item_id INTEGER NOT NULL REFERENCES items(id), user_id INTEGER NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK(role IN ('view','edit')), created REAL NOT NULL, PRIMARY KEY(item_id, user_id));
CREATE TABLE IF NOT EXISTS uploads(
  id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, parent_id INTEGER, name TEXT NOT NULL,
  size INTEGER NOT NULL, received INTEGER NOT NULL DEFAULT 0, mime TEXT, created REAL NOT NULL);
"""


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


# ---- passwords (same scheme as fMily Chat) ------------------------------------------------

def hash_password(password, salt=None, n=2 ** 14, r=8, p=1):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def check_password(password, stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        again = hash_password(password, bytes.fromhex(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(again.split("$")[-1], digest)


_DUMMY_HASH = hash_password("not-a-real-password")


# ---- names ---------------------------------------------------------------------------------

def clean_name(name):
    """A safe file or folder name: no path parts, no control characters, 1-255 characters."""
    if not isinstance(name, str):
        raise ValueError("name must be text")
    name = re.sub(r"[\x00-\x1f\x7f/\\]+", "_", name).strip()
    if name in ("", ".", "..") or len(name.encode("utf-8")) > 255:
        raise ValueError("name: 1-255 characters, not . or ..")
    return name


def split_ext(name):
    stem, dot, ext = name.rpartition(".")
    return (stem, "." + ext) if dot and stem else (name, "")


def guess_mime(name):
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# ---- storage -------------------------------------------------------------------------------

class Store:
    def __init__(self, data_dir, require_mount=None):
        self.data_dir = Path(data_dir)
        self.require_mount = Path(require_mount) if require_mount else None
        self.write_lock = threading.Lock()
        self.thumb_slots = threading.BoundedSemaphore(WORKERS)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = str(self.data_dir / "cloud.db")
        self.blobs, self.tmp, self.thumbs = (self.data_dir / d for d in ("blobs", "tmp", "thumbs"))
        for d in (self.blobs, self.tmp, self.thumbs):
            d.mkdir(exist_ok=True)
        with closing(self.conn()) as c, c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            if "sso_email" not in [r[1] for r in c.execute("PRAGMA table_info(users)")]:
                c.execute("ALTER TABLE users ADD COLUMN sso_email TEXT")    # the Downloader Web account this user is (single sign-on)
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_sso_email ON users(sso_email) WHERE sso_email IS NOT NULL")
        self.purge_trash()
        self.purge_stale_uploads()

    def conn(self):
        c = sqlite3.connect(self.db_path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    # -- users and sessions
    def add_user(self, username, display_name, password, quota_bytes=None):
        if not USERNAME.match(username):
            raise ValueError("username: 2-32 characters from a-z, 0-9, _ . -")
        if len(password) < 8:
            raise ValueError("password: at least 8 characters")
        with self.write_lock, closing(self.conn()) as c, c:
            try:
                return c.execute("INSERT INTO users(username, display_name, pw_hash, quota_bytes, created) VALUES(?,?,?,?,?)",
                                 (username, display_name.strip() or username, hash_password(password), quota_bytes or None, time.time())).lastrowid
            except sqlite3.IntegrityError:
                raise ValueError(f"user {username!r} already exists") from None

    def _user_update(self, username, sql, args, end_sessions=False):
        with self.write_lock, closing(self.conn()) as c, c:
            if not c.execute(f"UPDATE users SET {sql} WHERE username=?", (*args, username)).rowcount:
                raise ValueError(f"no user {username!r}")
            if end_sessions:
                c.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username=?)", (username,))

    def set_password(self, username, password):
        if len(password) < 8:
            raise ValueError("password: at least 8 characters")
        self._user_update(username, "pw_hash=?", (hash_password(password),), end_sessions=True)

    def disable_user(self, username):
        self._user_update(username, "disabled=1", (), end_sessions=True)

    def set_quota(self, username, quota_bytes):
        self._user_update(username, "quota_bytes=?", (quota_bytes or None,))

    def list_users(self, include_disabled=False):
        with closing(self.conn()) as c:
            rows = c.execute("SELECT id, username, display_name, quota_bytes, disabled FROM users ORDER BY display_name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows if include_disabled or not r["disabled"]]

    def login(self, username, password):
        with closing(self.conn()) as c:
            row = c.execute("SELECT * FROM users WHERE username=? AND disabled=0", (username.strip().lower(),)).fetchone()
        ok = check_password(password, row["pw_hash"] if row else _DUMMY_HASH)
        return row if (row and ok) else None

    def new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.write_lock, closing(self.conn()) as c, c:
            c.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, now, now))
        return token

    def session_user(self, token):
        if not token:
            return None
        h = hashlib.sha256(token.encode()).hexdigest()
        with closing(self.conn()) as c:
            row = c.execute("SELECT u.id, u.username, u.display_name, u.quota_bytes, s.last_seen FROM sessions s JOIN users u ON u.id=s.user_id "
                            "WHERE s.token_hash=? AND u.disabled=0 AND s.created > ?", (h, time.time() - SESSION_DAYS * 86400)).fetchone()
        if row and time.time() - row["last_seen"] > 3600:
            with self.write_lock, closing(self.conn()) as c, c:
                c.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (time.time(), h))
        return dict(row) if row else None

    # single sign-on: the user that belongs to a Downloader Web account (made on first visit)
    def ensure_sso_users(self, people):
        """A Cloud user for every account of the family, made before they ever open Cloud, so anybody can be given a file or a folder."""
        for p in people:
            self.sso_user({"email": str(p["email"]).strip().lower(), "name": str(p.get("name") or "")})

    def sso_user(self, who):
        email = who["email"]
        with closing(self.conn()) as c:
            row = c.execute("SELECT id, username, display_name, quota_bytes, disabled FROM users WHERE sso_email=?", (email,)).fetchone()
        if row is None:
            with self.write_lock, closing(self.conn()) as c, c:
                row = c.execute("SELECT id, username, display_name, quota_bytes, disabled FROM users WHERE sso_email=?", (email,)).fetchone()
                if row is None:
                    taken = lambda n: c.execute("SELECT 1 FROM users WHERE username=?", (n,)).fetchone() is not None   # noqa: E731
                    username = sso_mod.username_from_email(email, taken)
                    name = (who.get("name") or "").strip() or username
                    c.execute("INSERT INTO users(username, display_name, pw_hash, created, sso_email) VALUES(?,?,?,?,?)",
                              (username, name, "!", time.time(), email))      # "!" = no local password
                    row = c.execute("SELECT id, username, display_name, quota_bytes, disabled FROM users WHERE sso_email=?", (email,)).fetchone()
        if row["disabled"]:
            return None
        return {"id": row["id"], "username": row["username"], "display_name": row["display_name"], "quota_bytes": row["quota_bytes"], "last_seen": time.time()}

    def link_sso(self, username, email):
        """Make an existing user the owner of a Downloader Web e-mail address, so that account signs in to this user."""
        with self.write_lock, closing(self.conn()) as c, c:
            try:
                cur = c.execute("UPDATE users SET sso_email=? WHERE username=?", (email.strip().lower(), username))
            except sqlite3.IntegrityError:
                raise ValueError("that e-mail address already belongs to another user") from None
            if not cur.rowcount:
                raise ValueError(f"no user {username!r}")

    def end_session(self, token):
        with self.write_lock, closing(self.conn()) as c, c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    # -- space
    def usage(self, owner_id, c=None):
        own = c is None
        c = c or self.conn()
        try:
            return c.execute("SELECT COALESCE(SUM(size), 0) FROM items WHERE owner_id=? AND kind='file' AND COALESCE(blob,'') NOT LIKE 'dl%'", (owner_id,)).fetchone()[0]
        finally:
            if own:
                c.close()

    def disk_free(self):
        return shutil.disk_usage(self.data_dir).free

    def check_storage(self, owner_id, size, quota_bytes, c=None):
        if self.require_mount is not None and not os.path.ismount(self.require_mount):
            raise ApiError(503, "The storage drive is not connected, so nothing can be uploaded right now.")
        if quota_bytes and self.usage(owner_id, c) + size > quota_bytes:
            raise ApiError(413, "That would go over the storage limit set for this account.")
        if self.disk_free() - size < FREE_MARGIN:
            raise ApiError(507, "The server's drive is full.")

    def _quota_of(self, c, user_id):
        return c.execute("SELECT quota_bytes FROM users WHERE id=?", (user_id,)).fetchone()["quota_bytes"]

    def me(self, user):
        with closing(self.conn()) as c:
            used = self.usage(user["id"], c)
        return {"id": user["id"], "username": user["username"], "display_name": user["display_name"], "used": used,
                "quota": user["quota_bytes"], "disk_free": self.disk_free(),
                "drive_ok": self.require_mount is None or os.path.ismount(self.require_mount)}

    # -- access
    @staticmethod
    def get(c, item_id):
        row = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone() if item_id is not None else None
        if row is None:
            raise ApiError(404, "not found")
        return row

    def role_of(self, c, user_id, item):
        """'owner', 'edit', 'view' or None. Things in someone else's trash are invisible to everyone but the owner."""
        if item["owner_id"] == user_id:
            return "owner"
        best, cur = None, item
        for _ in range(500):
            if cur["trashed"] is not None:
                return None
            share = c.execute("SELECT role FROM shares WHERE item_id=? AND user_id=?", (cur["id"], user_id)).fetchone()
            if share:
                if share["role"] == "edit":
                    return "edit"
                best = "view"
            if cur["parent_id"] is None:
                break
            cur = c.execute("SELECT * FROM items WHERE id=?", (cur["parent_id"],)).fetchone()
            if cur is None:
                break
        return best

    def need(self, c, user_id, item_id, level):
        """The item, if the user has at least this level: 'view' < 'edit' < 'owner'."""
        item = self.get(c, item_id)
        role = self.role_of(c, user_id, item)
        order = {None: 0, "view": 1, "edit": 2, "owner": 3}
        if order[role] < order[level]:
            raise ApiError(404 if role is None else 403, "not found" if role is None else "you do not have permission to do that")
        return item, role

    def is_share_root(self, c, user_id, item):
        return item["owner_id"] != user_id and c.execute("SELECT 1 FROM shares WHERE item_id=? AND user_id=?", (item["id"], user_id)).fetchone() is not None

    def subtree(self, c, item_id):
        return [r[0] for r in c.execute("WITH RECURSIVE sub(id) AS (SELECT ? UNION ALL SELECT i.id FROM items i JOIN sub s ON i.parent_id=s.id) SELECT id FROM sub", (item_id,))]

    # -- views
    def describe(self, c, user_id, rows):
        shared = set()
        ids = [r["id"] for r in rows if r["owner_id"] == user_id]
        if ids:
            shared = {r[0] for r in c.execute(f"SELECT DISTINCT item_id FROM shares WHERE item_id IN ({','.join('?' * len(ids))})", ids)}
        out = []
        for r in rows:
            role = "owner" if r["owner_id"] == user_id else self.role_of(c, user_id, r)
            out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "size": r["size"], "mime": r["mime"], "owner": r["owner_id"],
                        "modified": r["modified"], "created": r["created"], "role": role, "shared": r["id"] in shared,
                        "trashed": r["trashed"], "thumb": bool(r["mime"] in THUMBABLE and r["blob"])})
        return out

    @staticmethod
    def order(rows):
        return sorted(rows, key=lambda r: (r["kind"] != "folder", r["name"].lower()))

    def folder(self, user_id, folder_id):
        with closing(self.conn()) as c:
            if folder_id is None:
                rows = c.execute("SELECT * FROM items WHERE owner_id=? AND parent_id IS NULL AND trashed IS NULL", (user_id,)).fetchall()
                return {"folder": None, "path": [], "role": "owner", "items": self.describe(c, user_id, self.order(rows))}
            folder, role = self.need(c, user_id, folder_id, "view")
            if folder["kind"] != "folder" or folder["trashed"] is not None:
                raise ApiError(404, "not found")
            rows = c.execute("SELECT * FROM items WHERE parent_id=? AND trashed IS NULL", (folder_id,)).fetchall()
            path, cur = [], folder
            while cur is not None:
                path.append({"id": cur["id"], "name": cur["name"]})
                if self.is_share_root(c, user_id, cur) or cur["parent_id"] is None:
                    break
                cur = c.execute("SELECT * FROM items WHERE id=?", (cur["parent_id"],)).fetchone()
            return {"folder": self.describe(c, user_id, [folder])[0], "path": list(reversed(path)), "role": role,
                    "items": self.describe(c, user_id, self.order(rows))}

    def shared_with_me(self, user_id):
        with closing(self.conn()) as c:
            rows = c.execute("SELECT i.* FROM items i JOIN shares s ON s.item_id=i.id WHERE s.user_id=? AND i.trashed IS NULL", (user_id,)).fetchall()
            rows = [r for r in rows if self.role_of(c, user_id, r)]
            return self.describe(c, user_id, self.order(rows))

    def trash_list(self, user_id):
        self.purge_trash()
        with closing(self.conn()) as c:
            rows = c.execute("SELECT * FROM items WHERE owner_id=? AND trashed IS NOT NULL ORDER BY trashed DESC", (user_id,)).fetchall()
            # only what was trashed itself, not things inside a trashed folder
            rows = [r for r in rows if r["parent_id"] is None or c.execute("SELECT trashed FROM items WHERE id=?", (r["parent_id"],)).fetchone()["trashed"] is None]
            return self.describe(c, user_id, rows)

    def search(self, user_id, q):
        q = (q or "").strip()
        if not q:
            return []
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with closing(self.conn()) as c:
            live = c.execute("WITH RECURSIVE live(id) AS (SELECT id FROM items WHERE owner_id=? AND parent_id IS NULL AND trashed IS NULL "
                             "UNION ALL SELECT i.id FROM items i JOIN live l ON i.parent_id=l.id WHERE i.trashed IS NULL) "
                             "SELECT i.* FROM items i JOIN live ON live.id=i.id WHERE i.name LIKE ? ESCAPE '\\' LIMIT 200", (user_id, like)).fetchall()
            shared = c.execute("WITH RECURSIVE sh(id) AS (SELECT item_id FROM shares s JOIN items i ON i.id=s.item_id WHERE s.user_id=? AND i.trashed IS NULL "
                               "UNION ALL SELECT i.id FROM items i JOIN sh ON i.parent_id=sh.id WHERE i.trashed IS NULL) "
                               "SELECT i.* FROM items i JOIN sh ON sh.id=i.id WHERE i.name LIKE ? ESCAPE '\\' LIMIT 200", (user_id, like)).fetchall()
            seen, rows = set(), []
            for r in list(live) + list(shared):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    rows.append(r)
            return self.describe(c, user_id, self.order(rows))

    # -- changes
    def unique_name(self, c, owner_id, parent_id, name, exclude=None):
        taken = {r[0].lower() for r in c.execute("SELECT name FROM items WHERE owner_id=? AND COALESCE(parent_id,0)=COALESCE(?,0) AND trashed IS NULL AND id IS NOT ?",
                                                  (owner_id, parent_id, exclude))}
        if name.lower() not in taken:
            return name
        stem, ext = split_ext(name)
        n = 1
        while f"{stem} ({n}){ext}".lower() in taken:
            n += 1
        return f"{stem} ({n}){ext}"

    def writable_parent(self, c, user_id, parent_id):
        """(owner of what will be created, parent row or None). Items made inside a shared folder belong to its owner."""
        if parent_id is None:
            return user_id, None
        parent, _ = self.need(c, user_id, parent_id, "edit")
        if parent["kind"] != "folder" or parent["trashed"] is not None:
            raise ApiError(400, "that is not a folder")
        return parent["owner_id"], parent

    def create_folder(self, user_id, parent_id, name):
        name = clean_name(name)
        with self.write_lock, closing(self.conn()) as c, c:
            owner, _ = self.writable_parent(c, user_id, parent_id)
            if name.lower() in {r[0].lower() for r in c.execute("SELECT name FROM items WHERE owner_id=? AND COALESCE(parent_id,0)=COALESCE(?,0) AND trashed IS NULL", (owner, parent_id))}:
                raise ApiError(409, "something with that name already exists here")
            now = time.time()
            new = c.execute("INSERT INTO items(owner_id, parent_id, name, kind, created, modified) VALUES(?,?,?,'folder',?,?)", (owner, parent_id, name, now, now)).lastrowid
            return self.describe(c, user_id, [self.get(c, new)])[0]

    def _editable(self, c, user_id, item_id):
        item, role = self.need(c, user_id, item_id, "edit")
        if self.is_share_root(c, user_id, item):
            raise ApiError(403, "only the owner can change what is shared with you")
        return item

    def rename(self, user_id, item_id, name):
        name = clean_name(name)
        with self.write_lock, closing(self.conn()) as c, c:
            item = self._editable(c, user_id, item_id)
            if item["trashed"] is not None:
                raise ApiError(400, "restore it first")
            if name.lower() in {r[0].lower() for r in c.execute("SELECT name FROM items WHERE owner_id=? AND COALESCE(parent_id,0)=COALESCE(?,0) AND trashed IS NULL AND id!=?",
                                                                 (item["owner_id"], item["parent_id"], item_id))}:
                raise ApiError(409, "something with that name already exists here")
            mime = guess_mime(name) if item["kind"] == "file" else None
            c.execute("UPDATE items SET name=?, mime=COALESCE(?, mime), modified=? WHERE id=?", (name, mime, time.time(), item_id))
            return self.describe(c, user_id, [self.get(c, item_id)])[0]

    def move(self, user_id, item_id, parent_id):
        with self.write_lock, closing(self.conn()) as c, c:
            item = self._editable(c, user_id, item_id)
            owner, parent = self.writable_parent(c, user_id, parent_id)
            if owner != item["owner_id"]:
                raise ApiError(400, "things cannot be moved into someone else's storage")
            if parent_id is not None and parent_id in self.subtree(c, item_id):
                raise ApiError(400, "a folder cannot be moved into itself")
            name = self.unique_name(c, owner, parent_id, item["name"], exclude=item_id)
            c.execute("UPDATE items SET parent_id=?, name=?, modified=? WHERE id=?", (parent_id, name, time.time(), item_id))
            return self.describe(c, user_id, [self.get(c, item_id)])[0]

    def trash(self, user_id, item_id):
        with self.write_lock, closing(self.conn()) as c, c:
            item = self._editable(c, user_id, item_id)
            if item["trashed"] is None:
                c.execute("UPDATE items SET trashed=?, modified=? WHERE id=?", (time.time(), time.time(), item_id))
                c.execute("DELETE FROM shares WHERE item_id=?", (item_id,))      # a trashed item is no longer shared
            return {"ok": True}

    def restore(self, user_id, item_id):
        with self.write_lock, closing(self.conn()) as c, c:
            item, _ = self.need(c, user_id, item_id, "owner")
            if item["trashed"] is None:
                return self.describe(c, user_id, [item])[0]
            parent = c.execute("SELECT * FROM items WHERE id=?", (item["parent_id"],)).fetchone() if item["parent_id"] else None
            parent_id = item["parent_id"] if parent and parent["trashed"] is None else None
            name = self.unique_name(c, user_id, parent_id, item["name"], exclude=item_id)
            c.execute("UPDATE items SET trashed=NULL, parent_id=?, name=?, modified=? WHERE id=?", (parent_id, name, time.time(), item_id))
            return self.describe(c, user_id, [self.get(c, item_id)])[0]

    def _purge(self, c, ids):
        freed = 0
        for i in ids:
            row = c.execute("SELECT blob, size, kind FROM items WHERE id=?", (i,)).fetchone()
            if row and row["kind"] == "file":
                freed += row["size"]
                if row["blob"]:
                    for p in (self.blob_path(row["blob"]), self.thumbs / f"{row['blob']}.jpg"):
                        try:
                            p.unlink()
                        except OSError:
                            pass
        c.executemany("DELETE FROM shares WHERE item_id=?", [(i,) for i in ids])
        c.executemany("DELETE FROM items WHERE id=?", [(i,) for i in reversed(ids)])      # subtree() lists parents first: children go first
        return freed

    def delete_forever(self, user_id, item_id):
        with self.write_lock, closing(self.conn()) as c, c:
            item, _ = self.need(c, user_id, item_id, "owner")
            if item["trashed"] is None:
                raise ApiError(400, "move it to the trash first")
            return {"freed": self._purge(c, self.subtree(c, item_id))}

    def empty_trash(self, user_id):
        freed = 0
        with self.write_lock, closing(self.conn()) as c, c:
            for r in c.execute("SELECT id FROM items WHERE owner_id=? AND trashed IS NOT NULL", (user_id,)).fetchall():
                if c.execute("SELECT 1 FROM items WHERE id=?", (r["id"],)).fetchone():
                    freed += self._purge(c, self.subtree(c, r["id"]))
        return {"freed": freed}

    def purge_trash(self):
        cutoff = time.time() - TRASH_DAYS * 86400
        with self.write_lock, closing(self.conn()) as c, c:
            for r in c.execute("SELECT id FROM items WHERE trashed IS NOT NULL AND trashed < ?", (cutoff,)).fetchall():
                if c.execute("SELECT 1 FROM items WHERE id=?", (r["id"],)).fetchone():
                    self._purge(c, self.subtree(c, r["id"]))

    # -- sharing
    def share(self, user_id, item_id, other_id, role):
        if role not in ("view", "edit", None):
            raise ValueError("role must be 'view', 'edit' or null")
        with self.write_lock, closing(self.conn()) as c, c:
            item, _ = self.need(c, user_id, item_id, "owner")
            if item["trashed"] is not None:
                raise ApiError(400, "restore it first")
            if other_id == user_id or not c.execute("SELECT 1 FROM users WHERE id=? AND disabled=0", (other_id,)).fetchone():
                raise ApiError(400, "pick another family member")
            if role is None:
                c.execute("DELETE FROM shares WHERE item_id=? AND user_id=?", (item_id, other_id))
            else:
                c.execute("INSERT INTO shares VALUES(?,?,?,?) ON CONFLICT(item_id, user_id) DO UPDATE SET role=excluded.role", (item_id, other_id, role, time.time()))
            return self.shares_of(user_id, item_id, c)

    def shares_of(self, user_id, item_id, c=None):
        own = c is None
        c = c or self.conn()
        try:
            self.need(c, user_id, item_id, "owner")
            return [dict(r) for r in c.execute("SELECT s.user_id, u.display_name, s.role FROM shares s JOIN users u ON u.id=s.user_id WHERE s.item_id=? ORDER BY u.display_name COLLATE NOCASE", (item_id,))]
        finally:
            if own:
                c.close()

    # -- uploads (chunked, resumable)
    def blob_path(self, blob):
        return self.blobs / blob[:2] / blob

    def start_upload(self, user_id, parent_id, name, size, mime=None):
        name = clean_name(name)
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_FILE:
            raise ApiError(413, f"a file can be at most {MAX_FILE // GB} GB")
        with self.write_lock, closing(self.conn()) as c, c:
            owner, _ = self.writable_parent(c, user_id, parent_id)
            self.check_storage(owner, size, self._quota_of(c, owner), c)
            upload_id = secrets.token_hex(16)
            (self.tmp / upload_id).write_bytes(b"")
            c.execute("INSERT INTO uploads(id, user_id, owner_id, parent_id, name, size, mime, created) VALUES(?,?,?,?,?,?,?,?)",
                      (upload_id, user_id, owner, parent_id, name, size, mime, time.time()))
        return {"id": upload_id, "chunk": CHUNK, "received": 0}

    def _upload(self, c, user_id, upload_id):
        row = c.execute("SELECT * FROM uploads WHERE id=? AND user_id=?", (upload_id, user_id)).fetchone() if re.fullmatch(r"[0-9a-f]{32}", upload_id or "") else None
        if row is None:
            raise ApiError(404, "no such upload")
        return row

    def upload_status(self, user_id, upload_id):
        with closing(self.conn()) as c:
            row = self._upload(c, user_id, upload_id)
        return {"id": upload_id, "size": row["size"], "received": row["received"]}

    def append_chunk(self, user_id, upload_id, offset, data):
        with closing(self.conn()) as c:
            row = self._upload(c, user_id, upload_id)
        path = self.tmp / upload_id
        actual = path.stat().st_size if path.exists() else 0
        if offset != row["received"] or actual != row["received"]:
            raise ApiError(409, "the upload is not at that offset", received=min(row["received"], actual))
        if row["received"] + len(data) > row["size"]:
            raise ApiError(400, "more data than the file's declared size")
        if self.require_mount is not None and not os.path.ismount(self.require_mount):
            raise ApiError(503, "The storage drive is not connected.")
        with open(path, "ab") as fh:
            fh.write(data)
        with self.write_lock, closing(self.conn()) as c, c:
            c.execute("UPDATE uploads SET received=? WHERE id=?", (row["received"] + len(data), upload_id))
        return {"received": row["received"] + len(data)}

    def finish_upload(self, user_id, upload_id):
        with closing(self.conn()) as c:
            row = self._upload(c, user_id, upload_id)
        path = self.tmp / upload_id
        if row["received"] != row["size"] or (path.stat().st_size if path.exists() else -1) != row["size"]:
            raise ApiError(409, "the upload is not complete", received=row["received"])
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        blob = secrets.token_hex(16)
        with self.write_lock, closing(self.conn()) as c, c:
            owner, parent = self.writable_parent(c, user_id, row["parent_id"])
            name = self.unique_name(c, owner, row["parent_id"], row["name"])
            dest = self.blob_path(blob)
            dest.parent.mkdir(exist_ok=True)
            shutil.move(str(path), str(dest))
            now = time.time()
            new = c.execute("INSERT INTO items(owner_id, parent_id, name, kind, size, mime, blob, sha256, created, modified) VALUES(?,?,?,'file',?,?,?,?,?,?)",
                            (owner, row["parent_id"], name, row["size"], guess_mime(name), blob, digest.hexdigest(), now, now)).lastrowid
            c.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
            return self.describe(c, user_id, [self.get(c, new)])[0]

    def cancel_upload(self, user_id, upload_id):
        with self.write_lock, closing(self.conn()) as c, c:
            self._upload(c, user_id, upload_id)
            c.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
        try:
            (self.tmp / upload_id).unlink()
        except OSError:
            pass
        return {"ok": True}

    def purge_stale_uploads(self):
        with self.write_lock, closing(self.conn()) as c, c:
            old = [r[0] for r in c.execute("SELECT id FROM uploads WHERE created < ?", (time.time() - STALE_UPLOAD,))]
            c.executemany("DELETE FROM uploads WHERE id=?", [(i,) for i in old])
        for upload_id in old:
            try:
                (self.tmp / upload_id).unlink()
            except OSError:
                pass

    # -- reading files
    def file_for_read(self, user_id, item_id):
        with closing(self.conn()) as c:
            item, _ = self.need(c, user_id, item_id, "view")
        if item["kind"] != "file" or item["trashed"] is not None and item["owner_id"] != user_id:
            raise ApiError(404, "not found")
        path = self.blob_path(item["blob"])
        if not path.is_file():
            raise ApiError(404, "the file is missing on the server's drive")
        return {"path": path, "mime": item["mime"] or "application/octet-stream", "size": item["size"], "name": item["name"], "blob": item["blob"]}

    def thumbnail(self, user_id, item_id):
        info = self.file_for_read(user_id, item_id)
        if info["mime"] not in THUMBABLE or not shutil.which("ffmpeg"):
            raise ApiError(404, "no thumbnail")
        out = self.thumbs / f"{info['blob']}.jpg"
        if not out.exists():
            tmp = out.with_suffix(f".{secrets.token_hex(4)}.tmp.jpg")
            seek = ["-ss", "0.5"] if info["mime"].startswith("video/") else []
            with self.thumb_slots:        # at most WORKERS ffmpeg processes at once, at low priority
                cmd = ["nice", "-n", "10", "ffmpeg", "-nostdin", "-v", "error", *seek, "-i", str(info["path"]), "-frames:v", "1",
                       "-vf", "scale='min(320,iw)':-2", "-y", str(tmp)]
                try:
                    done = subprocess.run(cmd if shutil.which("nice") else cmd[3:], capture_output=True, timeout=60)
                except (OSError, subprocess.SubprocessError):
                    done = None
            if not done or done.returncode != 0 or not tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise ApiError(404, "no thumbnail")
            os.replace(tmp, out)
        return {"path": out, "mime": "image/jpeg", "size": out.stat().st_size, "name": "thumb.jpg"}


class LoginLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.fails = {}

    def blocked(self, key):
        with self.lock:
            recent = [t for t in self.fails.get(key, []) if t > time.time() - 300]
            self.fails[key] = recent
            return len(recent) >= LOGIN_FAILS

    def fail(self, key):
        with self.lock:
            self.fails.setdefault(key, []).append(time.time())

    def clear(self, key):
        with self.lock:
            self.fails.pop(key, None)


# ---- HTTP ----------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    store = None
    limiter = None
    static_dir = HERE / "static"
    secure_cookie = False
    sso = None                 # sso_mod.Sso when Downloader Web accounts may sign in
    server_version = "FamilyCloud"

    def log_message(self, fmt, *args):
        print("%s %s" % (self.address_string(), fmt % args), file=sys.stderr)

    def send_json(self, data, status=200, headers=()):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def fail(self, status, message, **extra):
        self.send_json({"error": message, **extra}, status)

    def token(self):
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        return jar[COOKIE].value if COOKIE in jar else None

    def json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_JSON:
            raise ValueError("request body too large or missing")
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise ValueError("body is not valid JSON") from None
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def cookie_header(self, value, max_age):
        parts = [f"{COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
        if self.secure_cookie or sso_mod.effective_https(self.headers, self.client_address[0]):
            parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    def same_origin(self):
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc.lower() == sso_mod.effective_host(self.headers, self.client_address[0])

    def dlw_token(self):
        return sso_mod.cookie_value(self.headers.get("Cookie", ""), sso_mod.DW_COOKIE)

    def current_user(self):
        """This app's own session first; else a Downloader Web session (single sign-on)."""
        user = self.store.session_user(self.token())
        if user or self.sso is None:
            return user
        who = self.sso.whoami(self.dlw_token())
        if who and not sso_mod.allows(who, "cloud"):
            return None                                     # the account was not given this app: treated as not signed in here
        return self.store.sso_user(who) if who else None

    def dlw_cookie_header(self, value, max_age):
        parts = [f"{sso_mod.DW_COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if self.secure_cookie or sso_mod.effective_https(self.headers, self.client_address[0]):
            parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    def user_or_401(self):
        user = self.current_user()
        if not user:
            self.fail(401, "not signed in")
        return user

    @staticmethod
    def opt_int(value):
        if value in (None, "", "root", "null"):
            return None
        return int(value)

    # -- routing
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path.startswith("/api/"):
                self.guarded(lambda: self.api_get(url.path, q))
            else:
                self.static(url.path)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return self.fail(404, "not found")
        if self.headers.get(CSRF_HEADER[0]) != CSRF_HEADER[1] or "application/json" not in self.headers.get("Content-Type", "") or not self.same_origin():
            return self.fail(403, "forbidden")
        self.guarded(lambda: self.api_post(url.path, self.json_body()))

    def do_PUT(self):
        url = urlparse(self.path)
        m = re.fullmatch(r"/api/upload/([0-9a-f]{32})", url.path)
        if not m:
            return self.fail(404, "not found")
        if self.headers.get(CSRF_HEADER[0]) != CSRF_HEADER[1] or not self.same_origin():
            return self.fail(403, "forbidden")
        self.guarded(lambda: self.put_chunk(m.group(1), parse_qs(url.query)))

    def guarded(self, fn):
        try:
            fn()
        except ApiError as exc:
            self.fail(exc.status, exc.message, **exc.extra)
        except PermissionError as exc:
            self.fail(403, str(exc))
        except (ValueError, TypeError) as exc:
            self.fail(400, str(exc) if isinstance(exc, ValueError) else "bad request")
        except (BrokenPipeError, ConnectionResetError):
            raise
        except Exception:                      # a bug must not drop the connection or show internals to the browser
            import traceback
            traceback.print_exc()
            self.fail(500, "something went wrong on the server")

    def api_get(self, path, q):
        user = self.user_or_401()
        if not user:
            return
        s = self.store
        if path == "/api/me":
            return self.send_json(s.me(user))
        if path == "/api/users":
            if self.sso is not None:
                s.ensure_sso_users(self.sso.directory(self.dlw_token()))          # everybody in the family, not only who has already opened Cloud
            return self.send_json([{"id": u["id"], "display_name": u["display_name"]} for u in s.list_users() if u["id"] != user["id"]])
        if path == "/api/folder":
            return self.send_json(s.folder(user["id"], self.opt_int(q.get("id"))))
        if path == "/api/shared":
            return self.send_json(s.shared_with_me(user["id"]))
        if path == "/api/trash":
            return self.send_json(s.trash_list(user["id"]))
        if path == "/api/search":
            return self.send_json(s.search(user["id"], q.get("q")))
        if path == "/api/shares":
            return self.send_json(s.shares_of(user["id"], int(q.get("id", 0))))
        m = re.fullmatch(r"/api/upload/([0-9a-f]{32})", path)
        if m:
            return self.send_json(s.upload_status(user["id"], m.group(1)))
        m = re.fullmatch(r"/api/(file|thumb)/(\d+)", path)
        if m:
            info = s.file_for_read(user["id"], int(m.group(2))) if m.group(1) == "file" else s.thumbnail(user["id"], int(m.group(2)))
            return self.serve_file(info, m.group(1) == "thumb", q.get("download") == "1")
        self.fail(404, "not found")

    def api_post(self, path, body):
        if path == "/api/login":
            return self.login(body)
        user = self.user_or_401()
        if not user:
            return
        s, uid = self.store, user["id"]
        if path == "/api/logout":
            if self.token():
                s.end_session(self.token())
            headers = [self.cookie_header("", 0)]
            if self.sso is not None and self.dlw_token():
                self.sso.logout(self.dlw_token())       # one sign-out for every family app
                headers.append(self.dlw_cookie_header("", 0))
            return self.send_json({"ok": True}, headers=headers)
        if path == "/api/folder":
            return self.send_json(s.create_folder(uid, self.opt_int(body.get("parent")), body.get("name")), 201)
        if path == "/api/rename":
            return self.send_json(s.rename(uid, int(body.get("id", 0)), body.get("name")))
        if path == "/api/move":
            return self.send_json(s.move(uid, int(body.get("id", 0)), self.opt_int(body.get("parent"))))
        if path == "/api/trash":
            return self.send_json(s.trash(uid, int(body.get("id", 0))))
        if path == "/api/restore":
            return self.send_json(s.restore(uid, int(body.get("id", 0))))
        if path == "/api/delete":
            return self.send_json(s.delete_forever(uid, int(body.get("id", 0))))
        if path == "/api/trash/empty":
            return self.send_json(s.empty_trash(uid))
        if path == "/api/share":
            return self.send_json(s.share(uid, int(body.get("id", 0)), int(body.get("user", 0)), body.get("role")))
        if path == "/api/upload/start":
            return self.send_json(s.start_upload(uid, self.opt_int(body.get("parent")), body.get("name"), body.get("size"), body.get("mime")), 201)
        m = re.fullmatch(r"/api/upload/([0-9a-f]{32})/(finish|cancel)", path)
        if m:
            return self.send_json(s.finish_upload(uid, m.group(1)) if m.group(2) == "finish" else s.cancel_upload(uid, m.group(1)))
        self.fail(404, "not found")

    def put_chunk(self, upload_id, query):
        user = self.user_or_401()
        if not user:
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            offset = int(query.get("offset", ["-1"])[0])
        except ValueError:
            raise ValueError("bad upload headers") from None
        if not 0 < length <= MAX_CHUNK or offset < 0:
            self.close_connection = True
            return self.fail(413, f"a chunk is 1 byte to {MAX_CHUNK // (1024 * 1024)} MB")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError("chunk was cut short")
        self.send_json(self.store.append_chunk(user["id"], upload_id, offset, data))

    def login(self, body):
        username = str(body.get("username", "")).strip().lower()
        key = f"{self.client_address[0]}|{username}"
        if self.limiter.blocked(key):
            return self.fail(429, "too many attempts; wait a few minutes")
        if self.sso is not None and "@" in username:
            # the family login (a Downloader Web account). Never from the public internet: Funnel visitors use a local account.
            if sso_mod.is_funnel(self.headers):
                return self.fail(403, "sign in with your local username here")
            token, age = self.sso.login(username, str(body.get("password", "")))
            who = self.sso.whoami(token) if token else None
            if not who:
                self.limiter.fail(key)
                return self.fail(401, "wrong e-mail or password")
            self.limiter.clear(key)
            user = self.store.sso_user(who)
            if not user:
                return self.fail(403, "this account is disabled here")
            return self.send_json({"id": user["id"], "username": user["username"], "display_name": user["display_name"]},
                                  headers=[self.dlw_cookie_header(token, age)])
        row = self.store.login(username, str(body.get("password", "")))
        if not row:
            self.limiter.fail(key)
            return self.fail(401, "wrong username or password")
        self.limiter.clear(key)
        token = self.store.new_session(row["id"])
        self.send_json({"id": row["id"], "username": row["username"], "display_name": row["display_name"]},
                       headers=[self.cookie_header(token, SESSION_DAYS * 86400)])

    # -- file bodies
    def serve_file(self, info, is_thumb, force_download):
        size, mime = info["size"], info["mime"]
        start, end, status = 0, size - 1, 200
        spec = self.headers.get("Range")
        if spec and size:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", spec.strip())
            if not m or not (m.group(1) or m.group(2)):
                return self.not_satisfiable(size)
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                return self.not_satisfiable(size)
            status = 206
        inline = (is_thumb or mime in INLINE) and not force_download
        plain = re.sub(r'[^\x20-\x7e]', "_", info["name"]).replace('"', "'")
        encoded = "".join(c if (c.isascii() and c.isalnum()) or c in "-_.~" else "".join(f"%{b:02X}" for b in c.encode("utf-8")) for c in info["name"])
        disposition = ("inline" if inline else "attachment") + f"; filename=\"{plain}\"; filename*=UTF-8''{encoded}"   # all ASCII: safe in a header
        self.send_response(status)
        self.send_header("Content-Type", mime if (inline or is_thumb) else "application/octet-stream")
        self.send_header("Content-Disposition", disposition)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        if mime != "application/pdf":
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        with open(info["path"], "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(1024 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def not_satisfiable(self, size):
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (self.static_dir / unquote(rel)).resolve()
        if self.static_dir.resolve() not in target.parents or not target.is_file():
            return self.fail(404, "not found")
        raw = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; frame-src 'self'; connect-src 'self'; " + frame_ancestors(sso_mod.effective_host(self.headers, self.client_address[0])))
        self.end_headers()
        self.wfile.write(raw)


def make_server(host, port, data_dir, secure_cookie=False, static_dir=None, require_mount=None, sso_upstream=None, dw_users=None, dw_files=None):
    handler = type("BoundHandler", (Handler,), {
        "store": Store(data_dir, require_mount), "limiter": LoginLimiter(), "secure_cookie": secure_cookie, "sso": sso_mod.Sso(sso_upstream) if sso_upstream else None,
        "static_dir": Path(static_dir) if static_dir else HERE / "static"})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    if dw_users and dw_files:                       # what a person's Downloader and Uploader made shows in their own Cloud (see downloads.py)
        import downloads
        downloads.start(handler.store, Path(dw_users), Path(dw_files))
    return httpd


# ---- command line --------------------------------------------------------------------------

def prompt_password(args):
    if getattr(args, "password_stdin", False):
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Again: "):
        raise ValueError("passwords differ")
    return first


def main(argv=None):
    ap = argparse.ArgumentParser(description="fMily Cloud server")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA), help="where files and the database live (default %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8778)
    s.add_argument("--sso", default="", metavar="URL", help="Downloader Web address (e.g. http://127.0.0.1:8766): its accounts sign in here too, one login for every family app")
    s.add_argument("--secure-cookie", action="store_true", help="mark the session cookie Secure (use behind HTTPS)")
    s.add_argument("--dw-users", default="", metavar="FILE", help="Downloader Web's users.json: with --dw-files, each person's Downloader/Uploader files appear in their own Cloud, in a folder called Downloads")
    s.add_argument("--dw-files", default="", metavar="DIR", help="Downloader Web's user folders (default there: ~/DownloaderUsers)")
    s.add_argument("--require-mount", default="", help="refuse uploads unless this directory is a mount point (the big drive)")
    a = sub.add_parser("add-user")
    a.add_argument("username")
    a.add_argument("--name", default="")
    a.add_argument("--quota-gb", type=float, default=0, help="storage limit in GB (default: no limit beyond the drive)")
    a.add_argument("--password-stdin", action="store_true")
    r = sub.add_parser("reset-password")
    r.add_argument("username")
    r.add_argument("--password-stdin", action="store_true")
    k = sub.add_parser("link-user", help="make an existing user the owner of a Downloader Web e-mail address (single sign-on)")
    k.add_argument("username")
    k.add_argument("email")
    d = sub.add_parser("disable-user")
    d.add_argument("username")
    q = sub.add_parser("set-quota")
    q.add_argument("username")
    q.add_argument("gb", type=float, help="0 = no limit")
    sub.add_parser("list-users")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "serve":
            httpd = make_server(args.host, args.port, args.data_dir, args.secure_cookie, require_mount=args.require_mount or None, sso_upstream=args.sso or None, dw_users=args.dw_users or None, dw_files=args.dw_files or None)
            print(f"fMily Cloud on http://{args.host}:{args.port}/  (data {args.data_dir}"
                  f"{', needs ' + args.require_mount + ' mounted' if args.require_mount else ''})", file=sys.stderr)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
            return 0
        store = Store(args.data_dir)
        if args.cmd == "add-user":
            uid = store.add_user(args.username.strip().lower(), args.name or args.username, prompt_password(args), int(args.quota_gb * GB) or None)
            print(f"added {args.username} (id {uid})")
        elif args.cmd == "reset-password":
            store.set_password(args.username.strip().lower(), prompt_password(args))
            print("password changed; the user's sessions were ended")
        elif args.cmd == "link-user":
            store.link_sso(args.username.strip().lower(), args.email)
            print(f"{args.username} is now the family login of {args.email.strip().lower()}")
        elif args.cmd == "disable-user":
            store.disable_user(args.username.strip().lower())
            print("disabled; the user's sessions were ended")
        elif args.cmd == "set-quota":
            store.set_quota(args.username.strip().lower(), int(args.gb * GB) or None)
            print("quota set" if args.gb else "quota removed: limited only by the drive")
        elif args.cmd == "list-users":
            for u in store.list_users(include_disabled=True):
                quota = f"{u['quota_bytes'] / GB:.0f} GB" if u["quota_bytes"] else "no limit"
                print(f"{u['id']:>3}  {u['username']:<20} {u['display_name']:<24} {quota}{' DISABLED' if u['disabled'] else ''}")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
