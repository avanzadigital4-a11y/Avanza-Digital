/* ===========================================================================
 * portal.equipos.js  Feature "Mi Equipo" (Bloque 1: formacion del vinculo)
 * ---------------------------------------------------------------------------
 * Pinta la pestana "Mi Equipo": armar equipo con otro aliado, ver/aceptar/
 * rechazar solicitudes, ajustar el split y disolver. NO toca comisiones (eso
 * es el Bloque 2). Usa los helpers globales del portal: `aliado`, `apiJSON`,
 * `mostrarToast` y la constante `API`.
 * =========================================================================== */

let _equipoBanda = { min: 0.25, max: 0.50, default: 0.40 };

function _eqEsc(s) {
  return (s == null ? '' : String(s)).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

/* Se llama desde cambiarTab('equipo'). */
async function cargarEquipo() {
  if (typeof aliado === 'undefined' || !aliado) return;
  try {
    const res = await apiJSON(`${API}/aliados/${aliado.codigo}/equipo`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.banda_split) _equipoBanda = data.banda_split;
    _renderEquipo(data);
  } catch (e) { /* 401 ya lo maneja apiJSON */ }
}

function _renderEquipo(data) {
  _renderRecibidas(data.solicitudes_recibidas || []);
  _renderActivos(data.activos || []);
  _renderEnviadas(data.solicitudes_enviadas || []);

  // Inicializa el slider del formulario de invitacion con la banda real.
  const sp = document.getElementById('equipo-split-input');
  if (sp) {
    sp.min = Math.round(_equipoBanda.min * 100);
    sp.max = Math.round(_equipoBanda.max * 100);
    if (!sp.value) sp.value = Math.round(_equipoBanda.default * 100);
    _eqActualizarLabelSplit();
  }
}

function _eqActualizarLabelSplit() {
  const sp = document.getElementById('equipo-split-input');
  const lbl = document.getElementById('equipo-split-label');
  if (!sp || !lbl) return;
  const setter = parseInt(sp.value, 10);
  lbl.textContent = `Setter ${setter}% / Closer ${100 - setter}%`;
}

/*  Render de las tres listas  */

function _renderRecibidas(lista) {
  const wrap = document.getElementById('equipo-recibidas-wrap');
  const cont = document.getElementById('equipo-recibidas');
  if (!cont) return;
  if (!lista.length) { if (wrap) wrap.style.display = 'none'; cont.innerHTML = ''; return; }
  if (wrap) wrap.style.display = 'block';
  cont.innerHTML = lista.map(eq => `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;
                background:rgba(34,197,94,.07);border:1px solid rgba(34,197,94,.25);
                border-radius:var(--radius-sm);padding:12px 14px;margin-bottom:8px;">
      <div>
        <div style="font-weight:700;color:var(--text);">${_eqEsc(eq.companero.nombre)}
          <span style="color:var(--text-dim);font-weight:500;">(${_eqEsc(eq.companero.codigo)})</span></div>
        <div style="font-size:.78rem;color:var(--text-muted);">Te invito a hacer equipo  Split: setter ${eq.split.setter}% / closer ${eq.split.closer}%</div>
      </div>
      <div style="display:flex;gap:8px;flex-shrink:0;">
        <button onclick="aceptarEquipo(${eq.equipo_id})"
          style="background:var(--green,#22c55e);color:#fff;border:none;border-radius:8px;
                 padding:8px 14px;font-weight:700;font-size:.8rem;cursor:pointer;">Aceptar</button>
        <button onclick="rechazarEquipo(${eq.equipo_id})"
          style="background:transparent;color:var(--text-muted);border:1px solid var(--border);
                 border-radius:8px;padding:8px 14px;font-weight:600;font-size:.8rem;cursor:pointer;">Rechazar</button>
      </div>
    </div>`).join('');
}

function _renderActivos(lista) {
  const cont = document.getElementById('equipo-activos');
  if (!cont) return;
  if (!lista.length) {
    cont.innerHTML = `<div class="empty-state" style="padding:20px;text-align:center;color:var(--text-muted);">
      <i class="fa-solid fa-people-arrows" style="font-size:1.4rem;opacity:.5;"></i>
      <p style="margin-top:8px;font-size:.85rem;">Todavia no tenes equipos.<br>Invita a un companero para empezar a repartir deals.</p>
    </div>`;
    return;
  }
  cont.innerHTML = lista.map(eq => `
    <div style="background:rgba(255,255,255,.03);border:1px solid var(--border);
                border-radius:var(--radius-sm);padding:14px 16px;margin-bottom:10px;">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
        <div>
          <div style="font-weight:700;color:var(--text);">${_eqEsc(eq.companero.nombre)}
            <span style="color:var(--text-dim);font-weight:500;">(${_eqEsc(eq.companero.codigo)})</span></div>
          <div style="font-size:.78rem;color:var(--text-muted);">Equipo activo${eq.desde ? ' desde ' + _eqEsc(eq.desde) : ''}</div>
        </div>
        <div style="display:flex;align-items:center;gap:10px;flex-shrink:0;">
          <span style="background:rgba(124,58,237,.14);color:#c084fc;border-radius:99px;
                       padding:4px 12px;font-size:.74rem;font-weight:700;">
            Setter ${eq.split.setter}% / Closer ${eq.split.closer}%</span>
          <button onclick="ajustarSplit(${eq.equipo_id}, ${eq.split.setter})"
            style="background:transparent;color:var(--primary);border:1px solid var(--border);
                   border-radius:8px;padding:7px 12px;font-weight:600;font-size:.78rem;cursor:pointer;">Ajustar split</button>
          <button onclick="disolverEquipo(${eq.equipo_id}, '${_eqEsc(eq.companero.nombre)}')"
            style="background:transparent;color:#ef4444;border:1px solid rgba(239,68,68,.3);
                   border-radius:8px;padding:7px 12px;font-weight:600;font-size:.78rem;cursor:pointer;">Disolver</button>
        </div>
      </div>
    </div>`).join('');
}

function _renderEnviadas(lista) {
  const wrap = document.getElementById('equipo-enviadas-wrap');
  const cont = document.getElementById('equipo-enviadas');
  if (!cont) return;
  if (!lista.length) { if (wrap) wrap.style.display = 'none'; cont.innerHTML = ''; return; }
  if (wrap) wrap.style.display = 'block';
  cont.innerHTML = lista.map(eq => `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;
                background:rgba(245,158,11,.07);border:1px solid rgba(245,158,11,.22);
                border-radius:var(--radius-sm);padding:11px 14px;margin-bottom:8px;">
      <div>
        <div style="font-weight:700;color:var(--text);">${_eqEsc(eq.companero.nombre)}
          <span style="color:var(--text-dim);font-weight:500;">(${_eqEsc(eq.companero.codigo)})</span></div>
        <div style="font-size:.78rem;color:var(--text-muted);">Esperando que acepte  Split: setter ${eq.split.setter}% / closer ${eq.split.closer}%</div>
      </div>
      <span style="color:var(--amber,#f59e0b);font-size:.78rem;font-weight:700;flex-shrink:0;">Pendiente</span>
    </div>`).join('');
}

/*  Acciones  */

async function solicitarEquipo() {
  const inp = document.getElementById('equipo-companero-input');
  const codigo = (inp && inp.value || '').trim();
  if (!codigo) { mostrarToast('Ingresa el codigo del companero.', 'red'); return; }

  const sp = document.getElementById('equipo-split-input');
  const setterPct = sp ? (parseInt(sp.value, 10) / 100) : _equipoBanda.default;

  try {
    const res = await apiJSON(`${API}/aliados/${aliado.codigo}/equipo/solicitar`, 'POST',
      { companero: codigo, setter_split_pct: setterPct });
    const data = await res.json();
    if (res.ok) {
      mostrarToast(data.mensaje || 'Solicitud enviada.', 'green');
      if (inp) inp.value = '';
      cargarEquipo();
    } else {
      mostrarToast(data.detail || 'No se pudo enviar la solicitud.', 'red');
    }
  } catch (e) { /* manejado */ }
}

async function aceptarEquipo(id) {
  try {
    const res = await apiJSON(`${API}/aliados/${aliado.codigo}/equipo/${id}/aceptar`, 'POST');
    const data = await res.json();
    mostrarToast(res.ok ? (data.mensaje || 'Equipo activado.') : (data.detail || 'No se pudo aceptar.'),
                 res.ok ? 'green' : 'red');
    cargarEquipo();
  } catch (e) {}
}

async function rechazarEquipo(id) {
  try {
    const res = await apiJSON(`${API}/aliados/${aliado.codigo}/equipo/${id}/rechazar`, 'POST');
    const data = await res.json();
    mostrarToast(res.ok ? 'Solicitud rechazada.' : (data.detail || 'No se pudo rechazar.'),
                 res.ok ? 'green' : 'red');
    cargarEquipo();
  } catch (e) {}
}

async function ajustarSplit(id, setterActual) {
  const min = Math.round(_equipoBanda.min * 100);
  const max = Math.round(_equipoBanda.max * 100);
  const entrada = prompt(
    `Que % de la comision se lleva el setter? (entre ${min} y ${max})`,
    String(setterActual));
  if (entrada === null) return;
  let n = parseInt(entrada, 10);
  if (isNaN(n)) { mostrarToast('Poné un numero valido.', 'red'); return; }
  // El backend igual lo acota a la banda; avisamos si se pasaron.
  if (n < min || n > max) mostrarToast(`Se ajusto a la banda permitida (${min}-${max}%).`, 'green');

  try {
    const res = await apiJSON(`${API}/aliados/${aliado.codigo}/equipo/${id}/split`, 'POST',
      { setter_split_pct: n / 100 });
    const data = await res.json();
    mostrarToast(res.ok ? (data.mensaje || 'Split actualizado.') : (data.detail || 'No se pudo ajustar.'),
                 res.ok ? 'green' : 'red');
    cargarEquipo();
  } catch (e) {}
}

async function disolverEquipo(id, nombre) {
  if (!confirm(`Seguro que queres disolver el equipo con ${nombre || 'tu companero'}?\nLas comisiones ya cobradas no se tocan.`)) return;
  try {
    const res = await apiJSON(`${API}/aliados/${aliado.codigo}/equipo/${id}/disolver`, 'POST');
    const data = await res.json();
    mostrarToast(res.ok ? 'Equipo disuelto.' : (data.detail || 'No se pudo disolver.'),
                 res.ok ? 'green' : 'red');
    cargarEquipo();
  } catch (e) {}
}


/* ===========================================================================
 * BLOQUE 2  Handoff de lead (setter -> closer) y cierre con continuidad
 * =========================================================================== */

/* Abre un modal para que el SETTER elija a que companero de equipo le pasa el lead. */
async function abrirHandoff(leadId) {
  let activos = [];
  try {
    const res = await apiJSON(`${API}/aliados/${aliado.codigo}/equipo`);
    if (res.ok) { const d = await res.json(); activos = d.activos || []; }
  } catch (e) { return; }

  if (!activos.length) {
    mostrarToast('No tenes companeros de equipo. Arma uno en "Mi Equipo".', 'red');
    return;
  }

  const filas = activos.map(eq => `
    <button onclick="_ejecutarHandoff(${leadId}, '${_eqEsc(eq.companero.codigo)}')"
      style="display:flex;justify-content:space-between;align-items:center;width:100%;gap:10px;
             background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:10px;
             padding:12px 14px;margin-bottom:8px;cursor:pointer;color:var(--text);text-align:left;">
      <span><strong>${_eqEsc(eq.companero.nombre)}</strong>
        <span style="color:var(--text-dim);font-weight:500;">(${_eqEsc(eq.companero.codigo)})</span></span>
      <span style="font-size:.72rem;color:#c084fc;font-weight:700;white-space:nowrap;">Setter ${eq.split.setter}% / Closer ${eq.split.closer}%</span>
    </button>`).join('');

  const ov = document.createElement('div');
  ov.id = '_handoff-overlay';
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;';
  ov.onclick = (e) => { if (e.target === ov) _cerrarModalHandoff(); };
  ov.innerHTML = `
    <div style="background:var(--bg2,#16181d);border:1px solid var(--border);border-radius:16px;
                padding:22px;max-width:460px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,.5);">
      <div style="font-size:1.05rem;font-weight:800;color:var(--text);margin-bottom:4px;">Pasar lead a un companero</div>
      <div style="font-size:.82rem;color:var(--text-muted);margin-bottom:16px;line-height:1.5;">
        El lead pasa a ser del closer para que lo cierre. Si cierra, la comision se reparte segun el split del equipo.</div>
      ${filas}
      <button onclick="_cerrarModalHandoff()"
        style="width:100%;background:transparent;color:var(--text-muted);border:1px solid var(--border);
               border-radius:10px;padding:10px;margin-top:6px;cursor:pointer;font-weight:600;">Cancelar</button>
    </div>`;
  document.body.appendChild(ov);
}

function _cerrarModalHandoff() {
  const ov = document.getElementById('_handoff-overlay');
  if (ov) ov.remove();
}

async function _ejecutarHandoff(leadId, companeroCodigo) {
  try {
    const res = await apiJSON(`${API}/aliados/${aliado.codigo}/equipo/handoff`, 'POST',
      { lead_id: leadId, companero: companeroCodigo });
    const data = await res.json();
    if (res.ok) {
      mostrarToast(data.mensaje || 'Lead pasado a tu companero.', 'green');
      _cerrarModalHandoff();
      if (typeof cargarBolsa === 'function') cargarBolsa();
    } else {
      mostrarToast(data.detail || 'No se pudo pasar el lead.', 'red');
    }
  } catch (e) { /* manejado */ }
}

/* El CLOSER cierra un lead con plan de continuidad: lleva el lead_id al alta para
 * que, si el lead vino de un handoff, la comision se reparta con el setter. */
function prepararContinuidadDesdeLead(leadId, empresaEnc) {
  window._continuidadLeadId = leadId;
  let nombre = '';
  try { nombre = decodeURIComponent(empresaEnc || ''); } catch (e) {}
  const nc = document.getElementById('nc-nombre');
  if (nc && nombre) nc.value = nombre;
  const btn = document.querySelector('.tab-btn[data-tab="comisiones"]');
  if (typeof cambiarTab === 'function') cambiarTab('comisiones', btn || null);
  mostrarToast('Completa el plan y activa la continuidad para registrar el cierre.', 'green');
}