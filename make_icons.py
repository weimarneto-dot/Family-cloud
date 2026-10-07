#!/usr/bin/env python3
"""Writes static/icon-180.png and static/icon-512.png (the PNGs iOS wants for the home screen)
with the standard library only: a green rounded square with a white cloud."""
import struct
import zlib
from pathlib import Path


def png(size):
    green, white, clear = (11, 107, 83, 255), (255, 255, 255, 255), (0, 0, 0, 0)
    s = size / 512
    circles = [(190, 296, 78), (270, 235, 98), (370, 290, 70)]
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            X, Y = x / s, y / s
            cx, cy = min(max(X, 112), 400), min(max(Y, 112), 400)
            inside = (X - cx) ** 2 + (Y - cy) ** 2 <= 112 ** 2
            cloud = any((X - a) ** 2 + (Y - b) ** 2 <= r * r for a, b, r in circles) or (190 <= X <= 370 and 290 <= Y <= 370) \
                or ((X - 190) ** 2 + (Y - 296) ** 2 <= 74 ** 2 and Y > 296)
            row += bytes(clear if not inside else (white if cloud else green))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "static"
    for size in (180, 512):
        (out / f"icon-{size}.png").write_bytes(png(size))
        print("wrote", out / f"icon-{size}.png")
