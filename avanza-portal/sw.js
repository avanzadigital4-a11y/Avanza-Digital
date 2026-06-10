/* Service Worker del Portal de Aliados — Avanza Digital
 *
 * Estrategia deliberadamente MINIMALISTA (fase 1 del PWA):
 *  - network-first SIEMPRE: el portal cambia seguido y la data es en vivo,
 *    así que nunca servimos HTML/API viejos desde caché.
 *  - solo guardamos una copia de respaldo del shell para mostrar un aviso
 *    offline decente si el aliado abre la app sin conexión.
 *  - existe principalmente para que el portal sea INSTALABLE (Chrome exige
 *    un fetch handler). Las notificaciones push serán la fase 2.
 *
 * Para invalidar el caché en un deploy: subir la versión de CACHE.
 */
const CACHE = 'avanza-portal-v1';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Solo GET; POSTs y demás van directo a la red.
  if (req.method !== 'GET') return;

  // Navegaciones (abrir la app): red primero, copia de respaldo si falla.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copia = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copia)).catch(() => {});
          return res;
        })
        .catch(() =>
          caches.match(req).then((hit) =>
            hit || new Response(
              '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sin conexión — Avanza</title></head>' +
              '<body style="margin:0;background:#050505;color:#e2e8f0;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:24px;">' +
              '<div><div style="font-size:2.4rem;margin-bottom:12px;">📡</div><h1 style="font-size:1.1rem;margin:0 0 8px;">Sin conexión</h1>' +
              '<p style="color:#a1a1aa;font-size:.9rem;line-height:1.6;max-width:320px;">El portal necesita internet para mostrarte tus leads y comisiones en vivo. Reintentá cuando vuelva la señal.</p></div></body></html>',
              { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
            )
          )
        )
    );
    return;
  }

  // Resto de GETs (fuentes, íconos, CSS de CDN): red primero, caché de respaldo.
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok && (req.url.includes('/icons/') || req.url.includes('fonts.g'))) {
          const copia = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copia)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});