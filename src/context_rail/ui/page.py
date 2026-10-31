"""One self-contained page: no build step, no external asset, no stored state."""

from __future__ import annotations

__all__ = ["PAGE"]

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Context Rail</title>
<style>
  :root { color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }
  body { margin: 0; display: grid; grid-template-columns: 18rem 1fr; min-height: 100vh; }
  aside { padding: 1rem; border-right: 1px solid #8884; }
  main { padding: 1rem 1.5rem; display: grid; gap: 1rem; align-content: start; }
  h1 { font-size: 1.1rem; margin: 0 0 .75rem; }
  h2 { font-size: .95rem; margin: 1rem 0 .35rem; text-transform: uppercase; letter-spacing: .05em; }
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: .25rem; }
  li button { width: 100%; text-align: left; padding: .35rem .5rem; border: 0; border-radius: .3rem; background: #8881; cursor: pointer; }
  li button:hover { background: #8883; }
  .counts { display: flex; gap: .5rem; flex-wrap: wrap; }
  .pill { border: 1px solid #8886; border-radius: 999px; padding: .15rem .55rem; font-size: .8rem; }
  pre, .doc { white-space: pre-wrap; overflow-wrap: anywhere; background: #8881; padding: .75rem; border-radius: .4rem; }
  form { display: grid; gap: .5rem; }
  textarea { min-height: 5rem; font-family: ui-monospace, monospace; }
  .row { display: flex; gap: .5rem; align-items: center; }
  .err { color: #c33; }
  button.primary { padding: .4rem .8rem; border-radius: .3rem; border: 0; background: #36c; color: white; cursor: pointer; }
</style>
</head>
<body>
<aside>
  <h1>Context Rail</h1>
  <div id="counts" class="counts"></div>
  <h2>Items</h2>
  <ul id="items"></ul>
  <h2>Tool</h2>
  <form id="tool-form">
    <select id="tool"></select>
    <textarea id="args" spellcheck="false">{}</textarea>
    <button class="primary" type="submit">Run</button>
  </form>
</aside>
<main>
  <div class="row"><h2 style="margin:0">Context</h2><span id="stamp" class="pill"></span></div>
  <div id="context" class="doc">Loading...</div>
  <h2>Document</h2>
  <div id="document" class="doc">Pick an item.</div>
  <h2>Result</h2>
  <pre id="result"></pre>
</main>
<script>
const token = new URLSearchParams(location.search).get("token") || "";
const url = (path) => path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
const el = (id) => document.getElementById(id);
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
function show(id, value) {
  const target = el(id);
  target.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
async function load() {
  const context = await get("/api/context");
  if (!context.ok) { show("context", context.error); return; }
  const value = context.value;
  el("counts").innerHTML = "";
  for (const [name, count] of Object.entries(value.status.counts || {})) {
    const pill = document.createElement("span");
    pill.className = "pill"; pill.textContent = name + ": " + count;
    el("counts").appendChild(pill);
  }
  el("stamp").textContent = value.generated_at;
  show("context", value.status);
  el("items").innerHTML = "";
  for (const item of value.items) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.textContent = item.title + "  (" + item.status + ")";
    button.onclick = async () => show("document", await get("/api/doc/" + encodeURIComponent(item.slug)));
    li.appendChild(button); el("items").appendChild(li);
  }
  const tools = await get("/api/tools");
  if (tools.ok) {
    el("tool").innerHTML = "";
    for (const tool of tools.value) {
      const option = document.createElement("option");
      option.value = tool.name; option.textContent = tool.name + " · " + tool.permission;
      el("tool").appendChild(option);
    }
  }
}
el("tool-form").onsubmit = async (event) => {
  event.preventDefault();
  let args = {};
  try { args = JSON.parse(el("args").value || "{}"); }
  catch (error) { show("result", "Arguments must be JSON: " + error.message); return; }
  const name = el("tool").value;
  const result = await post("/api/tool/" + encodeURIComponent(name), args);
  show("result", result);
  if (name === "add" || name === "move") load();
};
load();
</script>
</body>
</html>
"""
