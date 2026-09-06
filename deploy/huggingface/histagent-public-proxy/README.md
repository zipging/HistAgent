---
title: HistAgent Public API
emoji: 🧬
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
license: apache-2.0
---

# HistAgent owner-authenticated public API

This small CPU service is intended for the existing `wli14/HistAgent-api` Space. It accepts anonymous requests from the website and creates a fresh authenticated HTTP request to the unified model service at `https://wli14-histagent-agent.hf.space`. It does not load models, use Gradio clients, copy `LocalContext`, or introduce an external hosting provider.

The only credential source is the existing `WLI14_HF_TOKEN` Space secret. At process startup, a read-only request to `https://huggingface.co/api/whoami-v2` verifies that its account name is exactly `wli14`. Missing, invalid, redirected, unavailable, or wrong-account verification leaves API calls closed. Only the confirmed account name is retained as identity metadata and added to health responses as `proxy_owner`; full whoami responses and token metadata are discarded. A verified owner is cached for the process lifetime. While enabled but unverified, an incoming API request may trigger another serialized identity check after a 60-second cooldown. This permits recovery from a transient startup network failure without manual restarts or concurrent probe storms. These bounded read-only readiness checks are separate from GPU requests, which are never automatically retried.

The model request supplies the existing owner token in `Authorization`, a validated website Origin, valid `X-HistAgent-Session`, and Content-Type. Incoming Authorization, `X-HF-Authorization`, cookies, `X-IP-Token`, and forwarding/IP headers are not copied. Every exchange uses a fresh httpx client with ambient proxies disabled, no inherited cookie jar, no redirect following, and no automatic retries. This creates a new authenticated request at the HF edge. Confirm actual GPU quota attribution with a controlled anonymous website request before describing owner-funded inference as verified: a startup whoami check alone verifies the credential account, not how a GPU run was charged.

## Deployment configuration

This bundle is disabled by default. Deploy `app.py`, `Dockerfile`, `requirements.txt`, and this README to the existing CPU Space; no new hardware is needed by this code. Preserve the existing durable `WLI14_HF_TOKEN` secret. Do not copy its value into chat, source, browser code, shell arguments, logs, or local files. Do not substitute a temporary OAuth token or another account's credential.

After code QA, set the Space variable `HISTAGENT_PROXY_ENABLED` to the exact string `true` and restart. Missing or other values keep API requests disabled. Startup still performs the single identity check when a syntactically valid secret is present, even while disabled. A disabled health request can therefore expose `proxy_owner: "wli14"` while returning 503. OPTIONS and the root homepage redirect remain available while disabled.

The parent deployment workflow performs live checks and the website endpoint update. This directory does not itself deploy anything or modify the frontend. Set `HISTAGENT_PROXY_ENABLED=false` and restart to stop new submissions. The Docker command uses one Uvicorn worker so the admission bound is shared by all requests in the process.

## Public contract and bounds

- `GET /` redirects to `https://histagent.bio/`. Only `GET /api/health`, `POST /api/generate`, and `POST /api/call` contact the model service. Other paths/methods, query strings, and attempted alternate destinations are rejected.
- API requests require exact Origin `https://histagent.bio` or `https://www.histagent.bio`, including health probes. No-Origin requests and localhost are rejected. Browser preflight allows only each route's method, Content-Type and X-HistAgent-Session. Credentials are never requested from the browser. Origin checks are browser isolation, not authentication against clients able to forge the header.
- Two POSTs may be admitted at a time, before any body is read or GPU request is submitted. Additional POSTs return `429 proxy_busy`. Admission is held through reading and the upstream response, then released in `finally`, including errors and cancellation. Health and preflight do not occupy upload slots. This limit complements the existing model service's queue and per-session rate limits.
- JSON calls are limited to 1 MiB. Multipart generation is limited to 21 MiB in total; the model API independently retains its 10 MiB per-image checks. Incremental reads enforce the cap even with missing or understated Content-Length. The entire inbound body read has a 60-second timeout; a slow or unfinished upload returns 408 and releases its admission slot without submitting to the model. Content-encoded uploads are rejected. No unbounded collection of body chunks is retained.
- httpx uses a 15-second connection limit, 300-second read limit for a GPU response, 30-second write limit, and 15-second pool limit. The read limit bounds network inactivity, not total elapsed response time. The startup identity check uses 15-second limits. An interrupted request is never automatically resubmitted.
- Metadata responses are capped at 64 KiB; model responses at 32 MiB. JSON parsing and header rebuilding reject malformed, oversized, redirected, or credential-echoing responses. Both raw input and final serialized JSON are checked so escaped credential values or keys cannot be exposed after decoding. Upstream cookies, identity, redirect and diagnostic headers are discarded; only validated Retry-After is retained alongside application CORS/cache/security headers. Backend JSON error codes and quota-reset wording remain intact. HTML rate-limit responses become concise JSON errors.
- The existing model backend remains the sole application ledger and reservation authority. Its configured 2,400-second (40-minute) application budget and default 90,000-second (25-hour) window are unchanged. This proxy adds no reservations and never resets the ledger. HF platform billing/quota and the application ledger remain separate accounting mechanisms.
- Application code does not log request bodies, headers, tokens, prompts, image contents, or raw exceptions. Uvicorn access logging is disabled. Do not enable HTTP debug/request capture while handling real credentials and uploaded images.

## Verification

Use Python 3.12 and the pinned runtime requirements plus pytest. Tests use only fabricated credentials, mocked httpx transports, and local ASGI requests:

```sh
python -m pytest -q test_proxy.py
```

Before updating the website, verify the deployed owner metadata, then exercise one uncached anonymous public-image request and inspect the resulting model evidence and platform quota attribution without exposing credentials. Keep the proxy disabled or the frontend unchanged if those checks fail. No local test can establish live HF quota attribution or capacity.

References: [HF Hub API](https://huggingface.co/docs/hub/api), [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/), and [HTTPX environment settings](https://www.python-httpx.org/environment_variables/).
