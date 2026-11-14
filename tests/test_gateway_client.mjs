import assert from "node:assert/strict";
import { test } from "node:test";
import { callHistAgentService, getHistAgentServiceHealth } from "../docs/assets/histagent-services.js";

globalThis.window = {
  localStorage: { getItem: () => "anonymous-test-browser", setItem() {} },
};

test("anonymous browser sends no account credential and accepts model outputs", async () => {
  globalThis.fetch = async (url, request) => {
    assert.equal(url, "https://wli14-histagent-api.hf.space/api/call");
    assert.equal(request.headers["X-HistAgent-Session"], "anonymous-test-browser");
    assert.equal(request.headers.Authorization, undefined);
    assert.equal(request.headers["X-HF-Authorization"], undefined);
    return Response.json({ data: ["", [{ role: "assistant", content: "answer" }]] });
  };
  const result = await callHistAgentService("reasoning", "answer_atlas_question", ["question", [], {}]);
  assert.equal(result[1][0].content, "answer");
});

test("host rate limiting remains distinct from GPU quota with a retry time", async () => {
  globalThis.fetch = async () => Response.json({ detail: {
    message: "The model host is temporarily rate-limiting requests.",
    code: "backend_rate_limited",
    retry_after_seconds: 90,
  } }, { status: 429 });
  await assert.rejects(callHistAgentService("reasoning", "retrieve_atlas", []), (error) => {
    assert.equal(error.code, "backend_rate_limited");
    assert.equal(error.retryAfterSeconds, 90);
    assert.match(error.message, /2 minutes/);
    assert.doesNotMatch(error.message, /quota/i);
    return true;
  });
});

test("daily GPU run quota does not turn the gateway cooldown into a reset promise", async () => {
  const message = "Hugging Face’s daily GPU run quota for this visitor has been reached. Please wait for the platform quota to reset.";
  globalThis.fetch = async () => Response.json({ detail: {
    message, code: "gpu_quota_exhausted", retry_after_seconds: 300,
  } }, { status: 429, headers: { "Retry-After": "300" } });
  await assert.rejects(callHistAgentService("reasoning", "answer_atlas_question", []), (error) => {
    assert.equal(error.code, "gpu_quota_exhausted");
    assert.equal(error.retryAfterSeconds, 300);
    assert.equal(error.message, message);
    assert.doesNotMatch(error.message, /Try again in|5 minutes/);
    return true;
  });
});

test("other GPU quota errors retain their retry guidance", async () => {
  globalThis.fetch = async () => Response.json({ detail: {
    message: "The GPU allowance for this request is temporarily exhausted. Please try later.",
    code: "gpu_quota_exhausted", retry_after_seconds: 300,
  } }, { status: 429 });
  await assert.rejects(callHistAgentService("reasoning", "retrieve_atlas", []), (error) => {
    assert.match(error.message, /Try again in 5 minutes/);
    assert.equal(error.retryAfterSeconds, 300);
    return true;
  });
});

test("legacy gateway errors and Retry-After header remain readable", async () => {
  globalThis.fetch = async () => Response.json({ detail: "Service is starting." }, {
    status: 503, headers: { "Retry-After": "30" },
  });
  await assert.rejects(callHistAgentService("reasoning", "retrieve_atlas", []), /Service is starting.*1 minute/);
});

test("unexpected HTML gateway errors do not appear as successful output", async () => {
  globalThis.fetch = async () => new Response("<html>unavailable</html>", { status: 502 });
  await assert.rejects(callHistAgentService("reasoning", "retrieve_atlas", []), /HistAgent service failed \(502\)/);
});

test("malformed success responses are rejected", async () => {
  globalThis.fetch = async () => Response.json({ status: "ready" });
  await assert.rejects(callHistAgentService("reasoning", "retrieve_atlas", []), /invalid response/);
});


test("missing HTML service endpoints explain unavailability without exposing HTML", async () => {
  globalThis.fetch = async () => new Response("<html>Hugging Face 404</html>", { status: 404 });
  await assert.rejects(callHistAgentService("reasoning", "retrieve_atlas", []), (error) => {
    assert.equal(error.code, "service_unavailable");
    assert.match(error.message, /service is currently unavailable/);
    assert.doesNotMatch(error.message, /html|Hugging Face/);
    return true;
  });
});

test("readiness is an anonymous uncached GET, not a model submission", async () => {
  globalThis.fetch = async (url, request) => {
    assert.match(url, /\/api\/health$/);
    assert.equal(request.method, "GET");
    assert.equal(request.cache, "no-store");
    assert.equal(request.credentials, "omit");
    assert.equal(request.body, undefined);
    assert.equal(request.headers, undefined);
    return Response.json({ status: "available", remaining_gpu_seconds: 2400 });
  };
  assert.equal((await getHistAgentServiceHealth()).status, "available");
});

test("degraded and invalid health responses never claim readiness", async () => {
  globalThis.fetch = async () => Response.json({ status: "degraded", remaining_gpu_seconds: 0 });
  await assert.rejects(getHistAgentServiceHealth(), /shared analysis allowance.*exhausted/);
  globalThis.fetch = async () => new Response("<html>starting</html>");
  await assert.rejects(getHistAgentServiceHealth(), /invalid availability/);
});

test("network failure is actionable and never retries a model submission", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; throw new TypeError("Failed to fetch"); };
  await assert.rejects(callHistAgentService("reasoning", "retrieve_atlas", []), (error) => {
    assert.equal(error.code, "service_unavailable");
    assert.match(error.message, /could not be reached/);
    return true;
  });
  assert.equal(calls, 1);
});

test("a stalled readiness check is aborted after its deadline without retry", async () => {
  let calls = 0;
  globalThis.fetch = (_url, request) => {
    calls += 1;
    return new Promise((_resolve, reject) => request.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))));
  };
  await assert.rejects(getHistAgentServiceHealth({ timeoutMs: 5 }), (error) => {
    assert.equal(error.code, "service_timeout");
    assert.match(error.message, /taking too long/);
    return true;
  });
  assert.equal(calls, 1);
});
