---
base_model: prov-gigapath/prov-gigapath
library_name: peft
pipeline_tag: image-to-text
tags:
  - computational-pathology
  - spatial-transcriptomics
  - histology
  - gene-ranking
  - lora
---

# HistAgent-GigaPath

HistAgent-GigaPath is the visual-omics foundation model used by HistAgent to generate ranked molecular readouts from routine H&E images. It combines a spot-centred image with surrounding tissue context and autoregressively predicts the top 50 genes for each location.

## Model details

| Property | Value |
|---|---|
| Base encoder | `prov-gigapath/prov-gigapath` tile encoder |
| Vision adaptation | LoRA, rank 16, alpha 32 |
| Image inputs | Paired local and context H&E views |
| Decoder | Six-layer Transformer decoder, hidden size 512 |
| Output | Ranked sequence of up to 50 genes |
| Vocabulary | 44,538 genes plus four special tokens |
| Species conditioning | Human, mouse or unknown |
| Organ conditioning | 32-entry training vocabulary |

The repository contains only the trained LoRA parameters and HistAgent-specific modules. The frozen GigaPath base weights are loaded separately from the official gated repository.

## Training data

The model was trained on 2.23 million paired H&E–ST locations from 936 human and mouse 10x Visium slides spanning 32 tissue categories. The release checkpoint corresponds to epoch 30 of the GigaPath training run.

## Use

Install the accompanying code:

```bash
git clone https://github.com/zipging/HistAgent.git
cd HistAgent
pip install -e .
```

Request access to [Prov-GigaPath](https://huggingface.co/prov-gigapath/prov-gigapath), set `HF_TOKEN`, and load the model:

```python
from histagent import load_pretrained

model, tokenizer, config = load_pretrained("wli13/HistAgent-GigaPath", device="cuda")
```

See the [GitHub repository](https://github.com/zipging/HistAgent) for image preprocessing and ranked-gene inference examples.

## Intended use and limitations

This model is intended for research on computational pathology and spatial molecular biology. It predicts rank-based molecular readouts rather than transcript counts. Performance can vary with tissue type, staining, scanner characteristics, image resolution and preprocessing. Predictions require independent biological validation and are not intended for clinical diagnosis or treatment decisions.

## Citation

The HistAgent manuscript and citation will be added when publicly available.
