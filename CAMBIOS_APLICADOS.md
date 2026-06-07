# Cambios aplicados al sitio Avanza Digital

Ejecutados sobre el código del repo, cruzando el plan de mejoras con el estado real.
**Importante:** al revisar el código encontré que **muchos quick wins del plan ya estaban hechos** (no los rehíce). Abajo separo lo que ya estaba, lo que apliqué ahora y lo que queda pendiente (requiere accesos externos o decisiones de contenido).

## ✅ Ya estaba hecho antes (verificado en el código, no se tocó)
- H1 único en `index.html` (el plan reportaba 3; hoy hay 1).
- Fuentes locales en las páginas raíz de servicio (`metalurgica/automatizar-ventas-pymes/guia-...`): ya migradas a `/fonts.css`.
- Font Awesome async (patrón `media="print" onload`) en las páginas raíz.
- `presencia-en-google (1).html` ya renombrado a `presencia-en-google.html` + enlace en `index.html` corregido + redirect 301 en `_redirects`.
- Regla de seguridad de `_redirects` para `/avanza-portal/*` (permite portal.html y admin.html, bloquea el resto con 404 duro).
- `nixpacks.toml` eliminado; `.htaccess` eliminado de la raíz.
- `_redirects` ya resuelve los archivos `VE__*` de Venezuela y los hubs LATAM.

## 🆕 Aplicado en esta pasada

### SEO
- **hreflang recíproco en las 24 páginas con versión por país** (home, metalúrgica, agro, logística × AR/MX/CO/CL/PE/VE + `x-default`→AR). Era el hallazgo crítico y estaba al 0%.
- **JSON-LD en `alianzas.html`**: `BreadcrumbList` + `Service` (programa de partners de 2 canales). No agregué `FAQPage` porque la página no tiene un FAQ visible real (sería schema inválido para Google).

### Performance
- **Google Fonts → fuentes locales** en las **16 páginas país** (MX/CO/CL/PE/VE) que aún cargaban `fonts.googleapis.com` (render-blocking). Quedan apuntando a `/fonts.css` + preload de inter-400/600. *Los demos NO se tocaron a propósito:* usan otras tipografías (Chakra Petch, Barlow, Roboto Condensed) y están bloqueados en robots.txt.
- **`defer` en `script.js`** en las 6 páginas que lo cargaban bloqueante (`alianzas`, `alianzas-canal1`, `alianzas-canal2`, `automatizar-ventas-pymes`, `guia-automatizacion-leads-industriales`, `recursos`).

### Cumplimiento (GDPR / tráfico UE)
- **Banner de consentimiento + Google Consent Mode v2** en las 41 páginas de marketing/país:
  - Snippet inline de `consent default = denied` **antes** de GTM (verificado el orden en las 41).
  - Nuevo archivo `consent.js` autocontenido (banner Aceptar/Rechazar, recuerda la elección en localStorage, dispara `gtag('consent','update')` y eventos a `dataLayer`).
  - No toca el portal ni los demos.

### Higiene
- Renombrado `gitignore` → `.gitignore` en la raíz (el contenido ya cubría `.db`, `.env`, `__pycache__`, etc.).

## ⏳ Pendiente — requiere tu acción / accesos externos
- **Diferenciar el contenido de las páginas por país** (casos, ciudades, datos locales). El hreflang ya está, pero si las páginas siguen calcadas Google las puede tratar como thin content. *Decisión de contenido — no lo autogeneré.*
- **Enlazado interno hacia páginas dinero** y subir las landings de la pág. 2 a la 1: trabajo de contenido/criterio.
- **GA4 key events** (formularios, WhatsApp, descargas, calculadora), embudo /alianzas, cross-domain con onrender, vincular Search Console: se configuran en GTM/GA4 (consola), no en el código.
- **UTMs** en LinkedIn/IG/WhatsApp/email: convención operativa.
- **Imágenes WebP con `<picture>`/`srcset`** y `loading="lazy"` (excepto hero/LCP): conviene hacerlo por página con cuidado.
- **CSS:** sincronizar `style.min.css` con `style.css` y purgar con safelist — el plan lo marca como riesgoso; no lo toqué.
- **Seguridad/infra:** rotar secretos, RLS en Supabase, purgar `test_avanza.db` del historial de git (`git filter-repo`/BFG), mover `portal.html`/`admin.html` a `/portal/`. Requieren acceso a los servicios.

---

## 🆕 Segunda pasada

### Medición (lo más importante que faltaba)
- **`events.js`** (nuevo, cableado en las 41 páginas con `defer`): empuja key events estandarizados a `dataLayer` sin tocar el HTML de cada botón:
  - `lead_whatsapp` — cualquier link a `wa.me`/`whatsapp.com`.
  - `file_download` — descargas `.pdf` (lead magnets), con `file_name`.
  - `form_submit` — todos los formularios, con `form_id`.
  - `partner_channel_click` — clics a canal1 vs canal2 (incluye `portal.html?canal=`).
  - `tool_use` — uso de `calculadora-ineficiencia` y `auditoria-digital`.
- Quedan listos para que en GTM crees un tag GA4 por evento (trigger = Custom Event con el mismo nombre).

### SEO / indexación
- **Sitemap: eliminé `/clinica.html`** — estaba listado pero el archivo no existe (daba 404 al rastrear). Verifiqué las 47 URLs; el resto resuelve (archivo o regla de `_redirects`). Quedan 46.
- **`lastmod` actualizado a 2026-06-07** en las 25 URLs que modifiqué (home, servicios y todas las país) para forzar el recrawl del hreflang/JSON-LD nuevo.

### Imágenes (revisado — sin acción necesaria)
- No hay `<img>` raster estáticos para convertir a WebP ni para `loading="lazy"`: las páginas de servicio usan fondos CSS, los únicos `<img>` son dinámicos (dentro de JS) y la única `.jpg` es la imagen Open Graph (correcto que siga en jpg/png). El punto del plan queda cubierto por no aplicar.

---

## 🆕 Tercera pasada — CSS (grupo 3, con verificación)

### Sincronización full ↔ min (ya estaba resuelta — verificado)
- Comprobé que **todas** las páginas ya usaban `style.min.css` (ninguna en la full) y que, al canonicalizar `style.css` y `style.min.css` con el mismo minificador, daban **byte-idéntico**. O sea la min ya era una minificación fiel de la full: el riesgo del plan (8 reglas distintas, 3 páginas en la full) ya estaba neutralizado. No hizo falta tocar nada acá.

### Purga de CSS muerto (con safelist y triple verificación)
- Confirmé que el **portal NO carga `style.min.css`** (es autocontenido, CSS inline) → purgar no puede romperlo.
- Corrí **PurgeCSS** escaneando TODO el repo (65 HTML + 5 JS) con safelist para clases de estado dinámico (`active`, `open`, `on`, `av-dot`, etc.), **conservando todos los keyframes y variables CSS**.
- **Verificación (lo importante):**
  1. De 37 clases candidatas a eliminar, crucé cada una contra los `class="..."`, `classList.*` y `className` de todo el repo → **0 estaban realmente aplicadas**.
  2. Spot-check manual de las "sospechosas" (`comparison-table`, `testimonial-card`, `roi-modal`, etc.) → 0 usos.
  3. Comparación a nivel de selector completo (original vs purgado): 49 selectores muertos eliminados, **ninguno toca estado dinámico** (`active/on/[open]/slider/av-dot` intactos).
- **Resultado:** `style.min.css` 26.415 → **21.973 bytes (−17%)**. Purgué también `style.css` (fuente legible) con el mismo safelist para que fuente y min queden **alineadas** (verificado: canonicalizan idénticas). Ambas parsean OK.

> Aun así, conviene una revisión visual rápida en el navegador antes de deployar (sobre todo home, alianzas, calculadora y una página país), por las dudas de algún estilo que dependa de un selector raro. La verificación automática dio limpio, pero el ojo humano sobre el render es el cierre ideal.
