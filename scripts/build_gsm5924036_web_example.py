#!/usr/bin/env python3
"""Build the public GSM5924036 Visium spot manifest used by the web demo."""

from __future__ import annotations

import csv
import gzip
import io
import json
import argparse
from pathlib import Path
from urllib.request import Request, urlopen


SLIDE_ID = "GSE175540_GSM5924036"
IMAGE_WIDTH = 2000
IMAGE_HEIGHT = 1988
SPOT_DIAMETER_UM = 55.0
DEFAULT_BARCODE = "ATATCTTAGGGCCTTC-1"
POSITIONS_URL = (
    "https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM5924036&"
    "file=GSM5924036_ffpe_c_21_tissue_positions_list.csv.gz&format=file"
)
SCALEFACTORS_URL = (
    "https://www.ncbi.nlm.nih.gov/geo/download/?acc=GSM5924036&"
    "file=GSM5924036_ffpe_c_21_scalefactors_json.json.gz&format=file"
)
SOURCE_IMAGE_URL = (
    "https://huggingface.co/datasets/wli13/HistAgent-data/resolve/main/"
    "chat/slice_images/GSE175540_GSM5924036.jpg"
)


def download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "HistAgent-web-example/1.0"})
    with urlopen(request, timeout=120) as response:
        return response.read()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--positions-gz", type=Path)
    parser.add_argument("--scalefactors-gz", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = Path(__file__).resolve().parents[1] / "docs" / "assets" / "gsm5924036-spots.json"
    scalefactor_bytes = (
        args.scalefactors_gz.read_bytes()
        if args.scalefactors_gz
        else download(SCALEFACTORS_URL)
    )
    positions_bytes = (
        args.positions_gz.read_bytes()
        if args.positions_gz
        else download(POSITIONS_URL)
    )
    scalefactors = json.loads(gzip.decompress(scalefactor_bytes))
    hires_scale = float(scalefactors["tissue_hires_scalef"])
    fullres_spot_diameter = float(scalefactors["spot_diameter_fullres"])
    hires_spot_diameter = fullres_spot_diameter * hires_scale
    mpp = SPOT_DIAMETER_UM / hires_spot_diameter

    text = gzip.decompress(positions_bytes).decode("utf-8")
    spots = []
    for row in csv.reader(io.StringIO(text)):
        barcode, in_tissue, array_row, array_col, pixel_row, pixel_col = row
        if int(in_tissue) != 1:
            continue
        spots.append(
            {
                "id": barcode,
                "barcode": barcode,
                "array_row": int(array_row),
                "array_col": int(array_col),
                "x": round(float(pixel_col) * hires_scale, 4),
                "y": round(float(pixel_row) * hires_scale, 4),
            }
        )

    if len(spots) != 4915:
        raise RuntimeError(f"Expected 4,915 tissue spots, found {len(spots):,}")
    if DEFAULT_BARCODE not in {spot["barcode"] for spot in spots}:
        raise RuntimeError("The selected default barcode is absent from the official coordinate file")

    manifest = {
        "schema_version": "histagent_web_example_v1",
        "slide_id": SLIDE_ID,
        "title": "Human clear-cell renal cell carcinoma · GSM5924036",
        "species": "human",
        "organ": "kidney",
        "image_url": "/assets/gsm5924036.jpg",
        "image_width": IMAGE_WIDTH,
        "image_height": IMAGE_HEIGHT,
        "mpp": round(mpp, 8),
        "spot_diameter_um": SPOT_DIAMETER_UM,
        "spot_diameter_px": round(hires_spot_diameter, 8),
        "context_diameter_um": 220.0,
        "default_barcode": DEFAULT_BARCODE,
        "source": {
            "dataset": "NCBI GEO GSE175540 / GSM5924036",
            "image_url": SOURCE_IMAGE_URL,
            "positions_url": POSITIONS_URL,
            "scalefactors_url": SCALEFACTORS_URL,
        },
        "spots": spots,
    }
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(spots):,} real Visium spots to {output}")
    print(f"55 micrometres = {hires_spot_diameter:.4f} px ({mpp:.4f} micrometres/px)")


if __name__ == "__main__":
    main()
