from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


SIZES = (16, 24, 32, 48, 64, 128, 256)
ACCENT = (96, 205, 255, 255)
DANGER = (255, 107, 107, 255)
SURFACE = (15, 20, 25, 255)
TRANSPARENT = (0, 0, 0, 0)


def sample(normal_x: float, normal_y: float) -> tuple[int, int, int, int]:
    distance = math.hypot(normal_x, normal_y)
    if distance > 0.46:
        return TRANSPARENT
    if distance >= 0.34:
        return ACCENT
    if distance >= 0.22:
        return SURFACE
    return DANGER


def pixel(size: int, x: int, y: int) -> tuple[int, int, int, int]:
    samples: list[tuple[int, int, int, int]] = []
    for sample_y in range(4):
        for sample_x in range(4):
            px = (x + (sample_x + 0.5) / 4) / size - 0.5
            py = (y + (sample_y + 0.5) / 4) / size - 0.5
            samples.append(sample(px, py))
    alpha_total = sum(color[3] for color in samples)
    if alpha_total == 0:
        return TRANSPARENT
    red = sum(color[0] * color[3] for color in samples) // alpha_total
    green = sum(color[1] * color[3] for color in samples) // alpha_total
    blue = sum(color[2] * color[3] for color in samples) // alpha_total
    alpha = alpha_total // len(samples)
    return red, green, blue, alpha


def png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def create_png(size: int) -> bytes:
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            rows.extend(pixel(size, x, y))
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        header
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + png_chunk(b"IEND", b"")
    )


def create_icon(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    images = [(size, create_png(size)) for size in SIZES]
    offset = 6 + 16 * len(images)
    entries = bytearray()
    payload = bytearray()
    for size, image in images:
        dimension = 0 if size == 256 else size
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(image),
                offset,
            )
        )
        payload.extend(image)
        offset += len(image)
    destination.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + entries + payload)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    create_icon(root / "assets" / "AeroRecorder.ico")
