const API = "";

const articleId = window.location.pathname.split("/").pop();
let pollInterval = null;

async function loadArticle() {
  try {
    const res = await fetch(`${API}/api/articles/${articleId}`);
    if (!res.ok) throw new Error("Article not found");
    const article = await res.json();
    renderArticle(article);
  } catch (e) {
    document.getElementById("article-body").innerHTML = `<p style="color:var(--red)">Could not load article: ${e.message}</p>`;
  }
}

function renderArticle(article) {
  document.title = `${article.topic || "Research"} - Anti-Limbo News`;
  document.getElementById("article-topic").textContent = article.topic || "";

  const date = article.date_researched
    ? new Date(article.date_researched).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })
    : "";
  document.getElementById("article-date").textContent = date;

  const status = article.status || "researching";
  const statusEl = document.getElementById("article-status");
  const classes = { complete: "badge-complete", researching: "badge-researching", failed: "badge-failed", cancelled: "badge-cancelled" };
  statusEl.className = `badge ${classes[status] || "badge-researching"}`;
  statusEl.textContent = { complete: "Complete", researching: "In Progress", failed: "Failed", cancelled: "Cancelled" }[status] || status;

  if (status === "researching") {
    clearInterval(pollInterval);
    pollInterval = setInterval(() => pollStatus(), 8000);
    renderLoading(article);
    return;
  }

  if (status === "failed") {
    document.getElementById("article-body").innerHTML = `<div class="article-section"><p style="color:var(--red)">Research failed: ${escHtml(article.error || "Unknown error")}</p></div>`;
    return;
  }

  if (status === "cancelled") {
    document.getElementById("article-body").innerHTML = `<div class="article-section"><p style="color:var(--text-muted)">Research was cancelled before it completed.</p></div>`;
    return;
  }

  renderComplete(article);
}

function renderLoading(article) {
  const notes = article.progress_notes || [];
  const notesHtml = notes.length
    ? `<ul class="progress-notes-list">${notes.map(n => `<li>${escHtml(n)}</li>`).join("")}</ul>`
    : "";
  document.getElementById("article-body").innerHTML = `
<div class="loading-state">
  <span class="spinner"></span>
  <p>Researching. This takes 5-10 minutes.</p>
  ${notesHtml}
</div>`;
}

async function pollStatus() {
  try {
    const res = await fetch(`${API}/api/research/${articleId}/status`);
    const data = await res.json();
    if (data.status === "complete" || data.status === "failed") {
      clearInterval(pollInterval);
      loadArticle();
    } else {
      const notes = data.progress_notes || [];
      const notesHtml = notes.length
        ? `<ul class="progress-notes-list">${notes.map(n => `<li>${escHtml(n)}</li>`).join("")}</ul>`
        : "";
      document.getElementById("article-body").innerHTML = `
<div class="loading-state">
  <span class="spinner"></span>
  <p>Researching...</p>
  ${notesHtml}
</div>`;
    }
  } catch (e) { /* keep polling */ }
}

function renderComplete(article) {
  const sections = [];

  // Summary
  if (article.executive_summary) {
    const paras = article.executive_summary.split(/\n+/).filter(p => p.trim());
    sections.push(`
<section class="article-section" id="sec-summary">
  <div class="section-heading">Summary</div>
  <div class="summary-text">${paras.map(p => `<p>${escHtml(p)}</p>`).join("")}</div>
</section>`);
  }

  // Key claims
  const claims = article.key_claims || [];
  if (claims.length) {
    sections.push(`
<section class="article-section" id="sec-claims">
  <div class="section-heading">Key Claims Traced</div>
  <div class="claims-list">${claims.map(claimCard).join("")}</div>
</section>`);
  }

  // Timeline
  const timeline = article.timeline || [];
  if (timeline.length) {
    sections.push(`
<section class="article-section" id="sec-timeline">
  <div class="section-heading">Timeline</div>
  <ul class="timeline-list">${timeline.map(timelineItem).join("")}</ul>
</section>`);
  }

  // Opinion map
  const opinionMap = article.opinion_map;
  if (opinionMap && (opinionMap.positions || []).length) {
    sections.push(`
<section class="article-section" id="sec-opinions">
  <div class="section-heading">Opinion Spectrum</div>
  ${renderOpinionMap(opinionMap)}
</section>`);
  }

  // Primary sources
  const sources = article.primary_sources || [];
  if (sources.length) {
    sections.push(`
<section class="article-section" id="sec-sources">
  <div class="section-heading">Sources (${sources.length})</div>
  <div class="sources-list">${sources.map(sourceCard).join("")}</div>
</section>`);
  }

  // Information gaps
  const gaps = article.information_gaps || [];
  if (gaps.length) {
    sections.push(`
<section class="article-section" id="sec-gaps">
  <div class="section-heading">Information Gaps</div>
  <div class="gaps-list">${gaps.map(gapCard).join("")}</div>
</section>`);
  }

  // Research tree
  const tree = article.research_tree;
  if (tree && tree.node) {
    sections.push(`
<section class="article-section" id="sec-tree">
  <div class="section-heading">Research Trail</div>
  <div class="tree-root">${renderTree(tree)}</div>
</section>`);
  }

  // Full report
  if (article.full_report) {
    sections.push(`
<section class="article-section" id="sec-report">
  <div class="section-heading">Full Report</div>
  <div class="full-report">${renderMarkdown(article.full_report)}</div>
</section>`);
  }

  // Process notes
  const notes = article.process_notes || [];
  if (notes.length) {
    sections.push(`
<section class="article-section" id="sec-process">
  <div class="section-heading">Research Process Notes</div>
  <ul class="process-notes-list">${notes.map(n => `<li>${escHtml(n)}</li>`).join("")}</ul>
</section>`);
  }

  sections.push(`
<section class="article-section article-actions">
  <button class="btn-archive" onclick="archiveArticle('${articleId}')">Archive this article</button>
</section>`);

  document.getElementById("article-body").innerHTML = sections.join("");
  renderSidebarNav(article);
  attachToggleHandlers();
}

async function archiveArticle(id) {
  const btn = document.querySelector(".btn-archive");
  if (btn) { btn.disabled = true; btn.textContent = "Archiving..."; }
  try {
    const res = await fetch(`${API}/api/articles/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed");
    window.location.href = "/";
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = "Archive this article"; }
    alert("Could not archive article. Try again.");
  }
}

function claimCard(claim) {
  const v = claim.verified || "unverified";
  const statusClass = { confirmed: "status-confirmed", disputed: "status-disputed", unverified: "status-unverified" }[v] || "status-unverified";
  const verifiedClass = { confirmed: "verified-confirmed", disputed: "verified-disputed", unverified: "verified-unverified" }[v] || "verified-unverified";
  const verifiedLabel = { confirmed: "Confirmed", disputed: "Disputed", unverified: "Unverified" }[v] || v;
  const quote = claim.direct_quote ? `<div class="claim-quote">"${escHtml(claim.direct_quote)}"</div>` : "";
  const sourceLink = claim.url ? `<div class="claim-source-link">Source: <a href="${escHtml(claim.url)}" target="_blank" rel="noopener">${escHtml(claim.url.slice(0, 80))}${claim.url.length > 80 ? "..." : ""}</a></div>` : "";
  const notes = claim.notes ? `<div class="claim-notes">${escHtml(claim.notes)}</div>` : "";

  return `
<div class="claim-card">
  <div class="claim-header" onclick="toggleCard(this)">
    <span class="claim-status-dot ${statusClass}"></span>
    <span class="claim-text">${escHtml(claim.claim || "")}</span>
    <span class="claim-verified ${verifiedClass}">${verifiedLabel}</span>
    <span class="claim-expand-icon">▾</span>
  </div>
  <div class="claim-body">
    ${quote}
    ${sourceLink}
    ${notes}
  </div>
</div>`;
}

function timelineItem(item) {
  return `
<li class="timeline-item">
  <div class="timeline-date">${escHtml(item.date || "")}</div>
  <div class="timeline-event">${escHtml(item.event || "")}</div>
  ${item.source ? `<div class="timeline-source">${escHtml(item.source)}</div>` : ""}
</li>`;
}

function sourceCard(source) {
  const t = (source.type || "secondary").toLowerCase().replace(/\s+/g, "_");
  const typeLabel = (source.type || "source").toUpperCase().replace(/_/g, " ");
  const dateStr = source.date ? ` · ${source.date}` : "";
  const summaryHtml = source.key_content ? `<div class="source-summary">${escHtml(source.key_content.slice(0, 300))}${source.key_content.length > 300 ? "..." : ""}</div>` : "";
  const inaccessible = source.accessible === false ? `<div class="source-inaccessible">Not publicly accessible${source.access_notes ? ": " + escHtml(source.access_notes) : ""}</div>` : "";
  const titleHtml = source.url
    ? `<a href="${escHtml(source.url)}" target="_blank" rel="noopener" class="source-title">${escHtml(source.title || source.url)}</a>`
    : `<div class="source-title">${escHtml(source.title || "Untitled")}</div>`;

  return `
<div class="source-card">
  <span class="source-type-badge type-${escHtml(t)}">${typeLabel}</span>
  <div class="source-info">
    ${titleHtml}
    <div class="source-meta">${dateStr}</div>
    ${summaryHtml}
    ${inaccessible}
  </div>
</div>`;
}

function gapCard(gap) {
  if (typeof gap === "string") {
    return `<div class="gap-card"><div class="gap-title">${escHtml(gap)}</div></div>`;
  }
  return `
<div class="gap-card">
  <div class="gap-title">${escHtml(gap.gap || "")}</div>
  <div class="gap-why">${escHtml(gap.why_unknown || "")}</div>
  ${gap.significance ? `<div class="gap-significance">${escHtml(gap.significance)}</div>` : ""}
</div>`;
}

function spectrumDisplayPcts(positions) {
  const raw = positions.map(p => Math.max(0, Math.min(100, p.spectrum_position || 50)));
  if (positions.length <= 1) return raw;
  const minP = Math.min(...raw);
  const maxP = Math.max(...raw);
  const range = maxP - minP;
  if (range >= 50) return raw;
  // Spread compressed positions across 5–95 to keep dots visible
  if (range === 0) return raw.map((_, i) => Math.round(5 + (i / (raw.length - 1)) * 90));
  return raw.map(v => Math.round(5 + ((v - minP) / range) * 90));
}

function renderOpinionMap(opinionMap) {
  const question = opinionMap.core_question || "";
  const positions = opinionMap.positions || [];
  const displayPcts = spectrumDisplayPcts(positions);

  const dots = positions.map((p, i) => {
    const pct = displayPcts[i];
    const hue = Math.round(240 - pct * 1.8);
    return `<div class="opinion-dot" title="${escHtml(p.label || "")}" style="left:${pct}%; background:hsl(${hue},70%,55%)"></div>`;
  }).join("");

  const positionCards = positions.map((p, i) => {
    const pct = displayPcts[i];
    const hue = Math.round(240 - pct * 1.8);
    const voices = (p.key_voices || []).map(v => `
<div class="voice-item">
  <div class="voice-name">${escHtml(v.name || "")}</div>
  <div class="voice-affil">${escHtml(v.affiliation || "")}</div>
  ${v.quote ? `<div class="voice-quote">"${escHtml(v.quote)}"</div>` : ""}
  ${v.source_url ? `<div style="font-size:12px;margin-top:4px"><a href="${escHtml(v.source_url)}" target="_blank" rel="noopener">Source</a></div>` : ""}
</div>`).join("");

    const evidenceCited = (p.evidence_cited || []);
    const evidenceIgnored = (p.evidence_ignored || []);
    const weaknesses = (p.logical_weaknesses || []);

    return `
<div class="opinion-card" id="opinion-${i}">
  <div class="opinion-header" onclick="toggleCard(this)">
    <span class="opinion-spectrum-marker" style="background:hsl(${hue},70%,55%)"></span>
    <span class="opinion-label">${escHtml(p.label || "")}</span>
    <span class="opinion-summary-short">${escHtml((p.summary || "").slice(0, 80))}${(p.summary || "").length > 80 ? "..." : ""}</span>
    <span class="opinion-toggle">▾</span>
  </div>
  <div class="opinion-body">
    <p style="font-size:14px;margin-top:12px;color:var(--text-secondary)">${escHtml(p.summary || "")}</p>
    ${voices ? `<div class="opinion-voices"><div class="opinion-voices-title">Key Voices</div>${voices}</div>` : ""}
    ${evidenceCited.length ? `
<div class="opinion-evidence">
  <div class="opinion-voices-title">Evidence Cited</div>
  <ul class="evidence-list">${evidenceCited.map(e => `<li>${escHtml(e)}</li>`).join("")}</ul>
</div>` : ""}
    ${weaknesses.length ? `
<div class="opinion-evidence">
  <div class="opinion-voices-title" style="color:var(--red)">Weaknesses / What This View Doesn't Address</div>
  <ul class="evidence-list">${weaknesses.map(e => `<li>${escHtml(e)}</li>`).join("")}</ul>
</div>` : ""}
  </div>
</div>`;
  }).join("");

  return `
<div class="opinion-question">${escHtml(question)}</div>
<div class="spectrum-bar">${dots}</div>
<div class="spectrum-clearfix">
  <span class="spectrum-label-left">Dismisses allegation</span>
  <span class="spectrum-label-right">Confirms allegation</span>
</div>
<br>
<div class="opinion-positions">${positionCards}</div>`;
}

function renderTree(node, depth = 0) {
  if (!node) return "";
  const label = node.node || node.label || "";
  const status = node.status || "unverified";
  const children = node.children || [];
  const statusClass = { verified: "tree-status-verified", gap: "tree-status-gap", unverified: "tree-status-unverified" }[status] || "tree-status-unverified";
  const icon = children.length ? "▸" : "·";
  const sourceLink = node.source_url ? `<a class="tree-source-link" href="${escHtml(node.source_url)}" target="_blank" rel="noopener">${escHtml(node.source_url.slice(0, 70))}...</a>` : "";

  const childrenHtml = children.length
    ? `<div class="tree-children">${children.map(c => renderTree(c, depth + 1)).join("")}</div>`
    : "";

  return `
<div class="tree-node">
  <div class="tree-node-header" onclick="this.parentElement.querySelector('.tree-children') && this.parentElement.querySelector('.tree-children').classList.toggle('hidden')">
    <span class="tree-icon">${icon}</span>
    <span class="tree-label">${escHtml(label)}</span>
    <span class="tree-status ${statusClass}"></span>
  </div>
  ${sourceLink}
  ${childrenHtml}
</div>`;
}

function renderMarkdown(md) {
  return md
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/^> (.+)$/gm, "<blockquote>$1</blockquote>")
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>[\s\S]+?<\/li>)/g, "<ul>$1</ul>")
    .replace(/\n{2,}/g, "</p><p>")
    .replace(/^(?!<[hbup])(.+)$/gm, "<p>$1</p>")
    .replace(/<\/p><p>/g, "</p>\n<p>");
}

function renderSidebarNav(article) {
  const nav = document.getElementById("sidebar-nav");
  const links = [
    article.executive_summary && { id: "sec-summary", label: "Summary" },
    (article.key_claims || []).length && { id: "sec-claims", label: "Claims Traced" },
    (article.timeline || []).length && { id: "sec-timeline", label: "Timeline" },
    (article.opinion_map?.positions || []).length && { id: "sec-opinions", label: "Opinion Spectrum" },
    (article.primary_sources || []).length && { id: "sec-sources", label: "Sources" },
    (article.information_gaps || []).length && { id: "sec-gaps", label: "Information Gaps" },
    article.research_tree && { id: "sec-tree", label: "Research Trail" },
    article.full_report && { id: "sec-report", label: "Full Report" },
    (article.process_notes || []).length && { id: "sec-process", label: "Process Notes" },
  ].filter(Boolean);

  nav.innerHTML = links.map(l => `<li><a href="#${l.id}">${l.label}</a></li>`).join("");
}

function toggleCard(header) {
  const card = header.closest(".claim-card, .opinion-card");
  if (card) card.classList.toggle("open");
}

function attachToggleHandlers() {
  // Intercept anchor clicks for smooth scroll
  document.querySelectorAll(".sidebar-nav a").forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      const target = document.getElementById(link.getAttribute("href").slice(1));
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

document.addEventListener("DOMContentLoaded", loadArticle);
