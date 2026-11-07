const CACHE_NAME = "smartlib-shell-v9";
const SHELL = ["/mobile/", "/mobile-assets/app.css", "/mobile-assets/app.js", "/mobile-assets/evsu-smartlib-logo.jpg", "/mobile-assets/evsu-smartlib-icon.png", "/mobile-manifest.json"];
const PAGES = ["/app", "/mobile", "/mobile/"];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('smartlib-shell-') && key !== CACHE_NAME).map(key => caches.delete(key)))));
  self.clients.claim();
});
self.addEventListener("fetch", event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (!PAGES.includes(url.pathname) && !SHELL.includes(url.pathname)) return;
  // Cache public app resources only; authenticated data is kept in the app's local storage snapshot.
  const key = PAGES.includes(url.pathname) ? '/mobile/' : url.pathname;
  event.respondWith((async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(request, {signal: controller.signal});
      if (!response.ok) throw new Error('Server unavailable');
      const cache = await caches.open(CACHE_NAME);
      await cache.put(key, response.clone());
      return response;
    } catch (error) {
      const cached = await caches.match(key);
      if (cached) return cached;
      throw error;
    } finally { clearTimeout(timeout); }
  })());
});
