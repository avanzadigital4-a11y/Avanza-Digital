
// ═══════════════════════════════════════════════════════════════════════════
// MIS CAPTURAS — bandeja de leads de magnets (auditoría / calculadora / recursos)
// ═══════════════════════════════════════════════════════════════════════════
let _capturasData = null;

function _pintarBadgeCapturas(n) {
  const pill = document.getElementById('capturas-pill');
  if (!pill) return;
  if (n > 0) { pill.style.display = ''; pill.textContent = n > 99 ? '99+' : n; }
  else pill.style.display = 'none';
}

async function actualizarBadgeCapturas() {
  if (!aliado) return;
  try {
    const r = await apiFetch(`${API}/aliados/${aliado.codigo}/capturas`);
    if (!r.ok) return;
    _capturasData = await r.json();
    _pintarBadgeCapturas(_capturasData.no_vistas || 0);
  } catch (e) { console.warn('capturas badge', e); }
}

const _CAPTURA_ICONOS = { auditoria: 'fa-magnifying-glass', recursos: 'fa-download', guia: 'fa-book-open' };

function _capturaCard(c) {
  const icono = _CAPTURA_ICONOS[c.fuente] || 'fa-fire';
  const wa = (c.telefono && typeof _waNumeroBolsa === 'function') ? _waNumeroBolsa(c.telefono, (aliado && aliado.pais) || 'AR') : '';
  const quien = c.nombre || c.email || '(sin datos)';
  const filas = [];
  if (c.nombre) filas.push(`<div style="font-size:.82rem;color:var(--text-muted);"><i class="fa-solid fa-user" style="width:16px;color:var(--text-dim);"></i> ${c.nombre}</div>`);
  filas.push(`<div style="font-size:.82rem;color:var(--text-muted);"><i class="fa-solid fa-envelope" style="width:16px;color:var(--text-dim);"></i> ${c.email}</div>`);
  if (c.telefono) filas.push(`<div style="font-size:.82rem;color:var(--text-muted);"><i class="fa-solid fa-phone" style="width:16px;color:var(--text-dim);"></i> ${c.telefono}</div>`);
  if (c.dominio) filas.push(`<div style="font-size:.82rem;color:var(--text-muted);"><i class="fa-solid fa-globe" style="width:16px;color:var(--text-dim);"></i> ${c.dominio}${c.score ? ` · score <strong style="color:${c.score < 50 ? 'var(--red)' : 'var(--amber)'};">${c.score}/100</strong>` : ''}</div>`);

  const acciones = [];
  if (wa) acciones.push(`<a href="https://wa.me/${wa}?text=${encodeURIComponent('Hola' + (c.nombre ? ' ' + c.nombre.split(' ')[0] : '') + ', vi que usaste mi herramienta de diagnóstico de Avanza Digital. Te escribo para ayudarte a interpretar el resultado, ¿tenés unos minutos?')}" target="_blank" rel="noopener" style="flex:1;min-width:110px;text-align:center;padding:9px;font-size:.8rem;font-weight:700;text-decoration:none;background:var(--green);color:#000;border-radius:8px;"><i class="fa-brands fa-whatsapp"></i> WhatsApp</a>`);
  acciones.push(`<a href="mailto:${c.email}?subject=${encodeURIComponent('Sobre tu diagnóstico — Avanza Digital')}" style="flex:1;min-width:90px;text-align:center;padding:9px;font-size:.8rem;font-weight:700;text-decoration:none;background:rgba(255,255,255,0.06);color:var(--text-muted);border:1px solid var(--border);border-radius:8px;"><i class="fa-solid fa-envelope"></i> Email</a>`);
  if (c.prospecto_id) {
    acciones.push(`<button onclick="cambiarTab('pipeline', document.getElementById('btn-tab-pipeline'))" style="flex:1.2;min-width:130px;padding:9px;font-size:.8rem;font-weight:700;background:rgba(59,130,246,0.15);color:#60a5fa;border:1px solid rgba(59,130,246,0.35);border-radius:8px;cursor:pointer;font-family:'Inter',sans-serif;"><i class="fa-solid fa-check"></i> Ver en Mi CRM</button>`);
  } else {
    acciones.push(`<button id="btn-conv-cap-${c.id}" onclick="convertirCaptura(${c.id})" style="flex:1.2;min-width:130px;padding:9px;font-size:.8rem;font-weight:800;background:#f97316;color:#000;border:none;border-radius:8px;cursor:pointer;font-family:'Inter',sans-serif;"><i class="fa-solid fa-user-plus"></i> Agregar a Mi CRM</button>`);
  }

  return `<div class="bento-box" style="margin-bottom:12px;${!c.visto ? 'border-color:rgba(249,115,22,0.45);background:linear-gradient(135deg,rgba(249,115,22,0.06),transparent);' : ''}">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap;">
      <div style="display:flex;align-items:center;gap:10px;">
        <div style="width:38px;height:38px;border-radius:10px;background:rgba(249,115,22,0.12);border:1px solid rgba(249,115,22,0.3);display:flex;align-items:center;justify-content:center;color:#fb923c;"><i class="fa-solid ${icono}"></i></div>
        <div>
          <div style="font-weight:800;font-size:.92rem;">${quien} ${!c.visto ? '<span style="background:#f97316;color:#000;font-size:.62rem;font-weight:800;padding:2px 8px;border-radius:99px;margin-left:6px;vertical-align:middle;">NUEVA</span>' : ''}</div>
          <div style="font-size:.7rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:.05em;">${c.fuente_label} · ${c.fecha || ''}</div>
        </div>
      </div>
    </div>
    <div style="display:grid;gap:5px;margin:12px 0;">${filas.join('')}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">${acciones.join('')}</div>
  </div>`;
}

async function cargarCapturas(force = false) {
  if (!aliado) return;
  const lista = document.getElementById('capturas-lista');
  const vacio = document.getElementById('capturas-vacio');
  const resumen = document.getElementById('capturas-resumen');
  if (!lista) return;
  try {
    const r = await apiFetch(`${API}/aliados/${aliado.codigo}/capturas`);
    if (!r.ok) { lista.innerHTML = '<div class="bento-box" style="color:var(--text-muted);font-size:.85rem;">No se pudieron cargar las capturas. Probá de nuevo.</div>'; return; }
    const d = await r.json();
    _capturasData = d;

    if (resumen) resumen.textContent = d.total
      ? `${d.total} captura${d.total !== 1 ? 's' : ''} · ${d.no_vistas} nueva${d.no_vistas !== 1 ? 's' : ''}`
      : 'Sin capturas todavía';

    if (!d.capturas || !d.capturas.length) {
      lista.innerHTML = '';
      if (vacio) vacio.style.display = 'block';
    } else {
      if (vacio) vacio.style.display = 'none';
      lista.innerHTML = d.capturas.map(_capturaCard).join('');
    }

    // Apagar el badge: el aliado ya vio la bandeja
    if (d.no_vistas > 0) {
      apiFetch(`${API}/aliados/${aliado.codigo}/capturas/marcar-vistas`, { method: 'POST' })
        .then(() => _pintarBadgeCapturas(0))
        .catch(() => {});
    } else {
      _pintarBadgeCapturas(0);
    }
  } catch (e) { console.warn('cargarCapturas', e); }
}

async function convertirCaptura(id) {
  const btn = document.getElementById(`btn-conv-cap-${id}`);
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>'; }
  try {
    const r = await apiFetch(`${API}/capturas/${id}/convertir-prospecto`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'No se pudo convertir.');
    mostrarToast(d.mensaje || '¡Ya está en tu CRM!', 'green');
    cargarCapturas(true);
  } catch (e) {
    mostrarToast(e.message || 'Error al convertir la captura.', 'red');
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-user-plus"></i> Agregar a Mi CRM'; }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// NOVEDADES — campanita in-app (centro de notificaciones)
// ═══════════════════════════════════════════════════════════════════════════
let _novedadesTimer = null;
let _novedadesData = null;

function iniciarNovedades() {
  cargarNovedades();
  if (_novedadesTimer) clearInterval(_novedadesTimer);
  _novedadesTimer = setInterval(() => { cargarNovedades(); actualizarBadgeCapturas(); }, 90000);
  // Cerrar el panel al hacer click afuera
  document.addEventListener('click', (ev) => {
    const panel = document.getElementById('novedades-panel');
    const btn = document.getElementById('btn-novedades');
    if (!panel || panel.style.display === 'none') return;
    if (!panel.contains(ev.target) && !btn.contains(ev.target)) panel.style.display = 'none';
  });
}

const _NOVEDAD_ICONOS = { captura: '🔥', comision: '💰', tarea: '⏰', sistema: '🔔', alerta_sin_venta: '🎯' };

async function cargarNovedades() {
  if (!aliado) return;
  try {
    const r = await apiFetch(`${API}/aliados/${aliado.codigo}/novedades`);
    if (!r.ok) return;
    _novedadesData = await r.json();
    const badge = document.getElementById('novedades-badge');
    if (badge) {
      const n = _novedadesData.no_leidas || 0;
      badge.style.display = n > 0 ? 'block' : 'none';
      badge.textContent = n > 99 ? '99+' : n;
    }
    _pintarNovedades();
  } catch (e) { console.warn('novedades', e); }
}

function _pintarNovedades() {
  const cont = document.getElementById('novedades-lista');
  if (!cont || !_novedadesData) return;
  const items = _novedadesData.novedades || [];
  if (!items.length) {
    cont.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-dim);font-size:.8rem;">Sin novedades por ahora.<br><span style="font-size:.72rem;">Acá vas a ver tus leads capturados, comisiones y recordatorios.</span></div>';
    return;
  }
  cont.innerHTML = items.map(n => `
    <div onclick="_clickNovedad('${n.tab || ''}')" style="display:flex;gap:10px;padding:10px 12px;border-radius:10px;cursor:${n.tab ? 'pointer' : 'default'};${!n.leida ? 'background:rgba(249,115,22,0.06);' : ''}" onmouseover="this.style.background='rgba(255,255,255,0.04)'" onmouseout="this.style.background='${!n.leida ? 'rgba(249,115,22,0.06)' : 'transparent'}'">
      <div style="font-size:1.1rem;line-height:1.4;">${_NOVEDAD_ICONOS[n.tipo] || '🔔'}</div>
      <div style="flex:1;min-width:0;">
        <div style="font-size:.8rem;font-weight:${n.leida ? '600' : '800'};color:${n.leida ? 'var(--text-muted)' : 'var(--text)'};">${n.titulo}</div>
        ${n.cuerpo ? `<div style="font-size:.74rem;color:var(--text-dim);line-height:1.45;margin-top:2px;">${n.cuerpo}</div>` : ''}
        <div style="font-size:.66rem;color:var(--text-dim);margin-top:3px;">${n.fecha || ''}</div>
      </div>
      ${!n.leida ? '<div style="width:7px;height:7px;border-radius:99px;background:#f97316;margin-top:6px;flex-shrink:0;"></div>' : ''}
    </div>`).join('');
}

function toggleNovedades() {
  const panel = document.getElementById('novedades-panel');
  if (!panel) return;
  const abierto = panel.style.display !== 'none';
  panel.style.display = abierto ? 'none' : 'block';
  if (!abierto) cargarNovedades();
}

async function marcarNovedadesLeidas() {
  if (!aliado) return;
  try {
    await apiFetch(`${API}/aliados/${aliado.codigo}/novedades/marcar-leidas`, { method: 'POST' });
    if (_novedadesData) { _novedadesData.no_leidas = 0; (_novedadesData.novedades || []).forEach(n => n.leida = true); }
    const badge = document.getElementById('novedades-badge');
    if (badge) badge.style.display = 'none';
    _pintarNovedades();
  } catch (e) {}
}

function _clickNovedad(tab) {
  if (!tab) return;
  const panel = document.getElementById('novedades-panel');
  if (panel) panel.style.display = 'none';
  const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
  cambiarTab(tab, btn || null);
}

// ═══════════════════════════════════════════════════════════════════════════
// OUTREACH IA — primer mensaje de WhatsApp por rubro/observación
// ═══════════════════════════════════════════════════════════════════════════
let _outreachCtx = null;

function abrirOutreach(tipo, id, wa) {
  _outreachCtx = { tipo, id, wa };
  const modal = document.getElementById('outreach-modal');
  modal.style.display = 'flex';
  generarOutreach();
}

function cerrarOutreach() {
  document.getElementById('outreach-modal').style.display = 'none';
  _outreachCtx = null;
}

async function generarOutreach() {
  if (!_outreachCtx) return;
  const cargando = document.getElementById('outreach-cargando');
  const texto = document.getElementById('outreach-texto');
  const fuente = document.getElementById('outreach-fuente');
  cargando.style.display = 'block'; texto.style.display = 'none'; fuente.style.display = 'none';
  try {
    const ruta = _outreachCtx.tipo === 'bolsa' ? 'bolsa' : 'prospectos';
    const r = await apiFetch(`${API}/${ruta}/${_outreachCtx.id}/mensaje-outreach`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'No se pudo generar el mensaje.');
    texto.value = d.mensaje || '';
    fuente.innerHTML = d.fuente === 'ia'
      ? '<i class="fa-solid fa-wand-magic-sparkles" style="color:#7fd8ff;"></i> Generado con IA según el rubro y la observación del lead'
      : '<i class="fa-solid fa-file-lines"></i> Plantilla por rubro (la IA no estaba disponible)';
  } catch (e) {
    texto.value = '';
    fuente.innerHTML = '<span style="color:var(--red);">' + (e.message || 'Error al generar.') + '</span>';
  }
  cargando.style.display = 'none'; texto.style.display = 'block'; fuente.style.display = 'block';
}

function copiarOutreach() {
  const t = document.getElementById('outreach-texto').value;
  if (!t) return;
  navigator.clipboard.writeText(t).then(() => mostrarToast('Mensaje copiado ✓', 'green'));
}

function abrirWhatsAppOutreach() {
  if (!_outreachCtx) return;
  const t = document.getElementById('outreach-texto').value.trim();
  if (!t) { mostrarToast('Generá o escribí un mensaje primero.', 'red'); return; }
  window.open('https://wa.me/' + _outreachCtx.wa + '?text=' + encodeURIComponent(t), '_blank');
  if (_outreachCtx.tipo === 'prospecto' && typeof _logEnvioFicha === 'function') {
    try { _logEnvioFicha('whatsapp'); } catch (e) {}
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// SIMULADOR DE GANANCIAS — la tabla de comisiones hecha objetivo tangible
// ═══════════════════════════════════════════════════════════════════════════
let _simConfig = null;
let _simInicializado = false;

async function inicializarSimulador() {
  if (_simInicializado) { calcularSimulador(); return; }
  try {
    const r = await apiFetch(`${API}/simulador/config`);
    if (!r.ok) return;
    _simConfig = await r.json();
  } catch (e) { return; }

  const filaInput = (id, gid, label, precio, mensual) => `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px;">
      <div>
        <div style="font-size:.84rem;color:var(--text);">${label} <span style="color:var(--text-dim);font-size:.74rem;">USD ${precio.toLocaleString('es-AR')}${mensual ? '/mes' : ''}</span></div>
        <div id="${gid}" style="font-size:.7rem;color:${mensual ? 'var(--amber)' : 'var(--green)'};font-weight:700;"></div>
      </div>
      <input type="number" min="0" max="50" value="0" id="${id}" oninput="calcularSimulador()"
             style="width:70px;background:#111;border:1px solid var(--border);border-radius:8px;padding:8px 10px;color:var(--text);font-size:.9rem;font-family:'Inter',sans-serif;outline:none;text-align:center;">
    </div>`;

  const contP = document.getElementById('sim-planes');
  contP.innerHTML = Object.entries(_simConfig.planes).map(([nombre, precio], i) =>
    filaInput(`sim-q-p${i}`, `sim-g-p${i}`, nombre, precio, false)).join('');

  const contC = document.getElementById('sim-continuidad');
  contC.innerHTML = Object.entries(_simConfig.planes_continuidad).map(([nombre, precio], i) =>
    filaInput(`sim-q-c${i}`, `sim-g-c${i}`, nombre, precio, true)).join('');

  const sel = document.getElementById('sim-nivel');
  sel.innerHTML = Object.entries(_simConfig.niveles).map(([nivel, info]) =>
    `<option value="${nivel}">${nivel} — ${Math.round(info.comision * 100)}% por venta</option>`).join('');
  if (aliado && aliado.nivel && _simConfig.niveles[aliado.nivel]) sel.value = aliado.nivel;

  _simInicializado = true;
  calcularSimulador();
}

function calcularSimulador() {
  if (!_simConfig) return;
  const nivel = document.getElementById('sim-nivel').value;
  const pctNivel = (_simConfig.niveles[nivel] || { comision: 0.10 }).comision;
  const pctRec = _simConfig.comision_recurrente_pct || 0.10;
  const fmt = n => 'USD ' + Math.round(n).toLocaleString('es-AR');

  let baseOneTime = 0, cierres = 0;
  Object.values(_simConfig.planes).forEach((precio, i) => {
    const q = parseInt(document.getElementById(`sim-q-p${i}`)?.value || '0', 10) || 0;
    baseOneTime += q * precio; cierres += q;
    // Ganancia por unidad según el nivel elegido — hace tangible la tabla
    const g = document.getElementById(`sim-g-p${i}`);
    if (g) g.textContent = `Ganás ${fmt(precio * pctNivel)} por venta`;
  });
  let mrr = 0, clientesRec = 0;
  Object.values(_simConfig.planes_continuidad).forEach((precio, i) => {
    const q = parseInt(document.getElementById(`sim-q-c${i}`)?.value || '0', 10) || 0;
    mrr += q * precio * pctRec; clientesRec += q;
    const g = document.getElementById(`sim-g-c${i}`);
    if (g) g.textContent = `Ganás ${fmt(precio * pctRec)} todos los meses`;
  });

  const oneTime = baseOneTime * pctNivel;
  const totalMes = oneTime + mrr;

  document.getElementById('sim-total-mes').textContent = fmt(totalMes);
  document.getElementById('sim-total-anio').textContent = fmt(totalMes * 12);
  document.getElementById('sim-desglose').innerHTML = (cierres || clientesRec)
    ? `${cierres} plan${cierres !== 1 ? 'es' : ''} de sistema (${Math.round(pctNivel * 100)}%): <strong style="color:var(--green);">${fmt(oneTime)}</strong> &nbsp;·&nbsp; ${clientesRec} mantenimiento${clientesRec !== 1 ? 's' : ''} (${Math.round(pctRec * 100)}% mensual): <strong style="color:var(--amber);">${fmt(mrr)}</strong>`
    : 'Elegí al menos un plan para ver tu proyección.';

  // Zanahoria: cuánto más ganaría con el siguiente nivel
  const upgrade = document.getElementById('sim-upgrade');
  const niveles = Object.entries(_simConfig.niveles).sort((a, b) => a[1].comision - b[1].comision);
  const idx = niveles.findIndex(([k]) => k === nivel);
  if (baseOneTime > 0 && idx >= 0 && idx < niveles.length - 1) {
    const [sigNivel, sigInfo] = niveles[idx + 1];
    const delta = baseOneTime * (sigInfo.comision - pctNivel);
    upgrade.style.display = 'block';
    upgrade.innerHTML = `<i class="fa-solid fa-arrow-trend-up"></i> Con nivel <strong>${sigNivel}</strong> (${Math.round(sigInfo.comision * 100)}%) este mismo escenario te pagaría <strong>${fmt(delta)} más por mes</strong> — se alcanza con ${sigInfo.requisito} venta${sigInfo.requisito !== 1 ? 's' : ''} confirmada${sigInfo.requisito !== 1 ? 's' : ''} en 6 meses.`;
  } else {
    upgrade.style.display = 'none';
  }
}

// Ejemplo precargado: el pitch clásico — "si cerrás 2 Plan Pro por mes ganás $X"
async function cargarEjemploSimulador() {
  if (!_simInicializado) await inicializarSimulador();
  if (!_simConfig) return;
  // Resetear todo
  Object.keys(_simConfig.planes).forEach((_, i) => {
    const el = document.getElementById(`sim-q-p${i}`); if (el) el.value = 0;
  });
  Object.keys(_simConfig.planes_continuidad).forEach((_, i) => {
    const el = document.getElementById(`sim-q-c${i}`); if (el) el.value = 0;
  });
  // 2 Plan Pro (buscado por nombre, no por posición) + 2 Plan Crecimiento
  const iPro = Object.keys(_simConfig.planes).findIndex(n => n === 'Plan Pro');
  const iCre = Object.keys(_simConfig.planes_continuidad).findIndex(n => n === 'Plan Crecimiento');
  const elPro = document.getElementById(`sim-q-p${iPro >= 0 ? iPro : 0}`);
  const elCre = document.getElementById(`sim-q-c${iCre >= 0 ? iCre : 0}`);
  if (elPro) elPro.value = 2;
  if (elCre) elCre.value = 2;
  calcularSimulador();
}

// ═══════════════════════════════════════════════════════════════════════════
// PWA — instalable en el celular (manifest + service worker)
// ═══════════════════════════════════════════════════════════════════════════
let _pwaPrompt = null;

function _esIOS() { return /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream; }
function _esStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(e => console.warn('SW no registrado:', e));
  });
}

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  _pwaPrompt = e;
  if (!_esStandalone()) {
    const btn = document.getElementById('btn-instalar-pwa');
    if (btn) btn.style.display = '';
  }
});

// En iOS no existe beforeinstallprompt: mostramos el botón con instrucciones.
window.addEventListener('load', () => {
  if (_esIOS() && !_esStandalone()) {
    const btn = document.getElementById('btn-instalar-pwa');
    if (btn) btn.style.display = '';
  }
});

async function instalarPWA() {
  if (_pwaPrompt) {
    _pwaPrompt.prompt();
    const { outcome } = await _pwaPrompt.userChoice;
    if (outcome === 'accepted') {
      mostrarToast('¡App instalada! Buscá el ícono Avanza en tu pantalla de inicio.', 'green');
      const btn = document.getElementById('btn-instalar-pwa');
      if (btn) btn.style.display = 'none';
    }
    _pwaPrompt = null;
  } else if (_esIOS()) {
    mostrarToast('En iPhone: tocá Compartir (□↑) y elegí "Agregar a pantalla de inicio".', 'green');
  } else {
    mostrarToast('Abrí el menú del navegador y elegí "Instalar app" o "Agregar a pantalla de inicio".', 'green');
  }
}
