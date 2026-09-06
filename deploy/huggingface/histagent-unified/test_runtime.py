"""CPU-only integration checks for the actual local and Gradio HTTP runtime."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import gradio as gr
import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from gradio.context import LocalContext
from gradio.routes import App
from PIL import Image


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).parent
LocalBackend = load_module("histagent_test_local_backend", HERE / "local_backend.py").LocalBackend
gateway = load_module("histagent_test_gateway", HERE.parent / "histagent-api" / "app.py")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("Runtime tests must not access the network")

    async def no_async_network(*_args, **_kwargs):
        raise AssertionError("Runtime tests must not access the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_async_network)
    monkeypatch.setattr(gateway, "HF_TOKEN", "test-token")
    monkeypatch.setattr(gateway, "_local_backend", None)
    for name in ("_gpu_lock", "_rate_lock", "_cache_lock", "_health_lock"):
        monkeypatch.setattr(gateway, name, asyncio.Lock())
    for name in ("_recent_calls", "_response_cache", "_backend_failures", "_backend_health"):
        getattr(gateway, name).clear()
    state = gateway._default_quota_state(datetime.now(timezone.utc))
    monkeypatch.setattr(gateway, "_load_quota_state", lambda _: state.copy())
    monkeypatch.setattr(gateway, "_save_quota_state", lambda value: state.update(value))
    request_token = gateway._backend_request.set(None)
    identity_token = gateway._backend_ip_token.set("")
    yield
    gateway._backend_request.reset(request_token)
    gateway._backend_ip_token.reset(identity_token)


def backend_for(dispatch, demo=None):
    def unused(*_args):
        raise AssertionError("The runtime must call the shared GPU dispatcher")

    return LocalBackend(
        demo if demo is not None else object(),
        SimpleNamespace(generate_ranked_readout=unused),
        SimpleNamespace(answer_atlas_question=unused, retrieve_atlas=unused),
        gpu_dispatch=dispatch,
    )


def png_bytes(size=(12, 16)):
    buffer = io.BytesIO()
    Image.new("RGBA", size, (100, 50, 120, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


def incoming_request(identity="signed-visitor"):
    return Request({"type": "http", "method": "POST", "path": "/api/generate",
                    "headers": [(b"x-ip-token", identity.encode())],
                    "client": ("127.0.0.1", 1), "server": ("testserver", 80), "scheme": "http"})


@pytest.mark.anyio
@pytest.mark.parametrize("fail", [False, True])
async def test_real_backend_removes_temporary_images_after_success_and_failure(fail):
    paths = []
    admitted = []
    request = incoming_request()
    demo = object()

    def dispatch(api_name, data):
        assert admitted == [True]
        assert api_name == "generate_ranked_readout"
        assert data[2:] == ["human", "kidney", 50]
        assert LocalContext.request.get().request is request
        assert LocalContext.blocks.get() is demo
        paths.extend(Path(value) for value in data[:2])
        assert all(path.is_file() for path in paths)
        for path in paths:
            with Image.open(path) as image:
                assert image.mode == "RGB"
                assert image.size == (12, 16)
        if fail:
            raise RuntimeError("Worker failed after reading images")
        return {"data": [[1, "COL1A1"]]}, "COL1A1"

    backend = backend_for(dispatch, demo)
    uploads = await backend.upload_images([
        ("../../local.png", png_bytes(), "image/png"),
        ("context.png", png_bytes(), "image/png"),
    ])
    assert uploads[0]["orig_name"] == "local.png"
    invoke = backend.call("generate_ranked_readout", [*uploads, "human", "kidney", 50], request,
                          on_admitted=lambda: admitted.append(True))
    if fail:
        with pytest.raises(RuntimeError, match="Worker failed"):
            await invoke
    else:
        assert await invoke == [{"data": [[1, "COL1A1"]]}, "COL1A1"]
    assert len(paths) == 2
    assert all(not path.exists() and not path.parent.exists() for path in paths)


@pytest.mark.anyio
@pytest.mark.parametrize("cancel_waiter", [False, True])
async def test_admission_callback_runs_only_after_capacity_and_cancelled_waiter_never_dispatches(cancel_waiter):
    callbacks = []
    dispatched = []

    def dispatch(api_name, data):
        assert callbacks == ["admitted"]
        dispatched.append(api_name)
        return "", [{"role": "assistant", "content": "Answer"}]

    backend = backend_for(dispatch)
    await backend._limiter.acquire()
    task = asyncio.create_task(backend.call(
        "answer_atlas_question", ["Question", [], {}], incoming_request(),
        on_admitted=lambda: callbacks.append("admitted"),
    ))

    async def wait_until_queued():
        while backend._limiter.statistics().tasks_waiting != 1:
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_until_queued(), timeout=2)
        assert callbacks == dispatched == []
        if cancel_waiter:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert callbacks == dispatched == []
    finally:
        backend._limiter.release()
    if not cancel_waiter:
        assert (await asyncio.wait_for(task, timeout=2))[1][-1]["content"] == "Answer"
        assert callbacks == ["admitted"]
        assert dispatched == ["answer_atlas_question"]


def test_real_gradio_app_serves_gateway_health_inference_and_cors():
    dispatched = []
    temporary_paths = []

    def dispatch(api_name, data):
        dispatched.append(api_name)
        assert LocalContext.request.get().request.headers["x-ip-token"] == "browser-visitor"
        assert data[2:] == ["human", "kidney", 50]
        temporary_paths.extend(Path(path) for path in data[:2])
        assert all(path.exists() for path in temporary_paths)
        return {"data": [[1, "COL1A1"]]}, "COL1A1"

    with gr.Blocks(analytics_enabled=False) as demo:
        gr.Markdown("HistAgent runtime test")
    backend = backend_for(dispatch, demo)
    gateway.configure_local_backend(backend)
    routes = [route for route in gateway.app.routes if getattr(route, "path", "").startswith("/api/")]
    app = App.create_app(demo, app_kwargs={"routes": routes, "middleware": gateway.app.user_middleware})
    client = TestClient(app)
    origin = "https://histagent.bio"
    preflight = client.options("/api/generate", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-HistAgent-Session",
    })
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    health = client.get("/api/health", headers={"Origin": origin})
    assert health.status_code == 200
    assert health.json()["status"] == "available"
    assert health.json()["execution"] == "in_process"
    assert health.json()["platform_quota_scope"] == "visitor"
    assert health.headers["access-control-allow-origin"] == origin
    generated = client.post("/api/generate", headers={"Origin": origin, "X-IP-Token": "browser-visitor"},
                            files={"local_image": ("local.png", png_bytes(), "image/png"),
                                   "context_image": ("context.png", png_bytes(), "image/png")},
                            data={"species": "human", "organ": "kidney", "top_k": 50})
    assert generated.status_code == 200
    assert generated.json()["data"] == [{"data": [[1, "COL1A1"]]}, "COL1A1"]
    assert generated.headers["access-control-allow-origin"] == origin
    assert dispatched == ["generate_ranked_readout"]
    assert all(not path.exists() for path in temporary_paths)
    assert gateway._backend_request.get() is None
