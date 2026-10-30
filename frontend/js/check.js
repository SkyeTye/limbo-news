// Fact Check: the private /check section. Plain JS, no build step.
(() => {
  "use strict";
  const $app = document.getElementById("app");
  const $nav = document.getElementById("barnav");
  let pollTimer = null;
  let viewedOnce = new Set();

  // ---------- labels ----------
  const VERDICT = {
    matches_origin: ["Matches origin", "v-green"],
    stretched: ["Stretched", "v-amber"],
    missing_context: ["Missing context", "v-amber"],
    misattributed: ["Misattributed", "v-red"],
    outdated: ["Outdated", "v-amber"],
    contradicted: ["Contradicted by origin", "v-red"],
    no_origin_found: ["No origin found", "v-gray"],
  };
  const ENDING = {
    origin_found: ["Origin found", "good"],
    unsupported_assertion: ["The trail starts with a claim that cites nothing", "bad"],
    circular: ["Circular: these sources cite each other", "bad"],
    dead_end: ["Dead end: the next source could not be opened or found", "bad"],
    stopped: ["Stopped here: the trace hit its step limit", ""],
  };
  const KIND = { primary: "Primary source", secondary: "Repeats it", unsupported: "Cites nothing" };
  const MUT = {
    number_changed: "Number changed", scope_widened: "Scope widened", hedge_dropped: "Hedge dropped",
    time_shifted: "Old info shown as new", correlation_to_causation: "Correlation became causation",
    quote_clipped: "Quote clipped", misattributed: "Misattributed", wrong_clip: "Wrong clip",
    cherry_picked: "Cherry-picked", other: "Changed",
  };
  const PLATFORM = { TT: "TikTok", IG: "Instagram", YT: "YouTube", X: "X", WEB: "Web" };
  const STEP_STATUS = { queued: "Waiting", tracing: "Tracing", failed: "Failed" };

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u || ""; } };
  const day = (s) => { if (!s) return ""; const d = new Date(s); return isNaN(d) ? String(s).slice(0, 10) : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); };
  const toast = (msg) => { const t = document.getElementById("toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 2600); };
  const chip = (v, big) => { const [label, cls] = VERDICT[v] || ["Pending", "v-gray"]; return `<span class="chip ${cls}${big ? " big" : ""}">${esc(label)}</span>`; };

  async function api(path, opts = {}) {
    const r = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts });
    let j = null; try { j = await r.json(); } catch {}
    if (r.status === 401) { renderLogin(); throw new Error("auth"); }
    if (!r.ok) throw new Error((j && j.error) || `Error ${r.status}`);
    return j;
  }
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
  const event = (checkId, body) => { post(`/api/check/${checkId}/events`, body).catch(() => {}); };

  function navigate(path, replace) {
    if (replace) history.replaceState({}, "", path); else history.pushState({}, "", path);
    route();
  }
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-nav]");
    if (a && a.getAttribute("href").startsWith("/check") && !e.metaKey && !e.ctrlKey) { e.preventDefault(); navigate(a.getAttribute("href")); window.scrollTo(0, 0); }
  });
  window.addEventListener("popstate", route);

  // Same regex idea as the server's receipt check: words in order, any punctuation or link syntax between.
  function passageRegex(p) {
    const words = (p || "").match(/[\p{L}\p{N}_]+/gu);
    if (!words) return null;
    return new RegExp(words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("(?:[^\\p{L}\\p{N}]|_|\\]\\([^)]*\\))+"), "iu");
  }
  // Escape text and turn markdown links into real links.
  function richText(s) {
    let out = "", last = 0;
    const re = /\[([^\]\n]{1,300})\]\((https?:\/\/[^\s)]+)\)/g;
    let m;
    while ((m = re.exec(s))) {
      out += esc(s.slice(last, m.index)) + `<a href="${esc(m[2])}" target="_blank" rel="noopener noreferrer">${esc(m[1])}</a>`;
      last = m.index + m[0].length;
    }
    return out + esc(s.slice(last));
  }

  // ---------- routing ----------
  async function route() {
    clearTimeout(pollTimer);
    const path = location.pathname.replace(/\/+$/, "");
    let s;
    try { s = await api("/api/check/session"); } catch { return; }
    if (!s.configured) { $nav.innerHTML = ""; $app.innerHTML = `<div class="card stack"><h2>Not set up yet</h2><p>Add <b>FACTCHECK_PASSWORD</b> in Railway → Variables, then reload.</p></div>`; return; }
    if (!s.authed) return renderLogin();
    $nav.innerHTML = `<a href="/check/settings" data-nav>Settings</a>`;
    if (path === "/check/share") return handleShare();
    if (path === "/check/settings") return renderSettings();
    let m;
    if ((m = path.match(/^\/check\/source\/([\w-]+)$/))) return renderSource(m[1]);
    if ((m = path.match(/^\/check\/log\/([\w-]+)$/))) return renderLog(m[1]);
    if ((m = path.match(/^\/check\/([\w-]+)$/))) return renderCheck(m[1]);
    return renderHome();
  }

  // ---------- login ----------
  function renderLogin() {
    $nav.innerHTML = "";
    $app.innerHTML = `<form class="card stack" id="login" style="margin-top:12vh">
      <h2>Sign in</h2>
      <input type="password" id="pw" autocomplete="current-password" placeholder="Password" required>
      <button class="btn" type="submit">Sign in</button>
      <p class="tiny muted" id="lerr"></p></form>`;
    document.getElementById("login").onsubmit = async (e) => {
      e.preventDefault();
      try { await post("/api/check/login", { password: document.getElementById("pw").value }); route(); }
      catch (err) { if (err.message !== "auth") document.getElementById("lerr").textContent = err.message; else document.getElementById("lerr").textContent = "Wrong password."; }
    };
  }

  // ---------- share target (Android) ----------
  async function handleShare() {
    const p = new URLSearchParams(location.search);
    const text = [p.get("url"), p.get("text"), p.get("title")].filter(Boolean).join(" ");
    $app.innerHTML = `<p class="pad muted">Sending it to be checked…</p>`;
    try { const r = await post("/api/check", { text, via: "share" }); navigate(`/check/${r.id}`, true); }
    catch (e) { $app.innerHTML = `<div class="card">${esc(e.message)}</div>`; }
  }

  // ---------- home ----------
  async function renderHome() {
    $app.innerHTML = `<div class="stack">
      <form class="newcheck" id="nc"><input type="url" id="nurl" inputmode="url" placeholder="Paste a TikTok, Instagram, YouTube or X link" required>
      <button class="btn" type="submit">Check</button></form>
      <div id="list"><p class="muted small">Loading…</p></div></div>`;
    document.getElementById("nc").onsubmit = async (e) => {
      e.preventDefault();
      try { const r = await post("/api/check", { url: document.getElementById("nurl").value }); if (r.duplicate) toast("Already checked. Opening it."); navigate(`/check/${r.id}`); }
      catch (err) { if (err.message !== "auth") toast(err.message); }
    };
    let data;
    try { data = await api("/api/checks"); } catch { return; }
    const el = document.getElementById("list");
    if (!data.checks.length) {
      el.innerHTML = `<div class="card stack small"><b>No checks yet.</b><p>Paste a link above, or share a video straight from TikTok or Instagram with the Shortcut (see <a href="/check/settings" data-nav>Settings</a>).</p></div>`;
      return;
    }
    el.innerHTML = `<ul class="list">${data.checks.map(itemHtml).join("")}</ul>`;
    if (data.checks.some((c) => ["queued", "running"].includes(c.status))) pollTimer = setTimeout(renderHomeQuiet, 5000);
  }
  async function renderHomeQuiet() {
    if (location.pathname.replace(/\/+$/, "") !== "/check") return;
    try { const data = await api("/api/checks"); const el = document.getElementById("list"); if (el) el.innerHTML = `<ul class="list">${data.checks.map(itemHtml).join("")}</ul>`;
      if (data.checks.some((c) => ["queued", "running"].includes(c.status))) pollTimer = setTimeout(renderHomeQuiet, 5000); } catch {}
  }
  function itemHtml(c) {
    const f = (c.data && c.data.frames && c.data.frames[Math.min(1, c.data.frames.length - 1)]) || null;
    const thumb = f ? `<img class="thumb" src="/api/check/${c.id}/frames/${f.name}" alt="" loading="lazy">` : `<span class="thumb ph">${esc(c.platform)}</span>`;
    const running = ["queued", "running"].includes(c.status);
    const state = running ? `<span class="chip v-gray"><span class="spin"></span>${esc(c.step || "Queued")}</span>`
      : c.status === "error" ? `<span class="chip v-red">Could not check</span>`
      : c.central && c.central.verdict ? chip(c.central.verdict) : `<span class="chip v-gray">No checkable claims</span>`;
    return `<li><a class="item" href="/check/${c.id}" data-nav>${thumb}<div style="min-width:0">
      <div class="t">${esc(c.title || c.thesis || c.url)}</div>
      <div class="m">${esc(PLATFORM[c.platform] || "")}${c.creator ? " · " + esc(c.creator) : ""} · ${esc(day(c.created_at))}</div>
      <div class="c">${state}</div></div></a></li>`;
  }

  // ---------- one check ----------
  let lastCheck = null;
  const openLadders = new Set();

  async function renderCheck(id, quiet) {
    let c;
    try { c = await api(`/api/check/${id}`); } catch (e) { if (e.message !== "auth") $app.innerHTML = `<div class="card">${esc(e.message)}</div>`; return; }
    lastCheck = c;
    if (!viewedOnce.has(id)) { viewedOnce.add(id); event(id, { action: "view" }); }
    const y = window.scrollY;
    $app.innerHTML = checkHtml(c);
    bindCheck(c);
    if (quiet) window.scrollTo(0, y);
    const busy = ["queued", "running"].includes(c.status) || c.claims.some((x) => ["queued", "tracing"].includes(x.status));
    if (busy) pollTimer = setTimeout(() => { if (location.pathname.endsWith(id)) renderCheck(id, true); }, 3000);
  }

  function frameNear(c, seconds) {
    const fr = (c.data && c.data.frames) || [];
    if (!fr.length) return null;
    if (seconds == null) return fr[Math.min(1, fr.length - 1)];
    return fr.reduce((a, b) => (Math.abs(b.t - seconds) < Math.abs(a.t - seconds) ? b : a));
  }

  function checkHtml(c) {
    const d = c.data || {};
    const cover = frameNear(c, null);
    const checked = c.claims.filter((x) => !["opinion", "unchecked"].includes(x.status));
    const unchecked = c.claims.filter((x) => x.status === "unchecked");
    const opinions = c.claims.filter((x) => x.status === "opinion");
    const running = ["queued", "running"].includes(c.status);
    let h = `<div class="stack">`;
    h += `<div class="vhead">${cover ? `<img src="/api/check/${c.id}/frames/${cover.name}" alt="">` : ""}
      <div class="stack" style="margin:0;min-width:0">
        <div class="label">${esc(PLATFORM[c.platform] || "Video")}${c.posted_at ? " · posted " + esc(day(c.posted_at)) : ""}</div>
        <h1>${esc(c.title || "Checking this video")}</h1>
        <div class="small">${c.creator ? `<b>${esc(c.creator)}</b> · ` : ""}<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer" data-ev="open_video">Open the video</a></div>
      </div></div>`;
    if (running) h += `<div class="status"><span class="spin"></span><span>${esc(c.step || "Queued")}…</span></div>`;
    if (c.status === "error") h += `<div class="status err"><span>${esc(c.error || "Something went wrong.")}</span><button class="btn sm line" data-act="retry" style="margin-left:auto">Retry</button></div>`;
    if (c.thesis) h += `<section class="stack"><div class="label">What this video wants you to believe</div><p class="thesis" style="margin:0">${esc(c.thesis)}</p></section>`;
    if (d.overall) h += `<section class="stack"><div class="label">Bottom line</div><p style="margin:0">${esc(d.overall)}</p></section>`;
    if (checked.length) h += `<section class="stack"><div class="label">Claims checked</div>${checked.map((x) => claimHtml(c, x)).join("")}</section>`;
    else if (!running && c.status !== "error") h += `<div class="card small">No specific factual claims to check were found in this video.</div>`;
    if (d.left_out && d.left_out.length) h += `<section class="card stack"><div class="label">What the video leaves out</div><ul class="plain">${d.left_out.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></section>`;
    if (unchecked.length) h += `<section class="card stack"><div class="label">Other claims, not checked yet</div>${unchecked.map((x) => `<div class="stack" style="margin-top:10px">
        <div>${x.timestamp ? `<span class="ts">${esc(x.timestamp)}</span> ` : ""}${esc(x.claim)}</div>
        <button class="btn sm line" data-act="trace" data-claim="${x.id}">Check this</button></div>`).join("")}</section>`;
    if (opinions.length) h += `<details class="card"><summary>Opinions and predictions (${opinions.length})</summary><p class="small muted">These are views, not facts, so they aren't checked.</p><ul class="plain small">${opinions.map((x) => `<li>${x.timestamp ? `<span class="ts">${esc(x.timestamp)}</span> ` : ""}"${esc(x.quote)}"</li>`).join("")}</ul></details>`;
    if (d.warnings && d.warnings.length) h += `<details class="small"><summary>Notes (${d.warnings.length})</summary><ul class="plain muted">${d.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>`;
    if (!running) h += `<p class="small"><a href="/check/log/${c.id}" data-nav>Research log</a> · <span class="muted">every search and page read for this check</span></p>`;
    return h + `</div>`;
  }

  function claimHtml(c, x) {
    const d = x.data || {};
    const busy = ["queued", "tracing"].includes(x.status);
    const fr = frameNear(c, x.seconds);
    let h = `<article class="card stack claim" id="claim-${x.id}">`;
    h += `<div class="top"><span class="label">${x.central ? "Central claim" : "Claim"}</span>
      ${busy ? `<span class="chip v-gray"><span class="spin"></span>${esc(d.retrying ? "Second pass" : STEP_STATUS[x.status])}</span>`
      : x.status === "failed" ? `<span class="chip v-red">Failed</span>`
      : `<span class="row">${chip(x.verdict, x.central)}${x.confidence ? `<span class="conf">${esc(x.confidence)} confidence</span>` : ""}</span>`}</div>`;
    h += `<p style="margin:0;font-weight:600">${esc(x.claim)}</p>`;
    h += `<blockquote class="quote">${x.timestamp ? `<span class="ts">${esc(x.timestamp)}</span> ` : ""}"${esc(x.quote)}"${x.where_said === "on_screen" ? ` <span class="tiny muted">(on screen)</span>` : ""}${x.where_said === "caption" ? ` <span class="tiny muted">(caption)</span>` : ""}</blockquote>`;
    if (x.stated_source) h += `<div class="small muted">Creator's stated source: ${esc(x.stated_source)}</div>`;
    if (busy && d.progress) h += `<div class="prog">${d.progress.hops} step${d.progress.hops === 1 ? "" : "s"} found · ${d.progress.reads}/${d.progress.read_limit} pages read · ${esc(d.progress.last || "")}</div>`;
    if (x.bottom_line) h += `<p class="bl" style="margin:0">${esc(x.bottom_line)}</p>`;
    if (d.video_vs_origin) h += `<p class="diff" style="margin:0">${esc(d.video_vs_origin)}</p>`;
    if (x.chain && x.chain.length || (!busy && x.ending)) {
      const open = openLadders.has(x.id);
      const n = x.chain.length;
      h += `<button class="btn line follow" data-act="ladder" data-claim="${x.id}"><span>${open ? "Hide the receipts" : n ? "Follow the receipts" : "See where the trail ends"}</span><span class="n">${n ? `${n} step${n === 1 ? "" : "s"} ` : ""}${open ? "▲" : "▼"}</span></button>`;
      if (open) h += ladderHtml(c, x, fr);
    }
    if (d.unknowns && d.unknowns.length) h += `<div class="small"><div class="label">Still unknown</div><ul class="plain">${d.unknowns.map((u) => `<li>${esc(u)}</li>`).join("")}</ul></div>`;
    const voices = (lastCheck.voices || []).filter((v) => v.claim_id === x.id);
    if (voices.length) h += `<div class="voices"><div class="label">What people on each side say</div>${voices.map((v) => `<div class="v">
        <div class="stance ${esc(v.stance)}">${esc(v.stance)}</div><div><b>${esc(v.name)}</b>${v.affiliation ? `, ${esc(v.affiliation)}` : ""}</div>
        <div class="small">"${esc(v.quote)}"</div>
        ${v.source_id ? `<a class="tiny" href="/check/source/${v.source_id}?check=${c.id}&claim=${x.id}&pid=${v.passage_id || ""}" data-nav>${esc(v.publisher || v.title || host(v.url))} ›</a>` : ""}</div>`).join("")}</div>`;
    const seconds = (lastCheck.second_opinions || []).filter((s) => s.claim_id === x.id);
    if (seconds.length) h += `<div class="small"><div class="label">Other fact-checkers (shown after our verdict)</div><ul class="plain">${seconds.map((s) => `<li>
        <a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.publisher || host(s.url))}</a>: ${esc(s.rating || "no rating")}
        <span class="chip ${s.agrees === "yes" ? "v-green" : s.agrees === "no" ? "v-red" : "v-amber"}" style="margin-left:4px">${s.agrees === "yes" ? "agrees" : s.agrees === "no" ? "disagrees" : "partly agrees"}</span>
        ${s.note ? `<div class="muted">${esc(s.note)}</div>` : ""}</li>`).join("")}</ul></div>`;
    if (!busy) h += `<div class="row">
      ${d.deeper_article ? `<a class="btn sm line" href="/article/${esc(d.deeper_article)}">Deep research ›</a>` : `<button class="btn sm line" data-act="deeper" data-claim="${x.id}">Go deeper</button>`}
      <button class="btn sm line" data-act="trace" data-claim="${x.id}">Re-check</button></div>`;
    return h + `</article>`;
  }

  function ladderHtml(c, x, fr) {
    const max = x.chain.length;
    let h = `<ol class="ladder">`;
    h += `<li class="rung video"><div class="who">The video</div><div class="meta">${esc(c.creator || "")}${x.timestamp ? ` <span class="ts">${esc(x.timestamp)}</span>` : ""}</div>
      ${fr ? `<img src="/api/check/${c.id}/frames/${fr.name}" alt="" style="width:96px;border-radius:8px;display:block;margin:4px 0">` : ""}
      <div class="passage">"${esc(x.quote)}"</div></li>`;
    x.chain.forEach((hop, i) => {
      const depth = i + 1;
      const q = `check=${c.id}&claim=${x.id}&pid=${hop.passage_id || ""}&depth=${depth}&max=${max}`;
      h += `<li class="rung" data-n="${depth}">
        <div class="who">${esc(hop.publisher || hop.title || host(hop.url))}</div>
        <div class="meta"><span class="kind ${esc(hop.kind)}">${esc(KIND[hop.kind] || hop.kind)}</span>${hop.published_at ? `<span>${esc(day(hop.published_at))}</span>` : ""}<span>${esc(host(hop.url))}</span></div>
        ${hop.title && hop.publisher ? `<div class="small">${esc(hop.title)}</div>` : ""}
        <div class="passage"><mark>${esc(hop.passage || "")}</mark></div>
        ${hop.says ? `<div class="small">${esc(hop.says)}</div>` : ""}
        ${hop.mutation && hop.mutation !== "none" ? `<div class="mut"><b>${esc(MUT[hop.mutation] || "Changed")} here</b>${esc(hop.mutation_note || "")}</div>` : ""}
        ${hop.cites && depth < max ? `<div class="cites">Points to: ${esc(hop.cites)}</div>` : ""}
        <div class="links">
          <a class="btn sm" href="/check/source/${hop.source_id}?${q}" data-nav data-ev="open_saved" data-depth="${depth}" data-max="${max}" data-claim="${x.id}" data-src="${hop.source_id}">Read it here</a>
          <a class="btn sm line" href="${esc(hop.url)}" target="_blank" rel="noopener noreferrer" data-ev="open_live" data-depth="${depth}" data-max="${max}" data-claim="${x.id}" data-src="${hop.source_id}">Live page</a>
          ${hop.archive_url ? `<a class="btn sm line" href="${esc(hop.archive_url)}" target="_blank" rel="noopener noreferrer" data-ev="open_archive" data-depth="${depth}" data-max="${max}" data-claim="${x.id}" data-src="${hop.source_id}">Archive</a>` : ""}
        </div>
        ${hop.previous_hash ? `<div class="tiny muted" style="margin-top:6px">This page has changed since we first saved it.</div>` : ""}
      </li>`;
    });
    const [label, cls] = ENDING[x.ending] || [x.status === "tracing" ? "Still tracing…" : "", ""];
    if (label) h += `<li class="rung end ${cls}"><div class="endcard">${esc(label)}</div></li>`;
    return h + `</ol>`;
  }

  function bindCheck(c) {
    $app.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
      const act = b.dataset.act, claim = b.dataset.claim;
      if (act === "ladder") {
        if (openLadders.has(claim)) openLadders.delete(claim); else { openLadders.add(claim); event(c.id, { action: "open_receipts", claim_id: claim }); }
        const y = window.scrollY; $app.innerHTML = checkHtml(lastCheck); bindCheck(lastCheck); window.scrollTo(0, y); return;
      }
      b.disabled = true;
      try {
        if (act === "retry") { const r = await post(`/api/check/${c.id}/retry`); navigate(`/check/${r.id}`); return; }
        if (act === "trace") { await post(`/api/check/${c.id}/claims/${claim}/trace`); toast("Tracing it now."); renderCheck(c.id, true); return; }
        if (act === "deeper") { const r = await post(`/api/check/${c.id}/claims/${claim}/deeper`); toast("Deep research started. It takes a while."); location.href = r.url; return; }
      } catch (e) { if (e.message !== "auth") toast(e.message); b.disabled = false; }
    }));
    $app.querySelectorAll("[data-ev]").forEach((a) => a.addEventListener("click", () => {
      event(c.id, { action: a.dataset.ev, claim_id: a.dataset.claim || "", depth: +(a.dataset.depth || 0), max_depth: +(a.dataset.max || 0), source_id: a.dataset.src || "" });
    }));
  }

  // ---------- saved source reader ----------
  async function renderSource(sid) {
    const p = new URLSearchParams(location.search);
    const checkId = p.get("check"), claimId = p.get("claim"), pid = p.get("pid"), depth = +(p.get("depth") || 0), max = +(p.get("max") || 0);
    let s;
    try { s = await api(`/api/check/sources/${sid}`); } catch (e) { if (e.message !== "auth") $app.innerHTML = `<div class="card">${esc(e.message)}</div>`; return; }
    const text = s.text || "";
    const ranges = [];
    for (const ps of s.passages || []) {
      const re = passageRegex(ps.text); if (!re) continue;
      const m = re.exec(text); if (m) ranges.push({ a: m.index, b: m.index + m[0].length, id: ps.id });
    }
    ranges.sort((r1, r2) => r1.a - r2.a);
    let html = "", at = 0;
    for (const r of ranges) {
      if (r.a < at) continue;
      html += richText(text.slice(at, r.a)) + `<mark id="p${r.id}" class="${String(r.id) === pid ? "cur" : ""}">${richText(text.slice(r.a, r.b))}</mark>`;
      at = r.b;
    }
    html += richText(text.slice(at));
    const back = checkId ? `/check/${checkId}` : "/check";
    $app.innerHTML = `<div class="stack">
      <div class="sticky-tools row"><a class="btn sm line" href="${back}" data-nav>‹ Back to the check</a>
        ${ranges.length > 1 ? `<button class="btn sm line" id="nextmark">Next highlight</button>` : ""}</div>
      <div class="stack"><div class="label">${depth ? `Step ${depth} of ${max} · ` : ""}Our saved copy</div>
        <h2>${esc(s.title || host(s.url))}</h2>
        <div class="small muted">${esc(s.publisher || host(s.url))}${s.published_at ? " · " + esc(day(s.published_at)) : ""} · saved ${esc(day(s.first_read))}</div>
        <div class="links"><a class="btn sm line" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" data-ev="open_live">Live page</a>
          ${s.archive_url ? `<a class="btn sm line" href="${esc(s.archive_url)}" target="_blank" rel="noopener noreferrer" data-ev="open_archive">Archive copy</a>` : ""}
          ${s.has_file ? `<a class="btn sm line" href="/api/check/sources/${sid}/file" target="_blank" data-ev="open_file">Original PDF</a>` : ""}</div>
        ${s.previous_hash ? `<p class="small" style="color:var(--amber)">This page changed after we first saved it. Our copy is the latest one we read.</p>` : ""}
      </div><hr><div class="reader">${html || '<span class="muted">No text saved.</span>'}</div>${s.truncated ? `<p class="muted small">Long document: showing the first 300,000 characters.</p>` : ""}</div>`;
    if (checkId) {
      $app.querySelectorAll("[data-ev]").forEach((a) => a.addEventListener("click", () => event(checkId, { action: a.dataset.ev, claim_id: claimId, depth, max_depth: max, source_id: sid })));
    }
    const target = document.getElementById("p" + pid) || $app.querySelector(".reader mark");
    if (target) setTimeout(() => target.scrollIntoView({ block: "center" }), 60);
    const nb = document.getElementById("nextmark");
    if (nb) { let i = 0; const marks = [...$app.querySelectorAll(".reader mark")]; nb.onclick = () => { i = (i + 1) % marks.length; marks.forEach((m) => m.classList.remove("cur")); marks[i].classList.add("cur"); marks[i].scrollIntoView({ block: "center" }); }; }
  }

  // ---------- research log ----------
  async function renderLog(id) {
    let r; try { r = await api(`/api/check/${id}/audit`); } catch { return; }
    event(id, { action: "open_log" });
    $app.innerHTML = `<div class="stack"><a class="btn sm line" href="/check/${id}" data-nav>‹ Back to the check</a>
      <h2>Research log</h2><p class="small muted">Every search, page read and decision the agents made, in order. ${r.audit.length} entries.</p>
      <div class="log">${r.audit.map((e) => `<div class="e"><div class="tiny muted">${esc(e.at.slice(11, 19))} · ${esc(e.agent)}</div>
        <div class="mono"><b>${esc(e.tool)}</b> ${esc(e.input)}</div><details><summary class="tiny">Result</summary><pre>${esc(e.result)}</pre></details></div>`).join("")}</div></div>`;
  }

  // ---------- settings ----------
  async function renderSettings() {
    let s; try { s = await api("/api/check/settings"); } catch { return; }
    const u = s.usage, pct = (a, b) => Math.min(100, Math.round((a / Math.max(b, 0.0001)) * 100));
    const rh = s.rabbit_hole, c = s.connections;
    const yes = (b, okText = "Connected", noText = "Not set", optional) => `<span class="${b ? "ok" : optional ? "opt" : "no"}">${b ? okText : noText}</span>`;
    $app.innerHTML = `<div class="stack">
      <section class="card stack"><h2>Share from your iPhone</h2>
        <p class="small">iPhone doesn't let web apps appear in the share sheet, so a Shortcut does it. One-time setup in the <b>Shortcuts</b> app:</p>
        <ol class="steps small">
          <li>Tap <b>+</b> and name it <b>Fact-check this</b>.</li>
          <li>Tap <b>ⓘ</b>, turn on <b>Show in Share Sheet</b>, and set it to receive <b>URLs</b> and <b>Text</b>.</li>
          <li>Add <b>Get Contents of URL</b>. URL: the address below. Method: <b>POST</b>. Headers: add <b>Authorization</b> with the value below. Request Body: <b>JSON</b> with <b>url</b> = Shortcut Input and <b>via</b> = share.</li>
          <li>Add <b>Get Dictionary Value</b>: key <b>open_url</b> from Contents of URL.</li>
          <li>Add <b>Open URLs</b> with that Dictionary Value.</li>
        </ol>
        <div class="copyrow"><input readonly id="ep" value="${esc(s.endpoint)}"><button class="btn sm line" data-copy="ep">Copy</button></div>
        <div class="copyrow"><input readonly id="tok" type="password" value="${esc(s.token)}"><button class="btn sm line" id="show">Show</button><button class="btn sm line" data-copy="tok">Copy</button></div>
        <p class="tiny muted">Keep the Authorization value private: anyone with it can start checks. Changing FACTCHECK_PASSWORD replaces it. Results open in Safari, so sign in there once too.</p>
        <p class="small"><b>Home screen:</b> in Safari tap Share → <b>Add to Home Screen</b> to open Fact Check like an app.</p>
        <p class="small"><b>Android:</b> install it from Chrome's menu (Add to Home screen). It then shows up in the share sheet.</p>
      </section>
      <section class="card stack"><h2>Rabbit hole depth</h2>
        <p class="small muted">The success metric: do people follow the receipts, or stop at the verdict?</p>
        <div class="kv"><span>Checks finished</span><b>${rh.checks}</b><span>Opened at least one receipt</span><b>${rh.opened_any_receipt}</b>
        <span>Reached the origin step</span><b>${rh.reached_origin}</b><span>Opened the origin document itself</span><b>${rh.opened_origin_document}</b>
        <span>Average deepest step opened</span><b>${rh.avg_max_depth}</b></div></section>
      <section class="card stack"><h2>Source library</h2>
        <div class="kv"><span>Sources saved</span><b>${s.library.sources}</b><span>Exact passages</span><b>${s.library.passages}</b>
        <span>Who-cites-whom links</span><b>${s.library.citations}</b><span>Claims traced</span><b>${s.library.claims_traced}</b></div>
        ${c.persistent_storage ? "" : `<p class="small" style="color:var(--red)">DATA_DIR is not set, so the library is wiped on every deploy. Point DATA_DIR at the Railway volume.</p>`}</section>
      <section class="card stack"><h2>Spending</h2>
        <div class="small">AI this month: $${u.month.usd.toFixed(2)} of $${u.limits.usd.toFixed(2)}</div><div class="meter"><i style="width:${pct(u.month.usd, u.limits.usd)}%"></i></div>
        <div class="small">Checks today: ${u.today.checks} of ${u.limits.checks_per_day}</div><div class="meter"><i style="width:${pct(u.today.checks, u.limits.checks_per_day)}%"></i></div>
        <div class="small">Post lookups this month: ${u.month.lookups} of ${u.limits.lookups}</div><div class="meter"><i style="width:${pct(u.month.lookups, u.limits.lookups)}%"></i></div>
        <p class="tiny muted">Change limits in Railway: FACTCHECK_MONTHLY_BUDGET_USD, FACTCHECK_DAILY_LIMIT, FACTCHECK_MONTHLY_LOOKUP_LIMIT.</p></section>
      <section class="card stack"><h2>Connections</h2>
        <div class="kv"><span>Claude</span>${yes(c.claude)}<span>Post lookup (ScrapeCreators)</span>${yes(c.post_lookup, "Connected", "Not set: yt-dlp fallback")}
        <span>Transcripts (Groq)</span>${yes(c.transcripts)}<span>Web search</span><b>${esc(c.search)}</b>
        <span>Google Fact Check API</span>${yes(c.factcheck_api, "Connected", "Optional, not set", true)}<span>Persistent storage</span>${yes(c.persistent_storage, "On", "Off")}</div>
        <p class="tiny muted">Tracer: ${esc(s.models.trace)} · retry on a bad central trace: ${esc(s.models.retry)}</p></section>
      <button class="btn line" id="logout">Sign out</button></div>`;
    document.getElementById("show").onclick = (e) => { const t = document.getElementById("tok"); t.type = t.type === "password" ? "text" : "password"; e.target.textContent = t.type === "password" ? "Show" : "Hide"; };
    $app.querySelectorAll("[data-copy]").forEach((b) => b.onclick = async () => { const v = document.getElementById(b.dataset.copy).value; try { await navigator.clipboard.writeText(v); toast("Copied"); } catch { toast("Copy failed"); } });
    document.getElementById("logout").onclick = async () => { await post("/api/check/logout"); route(); };
  }

  route();
})();
