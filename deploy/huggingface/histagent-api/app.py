from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import EntryNotFoundError, HfHubHTTPError
from pydantic import BaseModel, Field


HF_TOKEN = (
    os.environ.get("HF_TOKEN") or os.environ.get("WLI14_HF_TOKEN", "")
).strip()
INFERENCE_SPACE = os.environ.get(
    "HISTAGENT_INFERENCE_SPACE", "https://wli14-histagent-agent.hf.space"
).rstrip("/")
REASONING_SPACE = os.environ.get(
    "HISTAGENT_REASONING_SPACE", "https://wli14-histagent-agent.hf.space"
).rstrip("/")
QUOTA_REPO = os.environ.get(
    "HISTAGENT_QUOTA_REPO", "wli14/HistAgent-service-state"
)
QUOTA_FILE = "quota_state.json"
GPU_QUOTA_SECONDS = int(os.environ.get("HISTAGENT_GPU_QUOTA_SECONDS", "2400"))
QUOTA_WINDOW_SECONDS = int(
    os.environ.get("HISTAGENT_QUOTA_WINDOW_SECONDS", "90000")
)
RATE_LIMIT_WINDOW_SECONDS = int(
    os.environ.get("HISTAGENT_RATE_LIMIT_WINDOW_SECONDS", "3600")
)
RATE_LIMITS = {
    "generate_ranked_readout": int(
        os.environ.get("HISTAGENT_GENERATE_RATE_LIMIT", "12")
    ),
    "retrieve_atlas": int(os.environ.get("HISTAGENT_RETRIEVE_RATE_LIMIT", "60")),
    "answer_atlas_question": int(
        os.environ.get("HISTAGENT_CHAT_RATE_LIMIT", "120")
    ),
}

# Reserve each call at the maximum duration declared by the corresponding
# @spaces.GPU function. This deliberately stops before Hugging Face can draw
# from prepaid credits.
GPU_RESERVATIONS = {
    "generate_ranked_readout": 180,
    "retrieve_atlas": 120,
    "answer_atlas_question": 60,
}
GPU_MINIMUM_CHARGES = {
    "generate_ranked_readout": 15,
    "retrieve_atlas": 10,
    "answer_atlas_question": 10,
}
GPU_CHARGE_BUFFER_SECONDS = 5

ALLOWED_ORIGINS = [
    "https://histagent.bio",
    "https://www.histagent.bio",
    "http://localhost:4000",
    "http://127.0.0.1:4000",
]

app = FastAPI(title="HistAgent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-HistAgent-Session"],
)

_gpu_lock = asyncio.Lock()
_rate_lock = asyncio.Lock()
_recent_calls: dict[str, deque[float]] = defaultdict(deque)
_hf_api = HfApi(token=HF_TOKEN or None)
logger = logging.getLogger("histagent.gateway")


class GradioCall(BaseModel):
    service: str = Field(pattern="^(reasoning)$")
    api_name: str = Field(pattern="^(retrieve_atlas|answer_atlas_question)$")
    data: list[Any]


def _require_token() -> None:
    if not HF_TOKEN:
        raise HTTPException(status_code=503, detail="The HistAgent service is not configured.")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _client_identity(request: Request) -> str:
    session_id = request.headers.get("x-histagent-session", "").strip()
    if 8 <= len(session_id) <= 96 and re.fullmatch(r"[A-Za-z0-9._-]+", session_id):
        return f"session:{session_id}"
    return f"ip:{_client_ip(request)}"


async def _reserve_rate_limit(
    request: Request,
    api_name: str,
) -> tuple[str, float]:
    now = time.time()
    key = f"{api_name}:{_client_identity(request)}"
    limit = RATE_LIMITS[api_name]
    async with _rate_lock:
        calls = _recent_calls[key]
        while calls and calls[0] <= now - RATE_LIMIT_WINDOW_SECONDS:
            calls.popleft()
        if len(calls) >= limit:
            retry_after = max(1, math.ceil(calls[0] + RATE_LIMIT_WINDOW_SECONDS - now))
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "This public demo has reached its request limit. Please retry later.",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        calls.append(now)
    return key, now


async def _release_rate_limit(ticket: tuple[str, float]) -> None:
    key, timestamp = ticket
    async with _rate_lock:
        calls = _recent_calls.get(key)
        if not calls:
            return
        try:
            calls.remove(timestamp)
        except ValueError:
            return
        if not calls:
            _recent_calls.pop(key, None)


def _default_quota_state(now: datetime) -> dict[str, Any]:
    return {
        "window_started_at": now.isoformat(),
        "used_seconds": 0,
        "calls": 0,
        "updated_at": now.isoformat(),
    }


def _load_quota_state(now: datetime) -> dict[str, Any]:
    try:
        path = hf_hub_download(
            repo_id=QUOTA_REPO,
            filename=QUOTA_FILE,
            repo_type="dataset",
            token=HF_TOKEN,
            force_download=True,
        )
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except EntryNotFoundError:
        state = _default_quota_state(now)
    except (HfHubHTTPError, OSError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=503,
            detail="The GPU quota ledger is temporarily unavailable; no GPU call was made.",
        ) from error

    try:
        started = datetime.fromisoformat(str(state["window_started_at"]))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        state = _default_quota_state(now)
        started = now

    # The gateway starts its ledger just before the backend enters the ZeroGPU
    # queue. A 25-hour window prevents a new gateway window from opening a few
    # minutes before Hugging Face resets the corresponding account window.
    if now >= started + timedelta(seconds=QUOTA_WINDOW_SECONDS):
        state = _default_quota_state(now)
    return state


def _save_quota_state(state: dict[str, Any]) -> None:
    payload = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    try:
        _hf_api.upload_file(
            path_or_fileobj=io.BytesIO(payload),
            path_in_repo=QUOTA_FILE,
            repo_id=QUOTA_REPO,
            repo_type="dataset",
            commit_message="Update HistAgent public GPU quota ledger",
        )
    except HfHubHTTPError as error:
        raise HTTPException(
            status_code=503,
            detail="The GPU quota ledger could not be updated; no GPU call was made.",
        ) from error


def _reserve_gpu_seconds(api_name: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    state = _load_quota_state(now)
    reservation = GPU_RESERVATIONS[api_name]
    used = int(state.get("used_seconds", 0))
    if used + reservation > GPU_QUOTA_SECONDS:
        started = datetime.fromisoformat(state["window_started_at"])
        reset_at = started + timedelta(seconds=QUOTA_WINDOW_SECONDS)
        raise HTTPException(
            status_code=429,
            detail={
                "message": "今日 GPU 额度已用完，请在额度重置后重试。",
                "reset_at": reset_at.isoformat(),
                "remaining_seconds": max(0, GPU_QUOTA_SECONDS - used),
            },
        )
    state["used_seconds"] = used + reservation
    state["calls"] = int(state.get("calls", 0)) + 1
    state["updated_at"] = now.isoformat()
    _save_quota_state(state)
    return state


def _refund_gpu_seconds(api_name: str) -> dict[str, Any]:
    """Return a reservation when the backend fails before producing output."""

    now = datetime.now(timezone.utc)
    state = _load_quota_state(now)
    reservation = GPU_RESERVATIONS[api_name]
    state["used_seconds"] = max(
        0,
        int(state.get("used_seconds", 0)) - reservation,
    )
    state["calls"] = max(0, int(state.get("calls", 0)) - 1)
    state["updated_at"] = now.isoformat()
    _save_quota_state(state)
    return state


def _reconcile_gpu_seconds(api_name: str, elapsed_seconds: float) -> dict[str, Any]:
    """Replace the conservative reservation with measured GPU-call time."""

    now = datetime.now(timezone.utc)
    state = _load_quota_state(now)
    reservation = GPU_RESERVATIONS[api_name]
    charged = min(
        reservation,
        max(
            GPU_MINIMUM_CHARGES[api_name],
            math.ceil(elapsed_seconds) + GPU_CHARGE_BUFFER_SECONDS,
        ),
    )
    state["used_seconds"] = max(
        0,
        int(state.get("used_seconds", 0)) - reservation + charged,
    )
    state["updated_at"] = now.isoformat()
    _save_quota_state(state)
    return state


async def _call_with_reservation(
    space: str,
    api_name: str,
    data: list[Any],
) -> list[Any]:
    await asyncio.to_thread(_reserve_gpu_seconds, api_name)
    started = time.monotonic()
    try:
        outputs = await _call_gradio(space, api_name, data)
    except BaseException:
        try:
            await asyncio.to_thread(_refund_gpu_seconds, api_name)
        except Exception:
            logger.exception("Could not return the failed %s reservation", api_name)
        raise
    try:
        await asyncio.to_thread(
            _reconcile_gpu_seconds,
            api_name,
            time.monotonic() - started,
        )
    except Exception:
        # Keeping the full reservation is conservative and prevents overage if
        # accounting reconciliation is temporarily unavailable.
        logger.exception("Could not reconcile the %s reservation", api_name)
    return outputs


def _backend_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {HF_TOKEN}"}


def _is_quota_error(value: Any) -> bool:
    text = str(value).lower()
    return any(term in text for term in ("quota", "zerogpu", "over quota", "exceeded"))


async def _upload_images(files: list[tuple[str, bytes, str]]) -> list[dict[str, Any]]:
    multipart = [
        ("files", (name, content, mime_type)) for name, content, mime_type in files
    ]
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.post(
            f"{INFERENCE_SPACE}/gradio_api/upload",
            headers=_backend_headers(),
            files=multipart,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="The image service rejected the upload.")
    paths = response.json()
    return [
        {
            "path": path,
            "orig_name": files[index][0],
            "mime_type": files[index][2],
            "meta": {"_type": "gradio.FileData"},
        }
        for index, path in enumerate(paths)
    ]


async def _call_gradio(space: str, api_name: str, data: list[Any]) -> list[Any]:
    endpoint = f"{space}/gradio_api/call/{api_name}"
    timeout = httpx.Timeout(connect=30.0, read=240.0, write=60.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        submission = await client.post(
            endpoint,
            headers={**_backend_headers(), "Content-Type": "application/json"},
            json={"data": data},
        )
        if submission.status_code >= 400:
            detail = submission.text[:500]
            if _is_quota_error(detail):
                raise HTTPException(status_code=429, detail="今日 GPU 额度已用完，请稍后再试。")
            logger.error(
                "Backend submission failed api=%s status=%s body=%s",
                api_name,
                submission.status_code,
                detail,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "The model service could not start this request. Please retry.",
                    "code": "backend_submission_error",
                },
            )
        event_id = submission.json().get("event_id")
        if not event_id:
            raise HTTPException(status_code=502, detail="The model service returned no event identifier.")

        event_name = ""
        async with client.stream(
            "GET", f"{endpoint}/{event_id}", headers=_backend_headers()
        ) as stream:
            if stream.status_code >= 400:
                raise HTTPException(status_code=502, detail="The model response stream could not start.")
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    payload = line[5:].strip()
                    if event_name == "complete":
                        value = json.loads(payload)
                        return value if isinstance(value, list) else [value]
                    if event_name == "error":
                        if _is_quota_error(payload):
                            raise HTTPException(
                                status_code=429,
                                detail="今日 GPU 额度已用完，请稍后再试。",
                            )
                        logger.error(
                            "Backend generation failed api=%s payload=%s",
                            api_name,
                            payload[:1000],
                        )
                        raise HTTPException(
                            status_code=502,
                            detail={
                                "message": "The model could not complete this request. Please retry.",
                                "code": "backend_generation_error",
                            },
                        )
    raise HTTPException(status_code=502, detail="The model response ended unexpectedly.")


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "HistAgent API", "status": "ready"}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    _require_token()
    now = datetime.now(timezone.utc)
    state = await asyncio.to_thread(_load_quota_state, now)
    return {
        "status": "ready",
        "remaining_gpu_seconds": max(
            0, GPU_QUOTA_SECONDS - int(state.get("used_seconds", 0))
        ),
        "quota_window_started_at": state["window_started_at"],
    }


@app.post("/api/generate")
async def generate(
    request: Request,
    local_image: UploadFile = File(...),
    context_image: UploadFile = File(...),
    species: str = Form("human"),
    organ: str = Form("Unknown"),
    top_k: int = Form(50),
) -> dict[str, Any]:
    _require_token()
    if local_image.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(status_code=415, detail="Unsupported local image type.")
    if context_image.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(status_code=415, detail="Unsupported context image type.")
    local_bytes, context_bytes = await asyncio.gather(
        local_image.read(), context_image.read()
    )
    if max(len(local_bytes), len(context_bytes)) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Each image must be smaller than 10 MB.")

    rate_ticket = await _reserve_rate_limit(request, "generate_ranked_readout")
    try:
        async with _gpu_lock:
            uploaded = await _upload_images(
                [
                    (local_image.filename or "local.png", local_bytes, local_image.content_type),
                    (context_image.filename or "context.png", context_bytes, context_image.content_type),
                ]
            )
            outputs = await _call_with_reservation(
                INFERENCE_SPACE,
                "generate_ranked_readout",
                [uploaded[0], uploaded[1], species, organ, min(50, max(10, top_k))],
            )
    except BaseException:
        await _release_rate_limit(rate_ticket)
        raise
    return {"data": outputs}


@app.post("/api/call")
async def call(request: Request, payload: GradioCall) -> dict[str, Any]:
    _require_token()
    rate_ticket = await _reserve_rate_limit(request, payload.api_name)
    try:
        async with _gpu_lock:
            outputs = await _call_with_reservation(
                REASONING_SPACE,
                payload.api_name,
                payload.data,
            )
    except BaseException:
        await _release_rate_limit(rate_ticket)
        raise
    return {"data": outputs}
