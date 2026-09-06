// All authenticated requests go to this literal host. Never use a caller's URL.
export const UPSTREAM_ORIGIN = "https://wli14-histagent-agent.hf.space";
export const BODY_LIMITS = Object.freeze({
  "/api/call": 1024 * 1024,
  // The backend permits two images of at most 10 MiB each, plus form metadata.
  "/api/generate": 21 * 1024 * 1024,
});

const ORIGINS = new Set(["https://histagent.bio", "https://www.histagent.bio"]);
const ROUTES = new Map([
  ["/api/health", "GET"],
  ["/api/generate", "POST"],
  ["/api/call", "POST"],
]);
const REQUEST_HEADERS = new Set(["content-type", "x-histagent-session"]);
// Cloudflare's 128 MB limit is shared by concurrent requests in one isolate.
// Keep at most two fixed-size body buffers alive (42 MiB at the largest cap),
// leaving room for runtime/network copies. This is not a global rate limiter.
const MAX_ADMITTED_POSTS = 2;
let admittedPosts = 0;

class RequestFailure extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function responseHeaders(origin) {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Vary": "Origin",
    "X-Content-Type-Options": "nosniff",
  });
  if (ORIGINS.has(origin)) {
    headers.set("Access-Control-Allow-Origin", origin);
    headers.set("Access-Control-Expose-Headers", "Retry-After");
  }
  return headers;
}

function failure(origin, status, code, message, extraHeaders = {}) {
  const headers = responseHeaders(origin);
  for (const [name, value] of Object.entries(extraHeaders)) headers.set(name, value);
  return new Response(JSON.stringify({ detail: { code, message } }), { status, headers });
}

async function discardBody(body) {
  try {
    await body?.cancel();
  } catch {
    // Cancellation is best-effort and must not expose transport diagnostics.
  }
}

async function readBoundedBody(request, limit) {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && (!/^\d+$/.test(declared) || !Number.isSafeInteger(Number(declared)))) {
    throw new RequestFailure(400, "invalid_request", "Invalid request body length.");
  }
  if (declared !== null && Number(declared) > limit) {
    throw new RequestFailure(413, "request_too_large", "This request exceeds the upload limit.");
  }
  if (!request.body) throw new RequestFailure(400, "invalid_request", "A request body is required.");
  // A single bounded allocation avoids retaining every source chunk plus a
  // second concatenated body. The returned view does not copy the buffer.
  const body = new Uint8Array(limit);
  const reader = request.body.getReader();
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (length + value.byteLength > limit) {
        try { await reader.cancel(); } catch { /* Still reject before fetch. */ }
        throw new RequestFailure(413, "request_too_large", "This request exceeds the upload limit.");
      }
      body.set(value, length);
      length += value.byteLength;
    }
  } finally {
    reader.releaseLock();
  }
  if (!length) throw new RequestFailure(400, "invalid_request", "A request body is required.");
  return body.subarray(0, length);
}

function copyRetryAfter(upstreamHeaders, headers) {
  const value = upstreamHeaders.get("Retry-After");
  // Only a delay or HTTP-date can be exposed, never arbitrary upstream headers.
  if (value && (/^\d{1,10}$/.test(value) || /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(value))) {
    headers.set("Retry-After", value);
  }
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin");
    if (!ORIGINS.has(origin)) {
      return failure(null, 403, "origin_not_allowed", "This website origin is not allowed.");
    }
    const url = new URL(request.url);
    const method = ROUTES.get(url.pathname);
    if (!method || url.search) {
      return failure(origin, 404, "route_not_found", "This API route is not available.");
    }
    if (request.method === "OPTIONS") {
      const requestedMethod = request.headers.get("Access-Control-Request-Method");
      const requestedHeaders = (request.headers.get("Access-Control-Request-Headers") || "")
        .split(",").map((name) => name.trim().toLowerCase()).filter(Boolean);
      if (requestedMethod !== method || requestedHeaders.some((name) => !REQUEST_HEADERS.has(name))) {
        return failure(origin, 403, "preflight_not_allowed", "This browser request is not allowed.");
      }
      const headers = responseHeaders(origin);
      headers.delete("Content-Type");
      headers.set("Access-Control-Allow-Methods", `${method}, OPTIONS`);
      headers.set("Access-Control-Allow-Headers", "Content-Type, X-HistAgent-Session");
      headers.set("Access-Control-Max-Age", "600");
      headers.set("Vary", "Origin, Access-Control-Request-Method, Access-Control-Request-Headers");
      return new Response(null, { status: 204, headers });
    }
    if (request.method !== method) {
      return failure(origin, 405, "method_not_allowed", "This HTTP method is not allowed.", { Allow: `${method}, OPTIONS` });
    }
    const token = typeof env?.WLI14_HF_TOKEN === "string" ? env.WLI14_HF_TOKEN.trim() : "";
    if (env?.HISTAGENT_PROXY_ENABLED !== "true" || !token || token.length > 4096 || /[^\x21-\x7e]/.test(token)) {
      return failure(origin, 503, "proxy_not_configured", "The public HistAgent service is not enabled.");
    }

    const needsAdmission = method === "POST";
    if (needsAdmission && admittedPosts >= MAX_ADMITTED_POSTS) {
      return failure(origin, 429, "proxy_busy", "The public upload service is busy. Please retry shortly.", { "Retry-After": "5" });
    }
    if (needsAdmission) admittedPosts += 1;
    try {
      // Construct application-supplied headers from scratch; do not copy caller
      // auth, cookies, signed HF identity, or forwarding/IP headers. Cloudflare
      // can independently add CF-Connecting-IP, x-real-ip and CF-Worker headers.
      // Deployed upstream identity and quota attribution still need verification.
      const headers = new Headers({ Authorization: `Bearer ${token}`, Origin: origin });
      const session = request.headers.get("X-HistAgent-Session");
      if (session && /^[A-Za-z0-9._-]{8,96}$/.test(session)) headers.set("X-HistAgent-Session", session);
      let body;
      if (method === "POST") {
        const type = request.headers.get("Content-Type") || "";
        const supported = url.pathname === "/api/call"
          ? /^application\/json(?:\s*;|$)/i.test(type)
          : /^multipart\/form-data\s*;/i.test(type) && /(?:^|;)\s*boundary=(?:"[^"\r\n]+"|[^;\s]+)(?:\s*;|\s*$)/i.test(type);
        if (!supported || type.length > 512 || request.headers.has("Content-Encoding")) {
          return failure(origin, 415, "unsupported_media_type", "Use JSON for calls or multipart form data for image generation.");
        }
        headers.set("Content-Type", type);
        try {
          body = await readBoundedBody(request, BODY_LIMITS[url.pathname]);
        } catch (error) {
          return error instanceof RequestFailure
            ? failure(origin, error.status, error.code, error.message)
            : failure(origin, 400, "invalid_request", "The request body could not be read.");
        }
      }

      let upstream;
      try {
        // Exactly one submission. Retrying a GPU request could charge twice.
        upstream = await fetch(`${UPSTREAM_ORIGIN}${url.pathname}`, {
          method, headers, body, redirect: "manual", signal: request.signal,
        });
      } catch {
        return failure(origin, 502, "upstream_unavailable", "The HistAgent service could not be reached. Please retry later.");
      }
      if (upstream.status < 200 || (upstream.status >= 300 && upstream.status < 400)) {
        await discardBody(upstream.body);
        return failure(origin, 502, "upstream_redirect_rejected", "The HistAgent service returned an unexpected response.");
      }
      const outgoing = responseHeaders(origin);
      copyRetryAfter(upstream.headers, outgoing);
      if (!/^application\/(?:[a-z0-9.+-]+\+)?json(?:\s*;|$)/i.test(upstream.headers.get("Content-Type") || "")) {
        await discardBody(upstream.body);
        const status = upstream.status >= 400 ? upstream.status : 502;
        const rateLimited = status === 429;
        return failure(origin, status, rateLimited ? "backend_rate_limited" : "upstream_unavailable",
          rateLimited ? "The model host is temporarily rate-limiting requests." : "The HistAgent service returned an unexpected response.",
          outgoing.has("Retry-After") ? { "Retry-After": outgoing.get("Retry-After") } : {});
      }
      // Never copy Set-Cookie, Authorization, X-IP-Token, Location, or HF headers.
      return new Response(upstream.body, { status: upstream.status, headers: outgoing });
    } finally {
      // Hold admission through body reading and upstream submission, releasing
      // it on every success, validation/read failure, network error or redirect.
      if (needsAdmission) admittedPosts -= 1;
    }
  },
};
