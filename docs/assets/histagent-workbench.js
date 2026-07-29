const INFERENCE_SPACE = "https://wli13-histagent-demo.hf.space";
const CHAT_SPACE = "https://wli13-histagent-chat.hf.space";
const LOCAL_DIAMETER_UM = 55;
const CONTEXT_DIAMETER_UM = 220;

const tissueImage = document.querySelector("#histagent-tissue-image");
const tissueStage = document.querySelector("#histagent-tissue-stage");
const spotGrid = document.querySelector("#histagent-spot-grid");
const contextRing = document.querySelector("#histagent-context-ring");
const fileInput = document.querySelector("#histagent-file");
const uploadZone = document.querySelector("#histagent-upload-zone");
const exampleButton = document.querySelector("#histagent-example");
const mppInput = document.querySelector("#histagent-mpp");
const speciesInput = document.querySelector("#histagent-species");
const organInput = document.querySelector("#histagent-organ");
const generateButton = document.querySelector("#histagent-generate");
const stageLoading = document.querySelector("#histagent-stage-loading");
const spotCount = document.querySelector("#histagent-spot-count");
const spotId = document.querySelector("#selected-spot-id");
const selectedCoordinates = document.querySelector("#histagent-selected-coordinates");
const localPreview = document.querySelector("#histagent-local-preview");
const contextPreview = document.querySelector("#histagent-context-preview");
const imageName = document.querySelector("#histagent-image-name");
const imageMeta = document.querySelector("#histagent-image-meta");
const selectionBadge = document.querySelector("#histagent-selection-badge");
const runStatus = document.querySelector("#histagent-run-status");
const evidenceBadge = document.querySelector("#histagent-evidence-badge");
const geneCount = document.querySelector("#histagent-gene-count");
const geneList = document.querySelector("#histagent-gene-list");
const evidenceFields = document.querySelector("#histagent-evidence-fields");
const chatForm = document.querySelector("#histagent-chat-form");
const chatInput = document.querySelector("#histagent-chat-input");
const chatButton = chatForm?.querySelector("button");
const chatLog = document.querySelector("#histagent-chat-log");

const defaultGenes = [
  "IGKC", "IGHG1", "COL1A1", "UBC", "TMSB4X", "COL1A2", "IGFBP7",
  "COL3A1", "ACTB", "B2M", "MT2A", "IGLC1", "FN1", "VIM", "TIMP1",
  "FTL", "SPARC", "IGHG2", "UBA52", "PABPC1", "CD74", "CCN2", "EEF2",
  "LUM", "DCN", "BGN", "FTH1", "IFITM3", "IGLV3-1", "MT1E", "S100A6",
  "C1R", "C3", "VCAN", "IGHA1", "CD63", "RACK1", "AEBP1", "EEF1G",
  "HLA-DRA", "TPM1", "ITM2B", "MYL9", "JCHAIN", "HTRA1", "C7", "ACTA2",
  "IGKV4-1", "PFN1", "PSAP"
];

const markerCatalog = {
  cells: [
    { label: "B cell", markers: ["MS4A1", "CD79A", "CD74", "CD37", "CD79B", "CD22", "IGKC", "CD19"] },
    { label: "Plasma cell", markers: ["JCHAIN", "MZB1", "XBP1", "IGHG1", "IGHA1", "SDC1"] },
    { label: "T cell", markers: ["CD3D", "CD3E", "CD3G", "TRBC1", "TRBC2", "IL7R"] },
    { label: "Myeloid cell", markers: ["C1QA", "C1QB", "C1QC", "LYZ", "CTSS", "FCER1G", "TYROBP"] },
    { label: "Stromal cell", markers: ["COL1A1", "COL1A2", "COL3A1", "DCN", "LUM", "COL6A1", "COL6A2"] },
    { label: "Endothelial cell", markers: ["PECAM1", "VWF", "EMCN", "KDR", "ENG", "RAMP2"] },
    { label: "Epithelial cell", markers: ["EPCAM", "KRT8", "KRT18", "KRT19", "KRT7", "MUC1"] },
    { label: "Neuron", markers: ["NRGN", "SNAP25", "SYT1", "UCHL1", "NEFL", "RBFOX3"] },
    { label: "Astrocyte", markers: ["GFAP", "AQP4", "SLC1A3", "ALDOC", "CST3", "CLU", "SPARCL1"] },
    { label: "Oligodendrocyte", markers: ["MBP", "PLP1", "MOG", "MAG", "CNP", "CLDN11"] },
    { label: "Cardiomyocyte", markers: ["MYH6", "MYH7", "TNNT2", "ACTC1", "MYL2", "TNNI3"] }
  ],
  programs: [
    { label: "Antigen presentation", markers: ["CD74", "HLA-DRA", "HLA-DRB1", "HLA-DPA1", "HLA-DPB1", "B2M"] },
    { label: "Lymphoid organization", markers: ["CXCL13", "CCL19", "CCL21", "LTB", "LTA", "IL7R"] },
    { label: "Immunoglobulin production", markers: ["IGKC", "IGHG1", "IGHG2", "IGHM", "IGHA1", "JCHAIN", "MZB1"] },
    { label: "Extracellular matrix organization", markers: ["COL1A1", "COL1A2", "COL3A1", "DCN", "LUM", "COL6A1"] },
    { label: "Epithelial identity", markers: ["EPCAM", "KRT8", "KRT18", "KRT19", "KRT7"] },
    { label: "Cell proliferation", markers: ["MKI67", "TOP2A", "STMN1", "TUBA1B", "HMGB2", "CENPF"] },
    { label: "Neuronal activity", markers: ["NRGN", "SNAP25", "SYT1", "UCHL1", "NEFL", "GPM6A"] },
    { label: "Myelination", markers: ["MBP", "PLP1", "MOG", "MAG", "CNP", "CLDN11"] },
    { label: "Muscle contraction", markers: ["MYH6", "MYH7", "TNNT2", "ACTC1", "MYL2", "TNNI3"] },
    { label: "Oxidative phosphorylation", markers: ["NDUFA4", "COX6C", "COX6A1", "COX4I1", "COX5B", "ATP5F1E"] }
  ]
};

let sourceFile = null;
let sourceUrl = "/assets/rcc-tissue.jpg";
let sourceLabel = "RCC tissue example · GSM5924038";
let spots = [];
let selectedSpot = null;
let currentEvidence = buildEvidence(defaultGenes, {
  source: "manuscript RCC example",
  spatialLabel: "Selected spot with surrounding tissue context"
});
let chatHistory = [];
let evidenceSpotKey = "default";
let isDefaultExample = true;

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(message, error = false) {
  runStatus.textContent = message;
  runStatus.setAttribute("data-error", String(error));
}

function setBusy(value) {
  generateButton.disabled = value;
  generateButton.textContent = value ? "Generating…" : "Generate spot evidence";
  stageLoading.hidden = !value;
}

function setWorkflowStep(name) {
  document.querySelectorAll(".histagent-steps li").forEach((step) => {
    const stepName = step.dataset.step;
    step.classList.remove("active", "complete");
    if (name === "image") {
      if (stepName === "image") step.classList.add("active");
    } else if (name === "spot") {
      if (stepName === "image") step.classList.add("complete");
      if (stepName === "spot") step.classList.add("active");
    } else {
      if (stepName === "image" || stepName === "spot") step.classList.add("complete");
      if (stepName === "evidence") step.classList.add("active");
    }
  });
}

function displayBox() {
  if (!tissueImage.naturalWidth || !tissueImage.naturalHeight) return null;
  const stageWidth = tissueStage.clientWidth;
  const stageHeight = tissueStage.clientHeight;
  const scale = Math.min(
    stageWidth / tissueImage.naturalWidth,
    stageHeight / tissueImage.naturalHeight
  );
  const width = tissueImage.naturalWidth * scale;
  const height = tissueImage.naturalHeight * scale;
  return {
    left: (stageWidth - width) / 2,
    top: (stageHeight - height) / 2,
    width,
    height,
    scale
  };
}

function imageTissueMask() {
  const limit = 280;
  const ratio = Math.min(limit / tissueImage.naturalWidth, limit / tissueImage.naturalHeight, 1);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(tissueImage.naturalWidth * ratio));
  canvas.height = Math.max(1, Math.round(tissueImage.naturalHeight * ratio));
  const context = canvas.getContext("2d", { willReadFrequently: true });
  try {
    context.drawImage(tissueImage, 0, 0, canvas.width, canvas.height);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    return {
      isTissue(x, y) {
        const px = Math.max(0, Math.min(canvas.width - 1, Math.round(x * ratio)));
        const py = Math.max(0, Math.min(canvas.height - 1, Math.round(y * ratio)));
        const offset = (py * canvas.width + px) * 4;
        const red = pixels[offset];
        const green = pixels[offset + 1];
        const blue = pixels[offset + 2];
        const brightness = (red + green + blue) / 3;
        const spread = Math.max(red, green, blue) - Math.min(red, green, blue);
        return brightness < 232 || (brightness < 242 && spread > 30);
      }
    };
  } catch {
    return { isTissue: () => true };
  }
}

function createSpots(preserveEvidence = false) {
  if (!tissueImage.naturalWidth || !tissueImage.naturalHeight) return;
  const mpp = Number(mppInput.value);
  if (!Number.isFinite(mpp) || mpp <= 0) {
    setStatus("Enter a valid image scale before dividing the image into spots.", true);
    return;
  }
  const physicalSpacingPixels = 80 / mpp;
  const minimumSpacingForDisplay = Math.sqrt(
    (tissueImage.naturalWidth * tissueImage.naturalHeight) / 360
  );
  const spacing = Math.max(physicalSpacingPixels, minimumSpacingForDisplay);
  const margin = spacing / 2;
  const mask = imageTissueMask();
  const nextSpots = [];
  let row = 0;
  for (let y = margin; y < tissueImage.naturalHeight - margin / 2; y += spacing) {
    let column = 0;
    const offset = row % 2 ? spacing / 2 : 0;
    for (let x = margin + offset; x < tissueImage.naturalWidth - margin / 2; x += spacing) {
      if (mask.isTissue(x, y)) {
        nextSpots.push({
          id: `S${String(nextSpots.length + 1).padStart(3, "0")}`,
          x,
          y,
          xNorm: x / tissueImage.naturalWidth,
          yNorm: y / tissueImage.naturalHeight
        });
      }
      column += 1;
    }
    row += 1;
  }
  spots = nextSpots;
  const center = spots.reduce((best, spot) => {
    const distance = (spot.xNorm - 0.5) ** 2 + (spot.yNorm - 0.5) ** 2;
    return !best || distance < best.distance ? { spot, distance } : best;
  }, null)?.spot || spots[0];
  renderSpots();
  selectSpot(center, { preserveEvidence });
  spotCount.textContent = `${spots.length.toLocaleString()} spots`;
}

function renderSpots() {
  const box = displayBox();
  if (!box) return;
  const spotDiameter = Math.max(
    8,
    Math.min(12, (LOCAL_DIAMETER_UM / Number(mppInput.value)) * box.scale)
  );
  const fragment = document.createDocumentFragment();
  spots.forEach((spot) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "histagent-spot";
    button.dataset.spotId = spot.id;
    button.setAttribute("aria-label", `Select spot ${spot.id}`);
    button.setAttribute("aria-selected", String(selectedSpot?.id === spot.id));
    button.style.left = `${box.left + spot.xNorm * box.width}px`;
    button.style.top = `${box.top + spot.yNorm * box.height}px`;
    button.style.width = `${spotDiameter}px`;
    button.style.height = `${spotDiameter}px`;
    button.addEventListener("click", () => selectSpot(spot));
    fragment.append(button);
  });
  spotGrid.replaceChildren(fragment);
  positionContextRing();
}

function positionContextRing() {
  const box = displayBox();
  if (!box || !selectedSpot) return;
  const size = Math.max(
    24,
    Math.min(130, (CONTEXT_DIAMETER_UM / Number(mppInput.value)) * box.scale)
  );
  contextRing.style.left = `${box.left + selectedSpot.xNorm * box.width}px`;
  contextRing.style.top = `${box.top + selectedSpot.yNorm * box.height}px`;
  contextRing.style.width = `${size}px`;
  contextRing.style.height = `${size}px`;
}

function canvasCrop(centerX, centerY, diameterUm, outputSize = 224) {
  const mpp = Number(mppInput.value);
  const sourceSide = diameterUm / mpp;
  const canvas = document.createElement("canvas");
  canvas.width = outputSize;
  canvas.height = outputSize;
  const context = canvas.getContext("2d");
  context.fillStyle = "#fff";
  context.fillRect(0, 0, outputSize, outputSize);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";

  const sourceLeft = centerX - sourceSide / 2;
  const sourceTop = centerY - sourceSide / 2;
  const sx = Math.max(0, sourceLeft);
  const sy = Math.max(0, sourceTop);
  const sourceRight = Math.min(tissueImage.naturalWidth, sourceLeft + sourceSide);
  const sourceBottom = Math.min(tissueImage.naturalHeight, sourceTop + sourceSide);
  const sw = Math.max(0, sourceRight - sx);
  const sh = Math.max(0, sourceBottom - sy);
  if (sw && sh) {
    const scale = outputSize / sourceSide;
    context.drawImage(
      tissueImage,
      sx,
      sy,
      sw,
      sh,
      (sx - sourceLeft) * scale,
      (sy - sourceTop) * scale,
      sw * scale,
      sh * scale
    );
  }
  return canvas;
}

function updateCropPreviews() {
  if (!selectedSpot) return;
  const localCanvas = canvasCrop(selectedSpot.x, selectedSpot.y, LOCAL_DIAMETER_UM);
  const contextCanvas = canvasCrop(selectedSpot.x, selectedSpot.y, CONTEXT_DIAMETER_UM);
  localPreview.src = localCanvas.toDataURL("image/png");
  contextPreview.src = contextCanvas.toDataURL("image/png");
  selectedCoordinates.textContent =
    `Center ${Math.round(selectedSpot.x)}, ${Math.round(selectedSpot.y)} px`;
}

function clearEvidenceForSelection() {
  currentEvidence = null;
  evidenceSpotKey = "";
  evidenceBadge.textContent = "Not generated";
  geneCount.textContent = "Awaiting HistAgent";
  geneList.replaceChildren();
  evidenceFields.innerHTML = `
    <div class="histagent-evidence-empty">
      Generate the ranked molecular readout and evidence card for ${escapeHtml(selectedSpot?.id || "the selected spot")}.
    </div>
  `;
  chatHistory = [];
  chatLog.innerHTML = `
    <div class="atlas-message assistant">
      <span>HistAgent</span>
      <p>Generate evidence for the selected spot before starting the analysis.</p>
    </div>
  `;
  chatInput.disabled = true;
  chatButton.disabled = true;
}

function selectSpot(spot, options = {}) {
  if (!spot) return;
  const changed = selectedSpot?.id !== spot.id;
  selectedSpot = spot;
  spotId.textContent = spot.id;
  document.querySelectorAll(".histagent-spot").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.spotId === spot.id));
  });
  positionContextRing();
  updateCropPreviews();
  selectionBadge.className = "atlas-status-badge live";
  selectionBadge.textContent = "Spot selected";
  setWorkflowStep("spot");
  if (changed && !options.preserveEvidence) {
    clearEvidenceForSelection();
    setStatus(`${spot.id} selected. Generate its spot evidence.`);
  }
}

function matchedEvidence(genes, entries) {
  const geneSet = new Set(genes.map((gene) => gene.toUpperCase()));
  return entries
    .map((entry) => ({
      label: entry.label,
      genes: entry.markers.filter((gene) => geneSet.has(gene.toUpperCase()))
    }))
    .filter((entry) => entry.genes.length)
    .sort((a, b) => b.genes.length - a.genes.length)
    .slice(0, 4);
}

function buildEvidence(genes, options = {}) {
  const cleanGenes = genes
    .map((gene) => String(gene || "").trim())
    .filter(Boolean)
    .filter((gene, index, values) => values.indexOf(gene) === index)
    .slice(0, 50);
  const cells = options.cellOverride || matchedEvidence(cleanGenes, markerCatalog.cells);
  const programs = options.programOverride || matchedEvidence(cleanGenes, markerCatalog.programs);
  return {
    spot: {
      id: selectedSpot?.id || "S112",
      species: speciesInput?.value || "human",
      organ: organInput?.value || "kidney",
      x: selectedSpot ? Math.round(selectedSpot.x) : 759,
      y: selectedSpot ? Math.round(selectedSpot.y) : 875
    },
    ranked_genes: cleanGenes,
    cell_type_composition: cells,
    pathway_evidence: Object.fromEntries(programs.map((item) => [item.label, item.genes])),
    spatial_context: {
      available: true,
      selected_spot: selectedSpot?.id || "S112",
      local_diameter_um: LOCAL_DIAMETER_UM,
      context_diameter_um: CONTEXT_DIAMETER_UM,
      interpretation: options.spatialLabel || "Selected spot interpreted with surrounding tissue context"
    },
    display: {
      cells,
      programs,
      spatialLabel: options.spatialLabel || "Selected spot with surrounding tissue context"
    },
    provenance: {
      source: options.source || "HistAgent ranked molecular readout",
      image_name: sourceLabel
    }
  };
}

function geneMarkup(genes) {
  return genes.length
    ? genes.map((gene) => `<em>${escapeHtml(gene)}</em>`).join(", ")
    : "No supporting genes from the displayed marker groups";
}

function renderEvidence(evidence, badge = "Generated") {
  currentEvidence = evidence;
  evidenceSpotKey = selectedSpot?.id || "generated";
  const genes = evidence.ranked_genes || [];
  const cells = evidence.display?.cells || evidence.cell_type_composition || [];
  const programs = evidence.display?.programs || Object.entries(evidence.pathway_evidence || {})
    .map(([label, support]) => ({ label, genes: Array.isArray(support) ? support : [] }));
  evidenceBadge.textContent = badge;
  geneCount.textContent = `Top ${Math.min(20, genes.length)} of ${genes.length}`;
  geneList.innerHTML = genes.slice(0, 20)
    .map((gene) => `<li><em>${escapeHtml(gene)}</em></li>`)
    .join("");
  const cellLabel = cells.length ? cells.map((item) => item.label).join(" · ") : "No predefined cell marker group";
  const cellGenes = cells.flatMap((item) => item.genes || []).filter((gene, index, list) => list.indexOf(gene) === index);
  const programLabel = programs.length ? programs.map((item) => item.label).join(" · ") : "No predefined program marker group";
  const programGenes = programs.flatMap((item) => item.genes || []).filter((gene, index, list) => list.indexOf(gene) === index);
  evidenceFields.innerHTML = `
    <article>
      <span>Cell evidence</span>
      <strong>${escapeHtml(cellLabel)}</strong>
      <p>${geneMarkup(cellGenes)}</p>
    </article>
    <article>
      <span>Program evidence</span>
      <strong>${escapeHtml(programLabel)}</strong>
      <p>${geneMarkup(programGenes)}</p>
    </article>
    <article>
      <span>Spatial context</span>
      <strong>${escapeHtml(evidence.display?.spatialLabel || evidence.spatial_context?.interpretation || "Selected tissue spot")}</strong>
      <p>Selected 55 µm spot interpreted with its 220 µm surrounding context.</p>
    </article>
  `;
  chatHistory = [];
  chatInput.disabled = false;
  chatButton.disabled = false;
  chatLog.innerHTML = `
    <div class="atlas-message assistant">
      <span>HistAgent</span>
      <p>The evidence card for ${escapeHtml(selectedSpot?.id || "the selected spot")} is ready. Ask about its ranked genes, cellular states, functional programs or surrounding context.</p>
    </div>
  `;
  setWorkflowStep("evidence");
}

function normalizeRows(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  if (Array.isArray(value.data)) return value.data;
  return [];
}

function parseGenes(rows, sentence) {
  const fromRows = normalizeRows(rows)
    .map((row) => Array.isArray(row) ? row[1] : row?.Gene || row?.gene)
    .filter(Boolean);
  if (fromRows.length) return fromRows;
  return String(sentence || "")
    .replaceAll("→", ",")
    .split(/[\s,;|\n]+/)
    .map((value) => value.trim())
    .filter((value) => /^[A-Za-z][A-Za-z0-9.-]{1,24}$/.test(value))
    .slice(0, 50);
}

function canvasBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("Could not prepare the selected H&E view")), "image/png");
  });
}

async function uploadGradioFiles(files) {
  const form = new FormData();
  files.forEach(({ blob, name }) => form.append("files", blob, name));
  const response = await fetch(`${INFERENCE_SPACE}/gradio_api/upload`, {
    method: "POST",
    body: form
  });
  if (!response.ok) throw new Error(`Image upload failed (${response.status})`);
  const paths = await response.json();
  return paths.map((path, index) => ({
    path,
    orig_name: files[index].name,
    mime_type: "image/png",
    meta: { _type: "gradio.FileData" }
  }));
}

async function callGradio(space, apiName, data) {
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
        if (eventName === "error") throw new Error("The public model worker is temporarily unavailable");
      }
    }
    if (done) break;
  }
  throw new Error("The model stream ended before returning a result");
}

async function generateEvidence() {
  if (!selectedSpot) {
    setStatus("Select a tissue spot before generating evidence.", true);
    return;
  }
  const mpp = Number(mppInput.value);
  if (!Number.isFinite(mpp) || mpp <= 0 || LOCAL_DIAMETER_UM / mpp < 4) {
    setStatus("Enter an image scale that resolves the 55 µm spot.", true);
    return;
  }
  setBusy(true);
  setStatus(`Generating the ranked molecular readout for ${selectedSpot.id}.`);
  try {
    const localCanvas = canvasCrop(selectedSpot.x, selectedSpot.y, LOCAL_DIAMETER_UM);
    const contextCanvas = canvasCrop(selectedSpot.x, selectedSpot.y, CONTEXT_DIAMETER_UM);
    const [localBlob, contextBlob] = await Promise.all([
      canvasBlob(localCanvas),
      canvasBlob(contextCanvas)
    ]);
    const [localFile, contextFile] = await uploadGradioFiles([
      { blob: localBlob, name: `${selectedSpot.id}_local.png` },
      { blob: contextBlob, name: `${selectedSpot.id}_context.png` }
    ]);
    const outputs = await callGradio(INFERENCE_SPACE, "generate_ranked_readout", [
      localFile,
      contextFile,
      speciesInput.value,
      organInput.value,
      50
    ]);
    const genes = parseGenes(outputs?.[0], outputs?.[1]);
    if (!genes.length) throw new Error("HistAgent returned no ranked genes for this spot");
    const evidence = buildEvidence(genes, {
      source: "HistAgent ranked molecular readout"
    });
    renderEvidence(evidence, "Generated");
    selectionBadge.className = "atlas-status-badge live";
    selectionBadge.textContent = "Evidence ready";
    setStatus(`${genes.length} ranked genes generated for ${selectedSpot.id}.`);
  } catch (error) {
    console.error(error);
    if (isDefaultExample && evidenceSpotKey === selectedSpot.id) {
      setStatus("The live worker is temporarily unavailable. The example evidence remains available.", true);
    } else {
      clearEvidenceForSelection();
      setStatus(`${error.message || "Spot inference is temporarily unavailable"}. The selected local and contextual views are ready to retry.`, true);
    }
  } finally {
    setBusy(false);
  }
}

function appendMessage(role, content) {
  const message = document.createElement("div");
  message.className = `atlas-message ${role}`;
  message.innerHTML = `
    <span>${role === "user" ? "User" : "HistAgent"}</span>
    <p>${escapeHtml(content).replaceAll("\n", "<br>")}</p>
  `;
  chatLog.append(message);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function localEvidenceAnswer(message, evidence) {
  const prompt = message.toLowerCase();
  const genes = (evidence.ranked_genes || []).slice(0, 12);
  const cells = evidence.display?.cells || evidence.cell_type_composition || [];
  const programs = evidence.display?.programs || Object.entries(evidence.pathway_evidence || {})
    .map(([label, support]) => ({ label, genes: support }));
  if (/gene|marker|evidence/.test(prompt)) {
    return `The leading ranked molecular evidence is ${genes.join(", ")}. The evidence card links each cellular or functional interpretation only to supporting genes present in this list.`;
  }
  if (/cell|composition|type/.test(prompt)) {
    if (!cells.length) return "No predefined cell-type marker group is supported strongly enough by the current ranked genes.";
    return cells.map((item) => `${item.label}: ${(item.genes || []).join(", ")}`).join(". ") + ".";
  }
  if (/pathway|program|process|function/.test(prompt)) {
    if (!programs.length) return "No predefined functional-program marker group is supported strongly enough by the current ranked genes.";
    return programs.map((item) => `${item.label}: ${(item.genes || []).join(", ")}`).join(". ") + ".";
  }
  if (/spatial|surround|context|where|location/.test(prompt)) {
    return `This evidence corresponds to ${evidence.spot?.id || "the selected spot"} at image coordinates ${evidence.spot?.x}, ${evidence.spot?.y}. HistAgent used the 55 µm local view together with the 220 µm surrounding context.`;
  }
  return `The selected spot is supported by the ranked genes ${genes.slice(0, 8).join(", ")}. Ask about genes, cell evidence, functional programs or spatial context for a more specific evidence-grounded answer.`;
}

async function submitChat(message) {
  if (!currentEvidence) return;
  appendMessage("user", message);
  chatInput.value = "";
  chatButton.disabled = true;
  try {
    const outputs = await callGradio(CHAT_SPACE, "answer_atlas_question", [
      message,
      chatHistory,
      currentEvidence
    ]);
    chatHistory = outputs?.[1] || chatHistory;
    const last = Array.isArray(chatHistory) ? chatHistory.at(-1) : null;
    const answer = typeof last?.content === "string"
      ? last.content
      : localEvidenceAnswer(message, currentEvidence);
    appendMessage("assistant", answer);
  } catch (error) {
    console.error(error);
    appendMessage("assistant", localEvidenceAnswer(message, currentEvidence));
  } finally {
    chatButton.disabled = false;
  }
}

async function setSourceImage(file) {
  if (!file) return;
  if (file.size > 25 * 1024 * 1024) {
    setStatus("Choose an image smaller than 25 MB for the online workbench.", true);
    return;
  }
  if (!file.type.startsWith("image/")) {
    setStatus("Choose a PNG, JPEG or WebP tissue image.", true);
    return;
  }
  if (sourceUrl.startsWith("blob:")) URL.revokeObjectURL(sourceUrl);
  sourceFile = file;
  sourceUrl = URL.createObjectURL(file);
  sourceLabel = file.name || "Uploaded tissue image";
  isDefaultExample = false;
  mppInput.value = "0.50";
  tissueImage.src = sourceUrl;
  imageName.textContent = sourceLabel;
  clearEvidenceForSelection();
  setWorkflowStep("image");
  setStatus("Image loaded. Select its scale and choose a spot.");
}

function resetExample() {
  if (sourceUrl.startsWith("blob:")) URL.revokeObjectURL(sourceUrl);
  sourceFile = null;
  sourceUrl = "/assets/rcc-tissue.jpg";
  sourceLabel = "RCC tissue example · GSM5924038";
  isDefaultExample = true;
  mppInput.value = "1.00";
  speciesInput.value = "human";
  organInput.value = "kidney";
  tissueImage.src = sourceUrl;
  imageName.textContent = sourceLabel;
  currentEvidence = buildEvidence(defaultGenes, {
    source: "manuscript RCC example",
    spatialLabel: "Selected spot with surrounding tissue context"
  });
  evidenceSpotKey = "default";
  renderEvidence(currentEvidence, "Example");
  setStatus("Select a spot or generate evidence for the current selection.");
}

function onImageReady() {
  const dimensions = `${tissueImage.naturalWidth.toLocaleString()} × ${tissueImage.naturalHeight.toLocaleString()} px`;
  imageMeta.textContent = `${dimensions} · ${Number(mppInput.value).toFixed(2)} µm/px`;
  createSpots(isDefaultExample);
  if (isDefaultExample && currentEvidence) {
    currentEvidence.spot = {
      ...currentEvidence.spot,
      id: selectedSpot?.id || currentEvidence.spot?.id,
      x: selectedSpot ? Math.round(selectedSpot.x) : currentEvidence.spot?.x,
      y: selectedSpot ? Math.round(selectedSpot.y) : currentEvidence.spot?.y
    };
    currentEvidence.spatial_context.selected_spot =
      selectedSpot?.id || currentEvidence.spatial_context.selected_spot;
    evidenceSpotKey = selectedSpot?.id || "default";
    renderEvidence(currentEvidence, "Example");
  }
}

fileInput.addEventListener("change", () => setSourceImage(fileInput.files?.[0]));
["dragenter", "dragover"].forEach((eventName) => {
  uploadZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadZone.setAttribute("data-dragging", "true");
  });
});
["dragleave", "drop"].forEach((eventName) => {
  uploadZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadZone.removeAttribute("data-dragging");
  });
});
uploadZone.addEventListener("drop", (event) => setSourceImage(event.dataTransfer?.files?.[0]));
exampleButton.addEventListener("click", resetExample);
tissueImage.addEventListener("load", onImageReady);
mppInput.addEventListener("input", () => {
  imageMeta.textContent =
    `${tissueImage.naturalWidth.toLocaleString()} × ${tissueImage.naturalHeight.toLocaleString()} px · ${Number(mppInput.value).toFixed(2)} µm/px`;
  clearEvidenceForSelection();
  createSpots(false);
  setStatus("Image scale changed. Regenerate evidence for the selected spot.");
});
speciesInput.addEventListener("change", () => {
  clearEvidenceForSelection();
  setStatus("Species changed. Regenerate evidence for the selected spot.");
});
organInput.addEventListener("change", () => {
  clearEvidenceForSelection();
  setStatus("Organ changed. Regenerate evidence for the selected spot.");
});
window.addEventListener("resize", () => {
  renderSpots();
  positionContextRing();
});
generateButton.addEventListener("click", generateEvidence);

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (message && currentEvidence) submitChat(message);
});

document.querySelectorAll("#histagent-suggestions button").forEach((button) => {
  button.addEventListener("click", () => {
    if (!currentEvidence) return;
    chatInput.value = button.textContent;
    chatInput.focus();
  });
});

if (tissueImage.complete && tissueImage.naturalWidth) onImageReady();
