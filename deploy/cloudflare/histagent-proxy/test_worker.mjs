import assert from "node:assert/strict";
import { test } from "node:test";
import worker, { BODY_LIMITS, UPSTREAM_ORIGIN } from "./worker.mjs";

const ORIGIN = "https://histagent.bio";
const OWNER_TOKEN = "test_owner_credential_never_a_live_token";
const ENV = { HISTAGENT_PROXY_ENABLED: "true", WLI14_HF_TOKEN: OWNER_TOKEN };

function request(path = "/api/call", options = {}) {
  const method = options.method || "POST";
  const headers = new Headers({ Origin: ORIGIN, ...options.headers });
  const init = { method, headers };
  if (method === "POST") {
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    init.body = options.body ?? JSON.stringify({ service: "reasoning", api_name: "retrieve_atlas", data: ["test", "human", "Any", "__ready__", 5] });
    if (init.body instanceof ReadableStream) init.duplex = "half";
  }
  return new Request(`https://proxy.example${path}`, init);
}

function noUpstream(t) {
  return t.mock.method(globalThis, "fetch", () => { throw new Error("Unexpected upstream call"); });
}

test("all three routes supply the owner credential only to the literal HF host", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url, init });
    return Response.json({ data: ["ok"] });
  });
  for (const [path, method] of [["/api/health", "GET"], ["/api/call", "POST"], ["/api/generate", "POST"]]) {
    const headers = {
      Authorization: "Bearer untrusted_caller",
      "X-HF-Authorization": "Bearer untrusted_hf",
      "X-IP-Token": "untrusted_signed_identity",
      Cookie: "session=caller_cookie",
      "X-Forwarded-For": "192.0.2.1",
      "CF-Connecting-IP": "192.0.2.2",
      "X-HistAgent-Session": "browser-session-123",
      "X-Arbitrary": "not-forwarded",
    };
    if (path === "/api/generate") headers["Content-Type"] = "multipart/form-data; boundary=test-boundary";
    const response = await worker.fetch(request(path, { method, headers }), ENV);
    assert.equal(response.status, 200);
    const { url, init } = calls.at(-1);
    assert.equal(url, `${UPSTREAM_ORIGIN}${path}`);
    assert.equal(init.method, method);
    assert.equal(init.redirect, "manual");
    assert.equal(init.headers.get("Authorization"), `Bearer ${OWNER_TOKEN}`);
    assert.equal(init.headers.get("Origin"), ORIGIN);
    assert.equal(init.headers.get("X-HistAgent-Session"), "browser-session-123");
    assert.deepEqual([...init.headers.keys()].sort(), method === "GET"
      ? ["authorization", "origin", "x-histagent-session"]
      : ["authorization", "content-type", "origin", "x-histagent-session"]);
    assert.doesNotMatch(await response.text(), new RegExp(OWNER_TOKEN));
  }
  assert.equal(calls.length, 3);
});

test("response headers are rebuilt without upstream credentials or cookies", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ data: ["ok"] }, { headers: {
    Authorization: OWNER_TOKEN, "X-HF-Authorization": OWNER_TOKEN,
    "X-IP-Token": "server-identity", "Set-Cookie": "credential=secret",
    Location: "https://elsewhere.example/", "X-Secret": OWNER_TOKEN,
    "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Credentials": "true",
    "Retry-After": "90", "Cache-Control": "public, max-age=3600",
  } }));
  const response = await worker.fetch(request(), ENV);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), ORIGIN);
  assert.equal(response.headers.get("Retry-After"), "90");
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal(response.headers.get("Access-Control-Expose-Headers"), "Retry-After");
  for (const header of ["Authorization", "X-HF-Authorization", "X-IP-Token", "Set-Cookie", "Location", "X-Secret", "Access-Control-Allow-Credentials"]) {
    assert.equal(response.headers.get(header), null);
  }
  assert.doesNotMatch(JSON.stringify([...response.headers]), new RegExp(OWNER_TOKEN));
});

test("unknown, missing, null and lookalike origins fail before fetch", async (t) => {
  const mock = noUpstream(t);
  for (const origin of ["https://histagent.bio.attacker.example", "http://histagent.bio", "https://histagent.bio/", "null", "http://localhost:4000"]) {
    const response = await worker.fetch(request("/api/call", { headers: { Origin: origin } }), ENV);
    assert.equal(response.status, 403);
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), null);
  }
  const absent = request();
  absent.headers.delete("Origin");
  assert.equal((await worker.fetch(absent, ENV)).status, 403);
  assert.equal(mock.mock.callCount(), 0);
});

test("www production origin is allowed without wildcard or credentials", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ status: "available" }));
  const response = await worker.fetch(request("/api/health", { method: "GET", headers: { Origin: "https://www.histagent.bio" } }), ENV);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), "https://www.histagent.bio");
  assert.equal(response.headers.get("Access-Control-Allow-Credentials"), null);
});

test("unsupported routes, methods and caller-selected upstreams never fetch", async (t) => {
  const mock = noUpstream(t);
  for (const path of ["/", "/config", "/gradio_api/call/run", "/api/call/", "/api/call?url=https://attacker.example", "/api/health?token=ignored", "/api%2fcall", "//attacker.example/api/call"]) {
    assert.equal((await worker.fetch(request(path), ENV)).status, 404, path);
  }
  for (const [path, method] of [["/api/call", "GET"], ["/api/generate", "PUT"], ["/api/health", "POST"], ["/api/health", "HEAD"]]) {
    const response = await worker.fetch(request(path, { method }), ENV);
    assert.equal(response.status, 405);
    assert.match(response.headers.get("Allow"), /OPTIONS/);
  }
  assert.equal(mock.mock.callCount(), 0);
});

test("preflight permits only the route method and two public request headers", async (t) => {
  const mock = noUpstream(t);
  for (const [path, method] of [["/api/health", "GET"], ["/api/call", "POST"], ["/api/generate", "POST"]]) {
    const response = await worker.fetch(request(path, { method: "OPTIONS", headers: {
      "Access-Control-Request-Method": method,
      "Access-Control-Request-Headers": "Content-Type, x-HistAgent-Session",
    } }), {});
    assert.equal(response.status, 204);
    assert.equal(await response.text(), "");
    assert.equal(response.headers.get("Access-Control-Allow-Methods"), `${method}, OPTIONS`);
    assert.equal(response.headers.get("Access-Control-Allow-Headers"), "Content-Type, X-HistAgent-Session");
    assert.match(response.headers.get("Vary"), /Access-Control-Request-Headers/);
  }
  for (const headers of [
    { "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization" },
    { "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-IP-Token" },
    { "Access-Control-Request-Method": "DELETE" }, {},
  ]) {
    assert.equal((await worker.fetch(request("/api/call", { method: "OPTIONS", headers }), ENV)).status, 403);
  }
  assert.equal(mock.mock.callCount(), 0);
});

test("disabled, missing and malformed secret configuration fail closed", async (t) => {
  const mock = noUpstream(t);
  for (const env of [{}, { WLI14_HF_TOKEN: OWNER_TOKEN }, { ...ENV, HISTAGENT_PROXY_ENABLED: "false" }, { ...ENV, HISTAGENT_PROXY_ENABLED: true }, { ...ENV, WLI14_HF_TOKEN: "" }, { ...ENV, WLI14_HF_TOKEN: "bad\ntoken" }]) {
    const response = await worker.fetch(request(), env);
    assert.equal(response.status, 503);
    assert.doesNotMatch(await response.text(), /bad|token|credential/i);
  }
  assert.equal(mock.mock.callCount(), 0);
});

test("invalid session IDs are omitted while backend fallback remains intact", async (t) => {
  const sessions = [];
  t.mock.method(globalThis, "fetch", async (_, init) => {
    sessions.push(init.headers.get("X-HistAgent-Session"));
    return Response.json({ data: [] });
  });
  for (const session of ["short", "contains spaces", "a".repeat(97)]) {
    await worker.fetch(request("/api/call", { headers: { "X-HistAgent-Session": session } }), ENV);
  }
  assert.deepEqual(sessions, [null, null, null]);
});

test("JSON body is forwarded byte-for-byte, preserving backend validation", async (t) => {
  const body = JSON.stringify({ service: "reasoning", api_name: "answer_atlas_question", data: ["Where?", [], { ranked_genes: ["CD74"] }] });
  t.mock.method(globalThis, "fetch", async (_, init) => {
    assert.equal(new TextDecoder().decode(init.body), body);
    return Response.json({ data: ["", [{ role: "assistant", content: "Answer" }]] });
  });
  const response = await worker.fetch(request("/api/call", { body }), ENV);
  assert.equal(response.status, 200);
});

test("multipart uploads keep their original boundary and bytes", async (t) => {
  const form = new FormData();
  form.append("local_image", new Blob(["local-fixture"], { type: "image/png" }), "local.png");
  form.append("context_image", new Blob(["context-fixture"], { type: "image/png" }), "context.png");
  form.append("species", "human");
  const incoming = new Request("https://proxy.example/api/generate", { method: "POST", headers: { Origin: ORIGIN }, body: form });
  const expected = await incoming.clone().arrayBuffer();
  t.mock.method(globalThis, "fetch", async (_, init) => {
    assert.deepEqual(init.body, new Uint8Array(expected));
    assert.equal(init.body.buffer.byteLength, BODY_LIMITS["/api/generate"]);
    assert.equal(init.headers.get("Content-Type"), incoming.headers.get("Content-Type"));
    return Response.json({ data: [] });
  });
  assert.equal((await worker.fetch(incoming, ENV)).status, 200);
});

test("declared oversized and malformed body lengths never submit", async (t) => {
  const mock = noUpstream(t);
  for (const path of ["/api/call", "/api/generate"]) {
    const headers = { "Content-Length": String(BODY_LIMITS[path] + 1) };
    if (path === "/api/generate") headers["Content-Type"] = "multipart/form-data; boundary=test";
    assert.equal((await worker.fetch(request(path, { headers }), ENV)).status, 413);
  }
  assert.equal((await worker.fetch(request("/api/call", { headers: { "Content-Length": "-1" } }), ENV)).status, 400);
  assert.equal(mock.mock.callCount(), 0);
});

test("streamed body caps cannot be bypassed by missing or understated length", async (t) => {
  const mock = noUpstream(t);
  for (const declared of [undefined, "1"]) {
    let cancelled = false;
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(new Uint8Array(BODY_LIMITS["/api/call"]));
        controller.enqueue(new Uint8Array(1));
      },
      cancel() { cancelled = true; },
    });
    const headers = declared ? { "Content-Length": declared } : {};
    assert.equal((await worker.fetch(request("/api/call", { body, headers }), ENV)).status, 413);
    assert.equal(cancelled, true);
  }
  assert.equal(mock.mock.callCount(), 0);
});

test("JSON body exactly at the cap is accepted", async (t) => {
  t.mock.method(globalThis, "fetch", async (_, init) => {
    assert.equal(init.body.byteLength, BODY_LIMITS["/api/call"]);
    return Response.json({ data: [] });
  });
  const body = " ".repeat(BODY_LIMITS["/api/call"] - 2) + "{}";
  assert.equal((await worker.fetch(request("/api/call", { body }), ENV)).status, 200);
});

test("unsupported media, compressed and unreadable bodies never submit", async (t) => {
  const mock = noUpstream(t);
  for (const [path, headers] of [
    ["/api/call", { "Content-Type": "text/plain" }],
    ["/api/call", { "Content-Encoding": "gzip" }],
    ["/api/generate", { "Content-Type": "application/json" }],
    ["/api/generate", { "Content-Type": "multipart/form-data" }],
  ]) {
    assert.equal((await worker.fetch(request(path, { headers }), ENV)).status, 415);
  }
  const body = new ReadableStream({ start(controller) { controller.error(new Error("private input detail")); } });
  const response = await worker.fetch(request("/api/call", { body }), ENV);
  assert.equal(response.status, 400);
  assert.doesNotMatch(await response.text(), /private input detail/);
  assert.equal((await worker.fetch(request("/api/call", { body: "" }), ENV)).status, 400);
  assert.equal(mock.mock.callCount(), 0);
});

test("redirects are rejected without forwarding Location or following credentials", async (t) => {
  const mock = t.mock.method(globalThis, "fetch", async (_, init) => {
    assert.equal(init.redirect, "manual");
    return new Response(null, { status: 307, headers: { Location: "https://attacker.example/collect" } });
  });
  const response = await worker.fetch(request(), ENV);
  assert.equal(response.status, 502);
  assert.equal(response.headers.get("Location"), null);
  assert.doesNotMatch(await response.text(), /attacker/);
  assert.equal(mock.mock.callCount(), 1);
});

test("GPU quota responses preserve backend status, code and reset wording with no retry", async (t) => {
  const payload = { detail: { code: "gpu_quota_exhausted", message: "Daily GPU quota reached. Wait for the platform quota to reset.", retry_after_seconds: 300 } };
  const mock = t.mock.method(globalThis, "fetch", async () => Response.json(payload, { status: 429, headers: { "Retry-After": "300" } }));
  const response = await worker.fetch(request(), ENV);
  assert.equal(response.status, 429);
  assert.deepEqual(await response.json(), payload);
  assert.equal(response.headers.get("Retry-After"), "300");
  assert.equal(mock.mock.callCount(), 1);
});

test("backend JSON validation errors retain their original status and payload", async (t) => {
  const payload = { detail: [{ msg: "Field required", loc: ["body", "service"] }] };
  t.mock.method(globalThis, "fetch", async () => Response.json(payload, { status: 422 }));
  const response = await worker.fetch(request(), ENV);
  assert.equal(response.status, 422);
  assert.deepEqual(await response.json(), payload);
});

test("non-JSON upstream rate limiting is sanitized and never retried", async (t) => {
  const mock = t.mock.method(globalThis, "fetch", async () => new Response("<html>private edge diagnostic</html>", {
    status: 429, headers: { "Content-Type": "text/html", "Retry-After": "90" },
  }));
  const response = await worker.fetch(request(), ENV);
  assert.equal(response.status, 429);
  const payload = await response.json();
  assert.equal(payload.detail.code, "backend_rate_limited");
  assert.doesNotMatch(payload.detail.message, /private|quota/);
  assert.equal(response.headers.get("Retry-After"), "90");
  assert.equal(mock.mock.callCount(), 1);
});

test("non-JSON success and nonstandard Retry-After are not exposed", async (t) => {
  const mock = t.mock.method(globalThis, "fetch", async () => new Response("unexpected", {
    status: 200, headers: { "Content-Type": "text/html", "Retry-After": OWNER_TOKEN },
  }));
  const response = await worker.fetch(request(), ENV);
  assert.equal(response.status, 502);
  assert.equal(response.headers.get("Retry-After"), null);
  assert.doesNotMatch(await response.text(), new RegExp(OWNER_TOKEN));
  assert.equal(mock.mock.callCount(), 1);
});

test("network exceptions expose no credential, emit no logs and are never retried", async (t) => {
  const logs = ["log", "error", "warn"].map((method) => t.mock.method(console, method, () => {}));
  const mock = t.mock.method(globalThis, "fetch", async () => { throw new Error(`Network failure with ${OWNER_TOKEN}`); });
  const response = await worker.fetch(request(), ENV);
  assert.equal(response.status, 502);
  assert.doesNotMatch(await response.text(), new RegExp(OWNER_TOKEN));
  assert.equal(mock.mock.callCount(), 1);
  logs.forEach((entry) => assert.equal(entry.mock.callCount(), 0));
});

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("two uploads hold isolate admission during reading and submission; a third is rejected unread", async (t) => {
  const submitted = deferred();
  const complete = deferred();
  let postCalls = 0;
  let holdSubmissions = true;
  const mock = t.mock.method(globalThis, "fetch", async (url, init) => {
    if (url.endsWith("/api/health")) return Response.json({ status: "available" });
    postCalls += 1;
    assert.equal(init.body.buffer.byteLength, BODY_LIMITS["/api/generate"]);
    if (postCalls === 2) submitted.resolve();
    if (holdSubmissions) await complete.promise;
    return Response.json({ data: [] });
  });
  const controllers = [];
  const uploads = [0, 1].map(() => {
    const body = new ReadableStream({ start(controller) { controllers.push(controller); } }, { highWaterMark: 0 });
    return worker.fetch(request("/api/generate", { body, headers: { "Content-Type": "multipart/form-data; boundary=test" } }), ENV);
  });
  try {
    let thirdReadCount = 0;
    const blockedBody = new ReadableStream({ pull() { thirdReadCount += 1; } }, { highWaterMark: 0 });
    const rejected = await worker.fetch(request("/api/call", { body: blockedBody }), ENV);
    assert.equal(rejected.status, 429);
    assert.equal((await rejected.json()).detail.code, "proxy_busy");
    assert.equal(rejected.headers.get("Retry-After"), "5");
    assert.equal(thirdReadCount, 0);
    assert.equal(mock.mock.callCount(), 0);

    for (const controller of controllers) {
      controller.enqueue(new TextEncoder().encode("--test--\r\n"));
      controller.close();
    }
    await submitted.promise;
    assert.equal(postCalls, 2);
    const duringSubmission = await worker.fetch(request(), ENV);
    assert.equal((await duringSubmission.json()).detail.code, "proxy_busy");
    assert.equal(postCalls, 2);

    assert.equal((await worker.fetch(request("/api/health", { method: "GET" }), ENV)).status, 200);
    assert.equal((await worker.fetch(request("/api/call", { method: "OPTIONS", headers: { "Access-Control-Request-Method": "POST" } }), ENV)).status, 204);
  } finally {
    holdSubmissions = false;
    complete.resolve();
    // Also unblock readers if an assertion failed before completing the uploads.
    for (const controller of controllers) {
      try { controller.close(); } catch { /* Already closed. */ }
    }
    await Promise.all(uploads);
  }
  const afterRelease = await worker.fetch(request("/api/generate", { headers: { "Content-Type": "multipart/form-data; boundary=test" } }), ENV);
  assert.equal(afterRelease.status, 200);
  assert.equal(postCalls, 3);
});

test("admission is released after every validation, body, upstream error and success outcome", async (t) => {
  let upstreamOutcome = "ok";
  t.mock.method(globalThis, "fetch", async () => {
    if (upstreamOutcome === "network") throw new Error("Private network detail");
    if (upstreamOutcome === "abort") throw new DOMException("Aborted", "AbortError");
    if (upstreamOutcome === "redirect") return new Response(null, { status: 307 });
    if (upstreamOutcome === "html") return new Response("Unavailable", { status: 503 });
    if (upstreamOutcome === "quota") return Response.json({ detail: { code: "gpu_quota_exhausted" } }, { status: 429 });
    if (upstreamOutcome === "validation") return Response.json({ detail: "Invalid API payload" }, { status: 422 });
    return Response.json({ data: [] });
  });
  const cases = [
    { name: "unsupported media", status: 415, make: () => request("/api/call", { headers: { "Content-Type": "text/plain" } }) },
    { name: "empty body", status: 400, make: () => request("/api/call", { body: "" }) },
    { name: "declared length", status: 413, make: () => request("/api/call", { headers: { "Content-Length": String(BODY_LIMITS["/api/call"] + 1) } }) },
    { name: "stream overflow and cancellation failure", status: 413, make: () => request("/api/call", { body: new ReadableStream({
      start(controller) { controller.enqueue(new Uint8Array(BODY_LIMITS["/api/call"] + 1)); },
      cancel() { throw new Error("Private cancellation detail"); },
    }) }) },
    { name: "body read error", status: 400, make: () => request("/api/call", { body: new ReadableStream({ start(controller) { controller.error(new Error("Private read detail")); } }) }) },
    { name: "network", status: 502 },
    { name: "abort", status: 502 },
    { name: "redirect", status: 502 },
    { name: "html", status: 503 },
    { name: "quota", status: 429 },
    { name: "validation", status: 422 },
    { name: "ok", status: 200 },
  ];
  for (const outcome of cases) {
    upstreamOutcome = outcome.name;
    // Repeating beyond the two-slot cap catches a leaked admission slot even
    // when the original error is also a 429 response.
    for (let repeat = 0; repeat < 3; repeat += 1) {
      const response = await worker.fetch(outcome.make ? outcome.make() : request(), ENV);
      assert.equal(response.status, outcome.status, outcome.name);
      const payload = await response.json();
      assert.notEqual(payload.detail?.code, "proxy_busy", outcome.name);
    }
  }
});
