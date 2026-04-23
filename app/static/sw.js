/* Stenvik Leads — Service Worker.
 *
 * Стратегия:
 *   - Статика (css/js/иконки/manifest): cache-first, обновляется в фоне.
 *   - Внешние шрифты и Lucide: cache-first (inter.css, lucide.min.js) —
 *     чтобы PWA не теряла типографику и иконки в офлайне.
 *   - HTML-страницы: network-first с fallback на последний успешный кэш.
 *   - API (/api/*, PATCH, /partials/*): только сеть (CRM-действия не должны
 *     уходить в кэш — всегда свежие).
 *   - Offline fallback: /offline → /offline.html.
 *
 * Версия кэша повышается при каждом деплое новой статики.
 */

const CACHE_VERSION = 'stenvik-leads-v2-scandi';
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const PAGES_CACHE = `${CACHE_VERSION}-pages`;
const VENDOR_CACHE = `${CACHE_VERSION}-vendor`;

const PRECACHE_URLS = [
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/offline.html',
];

// Внешние ресурсы, которые должны жить в офлайне: шрифт + иконки.
const VENDOR_PRECACHE = [
  'https://rsms.me/inter/inter.css',
  'https://unpkg.com/lucide@latest/dist/umd/lucide.min.js',
  'https://unpkg.com/htmx.org@2.0.3',
  'https://unpkg.com/alpinejs@3.14.1/dist/cdn.min.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    Promise.all([
      caches.open(STATIC_CACHE).then((cache) =>
        cache.addAll(PRECACHE_URLS).catch((err) => {
          console.warn('[sw] static precache failed:', err);
        })
      ),
      caches.open(VENDOR_CACHE).then((cache) =>
        Promise.all(
          VENDOR_PRECACHE.map((url) =>
            fetch(url, { mode: 'no-cors' })
              .then((resp) => cache.put(url, resp))
              .catch((err) => console.warn('[sw] vendor precache failed:', url, err))
          )
        )
      ),
    ]).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((n) => !n.startsWith(CACHE_VERSION))
          .map((n) => caches.delete(n))
      )
    ).then(() => self.clients.claim())
  );
});

function isStatic(url) {
  return url.pathname.startsWith('/static/') || url.pathname === '/favicon.ico';
}

function isVendor(url) {
  return url.hostname === 'rsms.me' || url.hostname === 'unpkg.com';
}

function isApi(url) {
  return url.pathname.startsWith('/api/') ||
         url.pathname.startsWith('/partials/') ||
         url.pathname === '/logout' ||
         url.pathname === '/login' ||
         url.pathname === '/register';
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  // Только GET — остальное (POST/PATCH) напрямую в сеть
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // API: всегда сеть
  if (isApi(url)) return;

  // Внешние шрифты/иконки — cache-first через VENDOR_CACHE
  if (isVendor(url)) {
    event.respondWith(
      caches.open(VENDOR_CACHE).then((cache) =>
        cache.match(req).then((cached) => {
          if (cached) return cached;
          return fetch(req, { mode: 'no-cors' }).then((fresh) => {
            cache.put(req, fresh.clone());
            return fresh;
          }).catch(() => cached);
        })
      )
    );
    return;
  }

  // Только same-origin для остального
  if (url.origin !== location.origin) return;

  // Static: cache-first
  if (isStatic(url)) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) {
          // background revalidate
          fetch(req).then((fresh) => {
            if (fresh && fresh.ok) {
              caches.open(STATIC_CACHE).then((c) => c.put(req, fresh.clone()));
            }
          }).catch(() => {});
          return cached;
        }
        return fetch(req).then((fresh) => {
          if (fresh.ok) {
            const copy = fresh.clone();
            caches.open(STATIC_CACHE).then((c) => c.put(req, copy));
          }
          return fresh;
        });
      })
    );
    return;
  }

  // HTML-страницы: network-first с cache fallback
  event.respondWith(
    fetch(req)
      .then((resp) => {
        if (resp.ok && resp.type === 'basic') {
          const copy = resp.clone();
          caches.open(PAGES_CACHE).then((c) => c.put(req, copy));
        }
        return resp;
      })
      .catch(() => {
        return caches.match(req).then((cached) => {
          if (cached) return cached;
          return caches.match('/static/offline.html');
        });
      })
  );
});

// Message API: клиент может попросить принудительно обновить SW
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
