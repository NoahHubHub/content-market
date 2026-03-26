const CACHE = 'cm-static-v1';

const PRECACHE = [
  '/offline',
  '/static/manifest.json',
  '/static/icon-192.svg',
  '/static/icon-512.svg',
  '/static/icon-maskable.svg',
];

// ── Install ────────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

// ── Activate ───────────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch ──────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  if (url.origin !== location.origin) return;

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE).then(cache => cache.put(request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/offline'))
    );
    return;
  }

  event.respondWith(fetch(request));
});

// ── Push Notifications ─────────────────────────────────────
self.addEventListener('push', event => {
  if (!event.data) return;

  let data = {};
  try { data = event.data.json(); } catch { data = { title: 'Content Market', body: event.data.text() }; }

  const title   = data.title || 'Content Market';
  const options = {
    body:    data.body  || '',
    icon:    data.icon  || '/static/icon-192.svg',
    badge:   '/static/icon-192.svg',
    data:    { url: data.url || '/' },
    vibrate: [200, 100, 200],
    tag:     data.tag   || 'cm-notification',
    renotify: true,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = event.notification.data?.url || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) {
        if (client.url === url && 'focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
