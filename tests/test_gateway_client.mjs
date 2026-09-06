import assert from "node:assert/strict";
import { test } from "node:test";
import { callHistAgentService } from "../docs/assets/histagent-services.js";

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
