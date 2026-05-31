"""Encode palette + index-grid pixel art into a base64 RGB565 blob.

The device renders a small grid scaled up nearest-neighbor to fill the
320x240 LCD, so the art is authored as a compact palette (<=16 colors)
plus a grid of single hex-digit indices. This stays tiny enough to ride
the existing WebSocket inline — no HTTP, no host config.

RGB565 packing matches firmware/scripts/avatar_convert/convert_avatars.py
exactly (standard RGB565, little-endian, "LVGL native"); the panel's BGR
order and byte swap are handled in firmware, so do not pre-swap here.
"""

from __future__ import annotations

import base64
import struct

# Locked v1 resolution: 32x24 scales x10 to exactly fill the 320x240 LCD.
GRID_WIDTH = 32
GRID_HEIGHT = 24
MAX_PALETTE = 16


def _parse_hex_color(value: object) -> tuple[int, int, int]:
    """Parse '#RRGGBB' (or 'RRGGBB') into an (r, g, b) tuple."""
    if not isinstance(value, str):
        raise ValueError(f"palette color must be a string, got {type(value).__name__}")
    s = value.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"palette color must be #RRGGBB, got {value!r}")
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
    except ValueError:
        raise ValueError(f"palette color has non-hex digits: {value!r}") from None
    return r, g, b


def _rgb565_le(r: int, g: int, b: int) -> bytes:
    """Pack one RGB888 pixel into little-endian RGB565 (LVGL native)."""
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return struct.pack("<H", rgb565)


def encode_pixel_art(
    palette: list[str],
    pixels: list[str],
    *,
    width: int = GRID_WIDTH,
    height: int = GRID_HEIGHT,
) -> tuple[str, int, int]:
    """Validate + expand a palette/index grid to a base64 RGB565 blob.

    Returns (base64_data, width, height). Raises ValueError on any
    malformed input so the caller can surface a clean MCP error.
    """
    if not isinstance(palette, list) or not (1 <= len(palette) <= MAX_PALETTE):
        raise ValueError(f"palette must be a list of 1..{MAX_PALETTE} colors")
    rgb565_palette = [_rgb565_le(*_parse_hex_color(c)) for c in palette]
    palette_size = len(rgb565_palette)

    if not isinstance(pixels, list) or len(pixels) != height:
        raise ValueError(f"pixels must be a list of {height} rows (got {len(pixels)})")

    buf = bytearray()
    for y, row in enumerate(pixels):
        if not isinstance(row, str) or len(row) != width:
            raise ValueError(
                f"row {y} must be a string of {width} hex-digit indices "
                f"(got length {len(row) if isinstance(row, str) else 'non-string'})"
            )
        for x, ch in enumerate(row):
            try:
                idx = int(ch, 16)
            except ValueError:
                raise ValueError(
                    f"pixel ({x},{y}) is not a hex digit 0-f: {ch!r}"
                ) from None
            if idx >= palette_size:
                raise ValueError(
                    f"pixel ({x},{y}) index {idx} exceeds palette size {palette_size}"
                )
            buf += rgb565_palette[idx]

    return base64.b64encode(bytes(buf)).decode("ascii"), width, height
