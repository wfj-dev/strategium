#!/usr/bin/env python3
"""Generate the downscaled WebP images the Strategium serves to browsers.

Source PNGs stay untouched; re-run after adding or repainting artwork:
    .venv/bin/python scripts/build_web_assets.py
"""

from __future__ import annotations

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


def main() -> None:
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


if __name__ == "__main__":
    main()
