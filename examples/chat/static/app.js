const BUILD_ID = "20260726copy1";

function safeGetLocal(k, fallback) {
  try {
    const v = window.localStorage.getItem(k);
    return v === null || v === undefined ? fallback : v;
  } catch (e) {
    return fallback;
  }
}

function safeSetLocal(k, v) {
  try {
    window.localStorage.setItem(k, v);
  } catch (e) {
    // ignore storage errors
  }
}

function parseStoredBool(v, fallback) {
  if (v === null || v === undefined || v === "") return fallback;
  if (typeof v === "boolean") return v;
  const s = String(v).trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(s)) return true;
  if (["0", "false", "no", "off"].includes(s)) return false;
  return fallback;
}

function fallbackUuid() {
  try {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
  } catch (e) {}
  // RFC4122-ish fallback
  let d = Date.now();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    const r = (d + Math.random() * 16) % 16 | 0;
    d = Math.floor(d / 16);
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

const state = {
  sessionId: safeGetLocal("histagent_session_id", fallbackUuid()),
  selectedSpotKey: null,
  loading: false,
  interactiveSpots: [],
  slices: [],
  currentSliceMap: null,
  currentSliceId: "",
  lang: "en",
  config: null,
  currentSpotRec: null,
  lastUserMessage: "",
  lastAssistantMessage: "",
  followupQuestions: [],
  askedUserQuestions: [],
  enableThinking: parseStoredBool(safeGetLocal("histagent_enable_thinking", ""), false),
  workspace: "chat",
  retrievalMode: "text",
  retrievalLoading: false,
  retrievalFile: null,
};

const spotMapView = {
  activeMapKey: "",
  initializedFor: "",
  scale: 1,
  fitScale: 1,
  tx: 0,
  ty: 0,
  drawPending: false,
  interactionBound: false,
  resizeObserver: null,
  dragging: false,
  pointerId: null,
  lastX: 0,
  lastY: 0,
  downX: 0,
  downY: 0,
  moved: false,
};

safeSetLocal("histagent_session_id", state.sessionId);

const I18N = {
  zh: {
    brandSubtitle: "组织 Spot 分子分析",
    labelSearchSpot: "选择 Spot",
    manualTitle: "手动输入 Spot ID",
    manualInputPlaceholder: "输入 spot_key（格式：slice_id<TAB>barcode）",
    manualSpotBtn: "加载该 Spot",
    clearSpotBtn: "通用聊天（不选 Spot）",
    interactiveTitle: "切片图选择",
    speciesLabel: "物种",
    speciesPlaceholder: "请选择物种",
    organLabel: "器官",
    organPlaceholder: "请选择器官",
    sliceLabel: "组织切片",
    slicePlaceholder: "请选择切片",
    spotMapHintEmpty: "当前 organ 暂无可视化 spot。",
    spotMapHintReady: "显示 {n} 个 spot · 缩放 {zoom}x",
    spotMapHintImageMissing: "未找到该切片原图，当前显示真实坐标下的 spot 分布。",
    spotFilterLabel: "筛选 Spot（可选）",
    spotFilterPlaceholder: "输入 slice/barcode 关键字筛选",
    spotPickLabel: "Spot 列表（点击即切换）",
    spotPickHintEmpty: "当前筛选条件下没有 Spot。",
    spotPickHintCount: "共 {n} 个 Spot（点击条目即可切换）",
    currentSpotLabel: "Spot 分子概览",
    serviceConfigLabel: "服务配置",
    resetBtn: "重置当前会话",
    chatTitle: "HistAgent Spot 对话",
    chatSubtitle: "询问所选 Spot 的基因排序、细胞状态、通路或空间上下文。",
    thinkingToggleLabel: "Thinking：开",
    thinkingToggleLabelOff: "Thinking：关",
    suggestionTitle: "可参考问题",
    messagePlaceholder: "输入你的问题（例如：这个 spot 的主导细胞类型是什么？）",
    sendBtn: "发送",
    sendBtnBusy: "生成中...",
    chooseLanguageZh: "中文",
    chooseLanguageEn: "EN",
    msgNotSelected: "未选择",
    msgSpecies: "物种",
    msgOrgan: "器官",
    msgSample: "样本",
    msgSpotId: "Spot 编号",
    msgMolecularSignals: "分子线索",
    msgPathwaySignals: "通路线索",
    msgSpatialContext: "空间上下文",
    msgMolecularSummary: "{genes} 个基因 · {cells} 类细胞线索",
    msgPathwaySummary: "{reactome} 条 Reactome · {gobp} 条 GO BP",
    msgPathwaySummaryGeneric: "{pathways} 条通路线索",
    msgSpatialSummaryNone: "未载入",
    msgSpatialSummaryYes: "已载入，邻域 {n}",
    cfgModel: "model",
    cfgApi: "api_base_url",
    cfgSpots: "spots",
    cfgSession: "session_id",
    welcome:
      "在左侧组织切片图上选择一个 Spot，然后开始提问。",
    generalMode: "已切换到通用聊天模式（未选择 Spot）。",
    switchedSpot: "已切换到 spot：{slice} / {barcode}\n你现在可以直接提问。",
    noSpotSelected: "请先通过左侧任意方式选择一个 spot。",
    noSpotCandidates: "当前没有可选 spot，请先调整 species/organ 或筛选条件。",
    emptySpotKey: "请先输入 spot_key。",
    manualLoadFailed: "手动加载失败: {err}",
    thinking: "思考中...",
    reqFailed: "请求失败: {err}",
    resetDone: "当前 spot 会话已重置。",
    initFailed: "初始化失败: {err}",
    statusReady: "就绪",
    statusGenerating: "正在生成",
    statusStreaming: "正在输出",
    suggestionQuestionsInit: [
      "请用一句话概括这个 spot 的分子身份。",
      "这个 spot 的主导细胞状态与关键通路之间是什么关系？",
    ],
    snapshotTitle: "Spot 分子概览",
    snapshotGeneral: "当前未选择 spot。你可以先提一般问题，或选择一个 spot 获得针对性推断。",
    snapshotCells: "细胞组成",
    snapshotPathway: "代表通路",
    snapshotSpatial: "空间上下文",
    snapshotSpatialNone: "无",
    snapshotSpatialYes: "可用（n={n}）",
    landingTag: "HistAgent",
    landingTitle: "询问一个组织 Spot",
    landingSubtitle: "先在左侧选择 Spot，再询问它的分子特征。",
    landingCap1: "基因排序",
    landingCap2: "细胞状态",
    landingCap3: "通路与空间上下文",
    workspaceChat: "Spot 对话",
    workspaceRetrieval: "图谱检索",
    retrievalEyebrow: "真实测量 ST 图谱",
    retrievalTitle: "检索生物学相关的 Spot",
    retrievalSubtitle: "使用自然语言描述或 H&E 查询图像检索空间图谱位置。",
    retrievalIndexBadge: "图谱已就绪",
    retrievalTextMode: "自然语言",
    retrievalImageMode: "H&E 图像",
    retrievalTextLabel: "描述你希望查找的组织状态",
    retrievalTextPlaceholder: "例如：伴随 T 细胞浸润和干扰素响应的乳腺肿瘤 spot",
    retrievalTextExample1: "富含 T 细胞的肿瘤微环境",
    retrievalTextExample2: "伴随细胞周期活性的上皮增殖",
    retrievalTextSubmit: "检索图谱",
    retrievalImageLabel: "上传以目标 Spot 为中心的 H&E 区域",
    retrievalDropTitle: "选择 H&E 图像",
    retrievalDropHint: "PNG、JPEG、TIFF 或 WebP · 最大 16 MB",
    retrievalMppLabel: "图像比例",
    retrievalSpeciesLabel: "物种（可选）",
    retrievalOrganLabel: "器官（可选）",
    retrievalAnySpecies: "全部物种",
    retrievalAnyOrgan: "全部器官",
    retrievalImageNote: "HistAgent 会以图像中心截取 55 μm 局部视野和 4× 上下文视野，再进行图像分析。",
    retrievalImageSubmit: "使用图像检索",
    retrievalTextBusy: "正在解析生物学描述并检索图谱…",
    retrievalImageBusy: "正在运行 HistAgent 图像推断；首次载入模型可能需要数分钟…",
    retrievalNeedText: "请先输入要检索的组织状态。",
    retrievalNeedImage: "请先选择一张 H&E 图像。",
    retrievalNeedScale: "请输入图像比例（μm / pixel）。",
    retrievalNoResults: "没有找到满足当前条件的 spot；可以减少限制或换一种描述。",
    retrievalResultsFound: "找到 {n} 个最相关 spot",
    retrievalPredictedGenes: "图像推断的前列基因",
    retrievalInterpretedAs: "检索理解",
    retrievalRank: "结果 {rank}",
    retrievalSimilarity: "相关度",
    retrievalGenes: "前列基因",
    retrievalPathways: "通路",
    retrievalMatched: "匹配证据",
    retrievalOpenChat: "载入并对话",
    retrievalFailed: "检索失败：{err}",
  },
  en: {
    brandSubtitle: "Molecular analysis of tissue spots",
    labelSearchSpot: "Select a spot",
    manualTitle: "Manual spot ID",
    manualInputPlaceholder: "Enter spot_key (format: slice_id<TAB>barcode)",
    manualSpotBtn: "Load Spot",
    clearSpotBtn: "General Chat (no spot)",
    interactiveTitle: "Select from Tissue Map",
    speciesLabel: "Species",
    speciesPlaceholder: "Select species",
    organLabel: "Organ",
    organPlaceholder: "Select organ",
    sliceLabel: "Tissue slice",
    slicePlaceholder: "Select slice",
    spotMapHintEmpty: "No visualized spots are available for this organ.",
    spotMapHintReady: "{n} spots · zoom {zoom}x",
    spotMapHintImageMissing: "No tissue image found for this slice; showing the real-coordinate spot layout.",
    spotFilterLabel: "Filter Spot (optional)",
    spotFilterPlaceholder: "Type slice/barcode keywords to filter",
    spotPickLabel: "Spot list (click to switch)",
    spotPickHintEmpty: "No spots found under current filters.",
    spotPickHintCount: "{n} spots found (click an item to switch)",
    currentSpotLabel: "Spot molecular profile",
    serviceConfigLabel: "Service Config",
    resetBtn: "Reset Current Session",
    chatTitle: "HistAgent Spot Chat",
    chatSubtitle:
      "Ask about the selected spot's ranked genes, cell states, pathways or spatial context.",
    thinkingToggleLabel: "Thinking: On",
    thinkingToggleLabelOff: "Thinking: Off",
    suggestionTitle: "Suggested Questions",
    messagePlaceholder: "Type your question (e.g., What is the dominant cell type in this spot?)",
    sendBtn: "Send",
    sendBtnBusy: "Generating...",
    chooseLanguageZh: "中文",
    chooseLanguageEn: "EN",
    msgNotSelected: "Not selected",
    msgSpecies: "Species",
    msgOrgan: "Organ",
    msgSample: "Sample",
    msgSpotId: "Spot ID",
    msgMolecularSignals: "Molecular signals",
    msgPathwaySignals: "Pathway signals",
    msgSpatialContext: "Spatial context",
    msgMolecularSummary: "{genes} genes · {cells} cell signals",
    msgPathwaySummary: "{reactome} Reactome · {gobp} GO BP",
    msgPathwaySummaryGeneric: "{pathways} pathway signals",
    msgSpatialSummaryNone: "Not included",
    msgSpatialSummaryYes: "Included, {n} neighbors",
    cfgModel: "model",
    cfgApi: "api_base_url",
    cfgSpots: "spots",
    cfgSession: "session_id",
    welcome:
      "Select a spot on the tissue map, then ask a question.",
    generalMode: "Switched to general chat mode (no spot selected).",
    switchedSpot: "Switched to spot: {slice} / {barcode}\nYou can ask directly now.",
    noSpotSelected: "Please select a spot from the left panel first.",
    noSpotCandidates: "No selectable spot under current filters. Adjust species/organ/filter first.",
    emptySpotKey: "Please input a spot_key first.",
    manualLoadFailed: "Manual load failed: {err}",
    thinking: "Thinking...",
    reqFailed: "Request failed: {err}",
    resetDone: "Current spot session has been reset.",
    initFailed: "Initialization failed: {err}",
    statusReady: "Ready",
    statusGenerating: "Generating",
    statusStreaming: "Streaming",
    suggestionQuestionsInit: [
      "Summarize the molecular identity of this spot in one sentence.",
      "How does the dominant cell state relate to the key pathway signal in this spot?",
    ],
    snapshotTitle: "Spot molecular profile",
    snapshotGeneral: "No spot is selected. You can ask general questions, or select a spot for targeted reasoning.",
    snapshotCells: "Cell composition",
    snapshotPathway: "Representative pathway",
    snapshotSpatial: "Spatial context",
    snapshotSpatialNone: "None",
    snapshotSpatialYes: "Available (n={n})",
    landingTag: "HistAgent",
    landingTitle: "Ask about a tissue spot",
    landingSubtitle:
      "Select a spot on the left, then ask about its molecular profile.",
    landingCap1: "Ranked genes",
    landingCap2: "Cell states",
    landingCap3: "Pathways and spatial context",
    workspaceChat: "Spot chat",
    workspaceRetrieval: "Atlas retrieval",
    retrievalEyebrow: "Measured ST atlas",
    retrievalTitle: "Retrieve biologically related spots",
    retrievalSubtitle: "Search spatial atlas locations with a biological description or an H&E query image.",
    retrievalIndexBadge: "Atlas ready",
    retrievalTextMode: "Natural language",
    retrievalImageMode: "H&E image",
    retrievalTextLabel: "Describe the tissue state you want to find",
    retrievalTextPlaceholder: "e.g. Breast tumor spots with T-cell infiltration and interferon-response programs",
    retrievalTextExample1: "T-cell-rich tumor microenvironment",
    retrievalTextExample2: "Epithelial proliferation with cell-cycle activity",
    retrievalTextSubmit: "Search atlas",
    retrievalImageLabel: "Upload a spot-centered H&E field",
    retrievalDropTitle: "Choose an H&E image",
    retrievalDropHint: "PNG, JPEG, TIFF or WebP · up to 16 MB",
    retrievalMppLabel: "Image scale",
    retrievalSpeciesLabel: "Species (optional)",
    retrievalOrganLabel: "Organ (optional)",
    retrievalAnySpecies: "Any species",
    retrievalAnyOrgan: "Any organ",
    retrievalImageNote: "HistAgent crops a 55 μm local view and a 4× context view around the image center before image analysis.",
    retrievalImageSubmit: "Search with image",
    retrievalTextBusy: "Interpreting the biological description and searching the atlas…",
    retrievalImageBusy: "Running HistAgent image inference; the first model load can take several minutes…",
    retrievalNeedText: "Describe a tissue state before searching.",
    retrievalNeedImage: "Choose an H&E image before searching.",
    retrievalNeedScale: "Enter the image scale in μm / pixel.",
    retrievalNoResults: "No spots matched the current constraints. Try a broader description or fewer filters.",
    retrievalResultsFound: "{n} related spots found",
    retrievalPredictedGenes: "Top image-inferred genes",
    retrievalInterpretedAs: "Query interpretation",
    retrievalRank: "Result {rank}",
    retrievalSimilarity: "relevance",
    retrievalGenes: "Top genes",
    retrievalPathways: "Pathways",
    retrievalMatched: "Matched evidence",
    retrievalOpenChat: "Open in chat",
    retrievalFailed: "Retrieval failed: {err}",
  },
};

const els = {
  brandSubtitle: document.getElementById("brandSubtitle"),
  labelSearchSpot: document.getElementById("labelSearchSpot"),
  manualTitle: document.getElementById("manualTitle"),
  manualSpotInput: document.getElementById("manualSpotInput"),
  manualSpotBtn: document.getElementById("manualSpotBtn"),
  clearSpotBtn: document.getElementById("clearSpotBtn"),
  interactiveTitle: document.getElementById("interactiveTitle"),
  speciesLabel: document.getElementById("speciesLabel"),
  speciesSelect: document.getElementById("speciesSelect"),
  organLabel: document.getElementById("organLabel"),
  organSelect: document.getElementById("organSelect"),
  sliceLabel: document.getElementById("sliceLabel"),
  sliceSelect: document.getElementById("sliceSelect"),
  spotMapStage: document.getElementById("spotMapStage"),
  spotMapImage: document.getElementById("spotMapImage"),
  spotMapCanvas: document.getElementById("spotMapCanvas"),
  spotZoomOutBtn: document.getElementById("spotZoomOutBtn"),
  spotZoomResetBtn: document.getElementById("spotZoomResetBtn"),
  spotZoomInBtn: document.getElementById("spotZoomInBtn"),
  spotMapHint: document.getElementById("spotMapHint"),
  spotFilterLabel: document.getElementById("spotFilterLabel"),
  spotFilterInput: document.getElementById("spotFilterInput"),
  spotPickLabel: document.getElementById("spotPickLabel"),
  spotPickList: document.getElementById("spotPickList"),
  spotPickHint: document.getElementById("spotPickHint"),
  currentSpotLabel: document.getElementById("currentSpotLabel"),
  serviceConfigLabel: document.getElementById("serviceConfigLabel"),
  spotMeta: document.getElementById("spotMeta"),
  configMeta: document.getElementById("configMeta"),
  resetBtn: document.getElementById("resetBtn"),
  chatTitle: document.getElementById("chatTitle"),
  chatSubtitle: document.getElementById("chatSubtitle"),
  thinkingToggleBtn: document.getElementById("thinkingToggleBtn"),
  langZhBtn: document.getElementById("langZhBtn"),
  langEnBtn: document.getElementById("langEnBtn"),
  suggestionTitle: document.getElementById("suggestionTitle"),
  suggestions: document.getElementById("suggestions"),
  spotSnapshot: document.getElementById("spotSnapshot"),
  messages: document.getElementById("messages"),
  chatForm: document.getElementById("chatForm"),
  messageInput: document.getElementById("messageInput"),
  sendBtn: document.getElementById("sendBtn"),
  msgTpl: document.getElementById("msgTpl"),
  appStatus: document.getElementById("appStatus"),
  suggestionBar: document.querySelector(".suggestion-bar"),
  chatWorkspaceBtn: document.getElementById("chatWorkspaceBtn"),
  retrievalWorkspaceBtn: document.getElementById("retrievalWorkspaceBtn"),
  retrievalPanel: document.getElementById("retrievalPanel"),
  retrievalEyebrow: document.getElementById("retrievalEyebrow"),
  retrievalTitle: document.getElementById("retrievalTitle"),
  retrievalSubtitle: document.getElementById("retrievalSubtitle"),
  retrievalIndexBadge: document.getElementById("retrievalIndexBadge"),
  retrievalTextModeBtn: document.getElementById("retrievalTextModeBtn"),
  retrievalImageModeBtn: document.getElementById("retrievalImageModeBtn"),
  retrievalTextForm: document.getElementById("retrievalTextForm"),
  retrievalTextLabel: document.getElementById("retrievalTextLabel"),
  retrievalTextInput: document.getElementById("retrievalTextInput"),
  retrievalTextSubmit: document.getElementById("retrievalTextSubmit"),
  retrievalImageForm: document.getElementById("retrievalImageForm"),
  retrievalImageLabel: document.getElementById("retrievalImageLabel"),
  retrievalImageInput: document.getElementById("retrievalImageInput"),
  retrievalImagePreview: document.getElementById("retrievalImagePreview"),
  retrievalDropTitle: document.getElementById("retrievalDropTitle"),
  retrievalDropHint: document.getElementById("retrievalDropHint"),
  retrievalMppLabel: document.getElementById("retrievalMppLabel"),
  retrievalMppInput: document.getElementById("retrievalMppInput"),
  retrievalSpeciesLabel: document.getElementById("retrievalSpeciesLabel"),
  retrievalOrganLabel: document.getElementById("retrievalOrganLabel"),
  retrievalSpeciesSelect: document.getElementById("retrievalSpeciesSelect"),
  retrievalOrganSelect: document.getElementById("retrievalOrganSelect"),
  retrievalImageNote: document.getElementById("retrievalImageNote"),
  retrievalImageSubmit: document.getElementById("retrievalImageSubmit"),
  retrievalStatus: document.getElementById("retrievalStatus"),
  retrievalQuerySummary: document.getElementById("retrievalQuerySummary"),
  retrievalResults: document.getElementById("retrievalResults"),
};

function t(key) {
  const dict = I18N[state.lang] || I18N.zh;
  return dict[key] !== undefined ? dict[key] : key;
}

function tf(key, vars = {}) {
  let s = t(key);
  for (const kv of Object.entries(vars)) {
    const k = kv[0];
    const v = kv[1];
    s = s.split(`{${k}}`).join(String(v));
  }
  return s;
}

function escapeHtml(str) {
  return (str || "").split("&").join("&amp;").split("<").join("&lt;").split(">").join("&gt;");
}

function renderUserText(text) {
  return escapeHtml(text || "").split("\n").join("<br/>");
}

function renderMarkdown(text) {
  const source = String(text || "").replace(/\r\n/g, "\n");
  const hasMarked = !!(window.marked && typeof window.marked.parse === "function");
  const hasPurify = !!(window.DOMPurify && typeof window.DOMPurify.sanitize === "function");

  if (hasMarked && hasPurify) {
    try {
      const rawHtml = window.marked.parse(source, {
        gfm: true,
        breaks: true,
        mangle: false,
        headerIds: false,
      });
      const cleanHtml = window.DOMPurify.sanitize(rawHtml, {
        USE_PROFILES: { html: true },
        FORBID_TAGS: ["style", "script", "iframe", "object", "embed", "svg", "math", "form"],
        FORBID_ATTR: ["style"],
      });
      const wrapper = document.createElement("div");
      wrapper.innerHTML = cleanHtml;
      const links = wrapper.querySelectorAll("a[href]");
      links.forEach((a) => {
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener noreferrer nofollow");
      });
      return wrapper.innerHTML;
    } catch (err) {
      console.warn("[renderMarkdown] marked+DOMPurify failed, falling back:", err);
    }
  }

  return renderMarkdownLegacy(source);
}

function renderMarkdownLegacy(text) {
  let s = String(text || "");
  s = s.replace(/\r\n/g, "\n");
  s = escapeHtml(s);

  // fenced code blocks
  const codeBlocks = [];
  s = s.replace(/```([\s\S]*?)```/g, function (_, code) {
    const html = `<pre><code>${code.trim()}</code></pre>`;
    codeBlocks.push(html);
    return `@@CODEBLOCK_${codeBlocks.length - 1}@@`;
  });

  // inline markdown
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");

  const lines = s.split("\n");
  const out = [];
  let inUl = false;
  let inOl = false;
  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");
    if (!line.trim()) {
      if (inUl) {
        out.push("</ul>");
        inUl = false;
      }
      if (inOl) {
        out.push("</ol>");
        inOl = false;
      }
      out.push("<br/>");
      continue;
    }
    const ulMatch = line.match(/^\s*[-*]\s+(.+)$/);
    const olMatch = line.match(/^\s*\d+\.\s+(.+)$/);
    if (ulMatch) {
      if (inOl) {
        out.push("</ol>");
        inOl = false;
      }
      if (!inUl) {
        out.push("<ul>");
        inUl = true;
      }
      out.push(`<li>${ulMatch[1]}</li>`);
      continue;
    }
    if (olMatch) {
      if (inUl) {
        out.push("</ul>");
        inUl = false;
      }
      if (!inOl) {
        out.push("<ol>");
        inOl = true;
      }
      out.push(`<li>${olMatch[1]}</li>`);
      continue;
    }
    if (inUl) {
      out.push("</ul>");
      inUl = false;
    }
    if (inOl) {
      out.push("</ol>");
      inOl = false;
    }
    if (/^###\s+/.test(line)) {
      out.push(`<h4>${line.replace(/^###\s+/, "")}</h4>`);
    } else if (/^##\s+/.test(line)) {
      out.push(`<h3>${line.replace(/^##\s+/, "")}</h3>`);
    } else if (/^#\s+/.test(line)) {
      out.push(`<h2>${line.replace(/^#\s+/, "")}</h2>`);
    } else {
      out.push(`<p>${line}</p>`);
    }
  }
  if (inUl) out.push("</ul>");
  if (inOl) out.push("</ol>");

  let html = out.join("");
  html = html.replace(/@@CODEBLOCK_(\d+)@@/g, function (_, idx) {
    return codeBlocks[Number(idx)] || "";
  });
  return html;
}

function renderMessageHtml(role, text) {
  if (role === "assistant") {
    return renderMarkdown(text);
  }
  return renderUserText(text);
}

function setStatus(text) {
  if (els.appStatus) {
    els.appStatus.textContent = text;
  }
}

function renderThinkingToggle() {
  if (!els.thinkingToggleBtn) return;
  els.thinkingToggleBtn.textContent = state.enableThinking ? t("thinkingToggleLabel") : t("thinkingToggleLabelOff");
  els.thinkingToggleBtn.classList.toggle("active", !!state.enableThinking);
  els.thinkingToggleBtn.setAttribute("aria-pressed", state.enableThinking ? "true" : "false");
}

function renderLanding() {
  if (!els.messages) return;
  els.messages.classList.add("is-landing");
  els.messages.innerHTML = `
<div class="landing-shell">
  <div class="landing-lockup">
    <div class="landing-logo-ha" aria-hidden="true">HA</div>
    <div class="landing-wordmark landing-wordmark-stacked"><span class="hist">Hist</span><span class="reason">Agent</span></div>
  </div>
  <p class="landing-subtitle">${escapeHtml(t("landingSubtitle"))}</p>
  <div class="landing-caps">
    <span class="landing-cap"><span class="icon-mask icon-gene" aria-hidden="true"></span>${escapeHtml(t("landingCap1"))}</span>
    <span class="landing-cap"><span class="icon-mask icon-cells" aria-hidden="true"></span>${escapeHtml(t("landingCap2"))}</span>
    <span class="landing-cap"><span class="icon-mask icon-pathway" aria-hidden="true"></span>${escapeHtml(t("landingCap3"))}</span>
  </div>
</div>`;
}

function leaveLandingIfNeeded() {
  if (!els.messages) return;
  if (els.messages.classList.contains("is-landing")) {
    els.messages.classList.remove("is-landing");
    els.messages.innerHTML = "";
  }
}

function syncLandingState() {
  if (!els.messages) return;
  const hasMsg = !!els.messages.querySelector(".msg");
  if (!hasMsg) {
    renderLanding();
  }
  const showSuggestion = hasMsg;
  if (els.suggestionBar) {
    els.suggestionBar.style.display = showSuggestion ? "" : "none";
  }
}

function syncModeClass() {
  const hasSpot = !!state.currentSpotRec;
  document.body.classList.toggle("spot-mode", hasSpot);
  document.body.classList.toggle("general-mode", !hasSpot);
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await resp.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!resp.ok) {
    const detail = (data && data.detail) || (data && data.raw) || `HTTP ${resp.status}`;
    throw new Error(detail);
  }
  return data;
}

function addMessage(role, text) {
  if (!els.msgTpl || !els.messages) {
    return null;
  }
  leaveLandingIfNeeded();
  const node = els.msgTpl.content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  node.querySelector(".bubble").innerHTML = renderMessageHtml(role, text);
  els.messages.appendChild(node);
  els.messages.scrollTop = els.messages.scrollHeight;
  syncLandingState();
  return node;
}

function setAssistantBubbleStreaming(node, text, active = true) {
  if (!node || !node.querySelector) return;
  const bubble = node.querySelector(".bubble");
  if (!bubble) return;
  bubble.classList.toggle("is-streaming", !!active);
  bubble.classList.toggle("is-thinking", !!active && !String(text || "").trim());
  bubble.textContent = String(text || "").trim() ? String(text || "") : t("thinking");
}

function setAssistantBubbleFinal(node, text) {
  if (!node || !node.querySelector) return;
  const bubble = node.querySelector(".bubble");
  if (!bubble) return;
  bubble.classList.remove("is-streaming", "is-thinking");
  bubble.innerHTML = renderMessageHtml("assistant", text || "");
}

function setComposerBusy(busy) {
  state.loading = !!busy;
  if (els.sendBtn) {
    els.sendBtn.disabled = !!busy;
    els.sendBtn.classList.toggle("is-busy", !!busy);
    els.sendBtn.textContent = busy ? t("sendBtnBusy") : t("sendBtn");
  }
  if (els.messageInput) {
    els.messageInput.disabled = !!busy;
  }
  if (els.manualSpotBtn) els.manualSpotBtn.disabled = !!busy;
  if (els.clearSpotBtn) els.clearSpotBtn.disabled = !!busy;
  if (els.manualSpotInput) els.manualSpotInput.disabled = !!busy;
  if (els.speciesSelect) els.speciesSelect.disabled = !!busy;
  if (els.organSelect) els.organSelect.disabled = !!busy;
  if (els.sliceSelect) els.sliceSelect.disabled = !!busy;
  if (els.spotFilterInput) els.spotFilterInput.disabled = !!busy;
  if (els.resetBtn) els.resetBtn.disabled = !!busy;
  if (els.thinkingToggleBtn) els.thinkingToggleBtn.disabled = !!busy;
}

async function streamChat(payload, handlers = {}) {
  const resp = await fetch("/api/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!resp.ok) {
    const text = await resp.text();
    let detail = `HTTP ${resp.status}`;
    try {
      const data = text ? JSON.parse(text) : {};
      detail = (data && data.detail) || (data && data.raw) || detail;
    } catch {
      detail = text || detail;
    }
    throw new Error(detail);
  }

  if (!resp.body) {
    throw new Error("stream body is empty");
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const emit = (evt) => {
    if (!evt || typeof evt !== "object") return;
    if (evt.type === "start" && typeof handlers.onStart === "function") handlers.onStart(evt);
    if (evt.type === "delta" && typeof handlers.onDelta === "function") handlers.onDelta(String(evt.delta || ""));
    if (evt.type === "replace" && typeof handlers.onReplace === "function") handlers.onReplace(String(evt.answer || ""));
    if (evt.type === "final" && typeof handlers.onFinal === "function") handlers.onFinal(evt);
    if (evt.type === "error") throw new Error(String(evt.error || "stream failed"));
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

    let idx = buffer.indexOf("\n");
    while (idx >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (line) {
        emit(JSON.parse(line));
      }
      idx = buffer.indexOf("\n");
    }

    if (done) break;
  }

  const tail = buffer.trim();
  if (tail) {
    emit(JSON.parse(tail));
  }
}

function renderMetaRows(rows) {
  return (rows || [])
    .map(
      (row) => `
<div class="meta-row">
  <div class="meta-key"><span class="icon-mask icon-${escapeHtml(row.icon || "spot")}" aria-hidden="true"></span><span>${escapeHtml(row.label || "")}</span></div>
  <div class="meta-value">${escapeHtml(row.value || "")}</div>
</div>`
    )
    .join("");
}

function toTitleToken(v) {
  const s = String(v || "").trim();
  if (!s) return "";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function formatSampleId(v) {
  return String(v || "").replace(/^GEO_/, "").trim();
}

function setSpotMeta(rec) {
  if (!els.spotMeta) {
    return;
  }
  if (!rec) {
    els.spotMeta.textContent = t("msgNotSelected");
    els.spotMeta.classList.remove("meta-grid");
    els.spotMeta.classList.add("is-empty");
    return;
  }
  els.spotMeta.classList.remove("is-empty");
  els.spotMeta.classList.add("meta-grid");
  const decon = rec.decon_topk || [];
  const pw = rec.pathways || {};
  const spatial = rec.spatial_context || {};
  const reactomeN = (pw.reactome_top || []).length;
  const gobpN = (pw.gobp_top || []).length;
  const pathwayN = reactomeN + gobpN + (pw.compact_top || []).length;
  const molecularSummary = tf("msgMolecularSummary", {
    genes: (rec.top_genes || []).length,
    cells: decon.length,
  });
  const pathwaySummary =
    reactomeN || gobpN
      ? tf("msgPathwaySummary", {
          reactome: reactomeN,
          gobp: gobpN,
        })
      : tf("msgPathwaySummaryGeneric", { pathways: pathwayN });
  const spatialSummary = spatial.available
    ? tf("msgSpatialSummaryYes", { n: spatial.n_neighbors || 0 })
    : t("msgSpatialSummaryNone");
  els.spotMeta.innerHTML = renderMetaRows([
    { icon: "cells", label: t("msgSpecies"), value: toTitleToken(rec.species) || "NA" },
    { icon: "spot", label: t("msgOrgan"), value: toTitleToken(rec.organ) || "NA" },
    { icon: "spot", label: t("msgSample"), value: formatSampleId(rec.slice_id) || "NA" },
    { icon: "spot", label: t("msgSpotId"), value: rec.barcode || "NA" },
    { icon: "gene", label: t("msgMolecularSignals"), value: molecularSummary },
    { icon: "pathway", label: t("msgPathwaySignals"), value: pathwaySummary },
    { icon: "spatial", label: t("msgSpatialContext"), value: spatialSummary },
  ]);
}

function setOptions(selectEl, items, placeholder = "") {
  if (!selectEl) return;
  selectEl.innerHTML = "";
  if (placeholder) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = placeholder;
    selectEl.appendChild(opt);
  }
  for (const x of items || []) {
    const opt = document.createElement("option");
    opt.value = String(x);
    opt.textContent = String(x);
    selectEl.appendChild(opt);
  }
}

function renderSpotPickList(items) {
  if (!els.spotPickList || !els.spotPickHint) {
    return;
  }
  els.spotPickList.innerHTML = "";
  const rows = (items || []).slice(0, 120);
  for (const row of rows) {
    const div = document.createElement("div");
    div.className = "spot-pick-item" + (row.spot_key === state.selectedSpotKey ? " active" : "");
    div.innerHTML = `
      <div class="k1">${escapeHtml(row.species)}/${escapeHtml(row.organ)}</div>
      <div class="k2">${escapeHtml(row.slice_id)}</div>
      <div class="k1">${escapeHtml(row.barcode)}</div>
    `;
    div.onclick = async () => {
      await setCurrentSpot(row.spot_key, true);
      renderSpotPickList(state.interactiveSpots);
    };
    els.spotPickList.appendChild(div);
  }
  if (!items || items.length === 0) {
    els.spotPickHint.textContent = t("spotPickHintEmpty");
  } else {
    const shown = items.length > rows.length ? `${rows.length}/${items.length}` : String(items.length);
    els.spotPickHint.textContent = tf("spotPickHintCount", { n: shown });
  }
}

function _spotColor(label) {
  const palette = ["#10a37f", "#2563eb", "#f97316", "#dc2626", "#7c3aed", "#0891b2", "#65a30d", "#be123c"];
  const s = String(label || "spot");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return palette[h % palette.length];
}

function _mapWorld(map) {
  const hasImage = !!(map && map.image_url);
  const img = els.spotMapImage;
  const imageW = Number(map && map.image_width);
  const imageH = Number(map && map.image_height);
  if (hasImage && Number.isFinite(imageW) && Number.isFinite(imageH) && imageW > 0 && imageH > 0) {
    return { x: 0, y: 0, w: imageW, h: imageH };
  }
  if (hasImage && img && img.complete && img.naturalWidth > 0 && img.naturalHeight > 0) {
    return { x: 0, y: 0, w: img.naturalWidth, h: img.naturalHeight };
  }
  const b = (map && map.bounds) || {};
  const spots = (map && map.spots) || [];
  const xs = spots.map((x) => Number(x.x)).filter((x) => Number.isFinite(x));
  const ys = spots.map((x) => Number(x.y)).filter((x) => Number.isFinite(x));
  const minX = Number.isFinite(Number(b.min_x)) ? Number(b.min_x) : Math.min(...xs, 0);
  const maxX = Number.isFinite(Number(b.max_x)) ? Number(b.max_x) : Math.max(...xs, 1);
  const minY = Number.isFinite(Number(b.min_y)) ? Number(b.min_y) : Math.min(...ys, 0);
  const maxY = Number.isFinite(Number(b.max_y)) ? Number(b.max_y) : Math.max(...ys, 1);
  const w = Math.max(1, maxX - minX);
  const h = Math.max(1, maxY - minY);
  const pad = Math.max(35, Math.max(w, h) * 0.08);
  return { x: minX - pad, y: minY - pad, w: w + pad * 2, h: h + pad * 2 };
}

function _spotMapImageSrc(map) {
  if (!map || !map.image_url) return "";
  const sep = String(map.image_url).includes("?") ? "&" : "?";
  return `${map.image_url}${sep}v=${BUILD_ID}`;
}

function _spotMapCanvasSize() {
  const canvas = els.spotMapCanvas;
  if (!canvas) return { cssW: 0, cssH: 0, dpr: 1 };
  const rect = canvas.getBoundingClientRect();
  const cssW = Math.max(1, Math.floor(rect.width || 1));
  const cssH = Math.max(1, Math.floor(rect.height || 1));
  const dpr = Math.max(1, Math.min(2.5, window.devicePixelRatio || 1));
  const bw = Math.max(1, Math.floor(cssW * dpr));
  const bh = Math.max(1, Math.floor(cssH * dpr));
  if (canvas.width !== bw || canvas.height !== bh) {
    canvas.width = bw;
    canvas.height = bh;
  }
  return { cssW, cssH, dpr };
}

function _fitSpotMapView(world, cssW, cssH) {
  const pad = 12;
  const fit = Math.max(0.0001, Math.min((cssW - pad * 2) / world.w, (cssH - pad * 2) / world.h));
  spotMapView.fitScale = fit;
  spotMapView.scale = fit;
  spotMapView.tx = (cssW - world.w * fit) / 2 - world.x * fit;
  spotMapView.ty = (cssH - world.h * fit) / 2 - world.y * fit;
}

function _spotMapZoomText() {
  const base = spotMapView.fitScale || spotMapView.scale || 1;
  const z = Math.max(1, spotMapView.scale / base);
  return z >= 10 ? z.toFixed(0) : z.toFixed(1);
}

function _spotWorldRadius(map, spots, world) {
  if (map && Number.isFinite(map._spotWorldRadius) && map._spotWorldRadius > 0) {
    return map._spotWorldRadius;
  }
  const b = (map && map.bounds) || {};
  const minX = Number(b.min_x);
  const maxX = Number(b.max_x);
  const minY = Number(b.min_y);
  const maxY = Number(b.max_y);
  const bw = Number.isFinite(minX) && Number.isFinite(maxX) && maxX > minX ? maxX - minX : world.w;
  const bh = Number.isFinite(minY) && Number.isFinite(maxY) && maxY > minY ? maxY - minY : world.h;
  const spacing = Math.sqrt(Math.max(1, (bw * bh) / Math.max(1, (spots || []).length)));
  const radius = Math.max(8, Math.min(spacing * 0.22, Math.max(world.w, world.h) * 0.008));
  if (map) {
    map._spotWorldRadius = radius;
  }
  return radius;
}

function _updateSpotMapHint() {
  if (!els.spotMapHint) return;
  const map = state.currentSliceMap || null;
  const spots = (map && map.spots) || [];
  if (!map || spots.length === 0) {
    els.spotMapHint.textContent = t("spotMapHintEmpty");
    return;
  }
  const total = Number(map.total_spots || map.n_spots || spots.length);
  const shownText = total > spots.length ? `${spots.length}/${total}` : String(spots.length);
  const text = tf("spotMapHintReady", { n: shownText, zoom: _spotMapZoomText() });
  els.spotMapHint.textContent = map.image_url ? text : `${text} ${t("spotMapHintImageMissing")}`;
  if (els.spotZoomResetBtn) {
    els.spotZoomResetBtn.textContent = `${_spotMapZoomText()}x`;
  }
}

function _clearSpotMapCanvas() {
  const canvas = els.spotMapCanvas;
  if (!canvas) return;
  const { cssW, cssH, dpr } = _spotMapCanvasSize();
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
}

function scheduleSpotMapDraw(resetView = false) {
  if (resetView) {
    spotMapView.initializedFor = "";
  }
  if (spotMapView.drawPending) return;
  spotMapView.drawPending = true;
  window.requestAnimationFrame(() => {
    spotMapView.drawPending = false;
    drawSpotMap();
  });
}

function drawSpotMap() {
  const canvas = els.spotMapCanvas;
  if (!canvas || !els.spotMapStage) return;
  const map = state.currentSliceMap || null;
  const spots = (map && map.spots) || [];
  const ctx = canvas.getContext("2d");
  if (!ctx || !map || spots.length === 0) {
    _clearSpotMapCanvas();
    return;
  }
  const { cssW, cssH, dpr } = _spotMapCanvasSize();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const img = els.spotMapImage;
  const hasImage = !!map.image_url;
  const imageReady = !!(hasImage && img && img.complete && img.naturalWidth > 0 && img.naturalHeight > 0);
  const world = _mapWorld(map);
  const viewKey = `${map.slice_id || ""}|${world.x}|${world.y}|${world.w}|${world.h}|${imageReady ? _spotMapImageSrc(map) : "no-image"}`;
  if (spotMapView.initializedFor !== viewKey) {
    _fitSpotMapView(world, cssW, cssH);
    spotMapView.initializedFor = viewKey;
  } else {
    spotMapView.fitScale = Math.max(0.0001, Math.min((cssW - 24) / world.w, (cssH - 24) / world.h));
  }

  const scale = spotMapView.scale;
  const tx = spotMapView.tx;
  const ty = spotMapView.ty;

  if (imageReady) {
    ctx.save();
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, tx + world.x * scale, ty + world.y * scale, world.w * scale, world.h * scale);
    ctx.restore();
  }

  const margin = 24 / Math.max(scale, 0.0001);
  const minX = (0 - tx) / scale - margin;
  const maxX = (cssW - tx) / scale + margin;
  const minY = (0 - ty) / scale - margin;
  const maxY = (cssH - ty) / scale + margin;
  const spotRadius = Math.max(0.9, Math.min(10, _spotWorldRadius(map, spots, world) * scale));
  let activeRow = null;

  ctx.save();
  ctx.globalAlpha = imageReady ? 0.78 : 0.86;
  for (const row of spots) {
    const x = Number(row.x);
    const y = Number(row.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    if (x < minX || x > maxX || y < minY || y > maxY) continue;
    const active = row.spot_key === state.selectedSpotKey;
    if (active) {
      activeRow = row;
      continue;
    }
    const sx = tx + x * scale;
    const sy = ty + y * scale;
    ctx.beginPath();
    ctx.fillStyle = row._spotColor || (row._spotColor = _spotColor(row.dominant_cell_type || row.organ));
    ctx.arc(sx, sy, spotRadius, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();

  if (activeRow) {
    const x = Number(activeRow.x);
    const y = Number(activeRow.y);
    if (Number.isFinite(x) && Number.isFinite(y)) {
      const sx = tx + x * scale;
      const sy = ty + y * scale;
      ctx.save();
      ctx.shadowColor = "rgba(15, 23, 42, 0.4)";
      ctx.shadowBlur = 8;
      ctx.beginPath();
      ctx.fillStyle = activeRow._spotColor || (activeRow._spotColor = _spotColor(activeRow.dominant_cell_type || activeRow.organ));
      ctx.arc(sx, sy, Math.min(14, spotRadius * 2.0), 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.lineWidth = 2.2;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();
      ctx.restore();
    }
  }
  _updateSpotMapHint();
}

function renderSpotMap() {
  if (!els.spotMapCanvas || !els.spotMapStage || !els.spotMapHint) return;
  const map = state.currentSliceMap || null;
  const spots = (map && map.spots) || [];

  if (!map || spots.length === 0) {
    if (els.spotMapImage) {
      els.spotMapImage.removeAttribute("src");
    }
    spotMapView.initializedFor = "";
    els.spotMapStage.classList.add("is-empty", "no-image");
    els.spotMapHint.textContent = t("spotMapHintEmpty");
    if (els.spotZoomResetBtn) {
      els.spotZoomResetBtn.textContent = "1.0x";
    }
    _clearSpotMapCanvas();
    return;
  }

  const hasImage = !!map.image_url;
  els.spotMapStage.classList.toggle("no-image", !hasImage);
  els.spotMapStage.classList.remove("is-empty");
  const mapKey = `${map.slice_id || ""}|${map.image_url || ""}|${spots.length}`;
  if (spotMapView.activeMapKey !== mapKey) {
    spotMapView.activeMapKey = mapKey;
    spotMapView.initializedFor = "";
  }
  if (els.spotMapImage) {
    if (hasImage) {
      const src = _spotMapImageSrc(map);
      if (els.spotMapImage.getAttribute("src") !== src) {
        els.spotMapImage.onload = () => scheduleSpotMapDraw(true);
        els.spotMapImage.onerror = () => scheduleSpotMapDraw(true);
        els.spotMapImage.src = src;
      }
    } else {
      els.spotMapImage.removeAttribute("src");
    }
  }

  _updateSpotMapHint();
  scheduleSpotMapDraw(false);
}

function _spotMapPoint(evt) {
  const canvas = els.spotMapCanvas;
  if (!canvas) return { x: 0, y: 0 };
  const rect = canvas.getBoundingClientRect();
  return {
    x: evt.clientX - rect.left,
    y: evt.clientY - rect.top,
  };
}

function zoomSpotMapAt(x, y, factor) {
  const map = state.currentSliceMap || null;
  if (!map || !els.spotMapCanvas) return;
  const nextScale = Math.max(
    Math.max(spotMapView.fitScale * 0.65, 0.0001),
    Math.min(spotMapView.scale * factor, spotMapView.fitScale * 80)
  );
  const wx = (x - spotMapView.tx) / spotMapView.scale;
  const wy = (y - spotMapView.ty) / spotMapView.scale;
  spotMapView.scale = nextScale;
  spotMapView.tx = x - wx * nextScale;
  spotMapView.ty = y - wy * nextScale;
  scheduleSpotMapDraw(false);
}

function resetSpotMapView() {
  spotMapView.initializedFor = "";
  scheduleSpotMapDraw(true);
}

function _nearestSpotAt(x, y) {
  const map = state.currentSliceMap || null;
  const spots = (map && map.spots) || [];
  if (!spots.length || !Number.isFinite(spotMapView.scale) || spotMapView.scale <= 0) return null;
  const threshold = 13;
  let best = null;
  let bestD2 = threshold * threshold;
  for (const row of spots) {
    const rx = Number(row.x);
    const ry = Number(row.y);
    if (!Number.isFinite(rx) || !Number.isFinite(ry)) continue;
    const sx = spotMapView.tx + rx * spotMapView.scale;
    const sy = spotMapView.ty + ry * spotMapView.scale;
    const dx = sx - x;
    const dy = sy - y;
    const d2 = dx * dx + dy * dy;
    if (d2 <= bestD2) {
      bestD2 = d2;
      best = row;
    }
  }
  return best;
}

function bindSpotMapInteractions() {
  if (spotMapView.interactionBound || !els.spotMapCanvas) return;
  spotMapView.interactionBound = true;
  const canvas = els.spotMapCanvas;

  canvas.addEventListener(
    "wheel",
    (e) => {
      if (!state.currentSliceMap) return;
      e.preventDefault();
      const pt = _spotMapPoint(e);
      zoomSpotMapAt(pt.x, pt.y, Math.exp(-e.deltaY * 0.0012));
    },
    { passive: false }
  );

  canvas.addEventListener("pointerdown", (e) => {
    if (!state.currentSliceMap || (e.pointerType === "mouse" && e.button !== 0)) return;
    const pt = _spotMapPoint(e);
    spotMapView.dragging = true;
    spotMapView.pointerId = e.pointerId;
    spotMapView.downX = pt.x;
    spotMapView.downY = pt.y;
    spotMapView.lastX = pt.x;
    spotMapView.lastY = pt.y;
    spotMapView.moved = false;
    if (els.spotMapStage) els.spotMapStage.classList.add("is-dragging");
    try {
      canvas.setPointerCapture(e.pointerId);
    } catch (err) {}
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!spotMapView.dragging || spotMapView.pointerId !== e.pointerId) return;
    e.preventDefault();
    const pt = _spotMapPoint(e);
    const dx = pt.x - spotMapView.lastX;
    const dy = pt.y - spotMapView.lastY;
    const totalDx = pt.x - spotMapView.downX;
    const totalDy = pt.y - spotMapView.downY;
    if (Math.hypot(totalDx, totalDy) > 4) {
      spotMapView.moved = true;
    }
    spotMapView.tx += dx;
    spotMapView.ty += dy;
    spotMapView.lastX = pt.x;
    spotMapView.lastY = pt.y;
    scheduleSpotMapDraw(false);
  });

  const finishPointer = (e) => {
    if (!spotMapView.dragging || spotMapView.pointerId !== e.pointerId) return;
    const pt = _spotMapPoint(e);
    spotMapView.dragging = false;
    spotMapView.pointerId = null;
    if (els.spotMapStage) els.spotMapStage.classList.remove("is-dragging");
    try {
      canvas.releasePointerCapture(e.pointerId);
    } catch (err) {}
    if (!spotMapView.moved) {
      const row = _nearestSpotAt(pt.x, pt.y);
      if (row && row.spot_key) {
        setCurrentSpot(row.spot_key, true).catch((err) => addMessage("assistant", tf("manualLoadFailed", { err: err.message })));
      }
    }
  };

  canvas.addEventListener("pointerup", finishPointer);
  canvas.addEventListener("pointercancel", finishPointer);

  if (els.spotZoomOutBtn) {
    els.spotZoomOutBtn.addEventListener("click", () => {
      const { cssW, cssH } = _spotMapCanvasSize();
      zoomSpotMapAt(cssW / 2, cssH / 2, 1 / 1.35);
    });
  }
  if (els.spotZoomInBtn) {
    els.spotZoomInBtn.addEventListener("click", () => {
      const { cssW, cssH } = _spotMapCanvasSize();
      zoomSpotMapAt(cssW / 2, cssH / 2, 1.35);
    });
  }
  if (els.spotZoomResetBtn) {
    els.spotZoomResetBtn.addEventListener("click", resetSpotMapView);
  }

  if (window.ResizeObserver && els.spotMapStage) {
    spotMapView.resizeObserver = new ResizeObserver(() => resetSpotMapView());
    spotMapView.resizeObserver.observe(els.spotMapStage);
  } else {
    window.addEventListener("resize", resetSpotMapView);
  }
}

function _uniqKeepOrder(arr, topk) {
  const out = [];
  const seen = {};
  for (const x of arr || []) {
    const s = String(x || "").trim();
    if (!s || seen[s]) continue;
    seen[s] = true;
    out.push(s);
    if (out.length >= topk) break;
  }
  return out;
}

function _hasAny(text, kws) {
  const t = String(text || "").toLowerCase();
  for (const k of kws || []) {
    if (t.indexOf(String(k).toLowerCase()) >= 0) return true;
  }
  return false;
}

function renderSpotSnapshot() {
  if (!els.spotSnapshot) return;
  const rec = state.currentSpotRec || null;
  if (!rec) {
    els.spotSnapshot.innerHTML = `<div class="snapshot-title has-icon icon-spot">${escapeHtml(t("snapshotTitle"))}</div>
<div class="snapshot-empty"><span class="icon-mask icon-chat" aria-hidden="true"></span><span>${escapeHtml(t("snapshotGeneral"))}</span></div>`;
    return;
  }
  const decon = rec.decon_topk || [];
  const cells = decon
    .slice(0, 3)
    .map((x) => `${x.cell_type || "NA"} (${Number(x.proportion || 0).toFixed(2)})`)
    .join("; ");
  const react = ((rec.pathways || {}).reactome_top || [])[0] || "";
  const gobp = ((rec.pathways || {}).gobp_top || [])[0] || "";
  const compact = ((rec.pathways || {}).compact_top || [])[0] || "";
  const pw = react || gobp || compact || "NA";
  const spatial = rec.spatial_context || {};
  const spatialTxt = spatial.available
    ? tf("snapshotSpatialYes", { n: spatial.n_neighbors || 0 })
    : t("snapshotSpatialNone");

  els.spotSnapshot.innerHTML = `
<div class="snapshot-title has-icon icon-spot">${escapeHtml(t("snapshotTitle"))}</div>
<div class="snapshot-line">
  <div class="snapshot-label"><span class="icon-mask icon-cells" aria-hidden="true"></span><span>${escapeHtml(t("snapshotCells"))}</span></div>
  <div class="snapshot-value">${escapeHtml(cells || "NA")}</div>
</div>
<div class="snapshot-line">
  <div class="snapshot-label"><span class="icon-mask icon-pathway" aria-hidden="true"></span><span>${escapeHtml(t("snapshotPathway"))}</span></div>
  <div class="snapshot-value">${escapeHtml(pw)}</div>
</div>
<div class="snapshot-line">
  <div class="snapshot-label"><span class="icon-mask icon-spatial" aria-hidden="true"></span><span>${escapeHtml(t("snapshotSpatial"))}</span></div>
  <div class="snapshot-value">${escapeHtml(spatialTxt)}</div>
</div>
`;
}

function fallbackFollowupsLocal() {
  const isZh = state.lang === "zh";
  const rec = state.currentSpotRec || null;
  if (!rec) {
    return isZh
      ? ["HistAgent 如何从 H&E 图像生成基因排序？", "Hit Rate@50、mAP@50 和 PCC 分别衡量什么？"]
      : ["How does HistAgent generate a gene ranking from H&E?", "What do Hit Rate@50, mAP@50 and PCC measure?"];
  }
  const dom = ((rec.decon_topk || [])[0] || {}).cell_type || "";
  if (isZh) {
    return _uniqKeepOrder(
      [
        dom ? `哪些基因支持该 Spot 的 ${dom} 状态？` : "",
        "这个 Spot 与相邻位置有何不同？",
      ],
      2
    );
  }
  return _uniqKeepOrder(
    [
      dom ? `Which genes support the ${dom} state in this spot?` : "",
      "How does this spot differ from its neighboring locations?",
    ],
    2
  );
}

function looksLikeAssistantQuestion(q) {
  const s = String(q || "").trim();
  if (!s) return true;
  const lq = s.toLowerCase();
  const zhPrefixes = ["你想", "你希望我", "你更想", "你会优先", "如果你给我", "如果你提供", "如果给我", "要不要我", "是否要我", "你要我"];
  const enPrefixes = ["do you want", "would you like", "should i", "should we", "if you provide", "if you give me", "do you prefer", "would you prefer"];
  if (zhPrefixes.some((prefix) => s.startsWith(prefix))) return true;
  if (enPrefixes.some((prefix) => lq.startsWith(prefix))) return true;
  return false;
}

function sanitizeFollowups(arr) {
  const normalize = (s) =>
    String(s || "")
      .toLowerCase()
      .replace(/[\s\W_]+/g, "");
  const blocked = {};
  for (const q of state.askedUserQuestions || []) {
    const sig = normalize(q);
    if (sig) blocked[sig] = true;
  }
  const userSig = normalize(state.lastUserMessage || "");
  if (userSig) blocked[userSig] = true;
  const seen = {};
  const out = [];
  for (const x of arr || []) {
    const q = String(x || "").trim();
    if (!q) continue;
    const lq = q.toLowerCase();
    if (q.length > 180) continue;
    if (!(q.indexOf("?") >= 0 || q.indexOf("？") >= 0)) continue;
    if (lq.indexOf("think") >= 0) continue;
    if (looksLikeAssistantQuestion(q)) continue;
    const sig = normalize(q);
    if (!sig) continue;
    if (blocked[sig]) continue;
    if (seen[sig]) continue;
    seen[sig] = true;
    out.push(q);
    if (out.length >= 2) break;
  }
  return out;
}

function renderSuggestions() {
  if (!els.suggestions) {
    return;
  }
  els.suggestions.innerHTML = "";
  const qs = _uniqKeepOrder(
    (state.followupQuestions && state.followupQuestions.length > 0
      ? state.followupQuestions
      : t("suggestionQuestionsInit")) || fallbackFollowupsLocal(),
    2
  );
  for (const q of qs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "suggestion-chip";
    btn.textContent = q;
    btn.onclick = () => {
      els.messageInput.value = q;
      els.messageInput.focus();
    };
    els.suggestions.appendChild(btn);
  }
}

function renderConfigMeta() {
  if (!state.config || !els.configMeta) return;
  els.configMeta.classList.add("meta-grid");
  els.configMeta.classList.remove("is-empty");
  els.configMeta.innerHTML = renderMetaRows([
    { icon: "chat", label: t("cfgModel"), value: state.config.model },
    { icon: "service", label: t("cfgApi"), value: state.config.api_base_url },
    { icon: "spot", label: t("cfgSpots"), value: String(state.config.spot_count) },
    { icon: "chat", label: t("cfgSession"), value: state.sessionId },
  ]);
}

function applyLanguage() {
  safeSetLocal("histagent_lang", state.lang);
  document.documentElement.setAttribute("lang", state.lang === "zh" ? "zh-CN" : "en");

  if (els.brandSubtitle) els.brandSubtitle.textContent = t("brandSubtitle");
  if (els.labelSearchSpot) els.labelSearchSpot.textContent = t("labelSearchSpot");
  if (els.manualTitle) els.manualTitle.textContent = t("manualTitle");
  if (els.manualSpotInput) els.manualSpotInput.placeholder = t("manualInputPlaceholder");
  if (els.manualSpotBtn) els.manualSpotBtn.textContent = t("manualSpotBtn");
  if (els.clearSpotBtn) els.clearSpotBtn.textContent = t("clearSpotBtn");
  if (els.interactiveTitle) els.interactiveTitle.textContent = t("interactiveTitle");
  if (els.speciesLabel) els.speciesLabel.textContent = t("speciesLabel");
  if (els.organLabel) els.organLabel.textContent = t("organLabel");
  if (els.sliceLabel) els.sliceLabel.textContent = t("sliceLabel");
  if (els.spotFilterLabel) els.spotFilterLabel.textContent = t("spotFilterLabel");
  if (els.spotFilterInput) els.spotFilterInput.placeholder = t("spotFilterPlaceholder");
  if (els.spotPickLabel) els.spotPickLabel.textContent = t("spotPickLabel");
  if (els.currentSpotLabel) els.currentSpotLabel.textContent = t("currentSpotLabel");
  if (els.serviceConfigLabel) els.serviceConfigLabel.textContent = t("serviceConfigLabel");
  if (els.resetBtn) els.resetBtn.textContent = t("resetBtn");
  if (els.chatTitle) els.chatTitle.textContent = t("chatTitle");
  if (els.chatSubtitle) els.chatSubtitle.textContent = t("chatSubtitle");
  renderThinkingToggle();
  if (els.suggestionTitle) els.suggestionTitle.textContent = t("suggestionTitle");
  if (els.messageInput) els.messageInput.placeholder = t("messagePlaceholder");
  if (els.langZhBtn) els.langZhBtn.textContent = t("chooseLanguageZh");
  if (els.langEnBtn) els.langEnBtn.textContent = t("chooseLanguageEn");
  if (els.chatWorkspaceBtn) els.chatWorkspaceBtn.textContent = t("workspaceChat");
  if (els.retrievalWorkspaceBtn) els.retrievalWorkspaceBtn.textContent = t("workspaceRetrieval");
  if (els.retrievalEyebrow) els.retrievalEyebrow.textContent = t("retrievalEyebrow");
  if (els.retrievalTitle) els.retrievalTitle.textContent = t("retrievalTitle");
  if (els.retrievalSubtitle) els.retrievalSubtitle.textContent = t("retrievalSubtitle");
  if (els.retrievalIndexBadge) {
    const n = (state.config && state.config.rich_spot_count) || 9150;
    els.retrievalIndexBadge.textContent = tf("retrievalIndexBadge", { n: Number(n).toLocaleString() });
  }
  if (els.retrievalTextModeBtn) els.retrievalTextModeBtn.textContent = t("retrievalTextMode");
  if (els.retrievalImageModeBtn) els.retrievalImageModeBtn.textContent = t("retrievalImageMode");
  if (els.retrievalTextLabel) els.retrievalTextLabel.textContent = t("retrievalTextLabel");
  if (els.retrievalTextInput) els.retrievalTextInput.placeholder = t("retrievalTextPlaceholder");
  const retrievalExamples = document.querySelectorAll(".retrieval-example");
  if (retrievalExamples[0]) retrievalExamples[0].textContent = t("retrievalTextExample1");
  if (retrievalExamples[1]) retrievalExamples[1].textContent = t("retrievalTextExample2");
  if (els.retrievalTextSubmit) els.retrievalTextSubmit.textContent = t("retrievalTextSubmit");
  if (els.retrievalImageLabel) els.retrievalImageLabel.textContent = t("retrievalImageLabel");
  if (els.retrievalDropTitle) els.retrievalDropTitle.textContent = t("retrievalDropTitle");
  if (els.retrievalDropHint) els.retrievalDropHint.textContent = t("retrievalDropHint");
  if (els.retrievalMppLabel) els.retrievalMppLabel.textContent = t("retrievalMppLabel");
  if (els.retrievalSpeciesLabel) els.retrievalSpeciesLabel.textContent = t("retrievalSpeciesLabel");
  if (els.retrievalOrganLabel) els.retrievalOrganLabel.textContent = t("retrievalOrganLabel");
  if (els.retrievalImageNote) els.retrievalImageNote.textContent = t("retrievalImageNote");
  if (els.retrievalImageSubmit) els.retrievalImageSubmit.textContent = t("retrievalImageSubmit");

  if (els.langZhBtn) els.langZhBtn.classList.toggle("active", state.lang === "zh");
  if (els.langEnBtn) els.langEnBtn.classList.toggle("active", state.lang === "en");

  // refresh dependent text blocks
  setSpotMeta(state.currentSpotRec || null);
  renderSpotSnapshot();
  renderConfigMeta();
  renderSuggestions();
  renderSpotPickList(state.interactiveSpots);
  renderSpotMap();
  syncLandingState();
  setComposerBusy(state.loading);
  setStatus(t("statusReady"));
}

function switchWorkspace(workspace) {
  state.workspace = workspace === "retrieval" ? "retrieval" : "chat";
  const retrievalActive = state.workspace === "retrieval";
  document.querySelectorAll(".chat-workspace").forEach((node) => {
    node.classList.toggle("is-hidden", retrievalActive);
  });
  if (els.retrievalPanel) els.retrievalPanel.hidden = !retrievalActive;
  if (els.chatWorkspaceBtn) els.chatWorkspaceBtn.classList.toggle("active", !retrievalActive);
  if (els.retrievalWorkspaceBtn) els.retrievalWorkspaceBtn.classList.toggle("active", retrievalActive);
}

function switchRetrievalMode(mode) {
  const changed = state.retrievalMode !== (mode === "image" ? "image" : "text");
  state.retrievalMode = mode === "image" ? "image" : "text";
  const imageMode = state.retrievalMode === "image";
  if (els.retrievalTextModeBtn) els.retrievalTextModeBtn.classList.toggle("active", !imageMode);
  if (els.retrievalImageModeBtn) els.retrievalImageModeBtn.classList.toggle("active", imageMode);
  if (els.retrievalTextForm) els.retrievalTextForm.hidden = imageMode;
  if (els.retrievalImageForm) els.retrievalImageForm.hidden = !imageMode;
  if (els.retrievalStatus) {
    els.retrievalStatus.textContent = "";
    els.retrievalStatus.classList.remove("is-error");
  }
  if (changed) {
    if (els.retrievalQuerySummary) els.retrievalQuerySummary.innerHTML = "";
    if (els.retrievalResults) els.retrievalResults.innerHTML = "";
  }
}

function setRetrievalBusy(busy, message = "", isError = false) {
  state.retrievalLoading = !!busy;
  if (els.retrievalTextSubmit) els.retrievalTextSubmit.disabled = !!busy;
  if (els.retrievalImageSubmit) els.retrievalImageSubmit.disabled = !!busy;
  if (els.retrievalTextInput) els.retrievalTextInput.disabled = !!busy;
  if (els.retrievalImageInput) els.retrievalImageInput.disabled = !!busy;
  if (els.retrievalMppInput) els.retrievalMppInput.disabled = !!busy;
  if (els.retrievalSpeciesSelect) els.retrievalSpeciesSelect.disabled = !!busy;
  if (els.retrievalOrganSelect) els.retrievalOrganSelect.disabled = !!busy;
  if (els.retrievalStatus) {
    els.retrievalStatus.textContent = message || "";
    els.retrievalStatus.classList.toggle("is-error", !!isError);
  }
}

function renderRetrievalResults(data) {
  const items = (data && data.items) || [];
  const candidateSpots = Number((data && data.candidate_spots) || 0);
  if (els.retrievalStatus) {
    els.retrievalStatus.classList.remove("is-error");
    els.retrievalStatus.textContent = items.length
      ? tf("retrievalResultsFound", {
          n: items.length,
          candidates: candidateSpots.toLocaleString(),
        })
      : t("retrievalNoResults");
  }

  if (els.retrievalQuerySummary) {
    if (data && data.mode === "he_image") {
      const genes = (data.query_genes || []).slice(0, 12).map((x) => `<i>${escapeHtml(x)}</i>`).join(", ");
      els.retrievalQuerySummary.innerHTML = genes
        ? `<strong>${escapeHtml(t("retrievalPredictedGenes"))}:</strong> ${genes}`
        : "";
    } else {
      const plan = (data && data.query_interpretation) || {};
      const fields = ["genes", "cell_types", "pathways", "organs", "species"];
      const terms = [];
      fields.forEach((field) => {
        (plan[field] || []).slice(0, 6).forEach((term) => terms.push(String(term)));
      });
      els.retrievalQuerySummary.innerHTML = terms.length
        ? `<strong>${escapeHtml(t("retrievalInterpretedAs"))}:</strong> ${terms
            .slice(0, 16)
            .map((x) => escapeHtml(x))
            .join(" · ")}`
        : "";
    }
  }

  if (!els.retrievalResults) return;
  els.retrievalResults.innerHTML = items
    .map((row, index) => {
      const genes = (row.top_genes || []).slice(0, 8).map((x) => `<i>${escapeHtml(x)}</i>`).join(", ") || "—";
      const pathways = (row.pathways || []).slice(0, 3).map((x) => escapeHtml(x)).join(" · ") || "—";
      const matched = (row.matched_evidence || []).slice(0, 6).map((x) => escapeHtml(x)).join(" · ") || "—";
      const score = Math.round(Number(row.similarity || 0) * 100);
      return `
<article class="retrieval-result">
  <div>
    <div class="retrieval-result-rank">${escapeHtml(tf("retrievalRank", { rank: index + 1 }))}</div>
    <h4>${escapeHtml(row.dominant_cell_type || "Unresolved cell state")}</h4>
    <div class="retrieval-result-meta">${escapeHtml(toTitleToken(row.species) || "NA")} · ${escapeHtml(
        toTitleToken(row.organ) || "NA"
      )} · ${escapeHtml(formatSampleId(row.slice_id) || "NA")}</div>
    <div class="retrieval-evidence-line"><strong>${escapeHtml(t("retrievalGenes"))}:</strong> ${genes}</div>
    <div class="retrieval-evidence-line"><strong>${escapeHtml(t("retrievalPathways"))}:</strong> ${pathways}</div>
    <div class="retrieval-evidence-line"><strong>${escapeHtml(t("retrievalMatched"))}:</strong> ${matched}</div>
  </div>
  <div class="retrieval-result-actions">
    <div class="retrieval-score">${score}%<span>${escapeHtml(t("retrievalSimilarity"))}</span></div>
    <button class="retrieval-open-btn" type="button" data-spot-key="${escapeHtml(row.spot_key || "")}">${escapeHtml(
        t("retrievalOpenChat")
      )}</button>
  </div>
</article>`;
    })
    .join("");

  els.retrievalResults.querySelectorAll(".retrieval-open-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const key = String(button.dataset.spotKey || "").trim();
      if (!key) return;
      button.disabled = true;
      try {
        await setCurrentSpot(key, true);
        switchWorkspace("chat");
      } catch (err) {
        setRetrievalBusy(false, tf("retrievalFailed", { err: err.message }), true);
        button.disabled = false;
      }
    });
  });
}

async function submitTextRetrieval(e) {
  e.preventDefault();
  if (state.retrievalLoading) return;
  const query = String((els.retrievalTextInput && els.retrievalTextInput.value) || "").trim();
  if (!query) {
    setRetrievalBusy(false, t("retrievalNeedText"), true);
    return;
  }
  if (els.retrievalResults) els.retrievalResults.innerHTML = "";
  if (els.retrievalQuerySummary) els.retrievalQuerySummary.innerHTML = "";
  setRetrievalBusy(true, t("retrievalTextBusy"));
  try {
    const data = await api("/api/retrieval/text", {
      method: "POST",
      body: JSON.stringify({ query, limit: 6 }),
    });
    renderRetrievalResults(data);
  } catch (err) {
    setRetrievalBusy(false, tf("retrievalFailed", { err: err.message }), true);
    return;
  }
  setRetrievalBusy(false, els.retrievalStatus ? els.retrievalStatus.textContent : "");
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Cannot read the selected image"));
    reader.readAsDataURL(file);
  });
}

function loadDataUrlImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Cannot decode the selected image"));
    image.src = dataUrl;
  });
}

function centeredFovDataUrl(image, sidePixels) {
  const outputSize = 224;
  const canvas = document.createElement("canvas");
  canvas.width = outputSize;
  canvas.height = outputSize;
  const context = canvas.getContext("2d");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, outputSize, outputSize);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";

  const centerX = image.naturalWidth / 2;
  const centerY = image.naturalHeight / 2;
  const sourceLeft = centerX - sidePixels / 2;
  const sourceTop = centerY - sidePixels / 2;
  const sx = Math.max(0, sourceLeft);
  const sy = Math.max(0, sourceTop);
  const sourceRight = Math.min(image.naturalWidth, sourceLeft + sidePixels);
  const sourceBottom = Math.min(image.naturalHeight, sourceTop + sidePixels);
  const sw = Math.max(0, sourceRight - sx);
  const sh = Math.max(0, sourceBottom - sy);
  const scale = outputSize / sidePixels;
  if (sw && sh) {
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

async function submitImageRetrieval(e) {
  e.preventDefault();
  if (state.retrievalLoading) return;
  const file = state.retrievalFile || (els.retrievalImageInput && els.retrievalImageInput.files[0]);
  if (!file) {
    setRetrievalBusy(false, t("retrievalNeedImage"), true);
    return;
  }
  if (file.size > 16 * 1024 * 1024) {
    setRetrievalBusy(false, tf("retrievalFailed", { err: "image is larger than 16 MB" }), true);
    return;
  }
  const micronsPerPixel = Number((els.retrievalMppInput && els.retrievalMppInput.value) || 0);
  if (!Number.isFinite(micronsPerPixel) || micronsPerPixel <= 0) {
    setRetrievalBusy(false, t("retrievalNeedScale"), true);
    return;
  }
  const localPixels = 55 / micronsPerPixel;
  if (localPixels < 4) {
    setRetrievalBusy(false, tf("retrievalFailed", { err: t("retrievalNeedScale") }), true);
    return;
  }
  if (els.retrievalResults) els.retrievalResults.innerHTML = "";
  if (els.retrievalQuerySummary) els.retrievalQuerySummary.innerHTML = "";
  setRetrievalBusy(true, t("retrievalImageBusy"));
  try {
    const imageBase64 = await fileToDataUrl(file);
    const sourceImage = await loadDataUrlImage(imageBase64);
    const localImageBase64 = centeredFovDataUrl(sourceImage, localPixels);
    const contextImageBase64 = centeredFovDataUrl(sourceImage, localPixels * 4);
    const data = await api("/api/retrieval/image", {
      method: "POST",
      body: JSON.stringify({
        local_image_base64: localImageBase64,
        context_image_base64: contextImageBase64,
        filename: file.name,
        spot_diameter_um: 55,
        context_diameter_um: 220,
        microns_per_pixel: micronsPerPixel,
        species: (els.retrievalSpeciesSelect && els.retrievalSpeciesSelect.value) || null,
        organ: (els.retrievalOrganSelect && els.retrievalOrganSelect.value) || null,
        limit: 6,
      }),
    });
    renderRetrievalResults(data);
  } catch (err) {
    setRetrievalBusy(false, tf("retrievalFailed", { err: err.message }), true);
    return;
  }
  setRetrievalBusy(false, els.retrievalStatus ? els.retrievalStatus.textContent : "");
}

async function loadRetrievalOrgans() {
  if (!els.retrievalOrganSelect) return;
  const species = (els.retrievalSpeciesSelect && els.retrievalSpeciesSelect.value) || "";
  const data = await api(`/api/organs?species=${encodeURIComponent(species)}`);
  setOptions(els.retrievalOrganSelect, data.items || [], t("retrievalAnyOrgan"));
}

async function setCurrentSpot(spotKey, announce = true) {
  const key = (spotKey || "").trim();
  if (!key) {
    throw new Error("spot_key is empty");
  }
  const detail = await api(`/api/spot?spot_key=${encodeURIComponent(key)}`);
  state.selectedSpotKey = detail.spot.spot_key;
  state.currentSpotRec = detail.spot;
  state.lastUserMessage = "";
  state.lastAssistantMessage = "";
  state.followupQuestions = [];
  state.askedUserQuestions = [];
  syncModeClass();
  setSpotMeta(detail.spot);
  renderSpotSnapshot();
  renderSpotMap();
  els.manualSpotInput.value = detail.spot.spot_key;
  if (announce) {
    els.messages.innerHTML = "";
    addMessage(
      "assistant",
      tf("switchedSpot", {
        slice: detail.spot.slice_id,
        barcode: detail.spot.barcode,
      })
    );
  }
  renderSuggestions();
}

function clearCurrentSpot(announce = true) {
  state.selectedSpotKey = null;
  state.currentSpotRec = null;
  state.lastUserMessage = "";
  state.lastAssistantMessage = "";
  state.followupQuestions = [];
  state.askedUserQuestions = [];
  syncModeClass();
  els.manualSpotInput.value = "";
  setSpotMeta(null);
  renderSpotSnapshot();
  renderSpotPickList(state.interactiveSpots);
  renderSpotMap();
  if (announce) {
    els.messages.innerHTML = "";
    addMessage("assistant", t("generalMode"));
  }
  renderSuggestions();
  syncLandingState();
}

async function loadSpecies() {
  const data = await api("/api/species");
  const items = data.items || [];
  setOptions(els.speciesSelect, items, t("speciesPlaceholder"));
  setOptions(els.retrievalSpeciesSelect, items, t("retrievalAnySpecies"));
  if (items.length > 0) {
    els.speciesSelect.value = items[0];
  }
  if (els.retrievalSpeciesSelect) {
    els.retrievalSpeciesSelect.value = "";
  }
}

async function loadOrgansForSpecies() {
  const sp = (els.speciesSelect.value || "").trim();
  const data = await api(`/api/organs?species=${encodeURIComponent(sp)}`);
  const items = data.items || [];
  setOptions(els.organSelect, items, t("organPlaceholder"));
  if (items.length > 0) {
    els.organSelect.value = items[0];
  }
}

async function loadSlicesBySelectors() {
  const sp = (els.speciesSelect.value || "").trim();
  const og = (els.organSelect.value || "").trim();
  const data = await api(`/api/slices?species=${encodeURIComponent(sp)}&organ=${encodeURIComponent(og)}`);
  state.slices = data.items || [];
  if (els.sliceSelect) {
    els.sliceSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = t("slicePlaceholder");
    els.sliceSelect.appendChild(placeholder);
    for (const row of state.slices) {
      const opt = document.createElement("option");
      opt.value = String(row.slice_id || "");
      opt.textContent = `${row.slice_id || "slice"} (${row.n_spots || 0})${row.has_image ? "" : " · map"}`;
      els.sliceSelect.appendChild(opt);
    }
    if (state.slices.length > 0) {
      els.sliceSelect.value = state.slices[0].slice_id;
      state.currentSliceId = state.slices[0].slice_id;
    }
  }
}

async function loadSliceMapBySelectors() {
  const sp = (els.speciesSelect && els.speciesSelect.value ? els.speciesSelect.value : "").trim();
  const og = (els.organSelect && els.organSelect.value ? els.organSelect.value : "").trim();
  const sid = (els.sliceSelect && els.sliceSelect.value ? els.sliceSelect.value : state.currentSliceId || "").trim();
  const data = await api(
    `/api/slice/map?species=${encodeURIComponent(sp)}&organ=${encodeURIComponent(og)}&slice_id=${encodeURIComponent(
      sid
    )}&limit=5000`
  );
  state.currentSliceMap = data;
  state.currentSliceId = data.slice_id || sid;
  state.interactiveSpots = data.spots || [];
  renderSpotMap();
}

async function loadSpotsBySelectors() {
  await loadSlicesBySelectors();
  await loadSliceMapBySelectors();
}

async function chooseSpotManually() {
  const raw = (els.manualSpotInput.value || "").trim();
  if (!raw) {
    addMessage("assistant", t("emptySpotKey"));
    return;
  }
  try {
    await setCurrentSpot(raw, true);
    renderSpotPickList(state.interactiveSpots);
  } catch (err) {
    addMessage("assistant", tf("manualLoadFailed", { err: err.message }));
  }
}

async function loadConfig() {
  state.config = await api("/api/config");
  renderConfigMeta();
  if (els.retrievalIndexBadge) {
    const n = state.config.rich_spot_count || 9150;
    els.retrievalIndexBadge.textContent = tf("retrievalIndexBadge", { n: Number(n).toLocaleString() });
  }
}

async function sendMessage(e) {
  e.preventDefault();
  const text = ((els.messageInput && els.messageInput.value) || "").trim();
  if (!text || state.loading) return;
  state.lastUserMessage = text;
  state.askedUserQuestions.push(text);
  setComposerBusy(true);
  setStatus(t("statusGenerating"));
  addMessage("user", text);
  if (els.messageInput) els.messageInput.value = "";
  const placeholder = addMessage("assistant", "");
  setAssistantBubbleStreaming(placeholder, "", true);

  let streamedAnswer = "";
  let finalData = null;
  try {
    const payload = {
      session_id: state.sessionId,
      message: text,
      lang: state.lang,
      enable_thinking: !!state.enableThinking,
    };
    if (state.selectedSpotKey) {
      payload.spot_key = state.selectedSpotKey;
    }
    await streamChat(payload, {
      onStart: (evt) => {
        if (evt && evt.session_id) {
          state.sessionId = evt.session_id;
          safeSetLocal("histagent_session_id", state.sessionId);
        }
      },
      onDelta: (delta) => {
        streamedAnswer += String(delta || "");
        setAssistantBubbleStreaming(placeholder, streamedAnswer, true);
        setStatus(t("statusStreaming"));
      },
      onReplace: (answer) => {
        streamedAnswer = String(answer || "");
        setAssistantBubbleStreaming(placeholder, streamedAnswer, true);
        setStatus(t("statusStreaming"));
      },
      onFinal: (evt) => {
        finalData = evt || {};
      },
    });
    const ans = String((finalData && finalData.answer) || streamedAnswer || "");
    state.lastAssistantMessage = ans;
    if (finalData && finalData.session_id) {
      state.sessionId = finalData.session_id;
      safeSetLocal("histagent_session_id", state.sessionId);
    }
    state.followupQuestions = sanitizeFollowups((finalData && finalData.followup_questions) || []);
    if (!state.followupQuestions || state.followupQuestions.length === 0) {
      state.followupQuestions = fallbackFollowupsLocal();
    }
    setAssistantBubbleFinal(placeholder, ans);
    renderSuggestions();
  } catch (err) {
    const partial = String(streamedAnswer || "").trim();
    const errMsg = partial
      ? `${partial}\n\n${tf("reqFailed", { err: err.message })}`
      : tf("reqFailed", { err: err.message });
    state.lastAssistantMessage = errMsg;
    state.followupQuestions = fallbackFollowupsLocal();
    setAssistantBubbleFinal(placeholder, errMsg);
    renderSuggestions();
  } finally {
    setComposerBusy(false);
    setStatus(t("statusReady"));
  }
}

async function resetSession() {
  await api("/api/session/reset", {
    method: "POST",
    body: JSON.stringify({
      session_id: state.sessionId,
      spot_key: state.selectedSpotKey || null,
    }),
  });
  els.messages.innerHTML = "";
  addMessage("assistant", t("resetDone"));
  syncLandingState();
}

function bindEvents() {
  if (!els.chatForm || !els.messageInput) {
    // if core elements are missing, do nothing but avoid crashing
    return;
  }
  bindSpotMapInteractions();
  if (els.chatWorkspaceBtn) {
    els.chatWorkspaceBtn.addEventListener("click", () => switchWorkspace("chat"));
  }
  if (els.retrievalWorkspaceBtn) {
    els.retrievalWorkspaceBtn.addEventListener("click", () => switchWorkspace("retrieval"));
  }
  if (els.retrievalTextModeBtn) {
    els.retrievalTextModeBtn.addEventListener("click", () => switchRetrievalMode("text"));
  }
  if (els.retrievalImageModeBtn) {
    els.retrievalImageModeBtn.addEventListener("click", () => switchRetrievalMode("image"));
  }
  if (els.retrievalTextForm) {
    els.retrievalTextForm.addEventListener("submit", submitTextRetrieval);
  }
  if (els.retrievalImageForm) {
    els.retrievalImageForm.addEventListener("submit", submitImageRetrieval);
  }
  document.querySelectorAll(".retrieval-example").forEach((button) => {
    button.addEventListener("click", () => {
      if (els.retrievalTextInput) {
        els.retrievalTextInput.value = button.textContent || "";
        els.retrievalTextInput.focus();
      }
    });
  });
  if (els.retrievalImageInput) {
    els.retrievalImageInput.addEventListener("change", async () => {
      const file = els.retrievalImageInput.files && els.retrievalImageInput.files[0];
      state.retrievalFile = file || null;
      if (!file || !els.retrievalImagePreview) {
        if (els.retrievalImagePreview) els.retrievalImagePreview.hidden = true;
        return;
      }
      try {
        els.retrievalImagePreview.src = await fileToDataUrl(file);
        els.retrievalImagePreview.hidden = false;
        if (els.retrievalDropTitle) els.retrievalDropTitle.textContent = file.name;
      } catch (err) {
        setRetrievalBusy(false, tf("retrievalFailed", { err: err.message }), true);
      }
    });
  }
  if (els.retrievalSpeciesSelect) {
    els.retrievalSpeciesSelect.addEventListener("change", loadRetrievalOrgans);
  }
  let tmr = null;
  if (els.speciesSelect) {
    els.speciesSelect.addEventListener("change", async () => {
      await loadOrgansForSpecies();
      await loadSpotsBySelectors();
    });
  }
  if (els.organSelect) {
    els.organSelect.addEventListener("change", loadSpotsBySelectors);
  }
  if (els.sliceSelect) {
    els.sliceSelect.addEventListener("change", loadSliceMapBySelectors);
  }
  if (els.spotFilterInput) {
    els.spotFilterInput.addEventListener("input", () => {
      clearTimeout(tmr);
      tmr = setTimeout(() => loadSpotsBySelectors(), 220);
    });
  }

  if (els.manualSpotBtn) els.manualSpotBtn.addEventListener("click", chooseSpotManually);
  if (els.clearSpotBtn) els.clearSpotBtn.addEventListener("click", () => clearCurrentSpot(true));
  if (els.manualSpotInput) {
    els.manualSpotInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        chooseSpotManually();
      }
    });
  }

  els.chatForm.addEventListener("submit", sendMessage);
  els.messageInput.addEventListener("keydown", (e) => {
    const isEnter = e.key === "Enter" || e.code === "NumpadEnter";
    if (!isEnter) return;
    // Shift+Enter => newline
    if (e.shiftKey) return;
    // Enter during IME composition should not submit.
    if (e.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    e.stopPropagation();
    sendMessage({
      preventDefault: () => {},
    });
  });
  if (els.resetBtn) els.resetBtn.addEventListener("click", resetSession);

  if (els.langZhBtn) {
    els.langZhBtn.addEventListener("click", () => {
      state.lang = "zh";
      applyLanguage();
    });
  }
  if (els.langEnBtn) {
    els.langEnBtn.addEventListener("click", () => {
      state.lang = "en";
      applyLanguage();
    });
  }
  if (els.thinkingToggleBtn) {
    els.thinkingToggleBtn.addEventListener("click", () => {
      state.enableThinking = !state.enableThinking;
      safeSetLocal("histagent_enable_thinking", state.enableThinking ? "true" : "false");
      renderThinkingToggle();
    });
  }
}

async function init() {
  // Bind UI first, so buttons are always responsive even if async loading fails.
  bindEvents();
  setStatus(state.lang === "zh" ? "加载中" : "Loading");
  applyLanguage();

  try {
    await loadConfig();
    if (safeGetLocal("histagent_enable_thinking", "") === "") {
      state.enableThinking = !!(state.config && state.config.enable_thinking_default);
      safeSetLocal("histagent_enable_thinking", state.enableThinking ? "true" : "false");
    }
    renderThinkingToggle();
    setStatus(state.lang === "zh" ? "正在加载组织切片" : "Loading tissue maps");
  } catch (err) {
    addMessage("assistant", tf("reqFailed", { err: `config load failed: ${err.message}` }));
    setStatus(state.lang === "zh" ? "配置加载失败" : "Config failed");
  }
  try {
    await loadSpecies();
    await loadOrgansForSpecies();
    await loadSpotsBySelectors();
    setStatus(state.lang === "zh" ? "就绪" : "Ready");
  } catch (err) {
    addMessage("assistant", tf("reqFailed", { err: `spot list load failed: ${err.message}` }));
    setStatus(state.lang === "zh" ? "Spot 列表加载失败" : "Spot list failed");
  }

  const requestedSpot = new URLSearchParams(window.location.search).get("spot_key");
  if (requestedSpot) {
    try {
      await setCurrentSpot(requestedSpot, true);
    } catch (err) {
      clearCurrentSpot(false);
      addMessage("assistant", tf("manualLoadFailed", { err: err.message }));
    }
  } else {
    clearCurrentSpot(false);
  }
  syncModeClass();
  syncLandingState();
  switchWorkspace("chat");
}

init().catch((err) => {
  setStatus(state.lang === "zh" ? "页面异常" : "UI error");
  addMessage("assistant", tf("initFailed", { err: err.message }));
});

window.addEventListener("error", function (e) {
  setStatus(state.lang === "zh" ? "脚本错误" : "Script error");
  try {
    addMessage("assistant", `UI runtime error: ${e.message}`);
  } catch (_) {}
});
