const API = "";
const ACTIVE_JOB_KEY = "limboActiveJob";

function getActiveJob() {
  try { return JSON.parse(localStorage.getItem(ACTIVE_JOB_KEY)); }
  catch (e) { return null; }
}
function setActiveJob(id, topic) {
  localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify({ id, topic }));
}
function clearActiveJob() {
  localStorage.removeItem(ACTIVE_JOB_KEY);
}

async function loadArticles() {
  const grid = document.getElementById("articles-grid");
  const archiveSection = document.getElementById("failed-archive-section");
  const archiveGrid = document.getElementById("failed-archive-grid");
  const archiveCount = document.getElementById("failed-archive-count");

  try {
    const res = await fetch(`${API}/api/articles`);
    const articles = await res.json();

    const active = articles.filter(a => a.status !== "failed");
    const failed = articles.filter(a => a.status === "failed");

    if (!active.length) {
      grid.innerHTML = `<p class="empty-state">No research yet. Use the prompt above to get started.</p>`;
    } else {
      grid.innerHTML = active.map(a => articleCardHTML(a)).join("");
    }

    if (failed.length) {
      archiveSection.style.display = "block";
      archiveCount.textContent = failed.length;
      archiveGrid.innerHTML = failed.map(a => articleCardHTML(a)).join("");
    } else {
      archiveSection.style.display = "none";
    }
  } catch (e) {
    grid.innerHTML = `<p class="empty-state">Could not load articles. Make sure the server is running.</p>`;
  }
}

function articleCardHTML(a) {
  const date = a.date_researched
    ? new Date(a.date_researched).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" })
    : "";
  const statusClass = { complete: "badge-complete", researching: "badge-researching", failed: "badge-failed" }[a.status] || "badge-researching";
  const statusLabel = { complete: "Complete", researching: "Researching", failed: "Failed" }[a.status] || a.status;
  const excerpt = a.excerpt || "";
  const sources = a.source_count || 0;
  const claims = a.claim_count || 0;

  return `
<a class="article-card" href="/article/${a.id}">
  <div class="card-meta">
    <span class="card-date">${date}</span>
    <span class="badge ${statusClass}">${statusLabel}</span>
  </div>
  <div class="card-topic">${escHtml(a.topic || "")}</div>
  ${excerpt ? `<div class="card-excerpt">${escHtml(excerpt)}</div>` : ""}
  <div class="card-stats">
    <span class="card-stat"><strong>${sources}</strong> sources</span>
    <span class="card-stat"><strong>${claims}</strong> claims traced</span>
  </div>
</a>`;
}

let pollInterval = null;

function showResearchLocked(id, topic, progressNotes) {
  const box = document.getElementById("research-box");
  const notes = (progressNotes || []);
  const lastNote = notes[notes.length - 1] || "Starting...";
  box.innerHTML = `
    <div style="display:flex; align-items:flex-start; gap:14px;">
      <span class="spinner" style="width:16px;height:16px;border-width:2.5px;flex-shrink:0;margin-top:3px"></span>
      <div>
        <div style="font-size:14px;font-weight:600;margin-bottom:4px;">Research in progress</div>
        <div style="font-size:13px;color:var(--text-secondary);margin-bottom:8px;">${escHtml(topic)}</div>
        <div id="live-progress" style="font-size:13px;color:var(--text-muted);">${escHtml(lastNote)}</div>
        <div style="margin-top:10px;font-size:13px;">
          <a href="/article/${id}">Watch progress →</a>
        </div>
      </div>
    </div>`;
}

function restoreResearchBox() {
  const box = document.getElementById("research-box");
  box.innerHTML = `
    <label for="research-input">Research a new topic</label>
    <div class="research-input-row">
      <textarea
        id="research-input"
        class="research-textarea"
        placeholder="Describe a topic, paste an article URL, or ask a question. Press Cmd+Enter or click Research It."
      ></textarea>
      <button id="research-btn" class="btn btn-primary">Research It</button>
    </div>
    <div id="research-status" class="research-status"></div>`;
  document.getElementById("research-btn").addEventListener("click", startResearch);
  document.getElementById("research-input").addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) startResearch();
  });
}

async function startResearch() {
  const textarea = document.getElementById("research-input");
  const topic = textarea.value.trim();
  if (!topic) return;

  const statusEl = document.getElementById("research-status");
  statusEl.classList.add("visible");
  statusEl.innerHTML = `<span class="spinner"></span> Submitting...`;

  try {
    const res = await fetch(`${API}/api/research`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic })
    });
    if (!res.ok) throw new Error("Server error");
    const job = await res.json();

    setActiveJob(job.id, topic);
    showResearchLocked(job.id, topic, []);
    startPolling(job.id, topic);

  } catch (e) {
    statusEl.innerHTML = `Error: ${e.message}`;
  }
}

function startPolling(id, topic) {
  clearInterval(pollInterval);
  pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`${API}/api/research/${id}/status`);
      const data = await res.json();

      const progressEl = document.getElementById("live-progress");
      if (progressEl && data.progress_notes?.length) {
        progressEl.textContent = data.progress_notes[data.progress_notes.length - 1];
      }

      if (data.status === "complete" || data.status === "failed") {
        clearInterval(pollInterval);
        clearActiveJob();
        restoreResearchBox();
        loadArticles();
      }
    } catch (e) { /* keep polling */ }
  }, 8000);
}

async function checkStatus(jobId) {
  try {
    const res = await fetch(`${API}/api/research/${jobId}/status`);
    const data = await res.json();
    return data.status;
  } catch (e) {
    return "unknown";
  }
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

document.addEventListener("DOMContentLoaded", async () => {
  loadArticles();

  // Restore locked state if a job was running when the page was closed/refreshed
  const activeJob = getActiveJob();
  if (activeJob) {
    const status = await checkStatus(activeJob.id);
    if (status === "researching" || status === "queued") {
      const res = await fetch(`${API}/api/research/${activeJob.id}/status`);
      const data = await res.json();
      showResearchLocked(activeJob.id, activeJob.topic, data.progress_notes);
      startPolling(activeJob.id, activeJob.topic);
    } else {
      // Job finished while page was closed — clear the lock
      clearActiveJob();
    }
  }

  const btn = document.getElementById("research-btn");
  if (btn) {
    btn.addEventListener("click", startResearch);
    document.getElementById("research-input").addEventListener("keydown", e => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) startResearch();
    });
  }
});
