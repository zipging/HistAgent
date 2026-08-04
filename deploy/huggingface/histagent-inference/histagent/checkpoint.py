from __future__ import annotations

import os
from pathlib import Path

import torch
from safetensors.torch import load_file

from .config import HistAgentConfig
from .model import HistAgentVisualOmics
from .tokenizer import GeneTokenizer


DEFAULT_REPO_ID = "wli13/HistAgent"


def _is_frozen_base_key(key: str) -> bool:
    return key.startswith("vision_encoder.") and ".lora_" not in key


def load_pretrained(
    repo_id_or_path: str | Path = DEFAULT_REPO_ID,
    *,
    token: str | None = None,
    base_checkpoint_path: str | Path | None = None,
    device: str | torch.device = "cpu",
) -> tuple[HistAgentVisualOmics, GeneTokenizer, HistAgentConfig]:
    """Load HistAgent's trained LoRA and gene decoder on the official GigaPath base."""

    source = Path(repo_id_or_path)
    if source.exists():
        model_dir = source
    else:
        from huggingface_hub import snapshot_download

        model_dir = Path(
            snapshot_download(
                repo_id=str(repo_id_or_path),
                token=token,
                allow_patterns=[
                    "config.json",
                    "model.safetensors",
                    "gene_vocab.txt",
                    "organ_vocab.json",
                    "species_vocab.json",
                ],
            )
        )

    if token:
        os.environ.setdefault("HF_TOKEN", token)
    config = HistAgentConfig.from_json(model_dir / "config.json")
    tokenizer = GeneTokenizer(
        model_dir / "gene_vocab.txt",
        model_dir / "organ_vocab.json",
        model_dir / "species_vocab.json",
    )
    if tokenizer.vocab_size != config.vocab_size:
        raise ValueError(
            f"Vocabulary has {tokenizer.vocab_size} tokens; expected {config.vocab_size}."
        )

    model = HistAgentVisualOmics(
        vocab_size=config.vocab_size,
        num_organs=config.num_organs,
        num_species=config.num_species,
        d_model=config.d_model,
        n_head=config.n_head,
        n_layers=config.n_layers,
        max_len=config.max_len,
        num_latents=config.num_latents,
        vision_embed_dim=config.vision_embed_dim,
        base_model_id=config.base_model_id,
        base_checkpoint_path=base_checkpoint_path,
        use_lora=True,
    )
    release_state = load_file(model_dir / "model.safetensors", device="cpu")
    missing, unexpected = model.load_state_dict(release_state, strict=False)
    invalid_missing = [key for key in missing if not _is_frozen_base_key(key)]
    if invalid_missing or unexpected:
        raise RuntimeError(
            "Checkpoint mismatch. "
            f"Missing non-base keys: {invalid_missing}; unexpected keys: {unexpected}."
        )
    model.to(device).eval()
    return model, tokenizer, config
