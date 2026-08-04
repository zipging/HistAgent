from __future__ import annotations

from pathlib import Path

import torch
from PIL import Image
from torchvision import transforms

from .model import HistAgentVisualOmics
from .tokenizer import GeneTokenizer


_NORMALIZE = transforms.Normalize(
    mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)
)


def _open_rgb(image: str | Path | Image.Image) -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    return Image.open(image).convert("RGB")


def preprocess_pair(
    local_image: str | Path | Image.Image,
    context_image: str | Path | Image.Image,
) -> torch.Tensor:
    """Prepare the local and surrounding H&E views used by HistAgent."""

    transform = transforms.Compose(
        [transforms.CenterCrop(224), transforms.ToTensor(), _NORMALIZE]
    )
    local = transform(_open_rgb(local_image))
    context = transform(_open_rgb(context_image))
    return torch.stack([local, context], dim=0)


@torch.inference_mode()
def predict_ranked_genes(
    model: HistAgentVisualOmics,
    tokenizer: GeneTokenizer,
    local_image: str | Path | Image.Image,
    context_image: str | Path | Image.Image,
    *,
    organ: str = "Unknown",
    species: str = "unknown",
    top_k: int = 50,
    device: str | torch.device | None = None,
) -> list[str]:
    if device is None:
        device = next(model.parameters()).device
    images = preprocess_pair(local_image, context_image).unsqueeze(0).to(device)
    organ_id = torch.tensor([tokenizer.organ_id(organ)], device=device)
    species_id = torch.tensor([tokenizer.species_id(species)], device=device)
    generated = model.generate(
        images,
        organ_id,
        species_id,
        bos_id=tokenizer.bos_id,
        eos_id=tokenizer.eos_id,
        pad_id=tokenizer.pad_id,
        unk_id=tokenizer.unk_id,
        max_new_tokens=top_k,
    )
    return tokenizer.decode(generated[0].tolist()[1:])[:top_k]
