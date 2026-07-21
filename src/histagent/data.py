from __future__ import annotations

from pathlib import Path

import h5py
import pandas as pd
import torch
from torch.utils.data import Dataset
from torchvision import transforms

from .tokenizer import GeneTokenizer


class PairedHEDataset(Dataset):
    """Read paired local/context H&E crops and ranked genes from HDF5 metadata."""

    required_columns = {"slice_id", "h5_idx", "top_50_genes", "organ"}

    def __init__(
        self,
        data_root: str | Path,
        metadata_parquet: str | Path,
        tokenizer: GeneTokenizer,
        max_len: int = 52,
    ) -> None:
        self.data_root = Path(data_root)
        self.frame = pd.read_parquet(metadata_parquet)
        missing = self.required_columns.difference(self.frame.columns)
        if missing:
            raise ValueError(f"Metadata is missing required columns: {sorted(missing)}")
        self.tokenizer = tokenizer
        self.max_len = max_len
        self.local_transform = transforms.Compose(
            [
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)
                ),
            ]
        )
        self.context_transform = transforms.Compose(
            [
                transforms.ToTensor(),
                transforms.CenterCrop(224),
                transforms.Normalize(
                    mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)
                ),
            ]
        )
        self._handles: dict[str, h5py.File] = {}

    def __len__(self) -> int:
        return len(self.frame)

    def __getitem__(self, index: int):
        row = self.frame.iloc[index]
        slide_id = str(row["slice_id"])
        if slide_id not in self._handles:
            self._handles[slide_id] = h5py.File(
                self.data_root / f"{slide_id}_patches.h5", "r", swmr=True
            )
        handle = self._handles[slide_id]
        h5_index = int(row["h5_idx"])
        local = self.local_transform(handle["local"][h5_index])
        context = self.context_transform(handle["global"][h5_index])
        image_pair = torch.stack([local, context])

        sequence = self.tokenizer.encode(str(row["top_50_genes"]))[: self.max_len]
        sequence.extend([self.tokenizer.pad_id] * (self.max_len - len(sequence)))
        organ_id = self.tokenizer.organ_id(str(row["organ"]))
        species_id = self.tokenizer.species_id(str(row.get("species", "unknown")))
        return (
            image_pair,
            torch.tensor(sequence, dtype=torch.long),
            torch.tensor(organ_id, dtype=torch.long),
            torch.tensor(species_id, dtype=torch.long),
        )

    def __del__(self) -> None:
        for handle in self._handles.values():
            try:
                handle.close()
            except Exception:
                pass


FastEndToEndDataset = PairedHEDataset
