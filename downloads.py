"""Brings what a person's Downloader and Uploader made into their own fMily Cloud.

Downloader Web keeps each person's files in ~/DownloaderUsers/<folder>/ (Music, Videos, ...). With `serve --dw-users users.json --dw-files ~/DownloaderUsers`
this module shows that folder in the person's Cloud as a folder called "Downloads", with the same sub-folders. Nothing is copied: each Cloud entry is a link to
the file (so a 4 GB video costs no second 4 GB), the Downloads folder follows the real one every minute (new files appear, removed files go), and removing an
entry in Cloud removes only the link, never the original. Only people who have a Cloud account linked to their Downloader Web e-mail are served, each into their own
Cloud and no one else's. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

ROOT_NAME = "Downloads"
SKIP_SUFFIXES = (".part", ".tmp", ".partial", ".ytdl", ".temp", ".crdownload", ".download")
PREFIX = "dl"                                 # the blob names of linked files start with this, so they are never confused with uploaded ones


def blob_name(path: str) -> str:
    return PREFIX + hashlib.sha1(path.encode("utf-8", "surrogateescape")).hexdigest()[:30]


def read_users(path: Path) -> dict:
    """{e-mail: folder name} from Downloader Web's users.json."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {e.lower(): u["folder"] for e, u in (data.get("users") or {}).items() if isinstance(u, dict) and u.get("folder") and "/" not in u["folder"] and ".." not in u["folder"]}
    except (OSError, ValueError, AttributeError):
        return {}


def sync_user(store, user_id: int, base: Path, limit: int = 50_000) -> dict:
    """Make the Downloads folder of this Cloud user match `base` (their Downloader Web folder). Returns counts."""
    added = removed = 0
    if not base.is_dir():
        return {"added": 0, "removed": 0}
    now = time.time()
    with store.write_lock, closing(store.conn()) as c, c:
        def folder_id(parent, name):
            r = c.execute("SELECT id FROM items WHERE owner_id=? AND COALESCE(parent_id,0)=COALESCE(?,0) AND kind='folder' AND trashed IS NULL AND name=? COLLATE NOCASE", (user_id, parent, name)).fetchone()
            if r:
                return r[0]
            try:
                return c.execute("INSERT INTO items(owner_id, parent_id, name, kind, created, modified) VALUES(?,?,?,'folder',?,?)", (user_id, parent, name, now, now)).lastrowid
            except sqlite3.IntegrityError:
                return None
        top = folder_id(None, ROOT_NAME)
        if top is None:
            return {"added": 0, "removed": 0}
        seen = set()
        count = 0
        for dirpath, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            rel = Path(dirpath).relative_to(base)
            parent = top
            for part in rel.parts:
                parent = folder_id(parent, part)
                if parent is None:
                    break
            if parent is None:
                dirs[:] = []
                continue
            for name in sorted(files):
                if name.startswith(".") or name.lower().endswith(SKIP_SUFFIXES):
                    continue
                p = Path(dirpath) / name
                try:
                    if p.is_symlink() or not p.is_file():
                        continue
                    st = p.stat()
                    name.encode("utf-8")
                except (OSError, UnicodeError):
                    continue
                if st.st_size <= 0:
                    continue
                count += 1
                if count > limit:
                    break
                blob = blob_name(str(p))
                seen.add(blob)
                if c.execute("SELECT 1 FROM items WHERE owner_id=? AND blob=?", (user_id, blob)).fetchone():
                    c.execute("UPDATE items SET size=?, modified=? WHERE owner_id=? AND blob=? AND (size!=? )", (st.st_size, st.st_mtime, user_id, blob, st.st_size))
                    continue
                link = store.blob_path(blob)
                try:
                    link.parent.mkdir(exist_ok=True)
                    if link.is_symlink() or link.exists():
                        link.unlink()
                    os.symlink(str(p), str(link))
                    from server import guess_mime
                    c.execute("INSERT INTO items(owner_id, parent_id, name, kind, size, mime, blob, sha256, created, modified) VALUES(?,?,?,'file',?,?,?,?,?,?)",
                              (user_id, parent, name, st.st_size, guess_mime(name), blob, "", st.st_mtime, st.st_mtime))
                    added += 1
                except (OSError, sqlite3.IntegrityError):
                    try:
                        link.unlink()
                    except OSError:
                        pass
        # entries whose original is gone: take them out (the link only; the original is not ours to touch)
        for r in c.execute("SELECT id, blob FROM items WHERE owner_id=? AND kind='file' AND blob LIKE ?", (user_id, PREFIX + "%")).fetchall():
            if r["blob"] not in seen:
                try:
                    link = store.blob_path(r["blob"])
                    if link.is_symlink():
                        link.unlink()
                except OSError:
                    pass
                c.execute("DELETE FROM shares WHERE item_id=?", (r["id"],))
                c.execute("DELETE FROM items WHERE id=?", (r["id"],))
                removed += 1
    return {"added": added, "removed": removed}


def sync_all(store, users_json: Path, files_dir: Path) -> dict:
    folders = read_users(users_json)
    out = {}
    with closing(store.conn()) as c:
        rows = [(r["id"], (r["sso_email"] or "").lower()) for r in c.execute("SELECT id, sso_email FROM users WHERE disabled=0 AND sso_email IS NOT NULL")]
    for uid, email in rows:
        folder = folders.get(email)
        if folder:
            out[email] = sync_user(store, uid, Path(files_dir) / folder)
    return out


def start(store, users_json: Path, files_dir: Path, every: float = 60.0) -> threading.Thread:
    def loop():
        while True:
            try:
                sync_all(store, users_json, files_dir)
            except Exception as exc:                                    # noqa: BLE001 - one bad pass must not stop the next
                print("downloads sync:", repr(exc))
            time.sleep(every)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
