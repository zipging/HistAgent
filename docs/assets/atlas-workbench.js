import { callHistAgentService } from "./histagent-services.js";

const ATLAS_IMAGE_QUERY_KEY = "histagent-atlas-image-query";
const ATLAS_EVIDENCE_QUERY_KEY = "histagent-atlas-evidence-query";
const ATLAS_TISSUE_MANIFEST = "https://huggingface.co/datasets/wli14/HistAgent-atlas-images/resolve/main/manifest.json";

const form = document.querySelector("#atlas-search-form");
const searchButton = document.querySelector("#atlas-search-button");
const queryInput = document.querySelector("#atlas-query");
const speciesInput = document.querySelector("#atlas-species");
const organInput = document.querySelector("#atlas-organ");
const slideInput = document.querySelector("#atlas-slide");
const mapExample = document.querySelector("#atlas-tissue-example");
const exampleCanvas = document.querySelector(".atlas-tissue-canvas");
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
const imageAnalyzeLink = document.querySelector("#atlas-image-analyze");
const evidenceChips = document.querySelector("#query-evidence-chips");
const mapControls = [...document.querySelectorAll("[data-map-action]")];
const evidenceFilterInputs = [
  document.querySelector("#atlas-cell-filter"),
  document.querySelector("#atlas-pathway-filter"),
  document.querySelector("#atlas-microenvironment-filter")
].filter(Boolean);

const manuscriptExampleEvidence = {
  spot: {
    slice_id: "GSE203612_GSM6177603",
    species: "human",
    organ: "breast"
  },
  ranked_genes: ["SCGB2A2", "SCGB2A1", "TFF3", "TMSB10", "EEF1A1", "KRT19", "FTL", "CD74"],
  cell_type_composition: ["Cancer cell", "CD8 T cell", "Fibroblast", "Epithelial cell"],
  pathway_evidence: {
    innate_immune_system: ["CD74", "HLA-B"],
    antigen_processing_and_cross_presentation: ["CD74", "HLA-B"]
  },
  spatial_context: {
    available: true,
    neighborhood_consensus: {
      label: "tumor-associated epithelial and immune context"
    }
  }
};

let topEvidence = manuscriptExampleEvidence;
let chatHistory = [];
let activeMode = "text";
let exampleZoom = 1;
let liveMapHome = null;
let liveMapView = null;
let stagedImagePayload = null;

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

const exampleSpotCoordinates = [[22.9,22.68],[37.09,34.65],[28.56,76.14],[25.35,16.13],[41.42,44.46],[48.18,36.81],[46.95,36.81],[19.82,21.6],[22.96,48.87],[33.98,18.29],[24.23,66.33],[51.36,85.91],[24.14,27.05],[35.21,20.47],[18.61,32.51],[11.89,55.44],[59.32,60.79],[16.77,33.61],[58.07,52.06],[35.22,24.84],[37.18,73.94],[54.96,37.88],[24.82,54.32],[20.5,53.24],[49.49,71.72],[54.98,46.61]];

const exampleRetrievedCoordinates = [
  { x: 24.14, y: 27.05, rank: 1, kind: "top" },
  { x: 16.77, y: 33.61, rank: 2, kind: "matched" }
];

function fitExampleCanvas() {
  if (!mapExample || !exampleCanvas) return;
  const image = exampleCanvas.querySelector("img");
  const width = image?.naturalWidth || 600;
  const height = image?.naturalHeight || 589;
  const scale = Math.min(mapExample.clientWidth / width, mapExample.clientHeight / height);
  exampleCanvas.style.width = `${width * scale}px`;
  exampleCanvas.style.height = `${height * scale}px`;
}

function seedDots() {
  const layer = document.querySelector("#atlas-dot-layer");
  if (!layer || layer.childElementCount) return;
  const dots = exampleSpotCoordinates.map(([x, y]) => {
    const dot = document.createElement("span");
    dot.className = "atlas-map-dot other";
    dot.style.left = `${x}%`;
    dot.style.top = `${y}%`;
    return dot;
  });
  exampleRetrievedCoordinates.forEach(({ x, y, rank, kind }) => {
    const dot = document.createElement("span");
    dot.className = `atlas-map-dot ${kind}`;
    dot.style.left = `${x}%`;
    dot.style.top = `${y}%`;
    dot.textContent = String(rank);
    dots.push(dot);
  });
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
  const selectedFilters = evidenceFilterInputs.map((input) => input.value).filter(Boolean);
  const terms = [...selectedFilters, ...queryTerms(query)]
    .filter((term, index, values) => values.indexOf(term) === index)
    .slice(0, 5);
  evidenceChips.innerHTML = terms.length
    ? terms.map((term) => `<span>${escapeHtml(term)}</span>`).join("")
    : "<span>Biological state</span>";
}

function evidenceLabels(value) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => typeof item === "string" ? item : item?.label)
    .filter(Boolean);
}

function evidenceCardQuery(evidence) {
  const genes = (evidence?.ranked_genes || evidence?.top_genes || []).slice(0, 50);
  const cells = evidenceLabels(evidence?.cell_type_composition);
  const programs = Object.keys(evidence?.pathway_evidence || {});
  const spatial = evidence?.spatial_context?.interpretation
    || evidence?.spatial_context?.neighborhood_consensus?.label
    || "";
  return [
    "HistAgent-predicted evidence card.",
    genes.length ? `Top-ranked genes: ${genes.join(", ")}.` : "",
    cells.length ? `Inferred cell composition: ${cells.join(", ")}.` : "",
    programs.length ? `Functional programs: ${programs.join(", ")}.` : "",
    spatial ? `Spatial context: ${spatial}.` : ""
  ].filter(Boolean).join("\n");
}

function evidenceChipText(evidence) {
  const cells = evidenceLabels(evidence?.cell_type_composition);
  const programs = Object.keys(evidence?.pathway_evidence || {});
  const genes = evidence?.ranked_genes || evidence?.top_genes || [];
  return [...cells, ...programs, ...genes].slice(0, 5).join(" ");
}

function dataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(reader.result), { once: true });
    reader.addEventListener("error", () => reject(reader.error || new Error("Could not read the image")), { once: true });
    reader.readAsDataURL(blob);
  });
}

async function prepareImagePayload(file) {
  if (!file?.type?.startsWith("image/")) throw new Error("Choose a PNG, JPEG or WebP tissue image.");
  if (file.size > 25 * 1024 * 1024) throw new Error("Choose an image smaller than 25 MB.");
  const bitmap = await createImageBitmap(file);
  const maximumSide = 1800;
  const scale = Math.min(1, maximumSide / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise((resolve, reject) => {
    canvas.toBlob(
      (value) => value ? resolve(value) : reject(new Error("Could not prepare the image query")),
      "image/jpeg",
      0.86
    );
  });
  return {
    dataUrl: await dataUrl(blob),
    name: file.name || "atlas-query-image.jpg",
    type: blob.type,
    species: speciesInput.value,
    organ: organInput.value
  };
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
    dragmode: "pan",
    font: { ...(plot.layout.font || {}), family: "Inter, ui-sans-serif, system-ui, sans-serif" },
    uirevision: "atlas-live-map"
  };
  liveMapHome = {
    x: Array.isArray(layout.xaxis?.range) ? [...layout.xaxis.range] : null,
    y: Array.isArray(layout.yaxis?.range) ? [...layout.yaxis.range] : null
  };
  liveMapView = {
    x: liveMapHome.x ? [...liveMapHome.x] : null,
    y: liveMapHome.y ? [...liveMapHome.y] : null
  };
  window.Plotly.react(plotTarget, plot.data, layout, {
    responsive: true,
    scrollZoom: true,
    doubleClick: "reset",
    displaylogo: false,
    displayModeBar: false
  });
  return true;
}

function zoomLivePlot(factor) {
  const xRange = liveMapView?.x || liveMapHome?.x;
  const yRange = liveMapView?.y || liveMapHome?.y;
  if (!Array.isArray(xRange) || !Array.isArray(yRange)) return;
  const scaled = (range) => {
    const center = (Number(range[0]) + Number(range[1])) / 2;
    const half = Math.abs(Number(range[1]) - Number(range[0])) * factor / 2;
    return Number(range[0]) <= Number(range[1])
      ? [center - half, center + half]
      : [center + half, center - half];
  };
  liveMapView = { x: scaled(xRange), y: scaled(yRange) };
  window.Plotly.relayout(plotTarget, {
    "xaxis.range": liveMapView.x,
    "yaxis.range": liveMapView.y
  });
}

function controlMap(action) {
  if (!plotTarget.hidden && window.Plotly) {
    if (action === "zoom-in") zoomLivePlot(0.72);
    if (action === "zoom-out") zoomLivePlot(1.38);
    if (action === "reset") {
      const resetLayout = liveMapHome?.x && liveMapHome?.y
        ? { "xaxis.range": liveMapHome.x, "yaxis.range": liveMapHome.y }
        : { "xaxis.autorange": true, "yaxis.autorange": "reversed" };
      liveMapView = {
        x: liveMapHome?.x ? [...liveMapHome.x] : null,
        y: liveMapHome?.y ? [...liveMapHome.y] : null
      };
      window.Plotly.relayout(plotTarget, resetLayout);
    }
    return;
  }
  if (action === "zoom-in") exampleZoom = Math.min(3, exampleZoom * 1.25);
  if (action === "zoom-out") exampleZoom = Math.max(1, exampleZoom / 1.25);
  if (action === "reset") exampleZoom = 1;
  mapExample.style.transform = `scale(${exampleZoom})`;
}

mapControls.forEach((button) => {
  button.addEventListener("click", () => controlMap(button.dataset.mapAction));
});

function setBusy(value) {
  searchButton.disabled = value;
  loading.hidden = !value;
  searchButton.textContent = value ? "Searching…" : activeMode === "image" ? "Open HistAgent" : "Search";
}

function showSearchError(error) {
  const quotaMessage = /quota|ZeroGPU/i.test(String(error));
  statusBadge.className = "atlas-status-badge";
  statusBadge.textContent = "Example view";
  resultSummary.textContent = quotaMessage
    ? "Live retrieval is temporarily unavailable · manuscript example remains visible"
    : "Live retrieval could not start · manuscript example remains visible";
}

async function runRetrieval(query, chipText = query) {
  setBusy(true);
  updateChips(chipText);
  try {
    const data = await callHistAgentService("reasoning", "retrieve_atlas", [
      query,
      speciesInput.value,
      organInput.value,
      slideInput.value,
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

async function loadReadySlides() {
  if (!slideInput) return;
  try {
    const response = await fetch(ATLAS_TISSUE_MANIFEST, { cache: "no-store" });
    if (!response.ok) throw new Error(`Atlas slide manifest failed (${response.status})`);
    const payload = await response.json();
    const slides = Object.entries(payload?.slides || {})
      .filter(([, record]) => record?.source !== "sampled_contextual_h_and_e_patches")
      .map(([slideId]) => slideId)
      .sort((left, right) => left.localeCompare(right));
    slideInput.replaceChildren(new Option(`All image-ready slides (${slides.length})`, "__ready__"));
    slides.forEach((slideId) => slideInput.add(new Option(slideId, slideId)));
  } catch (error) {
    console.error(error);
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
  const genes = (evidence?.ranked_genes || evidence?.top_genes || []).slice(0, 6).join(", ");
  const cells = evidenceLabels(evidence?.cell_type_composition);
  if (evidence?.dominant_cell_type) cells.unshift(evidence.dominant_cell_type);
  const cellText = [...new Set(cells)].slice(0, 4).join(", ");
  const programs = (evidence?.reactome_pathways || [])
    .map((item) => typeof item === "string" ? item : item?.pathway)
    .filter(Boolean)
    .slice(0, 4)
    .join(", ");
  const spatial = evidence?.spatial_context?.neighborhood_consensus?.label
    || evidence?.spatial_context?.interpretation
    || evidence?.organ
    || "the retrieved tissue region";
  if (/cell|composition|type/.test(prompt)) {
    return cellText
      ? `The retrieved evidence supports ${cellText}.`
      : "The retrieved evidence card does not provide a confident cell-state assignment.";
  }
  if (/gene|marker|evidence/.test(prompt)) {
    return genes
      ? `The main molecular evidence includes ${genes}.`
      : "No ranked genes are available in the selected evidence card.";
  }
  if (/pathway|program|process/.test(prompt)) {
    return programs
      ? `The supported functional programs include ${programs}.`
      : "No functional program is available in the selected evidence card.";
  }
  if (/where|spatial|location|adjacent|margin/.test(prompt)) {
    return `The spatial evidence places the retrieved state in ${spatial}.`;
  }
  return `The top-ranked measured ST spot supports ${cellText || "a spatial molecular state"}${genes ? ` through ${genes}` : ""}${programs ? `, with programs including ${programs}` : ""}.`;
}

function setQueryMode(mode) {
  activeMode = mode === "image" ? "image" : "text";
  document.querySelectorAll("[data-query-mode]").forEach((item) => {
    item.setAttribute("aria-selected", String(item.dataset.queryMode === activeMode));
  });
  const isImage = activeMode === "image";
  textFilters.hidden = isImage;
  imagePanel.hidden = !isImage;
  modeLabel.textContent = isImage ? "Image" : "Text";
  queryInput.placeholder = isImage ? "Upload an H&E query image" : "Describe a biological state";
  searchButton.textContent = isImage ? "Open HistAgent" : "Search";
}

function openImageInHistAgent() {
  if (!stagedImagePayload) {
    imageInput.focus();
    return;
  }
  window.location.href = "/histagent/?return=atlas";
}

form?.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (activeMode === "image") {
    openImageInHistAgent();
    return;
  }
  if (!query) {
    queryInput.focus();
    return;
  }
  const selectedFilters = evidenceFilterInputs.map((input) => input.value).filter(Boolean);
  const retrievalQuery = selectedFilters.length
    ? `${query}. Evidence constraints: ${selectedFilters.join("; ")}.`
    : query;
  runRetrieval(retrievalQuery);
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
    const data = await callHistAgentService("reasoning", "answer_atlas_question", [
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

chatInput?.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey) return;
  if (event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  if (!chatButton.disabled) chatForm.requestSubmit();
});

document.querySelectorAll("[data-query-mode]").forEach((button) => {
  button.addEventListener("click", () => setQueryMode(button.dataset.queryMode));
});

imageInput?.addEventListener("change", async () => {
  const file = imageInput.files?.[0];
  if (!file) return;
  try {
    stagedImagePayload = await prepareImagePayload(file);
    window.sessionStorage.setItem(ATLAS_IMAGE_QUERY_KEY, JSON.stringify(stagedImagePayload));
    imagePreview.src = stagedImagePayload.dataUrl;
    imagePreview.hidden = false;
    imageAnalyzeLink.removeAttribute("aria-disabled");
    queryInput.value = file.name;
    searchButton.textContent = "Open HistAgent";
  } catch (error) {
    console.error(error);
    queryInput.value = "";
    imagePreview.hidden = true;
  }
});

imageAnalyzeLink?.addEventListener("click", (event) => {
  if (stagedImagePayload) return;
  event.preventDefault();
  imageInput.focus();
});

evidenceFilterInputs.forEach((input) => {
  input.addEventListener("change", () => updateChips(queryInput.value));
});

seedDots();
fitExampleCanvas();
loadReadySlides();
if (typeof ResizeObserver !== "undefined" && mapExample) {
  new ResizeObserver(fitExampleCanvas).observe(mapExample);
} else {
  window.addEventListener("resize", fitExampleCanvas);
}

let transferredEvidence = null;
try {
  transferredEvidence = JSON.parse(window.sessionStorage.getItem(ATLAS_EVIDENCE_QUERY_KEY) || "null");
  if (transferredEvidence) window.sessionStorage.removeItem(ATLAS_EVIDENCE_QUERY_KEY);
} catch (_error) {
  transferredEvidence = null;
}

if (transferredEvidence) {
  setQueryMode("image");
  const sourceName = transferredEvidence?.provenance?.image_name || "HistAgent image query";
  queryInput.value = sourceName;
  const species = transferredEvidence?.spot?.species;
  const organ = transferredEvidence?.spot?.organ;
  if ([...speciesInput.options].some((option) => option.value === species)) speciesInput.value = species;
  if ([...organInput.options].some((option) => option.value === organ)) organInput.value = organ;
  runRetrieval(evidenceCardQuery(transferredEvidence), evidenceChipText(transferredEvidence));
} else if (new URLSearchParams(window.location.search).get("mode") === "image") {
  setQueryMode("image");
}
