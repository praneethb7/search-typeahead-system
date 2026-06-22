"use strict";

// ---- DOM handles ---------------------------------------------------------------------------
const inputEl = document.getElementById("search-input");
const listEl = document.getElementById("suggestions");
const btnEl = document.getElementById("search-btn");
const statusEl = document.getElementById("status");
const responseEl = document.getElementById("response");
const recencyEl = document.getElementById("recency-toggle");
const trendingEl = document.getElementById("trending-list");

const DEBOUNCE_MS = 180; // wait for a typing pause before calling the backend
let debounceTimer = null;
let activeIndex = -1; // highlighted suggestion for keyboard nav
let currentItems = []; // last rendered suggestions
let inFlight = null; // AbortController for the latest /suggest call

// ---- helpers -------------------------------------------------------------------------------
function setStatus(text, isError = false, loading = false) {
  statusEl.className = "status" + (isError ? " error" : "");
  statusEl.innerHTML = (loading ? '<span class="spinner"></span>' : "") + (text || "");
}

function hideSuggestions() {
  listEl.classList.add("hidden");
  listEl.innerHTML = "";
  activeIndex = -1;
  currentItems = [];
}

function fmtCount(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

// ---- suggestions (debounced) ---------------------------------------------------------------
function onType() {
  const q = inputEl.value;
  responseEl.classList.add("hidden");
  clearTimeout(debounceTimer);
  if (!q.trim()) {
    hideSuggestions();
    setStatus("");
    return;
  }
  debounceTimer = setTimeout(() => fetchSuggestions(q), DEBOUNCE_MS);
}

async function fetchSuggestions(q) {
  // cancel any earlier, slower request so results never arrive out of order
  if (inFlight) inFlight.abort();
  inFlight = new AbortController();
  setStatus("Searching…", false, true);
  try {
    const recency = recencyEl.checked;
    const res = await fetch(
      `/suggest?q=${encodeURIComponent(q)}&recency=${recency}`,
      { signal: inFlight.signal }
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderSuggestions(data);
    setStatus(
      data.suggestions.length
        ? `${data.suggestions.length} suggestions · served from ${data.source}`
        : "No matches"
    );
  } catch (err) {
    if (err.name === "AbortError") return; // superseded by a newer keystroke
    hideSuggestions();
    setStatus("Could not reach the server. Is the backend running?", true);
  }
}

function renderSuggestions(data) {
  currentItems = data.suggestions || [];
  activeIndex = -1;
  if (!currentItems.length) {
    hideSuggestions();
    return;
  }
  listEl.innerHTML = "";
  currentItems.forEach((item, i) => {
    const li = document.createElement("li");
    li.className = "suggestion";
    li.setAttribute("role", "option");
    li.dataset.index = i;
    const trendingBadge = item.recent > 0 ? '<span class="badge">trending</span>' : "";
    li.innerHTML =
      `<span class="q">${escapeHtml(item.query)}${trendingBadge}</span>` +
      `<span class="meta">${fmtCount(item.count)} searches</span>`;
    li.addEventListener("mousedown", (e) => {
      e.preventDefault(); // keep input focus
      choose(item.query);
    });
    listEl.appendChild(li);
  });
  listEl.classList.remove("hidden");
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// ---- keyboard navigation -------------------------------------------------------------------
function onKeyDown(e) {
  const visible = !listEl.classList.contains("hidden") && currentItems.length;
  if (e.key === "ArrowDown" && visible) {
    e.preventDefault();
    activeIndex = (activeIndex + 1) % currentItems.length;
    highlight();
  } else if (e.key === "ArrowUp" && visible) {
    e.preventDefault();
    activeIndex = (activeIndex - 1 + currentItems.length) % currentItems.length;
    highlight();
  } else if (e.key === "Enter") {
    if (visible && activeIndex >= 0) {
      choose(currentItems[activeIndex].query);
    } else {
      submitSearch(inputEl.value);
    }
  } else if (e.key === "Escape") {
    hideSuggestions();
  }
}

function highlight() {
  [...listEl.children].forEach((li, i) =>
    li.classList.toggle("active", i === activeIndex)
  );
}

// ---- submit search -------------------------------------------------------------------------
function choose(query) {
  inputEl.value = query;
  hideSuggestions();
  submitSearch(query);
}

async function submitSearch(rawQuery) {
  const query = (rawQuery || "").trim();
  if (!query) return;
  hideSuggestions();
  setStatus("Submitting…", false, true);
  try {
    const res = await fetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    responseEl.textContent = JSON.stringify(data);
    responseEl.classList.remove("hidden");
    setStatus(`Recorded “${query}”. It will surface in suggestions/trending shortly.`);
    loadTrending();
  } catch (err) {
    setStatus("Search submission failed.", true);
  }
}

// ---- trending ------------------------------------------------------------------------------
async function loadTrending() {
  try {
    const res = await fetch("/trending?limit=10");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderTrending(data.trending || []);
  } catch (err) {
    /* trending is non-critical; leave the placeholder */
  }
}

function renderTrending(items) {
  if (!items.length) {
    trendingEl.innerHTML =
      '<li class="muted">No trending data yet — submit some searches.</li>';
    return;
  }
  trendingEl.innerHTML = "";
  items.forEach((item, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="rank">${i + 1}</span>${escapeHtml(item.query)}`;
    li.addEventListener("click", () => choose(item.query));
    trendingEl.appendChild(li);
  });
}

// ---- wire up -------------------------------------------------------------------------------
inputEl.addEventListener("input", onType);
inputEl.addEventListener("keydown", onKeyDown);
btnEl.addEventListener("click", () => submitSearch(inputEl.value));
recencyEl.addEventListener("change", () => {
  if (inputEl.value.trim()) fetchSuggestions(inputEl.value);
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".combo")) hideSuggestions();
});

// press "/" anywhere to jump into the search field
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== inputEl) {
    e.preventDefault();
    inputEl.focus();
  }
});

loadTrending();
setInterval(loadTrending, 15000); // refresh trending periodically
