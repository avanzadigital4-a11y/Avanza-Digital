/* ===========================================================================
 * portal.canales.js  ·  Pestaña "Canales" (puente entre Canal 1 y Canal 2)
 * ---------------------------------------------------------------------------
 * Una identidad, dos modos. El aliado ve qué canal tiene habilitado y puede
 * activar el otro: un closer de Canal 1 que armó cartera habilita Canal 2; un
 * referidor de Canal 2 que quiere cerrar activo habilita la bolsa (Canal 1).
 * Al activar, re-pinta la visibilidad de tabs para que aparezcan las del nuevo
 * canal sin recargar. Usa `aliado`, `apiJSON`, `mostrarToast`, `API`.
 * =========================================================================== */

function _canEsc(s) {
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  
  /* Se llama desde cambiarTab('canales'). */
  async function cargarCanales() {
    if (typeof aliado === 'undefined' || !aliado) return;
    try {
      const res = await apiJSON(`${API}/aliados/${aliado.codigo}/canales`);
      if (!res.ok) return;
      _renderCanales(await res.json());
    } catch (e) { /* 401 lo maneja apiJSON */ }
  }
  
  function _renderCanales(d) {
    const cont = document.getElementById('canales-lista');
    if (!cont) return;
    cont.innerHTML = (d.canales || []).map(c => {
      const on = !!c.habilitado;
      const activo = (d.canal_activo === c.clave);
      return `
        <div style="background:rgba(255,255,255,.03);border:1px solid ${on ? 'rgba(34,197,94,.3)' : 'var(--border)'};
                    border-radius:var(--radius-sm);padding:18px;margin-bottom:14px;">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;">
            <div style="flex:1;min-width:200px;">
              <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-weight:800;color:var(--text);">${_canEsc(c.nombre)}</span>
                ${on ? `<span style="background:rgba(34,197,94,.15);color:#22c55e;border-radius:99px;
                            padding:2px 10px;font-size:.7rem;font-weight:700;">Activo</span>` : ''}
                ${activo ? `<span style="background:rgba(59,130,246,.15);color:#3b82f6;border-radius:99px;
                            padding:2px 10px;font-size:.7rem;font-weight:700;">Modo actual</span>` : ''}
              </div>
              <div style="font-size:.84rem;color:var(--text-muted);margin-top:6px;line-height:1.5;">
                ${_canEsc(c.desbloquea)}</div>
            </div>
            <div style="flex-shrink:0;">
              ${on
                ? (activo ? '' : `<button onclick="cambiarModoCanal('${c.clave}')"
                     style="background:transparent;color:var(--primary);border:1px solid var(--border);
                            border-radius:8px;padding:9px 16px;font-weight:700;font-size:.82rem;cursor:pointer;">Usar este modo</button>`)
                : `<button onclick="activarCanal('${c.clave}')"
                     style="background:var(--primary);color:#fff;border:none;border-radius:8px;
                            padding:9px 18px;font-weight:700;font-size:.82rem;cursor:pointer;">Activar</button>`}
            </div>
          </div>
        </div>`;
    }).join('');
  }
  
  async function activarCanal(canal) {
    try {
      const res = await apiJSON(`${API}/aliados/${aliado.codigo}/canales/activar`, 'POST', { canal });
      const d = await res.json();
      if (!res.ok) { mostrarToast(d.detail || 'No se pudo activar.', 'red'); return; }
      mostrarToast('Canal activado. Ya tenés sus herramientas disponibles.', 'green');
      // Reflejar en el objeto local + re-pintar visibilidad de tabs.
      if (canal === 'canal1') aliado.canal1_habilitado = true;
      if (canal === 'canal2') aliado.canal2_habilitado = true;
      aliado.canal_activo = d.canal_activo || canal;
      if (typeof configurarCanal === 'function') configurarCanal();
      _renderCanales(d);
    } catch (e) {}
  }
  
  async function cambiarModoCanal(canal) {
    try {
      const res = await apiJSON(`${API}/aliados/${aliado.codigo}/canales/modo`, 'POST', { canal });
      const d = await res.json();
      if (!res.ok) { mostrarToast(d.detail || 'No se pudo cambiar el modo.', 'red'); return; }
      aliado.canal_activo = d.canal_activo || canal;
      mostrarToast('Modo actualizado.', 'green');
      _renderCanales(d);
    } catch (e) {}
  }