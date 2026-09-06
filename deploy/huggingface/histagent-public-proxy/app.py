from __future__ import annotations

import asyncio
import os
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from fastapi import FastAPI, Request
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, RedirectResponse, Response


UPSTREAM_ORIGIN = "https://wli14-histagent-agent.hf.space"
WHOAMI_URL = "https://huggingface.co/api/whoami-v2"
EXPECTED_OWNER = "wli14"
ALLOWED_ORIGINS = frozenset({"https://histagent.bio", "https://www.histagent.bio"})
ROUTES = {"/api/health": "GET", "/api/generate": "POST", "/api/call": "POST"}
BODY_LIMITS = {"/api/generate": 21 * 1024 * 1024, "/api/call": 1024 * 1024}
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024
ALLOWED_REQUEST_HEADERS = frozenset({"content-type", "x-histagent-session"})
UPSTREAM_TIMEOUT = httpx.Timeout(300.0, connect=15.0, write=30.0, pool=15.0)
IDENTITY_TIMEOUT = httpx.Timeout(15.0)
OWNER_CHECK_RETRY_SECONDS = 60.0
BODY_READ_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    token: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            enabled=os.environ.get("HISTAGENT_PROXY_ENABLED", "false") == "true",
            token=os.environ.get("WLI14_HF_TOKEN", "").strip(),
        )


class Admission:
    """One process/event loop, with no await between checking and reserving."""

    def __init__(self, limit: int = 2) -> None:
        self.limit = limit
        self.active = 0

    def acquire(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active -= 1


class ProxyFailure(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


def cors_headers(origin: str | None) -> dict[str, str]:
    headers = {"Cache-Control": "no-store", "Vary": "Origin", "X-Content-Type-Options": "nosniff"}
    if origin in ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Expose-Headers"] = "Retry-After"
    return headers


def failure(
    origin: str | None, status: int, code: str, message: str,
    *, headers: dict[str, str] | None = None, health_owner: str | None = None,
) -> JSONResponse:
    payload: dict[str, Any] = {"detail": {"code": code, "message": message}}
    if health_owner is not None:
        payload["proxy_owner"] = health_owner
    return JSONResponse(payload, status_code=status, headers={**cors_headers(origin), **(headers or {})})


def valid_token(token: str) -> bool:
    return bool(token) and len(token) <= 4096 and re.fullmatch(r"[\x21-\x7e]+", token) is not None


def client(transport: httpx.AsyncBaseTransport | None, timeout: httpx.Timeout) -> httpx.AsyncClient:
    # Fresh client for every exchange: no incoming or previous response cookie
    # jar, ambient proxy credentials, forwarded request context, or retry policy.
    return httpx.AsyncClient(
        transport=transport, timeout=timeout, follow_redirects=False, trust_env=False,
    )


async def read_response(response: httpx.Response, limit: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > limit:
            raise ProxyFailure(502, "upstream_invalid_response", "The HistAgent service returned an oversized response.")
        body.extend(chunk)
    return bytes(body)


async def verify_owner(settings: Settings, transport: httpx.AsyncBaseTransport | None) -> str | None:
    if not valid_token(settings.token):
        return None
    try:
        async with asyncio.timeout(15.0), client(transport, IDENTITY_TIMEOUT) as identity_client:
            async with identity_client.stream(
                "GET", WHOAMI_URL, headers={"Authorization": f"Bearer {settings.token}"},
            ) as response:
                if response.status_code != 200:
                    return None
                raw = await read_response(response, MAX_METADATA_BYTES)
                metadata = httpx.Response(200, content=raw).json()
                # Retain only the exact expected account name, never the full
                # whoami response, token metadata, or an unexpected identity.
                return EXPECTED_OWNER if isinstance(metadata, dict) and metadata.get("name") == EXPECTED_OWNER else None
    except Exception:
        # Keep the API closed; a later serialized readiness check may recover.
        return None


async def read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        if not re.fullmatch(r"[0-9]{1,12}", declared):
            raise ProxyFailure(400, "invalid_request", "Invalid request body length.")
        if int(declared) > limit:
            raise ProxyFailure(413, "request_too_large", "This request exceeds the upload limit.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise ProxyFailure(413, "request_too_large", "This request exceeds the upload limit.")
        body.extend(chunk)
    if not body:
        raise ProxyFailure(400, "invalid_request", "A request body is required.")
    # At most two body readers/submissions are admitted. No chunk collection is
    # retained alongside this bounded bytearray and its final immutable copy.
    return bytes(body)


def retry_after(headers: httpx.Headers) -> dict[str, str]:
    value = headers.get("Retry-After", "")
    if re.fullmatch(r"[0-9]{1,10}", value) or re.fullmatch(
        r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} [A-Z][a-z]{2} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT", value,
    ):
        return {"Retry-After": value}
    return {}


async def forward(request: Request, settings: Settings, owner: str, transport: httpx.AsyncBaseTransport | None) -> Response:
    path = request.url.path
    origin = request.headers["origin"]
    headers = {"Authorization": f"Bearer {settings.token}", "Origin": origin}
    session = request.headers.get("x-histagent-session", "")
    if re.fullmatch(r"[A-Za-z0-9._-]{8,96}", session):
        headers["X-HistAgent-Session"] = session
    body = None
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        supported = bool(re.match(r"application/json(?:\s*;|$)", content_type, re.I)) if path == "/api/call" else bool(
            re.match(r"multipart/form-data\s*;", content_type, re.I)
            and re.search(r'(?:^|;)\s*boundary=(?:"[^"\r\n]+"|[^;\s]+)(?:\s*;|\s*$)', content_type, re.I)
        )
        if not supported or len(content_type) > 512 or "\r" in content_type or "\n" in content_type or "content-encoding" in request.headers:
            raise ProxyFailure(415, "unsupported_media_type", "Use JSON for calls or multipart form data for image generation.")
        headers["Content-Type"] = content_type
        try:
            async with asyncio.timeout(BODY_READ_TIMEOUT_SECONDS):
                body = await read_body(request, BODY_LIMITS[path])
        except TimeoutError:
            raise ProxyFailure(408, "upload_timeout", "The request upload took too long. Please retry.") from None

    # This is a fresh HTTP request to one literal model host, not forwarding
    # the incoming ASGI request. In particular, never copy X-IP-Token, Cookie,
    # caller Authorization, X-HF-Authorization, or forwarding/IP headers.
    async with client(transport, UPSTREAM_TIMEOUT) as upstream_client:
        async with upstream_client.stream(
            request.method, f"{UPSTREAM_ORIGIN}{path}", headers=headers, content=body,
        ) as upstream:
            if upstream.status_code < 200 or 300 <= upstream.status_code < 400:
                raise ProxyFailure(502, "upstream_redirect_rejected", "The HistAgent service returned an unexpected redirect.")
            waiting = retry_after(upstream.headers)
            content_type = upstream.headers.get("content-type", "")
            if not re.match(r"application/(?:[a-z0-9.+-]+\+)?json(?:\s*;|$)", content_type, re.I):
                status = upstream.status_code if upstream.status_code >= 400 else 502
                return failure(origin, status, "backend_rate_limited" if status == 429 else "upstream_unavailable",
                    "The model host is temporarily rate-limiting requests." if status == 429 else "The HistAgent service returned an unexpected response.", headers=waiting)
            raw = await read_response(upstream, MAX_METADATA_BYTES if path == "/api/health" else MAX_RESPONSE_BYTES)
            # Fail closed if upstream accidentally echoes the service credential.
            if settings.token.encode() in raw:
                raise ProxyFailure(502, "upstream_invalid_response", "The HistAgent service returned an invalid response.")
            try:
                payload = httpx.Response(200, content=raw).json()
            except ValueError:
                raise ProxyFailure(502, "upstream_invalid_response", "The HistAgent service returned invalid JSON.") from None
            if path == "/api/health":
                if not isinstance(payload, dict):
                    raise ProxyFailure(502, "upstream_invalid_response", "The HistAgent service returned invalid health metadata.")
                payload["proxy_owner"] = owner
            # Rebuild headers; discard cookies, credentials, signed identities,
            # location and diagnostics. Preserve backend error codes and wording.
            outgoing = JSONResponse(payload, status_code=upstream.status_code, headers={**cors_headers(origin), **waiting})
            # JSON decoding can reconstruct a token from escaped characters in
            # values or keys. Check the bytes that would actually be returned.
            if settings.token.encode() in outgoing.body:
                raise ProxyFailure(502, "upstream_invalid_response", "The HistAgent service returned an invalid response.")
            return outgoing


def create_app(
    settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    async def refresh_owner(application: FastAPI) -> None:
        if application.state.owner == EXPECTED_OWNER:
            return
        if clock() < application.state.next_owner_check and not application.state.owner_check_lock.locked():
            return
        async with application.state.owner_check_lock:
            if application.state.owner == EXPECTED_OWNER or clock() < application.state.next_owner_check:
                return
            # Reserve the cooldown before awaiting so a cancelled probe cannot
            # cause every waiting caller to issue its own identity request.
            application.state.next_owner_check = clock() + OWNER_CHECK_RETRY_SECONDS
            application.state.owner = await verify_owner(application.state.settings, transport)
            application.state.next_owner_check = clock() + OWNER_CHECK_RETRY_SECONDS

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.settings = settings if settings is not None else Settings.from_environment()
        await refresh_owner(application)
        yield

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    application.state.settings = Settings()
    application.state.owner = None
    application.state.owner_check_lock = asyncio.Lock()
    application.state.next_owner_check = 0.0
    application.state.admission = Admission(2)

    @application.get("/", include_in_schema=False)
    async def home() -> Response:
        return RedirectResponse("https://histagent.bio/", status_code=307, headers={"Cache-Control": "no-store"})

    @application.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"])
    async def proxy(request: Request, path: str) -> Response:
        origin = request.headers.get("origin")
        if origin not in ALLOWED_ORIGINS:
            return failure(None, 403, "origin_not_allowed", "This website origin is not allowed.")
        method = ROUTES.get(request.url.path)
        if method is None or request.url.query or request.scope.get("raw_path", request.url.path.encode()) != request.url.path.encode():
            return failure(origin, 404, "route_not_found", "This API route is not available.")
        if request.method == "OPTIONS":
            requested_headers = {item.strip().lower() for item in request.headers.get("access-control-request-headers", "").split(",") if item.strip()}
            if request.headers.get("access-control-request-method") != method or not requested_headers.issubset(ALLOWED_REQUEST_HEADERS):
                return failure(origin, 403, "preflight_not_allowed", "This browser request is not allowed.")
            return Response(status_code=204, headers={**cors_headers(origin),
                "Access-Control-Allow-Methods": f"{method}, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-HistAgent-Session",
                "Access-Control-Max-Age": "600",
                "Vary": "Origin, Access-Control-Request-Method, Access-Control-Request-Headers",
            })
        if request.method != method:
            return failure(origin, 405, "method_not_allowed", "This HTTP method is not allowed.", headers={"Allow": f"{method}, OPTIONS"})
        config = application.state.settings
        owner = application.state.owner
        if not config.enabled:
            return failure(origin, 503, "proxy_disabled", "The public HistAgent service is not enabled.", health_owner=owner if request.url.path == "/api/health" else None)
        if owner != EXPECTED_OWNER:
            await refresh_owner(application)
            owner = application.state.owner
            if owner != EXPECTED_OWNER:
                return failure(origin, 503, "proxy_owner_unverified", "The public HistAgent service is not ready.")
        needs_admission = request.method == "POST"
        if needs_admission and not application.state.admission.acquire():
            return failure(origin, 429, "proxy_busy", "The public upload service is busy. Please retry shortly.", headers={"Retry-After": "5"})
        try:
            return await forward(request, config, owner, transport)
        except ProxyFailure as error:
            return failure(origin, error.status, error.code, error.message)
        except httpx.TimeoutException:
            return failure(origin, 504, "upstream_timeout", "The HistAgent service did not respond in time. Please retry later.")
        except httpx.HTTPError:
            return failure(origin, 502, "upstream_unavailable", "The HistAgent service could not be reached. Please retry later.")
        except ClientDisconnect:
            return failure(origin, 400, "invalid_request", "The request upload was interrupted.")
        except Exception:
            return failure(origin, 502, "proxy_error", "The HistAgent service could not complete this request.")
        finally:
            # Also runs for task cancellation; no automatic resubmission follows.
            if needs_admission:
                application.state.admission.release()

    return application


app = create_app()
