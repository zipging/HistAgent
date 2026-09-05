from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import app as gateway


@pytest.fixture(autouse=True)
def configured_gateway(monkeypatch):
    monkeypatch.setattr(gateway, "HF_TOKEN", "test-token")
    for name in ("_gpu_lock", "_rate_lock", "_cache_lock", "_health_lock"):
        monkeypatch.setattr(gateway, name, asyncio.Lock())
    gateway._recent_calls.clear()
    gateway._response_cache.clear()
    gateway._backend_failures.clear()
    gateway._backend_health.clear()


def test_reservation_stops_at_included_quota(monkeypatch):
    now = datetime.now(timezone.utc)
    state = {
        "window_started_at": now.isoformat(),
        "used_seconds": 2280,
        "calls": 4,
        "updated_at": now.isoformat(),
    }
    saved = []
    monkeypatch.setattr(gateway, "_load_quota_state", lambda _: state.copy())
    monkeypatch.setattr(gateway, "_save_quota_state", lambda value: saved.append(value))

    reserved = gateway._reserve_gpu_seconds("retrieve_atlas")
    assert reserved["used_seconds"] == 2400
    assert saved[-1]["used_seconds"] == 2400

    state["used_seconds"] = 2400
    with pytest.raises(gateway.HTTPException) as caught:
        gateway._reserve_gpu_seconds("retrieve_atlas")
    assert caught.value.status_code == 429


def test_refund_returns_failed_reservation(monkeypatch):
    now = datetime.now(timezone.utc)
    state = {
        "window_started_at": now.isoformat(),
        "used_seconds": 300,
        "calls": 2,
        "updated_at": now.isoformat(),
    }
    saved = []
    monkeypatch.setattr(gateway, "_load_quota_state", lambda _: state.copy())
    monkeypatch.setattr(gateway, "_save_quota_state", lambda value: saved.append(value))

    refunded = gateway._refund_gpu_seconds("generate_ranked_readout")
    assert refunded["used_seconds"] == 120
    assert refunded["calls"] == 1
    assert saved[-1]["used_seconds"] == 120


def test_reconcile_charges_measured_time_with_safety_floor(monkeypatch):
    now = datetime.now(timezone.utc)
    state = {
        "window_started_at": now.isoformat(),
        "used_seconds": 180,
        "calls": 1,
        "updated_at": now.isoformat(),
    }
    saved = []
    monkeypatch.setattr(gateway, "_load_quota_state", lambda _: state.copy())
    monkeypatch.setattr(gateway, "_save_quota_state", lambda value: saved.append(value))

    reconciled = gateway._reconcile_gpu_seconds("generate_ranked_readout", 7.2)
    assert reconciled["used_seconds"] == 15
    assert reconciled["calls"] == 1
    assert saved[-1]["used_seconds"] == 15


@pytest.mark.anyio
async def test_backend_failure_refunds_reservation(monkeypatch):
    calls = []

    monkeypatch.setattr(
        gateway,
        "_reserve_gpu_seconds",
        lambda api_name: calls.append(("reserve", api_name)),
    )
    monkeypatch.setattr(
        gateway,
        "_refund_gpu_seconds",
        lambda api_name: calls.append(("refund", api_name)),
    )

    async def fail_call(*_args, **_kwargs):
        raise gateway.HTTPException(status_code=502, detail="backend failed")

    monkeypatch.setattr(gateway, "_call_gradio", fail_call)
    with pytest.raises(gateway.HTTPException):
        await gateway._call_with_reservation(
            "https://example.invalid",
            "retrieve_atlas",
            ["query"],
        )

    assert calls == [
        ("reserve", "retrieve_atlas"),
        ("refund", "retrieve_atlas"),
    ]


@pytest.mark.anyio
async def test_backend_wait_is_not_globally_serialized(monkeypatch):
    active = 0
    maximum_active = 0

    monkeypatch.setattr(gateway, "_reserve_gpu_seconds", lambda _api: {})
    monkeypatch.setattr(gateway, "_reconcile_gpu_seconds", lambda _api, _elapsed: {})

    async def fake_call(*_args, **_kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return ["ok"]

    monkeypatch.setattr(gateway, "_call_gradio", fake_call)
    await asyncio.gather(
        gateway._call_with_reservation("https://example.invalid", "retrieve_atlas", ["a"]),
        gateway._call_with_reservation("https://example.invalid", "answer_atlas_question", ["b"]),
    )
    assert maximum_active == 2


@pytest.mark.anyio
async def test_response_cache_reuses_successful_output():
    key = gateway._cache_key("retrieve_atlas", ["TLS", "human"])
    assert await gateway._cached_response(key) is None
    await gateway._store_response(key, ["result"])
    assert await gateway._cached_response(key) == ["result"]


@pytest.mark.anyio
async def test_rate_limits_are_session_and_endpoint_specific(monkeypatch):
    monkeypatch.setitem(gateway.RATE_LIMITS, "answer_atlas_question", 1)
    headers = [(b"x-histagent-session", b"browser-session-123")]
    request = Request({"type": "http", "headers": headers, "client": ("127.0.0.1", 1)})

    await gateway._reserve_rate_limit(request, "answer_atlas_question")
    with pytest.raises(gateway.HTTPException) as caught:
        await gateway._reserve_rate_limit(request, "answer_atlas_question")
    assert caught.value.status_code == 429
    await gateway._reserve_rate_limit(request, "retrieve_atlas")


def test_quota_window_resets_after_safety_window(monkeypatch):
    now = datetime.now(timezone.utc)
    stale = {
        "window_started_at": (now - timedelta(hours=26)).isoformat(),
        "used_seconds": 2400,
        "calls": 20,
        "updated_at": (now - timedelta(hours=24)).isoformat(),
    }
    monkeypatch.setattr(gateway, "hf_hub_download", lambda **_: "/tmp/quota.json")
    monkeypatch.setattr(
        gateway.Path,
        "read_text",
        lambda *_args, **_kwargs: __import__("json").dumps(stale),
    )
    state = gateway._load_quota_state(now)
    assert state["used_seconds"] == 0
    assert state["calls"] == 0


def test_reasoning_proxy_returns_backend_outputs(monkeypatch):
    async def fake_call(_space, api_name, data):
        assert api_name == "retrieve_atlas"
        assert data == ["TLS-like immune niche", "human", "Any", "__ready__", 5]
        return [["rows"], {"ranked_genes": ["CXCL13"]}]

    monkeypatch.setattr(gateway, "_call_gradio", fake_call)
    monkeypatch.setattr(gateway, "_reserve_gpu_seconds", lambda _api: {})
    monkeypatch.setattr(gateway, "_reconcile_gpu_seconds", lambda _api, _elapsed: {})

    response = TestClient(gateway.app).post(
        "/api/call",
        json={
            "service": "reasoning",
            "api_name": "retrieve_atlas",
            "data": ["TLS-like immune niche", "human", "Any", 5],
        },
    )
    assert response.status_code == 200
    assert response.json()["data"][1]["ranked_genes"] == ["CXCL13"]


def test_generate_proxy_accepts_two_images(monkeypatch):
    async def fake_upload(files):
        assert len(files) == 2
        return [
            {"path": "/tmp/local.png", "meta": {"_type": "gradio.FileData"}},
            {"path": "/tmp/context.png", "meta": {"_type": "gradio.FileData"}},
        ]

    async def fake_call(_space, api_name, data):
        assert api_name == "generate_ranked_readout"
        assert data[2:5] == ["human", "kidney", 50]
        return [{"data": [[1, "CXCL13"]]}, "CXCL13", {}, "ready"]

    monkeypatch.setattr(gateway, "_upload_images", fake_upload)
    monkeypatch.setattr(gateway, "_call_gradio", fake_call)
    monkeypatch.setattr(gateway, "_reserve_gpu_seconds", lambda _api: {})
    monkeypatch.setattr(gateway, "_reconcile_gpu_seconds", lambda _api, _elapsed: {})

    files = {
        "local_image": ("local.png", b"png", "image/png"),
        "context_image": ("context.png", b"png", "image/png"),
    }
    response = TestClient(gateway.app).post(
        "/api/generate",
        files=files,
        data={"species": "human", "organ": "kidney", "top_k": "50"},
    )
    assert response.status_code == 200
    assert response.json()["data"][1] == "CXCL13"


@pytest.fixture
def backend_transport(monkeypatch):
    """Exercise real gateway HTTP handling without contacting Hugging Face."""
    original_client = httpx.AsyncClient

    def install(handler):
        requests = []

        def record(request):
            requests.append(request)
            return handler(request)

        transport = httpx.MockTransport(record)

        def client(*args, **kwargs):
            kwargs["transport"] = transport
            return original_client(*args, **kwargs)

        monkeypatch.setattr(gateway.httpx, "AsyncClient", client)
        return requests

    return install


@pytest.fixture
def local_quota(monkeypatch):
    now = datetime.now(timezone.utc)
    state = gateway._default_quota_state(now)
    monkeypatch.setattr(gateway, "_load_quota_state", lambda _: state.copy())
    monkeypatch.setattr(gateway, "_save_quota_state", lambda value: state.update(value))
    return state


def test_anonymous_generate_authenticates_upload_submission_and_stream(
    monkeypatch, backend_transport, local_quota
):
    space = "https://private-inference.hf.space"
    monkeypatch.setattr(gateway, "INFERENCE_SPACE", space)
    outputs = [{"data": [[1, "COL1A1"]]}, "COL1A1", {}, "ready"]

    def respond(request):
        assert request.url.host == "private-inference.hf.space"
        assert request.headers["x-hf-authorization"] == "Bearer test-token"
        if request.url.path == "/gradio_api/upload":
            assert request.method == "POST"
            return httpx.Response(200, json=["/tmp/local.png", "/tmp/context.png"])
        if request.url.path == "/gradio_api/call/generate_ranked_readout":
            assert request.method == "POST"
            payload = json.loads(request.content)
            assert payload["data"][0]["path"] == "/tmp/local.png"
            assert payload["data"][1]["path"] == "/tmp/context.png"
            assert payload["data"][2:] == ["human", "kidney", 50]
            return httpx.Response(200, json={"event_id": "event-123"})
        if request.url.path == "/gradio_api/call/generate_ranked_readout/event-123":
            assert request.method == "GET"
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text=f"event: complete\ndata: {json.dumps(outputs)}\n\n",
            )
        raise AssertionError(f"Unexpected backend request: {request.method} {request.url}")

    requests = backend_transport(respond)
    response = TestClient(gateway.app).post(
        "/api/generate",
        files={
            "local_image": ("local.png", b"png-local", "image/png"),
            "context_image": ("context.png", b"png-context", "image/png"),
        },
        data={"species": "human", "organ": "kidney", "top_k": "50"},
    )
    assert response.status_code == 200
    assert response.json()["data"] == outputs
    assert [request.method for request in requests] == ["POST", "POST", "GET"]
    assert "test-token" not in response.text


@pytest.mark.anyio
async def test_backend_redirect_never_forwards_token_to_another_host(backend_transport):
    def respond(_request):
        return httpx.Response(302, headers={"Location": "https://untrusted.invalid/collect"})

    requests = backend_transport(respond)
    async with gateway.httpx.AsyncClient(follow_redirects=True) as client:
        with pytest.raises(gateway.HTTPException) as caught:
            await gateway._post_backend(
                client,
                "https://private-agent.hf.space",
                "/gradio_api/call/answer_atlas_question",
                json={"data": ["Question", [], {}]},
            )
    assert caught.value.status_code >= 500
    assert len(requests) == 1
    assert requests[0].url.host == "private-agent.hf.space"


@pytest.mark.anyio
async def test_hf_edge_rate_limit_opens_circuit_without_claiming_gpu_exhaustion(
    backend_transport,
):
    def respond(_request):
        return httpx.Response(
            429,
            headers={"Retry-After": "60", "Content-Type": "text/html"},
            text="<html>We had to rate limit you. If you think it's an error, contact support.</html>",
        )

    requests = backend_transport(respond)
    async with gateway.httpx.AsyncClient() as client:
        for _ in range(2):
            with pytest.raises(gateway.HTTPException) as caught:
                await gateway._post_backend(
                    client,
                    "https://private-agent.hf.space",
                    "/gradio_api/call/answer_atlas_question",
                    json={"data": ["Question", [], {}]},
                )
            assert caught.value.status_code == 429
            assert caught.value.detail["code"] == "backend_rate_limited"
            detail = str(caught.value.detail).lower()
            assert "gpu quota" not in detail
            assert "额度已用完" not in detail
            assert int(caught.value.headers["Retry-After"]) > 0
    assert len(requests) == 1


@pytest.mark.parametrize(
    "message",
    [
        "ZeroGPU service is temporarily unavailable.",
        "An upstream ZeroGPU connection failed.",
        "ZeroGPU illegal duration: requested duration exceeds the per-call limit.",
        "Request size exceeded the supported maximum.",
        "We had to rate limit you. Review ZeroGPU and Hub quota documentation.",
    ],
)
def test_service_or_limit_text_is_not_gpu_quota_exhaustion(message):
    assert not gateway._is_quota_error(message)


@pytest.mark.parametrize(
    "message",
    [
        "You have exceeded your GPU quota (60s requested vs. 0s left).",
        "You have exceeded your Pro GPU quota (60s requested vs. 0s left).",
        "You have exceeded your Pro ZeroGPU quota (60s requested vs. 30s left). Try again in 1:23:45.",
        "ZeroGPU quota exceeded",
    ],
)
def test_explicit_gpu_quota_exhaustion_is_recognized(message):
    assert gateway._is_quota_error(message)


def test_health_reports_backend_failure_even_when_quota_is_available(
    backend_transport, local_quota
):
    def respond(_request):
        return httpx.Response(
            429,
            headers={"Retry-After": "60", "Content-Type": "text/html"},
            text="<html>We had to rate limit you.</html>",
        )

    requests = backend_transport(respond)
    client = TestClient(gateway.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["remaining_gpu_seconds"] == gateway.GPU_QUOTA_SECONDS
    assert requests
    for backend in response.json()["backends"].values():
        assert backend["status"] == "unavailable"
        assert backend["code"] == "backend_rate_limited"
    request_count = len(requests)
    repeated = client.get("/api/health")
    assert repeated.json()["status"] == "degraded"
    assert len(requests) == request_count


@pytest.mark.anyio
async def test_submission_timeout_keeps_budget_and_does_not_repeat_the_job(
    backend_transport, local_quota
):
    def respond(request):
        raise httpx.ReadTimeout("Reply lost after submission", request=request)

    requests = backend_transport(respond)
    previous = gateway._submission_state.get()
    with pytest.raises(gateway.HTTPException):
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
        )
    assert len(requests) == 1
    assert local_quota["used_seconds"] == gateway.GPU_RESERVATIONS["answer_atlas_question"]
    assert local_quota["calls"] == 1
    assert gateway._submission_state.get() == previous


@pytest.mark.anyio
@pytest.mark.parametrize("status", [502, 503, 504])
async def test_ambiguous_submission_http_failure_keeps_budget_without_resubmitting(
    backend_transport, local_quota, status
):
    requests = backend_transport(lambda _: httpx.Response(status, text="Upstream response unavailable"))
    with pytest.raises(gateway.HTTPException):
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
        )
    assert len(requests) == 1
    assert local_quota["used_seconds"] == gateway.GPU_RESERVATIONS["answer_atlas_question"]
    assert local_quota["calls"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("stream_failure", ["disconnect", "http_429"])
async def test_accepted_job_keeps_budget_when_its_response_stream_fails(
    backend_transport, local_quota, stream_failure
):
    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"event: heartbeat\ndata: null\n\n"
            raise httpx.ReadError("Connection closed while the GPU job may still be running")

    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, json={"event_id": "accepted-job"})
        if stream_failure == "http_429":
            return httpx.Response(429, headers={"Retry-After": "60"}, text="We had to rate limit you.")
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=InterruptedStream()
        )

    requests = backend_transport(respond)
    with pytest.raises(gateway.HTTPException):
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
        )
    assert [request.method for request in requests] == ["POST", "GET"]
    assert local_quota["used_seconds"] == gateway.GPU_RESERVATIONS["answer_atlas_question"]
    assert local_quota["calls"] == 1


@pytest.mark.anyio
async def test_explicit_http_rejection_refunds_the_unused_gpu_reservation(
    backend_transport, local_quota
):
    requests = backend_transport(
        lambda _: httpx.Response(429, headers={"Retry-After": "60"}, text="We had to rate limit you.")
    )
    with pytest.raises(gateway.HTTPException) as caught:
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
        )
    assert caught.value.detail["code"] == "backend_rate_limited"
    assert len(requests) == 1
    assert local_quota["used_seconds"] == 0
    assert local_quota["calls"] == 0


@pytest.mark.anyio
async def test_gpu_scheduler_quota_rejection_refunds_the_unused_reservation(
    backend_transport, local_quota
):
    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, json={"event_id": "queued-job"})
        payload = json.dumps("You have exceeded your Pro ZeroGPU quota (60s requested vs. 0s left).")
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            text=f"event: error\ndata: {payload}\n\n",
        )

    requests = backend_transport(respond)
    with pytest.raises(gateway.HTTPException) as caught:
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
        )
    assert caught.value.detail["code"] == "gpu_quota_exhausted"
    assert [request.method for request in requests] == ["POST", "GET"]
    assert local_quota["used_seconds"] == 0
    assert local_quota["calls"] == 0


@pytest.mark.anyio
async def test_concurrent_submission_states_cannot_refund_another_running_job(
    monkeypatch, local_quota
):
    accepted = asyncio.Event()
    rejected = asyncio.Event()

    async def overlapping_calls(_space, api_name, _data):
        if api_name == "retrieve_atlas":
            gateway._submission_state.set("accepted")
            accepted.set()
            await rejected.wait()
            raise gateway.HTTPException(502, detail="Accepted job lost its response stream")
        await accepted.wait()
        gateway._submission_state.set("rejected")
        rejected.set()
        await asyncio.sleep(0)
        raise gateway.HTTPException(429, detail="Second job was rejected before execution")

    monkeypatch.setattr(gateway, "_call_gradio", overlapping_calls)
    previous = gateway._submission_state.get()
    results = await asyncio.wait_for(
        asyncio.gather(
            gateway._call_with_reservation("https://private-agent.hf.space", "retrieve_atlas", ["query"]),
            gateway._call_with_reservation("https://private-agent.hf.space", "answer_atlas_question", ["query"]),
            return_exceptions=True,
        ),
        timeout=2,
    )
    assert all(isinstance(result, gateway.HTTPException) for result in results)
    assert local_quota["used_seconds"] == gateway.GPU_RESERVATIONS["retrieve_atlas"]
    assert local_quota["calls"] == 1
    assert gateway._submission_state.get() == previous


@pytest.mark.anyio
async def test_earlier_job_completion_preserves_a_newer_backend_cooldown(backend_transport):
    space = "https://private-agent.hf.space"

    class CompletedAfterRateLimit(httpx.AsyncByteStream):
        async def __aiter__(self):
            # Another visitor receives a rate limit while this job is running.
            gateway._backend_failure(
                space, "backend_rate_limited", "Please wait", status=429, seconds=60
            )
            yield b'event: complete\ndata: ["Completed answer"]\n\n'

    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, json={"event_id": "earlier-job"})
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=CompletedAfterRateLimit()
        )

    requests = backend_transport(respond)
    assert await gateway._call_gradio(space, "answer_atlas_question", ["Question", [], {}]) == ["Completed answer"]
    with pytest.raises(gateway.HTTPException) as caught:
        gateway._check_backend_cooldown(space)
    assert caught.value.detail["code"] == "backend_rate_limited"
    assert len(requests) == 2
