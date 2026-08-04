#!/usr/bin/env python3
"""Export compact H&E thumbnails for atlas spots with retained source images."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata-jsonl", type=Path, required=True)
    parser.add_argument("--search-root", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-pixels", type=int, default=1200)
    return parser.parse_args()


def slide_ids(path: Path) -> list[str]:
    values: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            slide_id = str(json.loads(line).get("slice_id") or "").strip()
            if slide_id:
                values.add(slide_id)
    return sorted(values)


def gsm_token(value: str) -> str | None:
    matches = re.findall(r"GSM\d+", value, flags=re.IGNORECASE)
    return matches[-1].upper() if matches else None


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")


def source_index(roots: list[Path]) -> dict[str, Path]:
    indexed: dict[str, Path] = {}
    for root in roots:
        for path in root.rglob("tissue_lowres_image.png"):
            sample_name = path.parent.parent.name
            indexed.setdefault(sample_name.lower(), path)
            token = gsm_token(sample_name)
            if token:
                indexed.setdefault(token.lower(), path)
    return indexed


def match_source(slide_id: str, indexed: dict[str, Path]) -> Path | None:
    exact = indexed.get(slide_id.lower())
    if exact:
        return exact
    token = gsm_token(slide_id)
    return indexed.get(token.lower()) if token else None


def image_extent(source: Path) -> tuple[int, int]:
    hires = source.with_name("tissue_hires_image.png")
    with Image.open(hires if hires.exists() else source) as image:
        return image.size


def main() -> None:
    args = parse_args()
    image_dir = args.output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    indexed = source_index(args.search_root)
    manifest: dict[str, dict[str, object]] = {}
    for slide_id in slide_ids(args.metadata_jsonl):
        source = match_source(slide_id, indexed)
        if source is None:
            continue
        output_name = f"{safe_name(slide_id)}.webp"
        destination = image_dir / output_name
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((args.max_pixels, args.max_pixels), Image.Resampling.LANCZOS)
            image.save(destination, format="WEBP", quality=82, method=6)
        width, height = image_extent(source)
        manifest[slide_id] = {
            "file": f"images/{output_name}",
            "coordinate_width": width,
            "coordinate_height": height,
        }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "histagent_atlas_tissue_images_v1",
                "slides": manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Exported {len(manifest)} atlas tissue images")


if __name__ == "__main__":
    main()
