const API = "";

async function loadArticles() {
  const grid = document.getElementById("articles-grid");
  try {
    const res = await fetch(`${API}/api/articles`);
    const articles = await res.json();

    if (!articles.length) {
      grid.innerHTML = `<p class="empty-state">No research yet. Use the prompt above to get started.</p>`;
      return;
    }

    grid.innerHTML = articles.map(a => articleCardHTML(a)).join("");
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

let activeJobId = null;
let pollInterval = null;

async function startResearch() {
  const textarea = document.getElementById("research-input");
  const btn = document.getElementById("research-btn");
  const statusEl = document.getElementById("research-status");
  const topic = textarea.value.trim();

  if (!topic) return;

  btn.disabled = true;
  statusEl.classList.add("visible");
  statusEl.innerHTML = `<span class="spinner"></span> Submitting research request...`;

  try {
    const res = await fetch(`${API}/api/research`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic })
    });

    if (!res.ok) throw new Error("Server error");

    const job = await res.json();
    activeJobId = job.id;

    statusEl.innerHTML = `<span class="spinner"></span> Research started (ID: ${job.id}). This takes 5-10 minutes. <a href="/article/${job.id}">Watch progress</a>`;

    pollInterval = setInterval(async () => {
      const status = await checkStatus(job.id);
      if (status === "complete" || status === "failed") {
        clearInterval(pollInterval);
        if (status === "complete") {
          statusEl.innerHTML = `Research complete. <a href="/article/${job.id}">View report</a>`;
          loadArticles();
        } else {
          statusEl.innerHTML = `Research failed. Please try again.`;
          btn.disabled = false;
        }
      }
    }, 8000);

  } catch (e) {
    statusEl.innerHTML = `Error: ${e.message}`;
    btn.disabled = false;
  }
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

document.addEventListener("DOMContentLoaded", () => {
  loadArticles();

  const btn = document.getElementById("research-btn");
  btn.addEventListener("click", startResearch);

  const textarea = document.getElementById("research-input");
  textarea.addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) startResearch();
  });
});
