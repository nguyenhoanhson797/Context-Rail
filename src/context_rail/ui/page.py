"""The self-contained loopback UI: the compiled pack, one item card at a time."""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Context Rail</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #f5f6f8; --panel: #ffffff; --ink: #14171c; --muted: #626b78;
    --line: #dfe3e9; --accent: #2563eb; --accent-ink: #ffffff;
    --ok: #15803d; --warn: #a16207; --bad: #b91c1c; --code: #f1f3f6;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0e1116; --panel: #171b22; --ink: #e8ebf0; --muted: #9aa4b2;
      --line: #2a3140; --accent: #5b8cff; --accent-ink: #0b0e14;
      --ok: #4ade80; --warn: #fbbf24; --bad: #f87171; --code: #11151c;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 14px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif;
  }
  h1, h2, h3 { margin: 0; font-weight: 600; }
  h1 { font-size: 19px; letter-spacing: -.01em; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .09em; color: var(--muted); }
  p { margin: 0; }
  button {
    font: inherit; color: var(--ink); background: var(--panel);
    border: 1px solid var(--line); border-radius: 8px; padding: 5px 10px;
    cursor: pointer;
  }
  button:hover { border-color: var(--accent); }
  button.primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
  button:disabled { opacity: .55; cursor: progress; }
  code, pre { font-family: "Cascadia Code", Consolas, monospace; font-size: 12.5px; }
  code { background: var(--code); border-radius: 5px; padding: 1px 5px; }
  pre { margin: 0; white-space: pre-wrap; word-break: break-word; }
  .top {
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 14px 20px; background: var(--panel); border-bottom: 1px solid var(--line);
    position: sticky; top: 0; z-index: 2;
  }
  .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .mark {
    width: 30px; height: 30px; border-radius: 9px; flex: none;
    background: linear-gradient(135deg, var(--accent), #7c3aed);
  }
  .brand p { color: var(--muted); font-size: 12.5px; }
  .badges { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
  .pill {
    display: inline-flex; align-items: center; gap: 5px; padding: 2px 9px;
    border-radius: 999px; border: 1px solid var(--line); font-size: 12px;
    color: var(--muted); background: var(--panel); white-space: nowrap;
  }
  .pill.ok { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 45%, var(--line)); }
  .pill.warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }
  .pill.bad { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 45%, var(--line)); }
  .pill.accent { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
  .counts { display: flex; gap: 6px; flex-wrap: wrap; padding: 12px 20px 0; }
  .layout {
    display: grid; grid-template-columns: 330px minmax(0, 1fr); gap: 16px;
    padding: 16px 20px 32px; align-items: start;
  }
  aside { display: flex; flex-direction: column; gap: 16px; position: sticky; top: 76px; }
  .panel {
    background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
    padding: 14px;
  }
  .panel > h2 { margin-bottom: 10px; }
  .hint { color: var(--muted); font-size: 12.5px; }
  .group { margin-bottom: 12px; }
  .group:last-child { margin-bottom: 0; }
  .group-head {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 5px; font-size: 12px; color: var(--muted);
  }
  .item {
    display: flex; align-items: baseline; gap: 8px; width: 100%; text-align: left;
    border: 0; border-radius: 7px; padding: 6px 8px; background: transparent;
  }
  .item:hover { background: color-mix(in srgb, var(--accent) 9%, transparent); }
  .item.active { background: color-mix(in srgb, var(--accent) 16%, transparent); }
  .item .name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .item .state { color: var(--muted); font-size: 12px; flex: none; }
  .wf { border-top: 1px solid var(--line); padding-top: 9px; margin-top: 9px; }
  .wf:first-child { border-top: 0; padding-top: 0; margin-top: 0; }
  .wf-head { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
  .wf-head b { font-size: 13px; }
  .states { display: flex; flex-wrap: wrap; gap: 4px; margin: 4px 0; }
  .chip {
    font-family: "Cascadia Code", Consolas, monospace; font-size: 11px;
    border: 1px solid var(--line); border-radius: 5px; padding: 0 5px; color: var(--muted);
  }
  .rule { font-size: 12px; color: var(--muted); }
  .rule code { color: var(--ink); }
  .verbs .verb { padding: 7px 0; border-top: 1px solid var(--line); }
  .verbs .verb:first-child { border-top: 0; padding-top: 0; }
  .verb b { font-size: 13px; }
  main { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
  .empty h2 { margin-bottom: 8px; }
  .empty p { color: var(--muted); max-width: 62ch; margin-bottom: 8px; }
  .empty ul { margin: 0; padding-left: 18px; color: var(--muted); }
  .empty li { margin: 3px 0; }
  .item-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
  .item-head h2 { color: var(--ink); font-size: 20px; letter-spacing: -.01em; text-transform: none; }
  .meta { color: var(--muted); font-size: 12.5px; margin-top: 4px; }
  .section { border-top: 1px solid var(--line); margin-top: 14px; padding-top: 12px; }
  .section > h3 {
    font-size: 11.5px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); margin-bottom: 8px;
  }
  .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .next button { font-family: "Cascadia Code", Consolas, monospace; }
  .proof { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .checks { margin: 8px 0 0; padding-left: 18px; }
  .checks li { margin: 2px 0; }
  .ok-text { color: var(--ok); }
  .bad-text { color: var(--bad); }
  .banner { border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; font-size: 13px; }
  .banner.bad { background: color-mix(in srgb, var(--bad) 12%, transparent); color: var(--bad); }
  .banner.ok { background: color-mix(in srgb, var(--ok) 12%, transparent); color: var(--ok); }
  .banner code { background: transparent; color: inherit; }
  .body {
    background: var(--code); border-radius: 8px; padding: 12px;
    max-height: 420px; overflow: auto; color: var(--ink);
  }
  .rel { display: flex; flex-wrap: wrap; gap: 6px; }
  .rel button { font-size: 12px; padding: 2px 8px; }
  .fields { width: 100%; border-collapse: collapse; font-size: 13px; }
  .fields td { padding: 3px 8px 3px 0; vertical-align: top; }
  .fields td:first-child { color: var(--muted); width: 120px; }
  details > summary { cursor: pointer; color: var(--muted); font-size: 13px; }
  .tool-form { display: grid; gap: 8px; margin-top: 10px; }
  .tool-form textarea {
    min-height: 110px; resize: vertical; font-family: "Cascadia Code", Consolas, monospace;
    font-size: 12.5px; color: var(--ink); background: var(--code);
    border: 1px solid var(--line); border-radius: 8px; padding: 8px;
  }
  .result { margin-top: 10px; }
  .issues { margin: 8px 0 0; padding-left: 18px; }
  .issues li { margin: 3px 0; font-size: 13px; }
  @media (max-width: 900px) {
    .layout { grid-template-columns: 1fr; }
    aside { position: static; }
  }
</style>
</head>
<body>
<header class="top">
  <div class="brand">
    <div class="mark"></div>
    <div>
      <h1>Context Rail</h1>
      <p id="subtitle">Reading the store...</p>
    </div>
  </div>
  <div class="badges" id="badges"></div>
</header>
<div class="counts" id="counts"></div>
<div class="layout">
  <aside>
    <section class="panel">
      <h2>Workflows</h2>
      <div id="workflows"><p class="hint">Loading...</p></div>
    </section>
    <section class="panel">
      <h2>Items</h2>
      <p class="hint" id="items-hint"></p>
      <div id="items"></div>
    </section>
    <section class="panel verbs">
      <h2>Verbs</h2>
      <div id="verbs"><p class="hint">No declarative verbs in this pack.</p></div>
    </section>
  </aside>
  <main>
    <section class="panel empty" id="empty">
      <h2>Read the engine, not the YAML</h2>
      <p>A pack is one project type. Every document type selects exactly one
         workflow, a workflow names the legal states, and a gate names the proof
         a move needs.</p>
      <ul>
        <li><b>Workflows</b> show each lifecycle, its states and quality gates.</li>
        <li><b>Items</b> are grouped by the workflow they follow.</li>
        <li><b>Verbs</b> are the pack's declarative actions.</li>
      </ul>
      <p style="margin-top:10px">Pick an item to see its card: current state, legal
         next moves, the gate and the exact proof that satisfies it.</p>
    </section>
    <section class="panel" id="item" hidden></section>
    <section class="panel">
      <div class="row" style="justify-content: space-between">
        <h2>Store checks</h2>
        <div class="row">
          <span class="hint" id="health-summary"></span>
          <button id="check">Run checks</button>
        </div>
      </div>
      <div id="health"></div>
    </section>
    <details class="panel">
      <summary>Advanced · run any tool</summary>
      <div class="tool-form">
        <div class="row">
          <select id="tool" style="min-width: 240px"></select>
          <span class="hint">Arguments are one JSON object.</span>
        </div>
        <textarea id="args" spellcheck="false">{}</textarea>
        <div class="row">
          <button class="primary" id="run">Run</button>
        </div>
      </div>
      <div class="result" id="result"></div>
    </details>
  </main>
</div>
<script>
const state = { slug: null, doc: null, reason: null, surface: null };

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function pill(text, cls) { return el("span", "pill" + (cls ? " " + cls : ""), text); }
function byId(id) { return document.getElementById(id); }

async function call(path, options) {
  try {
    const response = await fetch(url(path), options);
    return await response.json();
  } catch (error) {
    return {ok: false, error: {code: "NETWORK", message: String(error)}};
  }
}
async function get(path) {
  return call(path, {credentials: "same-origin"});
}
async function post(path, body) {
  return call(path, {
    method: "POST", credentials: "same-origin",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
}
function url(path) {
  const token = new URLSearchParams(location.search).get("token");
  return path + (token ? (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token) : "");
}
function showRefusal(banner, result) {
  clear(banner);
  const error = result.error || {};
  banner.className = "banner bad";
  banner.appendChild(el("b", null, error.code || "REFUSED"));
  banner.appendChild(document.createTextNode(" " + (error.message || "")));
  const command = error.details && error.details.command;
  if (command) {
    banner.appendChild(document.createTextNode("  "));
    banner.appendChild(el("code", null, command));
  }
}

function renderBadges(context, surface) {
  const badges = byId("badges");
  clear(badges);
  badges.appendChild(pill(surface && surface.pack ? surface.pack + " pack" : "pack", "accent"));
  const approval = (context.status.approval || {});
  const approvalClass = approval.state === "approved" ? "ok"
    : approval.state === "missing" ? "warn" : "bad";
  badges.appendChild(pill("approval: " + (approval.state || "unknown"), approvalClass));
  if (context.status.approval && context.status.approval.state !== "approved") {
    badges.appendChild(pill('rail approve charter', "warn"));
  }
  if (context.omitted) badges.appendChild(pill(context.omitted + " more item(s)", "warn"));
}

function renderCounts(context) {
  const counts = byId("counts");
  clear(counts);
  for (const [name, count] of Object.entries(context.status.counts || {})) {
    counts.appendChild(pill(name + ": " + count, count ? "accent" : ""));
  }
}

function workflowCounts(items) {
  const counts = {};
  for (const item of items) {
    const workflow = item.workflow || "default";
    counts[workflow] = counts[workflow] || {};
    const state = item.status || "unknown";
    counts[workflow][state] = (counts[workflow][state] || 0) + 1;
  }
  return counts;
}

function renderWorkflows(surface, items) {
  const target = byId("workflows");
  clear(target);
  if (!surface || !surface.workflows.length) {
    target.appendChild(el("p", "hint", "This pack declares no named workflows."));
    return;
  }
  const counts = workflowCounts(items);
  for (const workflow of surface.workflows) {
    const box = el("div", "wf");
    const head = el("div", "wf-head");
    head.appendChild(el("b", null, workflow.name));
    const own = counts[workflow.name] || {};
    const total = Object.values(own).reduce((sum, value) => sum + value, 0);
    head.appendChild(el("span", "hint", total + " item" + (total === 1 ? "" : "s")));
    box.appendChild(head);
    const states = el("div", "states");
    for (const name of workflow.states) states.appendChild(el("span", "chip", name));
    box.appendChild(states);
    const quality = workflow.quality || {};
    for (const [status, rule] of Object.entries(quality)) {
      const line = el("div", "rule");
      line.appendChild(document.createTextNode("→ " + status + ": "));
      line.appendChild(el("code", null, rule.gate));
      if (rule.checks && rule.checks.length) {
        line.appendChild(document.createTextNode(" · " + rule.checks.join(", ")));
      }
      box.appendChild(line);
    }
    target.appendChild(box);
  }
}

function renderItems(items) {
  const target = byId("items");
  clear(target);
  if (!items.length) {
    target.appendChild(el("p", "hint", "No items yet. Use `rail change add` or the tool runner."));
    byId("items-hint").textContent = "";
    return;
  }
  byId("items-hint").textContent = "Newest first · select to open the card";
  const groups = {};
  for (const item of items) {
    const workflow = item.workflow || "default";
    (groups[workflow] = groups[workflow] || []).push(item);
  }
  for (const [workflow, rows] of Object.entries(groups)) {
    const group = el("div", "group");
    const head = el("div", "group-head");
    head.appendChild(el("span", null, workflow));
    head.appendChild(el("span", null, rows.length));
    group.appendChild(head);
    for (const item of rows) {
      const button = el("button", "item" + (item.slug === state.slug ? " active" : ""));
      button.appendChild(el("span", "name", item.title || item.slug));
      button.appendChild(el("span", "state", item.status || "unknown"));
      button.onclick = () => selectItem(item.slug);
      group.appendChild(button);
    }
    target.appendChild(group);
  }
}

function renderVerbs(surface) {
  const target = byId("verbs");
  clear(target);
  if (!surface || !surface.actions.length) {
    target.appendChild(el("p", "hint", "No declarative verbs in this pack."));
    return;
  }
  for (const action of surface.actions) {
    const box = el("div", "verb");
    box.appendChild(el("b", null, action.name));
    box.appendChild(el("div", "hint", action.use_when || ""));
    const rule = el("div", "rule");
    rule.appendChild(el("code", null, action.on));
    if (action.from && action.from.length) {
      rule.appendChild(document.createTextNode(" · from " + action.from.join(", ")));
    }
    box.appendChild(rule);
    target.appendChild(box);
  }
}

function proofRow(label, value) {
  const item = el("span", "pill" + (value ? " ok" : ""), label + (value ? "" : ": off"));
  return item;
}

function renderItem() {
  const panel = byId("item");
  const empty = byId("empty");
  if (!state.doc) { panel.hidden = true; empty.hidden = false; return; }
  const doc = state.doc.doc;
  const card = state.doc.card || {};
  const reason = state.reason || {};
  const missing = new Set(reason.missing || []);
  empty.hidden = true;
  panel.hidden = false;
  clear(panel);

  const banner = el("div");
  panel.appendChild(banner);

  const head = el("div", "item-head");
  const left = el("div");
  left.appendChild(el("h2", null, doc.title || doc.slug));
  const meta = el("div", "meta");
  meta.appendChild(document.createTextNode("preset "));
  meta.appendChild(el("code", null, doc.preset));
  meta.appendChild(document.createTextNode(" · workflow "));
  meta.appendChild(el("code", null, doc.workflow || card.workflow || "default"));
  meta.appendChild(document.createTextNode(" · state "));
  meta.appendChild(el("code", null, doc.status || card.state || "unknown"));
  meta.appendChild(el("div", null, doc.path));
  left.appendChild(meta);
  head.appendChild(left);
  head.appendChild(pill("gate: " + (card.gate || "none"), "accent"));
  panel.appendChild(head);

  const next = el("div", "section next");
  next.appendChild(el("h3", null, "Next moves"));
  const row = el("div", "row");
  const targets = card.next || [];
  if (!targets.length) {
    row.appendChild(el("span", "hint", "No further moves from this state."));
  } else {
    row.appendChild(el("span", "hint", "Allowed: "));
    for (const target of targets) {
      const button = el("button", null, target);
      button.onclick = async () => {
        button.disabled = true;
        const result = await post("/api/tool/change%20move", {
          slug: doc.slug, status: target, expect: doc.etag,
        });
        if (!result.ok) {
          button.disabled = false;
          showRefusal(banner, result);
          return;
        }
        await load();
      };
      row.appendChild(button);
    }
  }
  next.appendChild(row);
  panel.appendChild(next);

  const proof = el("div", "section");
  proof.appendChild(el("h3", null, "Proof this gate needs"));
  const gateNone = !card.gate || card.gate === "none";
  if (gateNone) {
    proof.appendChild(el("p", "hint", "This move runs no gate; only the workflow transition is checked."));
  } else {
    const pills = el("div", "proof");
    const rule = card.proof || {};
    pills.appendChild(proofRow("acceptance", rule.acceptance));
    pills.appendChild(proofRow("review", rule.review));
    pills.appendChild(proofRow("human", rule.human));
    if (rule.children) pills.appendChild(pill("children: " + rule.children, "warn"));
    proof.appendChild(pills);
    const checks = rule.checks || [];
    if (checks.length) {
      const list = el("ul", "checks");
      for (const check of checks) {
        const item = el("li");
        const failed = missing.has(check);
        item.appendChild(el("code", null, check));
        item.appendChild(document.createTextNode(" "));
        item.appendChild(el("span", failed ? "bad-text" : "ok-text", failed ? "missing" : "verified"));
        list.appendChild(item);
      }
      proof.appendChild(list);
    } else {
      proof.appendChild(el("p", "hint", "This gate selects no named checks."));
    }
  }
  if (reason.command) {
    const line = el("p", "hint");
    line.appendChild(document.createTextNode("Next command: "));
    line.appendChild(el("code", null, reason.command));
    proof.appendChild(line);
  }
  panel.appendChild(proof);

  if (card.command) {
    const section = el("div", "section");
    section.appendChild(el("h3", null, "Command"));
    const line = el("div", "row");
    line.appendChild(el("code", null, card.command));
    const copy = el("button", null, "Copy");
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(card.command); copy.textContent = "Copied"; }
      catch (error) { copy.textContent = "Select the text"; }
    };
    line.appendChild(copy);
    section.appendChild(line);
    panel.appendChild(section);
  }

  const fields = Object.entries(doc.fields || {});
  if (fields.length) {
    const section = el("div", "section");
    section.appendChild(el("h3", null, "Metadata"));
    const table = el("table", "fields");
    for (const [key, value] of fields) {
      const row = el("tr");
      row.appendChild(el("td", null, key));
      row.appendChild(el("td", null, typeof value === "string" ? value : JSON.stringify(value)));
      table.appendChild(row);
    }
    section.appendChild(table);
    panel.appendChild(section);
  }

  const relations = {out: state.doc.out || {}, in: state.doc.in || {}};
  const relationSection = el("div", "section");
  relationSection.appendChild(el("h3", null, "Relations"));
  const relationRow = el("div", "rel");
  let anyRelation = false;
  for (const direction of ["out", "in"]) {
    for (const [name, targets] of Object.entries(relations[direction])) {
      for (const target of targets) {
        anyRelation = true;
        const button = el("button", null, (direction === "out" ? name + " → " : "← " + name + " ") + target);
        button.onclick = () => selectItem(target);
        relationRow.appendChild(button);
      }
    }
  }
  if (!anyRelation) relationRow.appendChild(el("span", "hint", "No relations."));
  relationSection.appendChild(relationRow);
  panel.appendChild(relationSection);

  if (state.doc.body) {
    const section = el("div", "section");
    section.appendChild(el("h3", null, "Body"));
    section.appendChild(el("pre", "body", state.doc.body));
    panel.appendChild(section);
  }

  const raw = el("details", "section");
  raw.appendChild(el("summary", null, "Raw document and card"));
  const pre = el("pre", "body");
  pre.textContent = JSON.stringify(state.doc, null, 2);
  raw.appendChild(pre);
  panel.appendChild(raw);
}

async function selectItem(slug) {
  state.slug = slug;
  const [shown, reason] = await Promise.all([
    get("/api/doc/" + encodeURIComponent(slug)),
    get("/api/why/" + encodeURIComponent(slug)),
  ]);
  if (!shown.ok) {
    state.doc = null;
    byId("empty").hidden = false;
    const panel = byId("item");
    panel.hidden = false;
    clear(panel);
    const banner = el("div");
    panel.appendChild(banner);
    showRefusal(banner, shown);
    return;
  }
  state.doc = shown.value;
  state.reason = reason.ok ? reason.value : null;
  renderItem();
  renderItems((byId("items").dataset.items ? JSON.parse(byId("items").dataset.items) : []));
}

async function load() {
  const [context, surface] = await Promise.all([
    get("/api/context"),
    get("/api/surface"),
  ]);
  if (!context.ok) {
    byId("subtitle").textContent = "The store could not be read.";
    const badge = byId("badges");
    clear(badge);
    showRefusal(badge, context);
    return;
  }
  state.surface = surface.ok ? surface.value : null;
  const value = context.value;
  byId("subtitle").textContent =
    (state.surface ? state.surface.pack + " pack" : "pack") +
    " · " + value.total + " item" + (value.total === 1 ? "" : "s") +
    " · compiled surface";
  renderBadges(value, state.surface);
  renderCounts(value);
  renderWorkflows(state.surface, value.items);
  byId("items").dataset.items = JSON.stringify(value.items);
  renderItems(value.items);
  renderVerbs(state.surface);
  if (state.slug) await selectItem(state.slug);
}

async function runHealth() {
  const button = byId("check");
  button.disabled = true;
  const result = await get("/api/health");
  button.disabled = false;
  const target = byId("health");
  clear(target);
  if (!result.ok) {
    byId("health-summary").textContent = "";
    const banner = el("div");
    target.appendChild(banner);
    showRefusal(banner, result);
    return;
  }
  const value = result.value;
  byId("health-summary").textContent =
    value.counts.error + " error(s), " + value.counts.warning + " warning(s), " +
    value.counts.info + " info";
  if (!value.issues.length) {
    target.appendChild(el("p", "hint", "No issues. The store is consistent."));
    return;
  }
  const list = el("ul", "issues");
  for (const issue of value.issues) {
    const item = el("li");
    item.appendChild(el("b", null, issue.severity + " · " + issue.code));
    item.appendChild(document.createTextNode(" " + issue.message));
    if (issue.path) {
      item.appendChild(document.createTextNode("  "));
      item.appendChild(el("code", null, issue.path));
    }
    if (issue.fix) {
      item.appendChild(document.createTextNode("  "));
      item.appendChild(el("code", null, issue.fix));
    }
    list.appendChild(item);
  }
  target.appendChild(list);
}

async function loadTools() {
  const tools = await get("/api/tools");
  if (!tools.ok) return;
  const select = byId("tool");
  clear(select);
  for (const tool of tools.value) {
    const option = el("option", null, tool.name + " · " + tool.permission);
    option.value = tool.name;
    select.appendChild(option);
  }
}

byId("check").onclick = runHealth;
byId("run").onclick = async () => {
  let args = {};
  try { args = JSON.parse(byId("args").value || "{}"); }
  catch (error) {
    byId("result").textContent = "Arguments must be JSON: " + error.message;
    return;
  }
  const button = byId("run");
  button.disabled = true;
  const result = await post("/api/tool/" + encodeURIComponent(byId("tool").value), args);
  button.disabled = false;
  const target = byId("result");
  clear(target);
  const pre = el("pre", "body");
  pre.textContent = JSON.stringify(result, null, 2);
  target.appendChild(pre);
  if (result.ok && state.surface) await load();
};

loadTools();
load();
</script>
</body>
</html>
"""
