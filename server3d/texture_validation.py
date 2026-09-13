"""Opt-in, bounded embedded PNG textures for managed components.

This is not a general image/model importer. There are no URLs, plugins, external
processes or optional native decoders. Only canonical non-interlaced 8-bit RGB(A)
PNG is supported; exporters must strip other metadata and convert other formats.
"""
from __future__ import annotations

import hashlib
import struct
import zlib

from .glb_validation import _array, _fail, _integer, _object, _reference

TEXTURE_PROFILE = "embedded-png-v1"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 4096
MAX_TEXTURE_PIXELS = 16 * 1024 * 1024
MAX_TEXTURES = 8
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def inspect_png(payload: bytes) -> dict:
    """Validate actual PNG framing, CRCs and bounded zlib scanlines before decode."""
    if not 45 <= len(payload) <= MAX_IMAGE_BYTES or not payload.startswith(PNG_SIGNATURE):
        _fail("texture must be a bounded embedded PNG")
    offset = 8
    width = height = channels = 0
    compressed = bytearray()
    idat = ended = srgb = False
    count = 0
    while offset < len(payload):
        count += 1
        if count > 256 or offset + 12 > len(payload) or ended:
            _fail("invalid PNG chunk framing or count")
        length, kind = struct.unpack_from(">I4s", payload, offset)
        start, end = offset + 8, offset + 8 + length
        if end + 4 > len(payload):
            _fail("PNG chunk exceeds image bytes")
        data = payload[start:end]
        crc = struct.unpack_from(">I", payload, end)[0]
        if zlib.crc32(data, zlib.crc32(kind)) & 0xffffffff != crc:
            _fail("PNG chunk CRC mismatch")
        if count == 1:
            if kind != b"IHDR" or length != 13:
                _fail("PNG must start with IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if not (1 <= width <= MAX_IMAGE_SIDE and 1 <= height <= MAX_IMAGE_SIDE):
                _fail("PNG dimensions exceed texture budget")
            if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
                _fail("PNG must be non-interlaced 8-bit RGB or RGBA")
            channels = 3 if color == 2 else 4
        elif kind == b"sRGB" and not srgb and not idat:
            if length != 1 or data[0] > 3:
                _fail("invalid PNG sRGB intent")
            srgb = True
        elif kind == b"IDAT":
            if not length:
                _fail("empty PNG IDAT is unsupported")
            idat = True
            compressed.extend(data)
        elif kind == b"IEND":
            if length or not idat or end + 4 != len(payload):
                _fail("invalid PNG end or trailing bytes")
            ended = True
        else:
            _fail("unsupported PNG chunk; export canonical RGB(A) PNG without metadata")
        offset = end + 4
    if not ended:
        _fail("PNG is missing IEND")
    stride = 1 + width * channels
    expected = stride * height
    decoder = zlib.decompressobj()
    try:
        # max_length is essential: do not decompress a tiny attacker-controlled
        # payload without a ceiling. Never call unbounded decompress()/flush().
        scanlines = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error as exc:
        _fail(f"invalid PNG compressed data ({type(exc).__name__})")
    if len(scanlines) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        _fail("PNG decoded size or compressed stream mismatch")
    if any(scanlines[row * stride] > 4 for row in range(height)):
        _fail("invalid PNG scanline filter")
    return {"width": width, "height": height, "pixels": width * height,
            "byte_length": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def inspect_texture_resources(document: dict, binary: bytes, views: list) -> dict:
    images = _array(document.get("images", []), "images", MAX_TEXTURES)
    textures = _array(document.get("textures", []), "textures", MAX_TEXTURES)
    samplers = _array(document.get("samplers", []), "samplers", MAX_TEXTURES)
    image_views = set()
    image_metadata = []
    for image in images:
        _object(image, "image", {"name", "mimeType", "bufferView"})
        if image.get("mimeType") != "image/png":
            _fail("only embedded image/png is supported")
        vi = _reference(image.get("bufferView"), views, "image bufferView")
        view = views[vi]
        if "byteStride" in view or "target" in view or vi in image_views:
            _fail("image bufferViews must be unique, tightly packed and have no target")
        start, size = view.get("byteOffset", 0), view["byteLength"]
        if size > MAX_IMAGE_BYTES:
            _fail("PNG encoded byte budget exceeded")
        # Image bytes cannot masquerade as geometry, including through a second
        # overlapping view. Ordinary interleaved geometry remains supported.
        for other_i, other in enumerate(views):
            other_start = other.get("byteOffset", 0)
            if other_i != vi and max(start, other_start) < min(start + size, other_start + other["byteLength"]):
                _fail("image bufferView overlaps another bufferView")
        image_views.add(vi)
        image_metadata.append(inspect_png(binary[start:start + size]))
    for sampler in samplers:
        _object(sampler, "sampler", {"name", "magFilter", "minFilter", "wrapS", "wrapT"})
        choices = {"magFilter": (9728, 9729), "minFilter": (9728, 9729, 9984, 9985, 9986, 9987),
                   "wrapS": (33071, 33648, 10497), "wrapT": (33071, 33648, 10497)}
        for key, allowed in choices.items():
            if key in sampler and (type(sampler[key]) is not int or sampler[key] not in allowed):
                _fail(f"unsupported sampler {key}")
    image_ids, sampler_ids = set(), set()
    texture_pixels = 0
    for texture in textures:
        _object(texture, "texture", {"name", "source", "sampler"})
        source = _reference(texture.get("source"), images, "texture source")
        image_ids.add(source)
        # Charge every texture, not only every image: different samplers can
        # cause separate GPU allocations even when their image bytes are shared.
        texture_pixels += image_metadata[source]["pixels"]
        if "sampler" in texture:
            sampler_ids.add(_reference(texture["sampler"], samplers, "texture sampler"))
    if image_ids != set(range(len(images))) or sampler_ids != set(range(len(samplers))):
        _fail("unused images or samplers are unsupported")
    if texture_pixels > MAX_TEXTURE_PIXELS:
        _fail("component texture pixel budget exceeded")
    return {"images": image_metadata, "image_views": image_views,
            "texture_count": len(textures), "texture_pixels": texture_pixels}
