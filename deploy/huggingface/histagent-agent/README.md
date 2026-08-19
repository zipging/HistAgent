---
title: HistAgent Chat
emoji: 🧬
colorFrom: indigo
colorTo: red
sdk: gradio
sdk_version: 5.44.1
app_file: app.py
pinned: false
license: other
models:
  - Qwen/Qwen3-8B
  - wli14/HistAgent-Qwen3-8B-LoRA
  - Qwen/Qwen3-Embedding-8B
datasets:
  - wli13/HistAgent-data
  - wli14/HistAgent-atlas-images
tags:
  - histopathology
  - spatial-transcriptomics
  - biology
  - chat
  - retrieval
---

# HistAgent Chat

HistAgent Chat answers questions about selected tissue spots using ranked genes,
cell-state estimates, functional programs and spatial context contained in a
structured evidence card.

The online demonstration uses the HistAgent LoRA adapter with Qwen3-8B and the
HistAgent evidence-card schema. The interface therefore describes the
language-model component as **adapted Qwen3-8B**. Thinking mode is disabled,
and the interface does not expose chain-of-thought. It is intended for research use.

The Atlas Explorer searches a balanced public index of 100,000 measured ST
spots using 4,096-dimensional Qwen3-Embedding-8B vectors and cosine similarity.
It presents retrieved spots in tissue space, displays ranked evidence cards and
supports follow-up questions about the top-ranked measured evidence. The
demonstration index and checksum manifest are published in the
[HistAgent evidence-bank release](https://github.com/zipging/HistAgent/releases/tag/public-evidence-bank-v1).
The manuscript analyses use the complete 2.23-million-spot evidence bank.
