#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import torch
from safetensors.torch import save_file


def sha256(path: Path, block_size: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def keep_release_key(key: str) -> bool:
    if not key.startswith("vision_encoder."):
        return True
    return ".lora_A." in key or ".lora_B." in key


def base_key(release_key: str) -> str:
    key = release_key.removeprefix("vision_encoder.base_model.model.")
    return key.replace(".base_layer.", ".")


def verify_frozen_base(full_state: dict[str, torch.Tensor], base_path: Path) -> int:
    base_state = torch.load(base_path, map_location="cpu", weights_only=True, mmap=True)
    if isinstance(base_state, dict) and "model" in base_state:
        base_state = base_state["model"]
    checked = 0
    for key, value in full_state.items():
        if not key.startswith("vision_encoder.") or keep_release_key(key):
            continue
        source_key = base_key(key)
        if source_key not in base_state:
            raise KeyError(f"Cannot map frozen parameter {key!r} to the GigaPath checkpoint.")
        if not torch.equal(value, base_state[source_key]):
            raise ValueError(f"Frozen GigaPath parameter changed during training: {key}")
        checked += 1
    return checked


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export HistAgent LoRA and non-GigaPath modules as safetensors."
    )
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--base-checkpoint", type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--vocab-dir", required=True, type=Path)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    full_state = torch.load(
        args.checkpoint, map_location="cpu", weights_only=True, mmap=True
    )
    checked_base_keys = 0
    if args.base_checkpoint:
        checked_base_keys = verify_frozen_base(full_state, args.base_checkpoint)

    release_state = {
        key: value.detach().cpu().contiguous()
        for key, value in full_state.items()
        if keep_release_key(key)
    }
    output_model = args.output_dir / "model.safetensors"
    save_file(release_state, output_model, metadata={"format": "pt"})
    shutil.copy2(args.config, args.output_dir / "config.json")
    for source_name, output_name in (
        ("gene_vocab.txt", "gene_vocab.txt"),
        ("organ_vocab.json", "organ_vocab.json"),
        ("species_vocab.json", "species_vocab.json"),
    ):
        shutil.copy2(args.vocab_dir / source_name, args.output_dir / output_name)

    manifest = {
        "source_checkpoint": args.checkpoint.name,
        "source_sha256": sha256(args.checkpoint),
        "release_file": output_model.name,
        "release_sha256": sha256(output_model),
        "source_keys": len(full_state),
        "release_keys": len(release_state),
        "verified_frozen_gigapath_keys": checked_base_keys,
        "release_bytes": output_model.stat().st_size,
        "base_model_id": "prov-gigapath/prov-gigapath",
    }
    with (args.output_dir / "checkpoint_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
