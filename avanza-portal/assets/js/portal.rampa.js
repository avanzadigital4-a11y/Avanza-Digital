/* ===========================================================================
 * portal.rampa.js  ·  Pestaña "Mi Rampa" (Canal 1) + extras de bolsa
 * ---------------------------------------------------------------------------
 * Acompaña el primer cierre del aliado nuevo: progreso, checklist y mentor.
 * Si el aliado es mentor, ve también a sus mentees. Usa los helpers globales
 * del portal: `aliado`, `apiJSON`, `mostrarToast` y la constante `API`.
 *
 * Incluye además dos extras de Canal 1 que cuelgan de la Bolsa:
 *   - verHistorialLead(id): historial de intentos de un lead reciclado.
 *   - verReparto(id):       proyección del split setter/closer de un lead.
 * =========================================================================== */

function _rmpEsc(s) {
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  
  /* Se llama desde cambiarTab('rampa'). */
  async function cargarRampa() {
    if (typeof aliado === 'undefined' || !aliado) return;
    try {
      const res = await apiJSON(`${API}/aliados/${aliado.codigo}/rampa`);
      if (!res.ok) return;
      _renderRampa(await res.json());
    } catch (e) { /* 401 lo maneja apiJSON */ }
  
    // Vista de mentor (si corresponde).
    if (aliado.es_mentor) {
      try {
        const r = await apiJSON(`${API}/aliados/${aliado.codigo}/mentorias`);
        if (r.ok) _renderMentorias(await r.json());
      } catch (e) {}
    }
  }
  
  function _renderRampa(d) {
    const cont = document.getElementById('rampa-cuerpo');
    if (!cont) return;
  
    // Si ya debutó, mensaje de graduación en vez del checklist.
    if (d.graduado) {
      cont.innerHTML = `
        <div style="background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.3);
                    border-radius:var(--radius-sm);padding:22px;text-align:center;">
          <i class="fa-solid fa-trophy" style="font-size:1.8rem;color:#22c55e;"></i>
          <h3 style="margin:10px 0 4px;color:var(--text);font-weight:800;">¡Ya rompiste el hielo!</h3>
          <p style="color:var(--text-muted);font-size:.88rem;">
            Cerraste tu primer deal${d.primer_cierre_en ? ' el ' + _rmpEsc(d.primer_cierre_en) : ''}.
            El que sigue es más fácil — ya conocés el camino.</p>
        </div>`;
      return;
    }
  
    const pct = d.progreso_pct || 0;
    const checklist = (d.checklist || []).map(it => `
      <div style="display:flex;align-items:center;gap:11px;padding:11px 0;border-bottom:1px solid var(--border);">
        <i class="fa-solid ${it.hecho ? 'fa-circle-check' : 'fa-circle'}"
           style="font-size:1.1rem;color:${it.hecho ? '#22c55e' : 'var(--text-dim)'};"></i>
        <span style="font-size:.9rem;color:${it.hecho ? 'var(--text-muted)' : 'var(--text)'};
                     ${it.hecho ? 'text-decoration:line-through;' : 'font-weight:600;'}">
          ${_rmpEsc(it.texto)}</span>
      </div>`).join('');
  
    let mentorHtml = `
      <div style="background:rgba(255,255,255,.03);border:1px dashed var(--border);
                  border-radius:var(--radius-sm);padding:16px;color:var(--text-muted);font-size:.86rem;">
        Todavía no tenés un mentor asignado. Apenas haya uno disponible te avisamos.</div>`;
    if (d.mentor) {
      const wa = d.mentor.whatsapp
        ? `<a href="https://wa.me/${_rmpEsc(String(d.mentor.whatsapp).replace(/[^0-9]/g, ''))}"
              target="_blank" style="background:var(--green,#22c55e);color:#fff;border-radius:8px;
              padding:8px 14px;font-weight:700;font-size:.8rem;text-decoration:none;white-space:nowrap;">
              <i class="fa-brands fa-whatsapp"></i> Escribirle</a>` : '';
      mentorHtml = `
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;
                    background:rgba(124,58,237,.1);border:1px solid rgba(124,58,237,.3);
                    border-radius:var(--radius-sm);padding:14px 16px;flex-wrap:wrap;">
          <div>
            <div style="font-size:.74rem;color:#c084fc;font-weight:700;text-transform:uppercase;letter-spacing:.5px;">Tu mentor</div>
            <div style="font-weight:800;color:var(--text);">${_rmpEsc(d.mentor.nombre)}
              <span style="color:var(--text-dim);font-weight:500;">(${_rmpEsc(d.mentor.codigo)})</span></div>
          </div>
          <div style="display:flex;gap:8px;align-items:center;">
            ${wa}
            <button onclick="reasignarMentor()" title="Pedir otro mentor"
              style="background:transparent;color:var(--text-muted);border:1px solid var(--border);
                     border-radius:8px;padding:8px 12px;font-weight:600;font-size:.78rem;cursor:pointer;">Cambiar</button>
          </div>
        </div>`;
    }
  
    cont.innerHTML = `
      <div style="background:rgba(255,255,255,.03);border:1px solid var(--border);
                  border-radius:var(--radius-sm);padding:18px;margin-bottom:18px;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;">
          <span style="font-weight:800;color:var(--text);">${_rmpEsc(d.estado_label || 'Tu progreso')}</span>
          <span style="font-size:.8rem;color:var(--primary);font-weight:700;">${pct}%</span>
        </div>
        <div style="height:8px;background:rgba(0,0,0,.3);border-radius:99px;overflow:hidden;">
          <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,var(--primary),#22c55e);
                      border-radius:99px;transition:width .4s;"></div>
        </div>
      </div>
      <h3 style="font-size:1rem;font-weight:800;color:var(--text);margin-bottom:6px;">Tus primeros pasos</h3>
      <div style="margin-bottom:22px;">${checklist}</div>
      <h3 style="font-size:1rem;font-weight:800;color:var(--text);margin-bottom:12px;">Tu acompañamiento</h3>
      ${mentorHtml}`;
  }
  
  function _renderMentorias(d) {
    const wrap = document.getElementById('rampa-mentor-wrap');
    const cont = document.getElementById('rampa-mentees');
    if (!wrap || !cont) return;
    wrap.style.display = 'block';
    const activas = d.activas || [];
    if (!activas.length) {
      cont.innerHTML = `<div style="color:var(--text-muted);font-size:.85rem;padding:8px 0;">
        No tenés mentees activos ahora mismo.</div>`;
      return;
    }
    cont.innerHTML = activas.map(m => `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;
                  background:rgba(255,255,255,.03);border:1px solid var(--border);
                  border-radius:var(--radius-sm);padding:11px 14px;margin-bottom:8px;">
        <div>
          <div style="font-weight:700;color:var(--text);">${_rmpEsc(m.mentee ? m.mentee.nombre : '—')}
            <span style="color:var(--text-dim);font-weight:500;">(${_rmpEsc(m.mentee ? m.mentee.codigo : '')})</span></div>
          <div style="font-size:.76rem;color:var(--text-muted);">Estado: ${_rmpEsc(m.mentee_estado || 'nuevo')}</div>
        </div>
        <span style="color:#c084fc;font-size:.74rem;font-weight:700;">Acompañando</span>
      </div>`).join('');
  }
  
  async function reasignarMentor() {
    if (!confirm('¿Pedir otro mentor? Cerramos el acompañamiento actual y buscamos uno nuevo.')) return;
    try {
      const res = await apiJSON(`${API}/aliados/${aliado.codigo}/rampa/reasignar-mentor`, 'POST');
      const d = await res.json();
      mostrarToast(res.ok ? (d.mensaje || 'Listo.') : (d.detail || 'No se pudo.'), res.ok ? 'green' : 'red');
      cargarRampa();
    } catch (e) {}
  }
  
  
  /* ===========================================================================
   * EXTRAS DE BOLSA (Canal 1): historial de reciclado + proyección de reparto
   * =========================================================================== */
  
  function _rmpModal(html) {
    const ov = document.createElement('div');
    ov.id = '_rmp-overlay';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;';
    ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
    ov.innerHTML = `
      <div style="background:var(--bg2,#16181d);border:1px solid var(--border);border-radius:16px;
                  padding:22px;max-width:480px;width:100%;max-height:80vh;overflow:auto;
                  box-shadow:0 20px 60px rgba(0,0,0,.5);">${html}
        <button onclick="document.getElementById('_rmp-overlay').remove()"
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
          <div style="font-size:.82rem;color:var(--text);font-weight:600;">${_rmpEsc(h.resultado)}
            <span style="color:var(--text-dim);font-weight:400;">· ${_rmpEsc(h.fecha)}</span></div>
          ${h.aliado ? `<div style="font-size:.74rem;color:var(--text-muted);">por ${_rmpEsc(h.aliado)}</div>` : ''}
          ${h.nota ? `<div style="font-size:.78rem;color:var(--text-muted);margin-top:2px;">"${_rmpEsc(h.nota)}"</div>` : ''}
        </div>`).join('') || '<div style="color:var(--text-muted);font-size:.85rem;">Sin intentos previos.</div>';
      _rmpModal(`
        <div style="font-size:1.05rem;font-weight:800;color:var(--text);margin-bottom:4px;">Historial del lead</div>
        <div style="font-size:.82rem;color:var(--text-muted);margin-bottom:14px;">
          ${_rmpEsc(d.empresa || '')} · ${d.intentos || 0} intento(s), ${d.reciclados || 0} reciclado(s)</div>
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
          <td style="padding:7px 4px;color:var(--text);font-size:.82rem;">${_rmpEsc(f.plan)}</td>
          <td style="padding:7px 4px;text-align:right;color:#c084fc;font-size:.82rem;">$${f.setter_usd}</td>
          <td style="padding:7px 4px;text-align:right;color:var(--primary);font-size:.82rem;">$${f.closer_usd}</td>
        </tr>`).join('');
      _rmpModal(`
        <div style="font-size:1.05rem;font-weight:800;color:var(--text);margin-bottom:4px;">Reparto del deal</div>
        <div style="font-size:.82rem;color:var(--text-muted);margin-bottom:14px;">
          Setter ${d.split.setter}% / Closer ${d.split.closer}% · ${_rmpEsc(d.nota || '')}</div>
        <table style="width:100%;border-collapse:collapse;">
          <thead><tr style="border-bottom:1px solid var(--border);">
            <th style="text-align:left;padding:6px 4px;font-size:.72rem;color:var(--text-dim);">Plan</th>
            <th style="text-align:right;padding:6px 4px;font-size:.72rem;color:#c084fc;">Setter</th>
            <th style="text-align:right;padding:6px 4px;font-size:.72rem;color:var(--primary);">Closer</th>
          </tr></thead><tbody>${filas}</tbody></table>`);
    } catch (e) {}
  }