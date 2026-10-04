const CACHE_NAME = "smartlib-shell-v4";
const SHELL = ["/mobile/", "/mobile-assets/app.css", "/mobile-assets/app.js", "/mobile-assets/evsu-smartlib-logo.jpg", "/mobile-assets/evsu-smartlib-icon.png", "/mobile-manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || !SHELL.includes(url.pathname)) return;
  event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
});
