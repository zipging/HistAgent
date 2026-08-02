#!/usr/bin/env python3
"""Small OpenAI-compatible server for the HistAgent Qwen3-8B adapter."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from peft import PeftModel
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer


BASE_MODEL_PATH = os.getenv("HISTAGENT_QWEN_BASE", "Qwen/Qwen3-8B")
ADAPTER_PATH = os.getenv(
    "HISTAGENT_QWEN_ADAPTER",
    "wli14/HistAgent-Qwen3-8B-LoRA",
)
SERVED_MODEL = os.getenv("HISTAGENT_MODEL", "HistAgent-Qwen3-8B-LoRA")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8001"))

tokenizer = None
model = None
generation_lock = threading.Lock()
loaded_at = None


class ChatRequest(BaseModel):
    model: str = SERVED_MODEL
    messages: list[dict[str, Any]]
    temperature: float = 0.2
    max_tokens: int = Field(default=768, ge=1, le=4096)
    stream: bool = False
    chat_template_kwargs: dict[str, Any] = Field(default_factory=dict)


def load_model() -> None:
    global tokenizer, model, loaded_at
    tokenizer = AutoTokenizer.from_pretrained(ADAPTER_PATH)
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL_PATH,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        low_cpu_mem_usage=True,
        attn_implementation="sdpa",
    )
    model = PeftModel.from_pretrained(base_model, ADAPTER_PATH)
    model.eval()
    loaded_at = time.time()


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_model()
    yield


app = FastAPI(title="HistAgent Qwen API", lifespan=lifespan)


def prepare_generation(req: ChatRequest):
    if tokenizer is None or model is None:
        raise HTTPException(status_code=503, detail="Model is still loading")
    thinking = bool(req.chat_template_kwargs.get("enable_thinking", False))
    input_ids = tokenizer.apply_chat_template(
        req.messages,
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt",
        enable_thinking=thinking,
    ).to(model.device)
    do_sample = req.temperature > 0
    kwargs = {
        "input_ids": input_ids,
        "max_new_tokens": req.max_tokens,
        "do_sample": do_sample,
        "pad_token_id": tokenizer.eos_token_id,
    }
    if do_sample:
        kwargs.update(
            {
                "temperature": max(req.temperature, 1e-4),
                "top_p": 0.9,
            }
        )
    return kwargs


def generate_text(req: ChatRequest) -> str:
    kwargs = prepare_generation(req)
    with generation_lock, torch.inference_mode():
        output = model.generate(**kwargs)
    new_tokens = output[0, kwargs["input_ids"].shape[-1] :]
    return tokenizer.decode(new_tokens, skip_special_tokens=True)


def stream_text(req: ChatRequest):
    kwargs = prepare_generation(req)
    streamer = TextIteratorStreamer(
        tokenizer,
        skip_prompt=True,
        skip_special_tokens=True,
        timeout=300,
    )
    kwargs["streamer"] = streamer

    def worker():
        with generation_lock, torch.inference_mode():
            model.generate(**kwargs)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    for text in streamer:
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": SERVED_MODEL,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": text},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": SERVED_MODEL,
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": "stop"}
        ],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/health")
def health():
    return {
        "status": "ok" if model is not None else "loading",
        "model": SERVED_MODEL,
        "base_model": BASE_MODEL_PATH,
        "adapter": ADAPTER_PATH,
        "loaded_at": loaded_at,
        "cuda": torch.cuda.is_available(),
    }


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [{"id": SERVED_MODEL, "object": "model", "owned_by": "local"}],
    }


@app.post("/v1/chat/completions")
def chat(req: ChatRequest):
    if req.stream:
        return StreamingResponse(stream_text(req), media_type="text/event-stream")
    content = generate_text(req)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": SERVED_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
