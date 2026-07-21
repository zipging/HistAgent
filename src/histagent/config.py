from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class HistAgentConfig:
    architecture: str = "HistAgentVisualOmics"
    base_model_id: str = "prov-gigapath/prov-gigapath"
    vocab_size: int = 44_542
    num_organs: int = 32
    num_species: int = 3
    d_model: int = 512
    n_head: int = 8
    n_layers: int = 6
    max_len: int = 52
    num_latents: int = 16
    vision_embed_dim: int = 1_536
    lora_rank: int = 16
    lora_alpha: int = 32
    max_gene_rank: int = 50

    @classmethod
    def from_json(cls, path: str | Path) -> "HistAgentConfig":
        with Path(path).open(encoding="utf-8") as handle:
            return cls(**json.load(handle))

    def to_json(self, path: str | Path) -> None:
        with Path(path).open("w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle, indent=2)
            handle.write("\n")
