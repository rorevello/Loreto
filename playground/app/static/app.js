"use strict";

/* Loreto Playground front end. No dependencies: the page must work offline. */

const DEFAULT_OPTIONS = Object.freeze({
  layout: "auto", aliases: "auto", prefixes: true, id_prefixes: true, dictionary: true,
  lists: true, inline_bnodes: true, literal_shorthand: true, legend: false, deskolemize: false,
});
const FILE_NAMES = {
  input: "input.txt", "turtle-prefixed": "graph.ttl", turtle: "graph.ttl", trig: "graph.trig",
  ntriples: "graph.nt", nquads: "graph.nq", jsonld: "graph.jsonld", rdfxml: "graph.rdf", loreto: "graph.loreto",
};
const EXTENSIONS = {
  ttl: "turtle", trig: "trig", nt: "nt", nq: "nquads", jsonld: "json-ld", json: "json-ld", rdf: "xml",
  owl: "xml", xml: "xml", n3: "n3", trix: "trix", loreto: "loreto",
};

const state = {
  info: null,
  input: null,                 // { text, format, base, label }
  inputVersion: 0,
  formatChosenByUser: false,
  tokenizer: null,
  options: { ...DEFAULT_OPTIONS },
  view: "turtle-prefixed",
  last: null,                  // last /api/compare response
  viewPane: null,              // pane on the left
  seq: { compare: 0, view: 0, ablation: 0, matrix: 0 },
  ablationKey: null,
  matrixKey: null,
};

/* ------------------------- helpers ------------------------- */

const $ = (id) => document.getElementById(id);

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") node.setAttribute("style", value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const fmtInt = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-US"));
const fmtMs = (ms) => (ms == null ? "–" : ms < 10 ? `${ms.toFixed(1)} ms` : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`);
function fmtBytes(b) {
  if (b == null) return "–";
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1048576).toFixed(1)} MB`;
}
function fmtSigned(p, digits = 1) {
  if (p == null) return "–";
  const v = Number(p.toFixed(digits));
  if (v === 0) return `0${digits ? "." + "0".repeat(digits) : ""}%`;
  return `${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}%`;
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

async function api(path, body) {
  const init = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error("The server cannot be reached. Is the container running?");
  }
  let data = null;
  try { data = await res.json(); } catch (_) { /* not JSON */ }
  if (!res.ok) {
    const detail = data && data.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("\n")
      : `The server answered ${res.status}.`;
    throw new Error(message);
  }
  return data;
}

function inputSpec() {
  return { text: state.input.text, format: state.input.format, base: state.input.base || null };
}

function paneText(pane) {
  return pane ? pane.pieces.join("") + pane.rest : "";
}

/* ------------------------- busy indicator and errors ------------------------- */

let busyTimer = null;
function setBusy(on, text) {
  clearTimeout(busyTimer);
  if (on) {
    $("busy-text").textContent = text || "Working…";
    busyTimer = setTimeout(() => { $("busy").hidden = false; }, 150);
  } else {
    $("busy").hidden = true;
  }
}

function showError(message) {
  const box = $("error-box");
  box.textContent = message;
  box.hidden = false;
  $("content").classList.add("stale");
}

function hideError() {
  $("error-box").hidden = true;
  $("content").classList.remove("stale");
}

/* ------------------------- input ------------------------- */

function setInput(input, { format } = {}) {
  state.input = input;
  state.inputVersion += 1;
  if (format) {
    $("format-select").value = format;
    state.input.format = format;
  }
  const label = state.input.label ? `${state.input.label} · ` : "";
  $("input-status").textContent = `${label}${fmtBytes(new Blob([input.text]).size)}`;
}

function currentFormat() {
  return $("format-select").value || "auto";
}

function tooLarge(text) {
  const limit = state.info && state.info.limits.max_input_bytes;
  const size = new Blob([text]).size;
  if (limit && size > limit) {
    showError(`The input has ${fmtBytes(size)}; this server accepts up to ${fmtBytes(limit)}.`);
    return true;
  }
  return false;
}

async function loadExample(id) {
  const item = state.info.examples.find((e) => e.id === id);
  $("example-desc").textContent = item ? item.description + (item.license ? ` Licence: ${item.license}.` : "") : "";
  try {
    const ex = await api(`/api/examples/${encodeURIComponent(id)}`);
    state.formatChosenByUser = false;
    setInput({ text: ex.text, format: ex.format, base: null, label: item ? item.title : id }, { format: ex.format });
    await runCompare();
  } catch (e) {
    showError(e.message);
  }
}

function onPaste() {
  const text = $("paste-text").value;
  if (!text.trim() || tooLarge(text)) return;
  if (!state.formatChosenByUser) $("format-select").value = "auto";
  setInput({ text, format: currentFormat(), base: null, label: "Pasted text" });
  runCompare();
}
const onPasteDebounced = debounce(onPaste, 700);

function readFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const text = String(reader.result);
    if (tooLarge(text)) return;
    const ext = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    const format = EXTENSIONS[ext] || "auto";
    state.formatChosenByUser = false;
    $("file-hint").textContent = `${file.name} (${fmtBytes(file.size)})`;
    setInput({ text, format, base: null, label: file.name }, { format });
    runCompare();
  };
  reader.onerror = () => showError(`Could not read ${file.name}.`);
  reader.readAsText(file);
}

async function fetchUrl(url, hintEl) {
  hintEl.textContent = "Fetching…";
  setBusy(true, "Fetching…");
  try {
    const res = await api("/api/fetch", { url });
    state.formatChosenByUser = false;
    hintEl.textContent = `${fmtBytes(res.bytes)} from ${res.base}`;
    setInput({ text: res.text, format: res.format, base: res.base, label: res.base }, { format: res.format });
    $("paste-text").value = "";
    await runCompare();
  } catch (e) {
    hintEl.textContent = "";
    showError(e.message);
  } finally {
    setBusy(false);
  }
}

/* ------------------------- requests ------------------------- */

async function runCompare() {
  if (!state.input) return;
  const seq = ++state.seq.compare;
  setBusy(true, "Serialising…");
  try {
    const res = await api("/api/compare", {
      ...inputSpec(), tokenizer: state.tokenizer, options: state.options, view: state.view,
    });
    if (seq !== state.seq.compare) return;
    state.last = res;
    state.viewPane = res.view;
    hideError();
    renderCompare();
    const ablationKey = `${state.inputVersion}|${state.input.format}|${state.tokenizer}`;
    if (ablationKey !== state.ablationKey) runAblation(ablationKey);
    const matrixKey = `${state.inputVersion}|${state.input.format}`;
    if (matrixKey !== state.matrixKey) runMatrix(matrixKey);
  } catch (e) {
    if (seq === state.seq.compare) showError(e.message);
  } finally {
    if (seq === state.seq.compare) setBusy(false);
  }
}

async function runView() {
  if (!state.input || !state.last) return;
  const seq = ++state.seq.view;
  try {
    const res = await api("/api/view", { ...inputSpec(), tokenizer: state.tokenizer, view: state.view });
    if (seq !== state.seq.view) return;
    state.viewPane = res;
    renderViewPane();
  } catch (e) {
    if (seq === state.seq.view) showError(e.message);
  }
}

async function runAblation(key) {
  const seq = ++state.seq.ablation;
  state.ablationKey = key;
  const box = $("ablation");
  box.replaceChildren(placeholder("Running the ablation…", true));
  try {
    const res = await api("/api/ablation", { ...inputSpec(), tokenizer: state.tokenizer });
    if (seq === state.seq.ablation) renderAblation(res);
  } catch (e) {
    if (seq === state.seq.ablation) {
      state.ablationKey = null;
      box.replaceChildren(placeholder(e.message));
    }
  }
}

async function runMatrix(key) {
  const seq = ++state.seq.matrix;
  state.matrixKey = key;
  const box = $("matrix");
  box.replaceChildren(placeholder("Counting with every tokeniser…", true));
  try {
    const res = await api("/api/tokenizers", inputSpec());
    if (seq === state.seq.matrix) renderMatrix(res);
  } catch (e) {
    if (seq === state.seq.matrix) {
      state.matrixKey = null;
      box.replaceChildren(placeholder(e.message));
    }
  }
}

async function fullText(which) {
  const pane = which === "loreto" ? state.last && state.last.loreto.pane : state.viewPane;
  if (pane && pane.error) throw new Error("This format could not be written for this input.");
  if (pane && !pane.text_truncated) return paneText(pane);
  const res = await fetch("/api/download", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...inputSpec(), tokenizer: state.tokenizer, options: state.options, view: state.view,
      which: which === "loreto" ? "loreto" : state.view }),
  });
  if (!res.ok) throw new Error(`Download failed (${res.status}).`);
  return res.text();
}

function placeholder(text, spinning) {
  return el("p", { class: "placeholder" }, spinning ? el("span", { class: "spinner", "aria-hidden": "true" }) : null, text);
}

/* ------------------------- rendering ------------------------- */

function renderCompare() {
  const r = state.last;
  const lo = r.loreto;
  const byId = Object.fromEntries(r.formats.map((f) => [f.id, f]));

  $("kpi-triples").textContent = fmtInt(r.input.triples);
  $("kpi-triples-sub").textContent =
    `${r.input.format_label}${r.input.named_graphs ? ` · ${r.input.named_graphs} named graph${r.input.named_graphs > 1 ? "s" : ""}` : ""}`;
  $("kpi-tokens").textContent = fmtInt(lo.tokens);
  $("kpi-tokens-sub").textContent = `${r.tokenizer.label} · ${lo.layout}`;
  renderSavingKpi("kpi-turtle", byId["turtle-prefixed"], lo.tokens);
  renderSavingKpi("kpi-jsonld", byId.jsonld, lo.tokens);
  $("kpi-time").textContent = fmtMs(lo.timings_ms.total);
  $("kpi-time-sub").textContent = `${fmtInt(Math.round(r.input.triples / Math.max(lo.timings_ms.total, 0.001) * 1000))} triples/s`;
  const verify = $("kpi-verify");
  verify.textContent = lo.verified ? "Lossless" : "Mismatch";
  verify.className = `kpi-value ${lo.verified ? "good" : "bad"}`;
  $("kpi-verify-sub").textContent = lo.verified
    ? `${state.options.deskolemize ? "de-skolemised, " : ""}isomorphic · ${fmtMs(lo.verify_ms)}`
    : "parsed text differs";

  renderBars(r);
  renderLoretoPane();
  renderViewPane();
}

function renderSavingKpi(id, format, loretoTokens) {
  const value = $(id);
  const sub = $(`${id}-sub`);
  if (!format || format.tokens == null) {
    value.textContent = "–";
    value.className = "kpi-value";
    sub.textContent = format && format.error ? "not available" : "";
    return;
  }
  value.textContent = fmtSigned(-format.saving_pct);
  value.className = `kpi-value ${format.saving_pct >= 0 ? "good" : "bad"}`;
  sub.textContent = `${fmtInt(format.tokens)} → ${fmtInt(loretoTokens)} tokens`;
}

function renderBars(r) {
  const rows = [...r.formats].sort((a, b) => {
    if (a.tokens == null) return 1;
    if (b.tokens == null) return -1;
    return b.tokens - a.tokens;
  });
  const max = Math.max(...rows.map((f) => f.tokens || 0), 1);
  const nodes = [];
  for (const f of rows) {
    const isLoreto = f.id === "loreto";
    const cls = isLoreto ? " bar-row-loreto" : "";
    nodes.push(el("div", { class: `bar-label${cls}` }, f.label,
      f.merges_graphs ? el("small", { title: "Written from the union of all graphs" }, " · union") : null));
    if (f.tokens == null) {
      nodes.push(el("div", { class: "bar-error", style: "grid-column: span 3", title: f.error || "" },
        "could not be written for this input"));
      continue;
    }
    nodes.push(el("div", { class: `bar-track${cls}` },
      el("div", { class: "bar-fill", style: `width: ${(100 * f.tokens / max).toFixed(2)}%` })));
    nodes.push(el("div", { class: "bar-value" }, fmtInt(f.tokens)));
    let saving;
    if (isLoreto) saving = el("span", { class: "pill pill-muted" }, `${fmtBytes(f.bytes)}`);
    else if (f.saving_pct == null) saving = el("span", { class: "pill pill-muted" }, "–");
    else saving = el("span", {
      class: `pill ${f.saving_pct >= 0 ? "pill-good" : "pill-bad"}`,
      title: `Loreto uses ${fmtSigned(-f.saving_pct)} tokens compared with ${f.label}`,
    }, fmtSigned(-f.saving_pct));
    nodes.push(el("div", { class: "bar-saving" }, saving));
  }
  $("bars").replaceChildren(...nodes);
  $("bars-sub").textContent =
    `${r.tokenizer.label} tokens. The percentage is Loreto's token count relative to each format.`;
  const notes = ["“Turtle, all prefixes” declares a prefix for every namespace used twice or more, and only the prefixes it uses: the stronger Turtle baseline.",
    "“Turtle (rdflib)” and the other formats are written by rdflib as in the paper's corpus measurements; rdflib prefixes predicates only."];
  if (r.input.named_graphs) notes.push("Formats marked “union” merge the named graphs; TriG, N-Quads and Loreto keep them.");
  $("bars-foot").textContent = notes.join(" ");
}

function renderCode(pre, pane) {
  const top = pre.scrollTop;
  const left = pre.scrollLeft;
  pre.replaceChildren();
  pre.classList.remove("code-error");
  if (!pane) return;
  if (pane.error) {
    pre.classList.add("code-error");
    pre.textContent = `This format could not be written for this input.\n\n${pane.error}`;
    return;
  }
  if (!$("show-tokens").checked) {
    pre.textContent = paneText(pane);
  } else {
    const frag = document.createDocumentFragment();
    const pieces = pane.pieces;
    for (let i = 0; i < pieces.length; i++) {
      const span = document.createElement("span");
      span.className = `t${i % 5}`;
      span.textContent = pieces[i];
      frag.appendChild(span);
    }
    if (pane.rest) frag.appendChild(document.createTextNode(pane.rest));
    pre.appendChild(frag);
  }
  pre.scrollTop = top;
  pre.scrollLeft = left;
}

function paneNote(pane) {
  if (!pane || pane.error) return "";
  const notes = [];
  const limit = state.info.limits.max_view_tokens;
  if (pane.rest && $("show-tokens").checked) notes.push(`Token boundaries are shown for the first ${fmtInt(limit)} tokens.`);
  if (pane.text_truncated) notes.push(`Showing the first ${fmtInt(pane.pieces.join("").length + pane.rest.length)} characters; Copy and Download give the full text.`);
  return notes.join(" ");
}

function renderLoretoPane() {
  const lo = state.last.loreto;
  const parts = [`${fmtInt(lo.tokens)} tokens`, fmtBytes(lo.bytes), lo.layout,
    `${lo.prefixes} prefix${lo.prefixes === 1 ? "" : "es"}`];
  if (lo.dictionary_entries) parts.push(`${lo.dictionary_entries} DICT`);
  $("loreto-meta").textContent = parts.join(" · ");
  renderCode($("loreto-code"), lo.pane);
  $("loreto-note").textContent = paneNote(lo.pane);
}

function renderViewPane() {
  const pane = state.viewPane;
  $("view-select").value = state.view;
  $("view-meta").textContent = pane && !pane.error ? `${fmtInt(pane.tokens)} tokens · ${fmtBytes(pane.bytes)}` : "";
  renderCode($("view-code"), pane);
  $("view-note").textContent = paneNote(pane);
}

function renderAblation(res) {
  const rows = res.rows;
  // "Everything off" would flatten every other bar: it is left out of the scale and clipped.
  const scale = Math.max(1, ...rows.filter((r) => r.id !== "full" && r.id !== "minimal").map((r) => Math.abs(r.delta_pct || 0)));
  const body = rows.map((r) => {
    const isFull = r.id === "full";
    let delta;
    if (isFull) {
      delta = el("span", { class: "group-cell" }, "reference");
    } else {
      const width = `width: ${(100 * Math.min(Math.abs(r.delta_pct), scale) / scale).toFixed(1)}%`;
      const cls = r.delta < 0 ? "good" : r.delta > 0 ? "bad" : "";
      delta = el("div", { class: "delta" },
        el("div", { class: "delta-neg" }, r.delta < 0 ? el("span", { style: width }) : null),
        el("div", { class: "delta-pos" }, r.delta > 0 ? el("span", { style: width }) : null),
        el("span", { class: `delta-text ${cls}` }, fmtSigned(r.delta_pct)));
    }
    return el("tr", { class: isFull ? "row-full" : null },
      el("td", {}, r.label),
      el("td", { class: "group-cell" }, r.group),
      el("td", {}, fmtInt(r.tokens), isFull ? null : el("span", { class: "sub" }, `${r.delta > 0 ? "+" : ""}${fmtInt(r.delta)}`)),
      el("td", { class: "delta-cell" }, delta),
      el("td", { class: "group-cell" }, r.layout));
  });
  const table = el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "Variant"), el("th", {}, "Part"), el("th", {}, "Tokens"),
      el("th", {}, "Change against the full method"), el("th", {}, "Layout"))),
    el("tbody", {}, body));
  $("ablation").replaceChildren(table);
  $("abl-sub").textContent =
    `${res.tokenizer.label} tokens. Green: fewer tokens than the full method; amber: more. The legend and de-skolemisation are optional extras.`;
}

function renderMatrix(res) {
  const head = el("tr", {}, el("th", {}, "Tokeniser"),
    res.columns.map((c) => el("th", {}, c.label)),
    el("th", {}, "Loreto"),
    el("th", { title: `The text optimised for ${res.reference.label}, counted with each tokeniser` },
      `Loreto, ${res.reference.label} text`),
    el("th", {}, "vs Turtle, all prefixes"));
  const body = res.rows.map((row) => {
    const values = [...res.columns.map((c) => row.formats[c.id]), row.loreto].filter((v) => v != null);
    const best = Math.min(...values);
    const cells = res.columns.map((c) => {
      const v = row.formats[c.id];
      return el("td", { class: v === best ? "best" : null }, fmtInt(v));
    });
    const gain = row.reoptimisation_gain_pct;
    return el("tr", {},
      el("td", {}, row.tokenizer.label, el("span", { class: "sub" }, row.tokenizer.models)),
      cells,
      el("td", { class: row.loreto === best ? "best" : null }, fmtInt(row.loreto)),
      el("td", {}, fmtInt(row.loreto_reference_text),
        el("span", { class: "sub" }, gain ? `${fmtSigned(gain, 2)} vs its own` : "same cost")),
      el("td", {}, el("span", {
        class: `pill ${row.saving_vs_turtle_pct >= 0 ? "pill-good" : "pill-bad"}`,
      }, fmtSigned(-row.saving_vs_turtle_pct))));
  });
  $("matrix").replaceChildren(el("table", {}, el("thead", {}, head), el("tbody", {}, body)));
  $("mat-sub").textContent =
    `Every format counted with every tokeniser. Loreto is optimised for each tokeniser (full method); the next column shows what the ${res.reference.label}-optimised text costs instead.`;
}

/* ------------------------- controls ------------------------- */

function selectTab(name) {
  for (const btn of document.querySelectorAll(".tabs [role=tab]")) {
    const on = btn.id === `tab-btn-${name}`;
    btn.setAttribute("aria-selected", String(on));
    btn.tabIndex = on ? 0 : -1;
    $(btn.getAttribute("aria-controls")).hidden = !on;
  }
}

function syncOptionControls() {
  for (const input of document.querySelectorAll(".segmented input")) {
    input.checked = state.options[input.name] === input.value;
  }
  for (const input of document.querySelectorAll(".switches input[data-option]")) {
    input.checked = Boolean(state.options[input.dataset.option]);
  }
}

const runCompareSoon = debounce(runCompare, 120);

function bindControls() {
  const tabs = [...document.querySelectorAll(".tabs [role=tab]")];
  tabs.forEach((btn, i) => {
    btn.addEventListener("click", () => selectTab(btn.id.replace("tab-btn-", "")));
    btn.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      next.focus();
      next.click();
    });
  });

  $("example-select").addEventListener("change", (e) => loadExample(e.target.value));

  $("paste-text").addEventListener("input", onPasteDebounced);
  $("paste-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); onPaste(); }
  });

  $("file-input").addEventListener("change", (e) => readFile(e.target.files[0]));
  const drop = $("dropzone");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("dragging"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("dragging"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("dragging");
    readFile(e.dataTransfer.files[0]);
  });

  $("url-form").addEventListener("submit", (e) => {
    e.preventDefault();
    fetchUrl($("url-input").value, $("url-hint"));
  });

  $("format-select").addEventListener("change", (e) => {
    state.formatChosenByUser = e.target.value !== "auto";
    if (!state.input) return;
    state.input.format = e.target.value;
    state.inputVersion += 1;
    runCompare();
  });

  $("tokenizer-select").addEventListener("change", (e) => {
    state.tokenizer = e.target.value;
    renderTokenizerHint();
    runCompare();
  });

  for (const input of document.querySelectorAll(".segmented input")) {
    input.addEventListener("change", () => {
      state.options = { ...state.options, [input.name]: input.value };
      runCompareSoon();
    });
  }
  for (const input of document.querySelectorAll(".switches input[data-option]")) {
    input.addEventListener("change", () => {
      state.options = { ...state.options, [input.dataset.option]: input.checked };
      runCompareSoon();
    });
  }
  $("reset-options").addEventListener("click", () => {
    state.options = { ...DEFAULT_OPTIONS };
    syncOptionControls();
    runCompare();
  });
  $("run").addEventListener("click", () => {
    if ($("tab-paste").hidden === false && $("paste-text").value.trim()) onPaste();
    else runCompare();
  });

  $("view-select").addEventListener("change", (e) => { state.view = e.target.value; runView(); });
  $("show-tokens").addEventListener("change", () => {
    if (!state.last) return;
    renderLoretoPane();
    renderViewPane();
  });

  for (const btn of document.querySelectorAll("[data-copy]")) {
    btn.addEventListener("click", async () => {
      try {
        await copyText(await fullText(btn.dataset.copy));
        flash(btn, "Copied");
      } catch (e) { showError(e.message); }
    });
  }
  for (const btn of document.querySelectorAll("[data-download]")) {
    btn.addEventListener("click", async () => {
      try {
        const which = btn.dataset.download;
        const name = FILE_NAMES[which === "loreto" ? "loreto" : state.view] || "graph.txt";
        saveFile(name, await fullText(which));
      } catch (e) { showError(e.message); }
    });
  }
  $("decode-btn").addEventListener("click", async () => {
    try {
      const text = await fullText("loreto");
      $("paste-text").value = text;
      selectTab("paste");
      state.formatChosenByUser = true;
      setInput({ text, format: "loreto", base: null, label: "Loreto text (decoded)" }, { format: "loreto" });
      runCompare();
    } catch (e) { showError(e.message); }
  });
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = el("textarea", { style: "position:fixed;opacity:0;top:0;left:0" });
  area.value = text;
  document.body.append(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

function saveFile(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function flash(btn, text) {
  const old = btn.textContent;
  btn.textContent = text;
  setTimeout(() => { btn.textContent = old; }, 1200);
}

function renderTokenizerHint() {
  const t = state.info.tokenizers.available.find((x) => x.id === state.tokenizer);
  $("tokenizer-hint").textContent = t ? `${t.models}. Source: ${t.source}.` : "";
}

/* ------------------------- start ------------------------- */

async function init() {
  bindControls();
  try {
    state.info = await api("/api/info");
  } catch (e) {
    showError(e.message);
    return;
  }
  const info = state.info;
  $("footer-versions").textContent = `Loreto ${info.loreto_version} · Playground ${info.version}`;

  $("example-select").replaceChildren(...info.examples.map((e) => el("option", { value: e.id }, e.title)));
  $("format-select").replaceChildren(...info.input_formats.map((f) => el("option", { value: f.id }, f.label)));
  $("view-select").replaceChildren(...info.views.map((v) => el("option", { value: v.id }, v.label)));
  $("tokenizer-select").replaceChildren(...info.tokenizers.available.map((t) =>
    el("option", { value: t.id }, t.label)));
  state.tokenizer = info.tokenizers.default;
  $("tokenizer-select").value = state.tokenizer;
  renderTokenizerHint();
  if (info.tokenizers.unavailable.length) {
    $("tokenizer-hint").textContent += ` Not available here: ${info.tokenizers.unavailable.map((t) => t.label).join(", ")}.`;
  }

  if (!info.features.url) {
    $("url-input").disabled = true;
    $("url-hint").textContent = "Fetching URLs is disabled on this server.";
  }
  syncOptionControls();
  if (info.examples.length) {
    $("example-select").value = info.examples[0].id;
    await loadExample(info.examples[0].id);
  }
}

window.addEventListener("error", (e) => {
  showError(`The page failed: ${e.message}. Reload it with Ctrl+Shift+R (Cmd+Shift+R on a Mac).`);
});

document.addEventListener("DOMContentLoaded", init);
