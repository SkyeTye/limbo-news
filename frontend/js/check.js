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
  const STYPE = { record_or_filing: "Record or filing", study_or_dataset: "Study or data", official_statement: "Official statement",
    transcript_or_footage: "Transcript or footage", own_post: "Their own post", news_or_analysis: "News or analysis",
    advocacy: "Advocacy group", social_post: "Social post", reference: "Reference site" };
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
  const toast = (msg, actLabel, actFn) => {
    const t = document.getElementById("toast");
    t.innerHTML = esc(msg) + (actLabel ? ` <button class="toast-act">${esc(actLabel)}</button>` : "");
    if (actLabel) t.querySelector(".toast-act").onclick = () => { t.classList.remove("on"); actFn && actFn(); };
    t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), actLabel ? 5000 : 2600);
  };
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
    if (path === "/check/archived") return renderHome(true);
    let m;
    if ((m = path.match(/^\/check\/source\/([\w-]+)$/))) return renderSource(m[1]);
    if ((m = path.match(/^\/check\/log\/([\w-]+)$/))) return renderLog(m[1]);
    if ((m = path.match(/^\/check\/([\w-]+)$/))) return renderCheck(m[1]);
    return renderHome(false);
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

  // ---------- motion helpers (Motion, vendored at /js/motion.min.js) ----------
  const M = window.Motion || null;
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SPRING = { type: "spring", stiffness: 260, damping: 30, mass: 1 };
  const SOFT = { type: "spring", stiffness: 170, damping: 26 };
  function anim(el, kf, opts) {
    if (!el) return;
    if (!M || reduce) { for (const k of Object.keys(kf)) { const v = kf[k]; const last = Array.isArray(v) ? v[v.length - 1] : v; if (k === "opacity") el.style.opacity = last; } return; }
    try { return M.animate(el, kf, opts || SPRING); } catch {}
  }
  // FLIP: record where elements are, change the DOM, then spring them from the old place to the new one.
  function flip(els, mutate) {
    const live = els.filter(Boolean);
    const before = live.map((el) => el.getBoundingClientRect().top);
    mutate();
    if (!M || reduce) return;
    live.forEach((el, i) => {
      const dy = before[i] - el.getBoundingClientRect().top;
      if (Math.abs(dy) > 1) anim(el, { y: [dy, 0] }, SOFT);
    });
  }
  function wordsIn(el) {
    if (!M || reduce || !el) return;
    const text = el.textContent;
    el.innerHTML = text.split(/(\s+)/).map((w) => (/^\s+$/.test(w) ? w : `<span class="w">${esc(w)}</span>`)).join("");
    M.animate(el.querySelectorAll(".w"), { opacity: [0, 1], y: [6, 0], filter: ["blur(4px)", "blur(0px)"] }, { delay: M.stagger(0.025), duration: 0.35, ease: "easeOut" });
  }

  // ---------- home ----------
  async function renderHome(showArchived) {
    $app.innerHTML = `<div class="stack">
      ${showArchived ? `<div class="row"><a class="btn sm line" href="/check" data-nav>‹ Back</a><h2 style="margin:0">Archived</h2></div>`
        : `<form class="newcheck" id="nc"><input type="url" id="nurl" inputmode="url" placeholder="Paste a TikTok, Instagram, YouTube or X link" required>
      <button class="btn" type="submit">Check</button></form>`}
      <ul class="list swipe" id="list"><li class="muted small" style="padding:12px 0">Loading…</li></ul>
      <div id="archlink"></div>
      <p class="tiny muted hint">${matchMedia("(hover: hover)").matches ? "Right-click a check to archive or delete it." : "Swipe a check left to archive or delete it."}</p></div>`;
    const nc = document.getElementById("nc");
    if (nc) nc.onsubmit = async (e) => {
      e.preventDefault();
      try { const r = await post("/api/check", { url: document.getElementById("nurl").value }); if (r.duplicate) toast("Already checked. Opening it."); navigate(`/check/${r.id}`); }
      catch (err) { if (err.message !== "auth") toast(err.message); }
    };
    await refreshList(showArchived, true);
  }
  async function refreshList(showArchived, first) {
    let data;
    try { data = await api("/api/checks" + (showArchived ? "?archived=1" : "")); } catch { return; }
    const el = document.getElementById("list");
    if (!el) return;
    if (!data.checks.length) {
      el.innerHTML = showArchived ? `<li class="muted small" style="padding:12px 0">Nothing archived.</li>`
        : `<li class="card stack small"><b>No checks yet.</b><p style="margin:0">Paste a link above, or share a video straight from TikTok or Instagram with the Shortcut (see <a href="/check/settings" data-nav>Settings</a>).</p></li>`;
    } else {
      el.innerHTML = data.checks.map((c) => itemHtml(c, showArchived)).join("");
      el.querySelectorAll(".sw").forEach((li) => bindSwipe(li, showArchived));
      if (first && M && !reduce) M.animate(el.querySelectorAll(".sw"), { opacity: [0, 1], y: [8, 0] }, { delay: M.stagger(0.03), duration: 0.3 });
    }
    const al = document.getElementById("archlink");
    if (al) al.innerHTML = !showArchived && data.archived_count ? `<a class="small" href="/check/archived" data-nav>Archived (${data.archived_count})</a>` : "";
    clearTimeout(pollTimer);
    if (data.checks.some((c) => ["queued", "running"].includes(c.status))) pollTimer = setTimeout(() => {
      const p = location.pathname.replace(/\/+$/, "");
      if (p === "/check" || p === "/check/archived") refreshList(showArchived, false);
    }, 4000);
  }
  function itemHtml(c, archived) {
    const f = (c.data && c.data.frames && c.data.frames[Math.min(1, c.data.frames.length - 1)]) || null;
    const thumb = f ? `<img class="thumb" src="/api/check/${c.id}/frames/${f.name}" alt="" loading="lazy" draggable="false">` : `<span class="thumb ph">${esc(c.platform)}</span>`;
    const running = ["queued", "running"].includes(c.status);
    const state = running ? `<span class="chip v-gray"><span class="spin"></span>${esc(c.step || "Queued")}</span>`
      : c.status === "error" ? `<span class="chip v-red">Could not check</span>`
      : c.central && c.central.verdict ? chip(c.central.verdict) : `<span class="chip v-gray">No claim to check</span>`;
    const title = (c.central && c.central.claim) || c.title || c.url;
    return `<li class="sw" data-id="${c.id}" data-running="${running ? 1 : 0}">
      <div class="sw-actions">${archived
        ? `<button class="sw-b sw-arch" data-do="restore">Restore</button><button class="sw-b sw-del" data-do="delete">Delete</button>`
        : `<button class="sw-b sw-arch" data-do="archive">Archive</button><button class="sw-b sw-del" data-do="delete">Delete</button>`}</div>
      <a class="item sw-front" href="/check/${c.id}" data-nav draggable="false">${thumb}<div style="min-width:0">
        <div class="t">${esc(title)}</div>
        <div class="m">${esc(PLATFORM[c.platform] || "")}${c.creator ? " · " + esc(c.creator) : ""} · ${esc(day(c.created_at))}</div>
        <div class="c">${state}</div></div></a></li>`;
  }

  // Mail-style swipe: drag left to reveal Archive / Delete; a long fast swipe archives straight away.
  const OPEN_X = -168;
  function bindSwipe(li, archived) {
    const front = li.querySelector(".sw-front");
    let x0 = 0, y0 = 0, dx = 0, base = 0, dragging = false, decided = false, horiz = false, moved = false, pid = null;
    const setX = (x) => { if (M && !reduce) M.animate(front, { x }, { duration: 0 }); else front.style.transform = `translateX(${x}px)`; };
    const settle = (to) => { li.dataset.open = to < 0 ? "1" : ""; if (M && !reduce) M.animate(front, { x: to }, SPRING); else front.style.transform = `translateX(${to}px)`; base = to; };
    front.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      x0 = e.clientX; y0 = e.clientY; dx = 0; dragging = true; decided = false; horiz = false; moved = false; pid = e.pointerId;
      base = li.dataset.open ? OPEN_X : 0;
    });
    front.addEventListener("pointermove", (e) => {
      if (!dragging || e.pointerId !== pid) return;
      const mx = e.clientX - x0, my = e.clientY - y0;
      if (!decided && Math.abs(mx) + Math.abs(my) > 8) { decided = true; horiz = Math.abs(mx) > Math.abs(my); if (horiz) front.setPointerCapture(pid); }
      if (!horiz) return;
      moved = true;
      dx = Math.min(40, base + mx);
      if (dx > 0) dx = dx * 0.3;  // rubber band to the right
      setX(dx);
    });
    const end = () => {
      if (!dragging) return;
      dragging = false;
      if (!horiz) return;
      const w = li.offsetWidth;
      if (dx < -w * 0.62 && !archived) return doAction(li, "archive");
      settle(dx < OPEN_X / 2 ? OPEN_X : 0);
    };
    front.addEventListener("pointerup", end);
    front.addEventListener("pointercancel", end);
    front.addEventListener("click", (e) => {
      if (moved || li.dataset.open) { e.preventDefault(); e.stopPropagation(); if (li.dataset.open && !moved) settle(0); moved = false; }
    }, true);
    li.querySelectorAll(".sw-b").forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); doAction(li, b.dataset.do, b); }));
    li.addEventListener("contextmenu", (e) => { e.preventDefault(); openMenu(e.clientX, e.clientY, li, archived); });
  }

  function closeMenu() { const m = document.getElementById("ctxmenu"); if (m) m.remove(); }
  document.addEventListener("click", (e) => { if (!e.target.closest("#ctxmenu")) closeMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
  window.addEventListener("scroll", closeMenu, { passive: true });
  function openMenu(x, y, li, archived) {
    closeMenu();
    const m = document.createElement("div");
    m.id = "ctxmenu"; m.setAttribute("role", "menu");
    m.innerHTML = `<button role="menuitem" data-do="open">Open</button>
      ${archived ? `<button role="menuitem" data-do="restore">Restore</button>` : `<button role="menuitem" data-do="archive">Archive</button>`}
      <button role="menuitem" class="danger" data-do="delete">Delete…</button>`;
    document.body.appendChild(m);
    const r = m.getBoundingClientRect();
    m.style.left = Math.min(x, innerWidth - r.width - 8) + "px";
    m.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
    anim(m, { opacity: [0, 1], scale: [0.96, 1] }, { duration: 0.12 });
    m.querySelectorAll("button").forEach((b) => b.onclick = (e) => {
      e.stopPropagation(); closeMenu();
      if (b.dataset.do === "open") return navigate(`/check/${li.dataset.id}`);
      doAction(li, b.dataset.do);
    });
    m.querySelector("button").focus();
  }

  async function collapse(li) {
    const front = li.querySelector(".sw-front");
    if (M && !reduce) {
      await M.animate(front, { x: -li.offsetWidth, opacity: 0 }, { duration: 0.22, ease: "easeIn" });
      li.style.overflow = "hidden";
      await M.animate(li, { height: [li.offsetHeight + "px", "0px"], opacity: [1, 0] }, { duration: 0.22 });
    }
    li.remove();
  }
  async function doAction(li, what, btn) {
    const id = li.dataset.id;
    try {
      if (what === "delete") {
        if (li.dataset.running === "1") return toast("Still running. Archive it, or wait for it to finish.");
        if (!btn || btn.dataset.confirm !== "1") {
          if (btn) { btn.dataset.confirm = "1"; btn.textContent = "Confirm"; setTimeout(() => { if (btn.isConnected) { btn.dataset.confirm = ""; btn.textContent = "Delete"; } }, 3500); return; }
          return confirmSheet("Delete this check?", "Its steps and verdict are removed. Sources stay in your library.", async () => {
            await api(`/api/check/${id}`, { method: "DELETE" }); await collapse(li); toast("Deleted"); refreshCounts();
          });
        }
        await api(`/api/check/${id}`, { method: "DELETE" }); await collapse(li); toast("Deleted"); refreshCounts();
        return;
      }
      const archived = what === "archive";
      await post(`/api/check/${id}/archive`, { archived });
      await collapse(li);
      refreshCounts();
      toast(archived ? "Archived" : "Restored", "Undo", async () => {
        await post(`/api/check/${id}/archive`, { archived: !archived });
        const p = location.pathname.replace(/\/+$/, "");
        refreshList(p === "/check/archived", false);
      });
    } catch (e) { if (e.message !== "auth") toast(e.message); }
  }
  async function refreshCounts() {
    const al = document.getElementById("archlink");
    if (!al) return;
    try { const d = await api("/api/checks?archived=1"); al.innerHTML = d.checks.length && location.pathname.replace(/\/+$/, "") === "/check" ? `<a class="small" href="/check/archived" data-nav>Archived (${d.checks.length})</a>` : ""; } catch {}
    const list = document.getElementById("list");
    if (list && !list.querySelector(".sw")) refreshList(location.pathname.replace(/\/+$/, "") === "/check/archived", false);
  }
  function confirmSheet(title, body, onYes) {
    const d = document.createElement("div");
    d.className = "sheet-wrap";
    d.innerHTML = `<div class="sheet" role="dialog" aria-modal="true"><h3>${esc(title)}</h3><p class="small muted">${esc(body)}</p>
      <div class="row"><button class="btn line" data-no>Cancel</button><button class="btn danger" data-yes>Delete</button></div></div>`;
    document.body.appendChild(d);
    anim(d.querySelector(".sheet"), { opacity: [0, 1], y: [20, 0] }, SPRING);
    const close = () => d.remove();
    d.addEventListener("click", (e) => { if (e.target === d) close(); });
    d.querySelector("[data-no]").onclick = close;
    d.querySelector("[data-yes]").onclick = async () => { close(); await onYes(); };
    d.querySelector("[data-yes]").focus();
  }

  // ---------- one check: built live, step by step ----------
  const ENDLABEL = {
    origin_found: "This is where it started",
    unsupported_assertion: "The trail starts with a claim that cites nothing",
    circular: "The sources cite each other in a loop",
    dead_end: "The trail goes cold here",
    stopped: "Stopped here: the trace ran out of steps",
  };
  const KINDTAG = { primary: "Original source", secondary: "Repeats it", unsupported: "Cites nothing" };
  const ACT_ICON = { search: "⌕", read: "◧", found: "✓", library: "▤", fail: "×", note: "·" };
  let view = null;   // the live view for the check on screen

  function stripMd(s) { return String(s || "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[*_#>`]/g, "").replace(/\s+/g, " ").trim(); }
  // Link to the original page, scrolled to the quote and highlighted (URL text fragments: Chrome, Edge, Safari 16.1+).
  function fragmentUrl(hop) {
    const base = hop.fetched_via === "wayback" && hop.archive_url ? hop.archive_url : (hop.final_url || hop.url);
    if (!base || /\.pdf($|\?)/i.test(base)) return base;
    const words = stripMd(hop.passage).split(" ").filter(Boolean);
    if (!words.length) return base;
    const enc = (s) => encodeURIComponent(s).replace(/-/g, "%2D").replace(/,/g, "%2C").replace(/&/g, "%26");
    const frag = words.length <= 8 ? enc(words.join(" ")) : enc(words.slice(0, 5).join(" ")) + "," + enc(words.slice(-5).join(" "));
    return base.split("#")[0] + "#:~:text=" + frag;
  }

  async function renderCheck(id) {
    let c;
    try { c = await api(`/api/check/${id}`); } catch (e) { if (e.message !== "auth") $app.innerHTML = `<div class="card">${esc(e.message)}</div>`; return; }
    if (!viewedOnce.has(id)) { viewedOnce.add(id); event(id, { action: "view" }); }
    $app.innerHTML = `<div class="cv" id="cv">
      <header class="cv-head" id="cv-head"></header>
      <section class="cv-claim" id="cv-claim"></section>
      <section class="cv-trace" id="cv-trace" hidden>
        <div class="label">Follow it back</div>
        <ol class="tl" id="tl"></ol>
      </section>
      <div id="cv-below">
        <section class="cv-unknowns" id="cv-unknowns" hidden></section>
        <section class="cv-script" id="cv-script"></section>
        <footer class="cv-foot" id="cv-foot"></footer>
      </div></div>`;
    view = { id, nodes: new Map(), claimShown: false, transcriptKey: "", endShown: false, building: null, steps: 0 };
    update(c, true);
  }

  async function poll(id) {
    if (!view || view.id !== id || !location.pathname.endsWith(id)) return;
    try { update(await api(`/api/check/${id}`), false); } catch {}
  }

  function update(c, first) {
    clearTimeout(pollTimer);
    const claim = c.claims.find((x) => x.central) || c.claims[0] || null;
    renderHead(c);
    renderClaim(c, claim, first);
    renderTimeline(c, claim, first);
    renderUnknowns(claim);
    renderTranscript(c, claim, first);
    renderFoot(c);
    const busy = ["queued", "running"].includes(c.status) || (claim && ["queued", "tracing"].includes(claim.status));
    if (busy) pollTimer = setTimeout(() => poll(c.id), 2500);
  }

  function renderHead(c) {
    const el = document.getElementById("cv-head");
    const cover = (c.data && c.data.frames && c.data.frames[Math.min(1, c.data.frames.length - 1)]) || null;
    const running = ["queued", "running"].includes(c.status);
    const html = `${cover ? `<img src="/api/check/${c.id}/frames/${cover.name}" alt="">` : `<span class="ph">${esc(PLATFORM[c.platform] || "")}</span>`}
      <div class="hmeta"><div class="small"><b>${esc(c.creator || "")}</b>${c.creator ? "" : `<span class="muted">${esc(PLATFORM[c.platform] || "Video")}</span>`}</div>
      <div class="tiny muted">${esc(PLATFORM[c.platform] || "Video")}${c.posted_at ? " · posted " + esc(day(c.posted_at)) : ""} · <a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer" data-ev="open_video">Open the video</a></div>
      ${running && c.step && !(c.claims || []).length ? `<div class="stepline"><span class="pulse"></span>${esc(c.step)}…</div>` : ""}
      ${c.status === "error" ? `<div class="errline">${esc(c.error || "Something went wrong.")} <button class="btn sm line" id="retry">Retry</button></div>` : ""}</div>`;
    if (el.dataset.h !== html) {
      el.dataset.h = html; el.innerHTML = html;
      const r = document.getElementById("retry");
      if (r) r.onclick = async () => { r.disabled = true; try { const x = await post(`/api/check/${c.id}/retry`); navigate(`/check/${x.id}`, true); } catch (e) { toast(e.message); r.disabled = false; } };
      el.querySelectorAll("[data-ev]").forEach((a) => a.addEventListener("click", () => event(c.id, { action: a.dataset.ev })));
    }
  }

  function renderClaim(c, claim, first) {
    const el = document.getElementById("cv-claim");
    if (claim) {
      if (view.claimShown) return;
      view.claimShown = true;
      el.innerHTML = `<div class="label">The claim</div>
        <h1 class="claimtext" id="claimtext">${esc(claim.claim)}</h1>
        <p class="said">${claim.timestamp ? `<button class="ts" data-jump="${esc(claim.timestamp)}">${esc(claim.timestamp)}</button> ` : ""}“${esc(claim.quote)}”${claim.where_said === "on_screen" ? ` <span class="tiny muted">on screen</span>` : claim.where_said === "caption" ? ` <span class="tiny muted">in the caption</span>` : ""}</p>
        ${claim.stated_source ? `<p class="tiny muted" style="margin:4px 0 0">Their source: ${esc(claim.stated_source)}</p>` : ""}`;
      if (!first) wordsIn(document.getElementById("claimtext"));
      el.querySelector("[data-jump]")?.addEventListener("click", (e) => jumpTo(e.target.dataset.jump));
      return;
    }
    const noClaim = c.data && c.data.no_claim;
    if (noClaim) { el.innerHTML = `<div class="label">The claim</div><p class="claimtext muted" style="font-size:18px">${esc(noClaim)}</p>`; return; }
    if (c.status === "error") { el.innerHTML = ""; return; }
    if (!el.querySelector(".skel")) el.innerHTML = `<div class="label">The claim</div><div class="skel build"><i style="width:92%"></i><i style="width:70%"></i></div>
      <p class="tiny muted" style="margin:8px 0 0">Finding the one central, checkable claim…</p>`;
  }

  function hopHtml(c, claim, hop, depth, max) {
    const kind = KINDTAG[hop.kind] || "";
    const cmp = hop.compare || hop.mutation_note || hop.says || "";
    const quote = stripMd(hop.passage);
    const long = quote.split(" ").length > 30 && hop.paraphrase;
    const url = fragmentUrl(hop);
    return `<div class="dot">${depth}</div>
      <div class="nb">
        <div class="nhead"><span class="src">${esc(hop.source_name || hop.publisher || host(hop.url))}</span>${kind ? `<span class="tag ${esc(hop.kind)}">${esc(kind)}</span>` : ""}</div>
        ${hop.title ? `<div class="hl">${esc(hop.title)}${hop.published_at ? ` · ${esc(day(hop.published_at))}` : ""}</div>` : ""}
        ${long ? `<p class="para">${esc(hop.paraphrase)}</p><details class="exact"><summary>Exact words</summary><blockquote class="q">“${esc(quote)}”</blockquote></details>`
          : `<blockquote class="q">“${esc(quote)}”</blockquote>`}
        ${cmp ? `<p class="cmp">${hop.mutation && hop.mutation !== "none" ? `<span class="mut">${esc(MUT[hop.mutation] || "Changed")}</span>` : `<span class="same">Same</span>`}${esc(cmp)}</p>` : ""}
        ${url ? `<a class="readbtn" href="${esc(url)}" target="_blank" rel="noopener noreferrer" data-ev="open_live" data-depth="${depth}" data-max="${max}" data-src="${esc(hop.source_id)}">Read the full article <span aria-hidden="true">↗</span></a>` : ""}
      </div>`;
  }

  function videoNodeHtml(c, claim) {
    return `<div class="dot vid">▶</div><div class="nb vidnb"><div class="nhead"><span class="src">The video</span>
      <span class="hl">${esc(c.creator || "")}${claim.timestamp ? ` · at ${esc(claim.timestamp)}` : ""}</span></div></div>`;
  }

  function buildingHtml(claim) {
    const p = (claim.data && claim.data.progress) || {};
    const steps = (p.steps || []).slice(-3);
    const second = p.attempt === 2 || (claim.data && claim.data.retrying);
    return `<div class="dot ripple"><span></span></div>
      <div class="nb building">
        <div class="nhead"><span class="src shimmer-text">${second ? "Digging deeper" : (claim.status === "queued" ? "Starting the trace" : "Finding the next step")}</span>
          ${p.read_limit ? `<span class="tag ghost">${p.reads || 0} of ${p.read_limit} pages read</span>` : ""}</div>
        <div class="skel build"><i style="width:40%"></i><i style="width:88%"></i><i style="width:64%"></i></div>
        <ul class="acts">${steps.map((s) => `<li data-at="${esc(s.at)}"><span class="ai">${ACT_ICON[s.kind] || "·"}</span>${esc(s.text)}</li>`).join("")}</ul>
      </div>`;
  }

  function endHtml(claim) {
    const failed = claim.status === "failed";
    const cls = claim.ending === "origin_found" ? "good" : (["unsupported_assertion", "circular", "dead_end"].includes(claim.ending) ? "bad" : "");
    return `<div class="dot end ${cls}"></div>
      <div class="nb">
        <div class="nhead"><span class="src">${failed ? "The trace failed" : esc(ENDLABEL[claim.ending] || "Done")}</span></div>
        ${failed ? `<p class="cmp">${esc(claim.bottom_line || "")}</p><button class="btn sm line" id="retrace">Try again</button>`
          : `<div class="verdict">${chip(claim.verdict, true)}${claim.confidence ? `<span class="conf">${esc(claim.confidence)} confidence</span>` : ""}</div>
             ${claim.bottom_line ? `<p class="bl">${esc(claim.bottom_line)}</p>` : ""}`}
      </div>`;
  }

  function renderTimeline(c, claim, first) {
    const sec = document.getElementById("cv-trace");
    const tl = document.getElementById("tl");
    if (!claim) { sec.hidden = true; return; }
    const below = [document.getElementById("cv-below")];
    if (sec.hidden) {
      flip(below, () => { sec.hidden = false; });
      const v = document.createElement("li"); v.className = "node video"; v.innerHTML = videoNodeHtml(c, claim);
      tl.appendChild(v);
      if (!first) anim(v, { opacity: [0, 1], y: [10, 0] });
    }
    const chain = claim.chain || [];
    const ids = new Set(chain.map((h) => String(h.id)));
    // steps that were dropped by the second pass fade out
    for (const [hid, el] of view.nodes) if (!ids.has(hid)) {
      view.nodes.delete(hid);
      const after = [...el.parentNode.children].slice([...el.parentNode.children].indexOf(el) + 1).concat(below);
      if (M && !reduce) M.animate(el, { opacity: 0, x: -12 }, { duration: 0.2 }).then(() => flip(after, () => el.remove())); else el.remove();
    }
    const busy = ["queued", "tracing"].includes(claim.status);
    // new steps slide in above the building step, pushing everything below down
    chain.forEach((hop, i) => {
      const key = String(hop.id);
      let el = view.nodes.get(key);
      if (el) { const dot = el.querySelector(".dot"); if (dot && dot.textContent !== String(i + 1)) dot.textContent = i + 1; return; }
      el = document.createElement("li");
      el.className = "node hop"; el.dataset.id = key;
      el.innerHTML = hopHtml(c, claim, hop, i + 1, chain.length);
      el.querySelectorAll("[data-ev]").forEach((a) => a.addEventListener("click", () =>
        event(c.id, { action: a.dataset.ev, claim_id: claim.id, depth: +a.dataset.depth, max_depth: +(claim.chain || []).length, source_id: a.dataset.src })));
      const anchor = view.building || tl.querySelector(".node.end");
      const movers = [view.building, tl.querySelector(".node.end")].concat(below);
      flip(movers, () => tl.insertBefore(el, anchor || null));
      view.nodes.set(key, el);
      if (!first) anim(el, { opacity: [0, 1], y: [14, 0], scale: [0.985, 1] }, SPRING);
    });
    // the step being built: rippling placeholder with live activity
    if (busy) {
      if (!view.building) {
        view.building = document.createElement("li");
        view.building.className = "node build-node";
        view.building.innerHTML = buildingHtml(claim);
        flip(below, () => tl.appendChild(view.building));
        anim(view.building, { opacity: [0, 1], y: [10, 0] });
      } else {
        const old = [...view.building.querySelectorAll(".acts li")].map((li) => li.dataset.at);
        const tmp = document.createElement("div"); tmp.innerHTML = buildingHtml(claim);
        view.building.querySelector(".nhead").innerHTML = tmp.querySelector(".nhead").innerHTML;
        const acts = view.building.querySelector(".acts");
        const fresh = tmp.querySelector(".acts");
        if (acts.innerHTML !== fresh.innerHTML) {
          acts.innerHTML = fresh.innerHTML;
          acts.querySelectorAll("li").forEach((li) => { if (!old.includes(li.dataset.at)) anim(li, { opacity: [0, 1], x: [-6, 0] }, { duration: 0.3 }); });
        }
      }
    } else if (view.building) {
      const b = view.building; view.building = null;
      flip(below, () => b.remove());
    }
    // the end: where it started, then the verdict (shown last, after the steps)
    if (!busy && claim.status !== "unchecked" && claim.status !== "opinion" && !view.endShown && (claim.ending || claim.status === "failed")) {
      view.endShown = true;
      const e = document.createElement("li"); e.className = "node end"; e.innerHTML = endHtml(claim);
      flip(below, () => tl.appendChild(e));
      if (!first) anim(e, { opacity: [0, 1], y: [14, 0] }, SPRING);
      const rt = e.querySelector("#retrace");
      if (rt) rt.onclick = async () => { rt.disabled = true; try { await post(`/api/check/${c.id}/claims/${claim.id}/trace`); renderCheck(c.id); } catch (er) { toast(er.message); rt.disabled = false; } };
    }
  }

  function renderUnknowns(claim) {
    const el = document.getElementById("cv-unknowns");
    const u = (claim && claim.data && claim.data.unknowns) || [];
    if (!u.length || ["queued", "tracing"].includes(claim.status)) { el.hidden = true; return; }
    const html = `<div class="label">Still unknown</div><ul class="plain small">${u.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    if (el.dataset.h !== html) { el.dataset.h = html; el.innerHTML = html; el.hidden = false; anim(el, { opacity: [0, 1] }, { duration: 0.4 }); }
  }

  function normWords(s) { return String(s || "").toLowerCase().replace(/[^a-z0-9%$ ]+/g, " ").split(/\s+/).filter((w) => w.length > 2); }
  function renderTranscript(c, claim, first) {
    const el = document.getElementById("cv-script");
    const t = c.transcript || "";
    const key = t.length + ":" + (claim ? claim.id : "") + ":" + (c.caption || "").length;
    if (key === view.transcriptKey) return;
    view.transcriptKey = key;
    if (!t && !c.caption) {
      el.innerHTML = ["queued", "running"].includes(c.status) ? `<div class="label">Transcript</div><div class="skel build lines"><i></i><i></i><i style="width:80%"></i><i></i><i style="width:60%"></i></div>` : "";
      return;
    }
    const lines = t.split("\n").filter(Boolean).map((l) => { const m = l.match(/^\[(\d+:\d\d(?::\d\d)?)\]\s*(.*)$/); return m ? { ts: m[1], text: m[2] } : { ts: "", text: l }; });
    const qw = claim ? new Set(normWords(claim.quote)) : null;
    const hit = (txt) => { if (!qw || !qw.size) return false; const w = normWords(txt); if (!w.length) return false; const n = w.filter((x) => qw.has(x)).length; return n >= Math.min(4, Math.ceil(qw.size * 0.5)) && n / w.length > 0.35; };
    el.innerHTML = `<div class="label">Transcript${c.transcript_source === "audio" ? ` <span class="tiny muted" style="text-transform:none;letter-spacing:0">from the audio</span>` : ""}</div>
      ${lines.length ? `<ol class="script">${lines.map((l) => `<li class="${hit(l.text) ? "hit" : ""}" data-ts="${esc(l.ts)}">${l.ts ? `<span class="lts">${esc(l.ts)}</span>` : ""}<span>${esc(l.text)}</span></li>`).join("")}</ol>` : `<p class="small muted">No speech in this video.</p>`}
      ${c.caption ? `<details class="cap"><summary class="small">Caption</summary><p class="small">${esc(c.caption)}</p></details>` : ""}`;
    if (!first && M && !reduce) M.animate(el.querySelectorAll(".script li"), { opacity: [0, 1], y: [4, 0] }, { delay: M.stagger(0.015, { startDelay: 0.05 }), duration: 0.25 });
  }
  function jumpTo(ts) {
    const li = document.querySelector(`.script li[data-ts="${CSS.escape(ts)}"]`) || document.querySelector(".script li.hit");
    if (!li) return;
    li.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    li.classList.remove("flash"); void li.offsetWidth; li.classList.add("flash");
  }

  function renderFoot(c) {
    const el = document.getElementById("cv-foot");
    const running = ["queued", "running"].includes(c.status);
    const warn = (c.data && c.data.warnings) || [];
    const html = running ? "" : `${warn.length ? `<details class="small"><summary>Notes (${warn.length})</summary><ul class="plain muted">${warn.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}
      <p class="tiny muted"><a href="/check/log/${c.id}" data-nav>Research log</a> · every search and page read for this check</p>`;
    if (el.dataset.h !== html) { el.dataset.h = html; el.innerHTML = html; }
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
