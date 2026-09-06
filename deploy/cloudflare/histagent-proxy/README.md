# HistAgent public API proxy

This Worker places a server-side owner-authenticated request between the anonymous website and the existing unified Hugging Face Space:

`histagent.bio → Cloudflare Worker → wli14-histagent-agent.hf.space`

Only `GET /api/health`, `POST /api/generate`, and `POST /api/call` are forwarded. The Worker supplies `Authorization: Bearer …` from its `WLI14_HF_TOKEN` secret. Its application-supplied headers exclude incoming account credentials, cookies, signed Hugging Face visitor identity, and forwarding headers. Cloudflare can independently add network identity headers; this code does not establish which identity Hugging Face will charge. The intended result is to run public requests under the existing wli14 credential. Confirm the actual deployed headers, platform identity, and quota with a controlled production request before directing the website here.

## Configuration and deployment

No Cloudflare account ID, domain route, or credential is committed. `wrangler.toml` defaults to **disabled**, even if the Worker is accidentally deployed. This directory alone does not change the live website or Space.

1. Use the approved Cloudflare account and its existing Wrangler authentication. Run commands from this directory. Install/use Wrangler 4 if it is not already available.
2. Deploy the disabled Worker with `npx wrangler@4 deploy`.
3. Install the **existing wli14 service credential** with `npx wrangler@4 secret put WLI14_HF_TOKEN`, entering it at Wrangler's secure prompt. Do not paste it in chat, source files, browser JavaScript, an inline shell argument, or deployment logs. Do not replace it with a temporary OAuth token or a credential belonging to another account. Cloudflare secret bindings are the only credential storage this Worker uses.
4. Change `HISTAGENT_PROXY_ENABLED` to the string `"true"` in `wrangler.toml`, then deploy again. Any absent or other value remains disabled. A missing or malformed token also returns HTTP 503 without contacting Hugging Face. Browser preflight is available while disabled and does not contact the upstream.
5. Send a health request to the resulting Worker URL with `Origin: https://histagent.bio`, then run one controlled public-image inference and confirm upstream owner identity/quota. Do not send a bearer token from the browser. Keep the website endpoint unchanged until this check succeeds.
6. Configure the selected API custom domain or use the returned Workers URL, then update the single `HISTAGENT_GATEWAY` constant in `docs/assets/histagent-services.js` and its cache version. The parent deployment workflow handles website publishing. No domain/account identifiers are assumed here.

To stop owner-funded submissions, set `HISTAGENT_PROXY_ENABLED` back to `"false"` and redeploy. Removing the secret also fails closed. There are no automatic retries, fallback providers, scheduled jobs, or new model deployments.

## Limits and preserved behavior

- Exact browser origins: `https://histagent.bio` and `https://www.histagent.bio`. Missing origins, localhost, wildcard/lookalike origins, query strings, other paths, and incorrect methods are rejected. This is browser-origin isolation, not authentication against a client that can forge Origin.
- The application copies only `Content-Type` and a valid `X-HistAgent-Session` from request headers, along with the validated website Origin. The browser session continues to identify visitors to the backend's existing per-session rate control. It is not a trusted user identity; the backend's global ledger remains the spending bound.
- Cloudflare adds `CF-Worker` to Worker fetch subrequests. For destinations outside Cloudflare customer zones, its runtime can set `CF-Connecting-IP` and `x-real-ip` from the original client address even though these are absent from the headers constructed here. Local mocked fetch tests verify only the application-supplied headers. Verify deployed behavior without logging raw credentials or signed identity tokens; do not claim that this proxy automatically removes every form of visitor identity or guarantees owner quota attribution. See [Cloudflare Worker subrequest headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/#cf-connecting-ip-in-worker-subrequests).
- JSON calls: **1 MiB**. The backend has no aggregate JSON ceiling, so this is an additional proxy bound that accommodates normal evidence and chat history.
- Multipart generation: **21 MiB total**. The backend still independently checks **10 MiB per image**; the extra aggregate allowance accommodates the two files and multipart metadata. Bodies are bounded while reading, even without Content-Length or with an understated value, before any authenticated upstream request.
- At most **two POST requests per isolate** are admitted for body buffering and upstream submission. Further POSTs receive `429 proxy_busy` before their bodies are read or sent upstream. Each admitted request uses one fixed-capacity buffer, so these buffers occupy at most **42 MiB per isolate**, without an additional chunk collection or concatenation copy. Admission is released in `finally` on every outcome. Health and preflight do not allocate body buffers and remain accessible. This is a memory admission guard, not a global concurrency limit across Cloudflare isolates; the backend still enforces the shared application budget. Runtime/network memory also counts toward Cloudflare's [128 MB per-isolate limit](https://developers.cloudflare.com/workers/platform/limits/#memory) and needs deployed load verification.
- No content-encoded uploads, open-proxy URLs, redirects, or automatic resubmissions. The original JSON/form body is preserved, and the backend remains responsible for API names, field validation, reservations, rate controls, and caching.
- The backend's configured **2,400-second (40-minute) application budget** remains in place. Its default reservation window is 90,000 seconds (25 hours), not a claim about Hugging Face's quota reset schedule. The application ledger and Hugging Face billing/quota are separate accounting mechanisms; this Worker does not reset or override either one.
- JSON errors and `Retry-After` remain readable. Daily GPU quota reset wording is not changed. Non-JSON host responses are replaced with concise JSON errors. Only response Content-Type, a validated Retry-After, and proxy-owned CORS/cache/security headers are exposed; upstream cookies, credentials, identity, and redirect headers are discarded.
- Worker application logging and Wrangler observability are disabled. Do not enable request-header/body capture while validating production credentials or user images.

## Local verification

Node 20 or later is sufficient; there are no npm runtime dependencies or real network calls in the tests:

```sh
node --test test_worker.mjs
```

The mocked-fetch tests cover fixed upstream routes, application-supplied owner authorization and header stripping, exact origins, CORS preflight, request caps including streams, concurrent admission and release on success/error paths, disabled/missing configuration, redirects, backend validation/quota errors, and no retries or secret logging. They do not emulate Cloudflare-added headers, exercise an actual deployment, measure deployed isolate memory, or confirm Hugging Face identity attribution.

References: [Cloudflare Request API](https://developers.cloudflare.com/workers/runtime-apis/request/), [Wrangler configuration](https://developers.cloudflare.com/workers/wrangler/configuration/), and [Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/).
