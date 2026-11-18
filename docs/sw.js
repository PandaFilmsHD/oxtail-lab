// Oxtail Lab service worker: always try the network first (fresh odds), fall back to the last saved copy when offline.
const CACHE = 'oxtail-v22';
const ACACHE = 'oxtail-alerts';   // full alert reports that came inside the push
self.addEventListener('install', e => { self.skipWaiting(); e.waitUntil(caches.open(CACHE).then(c => c.addAll(['./', 'index.html', 'style.css', 'app.js', 'chat.js', 'manifest.webmanifest', 'icons/icon-192.png']).catch(() => {}))); });
self.addEventListener('activate', e => e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE && k !== ACACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
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
  const save = d.alert ? caches.open(ACACHE).then(c => c.put('alert-' + d.alert.id, new Response(JSON.stringify(d.alert), { headers: { 'Content-Type': 'application/json' } }))) : Promise.resolve();
  e.waitUntil(save.then(() => self.registration.showNotification(d.title || 'Oxtail', { body: d.body || '', icon: 'icons/icon-192.png', badge: 'icons/icon-192.png', tag: d.tag, data: { url: d.url || './', alert: d.alert || null } })));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || './', self.registration.scope).href;
  const id = new URL(url).searchParams.get('alert');
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
    const c = cs[0];
    const alert = e.notification.data && e.notification.data.alert;
    // save the tap: the app checks this every time it comes on screen, so the report shows even if the message below gets lost
    return caches.open(ACACHE).then(k => k.put('pending', new Response(JSON.stringify({ id, alert, t: Date.now() }), { headers: { 'Content-Type': 'application/json' } }))).then(() => {
      if (c) { try { c.postMessage({ type: 'alert', id, alert }); } catch (x) {} return c.focus ? c.focus().catch(() => self.clients.openWindow(url)) : self.clients.openWindow(url); }
      return self.clients.openWindow(url);
    });
  }));
});
