#!/usr/bin/env python3
"""Generate the downscaled WebP images the Strategium serves to browsers.

Source PNGs stay untouched; re-run after adding or repainting artwork:
    .venv/bin/python scripts/build_web_assets.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
WEB = ASSETS / "web"
RANK_CARDS_SOURCE_DIR = Path(
    os.getenv(
        "STRATEGIUM_RANK_CARDS_SOURCE",
        str(ROOT.parent / "discord-bots" / "op-scribe-servitor" / "assets" / "ranks"),
    )
)


def _save(source: Path, target: Path, *, max_size: tuple[int, int] | None = None, quality: int = 82) -> None:
    image = Image.open(source)
    image = image.convert("RGBA" if "A" in image.getbands() or image.mode == "P" else "RGB")
    if max_size:
        image.thumbnail(max_size, Image.Resampling.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "WEBP", quality=quality, method=6, alpha_quality=90)
    print(f"{target.relative_to(ROOT)}  {source.stat().st_size // 1024} KB -> {target.stat().st_size // 1024} KB")


def build_map_icons() -> None:
    import cv2

    sheet = Image.open(ASSETS / "40k map icons.webp").convert("RGBA")
    output = WEB / "map-icons"
    output.mkdir(parents=True, exist_ok=True)
    centers = {
        "planet": (20, 92), "station": (20, 111),
        "forge_world": (267, 92), "shrine_world": (267, 126),
        "penal_world": (267, 195), "mining_world": (267, 212),
        "fortress_world": (267, 316), "feral_world": (267, 384),
        "agri_world": (267, 418), "frontier_world": (267, 435),
        "hive_world": (267, 486), "pleasure_world": (267, 519),
        "death_world": (267, 570), "war_world": (267, 587),
        "dead_world": (267, 810), "watch_fortress": (24, 918),
        "special": (24, 1177),
        "star_o": (978, 95), "star_b": (978, 123), "star_a": (978, 150),
        "star_f": (978, 177), "star_g": (978, 205), "star_k": (978, 232), "star_m": (978, 260),
    }
    scale = sheet.width / 1147
    for key, (center_x, center_y) in centers.items():
        radius = 13 if key.startswith("star_") else 9
        box = tuple(round(value * scale) for value in (center_x - radius, center_y - radius, center_x + radius, center_y + radius))
        pixels = np.array(sheet.crop(box))
        white = np.all(pixels[:, :, :3] > 230, axis=2).astype(np.uint8)
        _, components = cv2.connectedComponents(white, connectivity=4)
        exterior = set(components[0]) | set(components[-1]) | set(components[:, 0]) | set(components[:, -1])
        exterior.discard(0)
        pixels[np.isin(components, list(exterior)), 3] = 0
        image = Image.fromarray(pixels)
        image.thumbnail((64, 64), Image.Resampling.LANCZOS)
        image.save(output / f"{key}.webp", "WEBP", lossless=True, method=6)
    print(f"Built {len(centers)} classification icons in {output.relative_to(ROOT)}")


def build_galactic_assets() -> dict:
    import cv2

    output = WEB / "galactic-map"
    output.mkdir(parents=True, exist_ok=True)
    sources = {}
    for status, suffix in (("secure", "SECURE"), ("critical", "CONTESTED"), ("lost", "LOST")):
        directory = ASSETS / f"JERICHO MAP - {suffix}" / f"JERICHO MAP - {suffix}"
        paths = {int(path.stem.split("-")[0]): path for path in directory.glob(f"*-{suffix}.png")}
        if set(paths) != set(range(1, 30)):
            raise ValueError(f"Expected sectors 1-29 in {directory}")
        sources[status] = paths
    secure = [np.asarray(Image.open(sources["secure"][number]).convert("RGB")) for number in range(1, 30)]
    if len({image.shape for image in secure}) != 1:
        raise ValueError("Sector layers must have identical dimensions")
    base = np.median(np.stack(secure), axis=0).astype(np.uint8)
    height, width = base.shape[:2]
    Image.fromarray(base).save(output / "base.webp", "WEBP", lossless=True, method=6)
    green = (base[:, :, 1].astype(int) > base[:, :, 0].astype(int) * 1.25) & (base[:, :, 1] > 75) & (base[:, :, 1].astype(int) > base[:, :, 2].astype(int) * 1.15)
    lines = cv2.dilate(green.astype(np.uint8), np.ones((7, 7), np.uint8))
    _, components = cv2.connectedComponents(1 - lines, connectivity=4)
    outside = int(components[0, 0])
    sector_mask = np.zeros((height, width), dtype=np.uint8)
    sectors = []
    used = {0, outside}
    for number, image in enumerate(secure, start=1):
        difference = np.abs(image.astype(np.int16) - base.astype(np.int16)).sum(axis=2)
        nearby = cv2.dilate((difference > 20).astype(np.uint8), np.ones((19, 19), np.uint8))
        votes = np.bincount(components[nearby != 0].ravel())
        for component in used:
            if component < len(votes):
                votes[component] = 0
        component = int(votes.argmax())
        if not votes[component]:
            raise ValueError(f"No distinct enclosed region for sector {number}")
        used.add(component)
        mask = (components == component).astype(np.uint8)
        depth = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
        label_y, label_x = np.unravel_index(depth.argmax(), depth.shape)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contour = max(contours, key=cv2.contourArea)
        boundary = cv2.approxPolyDP(contour, .75, True)[:, 0, :].tolist()
        if len(boundary) < 3 or depth.max() < 3:
            raise ValueError(f"Invalid sector {number} geometry")
        sector_mask[mask != 0] = number
        sectors.append({"number": number, "boundary": boundary, "label": [int(label_x), int(label_y)]})
    for status, paths in sources.items():
        for number, path in sorted(paths.items()):
            image = np.asarray(Image.open(path).convert("RGB"))
            if image.shape != base.shape:
                raise ValueError(f"Layer dimensions differ: {path}")
            changed = np.any(image != base, axis=2)
            rgba = np.dstack((image, changed.astype(np.uint8) * 255))
            Image.fromarray(rgba).save(output / f"{number}-{status}.webp", "WEBP", lossless=True, method=6)
    Image.fromarray(sector_mask).save(output / "sectors.png")
    geometry = {"width": width, "height": height, "sectors": sectors}
    (output / "geometry.json").write_text(json.dumps(geometry, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(sectors)} sectors and 87 sparse status layers in {output.relative_to(ROOT)}")
    build_map_icons()
    return geometry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--galactic-map-only", action="store_true")
    parser.add_argument("--map-icons-only", action="store_true")
    args = parser.parse_args()
    if args.map_icons_only:
        build_map_icons()
        return
    if args.galactic_map_only:
        build_galactic_assets()
        return
    for source in sorted((ASSETS / "Painted Pauldrons" / "Completed").glob("*.png")):
        _save(source, WEB / "pauldrons" / f"{source.stem}.webp", max_size=(192, 192))
    for source in sorted((ASSETS / "Ranks").glob("*.png")):
        name = re.sub(r"^\d+-", "", source.stem)
        _save(source, WEB / "ranks" / f"{name}.webp", max_size=(360, 128))
    rank_card_sources = sorted(RANK_CARDS_SOURCE_DIR.glob("*.png")) if RANK_CARDS_SOURCE_DIR.is_dir() else []
    if rank_card_sources:
        for source in rank_card_sources:
            _save(source, WEB / "rank-cards" / f"{source.stem}-preview.webp", max_size=(720, 405), quality=80)
            _save(source, WEB / "rank-cards" / f"{source.stem}.webp", max_size=(1200, 675), quality=86)
    else:
        for source in sorted((WEB / "rank-cards").glob("*.webp")):
            if source.stem.endswith("-preview"):
                continue
            _save(source, source.with_name(f"{source.stem}-preview.webp"), max_size=(720, 405), quality=80)
    for source in ("Armory", "Apothecarion", "Librarians", "Reclusiam", "Recon", "Watch_Blades"):
        _save(ASSETS / f"{source}.png", WEB / "formation-symbols" / f"{source}.webp", max_size=(96, 96))
    _save(ASSETS / "jericho symbol.png", WEB / "jericho-symbol.webp", max_size=(96, 96))
    _save(ASSETS / "Inquisitorial_Rosette.png", WEB / "inquisitorial-rosette.webp", max_size=(96, 96))
    _save(ASSETS / "Watch_Fortress_Jericho_Map.png", ASSETS / "Watch_Fortress_Jericho_Map.webp", quality=80)
    _save(ASSETS / "Jericho_Warp_Storm.png", ASSETS / "Jericho_Warp_Storm.webp", quality=78)
    _save(ASSETS / "Quiet_Stars.png", ASSETS / "Quiet_Stars.webp", quality=82)
    base = np.asarray(Image.open(ASSETS / "Watch_Fortress_Jericho_Map.png").convert("RGBA"), dtype=np.int16)
    for source in sorted((ASSETS / "Jericho Fortress Layers").glob("*.png")):
        # Layers repaint the whole atlas; keep only pixels that differ from the base map.
        layer = np.asarray(Image.open(source).convert("RGBA"), dtype=np.int16)
        changed = np.abs(layer - base).sum(axis=2) > 12
        changed = Image.fromarray((changed * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))
        sparse = layer.astype(np.uint8)
        sparse[:, :, 3] = np.minimum(sparse[:, :, 3], np.asarray(changed))
        target = source.with_suffix(".webp")
        Image.fromarray(sparse, "RGBA").save(target, "WEBP", quality=78, method=6, alpha_quality=80)
        print(f"{target.relative_to(ROOT)}  {source.stat().st_size // 1024} KB -> {target.stat().st_size // 1024} KB")
    if (ASSETS / "JERICHO MAP - SECURE").is_dir():
        build_galactic_assets()


if __name__ == "__main__":
    main()
