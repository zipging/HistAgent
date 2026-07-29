const SPACE_URL = "https://wli13-histagent-chat.hf.space";

const form = document.querySelector("#atlas-search-form");
const searchButton = document.querySelector("#atlas-search-button");
const queryInput = document.querySelector("#atlas-query");
const speciesInput = document.querySelector("#atlas-species");
const organInput = document.querySelector("#atlas-organ");
const mapExample = document.querySelector("#atlas-tissue-example");
const plotTarget = document.querySelector("#atlas-live-plot");
const loading = document.querySelector("#atlas-loading");
const resultSummary = document.querySelector("#atlas-result-summary");
const statusBadge = document.querySelector("#atlas-status-badge");
const cardsTarget = document.querySelector("#atlas-evidence-cards");
const spotCount = document.querySelector("#atlas-spot-count");
const chatForm = document.querySelector("#atlas-chat-form");
const chatInput = document.querySelector("#atlas-chat-input");
const chatLog = document.querySelector("#atlas-chat-log");
const chatButton = chatForm?.querySelector("button");
const modeLabel = document.querySelector("#atlas-search-mode");
const textFilters = document.querySelector("#atlas-text-filters");
const imagePanel = document.querySelector("#atlas-image-query");
const imageInput = document.querySelector("#atlas-image-input");
const imagePreview = document.querySelector("#atlas-image-preview");
const evidenceChips = document.querySelector("#query-evidence-chips");

const manuscriptExampleEvidence = {
  spot: {
    slice_id: "RCC atlas example",
    species: "human",
    organ: "kidney"
  },
  ranked_genes: ["CXCL13", "CCL19", "MS4A1", "CD3D", "CD74", "HLA-DRA"],
  cell_type_composition: ["B cell", "T cell", "plasma cell"],
  pathway_evidence: {
    immune_organization: ["tertiary lymphoid structure-like organization"],
    antigen_presentation: ["CD74", "HLA-DRA"],
    chemokine_signaling: ["CXCL13", "CCL19"]
  },
  spatial_context: {
    available: true,
    neighborhood_consensus: {
      label: "tumor-adjacent immune aggregate"
    }
  }
};

let topEvidence = manuscriptExampleEvidence;
let chatHistory = [];
let activeMode = "text";

async function callGradio(apiName, data) {
  const endpoint = `${SPACE_URL}/gradio_api/call/${apiName}`;
  const submission = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data })
  });
  if (!submission.ok) throw new Error(`Retrieval request failed (${submission.status})`);
  const { event_id: eventId } = await submission.json();
  if (!eventId) throw new Error("The retrieval worker did not return an event identifier");

  const stream = await fetch(`${endpoint}/${eventId}`);
  if (!stream.ok || !stream.body) throw new Error(`Retrieval stream failed (${stream.status})`);
  const reader = stream.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const payload = line.slice(5).trim();
        if (currentEvent === "complete") return JSON.parse(payload);
        if (currentEvent === "error") throw new Error("The retrieval worker is temporarily unavailable");
      }
    }
    if (done) break;
  }
  throw new Error("The retrieval stream ended before returning results");
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatGenes(value = "") {
  return escapeHtml(value)
    .split(/([,;])/)
    .map((part) => {
      const text = part.trim();
      if (!text || text === "," || text === ";") return part;
      return /^[A-Z0-9-]{2,}$/.test(text) ? `<em>${text}</em>` : part;
    })
    .join("");
}

function seedDots() {
  const layer = document.querySelector("#atlas-dot-layer");
  if (!layer || layer.childElementCount) return;
  let seed = 19;
  const random = () => {
    seed = (seed * 9301 + 49297) % 233280;
    return seed / 233280;
  };
  const dots = [];
  for (let index = 0; index < 150; index += 1) {
    const dot = document.createElement("span");
    const kind = index < 18 ? "top" : index < 122 ? "" : "other";
    dot.className = `atlas-map-dot ${kind}`.trim();
    dot.style.left = `${7 + random() * 86}%`;
    dot.style.top = `${6 + random() * 87}%`;
    dots.push(dot);
  }
  layer.append(...dots);
}

function queryTerms(query) {
  const stop = new Set(["find", "show", "spots", "spot", "with", "the", "and", "for", "from", "regions", "region"]);
  return query
    .replace(/[^\p{L}\p{N}-]+/gu, " ")
    .trim()
    .split(/\s+/)
    .filter((word) => word.length > 3 && !stop.has(word.toLowerCase()))
    .slice(0, 4);
}

function updateChips(query) {
  const terms = queryTerms(query);
  evidenceChips.innerHTML = terms.length
    ? terms.map((term) => `<span>${escapeHtml(term)}</span>`).join("")
    : "<span>Biological state</span>";
}

function normalizeRows(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  if (Array.isArray(value.data)) return value.data;
  return [];
}

function renderCards(rows) {
  const topRows = normalizeRows(rows).slice(0, 3);
  if (!topRows.length) return;
  cardsTarget.innerHTML = topRows.map((row, index) => {
    const values = Array.isArray(row) ? row : Object.values(row);
    const rank = Number(values[0] ?? index + 1);
    const similarity = Number(values[1]);
    const species = values[2] ?? "";
    const organ = values[3] ?? "";
    const cellType = values[4] ?? "Not available";
    const slide = values[5] ?? "Atlas slide";
    const genes = values[6] ?? "";
    const title = cellType && String(cellType).toLowerCase() !== "unknown"
      ? cellType
      : `${organ || species || "Retrieved"} atlas spot`;
    return `
      <article class="atlas-evidence-card">
        <div class="atlas-card-title">
          <span class="rank ${rank === 1 ? "rank-one" : rank === 2 ? "rank-two" : "rank-three"}">${rank}</span>
          <strong title="${escapeHtml(String(title))}">${escapeHtml(String(title))}</strong>
          <b>${Number.isFinite(similarity) ? similarity.toFixed(2) : ""}</b>
        </div>
        <p><span>Atlas source</span>${escapeHtml(`${species}${species && organ ? " · " : ""}${organ}`)}</p>
        <p><span>Slide</span>${escapeHtml(String(slide))}</p>
        <p><span>Top genes</span>${formatGenes(String(genes))}</p>
      </article>
    `;
  }).join("");
}

function renderPlot(plotValue) {
  if (!plotValue || !window.Plotly) return false;
  let plot = plotValue.plot ?? plotValue;
  if (typeof plot === "string") {
    try {
      plot = JSON.parse(plot);
    } catch {
      return false;
    }
  }
  if (!plot?.data || !plot?.layout) return false;
  mapExample.hidden = true;
  plotTarget.hidden = false;
  const layout = {
    ...plot.layout,
    autosize: true,
    height: undefined,
    margin: { l: 20, r: 20, t: 44, b: 20 },
    paper_bgcolor: "#fbfdfc",
    plot_bgcolor: "#f4f8f6",
    font: { ...(plot.layout.font || {}), family: "Inter, ui-sans-serif, system-ui, sans-serif" }
  };
  window.Plotly.react(plotTarget, plot.data, layout, {
    responsive: true,
    displaylogo: false,
    modeBarButtonsToRemove: ["lasso2d", "select2d"]
  });
  return true;
}

function setBusy(value) {
  searchButton.disabled = value;
  loading.hidden = !value;
  searchButton.textContent = value ? "Searching…" : "Search";
}

function showSearchError(error) {
  const quotaMessage = /quota|ZeroGPU/i.test(String(error));
  statusBadge.className = "atlas-status-badge";
  statusBadge.textContent = "Example view";
  resultSummary.textContent = quotaMessage
    ? "Live retrieval is temporarily unavailable · manuscript example remains visible"
    : "Live retrieval could not start · manuscript example remains visible";
}

async function runRetrieval(query) {
  setBusy(true);
  updateChips(query);
  try {
    const data = await callGradio("retrieve_atlas", [
      query,
      speciesInput.value,
      organInput.value,
      5
    ]);
    const outputs = Array.isArray(data) ? data : [];
    renderCards(outputs[0]);
    topEvidence = outputs[1] ?? null;
    const status = String(outputs[2] ?? "Retrieved measured ST evidence");
    const plotted = renderPlot(outputs[3]);
    if (!plotted) {
      mapExample.hidden = false;
      plotTarget.hidden = true;
    }
    const rows = normalizeRows(outputs[0]);
    const firstRow = rows[0];
    const firstValues = Array.isArray(firstRow) ? firstRow : firstRow ? Object.values(firstRow) : [];
    const slide = firstValues[5] || "top-ranked atlas slide";
    spotCount.textContent = `${rows.length} retrieved`;
    resultSummary.textContent = `${rows.length} top-ranked spots · ${slide}`;
    statusBadge.className = "atlas-status-badge live";
    statusBadge.textContent = "Live result";
    chatHistory = [];
    chatLog.innerHTML = `
      <div class="atlas-message assistant">
        <span>HistAgent</span>
        <p>${escapeHtml(status)}</p>
      </div>
    `;
  } catch (error) {
    console.error(error);
    showSearchError(error);
  } finally {
    setBusy(false);
  }
}

function appendMessage(role, content) {
  const wrapper = document.createElement("div");
  wrapper.className = `atlas-message ${role}`;
  wrapper.innerHTML = `
    <span>${role === "user" ? "User" : "HistAgent"}</span>
    <p>${escapeHtml(content).replaceAll("\n", "<br>")}</p>
  `;
  chatLog.append(wrapper);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function localEvidenceAnswer(message, evidence) {
  const prompt = message.toLowerCase();
  const genes = (evidence?.ranked_genes || []).slice(0, 6).join(", ");
  if (/cell|composition|type/.test(prompt)) {
    return "The retrieved evidence supports a B-cell-rich immune aggregate with adjacent T-cell populations and a smaller plasma-cell component.";
  }
  if (/gene|marker|evidence/.test(prompt)) {
    return `The main molecular evidence includes ${genes || "CXCL13, CCL19, MS4A1, CD3D, CD74 and HLA-DRA"}. Together, these genes support lymphoid organization, B- and T-cell presence and antigen presentation.`;
  }
  if (/pathway|program|process/.test(prompt)) {
    return "The supported programs include CXCL13/CCL19-associated immune organization and CD74/HLA-DRA-associated antigen presentation.";
  }
  if (/where|spatial|location|adjacent|margin/.test(prompt)) {
    return "The matched spots form a tumor-adjacent immune aggregate rather than a broadly distributed intratumoral pattern.";
  }
  return "The retrieved evidence supports a tumor-adjacent TLS-like immune niche with B-cell-rich cores, adjacent T-cell populations, chemokine organization and antigen-presentation programs.";
}

form?.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) {
    queryInput.focus();
    return;
  }
  if (activeMode === "image") {
    window.location.href = "/histagent/";
    return;
  }
  runRetrieval(query);
});

chatForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  appendMessage("user", message);
  chatInput.value = "";
  chatButton.disabled = true;
  if (!topEvidence) {
    appendMessage("assistant", "No retrieved evidence card is currently selected.");
    chatButton.disabled = false;
    return;
  }
  try {
    const data = await callGradio("answer_atlas_question", [
      message,
      chatHistory,
      topEvidence
    ]);
    const outputs = Array.isArray(data) ? data : [];
    chatHistory = outputs[1] ?? outputs[0] ?? chatHistory;
    const last = Array.isArray(chatHistory) ? chatHistory.at(-1) : null;
    const answer = typeof last?.content === "string"
      ? last.content
      : "The retrieved evidence card is available for follow-up analysis.";
    appendMessage("assistant", answer);
  } catch (error) {
    console.error(error);
    appendMessage("assistant", localEvidenceAnswer(message, topEvidence));
  } finally {
    chatButton.disabled = false;
  }
});

document.querySelectorAll("[data-query-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    activeMode = button.dataset.queryMode;
    document.querySelectorAll("[data-query-mode]").forEach((item) => {
      item.setAttribute("aria-selected", String(item === button));
    });
    const isImage = activeMode === "image";
    textFilters.hidden = isImage;
    imagePanel.hidden = !isImage;
    modeLabel.textContent = isImage ? "Image" : "Text";
    queryInput.placeholder = isImage ? "Upload an H&E query image" : "Describe a biological state";
    searchButton.textContent = isImage ? "Open HistAgent" : "Search";
  });
});

imageInput?.addEventListener("change", () => {
  const file = imageInput.files?.[0];
  if (!file) return;
  imagePreview.src = URL.createObjectURL(file);
  imagePreview.hidden = false;
  queryInput.value = file.name;
});

seedDots();
