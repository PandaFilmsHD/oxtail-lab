// Oxtail Lab service worker: always try the network first (fresh odds), fall back to the last saved copy when offline.
const CACHE = 'oxtail-v4';
self.addEventListener('install', e => { self.skipWaiting(); e.waitUntil(caches.open(CACHE).then(c => c.addAll(['./', 'index.html', 'style.css', 'app.js', 'chat.js', 'manifest.webmanifest', 'icons/icon-192.png']).catch(() => {}))); });
self.addEventListener('activate', e => e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;   // ESPN / AI calls go straight through
  const key = u.origin + u.pathname;   // one saved copy per file (ignore ?v=), so storage doesn't pile up
  e.respondWith(fetch(e.request).then(r => { if (r.ok) { const cp = r.clone(); caches.open(CACHE).then(c => c.put(key, cp)); } return r; })
    .catch(() => caches.match(key).then(r => r || caches.match(u.origin + u.pathname.replace(/[^/]*$/, '') + 'index.html'))));
});

// phone alerts sent straight from GitHub (web push): show it, and open Oxtail when tapped
self.addEventListener('push', e => {
  let d = {}; try { d = e.data ? e.data.json() : {}; } catch (x) { d = { title: 'Oxtail', body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'Oxtail', { body: d.body || '', icon: 'icons/icon-192.png', badge: 'icons/icon-192.png', tag: d.tag, data: { url: d.url || './' } }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || './', self.registration.scope).href;
  const id = new URL(url).searchParams.get('alert');
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
    const c = cs[0];
    if (c) { c.postMessage({ type: 'alert', id }); return c.focus(); }   // app already open: tell it which alert to show
    return self.clients.openWindow(url);
  }));
});
