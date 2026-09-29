"""Build a one-byte-per-pixel section map from the fortress highlight PNGs."""

from __future__ import annotations

import binascii
from collections import deque
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import FORTRESS_LAYERS, FORTRESS_LAYERS_DIR  # noqa: E402

BASE_MAP = ROOT / "assets" / "Watch_Fortress_Jericho_Map.png"
OUTPUT = ROOT / "assets" / "atlas-hover-mask.png"

ATLAS_LAYER_ORDER = [
    "strategium",
    "armory",
    "apothecarion",
    "reclusiam",
    "black-vault",
    "librarius",
    "dueling-grounds",
    "company-primus",
    "company-secundus",
    "company-tertius",
    "company-quartus",
    "company-quintus",
    "flight-deck",
    "vehicle-bays",
    "astropathic-choir",
]


def decode_rgba(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a PNG: {path}")

    offset = 8
    compressed = bytearray()
    width = height = color_type = bit_depth = None
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        offset += length + 12
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            if (bit_depth, color_type, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                raise ValueError(f"Expected non-interlaced 8-bit RGBA PNG: {path}")
        elif chunk_type == b"IDAT":
            compressed.extend(chunk)
        elif chunk_type == b"IEND":
            break

    assert width is not None and height is not None
    raw = zlib.decompress(compressed)
    stride = width * 4
    pixels = bytearray(stride * height)

    def paeth(left: int, above: int, upper_left: int) -> int:
        estimate = left + above - upper_left
        distances = (
            abs(estimate - left),
            abs(estimate - above),
            abs(estimate - upper_left),
        )
        return (left, above, upper_left)[distances.index(min(distances))]

    source = 0
    for y in range(height):
        filter_type = raw[source]
        source += 1
        row_start = y * stride
        for x in range(stride):
            value = raw[source]
            source += 1
            left = pixels[row_start + x - 4] if x >= 4 else 0
            above = pixels[row_start + x - stride] if y else 0
            upper_left = pixels[row_start + x - stride - 4] if y and x >= 4 else 0
            if filter_type == 1:
                value += left
            elif filter_type == 2:
                value += above
            elif filter_type == 3:
                value += (left + above) // 2
            elif filter_type == 4:
                value += paeth(left, above, upper_left)
            elif filter_type != 0:
                raise ValueError(f"Unsupported PNG filter {filter_type}: {path}")
            pixels[row_start + x] = value & 0xFF

    return width, height, bytes(pixels)


def png_chunk(kind: bytes, content: bytes) -> bytes:
    checksum = binascii.crc32(kind + content) & 0xFFFFFFFF
    return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", checksum)


def encode_mask(width: int, height: int, mask: bytearray) -> bytes:
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        start = y * width
        rows.extend(mask[start : start + width])
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(rows, 9))
        + png_chunk(b"IEND", b"")
    )


def fill_green_outline(
    width: int, height: int, outline: bytearray
) -> tuple[bytearray, int]:
    points = [index for index, value in enumerate(outline) if value]
    if not points:
        return outline, 0

    xs = [index % width for index in points]
    ys = [index // width for index in points]
    left, right = max(0, min(xs) - 3), min(width - 1, max(xs) + 3)
    top, bottom = max(0, min(ys) - 3), min(height - 1, max(ys) + 3)
    crop_width, crop_height = right - left + 1, bottom - top + 1
    blocked = bytearray(crop_width * crop_height)

    # Close small antialiasing gaps before filling the outlined building interior.
    gap_radius = 3
    for index in points:
        x, y = index % width - left, index // width - top
        for offset_y in range(-gap_radius, gap_radius + 1):
            for offset_x in range(-gap_radius, gap_radius + 1):
                px, py = x + offset_x, y + offset_y
                if 0 <= px < crop_width and 0 <= py < crop_height:
                    blocked[py * crop_width + px] = 1

    exterior = bytearray(len(blocked))
    queue: deque[int] = deque()

    def add_exterior(index: int) -> None:
        if not blocked[index] and not exterior[index]:
            exterior[index] = 1
            queue.append(index)

    for x in range(crop_width):
        add_exterior(x)
        add_exterior((crop_height - 1) * crop_width + x)
    for y in range(crop_height):
        add_exterior(y * crop_width)
        add_exterior(y * crop_width + crop_width - 1)

    while queue:
        index = queue.popleft()
        x, y = index % crop_width, index // crop_width
        if x:
            add_exterior(index - 1)
        if x + 1 < crop_width:
            add_exterior(index + 1)
        if y:
            add_exterior(index - crop_width)
        if y + 1 < crop_height:
            add_exterior(index + crop_width)

    result = bytearray(width * height)
    for index in points:
        result[index] = 1

    filled_pixels = 0
    visited = bytearray(len(blocked))
    for index, is_blocked in enumerate(blocked):
        if is_blocked or exterior[index] or visited[index]:
            continue
        component: list[int] = []
        queue.append(index)
        visited[index] = 1
        while queue:
            current = queue.popleft()
            component.append(current)
            x, y = current % crop_width, current // crop_width
            for neighbor in (
                current - 1 if x else -1,
                current + 1 if x + 1 < crop_width else -1,
                current - crop_width if y else -1,
                current + crop_width if y + 1 < crop_height else -1,
            ):
                if neighbor >= 0 and not blocked[neighbor] and not exterior[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)
        if len(component) < 100:
            continue
        filled_pixels += len(component)
        for local_index in component:
            x, y = local_index % crop_width, local_index // crop_width
            result[(top + y) * width + left + x] = 1

    if filled_pixels < 100:
        outline_points = sorted({(index % width, index // width) for index in points})

        def cross(origin: tuple[int, int], first: tuple[int, int], second: tuple[int, int]) -> int:
            return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (second[0] - origin[0])

        lower: list[tuple[int, int]] = []
        for point in outline_points:
            while len(lower) > 1 and cross(lower[-2], lower[-1], point) <= 0:
                lower.pop()
            lower.append(point)
        upper: list[tuple[int, int]] = []
        for point in reversed(outline_points):
            while len(upper) > 1 and cross(upper[-2], upper[-1], point) <= 0:
                upper.pop()
            upper.append(point)
        hull = lower[:-1] + upper[:-1]

        if len(hull) >= 3:
            hull_left = min(point[0] for point in hull)
            hull_right = max(point[0] for point in hull)
            hull_top = min(point[1] for point in hull)
            hull_bottom = max(point[1] for point in hull)
            for y in range(hull_top, hull_bottom + 1):
                scan_y = y + .5
                intersections = []
                for index, (x1, y1) in enumerate(hull):
                    x2, y2 = hull[(index + 1) % len(hull)]
                    if (y1 <= scan_y < y2) or (y2 <= scan_y < y1):
                        intersections.append(x1 + (scan_y - y1) * (x2 - x1) / (y2 - y1))
                if len(intersections) < 2:
                    continue
                left_x = max(hull_left, int(min(intersections)))
                right_x = min(hull_right, int(max(intersections)))
                start = y * width + left_x
                result[start : start + right_x - left_x + 1] = b"\x01" * (right_x - left_x + 1)
                filled_pixels += right_x - left_x + 1

    return result, filled_pixels


def main() -> None:
    width, height, base = decode_rgba(BASE_MAP)
    mask = bytearray(width * height)
    counts: dict[str, int] = {}

    for section_id, key in enumerate(ATLAS_LAYER_ORDER, start=1):
        layer_path = FORTRESS_LAYERS_DIR / FORTRESS_LAYERS[key]
        layer_width, layer_height, layer = decode_rgba(layer_path)
        if (layer_width, layer_height) != (width, height):
            raise ValueError(f"Layer must match {width}x{height}: {layer_path}")

        outline = bytearray(width * height)
        count = 0
        for pixel in range(width * height):
            index = pixel * 4
            red, green, blue = layer[index], layer[index + 1], layer[index + 2]
            changed = max(
                abs(base[index] - red),
                abs(base[index + 1] - green),
                abs(base[index + 2] - blue),
            )
            is_green_outline = (
                green > red * 1.3
                and green > blue * 1.08
                and green > 55
                and changed > 24
            )
            if is_green_outline:
                outline[pixel] = 1
                count += 1
        if count == 0:
            raise ValueError(f"No green highlight pixels detected in {layer_path}")
        section_mask, filled = fill_green_outline(width, height, outline)
        for pixel, selected in enumerate(section_mask):
            if selected:
                mask[pixel] = section_id
        counts[key] = (count, filled)

    OUTPUT.write_bytes(encode_mask(width, height, mask))
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({width}x{height})")
    for key, (outline_count, filled_count) in counts.items():
        print(f"{key}: {outline_count} outline pixels, {filled_count} enclosed section pixels")


if __name__ == "__main__":
    main()