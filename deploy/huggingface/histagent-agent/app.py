from __future__ import annotations

import html
import json
import os
import shutil
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

import gradio as gr
import numpy as np
import plotly.graph_objects as go
import spaces
import torch
import torch.nn.functional as F
from huggingface_hub import snapshot_download
from peft import PeftModel
from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer


DATA_REPO = "wli13/HistAgent-data"
QWEN_REPO = "Qwen/Qwen3-8B"
QWEN_ADAPTER_REPO = "wli14/HistAgent-Qwen3-8B-LoRA"
EMBEDDING_REPO = "Qwen/Qwen3-Embedding-8B"
DISPLAY_MODEL = "adapted Qwen3-8B"
MODEL_LOCK = threading.Lock()
EMBEDDING_LOCK = threading.Lock()
ATLAS_EMBEDDINGS_FILE = "atlas_demo/evidence_bank_embeddings_fp16.npy"
ATLAS_METADATA_FILE = "atlas_demo/evidence_bank_metadata.jsonl"
ATLAS_RELEASE_BASE = (
    "https://github.com/zipging/HistAgent/releases/download/"
    "public-evidence-bank-v1"
)
ATLAS_TISSUE_REPO = "wli14/HistAgent-atlas-images"
ATLAS_TISSUE_BASE = (
    "https://huggingface.co/datasets/"
    f"{ATLAS_TISSUE_REPO}/resolve/main"
)

SYSTEM_POLICY = """You are HistAgent, an assistant for evidence-grounded analysis of
histology-derived molecular and spatial evidence. Answer identity and capability questions
briefly and directly. For questions about the selected tissue spot, use only the supplied
evidence card and explain how the available genes, cell states, functional programs and
spatial context support the answer. Do not fabricate evidence that is absent from the card.
State uncertainty when the evidence is limited or ambiguous. Answer in the same language as
the user. Do not expose chain-of-thought, hidden reasoning or <think> tags."""


def _compact_evidence(row: dict[str, Any]) -> dict[str, Any]:
    input_evidence = row.get("input_evidence") or {}
    structured = row.get("structured_result") or {}
    spot = input_evidence.get("spot") or {}
    spatial = spot.get("spatial_context") or {}
    return {
        "spot": {
            "slice_id": row.get("slice_id"),
            "barcode": row.get("barcode"),
            "species": row.get("species"),
            "organ": row.get("organ"),
        },
        "ranked_genes": (input_evidence.get("inputs") or {}).get("top_genes", [])[:50],
        "cell_type_composition": structured.get("cell_type_composition", [])[:6],
        "pathway_evidence": structured.get("pathway_evidence", {}),
        "spatial_context": {
            "available": spatial.get("available", False),
            "n_neighbors": spatial.get("n_neighbors"),
            "neighborhood_consensus": spatial.get("neighborhood_consensus", {}),
            "boundary_label_discordance": spatial.get("boundary_label_discordance"),
            "boundary_entropy": spatial.get("boundary_entropy"),
            "local_dominance": spatial.get("local_dominance"),
            "neighbors": spatial.get("neighbors", [])[:6],
        },
        "quality_flags": input_evidence.get("quality_flags", {}),
    }


@lru_cache(maxsize=1)
def _load_spots() -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]]]:
    data_root = Path(
        snapshot_download(
            repo_id=DATA_REPO,
            repo_type="dataset",
            allow_patterns=["chat/spots.jsonl"],
        )
    )
    records: dict[str, dict[str, Any]] = {}
    choices: list[tuple[str, str]] = []
    with (data_root / "chat" / "spots.jsonl").open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            spot_key = str(row.get("spot_key") or "").strip()
            if not spot_key:
                continue
            records[spot_key] = _compact_evidence(row)
            label = " | ".join(
                [
                    str(row.get("organ") or "unknown"),
                    str(row.get("slice_id") or "unknown"),
                    str(row.get("barcode") or "unknown"),
                ]
            )
            choices.append((label, spot_key))
    choices.sort(key=lambda item: item[0].lower())
    return records, choices


SPOT_RECORDS, SPOT_CHOICES = _load_spots()
DEFAULT_SPOT = next(
    (
        key
        for label, key in SPOT_CHOICES
        if "GSE175540_GSM5924036" in label
    ),
    SPOT_CHOICES[0][1] if SPOT_CHOICES else None,
)


@lru_cache(maxsize=1)
def _load_atlas_index() -> tuple[np.ndarray, list[dict[str, Any]]]:
    data_root = Path(
        os.environ.get(
            "HISTAGENT_ATLAS_CACHE",
            Path.home() / ".cache" / "histagent" / "atlas_demo",
        )
    )
    data_root.mkdir(parents=True, exist_ok=True)
    for relative_path in (ATLAS_EMBEDDINGS_FILE, ATLAS_METADATA_FILE):
        destination = data_root / Path(relative_path).name
        if destination.exists():
            continue
        temporary = destination.with_suffix(destination.suffix + ".part")
        request = Request(
            f"{ATLAS_RELEASE_BASE}/{destination.name}",
            headers={"User-Agent": "HistAgent-Atlas-Explorer/1.0"},
        )
        with urlopen(request, timeout=120) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=8 * 1024 * 1024)
        temporary.replace(destination)
    embeddings = np.load(
        data_root / Path(ATLAS_EMBEDDINGS_FILE).name,
        mmap_mode="r",
    )
    if embeddings.ndim != 2 or embeddings.shape[1] != 4096:
        raise RuntimeError(f"Unexpected public evidence-bank shape: {embeddings.shape}")
    metadata: list[dict[str, Any]] = []
    with (data_root / Path(ATLAS_METADATA_FILE).name).open() as handle:
        for line in handle:
            if line.strip():
                metadata.append(json.loads(line))
    if len(metadata) != embeddings.shape[0]:
        raise RuntimeError(
            f"Evidence-bank rows do not match: {embeddings.shape[0]} embeddings "
            f"and {len(metadata)} metadata records"
        )
    return embeddings, metadata


def _load_tissue_manifest() -> dict[str, dict[str, Any]]:
    root = Path(
        snapshot_download(
            repo_id=ATLAS_TISSUE_REPO,
            repo_type="dataset",
            allow_patterns=["manifest.json"],
        )
    )
    payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return dict(payload.get("slides") or {})


def _tissue_image(slide_id: str) -> dict[str, Any] | None:
    record = _load_tissue_manifest().get(slide_id)
    if not record:
        return None
    return {
        **record,
        "url": f"{ATLAS_TISSUE_BASE}/{quote(str(record['file']), safe='/')}",
    }


def _ready_tissue_slides() -> list[str]:
    return sorted(
        slide_id
        for slide_id, record in _load_tissue_manifest().items()
        if record.get("source") != "sampled_contextual_h_and_e_patches"
    )


@lru_cache(maxsize=1)
def _load_qwen() -> tuple[Any, Any]:
    # The LoRA repository contains adapter weights, not a separate tokenizer.
    # Loading the canonical base tokenizer also avoids version-specific parsing
    # of tokenizer metadata saved by the fine-tuning environment.
    tokenizer = AutoTokenizer.from_pretrained(QWEN_REPO)
    base_model = AutoModelForCausalLM.from_pretrained(
        QWEN_REPO,
        dtype=torch.bfloat16,
        device_map="auto",
        low_cpu_mem_usage=True,
        attn_implementation="sdpa",
    )
    model = PeftModel.from_pretrained(
        base_model,
        QWEN_ADAPTER_REPO,
        is_trainable=False,
    )
    model.eval()
    return tokenizer, model


@lru_cache(maxsize=1)
def _load_embedder() -> tuple[Any, Any]:
    tokenizer = AutoTokenizer.from_pretrained(
        EMBEDDING_REPO,
        padding_side="left",
    )
    model = AutoModel.from_pretrained(
        EMBEDDING_REPO,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        low_cpu_mem_usage=True,
        attn_implementation="sdpa",
    )
    model.eval()
    return tokenizer, model


def _last_token_pool(
    last_hidden_states: torch.Tensor,
    attention_mask: torch.Tensor,
) -> torch.Tensor:
    if bool(attention_mask[:, -1].sum() == attention_mask.shape[0]):
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    batch_size = last_hidden_states.shape[0]
    return last_hidden_states[
        torch.arange(batch_size, device=last_hidden_states.device),
        sequence_lengths,
    ]


def _history_messages(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in (history or [])[-8:]:
        role = str(item.get("role") or "")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})
    return messages


def _answer_from_evidence(
    message: str,
    history: list[dict[str, Any]] | None,
    evidence: dict[str, Any],
) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_POLICY},
        {
            "role": "system",
            "content": "Selected-spot evidence card:\n"
            + json.dumps(evidence, ensure_ascii=False),
        },
        *_history_messages(history),
        {"role": "user", "content": message.strip()},
    ]

    tokenizer, model = _load_qwen()
    template_args = {
        "conversation": messages,
        "tokenize": True,
        "add_generation_prompt": True,
        "return_tensors": "pt",
    }
    try:
        input_ids = tokenizer.apply_chat_template(
            **template_args,
            enable_thinking=False,
        )
    except TypeError:
        input_ids = tokenizer.apply_chat_template(**template_args)
    input_ids = input_ids.to(model.device)

    with MODEL_LOCK, torch.inference_mode():
        output = model.generate(
            input_ids=input_ids,
            max_new_tokens=256,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    new_tokens = output[0, input_ids.shape[-1] :]
    return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


@spaces.GPU(duration=60)
def answer_question(
    message: str,
    history: list[dict[str, Any]] | None,
    spot_key: str | None,
) -> str:
    evidence = SPOT_RECORDS.get(str(spot_key or ""))
    if evidence is None:
        return "Please select a reference spot before asking a spot-specific question."
    return _answer_from_evidence(message, history, evidence)


@spaces.GPU(duration=60)
def answer_atlas_question(
    message: str,
    history: list[dict[str, Any]] | None,
    evidence: dict[str, Any] | None,
) -> tuple[str, list[dict[str, str]]]:
    message = str(message or "").strip()
    conversation = list(history or [])
    if not message:
        return "", conversation
    if not evidence:
        answer = "Run an atlas search before asking about the retrieved evidence."
    else:
        answer = _answer_from_evidence(message, conversation, evidence)
    conversation.extend(
        [
            {"role": "user", "content": message},
            {"role": "assistant", "content": answer},
        ]
    )
    return "", conversation


def show_evidence(spot_key: str | None) -> dict[str, Any]:
    return SPOT_RECORDS.get(str(spot_key or ""), {})


def _search_embeddings(
    embeddings: np.ndarray,
    query_embedding: np.ndarray,
    candidate_indices: np.ndarray,
    top_k: int,
) -> tuple[np.ndarray, np.ndarray]:
    if candidate_indices.size == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32)
    best_indices = np.empty(0, dtype=np.int64)
    best_scores = np.empty(0, dtype=np.float32)
    chunk_size = 8192
    for start in range(0, candidate_indices.size, chunk_size):
        indices = candidate_indices[start : start + chunk_size]
        matrix = np.asarray(embeddings[indices], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix /= np.maximum(norms, 1e-12)
        scores = matrix @ query_embedding
        combined_indices = np.concatenate([best_indices, indices])
        combined_scores = np.concatenate([best_scores, scores.astype(np.float32)])
        keep = min(int(top_k), combined_scores.size)
        if keep == combined_scores.size:
            selected = np.arange(combined_scores.size)
        else:
            selected = np.argpartition(combined_scores, -keep)[-keep:]
        order = selected[np.argsort(combined_scores[selected])[::-1]]
        best_indices = combined_indices[order]
        best_scores = combined_scores[order]
    return best_indices, best_scores


def _empty_atlas_figure() -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        height=480,
        margin=dict(l=20, r=20, t=48, b=20),
        paper_bgcolor="#edf2ef",
        plot_bgcolor="#edf2ef",
        title=dict(
            text="Retrieved spots will be shown in tissue space",
            font=dict(size=16, color="#526b63"),
            x=0.5,
        ),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        annotations=[
            dict(
                text="Search the evidence bank to view the top-ranked region.",
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(size=14, color="#7a918a"),
            )
        ],
    )
    return figure


def _atlas_tissue_map(
    metadata: list[dict[str, Any]],
    indices: np.ndarray,
    scores: np.ndarray,
) -> go.Figure:
    if not len(indices):
        return _empty_atlas_figure()
    top_record = metadata[int(indices[0])]
    slide_id = str(top_record.get("slice_id") or "unknown slide")
    tissue_image = _tissue_image(slide_id)
    slide_rows = [
        record
        for record in metadata
        if str(record.get("slice_id") or "") == slide_id
        and record.get("x") is not None
        and record.get("y") is not None
    ]
    retrieved = []
    for rank, (index, score) in enumerate(zip(indices, scores), start=1):
        record = metadata[int(index)]
        if (
            str(record.get("slice_id") or "") == slide_id
            and record.get("x") is not None
            and record.get("y") is not None
        ):
            retrieved.append((rank, float(score), record))

    figure = go.Figure()
    if tissue_image:
        width = float(tissue_image["coordinate_width"])
        height = float(tissue_image["coordinate_height"])
        figure.add_layout_image(
            dict(
                source=tissue_image["url"],
                xref="x",
                yref="y",
                x=0,
                y=0,
                sizex=width,
                sizey=height,
                xanchor="left",
                yanchor="top",
                sizing="stretch",
                opacity=0.94,
                layer="below",
            )
        )
    if slide_rows:
        figure.add_trace(
            go.Scattergl(
                x=[float(record["x"]) for record in slide_rows],
                y=[float(record["y"]) for record in slide_rows],
                mode="markers",
                marker=dict(
                    size=6,
                    color="#b9c7c2" if not tissue_image else "rgba(244,248,246,0.38)",
                    opacity=0.72,
                    line=dict(width=0.5, color="rgba(255,255,255,0.65)"),
                ),
                hovertext=[
                    f"{html.escape(str(record.get('dominant_cell_type') or 'Unassigned'))}"
                    for record in slide_rows
                ],
                hovertemplate="%{hovertext}<extra>Other indexed spots</extra>",
                name="Other indexed spots",
            )
        )
    if retrieved:
        figure.add_trace(
            go.Scatter(
                x=[float(record["x"]) for _, _, record in retrieved],
                y=[float(record["y"]) for _, _, record in retrieved],
                mode="markers+text",
                text=[str(rank) for rank, _, _ in retrieved],
                textposition="top center",
                textfont=dict(size=10, color="#233c35"),
                marker=dict(
                    size=[20 if rank == 1 else 17 for rank, _, _ in retrieved],
                    symbol="circle-open",
                    color=["#df7b57" if rank == 1 else "#176f63" for rank, _, _ in retrieved],
                    line=dict(width=3),
                ),
                hovertext=[
                    (
                        f"Rank {rank}<br>Similarity {score:.3f}<br>"
                        f"{html.escape(str(record.get('dominant_cell_type') or 'Unassigned'))}"
                    )
                    for rank, score, record in retrieved
                ],
                hovertemplate="%{hovertext}<extra>Retrieved spot</extra>",
                name="Retrieved spots",
            )
        )
    figure.update_layout(
        height=480,
        margin=dict(l=20, r=20, t=58, b=22),
        paper_bgcolor="#edf2ef",
        plot_bgcolor="#edf2ef",
        title=dict(
            text=f"Top-ranked slide · {html.escape(slide_id)}",
            font=dict(size=16, color="#233c35"),
            x=0.02,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="right",
            x=1,
            font=dict(size=11),
        ),
        xaxis=dict(
            visible=False,
            scaleanchor="y",
            scaleratio=1,
            range=[0, float(tissue_image["coordinate_width"])] if tissue_image else None,
        ),
        yaxis=dict(
            visible=False,
            range=[float(tissue_image["coordinate_height"]), 0] if tissue_image else None,
            autorange=False if tissue_image else "reversed",
        ),
        dragmode="pan",
        hoverlabel=dict(bgcolor="white", font_size=12),
        annotations=(
            []
            if tissue_image
            else [
                dict(
                    text="H&E preview is not available for this source slide",
                    x=0.5,
                    y=0.02,
                    xref="paper",
                    yref="paper",
                    showarrow=False,
                    bgcolor="rgba(255,255,255,0.88)",
                    borderpad=5,
                    font=dict(size=11, color="#687f77"),
                )
            ]
        ),
    )
    return figure


def _ranked_evidence_cards(
    metadata: list[dict[str, Any]],
    indices: np.ndarray,
    scores: np.ndarray,
) -> str:
    if not len(indices):
        return (
            '<div class="evidence-placeholder">'
            "Ranked evidence cards will appear after retrieval."
            "</div>"
        )
    cards = []
    for rank, (index, score) in enumerate(zip(indices[:3], scores[:3]), start=1):
        record = metadata[int(index)]
        genes = ", ".join(
            html.escape(str(gene)) for gene in list(record.get("top_genes") or [])[:6]
        )
        pathways = list(record.get("reactome_pathways") or [])[:2]
        pathway_text = ", ".join(
            html.escape(str(item.get("pathway") or ""))
            for item in pathways
            if item.get("pathway")
        )
        cards.append(
            f"""
            <article class="evidence-card {'top-card' if rank == 1 else ''}">
              <div class="card-rank"><span>{rank}</span><strong>{float(score):.3f}</strong></div>
              <h3>{html.escape(str(record.get("dominant_cell_type") or "Spatial molecular state"))}</h3>
              <p class="card-source">{html.escape(str(record.get("organ") or "Unknown tissue"))}
                · {html.escape(str(record.get("slice_id") or "Unknown slide"))}</p>
              <dl>
                <div><dt>Top-ranked genes</dt><dd>{genes or "Not available"}</dd></div>
                <div><dt>Functional programs</dt><dd>{pathway_text or "Not available"}</dd></div>
              </dl>
            </article>
            """
        )
    return '<div class="evidence-card-list">' + "".join(cards) + "</div>"


@spaces.GPU(duration=120)
def retrieve_atlas(
    query: str,
    species: str,
    organ: str,
    slide_id: str = "__ready__",
    top_k: int = 5,
    progress=gr.Progress(),
) -> tuple[
    list[list[Any]],
    dict[str, Any],
    str,
    go.Figure,
    str,
    dict[str, Any],
    list[dict[str, str]],
]:
    query = str(query or "").strip()
    if not query:
        return (
            [],
            {},
            "Enter a biological description before searching.",
            _empty_atlas_figure(),
            _ranked_evidence_cards([], np.empty(0), np.empty(0)),
            {},
            [],
        )

    progress(0.05, desc="Loading the public evidence-bank index")
    embeddings, metadata = _load_atlas_index()
    candidate_indices = np.arange(len(metadata), dtype=np.int64)
    normalized_species = str(species or "").strip().lower()
    normalized_organ = str(organ or "").strip().lower()
    normalized_slide = str(slide_id or "").strip()
    # Older cached clients sent top_k as the fourth positional argument. Keep
    # those requests on the image-ready collection instead of interpreting the
    # number as a slide identifier.
    if isinstance(slide_id, (int, float)) or normalized_slide.isdigit():
        top_k = int(slide_id)
        normalized_slide = "__ready__"
    if normalized_species and normalized_species != "any":
        candidate_indices = np.asarray(
            [
                index
                for index in candidate_indices
                if str(metadata[int(index)].get("species") or "").lower()
                == normalized_species
            ],
            dtype=np.int64,
        )
    if normalized_organ and normalized_organ != "any":
        candidate_indices = np.asarray(
            [
                index
                for index in candidate_indices
                if str(metadata[int(index)].get("organ") or "").lower()
                == normalized_organ
            ],
            dtype=np.int64,
        )
    if normalized_slide.lower() in {
        "",
        "any",
        "__ready__",
        "all indexed slides",
        "all image-ready slides",
    }:
        ready_slides = set(_ready_tissue_slides())
        candidate_indices = np.asarray(
            [
                index
                for index in candidate_indices
                if str(metadata[int(index)].get("slice_id") or "") in ready_slides
            ],
            dtype=np.int64,
        )
    else:
        candidate_indices = np.asarray(
            [
                index
                for index in candidate_indices
                if str(metadata[int(index)].get("slice_id") or "") == normalized_slide
            ],
            dtype=np.int64,
        )
    if candidate_indices.size == 0:
        return (
            [],
            {},
            "No public-demo spots match the selected filters.",
            _empty_atlas_figure(),
            _ranked_evidence_cards([], np.empty(0), np.empty(0)),
            {},
            [],
        )

    progress(0.25, desc="Embedding the query")
    tokenizer, model = _load_embedder()
    batch = tokenizer(
        [query],
        padding=True,
        truncation=True,
        max_length=8192,
        return_tensors="pt",
    )
    batch = {key: value.to(model.device) for key, value in batch.items()}
    with EMBEDDING_LOCK, torch.inference_mode():
        outputs = model(**batch)
        pooled = _last_token_pool(outputs.last_hidden_state, batch["attention_mask"])
        pooled = F.normalize(pooled.float(), p=2, dim=1)
    query_embedding = pooled[0].cpu().numpy().astype(np.float32, copy=False)

    progress(0.75, desc="Searching measured ST evidence")
    indices, scores = _search_embeddings(
        embeddings,
        query_embedding,
        candidate_indices,
        int(top_k),
    )
    rows: list[list[Any]] = []
    for rank, (index, score) in enumerate(zip(indices, scores), start=1):
        record = metadata[int(index)]
        rows.append(
            [
                rank,
                round(float(score), 4),
                record.get("species"),
                record.get("organ"),
                record.get("dominant_cell_type"),
                record.get("slice_id"),
                ", ".join(list(record.get("top_genes") or [])[:8]),
            ]
        )
    top_evidence = metadata[int(indices[0])] if len(indices) else {}
    return (
        rows,
        top_evidence,
        f"Retrieved {len(rows)} measured ST spots from "
        f"{candidate_indices.size:,} candidates in the public demonstration index.",
        _atlas_tissue_map(metadata, indices, scores),
        _ranked_evidence_cards(metadata, indices, scores),
        top_evidence,
        [],
    )


CSS = """
.gradio-container {
  max-width: 1320px !important;
  color: #18312b;
}
.evidence-card-list {
  display: grid;
  gap: .65rem;
}
.evidence-card {
  background: #fff;
  border: 1px solid #d8e3df;
  border-radius: 14px;
  box-shadow: 0 8px 24px rgba(25, 63, 53, .055);
  padding: .85rem .9rem;
  position: relative;
}
.evidence-card.top-card {
  background: linear-gradient(135deg, #f6fbf9, #fff);
  border-color: #8fc2b6;
}
.card-rank {
  align-items: center;
  display: flex;
  gap: .45rem;
  position: absolute;
  right: .75rem;
  top: .75rem;
}
.card-rank span {
  align-items: center;
  background: #176f63;
  border-radius: 999px;
  color: #fff;
  display: inline-flex;
  font-size: .72rem;
  height: 1.45rem;
  justify-content: center;
  width: 1.45rem;
}
.card-rank strong {color: #176f63; font-size: .83rem;}
.evidence-card h3 {
  color: #203a33;
  font-size: .98rem;
  margin: 0 5.5rem .15rem 0;
}
.card-source {color: #789087; font-size: .76rem; margin: 0 0 .6rem;}
.evidence-card dl {display: grid; gap: .42rem; margin: 0;}
.evidence-card dl div {display: grid; grid-template-columns: 8.2rem 1fr; gap: .45rem;}
.evidence-card dt {color: #557068; font-size: .75rem; font-weight: 700;}
.evidence-card dd {color: #3b554e; font-size: .78rem; line-height: 1.35; margin: 0;}
.evidence-placeholder {
  align-items: center;
  background: #f5f8f7;
  border: 1px dashed #b9cbc5;
  border-radius: 14px;
  color: #758b84;
  display: flex;
  justify-content: center;
  min-height: 180px;
  padding: 1rem;
  text-align: center;
}
.histagent-note { color: var(--body-text-color-subdued); font-size: .92rem; }
@media (max-width: 760px) {
  .evidence-card dl div {grid-template-columns: 1fr;}
}
"""


with gr.Blocks(
    title="HistAgent Chat",
    theme=gr.themes.Soft(
        primary_hue="teal",
        secondary_hue="orange",
        neutral_hue="slate",
    ),
    css=CSS,
) as demo:
    with gr.Tab("Atlas Explorer"):
        # Use a hidden JSON component rather than gr.State so the public API
        # can receive the evidence card supplied by histagent.bio.
        atlas_selected_evidence = gr.JSON(value={}, visible=False)
        with gr.Row(equal_height=False):
            with gr.Column(scale=2, min_width=310):
                gr.Markdown("### Query setup")
                atlas_query = gr.Textbox(
                    value="tumor-adjacent tertiary lymphoid structure-like immune niches",
                    label="Natural-language query",
                    placeholder="Describe a tissue state, cell program or local microenvironment",
                    lines=3,
                )
                with gr.Row():
                    atlas_species = gr.Dropdown(
                        ["Any", "human", "mouse"],
                        value="human",
                        label="Species",
                    )
                    atlas_organ = gr.Textbox(
                        value="Any",
                        label="Organ",
                        placeholder="Any or an organ name",
                    )
                atlas_slide = gr.Dropdown(
                    choices=[("All image-ready slides", "__ready__")]
                    + [(slide_id, slide_id) for slide_id in _ready_tissue_slides()],
                    value="__ready__",
                    label="Atlas slide",
                )
                atlas_top_k = gr.Slider(
                    minimum=3,
                    maximum=10,
                    value=5,
                    step=1,
                    label="Number of retrieved spots",
                )
                atlas_submit = gr.Button(
                    "Search measured ST evidence",
                    variant="primary",
                    size="lg",
                )
                atlas_status = gr.Markdown(
                    "Submit the example query or enter your own biological description.",
                    elem_classes=["histagent-note"],
                )
                gr.Examples(
                    examples=[
                        [
                            "tumor-adjacent immune-stromal interface regions",
                            "human",
                            "Any",
                            "__ready__",
                            5,
                        ],
                        [
                            "heart spots with active muscle-contraction pathways",
                            "Any",
                            "heart",
                            "__ready__",
                            5,
                        ],
                        [
                            "myelination and oligodendrocyte programs",
                            "Any",
                            "brain",
                            "__ready__",
                            5,
                        ],
                    ],
                    inputs=[atlas_query, atlas_species, atlas_organ, atlas_slide, atlas_top_k],
                    cache_examples=False,
                    label="Queries from the manuscript workflow",
                )
            with gr.Column(scale=4, min_width=520):
                gr.Markdown("### Spatial tissue map")
                atlas_map = gr.Plot(
                    value=_empty_atlas_figure(),
                    show_label=False,
                )

        with gr.Row(equal_height=False):
            with gr.Column(scale=3, min_width=460):
                gr.Markdown("### Ranked evidence cards")
                atlas_cards = gr.HTML(
                    value=_ranked_evidence_cards(
                        [], np.empty(0), np.empty(0)
                    )
                )
            with gr.Column(scale=2, min_width=360):
                gr.Markdown("### Retrieved spots")
                atlas_results = gr.Dataframe(
                    headers=[
                        "Rank",
                        "Cosine similarity",
                        "Species",
                        "Organ",
                        "Dominant cell type",
                        "Slide",
                        "Top genes",
                    ],
                    datatype=[
                        "number",
                        "number",
                        "str",
                        "str",
                        "str",
                        "str",
                        "str",
                    ],
                    interactive=False,
                    wrap=True,
                )
                with gr.Accordion("Inspect the top evidence card", open=False):
                    atlas_evidence = gr.JSON(
                        label="Top retrieved evidence card",
                        open=False,
                    )

        gr.Markdown("### Retrieval-grounded follow-up analysis")
        gr.Markdown(
            "Ask about the top-ranked retrieved spot. Responses are constrained "
            "to its measured evidence card.",
            elem_classes=["histagent-note"],
        )
        atlas_chatbot = gr.Chatbot(
            label="Conversation about the top-ranked evidence",
            type="messages",
            height=360,
            show_copy_button=True,
        )
        with gr.Row():
            atlas_question = gr.Textbox(
                placeholder="What cell types and programs are enriched in this spot?",
                lines=2,
                scale=5,
                show_label=False,
            )
            atlas_ask = gr.Button("Analyze retrieved evidence", scale=1)

        atlas_submit.click(
            fn=retrieve_atlas,
            inputs=[atlas_query, atlas_species, atlas_organ, atlas_slide, atlas_top_k],
            outputs=[
                atlas_results,
                atlas_evidence,
                atlas_status,
                atlas_map,
                atlas_cards,
                atlas_selected_evidence,
                atlas_chatbot,
            ],
            api_name="retrieve_atlas",
        )
        atlas_ask.click(
            fn=answer_atlas_question,
            inputs=[atlas_question, atlas_chatbot, atlas_selected_evidence],
            outputs=[atlas_question, atlas_chatbot],
        )
        atlas_question.submit(
            fn=answer_atlas_question,
            inputs=[atlas_question, atlas_chatbot, atlas_selected_evidence],
            outputs=[atlas_question, atlas_chatbot],
        )

    with gr.Tab("Spot evidence chat"):
        with gr.Row():
            with gr.Column(scale=2, min_width=360):
                gr.Markdown(
                    f"Select one of **{len(SPOT_RECORDS):,} measured reference spots** "
                    "and inspect the evidence available to the model."
                )
                spot_selector = gr.Dropdown(
                    choices=SPOT_CHOICES,
                    value=DEFAULT_SPOT,
                    label="Reference spot",
                    filterable=True,
                )
                evidence_view = gr.JSON(
                    value=show_evidence(DEFAULT_SPOT),
                    label="Evidence card",
                    open=False,
                )
                gr.Markdown(
                    "The response is constrained to the selected evidence card. "
                    "Research use only.",
                    elem_classes=["histagent-note"],
                )
            with gr.Column(scale=3, min_width=480):
                chatbot = gr.Chatbot(
                    label="Conversation",
                    type="messages",
                    height=560,
                    show_copy_button=True,
                )
                gr.ChatInterface(
                    fn=answer_question,
                    chatbot=chatbot,
                    additional_inputs=[spot_selector],
                    textbox=gr.Textbox(
                        placeholder="Ask about ranked genes, cell composition, pathways or spatial context…",
                        lines=2,
                    ),
                    examples=[
                        ["What cell states are supported by this evidence?", DEFAULT_SPOT],
                        ["Which pathways are most strongly represented?", DEFAULT_SPOT],
                        ["这个 spot 的局部微环境有什么特征？", DEFAULT_SPOT],
                    ],
                    cache_examples=False,
                    type="messages",
                )

        spot_selector.change(
            fn=show_evidence,
            inputs=spot_selector,
            outputs=evidence_view,
            queue=False,
        )

    demo.load(
        fn=None,
        inputs=None,
        outputs=None,
        js="""
        () => {
          if (new URLSearchParams(window.location.search).get("view") !== "chat") {
            return;
          }
          let attempts = 0;
          const openChat = () => {
            const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
            const chat = tabs.find(
              (tab) => tab.textContent.trim() === "Spot evidence chat"
            );
            if (chat) {
              chat.click();
              const tabList = chat.closest('[role="tablist"]');
              if (tabList) tabList.style.display = "none";
              return;
            }
            attempts += 1;
            if (attempts < 40) window.setTimeout(openChat, 150);
          };
          openChat();
        }
        """,
    )

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=8).launch()
