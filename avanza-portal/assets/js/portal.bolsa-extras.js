/* ===========================================================================
 * portal.bolsa-extras.js  ·  Extras de la Bolsa (Canal 1)
 * ---------------------------------------------------------------------------
 * Helpers que cuelgan de las tarjetas de lead de la Bolsa:
 *   - verHistorialLead(id): historial de intentos de un lead reciclado, para
 *     no repetir el laburo del anterior.
 *   - verReparto(id):       proyección del split setter/closer de un lead
 *     pasado a un compañero (transparencia antes de cerrar).
 * Usa los helpers globales del portal: `aliado`, `apiJSON`, `mostrarToast`, `API`.
 * =========================================================================== */

function _bxEsc(s) {
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  
  function _bxModal(html) {
    const ov = document.createElement('div');
    ov.id = '_bx-overlay';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;';
    ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
    ov.innerHTML = `
      <div style="background:var(--bg2,#16181d);border:1px solid var(--border);border-radius:16px;
                  padding:22px;max-width:480px;width:100%;max-height:80vh;overflow:auto;
                  box-shadow:0 20px 60px rgba(0,0,0,.5);">${html}
        <button onclick="document.getElementById('_bx-overlay').remove()"
          style="width:100%;background:transparent;color:var(--text-muted);border:1px solid var(--border);
                 border-radius:10px;padding:10px;margin-top:14px;cursor:pointer;font-weight:600;">Cerrar</button>
      </div>`;
    document.body.appendChild(ov);
  }
  
  /* Historial de intentos de un lead (para no repetir el laburo del anterior). */
  async function verHistorialLead(leadId) {
    try {
      const res = await apiJSON(`${API}/bolsa/${leadId}/historial`);
      if (!res.ok) { mostrarToast('No se pudo cargar el historial.', 'red'); return; }
      const d = await res.json();
      if (!d.existe) { mostrarToast('Lead no encontrado.', 'red'); return; }
      const filas = (d.historial || []).map(h => `
        <div style="border-left:2px solid var(--border);padding:8px 0 8px 12px;margin-bottom:6px;">
          <div style="font-size:.82rem;color:var(--text);font-weight:600;">${_bxEsc(h.resultado)}
            <span style="color:var(--text-dim);font-weight:400;">· ${_bxEsc(h.fecha)}</span></div>
          ${h.aliado ? `<div style="font-size:.74rem;color:var(--text-muted);">por ${_bxEsc(h.aliado)}</div>` : ''}
          ${h.nota ? `<div style="font-size:.78rem;color:var(--text-muted);margin-top:2px;">"${_bxEsc(h.nota)}"</div>` : ''}
        </div>`).join('') || '<div style="color:var(--text-muted);font-size:.85rem;">Sin intentos previos.</div>';
      _bxModal(`
        <div style="font-size:1.05rem;font-weight:800;color:var(--text);margin-bottom:4px;">Historial del lead</div>
        <div style="font-size:.82rem;color:var(--text-muted);margin-bottom:14px;">
          ${_bxEsc(d.empresa || '')} · ${d.intentos || 0} intento(s), ${d.reciclados || 0} reciclado(s)</div>
        ${filas}`);
    } catch (e) {}
  }
  
  /* Proyección del split setter/closer de un lead (transparencia antes de cerrar). */
  async function verReparto(leadId) {
    try {
      const res = await apiJSON(`${API}/aliados/${aliado.codigo}/reparto/lead/${leadId}`);
      if (!res.ok) { const e = await res.json(); mostrarToast(e.detail || 'No disponible.', 'red'); return; }
      const d = await res.json();
      const filas = (d.tabla || []).map(f => `
        <tr style="border-bottom:1px solid var(--border);">
          <td style="padding:7px 4px;color:var(--text);font-size:.82rem;">${_bxEsc(f.plan)}</td>
          <td style="padding:7px 4px;text-align:right;color:#c084fc;font-size:.82rem;">$${f.setter_usd}</td>
          <td style="padding:7px 4px;text-align:right;color:var(--primary);font-size:.82rem;">$${f.closer_usd}</td>
        </tr>`).join('');
      _bxModal(`
        <div style="font-size:1.05rem;font-weight:800;color:var(--text);margin-bottom:4px;">Reparto del deal</div>
        <div style="font-size:.82rem;color:var(--text-muted);margin-bottom:14px;">
          Setter ${d.split.setter}% / Closer ${d.split.closer}% · ${_bxEsc(d.nota || '')}</div>
        <table style="width:100%;border-collapse:collapse;">
          <thead><tr style="border-bottom:1px solid var(--border);">
            <th style="text-align:left;padding:6px 4px;font-size:.72rem;color:var(--text-dim);">Plan</th>
            <th style="text-align:right;padding:6px 4px;font-size:.72rem;color:#c084fc;">Setter</th>
            <th style="text-align:right;padding:6px 4px;font-size:.72rem;color:var(--primary);">Closer</th>
          </tr></thead><tbody>${filas}</tbody></table>`);
    } catch (e) {}
  }