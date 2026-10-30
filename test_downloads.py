import json, os, tempfile, unittest
from pathlib import Path
import downloads, server as srv


class DownloadsTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)
        t = self.t = Path(self.d.name)
        self.store = srv.Store(t / "cloud")
        self.anna = self.store.add_user("anna", "Anna", "a-long-password-1")
        self.bob = self.store.add_user("bob", "Bob", "a-long-password-2")
        self.store.link_sso("anna", "anna@x.org"); self.store.link_sso("bob", "bob@x.org")
        self.files = t / "DownloaderUsers"
        (self.files / "anna" / "Music" / "Pop").mkdir(parents=True); (self.files / "bob" / "Videos").mkdir(parents=True)
        (self.files / "anna" / "Music" / "Pop" / "song.mp3").write_bytes(b"x" * 1000)
        (self.files / "anna" / "Music" / "Pop" / "half.mp3.part").write_bytes(b"x" * 10)
        (self.files / "bob" / "Videos" / "clip.mp4").write_bytes(b"y" * 500)
        self.users = t / "users.json"
        self.users.write_text(json.dumps({"users": {"anna@x.org": {"name": "Anna", "folder": "anna"}, "bob@x.org": {"name": "Bob", "folder": "bob"}, "ghost@x.org": {"name": "G", "folder": "../etc"}}}))

    def tree(self, uid):
        with self.store.conn() as c:
            rows = c.execute("SELECT id, parent_id, name, kind, size FROM items WHERE owner_id=?", (uid,)).fetchall()
        by = {r["id"]: r for r in rows}
        def path(i):
            return (path(by[i]["parent_id"]) + "/" if by[i]["parent_id"] else "") + by[i]["name"]
        return sorted(path(r["id"]) for r in rows if r["kind"] == "file")

    def test_each_person_gets_their_own_files_in_their_own_downloads_folder(self):
        out = downloads.sync_all(self.store, self.users, self.files)
        self.assertEqual(out["anna@x.org"], {"added": 1, "removed": 0})
        self.assertEqual(self.tree(self.anna), ["Downloads/Music/Pop/song.mp3"])           # the half-finished download is left out
        self.assertEqual(self.tree(self.bob), ["Downloads/Videos/clip.mp4"])
        self.assertNotIn("ghost@x.org", out)

    def test_nothing_is_copied_and_the_file_can_be_read(self):
        downloads.sync_all(self.store, self.users, self.files)
        with self.store.conn() as c:
            item = c.execute("SELECT id FROM items WHERE owner_id=? AND name='song.mp3'", (self.anna,)).fetchone()
        info = self.store.file_for_read(self.anna, item["id"])
        self.assertTrue(info["path"].is_symlink())
        self.assertEqual(info["path"].read_bytes(), b"x" * 1000)
        with self.assertRaises(srv.ApiError):
            self.store.file_for_read(self.bob, item["id"])                                     # Bob cannot read Anna's

    def test_follows_new_and_removed_files_and_is_repeatable(self):
        downloads.sync_all(self.store, self.users, self.files)
        self.assertEqual(downloads.sync_all(self.store, self.users, self.files)["anna@x.org"], {"added": 0, "removed": 0})
        (self.files / "anna" / "Music" / "Pop" / "new.mp3").write_bytes(b"z" * 20)
        (self.files / "anna" / "Music" / "Pop" / "song.mp3").unlink()
        self.assertEqual(downloads.sync_all(self.store, self.users, self.files)["anna@x.org"], {"added": 1, "removed": 1})
        self.assertEqual(self.tree(self.anna), ["Downloads/Music/Pop/new.mp3"])

    def test_removing_it_in_cloud_never_deletes_the_original(self):
        downloads.sync_all(self.store, self.users, self.files)
        with self.store.conn() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM items WHERE owner_id=?", (self.anna,))]
        with self.store.write_lock, self.store.conn() as c:
            self.store._purge(c, ids)
            c.commit()
        self.assertTrue((self.files / "anna" / "Music" / "Pop" / "song.mp3").is_file())

    def test_linked_files_do_not_use_the_quota(self):
        downloads.sync_all(self.store, self.users, self.files)
        self.assertEqual(self.store.usage(self.anna), 0)


if __name__ == "__main__":
    unittest.main()
