from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import app as gateway


@pytest.mark.parametrize("path", [
    "/config", "/gradio_api/call/retrieve_atlas/event", "/gradio_api/upload",
    "/gradio_api/queue/join", "/gradio_api/queue/data",
])
def test_backend_diagnostics_redact_credentials_and_exclude_user_content(caplog, path):
    response = httpx.Response(
        429,
        request=httpx.Request("GET", "https://example.invalid" + path),
        headers={"x-request-id": "request-123", "x-error-message": "test-token hf_secret123 signed-identity"},
        text=("<style>hidden-style</style><script>hidden-script</script>"
              "<p>upstream-detail test-token hf_secret123 signed-identity</p>"),
    )
    context = gateway._backend_ip_token.set("signed-identity")
    try:
        gateway._response_error("https://example.invalid", response)
    finally:
        gateway._backend_ip_token.reset(context)
    assert "request-123" in caplog.text
    assert "test-token" not in caplog.text
    assert "hf_secret123" not in caplog.text
    assert "signed-identity" not in caplog.text
    assert "hidden-style" not in caplog.text
    assert "hidden-script" not in caplog.text
    assert ("upstream-detail" in caplog.text) == (path == "/config")


@pytest.mark.parametrize("credential", ["visitor", "hf"])
def test_backend_diagnostics_redact_secrets_before_header_and_body_truncation(
    monkeypatch, caplog, credential
):
    secret = "signed-private-identity-" + "z" * 17000
    if credential == "hf":
        secret = "hf_privatecredential" + "z" * 17000
        monkeypatch.setattr(gateway, "HF_TOKEN", secret)
    context = gateway._backend_ip_token.set(secret if credential == "visitor" else "")
    try:
        gateway._response_error(
            "https://example.invalid",
            httpx.Response(
                429,
                request=httpx.Request("GET", "https://example.invalid/config"),
                headers={"x-request-id": "long-secret-request",
                         "x-error-message": "h" * 230 + secret},
                text="b" * 1170 + secret,
            ),
        )
    finally:
        gateway._backend_ip_token.reset(context)
    assert "long-secret-request" in caplog.text
    assert "[redacted]" in caplog.text
    assert secret[:20] not in caplog.text


@pytest.fixture(autouse=True)
def configured_gateway(monkeypatch):
    monkeypatch.setattr(gateway, "HF_TOKEN", "test-token")
    monkeypatch.setattr(gateway, "FORWARD_VISITOR_IDENTITY", True)
    for name in ("_gpu_lock", "_rate_lock", "_cache_lock", "_health_lock"):
        monkeypatch.setattr(gateway, name, asyncio.Lock())
    gateway._recent_calls.clear()
    gateway._response_cache.clear()
    gateway._backend_failures.clear()
    gateway._backend_health.clear()
    context = gateway._backend_ip_token.set("")
    yield
    gateway._backend_ip_token.reset(context)


def test_owner_funded_mode_keeps_service_identity(monkeypatch):
    monkeypatch.setattr(gateway, "FORWARD_VISITOR_IDENTITY", False)
    context = gateway._backend_ip_token.set("visitor-identity")
    try:
        assert gateway._backend_headers() == {
            "Authorization": "Bearer test-token", "X-HF-Authorization": "Bearer test-token",
        }
        assert gateway._backend_state_key("https://example.invalid") == ("https://example.invalid", "service")
    finally:
        gateway._backend_ip_token.reset(context)


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

        async def record(request):
            requests.append(request)
            response = handler(request)
            return await response if inspect.isawaitable(response) else response

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


def gradio_config_response():
    return httpx.Response(200, json={"dependencies": [
        {"id": 7, "api_name": "generate_ranked_readout"},
        {"id": 11, "api_name": "retrieve_atlas"},
        {"id": 23, "api_name": "answer_atlas_question"},
    ]})


def queue_completion(event_id, data=None, error=None):
    message = {"msg": "process_completed", "event_id": event_id,
               "success": error is None,
               "output": {"data": data} if error is None else {"error": error}}
    return f"data: {json.dumps(message)}\n\n"


@pytest.mark.parametrize("ip_token", [None, "signed-visitor-token"])
def test_anonymous_generate_authenticates_upload_submission_and_stream(
    monkeypatch, backend_transport, local_quota, ip_token
):
    space = "https://private-inference.hf.space"
    monkeypatch.setattr(gateway, "INFERENCE_SPACE", space)
    outputs = [{"data": [[1, "COL1A1"]]}, "COL1A1", {}, "ready"]
    session_hash = None

    def respond(request):
        nonlocal session_hash
        assert request.url.host == "private-inference.hf.space"
        assert request.headers["x-hf-authorization"] == "Bearer test-token"
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.headers.get("x-ip-token") == ip_token
        if request.url.path == "/config":
            assert request.method == "GET"
            return gradio_config_response()
        if request.url.path == "/gradio_api/upload":
            assert request.method == "POST"
            return httpx.Response(200, json=["/tmp/local.png", "/tmp/context.png"])
        if request.url.path == "/gradio_api/queue/join":
            assert request.method == "POST"
            payload = json.loads(request.content)
            assert payload["data"][0]["path"] == "/tmp/local.png"
            assert payload["data"][1]["path"] == "/tmp/context.png"
            assert payload["data"][2:] == ["human", "kidney", 50]
            assert payload["fn_index"] == 7
            assert payload["event_data"] is None
            session_hash = payload["session_hash"]
            assert len(session_hash) == 32
            return httpx.Response(200, json={"event_id": "event-123"})
        if request.url.path == "/gradio_api/queue/data":
            assert request.method == "GET"
            assert request.url.params["session_hash"] == session_hash
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text=queue_completion("event-123", outputs),
            )
        raise AssertionError(f"Unexpected backend request: {request.method} {request.url}")

    requests = backend_transport(respond)
    response = TestClient(gateway.app).post(
        "/api/generate",
        headers={"X-IP-Token": ip_token} if ip_token else {},
        files={
            "local_image": ("local.png", b"png-local", "image/png"),
            "context_image": ("context.png", b"png-context", "image/png"),
        },
        data={"species": "human", "organ": "kidney", "top_k": "50"},
    )
    assert response.status_code == 200
    assert response.json()["data"] == outputs
    assert [request.method for request in requests] == ["POST", "GET", "POST", "GET"]
    assert "test-token" not in response.text


@pytest.mark.parametrize("ip_token", [None, "signed-health-visitor"])
def test_anonymous_health_forwards_platform_identity_and_owner_authentication(
    monkeypatch, backend_transport, local_quota, ip_token
):
    monkeypatch.setattr(gateway, "INFERENCE_SPACE", "https://private-inference.hf.space")
    monkeypatch.setattr(gateway, "REASONING_SPACE", "https://private-agent.hf.space")

    def respond(request):
        assert request.url.path == "/config"
        assert request.headers["x-hf-authorization"] == "Bearer test-token"
        assert request.headers.get("x-ip-token") == ip_token
        return httpx.Response(200, json={"dependencies": []})

    requests = backend_transport(respond)
    response = TestClient(gateway.app).get(
        "/api/health", headers={"X-IP-Token": ip_token} if ip_token else {}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "available"
    assert response.json()["request_identity_forwarded"] is bool(ip_token)
    assert response.json()["budget_source"] == "application_ledger"
    assert len(requests) == 2
    assert "signed-health-visitor" not in response.text
    assert gateway._backend_ip_token.get() == ""


@pytest.mark.anyio
async def test_parallel_requests_keep_visitor_identity_through_submission_and_stream(
    backend_transport, local_quota
):
    original_client = httpx.AsyncClient
    both_submitted = asyncio.Event()
    submitted = set()
    sessions = {}

    async def respond(request):
        assert request.headers["x-hf-authorization"] == "Bearer test-token"
        identity = request.headers.get("x-ip-token")
        if request.url.path == "/config":
            return gradio_config_response()
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["fn_index"] == 23
            query = payload["data"][0]
            assert payload["session_hash"] not in sessions
            sessions[payload["session_hash"]] = query
            assert identity == (None if query == "no-token" else query)
            if identity:
                submitted.add(identity)
                if len(submitted) == 2:
                    both_submitted.set()
                await both_submitted.wait()
            return httpx.Response(200, json={"event_id": query})
        assert request.url.path == "/gradio_api/queue/data"
        event = sessions[request.url.params["session_hash"]]
        assert identity == (None if event == "no-token" else event)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=queue_completion(event, ["completed"]),
        )

    requests = backend_transport(respond)
    # A request with no platform header must not inherit an enclosing identity.
    context = gateway._backend_ip_token.set("outer-context")
    try:
        async with original_client(
            transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
        ) as client:
            async def call(identity):
                headers = {"X-HistAgent-Session": identity}
                if identity != "no-token":
                    headers["X-IP-Token"] = identity
                response = await client.post(
                    "/api/call", headers=headers,
                    json={"service": "reasoning", "api_name": "answer_atlas_question",
                          "data": [identity, [], {}]},
                )
                assert gateway._backend_ip_token.get() == "outer-context"
                return response

            responses = await asyncio.wait_for(
                asyncio.gather(call("visitor-a"), call("visitor-b")), timeout=3
            )
            responses.append(await call("no-token"))
        assert all(response.status_code == 200 for response in responses)
        assert len(requests) == 9
        assert gateway._backend_ip_token.get() == "outer-context"
    finally:
        gateway._backend_ip_token.reset(context)


def test_backend_rate_limit_and_health_cache_are_isolated_by_visitor(
    monkeypatch, backend_transport, local_quota
):
    monkeypatch.setattr(gateway, "INFERENCE_SPACE", "https://private-inference.hf.space")
    monkeypatch.setattr(gateway, "REASONING_SPACE", "https://private-agent.hf.space")

    def respond(request):
        if request.headers.get("x-ip-token") == "limited-visitor":
            return httpx.Response(429, headers={"Retry-After": "60"}, text="Rate limited")
        return httpx.Response(200, json={"dependencies": []})

    requests = backend_transport(respond)
    client = TestClient(gateway.app)
    limited = client.get("/api/health", headers={"X-IP-Token": "limited-visitor"})
    assert limited.json()["status"] == "degraded"
    assert len(requests) == 2

    healthy = client.get("/api/health", headers={"X-IP-Token": "healthy-visitor"})
    assert healthy.json()["status"] == "available"
    assert len(requests) == 4
    assert client.get("/api/health").json()["status"] == "available"
    assert len(requests) == 6

    for identity, expected in [("limited-visitor", "degraded"), ("healthy-visitor", "available")]:
        assert client.get("/api/health", headers={"X-IP-Token": identity}).json()["status"] == expected
    assert client.get("/api/health").json()["status"] == "available"
    assert len(requests) == 6

    limited_hash = hashlib.sha256(b"limited-visitor").hexdigest()
    healthy_hash = hashlib.sha256(b"healthy-visitor").hexdigest()
    for space in (gateway.INFERENCE_SPACE, gateway.REASONING_SPACE):
        assert (space, limited_hash) in gateway._backend_failures
        assert (space, healthy_hash) not in gateway._backend_failures
        assert (space, healthy_hash) in gateway._backend_health
        assert (space, "service") in gateway._backend_health
    assert "limited-visitor" not in repr(gateway._backend_failures)
    assert "healthy-visitor" not in repr(gateway._backend_health)


@pytest.mark.anyio
async def test_failed_call_restores_context_and_does_not_block_an_unidentified_visitor(
    backend_transport, local_quota
):
    original_client = httpx.AsyncClient

    def respond(request):
        if request.headers.get("x-ip-token") == "limited-visitor":
            return httpx.Response(429, headers={"Retry-After": "60"}, text="Rate limited")
        assert "x-ip-token" not in request.headers
        if request.url.path == "/config":
            return gradio_config_response()
        if request.method == "POST":
            return httpx.Response(200, json={"event_id": "healthy-job"})
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            text=queue_completion("healthy-job", ["completed"]),
        )

    requests = backend_transport(respond)
    context = gateway._backend_ip_token.set("outer-context")
    payload = {"service": "reasoning", "api_name": "answer_atlas_question", "data": ["Question", [], {}]}
    try:
        async with original_client(
            transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver"
        ) as client:
            failed = await client.post("/api/call", headers={"X-IP-Token": "limited-visitor"}, json=payload)
            assert failed.status_code == 429
            assert gateway._backend_ip_token.get() == "outer-context"
            repeated = await client.post("/api/call", headers={"X-IP-Token": "limited-visitor"}, json=payload)
            assert repeated.status_code == 429
            assert len(requests) == 1
            healthy = await client.post("/api/call", json=payload)
            assert healthy.status_code == 200
            assert healthy.json()["data"] == ["completed"]
        assert len(requests) == 4
        assert gateway._backend_ip_token.get() == "outer-context"
    finally:
        gateway._backend_ip_token.reset(context)


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


@pytest.mark.parametrize("remaining, expected", [(120, "degraded"), (180, "available")])
def test_health_requires_enough_budget_for_every_advertised_service(
    backend_transport, local_quota, remaining, expected
):
    local_quota["used_seconds"] = gateway.GPU_QUOTA_SECONDS - remaining
    backend_transport(lambda _: httpx.Response(200, json={"dependencies": []}))

    response = TestClient(gateway.app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["remaining_gpu_seconds"] == remaining
    assert all(backend["status"] == "reachable" for backend in response.json()["backends"].values())
    assert response.json()["status"] == expected


@pytest.mark.anyio
async def test_submission_timeout_keeps_budget_and_does_not_repeat_the_job(
    backend_transport, local_quota
):
    def respond(request):
        if request.url.path == "/config":
            return gradio_config_response()
        assert request.url.path == "/gradio_api/queue/join"
        raise httpx.ReadTimeout("Reply lost after submission", request=request)

    requests = backend_transport(respond)
    previous = gateway._submission_state.get()
    with pytest.raises(gateway.HTTPException):
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
        )
    assert [request.method for request in requests] == ["GET", "POST"]
    assert local_quota["used_seconds"] == gateway.GPU_RESERVATIONS["answer_atlas_question"]
    assert local_quota["calls"] == 1
    assert gateway._submission_state.get() == previous


@pytest.mark.anyio
@pytest.mark.parametrize("status", [502, 503, 504])
async def test_ambiguous_submission_http_failure_keeps_budget_without_resubmitting(
    backend_transport, local_quota, status
):
    def respond(request):
        if request.url.path == "/config":
            return gradio_config_response()
        assert request.url.path == "/gradio_api/queue/join"
        return httpx.Response(status, text="Upstream response unavailable")

    requests = backend_transport(respond)
    with pytest.raises(gateway.HTTPException):
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
        )
    assert [request.method for request in requests] == ["GET", "POST"]
    assert local_quota["used_seconds"] == gateway.GPU_RESERVATIONS["answer_atlas_question"]
    assert local_quota["calls"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("stream_failure", ["disconnect", "http_429"])
async def test_accepted_job_keeps_budget_when_its_response_stream_fails(
    backend_transport, local_quota, stream_failure
):
    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"msg":"heartbeat"}\n\n'
            raise httpx.ReadError("Connection closed while the GPU job may still be running")

    def respond(request):
        if request.url.path == "/config":
            return gradio_config_response()
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
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert local_quota["used_seconds"] == gateway.GPU_RESERVATIONS["answer_atlas_question"]
    assert local_quota["calls"] == 1


@pytest.mark.anyio
async def test_explicit_http_rejection_refunds_the_unused_gpu_reservation(
    backend_transport, local_quota
):
    def respond(request):
        if request.url.path == "/config":
            return gradio_config_response()
        assert request.url.path == "/gradio_api/queue/join"
        return httpx.Response(429, headers={"Retry-After": "60"}, text="We had to rate limit you.")

    requests = backend_transport(respond)
    with pytest.raises(gateway.HTTPException) as caught:
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
        )
    assert caught.value.detail["code"] == "backend_rate_limited"
    assert [request.method for request in requests] == ["GET", "POST"]
    assert local_quota["used_seconds"] == 0
    assert local_quota["calls"] == 0


@pytest.mark.anyio
async def test_gpu_scheduler_quota_rejection_refunds_the_unused_reservation(
    backend_transport, local_quota
):
    def respond(request):
        if request.url.path == "/config":
            return gradio_config_response()
        if request.method == "POST":
            return httpx.Response(200, json={"event_id": "queued-job"})
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            # Gradio's simple /call stream loses this error by reading only
            # output.data. The rich queue protocol must preserve output.error.
            text=queue_completion("queued-job", error="You have exceeded your Pro ZeroGPU quota (60s requested vs. 0s left)."),
        )

    requests = backend_transport(respond)
    with pytest.raises(gateway.HTTPException) as caught:
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
        )
    assert caught.value.detail["code"] == "gpu_quota_exhausted"
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert local_quota["used_seconds"] == 0
    assert local_quota["calls"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("message, platform_rejection", [
    ("The requested GPU duration (270s) is larger than the maximum allowed", True),
    ("The requested GPU duration (180s) is larger than the maximum allowed.", True),
    ("  THE REQUESTED GPU DURATION (60.5s) IS LARGER THAN THE MAXIMUM ALLOWED.  ", True),
    ("Model execution duration exceeded the configured maximum.", False),
    ("Generation failed: The requested GPU duration (270s) is larger than the maximum allowed", False),
    ("The requested GPU duration (270s) is larger than the maximum allowed. Model already ran.", False),
])
async def test_only_exact_platform_duration_rejection_refunds_budget(
    monkeypatch, backend_transport, local_quota, message, platform_rejection
):
    refund_states = []
    original_refund = gateway._refund_gpu_seconds

    def refund(api_name):
        refund_states.append(gateway._submission_state.get())
        return original_refund(api_name)

    monkeypatch.setattr(gateway, "_refund_gpu_seconds", refund)

    def respond(request):
        if request.url.path == "/config":
            return gradio_config_response()
        if request.url.path == "/gradio_api/queue/join":
            return httpx.Response(200, json={"event_id": "duration-job"})
        assert request.url.path == "/gradio_api/queue/data"
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            text=queue_completion("duration-job", error=message),
        )

    requests = backend_transport(respond)
    previous = gateway._submission_state.get()
    with pytest.raises(gateway.HTTPException) as caught:
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "retrieve_atlas", ["query"]
        )
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert caught.value.status_code == (503 if platform_rejection else 502)
    assert caught.value.detail["code"] == (
        "backend_duration_rejected" if platform_rejection else "backend_generation_error"
    )
    assert refund_states == (["rejected"] if platform_rejection else [])
    assert local_quota["used_seconds"] == (
        0 if platform_rejection else gateway.GPU_RESERVATIONS["retrieve_atlas"]
    )
    assert local_quota["calls"] == (0 if platform_rejection else 1)
    assert gateway._submission_state.get() == previous


@pytest.mark.anyio
@pytest.mark.parametrize("stream_text", [
    "data: {malformed-json}\n\n",
    queue_completion("another-visitors-job", ["Do not return this answer"]),
    'data: {"msg":"process_completed","event_id":"accepted-job","success":true,"output":{"data":null}}\n\n',
])
async def test_invalid_queue_stream_keeps_budget_without_duplicate_submission(
    backend_transport, local_quota, stream_text
):
    def respond(request):
        if request.url.path == "/config":
            return gradio_config_response()
        if request.url.path == "/gradio_api/queue/join":
            return httpx.Response(200, json={"event_id": "accepted-job"})
        assert request.url.path == "/gradio_api/queue/data"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=stream_text)

    requests = backend_transport(respond)
    with pytest.raises(gateway.HTTPException):
        await gateway._call_with_reservation(
            "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
        )
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert local_quota["used_seconds"] == gateway.GPU_RESERVATIONS["answer_atlas_question"]
    assert local_quota["calls"] == 1


@pytest.mark.anyio
async def test_queue_uses_dependency_index_fallback_and_ignores_heartbeat(backend_transport):
    def respond(request):
        if request.url.path == "/config":
            return httpx.Response(200, json={"dependencies": [
                {"id": 99, "api_name": "unrelated"},
                {"api_name": "answer_atlas_question"},
            ]})
        if request.url.path == "/gradio_api/queue/join":
            assert json.loads(request.content)["fn_index"] == 1
            return httpx.Response(200, json={"event_id": "own-job"})
        assert request.url.path == "/gradio_api/queue/data"
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            text=('data: {"msg":"heartbeat"}\n\n'
                  + queue_completion("own-job", ["Correct answer"])),
        )

    requests = backend_transport(respond)
    assert await gateway._call_gradio(
        "https://private-agent.hf.space", "answer_atlas_question", ["Question", [], {}]
    ) == ["Correct answer"]
    assert [request.method for request in requests] == ["GET", "POST", "GET"]


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
            yield queue_completion("earlier-job", ["Completed answer"]).encode()

    def respond(request):
        if request.url.path == "/config":
            return gradio_config_response()
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
    assert len(requests) == 3
