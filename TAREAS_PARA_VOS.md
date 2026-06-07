# Lo que tenés que hacer vos (yo no tengo acceso)

Esto NO se resuelve tocando el código del repo: necesita tus cuentas/consolas o son decisiones de contenido.

## 1. GTM / GA4 — activar los eventos (alta prioridad)
El `events.js` ya empuja los eventos a `dataLayer`. Falta crearlos como tags en GTM:
1. En **GTM (GTM-P499M76P)** → por cada evento (`lead_whatsapp`, `file_download`, `form_submit`, `partner_channel_click`, `tool_use`): creá un **Trigger** tipo *Custom Event* con ese nombre exacto, y un **Tag GA4 Event** que lo dispare (pasando los parámetros: `link_url`, `file_name`, `form_id`, `channel`, `tool`, `page_path`).
2. En **GA4** → marcá como **key events** (conversiones) al menos `lead_whatsapp`, `form_submit` y `file_download`.
3. **Probá con Vista previa de GTM** que cada evento aparezca al hacer la acción.

## 2. GA4 — configuración de cuenta
- **Cross-domain** con `avanza-digital.onrender.com` (el portal). Sin esto se pierde toda la conversión dentro del portal. GA4 → Admin → Data Streams → Configure tag settings → Configure your domains.
- **Vincular Search Console con GA4** (Admin → Search Console links) para el reporte orgánico nativo.
- **Investigar el tráfico de EE.UU. en desktop** (802 sesiones): si es ruido/bots, aplicar filtros de datos en GA4.

## 3. Consentimiento — verificar
- El banner (`consent.js`) y el Consent Mode v2 ya están. **Confirmá en GTM** que tus tags estén en modo "respetar consentimiento" (Consent settings de cada tag) para que el `default denied` tenga efecto real.

## 4. Contenido (decisión tuya, no lo autogeneré)
- **Diferenciar las páginas por país**: el hreflang ya está, pero si las versiones MX/CO/CL/PE/VE siguen calcadas, Google las puede ver como thin content. Sumá casos locales, ciudades, datos de cada mercado.
- **Enlazado interno** desde home y blogs hacia las páginas dinero, con anchor text descriptivo; y enlazar los 4 blogs entre sí (clúster).
- **UTMs**: definí convención para LinkedIn/IG/WhatsApp/email y usala siempre (mata el "Direct/Unassigned").

## 5. Infra / seguridad (requiere acceso a los servicios)
- **Rotar secretos** (`JWT_SECRET`, `ADMIN_API_KEY`, `MP_ACCESS_TOKEN`, `MP_WEBHOOK_SECRET`, MailerLite, Groq). Rotar `JWT_SECRET` cierra sesiones del portal → hacelo en horario de bajo tráfico.
- **Supabase**: confirmar que las credenciales viven solo en variables de entorno de Render, que la `service_role` key no está en ningún archivo público y que **RLS está activado**.
- **Purgar `test_avanza.db` del historial de git** (sigue recuperable desde el commit ff33929):
  ```bash
  # opción BFG
  bfg --delete-files test_avanza.db
  git reflog expire --expire=now --all && git gc --prune=now --aggressive
  # o git filter-repo
  git filter-repo --path test_avanza.db --invert-paths
  ```
  Avisá a quien tenga el repo clonado (deberá re-clonar). Si el repo es público, pasalo a privado.
- **No deployar `avanza-portal/` a Netlify** (fix definitivo): mover `portal.html`/`admin.html` a `/portal/`, actualizar los links en `alianzas-canal1/2` y dejar de subir el código Python al frontend. Mientras tanto, la regla de `_redirects` ya bloquea el resto.

## 6. Windsor.ai / medición sostenible
- Reactivar un plan que cubra Search Console + Analytics + PageSpeed a la vez (el gratis solo permite 1 fuente).
- Dejar correr PageSpeed para tener histórico.

## ⚠️ No tocar sin cuidado (riesgo, marcado en el plan)
- **Sincronizar `style.min.css` con `style.css`** antes de unificar (difieren ~8 reglas; 3 páginas usan la full).
- **PurgeCSS** solo con safelist o escaneando `script.js` (usa `classList` dinámico), o se rompen menús/toggles.
