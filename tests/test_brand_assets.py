"""The integration's brand images must satisfy what Home Assistant serves.

Home Assistant serves ``<integration>/brand/<image>`` from disk for custom
integrations (it checks ``has_branding``, which is true when a ``brand``
directory exists) and falls back to the brands CDN otherwise. So the files
travel in the release archive, and their names and sizes have to match the
brands repository's specification or the UI falls back to a generic icon:

    icon.png      256x256
    icon@2x.png   512x512

Square, PNG, opaque. A 1024px marketing render dropped in as ``icon.png`` still
displays, but it costs every user 20x the bytes for something drawn at 48px.

The artwork itself is the vendor's own app icon: the Surplife iOS app
(``com.zennge.magichome2``, 1.7.2) ships it as the sole ``AppIcon`` rendition in
``Assets.car``, and the App Store listing for the same app serves the identical
1024x1024 artwork. Using it means a user recognises the integration next to the
app they set the roof up with.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

BRAND = Path(__file__).resolve().parent.parent / "custom_components" / "surplife" / "brand"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: ``name -> (width, height)``, per the Home Assistant brands specification.
EXPECTED = {
    "icon.png": (256, 256),
    "icon@2x.png": (512, 512),
}


def _png_header(path: Path) -> tuple[int, int, int, int]:
    """Return ``(width, height, bit_depth, colour_type)`` from the IHDR chunk."""
    data = path.read_bytes()
    assert data.startswith(PNG_SIGNATURE), f"{path.name} is not a PNG"
    # Byte 8 starts the first chunk: 4-byte length, 4-byte type, then IHDR body.
    assert data[12:16] == b"IHDR", f"{path.name} has no IHDR chunk"
    width, height, bit_depth, colour_type = struct.unpack(">IIBB", data[16:26])
    return width, height, bit_depth, colour_type


@pytest.mark.parametrize(("name", "size"), sorted(EXPECTED.items()))
def test_brand_images_match_the_serving_specification(name, size):
    path = BRAND / name
    assert path.is_file(), f"missing brand asset {name}"

    width, height, bit_depth, colour_type = _png_header(path)

    assert (width, height) == size
    assert bit_depth == 8
    # 2 = truecolour RGB, 6 = truecolour with alpha. The icon is a full bleed
    # square, so alpha would only be dead weight.
    assert colour_type == 2


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_brand_images_are_optimised(name):
    """A correct image at ten times the necessary size is still a regression."""
    path = BRAND / name
    width, height, _, _ = _png_header(path)
    pixels = width * height
    # The artwork is a smooth two-colour gradient, which compresses well: the
    # 256px icon lands near 45 kB. This bound catches "saved as uncompressed",
    # not pixel-perfect tuning.
    assert path.stat().st_size < pixels / 1.2, (
        f"{name} is {path.stat().st_size} bytes for {pixels} pixels"
    )


def test_brand_images_are_readable_pngs():
    """The IDAT stream must inflate, or the browser shows a broken image."""
    for name in EXPECTED:
        data = (BRAND / name).read_bytes()
        idat = bytearray()
        offset = 8
        while offset < len(data):
            (length,) = struct.unpack(">I", data[offset : offset + 4])
            chunk_type = data[offset + 4 : offset + 8]
            if chunk_type == b"IDAT":
                idat += data[offset + 8 : offset + 8 + length]
            offset += 12 + length
        assert idat, f"{name} has no image data"
        zlib.decompress(bytes(idat))


def test_no_stray_files_in_the_brand_directory():
    """Only the names Home Assistant resolves are allowed to ship.

    ``logo.png`` is deliberately absent: the artwork is square, and the brands
    specification says a square brand should ship icons only -- ``icon.png`` is
    the documented fallback for the logo.
    """
    assert sorted(p.name for p in BRAND.iterdir()) == sorted(EXPECTED)
