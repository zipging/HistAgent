"""Call the co-located model functions without a Space-to-Space HTTP hop."""

from __future__ import annotations

import io
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

import anyio
import gradio as gr
from fastapi import HTTPException, Request
from gradio.context import LocalContext
from PIL import Image, ImageOps, UnidentifiedImageError


class LocalBackend:
    """Serialize GPU work while preserving the actual visitor's HF identity.

    Models must already be loaded before constructing this object. The pinned
    spaces wrapper reads LocalContext.request before scheduling its GPU worker;
    a request argument alone would not establish that context.
    """

    _API_INPUTS = {
        "generate_ranked_readout": 5,
        "answer_atlas_question": 3,
        "retrieve_atlas": 5,
    }

    def __init__(
        self,
        demo: Any,
        vision: Any,
        reasoning: Any,
        *,
        gpu_dispatch: Callable[[str, list[Any]], Any],
    ) -> None:
        self._ready = False
        self._demo = demo
        self._vision = vision
        self._reasoning = reasoning
        self._gpu_dispatch = gpu_dispatch
        self._gpu_lock = threading.Lock()
        self._limiter = anyio.CapacityLimiter(1)
        self._handlers = {
            "generate_ranked_readout": vision.generate_ranked_readout,
            "answer_atlas_question": reasoning.answer_atlas_question,
            "retrieve_atlas": reasoning.retrieve_atlas,
        }
        if not callable(gpu_dispatch) or not all(callable(fn) for fn in self._handlers.values()):
            raise RuntimeError("The local model functions are not initialized.")
        self._ready = True

    async def health(self) -> dict[str, Any]:
        return {
            "status": "reachable" if self._ready else "starting",
            "transport": "in_process",
        }

    async def upload_images(
        self, files: list[tuple[str, bytes, str]]
    ) -> list[dict[str, Any]]:
        def validate_images() -> None:
            for _, content, _ in files:
                try:
                    with Image.open(io.BytesIO(content)) as image:
                        image.load()
                except (UnidentifiedImageError, OSError, ValueError) as error:
                    raise HTTPException(
                        status_code=422,
                        detail="The local and contextual images must contain valid image data.",
                    ) from error

        # Validate before the gateway reserves GPU budget. Loading pixels also
        # rejects truncated files whose headers alone identify a valid image.
        await anyio.to_thread.run_sync(validate_images)
        # Keep images in the request's data, never in a global upload registry.
        # Preprocessing still happens in the worker immediately before use.
        return [
            {
                "_histagent_image_bytes": content,
                "orig_name": Path(name).name,
                "mime_type": mime_type,
            }
            for name, content, mime_type in files
        ]

    @staticmethod
    def _write_image(value: Any, destination: Path) -> str:
        if not isinstance(value, dict) or not isinstance(
            value.get("_histagent_image_bytes"), bytes
        ):
            raise ValueError("A local and contextual H&E image are required.")
        with Image.open(io.BytesIO(value["_histagent_image_bytes"])) as image:
            # Match Gradio Image's orientation and RGB preprocessing while
            # avoiding caller-selected filesystem paths.
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            try:
                normalized.save(destination, format="PNG")
            finally:
                normalized.close()
        return str(destination)

    def _invoke(
        self, api_name: str, data: list[Any], request: Request
    ) -> list[Any]:
        with self._gpu_lock:
            request_token = LocalContext.request.set(gr.Request(request=request))
            blocks_token = LocalContext.blocks.set(self._demo)
            try:
                if api_name == "generate_ranked_readout":
                    with tempfile.TemporaryDirectory(prefix="histagent-images-") as directory:
                        folder = Path(directory)
                        local_path = self._write_image(data[0], folder / "local.png")
                        context_path = self._write_image(data[1], folder / "context.png")
                        outputs = self._gpu_dispatch(
                            api_name, [local_path, context_path, *data[2:]]
                        )
                else:
                    outputs = self._gpu_dispatch(api_name, data)

                if not isinstance(outputs, (tuple, list)):
                    raise RuntimeError("The model returned an invalid result.")
                result = list(outputs)
                if api_name == "retrieve_atlas":
                    if len(result) != 7:
                        raise RuntimeError("The atlas model returned an invalid result.")
                    result[3] = {"type": "plotly", "plot": result[3].to_json()}
                elif len(result) != 2:
                    raise RuntimeError("The model returned an invalid result.")
                return result
            finally:
                LocalContext.blocks.reset(blocks_token)
                LocalContext.request.reset(request_token)

    async def call(
        self, api_name: str, data: list[Any], request: Request,
        *, on_admitted: Callable[[], Any] | None = None,
    ) -> list[Any]:
        if not self._ready:
            raise RuntimeError("The local model service is still starting.")
        if api_name not in self._handlers:
            raise ValueError("Unsupported local model endpoint.")
        if not isinstance(data, list) or len(data) != self._API_INPUTS[api_name]:
            raise ValueError("Invalid local model inputs.")
        if not isinstance(request, Request):
            raise ValueError("A real incoming HTTP request is required.")

        # Await admission before occupying a thread. AnyIO waits for an already
        # running worker on cancellation, so temporary images and GPU ownership
        # are not released while the model is still using them.
        async with self._limiter:
            # A canceled waiter never submitted GPU work. Set the gateway's
            # context in this task, immediately before starting its worker.
            if on_admitted is not None:
                on_admitted()
            return await anyio.to_thread.run_sync(self._invoke, api_name, data, request)
