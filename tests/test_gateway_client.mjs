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
