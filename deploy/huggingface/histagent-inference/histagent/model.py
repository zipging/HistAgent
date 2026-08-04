from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F
from peft import LoraConfig, get_peft_model


GIGAPATH_MODEL_ID = "prov-gigapath/prov-gigapath"


class RankWeightedCrossEntropy(nn.Module):
    """Cross entropy that assigns larger weights to earlier gene ranks."""

    def __init__(self, pad_id: int, label_smoothing: float = 0.1) -> None:
        super().__init__()
        self.pad_id = pad_id
        self.label_smoothing = label_smoothing

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        batch_size, length, vocab_size = logits.shape
        ranks = torch.arange(length, device=logits.device, dtype=torch.float32)
        weights = ((length - ranks) / length).unsqueeze(0).expand(batch_size, -1)
        weights = weights.masked_fill(targets.eq(self.pad_id), 0.0)
        loss = F.cross_entropy(
            logits.reshape(batch_size * length, vocab_size),
            targets.reshape(batch_size * length),
            ignore_index=self.pad_id,
            label_smoothing=self.label_smoothing,
            reduction="none",
        )
        flat_weights = weights.reshape(batch_size * length)
        return (loss * flat_weights).sum() / flat_weights.sum().clamp_min(1e-8)


class PerceiverResampler(nn.Module):
    def __init__(
        self,
        embed_dim: int,
        input_dim: int,
        num_latents: int = 16,
        num_heads: int = 8,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.latents = nn.Parameter(torch.randn(1, num_latents, embed_dim))
        self.input_proj = nn.Linear(input_dim, embed_dim)
        self.cross_attn = nn.MultiheadAttention(
            embed_dim, num_heads, batch_first=True, dropout=dropout
        )
        self.ln1 = nn.LayerNorm(embed_dim)
        self.ln2 = nn.LayerNorm(embed_dim)
        self.ln3 = nn.LayerNorm(embed_dim)
        self.ffn = nn.Sequential(
            nn.Linear(embed_dim, embed_dim * 4),
            nn.GELU(),
            nn.Linear(embed_dim * 4, embed_dim),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        if features.dim() == 2:
            features = features.unsqueeze(1)
        features = self.ln1(self.input_proj(features))
        latents = self.latents.expand(features.shape[0], -1, -1)
        attended, _ = self.cross_attn(latents, features, features)
        latents = self.ln2(latents + attended)
        return self.ln3(latents + self.ffn(latents))


def _new_gigapath_encoder(
    model_id: str = GIGAPATH_MODEL_ID,
    checkpoint_path: str | Path | None = None,
) -> nn.Module:
    """Load the official GigaPath tile encoder from the Hub or a local checkpoint."""

    import timm

    if checkpoint_path is None:
        return timm.create_model(f"hf_hub:{model_id}", pretrained=True)

    model = timm.create_model(
        model_name="vit_giant_patch14_dinov2",
        img_size=224,
        in_chans=3,
        patch_size=16,
        embed_dim=1536,
        depth=40,
        num_heads=24,
        init_values=1e-5,
        mlp_ratio=5.33334,
        num_classes=0,
    )
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if isinstance(state, dict) and "model" in state:
        state = state["model"]
    model.load_state_dict(state, strict=True)
    return model


class HistAgentVisualOmics(nn.Module):
    """GigaPath-backed visual-omics model for ranked gene generation."""

    def __init__(
        self,
        *,
        vocab_size: int,
        num_organs: int,
        num_species: int,
        d_model: int = 512,
        n_head: int = 8,
        n_layers: int = 6,
        max_len: int = 52,
        dropout: float = 0.1,
        noise_scale: float = 0.02,
        num_latents: int = 16,
        vision_embed_dim: int = 1536,
        base_model_id: str = GIGAPATH_MODEL_ID,
        base_checkpoint_path: str | Path | None = None,
        use_lora: bool = True,
    ) -> None:
        super().__init__()
        self.noise_scale = noise_scale
        self.base_model_id = base_model_id
        self.vision_encoder = _new_gigapath_encoder(
            model_id=base_model_id, checkpoint_path=base_checkpoint_path
        )
        self.vision_embed_dim = self._infer_vision_embed_dim(
            self.vision_encoder, fallback=vision_embed_dim
        )

        if use_lora:
            lora_config = LoraConfig(
                r=16,
                lora_alpha=32,
                target_modules=["qkv", "proj", "fc1", "fc2"],
                lora_dropout=0.05,
                bias="none",
            )
            self.vision_encoder = get_peft_model(self.vision_encoder, lora_config)
        else:
            self.vision_encoder.requires_grad_(False)

        self.local_projector = PerceiverResampler(
            d_model, self.vision_embed_dim, num_latents, n_head, dropout
        )
        self.global_projector = PerceiverResampler(
            d_model, self.vision_embed_dim, num_latents, n_head, dropout
        )
        self.organ_embedding = nn.Embedding(num_organs + 1, d_model)
        self.species_embedding = nn.Embedding(num_species + 1, d_model)
        self.gene_embedding = nn.Embedding(vocab_size, d_model)
        self.pos_enc = nn.Parameter(torch.zeros(1, max_len, d_model))
        self.pos_drop = nn.Dropout(dropout)
        self.decoder = nn.TransformerDecoder(
            nn.TransformerDecoderLayer(
                d_model, n_head, dropout=dropout, batch_first=True, norm_first=True
            ),
            num_layers=n_layers,
        )
        self.head = nn.Linear(d_model, vocab_size)
        self._init_weights()

    @staticmethod
    def _infer_vision_embed_dim(encoder: nn.Module, fallback: int) -> int:
        for obj in (encoder, getattr(encoder, "model", None)):
            if obj is None:
                continue
            for name in ("num_features", "embed_dim", "hidden_size"):
                value = getattr(obj, name, None)
                if isinstance(value, int) and value > 0:
                    return value
        return fallback

    def _init_weights(self) -> None:
        modules = (
            self.organ_embedding,
            self.species_embedding,
            self.local_projector,
            self.global_projector,
            self.gene_embedding,
            self.decoder,
            self.head,
        )
        for module in modules:
            for parameter in module.parameters():
                if parameter.dim() > 1:
                    nn.init.xavier_uniform_(parameter)

    @staticmethod
    def _normalize_vision_output(output: Any) -> torch.Tensor:
        if isinstance(output, (tuple, list)):
            output = output[0]
        elif isinstance(output, dict):
            for key in (
                "features",
                "embedding",
                "embeddings",
                "x",
                "last_hidden_state",
                "x_norm_patchtokens",
                "x_norm_clstoken",
            ):
                if key in output:
                    output = output[key]
                    break
        if not torch.is_tensor(output):
            raise TypeError(f"The vision encoder returned {type(output)!r}, not a tensor.")
        if output.dim() == 4:
            output = output.flatten(2).transpose(1, 2)
        return output

    def encode_images(self, images: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Encode paired local/context crops with shape ``(B, 2, 3, 224, 224)``."""

        batch_size, views, channels, height, width = images.shape
        if views != 2:
            raise ValueError(f"Expected local and context views, received {views} views.")
        encoded = self.vision_encoder(
            images.reshape(batch_size * views, channels, height, width)
        )
        encoded = self._normalize_vision_output(encoded)
        if encoded.dim() == 2:
            encoded = encoded.reshape(batch_size, views, -1)
        elif encoded.dim() == 3:
            encoded = encoded.reshape(batch_size, views, encoded.shape[1], -1)
        else:
            raise ValueError(f"Unsupported vision output shape: {tuple(encoded.shape)}")

        if self.training and self.noise_scale > 0:
            encoded = encoded + torch.randn_like(encoded) * self.noise_scale
        return self.local_projector(encoded[:, 0]), self.global_projector(encoded[:, 1])

    def _memory(
        self, images: torch.Tensor, organ_id: torch.Tensor, species_id: torch.Tensor
    ) -> torch.Tensor:
        local_tokens, context_tokens = self.encode_images(images)
        organ_tokens = self.organ_embedding(organ_id).unsqueeze(1)
        species_tokens = self.species_embedding(species_id).unsqueeze(1)
        return torch.cat(
            [species_tokens, organ_tokens, local_tokens, context_tokens], dim=1
        )

    def forward(
        self,
        images: torch.Tensor,
        organ_id: torch.Tensor,
        species_id: torch.Tensor,
        target_sequence: torch.Tensor,
        target_mask: torch.Tensor | None = None,
        target_padding_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        batch_size = images.shape[0]
        if self.training:
            organ_id = organ_id.clone()
            species_id = species_id.clone()
            organ_id[torch.rand(batch_size, device=images.device) < 0.15] = 0
            species_id[torch.rand(batch_size, device=images.device) < 0.15] = 0
        memory = self._memory(images, organ_id, species_id)
        target = self.gene_embedding(target_sequence)
        target = self.pos_drop(target + self.pos_enc[:, : target_sequence.shape[1]])
        decoded = self.decoder(
            target,
            memory,
            tgt_mask=target_mask,
            tgt_key_padding_mask=target_padding_mask,
        )
        return self.head(decoded)

    @staticmethod
    def causal_mask(length: int, device: torch.device | None = None) -> torch.Tensor:
        return torch.triu(
            torch.full((length, length), float("-inf"), device=device), diagonal=1
        )

    @torch.inference_mode()
    def generate(
        self,
        images: torch.Tensor,
        organ_id: torch.Tensor,
        species_id: torch.Tensor,
        *,
        bos_id: int,
        eos_id: int,
        pad_id: int,
        unk_id: int,
        max_new_tokens: int = 50,
    ) -> torch.Tensor:
        memory = self._memory(images, organ_id, species_id)
        generated = torch.full(
            (images.shape[0], 1), bos_id, dtype=torch.long, device=images.device
        )
        finished = torch.zeros(images.shape[0], dtype=torch.bool, device=images.device)
        forbidden = [{pad_id, bos_id, unk_id} for _ in range(images.shape[0])]

        for _ in range(max_new_tokens):
            target = self.gene_embedding(generated)
            target = self.pos_drop(target + self.pos_enc[:, : generated.shape[1]])
            decoded = self.decoder(
                target, memory, tgt_mask=self.causal_mask(generated.shape[1], images.device)
            )
            logits = self.head(decoded[:, -1]).clone()
            for row, blocked in enumerate(forbidden):
                if not finished[row]:
                    logits[row, list(blocked)] = -torch.inf
            next_id = logits.argmax(dim=-1)
            generated = torch.cat([generated, next_id[:, None]], dim=1)
            for row, token_id in enumerate(next_id.tolist()):
                if token_id == eos_id:
                    finished[row] = True
                else:
                    forbidden[row].add(token_id)
            if bool(finished.all()):
                break
        return generated


# Backward-compatible name used by the training code that produced the release.
EndToEndModelV3 = HistAgentVisualOmics
RankWeightedCELoss = RankWeightedCrossEntropy
