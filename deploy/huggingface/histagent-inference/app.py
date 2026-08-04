from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import gradio as gr
import spaces
import torch

from histagent.checkpoint import load_pretrained
from histagent.inference import predict_ranked_genes


MODEL_REPO = "wli13/HistAgent"
MODEL_LOCK = threading.Lock()


def _file_path(value: Any) -> str:
    if isinstance(value, (str, Path)):
        return str(value)
    if isinstance(value, dict):
        path = value.get("path") or value.get("name")
        if path:
            return str(path)
    path = getattr(value, "path", None) or getattr(value, "name", None)
    if path:
        return str(path)
    raise ValueError("A local and contextual H&E image are required.")


@lru_cache(maxsize=1)
def _load_histagent():
    if not torch.cuda.is_available():
        raise RuntimeError("A GPU worker is required for HistAgent inference.")
    model, tokenizer, config = load_pretrained(
        MODEL_REPO,
        token=os.environ.get("HF_TOKEN"),
        device="cuda",
    )
    return model, tokenizer, config


@spaces.GPU(duration=180)
def generate_ranked_readout(
    local_image: Any,
    context_image: Any,
    species: str,
    organ: str,
    top_k: int,
) -> tuple[list[list[Any]], str]:
    top_k = min(50, max(10, int(top_k or 50)))
    model, tokenizer, _ = _load_histagent()
    with MODEL_LOCK:
        genes = predict_ranked_genes(
            model,
            tokenizer,
            _file_path(local_image),
            _file_path(context_image),
            species=str(species or "unknown"),
            organ=str(organ or "Unknown"),
            top_k=top_k,
            device="cuda",
        )
    rows = [[rank, gene] for rank, gene in enumerate(genes, start=1)]
    return rows, " ".join(genes)


with gr.Blocks(title="HistAgent inference") as demo:
    gr.Markdown("HistAgent visual–omics inference service")
    with gr.Row():
        local_input = gr.Image(type="filepath", label="Local H&E")
        context_input = gr.Image(type="filepath", label="Context H&E")
    with gr.Row():
        species_input = gr.Textbox(value="human", label="Species")
        organ_input = gr.Textbox(value="Unknown", label="Organ")
        top_k_input = gr.Number(value=50, precision=0, label="Genes")
    run_button = gr.Button("Generate ranked molecular readout", variant="primary")
    ranked_output = gr.Dataframe(
        headers=["Rank", "Gene"],
        datatype=["number", "str"],
        interactive=False,
    )
    sentence_output = gr.Textbox(label="Ranked molecular readout")
    run_button.click(
        fn=generate_ranked_readout,
        inputs=[
            local_input,
            context_input,
            species_input,
            organ_input,
            top_k_input,
        ],
        outputs=[ranked_output, sentence_output],
        api_name="generate_ranked_readout",
    )


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch()
