from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


class GeneTokenizer:
    special_tokens = ("[PAD]", "[BOS]", "[EOS]", "[UNK]")

    def __init__(
        self,
        vocab_file: str | Path,
        organ_vocab_file: str | Path,
        species_vocab_file: str | Path,
    ) -> None:
        with Path(vocab_file).open(encoding="utf-8") as handle:
            genes = [line.strip() for line in handle if line.strip()]
        tokens = [*self.special_tokens, *genes]
        self.token_to_id = {token: index for index, token in enumerate(tokens)}
        self.id_to_token = dict(enumerate(tokens))
        with Path(organ_vocab_file).open(encoding="utf-8") as handle:
            self.organ_to_id = json.load(handle)
        with Path(species_vocab_file).open(encoding="utf-8") as handle:
            self.species_to_id = json.load(handle)
        self.vocab_size = len(tokens)
        self.pad_id, self.bos_id, self.eos_id, self.unk_id = range(4)

    def encode(self, genes: str | Iterable[str], add_special_tokens: bool = True) -> list[int]:
        if isinstance(genes, str):
            genes = genes.split()
        ids = [self.token_to_id.get(gene, self.unk_id) for gene in genes]
        return [self.bos_id, *ids, self.eos_id] if add_special_tokens else ids

    def decode(self, ids: Iterable[int], stop_at_eos: bool = True) -> list[str]:
        genes: list[str] = []
        for token_id in ids:
            token_id = int(token_id)
            if stop_at_eos and token_id == self.eos_id:
                break
            if token_id in (self.pad_id, self.bos_id, self.eos_id):
                continue
            genes.append(self.id_to_token.get(token_id, "[UNK]"))
        return genes

    def organ_id(self, organ: str) -> int:
        return int(self.organ_to_id.get(organ, 0))

    def species_id(self, species: str) -> int:
        return int(self.species_to_id.get(species.lower(), 0))


SimpleGeneTokenizer = GeneTokenizer
