"""Single Gradio ZeroGPU process for the HistAgent public API and models."""

from __future__ import annotations

import os
import time
from typing import Any

# Select a durable model credential before importing Hub/Transformers clients.
# An explicitly separate model token takes precedence over the service token.
_service_token = os.environ.get("WLI14_HF_TOKEN") or os.environ.get("HF_TOKEN")
if not _service_token:
    raise RuntimeError("A service credential for the private usage ledger is required.")
# Preserve HF_TOKEN's original ledger fallback when a distinct model token is
# configured. Both selections happen once, before any client is constructed.
os.environ.setdefault("WLI14_HF_TOKEN", _service_token)
_model_token = (
    os.environ.get("HISTAGENT_MODEL_TOKEN")
    or _service_token
)
if _model_token:
    os.environ["HF_TOKEN"] = _model_token

# ZeroGPU must patch CUDA before any model or torch import.
import spaces  # noqa: F401, E402

import gradio as gr  # noqa: E402
from huggingface_hub import hf_hub_download  # noqa: E402

import gateway  # noqa: E402
import reasoning_backend as reasoning  # noqa: E402
import vision_backend as vision  # noqa: E402
from local_backend import LocalBackend  # noqa: E402


def _ignore_progress(*_args: Any, **_kwargs: Any) -> None:
    """No UI progress events are emitted from the guarded JSON API."""


@spaces.GPU(duration=60)
def gpu_dispatch(api_name: str, data: list[Any]) -> Any:
    """One GPU worker serves all models packed into this process at startup."""
    started = time.monotonic()
    try:
        if api_name == "generate_ranked_readout":
            return vision.generate_ranked_readout(*data)
        if api_name == "answer_atlas_question":
            return reasoning.answer_atlas_question(*data)
        if api_name == "retrieve_atlas":
            return reasoning.retrieve_atlas(*data, progress=_ignore_progress)
        raise ValueError("Unsupported local model endpoint.")
    finally:
        # Operational metrics only; no image, question, answer or identity data.
        print(f"GPU task {api_name}: {time.monotonic() - started:.2f}s", flush=True)


def _require_cuda_model(name: str, model: object) -> None:
    """Fail startup if automatic placement silently offloaded a model to CPU."""
    device = getattr(model, "device", None)
    if device is not None and getattr(device, "type", str(device).split(":")[0]) != "cuda":
        raise RuntimeError(f"{name} must be placed on CUDA before the server starts.")
    device_map = getattr(model, "hf_device_map", None)
    if isinstance(device_map, dict) and any(
        str(device).lower() in {"cpu", "disk", "meta"}
        for device in device_map.values()
    ):
        raise RuntimeError(f"{name} was offloaded from CUDA during startup.")
    parameters = getattr(model, "parameters", None)
    if callable(parameters) and any(parameter.device.type != "cuda" for parameter in parameters()):
        raise RuntimeError(f"{name} has parameters outside CUDA during startup.")


def main() -> None:
    # Eager loading lets spaces pack CUDA allocations during Blocks.launch.
    # A model/data loading failure prevents the public API from claiming ready.
    vision_token = os.environ.get("HISTAGENT_VISION_TOKEN")
    if vision_token and not os.environ.get("HISTAGENT_BASE_CHECKPOINT"):
        os.environ["HISTAGENT_BASE_CHECKPOINT"] = hf_hub_download(
            repo_id="prov-gigapath/prov-gigapath",
            filename="pytorch_model.bin",
            token=vision_token,
        )
    print("Loading HistAgent visual model", flush=True)
    vision_model, _, _ = vision._load_histagent()
    _require_cuda_model("HistAgent", vision_model)
    print("Loading adapted Qwen reasoning model", flush=True)
    _, reasoning_model = reasoning._load_qwen()
    _require_cuda_model("Qwen", reasoning_model)
    print("Loading Atlas embedding model", flush=True)
    _, embedding_model = reasoning._load_embedder()
    _require_cuda_model("Atlas embedding model", embedding_model)
    print("Loading Atlas evidence index", flush=True)
    reasoning._load_atlas_index()
    if not reasoning._coordinate_aligned_tissue_slides():
        raise RuntimeError("The atlas has no coordinate-aligned tissue slides.")
    print("All three models and the Atlas index are loaded", flush=True)

    with gr.Blocks(title="HistAgent service") as demo:
        gr.Markdown("HistAgent service · [Open HistAgent](https://histagent.bio)")

    backend = LocalBackend(demo, vision, reasoning, gpu_dispatch=gpu_dispatch)
    gateway.configure_local_backend(backend)
    api_routes = [
        route
        for route in gateway.app.routes
        if getattr(route, "path", "").startswith("/api/")
    ]
    if not api_routes:
        raise RuntimeError("The HistAgent public API routes are missing.")

    # Keep the real launch call: spaces hooks it for torch.pack and its startup
    # report. app_kwargs places our guarded routes in this same FastAPI server.
    # There are no Gradio GPU listeners that bypass gateway admission controls.
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        ssr_mode=False,
        show_api=False,
        app_kwargs={
            "routes": api_routes,
            "middleware": gateway.app.user_middleware,
        },
    )


if __name__ == "__main__":
    main()
