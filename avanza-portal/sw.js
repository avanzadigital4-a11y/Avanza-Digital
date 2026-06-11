/* Service Worker del Portal de Aliados — Avanza Digital
 *
 * v3 — MÍNIMO Y SEGURO (lección aprendida):
 *  - El SW SOLO intercepta navegaciones (abrir la app): red primero, con
 *    página de respaldo si no hay señal. Eso alcanza para que el portal sea
 *    instalable (Chrome solo exige que exista un fetch handler).
 *  - NO toca Font Awesome, Google Fonts, íconos ni la API. El navegador ya
 *    cachea los CDN perfecto por su cuenta (cdnjs manda immutable + 1 año).
 *    En v2 cacheábamos esas respuestas "opacas" sin poder verificar si eran
 *    válidas, y una copia rota quedaba envenenada y se servía para siempre
 *    (íconos en blanco; Ctrl+Shift+R los traía de vuelta porque saltea al SW).
 *  - AUTO-REPARACIÓN: al activarse borra los cachés envenenados de v1/v2 y,
 *    solo en ese caso, recarga la pestaña UNA vez para que los íconos vuelvan
 *    al instante sin que el usuario haga nada.
 *
 * Para invalidar el caché en un deploy: subir la versión de CACHE.
 */
const CACHE = 'avanza-portal-v3';
const CACHES_ENVENENADOS = ['avanza-portal-v1', 'avanza-portal-v2'];

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    let habiaVeneno = false;
    await Promise.all(keys.map((k) => {
      if (k === CACHE) return Promise.resolve();
      if (CACHES_ENVENENADOS.includes(k)) habiaVeneno = true;
      return caches.delete(k);
    }));
    await self.clients.claim();

    // Auto-reparación: si veníamos de un caché envenenado (v1/v2), recargar
    // las pestañas abiertas UNA vez para que los íconos carguen limpios.
    // En instalaciones nuevas o futuras versiones esto no se ejecuta.
    if (habiaVeneno) {
      const ventanas = await self.clients.matchAll({ type: 'window' });
      for (const c of ventanas) {
        try { await c.navigate(c.url); } catch (e) { /* algunos navegadores no lo permiten */ }
      }
    }
  })());
});

const PAGINA_OFFLINE =
  '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sin conexión — Avanza</title></head>' +
  '<body style="margin:0;background:#050505;color:#e2e8f0;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:24px;">' +
  '<div><div style="font-size:2.4rem;margin-bottom:12px;">📡</div><h1 style="font-size:1.1rem;margin:0 0 8px;">Sin conexión</h1>' +
  '<p style="color:#a1a1aa;font-size:.9rem;line-height:1.6;max-width:320px;">El portal necesita internet para mostrarte tus leads y comisiones en vivo. Reintentá cuando vuelva la señal.</p></div></body></html>';

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // ÚNICA intercepción: navegaciones. Todo lo demás (CSS, fuentes, íconos,
  // API) lo maneja el navegador directamente, sin pasar por este SW.
  if (req.method !== 'GET' || req.mode !== 'navigate') return;

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
});


// ─── Web Push: mostrar la notificación y abrir la app al tocarla ───
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch (e) { data = { title: 'Avanza Digital', body: (event.data && event.data.text && event.data.text()) || '' }; }
  const title = data.title || 'Avanza Digital';
  const options = {
    body: data.body || '',
    icon: 'icons/icon-192.png',
    badge: 'icons/icon-192.png',
    data: { url: data.url || 'portal.html' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const destino = (event.notification.data && event.notification.data.url) || 'portal.html';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) { if ('focus' in w) return w.focus(); }
      if (self.clients.openWindow) return self.clients.openWindow(destino);
    })
  );
});