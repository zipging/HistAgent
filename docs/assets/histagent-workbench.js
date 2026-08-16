import {
  callHistAgentService,
  generateHistAgentReadout
} from "./histagent-services.js?v=20260816service1";
const LOCAL_DIAMETER_UM = 55;
const CONTEXT_DIAMETER_UM = 220;
const EXAMPLE_MANIFEST_URL = "/assets/gsm5924036-spots.json";

const tissueImage = document.querySelector("#histagent-tissue-image");
const tissueStage = document.querySelector("#histagent-tissue-stage");
const tissueLayer = document.querySelector("#histagent-tissue-layer");
const spotGrid = document.querySelector("#histagent-spot-grid");
const spotCanvasContext = spotGrid.getContext("2d");
const contextRing = document.querySelector("#histagent-context-ring");
const zoomInButton = document.querySelector("#histagent-zoom-in");
const zoomOutButton = document.querySelector("#histagent-zoom-out");
const zoomResetButton = document.querySelector("#histagent-zoom-reset");
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
let sourceUrl = "/assets/gsm5924036.jpg";
let sourceLabel = "Human clear-cell renal cell carcinoma · GSM5924036";
let spots = [];
let selectedSpot = null;
let currentEvidence = null;
let chatHistory = [];
let evidenceSpotKey = "";
let isDefaultExample = true;
let exampleManifest = null;
let viewerZoom = 1;
let viewerPanX = 0;
let viewerPanY = 0;
let dragState = null;
let suppressNextSpotClick = false;

const MIN_VIEWER_ZOOM = 1;
const MAX_VIEWER_ZOOM = 8;

function clampViewerPan() {
  if (viewerZoom <= MIN_VIEWER_ZOOM) {
    viewerPanX = 0;
    viewerPanY = 0;
    return;
  }
  const maximumX = tissueStage.clientWidth * (viewerZoom - 1) / 2;
  const maximumY = tissueStage.clientHeight * (viewerZoom - 1) / 2;
  viewerPanX = Math.max(-maximumX, Math.min(maximumX, viewerPanX));
  viewerPanY = Math.max(-maximumY, Math.min(maximumY, viewerPanY));
}

function applyViewerTransform() {
  clampViewerPan();
  tissueLayer.style.transform = `translate3d(${viewerPanX}px, ${viewerPanY}px, 0) scale(${viewerZoom})`;
  zoomInButton.disabled = viewerZoom >= MAX_VIEWER_ZOOM;
  zoomOutButton.disabled = viewerZoom <= MIN_VIEWER_ZOOM;
  zoomResetButton.disabled = viewerZoom <= MIN_VIEWER_ZOOM && viewerPanX === 0 && viewerPanY === 0;
  tissueStage.dataset.zoom = viewerZoom.toFixed(2);
}

function setViewerZoom(nextZoom, anchorX = tissueStage.clientWidth / 2, anchorY = tissueStage.clientHeight / 2) {
  const clampedZoom = Math.max(MIN_VIEWER_ZOOM, Math.min(MAX_VIEWER_ZOOM, nextZoom));
  if (Math.abs(clampedZoom - viewerZoom) < 0.001) return;
  const centerX = tissueStage.clientWidth / 2;
  const centerY = tissueStage.clientHeight / 2;
  const ratio = clampedZoom / viewerZoom;
  viewerPanX = anchorX - centerX - (anchorX - centerX - viewerPanX) * ratio;
  viewerPanY = anchorY - centerY - (anchorY - centerY - viewerPanY) * ratio;
  viewerZoom = clampedZoom;
  applyViewerTransform();
}

function resetViewer() {
  viewerZoom = MIN_VIEWER_ZOOM;
  viewerPanX = 0;
  viewerPanY = 0;
  applyViewerTransform();
}

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
  generateButton.textContent = value ? "Analyzing…" : "Analyze selected spot";
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
  if (isDefaultExample && exampleManifest) {
    spots = exampleManifest.spots.map((spot) => ({
      ...spot,
      xNorm: Number(spot.x) / tissueImage.naturalWidth,
      yNorm: Number(spot.y) / tissueImage.naturalHeight
    }));
    const defaultSpot = spots.find(
      (spot) => spot.barcode === exampleManifest.default_barcode
    ) || spots[0];
    selectSpot(defaultSpot, { preserveEvidence });
    spotCount.textContent = `${spots.length.toLocaleString()} Visium spots · 55 µm`;
    return;
  }
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
  selectSpot(center, { preserveEvidence });
  spotCount.textContent = `${spots.length.toLocaleString()} 55 µm sampling spots`;
}

function renderSpots() {
  const box = displayBox();
  if (!box) return;
  const width = tissueStage.clientWidth;
  const height = tissueStage.clientHeight;
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  const renderWidth = Math.max(1, Math.round(width * pixelRatio));
  const renderHeight = Math.max(1, Math.round(height * pixelRatio));
  if (spotGrid.width !== renderWidth || spotGrid.height !== renderHeight) {
    spotGrid.width = renderWidth;
    spotGrid.height = renderHeight;
  }
  spotCanvasContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  spotCanvasContext.clearRect(0, 0, width, height);

  const spotDiameter = Math.max(
    isDefaultExample ? 4 : 8,
    Math.min(12, (LOCAL_DIAMETER_UM / Number(mppInput.value)) * box.scale)
  );
  const radius = spotDiameter / 2;
  spotCanvasContext.beginPath();
  spots.forEach((spot) => {
    if (spot.id === selectedSpot?.id) return;
    const x = box.left + spot.xNorm * box.width;
    const y = box.top + spot.yNorm * box.height;
    spotCanvasContext.moveTo(x + radius, y);
    spotCanvasContext.arc(x, y, radius, 0, Math.PI * 2);
  });
  spotCanvasContext.fillStyle = "rgba(255, 255, 255, 0.20)";
  spotCanvasContext.strokeStyle = "rgba(24, 119, 101, 0.70)";
  spotCanvasContext.lineWidth = 0.75;
  spotCanvasContext.fill();
  spotCanvasContext.stroke();

  if (selectedSpot) {
    const x = box.left + selectedSpot.xNorm * box.width;
    const y = box.top + selectedSpot.yNorm * box.height;
    const selectedRadius = Math.max(4, radius * 1.8);
    spotCanvasContext.save();
    spotCanvasContext.beginPath();
    spotCanvasContext.arc(x, y, selectedRadius + 2.5, 0, Math.PI * 2);
    spotCanvasContext.fillStyle = "rgba(239, 163, 58, 0.28)";
    spotCanvasContext.fill();
    spotCanvasContext.beginPath();
    spotCanvasContext.arc(x, y, selectedRadius, 0, Math.PI * 2);
    spotCanvasContext.fillStyle = "#efa33a";
    spotCanvasContext.strokeStyle = "#ffffff";
    spotCanvasContext.lineWidth = 1.5;
    spotCanvasContext.fill();
    spotCanvasContext.stroke();
    spotCanvasContext.restore();
  }
  positionContextRing();
}

function eventPointOnSpotCanvas(event) {
  const bounds = spotGrid.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return null;
  return {
    x: (event.clientX - bounds.left) * tissueStage.clientWidth / bounds.width,
    y: (event.clientY - bounds.top) * tissueStage.clientHeight / bounds.height
  };
}

function nearestSpotAtPoint(point) {
  const box = displayBox();
  if (!box || !point) return null;
  if (
    point.x < box.left || point.x > box.left + box.width ||
    point.y < box.top || point.y > box.top + box.height
  ) return null;
  let nearest = null;
  let nearestDistance = Number.POSITIVE_INFINITY;
  spots.forEach((spot) => {
    const x = box.left + spot.xNorm * box.width;
    const y = box.top + spot.yNorm * box.height;
    const distance = (point.x - x) ** 2 + (point.y - y) ** 2;
    if (distance < nearestDistance) {
      nearest = spot;
      nearestDistance = distance;
    }
  });
  return nearest;
}

function directionalSpot(horizontal, vertical) {
  if (!selectedSpot) return spots[0] || null;
  let nearest = null;
  let nearestScore = Number.POSITIVE_INFINITY;
  spots.forEach((spot) => {
    if (spot.id === selectedSpot.id) return;
    const dx = spot.xNorm - selectedSpot.xNorm;
    const dy = spot.yNorm - selectedSpot.yNorm;
    const forward = dx * horizontal + dy * vertical;
    if (forward <= 0) return;
    const sideways = Math.abs(dx * vertical - dy * horizontal);
    const score = forward + sideways * 3;
    if (score < nearestScore) {
      nearest = spot;
      nearestScore = score;
    }
  });
  return nearest;
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
  const coordinateText = `Center ${Math.round(selectedSpot.x)}, ${Math.round(selectedSpot.y)} px`;
  selectedCoordinates.textContent = selectedSpot.array_row == null
    ? `${coordinateText} · 55 µm sampling diameter`
    : `${coordinateText} · Visium row ${selectedSpot.array_row}, column ${selectedSpot.array_col} · 55 µm`;
}

function clearEvidenceForSelection() {
  currentEvidence = null;
  evidenceSpotKey = "";
  evidenceBadge.textContent = "Not generated";
  geneCount.textContent = "Awaiting HistAgent";
  geneList.replaceChildren();
  evidenceFields.innerHTML = `
    <div class="histagent-evidence-empty">
      Analyze ${escapeHtml(selectedSpot?.id || "the selected spot")} with HistAgent.
    </div>
  `;
  chatHistory = [];
  chatLog.innerHTML = `
    <div class="atlas-message assistant">
      <span>HistAgent</span>
      <p>Analyze the selected spot before starting the conversation.</p>
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
  renderSpots();
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
      barcode: selectedSpot?.barcode || null,
      species: speciesInput?.value || "human",
      organ: organInput?.value || "kidney",
      x: selectedSpot ? Math.round(selectedSpot.x) : 759,
      y: selectedSpot ? Math.round(selectedSpot.y) : 875,
      array_row: selectedSpot?.array_row ?? null,
      array_col: selectedSpot?.array_col ?? null
    },
    ranked_genes: cleanGenes,
    cell_type_composition: cells,
    pathway_evidence: Object.fromEntries(programs.map((item) => [item.label, item.genes])),
    spatial_context: {
      available: true,
      selected_spot: selectedSpot?.id || "S112",
      local_diameter_um: LOCAL_DIAMETER_UM,
      context_diameter_um: CONTEXT_DIAMETER_UM,
      coordinate_source: isDefaultExample
        ? "Official 10x Visium tissue positions for GSM5924036"
        : "User-defined physical sampling grid",
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
  setStatus(`Analyzing ${selectedSpot.id} with HistAgent.`);
  try {
    const localCanvas = canvasCrop(selectedSpot.x, selectedSpot.y, LOCAL_DIAMETER_UM);
    // Training stored the four-times context field at 256 px; the released
    // inference transform then applies its 224 px center crop.
    const contextCanvas = canvasCrop(selectedSpot.x, selectedSpot.y, CONTEXT_DIAMETER_UM, 256);
    const [localBlob, contextBlob] = await Promise.all([
      canvasBlob(localCanvas),
      canvasBlob(contextCanvas)
    ]);
    const outputs = await generateHistAgentReadout({
      localBlob,
      contextBlob,
      localName: `${selectedSpot.id}_local.png`,
      contextName: `${selectedSpot.id}_context.png`,
      species: speciesInput.value,
      organ: organInput.value,
      topK: 50
    });
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
    clearEvidenceForSelection();
    setStatus(`${error.message || "Spot inference is temporarily unavailable"}. The selected local and contextual views are ready to retry.`, true);
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
  return message;
}

function chatServiceError(error) {
  const message = String(error?.message || "").trim();
  if (/额度|quota/i.test(message)) {
    return "The public GPU allowance is temporarily unavailable. Your evidence card is preserved; please retry after the allowance resets.";
  }
  return message || "The reasoning service could not answer this question. Please retry.";
}

async function submitChat(message, appendUser = true) {
  if (!currentEvidence) return;
  if (appendUser) appendMessage("user", message);
  const pendingMessage = appendMessage(
    "assistant",
    "Analyzing the selected molecular and spatial evidence…"
  );
  pendingMessage.classList.add("pending");
  chatInput.value = "";
  chatButton.disabled = true;
  try {
    const outputs = await callHistAgentService("reasoning", "answer_atlas_question", [
      message,
      chatHistory,
      currentEvidence
    ]);
    if (!Array.isArray(outputs?.[1])) {
      throw new Error("The reasoning service returned an invalid response. Please retry.");
    }
    chatHistory = outputs[1];
    const last = Array.isArray(chatHistory) ? chatHistory.at(-1) : null;
    if (last?.role !== "assistant" || typeof last?.content !== "string" || !last.content.trim()) {
      throw new Error("The reasoning service returned no answer. Please retry.");
    }
    const answer = last.content.trim();
    pendingMessage.querySelector("p").innerHTML = escapeHtml(answer).replaceAll("\n", "<br>");
    pendingMessage.classList.remove("pending");
  } catch (error) {
    console.error(error);
    pendingMessage.querySelector("p").textContent = chatServiceError(error);
    pendingMessage.classList.remove("pending");
    pendingMessage.classList.add("error");
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "histagent-chat-retry";
    retry.textContent = "Retry";
    retry.addEventListener("click", () => {
      pendingMessage.remove();
      submitChat(message, false);
    }, { once: true });
    pendingMessage.append(retry);
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
  mppInput.readOnly = false;
  mppInput.value = "0.50";
  tissueImage.src = sourceUrl;
  imageName.textContent = sourceLabel;
  clearEvidenceForSelection();
  setWorkflowStep("image");
  setStatus("Image loaded. Select its scale and choose a spot.");
}

async function resetExample() {
  setStatus("Loading the GSM5924036 example and its official Visium coordinates.");
  if (!exampleManifest) {
    const response = await fetch(EXAMPLE_MANIFEST_URL);
    if (!response.ok) {
      throw new Error(`Could not load the example spot manifest (${response.status})`);
    }
    exampleManifest = await response.json();
    if (!Array.isArray(exampleManifest.spots) || !exampleManifest.spots.length) {
      throw new Error("The example spot manifest contains no tissue spots");
    }
  }
  if (sourceUrl.startsWith("blob:")) URL.revokeObjectURL(sourceUrl);
  sourceFile = null;
  sourceUrl = exampleManifest.image_url;
  sourceLabel = exampleManifest.title;
  isDefaultExample = true;
  mppInput.value = Number(exampleManifest.mpp).toFixed(4);
  mppInput.readOnly = true;
  speciesInput.value = exampleManifest.species;
  organInput.value = exampleManifest.organ;
  imageName.textContent = sourceLabel;
  currentEvidence = null;
  evidenceSpotKey = "";
  if (tissueImage.getAttribute("src") === sourceUrl && tissueImage.complete) {
    onImageReady();
  } else {
    tissueImage.src = sourceUrl;
  }
  clearEvidenceForSelection();
  setStatus("Select any real 55 µm Visium spot, then analyze it with HistAgent.");
}

function onImageReady() {
  const dimensions = `${tissueImage.naturalWidth.toLocaleString()} × ${tissueImage.naturalHeight.toLocaleString()} px`;
  imageMeta.textContent = `${dimensions} · ${Number(mppInput.value).toFixed(2)} µm/px`;
  tissueStage.style.setProperty(
    "--histagent-image-ratio",
    `${tissueImage.naturalWidth} / ${tissueImage.naturalHeight}`
  );
  resetViewer();
  createSpots(false);
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
exampleButton.addEventListener("click", () => {
  resetExample().catch((error) => setStatus(error.message, true));
});
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

zoomInButton.addEventListener("click", () => setViewerZoom(viewerZoom * 1.5));
zoomOutButton.addEventListener("click", () => setViewerZoom(viewerZoom / 1.5));
zoomResetButton.addEventListener("click", resetViewer);

tissueStage.addEventListener("wheel", (event) => {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  const bounds = tissueStage.getBoundingClientRect();
  const factor = Math.exp(-event.deltaY * 0.0015);
  setViewerZoom(
    viewerZoom * factor,
    event.clientX - bounds.left,
    event.clientY - bounds.top
  );
}, { passive: false });

tissueStage.addEventListener("dblclick", (event) => {
  if (event.target.closest(".histagent-map-tools")) return;
  const bounds = tissueStage.getBoundingClientRect();
  setViewerZoom(
    viewerZoom >= 4 ? MIN_VIEWER_ZOOM : viewerZoom * 1.75,
    event.clientX - bounds.left,
    event.clientY - bounds.top
  );
});

tissueStage.addEventListener("pointerdown", (event) => {
  if (viewerZoom <= MIN_VIEWER_ZOOM || event.button !== 0) return;
  if (event.target.closest(".histagent-map-tools")) return;
  dragState = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    panX: viewerPanX,
    panY: viewerPanY,
    moved: false
  };
  tissueStage.setPointerCapture(event.pointerId);
  tissueStage.dataset.dragging = "true";
});

tissueStage.addEventListener("pointermove", (event) => {
  if (!dragState || dragState.pointerId !== event.pointerId) return;
  if (Math.abs(event.clientX - dragState.startX) + Math.abs(event.clientY - dragState.startY) > 4) {
    dragState.moved = true;
  }
  viewerPanX = dragState.panX + event.clientX - dragState.startX;
  viewerPanY = dragState.panY + event.clientY - dragState.startY;
  applyViewerTransform();
});

function finishViewerDrag(event) {
  if (!dragState || dragState.pointerId !== event.pointerId) return;
  if (tissueStage.hasPointerCapture(event.pointerId)) {
    tissueStage.releasePointerCapture(event.pointerId);
  }
  const moved = dragState.moved;
  dragState = null;
  tissueStage.removeAttribute("data-dragging");
  if (moved) {
    suppressNextSpotClick = true;
    window.setTimeout(() => { suppressNextSpotClick = false; }, 0);
  }
}

tissueStage.addEventListener("pointerup", finishViewerDrag);
tissueStage.addEventListener("pointercancel", finishViewerDrag);

// Pointer capture moves the click target to the stage while the viewer is
// zoomed. Listen on the stage so spot selection remains available after zoom,
// while still ignoring toolbar clicks and completed pan gestures.
tissueStage.addEventListener("click", (event) => {
  if (event.target.closest(".histagent-map-tools")) return;
  if (suppressNextSpotClick) return;
  const spot = nearestSpotAtPoint(eventPointOnSpotCanvas(event));
  if (spot) selectSpot(spot);
});

spotGrid.addEventListener("keydown", (event) => {
  const directions = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, -1],
    ArrowDown: [0, 1]
  };
  const direction = directions[event.key];
  if (!direction) return;
  event.preventDefault();
  const spot = directionalSpot(direction[0], direction[1]);
  if (spot) selectSpot(spot);
});

window.addEventListener("resize", () => {
  applyViewerTransform();
  renderSpots();
});
generateButton.addEventListener("click", generateEvidence);

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (message && currentEvidence) submitChat(message);
});

chatInput.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey) return;
  if (event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  if (!chatButton.disabled) chatForm.requestSubmit();
});

document.querySelectorAll("#histagent-suggestions button").forEach((button) => {
  button.addEventListener("click", () => {
    if (!currentEvidence) return;
    chatInput.value = button.textContent;
    chatInput.focus();
  });
});

resetExample().catch((error) => setStatus(error.message, true));
