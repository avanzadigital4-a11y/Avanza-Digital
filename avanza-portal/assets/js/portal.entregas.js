/* ===========================================================================
 * portal.entregas.js  ·  Pestaña "Entregas" (Canal 2)
 * ---------------------------------------------------------------------------
 * El aliado de Canal 2 ve en qué estado va la implementación de SUS clientes
 * referidos: estado actual, ETA y timeline de cambios. Es lo que lo anima a
 * poner su nombre adelante con un cliente de su cartera.
 * Usa los helpers globales: `aliado`, `apiJSON`, `mostrarToast`, `API`.
 * =========================================================================== */

const _ENTREGA_COLOR = {
    sin_iniciar:   'var(--text-dim)',
    onboarding:    '#f59e0b',
    en_desarrollo: '#3b82f6',
    en_revision:   '#a855f7',
    entregado:     '#22c55e',
    pausado:       '#ef4444',
  };
  
  function _entEsc(s) {
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  
  /* Se llama desde cambiarTab('entregas'). */
  async function cargarEntregas() {
    if (typeof aliado === 'undefined' || !aliado) return;
    try {
      const res = await apiJSON(`${API}/aliados/${aliado.codigo}/entregas`);
      if (!res.ok) return;
      _renderEntregas(await res.json());
    } catch (e) { /* 401 lo maneja apiJSON */ }
  }
  
  function _renderEntregas(d) {
    const cont = document.getElementById('entregas-lista');
    if (!cont) return;
  
    // Resumen arriba.
    const resumen = document.getElementById('entregas-resumen');
    if (resumen) {
      resumen.innerHTML = `
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px;">
          ${_entChip('En curso', d.en_curso || 0, '#3b82f6')}
          ${_entChip('Entregados', d.entregados || 0, '#22c55e')}
          ${_entChip('Total', d.total || 0, 'var(--text-muted)')}
        </div>`;
    }
  
    const items = d.entregas || [];
    if (!items.length) {
      cont.innerHTML = `<div class="empty-state" style="padding:28px;text-align:center;color:var(--text-muted);">
        <i class="fa-solid fa-box-open" style="font-size:1.6rem;opacity:.5;"></i>
        <p style="margin-top:10px;font-size:.88rem;">Todavía no hay implementaciones en curso.<br>
          Cuando un cliente que referiste convierta, vas a poder seguir acá cómo avanza.</p>
      </div>`;
      return;
    }
  
    cont.innerHTML = items.map(it => {
      const color = _ENTREGA_COLOR[it.estado] || 'var(--text-muted)';
      const timeline = (it.timeline || []).slice().reverse().map(t => `
        <div style="display:flex;gap:8px;align-items:baseline;font-size:.76rem;color:var(--text-muted);padding:3px 0;">
          <span style="color:var(--text-dim);min-width:96px;">${_entEsc(t.fecha)}</span>
          <span style="color:var(--text);">${_entEsc(t.a)}</span>
          ${t.nota ? `<span>· ${_entEsc(t.nota)}</span>` : ''}
        </div>`).join('');
  
      return `
        <div style="background:rgba(255,255,255,.03);border:1px solid var(--border);
                    border-radius:var(--radius-sm);padding:16px;margin-bottom:12px;">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
            <div>
              <div style="font-weight:800;color:var(--text);">${_entEsc(it.cliente)}</div>
              <div style="font-size:.78rem;color:var(--text-muted);">${_entEsc(it.plan || '')}${it.eta ? ' · ETA: ' + _entEsc(it.eta) : ''}</div>
            </div>
            <span style="background:${color}22;color:${color};border:1px solid ${color}55;border-radius:99px;
                         padding:5px 13px;font-size:.76rem;font-weight:700;white-space:nowrap;">
              ${_entEsc(it.estado_label)}</span>
          </div>
          ${timeline ? `<details style="margin-top:12px;">
            <summary style="cursor:pointer;font-size:.78rem;color:var(--primary);font-weight:600;">Ver historial</summary>
            <div style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px;">${timeline}</div>
          </details>` : ''}
        </div>`;
    }).join('');
  }
  
  function _entChip(label, valor, color) {
    return `<div style="flex:1;min-width:90px;background:rgba(255,255,255,.03);border:1px solid var(--border);
                 border-radius:var(--radius-sm);padding:12px 14px;">
        <div style="font-size:1.5rem;font-weight:900;color:${color};">${valor}</div>
        <div style="font-size:.74rem;color:var(--text-muted);">${label}</div>
      </div>`;
  }