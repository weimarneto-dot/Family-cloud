import os
#!/usr/bin/env python3
"""fMily Cloud's Photos view: the date a photo was taken (the camera's EXIF, else the file's own date from the phone), newest first,
paging, only your own pictures, thumbnails turned the way the camera says.

  python3 test_photos.py
"""
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

# the suite's server runs with no free-space margin (see test_cloud.py; the margin is the real server's rule)
os.environ.setdefault("FAMILY_CLOUD_FREE_MARGIN_GB", "0")
import server as cloud
from test_cloud import Case, PNG_1x1


def exif_jpeg(taken=None, orientation=1, size="64x32"):
    """A real JPEG (ffmpeg, a size x size test picture, wider than tall) with an EXIF block: DateTimeOriginal and Orientation."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "p.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s={size}", "-frames:v", "1", str(out)], check=True)
        jpg = out.read_bytes()
    entries0 = [(0x0112, 3, 1, struct.pack("<HH", orientation, 0))]
    date = (time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(taken)).encode() + b"\0") if taken else None
    # TIFF header, IFD0 (orientation, pointer to the EXIF IFD), the EXIF IFD (DateTimeOriginal), the date text
    ifd0_off = 8
    n0 = len(entries0) + (1 if date else 0)
    exif_off = ifd0_off + 2 + 12 * n0 + 4
    date_off = exif_off + 2 + 12 + 4
    t = b"II*\0" + struct.pack("<I", ifd0_off)
    ents = [struct.pack("<HHI", tag, typ, cnt) + val for tag, typ, cnt, val in entries0]
    if date:
        ents.append(struct.pack("<HHII", 0x8769, 4, 1, exif_off))
    t += struct.pack("<H", n0) + b"".join(ents) + struct.pack("<I", 0)
    if date:
        t += struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, len(date), date_off) + struct.pack("<I", 0) + date
    app1 = b"Exif\0\0" + t
    return jpg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpg[2:]


class Photos(Case):
    def upload(self, c, name, data, parent=None, modified=None):
        status, start, _ = c.post("/api/upload/start", {"parent": parent, "name": name, "size": len(data), "modified": modified})
        self.assertEqual(status, 201, start)
        self.assertEqual(c.put_chunk(start["id"], 0, data)[0], 200)
        status, item, _ = c.post(f"/api/upload/{start['id']}/finish")
        self.assertEqual(status, 200, item)
        return item

    def test_exif_reader(self):
        t = time.mktime((2024, 7, 21, 15, 30, 5, 0, 0, -1))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.jpg"
            p.write_bytes(exif_jpeg(t, 6))
            self.assertEqual(cloud.read_exif(p), (t, 6))
            p.write_bytes(b"not a jpeg")
            self.assertEqual(cloud.read_exif(p), (None, 1))

    def test_date_order_paging_and_only_mine(self):
        ann, ben = self.users("pann", "pben")
        folder = self.folder(ann, "Holidays")["id"]
        july = time.mktime((2024, 7, 21, 15, 30, 0, 0, 0, -1))
        march = time.mktime((2025, 3, 2, 9, 0, 0, 0, 0, -1))
        a = self.upload(ann, "camera.jpg", exif_jpeg(july), folder, modified=time.time())        # EXIF wins over the file date
        b = self.upload(ann, "screenshot.png", PNG_1x1, None, modified=march)                     # no EXIF: the phone's file date
        c = self.upload(ann, "now.png", PNG_1x1)                                                  # neither: the upload time
        self.upload(ann, "notes.txt", b"hello")                                                   # not a photo
        self.upload(ben, "bens.png", PNG_1x1)                                                     # somebody else's
        self.assertAlmostEqual(a["taken"], july, delta=1)
        self.assertAlmostEqual(b["taken"], march, delta=1)
        status, page, _ = ann.get("/api/photos")
        self.assertEqual(status, 200)
        names = [i["name"] for i in page["items"]]
        self.assertEqual(names, ["now.png", "screenshot.png", "camera.jpg"], "newest taken first, in every folder, nothing else")
        self.assertFalse(page["more"])
        self.assertTrue(all(i["thumb"] for i in page["items"]))
        # paging: after the first one
        first = page["items"][0]
        status, rest, _ = ann.get(f"/api/photos?before={first['taken']}&before_id={first['id']}")
        self.assertEqual([i["name"] for i in rest["items"]], ["screenshot.png", "camera.jpg"])
        self.assertEqual(ann.get("/api/photos?before=abc")[0], 400)
        # a trashed photo leaves the view
        ann.post("/api/trash", {"id": c["id"]})
        self.assertNotIn("now.png", [i["name"] for i in ann.get("/api/photos")[1]["items"]])
        # a date from the browser that makes no sense is ignored
        odd = self.upload(ann, "future.png", PNG_1x1, None, modified=time.time() + 10 * 86400)
        self.assertGreater(odd["taken"], time.time() - 60)

    def test_photos_uploaded_before_the_view_get_their_date(self):
        ann = self.users("pold")
        july = time.mktime((2023, 7, 1, 12, 0, 0, 0, 0, -1))
        item = self.upload(ann, "old.jpg", exif_jpeg(july))
        with self.store.conn() as c:
            c.execute("UPDATE items SET taken=NULL WHERE id=?", (item["id"],))      # as before this version
        got = [i for i in ann.get("/api/photos")[1]["items"] if i["id"] == item["id"]][0]
        self.assertAlmostEqual(got["taken"], july, delta=1)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg makes the thumbnails")
    def test_thumbnail_turned_like_the_camera_says(self):
        ann = self.users("prot")
        flat = self.upload(ann, "flat.jpg", exif_jpeg(None, 1, "1280x640"))
        turned = self.upload(ann, "turned.jpg", exif_jpeg(None, 6, "1280x640"))      # held upright: stored wide, shown tall
        sizes = {}
        for it in (flat, turned):
            status, data, hdr = ann.raw("GET", f"/api/thumb/{it['id']}")
            self.assertEqual(status, 200)
            self.assertEqual(hdr["content-type"], "image/jpeg")
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "t.jpg"
                p.write_bytes(data)
                out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(p)],
                                     capture_output=True, text=True).stdout.strip()
            sizes[it["name"]] = tuple(int(x) for x in out.split(","))
        self.assertEqual(sizes["flat.jpg"], (720, 360), "the short side is 360 px, so a square tile stays sharp")
        self.assertEqual(sizes["turned.jpg"], (360, 720), "orientation 6: the thumbnail is upright")


if __name__ == "__main__":
    unittest.main()
