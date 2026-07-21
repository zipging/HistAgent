from __future__ import annotations

import argparse

import torch

from .checkpoint import DEFAULT_REPO_ID, load_pretrained
from .inference import predict_ranked_genes


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a ranked molecular readout from H&E.")
    parser.add_argument("--local", required=True, help="Spot-centred H&E crop.")
    parser.add_argument("--context", required=True, help="Surrounding-tissue H&E crop.")
    parser.add_argument("--organ", default="Unknown")
    parser.add_argument("--species", default="unknown", choices=["unknown", "human", "mouse"])
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument("--base-checkpoint", default=None)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    model, tokenizer, _ = load_pretrained(
        args.repo_id, base_checkpoint_path=args.base_checkpoint, device=args.device
    )
    genes = predict_ranked_genes(
        model,
        tokenizer,
        args.local,
        args.context,
        organ=args.organ,
        species=args.species,
        top_k=args.top_k,
        device=args.device,
    )
    print(" ".join(genes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
