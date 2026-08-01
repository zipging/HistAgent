const SERVICE_CANDIDATES = Object.freeze({
  inference: Object.freeze([
    "https://wli14-histagent-demo.hf.space",
    "https://wli13-histagent-demo.hf.space"
  ]),
  reasoning: Object.freeze([
    "https://wli14-histagent-chat.hf.space",
    "https://wli13-histagent-chat.hf.space"
  ])
});

const preferredSpace = new Map();

function orderedCandidates(service) {
  const candidates = SERVICE_CANDIDATES[service];
  if (!candidates) throw new Error(`Unknown HistAgent service: ${service}`);
  const preferred = preferredSpace.get(service);
  return preferred
    ? [preferred, ...candidates.filter((candidate) => candidate !== preferred)]
    : [...candidates];
}

function serviceError(error, fallback) {
  if (error instanceof Error && error.message) return error;
  return new Error(fallback);
}

export async function callGradioAt(space, apiName, data) {
  const endpoint = `${space}/gradio_api/call/${apiName}`;
  const submission = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data })
  });
  if (!submission.ok) throw new Error(`Request failed (${submission.status})`);
  const { event_id: eventId } = await submission.json();
  if (!eventId) throw new Error("The model service did not return an event identifier");

  const stream = await fetch(`${endpoint}/${eventId}`);
  if (!stream.ok || !stream.body) throw new Error(`Model stream failed (${stream.status})`);
  const reader = stream.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const payload = line.slice(5).trim();
        if (eventName === "complete") return JSON.parse(payload);
        if (eventName === "error") {
          throw new Error("The GPU worker is temporarily unavailable");
        }
      }
    }
    if (done) break;
  }
  throw new Error("The model stream ended before returning a result");
}

export async function withHistAgentService(service, operation) {
  const errors = [];
  for (const space of orderedCandidates(service)) {
    try {
      const result = await operation(space);
      preferredSpace.set(service, space);
      return result;
    } catch (error) {
      errors.push(serviceError(error, `Could not connect to ${space}`));
    }
  }
  throw errors.at(-1) || new Error("The HistAgent service is temporarily unavailable");
}

export async function callHistAgentService(service, apiName, data) {
  return withHistAgentService(service, (space) => callGradioAt(space, apiName, data));
}
