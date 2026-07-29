(() => {
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const helper = document.createElement("textarea");
    helper.value = text;
    helper.setAttribute("readonly", "");
    helper.style.position = "absolute";
    helper.style.left = "-9999px";
    document.body.appendChild(helper);
    helper.select();
    document.execCommand("copy");
    helper.remove();
  }

  const navToggle = document.querySelector("[data-nav-toggle]");
  const nav = document.querySelector("[data-primary-nav]");

  if (navToggle && nav) {
    navToggle.addEventListener("click", () => {
      const open = nav.getAttribute("data-open") === "true";
      nav.setAttribute("data-open", String(!open));
      navToggle.setAttribute("aria-expanded", String(!open));
    });
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const selector = button.getAttribute("data-copy");
      const code = selector ? document.querySelector(selector) : null;
      if (!code) return;
      try {
        await copyText(code.textContent.trim());
      } catch {
        const original = button.textContent;
        button.textContent = "Copy failed";
        setTimeout(() => {
          button.textContent = original;
        }, 1200);
        return;
      }
      const original = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => {
        button.textContent = original;
      }, 1200);
    });
  });

  const patchData = {
    tls: {
      image: "/assets/patch-tls.png",
      title: "TLS-high dense immune area",
      label: "RCC TLS region · spot 1015",
      summary: "Immune and stromal signals co-occur around a dense cellular focus.",
      genes: ["TMSB4X", "UBC", "B2M", "ACTB", "IGFBP7", "COL1A1", "COL3A1", "VIM", "CD74", "IGKC"]
    },
    marker: {
      image: "/assets/patch-marker.png",
      title: "TLS marker-rich area",
      label: "RCC TLS region · spot 491",
      summary: "CCL19, CD74 and HLA-DRA support a lymphoid-like state.",
      genes: ["UBC", "TMSB4X", "ACTB", "B2M", "CD74", "IGFBP7", "CCL19", "IGKC", "HLA-DRA", "CXCR4"]
    },
    stroma: {
      image: "/assets/patch-stroma.png",
      title: "Collagen-rich fibrotic stroma",
      label: "Non-TLS region · spot 1112",
      summary: "Collagen, fibronectin and contractile programs dominate this region.",
      genes: ["COL1A1", "FN1", "COL1A2", "COL3A1", "SPARC", "ACTB", "IGFBP7", "TIMP1", "POSTN", "ACTA2"]
    },
    edge: {
      image: "/assets/patch-edge.png",
      title: "Tissue edge / low TLS context",
      label: "Non-TLS region · spot 391",
      summary: "A mixed edge state with stromal and vascular-response signals.",
      genes: ["UBC", "MT2A", "ACTB", "B2M", "MFSD11", "SPARC", "IGFBP7", "FN1", "COL1A1", "VEGFA"]
    }
  };

  const patchButtons = document.querySelectorAll("[data-patch]");
  const patchImage = document.querySelector("[data-patch-image]");
  const patchTitle = document.querySelector("[data-patch-title]");
  const patchLabel = document.querySelector("[data-patch-label]");
  const patchSummary = document.querySelector("[data-patch-summary]");
  const patchGenes = document.querySelector("[data-patch-genes]");

  function renderPatch(key) {
    const patch = patchData[key];
    if (!patch || !patchImage || !patchGenes) return;
    patchButtons.forEach((button) => {
      button.setAttribute("aria-pressed", String(button.getAttribute("data-patch") === key));
    });
    patchImage.src = patch.image;
    patchImage.alt = patch.title;
    patchTitle.textContent = patch.title;
    patchLabel.textContent = patch.label;
    patchSummary.textContent = patch.summary;
    patchGenes.innerHTML = patch.genes.map((gene) => `<li>${gene}</li>`).join("");
  }

  patchButtons.forEach((button) => {
    button.addEventListener("click", () => renderPatch(button.getAttribute("data-patch")));
  });

  if (patchButtons.length) renderPatch("tls");

  const atlasQueries = {
    tls: "Find tumor-adjacent TLS-like regions.",
    heart: "Show heart spots with muscle contraction pathways.",
    tumor: "Find proliferative epithelial tumor states."
  };
  const queryInput = document.querySelector("[data-query-input]");
  const queryPresets = document.querySelectorAll("[data-query-preset]");
  const atlasSearchForm = document.querySelector("[data-atlas-search-form]");
  const atlasSearchSubmit = document.querySelector("[data-atlas-search-submit]");
  const atlasSearchStatus = document.querySelector("[data-atlas-search-status]");
  const atlasSearchResults = document.querySelector("[data-atlas-search-results]");
  const resultTitle = document.querySelector("[data-result-title]");
  const resultScore = document.querySelector("[data-result-score]");
  const resultSummary = document.querySelector("[data-result-summary]");
  const resultEvidence = document.querySelector("[data-result-evidence]");
  const resultChat = document.querySelector("[data-result-chat]");

  const atlasWorkspaceButtons = Array.from(document.querySelectorAll("[data-atlas-workspace]"));
  const atlasWorkspacePanels = Array.from(document.querySelectorAll("[data-atlas-panel]"));

  function setAtlasWorkspace(workspace, updateHash = true) {
    const allowed = new Set(["chat", "explore", "analyze"]);
    const selected = allowed.has(workspace) ? workspace : "chat";
    atlasWorkspaceButtons.forEach((button) => {
      const active = button.getAttribute("data-atlas-workspace") === selected;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    atlasWorkspacePanels.forEach((panel) => {
      panel.hidden = panel.getAttribute("data-atlas-panel") !== selected;
    });
    if (updateHash && window.history && window.history.replaceState) {
      window.history.replaceState(
        null,
        "",
        selected === "analyze"
          ? "#tissue-analysis"
          : selected === "explore"
            ? "#atlas-search"
            : "#spot-chat"
      );
    }
    document.dispatchEvent(
      new CustomEvent("histagent:workspace", { detail: { workspace: selected } })
    );
  }

  atlasWorkspaceButtons.forEach((button, index) => {
    button.addEventListener("click", () => {
      setAtlasWorkspace(button.getAttribute("data-atlas-workspace"));
    });
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      const direction = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1;
      const next = atlasWorkspaceButtons[
        (index + direction + atlasWorkspaceButtons.length) % atlasWorkspaceButtons.length
      ];
      next.focus();
      setAtlasWorkspace(next.getAttribute("data-atlas-workspace"));
    });
  });

  if (atlasWorkspaceButtons.length) {
    const initialWorkspace = {
      "#tissue-analysis": "analyze",
      "#analyze-he": "analyze",
      "#atlas-search": "explore",
      "#explore-atlas": "explore",
      "#spot-chat": "chat",
    }[window.location.hash] || "chat";
    setAtlasWorkspace(initialWorkspace, false);
  }

  const heForm = document.querySelector("[data-he-form]");
  const heInput = document.querySelector("[data-he-input]");
  const heDrop = document.querySelector("[data-he-drop]");
  const heEmpty = document.querySelector("[data-he-empty]");
  const hePreview = document.querySelector("[data-he-preview]");
  const hePreviewImage = document.querySelector("[data-he-preview-image]");
  const heImageStage = document.querySelector("[data-he-image-stage]");
  const heMarker = document.querySelector("[data-he-marker]");
  const heFileName = document.querySelector("[data-he-file-name]");
  const heFileMeta = document.querySelector("[data-he-file-meta]");
  const heExample = document.querySelector("[data-he-example]");
  const heMpp = document.querySelector("[data-he-mpp]");
  const heSelectionNote = document.querySelector("[data-he-selection-note]");
  const heCropPreview = document.querySelector("[data-he-crop-preview]");
  const heLocalPreview = document.querySelector("[data-he-local-preview]");
  const heContextPreview = document.querySelector("[data-he-context-preview]");
  const heSpecies = document.querySelector("[data-he-species]");
  const heOrgan = document.querySelector("[data-he-organ]");
  const heSubmit = document.querySelector("[data-he-submit]");
  const heStatus = document.querySelector("[data-he-status]");
  const hePlaceholder = document.querySelector("[data-he-output-placeholder]");
  const heResults = document.querySelector("[data-he-output-results]");
  const heResultTitle = document.querySelector("[data-he-result-title]");
  const heResultSummary = document.querySelector("[data-he-result-summary]");
  const heGenes = document.querySelector("[data-he-genes]");
  const heStates = document.querySelector("[data-he-states]");
  const hePathways = document.querySelector("[data-he-pathways]");
  const heSpots = document.querySelector("[data-he-spots]");
  const heContinue = document.querySelector("[data-he-continue]");
  let heSelectedFile = null;
  let hePreviewUrl = "";
  let heSelectedPoint = { x: 0.5, y: 0.5 };

  function getAtlasServiceOrigin() {
    const configured = document.querySelector('meta[name="histagent-chat-origin"]')?.content?.trim();
    if (configured) return configured.replace(/\/+$/, "");
    if (["127.0.0.1", "localhost"].includes(window.location.hostname)) {
      return "http://127.0.0.1:7860";
    }
    return "";
  }

  const atlasServiceOrigin = getAtlasServiceOrigin();

  function setHeStatus(message, error = false) {
    if (!heStatus) return;
    heStatus.textContent = message || "";
    heStatus.setAttribute("data-error", String(error));
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  function imageDimensions(file) {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(file);
      const image = new Image();
      image.onload = () => {
        URL.revokeObjectURL(url);
        resolve(`${image.naturalWidth} × ${image.naturalHeight}px`);
      };
      image.onerror = () => {
        URL.revokeObjectURL(url);
        resolve("");
      };
      image.src = url;
    });
  }

  function waitForImage(image) {
    return new Promise((resolve) => {
      if (image?.complete && image.naturalWidth) {
        resolve();
        return;
      }
      image?.addEventListener("load", resolve, { once: true });
      image?.addEventListener("error", resolve, { once: true });
    });
  }

  function previewDisplayBox() {
    if (!hePreviewImage?.naturalWidth || !hePreviewImage?.naturalHeight) return null;
    const width = hePreviewImage.clientWidth;
    const height = hePreviewImage.clientHeight;
    const scale = Math.min(
      width / hePreviewImage.naturalWidth,
      height / hePreviewImage.naturalHeight
    );
    const displayWidth = hePreviewImage.naturalWidth * scale;
    const displayHeight = hePreviewImage.naturalHeight * scale;
    return {
      left: (width - displayWidth) / 2,
      top: (height - displayHeight) / 2,
      width: displayWidth,
      height: displayHeight
    };
  }

  function positionHeMarker() {
    const box = previewDisplayBox();
    if (!box || !heMarker) return;
    heMarker.style.left = `${box.left + heSelectedPoint.x * box.width}px`;
    heMarker.style.top = `${box.top + heSelectedPoint.y * box.height}px`;
  }

  function drawFovCrop(image, centerX, centerY, sidePixels) {
    const outputSize = 224;
    const canvas = document.createElement("canvas");
    canvas.width = outputSize;
    canvas.height = outputSize;
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, outputSize, outputSize);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";

    const sourceLeft = centerX - sidePixels / 2;
    const sourceTop = centerY - sidePixels / 2;
    const sx = Math.max(0, sourceLeft);
    const sy = Math.max(0, sourceTop);
    const sourceRight = Math.min(image.naturalWidth, sourceLeft + sidePixels);
    const sourceBottom = Math.min(image.naturalHeight, sourceTop + sidePixels);
    const sw = Math.max(0, sourceRight - sx);
    const sh = Math.max(0, sourceBottom - sy);
    if (sw && sh) {
      const scale = outputSize / sidePixels;
      context.drawImage(
        image,
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
    return canvas.toDataURL("image/png");
  }

  function buildHeFovPair(renderPreview = true) {
    if (!heSelectedFile || !hePreviewImage?.naturalWidth) {
      throw new Error("Choose an H&E image before selecting a tissue location.");
    }
    const micronsPerPixel = Number(heMpp?.value || 0);
    if (!Number.isFinite(micronsPerPixel) || micronsPerPixel <= 0) {
      throw new Error("Enter the image scale in microns per pixel.");
    }
    const localPixels = 55 / micronsPerPixel;
    if (localPixels < 4) {
      throw new Error("The image resolution is too low to resolve a 55 μm spot.");
    }
    const centerX = heSelectedPoint.x * hePreviewImage.naturalWidth;
    const centerY = heSelectedPoint.y * hePreviewImage.naturalHeight;
    const localImage = drawFovCrop(hePreviewImage, centerX, centerY, localPixels);
    const contextImage = drawFovCrop(hePreviewImage, centerX, centerY, localPixels * 4);
    if (renderPreview) {
      if (heLocalPreview) heLocalPreview.src = localImage;
      if (heContextPreview) heContextPreview.src = contextImage;
      if (heCropPreview) heCropPreview.hidden = false;
      if (heSelectionNote) {
        heSelectionNote.textContent =
          `Selected center: ${Math.round(centerX)}, ${Math.round(centerY)} px · 55 μm and 220 μm views ready.`;
      }
    }
    return { localImage, contextImage, micronsPerPixel };
  }

  function refreshHeFovPreview() {
    try {
      buildHeFovPair(true);
      setHeStatus("");
    } catch {
      if (heCropPreview) heCropPreview.hidden = true;
      if (heSelectionNote) {
        heSelectionNote.textContent =
          "Choose an image, enter its scale and click the tissue location to analyze.";
      }
    }
  }

  async function setHeFile(file, options = {}) {
    if (!file) return;
    if (file.size > 16 * 1024 * 1024) {
      setHeStatus("The image is larger than 16 MB.", true);
      return;
    }
    if (hePreviewUrl) URL.revokeObjectURL(hePreviewUrl);
    heSelectedFile = file;
    heSelectedPoint = { x: 0.5, y: 0.5 };
    hePreviewUrl = URL.createObjectURL(file);
    if (hePreviewImage) hePreviewImage.src = hePreviewUrl;
    if (heMpp) heMpp.value = options.micronsPerPixel || "";
    if (heFileName) heFileName.textContent = file.name || "H&E query image";
    if (heEmpty) heEmpty.hidden = true;
    if (hePreview) hePreview.hidden = false;
    const dimensions = await imageDimensions(file);
    if (heFileMeta) {
      heFileMeta.textContent = [dimensions, formatBytes(file.size)].filter(Boolean).join(" · ");
    }
    await waitForImage(hePreviewImage);
    positionHeMarker();
    refreshHeFovPreview();
    setHeStatus("");
  }

  function setSelectOptions(select, values, preferred) {
    if (!select || !Array.isArray(values) || !values.length) return;
    const normalizedPreferred = String(preferred || select.value || "").toLowerCase();
    select.replaceChildren();
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = String(value).replace(/(^|[_-])([a-z])/g, (_, prefix, letter) =>
        `${prefix ? " " : ""}${letter.toUpperCase()}`
      );
      select.appendChild(option);
    });
    const match = values.find((value) => String(value).toLowerCase() === normalizedPreferred);
    if (match) select.value = String(match);
  }

  async function atlasApi(path, options = {}) {
    if (!atlasServiceOrigin) {
      throw new Error("The HistAgent analysis service needs a public endpoint.");
    }
    const response = await fetch(`${atlasServiceOrigin}${path}`, options);
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch {
        // Keep the HTTP status when the server did not return JSON.
      }
      throw new Error(detail);
    }
    return response.json();
  }

  function chatHref(spotKey) {
    const key = String(spotKey || "").trim();
    return key
      ? `/interactive/?spot_key=${encodeURIComponent(key)}#spot-chat`
      : "/interactive/#spot-chat";
  }

  function setAtlasSearchStatus(message, error = false) {
    if (!atlasSearchStatus) return;
    atlasSearchStatus.textContent = message || "";
    atlasSearchStatus.setAttribute("data-error", String(error));
  }

  function atlasEvidence(row) {
    const genes = (Array.isArray(row.matched_evidence) && row.matched_evidence.length
      ? row.matched_evidence
      : row.top_genes || []).slice(0, 8);
    const pathways = (row.pathways || []).slice(0, 2);
    return [
      genes.length ? `Genes: ${genes.join(", ")}` : "",
      ...pathways,
      [row.species, row.organ, row.slice_id].filter(Boolean).join(" · ")
    ].filter(Boolean);
  }

  function renderAtlasSearch(data) {
    const items = Array.isArray(data?.items) ? data.items : [];
    const top = items[0];
    if (!top) {
      setAtlasSearchStatus("No related atlas spots were found.", true);
      return;
    }

    if (resultScore) {
      resultScore.hidden = false;
      resultScore.textContent =
        `Cosine similarity ${Number(top.similarity || 0).toFixed(2)}`;
    }
    if (resultTitle) resultTitle.textContent = top.dominant_cell_type || "Related tissue state";
    if (resultSummary) {
      resultSummary.textContent = [top.species, top.organ, top.slice_id]
        .filter(Boolean)
        .join(" · ");
    }
    if (resultEvidence) {
      resultEvidence.replaceChildren(
        ...atlasEvidence(top).map((value) => {
          const item = document.createElement("li");
          item.textContent = value;
          return item;
        })
      );
    }
    if (resultChat) {
      resultChat.href = chatHref(top.spot_key);
      resultChat.hidden = !top.spot_key;
    }

    if (atlasSearchResults) {
      atlasSearchResults.replaceChildren(
        ...items.slice(1, 6).map((row, index) => {
          const card = document.createElement("article");
          card.className = "atlas-related-card";

          const rank = document.createElement("span");
          rank.className = "atlas-related-rank";
          rank.textContent = String(index + 2).padStart(2, "0");

          const body = document.createElement("div");
          const title = document.createElement("h4");
          title.textContent = row.dominant_cell_type || "Related tissue state";
          const meta = document.createElement("p");
          meta.textContent = [row.species, row.organ, row.slice_id]
            .filter(Boolean)
            .join(" · ");
          const genes = document.createElement("p");
          genes.className = "atlas-related-genes";
          genes.textContent = atlasEvidence(row)[0] || "Related molecular profile";
          body.append(title, meta, genes);

          const actions = document.createElement("div");
          actions.className = "atlas-related-actions";
          const score = document.createElement("strong");
          score.textContent =
            `Cosine ${Number(row.similarity || 0).toFixed(2)}`;
          const link = document.createElement("a");
          link.className = "button";
          link.textContent = "Open in Spot Chat";
          link.href = chatHref(row.spot_key);
          actions.append(score, link);

          card.append(rank, body, actions);
          return card;
        })
      );
    }
    setAtlasSearchStatus(`${items.length} related atlas spots found.`);
  }

  async function runAtlasSearch(rawQuery) {
    const query = String(rawQuery || queryInput?.value || "").trim();
    if (!query) {
      setAtlasSearchStatus("Describe the biological state you want to find.", true);
      queryInput?.focus();
      return;
    }
    if (queryInput) queryInput.value = query;
    if (atlasSearchSubmit) atlasSearchSubmit.disabled = true;
    setAtlasSearchStatus("Searching the atlas…");
    try {
      const data = await atlasApi("/api/retrieval/text", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, limit: 6 })
      });
      renderAtlasSearch(data);
    } catch (error) {
      setAtlasSearchStatus(`Search failed: ${error.message || String(error)}`, true);
    } finally {
      if (atlasSearchSubmit) atlasSearchSubmit.disabled = false;
    }
  }

  if (atlasSearchForm) {
    atlasSearchForm.addEventListener("submit", (event) => {
      event.preventDefault();
      runAtlasSearch();
    });
    queryPresets.forEach((button) => {
      button.addEventListener("click", () => {
        const query = atlasQueries[button.getAttribute("data-query-preset")] || atlasQueries.tls;
        runAtlasSearch(query);
      });
    });
  }

  async function loadHeOrgans(preferred = "") {
    if (!heOrgan || !heSpecies || !atlasServiceOrigin) return;
    const data = await atlasApi(`/api/organs?species=${encodeURIComponent(heSpecies.value)}`);
    setSelectOptions(heOrgan, data.items || [], preferred);
  }

  async function loadHeTaxonomy() {
    if (!heForm || !atlasServiceOrigin) return;
    try {
      const data = await atlasApi("/api/species");
      setSelectOptions(heSpecies, data.items || [], "human");
      await loadHeOrgans("breast");
    } catch {
      // The hard-coded human/breast defaults remain usable if discovery is unavailable.
    }
  }

  function rankedCounts(items, field, limit) {
    const counts = new Map();
    items.forEach((item) => {
      const values = field === "dominant_cell_type"
        ? [item[field]]
        : (Array.isArray(item[field]) ? item[field] : []);
      values.forEach((value) => {
        const label = String(value || "").trim();
        if (!label) return;
        counts.set(label, (counts.get(label) || 0) + 1);
      });
    });
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit);
  }

  function renderHeAnalysis(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    const genes = Array.isArray(data.query_genes) ? data.query_genes : [];
    const states = rankedCounts(items, "dominant_cell_type", 4);
    const pathways = rankedCounts(items, "pathways", 5);
    const organName = heOrgan?.value || "selected organ";

    if (heGenes) {
      heGenes.replaceChildren(
        ...genes.map((gene) => {
          const item = document.createElement("li");
          const italic = document.createElement("i");
          italic.textContent = String(gene);
          item.appendChild(italic);
          return item;
        })
      );
    }

    if (heStates) {
      heStates.replaceChildren(
        ...states.map(([label]) => {
          const row = document.createElement("div");
          row.className = "he-state-row";
          const name = document.createElement("strong");
          name.textContent = label;
          row.append(name);
          return row;
        })
      );
    }

    if (hePathways) {
      hePathways.replaceChildren(
        ...pathways.map(([label]) => {
          const item = document.createElement("li");
          item.textContent = label;
          return item;
        })
      );
    }

    if (heSpots) {
      heSpots.replaceChildren(
        ...items.slice(0, 5).map((item) => {
          const card = document.createElement("article");
          card.className = "he-spot-card";

          const body = document.createElement("div");
          const similarity = document.createElement("span");
          similarity.className = "he-spot-similarity";
          similarity.textContent =
            `Cosine similarity ${Number(item.similarity || 0).toFixed(2)}`;
          const title = document.createElement("h5");
          title.textContent = item.dominant_cell_type || "Related atlas spot";
          const meta = document.createElement("p");
          meta.textContent = [item.species, item.organ, item.slice_id].filter(Boolean).join(" · ");
          const evidence = document.createElement("p");
          const matches = Array.isArray(item.matched_evidence) ? item.matched_evidence.join(", ") : "";
          evidence.textContent = matches ? `Matched evidence: ${matches}` : "Related atlas spot";
          const chatLink = document.createElement("a");
          chatLink.className = "he-spot-chat";
          chatLink.textContent = "Open in Spot Chat";
          chatLink.href = chatHref(item.spot_key);
          body.append(similarity, title, meta, evidence, chatLink);
          card.append(body);
          return card;
        })
      );
    }

    if (heContinue) {
      heContinue.href = chatHref(items[0]?.spot_key);
      heContinue.hidden = !items[0]?.spot_key;
    }

    const topState = states[0]?.[0] || "related tissue states";
    const topPathway = pathways[0]?.[0] || "measured biological programs";
    if (heResultTitle) heResultTitle.textContent = `${organName} H&E analysis`;
    if (heResultSummary) {
      heResultSummary.textContent =
        `Closest atlas match: ${topState}. Top matched pathway: ${topPathway}.`;
    }
    if (hePlaceholder) hePlaceholder.hidden = true;
    if (heResults) heResults.hidden = false;
  }

  if (heInput) {
    heInput.addEventListener("change", () => setHeFile(heInput.files?.[0]));
  }

  if (heDrop) {
    ["dragenter", "dragover"].forEach((type) => {
      heDrop.addEventListener(type, (event) => {
        event.preventDefault();
        heDrop.setAttribute("data-dragging", "true");
      });
    });
    ["dragleave", "drop"].forEach((type) => {
      heDrop.addEventListener(type, (event) => {
        event.preventDefault();
        heDrop.removeAttribute("data-dragging");
      });
    });
    heDrop.addEventListener("drop", (event) => {
      setHeFile(event.dataTransfer?.files?.[0]);
    });
  }

  if (heImageStage) {
    heImageStage.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const box = previewDisplayBox();
      const rect = hePreviewImage?.getBoundingClientRect();
      if (!box || !rect) return;
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      if (
        x < box.left ||
        x > box.left + box.width ||
        y < box.top ||
        y > box.top + box.height
      ) return;
      heSelectedPoint = {
        x: Math.max(0, Math.min(1, (x - box.left) / box.width)),
        y: Math.max(0, Math.min(1, (y - box.top) / box.height))
      };
      positionHeMarker();
      refreshHeFovPreview();
    });
  }

  if (heMpp) {
    heMpp.addEventListener("input", refreshHeFovPreview);
  }

  if (hePreviewImage) {
    hePreviewImage.addEventListener("load", () => {
      positionHeMarker();
      refreshHeFovPreview();
    });
  }

  window.addEventListener("resize", positionHeMarker);

  if (heExample) {
    heExample.addEventListener("click", async () => {
      try {
        heExample.disabled = true;
        heExample.textContent = "Loading example…";
        const response = await fetch("/notebooks/data/figure5_he_query_brain_context.png");
        if (!response.ok) throw new Error("The example image is unavailable.");
        const blob = await response.blob();
        const file = new File([blob], "human-cortical-field.png", {
          type: blob.type || "image/png"
        });
        await setHeFile(file, { micronsPerPixel: "0.86" });
        if (heSpecies) heSpecies.value = "human";
        await loadHeOrgans("brain");
      } catch (error) {
        setHeStatus(error.message || String(error), true);
      } finally {
        heExample.disabled = false;
        heExample.textContent = "Use the cortical tissue example";
      }
    });
  }

  if (heSpecies) {
    heSpecies.addEventListener("change", () => {
      loadHeOrgans().catch((error) => setHeStatus(error.message || String(error), true));
    });
  }

  if (heForm) {
    heForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!heSelectedFile) {
        setHeStatus("Choose an H&E image before starting the analysis.", true);
        return;
      }
      try {
        if (heSubmit) {
          heSubmit.disabled = true;
          heSubmit.textContent = "Analyzing tissue…";
        }
        setHeStatus("Extracting the 55 μm spot and 220 μm context, then running HistAgent.");
        const fov = buildHeFovPair(true);
        const data = await atlasApi("/api/retrieval/image", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            local_image_base64: fov.localImage,
            context_image_base64: fov.contextImage,
            spot_diameter_um: 55,
            context_diameter_um: 220,
            microns_per_pixel: fov.micronsPerPixel,
            species: heSpecies?.value || null,
            organ: heOrgan?.value || null,
            limit: 8
          })
        });
        if (!Array.isArray(data.items) || !data.items.length) {
          throw new Error("No measured atlas spots matched the current image and filters.");
        }
        renderHeAnalysis(data);
        setHeStatus(`Analysis complete · ${data.items.length} related measured spots returned.`);
      } catch (error) {
        setHeStatus(`Analysis failed: ${error.message || String(error)}`, true);
      } finally {
        if (heSubmit) {
          heSubmit.disabled = false;
          heSubmit.textContent = "Analyze tissue image";
        }
      }
    });
    let taxonomyLoaded = false;
    const initializeTissueAnalysis = () => {
      if (taxonomyLoaded) return;
      taxonomyLoaded = true;
      loadHeTaxonomy();
    };
    document.addEventListener("histagent:workspace", (event) => {
      if (event.detail?.workspace === "analyze") initializeTissueAnalysis();
    });
    const analyzePanel = document.querySelector('[data-atlas-panel="analyze"]');
    if (analyzePanel && !analyzePanel.hidden) initializeTissueAnalysis();
  }
})();
