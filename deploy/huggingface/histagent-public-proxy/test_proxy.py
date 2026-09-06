from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


SPEC = importlib.util.spec_from_file_location("histagent_public_proxy", Path(__file__).with_name("app.py"))
proxy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = proxy
SPEC.loader.exec_module(proxy)

TOKEN = "test_owner_credential_never_a_live_token"
ORIGIN = "https://histagent.bio"
BASE_HEADERS = {"Origin": ORIGIN, "Content-Type": "application/json"}
BODY = b'{"service":"reasoning","api_name":"retrieve_atlas","data":["test","human","Any","__ready__",5]}'


def make_app(handler=None, *, owner="wli14", enabled=True, token=TOKEN, identity_handler=None, **kwargs):
    requests = []

    async def respond(request):
        requests.append(request)
        if str(request.url) == proxy.WHOAMI_URL:
            if identity_handler:
                result = identity_handler(request)
                return await result if hasattr(result, "__await__") else result
            return httpx.Response(200, json={"name": owner, "private_metadata": "discard-me"}, headers={"Set-Cookie": "identity_cookie=private"})
        if handler:
            result = handler(request)
            return await result if hasattr(result, "__await__") else result
        return httpx.Response(200, json={"data": ["ok"]})

    app = proxy.create_app(proxy.Settings(enabled=enabled, token=token), transport=httpx.MockTransport(respond), **kwargs)
    return app, requests


def call(client, *, path="/api/call", headers=None, content=BODY):
    return client.post(path, headers={**BASE_HEADERS, **(headers or {})}, content=content)


def model_requests(requests):
    return [request for request in requests if str(request.url) != proxy.WHOAMI_URL]


def test_startup_checks_account_once_and_exposes_only_owner_in_health():
    app, requests = make_app(lambda _: httpx.Response(200, json={"status": "available", "remaining_gpu_seconds": 2400}))
    with TestClient(app) as client:
        assert len(requests) == 1
        assert str(requests[0].url) == proxy.WHOAMI_URL
        assert requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
        for _ in range(2):
            response = client.get("/api/health", headers={"Origin": ORIGIN})
            assert response.status_code == 200
            assert response.json() == {"status": "available", "remaining_gpu_seconds": 2400, "proxy_owner": "wli14"}
            assert TOKEN not in response.text
            assert "private_metadata" not in response.text
        assert len([r for r in requests if str(r.url) == proxy.WHOAMI_URL]) == 1
    assert TOKEN not in repr(proxy.Settings(enabled=True, token=TOKEN))


@pytest.mark.parametrize("owner", ["wli13", "other-account", "WLI14", None])
def test_wrong_account_fails_closed_without_revealing_identity(owner):
    app, requests = make_app(owner=owner)
    with TestClient(app) as client:
        response = call(client)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "proxy_owner_unverified"
        assert owner is None or owner not in response.text
        assert not model_requests(requests)


@pytest.mark.parametrize("token", ["", "bad\ntoken", "bad token", "x" * 4097])
def test_missing_or_malformed_token_makes_no_network_request(token):
    app, requests = make_app(token=token)
    with TestClient(app) as client:
        assert call(client).status_code == 503
        assert requests == []


@pytest.mark.parametrize("identity_response", [
    lambda _: httpx.Response(401, json={"error": "invalid"}),
    lambda _: httpx.Response(302, headers={"Location": "https://attacker.example/"}),
    lambda _: httpx.Response(200, text="not JSON"),
    lambda _: httpx.Response(200, json=["wli14"]),
    lambda _: httpx.Response(200, content=b"x" * (proxy.MAX_METADATA_BYTES + 1)),
])
def test_failed_identity_response_does_not_retry_or_enable_api(identity_response):
    app, requests = make_app(identity_handler=identity_response)
    with TestClient(app) as client:
        assert call(client).status_code == 503
        assert len(requests) == 1


def test_identity_transport_failure_is_sanitized_and_checked_once(caplog):
    def unavailable(_):
        raise httpx.ConnectError(f"private error {TOKEN}")

    app, requests = make_app(identity_handler=unavailable)
    with TestClient(app) as client:
        for _ in range(3):
            response = call(client)
            assert response.status_code == 503
            assert TOKEN not in response.text
        assert len(requests) == 1
    assert TOKEN not in caplog.text


def test_disabled_mode_can_confirm_owner_without_submitting_and_root_redirects():
    app, requests = make_app(enabled=False)
    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/api/health", headers={"Origin": ORIGIN})
        assert response.status_code == 503
        assert response.json()["proxy_owner"] == "wli14"
        assert response.json()["detail"]["code"] == "proxy_disabled"
        assert call(client).status_code == 503
        root = client.get("/")
        assert root.status_code == 307
        assert root.headers["Location"] == "https://histagent.bio/"
        assert not model_requests(requests)


def test_environment_default_is_disabled_and_never_uses_hf_token_fallback(monkeypatch):
    monkeypatch.delenv("WLI14_HF_TOKEN", raising=False)
    monkeypatch.delenv("HISTAGENT_PROXY_ENABLED", raising=False)
    monkeypatch.setenv("HF_TOKEN", "unapproved-fallback")
    assert proxy.Settings.from_environment() == proxy.Settings(enabled=False, token="")


def test_fixed_routes_build_owner_headers_without_incoming_or_previous_cookies():
    def respond(request):
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["Origin"] == ORIGIN
        assert request.headers["X-HistAgent-Session"] == "browser-session-123"
        for name in ["Cookie", "X-HF-Authorization", "X-IP-Token", "X-Forwarded-For", "X-Real-IP", "Forwarded", "CF-Connecting-IP", "X-Arbitrary"]:
            assert name not in request.headers
        expected = {"host", "accept", "accept-encoding", "connection", "user-agent", "authorization", "origin", "x-histagent-session"}
        if request.method == "POST":
            expected |= {"content-type", "content-length"}
        assert set(request.headers) == expected
        assert request.extensions["timeout"] == {"connect": 15.0, "read": 300.0, "write": 30.0, "pool": 15.0}
        return httpx.Response(200, json={"data": []}, headers={"Set-Cookie": "upstream_cookie=private"})

    app, requests = make_app(respond)
    incoming = {"Authorization": "Bearer visitor", "Cookie": "visitor=private", "X-HF-Authorization": "visitor-hf",
        "X-IP-Token": "signed-visitor", "X-Forwarded-For": "192.0.2.1", "X-Real-IP": "192.0.2.2",
        "Forwarded": "for=192.0.2.3", "CF-Connecting-IP": "192.0.2.4", "X-Arbitrary": "untrusted", "X-HistAgent-Session": "browser-session-123"}
    with TestClient(app) as client:
        assert call(client, headers=incoming).status_code == 200
        assert call(client, path="/api/generate", headers={**incoming, "Content-Type": "multipart/form-data; boundary=test"}).status_code == 200
        assert client.get("/api/health", headers={**incoming, "Origin": ORIGIN}).status_code == 200
        assert call(client, headers=incoming).status_code == 200
    assert {str(r.url) for r in model_requests(requests)} == {f"{proxy.UPSTREAM_ORIGIN}{path}" for path in proxy.ROUTES}
    assert requests[0].extensions["timeout"] == {"connect": 15.0, "read": 15.0, "write": 15.0, "pool": 15.0}


def test_response_drops_secret_headers_and_preserves_safe_retry_after():
    app, _ = make_app(lambda _: httpx.Response(200, json={"data": []}, headers={
        "Authorization": TOKEN, "X-HF-Authorization": TOKEN, "X-IP-Token": "private-identity", "Set-Cookie": "private-cookie",
        "X-Secret": TOKEN, "Location": "https://elsewhere.example", "Retry-After": "90",
        "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Credentials": "true", "Cache-Control": "public",
    }))
    with TestClient(app) as client:
        response = call(client)
        for name in ["Authorization", "X-HF-Authorization", "X-IP-Token", "Set-Cookie", "X-Secret", "Location", "Access-Control-Allow-Credentials"]:
            assert name not in response.headers
        assert response.headers["Retry-After"] == "90"
        assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
        assert response.headers["Cache-Control"] == "no-store"
        assert TOKEN not in str(response.headers)


@pytest.mark.parametrize("origin", [None, "null", "http://histagent.bio", "https://histagent.bio/", "https://histagent.bio.attacker.example", "http://localhost:4000"])
def test_disallowed_and_missing_origins_are_rejected(origin):
    app, requests = make_app()
    with TestClient(app) as client:
        response = client.post("/api/call", content=BODY, headers={} if origin is None else {"Origin": origin})
        assert response.status_code == 403
        assert "Access-Control-Allow-Origin" not in response.headers
        assert not model_requests(requests)


def test_www_origin_is_allowed():
    app, _ = make_app()
    with TestClient(app) as client:
        response = call(client, headers={"Origin": "https://www.histagent.bio"})
        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == "https://www.histagent.bio"


@pytest.mark.parametrize("path", ["/config", "/docs", "/openapi.json", "/api/call/", "/api%2fcall", "/api/call?url=https://attacker.example", "/gradio_api/call/run", "/https://attacker.example"])
def test_unlisted_paths_and_query_strings_never_contact_model(path):
    app, requests = make_app()
    with TestClient(app) as client:
        assert call(client, path=path).status_code == 404
        assert not model_requests(requests)


@pytest.mark.parametrize("method,path", [("GET", "/api/call"), ("PUT", "/api/generate"), ("DELETE", "/api/call"), ("POST", "/api/health"), ("HEAD", "/api/health")])
def test_incorrect_route_methods_fail_closed(method, path):
    app, requests = make_app()
    with TestClient(app) as client:
        response = client.request(method, path, headers={"Origin": ORIGIN})
        assert response.status_code == 405
        assert "OPTIONS" in response.headers["Allow"]
        assert not model_requests(requests)


def test_preflight_works_while_disabled_and_restricts_methods_and_headers():
    app, requests = make_app(enabled=False)
    with TestClient(app) as client:
        for path, method in proxy.ROUTES.items():
            response = client.options(path, headers={"Origin": ORIGIN, "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Content-Type, x-HistAgent-Session"})
            assert response.status_code == 204
            assert not response.content
            assert response.headers["Access-Control-Allow-Methods"] == f"{method}, OPTIONS"
            assert "Access-Control-Request-Headers" in response.headers["Vary"]
        for headers in [{"Access-Control-Request-Method": "DELETE"},
            {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization"},
            {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-IP-Token"}, {}]:
            assert client.options("/api/call", headers={"Origin": ORIGIN, **headers}).status_code == 403
        assert not model_requests(requests)


@pytest.mark.parametrize("session", ["short", "contains spaces", "a" * 97])
def test_invalid_session_header_is_omitted(session):
    app, requests = make_app()
    with TestClient(app) as client:
        assert call(client, headers={"X-HistAgent-Session": session}).status_code == 200
        assert "X-HistAgent-Session" not in requests[-1].headers


def test_original_multipart_bytes_and_boundary_are_preserved():
    body = b'--test\r\nContent-Disposition: form-data; name="local_image"; filename="local.png"\r\nContent-Type: image/png\r\n\r\nfixture\r\n--test--\r\n'
    app, requests = make_app()
    with TestClient(app) as client:
        assert call(client, path="/api/generate", headers={"Content-Type": "multipart/form-data; boundary=test"}, content=body).status_code == 200
        assert requests[-1].content == body
        assert requests[-1].headers["Content-Type"] == "multipart/form-data; boundary=test"


@pytest.mark.parametrize("path,headers,status", [
    ("/api/call", {"Content-Type": "text/plain"}, 415),
    ("/api/call", {"Content-Encoding": "gzip"}, 415),
    ("/api/generate", {"Content-Type": "multipart/form-data"}, 415),
    ("/api/generate", {"Content-Type": "application/json"}, 415),
    ("/api/call", {"Content-Length": "-1"}, 400),
    ("/api/call", {"Content-Length": str(proxy.BODY_LIMITS["/api/call"] + 1)}, 413),
    ("/api/generate", {"Content-Type": "multipart/form-data; boundary=test", "Content-Length": str(proxy.BODY_LIMITS["/api/generate"] + 1)}, 413),
])
def test_media_and_declared_size_checks_happen_before_submission(path, headers, status):
    app, requests = make_app()
    with TestClient(app) as client:
        for _ in range(3):
            assert call(client, path=path, headers=headers).status_code == status
            assert app.state.admission.active == 0
        assert not model_requests(requests)


@pytest.mark.parametrize("declared", [None, "1"])
def test_streamed_size_limit_cannot_be_bypassed_by_content_length(declared):
    async def scenario():
        app, requests = make_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://proxy.test") as client:
                async def chunks():
                    yield b"x" * proxy.BODY_LIMITS["/api/call"]
                    yield b"x"
                headers = {**BASE_HEADERS, **({"Content-Length": declared} if declared is not None else {})}
                response = await client.post("/api/call", headers=headers, content=chunks())
                assert response.status_code == 413
                assert app.state.admission.active == 0
                assert not model_requests(requests)
    asyncio.run(scenario())


def test_exact_json_limit_is_accepted_and_empty_body_rejected():
    app, requests = make_app()
    with TestClient(app) as client:
        content = b" " * (proxy.BODY_LIMITS["/api/call"] - 2) + b"{}"
        assert call(client, content=content).status_code == 200
        assert requests[-1].content == content
        assert call(client, content=b"").status_code == 400
        assert app.state.admission.active == 0


@pytest.mark.parametrize("status,payload", [
    (429, {"detail": {"code": "gpu_quota_exhausted", "message": "Daily GPU quota reached. Wait for the platform quota to reset.", "retry_after_seconds": 300}}),
    (422, {"detail": [{"msg": "Field required", "loc": ["body", "service"]}]}),
    (503, {"detail": {"code": "backend_not_ready", "message": "Model is starting"}}),
])
def test_backend_json_errors_preserve_status_and_message_without_retry(status, payload):
    app, requests = make_app(lambda _: httpx.Response(status, json=payload, headers={"Retry-After": "300"}))
    with TestClient(app) as client:
        for _ in range(3):
            response = call(client)
            assert response.status_code == status
            assert response.json() == payload
            assert response.headers["Retry-After"] == "300"
            assert app.state.admission.active == 0
        assert len(model_requests(requests)) == 3


@pytest.mark.parametrize("upstream,expected,code", [
    (lambda _: httpx.Response(307, headers={"Location": "https://attacker.example"}), 502, "upstream_redirect_rejected"),
    (lambda _: httpx.Response(429, text="<html>private diagnostic</html>", headers={"Retry-After": "90"}), 429, "backend_rate_limited"),
    (lambda _: httpx.Response(200, text="unexpected HTML"), 502, "upstream_unavailable"),
    (lambda _: httpx.Response(200, content=b"not-json", headers={"Content-Type": "application/json"}), 502, "upstream_invalid_response"),
    (lambda _: httpx.Response(200, json={"data": [TOKEN]}), 502, "upstream_invalid_response"),
])
def test_redirects_html_invalid_json_and_secret_echo_are_sanitized(upstream, expected, code):
    app, requests = make_app(upstream)
    with TestClient(app) as client:
        for _ in range(3):
            response = call(client)
            assert response.status_code == expected
            assert response.json()["detail"]["code"] == code
            assert "Location" not in response.headers
            assert TOKEN not in response.text
            assert "private diagnostic" not in response.text
            assert app.state.admission.active == 0
        assert len(model_requests(requests)) == 3


@pytest.mark.parametrize("exception,status", [(httpx.ReadTimeout, 504), (httpx.ConnectError, 502), (RuntimeError, 502)])
def test_upstream_exceptions_are_not_logged_exposed_or_retried(exception, status, caplog):
    def unavailable(_):
        raise exception(f"private diagnostic {TOKEN}")
    app, requests = make_app(unavailable)
    with TestClient(app) as client:
        for _ in range(3):
            response = call(client)
            assert response.status_code == status
            assert TOKEN not in response.text
            assert app.state.admission.active == 0
        assert len(model_requests(requests)) == 3
    assert TOKEN not in caplog.text


def test_oversized_upstream_json_is_rejected(monkeypatch):
    monkeypatch.setattr(proxy, "MAX_RESPONSE_BYTES", 32)
    app, _ = make_app(lambda _: httpx.Response(200, json={"data": ["x" * 100]}))
    with TestClient(app) as client:
        response = call(client)
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "upstream_invalid_response"
        assert app.state.admission.active == 0


def test_two_requests_hold_admission_through_upload_and_submission_then_release():
    async def scenario():
        allow_body = asyncio.Event()
        submitted = asyncio.Event()
        finish = asyncio.Event()
        calls = 0

        async def upstream(request):
            nonlocal calls
            if request.method == "GET":
                return httpx.Response(200, json={"status": "available"})
            calls += 1
            if calls == 2:
                submitted.set()
            await finish.wait()
            return httpx.Response(200, json={"data": []})

        app, requests = make_app(upstream)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://proxy.test") as client:
                async def chunks():
                    await allow_body.wait()
                    yield BODY
                running = [asyncio.create_task(client.post("/api/call", headers=BASE_HEADERS, content=chunks())) for _ in range(2)]
                try:
                    await asyncio.sleep(0)
                    assert app.state.admission.active == 2
                    rejected_read_count = 0
                    async def rejected_body():
                        nonlocal rejected_read_count
                        rejected_read_count += 1
                        yield BODY
                    rejected = await client.post("/api/call", headers=BASE_HEADERS, content=rejected_body())
                    assert rejected.status_code == 429
                    assert rejected.json()["detail"]["code"] == "proxy_busy"
                    assert rejected_read_count == 0
                    assert not model_requests(requests)
                    allow_body.set()
                    await asyncio.wait_for(submitted.wait(), 2)
                    assert calls == 2
                    rejected = await client.post("/api/call", headers=BASE_HEADERS, content=BODY)
                    assert rejected.json()["detail"]["code"] == "proxy_busy"
                    assert (await client.get("/api/health", headers={"Origin": ORIGIN})).status_code == 200
                    assert (await client.options("/api/call", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})).status_code == 204
                finally:
                    allow_body.set()
                    finish.set()
                    await asyncio.gather(*running)
                assert app.state.admission.active == 0
                assert (await client.post("/api/call", headers=BASE_HEADERS, content=BODY)).status_code == 200
                assert calls == 3
    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_phase", ["upload", "upstream"])
def test_request_cancellation_releases_admission(cancel_phase):
    async def scenario():
        blocked = asyncio.Event()
        started = asyncio.Event()

        async def upstream(_):
            started.set()
            await blocked.wait()
            return httpx.Response(200, json={"data": []})

        app, _ = make_app(upstream)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://proxy.test") as client:
                async def upload():
                    if cancel_phase == "upload":
                        started.set()
                        await blocked.wait()
                    yield BODY
                task = asyncio.create_task(client.post("/api/call", headers=BASE_HEADERS, content=upload()))
                await asyncio.wait_for(started.wait(), 2)
                assert app.state.admission.active == 1
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert app.state.admission.active == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("location", ["value", "key"])
def test_escaped_credential_is_rejected_after_json_decoding(location):
    escaped = TOKEN.replace("_", "\\u005f")
    body = ('{"data":["' + escaped + '"]}') if location == "value" else ('{"' + escaped + '":"unexpected"}')
    assert TOKEN not in body
    app, _ = make_app(lambda _: httpx.Response(200, content=body, headers={"Content-Type": "application/json"}))
    with TestClient(app) as client:
        response = call(client)
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "upstream_invalid_response"
        assert TOKEN not in response.text
        assert app.state.admission.active == 0


def test_slow_incomplete_upload_times_out_before_submission_and_releases_slots(monkeypatch):
    monkeypatch.setattr(proxy, "BODY_READ_TIMEOUT_SECONDS", 0.01)

    async def scenario():
        app, requests = make_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://proxy.test") as client:
                async def unfinished():
                    yield b"{"
                    await asyncio.Event().wait()
                pending = [client.post("/api/call", headers=BASE_HEADERS, content=unfinished()) for _ in range(2)]
                responses = await asyncio.gather(*pending)
                assert [response.status_code for response in responses] == [408, 408]
                assert all(response.json()["detail"]["code"] == "upload_timeout" for response in responses)
                assert app.state.admission.active == 0
                assert not model_requests(requests)
                assert (await client.post("/api/call", headers=BASE_HEADERS, content=BODY)).status_code == 200
    asyncio.run(scenario())


def test_transient_owner_verification_recovers_after_cooldown_with_one_serialized_probe():
    async def scenario():
        now = [0.0]
        checks = 0
        probe_started = asyncio.Event()
        complete_probe = asyncio.Event()

        async def identity(_):
            nonlocal checks
            checks += 1
            if checks == 1:
                raise httpx.ConnectError("Transient startup failure")
            probe_started.set()
            await complete_probe.wait()
            return httpx.Response(200, json={"name": "wli14"})

        app, requests = make_app(identity_handler=identity, clock=lambda: now[0])
        async with app.router.lifespan_context(app):
            assert checks == 1
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://proxy.test") as client:
                for current in [0.0, 59.9]:
                    now[0] = current
                    response = await client.get("/api/health", headers={"Origin": ORIGIN})
                    assert response.status_code == 503
                    assert checks == 1
                    assert not model_requests(requests)
                now[0] = 60.0
                pending = [asyncio.create_task(client.get("/api/health", headers={"Origin": ORIGIN})) for _ in range(5)]
                try:
                    await asyncio.wait_for(probe_started.wait(), 2)
                    assert checks == 2
                    assert not model_requests(requests)
                finally:
                    complete_probe.set()
                responses = await asyncio.gather(*pending)
                assert all(response.status_code == 200 for response in responses)
                assert all(response.json()["proxy_owner"] == "wli14" for response in responses)
                assert checks == 2
                now[0] = 10000.0
                assert (await client.get("/api/health", headers={"Origin": ORIGIN})).status_code == 200
                assert checks == 2
    asyncio.run(scenario())
