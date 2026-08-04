#!/usr/bin/env python3
"""Assemble atlas H&E thumbnails from exact contextual patches when WSIs are absent."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas-metadata", type=Path, required=True)
    parser.add_argument("--training-metadata", type=Path, required=True)
    parser.add_argument("--patch-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-spots", type=int, default=500)
    parser.add_argument("--slide-id", action="append")
    parser.add_argument("--patch-side", type=int, default=68)
    parser.add_argument("--max-pixels", type=int, default=1200)
    return parser.parse_args()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")


def selected_rows(path: Path) -> dict[str, list[dict[str, object]]]:
    rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("x") is None or record.get("y") is None:
                continue
            rows[str(record["slice_id"])].append(
                {
                    "barcode": str(record["barcode"]),
                    "x": float(record["x"]),
                    "y": float(record["y"]),
                }
            )
    return rows


def main() -> None:
    args = parse_args()
    manifest_path = args.output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing = dict(manifest.get("slides") or {})
    atlas_rows = selected_rows(args.atlas_metadata)
    target_slides = sorted(
        slide_id
        for slide_id, rows in atlas_rows.items()
        if len(rows) >= args.min_spots and slide_id not in existing
        and (not args.slide_id or slide_id in set(args.slide_id))
    )
    if not target_slides:
        print("No patch mosaics were needed")
        return

    training = pd.read_parquet(
        args.training_metadata,
        columns=["slice_id", "barcode", "h5_idx"],
        filters=[("slice_id", "in", target_slides)],
    )
    by_slide = {
        str(slide_id): dict(zip(group["barcode"].astype(str), group["h5_idx"].astype(int)))
        for slide_id, group in training.groupby("slice_id", sort=False)
    }

    exported = 0
    image_dir = args.output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    half = args.patch_side // 2
    for slide_id in target_slides:
        h5_path = args.patch_dir / f"{slide_id}_patches.h5"
        indices = by_slide.get(slide_id, {})
        records = [row for row in atlas_rows[slide_id] if row["barcode"] in indices]
        if not h5_path.exists() or len(records) < args.min_spots:
            continue
        width = int(math.ceil(max(float(row["x"]) for row in records) + half + 2))
        height = int(math.ceil(max(float(row["y"]) for row in records) + half + 2))
        canvas = Image.new("RGB", (width, height), "white")
        with h5py.File(h5_path, "r") as handle:
            patches = handle["global"]
            ordered = sorted(records, key=lambda row: indices[row["barcode"]])
            selected_indices = [indices[row["barcode"]] for row in ordered]
            selected_patches = np.asarray(patches[selected_indices], dtype=np.uint8)
            for row, values in zip(ordered, selected_patches):
                patch = Image.fromarray(values)
                patch = patch.resize(
                    (args.patch_side, args.patch_side),
                    Image.Resampling.LANCZOS,
                )
                x = int(round(float(row["x"]))) - half
                y = int(round(float(row["y"]))) - half
                canvas.paste(patch, (x, y))
        output_name = f"{safe_name(slide_id)}.webp"
        canvas.thumbnail((args.max_pixels, args.max_pixels), Image.Resampling.LANCZOS)
        canvas.save(image_dir / output_name, "WEBP", quality=82, method=6)
        existing[slide_id] = {
            "file": f"images/{output_name}",
            "coordinate_width": width,
            "coordinate_height": height,
            "source": "sampled_contextual_h_and_e_patches",
        }
        exported += 1

    manifest["slides"] = existing
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Exported {exported} patch-mosaic atlas tissue images")


if __name__ == "__main__":
    main()
