/* Avanza Digital — Medición de key events → dataLayer (GTM-P499M76P)
   Empuja eventos estandarizados. Luego, en GTM, creá un tag GA4 "event"
   por cada uno (trigger = Custom Event con el mismo nombre) y marcá como
   key event en GA4 los que correspondan (lead_whatsapp, form_submit,
   file_download, partner_channel_click, tool_use). No requiere tocar el HTML. */
(function () {
  if (window.__avzEvents) return; window.__avzEvents = true;
  window.dataLayer = window.dataLayer || [];
  function push(o){ try{ window.dataLayer.push(o); }catch(e){} }
  function path(){ return location.pathname; }

  // Click delegado para enlaces (WhatsApp, descargas, canales de partner)
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a') : null;
    if (!a) return;
    var href = a.getAttribute('href') || '';

    // WhatsApp → lead
    if (/wa\.me|api\.whatsapp\.com/i.test(href)) {
      push({ event: 'lead_whatsapp', link_url: href, page_path: path(),
             cta_text: (a.textContent || '').trim().slice(0, 80) });
      return;
    }
    // Descargas de PDF (lead magnets)
    if (/\.pdf(\?|#|$)/i.test(href)) {
      var file = href.split('/').pop().split('?')[0];
      push({ event: 'file_download', file_name: file, link_url: href, page_path: path() });
      return;
    }
    // Canales del programa de partners
    if (/alianzas-canal1|portal\.html\?canal=1|[?&]canal=1/i.test(href)) {
      push({ event: 'partner_channel_click', channel: 'canal1', link_url: href, page_path: path() });
    } else if (/alianzas-canal2|portal\.html\?canal=2|[?&]canal=2/i.test(href)) {
      push({ event: 'partner_channel_click', channel: 'canal2', link_url: href, page_path: path() });
    }
  }, true);

  // Envío de formularios (contacto, lead magnet, cotizador, auditoría)
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f || f.tagName !== 'FORM') return;
    var id = f.id || f.getAttribute('name') || 'form';
    push({ event: 'form_submit', form_id: id, page_path: path() });

    // Herramientas: calculadora de ineficiencia y auditoría digital
    var p = path().toLowerCase();
    if (p.indexOf('calculadora') > -1) push({ event: 'tool_use', tool: 'calculadora_ineficiencia', page_path: path() });
    if (p.indexOf('auditoria') > -1)  push({ event: 'tool_use', tool: 'auditoria_digital',       page_path: path() });
  }, true);
})();
