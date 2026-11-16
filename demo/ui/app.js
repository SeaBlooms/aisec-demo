"use strict";

const BUSINESS_DOMAINS = ["northwind.io", "acme.com", "globex.com"];
const SCORE_KEY = "aisec-demo-score";
const OUTCOME_LABEL = { compromised: "Compromised", blocked: "Blocked", resisted: "Held", clean: "Clean", learned: "Learned" };
const VERDICT_SUB = {
  compromised: "No runtime control: the model's mistake became a real action.",
  blocked: "Same fooled model. The action was outside the approved baseline, so it never ran.",
  resisted: "The model caught this one. Will it catch the next one?",
  learned: "Review the drafted policy, then approve it. Everything not in it will be denied.",
  clean: "",
};
const verdictSub = (outcome, run) => {
  if (outcome === "resisted" && run?.scenario === "mcp_rugpull") {
    return "This scenario needs the rugpull flip first — Shared doc tab, then Run under Enforce.";
  }
  return VERDICT_SUB[outcome] || "";
};
const DECISION_LABEL = { allow: "Allowed", deny: "Blocked", observe: "Observed", none: "No control" };
const MODE_BUTTON = { none: "No defense", hardened_prompt: "Harden the prompt", learn: "Learn", enforce: "Enforce default deny" };
const SCENARIO_BUTTON = {
  benign: "Benign",
  poisoned_v1: "Poisoned shared doc v1",
  poisoned_v2: "Poisoned shared doc v2",
  poisoned_tool: "Poisoned MCP tool",
  mcp_rugpull: "MCP rugpull",
};
const SCENARIO_KEYS = ["5", "6", "7", "0", "-"];
const shortModel = (label) => String(label).replace(/^Claude /, "").replace(/ \(offline\)$/, "");
const shortHash = (h) => (h ? String(h).slice(0, 12) : "");

const S = {
  cfg: null,
  mode: "none",
  model: "older",
  scenario: "poisoned_v2",
  es: null,
  queue: [],
  draining: false,
  card: null,
  tab: "score",
  docView: "visible",
  mcpDocView: "live", // approved | live — only for mcp_rugpull
  running: false,
  previewing: false,
};

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const api = async (path, opts = {}) => {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
};
const time = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");

// ---------------------------------------------------------------- controls

function renderControls() {
  const modes = $("modeButtons");
  modes.innerHTML = "";
  Object.entries(S.cfg.modes).forEach(([key, label], i) => {
    const b = el("button", key === S.mode ? "active" : "", esc(MODE_BUTTON[key] || label));
    b.dataset.mode = key;
    b.title = `${label} (key ${i + 1})`;
    b.onclick = () => setMode(key);
    modes.appendChild(b);
  });
  const models = $("modelButtons");
  models.innerHTML = "";
  Object.entries(S.cfg.models).forEach(([key, m], i) => {
    const b = el("button", key === S.model ? "active" : "", esc(shortModel(m.label)));
    b.title = `${m.label}, ${key} model (key ${i + 8})`;
    b.onclick = () => setModel(key);
    models.appendChild(b);
  });
  const scen = $("scenarioButtons");
  scen.innerHTML = "";
  Object.entries(S.cfg.scenarios).forEach(([key, label], i) => {
    const b = el("button", key === S.scenario ? "active" : "", esc(SCENARIO_BUTTON[key] || label));
    b.title = `${label} (key ${SCENARIO_KEYS[i]})`;
    b.onclick = () => setScenario(key);
    scen.appendChild(b);
  });
}

function setMode(mode) {
  S.mode = mode;
  if (mode === "learn") S.scenario = "benign";
  renderControls();
  pickDefaultTrace();
  if (S.running) return;
  if (mode === "hardened_prompt") previewHardening();
  else if (S.previewing) {
    clearLanes();
    setVerdict("idle", "Pick a defense and a threat scenario, then run the agent.");
  }
}

function hardeningCard() {
  const rules = (S.cfg.hardening_rules || []).map((r) => `<li>${esc(r)}</li>`).join("");
  return el(
    "div",
    "rules",
    `<div class="rules-head">System prompt \u00b7 security rules added</div><ul>${rules}</ul>
     <div class="rules-foot">Instructions to the model only. Every tool still works.</div>`
  );
}

function previewHardening() {
  clearLanes();
  $("agentLane").appendChild(hardeningCard());
  S.previewing = true;
  setVerdict(
    "idle",
    "Hardened prompt: these security rules are now in the agent's instructions.",
    "No runtime control. Run the agent to see whether the rules hold."
  );
}

function setModel(key) {
  if (!S.cfg.models[key]) return;
  S.model = key;
  setModelBadge(S.cfg.models[key].label, S.cfg.simulated);
  renderControls();
  pickDefaultTrace();
}

function setScenario(scenario) {
  S.scenario = scenario;
  renderControls();
  pickDefaultTrace();
  if (S.tab === "doc") renderDoc();
}

function setBusy(busy) {
  S.running = busy;
  ["runBtn", "replayBtn", "resetBtn"].forEach((id) => ($(id).disabled = busy));
}

async function loadTraces() {
  const list = await api("/api/traces");
  const sel = $("traceSelect");
  const current = sel.value;
  sel.innerHTML = "";
  if (!list.length) sel.appendChild(el("option", "", "No recorded traces yet"));
  for (const t of list) {
    const o = el("option");
    o.value = t.id;
    const star = t.kind === "curated" ? "\u2605 " : "";
    const sim = t.simulated ? " (sim)" : "";
    const model = t.mode !== "learn" && t.model_key ? ` \u00b7 ${t.model_key}` : "";
    o.textContent = `${star}${S.cfg.modes[t.mode] || t.mode} \u00b7 ${t.scenario}${model} \u2192 ${t.outcome || "?"}${sim}`;
    o.dataset.mode = t.mode;
    o.dataset.scenario = t.scenario;
    o.dataset.model = t.model_key || "";
    o.dataset.kind = t.kind;
    sel.appendChild(o);
  }
  if (!pickDefaultTrace() && current && [...sel.options].some((o) => o.value === current)) sel.value = current;
}

function pickDefaultTrace() {
  const sel = $("traceSelect");
  const opts = [...sel.options];
  const same = (o) => o.dataset.mode === S.mode && o.dataset.scenario === S.scenario;
  const sameModel = (o) => same(o) && (S.mode === "learn" || o.dataset.model === S.model);
  const match =
    opts.find((o) => o.dataset.kind === "curated" && sameModel(o)) ||
    opts.find(sameModel) ||
    opts.find((o) => o.dataset.kind === "curated" && same(o)) ||
    opts.find(same);
  if (match) sel.value = match.value;
  return Boolean(match);
}

// ---------------------------------------------------------------- runs

function clearLanes() {
  $("agentLane").innerHTML = "";
  $("actionLane").innerHTML = "";
  S.card = null;
  S.queue = [];
  S.previewing = false;
}

function setVerdict(cls, text, sub = "") {
  const v = $("verdict");
  v.className = `verdict ${cls}`;
  $("verdictText").textContent = text;
  $("verdictSub").textContent = sub;
}

function listen(runId) {
  if (S.es) S.es.close();
  setBusy(true);
  const es = new EventSource(`/api/runs/${runId}/events`);
  S.es = es;
  es.onmessage = (m) => enqueue(JSON.parse(m.data));
  es.addEventListener("end", () => {
    es.close();
    enqueue({ type: "_end" });
  });
  es.onerror = () => {
    es.close();
    enqueue({ type: "_end" });
  };
}

async function startRun() {
  if (S.running) return;
  clearLanes();
  setVerdict("running", "Starting the agent\u2026");
  try {
    const { run_id } = await api("/api/runs", { method: "POST", body: { mode: S.mode, scenario: S.scenario, model: S.model } });
    listen(run_id);
  } catch (e) {
    setVerdict("error", `Could not start run: ${e.message}`);
  }
}

async function startReplay() {
  if (S.running) return;
  const trace = $("traceSelect").value;
  if (!trace || !trace.includes("/")) return;
  clearLanes();
  setVerdict("running", "Replaying recorded run\u2026");
  try {
    const { run_id } = await api("/api/replay", {
      method: "POST",
      body: { trace, speed: Number($("speedSelect").value) },
    });
    listen(run_id);
  } catch (e) {
    setVerdict("error", `Could not replay: ${e.message}`);
  }
}

function enqueue(ev) {
  S.queue.push(ev);
  if (!S.draining) drain();
}

async function drain() {
  S.draining = true;
  while (S.queue.length) {
    const ev = S.queue.shift();
    handle(ev);
    const slow = ["tool_call", "policy_decision", "user_prompt", "agent_final"].includes(ev.type);
    await new Promise((r) => setTimeout(r, slow ? 380 : 90));
  }
  S.draining = false;
}

function addAgent(cls, text) {
  const lane = $("agentLane");
  lane.appendChild(el("div", `bubble ${cls}`, esc(text)));
  lane.scrollTop = lane.scrollHeight;
}

function addCard(card) {
  const lane = $("actionLane");
  lane.appendChild(card);
  lane.scrollTop = lane.scrollHeight;
}

function handle(ev) {
  switch (ev.type) {
    case "run_started": {
      $("replayBadge").classList.toggle("hidden", !ev.replay);
      const label = ev.model_label || ev.model;
      setModelBadge(label, ev.simulated);
      const scen = S.cfg.scenarios[ev.scenario] || ev.scenario;
      setVerdict("running", `Running: ${ev.mode_label}`, `${scen} \u00b7 ${label}`);
      if (ev.mode === "learn") addCard(el("div", "card note", "Learn mode: every action is recorded to the behavior ledger. Nothing is blocked."));
      if (ev.mode === "hardened_prompt") {
        $("agentLane").appendChild(hardeningCard());
        addCard(el("div", "card note", "Fix the model: security rules added to the system prompt. No runtime control."));
      }
      if (ev.mode === "none") addCard(el("div", "card note", "No defenses: whatever the model decides, the tools do."));
      break;
    }
    case "policy_loaded":
      addCard(
        el(
          "div",
          "card note",
          ev.version
            ? `Default deny is ON. Approved policy v${ev.version}: ${ev.rules} allow rule(s). Everything else is blocked.`
            : "Default deny is ON, but no policy is approved yet: every action will be blocked."
        )
      );
      break;
    case "user_prompt":
      addAgent("user", ev.text);
      break;
    case "agent_text":
      addAgent("thinking", ev.text);
      break;
    case "agent_final":
      addAgent("final", ev.text);
      break;
    case "tool_call": {
      const c = ev.call;
      const card = el("div", `card${ev.exfiltration ? " exfil" : ""}`);
      const unvetted = String(c.resource).startsWith("unknown:");
      const hash = c.tool_hash ? ` \u00b7 sha256:${esc(shortHash(c.tool_hash))}` : "";
      card.innerHTML = `<div class="what">${esc(ev.plain)}</div>
        <div class="meta"><span class="chip ${c.action_class}">${unvetted ? "unvetted tool" : c.action_class}</span>
        <span class="mono">${esc(c.tool)} \u00b7 ${esc(c.resource)}${c.destinations.length ? " \u2192 " + esc(c.destinations.join(", ")) : ""}${hash}</span></div>`;
      S.card = card;
      addCard(card);
      break;
    }
    case "policy_decision": {
      if (!S.card) break;
      S.card.classList.add(`v-${ev.effect}`);
      const meta = S.card.querySelector(".meta");
      meta.insertAdjacentHTML("afterbegin", `<span class="chip ${ev.effect}">${DECISION_LABEL[ev.effect] || ev.effect}</span>`);
      S.card.insertAdjacentHTML("beforeend", `<div class="reason">${esc(ev.reason)}</div>`);
      break;
    }
    case "sandbox_write":
      if (S.card) S.card.querySelector(".meta").insertAdjacentHTML("beforeend", `<span class="chip exec">executed</span>`);
      refreshCounts();
      if (S.tab === "outbox") renderOutbox();
      break;
    case "ledger_entry":
      if (S.tab === "ledger") renderLedger();
      break;
    case "exception_requested":
      refreshCounts();
      if (S.tab === "exceptions") renderExceptions();
      break;
    case "policy_drafted":
      if (S.tab === "policy") renderPolicy();
      break;
    case "run_error":
      addAgent("error", ev.error);
      setVerdict("error", "The run hit an error", ev.error);
      break;
    case "run_finished": {
      if (ev.error) break;
      setVerdict(ev.outcome, ev.verdict, verdictSub(ev.outcome, S.lastStarted));
      recordScore(S.lastStarted || {}, ev.outcome);
      const next = { learned: "policy", blocked: "exceptions", compromised: "outbox" }[ev.outcome];
      if (next) showTab(next);
      else if (S.tab === "score") renderScore();
      break;
    }
    case "_end":
      setBusy(false);
      refreshCounts();
      loadTraces();
      break;
  }
  if (ev.type === "run_started") S.lastStarted = { mode: ev.mode, scenario: ev.scenario, model: ev.model_key, label: ev.model_label };
}

// ---------------------------------------------------------------- scoreboard

const loadScore = () => JSON.parse(localStorage.getItem(SCORE_KEY) || "{}");

function recordScore(run, outcome) {
  if (!run.scenario || !run.model) return;
  const scored = run.scenario.startsWith("poisoned") || run.scenario === "mcp_rugpull";
  if (!scored) return;
  if (!["none", "hardened_prompt", "enforce"].includes(run.mode)) return;
  const s = loadScore();
  s[`${run.mode}|${run.model}|${run.scenario}`] = outcome;
  localStorage.setItem(SCORE_KEY, JSON.stringify(s));
}

function renderScore() {
  const s = loadScore();
  const name = (key) => shortModel(S.cfg.models[key]?.label || key);
  const rows = [
    ["none", "older", "No defense", name("older")],
    ["hardened_prompt", "older", "Harden the prompt", name("older")],
    ["none", "newer", "Upgrade the model", name("newer")],
    ["enforce", null, "Control the action", "default deny \u00b7 either model"],
  ];
  const cols = ["poisoned_v1", "poisoned_v2", "poisoned_tool", "mcp_rugpull"];
  const outcome = (mode, model, sc) => {
    if (model) return s[`${mode}|${model}|${sc}`];
    const seen = ["older", "newer"].map((k) => s[`${mode}|${k}|${sc}`]).filter(Boolean);
    return seen.includes("blocked") ? "blocked" : seen[0];
  };
  const cell = (mode, model, sc) => {
    const o = outcome(mode, model, sc);
    return `<td class="cell ${o || ""}">${o ? OUTCOME_LABEL[o] : "\u2013"}</td>`;
  };
  $("panel-score").innerHTML = `
    <h3>Fix the model vs. control the action</h3>
    <table class="score">
      <tr><th></th><th>Shared doc v1<br><small>blunt</small></th><th>Shared doc v2<br><small>disguised</small></th><th>MCP tool<br><small>unvetted</small></th><th>MCP rugpull<br><small>approved, then changed</small></th></tr>
      ${rows
        .map(
          ([m, model, label, sub]) =>
            `<tr><td class="rowlabel">${label}<small>${sub}</small></td>${cols.map((sc) => cell(m, model, sc)).join("")}</tr>`
        )
        .join("")}
    </table>
    <div class="takeaway">Hardened prompts and model upgrades are signatures: they catch the attacks someone anticipated.
      Default deny doesn't need to recognize the attack. It only needs to know what the agent is allowed to do — including the hash of the tool that was approved.</div>
    <p class="hint"><a href="#" id="clearScore" style="color:var(--muted)">Clear scoreboard</a></p>`;
  $("clearScore").onclick = (e) => {
    e.preventDefault();
    localStorage.removeItem(SCORE_KEY);
    renderScore();
  };
}

// ---------------------------------------------------------------- panels

async function renderDoc() {
  const d = await api(`/api/doc?scenario=${S.scenario}`);
  const rugpull = S.scenario === "mcp_rugpull" && d.mcp_tool;
  let body;
  let hiddenNote;
  let toggle;
  if (rugpull) {
    const side = S.mcpDocView === "approved" ? d.mcp_tool.approved : d.mcp_tool.live;
    const label = S.mcpDocView === "approved" ? "Approved definition" : "What the agent reads now";
    body = `<p class="hint">${esc(label)} \u00b7 sha256:${esc(side.short_hash)} \u00b7 version <b>${esc(d.mcp_tool.version)}</b>${
      d.mcp_tool.drifted ? ' <span class="chip deny">definition changed</span>' : ""
    }</p><pre class="doc">${esc(side.description)}</pre>`;
    hiddenNote = d.mcp_tool.drifted
      ? `<p class="hint">Approved sha256:${esc(d.mcp_tool.approved.short_hash)} vs live sha256:${esc(d.mcp_tool.live.short_hash)}. Same tool name; different program. Enforce + Run should <b>Block</b> on the hash mismatch.</p>`
      : `<p class="hint"><b>Still benign</b> — the model will not call this tool (description says not to send contacts). Do not approve enrich_contacts from the Exceptions tab (that pins the <i>rugpulled</i> hash). Click <b>Approve by name, then rugpull</b>, then Run under Enforce.</p>`;
    toggle = `
      <div class="toggle seg">
        <button id="mcpApproved" class="${S.mcpDocView === "approved" ? "active" : ""}">Approved definition</button>
        <button id="mcpLive" class="${S.mcpDocView === "live" ? "active" : ""}">What the agent reads now</button>
      </div>
      <div class="row" style="margin:10px 0;gap:8px;flex-wrap:wrap">
        <button id="rugpullBtn">Rugpull the tool</button>
        <button id="approveRugpullBtn" class="approve">Approve by name, then rugpull</button>
        <button id="resetToolBtn">Reset to benign</button>
      </div>`;
  } else {
    if (S.docView === "visible") {
      body = esc(d.visible);
    } else {
      body = esc(d.raw);
      for (const h of d.hidden) body = body.replace(esc(h), `<mark>${esc(h)}</mark>`);
    }
    hiddenNote = d.hidden.length
      ? `<p class="hint">${d.hidden.length} hidden passage(s): invisible to the CSM, but read by the agent.</p>`
      : `<p class="hint">No hidden content in this version.</p>`;
    toggle = `
      <div class="toggle seg">
        <button id="docVisible" class="${S.docView === "visible" ? "active" : ""}">What the CSM sees</button>
        <button id="docRaw" class="${S.docView === "raw" ? "active" : ""}">What the agent reads</button>
      </div>`;
    body = `<div class="doc">${body}</div>`;
  }
  $("panel-doc").innerHTML = `${toggle}${hiddenNote}${body}`;
  if (rugpull) {
    $("mcpApproved").onclick = () => ((S.mcpDocView = "approved"), renderDoc());
    $("mcpLive").onclick = () => ((S.mcpDocView = "live"), renderDoc());
    $("rugpullBtn").onclick = async () => {
      try {
        await api("/api/mcp-tool", { method: "POST", body: { action: "rugpull" } });
        S.mcpDocView = "live";
        renderDoc();
      } catch (e) {
        alert(e.message);
      }
    };
    $("approveRugpullBtn").onclick = async () => {
      try {
        await api("/api/mcp-tool", { method: "POST", body: { action: "approve_then_rugpull" } });
        S.mcpDocView = "live";
        renderDoc();
        if (S.tab === "policy") renderPolicy();
        refreshCounts();
      } catch (e) {
        alert(e.message);
      }
    };
    $("resetToolBtn").onclick = async () => {
      try {
        await api("/api/mcp-tool", { method: "POST", body: { action: "set", version: "benign" } });
        S.mcpDocView = "approved";
        renderDoc();
      } catch (e) {
        alert(e.message);
      }
    };
  } else {
    $("docVisible").onclick = () => ((S.docView = "visible"), renderDoc());
    $("docRaw").onclick = () => ((S.docView = "raw"), renderDoc());
    const mark = document.querySelector("#panel-doc mark");
    if (mark) $("panel-doc").scrollTop = mark.offsetTop - 120;
  }
}

async function renderLedger() {
  const entries = await api("/api/ledger");
  const groups = {};
  for (const e of entries) {
    const k = `${e.tool}|${e.action_class}|${e.resource}`;
    groups[k] = groups[k] || { ...e, count: 0 };
    groups[k].count++;
  }
  const rows = Object.values(groups)
    .map(
      (g) => `<tr><td>${esc(g.tool)}</td><td><span class="chip ${g.action_class}">${g.action_class}</span></td>
      <td class="mono">${esc(g.resource)}${g.destinations?.length ? " \u2192 " + esc(g.destinations.join(", ")) : ""}</td><td>${g.count}</td></tr>`
    )
    .join("");
  $("panel-ledger").innerHTML = `
    <h3>Observed behavior (learn mode)</h3>
    <p class="hint">What the agent actually did during normal work. This becomes the baseline.</p>
    ${
      entries.length
        ? `<table class="list"><tr><th>Tool</th><th>Action</th><th>Resource</th><th>#</th></tr>${rows}</table>
           <p><button id="redraft">Draft policy from ledger</button></p>`
        : `<p class="hint">Empty. Run the agent in <b>Learn mode</b> first.</p>`
    }`;
  const b = $("redraft");
  if (b)
    b.onclick = async () => {
      await api("/api/policy/draft", { method: "POST" });
      showTab("policy");
    };
}

function ruleText(r) {
  let t = `${r.action_classes.join("/").toUpperCase()} via ${r.tools.join(", ")} on ${r.resources.join(", ")}`;
  if (r.destinations && r.destinations.length) t += ` \u2192 only to ${r.destinations.join(", ")}`;
  if (r.tool_hashes && r.tool_hashes.length) t += ` \u00b7 sha256:${shortHash(r.tool_hashes[0])}`;
  return t;
}

function ruleHtml(r, cls = "") {
  const exp = r.expires_at ? ` \u00b7 expires ${time(r.expires_at)}` : "";
  const why = r.justification ? ` \u00b7 ${esc(r.justification)}` : "";
  const pins =
    r.tool_hashes && r.tool_hashes.length
      ? ` \u00b7 pinned ${r.tool_hashes.map((h) => `sha256:${shortHash(h)}`).join(", ")}`
      : "";
  return `<div class="rule ${cls}"><div class="head">${cls === "proposed" ? "NEEDS JUSTIFICATION" : "ALLOW"}: ${esc(ruleText(r))}</div>
    <div class="mono">${esc(r.id)} \u00b7 ${esc(r.source)}${why}${pins}${exp}</div></div>`;
}

async function renderPolicy() {
  const { approved, draft } = await api("/api/policy");
  const defaultRule = `<div class="rule default"><div class="head">DENY: everything else (create, update, delete, unknown tools, other destinations)</div></div>`;
  let html = "";
  if (draft) {
    html += `<h3>Draft policy from learn mode</h3>
      <p class="hint">Reads the agent actually performed are proposed as allowed. Writes are never auto-allowed.</p>
      ${draft.rules.map((r) => ruleHtml(r)).join("")}
      ${draft.proposed.map((r) => ruleHtml(r, "proposed")).join("")}
      ${defaultRule}
      <p><button id="approvePolicy" class="approve">Approve policy</button></p>
      <details><summary class="mono">YAML</summary><pre>${esc(draft.yaml)}</pre></details>`;
  }
  if (approved) {
    html += `<h3>Approved policy v${approved.version}</h3>
      <p class="hint">Approved by ${esc(approved.approved_by)} at ${time(approved.approved_at)}. Enforced in "Enforce default deny" mode.</p>
      ${approved.rules.map((r) => ruleHtml(r)).join("")}
      ${defaultRule}
      <details><summary class="mono">YAML</summary><pre>${esc(approved.yaml)}</pre></details>`;
  } else if (!draft) {
    html += `<h3>No policy yet</h3><p class="hint">Run <b>Learn mode</b> to observe the agent and draft a baseline.</p>${defaultRule}`;
  }
  $("panel-policy").innerHTML = html;
  const b = $("approvePolicy");
  if (b)
    b.onclick = async () => {
      try {
        await api("/api/policy/approve", { method: "POST" });
        renderPolicy();
      } catch (e) {
        alert(e.message);
      }
    };
}

async function renderExceptions() {
  const items = await api("/api/exceptions");
  if (!items.length) {
    $("panel-exceptions").innerHTML = `<h3>Exception requests</h3><p class="hint">None. Blocked write actions show up here for review.</p>`;
    return;
  }
  const html = items
    .map((x) => {
      const dest = x.destinations.length ? ` to <b>${esc(x.destinations.join(", "))}</b>` : "";
      const head = `<div class="what"><b>${esc(x.tool)}</b> <span class="chip ${x.action_class}">${x.action_class}</span>${dest}</div>
        <div class="mono">${esc(x.deny_reason)}</div>`;
      if (x.status !== "pending") {
        const scope = x.approved_scope ? ` \u00b7 scope ${esc(x.approved_scope.join(", "))}` : "";
        const exp = x.expires_at ? ` \u00b7 until ${time(x.expires_at)}` : "";
        return `<div class="exc ${x.status}">${head}<div class="row"><span class="chip ${x.status === "approved" ? "allow" : "deny"}">${x.status}</span>
          <span class="mono">${esc(x.justification)}${scope}${exp}</span></div></div>`;
      }
      const destDefault = x.tool === "enrich_contacts" ? "contact-enrich.io" : "acme.com";
      const hashNote = x.tool_hash
        ? `<p class="hint mono">Pins tool definition sha256:${esc(shortHash(x.tool_hash))}</p>`
        : "";
      return `<div class="exc" data-id="${esc(x.id)}">${head}${hashNote}
        <details><summary class="mono">What the agent tried to send</summary><pre>${esc(x.args_preview)}</pre></details>
        <textarea placeholder="Business justification (required), e.g. Send QBR invite to Acme stakeholders, ticket CS-1042"></textarea>
        <div class="row"><span class="mono">Allow only to</span><input value="${esc(destDefault)}" title="Destination domains (comma-separated)" />
          <select><option value="15">15 min</option><option value="60" selected>1 hour</option><option value="1440">1 day</option></select></div>
        <div class="row"><button class="approve">Approve narrow exception</button><button class="reject">Reject</button></div>
        <p class="hint">Approving does not open the requested destination unless it is inside the scope you set.</p></div>`;
    })
    .join("");
  $("panel-exceptions").innerHTML = `<h3>Exception requests</h3>${html}`;
  document.querySelectorAll("#panel-exceptions .exc[data-id]").forEach((card) => {
    const id = card.dataset.id;
    const decide = async (approve) => {
      try {
        await api(`/api/exceptions/${id}`, {
          method: "POST",
          body: {
            approve,
            justification: card.querySelector("textarea").value,
            destination_scope: card.querySelector("input").value.split(",").map((s) => s.trim()).filter(Boolean),
            ttl_minutes: Number(card.querySelector("select").value),
          },
        });
        renderExceptions();
        refreshCounts();
      } catch (e) {
        alert(e.message);
      }
    };
    card.querySelector(".approve").onclick = () => decide(true);
    card.querySelector(".reject").onclick = () => decide(false);
  });
}

async function renderOutbox() {
  const mails = await api("/api/outbox");
  $("panel-outbox").innerHTML =
    `<h3>Outbox (sandbox)</h3><p class="hint">What the agent actually sent. Nothing leaves this laptop.</p>` +
    (mails.length
      ? mails
          .map((m) => {
            const domains = (String(m.to).match(/@([\w.-]+)/g) || []).map((d) => d.slice(1).toLowerCase());
            const outside = m.outside || domains.some((d) => !BUSINESS_DOMAINS.includes(d));
            return `<div class="mail ${outside ? "outside" : ""}"><b>To:</b> ${esc(m.to)}${outside ? ' <span class="chip deny">outside</span>' : ""}
              <br><b>Subject:</b> ${esc(m.subject)}<div class="body">${esc(m.body)}</div></div>`;
          })
          .join("")
      : `<p class="hint">Empty.</p>`);
}

async function renderAudit() {
  const entries = await api("/api/audit");
  $("panel-audit").innerHTML =
    `<h3>Audit log</h3><p class="hint">Every decision, approval and exception. This is what you hand to compliance.</p>` +
    (entries.length
      ? `<table class="list"><tr><th>Time</th><th>Event</th><th>Detail</th></tr>${entries
          .slice(0, 80)
          .map(
            (e) =>
              `<tr><td class="mono">${time(e.ts)}</td><td>${esc(e.kind)}${e.effect ? ` <span class="chip ${e.effect}">${e.effect}</span>` : ""}</td>
              <td class="mono">${esc(e.tool || "")} ${esc(e.reason || e.justification || "")}</td></tr>`
          )
          .join("")}</table>`
      : `<p class="hint">Empty.</p>`);
}

const RENDER = {
  score: renderScore,
  doc: renderDoc,
  ledger: renderLedger,
  policy: renderPolicy,
  exceptions: renderExceptions,
  outbox: renderOutbox,
  audit: renderAudit,
};

function showTab(tab) {
  S.tab = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("hidden", p.id !== `panel-${tab}`));
  RENDER[tab]();
}

async function refreshCounts() {
  const [exc, out] = await Promise.all([api("/api/exceptions"), api("/api/outbox")]);
  const pending = exc.filter((x) => x.status === "pending").length;
  $("excCount").textContent = pending;
  $("excCount").classList.toggle("hidden", !pending);
  $("outCount").textContent = out.length;
  $("outCount").classList.toggle("hidden", !out.length);
}

async function resetDemo() {
  if (!confirm("Clear the policy, ledger, exceptions, audit log, outbox and scoreboard?")) return;
  await api("/api/reset", { method: "POST", body: { keep_policy: false } });
  localStorage.removeItem(SCORE_KEY);
  clearLanes();
  setVerdict("idle", "Reset. Start with Learn mode to build a baseline.");
  refreshCounts();
  showTab(S.tab);
}

// ---------------------------------------------------------------- boot

function setModelBadge(label, simulated) {
  const badge = $("modelBadge");
  badge.textContent = label;
  badge.classList.toggle("sim", Boolean(simulated));
}

async function boot() {
  S.cfg = await api("/api/config");
  S.model = S.cfg.default_model;
  setModelBadge(S.cfg.models[S.model].label, S.cfg.simulated);
  if (!S.cfg.simulated && !S.cfg.api_key_present) {
    setVerdict("error", "No ANTHROPIC_API_KEY: live runs will fail. Use Replay.", "Add the key to .env and restart.");
  }
  renderControls();
  $("runBtn").onclick = startRun;
  $("replayBtn").onclick = startReplay;
  $("resetBtn").onclick = resetDemo;
  document.querySelectorAll("#tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  document.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A", "SUMMARY"].includes(e.target.tagName)) return;
    const modes = Object.keys(S.cfg.modes);
    const scens = Object.keys(S.cfg.scenarios);
    const models = Object.keys(S.cfg.models);
    if (e.key >= "1" && e.key <= "4") setMode(modes[Number(e.key) - 1]);
    else if (SCENARIO_KEYS.includes(e.key) && scens[SCENARIO_KEYS.indexOf(e.key)]) setScenario(scens[SCENARIO_KEYS.indexOf(e.key)]);
    else if (e.key >= "8" && e.key <= "9") setModel(models[Number(e.key) - 8]);
    else if (e.key === "Enter") startRun();
    else if (e.key === "r") startReplay();
  });
  await loadTraces();
  refreshCounts();
  showTab("score");
  autoplay(new URLSearchParams(location.search));
}

// ?mode=enforce&model=older&scenario=poisoned_v2&replay=curated/enforce__poisoned_v2__older.json&speed=4&tab=policy
function autoplay(params) {
  if (params.get("mode")) setMode(params.get("mode"));
  if (params.get("model")) setModel(params.get("model"));
  if (params.get("scenario")) setScenario(params.get("scenario"));
  if (params.get("speed")) $("speedSelect").value = params.get("speed");
  if (params.get("tab")) showTab(params.get("tab"));
  const trace = params.get("replay");
  if (trace) {
    if (![...$("traceSelect").options].some((o) => o.value === trace)) $("traceSelect").appendChild(new Option(trace, trace));
    $("traceSelect").value = trace;
    startReplay();
  }
}

boot();
