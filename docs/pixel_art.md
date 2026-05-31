# Pixel art on the LCD

Draw arbitrary **custom pixel art** on the StackChan's 320×240 LCD. You
author a small low-resolution grid (a palette plus a grid of color
indices); the gateway packs it and the firmware upscales it to fill the
screen with crisp, blocky pixels.

Unlike the camera/photo path, this needs **no `VISION_HOST` / HTTP
setup**: the image rides the WebSocket the device is already connected
on. The payload is tiny (a 32×24 grid is ~2 KB).

- **Resolution:** 32×24 cells, upscaled ×10 to exactly fill 320×240.
- **Colors:** a palette of up to 16 colors; each cell references one by
  index.
- **Persistence:** the art stays on screen (on top of the avatar) until
  you call `clear_pixel_art` — autonomous avatar reactions (touch, IMU,
  blink) keep happening underneath but are covered.

## Requirements

- **Firmware:** a build that includes the `self.display.draw_pixels` /
  `self.display.clear_pixels` MCP tools (built from
  `firmware/scripts/release.py stackchan`; flash `build/merged-binary.bin`).
- **Gateway:** a build that includes the `draw_pixel_art` /
  `clear_pixel_art` tools.

If the device firmware predates these tools, `draw_pixel_art` returns an
error from the device.

## Tools

### `draw_pixel_art(palette, pixels)`

| Field | Type | Description |
|---|---|---|
| `palette` | array of strings | 1–16 colors, each `#RRGGBB`. Index 0 is the first entry. |
| `pixels` | array of strings | Exactly **24 rows**, each a **32-character** string. Every character is a single hex digit `0`–`f` selecting a palette entry. |

The gateway validates the grid, expands it into a little-endian RGB565
buffer, base64-encodes it, and forwards it to the device as
`self.display.draw_pixels`. The firmware nearest-neighbor upscales it to
320×240 and shows it 1:1 (centered, undistorted), persisting until
cleared.

### `clear_pixel_art()`

Removes the pixel art and reveals the avatar underneath. Maps to
`self.display.clear_pixels`.

### Example

A red heart on a near-black background:

```json
draw_pixel_art({
  "palette": ["#0B0B14", "#FF3358"],
  "pixels": [
    "00000000000000000000000000000000",
    "00000011111110000001111111000000",
    "00001111111111111111111111110000",
    "00111111111111111111111111111100",
    "00111111111111111111111111111100",
    "01111111111111111111111111111110",
    "01111111111111111111111111111110",
    "01111111111111111111111111111110",
    "01111111111111111111111111111110",
    "00111111111111111111111111111100",
    "00111111111111111111111111111100",
    "00011111111111111111111111111000",
    "00001111111111111111111111110000",
    "00000111111111111111111111100000",
    "00000001111111111111111110000000",
    "00000000111111111111111100000000",
    "00000000001111111111110000000000",
    "00000000000011111111000000000000",
    "00000000000000111100000000000000",
    "00000000000000000000000000000000",
    "00000000000000000000000000000000",
    "00000000000000000000000000000000",
    "00000000000000000000000000000000",
    "00000000000000000000000000000000"
  ]
})
```

Then `clear_pixel_art({})` to return to the avatar.

## How to create images

The image is a **palette + index grid**, the same idea as classic
pixel-art / fantasy-console formats:

1. **Pick a palette** — up to 16 colors as `#RRGGBB`. Index `0` is the
   first color (commonly the background).
2. **Draw a 32-wide × 24-tall grid** — 24 rows, each row a 32-character
   string. Each character is a hex digit (`0`–`9`, `a`–`f`) that picks a
   palette color by index.

Each cell becomes a 10×10 block on the LCD. The grid is 4:3, matching the
320×240 screen, so cells render as squares.

### By hand

Lay out 24 rows of 32 characters. Use `0` for the background and other
digits for foreground colors. A tiny 3-color example (only the first few
rows shown — a full image needs all 24 rows of 32 chars):

```text
palette = ["#101018", "#FFCC00", "#1A1A22"]   # bg, yellow, dark
row  = "00000000001111111111111100000000"      # a band of yellow
```

Tips:
- All 24 rows must be exactly 32 characters, or the gateway rejects the
  call.
- An index must be smaller than the palette length (e.g. with a 2-color
  palette, only `0` and `1` are valid).
- Keep important detail away from the very edges — at ×10 a one-cell
  border is 10 px.

### From an existing image (downscale)

Use [Pillow](https://python-pillow.org/) to shrink any image to the grid
and reduce it to ≤16 colors:

```python
from PIL import Image

W, H, MAXC = 32, 24, 16

im = Image.open("art.png").convert("RGB")
# NEAREST keeps hard edges for pixel-art sources; use LANCZOS for photos.
im = im.resize((W, H), Image.NEAREST)

pal = im.quantize(colors=MAXC)                 # <=16 colors, indexed
n = max(pal.getdata()) + 1                      # colors actually used
raw = pal.getpalette()
palette = ["#%02X%02X%02X" % tuple(raw[i*3:i*3+3]) for i in range(n)]

idx = list(pal.getdata())
pixels = ["".join("%x" % idx[y*W + x] for x in range(W)) for y in range(H)]

# `palette` and `pixels` are the draw_pixel_art arguments.
```

### Procedurally

You can also compute the grid in code. For example, a filled disc:

```python
W, H = 32, 24
palette = ["#0B0B14", "#37C8FF"]
pixels = []
for y in range(H):
    row = ""
    for x in range(W):
        inside = ((x - 15.5) / 14) ** 2 + ((y - 11.5) / 11) ** 2 <= 1.0
        row += "1" if inside else "0"
    pixels.append(row)
```

### Validate before sending (optional)

The gateway's encoder raises a clear `ValueError` on any malformed grid.
You can dry-run it from this checkout:

```python
from stackchan_mcp.pixel_art import encode_pixel_art
b64, w, h = encode_pixel_art(palette, pixels)   # raises on bad input
```

## Color fidelity

The LCD is RGB565, so each `#RRGGBB` is reduced to 5 bits red, 6 green,
5 blue (the gateway packs it exactly like `convert_avatars.py`). Very
close shades may collapse to the same on-screen color. The conversion is
done on the gateway; you always author in `#RRGGBB`.

## Behavior and limits

- **Grid size is fixed at 32×24** in this version. Other sizes are not
  accepted by `draw_pixel_art`.
- **Persists until cleared.** The art sits on the LVGL top layer, above
  the avatar, so avatar state changes don't pop through. `clear_pixel_art`
  hides it and the avatar reappears.
- **No network setup.** The grid travels inline over the existing
  WebSocket — `VISION_HOST` is irrelevant here.
- **Brightness/standby** still apply: `set_brightness(0)` or a screen-off
  state will hide it like anything else on the LCD.

## Troubleshooting

- **`{"error": "No ESP32 device connected."}`** — the device isn't
  connected to the gateway. Check power / WiFi / the WebSocket URL.
- **Validation errors** (`palette must be a list of 1..16 colors`,
  `pixels must be a list of 24 rows`, `row N must be ... 32 ...`,
  `pixel (x,y) index N exceeds palette size`) — fix the grid shape /
  palette as described above; nothing is sent to the device.
- **Call succeeds but nothing shows** — check the device log. A success
  prints `DrawPixelArt: 32x24 -> 320x240 (x10) shown`; a rejection prints
  `DrawPixelArt rejected: ...`.
- **Device returns an error / unknown tool** — the firmware is older than
  the pixel-art tools; reflash a current build.

## Where it lives

- Gateway tool + encoder: `gateway/stackchan_mcp/stdio_server.py`
  (`draw_pixel_art` / `clear_pixel_art`) and
  `gateway/stackchan_mcp/pixel_art.py` (`encode_pixel_art`,
  `GRID_WIDTH`, `GRID_HEIGHT`, `MAX_PALETTE`).
- Firmware tool + render: `self.display.draw_pixels` /
  `self.display.clear_pixels` and `DrawPixelArt` / `ClearPixelArt` in
  `firmware/main/boards/stackchan/stackchan.cc`.
- Tool-name mapping: `docs/architecture.md`.
