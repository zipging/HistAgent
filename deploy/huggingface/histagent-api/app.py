from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import os
import re
import time
from collections import OrderedDict, defaultdict, deque
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import EntryNotFoundError, HfHubHTTPError
from pydantic import BaseModel, Field


HF_TOKEN = (
    os.environ.get("WLI14_HF_TOKEN") or os.environ.get("HF_TOKEN", "")
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
BACKEND_SUBMISSION_ATTEMPTS = 3
BACKEND_RETRY_DELAYS_SECONDS = (2.0, 5.0)
BACKEND_RATE_LIMIT_COOLDOWN = 300
BACKEND_HEALTH_TTL = 30
RESPONSE_CACHE_TTL_SECONDS = int(
    os.environ.get("HISTAGENT_RESPONSE_CACHE_TTL_SECONDS", "21600")
)
RESPONSE_CACHE_MAX_ENTRIES = int(
    os.environ.get("HISTAGENT_RESPONSE_CACHE_MAX_ENTRIES", "64")
)
RESPONSE_CACHE_VERSION = os.environ.get("HISTAGENT_RESPONSE_CACHE_VERSION", "v2")

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
    expose_headers=["Retry-After"],
)

_gpu_lock = asyncio.Lock()
_rate_lock = asyncio.Lock()
_cache_lock = asyncio.Lock()
_recent_calls: dict[str, deque[float]] = defaultdict(deque)
_response_cache: OrderedDict[str, tuple[float, list[Any]]] = OrderedDict()
_hf_api = HfApi(token=HF_TOKEN or None)
logger = logging.getLogger("histagent.gateway")
_backend_failures: dict[str, tuple[float, int, str, str]] = {}
_backend_health: dict[str, tuple[float, dict[str, Any]]] = {}
_health_lock = asyncio.Lock()
_submission_state: ContextVar[str] = ContextVar("submission_state", default="unsubmitted")


class GradioCall(BaseModel):
    service: str = Field(pattern="^(reasoning)$")
    api_name: str = Field(pattern="^(retrieve_atlas|answer_atlas_question)$")
    data: list[Any]


def _cache_key(namespace: str, payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"{RESPONSE_CACHE_VERSION}:{namespace}:{hashlib.sha256(serialized).hexdigest()}"


async def _cached_response(key: str) -> list[Any] | None:
    now = time.monotonic()
    async with _cache_lock:
        cached = _response_cache.get(key)
        if cached is None:
            return None
        stored_at, outputs = cached
        if now - stored_at > RESPONSE_CACHE_TTL_SECONDS:
            _response_cache.pop(key, None)
            return None
        _response_cache.move_to_end(key)
        return outputs


async def _store_response(key: str, outputs: list[Any]) -> None:
    async with _cache_lock:
        _response_cache[key] = (time.monotonic(), outputs)
        _response_cache.move_to_end(key)
        while len(_response_cache) > RESPONSE_CACHE_MAX_ENTRIES:
            _response_cache.popitem(last=False)


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
    _check_backend_cooldown(space)
    # Serialize only the quota-ledger updates. Waiting for ZeroGPU should not
    # make unrelated visitors queue behind the active request at this gateway.
    async with _gpu_lock:
        await asyncio.to_thread(_reserve_gpu_seconds, api_name)
    started = time.monotonic()
    submission_context = _submission_state.set("unsubmitted")
    try:
        outputs = await _call_gradio(space, api_name, data)
    except BaseException:
        if _submission_state.get() in {"unsubmitted", "rejected"}:
            try:
                async with _gpu_lock:
                    await asyncio.to_thread(_refund_gpu_seconds, api_name)
            except Exception:
                logger.exception("Could not return the failed %s reservation", api_name)
        else:
            logger.warning("Keeping %s reservation: execution may already have started", api_name)
        raise
    finally:
        _submission_state.reset(submission_context)
    try:
        async with _gpu_lock:
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
    # Match Gradio's official client: HF authenticates the owner on the server,
    # without asking anonymous website visitors for a Hugging Face account.
    return {"X-HF-Authorization": f"Bearer {HF_TOKEN}"}


def _is_quota_error(value: Any) -> bool:
    text = str(value).lower()
    return any(term in text for term in (
        "gpu quota", "exceeded your gpu", "not enough gpu quota",
        "zerogpu quota", "daily gpu limit",
    ))


def _retry_after(response: httpx.Response, default: int) -> int:
    value = response.headers.get("retry-after", "")
    try:
        return max(1, math.ceil(float(value)))
    except (ValueError, OverflowError):
        try:
            return max(1, math.ceil(
                parsedate_to_datetime(value).timestamp() - time.time()
            ))
        except (TypeError, ValueError, OverflowError):
            return default


def _backend_failure(
    space: str, code: str, message: str, *, status: int = 503, seconds: int = 30
) -> HTTPException:
    _backend_failures[space] = (time.monotonic() + seconds, status, code, message)
    _backend_health.pop(space, None)
    return HTTPException(
        status_code=status,
        detail={"message": message, "code": code, "retry_after_seconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


def _check_backend_cooldown(space: str) -> None:
    failure = _backend_failures.get(space)
    if not failure:
        return
    until, status, code, message = failure
    remaining = math.ceil(until - time.monotonic())
    if remaining > 0:
        raise HTTPException(
            status_code=status,
            detail={"message": message, "code": code, "retry_after_seconds": remaining},
            headers={"Retry-After": str(remaining)},
        )
    _backend_failures.pop(space, None)


def _response_error(space: str, response: httpx.Response) -> HTTPException:
    # Do not log response bodies: inference errors can contain submitted data.
    logger.warning("Backend HTTP failure host=%s status=%s", httpx.URL(space).host, response.status_code)
    if response.status_code == 429:
        gpu_quota = _is_quota_error(response.text[:4000])
        return _backend_failure(
            space,
            "gpu_quota_exhausted" if gpu_quota else "backend_rate_limited",
            "The shared GPU allowance is temporarily exhausted. Please try later."
            if gpu_quota else "The model host is temporarily rate-limiting requests. Please wait before retrying.",
            status=429, seconds=_retry_after(response, BACKEND_RATE_LIMIT_COOLDOWN),
        )
    return _backend_failure(
        space, "backend_access_unavailable" if response.status_code in {401, 403, 404} else "backend_temporarily_unavailable",
        "The model service is temporarily unavailable. Please retry shortly.",
        seconds=_retry_after(response, 30),
    )


async def _post_backend(
    client: httpx.AsyncClient, space: str, path: str, **kwargs: Any
) -> httpx.Response:
    _check_backend_cooldown(space)
    gpu_submission = path.startswith("/gradio_api/call/")
    for attempt in range(BACKEND_SUBMISSION_ATTEMPTS):
        try:
            if gpu_submission:
                _submission_state.set("uncertain")
            response = await client.post(
                f"{space}{path}", headers=_backend_headers(), follow_redirects=False, **kwargs
            )
        except httpx.TransportError as error:
            # A timeout may happen after a job was accepted. Never submit that
            # job again automatically, since it could use a second GPU session.
            raise _backend_failure(
                space, "backend_connection_error",
                "The connection to the model was interrupted. Please retry shortly.",
            ) from error
        if 200 <= response.status_code < 300:
            if gpu_submission:
                _submission_state.set("accepted")
            return response
        if gpu_submission and 300 <= response.status_code < 500:
            _submission_state.set("rejected")
        if not gpu_submission and response.status_code in {502, 503, 504} and attempt < BACKEND_SUBMISSION_ATTEMPTS - 1:
            delay = _retry_after(response, math.ceil(BACKEND_RETRY_DELAYS_SECONDS[attempt]))
            if delay <= 10:
                await asyncio.sleep(delay)
                continue
        # In particular, never hammer a 429 or follow a redirect with a token.
        raise _response_error(space, response)
    raise RuntimeError("Unreachable submission state")


async def _upload_images(files: list[tuple[str, bytes, str]]) -> list[dict[str, Any]]:
    multipart = [
        ("files", (name, content, mime_type)) for name, content, mime_type in files
    ]
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        response = await _post_backend(client, INFERENCE_SPACE, "/gradio_api/upload", files=multipart)
    try:
        paths = response.json()
        if not isinstance(paths, list) or len(paths) != len(files) or not all(isinstance(p, str) for p in paths):
            raise ValueError("Invalid image upload response")
    except (ValueError, TypeError) as error:
        raise _backend_failure(INFERENCE_SPACE, "backend_invalid_response", "The image service returned an invalid response. Please retry.") from error
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
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        submission = await _post_backend(client, space, f"/gradio_api/call/{api_name}", json={"data": data})
        try:
            event_id = submission.json().get("event_id")
            if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", event_id):
                raise ValueError("Missing event identifier")
        except (ValueError, AttributeError) as error:
            raise _backend_failure(space, "backend_invalid_response", "The model service returned an invalid response.") from error
        try:
            event_name = ""
            async with client.stream("GET", f"{endpoint}/{event_id}", headers=_backend_headers()) as stream:
                if not 200 <= stream.status_code < 300:
                    await stream.aread()
                    raise _response_error(space, stream)
                async for line in stream.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        payload = line[5:].strip()
                        if event_name == "complete":
                            value = json.loads(payload)
                            return value if isinstance(value, list) else [value]
                        if event_name == "error":
                            quota = _is_quota_error(payload)
                            if quota:
                                _submission_state.set("rejected")
                            raise _backend_failure(
                                space, "gpu_quota_exhausted" if quota else "backend_generation_error",
                                "The shared GPU allowance is temporarily exhausted. Please try later." if quota else "The model could not complete this request. Please retry shortly.",
                                status=429 if quota else 502, seconds=300 if quota else 30,
                            )
        except (httpx.TransportError, ValueError) as error:
            raise _backend_failure(space, "backend_stream_interrupted", "The model response was interrupted. Please retry shortly.") from error
    raise _backend_failure(space, "backend_stream_interrupted", "The model response ended unexpectedly. Please retry shortly.")


async def _probe_backend(space: str) -> dict[str, Any]:
    try:
        _check_backend_cooldown(space)
    except HTTPException as error:
        return {"status": "unavailable", **error.detail}
    cached = _backend_health.get(space)
    if cached and time.monotonic() - cached[0] < BACKEND_HEALTH_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(f"{space}/config", headers=_backend_headers())
        if not 200 <= response.status_code < 300:
            raise _response_error(space, response)
        config = response.json()
        if not isinstance(config, dict) or "dependencies" not in config:
            raise ValueError("Invalid Gradio configuration")
        # Reachable is deliberately not a claim that a GPU job has completed.
        result = {"status": "reachable"}
    except HTTPException as error:
        result = {"status": "unavailable", **error.detail}
    except (httpx.TransportError, ValueError):
        result = {"status": "unavailable", "code": "backend_not_ready", "message": "The model service is starting or temporarily unreachable."}
    _backend_health[space] = (time.monotonic(), result)
    return result


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "HistAgent API", "status": "online", "health": "/api/health"}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    _require_token()
    now = datetime.now(timezone.utc)
    state = await asyncio.to_thread(_load_quota_state, now)
    async with _health_lock:
        inference, reasoning = await asyncio.gather(_probe_backend(INFERENCE_SPACE), _probe_backend(REASONING_SPACE))
    remaining = max(0, GPU_QUOTA_SECONDS - int(state.get("used_seconds", 0)))
    return {
        "status": "available" if remaining >= min(GPU_RESERVATIONS.values()) and all(item["status"] == "reachable" for item in (inference, reasoning)) else "degraded",
        "backends": {"inference": inference, "reasoning": reasoning},
        "remaining_gpu_seconds": remaining,
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
    bounded_top_k = min(50, max(10, top_k))
    cache_key = _cache_key(
        "generate_ranked_readout",
        {
            "local_sha256": hashlib.sha256(local_bytes).hexdigest(),
            "context_sha256": hashlib.sha256(context_bytes).hexdigest(),
            "species": species,
            "organ": organ,
            "top_k": bounded_top_k,
        },
    )
    cached = await _cached_response(cache_key)
    if cached is not None:
        return {"data": cached, "cached": True}
    try:
        uploaded = await _upload_images(
            [
                (local_image.filename or "local.png", local_bytes, local_image.content_type),
                (context_image.filename or "context.png", context_bytes, context_image.content_type),
            ]
        )
        outputs = await _call_with_reservation(
            INFERENCE_SPACE,
            "generate_ranked_readout",
            [uploaded[0], uploaded[1], species, organ, bounded_top_k],
        )
    except BaseException:
        await _release_rate_limit(rate_ticket)
        raise
    await _store_response(cache_key, outputs)
    return {"data": outputs}


@app.post("/api/call")
async def call(request: Request, payload: GradioCall) -> dict[str, Any]:
    _require_token()
    rate_ticket = await _reserve_rate_limit(request, payload.api_name)
    call_data = list(payload.data)
    if payload.api_name == "retrieve_atlas" and len(call_data) == 4:
        call_data.insert(3, "__ready__")
    cache_key = _cache_key(payload.api_name, call_data)
    cached = await _cached_response(cache_key)
    if cached is not None:
        return {"data": cached, "cached": True}
    try:
        outputs = await _call_with_reservation(
            REASONING_SPACE,
            payload.api_name,
            call_data,
        )
    except BaseException:
        await _release_rate_limit(rate_ticket)
        raise
    await _store_response(cache_key, outputs)
    return {"data": outputs}
