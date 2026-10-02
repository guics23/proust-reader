// Offline support. The app shell and library.json are fetched fresh when
// online and served from cache when offline; volume files are cached for good
// (their URL carries a version, so a rebuild is a new URL).
const CACHE = "reader-v3";
const SHELL = ["./", "index.html", "app.css", "app.js", "manifest.webmanifest", "icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;

  if (url.pathname.endsWith("/books/library.json")) {
    e.respondWith(fetch(e.request).then((res) => put(e.request, res))
      .catch(() => caches.match(e.request)));
  } else if (url.pathname.includes("/books/")) {
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)
      .then((res) => { dropOldVersions(url); return put(e.request, res); })));
  } else {
    e.respondWith(fetch(e.request).then((res) => put(e.request, res))
      .catch(() => caches.match(e.request)));
  }
});

function put(req, res) {
  if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
  return res;
}

async function dropOldVersions(url) {
  const c = await caches.open(CACHE);
  for (const req of await c.keys()) {
    const u = new URL(req.url);
    if (u.pathname === url.pathname && u.search !== url.search) c.delete(req);
  }
}
