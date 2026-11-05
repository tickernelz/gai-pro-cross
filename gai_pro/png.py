"""Minimal PNG writer used for sandbox proof artifacts."""

from __future__ import annotations

import struct
import zlib


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def solid_png(width: int, height: int, rgb: tuple[int, int, int], rows: list[tuple[int, int, int, int]] | None = None) -> bytes:
    """Return PNG bytes for a solid canvas with optional coloured bands."""
    bands = rows or []
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        color = rgb
        for top, bottom, band_rgb, _pad in bands:
            if top <= y < bottom:
                color = band_rgb
                break
        raw.extend(bytes(color) * width)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def proof_png(width: int = 900, height: int = 520) -> bytes:
    bands = [
        (0, 64, (32, 33, 36), 0),
        (64, 120, (60, 64, 67), 0),
        (120, 180, (26, 115, 232), 0),
        (180, 240, (52, 168, 83), 0),
        (240, 300, (251, 188, 5), 0),
        (300, 360, (234, 67, 53), 0),
        (360, 420, (95, 99, 104), 0),
    ]
    return solid_png(width, height, (24, 24, 24), bands)
