"""Tests for the palette/index pixel-art encoder."""

import base64
import struct

import pytest

from stackchan_mcp.pixel_art import GRID_HEIGHT, GRID_WIDTH, encode_pixel_art


def _grid(fill: str = "0", *, rows: int = GRID_HEIGHT, cols: int = GRID_WIDTH) -> list[str]:
    return [fill * cols for _ in range(rows)]


def _decode(b64: str) -> bytes:
    return base64.b64decode(b64)


def test_encode_returns_dimensions_and_blob_size():
    b64, w, h = encode_pixel_art(["#FF0000"], _grid("0"))
    assert (w, h) == (GRID_WIDTH, GRID_HEIGHT)
    assert len(_decode(b64)) == GRID_WIDTH * GRID_HEIGHT * 2


def test_rgb565_packing_matches_avatar_converter():
    # ((r&0xF8)<<8)|((g&0xFC)<<3)|(b>>3), little-endian.
    cases = {
        "#000000": struct.pack("<H", 0x0000),
        "#FFFFFF": struct.pack("<H", 0xFFFF),
        "#FF0000": struct.pack("<H", 0xF800),
        "#00FF00": struct.pack("<H", 0x07E0),
        "#0000FF": struct.pack("<H", 0x001F),
    }
    for color, expected in cases.items():
        b64, _, _ = encode_pixel_art([color], _grid("0"))
        data = _decode(b64)
        assert data[0:2] == expected, color
        # Uniform grid → every pixel identical.
        assert data == expected * (GRID_WIDTH * GRID_HEIGHT)


def test_index_selects_palette_entry():
    palette = ["#000000", "#FFFFFF", "#FF0000"]
    rows = ["012" + "0" * (GRID_WIDTH - 3)] + _grid("0", rows=GRID_HEIGHT - 1)
    b64, _, _ = encode_pixel_art(palette, rows)
    data = _decode(b64)
    assert data[0:2] == struct.pack("<H", 0x0000)  # index 0 black
    assert data[2:4] == struct.pack("<H", 0xFFFF)  # index 1 white
    assert data[4:6] == struct.pack("<H", 0xF800)  # index 2 red


def test_hex_index_above_nine():
    # 16-color palette, index 'f' (15) selects the last entry.
    palette = [f"#{i:02x}0000" for i in range(16)]
    rows = ["f" + "0" * (GRID_WIDTH - 1)] + _grid("0", rows=GRID_HEIGHT - 1)
    b64, _, _ = encode_pixel_art(palette, rows)
    data = _decode(b64)
    r = 0x0F
    assert data[0:2] == struct.pack("<H", ((r & 0xF8) << 8))


@pytest.mark.parametrize("palette", [[], ["#FF0000"] * 17])
def test_rejects_bad_palette_size(palette):
    with pytest.raises(ValueError, match="palette"):
        encode_pixel_art(palette, _grid("0"))


@pytest.mark.parametrize("color", ["#FFF", "#GG0000", "red", "FF00", "#1234567"])
def test_rejects_malformed_color(color):
    with pytest.raises(ValueError):
        encode_pixel_art([color], _grid("0"))


def test_rejects_wrong_row_count():
    with pytest.raises(ValueError, match="rows"):
        encode_pixel_art(["#FF0000"], _grid("0", rows=GRID_HEIGHT - 1))


def test_rejects_wrong_row_length():
    bad = ["0" * (GRID_WIDTH - 1)] + _grid("0", rows=GRID_HEIGHT - 1)
    with pytest.raises(ValueError, match="row 0"):
        encode_pixel_art(["#FF0000"], bad)


def test_rejects_non_hex_pixel():
    bad = ["g" + "0" * (GRID_WIDTH - 1)] + _grid("0", rows=GRID_HEIGHT - 1)
    with pytest.raises(ValueError, match="hex digit"):
        encode_pixel_art(["#FF0000"], bad)


def test_rejects_index_beyond_palette():
    # Palette has 2 entries (indices 0,1); '2' is out of range.
    bad = ["2" + "0" * (GRID_WIDTH - 1)] + _grid("0", rows=GRID_HEIGHT - 1)
    with pytest.raises(ValueError, match="exceeds palette"):
        encode_pixel_art(["#000000", "#FFFFFF"], bad)
