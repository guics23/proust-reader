"use strict";

// ------------------------------------------------------------------ storage
// Everything the reader remembers lives under one localStorage key:
//   current: id of the open volume
//   pos:     { volumeId: key }   — key = index of the page's first sentence
//   progress: { volumeId: 0..1 } — for the library page
//   marks:   [{ vol, key, at, snippet, chapter }]
//   size, theme
// Keys are sentence indices rather than page numbers, so positions and
// bookmarks survive a rebuild that regroups pages.

const STORE_KEY = "reader.v1";
const store = loadStore();

function loadStore() {
  const fallback = { current: null, pos: {}, progress: {}, marks: [], size: 1.15, theme: "auto" };
  try {
    return { ...fallback, ...JSON.parse(localStorage.getItem(STORE_KEY) || "{}") };
  } catch {
    return fallback;
  }
}
function saveStore() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(store)); } catch { /* private mode */ }
}

// ------------------------------------------------------------------ state

const $ = (sel) => document.querySelector(sel);
const el = {
  reader: $("#reader"), page: $("#page"),
  top: $("#top"), bottom: $("#bottom"),
  fill: $("#fill"), ticks: $("#ticks"), progress: $("#progress"),
  mark: $("#mark-btn"), sheet: $("#sheet"), status: $("#status"),
};
let library = [];
let book = null;          // the open volume's JSON
let index = 0;            // current page index
const books = new Map();  // id → loaded volume

// ------------------------------------------------------------------ loading

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

async function loadBook(id) {
  if (books.has(id)) return books.get(id);
  const entry = library.find((v) => v.id === id);
  if (!entry) throw new Error(`unknown volume ${id}`);
  const data = await fetchJSON(`books/${entry.file}?v=${entry.version}`);
  books.set(id, data);
  return data;
}

async function openBook(id, { key = null, atEnd = false } = {}) {
  book = await loadBook(id);
  store.current = id;
  if (atEnd) index = book.pages.length - 1;
  else index = pageForKey(key ?? store.pos[id] ?? 0);
  document.documentElement.lang = book.languages[0];
  el.top.lang = book.languages[0];
  el.bottom.lang = book.languages[1];
  document.title = book.title[book.languages[0]];
  drawTicks();
  render();
}

function pageForKey(key) {
  // Last page whose first sentence is <= key.
  const pages = book.pages;
  let lo = 0, hi = pages.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (pages[mid][2] <= key) lo = mid; else hi = mid - 1;
  }
  return lo;
}

function chapterOf(i) {
  let c = 0;
  book.chapters.forEach((ch, k) => { if (ch.page <= i) c = k; });
  return c;
}

// ------------------------------------------------------------------ rendering

function escapeHTML(s) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function formatText(s) {
  return s.split("\n").map((p) =>
    `<p>${escapeHTML(p).replace(/_([^_]+)_/g, "<em>$1</em>")}</p>`).join("");
}

function render() {
  const [top, bottom, key] = book.pages[index];
  el.top.firstElementChild.innerHTML = formatText(top);
  el.bottom.firstElementChild.innerHTML = formatText(bottom);
  el.top.scrollTop = el.bottom.scrollTop = 0;
  updateFades(el.top);
  updateFades(el.bottom);

  const pct = book.pages.length > 1 ? (index / (book.pages.length - 1)) * 100 : 100;
  el.fill.style.width = `${pct}%`;
  el.progress.setAttribute("aria-valuenow", Math.round(pct));
  const marked = store.marks.some((m) => m.vol === book.id && m.key === key);
  el.mark.setAttribute("aria-pressed", String(marked));

  store.pos[book.id] = key;
  store.progress[book.id] = pct / 100;
  saveStore();
}

function drawTicks() {
  const n = book.pages.length;
  el.ticks.innerHTML = book.chapters.slice(1)
    .map((ch) => `<i style="left:${(ch.page / Math.max(n - 1, 1)) * 100}%"></i>`).join("");
}

function updateFades(half) {
  const more = half.scrollHeight - half.clientHeight;
  half.classList.toggle("more-above", half.scrollTop > 4);
  half.classList.toggle("more-below", more - half.scrollTop > 4);
}
// Scrolling one half scrolls the other to the same point of its own text, so
// both reach their bottoms together however unequal the two pages are.
// `driver` ignores the scroll event our own write provokes in the other half;
// it is released on the next frame (scroll events are dispatched before
// animation callbacks).
let driver = null, driverFrame = 0;
function syncScroll(src, dst) {
  if (driver && driver !== src) return;
  driver = src;
  if (!driverFrame) {
    driverFrame = requestAnimationFrame(() => { driver = null; driverFrame = 0; });
  }
  const srcMax = src.scrollHeight - src.clientHeight;
  const dstMax = Math.max(dst.scrollHeight - dst.clientHeight, 0);
  const target = srcMax > 0 ? (src.scrollTop / srcMax) * dstMax : 0;
  if (Math.abs(dst.scrollTop - target) > 0.5) {
    dst.scrollTop = target;
    updateFades(dst);
  }
}

el.top.addEventListener("scroll", () => { updateFades(el.top); syncScroll(el.top, el.bottom); }, { passive: true });
el.bottom.addEventListener("scroll", () => { updateFades(el.bottom); syncScroll(el.bottom, el.top); }, { passive: true });
window.addEventListener("resize", () => { if (book) { updateFades(el.top); updateFades(el.bottom); } });

function applyPrefs() {
  document.documentElement.style.setProperty("--size", `${store.size}rem`);
  if (store.theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = store.theme;
  const names = { auto: "auto", light: "clair", sepia: "sépia", dark: "sombre" };
  $("#theme-btn").textContent = `Thème : ${names[store.theme]}`;
}

// ------------------------------------------------------------------ navigation

let busy = false;

async function go(dir, fromOffset = 0) {
  if (busy) return;
  const next = index + dir;
  const vol = library.findIndex((v) => v.id === book.id);
  const crossing = next < 0 || next >= book.pages.length;
  const neighbour = library[vol + dir];
  if (crossing && !neighbour) { snapBack(); return; }

  busy = true;
  const w = el.reader.clientWidth;
  const page = el.page;
  page.classList.add("animate");
  page.style.transform = `translateX(${-dir * w * 0.35}px)`;
  page.style.opacity = "0";
  await wait(200);

  if (crossing) {
    history.replaceState(null, "", `#${encodeURIComponent(neighbour.id)}`);
    await openBook(neighbour.id, { key: dir > 0 ? 0 : null, atEnd: dir < 0 });
    toast(neighbour.title[book.languages[0]]);
  } else {
    index = next;
    render();
  }

  page.classList.remove("animate");
  page.style.transform = `translateX(${dir * w * 0.25}px)`;
  void page.offsetWidth;   // restart the transition from the far side
  page.classList.add("animate");
  page.style.transform = "";
  page.style.opacity = "";
  await wait(220);
  page.classList.remove("animate");
  busy = false;
}

function snapBack() {
  el.page.classList.add("animate");
  el.page.style.transform = "";
  el.page.style.opacity = "";
  setTimeout(() => el.page.classList.remove("animate"), 220);
}

function jump(i) {
  index = Math.max(0, Math.min(i, book.pages.length - 1));
  render();
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Swipe: horizontal drags turn the page; vertical drags scroll a half natively.
(() => {
  let start = null, horizontal = null, dx = 0;
  el.reader.addEventListener("pointerdown", (e) => {
    if (busy || e.button > 0) return;
    start = { x: e.clientX, y: e.clientY, t: e.timeStamp, id: e.pointerId };
    horizontal = null; dx = 0;
  });
  el.reader.addEventListener("pointermove", (e) => {
    if (!start || e.pointerId !== start.id) return;
    dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    if (horizontal === null && Math.hypot(dx, dy) > 10) {
      horizontal = Math.abs(dx) > Math.abs(dy) * 1.2;
      if (horizontal) el.reader.setPointerCapture(e.pointerId);
    }
    if (horizontal) {
      const atEdge = (dx > 0 && index === 0) || (dx < 0 && index === book.pages.length - 1);
      const shift = atEdge ? dx * 0.25 : dx * 0.8;
      el.page.style.transform = `translateX(${shift}px)`;
      el.page.style.opacity = String(1 - Math.min(Math.abs(shift) / el.reader.clientWidth, 0.6));
    }
  });
  const end = (e) => {
    if (!start || e.pointerId !== start.id) return;
    const v = Math.abs(dx) / Math.max(e.timeStamp - start.t, 1);   // px per ms
    if (horizontal && (Math.abs(dx) > 60 || (v > 0.5 && Math.abs(dx) > 20))) go(dx < 0 ? 1 : -1);
    else if (horizontal) snapBack();
    start = null;
  };
  el.reader.addEventListener("pointerup", end);
  el.reader.addEventListener("pointercancel", (e) => { if (horizontal) snapBack(); start = null; });
})();

document.addEventListener("keydown", (e) => {
  if (!book || document.body.classList.contains("at-home")) return;
  if (!el.sheet.hidden) { if (e.key === "Escape") closeSheet(); return; }
  if (e.key === "ArrowRight" || e.key === "PageDown" || e.key === " ") { e.preventDefault(); go(1); }
  else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(-1); }
  else if (e.key === "b") toggleMark();
});

// ------------------------------------------------------------------ bookmarks

function toggleMark() {
  const key = book.pages[index][2];
  const at = store.marks.findIndex((m) => m.vol === book.id && m.key === key);
  if (at >= 0) {
    store.marks.splice(at, 1);
    toast("Marque-page retiré");
  } else {
    const ch = book.chapters[chapterOf(index)];
    store.marks.unshift({
      vol: book.id, key, at: Date.now(),
      snippet: book.pages[index][0].slice(0, 160),
      chapter: ch.title[book.languages[0]],
    });
    toast("Marque-page ajouté");
  }
  render();
}
el.mark.addEventListener("click", toggleMark);

// ------------------------------------------------------------------ sheet

let tab = "chapters";

function openSheet(which = tab) {
  tab = which;
  el.sheet.hidden = false;
  drawSheet();
  const cur = el.sheet.querySelector(`#tab-${tab} .current`);
  if (cur) cur.scrollIntoView({ block: "center" });
}
function closeSheet() { el.sheet.hidden = true; }

function drawSheet() {
  for (const b of el.sheet.querySelectorAll("[role=tab]")) {
    const on = b.dataset.tab === tab;
    b.setAttribute("aria-selected", String(on));
    $(`#tab-${b.dataset.tab}`).hidden = !on;
  }
  const [a, b] = book.languages;
  const curCh = chapterOf(index);

  $("#tab-chapters").innerHTML = book.chapters.map((ch, k) => {
    const next = book.chapters[k + 1]?.page ?? book.pages.length;
    const pct = index >= ch.page && index < next
      ? ` · ${Math.round(((index - ch.page) / Math.max(next - ch.page, 1)) * 100)} %` : "";
    return `<li class="${k === curCh ? "current" : ""}"><button class="go" data-page="${ch.page}">
      <span>${escapeHTML(ch.title[a])}</span>
      <span class="sub">${escapeHTML(ch.title[b])}${pct}</span></button></li>`;
  }).join("");

  const marks = store.marks;
  $("#tab-marks").innerHTML = marks.length ? marks.map((m, k) => {
    const vol = library.find((v) => v.id === m.vol);
    const volName = vol && m.vol !== book.id ? `${vol.title[a] ?? m.vol} · ` : "";
    return `<li><button class="go" data-mark="${k}">
      <span class="snippet">${escapeHTML(m.snippet.replace(/_/g, ""))}</span>
      <span class="sub">${escapeHTML(volName + m.chapter)} · ${new Date(m.at).toLocaleDateString()}</span>
      </button><button class="del" data-del="${k}" aria-label="Supprimer">×</button></li>`;
  }).join("") : `<li class="empty">Aucun marque-page. Touchez le signet en bas à droite pour marquer la page.</li>`;

  $("#tab-books").innerHTML = `<li><button class="go" data-book="">
      <span>← Bibliothèque</span></button></li>` + library.map((v) => {
    const pos = store.pos[v.id];
    return `<li class="${v.id === book.id ? "current" : ""}"><button class="go" data-book="${v.id}">
      <span>${escapeHTML(v.title[a] ?? v.id)}</span>
      <span class="sub">${escapeHTML(v.title[b] ?? "")}${pos ? " · commencé" : ""}</span></button></li>`;
  }).join("");
}

el.sheet.addEventListener("click", async (e) => {
  if (e.target === el.sheet) return closeSheet();
  const t = e.target.closest("button");
  if (!t) return;
  if (t.dataset.tab) { tab = t.dataset.tab; drawSheet(); return; }
  if (t.dataset.page) { jump(+t.dataset.page); closeSheet(); return; }
  if (t.dataset.del) { store.marks.splice(+t.dataset.del, 1); saveStore(); drawSheet(); render(); return; }
  if (t.dataset.mark) {
    const m = store.marks[+t.dataset.mark];
    closeSheet();
    if (m.vol !== book.id) { store.pos[m.vol] = m.key; location.hash = encodeURIComponent(m.vol); }
    else jump(pageForKey(m.key));
    return;
  }
  if (t.dataset.book) {
    closeSheet();
    location.hash = t.dataset.book ? encodeURIComponent(t.dataset.book) : "";
    return;
  }
});
$("#sheet-close").addEventListener("click", closeSheet);
$("#menu-btn").addEventListener("click", () => openSheet("chapters"));
el.progress.addEventListener("click", () => openSheet("chapters"));
$("#font-down").addEventListener("click", () => { store.size = Math.max(0.85, +(store.size - 0.05).toFixed(2)); applyPrefs(); saveStore(); render(); });
$("#font-up").addEventListener("click", () => { store.size = Math.min(1.8, +(store.size + 0.05).toFixed(2)); applyPrefs(); saveStore(); render(); });
$("#theme-btn").addEventListener("click", () => {
  const order = ["auto", "light", "sepia", "dark"];
  store.theme = order[(order.indexOf(store.theme) + 1) % order.length];
  applyPrefs(); saveStore();
});

// ------------------------------------------------------------------ misc

let toastTimer;
function toast(msg) {
  let t = $("#toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.append(t); }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1400);
}

function showStatus(msg) {
  el.status.textContent = msg;
  el.status.hidden = !msg;
}

// ------------------------------------------------------------------ library page

function drawHome() {
  $("#shelf").innerHTML = library.map((v) => {
    const [a, b] = Object.keys(v.title);
    const p = store.progress[v.id];
    const started = p !== undefined;
    const state = started ? `Reprendre · ${Math.round(p * 100)} %` : "Commencer";
    return `<li class="${v.id === store.current ? "last" : ""}">
      <a href="#${encodeURIComponent(v.id)}">
        <span class="t">${escapeHTML(v.title[a])}</span>
        <span class="sub">${escapeHTML(v.title[b] ?? "")}</span>
        <span class="meta">${escapeHTML(v.author ?? "")}</span>
        <span class="state"><span class="bar"><span style="width:${(p ?? 0) * 100}%"></span></span>${state}</span>
      </a></li>`;
  }).join("");
}

// The URL hash names the open volume ("#1-swann"); no hash shows the library.
async function route() {
  closeSheet();
  const id = decodeURIComponent(location.hash.slice(1));
  const home = !library.some((v) => v.id === id);
  document.body.classList.toggle("at-home", home);
  $("#home").hidden = !home;
  if (home) {
    document.title = "Proust Reader";
    drawHome();
    return;
  }
  showStatus("Chargement…");
  try {
    await openBook(id);
    showStatus("");
  } catch (err) {
    showStatus(`Impossible de charger le livre. ${err.message}`);
  }
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ start

(async () => {
  applyPrefs();
  showStatus("Chargement…");
  try {
    library = await fetchJSON("books/library.json");
    if (!library.length) throw new Error("La bibliothèque est vide.");
    showStatus("");
    await route();
  } catch (err) {
    showStatus(`Impossible de charger la bibliothèque. ${err.message}`);
  }
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
})();
