const HISTAGENT_GATEWAY = "https://wli14-histagent-api.hf.space";
const HISTAGENT_SESSION_KEY = "histagent-public-session";
let inMemorySession = "";

function sessionId() {
  let value = inMemorySession;
  try {
    value = window.localStorage.getItem(HISTAGENT_SESSION_KEY) || value;
  } catch (_error) {
    // Storage can be disabled by privacy settings; an in-memory ID still keeps
    // the rate limit independent from other visitors sharing an IP address.
  }
  if (!value) {
    value = typeof window.crypto?.randomUUID === "function"
      ? window.crypto.randomUUID()
      : `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    inMemorySession = value;
    try {
      window.localStorage.setItem(HISTAGENT_SESSION_KEY, value);
    } catch (_error) {
      // Keep the in-memory value when persistent storage is unavailable.
    }
  }
  return value;
}

function errorMessage(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return fallback;
}

async function parseGatewayResponse(response, health = false) {
  let payload = null;
  let decodedJson = false;
  try {
    payload = await response.json();
    decodedJson = true;
  } catch {
    // The status code still provides a useful fallback when the body is empty.
  }
  if (!response.ok) {
    if (health && !decodedJson && response.status === 503) {
      const error = new Error("The analysis gateway is waking up. Readiness will be checked automatically.");
      error.code = "backend_starting";
      throw error;
    }
    const missingService = !payload && [404, 410].includes(response.status);
    let message = errorMessage(payload, missingService
      ? "The analysis service is currently unavailable. Please try again later."
      : `HistAgent service failed (${response.status})`);
    const seconds = Number(payload?.detail?.retry_after_seconds || response.headers.get("Retry-After"));
    const waitingForDailyQuotaReset = payload?.detail?.code === "gpu_quota_exhausted"
      && /\bdaily\b/i.test(message) && /\breset\b/i.test(message);
    // The gateway cooldown does not tell us when HF's daily allowance resets.
    if (Number.isFinite(seconds) && seconds > 0 && !waitingForDailyQuotaReset && payload?.detail?.code !== "backend_starting") {
      const minutes = Math.ceil(seconds / 60);
      message += ` Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
    }
    const error = new Error(message);
    error.code = payload?.detail?.code || (missingService ? "service_unavailable" : "gateway_error");
    error.retryAfterSeconds = seconds;
    throw error;
  }
  if (health) {
    if (!payload || !["available", "degraded"].includes(payload.status)) {
      throw new Error("The analysis service returned invalid availability information.");
    }
    if (payload.status !== "available") {
      const message = Object.values(payload.backends || {}).find((backend) => backend?.message)?.message;
      const error = new Error(message || (Number(payload.remaining_gpu_seconds) === 0
        ? "The shared analysis allowance is temporarily exhausted. Please try again later."
        : "The analysis service is starting or temporarily unavailable. Please try again later."));
      error.code = "service_unavailable";
      throw error;
    }
    return payload;
  }
  if (!Array.isArray(payload?.data)) {
    throw new Error("The HistAgent service returned an invalid response");
  }
  return payload.data;
}

// One bounded request per explicit action. A timeout never resubmits GPU work.
async function requestGateway(path, options, { health = false, timeoutMs = 330000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${HISTAGENT_GATEWAY}${path}`, {
      ...options, credentials: "omit", signal: controller.signal
    });
    return await parseGatewayResponse(response, health);
  } catch (error) {
    if (controller.signal.aborted) {
      const timeout = new Error(health
        ? "The analysis service is taking too long to respond. Please try again later."
        : "The analysis request timed out. It was not retried automatically; please wait before submitting again.");
      timeout.code = "service_timeout";
      throw timeout;
    }
    if (error instanceof TypeError) {
      const unavailable = new Error("The analysis service could not be reached. Check your connection or try again later.");
      unavailable.code = "service_unavailable";
      throw unavailable;
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export async function getHistAgentServiceHealth({ timeoutMs = 15000 } = {}) {
  return requestGateway("/api/health", { method: "GET", cache: "no-store" }, { health: true, timeoutMs });
}

// Only readiness GETs are repeated. Model submissions always remain single requests.
export async function waitForHistAgentService({
  timeoutMs = 300000,
  pollIntervalMs = 5000,
  onStarting = () => {},
  shouldContinue = () => true
} = {}) {
  const deadline = Date.now() + timeoutMs;
  let starting = false;
  while (shouldContinue()) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    try {
      const health = await getHistAgentServiceHealth({ timeoutMs: Math.min(15000, remaining) });
      return shouldContinue() ? health : null;
    } catch (error) {
      if (!shouldContinue()) return null;
      if (error.code === "backend_starting") {
        starting = true;
        onStarting(error.message);
      } else if (!starting || error.code !== "service_timeout") {
        throw error;
      }
      const delay = Math.min(pollIntervalMs, deadline - Date.now());
      if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  if (!shouldContinue()) return null;
  const error = new Error("The model service is still starting. Please try Analyze selected spot again shortly.");
  error.code = "service_startup_timeout";
  throw error;
}

export async function callHistAgentService(service, apiName, data) {
  return requestGateway("/api/call", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-HistAgent-Session": sessionId()
    },
    body: JSON.stringify({ service, api_name: apiName, data })
  });
}
export async function generateHistAgentReadout({
  localBlob,
  contextBlob,
  localName,
  contextName,
  species,
  organ,
  topK = 50
}) {
  const form = new FormData();
  form.append("local_image", localBlob, localName);
  form.append("context_image", contextBlob, contextName);
  form.append("species", species);
  form.append("organ", organ);
  form.append("top_k", String(topK));

  return requestGateway("/api/generate", {
    method: "POST",
    headers: { "X-HistAgent-Session": sessionId() },
    body: form
  });
}
