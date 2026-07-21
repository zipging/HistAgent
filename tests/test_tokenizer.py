from pathlib import Path

from histagent.tokenizer import GeneTokenizer


ROOT = Path(__file__).parents[1]


def test_release_vocabulary_matches_checkpoint_shape() -> None:
    tokenizer = GeneTokenizer(
        ROOT / "vocab/gene_vocab.txt",
        ROOT / "vocab/organ_vocab.json",
        ROOT / "vocab/species_vocab.json",
    )
    assert tokenizer.vocab_size == 44_542
    assert len(tokenizer.organ_to_id) == 32
    assert tokenizer.species_to_id == {"unknown": 0, "human": 1, "mouse": 2}


def test_gene_round_trip() -> None:
    tokenizer = GeneTokenizer(
        ROOT / "vocab/gene_vocab.txt",
        ROOT / "vocab/organ_vocab.json",
        ROOT / "vocab/species_vocab.json",
    )
    genes = [tokenizer.id_to_token[4], tokenizer.id_to_token[5]]
    assert tokenizer.decode(tokenizer.encode(genes)) == genes
