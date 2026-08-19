from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import app as gateway


@pytest.fixture(autouse=True)
def configured_gateway(monkeypatch):
    monkeypatch.setattr(gateway, "HF_TOKEN", "test-token")
    gateway._recent_calls.clear()


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
