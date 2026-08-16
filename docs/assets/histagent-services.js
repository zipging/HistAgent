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

async function parseGatewayResponse(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // The status code still provides a useful fallback when the body is empty.
  }
  if (!response.ok) {
    throw new Error(errorMessage(payload, `HistAgent service failed (${response.status})`));
  }
  if (!Array.isArray(payload?.data)) {
    throw new Error("The HistAgent service returned an invalid response");
  }
  return payload.data;
}

export async function callHistAgentService(service, apiName, data) {
  const response = await fetch(`${HISTAGENT_GATEWAY}/api/call`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-HistAgent-Session": sessionId()
    },
    body: JSON.stringify({ service, api_name: apiName, data })
  });
  return parseGatewayResponse(response);
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

  const response = await fetch(`${HISTAGENT_GATEWAY}/api/generate`, {
    method: "POST",
    headers: { "X-HistAgent-Session": sessionId() },
    body: form
  });
  return parseGatewayResponse(response);
}
