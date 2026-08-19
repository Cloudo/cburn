"""The monochrome speedometer for the macOS menu bar.

Renders src-tauri/icons/tray.png: black on transparent, 72x72 - macOS templates
draw from the alpha channel alone, so the glyph is the alpha mask itself.

Run:   python3 tools/tray_icon.py [out.png]
"""

import math
import struct
import sys
import zlib

SIZE = 72
SS = 4  # subsamples per axis

CX, CY = 0.5, 0.5
R_OUT, R_IN = 0.40, 0.285
GAP_HALF = math.radians(38)  # opening at the bottom of the dial
NEEDLE_ANGLE = math.radians(-50)
NEEDLE_LEN = 0.335
NEEDLE_HALF_W = 0.048
HUB_R = 0.085

NX, NY = math.cos(NEEDLE_ANGLE), math.sin(NEEDLE_ANGLE)


def inside(u, v):
    du, dv = u - CX, v - CY
    d = math.hypot(du, dv)
    # dial ring with the bottom sector cut out
    if R_IN <= d <= R_OUT:
        ang = math.atan2(dv, du)
        if abs(ang - math.pi / 2) > GAP_HALF:
            return True
    # hub
    if d <= HUB_R:
        return True
    # needle as a capsule from the hub outward
    t = du * NX + dv * NY
    t = max(0.0, min(NEEDLE_LEN, t))
    px, py = CX + NX * t, CY + NY * t
    return math.hypot(u - px, v - py) <= NEEDLE_HALF_W


rows = []
for y in range(SIZE):
    row = bytearray([0])  # filter byte
    for x in range(SIZE):
        hits = 0
        for sy in range(SS):
            for sx in range(SS):
                u = (x + (sx + 0.5) / SS) / SIZE
                v = (y + (sy + 0.5) / SS) / SIZE
                if inside(u, v):
                    hits += 1
        a = round(255 * hits / (SS * SS))
        row += bytes((0, 0, 0, a))
    rows.append(bytes(row))


def chunk(typ, data):
    return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data))


png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
png += chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
png += chunk(b"IEND", b"")
out = sys.argv[1] if len(sys.argv) > 1 else "src-tauri/icons/tray.png"
open(out, "wb").write(png)
print(f"wrote {out}: {SIZE}x{SIZE}, {len(png)} bytes")
