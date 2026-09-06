import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function element() {
  const listeners = new Map();
  const children = new Map();
  const classes = new Set();
  let markup = "";
  return {
    value: "", textContent: "", hidden: false, disabled: false,
    get innerHTML() { return markup; },
    set innerHTML(value) { markup = value; this.children = []; },
    dataset: {}, style: { setProperty() {} }, options: [], children: [],
    clientWidth: 600, clientHeight: 600, naturalWidth: 1000, naturalHeight: 1000,
    classList: { add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)) },
    addEventListener(name, listener) {
      listeners.set(name, [...(listeners.get(name) || []), listener]);
    },
    emit(name, event = {}) { return (listeners.get(name) || []).map((listener) => listener(event)); },
    querySelector(selector) {
      if (!children.has(selector)) children.set(selector, element());
      return children.get(selector);
    },
    setAttribute(name, value) { this[name] = value; },
    getAttribute(name) { return this[name] ?? null; },
    removeAttribute(name) { delete this[name]; },
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; markup = ""; },
    remove() { this.removed = true; },
    getContext() { return new Proxy({}, { get: () => () => {} }); },
    toBlob(callback) { queueMicrotask(() => callback(new Blob(["image"]))); },
    toDataURL() { return "data:image/png;base64,fixture"; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 600, height: 600 }; }
  };
}

function workbench(kind, services = {}) {
  const nodes = new Map();
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  const context = vm.createContext({
    document: { querySelector: node, querySelectorAll: () => [], createElement: element },
    window: { addEventListener() {}, devicePixelRatio: 1, setTimeout,
      sessionStorage: { getItem: () => null }, location: { search: "" } },
    console: { error() {} }, Blob, File, URL, URLSearchParams,
    fetch: () => new Promise(() => {}),
    ...services
  });
  let source = readFileSync(new URL(`../docs/assets/${kind}-workbench.js`, import.meta.url), "utf8")
    .replace(/^import\s+\{[\s\S]*?\}\s+from\s+"[^"]+";\s*/, "");
  // The tests supply their own image, rather than running the page's async loader.
  if (kind === "histagent") source = source.slice(0, source.lastIndexOf("\nloadAtlasImageQuery()\n"));
  vm.runInContext(source, context);
  const run = (code) => vm.runInContext(code, context);
  if (kind === "histagent") {
    node("#histagent-mpp").value = "0.50";
    node("#histagent-species").value = "human";
    node("#histagent-organ").value = "kidney";
    run("selectedSpot = { id: 'S001', x: 100, y: 200, xNorm: 0.1, yNorm: 0.2 }; sourceLabel = 'original.png'");
  }
  return { node, run, context };
}

const genes = [[[1, "EPCAM"], [2, "KRT8"]], "EPCAM KRT8"];

test("inference renders evidence only for its original, unchanged selection", async () => {
  let request;
  const app = workbench("histagent", {
    generateHistAgentReadout: async (value) => { request = value; return genes; }
  });
  await app.run("generateEvidence()");
  assert.equal(request.localName, "S001_local.png");
  assert.equal(request.contextName, "S001_context.png");
  assert.equal(request.species, "human");
  assert.equal(request.organ, "kidney");
  assert.equal(app.run("currentEvidence.spot.id"), "S001");
  assert.equal(app.run("currentEvidence.provenance.image_name"), "original.png");
  assert.equal(app.node("#histagent-selection-badge").textContent, "Evidence ready");
  assert.equal(app.node("#histagent-generate").disabled, false);
});

test("spot, metadata, scale, and image changes cannot receive a pending inference result", async (t) => {
  const changes = {
    spot: (app) => app.run("selectSpot({ id: 'S002', x: 300, y: 400, xNorm: 0.3, yNorm: 0.4 })"),
    species: (app) => { app.node("#histagent-species").value = "mouse"; app.node("#histagent-species").emit("change"); },
    organ: (app) => { app.node("#histagent-organ").value = "brain"; app.node("#histagent-organ").emit("change"); },
    scale: (app) => { app.node("#histagent-mpp").value = "0.75"; app.node("#histagent-mpp").emit("input"); },
    image: (app) => app.run("setSourceImage(new File(['image'], 'next.png', { type: 'image/png' }))")
  };
  for (const [name, change] of Object.entries(changes)) {
    await t.test(name, async () => {
      const reply = deferred();
      const started = deferred();
      const app = workbench("histagent", {
        generateHistAgentReadout: (request) => { started.resolve(request); return reply.promise; }
      });
      const pending = app.run("generateEvidence()");
      const request = await started.promise;
      await change(app);
      const newStatus = app.node("#histagent-run-status").textContent;
      reply.resolve(genes);
      await pending;
      assert.equal(request.localName, "S001_local.png");
      assert.equal(request.species, "human");
      assert.equal(request.organ, "kidney");
      assert.equal(app.run("currentEvidence"), null);
      assert.equal(app.node("#histagent-run-status").textContent, newStatus);
      assert.equal(app.node("#histagent-generate").disabled, name === "image");
    });
  }
});

test("changing away and back still discards the old request, including its errors", async () => {
  const reply = deferred();
  const started = deferred();
  const app = workbench("histagent", {
    generateHistAgentReadout: () => { started.resolve(); return reply.promise; }
  });
  const pending = app.run("generateEvidence()");
  await started.promise;
  for (const species of ["mouse", "human"]) {
    app.node("#histagent-species").value = species;
    app.node("#histagent-species").emit("change");
  }
  const status = app.node("#histagent-run-status").textContent;
  reply.reject(new Error("GPU quota exceeded for the earlier request"));
  await pending;
  assert.equal(app.node("#histagent-run-status").textContent, status);
  assert.equal(app.run("currentEvidence"), null);
});

test("selection changes during image preparation do not submit a stale GPU request", async () => {
  let calls = 0;
  const app = workbench("histagent", {
    generateHistAgentReadout: async () => { calls += 1; return genes; }
  });
  const pending = app.run("generateEvidence()");
  app.node("#histagent-species").value = "mouse";
  app.node("#histagent-species").emit("change");
  await pending;
  assert.equal(calls, 0);
});

test("a failed Atlas search preserves the previous result and its original query filters", async () => {
  let fail = false;
  const errorMessage = "Hugging Face daily GPU quota reached. Wait for the platform reset.";
  const app = workbench("atlas", {
    callHistAgentService: async () => {
      if (fail) throw new Error(errorMessage);
      return [
        [[1, 0.9, "human", "kidney", "Epithelial cell", "slide-original", "EPCAM"]],
        { spot: { slice_id: "slide-original" } }, "Retrieved evidence", {
          data: [], layout: { images: [{ source: "original-tissue.jpg", sizex: 1000, sizey: 1000 }] }
        }
      ];
    }
  });
  app.node("#atlas-cell-filter").value = "Original cell filter";
  await app.run("runRetrieval('Original kidney query')");
  const cards = app.node("#atlas-evidence-cards").innerHTML;
  const chips = app.node("#query-evidence-chips").innerHTML;
  const map = app.node("#atlas-live-plot").children[0];
  const evidence = app.run("topEvidence");
  fail = true;
  app.node("#atlas-cell-filter").value = "Changed cell filter";
  await app.run("runRetrieval('Different brain query')");
  assert.equal(app.node("#atlas-status-badge").textContent, "Previous result");
  assert.equal(app.node("#atlas-evidence-cards").innerHTML, cards);
  assert.equal(app.node("#query-evidence-chips").innerHTML, chips);
  assert.equal(app.node("#atlas-live-plot").children[0], map);
  assert.equal(app.node("#atlas-live-plot").hidden, false);
  assert.equal(app.run("topEvidence"), evidence);
  assert.match(app.node("#atlas-result-summary").textContent, /slide-original/);
  assert.ok(app.node("#atlas-result-summary").textContent.startsWith(errorMessage));
  assert.doesNotMatch(app.node("#atlas-result-summary").textContent, /manuscript example/);
});

test("a failed first Atlas search accurately leaves the manuscript example visible", async () => {
  const message = "Hugging Face quota exceeded.";
  const app = workbench("atlas", { callHistAgentService: async () => { throw new Error(message); } });
  await app.run("runRetrieval('Kidney query')");
  assert.equal(app.node("#atlas-status-badge").textContent, "Example view");
  assert.equal(app.node("#atlas-result-summary").textContent, `${message} The manuscript example remains visible.`);
});

test("HistAgent ignores an old chat callback while the new evidence's chat is pending", async (t) => {
  for (const outcome of ["success", "error"]) {
    await t.test(outcome, async () => {
      const replies = [deferred(), deferred()];
      const requests = [];
      const app = workbench("histagent", {
        callHistAgentService: (_service, _api, data) => {
          requests.push(data);
          return replies[requests.length - 1].promise;
        }
      });
      app.run("renderEvidence(buildEvidence(['EPCAM']))");
      const previous = app.run("submitChat('Original question')");
      await app.run("submitChat('Duplicate submission')");
      assert.equal(requests.length, 1);
      app.run("selectSpot({ id: 'S002', x: 300, y: 400, xNorm: 0.3, yNorm: 0.4 }); renderEvidence(buildEvidence(['GFAP']))");
      const current = app.run("submitChat('New question')");
      const newConversation = app.node("#histagent-chat-log").children.slice();
      assert.equal(requests[0][2].spot.id, "S001");
      assert.equal(requests[1][2].spot.id, "S002");
      if (outcome === "success") replies[0].resolve(["", [{ role: "assistant", content: "Obsolete answer" }]]);
      else replies[0].reject(new Error("Obsolete GPU quota error"));
      await previous;
      assert.equal(app.run("chatHistory.length"), 0);
      assert.deepEqual(app.node("#histagent-chat-log").children, newConversation);
      assert.equal(app.node("#histagent-chat-form").querySelector("button").disabled, true);
      replies[1].resolve(["", [{ role: "assistant", content: "Current answer" }]]);
      await current;
      assert.equal(app.run("chatHistory.at(-1).content"), "Current answer");
      assert.equal(app.node("#histagent-chat-form").querySelector("button").disabled, false);
    });
  }
});

test("obsolete HistAgent chat completion cannot enable chat before evidence is regenerated", async () => {
  const reply = deferred();
  const app = workbench("histagent", { callHistAgentService: () => reply.promise });
  app.run("renderEvidence(buildEvidence(['EPCAM']))");
  const pending = app.run("submitChat('Original question')");
  app.run("clearEvidenceForSelection()");
  reply.resolve(["", [{ role: "assistant", content: "Obsolete answer" }]]);
  await pending;
  assert.equal(app.run("chatHistory.length"), 0);
  assert.equal(app.node("#histagent-chat-form").querySelector("button").disabled, true);
});

test("HistAgent keeps the current HF error and binds its retry to the original evidence", async () => {
  const message = "Hugging Face daily GPU quota reached. Wait for the platform reset.";
  let requests = 0;
  const app = workbench("histagent", {
    callHistAgentService: async () => { requests += 1; throw Object.assign(new Error(message), { code: "gpu_quota_exhausted" }); }
  });
  app.run("renderEvidence(buildEvidence(['EPCAM']))");
  await app.run("submitChat('Original question')");
  const error = app.node("#histagent-chat-log").children.at(-1);
  const retry = error.children.at(-1);
  assert.ok(error.querySelector("p").textContent.startsWith(message));
  assert.equal(app.node("#histagent-chat-form").querySelector("button").disabled, false);
  app.run("clearEvidenceForSelection(); renderEvidence(buildEvidence(['GFAP']))");
  retry.emit("click");
  assert.equal(requests, 1);
});

test("Atlas discards an old evidence card's chat callbacks after a new retrieval", async (t) => {
  for (const outcome of ["success", "error"]) {
    await t.test(outcome, async () => {
      const replies = [deferred(), deferred()];
      const requests = [];
      const app = workbench("atlas", {
        callHistAgentService: (_service, api, data) => {
          if (api === "retrieve_atlas") return Promise.resolve([
            [[1, 0.9, "human", "kidney", "Epithelial cell", "slide-new", "EPCAM"]],
            { spot: { slice_id: "slide-new" } }, "New retrieved evidence", null
          ]);
          requests.push(data);
          return replies[requests.length - 1].promise;
        }
      });
      const submit = (message) => {
        app.node("#atlas-chat-input").value = message;
        return app.node("#atlas-chat-form").emit("submit", { preventDefault() {} })[0];
      };
      const previous = submit("Original question");
      await submit("Duplicate submission");
      assert.equal(requests.length, 1);
      await app.run("runRetrieval('New query')");
      const current = submit("New question");
      const newConversation = app.node("#atlas-chat-log").children.slice();
      assert.equal(requests[0][2].spot.slice_id, "GSE203612_GSM6177603");
      assert.equal(requests[1][2].spot.slice_id, "slide-new");
      if (outcome === "success") replies[0].resolve(["", [{ role: "assistant", content: "Obsolete answer" }]]);
      else replies[0].reject(new Error("Obsolete GPU quota error"));
      await previous;
      assert.equal(app.run("chatHistory.length"), 0);
      assert.deepEqual(app.node("#atlas-chat-log").children, newConversation);
      assert.equal(app.node("#atlas-chat-form").querySelector("button").disabled, true);
      replies[1].resolve(["", [{ role: "assistant", content: "Current answer" }]]);
      await current;
      assert.equal(app.run("chatHistory.at(-1).content"), "Current answer");
      assert.equal(app.node("#atlas-chat-form").querySelector("button").disabled, false);
    });
  }
});

test("an empty Atlas retrieval clears earlier results and remains distinct after a later error", async () => {
  let mode = "live";
  let chatRequests = 0;
  const noMatches = "No evidence-bank spots match the selected species, organ, or slide.";
  const app = workbench("atlas", {
    callHistAgentService: async (_service, api) => {
      if (api !== "retrieve_atlas") { chatRequests += 1; return []; }
      if (mode === "error") throw new Error("Hugging Face quota reached.");
      if (mode === "empty") return [[], {}, noMatches, { data: [], layout: {} }];
      return [
        [[1, 0.9, "human", "kidney", "Epithelial cell", "slide-original", "EPCAM"]],
        { spot: { slice_id: "slide-original" } }, "Retrieved evidence", {
          data: [], layout: { images: [{ source: "original-tissue.jpg", sizex: 1000, sizey: 1000 }] }
        }
      ];
    }
  });
  await app.run("runRetrieval('Original kidney query')");
  const originalMap = app.node("#atlas-live-plot").children[0];
  mode = "empty";
  await app.run("runRetrieval('Unmatched brain query')");
  const emptyChips = app.node("#query-evidence-chips").innerHTML;
  assert.equal(app.node("#atlas-evidence-cards").innerHTML, "");
  assert.equal(app.run("topEvidence"), null);
  assert.equal(app.run("liveMapState"), null);
  assert.equal(app.node("#atlas-tissue-example").hidden, true);
  assert.notEqual(app.node("#atlas-live-plot").children[0], originalMap);
  assert.match(app.node("#atlas-live-plot").children[0].textContent, /No matching spots/);
  assert.equal(app.node("#atlas-spot-count").textContent, "0 retrieved");
  assert.equal(app.node("#atlas-status-badge").textContent, "No matches");
  assert.equal(app.node("#atlas-result-summary").textContent, noMatches);
  assert.equal(app.node("#atlas-chat-input").disabled, true);
  assert.equal(app.node("#atlas-chat-form").querySelector("button").disabled, true);
  app.node("#atlas-chat-input").value = "Do not use earlier evidence";
  await app.node("#atlas-chat-form").emit("submit", { preventDefault() {} })[0];
  assert.equal(chatRequests, 0);
  assert.equal(app.node("#atlas-chat-form").querySelector("button").disabled, true);
  mode = "error";
  await app.run("runRetrieval('Later query')");
  assert.equal(app.node("#atlas-status-badge").textContent, "No matches");
  assert.equal(app.node("#query-evidence-chips").innerHTML, emptyChips);
  assert.match(app.node("#atlas-result-summary").textContent, /Hugging Face quota reached/);
  assert.doesNotMatch(app.node("#atlas-result-summary").textContent, /slide-original|manuscript example/);
  mode = "live";
  await app.run("runRetrieval('Recovery query')");
  assert.equal(app.node("#atlas-status-badge").textContent, "Live result");
  assert.equal(app.node("#atlas-chat-input").disabled, false);
  assert.equal(app.node("#atlas-chat-form").querySelector("button").disabled, false);
});

test("uploading a blank image clears old coordinates and actual grid creation cannot reselect them", async () => {
  let requests = 0;
  const app = workbench("histagent", {
    generateHistAgentReadout: async () => { requests += 1; return genes; }
  });
  let pixel = [255, 255, 255, 255];
  const createElement = app.context.document.createElement;
  app.context.document.createElement = (tag) => {
    const result = createElement(tag);
    if (tag === "canvas") result.getContext = () => new Proxy({}, {
      get: (_target, key) => key === "getImageData" ? () => ({
        data: Uint8ClampedArray.from({ length: result.width * result.height * 4 }, (_value, index) => pixel[index % 4])
      }) : () => {}
    });
    return result;
  };
  app.run("renderEvidence(buildEvidence(['EPCAM'])); updateCropPreviews()");
  await app.run("setSourceImage(new File(['white pixels'], 'blank.png', { type: 'image/png' }))");
  assert.equal(app.run("selectedSpot"), null);
  assert.equal(app.run("spots.length"), 0);
  assert.equal(app.node("#histagent-generate").disabled, true);
  app.node("#histagent-tissue-image").emit("load");
  assert.equal(app.run("selectedSpot"), null);
  assert.equal(app.run("spots.length"), 0);
  assert.equal(app.run("currentEvidence"), null);
  assert.equal(app.node("#selected-spot-id").textContent, "No spot selected");
  assert.equal(app.node("#histagent-local-preview").getAttribute("src"), null);
  assert.equal(app.node("#histagent-context-preview").getAttribute("src"), null);
  assert.equal(app.node("#histagent-context-ring").hidden, true);
  assert.match(app.node("#histagent-run-status").textContent, /No tissue sampling spots/);
  await app.run("generateEvidence()");
  assert.equal(requests, 0);
  assert.equal(app.node("#histagent-generate").disabled, true);
  pixel = [180, 70, 120, 255];
  await app.run("setSourceImage(new File(['tissue pixels'], 'tissue.png', { type: 'image/png' }))");
  app.node("#histagent-tissue-image").emit("load");
  assert.ok(app.run("spots.length") > 0);
  assert.ok(app.run("selectedSpot"));
  assert.equal(app.node("#histagent-generate").disabled, false);
  assert.equal(app.node("#histagent-local-preview").hidden, false);
  assert.equal(app.node("#histagent-context-ring").hidden, false);
});
