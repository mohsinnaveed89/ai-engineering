/* KYC Onboarding web app: no build step, vanilla JS. Talks to the API through /api (see nginx.conf). */
const API = window.KYC_API || "/api";
const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const COUNTRY = { UK: "United Kingdom", PT: "Portugal", BR: "Brazil" };
const STATUS = { queued: "Queued", running: "Running", awaiting_review: "Awaiting review", sent_back: "Sent back", approved: "Approved", declined: "Declined", failed: "Failed" };
let refreshTimer = null, eventSource = null;

async function api(path, opts = {}) {
  const r = await fetch(API + path, { headers: { "content-type": "application/json" }, ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}
const statusPill = (s) => `<span class="pill s-${esc(s)}">${s === "running" || s === "queued" ? '<span class="spinner"></span>' : ""}${esc(STATUS[s] || s)}</span>`;
const fmtTime = (t) => (t ? new Date(t).toLocaleString() : "");
// Source references point at service names inside Docker (e.g. http://mockdata:8001). Rewrite them so they open from the browser.
const openable = (ref) => String(ref || "").replace(/^http:\/\/mockdata:8001/, `${location.protocol}//${location.hostname}:8001`);

async function health() {
  try {
    const h = await api("/health");
    const ok = h.status === "ok";
    $("#health").innerHTML = `<span class="dot" style="background:${ok ? "#16a34a" : "#d97706"}"></span>${ok ? "All services up" : "Degraded: " + Object.entries(h).filter(([k, v]) => v === false).map(([k]) => k).join(", ")}`;
  } catch (e) {
    $("#health").innerHTML = `<span class="dot" style="background:#dc2626"></span>API unreachable`;
  }
}

/* ---------------- Home: submit + recent runs ---------------- */
async function home() {
  $("#app").innerHTML = `
    <h1>KYC onboarding packs</h1>
    <p class="sub">Submit a company, follow the agents as they work, then review the pack. The AI recommends; you decide.</p>
    <div class="grid">
      <form class="card" id="newRun">
        <div class="h"><h2>New check</h2></div>
        <div class="b">
          <label for="country">Country</label>
          <select id="country" required><option value="UK">United Kingdom</option><option value="PT">Portugal</option><option value="BR">Brazil</option></select>
          <label for="company">Company name</label>
          <input id="company" list="companyList" required minlength="2" placeholder="Start typing a company name" autocomplete="off">
          <datalist id="companyList"></datalist>
          <button class="btn full" type="submit">Start KYC check</button>
          <p class="muted" id="formMsg" style="margin:10px 0 0"></p>
        </div>
      </form>
      <div class="card">
        <div class="h"><h2>Recent runs</h2><span class="muted" style="margin-left:auto">Refreshes every 5s</span></div>
        <div class="b" style="padding-top:4px"><div id="runs"><div class="empty">Loading…</div></div></div>
      </div>
    </div>`;
  const fillSuggestions = async () => {
    try {
      const { items } = await api(`/companies?country=${$("#country").value}`);
      $("#companyList").innerHTML = items.map((c) => `<option value="${esc(c.name)}">`).join("");
    } catch (e) {}
  };
  $("#country").addEventListener("change", fillSuggestions);
  fillSuggestions();
  $("#newRun").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#formMsg").textContent = "Starting…";
    try {
      const run = await api("/runs", { method: "POST", body: JSON.stringify({ company_name: $("#company").value, country: $("#country").value }) });
      location.hash = `#/runs/${run.id}`;
    } catch (err) { $("#formMsg").textContent = err.message; }
  });
  const load = async () => {
    try {
      const { items } = await api("/runs?limit=50");
      $("#runs").innerHTML = items.length ? `<table><thead><tr><th>Company</th><th>Country</th><th>Status</th><th>Risk</th><th>Recommendation</th><th>Updated</th></tr></thead><tbody>
        ${items.map((r) => `<tr class="click" data-id="${r.id}"><td><b>${esc(r.company_name)}</b>${r.version > 1 ? ` <span class="muted">v${r.version}</span>` : ""}</td><td>${esc(r.country)}</td><td>${statusPill(r.status)}</td>
        <td>${r.risk_rating ? `<span class="pill r-${esc(r.risk_rating)}">${esc(r.risk_rating)}</span>` : ""}</td><td>${esc((r.recommendation || "").replace("_", " "))}</td><td class="muted">${fmtTime(r.updated_at)}</td></tr>`).join("")}
        </tbody></table>` : `<div class="empty">No runs yet. Submit a company to start.</div>`;
      document.querySelectorAll("tr.click").forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/runs/${tr.dataset.id}`)));
    } catch (e) { $("#runs").innerHTML = `<div class="empty">Could not load runs: ${esc(e.message)}</div>`; }
  };
  await load();
  refreshTimer = setInterval(load, 5000);
}

/* ---------------- Run view ---------------- */
async function runView(id) {
  $("#app").innerHTML = `<p><a href="#/">← All runs</a></p><div id="runHead"></div>
    <div class="grid run">
      <div id="pack"><div class="card"><div class="b empty">Waiting for the pack…</div></div></div>
      <div>
        <div class="card" id="reviewCard"></div>
        <div class="card section"><div class="h"><h2>Progress</h2></div><div class="b timeline" id="timeline"></div></div>
      </div>
    </div>`;
  let run = await api(`/runs/${id}`);
  let shownVersion = null;
  const render = () => {
    $("#runHead").innerHTML = `<h1>${esc(run.company_name)}</h1>
      <p class="sub">${esc(COUNTRY[run.country])} · ${statusPill(run.status)} · version ${run.version}${run.pack ? ` · generated by <code>${esc(run.pack.generated_by)}</code>` : ""}</p>
      ${run.error ? `<div class="warnbox"><b>Run failed.</b> ${esc(run.error)}</div>` : ""}`;
    renderTimeline(run.events || []);
    renderReview(run);
    const versions = [...(run.pack_history || []), run.pack].filter(Boolean);
    if (!versions.length) return;
    const idx = shownVersion == null ? versions.length - 1 : shownVersion;
    $("#pack").innerHTML = (versions.length > 1 ? `<div class="rowflex" style="margin-bottom:12px">${versions.map((v, i) => `<button class="btn ${i === idx ? "" : "line"}" data-v="${i}">v${i + 1}${i === versions.length - 1 ? " (latest)" : ""}</button>`).join("")}</div>` : "")
      + renderPack(versions[idx], run.events || []);
    $("#pack").querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", () => { shownVersion = +b.dataset.v; render(); }));
    $("#pack").querySelectorAll(".src[data-src]").forEach((s) => s.addEventListener("click", () => {
      const el = document.getElementById("source-" + s.dataset.src);
      if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.remove("hl"); void el.offsetWidth; el.classList.add("hl"); }
    }));
  };
  render();
  const subscribe = () => {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(`${API}/runs/${id}/stream`);
    eventSource.onmessage = async (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "end") { eventSource.close(); run = await api(`/runs/${id}`); render(); return; }
      run.events = [...(run.events || []).filter((e) => e.ts !== ev.ts || e.message !== ev.message), ev];
      if (ev.type === "status") { run.status = ev.status; }
      render();
    };
    eventSource.onerror = () => { eventSource.close(); setTimeout(async () => { run = await api(`/runs/${id}`); render(); if (["queued", "running", "sent_back"].includes(run.status)) subscribe(); }, 2000); };
  };
  if (["queued", "running", "sent_back"].includes(run.status)) subscribe();
  window.__rerender = async () => { run = await api(`/runs/${id}`); shownVersion = null; render(); if (["queued", "running", "sent_back"].includes(run.status)) subscribe(); };
}

function renderTimeline(events) {
  const el = $("#timeline");
  if (!el) return;
  el.innerHTML = events.length ? events.map((e) => `<div class="ev ${esc(e.type)}"><span class="t">${new Date(e.ts).toLocaleTimeString()}</span><span>${
    e.type === "status" ? `Status: <b>${esc(STATUS[e.status] || e.status)}</b>` : esc(e.message)}${e.step ? ` <span class="muted">· ${esc(e.step)}</span>` : ""}</span></div>`).join("") : `<div class="empty">No events yet</div>`;
  el.scrollTop = el.scrollHeight;
}

function renderReview(run) {
  const can = run.status === "awaiting_review";
  const reviews = run.reviews || [];
  $("#reviewCard").innerHTML = `<div class="h"><h2>Your decision</h2></div><div class="b">
    ${can ? `<p class="muted" style="margin-top:0">Check the facts against their sources, then decide.</p>` : `<p class="muted" style="margin-top:0">${run.status === "approved" || run.status === "declined" ? "This pack has been " + esc(run.status) + "." : "Available when the pack is ready for review."}</p>`}
    <div class="rowflex"><button class="btn ok" id="approve" ${can ? "" : "disabled"}>Approve</button><button class="btn bad" id="decline" ${can ? "" : "disabled"}>Decline</button></div>
    <label for="comments">Send back with comments</label>
    <textarea id="comments" placeholder="What should be fixed? e.g. 'Check the owners of the holding company'" ${can ? "" : "disabled"}></textarea>
    <input id="reviewer" placeholder="Your name (optional)" style="margin-top:8px" ${can ? "" : "disabled"}>
    <button class="btn warn full" id="sendBack" ${can ? "" : "disabled"}>Send back</button>
    <p class="muted" id="reviewMsg"></p>
    ${reviews.length ? `<div class="section"><h2 style="font-size:.95rem">History</h2><div class="list">${reviews.map((r) => `<div><b>v${r.version} · ${esc(r.decision.replace("_", " "))}</b> <span class="muted">${fmtTime(r.at)}${r.reviewer ? " · " + esc(r.reviewer) : ""}</span>${r.comments ? `<div>${esc(r.comments)}</div>` : ""}</div>`).join("")}</div></div>` : ""}
  </div>`;
  const send = async (decision) => {
    const comments = $("#comments").value.trim();
    if (decision === "send_back" && !comments) { $("#reviewMsg").textContent = "Add a comment so the agents know what to fix."; return; }
    try {
      await api(`/runs/${run.id}/review`, { method: "POST", body: JSON.stringify({ decision, comments: comments || null, reviewer: $("#reviewer").value.trim() || null }) });
      await window.__rerender();
    } catch (e) { $("#reviewMsg").textContent = e.message; }
  };
  $("#approve").onclick = () => send("approve");
  $("#decline").onclick = () => send("decline");
  $("#sendBack").onclick = () => send("send_back");
}

function srcChips(ids, known) {
  if (!ids || !ids.length) return `<span class="src missing" title="No source: this claim breaks 'no source, no claim'">no source</span>`;
  return ids.map((s) => `<span class="src ${known.has(s) ? "" : "missing"}" data-src="${esc(s)}" title="${known.has(s) ? "Jump to source" : "Unknown source id"}">${esc(s)}</span>`).join("");
}

function renderPack(p, events) {
  const known = new Set((p.sources || []).map((s) => s.id));
  const fact = (f) => (f && f.value != null && f.value !== "" ? `${esc(f.value)} ${srcChips(f.sources, known)}` : `<span class="muted">Not found</span>`);
  const c = p.company;
  const warnings = events.filter((e) => e.type === "warning").slice(-1)[0];
  const fs = p.financial_summary;
  const money = (v) => (v == null ? "n/a" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }));
  return `
  <div class="card dark"><div class="b">
    <div class="risk">
      <div><div class="muted" style="color:rgba(255,255,255,.6)">Risk rating</div><div class="big">${esc(p.risk.rating)}</div></div>
      <div><div class="muted" style="color:rgba(255,255,255,.6)">Recommendation</div><div class="big">${esc(p.risk.recommendation.replace("_", " "))}</div></div>
      <div style="flex:1;min-width:220px;color:rgba(255,255,255,.85)">${esc(p.risk.rationale)}</div>
    </div>
    ${(p.risk.factors || []).length ? `<div class="list" style="margin-top:12px">${p.risk.factors.map((f) => `<div style="border-color:rgba(255,255,255,.12)"><b>${esc(f.factor)}</b>${f.points != null ? ` · ${f.points} pt` : ""} ${srcChips(f.sources, known)}<div style="color:rgba(255,255,255,.75)">${esc(f.detail)}</div></div>`).join("")}</div>` : ""}
  </div></div>
  ${warnings ? `<div class="warnbox"><b>${esc(warnings.message)}</b><ul>${(warnings.data?.problems || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}

  ${(p.flags || []).length ? `<div class="card section"><div class="h"><h2>Flags for the reviewer</h2><span class="pill" style="margin-left:auto">${p.flags.length}</span></div><div class="b">
    ${p.flags.map((f) => `<div class="flag ${esc(f.severity)}"><div><b>${esc(f.type.replaceAll("_", " "))}</b> <span class="muted">${esc(f.severity)}</span><div>${esc(f.message)} ${srcChips(f.sources, known)}</div></div></div>`).join("")}</div></div>` : ""}

  <div class="card section"><div class="h"><h2>Company details</h2></div><div class="b"><dl class="kv">
    <dt>Legal name</dt><dd>${fact(c.legal_name)}</dd>
    <dt>Registration number</dt><dd>${fact(c.registration_number)}</dd>
    <dt>Status</dt><dd>${fact(c.status)}</dd>
    <dt>Registered address</dt><dd>${fact(c.address)}</dd>
    <dt>Incorporated</dt><dd>${fact(c.incorporation_date)}</dd>
    <dt>Sector</dt><dd>${fact(c.sector)}</dd>
  </dl></div></div>

  <div class="card section"><div class="h"><h2>Who runs it</h2></div><div class="b">
    ${(p.officers || []).length ? `<table><thead><tr><th>Name</th><th>Role</th><th>Appointed</th><th>Source</th></tr></thead><tbody>${p.officers.map((o) => `<tr><td>${esc(o.name)}</td><td>${esc(o.role)}</td><td>${esc(o.appointed_on || "")}</td><td>${srcChips(o.sources, known)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No officers found</div>`}
  </div></div>

  <div class="card section"><div class="h"><h2>Who owns it</h2></div><div class="b">
    ${(p.owners || []).length ? `<table><thead><tr><th>Owner</th><th>Type</th><th>Ownership</th><th>UBO</th><th>Via</th><th>Source</th></tr></thead><tbody>${p.owners.map((o) => `<tr><td>${esc(o.name)}${o.jurisdiction ? ` <span class="muted">(${esc(o.jurisdiction)})</span>` : ""}</td><td>${esc(o.type)}</td><td>${o.ownership_pct != null ? esc(o.ownership_pct) + "%" : ""}</td><td>${o.is_ultimate_beneficial_owner ? "Yes" : ""}</td><td class="muted">${esc((o.via || []).join(" → "))}</td><td>${srcChips(o.sources, known)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No owners found</div>`}
  </div></div>

  <div class="card section"><div class="h"><h2>News</h2><span class="pill" style="margin-left:auto">${(p.news || []).length}</span></div><div class="b"><div class="list">
    ${(p.news || []).length ? p.news.map((n) => `<div><span class="pill ${n.sentiment === "adverse" ? "r-high" : n.sentiment === "positive" ? "r-low" : ""}">${esc(n.sentiment)}${n.severity !== "none" ? " · " + esc(n.severity) : ""}</span>
      <b><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a></b> ${srcChips(n.sources, known)}
      <div class="muted">${esc(n.published_at || "")} ${n.language ? "· " + esc(n.language) : ""}</div><div>${esc(n.summary)}</div></div>`).join("") : `<div class="empty">No news found</div>`}
  </div></div></div>

  <div class="card section"><div class="h"><h2>Financial summary</h2></div><div class="b">
    ${fs ? `<dl class="kv"><dt>Year</dt><dd>${esc(fs.year ?? "n/a")}</dd><dt>Revenue</dt><dd>${esc(fs.currency || "")} ${money(fs.revenue)}</dd><dt>Net profit</dt><dd>${esc(fs.currency || "")} ${money(fs.net_profit)}</dd><dt>Total assets</dt><dd>${esc(fs.currency || "")} ${money(fs.total_assets)}</dd><dt>Going concern</dt><dd>${fs.going_concern_uncertainty == null ? "n/a" : fs.going_concern_uncertainty ? "Material uncertainty" : "No uncertainty reported"}</dd></dl>
      <p>${esc(fs.summary)} ${srcChips(fs.sources, known)}</p>` : `<div class="empty">No financial summary</div>`}
  </div></div>

  <div class="card section"><div class="h"><h2>Sources</h2><span class="pill" style="margin-left:auto">${(p.sources || []).length}</span></div><div class="b"><div class="list">
    ${(p.sources || []).map((s) => `<div id="source-${esc(s.id)}"><span class="src">${esc(s.id)}</span> <span class="pill">${esc(s.type)}</span> <b>${esc(s.title)}</b>
      <div class="muted mono" style="word-break:break-all">${/^https?:/.test(s.reference) ? `<a href="${esc(openable(s.reference))}" target="_blank" rel="noopener">${esc(s.reference)}</a>` : esc(s.reference)}</div>${s.excerpt ? `<div>“${esc(s.excerpt)}”</div>` : ""}</div>`).join("")}
  </div></div></div>
  ${p.notes ? `<p class="muted section">Notes: ${esc(p.notes)}</p>` : ""}`;
}

/* ---------------- Router ---------------- */
function route() {
  clearInterval(refreshTimer);
  if (eventSource) eventSource.close();
  const m = location.hash.match(/^#\/runs\/([\w-]+)/);
  if (m) runView(m[1]).catch((e) => ($("#app").innerHTML = `<p><a href="#/">← All runs</a></p><div class="warnbox">${esc(e.message)}</div>`));
  else home();
}
window.addEventListener("hashchange", route);
health(); setInterval(health, 15000);
route();
