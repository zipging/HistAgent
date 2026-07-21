#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path

import torch
from accelerate import Accelerator
from safetensors.torch import load_file, save_file
from torch.optim import AdamW
from torch.utils.data import DataLoader
from transformers import get_cosine_schedule_with_warmup

from histagent.config import HistAgentConfig
from histagent.data import PairedHEDataset
from histagent.model import HistAgentVisualOmics, RankWeightedCrossEntropy
from histagent.tokenizer import GeneTokenizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train HistAgent-GigaPath.")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--train-parquet", required=True, type=Path)
    parser.add_argument("--val-parquet", required=True, type=Path)
    parser.add_argument("--vocab-dir", default=Path(__file__).parents[1] / "vocab", type=Path)
    parser.add_argument("--output-dir", default=Path("checkpoints"), type=Path)
    parser.add_argument("--base-checkpoint", type=Path)
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--epochs", default=30, type=int)
    parser.add_argument("--batch-size", default=24, type=int)
    parser.add_argument("--num-workers", default=8, type=int)
    parser.add_argument("--learning-rate", default=1e-4, type=float)
    parser.add_argument("--weight-decay", default=0.05, type=float)
    parser.add_argument("--label-smoothing", default=0.1, type=float)
    parser.add_argument("--mixed-precision", default="bf16", choices=["no", "fp16", "bf16"])
    return parser.parse_args()


def collate(batch):
    return tuple(torch.stack(items) for items in zip(*batch))


def release_state_dict(state: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    return {
        key: value.detach().cpu().contiguous()
        for key, value in state.items()
        if not key.startswith("vision_encoder.") or ".lora_" in key
    }


def build_model(config: HistAgentConfig, base_checkpoint: Path | None) -> HistAgentVisualOmics:
    return HistAgentVisualOmics(
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
        base_checkpoint_path=base_checkpoint,
    )


def main() -> int:
    args = parse_args()
    accelerator = Accelerator(mixed_precision=args.mixed_precision)
    config = HistAgentConfig()
    tokenizer = GeneTokenizer(
        args.vocab_dir / "gene_vocab.txt",
        args.vocab_dir / "organ_vocab.json",
        args.vocab_dir / "species_vocab.json",
    )
    train_dataset = PairedHEDataset(
        args.data_root, args.train_parquet, tokenizer, max_len=config.max_len
    )
    val_dataset = PairedHEDataset(
        args.data_root, args.val_parquet, tokenizer, max_len=config.max_len
    )
    loader_options = dict(
        batch_size=args.batch_size,
        num_workers=args.num_workers,
        pin_memory=True,
        collate_fn=collate,
    )
    train_loader = DataLoader(train_dataset, shuffle=True, **loader_options)
    val_loader = DataLoader(val_dataset, shuffle=False, **loader_options)

    model = build_model(config, args.base_checkpoint)
    if args.resume_from:
        missing, unexpected = model.load_state_dict(load_file(args.resume_from), strict=False)
        invalid_missing = [
            key
            for key in missing
            if not (key.startswith("vision_encoder.") and ".lora_" not in key)
        ]
        if invalid_missing or unexpected:
            raise RuntimeError(
                f"Resume checkpoint mismatch: missing={invalid_missing}, unexpected={unexpected}"
            )

    criterion = RankWeightedCrossEntropy(
        tokenizer.pad_id, label_smoothing=args.label_smoothing
    )
    optimizer = AdamW(
        [parameter for parameter in model.parameters() if parameter.requires_grad],
        lr=args.learning_rate,
        weight_decay=args.weight_decay,
    )
    total_steps = args.epochs * len(train_loader)
    scheduler = get_cosine_schedule_with_warmup(
        optimizer, num_warmup_steps=math.ceil(total_steps * 0.05), num_training_steps=total_steps
    )
    model, optimizer, train_loader, val_loader, scheduler = accelerator.prepare(
        model, optimizer, train_loader, val_loader, scheduler
    )

    if accelerator.is_main_process:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        config.to_json(args.output_dir / "config.json")

    for epoch in range(args.epochs):
        model.train()
        train_total = torch.zeros((), device=accelerator.device)
        for images, sequence, organ_id, species_id in train_loader:
            inputs, targets = sequence[:, :-1], sequence[:, 1:]
            optimizer.zero_grad()
            logits = model(
                images,
                organ_id,
                species_id,
                inputs,
                model.module.causal_mask(inputs.shape[1], inputs.device)
                if hasattr(model, "module")
                else model.causal_mask(inputs.shape[1], inputs.device),
                inputs.eq(tokenizer.pad_id),
            )
            loss = criterion(logits.float(), targets)
            accelerator.backward(loss)
            accelerator.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            train_total += loss.detach()

        model.eval()
        val_total = torch.zeros((), device=accelerator.device)
        val_steps = torch.zeros((), device=accelerator.device)
        with torch.inference_mode():
            for images, sequence, organ_id, species_id in val_loader:
                inputs, targets = sequence[:, :-1], sequence[:, 1:]
                unwrapped = accelerator.unwrap_model(model)
                logits = model(
                    images,
                    organ_id,
                    species_id,
                    inputs,
                    unwrapped.causal_mask(inputs.shape[1], inputs.device),
                    inputs.eq(tokenizer.pad_id),
                )
                val_total += criterion(logits.float(), targets)
                val_steps += 1
        totals = accelerator.reduce(
            torch.stack([train_total, val_total, val_steps]), reduction="sum"
        )
        if accelerator.is_main_process:
            train_loss = totals[0].item() / max(len(train_loader) * accelerator.num_processes, 1)
            val_loss = totals[1].item() / max(totals[2].item(), 1)
            print(f"epoch={epoch + 1} train_loss={train_loss:.5f} val_loss={val_loss:.5f}")
            state = accelerator.get_state_dict(model)
            save_file(
                release_state_dict(state),
                args.output_dir / f"histagent_gigapath_epoch_{epoch + 1:02d}.safetensors",
                metadata={"format": "pt"},
            )
        accelerator.wait_for_everyone()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
