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
const CACHE = 'avanza-portal-v5';
const CACHES_ENVENENADOS = ['avanza-portal-v1', 'avanza-portal-v2', 'avanza-portal-v3', 'avanza-portal-v4'];
// v4: además de navegaciones, cacheamos los módulos JS propios versionados
// por hash (assets/js/portal.*.<hash>.js). Son inmutables: el hash cambia
// con el contenido, así que cache-first es seguro y no hay veneno posible
// (a diferencia de los CDN opacos de v2). Una copia vieja queda huérfana
// cuando sube el hash y se barre al activar la próxima versión de CACHE.

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
  const url = new URL(req.url);

  // ── Módulos JS propios versionados: cache-first (inmutables por hash) ──
  // Mismo-origin y bajo /assets/js/ con extensión .js. La primera visita los
  // baja de red y los guarda; las siguientes los sirven del caché al instante
  // (clave para el arranque en frío en celular). Como el nombre lleva el hash
  // del contenido, nunca servimos una versión equivocada: un deploy nuevo pide
  // un nombre nuevo y la copia vieja queda inerte hasta el barrido de activate.
  if (req.method === 'GET' && url.origin === self.location.origin &&
      url.pathname.includes('/assets/js/') && url.pathname.endsWith('.js')) {
    event.respondWith(
      caches.match(req).then((hit) => {
        if (hit) return hit;
        return fetch(req).then((res) => {
          // Solo cacheamos respuestas propias y OK (no opacas): verificable.
          if (res && res.ok && res.type === 'basic') {
            const copia = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copia)).catch(() => {});
          }
          return res;
        });
      })
    );
    return;
  }

  // ── Navegaciones: red primero, respaldo offline ── Todo lo demás (CSS, fuentes, íconos,
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
  let _url = data.url || 'portal.html';
  if (_url === '/') _url = 'portal.html';   // nunca abrir la raíz (sitio público)
  const options = {
    body: data.body || '',
    icon: 'icons/icon-192.png',
    badge: 'icons/badge-96.png',          // badge transparente -> flechas, no cuadrado
    data: { url: _url },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  let destino = (event.notification.data && event.notification.data.url) || 'portal.html';
  if (destino === '/' || !destino) destino = 'portal.html';
  const target = new URL(destino, self.location).href;   // resuelve dentro de /avanza-portal/
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if (w.url && w.url.indexOf('/avanza-portal/') !== -1 && 'focus' in w) {
          if ('navigate' in w && w.url !== target) { w.navigate(target).catch(() => {}); }
          return w.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })
  );
});