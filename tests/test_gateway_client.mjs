import assert from "node:assert/strict";
import { test } from "node:test";
import { callHistAgentService, getHistAgentServiceHealth, waitForHistAgentService } from "../docs/assets/histagent-services.js";

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

test("cold-start readiness repeats only GETs and reports progress before readiness", async () => {
  let calls = 0;
  const progress = [];
  globalThis.fetch = async (url, request) => {
    assert.match(url, /\/api\/health$/);
    assert.equal(request.method, "GET");
    assert.equal(request.body, undefined);
    calls += 1;
    return calls < 3
      ? Response.json({ detail: { code: "backend_starting", message: "The model is waking up." } }, {
        status: 503, headers: { "Retry-After": "5" }
      })
      : Response.json({ status: "available" });
  };
  const health = await waitForHistAgentService({ pollIntervalMs: 1, onStarting: (message) => progress.push(message) });
  assert.equal(health.status, "available");
  assert.equal(calls, 3);
  assert.deepEqual(progress, ["The model is waking up.", "The model is waking up."]);
});

test("cold-start polling has a finite deadline", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return Response.json({ detail: { code: "backend_starting", message: "Loading" } }, { status: 503 });
  };
  await assert.rejects(waitForHistAgentService({ timeoutMs: 15, pollIntervalMs: 2 }), (error) => {
    assert.equal(error.code, "service_startup_timeout");
    return true;
  });
  assert.ok(calls > 0 && calls < 20);
});

test("cold-start polling stops when its caller becomes obsolete", async () => {
  let active = true;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return Response.json({ detail: { code: "backend_starting", message: "Loading" } }, { status: 503 });
  };
  assert.equal(await waitForHistAgentService({
    pollIntervalMs: 1, shouldContinue: () => active, onStarting: () => { active = false; }
  }), null);
  assert.equal(calls, 1);
});

test("other availability failures and model cold-start failures are never retried", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return Response.json({ detail: { code: "upstream_unavailable", message: "Unavailable" } }, { status: 503 });
  };
  await assert.rejects(waitForHistAgentService({ pollIntervalMs: 1 }), /Unavailable/);
  assert.equal(calls, 1);
  globalThis.fetch = async () => {
    calls += 1;
    return Response.json({ detail: { code: "backend_starting", message: "Loading" } }, { status: 503 });
  };
  await assert.rejects(callHistAgentService("reasoning", "retrieve_atlas", []), /Loading/);
  assert.equal(calls, 2);
});


test("a health timeout during confirmed startup keeps polling within the original deadline", async () => {
  let calls = 0;
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (callback, delay, ...args) => originalSetTimeout(callback, Math.min(delay, 1), ...args);
  globalThis.fetch = async (_url, request) => {
    assert.equal(request.method, "GET");
    calls += 1;
    if (calls === 1) return Response.json({ detail: { code: "backend_starting", message: "Loading" } }, { status: 503 });
    if (calls === 2) {
      return new Promise((_resolve, reject) => request.signal.addEventListener("abort", () => {
        reject(new DOMException("Aborted", "AbortError"));
      }));
    }
    return Response.json({ status: "available" });
  };
  try {
    assert.equal((await waitForHistAgentService({ pollIntervalMs: 1 })).status, "available");
    assert.equal(calls, 3);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
  }
});


test("a sleeping public gateway's HTML 503 is polled only by health checks", async () => {
  let calls = 0;
  const progress = [];
  globalThis.fetch = async (_url, request) => {
    assert.equal(request.method, "GET");
    calls += 1;
    return calls === 1
      ? new Response("<html>Space is sleeping</html>", { status: 503 })
      : Response.json({ status: "available" });
  };
  const health = await waitForHistAgentService({ pollIntervalMs: 1, onStarting: (message) => progress.push(message) });
  assert.equal(health.status, "available");
  assert.equal(calls, 2);
  assert.match(progress[0], /gateway is waking up/);
  globalThis.fetch = async (_url, request) => {
    assert.equal(request.method, "POST");
    calls += 1;
    return new Response("<html>Space is sleeping</html>", { status: 503 });
  };
  await assert.rejects(callHistAgentService("reasoning", "retrieve_atlas", []), (error) => {
    assert.equal(error.code, "gateway_error");
    return true;
  });
  assert.equal(calls, 3);
});

test("structured JSON 503 without a starting code still fails immediately", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return Response.json(null, { status: 503 });
  };
  await assert.rejects(waitForHistAgentService({ pollIntervalMs: 1 }), (error) => {
    assert.equal(error.code, "gateway_error");
    return true;
  });
  assert.equal(calls, 1);
});

test("explicit analysis can wait through a transient host throttle before any POST", async () => {
  for (const code of ["host_rate_limited", "backend_rate_limited"]) {
    let calls = 0;
    let progress = 0;
    globalThis.fetch = async (_url, request) => {
      assert.equal(request.method, "GET");
      return ++calls === 1
        ? Response.json({ detail: { code, message: "Busy" } }, { status: 429 })
        : Response.json({ status: "available" });
    };
    const health = await waitForHistAgentService({
      pollIntervalMs: 1, onRateLimit: () => { progress++; }
    });
    assert.equal(health.status, "available");
    assert.equal(calls, 2);
    assert.equal(progress, 1);
  }
});

test("a long host cooldown is reported immediately and respects HTTP-date Retry-After", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    return new Response("Busy", { status: 429, headers: {
      "Retry-After": new Date(Date.now() + 3600000).toUTCString()
    } });
  };
  await assert.rejects(waitForHistAgentService({
    timeoutMs: 1000, onRateLimit: () => assert.fail("outside wait budget")
  }), error => error.code === "host_rate_limited" && error.retryAfterSeconds >= 3598);
  assert.equal(calls, 1);
});

test("passive health checks and GPU quota or POST failures never retry a 429", async () => {
  let calls = 0;
  let code = "backend_rate_limited";
  globalThis.fetch = async () => {
    calls++;
    return Response.json({ detail: { code, message: "Busy" } }, { status: 429 });
  };
  await assert.rejects(waitForHistAgentService(), /Busy/);
  await assert.rejects(callHistAgentService("reasoning", "retrieve_atlas", []), /Busy/);
  code = "gpu_quota_exhausted";
  await assert.rejects(waitForHistAgentService({ onRateLimit: () => assert.fail() }), /Busy/);
  assert.equal(calls, 3);
});

test("degraded health carries backend throttle metadata so Analyze can wait", async () => {
  globalThis.fetch = async () => Response.json({ status: "degraded", remaining_gpu_seconds: 2400,
    backends: {
      inference: { status: "unavailable", code: "backend_rate_limited", message: "Busy", retry_after_seconds: 42 },
      reasoning: { status: "reachable" }
    }
  });
  await assert.rejects(getHistAgentServiceHealth(),
    error => error.code === "backend_rate_limited" && error.retryAfterSeconds === 42);
});
