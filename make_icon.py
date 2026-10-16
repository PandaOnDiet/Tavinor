"""Generate cane.ico - a dark rounded tile with the accent chevron.

Matches the "›" prompt glyph in the command bar, so the tray icon and the
bar read as the same thing.

Stdlib only: no Pillow. The .ico is written by hand (ICONDIR + BMP DIBs) and
the artwork is rasterised with 4x supersampling for the antialiasing.

    python make_icon.py
"""
import struct
from pathlib import Path

PANEL = (0x15, 0x17, 0x1C)   # tile
ACCENT = (0x4C, 0x8D, 0xFF)  # chevron
SIZES = (16, 24, 32, 48, 64, 128, 256)
SS = 4                        # supersample factor


def in_round_rect(x, y, n, r):
    cx = min(max(x, r), n - 1 - r)
    cy = min(max(y, r), n - 1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def dist_to_segment(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    L = vx * vx + vy * vy
    t = 0.0 if L == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L))
    dx, dy = wx - t * vx, wy - t * vy
    return (dx * dx + dy * dy) ** 0.5


def render(size):
    """RGBA pixels for one size, supersampled then box-filtered down."""
    n = size * SS
    r = n * 0.22
    half = n * 0.075                       # chevron half-thickness

    # A "›": down-right then down-left, round caps from the distance test.
    ax, ay = n * 0.38, n * 0.27
    bx, by = n * 0.64, n * 0.50
    cx, cy = n * 0.38, n * 0.73

    hi = []
    for y in range(n):
        row = []
        for x in range(n):
            if not in_round_rect(x, y, n, r):
                row.append(None)
                continue
            d = min(dist_to_segment(x, y, ax, ay, bx, by),
                    dist_to_segment(x, y, bx, by, cx, cy))
            row.append(ACCENT if d <= half else PANEL)
        hi.append(row)

    out = []
    for y in range(size):
        for x in range(size):
            rs = gs = bs = a = 0
            for dy in range(SS):
                for dx in range(SS):
                    p = hi[y * SS + dy][x * SS + dx]
                    if p is not None:
                        rs += p[0]; gs += p[1]; bs += p[2]; a += 1
            total = SS * SS
            if a == 0:
                out.append((0, 0, 0, 0))
            else:
                out.append((rs // a, gs // a, bs // a, round(255 * a / total)))
    return out


def bmp_dib(size, rgba):
    """BITMAPINFOHEADER + BGRA rows bottom-up + an empty AND mask."""
    hdr = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                      size * size * 4, 0, 0, 0, 0)
    rows = []
    for y in range(size - 1, -1, -1):
        row = bytearray()
        for x in range(size):
            r, g, b, a = rgba[y * size + x]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    stride = ((size + 31) // 32) * 4       # 1bpp mask, 4-byte aligned rows
    return hdr + b"".join(rows) + b"\x00" * (stride * size)


def main():
    images = [(s, bmp_dib(s, render(s))) for s in SIZES]
    n = len(images)
    entries, blobs, offset = b"", b"", 6 + 16 * n
    for size, dib in images:
        dim = 0 if size >= 256 else size   # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(dib), offset)
        offset += len(dib)
        blobs += dib
    ico = struct.pack("<HHH", 0, 1, n) + entries + blobs
    out = Path(__file__).parent / "cane.ico"
    out.write_bytes(ico)
    print("wrote %s (%d bytes, sizes %s)"
          % (out, len(ico), ", ".join(str(s) for s in SIZES)))


if __name__ == "__main__":
    main()
