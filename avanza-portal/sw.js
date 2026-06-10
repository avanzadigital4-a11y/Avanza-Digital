/* Service Worker del Portal de Aliados — Avanza Digital
 *
 * Estrategia (v2):
 *  - Navegaciones (abrir la app): red primero; copia de respaldo si no hay señal.
 *  - CDN de fuentes e íconos (Font Awesome, Google Fonts) + /icons/ propios:
 *    cache-first con actualización en segundo plano. FIX v2: en la app
 *    instalada, si el CDN fallaba al arrancar, los íconos quedaban en blanco
 *    toda la sesión. Ahora la primera carga buena queda guardada y se usa de
 *    respaldo siempre.
 *  - TODO lo demás (llamadas a la API, datos en vivo): el SW NO lo toca.
 *
 * Para invalidar el caché en un deploy: subir la versión de CACHE.
 */
const CACHE = 'avanza-portal-v2';

// Hosts cuyos recursos son estáticos e inmutables → seguros de cachear fuerte.
const CDN_HOSTS = ['cdnjs.cloudflare.com', 'fonts.googleapis.com', 'fonts.gstatic.com'];

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

const PAGINA_OFFLINE =
  '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sin conexión — Avanza</title></head>' +
  '<body style="margin:0;background:#050505;color:#e2e8f0;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:24px;">' +
  '<div><div style="font-size:2.4rem;margin-bottom:12px;">📡</div><h1 style="font-size:1.1rem;margin:0 0 8px;">Sin conexión</h1>' +
  '<p style="color:#a1a1aa;font-size:.9rem;line-height:1.6;max-width:320px;">El portal necesita internet para mostrarte tus leads y comisiones en vivo. Reintentá cuando vuelva la señal.</p></div></body></html>';

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  const esCDN = CDN_HOSTS.includes(url.hostname);
  const esIconoPropio = url.pathname.includes('/icons/');

  // ── 1. Navegaciones (abrir la app): red primero, respaldo si falla ─────────
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
            hit || new Response(PAGINA_OFFLINE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })
          )
        )
    );
    return;
  }

  // ── 2. Fuentes/íconos (CDN + propios): cache-first ─────────────────────────
  // Sin esto, si cdnjs falla al abrir la app, Font Awesome no carga y todos
  // los íconos del portal se ven como cuadrados vacíos durante la sesión.
  if (esCDN || esIconoPropio) {
    event.respondWith(
      caches.match(req).then((hit) => {
        // Actualización en segundo plano (no bloquea la respuesta)
        const refrescar = fetch(req)
          .then((res) => {
            if (res && (res.ok || res.type === 'opaque')) {
              const copia = res.clone();
              caches.open(CACHE).then((c) => c.put(req, copia)).catch(() => {});
            }
            return res;
          })
          .catch(() => hit || Response.error());
        return hit || refrescar;
      })
    );
    return;
  }

  // ── 3. Todo lo demás (API, datos en vivo): el SW no interviene ─────────────
  // No llamamos a respondWith → el navegador maneja el request normalmente.
});