#!/usr/bin/env python3
"""Download and verify the public HistAgent Chat data bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import snapshot_download


DEFAULT_REPO_ID = "wli13/HistAgent-data"
APP_DIR = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(data_dir: Path) -> None:
    manifest_path = data_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing dataset manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest.get("files", []):
        relative = str(item.get("path", ""))
        if not relative.startswith("chat/"):
            continue
        path = data_dir / relative
        if not path.is_file():
            raise FileNotFoundError(f"Missing downloaded file: {path}")
        expected_size = int(item["bytes"])
        if path.stat().st_size != expected_size:
            raise ValueError(f"Size mismatch for {path}")
        if sha256(path) != item["sha256"]:
            raise ValueError(f"Checksum mismatch for {path}")


def download_chat_data(
    repo_id: str = DEFAULT_REPO_ID,
    data_dir: Path = APP_DIR / "data",
) -> Path:
    data_dir = data_dir.resolve()
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        allow_patterns=["manifest.json", "chat/**"],
        local_dir=data_dir,
    )
    verify_manifest(data_dir)
    chat_dir = data_dir / "chat"
    spots = chat_dir / "spots.jsonl"
    images = chat_dir / "slice_images"
    if not spots.is_file():
        raise FileNotFoundError(spots)

    data_env = APP_DIR / "data.env"
    data_env.write_text(
        "\n".join(
            [
                f"HISTAGENT_INPUT_JSONL={spots}",
                "HISTAGENT_ATLAS_SQLITE=",
                f"HISTAGENT_SLICE_IMAGE_DIRS={images}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return chat_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument("--data-dir", type=Path, default=APP_DIR / "data")
    args = parser.parse_args()
    chat_dir = download_chat_data(args.repo_id, args.data_dir)
    print(f"HistAgent Chat data ready: {chat_dir}")


if __name__ == "__main__":
    main()
