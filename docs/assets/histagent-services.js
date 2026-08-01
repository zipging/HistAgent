const HISTAGENT_GATEWAY = "https://wli14-histagent-api.hf.space";

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
    headers: { "Content-Type": "application/json" },
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
    body: form
  });
  return parseGatewayResponse(response);
}
