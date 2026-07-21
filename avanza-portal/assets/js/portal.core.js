const API = 'https://avanza-digital.onrender.com';

// ─── PAÍSES (multi-país) ─────────────────────────────────────────────────────
const PAISES_INFO = {
  AR: { bandera: "🇦🇷", nombre: "Argentina" },
  CR: { bandera: "🇨🇷", nombre: "Costa Rica" },
  MX: { bandera: "🇲🇽", nombre: "México" },
  CO: { bandera: "🇨🇴", nombre: "Colombia" },
  US: { bandera: "🇺🇸", nombre: "Estados Unidos" },
  CL: { bandera: "🇨🇱", nombre: "Chile" },
  UY: { bandera: "🇺🇾", nombre: "Uruguay" },
  PE: { bandera: "🇵🇪", nombre: "Perú" },
  EC: { bandera: "🇪🇨", nombre: "Ecuador" },
  BO: { bandera: "🇧🇴", nombre: "Bolivia" },
  PY: { bandera: "🇵🇾", nombre: "Paraguay" },
  VE: { bandera: "🇻🇪", nombre: "Venezuela" },
  BR: { bandera: "🇧🇷", nombre: "Brasil" },
  ES: { bandera: "🇪🇸", nombre: "España" },
  PA: { bandera: "🇵🇦", nombre: "Panamá" },
  GT: { bandera: "🇬🇹", nombre: "Guatemala" },
  HN: { bandera: "🇭🇳", nombre: "Honduras" },
  SV: { bandera: "🇸🇻", nombre: "El Salvador" },
  NI: { bandera: "🇳🇮", nombre: "Nicaragua" },
  DO: { bandera: "🇩🇴", nombre: "Rep. Dominicana" },
  CU: { bandera: "🇨🇺", nombre: "Cuba" },
  BZ: { bandera: "🇧🇿", nombre: "Belice" },
  GY: { bandera: "🇬🇾", nombre: "Guyana" },
  SR: { bandera: "🇸🇷", nombre: "Surinam" },
};
function paisInfo(codigo) {
  return PAISES_INFO[codigo] || { bandera: "🌎", nombre: codigo || "AR" };
}

// ─── AUTH HELPERS (JWT) ─────────────────────────────────────────────────────
// Token JWT firmado por el backend (HS256) — viaja en Authorization: Bearer.
// Antes el portal mandaba password y codigo por query string (quedaban en logs).
// Ahora todo va por body JSON + JWT en header.
function _getToken() {
  try {
    const raw = localStorage.getItem('avanza_session');
    if (!raw) return (window.aliado && window.aliado.token) || '';
    const { token, expiry } = JSON.parse(raw);
    if (Date.now() > expiry) { localStorage.removeItem('avanza_session'); return ''; }
    return token || '';
  } catch { return ''; }
}
function _setToken(t) {
  if (!t) return;
  const expiry = Date.now() + 30 * 24 * 60 * 60 * 1000;
  localStorage.setItem('avanza_session', JSON.stringify({ token: t, expiry }));
}
function _clearToken() {
  localStorage.removeItem('avanza_session');
}

async function apiFetch(url, opts = {}) {
  opts.headers = opts.headers || {};
  const tok = _getToken();
  if (tok && !opts.headers['Authorization']) {
    opts.headers['Authorization'] = 'Bearer ' + tok;
  }
  if (opts.body && typeof opts.body !== 'string' && !(opts.body instanceof FormData)) {
    opts.body = JSON.stringify(opts.body);
    opts.headers['Content-Type'] = 'application/json';
  }
  if (opts.method === 'POST' || opts.method === 'PATCH' || opts.method === 'PUT') {
    if (opts.body && typeof opts.body === 'string' && !opts.headers['Content-Type']) {
      opts.headers['Content-Type'] = 'application/json';
    }
  }
  return fetch(url, opts);
}

// ── TRACKING DE USO DEL PORTAL ───────────────────────────────────────────────
// Fire-and-forget: nunca debe frenar ni romper la UI. Alimenta el panel
// admin "Uso del Portal" (/admin/eventos-uso) para ver qué tabs/funciones
// se usan de verdad y cuáles no toca nadie.
function logEventoUso(evento, detalle) {
  try {
    apiFetch(`${API}/eventos/log`, {
      method: 'POST',
      body: { evento, detalle },
    }).catch(() => {});
  } catch (e) { /* nunca romper por esto */ }
}

// Identificador legible de un elemento clickeado, en orden de preferencia:
// id > data-tab > nombre de función del onclick > texto visible > tag.
function _identificarElementoUso(el) {
  if (el.id) return el.id;
  var dt = el.getAttribute('data-tab');
  if (dt) return 'tab:' + dt;
  var oc = el.getAttribute('onclick');
  if (oc) {
    var m = oc.match(/^\s*([a-zA-Z0-9_$.]+)\s*\(/);
    if (m) return m[1];
  }
  var txt = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40);
  return txt || el.tagName.toLowerCase();
}

// Tracking GENÉRICO de clicks: cualquier botón, link, elemento con onclick
// o role="button" en todo el portal queda registrado — no hace falta ir
// agregando ids a mano cada vez que se suma una función nueva.
// Los .tab-btn se excluyen acá para no duplicar: ya quedan como "tab_view"
// dentro de cambiarTab().
document.addEventListener('click', function (e) {
  var el = e.target && e.target.closest
    ? e.target.closest('button, a, input[type="submit"], input[type="button"], [onclick], [role="button"]')
    : null;
  if (!el || el.classList.contains('tab-btn')) return;
  logEventoUso('click', _identificarElementoUso(el));
}, true);

// Tracking de envío de formularios (cotizador, mi cuenta, comunidad, etc.)
document.addEventListener('submit', function (e) {
  var f = e.target;
  if (!f || f.tagName !== 'FORM') return;
  logEventoUso('form_submit', f.id || f.getAttribute('name') || 'form');
}, true);

async function apiJSON(url, method = 'GET', body = null) {
  const opts = { method };
  if (body) opts.body = body; // apiFetch lo serializa
  const r = await apiFetch(url, opts);
  if (r.status === 401) {
    _clearToken();
    if (typeof mostrarToast === 'function') mostrarToast('Sesión expirada, ingresá de nuevo.', 'red');
    location.reload();
    throw new Error('401');
  }
  return r;
}
let aliado = null;

const PLANES = { 'Plan Base':1050, 'Plan Pro':2900, 'Plan Industrial':4900, 'Estrategico 360':7500 };

// PLANES DE CONTINUIDAD (mensuales recurrentes) — comisión fija 10% para el aliado
// mientras el cliente mantenga el plan activo.
const PLANES_CONTINUIDAD = { 'Plan Cuidado':80, 'Plan Crecimiento':170, 'Plan Escala':280, 'Plan Liderazgo':450 };
const COMISION_RECURRENTE_PCT = 0.10;

const BENEFICIOS = {
  BASIC:   ['Kit de bienvenida incluido','Brochure comercial oficial','Herramienta de Auditoría B2B','Soporte por email'],
  SILVER:  ['Todo lo de BASIC','Bono USD 50 en tu primera venta','Acceso al grupo WhatsApp de aliados'],
  PREMIUM: ['Todo lo de SILVER','Badge Aliado Oficial','WhatsApp directo con asesor de Avanza'],
  ELITE:   ['Todo lo de PREMIUM','Directorio web como consultor','Sesión estratégica trimestral con Iván'],
};

// HELPER PORCENTAJE
function pct(nivel) {
  if (nivel === 'ELITE') return 0.20;
  if (nivel === 'PREMIUM') return 0.15;
  if (nivel === 'SILVER') return 0.12;
  return 0.10;
}

// Lógica del cotizador consolidada más abajo: actualizarCotizador() + copiarCotizacion()
// (versión única con checkout dual ARS/USDT). Las entradas por canal son
// irACotizadorConRubro() [Canal 2] e irACotizador() [Canal 1].

function mostrarRegistro() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('registro-screen').style.display = 'block';
  window.scrollTo(0,0);
}

function mostrarLogin() {
  document.getElementById('registro-screen').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';
}

// ── Canal por URL: aplica tema y precarga selección ──────────────────────────
function aplicarCanalDesdeURL() {
  const params = new URLSearchParams(window.location.search);
  const canal = params.get('canal');
  if (canal !== 'canal1' && canal !== 'canal2') return;

  // Definir colores y textos según canal
  const config = canal === 'canal1' ? {
    color: '#fb923c',
    rgba: 'rgba(251,146,60',
    nombre: 'Canal 1 · Bolsa de Leads',
    headlineReg: 'Empezás sin cartera.<br>El sistema te trae los leads.',
    sublineReg: 'Reclamás un lead, la IA te prepara el pitch, vos cerrás. Comisiones del 10% al 20% · Pago en 24hs.',
    promoTitle: 'Tu portal de aliado<br><span>Canal 1</span>',
    promoSub: 'Bolsa de leads + IA + Academia. Ingresá para ver los prospectos disponibles esta semana.'
  } : {
    color: '#4ade80',
    rgba: 'rgba(74,222,128',
    nombre: 'Canal 2 · Profesional con cartera',
    headlineReg: 'Convertí tu red en una<br>nueva línea de ingresos.',
    sublineReg: 'Vos cerrás con tu cliente, nosotros implementamos. Comisiones del 10% al 20% · Pago en 24hs.',
    promoTitle: 'Tu portal de aliado<br><span>Canal 2</span>',
    promoSub: 'Para profesionales con cartera propia. Ingresá para registrar tu próxima venta.'
  };

  // Aplicar tema al login (badge + botón register)
  const loginBadge = document.getElementById('login-badge');
  if (loginBadge) {
    loginBadge.style.background = config.rgba + ',0.12)';
    loginBadge.style.borderColor = config.rgba + ',0.3)';
    loginBadge.style.color = config.color;
    loginBadge.textContent = config.nombre;
  }
  const promoTitle = document.getElementById('login-promo-title');
  if (promoTitle) {
    promoTitle.innerHTML = config.promoTitle;
    const span = promoTitle.querySelector('span');
    if (span) span.style.color = config.color;
  }
  const promoSub = document.getElementById('login-promo-sub');
  if (promoSub) promoSub.textContent = config.promoSub;
  const btnRegister = document.getElementById('btn-register-link');
  if (btnRegister) {
    btnRegister.style.background = config.color;
    btnRegister.style.borderColor = config.color;
    btnRegister.style.color = '#000';
  }
  const promoBadge = document.getElementById('login-promo-badge');
  if (promoBadge) {
    promoBadge.style.background = config.rgba + ',0.12)';
    promoBadge.style.borderColor = config.rgba + ',0.3)';
    promoBadge.style.color = config.color;
  }

  // Aplicar tema al registro
  const regLogo = document.getElementById('reg-logo-icon');
  if (regLogo) regLogo.style.color = config.color;
  const regHeadline = document.getElementById('reg-headline');
  if (regHeadline) regHeadline.innerHTML = config.headlineReg;
  const regSubline = document.getElementById('reg-subline');
  if (regSubline) regSubline.textContent = config.sublineReg;

  // Mostrar chip de canal y ocultar badge default
  const chip = document.getElementById('reg-canal-chip');
  const chipText = document.getElementById('reg-canal-chip-text');
  const defaultBadge = document.getElementById('reg-default-badge');
  if (chip && chipText) {
    chipText.textContent = 'Te registrás como aliado ' + (canal === 'canal1' ? 'Canal 1' : 'Canal 2');
    chip.style.background = config.rgba + ',0.12)';
    chip.style.borderColor = config.rgba + ',0.3)';
    chip.style.color = config.color;
    chip.style.display = 'inline-flex';
  }
  if (defaultBadge) defaultBadge.style.display = 'none';

  // Precargar canal y ocultar el selector
  setTimeout(() => {
    if (typeof seleccionarCanal === 'function') seleccionarCanal(canal);
    const selectorBox = document.getElementById('canal-selector');
    if (selectorBox && selectorBox.parentElement) {
      selectorBox.parentElement.style.display = 'none';
    }
    const tipoInput = document.getElementById('reg-tipo-aliado');
    if (tipoInput) tipoInput.value = canal;
  }, 50);

  // Abrir directamente el registro
  mostrarRegistro();
}

// Permite cambiar de canal desde el chip del registro
function cambiarCanalRegistro() {
  // Remover ?canal= de la URL y volver al selector de alianzas
  window.location.href = '../alianzas.html';
}


// ─── USERNAME / SLUG ─────────────────────────────────────────────────────────
// Live-preview + chequeo de disponibilidad mientras el usuario tipea.
// Debounce de 400ms para no martillar el endpoint.
let _usernameCheckTimer = null;
let _usernameUltimo = '';

function _normalizarUsername(u) {
  if (!u) return '';
  return u.toLowerCase().trim()
    .replace(/[áä]/g, 'a').replace(/[éë]/g, 'e').replace(/[íï]/g, 'i')
    .replace(/[óö]/g, 'o').replace(/[úü]/g, 'u').replace(/ñ/g, 'n')
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
    .replace(/-+/g, '-')
    .slice(0, 30);
}

function _setUsernameFeedback(estado, mensaje) {
  // estado: 'idle' | 'loading' | 'ok' | 'warn' | 'error'
  const fb = document.getElementById('reg-username-feedback');
  if (!fb) return;
  const colores = {
    idle:    { c: 'var(--text-muted)', i: '' },
    loading: { c: 'var(--text-muted)', i: '<i class="fa-solid fa-spinner fa-spin"></i>' },
    ok:      { c: '#4ade80',           i: '<i class="fa-solid fa-circle-check"></i>' },
    warn:    { c: '#fbbf24',           i: '<i class="fa-solid fa-triangle-exclamation"></i>' },
    error:   { c: '#ef4444',           i: '<i class="fa-solid fa-circle-xmark"></i>' },
  };
  const cfg = colores[estado] || colores.idle;
  fb.style.color = cfg.c;
  fb.innerHTML = `${cfg.i} <span>${mensaje}</span>`;
}

function onUsernameInput() {
  const input   = document.getElementById('reg-username');
  const preview = document.getElementById('reg-username-preview');
  if (!input || !preview) return;

  // Auto-normalizar lo que tipea el usuario (sin mover el caret bruscamente)
  const original = input.value;
  const limpio = _normalizarUsername(original);
  if (limpio !== original) {
    const caret = input.selectionStart;
    input.value = limpio;
    // Restaurar caret aproximadamente
    try { input.setSelectionRange(caret, caret); } catch(e) {}
  }

  // Update preview
  preview.textContent = limpio || 'tu-username';
  preview.style.color = limpio ? '#4ade80' : '#71717a';

  // Reset feedback si está vacío
  if (!limpio) {
    _setUsernameFeedback('idle', 'Dejalo vacío y se autogenera. Pero un username elegido por vos rinde más.');
    if (_usernameCheckTimer) clearTimeout(_usernameCheckTimer);
    return;
  }

  // Validación local rápida antes de pegarle al servidor
  if (limpio.length < 3) {
    _setUsernameFeedback('warn', `Mínimo 3 caracteres (llevás ${limpio.length}).`);
    return;
  }
  if (/^-|-$/.test(limpio)) {
    _setUsernameFeedback('error', 'No puede empezar ni terminar con guion.');
    return;
  }

  // Debounce + check al servidor
  _setUsernameFeedback('loading', 'Chequeando disponibilidad...');
  if (_usernameCheckTimer) clearTimeout(_usernameCheckTimer);
  _usernameCheckTimer = setTimeout(() => _checkUsernameDisponible(limpio), 400);
}

async function _checkUsernameDisponible(username) {
  if (username === _usernameUltimo) return;
  _usernameUltimo = username;
  try {
    const res = await fetch(`${API}/aliados/check-username/${encodeURIComponent(username)}`);
    const data = await res.json();
    // Si el usuario ya tipeó algo distinto mientras esperábamos, abortamos
    if (document.getElementById('reg-username').value !== username) return;
    if (!data.valid) {
      _setUsernameFeedback('error', data.razon || 'Username inválido.');
    } else if (!data.disponible) {
      _setUsernameFeedback('warn', 'Ese username ya está en uso. Probá con otro.');
    } else {
      _setUsernameFeedback('ok', '¡Disponible! Va a ser tuyo.');
    }
  } catch(e) {
    _setUsernameFeedback('idle', 'No pude verificar. Lo chequeamos al registrarte.');
  }
}


// ─── PERSONALIZAR USERNAME DESDE EL PANEL ────────────────────────────────────
// Usado por aliados que ya tienen cuenta y quieren reclamar un slug bonito
// (ref_code autogenerado → username elegido). Una sola vez por aliado.
let _persoCheckTimer = null;
let _persoUltimo = '';
let _persoEstaDisponible = false;

function abrirPersonalizarUsername() {
  // Reset state
  const input = document.getElementById('perso-username');
  const preview = document.getElementById('perso-username-preview');
  const fb = document.getElementById('perso-username-feedback');
  if (input) input.value = '';
  if (preview) { preview.textContent = 'tu-username'; preview.style.color = '#71717a'; }
  if (fb) {
    fb.style.color = 'var(--text-muted)';
    fb.innerHTML = '<span>3-30 caracteres. Letras, números y guiones.</span>';
  }
  _persoUltimo = '';
  _persoEstaDisponible = false;
  document.getElementById('modal-personalizar-username').classList.add('open');
  setTimeout(() => { if (input) input.focus(); }, 150);
}

function _setPersoFeedback(estado, mensaje) {
  const fb = document.getElementById('perso-username-feedback');
  if (!fb) return;
  const colores = {
    idle:    { c: 'var(--text-muted)', i: '' },
    loading: { c: 'var(--text-muted)', i: '<i class="fa-solid fa-spinner fa-spin"></i>' },
    ok:      { c: '#4ade80',           i: '<i class="fa-solid fa-circle-check"></i>' },
    warn:    { c: '#fbbf24',           i: '<i class="fa-solid fa-triangle-exclamation"></i>' },
    error:   { c: '#ef4444',           i: '<i class="fa-solid fa-circle-xmark"></i>' },
  };
  const cfg = colores[estado] || colores.idle;
  fb.style.color = cfg.c;
  fb.innerHTML = `${cfg.i} <span>${mensaje}</span>`;
}

function onPersoUsernameInput() {
  const input   = document.getElementById('perso-username');
  const preview = document.getElementById('perso-username-preview');
  if (!input || !preview) return;

  const original = input.value;
  const limpio = _normalizarUsername(original);
  if (limpio !== original) {
    const caret = input.selectionStart;
    input.value = limpio;
    try { input.setSelectionRange(caret, caret); } catch(e) {}
  }

  preview.textContent = limpio || 'tu-username';
  preview.style.color = limpio ? '#4ade80' : '#71717a';

  _persoEstaDisponible = false;

  if (!limpio) {
    _setPersoFeedback('idle', '3-30 caracteres. Letras, números y guiones.');
    if (_persoCheckTimer) clearTimeout(_persoCheckTimer);
    return;
  }
  if (limpio.length < 3) {
    _setPersoFeedback('warn', `Mínimo 3 caracteres (llevás ${limpio.length}).`);
    return;
  }
  if (/^-|-$/.test(limpio)) {
    _setPersoFeedback('error', 'No puede empezar ni terminar con guion.');
    return;
  }
  // Si es el mismo que el actual del aliado, no chequeamos
  if (aliado && aliado.ref_code === limpio) {
    _setPersoFeedback('warn', 'Ese ya es tu username actual.');
    return;
  }

  _setPersoFeedback('loading', 'Chequeando disponibilidad...');
  if (_persoCheckTimer) clearTimeout(_persoCheckTimer);
  _persoCheckTimer = setTimeout(async () => {
    if (limpio === _persoUltimo) return;
    _persoUltimo = limpio;
    try {
      const res = await fetch(`${API}/aliados/check-username/${encodeURIComponent(limpio)}`);
      const data = await res.json();
      if (document.getElementById('perso-username').value !== limpio) return;
      if (!data.valid) {
        _setPersoFeedback('error', data.razon || 'Username inválido.');
      } else if (!data.disponible) {
        _setPersoFeedback('warn', 'Ese username ya está en uso. Probá con otro.');
      } else {
        _setPersoFeedback('ok', '¡Disponible! Listo para confirmar.');
        _persoEstaDisponible = true;
      }
    } catch(e) {
      _setPersoFeedback('idle', 'No pude verificar. Probá de nuevo.');
    }
  }, 400);
}

async function confirmarPersonalizarUsername() {
  const input = document.getElementById('perso-username');
  const btn = document.getElementById('btn-perso-confirmar');
  const username = _normalizarUsername(input ? input.value : '');

  if (!username) {
    _setPersoFeedback('error', 'Ingresá un username primero.');
    return;
  }
  if (!_persoEstaDisponible) {
    _setPersoFeedback('warn', 'Esperá a que confirmemos disponibilidad.');
    return;
  }

  // Confirmación final del usuario — dado que es one-shot, le damos una
  // segunda chance de retractarse.
  if (!confirm(`Vas a personalizar tu link a:\n\navanzadigital.digital/p/${username}\n\nTu link viejo va a dejar de funcionar. Esta acción se puede hacer una sola vez.\n\n¿Confirmás?`)) {
    return;
  }

  if (btn) {
    btn.innerHTML = '<span class="spinner"></span> Aplicando...';
    btn.disabled = true;
  }

  try {
    const res = await apiFetch(`${API}/aliados/me/cambiar-username`, {
      method: 'POST',
      body: { username }
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'No se pudo cambiar el username.');

    // Actualizar el objeto aliado en memoria
    if (aliado) {
      aliado.ref_code = data.ref_code_nuevo;
      aliado.link_ref = data.link_ref;
      aliado.link_perfil = data.link_perfil;
      aliado.username_personalizado = true;
    }

    // Cerrar modal
    document.getElementById('modal-personalizar-username').classList.remove('open');

    // Toast de éxito
    if (typeof mostrarToast === 'function') {
      mostrarToast(`¡Listo! Tu link ahora es /p/${data.ref_code_nuevo}`, 'green');
    }

    // Recargar todo el dashboard para que se actualicen los links visibles
    if (typeof cargarTodo === 'function') {
      cargarTodo();
    }
  } catch(e) {
    _setPersoFeedback('error', e.message || 'Error al guardar.');
    if (btn) {
      btn.innerHTML = '<i class="fa-solid fa-check"></i> Confirmar y personalizar';
      btn.disabled = false;
    }
  }
}


async function registrarse() {
  const nombre   = document.getElementById('reg-nombre').value.trim();
  const email    = document.getElementById('reg-email').value.trim();
  const whatsapp = document.getElementById('reg-whatsapp').value.trim();
  const username = _normalizarUsername(document.getElementById('reg-username').value || '');
  const ciudad   = document.getElementById('reg-ciudad').value.trim();
  const pais     = document.getElementById('reg-pais').value;
  const dni      = document.getElementById('reg-dni').value.trim();
  const perfil   = document.getElementById('reg-perfil').value;
  const pass     = document.getElementById('reg-pass').value;
  const pass2    = document.getElementById('reg-pass2').value;
  const tyc      = document.getElementById('reg-tyc').checked;

  const errDiv = document.getElementById('reg-error');
  const errMsg = document.getElementById('reg-error-msg');
  errDiv.style.display = 'none';

  const mostrarError = (msg) => {
    errMsg.textContent = msg;
    errDiv.style.display = 'flex';
    errDiv.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  if (!nombre || !email || !whatsapp || !perfil) return mostrarError('Completá todos los campos obligatorios.');
  if (!pais) return mostrarError('Elegí tu país.');
  if (pass.length < 6) return mostrarError('La contraseña debe tener al menos 6 caracteres.');
  if (pass !== pass2) return mostrarError('Las contraseñas no coinciden.');
  if (!tyc) return mostrarError('Tenés que aceptar los Términos del Programa para continuar.');

  const tipoAliado = document.getElementById('reg-tipo-aliado').value;
  if (!tipoAliado) return mostrarError('Elegí cómo vas a operar: "Tengo mis clientes" o "Busco clientes".');

  const btn = document.getElementById('btn-registrar');
  btn.innerHTML = '<span class="spinner"></span> Creando tu cuenta...';
  btn.disabled = true;

  try {
    const urlParams = new URLSearchParams(window.location.search);
    const refSponsor = urlParams.get('ref') || localStorage.getItem('avanza_ref') || '';
    // Timeout de 15 segundos para evitar cuelgues infinitos
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000);

    const res = await apiFetch(`${API}/registrarse`, {
      method: 'POST',
      signal: controller.signal,
      body: { nombre, email, whatsapp, ciudad, pais, dni, perfil, password: pass,
              ref_sponsor: refSponsor, tipo_aliado: tipoAliado, acepto_terminos: true,
              username: username || null }
    });
    clearTimeout(timeoutId);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Error al registrarse.');

    aliado = data;
    if (aliado.token) _setToken(aliado.token);
    // Ocultar pantalla de registro, mostrar portal
    document.getElementById('registro-screen').style.display = 'none';
    document.getElementById('portal-screen').style.display = 'block';
    // Forzar onboarding siempre para nuevos registros
    localStorage.removeItem(`portal_onboarded_${aliado.codigo}`);
    cargarTodo();
    mostrarToast(`¡Bienvenido, ${aliado.nombre.split(' ')[0]}! Tu código es ${aliado.codigo}`, 'green');
  } catch(e) {
    if (e.name === 'AbortError') {
      mostrarError('El registro tardó demasiado. Verificá tu conexión y probá de nuevo. Si ya creaste la cuenta, intentá iniciar sesión.');
    } else {
      mostrarError(e.message || 'Error de conexión. Intentá de nuevo.');
    }
  }

  btn.innerHTML = '<i class="fa-solid fa-rocket"></i> Crear mi cuenta de aliado gratis';
  btn.disabled = false;
}

function copiarRefOnboarding() {
  if (!aliado) return;
  const link = `https://avanzadigital.digital/p/${aliado.ref_code}`;
  navigator.clipboard.writeText(link).then(() => mostrarToast('Página de ventas copiada al portapapeles ✓', 'green'));
}



function mostrarToast(msg, tipo='green') {
  const t = document.createElement('div');
  t.className = `toast ${tipo}`;
  t.innerHTML = `<i class="fa-solid ${tipo==='green'?'fa-circle-check':'fa-circle-info'}"></i> ${msg}`;
  document.body.appendChild(t);
  setTimeout(()=>t.remove(), 3500);
}

async function iniciarSesion() {
  const codigo = document.getElementById('login-codigo').value.trim().toUpperCase();
  const pass   = document.getElementById('login-pass').value;
  const err = document.getElementById('login-error');
  err.style.display = 'none';
  if (!codigo || !pass) { err.style.display = 'block'; return; }
  const btn = document.getElementById('btn-login');
  btn.innerHTML = '<span class="spinner"></span> Verificando...';
  btn.disabled = true;
  try {
    const res = await apiFetch(`${API}/aliados/login`, {
      method: 'POST',
      body: { codigo, password: pass }
    });
    if (!res.ok) throw new Error();
    aliado = await res.json();
    if (aliado.token) _setToken(aliado.token);
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('portal-screen').style.display = 'block';
    cargarTodo();
  } catch {
    err.style.display = 'block';
  }
  btn.innerHTML = '<i class="fa-solid fa-arrow-right-to-bracket"></i> Ingresar al portal';
  btn.disabled = false;
}

function cambiarTab(tab, btn) {
  // Cualquier cambio de solapa frena el auto-refresh de Mi Red (si estaba
  // activo). Se vuelve a encender más abajo solo si la nueva solapa es 'red'.
  detenerAutoRefreshRed();
  if (typeof chatDetenerPolling === 'function') chatDetenerPolling();
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  const panel = document.getElementById(`tab-${tab}`);
  if (panel) panel.classList.add('active');
  if (btn) btn.classList.add('active');

  logEventoUso('tab_view', tab);

  // Gestionar estado del botón "Herramientas" y visibilidad de fila secundaria
  // Nota: 'academia' se quitó de TABS_SEC — es tab primario para Canal 1
  const TABS_SEC = ['cotizador','herramientas','prospectos','ventas','red','comunidad','jarvis'];
  const extraRow = document.getElementById('tabs-extra-row');
  const btnMas   = document.getElementById('btn-mas-tabs');
  if (TABS_SEC.includes(tab)) {
    if (extraRow) extraRow.classList.add('visible');
    if (btnMas)   btnMas.classList.add('active');
  } else {
    if (btnMas)   btnMas.classList.remove('active');
    // No ocultamos la fila si el usuario la dejó abierta — se cierra solo con el toggle
  }

  if(tab==='prospectos') cargarProspectos();
  if(tab==='pipeline') { cargarProspectosPipeline(); plRenderReferidos(); plRenderVentas(); }
  if(tab==='dashboard') { renderTareasHoy(); }
  if(tab==='mi-cuenta') { try { _renderEstadoPush(); } catch (e) {} }
  if(tab==='bolsa') {
    // Soft-gate: verificar si el aliado completó al menos los primeros 3 módulos de la Academia
    const progresoBolsa = getAcademiaProgreso ? getAcademiaProgreso() : [];
    const mod1 = progresoBolsa.includes(1);
    const mod2 = progresoBolsa.includes(2);
    const mod3 = progresoBolsa.includes(3);
    const completadosReq = [mod1, mod2, mod3].filter(Boolean).length;
    const yaVioAviso = aliado && localStorage.getItem(`bolsa_aviso_visto_${aliado.codigo}`);
    if (completadosReq < 3 && !yaVioAviso) {
      // Mostrar modal de gate suave antes de entrar
      abrirModalGateBolsa(completadosReq);
      return; // No cambiar al tab todavía
    }
    cargarBolsa(); cargarHistorialBolsa(); cargarMarketplace();
  }
  if(tab==='red') { cargarRed(); iniciarAutoRefreshRed(); }
  if(tab==='equipo') cargarEquipo();
  if(tab==='entregas') cargarEntregas();
  if(tab==='comunidad') _comunidadCargarSubtabActivo();
  if(tab==='academia') inicializarAcademia();
  // v1.4: refrescar TC al entrar al cotizador y comisiones al entrar a su tab
  if(tab==='cotizador') { cargarTipoDeCambio(); recuperarUltimoLinkActivo(); }
  if(tab==='comisiones') { renderComisiones(); inicializarSimulador(); }
  if(tab==='capturas') cargarCapturas();
  if(tab==='mi-cuenta') poblarMiCuenta();
  if(tab==='jarvis') {
    // Carga perezosa: baja portal.jarvis.*.js la 1ra vez y luego inicializa.
    cargarModulo('jarvis', window.__MODS.jarvis.file, window.__MODS.jarvis.centinela)
      .then(() => inicializarJarvisV2())
      .catch((e) => console.error('[Jarvis] carga diferida falló:', e));
  }
}

// ── Comunidad: sub-vistas Foro / Chat dentro de la misma pestaña ────────────
// El Chat (sala general + directos) vive ADENTRO de "Comunidad" para no sumar
// otro botón más al nav lateral, que ya viene largo. Al tocar "Chat" ocupa
// todo el espacio del panel, igual que el Foro cuando está activo.
let _comunidadSubtabActual = 'foro';

function comunidadCambiarSubtab(sub, btn) {
  _comunidadSubtabActual = sub;
  document.querySelectorAll('#comunidad-subtabs .chat-subtab-btn').forEach(b => b.classList.remove('activo'));
  if (btn) btn.classList.add('activo');
  const panelForo = document.getElementById('comunidad-panel-foro');
  const panelChat = document.getElementById('comunidad-panel-chat');
  if (panelForo) panelForo.style.display = sub === 'foro' ? '' : 'none';
  if (panelChat) panelChat.style.display = sub === 'chat' ? '' : 'none';
  _comunidadCargarSubtabActivo();
}

function _comunidadCargarSubtabActivo() {
  if (_comunidadSubtabActual === 'chat') {
    // Carga perezosa: baja portal.chat.js la 1ra vez y luego inicializa.
    cargarModulo('chat', window.__MODS.chat.file, window.__MODS.chat.centinela)
      .then(() => inicializarChat())
      .catch((e) => console.error('[Chat] carga diferida falló:', e));
  } else {
    if (typeof chatDetenerPolling === 'function') chatDetenerPolling();
    cargarComunidad();
  }
}

// ── Toggle fila de herramientas secundarias ──────────────────────────────────
function toggleMasTabsRow() {
  const row = document.getElementById('tabs-extra-row');
  const btn = document.getElementById('btn-mas-tabs');
  const visible = row.classList.toggle('visible');
  // Solo marca activo el botón "Herramientas" si la fila está visible
  // Y si hay un tab secundario activo (lo hace cambiarTab), o si el usuario
  // lo abrió manualmente sin estar en un tab secundario.
  if (!visible) btn.classList.remove('active');
  else {
    // Verificar si el tab activo actual es secundario
    const TABS_SEC = ['cotizador','herramientas','prospectos','ventas','red','comunidad','jarvis'];
    const panelActivo = document.querySelector('.tab-panel.active');
    const tabId = panelActivo ? panelActivo.id.replace('tab-','') : '';
    if (TABS_SEC.includes(tabId)) btn.classList.add('active');
  }
}

function copiarTexto(id) {
  const el = document.getElementById(id);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent.trim()).then(() => {
    mostrarToast('¡Mensaje copiado! Pegalo directo en WhatsApp.', 'green');
  });
}

function irARegistrarProspecto(planPreseleccionado) {
  cambiarTab('pipeline', document.getElementById('btn-tab-pipeline'));
  setTimeout(() => {
    const sel = document.getElementById('pl-np-plan');
    if (sel) {
      sel.value = planPreseleccionado;
      if (!sel.value) {
        const opt = Array.from(sel.options).find(o => /360/.test(o.value) && /360/.test(planPreseleccionado));
        if (opt) sel.value = opt.value;
      }
    }
    const campo = document.getElementById('pl-np-nombre');
    if (campo) campo.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, 250);
}

function irACotizadorConRubro(rubro, plan) {
  cambiarTab('cotizador', document.querySelector('.tab-btn[onclick*="cotizador"]'));
  setTimeout(() => {
    const selRubro = document.getElementById('cot-rubro');
    const selPlan = document.getElementById('cot-plan');
    if (selRubro) { selRubro.value = rubro; }
    if (selPlan) { selPlan.value = plan; }
    if (typeof actualizarCotizador === 'function') actualizarCotizador();
  }, 200);
}

// ── CANAL: configurar visibilidad de tabs y bloques ─────────────────────────
// ── CANAL: configurar visibilidad de tabs y bloques ─────────────────────────
// El canal queda definido al registrarse (tipo_aliado) y no se cambia desde el
// portal: Canal 1 = sin cartera (bolsa), Canal 2 = con cartera (referir).
function configurarCanal() {
  if (!aliado) return;
  const canal = aliado.tipo_aliado || 'canal1';
  const esCanal2 = canal === 'canal2';

  // Tabs
  document.querySelectorAll('.tab-canal1').forEach(el => { el.style.display = esCanal2 ? 'none' : ''; });
  document.querySelectorAll('.tab-canal2').forEach(el => { el.style.display = esCanal2 ? '' : 'none'; });

  // Bloques internos del dashboard
  document.querySelectorAll('.d-canal1-only').forEach(el => { el.style.display = esCanal2 ? 'none' : ''; });
  document.querySelectorAll('.d-canal2-only').forEach(el => { el.style.display = esCanal2 ? '' : 'none'; });

  // Para Canal 2: la primera tab activa es kit-ventas, no dashboard
  if (esCanal2) {
    const tabKitVentas = document.querySelector('.tab-canal2[onclick*="kit-ventas"]');
    if (tabKitVentas) {
      cambiarTab('kit-ventas', tabKitVentas);
    }
  }

  // Para Canal 1: mostrar banner del kit PDF (Módulo 8) si no fue cerrado
  if (!esCanal2) {
    const dismissKey = `kit_banner_dismiss_${aliado.codigo}`;
    const banner = document.getElementById('d-canal1-kit-banner');
    if (banner) {
      banner.style.display = localStorage.getItem(dismissKey) ? 'none' : 'block';
    }
  }
}

// ── BANNER KIT VENTAS: cerrar (recordado en localStorage) ────────────────────
function cerrarBannerKitVentas() {
  const banner = document.getElementById('d-canal1-kit-banner');
  if (banner) banner.style.display = 'none';
  if (aliado && aliado.codigo) {
    localStorage.setItem(`kit_banner_dismiss_${aliado.codigo}`, '1');
  }
}

// ── BANNER KIT VENTAS: ir directo al Módulo 8 de la Academia ─────────────────
function irAModulo8KitVentas() {
  const tabAcademia = document.querySelector('.tab-btn[onclick*="academia"]');
  if (tabAcademia) {
    cambiarTab('academia', tabAcademia);
    setTimeout(() => {
      if (typeof abrirModulo === 'function') abrirModulo(8);
    }, 200);
  }
}

// ── CANAL: selector de canal en formulario de registro ───────────────────────
function seleccionarCanal(canal) {
  document.querySelectorAll('.canal-card').forEach(card => {
    const esEste = card.dataset.canal === canal;
    card.style.borderColor = esEste
      ? (canal === 'canal2' ? '#4ade80' : 'var(--primary)')
      : 'var(--border)';
    card.style.background = esEste
      ? (canal === 'canal2' ? 'rgba(74,222,128,0.1)' : 'rgba(59,130,246,0.1)')
      : (canal === 'canal2' ? 'rgba(74,222,128,0.04)' : 'rgba(59,130,246,0.04)');
  });
  document.getElementById('reg-tipo-aliado').value = canal;

  // Actualizar opciones del dropdown según canal
  const perfiles = canal === 'canal1'
    ? [
        { value: 'Closer de Ventas',        label: 'Closer de Ventas' },
        { value: 'SDR / Prospectador',       label: 'SDR / Prospectador' },
        { value: 'Representante Comercial',  label: 'Representante Comercial' },
        { value: 'Vendedor Freelance',       label: 'Vendedor Freelance' },
        { value: 'Otro',                     label: 'Otro' },
      ]
    : [
        { value: 'Consultor Industrial',     label: 'Consultor Industrial' },
        { value: 'Estudio Contable',         label: 'Estudio / Contador' },
        { value: 'Agencia B2B',             label: 'Agencia B2B / Marketing' },
        { value: 'Vendedor Freelance',       label: 'Vendedor Freelance' },
        { value: 'Proveedor de Industrias',  label: 'Proveedor de Industrias' },
        { value: 'Otro',                     label: 'Otro' },
      ];

  const sel = document.getElementById('reg-perfil');
  sel.innerHTML = '<option value="" disabled selected style="background:#111827;color:#888;">Elegí tu perfil</option>';
  perfiles.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.value;
    opt.textContent = p.label;
    opt.style.background = '#111827';
    opt.style.color = '#fff';
    sel.appendChild(opt);
  });
  sel.value = '';
}

function cerrarSesion() { 
  aliado = null;
  _clearToken();
  document.getElementById('portal-screen').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('login-codigo').value = '';
  document.getElementById('login-pass').value = '';
  document.getElementById('float-support').style.display = 'none';
}

async function intentarAutoLogin() {
  const token = _getToken();
  if (!token) return false;
  try {
    const res = await fetch(API + '/aliados/me', {
      headers: { 'Authorization': 'Bearer ' + token }
    });
    if (!res.ok) { _clearToken(); return false; }
    aliado = await res.json();
    aliado.token = token;
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('portal-screen').style.display = 'block';
    cargarTodo();
    return true;
  } catch { _clearToken(); return false; }
}

let _heartbeatTimer = null;
let _heartbeatVisibilityBound = false;
function iniciarHeartbeat() {
  const ping = () => { if (!document.hidden) apiFetch(`${API}/aliados/ping`).catch(()=>{}); };
  if (!_heartbeatVisibilityBound) {
    // Los navegadores móviles pausan/throttlean los setInterval de pestañas en
    // segundo plano (al cambiar de app, bloquear el teléfono, etc.), así que el
    // heartbeat de 60s deja de llegar. Sin esto, el aliado puede tardar hasta
    // 5 min en volver a figurar "activo ahora" en el admin después de volver
    // a la pestaña. Con este listener, pingueamos apenas la pestaña vuelve a
    // primer plano, además del intervalo normal.
    document.addEventListener('visibilitychange', () => { if (!document.hidden) ping(); });
    _heartbeatVisibilityBound = true;
  }
  if (_heartbeatTimer) return; // ya está corriendo, cargarTodo() se llama varias veces por sesión
  ping(); // primer ping inmediato para que figure "activo" apenas entra
  _heartbeatTimer = setInterval(ping, 60000);
}

async function cargarTodo() {
  try { _initPush(); } catch (e) {}
  iniciarHeartbeat();
  try { const res = await apiFetch(`${API}/aliados/${aliado.codigo}`); if(res.ok) aliado = await res.json(); } catch {}
  
  // Configurar visibilidad de tabs y bloques según canal del aliado
  configurarCanal();

  renderDashboard(); 
  renderVentas(); 
  renderComisiones();
  cargarReputacion();
  cargarCreditos();
  cargarSiguienteAccion();
  cargarOnboardingChecklist();
  actualizarBadgeAcademia();
  actualizarBadgeCapturas();
  iniciarNovedades();

  // Mostrar botón flotante de soporte
  document.getElementById('float-support').style.display = 'flex';

  // Onboarding primer login: poblar ref link
  const refBox = document.getElementById('onboard-sales-link');
  if (refBox && aliado) refBox.textContent = `avanzadigital.digital/p/${aliado.ref_code}`;

  // Onboarding primer login: guion recomendado para Canal 2
  _inyectarGuionRecomendado(aliado);

  // Onboarding: mostrar saldo de créditos de bienvenida
  const creditosEl = document.getElementById('onboard-creditos-num');
  if (creditosEl && aliado) {
    const creds = aliado.creditos ?? aliado.creditos_bienvenida ?? 0;
    creditosEl.textContent = creds;
  }

  // Onboarding primer login
  const key = `portal_onboarded_${aliado.codigo}`;
  if (!localStorage.getItem(key)) {
    // Aliado nuevo: primero el modal de bienvenida; el tour se encadena al cerrarlo (cerrarOnboarding).
    setTimeout(() => document.getElementById('modal-onboarding').classList.add('open'), 600);
  } else {
    // Aliado que ya pasó el onboarding: si todavía no vio el tour, se lo mostramos una vez.
    setTimeout(() => { try { window.AvanzaTour && AvanzaTour.maybeStart(); } catch (e) {} }, 900);
  }
}

function _inyectarGuionRecomendado(aliado) {
  if (!aliado || aliado.tipo_aliado !== 'canal2') return;
  const GUIONES_C2 = {
    'Consultor Industrial':    { id: '1cZICwO6cfT4MmLj7gn37clCr-nN6mgcW', nombre: 'Guón Canal 2 — Consultores' },
    'Estudio Contable':        { id: '1hT4iHxV-2suvC4LAlg2Y0y7gvUsM25iN', nombre: 'Guón Canal 2 — Contadores' },
    'Agencia B2B':             { id: '1GH0y75Db9LtTRUweAnQBm4ZKw9fHkf1H', nombre: 'Guón Canal 2 — Agencias' },
    'Proveedor de Industrias': { id: '1KBzZB9XMmwjDhGFOO0kuCp7yIe6eCXM7', nombre: 'Guón Canal 2 — Proveedores' },
  };
  const guion = GUIONES_C2[aliado.perfil];
  if (!guion) return;
  const kitBox = document.querySelector('#modal-onboarding .onboard-col-left > div:first-child');
  if (!kitBox) return;
  const url = `https://drive.google.com/uc?export=download&id=${guion.id}`;
  const card = document.createElement('a');
  card.href = '#';
  card.onclick = (e) => { e.preventDefault(); descargarPDF(url, guion.nombre + '.pdf'); };
  card.style.cssText = 'display:flex;align-items:center;gap:10px;background:rgba(74,222,128,0.1);border:1px solid rgba(74,222,128,0.4);border-radius:8px;padding:12px;text-decoration:none;color:var(--text);transition:all .2s;margin-bottom:10px;';
  card.innerHTML = `
    <i class="fa-solid fa-star" style="color:#4ade80;font-size:1.1rem;flex-shrink:0;"></i>
    <div>
      <div style="font-size:.65rem;font-weight:800;text-transform:uppercase;letter-spacing:1.2px;color:#4ade80;margin-bottom:2px;">Recomendado para vos</div>
      <div style="font-weight:700;font-size:.83rem;">${guion.nombre}</div>
      <div style="font-size:.71rem;color:var(--text-muted);">El guión exacto para tu perfil</div>
    </div>
    <i class="fa-solid fa-download" style="margin-left:auto;color:#4ade80;flex-shrink:0;"></i>
  `;
  kitBox.insertBefore(card, kitBox.firstChild);
}

function cerrarOnboarding() {
  document.getElementById('modal-onboarding').classList.remove('open');
  if (aliado) localStorage.setItem(`portal_onboarded_${aliado.codigo}`, '1');
  // Encadenar el tour guiado del portal (solo la primera vez)
  setTimeout(() => { try { window.AvanzaTour && AvanzaTour.maybeStart(); } catch (e) {} }, 450);
}

// ── GATE SUAVE: Bolsa de Leads ────────────────────────────────────────────────
function abrirModalGateBolsa(completadosReq) {
  const modConfig = [
    { id: 1, label: 'Módulo 1 · Cómo funciona el programa' },
    { id: 2, label: 'Módulo 2 · Cómo detectar al cliente ideal' },
    { id: 3, label: 'Módulo 3 · Cómo hacer el diagnóstico' },
  ];
  const progreso = getAcademiaProgreso ? getAcademiaProgreso() : [];
  const lista = document.getElementById('gate-modulos-lista');
  if (lista) {
    lista.innerHTML = modConfig.map(m => {
      const hecho = progreso.includes(m.id);
      return `<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:8px;background:${hecho ? 'rgba(74,222,128,0.07)' : 'rgba(255,255,255,0.03)'};border:1px solid ${hecho ? 'rgba(74,222,128,0.25)' : 'var(--border)'};">
        <div style="width:22px;height:22px;border-radius:50%;background:${hecho ? 'var(--green)' : 'rgba(255,255,255,0.08)'};display:flex;align-items:center;justify-content:center;flex-shrink:0;">
          ${hecho ? '<i class="fa-solid fa-check" style="color:#000;font-size:.65rem;"></i>' : '<span style="font-size:.65rem;color:var(--text-dim);">' + m.id + '</span>'}
        </div>
        <span style="font-size:.82rem;color:${hecho ? 'var(--green)' : 'var(--text-muted)'};">${m.label}</span>
        ${hecho ? '' : '<span style="margin-left:auto;font-size:.72rem;color:var(--text-dim);background:rgba(255,255,255,0.05);border-radius:4px;padding:2px 7px;">pendiente</span>'}
      </div>`;
    }).join('');
  }
  document.getElementById('modal-gate-bolsa').classList.add('open');
}

function ignorarGateBolsa() {
  document.getElementById('modal-gate-bolsa').classList.remove('open');
  if (aliado) localStorage.setItem(`bolsa_aviso_visto_${aliado.codigo}`, '1');
  // Ahora sí abrir la bolsa
  const btnBolsa = document.querySelector('.tab-canal1[onclick*="bolsa"]');
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  const panel = document.getElementById('tab-bolsa');
  if (panel) panel.classList.add('active');
  if (btnBolsa) btnBolsa.classList.add('active');
  cargarBolsa(); cargarHistorialBolsa(); cargarMarketplace();
}

function irAcademiaDesdeGate() {
  document.getElementById('modal-gate-bolsa').classList.remove('open');
  const tabAcademia = document.getElementById('btn-tab-academia-primary') ||
                      document.querySelector('.tab-btn[onclick*="academia"]');
  cambiarTab('academia', tabAcademia);
}

// ── BADGE progreso Academia en tab primario ──────────────────────────────────
function actualizarBadgeAcademia() {
  if (!aliado) return;
  const badge = document.getElementById('academia-progress-badge');
  if (!badge) return;
  const progreso = getAcademiaProgreso ? getAcademiaProgreso() : [];
  if (progreso.length === 0) {
    badge.textContent = '¡Empezá!';
    badge.style.display = 'inline';
  } else {
    badge.style.display = 'none';
  }
}

document.getElementById('modal-onboarding').addEventListener('click', e => {
  if (e.target === document.getElementById('modal-onboarding')) cerrarOnboarding();
});

function getMotivationalMsg(aliado) {
  const v6   = aliado.ventas_6m || 0;
  const refs = (aliado.referidos||[]).filter(r=>!r.convertido).length;
  const nivel = aliado.nivel_calculado || aliado.nivel_actual || 'BASIC';
  if (nivel === 'ELITE') return { icon:'🏆', text:`<strong>¡Sos ELITE!</strong> Tenés el máximo 20% en todos los planes. Seguís rompiendo récords.` };
  if (v6 >= 3) return { icon:'🔥', text:`<strong>${v6} ventas</strong> en los últimos 6 meses. Estás a ${5-v6} de nivel ELITE y 20% de comisión.` };
  if (v6 === 2) return { icon:'⚡', text:`¡Bien! ${v6} ventas cerradas. <strong>Solo 1 más</strong> para llegar a PREMIUM (15%).` };
  if (v6 === 1) return { icon:'💡', text:`<strong>Primera venta lograda.</strong> Agregá 1 más para subir a SILVER y ganar más en cada cierre.` };
  if (refs > 0) return { icon:'👀', text:`Tenés <strong>${refs} prospecto${refs>1?'s':''} en proceso</strong>. El próximo cierre puede ser el tuyo. ¡Hacé seguimiento!` };
  return { icon:'🚀', text:`<strong>Prospectá hoy mismo.</strong> Usá tu enlace de auditoría y envíaselo a empresas industriales. ¡Funciona!` };
}

function renderDashboard() {
  try { renderTareasHoy(); } catch (e) {}
  const nivel = aliado.nivel_calculado || aliado.nivel_actual || 'BASIC';
  const p = Math.round(pct(nivel)*100);
  const v6 = aliado.ventas_6m || 0;
  const refsActivos = (aliado.referidos||[]).filter(r=>!r.convertido).length;

  document.getElementById('nav-nivel').textContent = nivel;
  document.getElementById('nav-nombre').textContent = (aliado.nombre||'').split(' ')[0];
  document.getElementById('d-firstname').textContent = (aliado.nombre||'aliado').split(' ')[0];
  document.getElementById('d-welcome-sub').textContent = `Nivel: ${nivel} · Comisión: ${p}% · Código: ${aliado.codigo}`;
  document.getElementById('d-ventas6m').textContent = v6;
  document.getElementById('d-ganado').textContent = `USD ${Math.round(aliado.total_ganado||0).toLocaleString()}`;
  document.getElementById('d-pendiente').textContent = `USD ${Math.round(aliado.total_pendiente||0).toLocaleString()}`;
  // MRR del aliado (10% de planes de continuidad activos de sus clientes).
  // Si el backend todavía no devuelve este campo, queda en 0 y el sub-texto invita a vender continuidad.
  const mrrEl = document.getElementById('d-mrr');
  if(mrrEl) {
    const mrr = Number(aliado.mrr_recurrente_usd || 0);
    mrrEl.textContent = `USD ${Math.round(mrr).toLocaleString()}`;
  }
  document.getElementById('d-refs-activos').textContent = refsActivos;
  
  const linkAuditoria = `https://avanzadigital.digital/auditoria-digital?ref=${aliado.ref_code}`;

  document.getElementById('d-auditlink').textContent = linkAuditoria;

  const linkCalculadora = `https://avanzadigital.digital/calculadora-ineficiencia?ref=${aliado.ref_code}`;
  const calcEl = document.getElementById('d-calclink');
  if (calcEl) calcEl.textContent = linkCalculadora;
  const calcMsgEl = document.getElementById('d-calc-link-msg');
  if (calcMsgEl) calcMsgEl.textContent = linkCalculadora;

  // Card de "Personalizá tu link": visible solo si todavía no personalizó.
  // Una vez que personaliza, queda oculta para siempre.
  const persoCard = document.getElementById('personalizar-username-card');
  if (persoCard) {
    persoCard.style.display = aliado.username_personalizado ? 'none' : 'flex';
  }

  // Página de ventas personal (/p/{ref_code})
  const perfilCard = document.getElementById('perfil-publico-card');
  const perfilEditor = document.getElementById('perfil-editor-card');
  if (aliado.portal_publico_activo && aliado.link_perfil) {
    document.getElementById('d-perfillink').textContent = aliado.link_perfil;
    if (perfilCard) perfilCard.style.display = 'flex';
    // Mostrar y pre-cargar el bloque editor
    if (perfilEditor) {
      perfilEditor.style.display = 'block';
      const titularInput = document.getElementById('pp-titular');
      const bioInput = document.getElementById('pp-bio');
      if (titularInput) {
        titularInput.value = aliado.portal_publico_titular || '';
        document.getElementById('pp-titular-count').textContent = titularInput.value.length;
      }
      if (bioInput) {
        bioInput.value = aliado.portal_publico_bio || '';
        document.getElementById('pp-bio-count').textContent = bioInput.value.length;
      }
      const fotoInput = document.getElementById('pp-foto-url');
      if (fotoInput && aliado.portal_publico_foto_url) {
        fotoInput.value = aliado.portal_publico_foto_url;
        previsualizarFoto(aliado.portal_publico_foto_url);
      }
    }
  } else {
    if (perfilCard) perfilCard.style.display = 'none';
    if (perfilEditor) perfilEditor.style.display = 'none';
  }
  document.getElementById('d-wpp-link').textContent = linkAuditoria;

  const proxMap = {BASIC:'SILVER (1 venta)',SILVER:'PREMIUM (2 ventas)',PREMIUM:'ELITE (5 ventas)',ELITE:'¡Nivel máximo!'};
  document.getElementById('d-prox').textContent = `→ Próximo: ${proxMap[nivel]}`;

  // Banner motivacional
  const { icon, text } = getMotivationalMsg(aliado);
  document.getElementById('motive-icon').textContent = icon;
  document.getElementById('motive-text').innerHTML = text;

  // Barra de progreso
  const nivelOrden = ['BASIC','SILVER','PREMIUM','ELITE'];
  const maxVentas  = [1, 2, 5, Infinity];
  const idx = nivelOrden.indexOf(nivel);
  const prevMax = idx > 0 ? maxVentas[idx-1] : 0;
  const nextMax = maxVentas[idx];
  let pctBar = 0;
  const segmentWidth = 100 / 3;

  if (nivel === 'ELITE') {
    pctBar = 100;
  } else {
    const ventasEnSegmento = Math.min(v6, nextMax) - prevMax;
    const rangoSegmento = nextMax - prevMax;
    const pctEnSegmento = Math.min(ventasEnSegmento / rangoSegmento, 1);
    pctBar = idx * segmentWidth + pctEnSegmento * segmentWidth;
  }

  setTimeout(() => { document.getElementById('nivel-bar').style.width = Math.max(pctBar, 2) + '%'; }, 200);

  // Labels de la barra
  nivelOrden.forEach((n, i) => {
    const lbl = document.getElementById(`plbl-${n}`);
    lbl.className = 'nivel-progress-label' + (i < idx ? ' done' : i === idx ? ' active' : '');
  });

  // Caption de progreso
  const faltan = Math.max(nextMax - v6, 0);
  document.getElementById('d-progreso-text').innerHTML = nivel === 'ELITE'
    ? `<i class="fa-solid fa-trophy" style="color:var(--amber);"></i> ¡Nivel máximo alcanzado! Tenés el <strong style="color:white;">20%</strong> en todos los planes.`
    : `Te ${faltan===1?'falta':'faltan'} <strong style="color:white;">${faltan} ${faltan===1?'venta':'ventas'}</strong> para llegar a <strong style="color:var(--primary);">${proxMap[nivel].split(' ')[0]}</strong>.`;

  // Beneficios
  document.getElementById('d-beneficios').innerHTML = (BENEFICIOS[nivel]||BENEFICIOS.BASIC).map(b=>
    `<div style="display:flex;align-items:center;gap:10px;font-size:0.88rem;color:var(--text-muted);">
      <i class="fa-solid fa-circle-check" style="color:var(--green);font-size:0.8rem;flex-shrink:0;"></i> ${b}
    </div>`
  ).join('');
}

function copiarPerfilLink() {
  navigator.clipboard.writeText(document.getElementById('d-perfillink').textContent).then(()=>{
    mostrarToast('Página de ventas copiada — compartíla con tus prospectos', 'green');
  });
}

async function guardarPortalPublico() {
  if (!aliado) return;
  const titular  = (document.getElementById('pp-titular').value  || '').trim();
  const bio      = (document.getElementById('pp-bio').value      || '').trim();
  const foto_url = (document.getElementById('pp-foto-url')?.value || '').trim();

  const btn = document.querySelector('[onclick="guardarPortalPublico()"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Guardando…'; }

  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/portal-publico`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ portal_publico_titular: titular, portal_publico_bio: bio, portal_publico_foto_url: foto_url || null })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    // Actualizar el objeto local para que el fallback sea coherente si recarga
    aliado.portal_publico_titular  = titular;
    aliado.portal_publico_bio      = bio;
    aliado.portal_publico_foto_url = foto_url || null;

    // Mostrar confirmación inline
    const msg = document.getElementById('pp-guardado-msg');
    if (msg) { msg.style.display = 'inline-flex'; setTimeout(() => { msg.style.display = 'none'; }, 3500); }
    mostrarToast('Perfil público actualizado ✓', 'green');
  } catch (err) {
    console.error('Error al guardar perfil público:', err);
    mostrarToast('Error al guardar. Intentá de nuevo.', 'red');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> Guardar'; }
  }
}

function previsualizarFoto(url) {
  const preview = document.getElementById('pp-foto-preview');
  if (!preview) return;
  if (url && url.startsWith('https://')) {
    preview.innerHTML = `<img src="${url}" alt="Foto de perfil"
      style="width:100%;height:100%;object-fit:cover;border-radius:50%;"
      onerror="this.parentNode.innerHTML='👤'">`;
  } else {
    preview.innerHTML = '👤';
  }
}

async function subirFotoCloudinary(input) {
  const file = input.files[0];
  if (!file) return;

  // Validar tamaño (5 MB máx)
  if (file.size > 5 * 1024 * 1024) {
    mostrarToast('La imagen no puede superar 5 MB.', 'red');
    input.value = '';
    return;
  }

  const status  = document.getElementById('pp-foto-status');
  const preview = document.getElementById('pp-foto-preview');

  // Mostrar spinner en el preview
  preview.innerHTML = '<i class="fa-solid fa-spinner fa-spin" style="font-size:1.4rem;color:var(--primary);"></i>';
  if (status) status.textContent = 'Subiendo imagen…';

  try {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('upload_preset', 'avanza-perfiles');
    // Guardar en carpeta separada por aliado para orden
    formData.append('folder', `avanza/perfiles/${aliado?.codigo || 'unknown'}`);
    // Recorte cuadrado automático centrado en la cara

    const res = await fetch('https://api.cloudinary.com/v1_1/dqbekidhy/image/upload', {
      method: 'POST',
      body: formData
    });

    if (!res.ok) throw new Error(`Cloudinary error ${res.status}`);
    const data = await res.json();

    // Usar URL segura con transformación circular de 400x400
    const url = data.secure_url;

    // Guardar en el campo oculto y actualizar preview
    document.getElementById('pp-foto-url').value = url;
    previsualizarFoto(url);

    if (status) status.innerHTML = '<i class="fa-solid fa-circle-check" style="color:var(--green);"></i> Foto lista — hacé clic en <strong>Guardar</strong>';
    mostrarToast('Foto subida ✓ — guardá para aplicar los cambios', 'green');

  } catch (err) {
    console.error('Error subiendo foto:', err);
    preview.innerHTML = '👤';
    if (status) status.textContent = 'Error al subir. Intentá de nuevo.';
    mostrarToast('No se pudo subir la foto.', 'red');
  } finally {
    input.value = ''; // limpiar input para permitir re-selección
  }
}

function copiarAuditLink() {
  navigator.clipboard.writeText(document.getElementById('d-auditlink').textContent).then(()=>{
    mostrarToast('Enlace de auditoría copiado', 'green');
  });
}

function copiarCalcLink() {
  const el = document.getElementById('d-calclink');
  if (!el || el.textContent === 'Cargando...') return;
  navigator.clipboard.writeText(el.textContent).then(()=>{
    mostrarToast('Enlace de calculadora copiado', 'green');
  });
}

function copiarMensajeCalc() {
  const link = document.getElementById('d-calclink');
  if (!link || link.textContent === 'Cargando...') return;
  const msg = `Hola, te comparto una calculadora gratuita que arma un diagnóstico en 2 minutos: te muestra cuánto dinero pierde tu empresa por mes por tareas comerciales manuales. Es solo para que tengas el número real, sin compromiso: ${link.textContent}`;
  navigator.clipboard.writeText(msg).then(()=>{
    mostrarToast('Plantilla de mensaje copiada', 'green');
  });
}

function copiarLinkRed() {
  const el = document.getElementById('red-link-reclutamiento');
  if (!el || !el.textContent || el.textContent === 'Cargando...') return;
  navigator.clipboard.writeText(el.textContent.trim()).then(()=>{
    mostrarToast('Link de reclutamiento copiado ✓', 'green');
  });
}

function copiarMensajeWpp() {
  const texto = document.getElementById('d-mensaje-wpp').innerText;
  navigator.clipboard.writeText(texto).then(()=>{
    mostrarToast('Plantilla copiada', 'green');
  });
}

function renderReferidos() {
  const refs = aliado.referidos||[];
  const el = document.getElementById('tabla-referidos');
  if (!refs.length) { el.innerHTML=`<div class="empty-state"><i class="fa-solid fa-user-plus"></i><p>Todavía no registraste ningún prospecto.<br>Usá el formulario de arriba <strong style="color:white;">antes de que el cliente pague</strong>.</p></div>`; return; }
  el.innerHTML=`<table><thead><tr><th>Cliente / Empresa</th><th>Plan</th><th>Comisión est.</th><th>Fecha</th><th>Confirmado</th><th>Estado</th></tr></thead><tbody>${refs.map(r=>{
    const nivel = aliado.nivel_calculado || aliado.nivel_actual || 'BASIC';
    const comEst = Math.round((PLANES[r.plan]||0)*pct(nivel));
    const confCol = r.rechazado
      ? '<span class="badge badge-red"><i class="fa-solid fa-circle-xmark"></i> No confirmado</span>'
      : (r.confirmado ? '<span class="badge badge-green"><i class="fa-solid fa-check"></i> Confirmado</span>' : '<span class="badge badge-gray"><i class="fa-solid fa-clock"></i> Pendiente</span>');
    const notaFila = r.nota_admin ? `<tr><td colspan="6" style="padding:4px 24px 16px;font-size:.8rem;color:var(--text-dim);background:rgba(255,255,255,0.015);"><i class="fa-solid fa-message" style="color:var(--amber);"></i> Nota de Avanza: <span style="color:var(--text);">${r.nota_admin}</span></td></tr>` : '';
    return `<tr>
      <td style="color:var(--text);font-weight:600;">${r.cliente}</td>
      <td><span class="badge badge-blue">${r.plan}</span></td>
      <td style="color:var(--green);font-weight:700;">${comEst>0?'USD '+comEst.toLocaleString():'—'}</td>
      <td style="font-size:.82rem;color:var(--text-dim);">${r.fecha}</td>
      <td>${confCol}</td>
      <td>${r.convertido?'<span class="badge badge-green"><i class="fa-solid fa-handshake"></i> Venta cerrada</span>':'<span class="badge badge-blue"><i class="fa-solid fa-clock"></i> En proceso</span>'}</td>
    </tr>${notaFila}`;
  }).join('')}</tbody></table>`;
}

function renderVentas() {
  const ventas = aliado.ventas||[];
  const el = document.getElementById('tabla-ventas');
  if (!ventas.length) { el.innerHTML=`<div class="empty-state"><i class="fa-solid fa-handshake"></i><p>Todavía no tenés ventas confirmadas.<br>Cuando Avanza registre tu primera venta, aparecerá acá.</p></div>`; return; }
  el.innerHTML=`<table><thead><tr><th>Cliente</th><th>Plan</th><th>Valor (USD)</th><th>Tu comisión</th><th>Estado</th><th>Fecha</th></tr></thead><tbody>${ventas.map(v=>`<tr>
    <td style="color:var(--text);font-weight:600;">${v.cliente}</td><td>${v.plan}</td>
    <td>USD ${v.valor.toLocaleString()}</td>
    <td style="color:var(--green);font-weight:800;">USD ${Math.round(v.comision).toLocaleString()}</td>
    <td>${v.pagada?'<span class="badge badge-green"><i class="fa-solid fa-circle-check"></i> Pagada</span>':'<span class="badge badge-amber"><i class="fa-solid fa-clock"></i> Pendiente 24hs</span>'}</td>
    <td style="font-size:.82rem;color:var(--text-dim);">${v.fecha||'—'}</td>
  </tr>`).join('')}</tbody></table>`;
}

async function renderComisiones() {
  if(!aliado) return;

  // Grilla ONE-TIME — tarjetas de comisión por plan según nivel del aliado
  const nivel = aliado.nivel_calculado||aliado.nivel_actual||'BASIC';
  const p = pct(nivel);
  document.getElementById('planes-grid').innerHTML = Object.entries(PLANES).map(([plan,precio])=>`
    <div class="plan-card">
      <div class="plan-name">${plan}</div>
      <div class="plan-precio">Precio: USD ${precio.toLocaleString()}</div>
      <div class="plan-comision">USD ${Math.round(precio*p).toLocaleString()}</div>
      <div class="plan-pct">Nivel ${nivel} · ${Math.round(p*100)}%</div>
    </div>`).join('');

  // Grilla RECURRENTE — tarjetas de comisión recurrente por Plan de Continuidad
  // Comisión fija 10% mensual, no depende del nivel del aliado.
  const gridRec = document.getElementById('planes-recurrentes-grid');
  if(gridRec) {
    gridRec.innerHTML = Object.entries(PLANES_CONTINUIDAD).map(([plan,precio])=>`
      <div class="plan-card" style="border-color:rgba(250,204,21,0.25); background:rgba(250,204,21,0.03);">
        <div class="plan-name" style="color:var(--amber);">${plan}</div>
        <div class="plan-precio">Cliente paga: USD ${precio}/mes</div>
        <div class="plan-comision" style="color:var(--amber);">USD ${Math.round(precio*COMISION_RECURRENTE_PCT)}/mes</div>
        <div class="plan-pct" style="color:var(--text-muted);">Tu 10% recurrente · de por vida</div>
      </div>`).join('');
  }

  // Pintar método de cobro desde el objeto aliado
  const cbuInput = document.getElementById('perfil-cbu');
  const cbuEstado = document.getElementById('perfil-cbu-estado');
  if(cbuInput) cbuInput.value = aliado.cbu_alias || '';
  if(cbuEstado) {
    const tieneMetodo = (aliado.payment_method === 'transferencia' && aliado.cobro_numero_cuenta)
      || (aliado.payment_method && aliado.payment_method !== 'transferencia' && aliado.payment_info);
    if(tieneMetodo) {
      cbuEstado.innerHTML = `<span style="color:var(--green);"><i class="fa-solid fa-circle-check"></i> Método de cobro configurado.</span>`;
    } else if(aliado.cbu_alias) {
      cbuEstado.innerHTML = `<span style="color:var(--green);"><i class="fa-solid fa-circle-check"></i> Método de cobro cargado.</span>`;
    } else {
      cbuEstado.innerHTML = `<span style="color:var(--amber);"><i class="fa-solid fa-triangle-exclamation"></i> Configurá tu método de cobro para poder cobrar.</span>`;
    }
  }

  // Consumir endpoint de comisiones (spec §16)
  const el = document.getElementById('tabla-comisiones');
  const totalPendEl = document.getElementById('com-total-pendiente');
  const totalAbonEl = document.getElementById('com-total-abonado');
  const mrrEl = document.getElementById('com-mrr-actual');
  const mrrDetalleEl = document.getElementById('com-mrr-detalle');
  const tablaRecEl = document.getElementById('tabla-recurrentes-activos');
  el.innerHTML = `<div class="empty-state"><div class="spinner"></div><p style="margin-top:12px;">Cargando comisiones...</p></div>`;

  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/comisiones`);
    if(!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();

    // Totales en grande
    if(totalPendEl) totalPendEl.textContent = 'USD ' + Math.round(data.total_pendiente_usd||0).toLocaleString();
    if(totalAbonEl) totalAbonEl.textContent = 'USD ' + Math.round(data.total_abonado_usd||0).toLocaleString();

    // MRR del aliado: suma del 10% sobre todos los planes de continuidad activos de sus clientes.
    // El backend debe devolver:
    //   - data.mrr_recurrente_usd: número (USD/mes que está cobrando ahora)
    //   - data.clientes_continuidad_activos: array de { cliente, plan, precio_mensual, comision_mensual, fecha_alta }
    // Si todavía no está implementado, mostramos cero + mensaje educativo.
    const mrr = Number(data.mrr_recurrente_usd || 0);
    const clientesRec = data.clientes_continuidad_activos || [];
    if(mrrEl) mrrEl.textContent = 'USD ' + Math.round(mrr).toLocaleString();
    if(mrrDetalleEl) {
      if(clientesRec.length > 0) {
        mrrDetalleEl.innerHTML = `<strong style="color:var(--text);">${clientesRec.length} cliente${clientesRec.length>1?'s':''}</strong> con Plan de Continuidad activo. Se acumula sobre el pendiente cada mes.`;
      } else {
        mrrDetalleEl.textContent = 'Todavía no tenés clientes con Plan de Continuidad activo. Cuando uno contrate, esto empieza a sumar.';
      }
    }

    // Tabla de clientes con continuidad activa
    if(tablaRecEl) {
      if(clientesRec.length === 0) {
        tablaRecEl.innerHTML = `<div class="empty-state"><i class="fa-solid fa-arrows-rotate"></i><p>Todavía no tenés clientes con Plan de Continuidad activo.<br><span style="font-size:.82rem;color:var(--text-dim);">Cuando uno de tus clientes contrate Plan Cuidado, Crecimiento, Escala o Liderazgo, vas a cobrar el 10% mensual mientras lo mantenga vivo.</span></p></div>`;
      } else {
        tablaRecEl.innerHTML = `
          <table>
            <thead>
              <tr>
                <th>Cliente</th>
                <th>Plan</th>
                <th>Precio mensual</th>
                <th>Mi comisión / mes</th>
                <th>Activo desde</th>
                <th style="text-align:right;">Acciones</th>
              </tr>
            </thead>
            <tbody>
              ${clientesRec.map(c => `
                <tr data-continuidad-id="${c.id}">
                  <td style="color:var(--text);font-weight:600;">${c.cliente||'—'}</td>
                  <td>${c.plan||'—'}</td>
                  <td>USD ${Math.round(c.precio_mensual||0).toLocaleString()}</td>
                  <td style="color:var(--amber);font-weight:800;">USD ${Math.round(c.comision_mensual||0).toLocaleString()}/mes</td>
                  <td style="font-size:.82rem;color:var(--text-dim);">${c.fecha_alta||'—'}</td>
                  <td style="text-align:right;">
                    <button onclick="darDeBajaContinuidad(${c.id}, '${(c.cliente||'').replace(/'/g,'\\\'')}', '${(c.plan||'').replace(/'/g,'\\\'')}')"
                            style="background:transparent;border:1px solid rgba(239,68,68,0.4);color:#ef4444;border-radius:6px;padding:6px 12px;font-size:.78rem;font-weight:700;cursor:pointer;">
                      <i class="fa-solid fa-circle-stop"></i> Baja
                    </button>
                  </td>
                </tr>
              `).join('')}
            </tbody>
          </table>`;
      }
    }

    const comisiones = data.comisiones || [];
    if(!comisiones.length) {
      el.innerHTML = `<div class="empty-state"><i class="fa-solid fa-receipt"></i><p>Todavía no tenés comisiones registradas.<br>Cuando un cliente tuyo pague, aparecerán acá.</p></div>`;
      return;
    }

    const badgeEstado = (estado) => estado === 'abonada'
      ? '<span class="badge badge-green"><i class="fa-solid fa-check"></i> Abonada</span>'
      : '<span class="badge badge-amber"><i class="fa-solid fa-clock"></i> Pendiente</span>';

    const formatoFecha = (iso) => {
      if(!iso) return '—';
      const d = new Date(iso);
      return d.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit', year: '2-digit' });
    };

    el.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Cliente</th>
            <th>Plan</th>
            <th>Monto plan</th>
            <th>Mi comisión</th>
            <th>Estado</th>
            <th>Fecha pago</th>
            <th>Fecha abono</th>
          </tr>
        </thead>
        <tbody>
          ${comisiones.map(c => `
            <tr>
              <td style="color:var(--text); font-weight:600;">${c.cliente || '—'}</td>
              <td>${c.plan}</td>
              <td style="color:var(--text-muted);">USD ${Math.round(c.monto_plan_usd).toLocaleString()}</td>
              <td style="color:var(--green); font-weight:800;">USD ${Math.round(c.comision_usd).toLocaleString()}</td>
              <td>${badgeEstado(c.estado)}</td>
              <td style="font-size:.82rem; color:var(--text-dim);">${formatoFecha(c.fecha_pago)}</td>
              <td style="font-size:.82rem; color:var(--text-dim);">${formatoFecha(c.fecha_abono)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  } catch(e) {
    console.error('Error cargando comisiones:', e);
    // Fallback: usar el formato viejo con aliado.ventas si el endpoint nuevo falla
    const ventas = aliado.ventas||[];
    if (!ventas.length) {
      el.innerHTML=`<div class="empty-state"><i class="fa-solid fa-receipt"></i><p>Las comisiones de tus ventas confirmadas aparecerán acá.</p></div>`;
      if(totalPendEl) totalPendEl.textContent = 'USD 0';
      if(totalAbonEl) totalAbonEl.textContent = 'USD 0';
      return;
    }
    const totalGanado = ventas.reduce((s,v)=>s+v.comision,0);
    const pendiente = ventas.filter(v=>!v.pagada).reduce((s,v)=>s+v.comision,0);
    if(totalPendEl) totalPendEl.textContent = 'USD ' + Math.round(pendiente).toLocaleString();
    if(totalAbonEl) totalAbonEl.textContent = 'USD ' + Math.round(totalGanado - pendiente).toLocaleString();
    el.innerHTML=`<table><thead><tr><th>Cliente</th><th>Plan</th><th>Comisión</th><th>Estado</th></tr></thead><tbody>${ventas.map(v=>`<tr>
      <td style="color:var(--text);font-weight:600;">${v.cliente}</td><td>${v.plan}</td>
      <td style="color:var(--green);font-weight:800;">USD ${Math.round(v.comision).toLocaleString()}</td>
      <td>${v.pagada?'<span class="badge badge-green"><i class="fa-solid fa-check"></i> Pagada</span>':'<span class="badge badge-amber"><i class="fa-solid fa-clock"></i> Pendiente</span>'}</td>
    </tr>`).join('')}</tbody></table>`;
  }
}

// ─── PLANES DE CONTINUIDAD — Aliado da de alta / baja sus propios clientes (v1.5) ──
function actualizarPreviewContinuidad() {
  const plan = document.getElementById('nc-plan').value;
  const preview = document.getElementById('nc-preview');
  if (!plan || !PLANES_CONTINUIDAD[plan]) {
    preview.innerHTML = 'Elegí un plan para ver tu comisión mensual estimada.';
    return;
  }
  const precio = PLANES_CONTINUIDAD[plan];
  const comision = Math.round(precio * COMISION_RECURRENTE_PCT);
  preview.innerHTML = `<i class="fa-solid fa-circle-info" style="color:var(--amber);"></i> Cliente paga <strong style="color:var(--text);">USD ${precio}/mes</strong> · Tu comisión: <strong style="color:var(--amber);">USD ${comision}/mes</strong> mientras esté activo. La primera comisión se acumula al instante en tus pendientes; cada 1ro del mes siguiente se suma otra automáticamente.`;
}

async function cargarNuevoContinuidad() {
  if (!aliado) return;
  const nombre = (document.getElementById('nc-nombre').value || '').trim();
  const plan   = document.getElementById('nc-plan').value;
  const email  = (document.getElementById('nc-email').value || '').trim();
  const notas  = (document.getElementById('nc-notas').value || '').trim();
  const btn    = document.getElementById('btn-nc-activar');
  const estado = document.getElementById('nc-estado');

  if (!nombre) { mostrarToast('Falta el nombre del cliente.', 'red'); return; }
  if (!plan)   { mostrarToast('Falta elegir el plan.', 'red'); return; }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Activando...';
  estado.textContent = '';
  try {
    const res = await apiJSON(`${API}/aliado/continuidad/alta`, 'POST', {
      nombre_cliente: nombre,
      plan_continuidad: plan,
      cliente_email: email || null,
      notas: notas || null,
      lead_id: window._continuidadLeadId || null,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.mensaje || `Error ${res.status}`);
    }
    if (data.status === 'already_active') {
      mostrarToast(data.mensaje || 'Ese cliente ya tiene este plan activo.', 'amber');
    } else {
      mostrarToast(data.mensaje || `${plan} activado.`, 'green');
      // Limpiar el formulario
      document.getElementById('nc-nombre').value = '';
      document.getElementById('nc-plan').value = '';
      document.getElementById('nc-email').value = '';
      document.getElementById('nc-notas').value = '';
      window._continuidadLeadId = null;
      actualizarPreviewContinuidad();
    }
    // Re-render comisiones para mostrar el cliente nuevo + la comisión recién creada
    await renderComisiones();
  } catch (e) {
    console.error('[CONTINUIDAD ALTA]', e);
    mostrarToast('No pude activar el plan: ' + e.message, 'red');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-arrows-rotate"></i> Activar renta recurrente';
  }
}

async function darDeBajaContinuidad(id, cliente, plan) {
  if (!aliado || !id) return;
  const ok = confirm(
    `¿Dar de baja a ${cliente} (${plan})?\n\n` +
    `A partir de ahora no se generan más comisiones recurrentes para este cliente. ` +
    `Las comisiones que ya tenés acumuladas no se afectan.\n\n` +
    `Solo confirmá si tu cliente ya canceló el servicio.`
  );
  if (!ok) return;
  try {
    const res = await apiJSON(`${API}/aliado/continuidad/${id}/baja`, 'POST', {
      motivo_baja: 'Cancelación reportada por el aliado desde el portal',
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.mensaje || `Error ${res.status}`);
    }
    mostrarToast(data.mensaje || 'Plan dado de baja.', 'green');
    await renderComisiones();
  } catch (e) {
    console.error('[CONTINUIDAD BAJA]', e);
    mostrarToast('No pude dar de baja: ' + e.message, 'red');
  }
}

// ── BAJA VOLUNTARIA (v1.8) ────────────────────────────────────────────────────
async function confirmarBajaVoluntaria() {
  if (!aliado) return;
  const btn    = document.getElementById('btn-confirmar-baja');
  const estado = document.getElementById('baja-voluntaria-estado');
  const motivo = (document.getElementById('baja-motivo').value || '').trim();

  btn.disabled  = true;
  btn.innerHTML = '<span class="spinner"></span> Procesando...';
  estado.style.color = 'var(--text-muted)';
  estado.textContent = '';

  try {
    const res = await fetch(`/aliados/me/solicitar-baja`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ motivo: motivo || null }),
    });
    const data = await res.json();

    if (!res.ok) {
      estado.style.color = '#f87171';
      estado.textContent = data.detail || 'Ocurrió un error. Intentá de nuevo.';
      btn.disabled = false;
      btn.innerHTML = '<i class="fa-solid fa-right-from-bracket"></i> Confirmar baja definitiva';
      return;
    }

    // Éxito: mostrar mensaje y cerrar sesión
    document.getElementById('modal-baja-voluntaria').classList.remove('open');
    alert(`✅ ${data.mensaje}`);
    // Cerrar sesión (la cuenta está suspendida)
    token = null; aliado = null;
    localStorage.removeItem('avanza_token');
    document.getElementById('app-shell').style.display = 'none';
    document.getElementById('login-section').style.display = 'flex';

  } catch (err) {
    estado.style.color = '#f87171';
    estado.textContent = 'Error de conexión. Intentá de nuevo.';
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-right-from-bracket"></i> Confirmar baja definitiva';
  }
}

// v2.1: seleccionar método de pago en Mi Cuenta
let _metodoPagoActual = null;
// v2.6: qué identificador de Wise eligió el aliado (email/telefono/wisetag)
let _wiseTipoActual = null;

const _PM_META = {
  usdt_trc20:    { label: 'USDT · TRC20',           icono: '₮', color: '#2ec99e', bg: 'rgba(38,161,123,0.16)' },
  airtm:         { label: 'Airtm',                   icono: 'A', color: '#38bdf8', bg: 'rgba(56,189,248,0.14)' },
  transferencia: { label: 'Transferencia bancaria',  icono: '<i class="fa-solid fa-building-columns"></i>', color: '#60a5fa', bg: 'rgba(59,130,246,0.14)' },
  wise:          { label: 'Wise',                    icono: 'W', color: '#9fe870', bg: 'rgba(159,232,112,0.13)' },
  payoneer:      { label: 'Payoneer',                icono: 'P', color: '#ff7a45', bg: 'rgba(255,106,51,0.13)' },
};

// v2.6 — espejo (solo para labels/placeholders en el front) del diccionario
// autoritativo que vive en el backend (aliados.py → FORMATOS_CUENTA_POR_PAIS).
// La validación real y definitiva siempre la hace el backend; esto es nomás
// para mostrarle al aliado el nombre correcto de su dato ("CLABE", "CCI", etc.)
// sin tener que ir a buscarlo al servidor.
const PM_FORMATOS_PAIS = {
  AR: { label: 'CBU o alias',    ejemplo: 'Ej: 0070003000000000000001 o juan.perez.mp' },
  BO: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  BR: { label: 'Chave PIX',      ejemplo: 'Ej: CPF, email, teléfono o clave aleatoria' },
  CL: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  CO: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  CR: { label: 'Cuenta IBAN',    ejemplo: 'Ej: CR05015201010026283666 (22 caracteres)' },
  CU: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  DO: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  EC: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  SV: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  GT: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  HN: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  MX: { label: 'CLABE',          ejemplo: 'Ej: 18 dígitos' },
  NI: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  PA: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  PY: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  PE: { label: 'CCI',            ejemplo: 'Ej: 20 dígitos' },
  UY: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  VE: { label: 'N° de cuenta',   ejemplo: 'Ej: 20 dígitos' },
  BZ: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  GY: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  SR: { label: 'N° de cuenta',   ejemplo: 'Ej: número de cuenta bancaria' },
  _default: { label: 'N° de cuenta bancaria', ejemplo: 'Ej: número de cuenta bancaria' },
};

// Ajusta el nombre de la opción, el label del campo y el hint de "transferencia"
// según el país del aliado. En Argentina aclara que Mercado Pago/Ualá/Prex ya
// funcionan acá mismo (CVU es interoperable con CBU/alias vía COELSA), así que
// no hace falta una opción aparte para eso.
function _actualizarCamposTransferencia() {
  if(!aliado) return;
  const pais = (aliado.pais || 'AR').toUpperCase();
  const fmt = PM_FORMATOS_PAIS[pais] || PM_FORMATOS_PAIS._default;

  const nameEl = document.getElementById('pm-name-transferencia');
  const tagEl  = document.getElementById('pm-tag-transferencia');
  const labelEl = document.getElementById('pm-label-cobro_numero_cuenta');
  const inputEl = document.getElementById('pm-input-cobro_numero_cuenta');
  const hintEl = document.getElementById('pm-hint-transferencia');

  if(pais === 'AR') {
    if(nameEl) nameEl.textContent = 'Transferencia / Mercado Pago';
    if(tagEl) tagEl.textContent = 'CBU, CVU o alias';
    if(hintEl) hintEl.innerHTML = '<i class="fa-solid fa-circle-info" style="color:#60a5fa;"></i> Cubre banco, Mercado Pago, Ualá y Prex: todos usan CBU/CVU/alias, interoperables entre sí (sistema COELSA). No hace falta una opción aparte para Mercado Pago.';
  } else {
    if(nameEl) nameEl.textContent = 'Transferencia bancaria';
    if(tagEl) tagEl.textContent = fmt.label;
    if(hintEl) hintEl.innerHTML = `<i class="fa-solid fa-circle-info" style="color:#60a5fa;"></i> Te depositamos a esta cuenta local (${fmt.label}).`;
  }
  if(labelEl) labelEl.textContent = fmt.label;
  if(inputEl) inputEl.placeholder = fmt.ejemplo;
}

// v2.6 — selector de identificador de Wise (email / teléfono / wisetag)
function seleccionarTipoWise(tipo) {
  _wiseTipoActual = tipo;
  ['email','telefono','wisetag'].forEach(t => {
    const btn = document.getElementById('wise-tipo-' + t);
    if(btn) btn.classList.toggle('selected', t === tipo);
  });
  const input = document.getElementById('pm-input-wise');
  const hint = document.getElementById('pm-hint-wise');
  if(!input) return;
  if(tipo === 'email') {
    input.type = 'text';
    input.placeholder = 'Ej: tu@email.com';
    if(hint) hint.innerHTML = '<i class="fa-solid fa-circle-info" style="color:#9fe870;"></i> Recibís en tu balance Wise y convertís a tu moneda local con comisión mínima.';
  } else if(tipo === 'telefono') {
    input.type = 'text';
    input.placeholder = 'Ej: +5491122334455';
    if(hint) hint.innerHTML = '<i class="fa-solid fa-circle-info" style="color:#9fe870;"></i> Formato internacional, con el código de país incluido (+54, +52, +57...).';
  } else if(tipo === 'wisetag') {
    input.type = 'text';
    input.placeholder = 'Ej: @tuusuario';
    if(hint) hint.innerHTML = '<i class="fa-solid fa-circle-info" style="color:#9fe870;"></i> Tu Wisetag empieza con @ y no lleva espacios.';
  }
}

function seleccionarMetodoPago(metodo) {
  _metodoPagoActual = metodo;
  ['usdt_trc20','wise','transferencia','payoneer','airtm'].forEach(m => {
    const btn = document.getElementById('pm-btn-' + m);
    if(btn) btn.classList.toggle('selected', m === metodo);
    // Mostrar/ocultar campos
    const fields = document.getElementById('pm-fields-' + m);
    if(fields) fields.style.display = m === metodo ? 'block' : 'none';
  });
  if(metodo === 'transferencia') _actualizarCamposTransferencia();
  if(metodo === 'wise' && !_wiseTipoActual) seleccionarTipoWise('email');
}

// Enmascara el dato de cobro para mostrarlo sin exponerlo entero en pantalla
function _maskPagoInfo(s) {
  s = (s || '').trim();
  if (s.length <= 14) return s;
  return s.slice(0, 7) + '··· ' + s.slice(-5);
}

// Pinta el chip de estado y el resumen "Cobrás por …" arriba del selector
function _pintarEstadoMetodoPago() {
  const chip = document.getElementById('pm-chip-estado');
  const resumen = document.getElementById('pm-resumen');
  if (!chip || !resumen || !aliado) return;

  const metodo = aliado.payment_method;
  const info = aliado.payment_info;
  const tieneAlgo = (metodo === 'transferencia' && aliado.cobro_numero_cuenta) || (metodo && metodo !== 'transferencia' && info) || aliado.cbu_alias;

  if (tieneAlgo) {
    chip.style.background = 'rgba(74,222,128,0.12)';
    chip.style.borderColor = 'rgba(74,222,128,0.4)';
    chip.style.color = 'var(--green)';
    chip.innerHTML = '<i class="fa-solid fa-circle-check"></i> Configurado';

    const meta = _PM_META[metodo] || { label: 'Transferencia / legacy', icono: '<i class="fa-solid fa-building-columns"></i>', color: 'var(--green)', bg: 'rgba(74,222,128,0.14)' };
    const logo = document.getElementById('pm-resumen-logo');
    if (logo) { logo.innerHTML = meta.icono; logo.style.background = meta.bg; logo.style.color = meta.color; }
    const met = document.getElementById('pm-resumen-metodo');
    if (met) met.textContent = meta.label;
    const det = document.getElementById('pm-resumen-detalle');
    const detalleRaw = metodo === 'transferencia'
      ? (aliado.cobro_numero_cuenta || aliado.cbu_alias || '')
      : (info || aliado.cbu_alias || '');
    if (det) det.textContent = _maskPagoInfo(detalleRaw);
    resumen.style.display = 'flex';
  } else {
    chip.style.background = 'rgba(250,204,21,0.12)';
    chip.style.borderColor = 'rgba(250,204,21,0.35)';
    chip.style.color = 'var(--amber)';
    chip.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Pendiente';
    resumen.style.display = 'none';
  }
}

function _getInputMetodoPago() {
  if(!_metodoPagoActual) return null;
  if(_metodoPagoActual === 'transferencia') {
    const banco = (document.getElementById('pm-input-cobro_banco')?.value || '').trim();
    const titular = (document.getElementById('pm-input-cobro_titular')?.value || '').trim();
    const numero = (document.getElementById('pm-input-cobro_numero_cuenta')?.value || '').trim();
    if(!banco || !titular || !numero) return null;
    return { banco, titular, numero }; // objeto, no string — se procesa aparte en guardarMetodoPago
  }
  const el = document.getElementById('pm-input-' + _metodoPagoActual);
  return el ? (el.value || '').trim() : null;
}

// v2.1: guardar método de cobro internacional
async function guardarMetodoPago() {
  if(!aliado) return;
  const btn   = document.getElementById('btn-guardar-cbu');
  const estado = document.getElementById('perfil-cbu-estado');

  if(!_metodoPagoActual) {
    estado.innerHTML = `<span style="color:var(--amber);"><i class="fa-solid fa-triangle-exclamation"></i> Seleccioná un método de cobro primero.</span>`;
    return;
  }
  if(_metodoPagoActual === 'wise' && !_wiseTipoActual) {
    estado.innerHTML = `<span style="color:var(--amber);"><i class="fa-solid fa-triangle-exclamation"></i> Elegí cómo te identificamos en Wise (email, teléfono o wisetag).</span>`;
    return;
  }
  const detail = _getInputMetodoPago();
  if(!detail) {
    estado.innerHTML = `<span style="color:var(--amber);"><i class="fa-solid fa-triangle-exclamation"></i> Completá los datos del método elegido.</span>`;
    return;
  }

  // Payload: cada método manda el campo que realmente lo identifica (ver
  // mejoras-metodos-cobro.md §3). El cbu_alias legible para el admin lo arma
  // el backend a partir de estos datos — el front ya no lo construye.
  const body = { payment_method: _metodoPagoActual };
  if(_metodoPagoActual === 'transferencia') {
    body.cobro_banco = detail.banco;
    body.cobro_titular = detail.titular;
    body.cobro_numero_cuenta = detail.numero;
    body.cobro_tipo_cuenta = document.getElementById('pm-input-cobro_tipo_cuenta')?.value || 'otra';
  } else {
    body.payment_info = detail;
    if(_metodoPagoActual === 'wise') body.payment_info_tipo = _wiseTipoActual;
  }

  btn.innerHTML = '<span class="spinner"></span> Guardando...';
  btn.disabled  = true;

  try {
    const res = await apiFetch(`${API}/aliado/perfil`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if(!res.ok) throw new Error(data.detail || 'Error');

    aliado.cbu_alias = data.cbu_alias;
    aliado.payment_method = data.payment_method || _metodoPagoActual;
    aliado.payment_info   = data.payment_info;
    aliado.payment_info_tipo = data.payment_info_tipo;
    aliado.cobro_banco = data.cobro_banco;
    aliado.cobro_titular = data.cobro_titular;
    aliado.cobro_numero_cuenta = data.cobro_numero_cuenta;
    aliado.cobro_tipo_cuenta = data.cobro_tipo_cuenta;

    estado.innerHTML = `<span style="color:var(--green);"><i class="fa-solid fa-circle-check"></i> Método de cobro guardado. Ya podés cobrar comisiones.</span>`;
    mostrarToast('Método de cobro guardado correctamente.', 'green');
    _pintarEstadoMetodoPago();
  } catch(e) {
    estado.innerHTML = `<span style="color:var(--red);"><i class="fa-solid fa-circle-xmark"></i> ${e.message}</span>`;
    mostrarToast('No se pudo guardar. Intentá de nuevo.', 'red');
  } finally {
    btn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> Guardar método de cobro';
    btn.disabled  = false;
  }
}

// LEGACY alias para compatibilidad
async function guardarCBU() { return guardarMetodoPago(); }

// ─── MI CUENTA: poblar datos ──────────────────────────────────────────────────
function poblarMiCuenta() {
  if(!aliado) return;
  const set = (id, val) => { const el = document.getElementById(id); if(el) el.textContent = val || '—'; };
  set('mc-nombre',  aliado.nombre);
  set('mc-email',   aliado.email);
  set('mc-ciudad',  aliado.ciudad);
  set('mc-perfil',  aliado.perfil);
  set('mc-nivel',   aliado.nivel);
  set('mc-ref-code', aliado.ref_code);
  const linkEl = document.getElementById('mc-link-aliado');
  if(linkEl) linkEl.textContent = `https://avanzadigital.digital/alianzas?ref=${aliado.ref_code}`;
  // Pre-poblar método de cobro si ya tiene configurado
  const cbuInput = document.getElementById('perfil-cbu');
  const cbuEstado = document.getElementById('perfil-cbu-estado');
  if(cbuInput) cbuInput.value = aliado.cbu_alias || '';

  // Restaurar método de pago guardado
  const metodo = aliado.payment_method;
  const info   = aliado.payment_info;
  if(metodo === 'transferencia') {
    seleccionarMetodoPago('transferencia');
    const setVal = (id, val) => { const el = document.getElementById(id); if(el) el.value = val || ''; };
    setVal('pm-input-cobro_banco', aliado.cobro_banco);
    setVal('pm-input-cobro_titular', aliado.cobro_titular);
    setVal('pm-input-cobro_numero_cuenta', aliado.cobro_numero_cuenta);
    setVal('pm-input-cobro_tipo_cuenta', aliado.cobro_tipo_cuenta || 'ahorro');
  } else if(metodo === 'wise') {
    seleccionarMetodoPago('wise');
    seleccionarTipoWise(aliado.payment_info_tipo || 'email');
    const inputEl = document.getElementById('pm-input-wise');
    if(inputEl) inputEl.value = info || '';
  } else if(metodo) {
    seleccionarMetodoPago(metodo);
    const inputEl = document.getElementById('pm-input-' + metodo);
    if(inputEl) inputEl.value = info || '';
  } else if(aliado.cbu_alias) {
    // Legacy: tenía cbu_alias pero sin payment_method → mostrar como transferencia
    seleccionarMetodoPago('transferencia');
    setTimeout(() => { const numEl = document.getElementById('pm-input-cobro_numero_cuenta'); if(numEl) numEl.value = aliado.cbu_alias; }, 0);
  } else {
    // Sin nada cargado todavía: igual reflejar el país para el label correcto
    _actualizarCamposTransferencia();
  }

  if(cbuEstado) {
    const tieneMetodo = (metodo === 'transferencia' && aliado.cobro_numero_cuenta)
      || (metodo && metodo !== 'transferencia' && info);
    if(tieneMetodo) {
      cbuEstado.innerHTML = `<span style="color:var(--green);"><i class="fa-solid fa-circle-check"></i> Método de cobro configurado.</span>`;
    } else if(aliado.cbu_alias) {
      cbuEstado.innerHTML = `<span style="color:var(--green);"><i class="fa-solid fa-circle-check"></i> Método de cobro cargado.</span>`;
    } else {
      cbuEstado.innerHTML = `<span style="color:var(--amber);"><i class="fa-solid fa-triangle-exclamation"></i> Configurá tu método de cobro para poder cobrar.</span>`;
    }
  }
  _pintarEstadoMetodoPago();
}

function copiarLinkAliado() {
  const link = aliado?.ref_code
    ? `https://avanzadigital.digital/alianzas?ref=${aliado.ref_code}`
    : (document.getElementById('mc-link-aliado')?.textContent || '');
  if(!link || link==='—') return;
  navigator.clipboard.writeText(link).then(() => mostrarToast('Link de referido copiado.', 'green'));
}

// ─── CAMBIAR CONTRASEÑA (self-service) ───────────────────────────────────────
async function cambiarPasswordAliado() {
  const actual   = (document.getElementById('mc-pass-actual')?.value   || '').trim();
  const nueva    = (document.getElementById('mc-pass-nueva')?.value    || '').trim();
  const confirm  = (document.getElementById('mc-pass-confirm')?.value  || '').trim();
  const estado   = document.getElementById('mc-pass-estado');
  const btn      = document.getElementById('btn-cambiar-pass');

  if(!actual || !nueva || !confirm) {
    estado.innerHTML = `<span style="color:var(--amber);">Completá los tres campos.</span>`; return;
  }
  if(nueva.length < 6) {
    estado.innerHTML = `<span style="color:var(--amber);">La nueva contraseña debe tener al menos 6 caracteres.</span>`; return;
  }
  if(nueva !== confirm) {
    estado.innerHTML = `<span style="color:var(--amber);">Las contraseñas no coinciden.</span>`; return;
  }

  btn.innerHTML = '<span class="spinner"></span> Guardando...';
  btn.disabled  = true;

  try {
    const res = await apiFetch(`${API}/aliado/cambiar-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password_actual: actual, nueva_password: nueva }),
    });
    const data = await res.json();
    if(!res.ok) throw new Error(data.detail || 'Error');

    estado.innerHTML = `<span style="color:var(--green);"><i class="fa-solid fa-circle-check"></i> Contraseña actualizada. Volvé a iniciar sesión.</span>`;
    mostrarToast('Contraseña cambiada. Iniciá sesión de nuevo.', 'green');
    // Limpiar campos
    ['mc-pass-actual','mc-pass-nueva','mc-pass-confirm'].forEach(id => {
      const el = document.getElementById(id); if(el) el.value = '';
    });
    // Logout suave tras 2s
    setTimeout(() => cerrarSesion(), 2500);
  } catch(e) {
    estado.innerHTML = `<span style="color:var(--red);"><i class="fa-solid fa-circle-xmark"></i> ${e.message}</span>`;
  } finally {
    btn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> Guardar nueva contraseña';
    btn.disabled  = false;
  }
}

// ─── RECUPERAR CONTRASEÑA: abrir modal ───────────────────────────────────────
function abrirModalRecuperarPass() {
  document.getElementById('rec-pass-email').value = '';
  document.getElementById('rec-pass-estado').innerHTML = '';
  document.getElementById('modal-recuperar-pass').classList.add('open');
  setTimeout(() => document.getElementById('rec-pass-email').focus(), 150);
}

async function enviarRecuperacionPass() {
  const email  = (document.getElementById('rec-pass-email').value || '').trim();
  const estado = document.getElementById('rec-pass-estado');
  const btn    = document.getElementById('btn-enviar-rec');

  if(!email) { estado.innerHTML = `<span style="color:var(--amber);">Ingresá tu email.</span>`; return; }

  btn.innerHTML = '<span class="spinner"></span> Enviando...';
  btn.disabled  = true;

  try {
    const res = await fetch(`${API}/auth/recuperar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await res.json();
    estado.innerHTML = `<span style="color:var(--green);"><i class="fa-solid fa-circle-check"></i> ${data.mensaje || 'Revisá tu casilla de correo.'}</span>`;
    btn.innerHTML = '<i class="fa-solid fa-check"></i> Enviado';
  } catch(e) {
    estado.innerHTML = `<span style="color:var(--red);">Error al enviar. Intentá de nuevo.</span>`;
    btn.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Enviar link';
    btn.disabled = false;
  }
}

async function registrarReferido() {
  const nombre = document.getElementById('ref-nombre').value.trim();
  const plan = document.getElementById('ref-plan').value;
  const notas = document.getElementById('ref-notas').value;
  const btn = document.getElementById('btn-ref');
  const success = document.getElementById('ref-success');
  if (!nombre||!plan) { alert('Completá el nombre del cliente y el plan elegido.'); return; }
  btn.innerHTML='<span class="spinner"></span> Registrando...'; btn.disabled=true; success.style.display='none';
  try {
    const res = await apiFetch(`${API}/referidos/registrar?ref_code=${aliado.ref_code}&nombre_cliente=${encodeURIComponent(nombre)}&plan_elegido=${encodeURIComponent(plan)}&notas=${encodeURIComponent(notas)}`,{method:'POST'});
    const data = await res.json();
    if (res.ok) {
      const comision = Math.round(PLANES[plan]*pct(aliado.nivel_calculado||'BASIC'));
      success.innerHTML=`<i class="fa-solid fa-circle-check"></i> <strong>Prospecto registrado.</strong> Avanza Digital fue notificado.<br>Comisión estimada si se cierra: <strong>USD ${comision.toLocaleString()}</strong>.`;
      success.style.display='block';
      document.getElementById('ref-nombre').value='';
      document.getElementById('ref-plan').value='';
      document.getElementById('ref-notas').value='';
      setTimeout(cargarTodo,800);
    } else { alert(data.detail||'Error al registrar'); }
  } catch { alert('Error de conexión. Verificá que el servidor esté activo.'); }
  btn.innerHTML='<i class="fa-solid fa-paper-plane"></i> Registrar prospecto ahora'; btn.disabled=false;
}

// ── PROSPECTOS ──────────────────────────────────────────────────────────────

let todosProspectos = [];
let filtroProspActivo = 'todos';

async function cargarProspectos() {
  try {
    const res = await apiFetch(`${API}/prospectos/aliado/${aliado.codigo}`);
    if(!res.ok) return;
    todosProspectos = await res.json();
    renderAlertas();
    renderProspectos();
  } catch {}
}

function renderAlertas() {
  const sinContactar = todosProspectos.filter(p=>p.estado==='sin_contactar').length;
  const sinProximoPaso = todosProspectos.filter(p=>(p.estado==='contactado'||p.estado==='respondio')&&!p.proxima_accion_en).length;
  const conPropuesta = todosProspectos.filter(p=>p.estado==='propuesta_enviada').length;
  let html = '';
  if(sinContactar>0)
    html += `<div style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);border-radius:10px;padding:12px 16px;margin-bottom:12px;font-size:.88rem;font-weight:600;color:#ef4444;">
      <i class="fa-solid fa-circle-exclamation"></i> &nbsp;Tenés <strong>${sinContactar}</strong> prospecto${sinContactar>1?'s':''} sin contactar
    </div>`;
  if(sinProximoPaso>0)
    html += `<div style="background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.25);border-radius:10px;padding:12px 16px;margin-bottom:12px;font-size:.88rem;font-weight:600;color:#f59e0b;">
      <i class="fa-solid fa-clock"></i> &nbsp;Tenés <strong>${sinProximoPaso}</strong> sin próximo paso agendado — agendales una acción para no perderlos
    </div>`;
  if(conPropuesta>0)
    html += `<div style="background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.25);border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:.88rem;font-weight:600;color:var(--primary);">
      <i class="fa-solid fa-file-invoice"></i> &nbsp;Tenés <strong>${conPropuesta}</strong> propuesta${conPropuesta>1?'s':''} enviada${conPropuesta>1?'s':''} esperando respuesta
    </div>`;
  document.getElementById('prosp-alertas').innerHTML = html;
}

function filtrarProspectos(filtro, btn) {
  filtroProspActivo = filtro;
  document.querySelectorAll('[id^="pfiltro-"]').forEach(b=>{
    b.style.background='transparent'; b.style.color='var(--text-muted)'; b.style.border='1px solid var(--border)';
  });
  btn.style.background='var(--primary)'; btn.style.color='white'; btn.style.border='none';
  renderProspectos();
}

function renderProspectos() {
  const lista = filtroProspActivo==='todos'
    ? todosProspectos
    : todosProspectos.filter(p=>p.estado===filtroProspActivo);

  const contenedor = document.getElementById('lista-prospectos');
  if(!lista.length) {
    contenedor.innerHTML = `<div style="padding:32px;text-align:center;color:var(--text-muted);font-size:.88rem;">
      ${filtroProspActivo==='todos'?'Todavía no cargaste ningún prospecto o lead.':'No hay prospectos en este estado.'}
    </div>`;
    return;
  }

  const estadoLabel = { sin_contactar:'Sin contactar', contactado:'Contactado', propuesta_enviada:'Propuesta enviada', respondio:'Respondió', ganado:'Ganado', perdido:'Perdido' };
  const estadoColor = { sin_contactar:'var(--red)', contactado:'var(--amber)', propuesta_enviada:'var(--primary)', respondio:'var(--green)', ganado:'var(--green)', perdido:'#71717a' };

  contenedor.innerHTML = lista.map(p=>`
    <div id="prosp-card-${p.id}" style="border-bottom:1px solid var(--border);padding:16px 8px;display:grid;gap:10px; ${p.estado==='perdido'?'opacity:0.62;':''}">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;">
        <div style="flex:1;min-width:0;">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span style="font-weight:700;font-size:.95rem; ${p.estado==='perdido'?'text-decoration:line-through;':''}">${p.nombre}</span>
            ${p.interesante?'<span title="Buen prospecto" style="font-size:1rem;">🔥</span>':''}
            <span style="font-size:.72rem;font-weight:700;padding:2px 9px;border-radius:50px;background:rgba(0,0,0,0.3);border:1px solid ${estadoColor[p.estado]||'var(--border)'};color:${estadoColor[p.estado]||'var(--text-muted)'};">${estadoLabel[p.estado]||p.estado}</span>
          </div>
          ${p.contacto?`<div style="font-size:.8rem;color:var(--text-muted);margin-top:3px;"><i class="fa-solid fa-phone" style="font-size:.7rem;"></i> ${p.contacto}</div>`:''}
          ${p.plan_interes?`<div style="font-size:.8rem;color:var(--text-muted);"><i class="fa-solid fa-tag" style="font-size:.7rem;"></i> ${p.plan_interes}</div>`:''}
          ${p.fecha_contacto?`<div style="font-size:.75rem;color:var(--text-dim);">Contactado: ${p.fecha_contacto}${p.fecha_respuesta?' · Respondió: '+p.fecha_respuesta:''}</div>`:''}
          <div style="margin-top:10px; font-size:0.8rem; font-weight:600; color:var(--${p.action_type}); background:rgba(255,255,255,0.03); padding:8px 12px; border-radius:6px; display:inline-block; border:1px solid var(--border); border-left: 3px solid var(--${p.action_type});">
                ${p.next_action}
              </div>
          </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
          ${p.estado==='sin_contactar'?`<button onclick="accionProspecto(${p.id},'contactar')" style="background:var(--primary);color:white;border:none;border-radius:8px;padding:7px 13px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-paper-plane"></i> Contacté</button>`:''}
          ${p.estado==='contactado'?`<button onclick="accionProspecto(${p.id},'propuesta_enviada')" style="background:rgba(59,130,246,0.15);color:var(--primary);border:1px solid rgba(59,130,246,0.35);border-radius:8px;padding:7px 13px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-file-invoice"></i> Envié la propuesta</button>`:''}
          ${p.estado==='contactado'||p.estado==='propuesta_enviada'?`<button onclick="accionProspecto(${p.id},'respondio')" style="background:rgba(74,222,128,0.15);color:var(--green);border:1px solid var(--green-border);border-radius:8px;padding:7px 13px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-reply"></i> Respondió</button>`:''}
          ${p.estado!=='sin_contactar'&&p.estado!=='perdido'?`<button onclick="accionProspecto(${p.id},'seguimiento')" style="background:rgba(245,158,11,0.12);color:var(--amber);border:1px solid rgba(245,158,11,0.3);border-radius:8px;padding:7px 13px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-rotate-right"></i> Seguimiento</button>`:''}
          ${p.estado!=='perdido'?`<button onclick="toggleInteresante(${p.id},${p.interesante})" title="${p.interesante?'Quitar flag':'Marcar como interesante'}" style="background:${p.interesante?'rgba(245,158,11,0.15)':'rgba(255,255,255,0.04)'};color:${p.interesante?'var(--amber)':'var(--text-dim)'};border:1px solid ${p.interesante?'rgba(245,158,11,0.3)':'var(--border)'};border-radius:8px;padding:7px 10px;font-size:.82rem;cursor:pointer;">🔥</button>`:''}
          ${p.estado!=='perdido'?`<button onclick="abrirPerfilado(${p.id},'${(p.nombre||'').replace(/'/g,"\\'")}')" title="Perfilar con IA" style="background:rgba(192,132,252,0.12);color:#c084fc;border:1px solid rgba(192,132,252,0.3);border-radius:8px;padding:7px 12px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-brain"></i> Perfilar IA</button>`:''}
          ${p.estado!=='perdido'?`<button onclick="abrirAsistenteIA('followup', ${p.id}, '${(p.nombre||'').replace(/'/g,"\\'")}')" title="Generar mensaje de follow-up con IA" style="background:rgba(192,132,252,0.12);color:#c084fc;border:1px solid rgba(192,132,252,0.3);border-radius:8px;padding:7px 12px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-paper-plane"></i> Follow-up IA</button>`:''}
          ${p.estado!=='perdido'?`<button onclick="abrirAsistenteIA('objecion', ${p.id}, '${(p.nombre||'').replace(/'/g,"\\'")}')" title="Te dijo una objeción y no sabés cómo responder" style="background:rgba(245,158,11,0.12);color:var(--amber);border:1px solid rgba(245,158,11,0.3);border-radius:8px;padding:7px 12px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-shield-halved"></i> Objeción IA</button>`:''}
          ${p.estado!=='perdido'?`<button onclick="togglePiloto(${p.id},${p.piloto_automatico?true:false})" title="${p.piloto_automatico?'Detener piloto automático':'Activar piloto automático (emails de seguimiento)'}" style="background:${p.piloto_automatico?'rgba(74,222,128,0.15)':'rgba(255,255,255,0.04)'};color:${p.piloto_automatico?'var(--green)':'var(--text-dim)'};border:1px solid ${p.piloto_automatico?'var(--green-border)':'var(--border)'};border-radius:8px;padding:7px 10px;font-size:.82rem;cursor:pointer;">🤖</button>`:''}
          ${p.estado!=='perdido'?`<button onclick="abrirMarcarPerdido(${p.id},'${(p.nombre||'').replace(/'/g,"\\'")}')" title="Marcar como perdido y obtener análisis IA" style="background:rgba(113,113,122,0.12);color:#a1a1aa;border:1px solid rgba(113,113,122,0.3);border-radius:8px;padding:7px 12px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-circle-xmark"></i> Perdido</button>`:`<button onclick="reanalizarPerdida(${p.id},'${(p.nombre||'').replace(/'/g,"\\'")}')" title="Re-analizar este prospecto perdido con IA" style="background:rgba(192,132,252,0.12);color:#c084fc;border:1px solid rgba(192,132,252,0.3);border-radius:8px;padding:7px 12px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-magnifying-glass"></i> Re-analizar IA</button>`}
        </div>
      </div>
      ${p.score_ia && p.score_ia > 0 ? `
      <div style="background:rgba(192,132,252,0.06); border:1px solid rgba(192,132,252,0.2); border-radius:8px; padding:10px 12px; font-size:.82rem;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
          <strong style="color:#c084fc;"><i class="fa-solid fa-brain"></i> Score IA: ${p.score_ia}/100</strong>
          ${p.plan_recomendado ? `<span style="color:var(--text-muted); font-size:.75rem;">Plan sugerido: <strong style="color:var(--text);">${p.plan_recomendado}</strong></span>` : ''}
        </div>
        ${p.pitch_sugerido ? `<details style="margin-top:6px;"><summary style="cursor:pointer; color:var(--text-muted); font-size:.78rem;">Ver pitch sugerido</summary><pre style="white-space:pre-wrap; margin-top:8px; padding:10px; background:rgba(0,0,0,0.3); border-radius:6px; font-family:inherit; font-size:.8rem; color:var(--text);">${p.pitch_sugerido}</pre></details>` : ''}
      </div>
      ` : ''}
      <div id="perdida-analisis-${p.id}" style="display:none;"></div>
      <div style="display:flex;align-items:center;gap:8px;">
        <input type="text" id="nota-input-${p.id}" value="${(p.nota||'').replace(/"/g,'&quot;')}" placeholder="Agregar nota... (ej: pidió demo, evalúa con su socio)"
          style="flex:1;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:8px;padding:7px 12px;color:var(--text);font-size:.82rem;font-family:'Inter',sans-serif;outline:none;">
        <button onclick="guardarNota(${p.id})" style="background:rgba(255,255,255,0.06);color:var(--text-muted);border:1px solid var(--border);border-radius:8px;padding:7px 12px;font-size:.78rem;font-weight:600;cursor:pointer;white-space:nowrap;">Guardar</button>
        <span id="nota-ok-${p.id}" style="font-size:.75rem;color:var(--green);display:none;"><i class="fa-solid fa-check"></i></span>
      </div>
    </div>
  `).join('');
}

async function crearProspecto() {
  const nombre = document.getElementById('np-nombre').value.trim();
  if(!nombre) { alert('El nombre es obligatorio.'); return; }
  const contacto    = document.getElementById('np-contacto').value.trim();
  const plan        = document.getElementById('np-plan').value;
  const rubro       = document.getElementById('np-rubro').value;
  const nota        = document.getElementById('np-nota').value.trim();
  const url = `${API}/prospectos/crear?codigo_aliado=${aliado.codigo}&nombre=${encodeURIComponent(nombre)}&contacto=${encodeURIComponent(contacto)}&plan_interes=${encodeURIComponent(plan)}&rubro=${encodeURIComponent(rubro)}&nota=${encodeURIComponent(nota)}`;
  try {
    const res = await apiFetch(url, {method:'POST'});
    if(res.ok) {
      document.getElementById('np-nombre').value='';
      document.getElementById('np-contacto').value='';
      document.getElementById('np-plan').value='';
      document.getElementById('np-rubro').value='';
      document.getElementById('np-nota').value='';
      const msg = document.getElementById('np-msg');
      msg.style.display='inline'; setTimeout(()=>msg.style.display='none',2500);
      await cargarProspectos();
    } else { const d=await res.json(); alert(d.detail||'Error al cargar.'); }
  } catch { alert('Error de conexión.'); }
}

// ── PIPELINE UNIFICADO: funciones wrapper ────────────────────────────────────

let todosProspectosPipeline = [];
let filtroProspPipelineActivo = 'todos';

async function cargarProspectosPipeline() {
  try {
    const res = await apiFetch(`${API}/prospectos/aliado/${aliado.codigo}`);
    if(!res.ok) return;
    todosProspectosPipeline = await res.json();
    plRenderAlertas();
    plRenderProspectos();
  } catch {}
}

function plRenderAlertas() {
  const sinContactar = todosProspectosPipeline.filter(p=>p.estado==='sin_contactar').length;
  const sinProximoPaso = todosProspectosPipeline.filter(p=>(p.estado==='contactado'||p.estado==='respondio')&&!p.proxima_accion_en).length;
  const conPropuesta = todosProspectosPipeline.filter(p=>p.estado==='propuesta_enviada').length;
  let html = '';
  if(sinContactar>0)
    html += `<div style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);border-radius:10px;padding:12px 16px;margin-bottom:12px;font-size:.88rem;font-weight:600;color:#ef4444;">
      <i class="fa-solid fa-circle-exclamation"></i> &nbsp;Tenés <strong>${sinContactar}</strong> prospecto${sinContactar>1?'s':''} sin contactar
    </div>`;
  if(sinProximoPaso>0)
    html += `<div style="background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.25);border-radius:10px;padding:12px 16px;margin-bottom:12px;font-size:.88rem;font-weight:600;color:#f59e0b;">
      <i class="fa-solid fa-clock"></i> &nbsp;Tenés <strong>${sinProximoPaso}</strong> sin próximo paso agendado — usá "Hice el seguimiento" y agendá la próxima acción
    </div>`;
  if(conPropuesta>0)
    html += `<div style="background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.25);border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:.88rem;font-weight:600;color:var(--primary);">
      <i class="fa-solid fa-file-invoice"></i> &nbsp;Tenés <strong>${conPropuesta}</strong> propuesta${conPropuesta>1?'s':''} enviada${conPropuesta>1?'s':''} esperando respuesta
    </div>`;
  const el = document.getElementById('pl-prosp-alertas');
  if(el) el.innerHTML = html;
}

function plFiltrarProspectos(filtro, btn) {
  filtroProspPipelineActivo = filtro;
  document.querySelectorAll('[id^="pl-pfiltro-"]').forEach(b=>{
    b.style.background='transparent'; b.style.color='var(--text-muted)'; b.style.border='1px solid var(--border)';
  });
  btn.style.background='var(--primary)'; btn.style.color='white'; btn.style.border='none';
  plRenderProspectos();
}

function plRenderProspectos() {
  if (typeof plRenderMetricas === 'function') plRenderMetricas();
  if (window._plVista === 'kanban') { plRenderKanban(); return; }
  const lista = filtroProspPipelineActivo==='todos'
    ? todosProspectosPipeline
    : todosProspectosPipeline.filter(p=>p.estado===filtroProspPipelineActivo);
  const contenedor = document.getElementById('pl-lista-prospectos');
  if(!contenedor) return;
  if(!lista.length) {
    contenedor.innerHTML = `<div style="padding:32px;text-align:center;color:var(--text-muted);font-size:.88rem;">
      ${filtroProspPipelineActivo==='todos'?'Todavía no cargaste ningún prospecto o lead.':'No hay prospectos en este estado.'}
    </div>`;
    return;
  }
  const estadoLabel = { sin_contactar:'Sin contactar', contactado:'Contactado', propuesta_enviada:'Propuesta enviada', respondio:'Respondió', ganado:'Ganado', perdido:'Perdido' };
  const estadoColor = { sin_contactar:'var(--red)', contactado:'var(--amber)', propuesta_enviada:'var(--primary)', respondio:'var(--green)', ganado:'var(--green)', perdido:'#71717a' };
  contenedor.innerHTML = lista.map(p=>`
    <div style="border-bottom:1px solid var(--border);padding:14px 8px;display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;${p.estado==='perdido'?'opacity:0.62;':''}">
      <div style="flex:1;min-width:0;">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <span style="font-weight:700;font-size:.92rem;${p.estado==='perdido'?'text-decoration:line-through;':''}">${p.nombre}</span>
          ${p.interesante?'<span title="Buen prospecto" style="font-size:.95rem;">🔥</span>':''}
          <span style="font-size:.7rem;font-weight:700;padding:2px 9px;border-radius:50px;background:rgba(0,0,0,0.3);border:1px solid ${estadoColor[p.estado]||'var(--border)'};color:${estadoColor[p.estado]||'var(--text-muted);'}">${estadoLabel[p.estado]||p.estado}</span>
        </div>
        ${p.plan_interes?`<div style="font-size:.78rem;color:var(--text-muted);margin-top:3px;"><i class="fa-solid fa-tag" style="font-size:.68rem;"></i> ${p.plan_interes}</div>`:''}
        ${p.nota?`<div style="font-size:.78rem;color:var(--text-muted);margin-top:3px;">${p.nota}</div>`:''}
        ${p.proxima_accion_en?`<div style="font-size:.74rem;color:var(--amber);margin-top:4px;"><i class="fa-regular fa-clock"></i> Próxima acción: ${_fmtFechaCRM(p.proxima_accion_en)}</div>`:''}
        ${p.tareas_pendientes?`<div style="font-size:.72rem;color:var(--text-dim);margin-top:2px;"><i class="fa-solid fa-list-check"></i> ${p.tareas_pendientes} tarea(s) pendiente(s)</div>`:''}
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
        <button onclick="abrirFichaLead(${p.id})" style="background:rgba(59,130,246,0.12);color:var(--primary);border:1px solid rgba(59,130,246,0.3);border-radius:8px;padding:7px 12px;font-size:.76rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-address-card"></i> Ficha</button>
        ${(p.estado!=='perdido'&&p.estado!=='ganado')?`<button onclick="plHiceSeguimiento(${p.id})" title="Registrá lo que hiciste, cerrá la tarea pendiente y agendá el próximo paso — todo en un paso" style="background:rgba(245,158,11,0.14);color:var(--amber);border:1px solid rgba(245,158,11,0.4);border-radius:8px;padding:7px 12px;font-size:.76rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-circle-check"></i> Hice el seguimiento</button>`:''}
        <button onclick="plRegistrarDesdeProspecto(${p.id})" title="Cargar este prospecto en el registro de atribución (paso crítico antes del pago)" style="background:rgba(74,222,128,0.12);color:var(--green);border:1px solid rgba(74,222,128,0.35);border-radius:8px;padding:7px 12px;font-size:.76rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-user-check"></i> Registrar para cobrar</button>
        <button onclick="cobrarDesdeProspecto(${p.id}, '${encodeURIComponent(p.nombre||'')}')" title="Generar link de pago (si vino de un handoff de equipo, reparte la comision con el setter)" style="background:rgba(124,58,237,0.12);color:#c084fc;border:1px solid rgba(124,58,237,0.35);border-radius:8px;padding:7px 12px;font-size:.76rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-link"></i> Generar link de pago</button>
        ${p.estado==='sin_contactar'?`<button onclick="accionProspecto(${p.id},'contactar');cargarProspectosPipeline()" style="background:var(--primary);color:white;border:none;border-radius:8px;padding:7px 13px;font-size:.76rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-paper-plane"></i> Contacté</button>`:''}
        ${p.estado==='contactado'?`<button onclick="accionProspecto(${p.id},'propuesta_enviada');cargarProspectosPipeline()" style="background:rgba(59,130,246,0.15);color:var(--primary);border:1px solid rgba(59,130,246,0.35);border-radius:8px;padding:7px 13px;font-size:.76rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-solid fa-file-invoice"></i> Envié propuesta</button>`:''}
        ${p.estado!=='perdido'?`<button onclick="abrirMarcarPerdido(${p.id},'${(p.nombre||'').replace(/'/g,"\\'")}');setTimeout(cargarProspectosPipeline,500)" title="Marcar como perdido" style="background:rgba(113,113,122,0.12);color:#a1a1aa;border:1px solid rgba(113,113,122,0.3);border-radius:8px;padding:7px 12px;font-size:.76rem;cursor:pointer;"><i class="fa-solid fa-circle-xmark"></i></button>`:''}
        <button onclick="eliminarLeadDirecto(${p.id})" title="Eliminar lead (definitivo)" style="background:transparent;color:var(--text-dim);border:1px solid var(--border);border-radius:8px;padding:7px 11px;font-size:.76rem;cursor:pointer;"><i class="fa-regular fa-trash-can"></i></button>
      </div>
    </div>
  `).join('');
}

// "Hice el seguimiento": registra lo hecho, cierra la(s) tarea(s) abierta(s) y
// agenda el próximo paso en un solo movimiento. Resuelve la confusión de
// "registro lo que hice pero los contadores no bajan".
async function plHiceSeguimiento(id){
  const detalle = prompt('¿Qué hiciste en este seguimiento?\n(ej: "2do toque por LinkedIn", "llamé y no atendió"). Opcional — podés dejarlo vacío:');
  if(detalle===null) return; // canceló
  const prox = prompt('¿Cuál es el próximo paso?\nDejalo vacío si por ahora no hay.\n(ej: "Volver a escribir si no responde")');
  let vence = '';
  if(prox && prox.trim()){
    vence = prompt('¿Para qué día? Formato AAAA-MM-DD (ej: 2026-06-16).\nVacío = sin fecha:') || '';
  }
  const qs = new URLSearchParams({
    detalle: (detalle||'').trim(),
    proxima_accion: (prox||'').trim(),
    vence_en: (vence||'').trim(),
  }).toString();
  try{
    const res = await apiFetch(`${API}/prospectos/${id}/seguimiento?${qs}`, { method:'POST' });
    if(!res.ok){ alert('No se pudo registrar el seguimiento. Probá de nuevo.'); return; }
    const data = await res.json().catch(()=>({}));
    if(typeof cargarProspectosPipeline==='function') await cargarProspectosPipeline();
    if(typeof renderTareasHoy==='function') renderTareasHoy();
    const cerradas = data.tareas_cerradas||0;
    let msg = '✓ Seguimiento registrado.';
    if(cerradas>0) msg += ` Cerré ${cerradas} tarea(s) vencida(s)/pendiente(s).`;
    if(data.proxima_accion_en) msg += ' Próximo paso agendado.';
    alert(msg);
  }catch{
    alert('No se pudo registrar el seguimiento. Probá de nuevo.');
  }
}

// Prefill del formulario "Registrar un prospecto" (Paso 2) desde una ficha ya cargada,
// para no re-tipear los datos. Resuelve la fricción entre Agregar y Registrar.
function plRegistrarDesdeProspecto(id) {
  const p = (typeof todosProspectosPipeline !== 'undefined' && todosProspectosPipeline)
    ? todosProspectosPipeline.find(x => x.id === id) : null;
  if (!p) return;
  const elNombre = document.getElementById('pl-ref-nombre');
  const elPlan   = document.getElementById('pl-ref-plan');
  const elNotas  = document.getElementById('pl-ref-notas');
  if (elNombre) elNombre.value = p.nombre || '';
  if (elPlan) {
    // El plan_interes del CRM usa los mismos values que el select de registro.
    const mapPlan = { 'Plan Base':'Plan Base', 'Plan Pro':'Plan Pro', 'Plan Industrial':'Plan Industrial', 'Estrategico 360':'Estrategico 360', 'Estratégico 360':'Estrategico 360' };
    elPlan.value = mapPlan[p.plan_interes] || '';
  }
  if (elNotas) elNotas.value = p.nota ? ('Desde CRM: ' + p.nota) : '';
  // Llevar al formulario del Paso 2 y resaltarlo brevemente.
  if (elNombre) {
    elNombre.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const box = elNombre.closest('.bento-box');
    if (box) {
      box.style.transition = 'box-shadow .3s';
      box.style.boxShadow = '0 0 0 3px rgba(239,68,68,0.55)';
      setTimeout(() => { box.style.boxShadow = ''; }, 1600);
    }
  }
  if (typeof mostrarToast === 'function') mostrarToast('Datos cargados en "Registrar un prospecto". Revisá el plan y confirmá.', 'green');
}

// ─── CRM v3.0: FICHA DEL LEAD (timeline + tareas) ────────────────────────────
let _fichaLeadId = null;

function _fmtFechaCRM(iso, conHora) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const f = d.toLocaleDateString('es-AR', { day: '2-digit', month: 'short' });
  return conHora ? (f + ' ' + d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })) : f;
}

function _iconoActividad(tipo) {
  return ({ nota: 'fa-note-sticky', llamada: 'fa-phone', whatsapp: 'fa-comment-dots',
            email: 'fa-envelope', reunion: 'fa-handshake', tarea: 'fa-clock',
            sistema: 'fa-circle-info' })[tipo] || 'fa-circle';
}

function abrirFichaLead(id) {
  _fichaLeadId = id;
  const p = (typeof todosProspectosPipeline !== 'undefined' && todosProspectosPipeline)
    ? todosProspectosPipeline.find(x => x.id === id) : null;
  document.getElementById('ficha-lead-nombre').textContent = (p && p.nombre) ? p.nombre : 'Lead';
  document.getElementById('ficha-lead-estado').textContent = (p && p.estado) ? p.estado.replace(/_/g, ' ') : '';
  document.getElementById('ficha-log-texto').value = '';
  document.getElementById('ficha-tarea-desc').value = '';
  document.getElementById('ficha-tarea-fecha').value = '';
  document.getElementById('ficha-tareas').innerHTML = '';
  document.getElementById('ficha-telefono').value  = (p && p.telefono)  ? p.telefono  : '';
  document.getElementById('ficha-whatsapp').value  = (p && p.whatsapp)  ? p.whatsapp  : '';
  document.getElementById('ficha-email').value     = (p && p.email)     ? p.email     : '';
  document.getElementById('ficha-valor').value     = (p && (p.valor_usd != null)) ? p.valor_usd : '';
  document.getElementById('ficha-etiquetas').value = (p && p.etiquetas) ? p.etiquetas : '';
  _fichaLeadNombre = (p && p.nombre) ? p.nombre : '';
  _pintarBtnRegistrarVenta(p);
  _renderAccionesFicha();
  document.getElementById('ficha-timeline').innerHTML = '<div style="color:var(--text-dim);font-size:.82rem;padding:10px 0;">Cargando…</div>';
  document.getElementById('modal-ficha-lead').classList.add('open');
  cargarFichaLead(id);
  cargarContactosFicha(id);
}

async function cargarFichaLead(id) {
  try {
    const res = await apiFetch(`${API}/prospectos/${id}/actividades`);
    if (!res.ok) throw new Error('no_ok');
    const acts = await res.json();
    const tareas = acts.filter(a => a.tipo === 'tarea' && !a.completada);
    const tl     = acts.filter(a => a.tipo !== 'tarea' || a.completada);

    const tdiv = document.getElementById('ficha-tareas');
    if (!tareas.length) {
      tdiv.innerHTML = '<div style="color:var(--text-dim);font-size:.8rem;">Sin tareas pendientes.</div>';
    } else {
      tdiv.innerHTML = tareas.map(t => {
        const vence = t.vence_en ? _fmtFechaCRM(t.vence_en) : '';
        const vencida = t.vence_en && (new Date(t.vence_en) < new Date());
        return `<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border);">
          <button onclick="completarTareaFicha(${t.id})" title="Completar tarea" style="background:rgba(74,222,128,0.15);color:var(--green);border:1px solid rgba(74,222,128,0.3);border-radius:6px;width:26px;height:26px;cursor:pointer;flex-shrink:0;"><i class="fa-solid fa-check"></i></button>
          <div style="flex:1;min-width:0;font-size:.84rem;color:var(--text);">${t.descripcion || ''}</div>
          ${vence ? `<span style="font-size:.72rem;font-weight:700;white-space:nowrap;color:${vencida ? 'var(--red)' : 'var(--amber)'};"><i class="fa-regular fa-clock"></i> ${vence}</span>` : ''}
        </div>`;
      }).join('');
    }

    const tldiv = document.getElementById('ficha-timeline');
    if (!tl.length) {
      tldiv.innerHTML = '<div style="color:var(--text-dim);font-size:.82rem;padding:8px 0;">Todavía no hay actividad registrada.</div>';
    } else {
      tldiv.innerHTML = tl.map(a => `
        <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.04);">
          <div style="color:var(--text-dim);font-size:.8rem;width:18px;text-align:center;flex-shrink:0;padding-top:1px;"><i class="fa-solid ${_iconoActividad(a.tipo)}"></i></div>
          <div style="flex:1;min-width:0;">
            <div style="font-size:.84rem;color:var(--text);">${a.descripcion || ('(' + a.tipo + ')')}</div>
            <div style="font-size:.7rem;color:var(--text-dim);margin-top:2px;text-transform:capitalize;">${a.tipo}${a.completada ? ' · completada' : ''} · ${_fmtFechaCRM(a.completada_en || a.creado_en, true)}</div>
          </div>
        </div>`).join('');
    }
  } catch (e) {
    document.getElementById('ficha-timeline').innerHTML = '<div style="color:var(--text-dim);font-size:.82rem;padding:8px 0;">No se pudo cargar la actividad. Probá de nuevo en un momento.</div>';
  }
}

async function logActividadFicha(tipo) {
  if (!_fichaLeadId) return;
  const txt = (document.getElementById('ficha-log-texto').value || '').trim();
  if (!txt) { mostrarToast('Escribí qué pasó antes de registrar.', 'amber'); return; }
  try {
    const res = await apiFetch(`${API}/prospectos/${_fichaLeadId}/actividad?tipo=${encodeURIComponent(tipo)}&descripcion=${encodeURIComponent(txt)}`, { method: 'POST' });
    if (res.ok) {
      document.getElementById('ficha-log-texto').value = '';
      mostrarToast('Actividad registrada.', 'green');
      cargarFichaLead(_fichaLeadId);
    } else { mostrarToast('No se pudo registrar.', 'red'); }
  } catch { mostrarToast('Error de conexión.', 'red'); }
}

async function crearTareaFicha() {
  if (!_fichaLeadId) return;
  const desc  = (document.getElementById('ficha-tarea-desc').value || '').trim();
  const fecha = document.getElementById('ficha-tarea-fecha').value || '';
  if (!desc) { mostrarToast('Describí la tarea.', 'amber'); return; }
  try {
    const res = await apiFetch(`${API}/prospectos/${_fichaLeadId}/tarea?descripcion=${encodeURIComponent(desc)}&vence_en=${encodeURIComponent(fecha)}`, { method: 'POST' });
    if (res.ok) {
      document.getElementById('ficha-tarea-desc').value = '';
      document.getElementById('ficha-tarea-fecha').value = '';
      mostrarToast('Tarea creada.', 'green');
      cargarFichaLead(_fichaLeadId);
      if (typeof cargarProspectosPipeline === 'function') cargarProspectosPipeline();
    } else { mostrarToast('No se pudo crear la tarea.', 'red'); }
  } catch { mostrarToast('Error de conexión.', 'red'); }
}

async function completarTareaFicha(actId) {
  try {
    const res = await apiFetch(`${API}/actividades/${actId}/completar`, { method: 'PATCH' });
    if (res.ok) {
      mostrarToast('Tarea completada.', 'green');
      cargarFichaLead(_fichaLeadId);
      if (typeof cargarProspectosPipeline === 'function') cargarProspectosPipeline();
    } else { mostrarToast('No se pudo completar.', 'red'); }
  } catch { mostrarToast('Error de conexión.', 'red'); }
}

// ─── CRM v3.0 (Fase C): contacto estructurado + multicanal + ganado ──────────
let _fichaLeadNombre = null;

function _renderAccionesFicha() {
  const cont = document.getElementById('ficha-contacto-acciones');
  if (!cont) return;
  const tel   = (document.getElementById('ficha-telefono').value || '').trim();
  const waRaw = (document.getElementById('ficha-whatsapp').value || tel || '').trim();
  const mail  = (document.getElementById('ficha-email').value || '').trim();
  const pais  = (typeof aliado !== 'undefined' && aliado && aliado.pais) ? aliado.pais : 'AR';
  const wa    = (typeof _waNumeroBolsa === 'function') ? _waNumeroBolsa(waRaw, pais) : waRaw.replace(/\D/g, '');
  const telD  = (tel || waRaw).replace(/[^\d+]/g, '');
  const nombre = _fichaLeadNombre || 'tu empresa';
  const msg  = encodeURIComponent('Hola, te contacto de Avanza Digital por ' + nombre + '. ¿Tenés un minuto para una consulta rápida?');
  const subj = encodeURIComponent('Avanza Digital — ' + nombre);
  const btns = [];
  if (wa)   btns.push('<a href="https://wa.me/' + wa + '?text=' + msg + '" target="_blank" rel="noopener" onclick="_logEnvioFicha(\'whatsapp\')" style="flex:1;min-width:100px;text-align:center;padding:8px;font-size:.8rem;font-weight:700;text-decoration:none;background:var(--green);color:#000;border-radius:8px;"><i class="fa-brands fa-whatsapp"></i> WhatsApp</a>');
  if (wa && typeof _fichaLeadId !== 'undefined' && _fichaLeadId) btns.push('<button onclick="abrirOutreach(\'prospecto\',' + _fichaLeadId + ',\'' + wa + '\')" style="flex:1;min-width:100px;text-align:center;padding:8px;font-size:.8rem;font-weight:700;background:rgba(127,216,255,0.1);color:#7fd8ff;border:1px solid rgba(127,216,255,0.35);border-radius:8px;cursor:pointer;font-family:\'Inter\',sans-serif;"><i class="fa-solid fa-wand-magic-sparkles"></i> Mensaje IA</button>');
  if (telD) btns.push('<a href="tel:' + telD + '" onclick="_logEnvioFicha(\'llamada\')" style="flex:1;min-width:80px;text-align:center;padding:8px;font-size:.8rem;font-weight:700;text-decoration:none;background:rgba(59,130,246,0.15);color:#60a5fa;border:1px solid rgba(59,130,246,0.3);border-radius:8px;"><i class="fa-solid fa-phone"></i> Llamar</a>');
  if (mail) btns.push('<a href="mailto:' + mail + '?subject=' + subj + '" onclick="_logEnvioFicha(\'email\')" style="flex:1;min-width:80px;text-align:center;padding:8px;font-size:.8rem;font-weight:700;text-decoration:none;background:rgba(255,255,255,0.06);color:var(--text-muted);border:1px solid var(--border);border-radius:8px;"><i class="fa-solid fa-envelope"></i> Email</a>');
  cont.innerHTML = btns.length
    ? '<div style="display:flex;gap:6px;flex-wrap:wrap;">' + btns.join('') + '</div>'
    : '<div style="font-size:.76rem;color:var(--text-dim);">Cargá teléfono, WhatsApp o email para contactar de un toque.</div>';
}

// Salto 1: registra en el timeline el toque enviado desde la ficha (fire-and-forget)
function _logEnvioFicha(tipo) {
  if (!_fichaLeadId) return;
  const txt = ({ whatsapp: 'WhatsApp enviado', email: 'Email enviado', llamada: 'Llamada iniciada' })[tipo] || 'Contacto';
  try {
    apiFetch(`${API}/prospectos/${_fichaLeadId}/actividad?tipo=${encodeURIComponent(tipo)}&descripcion=${encodeURIComponent(txt)}`, { method: 'POST' });
  } catch (e) {}
  setTimeout(() => { if (typeof cargarFichaLead === 'function' && _fichaLeadId) cargarFichaLead(_fichaLeadId); }, 1200);
}

async function guardarContactoFicha() {
  if (!_fichaLeadId) return;
  const email     = (document.getElementById('ficha-email').value || '').trim();
  const telefono  = (document.getElementById('ficha-telefono').value || '').trim();
  const whatsapp  = (document.getElementById('ficha-whatsapp').value || '').trim();
  const etiquetas = (document.getElementById('ficha-etiquetas').value || '').trim();
  const valorRaw  = (document.getElementById('ficha-valor').value || '').trim();
  let q = `${API}/prospectos/${_fichaLeadId}/contacto-datos?email=${encodeURIComponent(email)}&telefono=${encodeURIComponent(telefono)}&whatsapp=${encodeURIComponent(whatsapp)}&etiquetas=${encodeURIComponent(etiquetas)}`;
  if (valorRaw !== '') q += `&valor_usd=${encodeURIComponent(valorRaw)}`;
  try {
    const res = await apiFetch(q, { method: 'PATCH' });
    if (res.ok) {
      mostrarToast('Datos guardados.', 'green');
      const p = (typeof todosProspectosPipeline !== 'undefined' && todosProspectosPipeline) ? todosProspectosPipeline.find(x => x.id === _fichaLeadId) : null;
      if (p) { p.email = email; p.telefono = telefono; p.whatsapp = whatsapp; p.etiquetas = etiquetas; if (valorRaw !== '') p.valor_usd = parseFloat(valorRaw); }
      _renderAccionesFicha();
      if (typeof cargarProspectosPipeline === 'function') cargarProspectosPipeline();
    } else { mostrarToast('No se pudo guardar.', 'red'); }
  } catch { mostrarToast('Error de conexión.', 'red'); }
}

async function marcarGanadoFicha() {
  if (!_fichaLeadId) return;
  if (!confirm('¿Marcar este lead como GANADO? Queda registrado el cierre en el historial.')) return;
  const valorRaw = (document.getElementById('ficha-valor').value || '').trim();
  let q = `${API}/prospectos/${_fichaLeadId}/ganado`;
  if (valorRaw !== '') q += `?valor_usd=${encodeURIComponent(valorRaw)}`;
  try {
    const res = await apiFetch(q, { method: 'PATCH' });
    if (res.ok) {
      mostrarToast('¡Marcado como ganado!', 'green');
      document.getElementById('modal-ficha-lead').classList.remove('open');
      if (typeof cargarProspectosPipeline === 'function') cargarProspectosPipeline();
    } else { mostrarToast('No se pudo marcar.', 'red'); }
  } catch { mostrarToast('Error de conexión.', 'red'); }
}

// ─── Puente CRM → Referido: registrar para venta en 1 click ─────────────────
function _pintarBtnRegistrarVenta(p) {
  const btn = document.getElementById('ficha-btn-reg-venta');
  const panel = document.getElementById('ficha-ref-panel');
  if (!btn) return;
  if (panel) panel.style.display = 'none';

  // Canal 2 no usa referidos (misma regla que el Paso 2 del pipeline)
  if (aliado && (aliado.tipo_aliado || 'canal1') === 'canal2') {
    btn.style.display = 'none';
    return;
  }
  btn.style.display = '';

  if (p && p.referido_id) {
    btn.innerHTML = '<i class="fa-solid fa-circle-check"></i> Registrado para venta';
    btn.style.background = 'rgba(74,222,128,0.12)';
    btn.style.color = 'var(--green)';
    btn.style.borderColor = 'rgba(74,222,128,0.35)';
    btn.style.cursor = 'default';
    btn.disabled = true;
  } else {
    btn.innerHTML = '<i class="fa-solid fa-lock"></i> Registrar para venta';
    btn.style.background = 'rgba(239,68,68,0.12)';
    btn.style.color = '#f87171';
    btn.style.borderColor = 'rgba(239,68,68,0.35)';
    btn.style.cursor = 'pointer';
    btn.disabled = false;
    // Precargar el plan de interés del lead si coincide con un plan de sistema
    const sel = document.getElementById('ficha-ref-plan');
    if (sel && p && p.plan_interes) {
      const opt = Array.from(sel.options).find(o => o.value === p.plan_interes);
      sel.value = opt ? p.plan_interes : '';
    } else if (sel) { sel.value = ''; }
  }
}

function toggleRegistrarVentaFicha() {
  const panel = document.getElementById('ficha-ref-panel');
  if (!panel) return;
  panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

async function confirmarRegistrarVentaFicha() {
  if (!_fichaLeadId) return;
  const plan = (document.getElementById('ficha-ref-plan').value || '').trim();
  if (!plan) { mostrarToast('Elegí el plan que va a contratar el cliente.', 'red'); return; }
  const btn = document.getElementById('ficha-ref-confirm');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    const res = await apiFetch(`${API}/prospectos/${_fichaLeadId}/registrar-referido?plan=${encodeURIComponent(plan)}`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'No se pudo registrar.');
    mostrarToast(data.ya_existia
      ? 'Este lead ya estaba registrado para venta.'
      : `🔒 ${data.mensaje} Comisión estimada: USD ${(data.comision_estimada || 0).toLocaleString('es-AR')}.`, 'green');
    // Reflejar en memoria local + UI sin esperar al refetch
    const p = (typeof todosProspectosPipeline !== 'undefined' && todosProspectosPipeline)
      ? todosProspectosPipeline.find(x => x.id === _fichaLeadId) : null;
    if (p) p.referido_id = data.id_referido;
    _pintarBtnRegistrarVenta(p);
    cargarFichaLead(_fichaLeadId);  // timeline con la actividad nueva
    setTimeout(() => { cargarTodo(); if (typeof plRenderReferidos === 'function') plRenderReferidos(); }, 800);
  } catch (e) {
    mostrarToast(e.message || 'Error al registrar para venta.', 'red');
  }
  btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Registrar';
}

// ─── Eliminar lead del CRM (definitivo; para errores/duplicados) ─────────────
async function eliminarProspectoFicha() {
  if (!_fichaLeadId) return;
  const nombre = _fichaLeadNombre || 'este lead';
  if (!confirm(`¿Eliminar "${nombre}" definitivamente?\n\nSe borra el lead con todo su historial y tareas. Si el cliente no compró pero existió la conversación, te conviene marcarlo PERDIDO (conserva el historial y tus métricas).\n\nEliminar es para duplicados o errores de carga.`)) return;
  try {
    const res = await apiFetch(`${API}/prospectos/${_fichaLeadId}/eliminar`, { method: 'DELETE' });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || 'No se pudo eliminar.');
    }
    mostrarToast('Lead eliminado.', 'green');
    document.getElementById('modal-ficha-lead').classList.remove('open');
    if (typeof cargarProspectosPipeline === 'function') cargarProspectosPipeline();
  } catch (e) {
    mostrarToast(e.message || 'Error al eliminar.', 'red');
  }
}

// ─── Import masivo de prospectos (aliado -> su CRM) ───
function plToggleImport() {
  const el = document.getElementById('pl-import-panel');
  if (el) el.style.display = (el.style.display === 'none' || !el.style.display) ? 'block' : 'none';
}
function plImportFile(ev) {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = () => { const t = document.getElementById('pl-import-text'); if (t) t.value = r.result; plPreviewImport(); };
  r.readAsText(f);
}
function _plParseImport(text) {
  const HEADERS = {
    nombre: ['nombre','empresa','cliente','razon social','razón social'],
    contacto: ['contacto','telefono','teléfono','whatsapp','celular','tel','email','correo'],
    plan_interes: ['plan','plan_interes','plan de interes','plan de interés','interes','interés'],
    rubro: ['rubro','sector','industria'],
    nota: ['nota','notas','observacion','observación','observaciones','comentario']
  };
  const lines = (text || '').split(/\r?\n/).map(l => l.trim()).filter(l => l.length);
  if (!lines.length) return [];
  const delim = lines[0].indexOf('\t') >= 0 ? '\t' : ((lines[0].indexOf(';') >= 0 && lines[0].indexOf(',') < 0) ? ';' : ',');
  const split = l => l.split(delim).map(c => c.trim().replace(/^["']+|["']+$/g, ''));
  const first = split(lines[0]).map(c => c.toLowerCase());
  let map = null, start = 0;
  const isHeader = first.some(c => Object.values(HEADERS).some(arr => arr.indexOf(c) >= 0));
  if (isHeader) {
    map = {};
    first.forEach((c, i) => { for (const k in HEADERS) { if (HEADERS[k].indexOf(c) >= 0) map[k] = i; } });
    start = 1;
  }
  const order = ['nombre','contacto','plan_interes','rubro','nota'];
  const out = [];
  for (let i = start; i < lines.length; i++) {
    const cells = split(lines[i]);
    const obj = { nombre: '', contacto: '', plan_interes: '', rubro: '', nota: '' };
    if (map) { for (const k in map) obj[k] = cells[map[k]] || ''; }
    else { order.forEach((k, idx) => { obj[k] = cells[idx] || ''; }); }
    if (obj.nombre) out.push(obj);
  }
  return out;
}
function plPreviewImport() {
  const ta = document.getElementById('pl-import-text');
  const arr = _plParseImport(ta ? ta.value : '');
  const msg = document.getElementById('pl-import-msg');
  if (msg) msg.textContent = arr.length ? (arr.length + ' prospecto(s) detectado(s) para importar.') : 'No se detectaron filas válidas (falta el nombre).';
  return arr;
}
async function plDoImport() {
  const arr = plPreviewImport();
  if (!arr.length) return;
  const btn = document.getElementById('pl-import-do');
  if (btn) { btn.disabled = true; btn.textContent = 'Importando…'; }
  try {
    const res = await apiFetch(`${API}/prospectos/bulk`, { method: 'POST', body: { prospectos: arr } });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || 'No se pudo importar.');
    mostrarToast(d.mensaje || ((d.insertados || 0) + ' importados.'), 'green');
    const ta = document.getElementById('pl-import-text'); if (ta) ta.value = '';
    const msg = document.getElementById('pl-import-msg');
    if (msg) msg.textContent = (d.omitidos ? ('Omitidos por duplicado: ' + (d.omitidos_detalle || []).join(', ')) : '');
    if (typeof cargarProspectosPipeline === 'function') cargarProspectosPipeline();
  } catch (e) {
    mostrarToast(e.message || 'Error al importar.', 'red');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Importar'; }
  }
}

// ─── Mis tareas de hoy (pantalla de entrada) ───
async function renderTareasHoy() {
  const box = document.getElementById('tareas-hoy-box');
  const list = document.getElementById('tareas-hoy-list');
  const cnt = document.getElementById('tareas-hoy-count');
  if (!box || !list) return;
  try {
    const res = await apiFetch(`${API}/prospectos/tareas/pendientes`);
    if (!res.ok) { box.style.display = 'none'; return; }
    const tareas = await res.json();
    if (!Array.isArray(tareas) || !tareas.length) { box.style.display = 'none'; return; }
    const ahora = new Date();
    const items = tareas.map(t => ({ ...t, _v: t.vence_en ? new Date(t.vence_en) : null }));
    items.sort((a, b) => { if (!a._v) return 1; if (!b._v) return -1; return a._v - b._v; });
    const vencidas = items.filter(t => t._v && t._v < ahora).length;
    if (cnt) cnt.textContent = vencidas ? (vencidas + ' vencida(s)') : (items.length + ' pendiente(s)');
    list.innerHTML = items.map(t => {
      const overdue = t._v && t._v < ahora;
      const fecha = t._v ? t._v.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' }) : 'sin fecha';
      const color = overdue ? 'var(--red)' : 'var(--text-muted)';
      const tag = overdue ? 'Vencida' : 'Próxima';
      return `<div onclick="abrirFichaLead(${t.prospecto_id})" style="display:flex;align-items:center;gap:10px;padding:9px 6px;border-bottom:1px solid var(--border);cursor:pointer;">
        <span style="font-size:.72rem;font-weight:800;color:${color};border:1px solid ${color};border-radius:50px;padding:2px 9px;white-space:nowrap;">${tag} · ${fecha}</span>
        <span style="font-size:.86rem;font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:38%;">${t.prospecto_nombre || '—'}</span>
        <span style="font-size:.8rem;color:var(--text-muted);margin-left:auto;text-align:right;">${t.descripcion || ''}</span>
      </div>`;
    }).join('');
    box.style.display = 'block';
  } catch (e) { box.style.display = 'none'; }
}

// ─── Web Push: opt-in y gestión de suscripción ───
function _urlB64ToUint8(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}
async function activarNotificaciones() {
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
      mostrarToast('Tu navegador no soporta notificaciones push.', 'red'); return;
    }
    const vr = await apiFetch(`${API}/push/vapid-public`);
    const vd = await vr.json();
    if (!vd.enabled || !vd.public_key) { mostrarToast('Las notificaciones aún no están configuradas en el servidor.', 'red'); return; }
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') { mostrarToast('No diste permiso para notificaciones.', 'amber'); _renderEstadoPush(); return; }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: _urlB64ToUint8(vd.public_key) });
    const j = sub.toJSON();
    await apiFetch(`${API}/push/subscribe`, { method: 'POST', body: { endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth } });
    mostrarToast('Notificaciones activadas en este dispositivo ✓', 'green');
    _renderEstadoPush();
  } catch (e) {
    console.warn('push subscribe error', e);
    mostrarToast('No se pudieron activar las notificaciones.', 'red');
  }
}
async function desactivarNotificaciones() {
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      const j = sub.toJSON();
      try { await apiFetch(`${API}/push/unsubscribe`, { method: 'POST', body: { endpoint: j.endpoint } }); } catch (e) {}
      await sub.unsubscribe();
    }
    mostrarToast('Notificaciones desactivadas en este dispositivo.', 'amber');
    _renderEstadoPush();
  } catch (e) { console.warn(e); }
}
async function _renderEstadoPush() {
  const el = document.getElementById('push-estado');
  const btnOn = document.getElementById('push-btn-on');
  const btnOff = document.getElementById('push-btn-off');
  if (!el) return;
  let activo = false, soportado = ('serviceWorker' in navigator) && ('PushManager' in window) && ('Notification' in window);
  try {
    if (soportado) {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      activo = !!sub && Notification.permission === 'granted';
    }
  } catch (e) {}
  if (!soportado) { el.textContent = 'No disponible en este navegador'; el.style.color = 'var(--text-dim)'; if (btnOn) btnOn.style.display = 'none'; if (btnOff) btnOff.style.display = 'none'; return; }
  const bloqueado = (typeof Notification !== 'undefined' && Notification.permission === 'denied');
  el.textContent = activo ? 'Activadas en este dispositivo' : (bloqueado ? 'Bloqueadas en el navegador' : 'Desactivadas');
  el.style.color = activo ? 'var(--green)' : 'var(--text-muted)';
  if (btnOn) btnOn.style.display = (activo || bloqueado) ? 'none' : 'inline-block';
  if (btnOff) btnOff.style.display = activo ? 'inline-block' : 'none';
}
async function _initPush() {
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return;
    if (Notification.permission === 'granted') {
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        const vr = await apiFetch(`${API}/push/vapid-public`); const vd = await vr.json();
        if (vd.enabled && vd.public_key) {
          sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: _urlB64ToUint8(vd.public_key) });
        }
      }
      if (sub) { const j = sub.toJSON(); apiFetch(`${API}/push/subscribe`, { method: 'POST', body: { endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth } }).catch(() => {}); }
    }
    _renderEstadoPush();
  } catch (e) {}
}

async function eliminarLeadDirecto(id) {
  let nombre = 'este lead';
  try {
    const arr = (typeof todosProspectosPipeline !== 'undefined' && todosProspectosPipeline) ? todosProspectosPipeline : [];
    const _p = arr.find(x => x.id === id);
    if (_p && _p.nombre) nombre = _p.nombre;
  } catch (e) {}
  if (!confirm(`¿Eliminar "${nombre}" definitivamente?\n\nSe borra el lead con todo su historial y tareas. Si hubo conversación pero el cliente no compró, conviene marcarlo PERDIDO (conserva historial y métricas).\n\nEliminar es para duplicados o errores de carga.`)) return;
  try {
    const res = await apiFetch(`${API}/prospectos/${id}/eliminar`, { method: 'DELETE' });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || 'No se pudo eliminar.'); }
    mostrarToast('Lead eliminado.', 'green');
    if (typeof cargarProspectosPipeline === 'function') cargarProspectosPipeline();
  } catch (e) {
    mostrarToast(e.message || 'Error al eliminar.', 'red');
  }
}

// ─── CRM v3.0 (Fase D): métricas del embudo + vista kanban ───────────────────
window._plVista = window._plVista || 'lista';

function _plLeads() {
  return (typeof todosProspectosPipeline !== 'undefined' && todosProspectosPipeline) ? todosProspectosPipeline : [];
}
function _fmtUSD(n) {
  n = Number(n) || 0;
  return 'US$ ' + n.toLocaleString('es-AR');
}
function plCambiarVista(v) {
  window._plVista = v;
  if (typeof plRenderProspectos === 'function') plRenderProspectos();
}

const _PL_ETAPAS = [
  { key: 'sin_contactar',     label: 'Sin contactar',     color: 'var(--red)' },
  { key: 'contactado',        label: 'Contactado',        color: 'var(--amber)' },
  { key: 'propuesta_enviada', label: 'Propuesta enviada', color: 'var(--primary)' },
  { key: 'respondio',         label: 'Respondió',         color: '#22d3ee' },
  { key: 'ganado',            label: 'Ganado',            color: 'var(--green)' },
  { key: 'perdido',           label: 'Perdido',           color: '#71717a' },
];
const _PL_ABIERTAS = ['sin_contactar', 'contactado', 'propuesta_enviada', 'respondio'];

function plRenderMetricas() {
  const cont = document.getElementById('pl-metricas');
  if (!cont) return;
  const leads = _plLeads();
  const porEstado = {};
  let valorPipeline = 0, valorGanado = 0, ganados = 0, perdidos = 0;
  leads.forEach(p => {
    porEstado[p.estado] = (porEstado[p.estado] || 0) + 1;
    const v = Number(p.valor_usd) || 0;
    if (_PL_ABIERTAS.indexOf(p.estado) >= 0) valorPipeline += v;
    if (p.estado === 'ganado') { valorGanado += v; ganados++; }
    if (p.estado === 'perdido') perdidos++;
  });
  const cerrados = ganados + perdidos;
  const winRate = cerrados ? Math.round(ganados / cerrados * 100) : 0;
  const activos = leads.length - (porEstado['ganado'] || 0) - (porEstado['perdido'] || 0);

  const toggle = `
    <div style="display:inline-flex;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:8px;overflow:hidden;">
      <button onclick="plCambiarVista('lista')" style="background:${window._plVista === 'lista' ? 'var(--primary)' : 'transparent'};color:${window._plVista === 'lista' ? '#fff' : 'var(--text-muted)'};border:none;padding:6px 14px;font-size:.78rem;font-weight:700;cursor:pointer;"><i class="fa-solid fa-list"></i> Lista</button>
      <button onclick="plCambiarVista('kanban')" style="background:${window._plVista === 'kanban' ? 'var(--primary)' : 'transparent'};color:${window._plVista === 'kanban' ? '#fff' : 'var(--text-muted)'};border:none;padding:6px 14px;font-size:.78rem;font-weight:700;cursor:pointer;"><i class="fa-solid fa-table-columns"></i> Kanban</button>
    </div>`;

  const tile = (label, valor, color) => `
    <div style="flex:1;min-width:115px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:10px;padding:11px 13px;">
      <div style="font-size:.66rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:.06em;font-weight:700;">${label}</div>
      <div style="font-size:1.1rem;font-weight:800;color:${color || 'var(--text)'};margin-top:3px;">${valor}</div>
    </div>`;

  const funnel = _PL_ETAPAS.map(e => `
    <div style="flex:1;min-width:64px;text-align:center;padding:8px 4px;background:rgba(255,255,255,0.02);border-radius:8px;border-top:3px solid ${e.color};">
      <div style="font-size:1.05rem;font-weight:800;color:var(--text);">${porEstado[e.key] || 0}</div>
      <div style="font-size:.62rem;color:var(--text-dim);margin-top:2px;">${e.label}</div>
    </div>`).join('');

  cont.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px;">
      <div style="font-size:.8rem;color:var(--text-muted);font-weight:700;"><i class="fa-solid fa-chart-simple" style="color:var(--primary);"></i> Embudo</div>
      ${toggle}
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
      ${tile('Leads activos', activos, 'var(--text)')}
      ${tile('Pipeline abierto', _fmtUSD(valorPipeline), 'var(--amber)')}
      ${tile('Ganado', _fmtUSD(valorGanado), 'var(--green)')}
      ${tile('Win rate', winRate + '%' + (cerrados ? ` · ${ganados}/${cerrados}` : ''), 'var(--primary)')}
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;">${funnel}</div>`;
}

function plRenderKanban() {
  const cont = document.getElementById('pl-lista-prospectos');
  if (!cont) return;
  const leads = _plLeads();
  const cols = _PL_ETAPAS.map(e => {
    const items = leads.filter(p => p.estado === e.key);
    const suma = items.reduce((a, p) => a + (Number(p.valor_usd) || 0), 0);
    const cards = items.length ? items.map(p => {
      const prox = p.proxima_accion_en ? `<div style="font-size:.68rem;color:var(--amber);margin-top:4px;"><i class="fa-regular fa-clock"></i> ${_fmtFechaCRM(p.proxima_accion_en)}</div>` : '';
      const val  = (p.valor_usd != null && p.valor_usd !== '') ? `<span style="font-size:.72rem;color:var(--green);font-weight:700;white-space:nowrap;">${_fmtUSD(p.valor_usd)}</span>` : '';
      return `<div onclick="abrirFichaLead(${p.id})" style="background:#111;border:1px solid var(--border);border-radius:8px;padding:9px 10px;margin-bottom:7px;cursor:pointer;">
        <div style="display:flex;justify-content:space-between;gap:6px;align-items:flex-start;">
          <div style="font-size:.83rem;font-weight:700;color:var(--text);min-width:0;">${p.nombre || '—'}</div>
          <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">${val}<button onclick="event.stopPropagation();eliminarLeadDirecto(${p.id})" title="Eliminar lead" style="background:transparent;border:none;color:var(--text-dim);cursor:pointer;font-size:.74rem;padding:2px;line-height:1;"><i class="fa-regular fa-trash-can"></i></button></div>
        </div>
        ${p.plan_interes ? `<div style="font-size:.7rem;color:var(--text-dim);margin-top:2px;">${p.plan_interes}</div>` : ''}
        ${prox}
      </div>`;
    }).join('') : `<div style="font-size:.72rem;color:var(--text-dim);padding:8px 2px;">—</div>`;
    return `<div style="flex:0 0 220px;background:rgba(255,255,255,0.015);border:1px solid var(--border);border-radius:10px;padding:10px;">
      <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid ${e.color};padding-bottom:7px;margin-bottom:9px;">
        <span style="font-size:.76rem;font-weight:800;color:var(--text);">${e.label}</span>
        <span style="font-size:.68rem;color:var(--text-dim);white-space:nowrap;">${items.length}${suma ? ' · ' + _fmtUSD(suma) : ''}</span>
      </div>
      ${cards}
    </div>`;
  }).join('');
  cont.innerHTML = `<div style="display:flex;gap:10px;overflow-x:auto;padding:4px 0 10px;">${cols}</div>`;
}

// ─── CRM v3.0 (Salto 3): contactos de la empresa (varios interlocutores) ─────
async function cargarContactosFicha(id) {
  const cont = document.getElementById('ficha-contactos');
  if (!cont) return;
  const pais = (typeof aliado !== 'undefined' && aliado && aliado.pais) ? aliado.pais : 'AR';
  try {
    const res = await apiFetch(`${API}/prospectos/${id}/contactos`);
    if (!res.ok) throw new Error('no_ok');
    const cs = await res.json();
    if (!cs.length) {
      cont.innerHTML = '<div style="font-size:.78rem;color:var(--text-dim);">Sin contactos adicionales. Agregá los otros interlocutores de la empresa (dueño, compras, técnico...).</div>';
      return;
    }
    cont.innerHTML = cs.map(c => {
      const wa   = (typeof _waNumeroBolsa === 'function') ? _waNumeroBolsa(c.whatsapp || c.telefono || '', pais) : '';
      const telD = (c.telefono || c.whatsapp || '').replace(/[^\d+]/g, '');
      const mail = (c.email || '').trim();
      const acc = [];
      if (wa)   acc.push('<a href="https://wa.me/' + wa + '" target="_blank" rel="noopener" onclick="_logEnvioFicha(\'whatsapp\')" style="padding:5px 10px;font-size:.74rem;font-weight:700;text-decoration:none;background:rgba(74,222,128,0.12);color:var(--green);border:1px solid rgba(74,222,128,0.3);border-radius:6px;"><i class="fa-brands fa-whatsapp"></i> WhatsApp</a>');
      if (telD) acc.push('<a href="tel:' + telD + '" onclick="_logEnvioFicha(\'llamada\')" style="padding:5px 10px;font-size:.74rem;font-weight:700;text-decoration:none;background:rgba(59,130,246,0.12);color:#60a5fa;border:1px solid rgba(59,130,246,0.3);border-radius:6px;"><i class="fa-solid fa-phone"></i> Llamar</a>');
      if (mail) acc.push('<a href="mailto:' + mail + '" onclick="_logEnvioFicha(\'email\')" style="padding:5px 10px;font-size:.74rem;font-weight:700;text-decoration:none;background:rgba(255,255,255,0.05);color:var(--text-muted);border:1px solid var(--border);border-radius:6px;"><i class="fa-solid fa-envelope"></i> Email</a>');
      return `<div style="border:1px solid var(--border);border-radius:8px;padding:9px 10px;margin-bottom:7px;">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
          <div style="font-size:.85rem;color:var(--text);min-width:0;"><strong>${c.nombre}</strong>${c.rol ? ` · <span style="color:var(--text-muted);font-size:.78rem;">${c.rol}</span>` : ''}</div>
          <button onclick="eliminarContactoFicha(${c.id})" title="Eliminar contacto" style="background:transparent;border:none;color:var(--text-dim);cursor:pointer;font-size:1.1rem;line-height:1;flex-shrink:0;">&times;</button>
        </div>
        ${acc.length ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:7px;">${acc.join('')}</div>` : '<div style="font-size:.72rem;color:var(--text-dim);margin-top:4px;">Sin datos de contacto.</div>'}
      </div>`;
    }).join('');
  } catch (e) {
    cont.innerHTML = '<div style="font-size:.78rem;color:var(--text-dim);">No se pudieron cargar los contactos.</div>';
  }
}

async function agregarContactoFicha() {
  if (!_fichaLeadId) return;
  const nombre = (document.getElementById('fc-nuevo-nombre').value || '').trim();
  if (!nombre) { mostrarToast('Poné al menos el nombre.', 'amber'); return; }
  const rol  = (document.getElementById('fc-nuevo-rol').value || '').trim();
  const tel  = (document.getElementById('fc-nuevo-tel').value || '').trim();
  const wa   = (document.getElementById('fc-nuevo-wa').value || '').trim();
  const mail = (document.getElementById('fc-nuevo-email').value || '').trim();
  const q = `${API}/prospectos/${_fichaLeadId}/contactos?nombre=${encodeURIComponent(nombre)}&rol=${encodeURIComponent(rol)}&telefono=${encodeURIComponent(tel)}&whatsapp=${encodeURIComponent(wa)}&email=${encodeURIComponent(mail)}`;
  try {
    const res = await apiFetch(q, { method: 'POST' });
    if (res.ok) {
      ['fc-nuevo-nombre', 'fc-nuevo-rol', 'fc-nuevo-tel', 'fc-nuevo-wa', 'fc-nuevo-email'].forEach(i => document.getElementById(i).value = '');
      mostrarToast('Contacto agregado.', 'green');
      cargarContactosFicha(_fichaLeadId);
    } else { mostrarToast('No se pudo agregar.', 'red'); }
  } catch { mostrarToast('Error de conexión.', 'red'); }
}

async function eliminarContactoFicha(cid) {
  if (!confirm('¿Eliminar este contacto?')) return;
  try {
    const res = await apiFetch(`${API}/contactos/${cid}`, { method: 'DELETE' });
    if (res.ok) { mostrarToast('Contacto eliminado.', 'green'); cargarContactosFicha(_fichaLeadId); }
    else { mostrarToast('No se pudo eliminar.', 'red'); }
  } catch { mostrarToast('Error de conexión.', 'red'); }
}

async function plCrearProspecto() {
  const nombre = document.getElementById('pl-np-nombre').value.trim();
  if(!nombre) { alert('El nombre es obligatorio.'); return; }
  const contacto = document.getElementById('pl-np-contacto').value.trim();
  const plan = document.getElementById('pl-np-plan').value;
  const rubro = document.getElementById('pl-np-rubro').value;
  const nota = document.getElementById('pl-np-nota').value.trim();
  const url = `${API}/prospectos/crear?codigo_aliado=${aliado.codigo}&nombre=${encodeURIComponent(nombre)}&contacto=${encodeURIComponent(contacto)}&plan_interes=${encodeURIComponent(plan)}&rubro=${encodeURIComponent(rubro)}&nota=${encodeURIComponent(nota)}`;
  try {
    const res = await apiFetch(url, {method:'POST'});
    if(res.ok) {
      ['pl-np-nombre','pl-np-contacto','pl-np-nota'].forEach(id=>document.getElementById(id).value='');
      document.getElementById('pl-np-plan').value='';
      document.getElementById('pl-np-rubro').value='';
      const msg = document.getElementById('pl-np-msg');
      msg.style.display='inline'; setTimeout(()=>msg.style.display='none',2500);
      await cargarProspectosPipeline();
    } else { const d=await res.json(); alert(d.detail||'Error al cargar.'); }
  } catch { alert('Error de conexión.'); }
}

async function plRegistrarReferido() {
  const nombre = document.getElementById('pl-ref-nombre').value.trim();
  const plan = document.getElementById('pl-ref-plan').value;
  const notas = document.getElementById('pl-ref-notas').value;
  const btn = document.getElementById('pl-btn-ref');
  const success = document.getElementById('pl-ref-success');
  if (!nombre||!plan) { alert('Completá el nombre del cliente y el plan elegido.'); return; }
  btn.innerHTML='<span class="spinner"></span> Registrando...'; btn.disabled=true; success.style.display='none';
  try {
    const res = await apiFetch(`${API}/referidos/registrar?ref_code=${aliado.ref_code}&nombre_cliente=${encodeURIComponent(nombre)}&plan_elegido=${encodeURIComponent(plan)}&notas=${encodeURIComponent(notas)}`,{method:'POST'});
    const data = await res.json();
    if (res.ok) {
      const comision = Math.round(PLANES[plan]*pct(aliado.nivel_calculado||'BASIC'));
      success.innerHTML=`<i class="fa-solid fa-circle-check"></i> <strong>Prospecto registrado.</strong> Avanza Digital fue notificado.<br>Comisión estimada si se cierra: <strong>USD ${comision.toLocaleString()}</strong>.`;
      success.style.display='block';
      document.getElementById('pl-ref-nombre').value='';
      document.getElementById('pl-ref-plan').value='';
      document.getElementById('pl-ref-notas').value='';
      setTimeout(()=>{ cargarTodo(); plRenderReferidos(); },800);
    } else { alert(data.detail||'Error al registrar'); }
  } catch { alert('Error de conexión. Verificá que el servidor esté activo.'); }
  btn.innerHTML='<i class="fa-solid fa-paper-plane"></i> Registrar prospecto ahora'; btn.disabled=false;
}

function plRenderReferidos() {
  const refs = aliado?.referidos||[];
  const el = document.getElementById('pl-tabla-referidos');
  if(!el) return;
  if (!refs.length) { el.innerHTML=`<div class="empty-state"><i class="fa-solid fa-user-plus"></i><p>Todavía no registraste ningún prospecto.<br>Usá el formulario de arriba <strong style="color:white;">antes de que el cliente pague</strong>.</p></div>`; return; }
  el.innerHTML=`<table><thead><tr><th>Cliente / Empresa</th><th>Plan</th><th>Comisión est.</th><th>Fecha</th><th>Confirmado</th><th>Estado</th></tr></thead><tbody>${refs.map(r=>{
    const nivel = aliado.nivel_calculado || aliado.nivel_actual || 'BASIC';
    const comEst = Math.round((PLANES[r.plan]||0)*pct(nivel));
    const confCol = r.rechazado
      ? '<span class="badge badge-red"><i class="fa-solid fa-circle-xmark"></i> No confirmado</span>'
      : (r.confirmado ? '<span class="badge badge-green"><i class="fa-solid fa-check"></i> Confirmado</span>' : '<span class="badge badge-gray"><i class="fa-solid fa-clock"></i> Pendiente</span>');
    const notaFila = r.nota_admin ? `<tr><td colspan="6" style="padding:4px 24px 16px;font-size:.8rem;color:var(--text-dim);background:rgba(255,255,255,0.015);"><i class="fa-solid fa-message" style="color:var(--amber);"></i> Nota de Avanza: <span style="color:var(--text);">${r.nota_admin}</span></td></tr>` : '';
    return `<tr>
      <td style="color:var(--text);font-weight:600;">${r.cliente}</td>
      <td><span class="badge badge-blue">${r.plan}</span></td>
      <td style="color:var(--green);font-weight:700;">${comEst>0?'USD '+comEst.toLocaleString():'—'}</td>
      <td style="font-size:.82rem;color:var(--text-dim);">${r.fecha}</td>
      <td>${confCol}</td>
      <td>${r.convertido?'<span class="badge badge-green"><i class="fa-solid fa-handshake"></i> Venta cerrada</span>':'<span class="badge badge-blue"><i class="fa-solid fa-clock"></i> En proceso</span>'}</td>
    </tr>${notaFila}`;
  }).join('')}</tbody></table>`;
}

function plRenderVentas() {
  const ventas = aliado?.ventas||[];
  const el = document.getElementById('pl-tabla-ventas');
  if(!el) return;
  if (!ventas.length) { el.innerHTML=`<div class="empty-state"><i class="fa-solid fa-handshake"></i><p>Todavía no tenés ventas confirmadas.<br>Cuando Avanza registre tu primera venta, aparecerá acá.</p></div>`; return; }
  el.innerHTML=`<table><thead><tr><th>Cliente</th><th>Plan</th><th>Valor (USD)</th><th>Tu comisión</th><th>Estado</th><th>Fecha</th></tr></thead><tbody>${ventas.map(v=>`<tr>
    <td style="color:var(--text);font-weight:600;">${v.cliente}</td><td>${v.plan}</td>
    <td>USD ${v.valor.toLocaleString()}</td>
    <td style="color:var(--green);font-weight:800;">USD ${Math.round(v.comision).toLocaleString()}</td>
    <td>${v.pagada?'<span class="badge badge-green"><i class="fa-solid fa-circle-check"></i> Pagada</span>':'<span class="badge badge-amber"><i class="fa-solid fa-clock"></i> Pendiente 24hs</span>'}</td>
    <td style="font-size:.82rem;color:var(--text-dim);">${v.fecha||'—'}</td>
  </tr>`).join('')}</tbody></table>`;
}

async function accionProspecto(id, accion) {
  const endpointMap = { seguimiento: 'contactar', propuesta_enviada: 'propuesta-enviada' };
  const endpoint = endpointMap[accion] ?? accion;
  try {
    const res = await apiFetch(`${API}/prospectos/${id}/${endpoint}`,{method:'PATCH'});
    if(res.ok) await cargarProspectos();
  } catch {}
}

async function toggleInteresante(id, actual) {
  try {
    const res = await apiFetch(`${API}/prospectos/${id}/interesante`,{method:'PATCH'});
    if(res.ok) await cargarProspectos();
  } catch {}
}

async function guardarNota(id) {
  const nota = document.getElementById(`nota-input-${id}`).value;
  try {
    const res = await apiFetch(`${API}/prospectos/${id}/nota?nota=${encodeURIComponent(nota)}`,{method:'PATCH'});
    if(res.ok) {
      const ok = document.getElementById(`nota-ok-${id}`);
      ok.style.display='inline'; setTimeout(()=>ok.style.display='none',2000);
      // update local cache sin recargar toda la lista
      const p = todosProspectos.find(x=>x.id===id);
      if(p) p.nota = nota;
    }
  } catch {}
}

// ── BOLSA DE LEADS (ALIADO) ──────────────────────────────────────────────────

// ── Bolsa Canal 1: WhatsApp normalizado a formato internacional para wa.me ────
function _waNumeroBolsa(raw, pais) {
  let d = (raw || '').replace(/\D/g, '');
  if (!d) return '';
  if (d.indexOf('00') === 0) d = d.slice(2);
  const DIAL = { AR:'54', UY:'598', CL:'56', PY:'595', BO:'591', BR:'55', PE:'51', CO:'57', EC:'593', VE:'58', MX:'52', US:'1', ES:'34', CR:'506', PA:'507', GT:'502', HN:'504', SV:'503', NI:'505', DO:'1', CU:'53' };
  const code = DIAL[(pais || 'AR').toUpperCase()] || '54';
  if (d.indexOf(code) === 0) return d;
  d = d.replace(/^0+/, '');
  if (code === '54') { d = d.replace(/^15/, ''); return '549' + d; }
  return code + d;
}

// ── Bolsa Canal 1: bloque "Contactá ahora" adaptado al dato disponible ────────
function _contactoAccionesBolsa(l) {
  const wa   = _waNumeroBolsa(l.whatsapp || l.telefono, l.pais);
  const tel  = (l.telefono || l.whatsapp || '').replace(/[^\d+]/g, '');
  const mail = (l.email || '').trim();
  const nombre = l.nombre_contacto ? String(l.nombre_contacto).split(' ')[0] : '';
  const saludo = nombre ? ('Hola ' + nombre) : 'Hola';
  const empresa = l.empresa || 'tu empresa';
  const msg  = encodeURIComponent(saludo + ', te contacto de Avanza Digital por ' + empresa + '. ¿Tenés un minuto para una consulta rápida?');
  const subj = encodeURIComponent('Avanza Digital — ' + empresa);
  const body = encodeURIComponent(saludo + '. Te escribo de Avanza Digital: ayudamos a PYMEs como ' + empresa + ' a que su presencia digital genere clientes. ¿Tenés unos minutos esta semana?');
  const btns = [];
  if (wa)   btns.push('<a href="https://wa.me/' + wa + '?text=' + msg + '" target="_blank" rel="noopener" style="flex:1;min-width:110px;text-align:center;padding:9px;font-size:.82rem;font-weight:700;text-decoration:none;background:var(--green);color:#000;border-radius:8px;"><i class="fa-brands fa-whatsapp"></i> WhatsApp</a>');
  if (wa)   btns.push('<button onclick="abrirOutreach(\'bolsa\',' + l.id + ',\'' + wa + '\')" style="flex:1;min-width:110px;text-align:center;padding:9px;font-size:.82rem;font-weight:700;background:rgba(127,216,255,0.1);color:#7fd8ff;border:1px solid rgba(127,216,255,0.35);border-radius:8px;cursor:pointer;font-family:\'Inter\',sans-serif;"><i class="fa-solid fa-wand-magic-sparkles"></i> Mensaje IA</button>');
  if (tel)  btns.push('<a href="tel:' + tel + '" style="flex:1;min-width:90px;text-align:center;padding:9px;font-size:.82rem;font-weight:700;text-decoration:none;background:rgba(59,130,246,0.15);color:#60a5fa;border:1px solid rgba(59,130,246,0.3);border-radius:8px;"><i class="fa-solid fa-phone"></i> Llamar</a>');
  if (mail) btns.push('<a href="mailto:' + mail + '?subject=' + subj + '&body=' + body + '" style="flex:1;min-width:90px;text-align:center;padding:9px;font-size:.82rem;font-weight:700;text-decoration:none;background:rgba(255,255,255,0.06);color:var(--text-muted);border:1px solid var(--border);border-radius:8px;"><i class="fa-solid fa-envelope"></i> Email</a>');
  if (!btns.length) {
    return '<div style="margin-top:10px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.25);border-radius:8px;padding:10px 12px;font-size:.8rem;color:var(--amber);"><i class="fa-solid fa-circle-info"></i> Sin datos de contacto directos — buscá la empresa online para conseguir un canal.</div>';
  }
  const mejor = wa ? 'WhatsApp' : (l.telefono ? 'teléfono' : 'email');
  return '<div style="margin-top:12px;border-top:1px solid rgba(255,255,255,0.05);padding-top:12px;">' +
    '<div style="font-size:.72rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;"><i class="fa-solid fa-paper-plane"></i> Contactá ahora · mejor canal: <strong style="color:var(--green);">' + mejor + '</strong></div>' +
    '<div style="display:flex;gap:8px;flex-wrap:wrap;">' + btns.join('') + '</div></div>';
}

// Config visual + acción por estado de un lead reclamado, según el ciclo
// completo del reciclado (ver reciclado.py): reclamado (48h) -> contactado
// (exitoso, cierre) o nurture (cooldown, no cerrado) -> disponible (ya
// liberado a la bolsa general, pero este aliado conserva prioridad de
// reclamo sin límite de horas mientras nadie más lo tome).
function _estadoCfgBolsa(l) {
  const ULTIMO_RESULTADO_LABEL = {
    no_contesto: 'No contestó la última vez',
    no_interesado: 'No le interesó la última vez',
  };
  const notaUltimo = ULTIMO_RESULTADO_LABEL[l.ultimo_resultado] || '';

  if (l.estado === 'reclamado') {
    const colorReloj = l.horas_restantes < 12 ? 'var(--red)' : 'var(--amber)';
    return {
      borderColor: 'rgba(245,158,11,0.35)',
      badge: `<span style="background:rgba(245,158,11,0.15);color:${colorReloj};border:1px solid rgba(245,158,11,0.3);border-radius:20px;font-size:.7rem;font-weight:700;padding:3px 10px;"><i class="fa-solid fa-hourglass-half"></i> Quedan ${l.horas_restantes}h</span>`,
      accionBtn: `<button class="btn-green" style="width:100%;padding:9px;font-size:.82rem;" onclick="marcarContactadoBolsa(${l.id})"><i class="fa-solid fa-phone"></i> Ya lo contacté</button>`,
    };
  }

  if (l.estado === 'nurture') {
    const h = l.cooldown_horas_restantes;
    const texto = (h !== null && h !== undefined) ? `Vuelve a la bolsa general en ${h}h` : 'En pausa';
    return {
      borderColor: 'rgba(148,163,184,0.3)',
      badge: `<span style="background:rgba(148,163,184,0.12);color:#94a3b8;border:1px solid rgba(148,163,184,0.3);border-radius:20px;font-size:.7rem;font-weight:700;padding:3px 10px;"><i class="fa-solid fa-moon"></i> En pausa</span>`,
      accionBtn: `
        <div style="text-align:center;font-size:.74rem;color:var(--text-dim);margin-bottom:6px;line-height:1.5;">
          <i class="fa-solid fa-clock"></i> ${texto}${notaUltimo ? ' · ' + notaUltimo : ''}<br>
          Mientras tanto, sos el único que la puede reclamar.
        </div>
        <button class="btn-primary" style="width:100%;padding:9px;font-size:.82rem;font-weight:700;" onclick="reactivarLeadBolsa(${l.id}, '${(l.empresa||'').replace(/'/g, "\\'")}')"><i class="fa-solid fa-rotate-left"></i> ¿Te respondió? Reactivar</button>`,
    };
  }

  if (l.estado === 'disponible' && l.puede_reactivar) {
    return {
      borderColor: 'rgba(239,68,68,0.4)',
      badge: `<span style="background:rgba(239,68,68,0.12);color:var(--red);border:1px solid rgba(239,68,68,0.3);border-radius:20px;font-size:.7rem;font-weight:700;padding:3px 10px;"><i class="fa-solid fa-triangle-exclamation"></i> Volvió a la bolsa</span>`,
      accionBtn: `
        <div style="text-align:center;font-size:.74rem;color:var(--red);margin-bottom:6px;font-weight:700;line-height:1.5;">
          <i class="fa-solid fa-bolt"></i> Ya está visible para cualquier aliado${notaUltimo ? ' · ' + notaUltimo : ''}
        </div>
        <button class="btn-primary" style="width:100%;padding:9px;font-size:.82rem;font-weight:700;" onclick="reactivarLeadBolsa(${l.id}, '${(l.empresa||'').replace(/'/g, "\\'")}')"><i class="fa-solid fa-rotate-left"></i> Reactivar antes que otro lo tome</button>`,
    };
  }

  // 'contactado' (cierre exitoso) — estado final, sin acción pendiente.
  return {
    borderColor: 'var(--border)',
    badge: `<span style="background:rgba(59,130,246,0.12);color:#60a5fa;border:1px solid rgba(59,130,246,0.25);border-radius:20px;font-size:.7rem;font-weight:700;padding:3px 10px;"><i class="fa-solid fa-check"></i> Contactado</span>`,
    accionBtn: `<div style="text-align:center;font-size:.78rem;color:var(--text-dim);"><i class="fa-solid fa-check-double"></i> Gestionado</div>`,
  };
}

async function cargarBolsa() {
  if(!aliado) return;
  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/bolsa`);
    if(!res.ok) return;
    const data = await res.json();

    // Actualizar badge de límite
    const activos = data.reclamos_activos ?? 0;
    const limite  = data.limite_reclamos ?? 3;
    const badge   = document.getElementById('bolsa-limite-badge');
    const numEl   = document.getElementById('bolsa-limite-num');
    if(badge && numEl) {
      badge.style.display = 'block';
      numEl.textContent   = `${activos}/${limite}`;
      numEl.style.color   = activos >= limite ? 'var(--red)' : activos === limite - 1 ? 'var(--amber)' : 'var(--green)';
      badge.style.borderColor = activos >= limite ? 'rgba(239,68,68,0.4)' : 'var(--border)';
    }
    const limitAlcanzado = activos >= limite;

    // Hidratar score IA persistido en localStorage (los leads de bolsa
    // perfilan client-side; el backend no tiene ruta /bolsa/{id}/perfilar).
    if(Array.isArray(data.mis_reclamos)) {
      data.mis_reclamos.forEach(l => {
        const cached = _bolsaPerfiladoGet(l.id);
        if(cached) {
          l.score_ia = cached.score;
          l.plan_recomendado = cached.plan_recomendado;
          l.pitch_sugerido = cached.pitch_sugerido;
          l.tamano = l.tamano || cached.tamano;
          l.urgencia = l.urgencia || cached.urgencia;
        }
      });
    }

    // ── helpers de tarjeta ──────────────────────────────────────────
    const fila = (icono, label, valor, color='') =>
      valor ? `<div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:7px;">
        <span style="min-width:16px;color:var(--text-dim);font-size:.78rem;margin-top:1px;">${icono}</span>
        <div>
          <div style="font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);margin-bottom:1px;">${label}</div>
          <div style="font-size:.87rem;color:${color||'var(--text)'};font-weight:500;">${valor}</div>
        </div>
      </div>` : '';

    // 1. Renderizar Mis Reclamos — tarjetas
    const tbodyReclamos = document.getElementById('tabla-mis-reclamos');
    if(!data.mis_reclamos.length) {
      tbodyReclamos.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-dim);grid-column:1/-1;"><p>No tenés leads reclamados. ¡Aprovechá la bolsa!</p></div>';
    } else {
      tbodyReclamos.innerHTML = data.mis_reclamos.map(l => {
        const estadoCfg = _estadoCfgBolsa(l);
        const estadoBadge = estadoCfg.badge;
        const accionBtn = estadoCfg.accionBtn;
        return `<div style="background:var(--bg2);border:1px solid ${estadoCfg.borderColor};border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:4px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;">
            <div>
              <div style="font-size:1rem;font-weight:800;color:var(--text);line-height:1.2;">${l.empresa}</div>
              ${l.ciudad ? `<div style="font-size:.75rem;color:var(--text-dim);margin-top:2px;"><i class="fa-solid fa-location-dot"></i> ${l.ciudad}</div>` : ''}
            </div>
            ${estadoBadge}
          </div>
          ${fila('🏭','Rubro', l.rubro)}
          ${fila('👤','Contacto', l.nombre_contacto||'')}
          ${fila('📞','Teléfono', l.telefono||'')}
          ${fila('📱','WhatsApp', l.whatsapp && l.whatsapp !== l.telefono ? l.whatsapp : '', 'var(--green)')}
          ${fila('✉️','Email', l.email)}
          ${_contactoAccionesBolsa(l)}
          <div style="display:flex;gap:8px;margin-top:8px;">
            <button onclick="abrirHandoff(${l.id})" style="flex:1;background:rgba(124,58,237,.12);color:#c084fc;border:1px solid rgba(124,58,237,.3);border-radius:8px;padding:9px;font-size:.78rem;font-weight:700;cursor:pointer;"><i class="fa-solid fa-people-arrows"></i> Pasar a companero</button>
            <button onclick="prepararContinuidadDesdeLead(${l.id}, '${encodeURIComponent(l.empresa||'')}')" style="flex:1;background:rgba(34,197,94,.10);color:var(--green);border:1px solid rgba(34,197,94,.3);border-radius:8px;padding:9px;font-size:.78rem;font-weight:700;cursor:pointer;"><i class="fa-solid fa-handshake"></i> Cerrar (continuidad)</button>
          </div>
          <div style="display:flex;gap:16px;margin-top:8px;">
            <button onclick="verHistorialLead(${l.id})" style="background:none;border:none;color:var(--text-dim);font-size:.74rem;cursor:pointer;padding:0;">${(l.reciclados>0)?'♻ ':''}<i class="fa-solid fa-clock-rotate-left"></i> Ver historial</button>
            ${l.setter_id ? `<button onclick="verReparto(${l.id})" style="background:none;border:none;color:#c084fc;font-size:.74rem;cursor:pointer;padding:0;"><i class="fa-solid fa-scale-balanced"></i> Ver reparto</button>` : ''}
          </div>
          ${l.web ? `
          <div style="margin-top:6px;display:flex;align-items:center;gap:6px;">
            <span style="font-size:.8rem;color:var(--text-dim);">🌐</span>
            <a href="${l.web.startsWith('http') ? l.web : 'https://'+l.web}" target="_blank" rel="noopener"
               style="font-size:.82rem;color:var(--primary);text-decoration:underline;">Ver sitio web</a>
          </div>` : ''}
          ${l.instagram ? `
          <div style="margin-top:4px;display:flex;align-items:center;gap:6px;">
            <span style="font-size:.8rem;color:var(--text-dim);">📸</span>
            <span style="font-size:.82rem;color:var(--text-muted);">${l.instagram}</span>
          </div>` : ''}
          ${l.observacion ? `
          <div style="margin-top:8px;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:8px;padding:10px 12px;">
            <div style="font-size:.72rem;color:var(--text-dim);font-weight:700;margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em;">💬 Nota del prospectador</div>
            <div style="font-size:.82rem;color:var(--text-muted);line-height:1.5;">${l.observacion}</div>
          </div>` : ''}
          ${!l.web ? (() => {
            if (l.tiene_web && l.tiene_redes) {
              return `<div style="margin-top:8px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.25);border-radius:8px;padding:10px 12px;font-size:.8rem;color:var(--amber);line-height:1.5;">
                <i class="fa-solid fa-lightbulb"></i> Esta empresa tiene sitio web y redes sociales — buscala en Google antes de contactar para llegar mejor preparado.
              </div>`;
            } else if (l.tiene_web) {
              return `<div style="margin-top:8px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.25);border-radius:8px;padding:10px 12px;font-size:.8rem;color:var(--amber);line-height:1.5;">
                <i class="fa-solid fa-lightbulb"></i> Esta empresa tiene sitio web — buscala en Google antes de contactar.
              </div>`;
            } else if (l.tiene_redes) {
              return `<div style="margin-top:8px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.25);border-radius:8px;padding:10px 12px;font-size:.8rem;color:var(--amber);line-height:1.5;">
                <i class="fa-solid fa-lightbulb"></i> Esta empresa tiene redes sociales — buscala en Instagram o Facebook antes de contactar.
              </div>`;
            } else {
              return `<div style="margin-top:8px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.25);border-radius:8px;padding:10px 12px;font-size:.8rem;color:var(--amber);line-height:1.5;">
                <i class="fa-solid fa-magnifying-glass"></i> Buscá <strong>${l.empresa}</strong> en Google, LinkedIn o Instagram antes de contactar para llegar mejor preparado.
              </div>`;
            }
          })() : ''}
          ${l.score_ia && l.score_ia > 0 ? `
          <div style="margin-top:10px;background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.25);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:4px;">
            <strong style="color:#c084fc;font-size:.82rem;"><i class="fa-solid fa-brain"></i> Score IA: ${l.score_ia}/100</strong>
            ${l.plan_recomendado ? `<span style="color:var(--text-muted);font-size:.75rem;">Plan sugerido: <strong style="color:var(--text);">${l.plan_recomendado}</strong></span>` : ''}
            ${l.pitch_sugerido ? `<details style="margin-top:4px;"><summary style="cursor:pointer;color:var(--text-muted);font-size:.78rem;">Ver pitch sugerido</summary><pre style="white-space:pre-wrap;margin-top:8px;padding:10px;background:rgba(0,0,0,0.3);border-radius:6px;font-family:inherit;font-size:.8rem;color:var(--text);">${l.pitch_sugerido}</pre></details>` : ''}
          </div>` : `
          <div style="margin-top:10px;">
            <button onclick="abrirPerfiladoBolsa(${l.id},'${(l.empresa||'').replace(/'/g,"\\'")}','${(l.rubro||'').replace(/'/g,"\\'")}')" style="width:100%;background:rgba(192,132,252,0.12);color:#c084fc;border:1px solid rgba(192,132,252,0.3);border-radius:8px;padding:8px 12px;font-size:.8rem;font-weight:700;cursor:pointer;"><i class="fa-solid fa-brain"></i> Perfilar con IA — Score + Pitch</button>
          </div>`}
          <div style="margin-top:10px;display:flex;flex-direction:column;gap:8px;">
            ${l.prospecto_id
              ? `<button onclick="cambiarTabDesdeNBA('pipeline')" style="width:100%;background:rgba(74,222,128,0.10);color:var(--green);border:1px solid rgba(74,222,128,0.35);border-radius:8px;padding:9px;font-size:.8rem;font-weight:800;cursor:pointer;"><i class="fa-solid fa-bars-progress"></i> Ya está en Mi CRM — Ver pipeline →</button>`
              : `<button onclick="convertirLeadEnProspecto(${l.id}, '${(l.empresa||'').replace(/'/g,"\\'")}')" style="width:100%;background:rgba(59,130,246,0.10);color:#60a5fa;border:1px solid rgba(59,130,246,0.35);border-radius:8px;padding:9px;font-size:.8rem;font-weight:800;cursor:pointer;"><i class="fa-solid fa-arrow-right-to-bracket"></i> Convertir en prospecto (Mi CRM)</button>`}
            ${accionBtn}
          </div>
        </div>`;
      }).join('');
    }

    // 2. Renderizar Disponibles — tarjetas
    const tbodyDisp = document.getElementById('tabla-bolsa-disponibles');
    // Actualizar contador en la tarjeta-categoría "Básicos GRATIS"
    if (typeof _setContadorCategoria === 'function') {
      _setContadorCategoria('basico', data.disponibles.length);
    }
    if(!data.disponibles.length) {
      tbodyDisp.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-dim);grid-column:1/-1;"><p>No hay leads disponibles en este momento. Volvé más tarde.</p></div>';
    } else {
      // Extraer países únicos
      const paisesUnicos = [...new Set(data.disponibles.map(l => l.pais || 'AR'))];
      let filtroActivoPais = '';

      function renderDisponibles() {
        const q = (document.getElementById('bolsa-buscar-basico')?.value || '').trim();
        let filtrados = filtroActivoPais
          ? data.disponibles.filter(l => (l.pais || 'AR') === filtroActivoPais)
          : data.disponibles;
        filtrados = filtrados.filter(l => _matchLeadTexto(l, q));
        if (!filtrados.length) {
          tbodyDisp.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-dim);grid-column:1/-1;">Sin resultados para esa búsqueda.</div>';
          return;
        }

        tbodyDisp.innerHTML = filtrados.map(l => {
          const accion = limitAlcanzado
            ? `<button disabled style="width:100%;padding:10px;font-size:.82rem;opacity:.45;cursor:not-allowed;background:var(--border);color:var(--text-dim);border:none;border-radius:8px;font-weight:700;"><i class="fa-solid fa-lock"></i> Límite alcanzado (3/3)</button>`
            : `<button class="btn-primary" style="width:100%;padding:10px;font-size:.82rem;font-weight:800;display:flex;align-items:center;justify-content:center;gap:6px;" onclick="reclamarLead(${l.id})"><i class="fa-solid fa-hand-sparkles"></i> Reclamar gratis</button>`;
          return renderLeadCard(l, { accion, mostrarCosto: false });
        }).join('');
      }

      // Renderizar botones de filtro solo si hay más de un país
      const filtroContainer = document.getElementById('bolsa-filtro-paises');
      if(filtroContainer) filtroContainer.remove();
      if(paisesUnicos.length > 1) {
        const fc = document.createElement('div');
        fc.id = 'bolsa-filtro-paises';
        fc.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;padding:0 16px;';
        const allBtn = document.createElement('button');
        allBtn.textContent = '🌎 Todos';
        allBtn.style.cssText = 'padding:6px 14px;border-radius:20px;border:1px solid var(--border);background:var(--primary);color:#fff;font-size:.8rem;cursor:pointer;font-weight:700;';
        allBtn.onclick = () => {
          filtroActivoPais = '';
          [...fc.querySelectorAll('button')].forEach(b => b.style.background = 'transparent');
          [...fc.querySelectorAll('button')].forEach(b => b.style.color = 'var(--text-muted)');
          allBtn.style.background = 'var(--primary)'; allBtn.style.color = '#fff';
          renderDisponibles();
        };
        fc.appendChild(allBtn);
        paisesUnicos.forEach(p => {
          const pi = paisInfo(p);
          const btn = document.createElement('button');
          btn.textContent = `${pi.bandera} ${pi.nombre}`;
          btn.style.cssText = 'padding:6px 14px;border-radius:20px;border:1px solid var(--border);background:transparent;color:var(--text-muted);font-size:.8rem;cursor:pointer;';
          btn.onclick = () => {
            filtroActivoPais = p;
            [...fc.querySelectorAll('button')].forEach(b => { b.style.background='transparent'; b.style.color='var(--text-muted)'; });
            btn.style.background = 'var(--primary)'; btn.style.color = '#fff';
            renderDisponibles();
          };
          fc.appendChild(btn);
        });
        tbodyDisp.before(fc);
      }

      window._repintarBolsa['basico'] = renderDisponibles;
      renderDisponibles();
    }

    // Banner de límite alcanzado
    const bannerExist = document.getElementById('bolsa-banner-limite');
    if(limitAlcanzado && !bannerExist) {
      const banner = document.createElement('div');
      banner.id = 'bolsa-banner-limite';
      banner.style.cssText = 'background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:8px;padding:14px 18px;margin-bottom:16px;font-size:.85rem;color:#f87171;display:flex;align-items:center;gap:10px;';
      banner.innerHTML = `<i class="fa-solid fa-hand" style="font-size:1.1rem;"></i> <span><strong>Límite alcanzado:</strong> Tenés 3 leads reclamados activos. Marcá al menos uno como "Ya lo contacté" para poder reclamar más.</span>`;
      tbodyDisp.closest('.table-wrap').before(banner);
    } else if(!limitAlcanzado && bannerExist) {
      bannerExist.remove();
    }

  } catch(e) { console.error(e); }
}

// ─── MODAL DE CONFIRMACIÓN REUTILIZABLE ──────────────────────────────────
// Reemplaza al confirm() nativo del navegador por el modal con el estilo del
// portal. Devuelve una Promise<boolean>: true si confirma, false si cancela.
function confirmarModal({ titulo, cuerpoHTML = '', textoOk = 'Confirmar', textoCancel = 'Cancelar', icono = '⏰' }) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:10000;display:flex;align-items:center;justify-content:center;';
    overlay.innerHTML = `
      <div style="background:#1e293b;border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:32px;max-width:440px;width:90%;text-align:center;">
        <div style="font-size:2.2rem;margin-bottom:12px;">${icono}</div>
        <h3 style="margin-bottom:8px;font-size:1.15rem;">${titulo}</h3>
        ${cuerpoHTML}
        <div style="display:flex;gap:10px;margin-top:16px;">
          <button data-rol="cancelar" style="flex:1;padding:12px;border-radius:10px;border:1px solid rgba(255,255,255,0.1);background:transparent;color:#94a3b8;font-weight:700;cursor:pointer;">${textoCancel}</button>
          <button data-rol="ok" style="flex:1;padding:12px;border-radius:10px;border:none;background:#f97316;color:#000;font-weight:800;cursor:pointer;">${textoOk}</button>
        </div>
      </div>
    `;
    const cerrar = (valor) => { overlay.remove(); resolve(valor); };
    overlay.querySelector('[data-rol="ok"]').onclick = () => cerrar(true);
    overlay.querySelector('[data-rol="cancelar"]').onclick = () => cerrar(false);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cerrar(false); });
    document.body.appendChild(overlay);
  });
}

// ─── CRM BRIDGE: convertir un lead reclamado en prospecto del CRM ─────────
async function convertirLeadEnProspecto(id, empresa) {
  const ok = await confirmarModal({
    icono: '➡️',
    titulo: `¿Pasar "${empresa}" a Mi CRM como prospecto?`,
    textoOk: 'Sí, pasar a Mi CRM',
    cuerpoHTML: `
      <div style="background:rgba(96,165,250,0.1);border:1px solid rgba(96,165,250,0.3);border-radius:10px;padding:16px;margin:16px 0;text-align:left;">
        <ul style="margin:0;padding-left:18px;color:#cbd5e1;font-size:.85rem;line-height:1.8;">
          <li>Se copian todos los datos: <strong>contacto, teléfono, email, rubro y observaciones</strong>.</li>
          <li>El lead queda gestionado en la bolsa.</li>
          <li>Después lo trabajás con <strong>etapas, notas y tareas</strong> desde la pestaña "Mi CRM".</li>
        </ul>
      </div>`
  });
  if (!ok) return;
  try {
    const res = await apiFetch(`${API}/bolsa/${id}/convertir-prospecto`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      mostrarToast(typeof data.detail === 'string' ? data.detail : (data.detail?.mensaje || 'Error al convertir.'), 'red');
      return;
    }
    mostrarToast(data.ya_existia ? 'ℹ️ Este lead ya estaba en tu CRM' : `✅ ${empresa} agregado a Mi CRM`, 'green');
    await cargarBolsa();   // refresca la tarjeta: ahora muestra "Ver pipeline →"
    const abrir = await confirmarModal({
      icono: '🗂️',
      titulo: '¿Abrir Mi CRM ahora?',
      cuerpoHTML: `<p style="margin:8px 0 0;color:#cbd5e1;font-size:.9rem;">Para trabajarlo con etapa, notas y próxima tarea.</p>`,
      textoOk: 'Abrir Mi CRM',
      textoCancel: 'Más tarde'
    });
    if (abrir) cambiarTabDesdeNBA('pipeline');
  } catch (e) { mostrarToast('Error de conexión.', 'red'); }
}

async function reclamarLead(id) {
  // Modal de confirmación con aviso claro de las 48hs
  const overlay = document.createElement('div');
  overlay.id = 'modal-reclamar';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:9999;display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = `
    <div style="background:#1e293b;border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:32px;max-width:420px;width:90%;text-align:center;">
      <div style="font-size:2.2rem;margin-bottom:12px;">⏰</div>
      <h3 style="margin-bottom:8px;font-size:1.15rem;">¿Reclamar este lead?</h3>
      <div style="background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:10px;padding:16px;margin:16px 0;text-align:left;">
        <p style="margin:0 0 8px;color:#f59e0b;font-weight:700;font-size:.9rem;">⚠️ Importante antes de reclamar:</p>
        <ul style="margin:0;padding-left:18px;color:#cbd5e1;font-size:.85rem;line-height:1.8;">
          <li>El contador de <strong style="color:#f59e0b;">48 horas empieza ahora</strong>, no cuando lo contactes.</li>
          <li>Si no marcás el lead como <strong>"Contactado"</strong> dentro de ese tiempo, vuelve automáticamente a la bolsa.</li>
          <li>Reclamá solo cuando estés lista para contactarlo pronto.</li>
        </ul>
      </div>
      <div style="display:flex;gap:10px;margin-top:8px;">
        <button onclick="document.getElementById('modal-reclamar').remove()" style="flex:1;padding:12px;border-radius:10px;border:1px solid rgba(255,255,255,0.1);background:transparent;color:#94a3b8;font-weight:700;cursor:pointer;">Cancelar</button>
        <button id="btn-confirmar-reclamo" style="flex:1;padding:12px;border-radius:10px;border:none;background:#f97316;color:#000;font-weight:800;cursor:pointer;">Sí, reclamar ahora</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  document.getElementById('btn-confirmar-reclamo').onclick = async () => {
    document.getElementById('modal-reclamar').remove();
    try {
      const res = await apiFetch(`${API}/bolsa/${id}/reclamar?codigo_aliado=${aliado.codigo}`, { method: 'POST' });
      const data = await res.json();
      if(res.ok) {
        mostrarToast('¡Lead reclamado! Tenés 48hs para contactarlo — el contador ya empezó.', 'green');
        cargarBolsa();
      } else {
        mostrarToast(data.detail || 'Error al reclamar', 'red');
      }
    } catch(e) { mostrarToast('Error de conexión', 'red'); }
  };
}

async function marcarContactadoBolsa(id, empresa) {
  // Mostrar modal de calificación
  const overlay = document.createElement('div');
  overlay.id = 'modal-resultado';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = `
    <div style="background:#1e293b;border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:32px;max-width:400px;width:90%;text-align:center;">
      <div style="font-size:2rem;margin-bottom:12px;">📋</div>
      <h3 style="margin-bottom:6px;font-size:1.1rem;">¿Cómo fue el contacto?</h3>
      <p style="font-size:.83rem;color:var(--text-muted);margin-bottom:24px;">Calificá a <strong>${empresa}</strong> para que podamos mejorar la calidad de los leads.</p>
      <div style="display:flex;flex-direction:column;gap:10px;">
        <button onclick="confirmarResultado(${id},'exitoso')" style="padding:12px;border-radius:10px;border:none;background:rgba(16,185,129,0.15);border:1px solid rgba(16,185,129,0.3);color:#10b981;font-weight:700;cursor:pointer;font-size:.9rem;">
          ✅ Exitoso — Está interesado / quedamos en hablar
          <span style="display:block;font-size:.7rem;font-weight:600;color:#6ee7b7;margin-top:3px;">Pasa automáticamente a Mi CRM como prospecto</span>
        </button>
        <button onclick="confirmarResultado(${id},'no_interesado')" style="padding:12px;border-radius:10px;border:none;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.2);color:#f87171;font-weight:700;cursor:pointer;font-size:.9rem;">
          ❌ No le interesó
        </button>
        <button onclick="confirmarResultado(${id},'no_contesto')" style="padding:12px;border-radius:10px;border:none;background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.2);color:#f59e0b;font-weight:700;cursor:pointer;font-size:.9rem;">
          📵 No contestó
        </button>
      </div>
      <button onclick="document.getElementById('modal-resultado').remove()" style="margin-top:16px;background:transparent;border:none;color:var(--text-dim);cursor:pointer;font-size:.8rem;">Cancelar</button>
    </div>
  `;
  document.body.appendChild(overlay);
}

async function confirmarResultado(id, resultado) {
  const modal = document.getElementById('modal-resultado');
  if(modal) modal.remove();
  try {
    const res = await apiFetch(`${API}/bolsa/${id}/contactar?codigo_aliado=${aliado.codigo}&resultado=${resultado}`, { method: 'PATCH' });
    const data = await res.json();
    if(res.ok) {
      const colores = { exitoso: 'green', no_interesado: 'red', no_contesto: 'amber' };
      mostrarToast(data.mensaje, colores[resultado] || 'green');
      cargarBolsa();
      cargarHistorialBolsa();
      if (data.convertido_a_crm) {
        // El exitoso ya está en Mi CRM — ofrecer ir a trabajarlo ahí.
        setTimeout(() => {
          if (confirm('Este lead ya está en Mi CRM como prospecto. ¿Abrirlo ahora para definir la próxima acción?')) {
            cambiarTabDesdeNBA('pipeline');
          }
        }, 400);
      }
    } else {
      mostrarToast(data.detail || 'Error al actualizar', 'red');
    }
  } catch(e) { mostrarToast('Error de conexión', 'red'); }
}

async function reactivarLeadBolsa(id, empresa) {
  // El aliado recupera prioridad sobre un lead en pausa ('nurture') o ya
  // liberado a la bolsa general ('disponible') porque el cliente respondió
  // tarde. Sin límite de horas — ver bolsa.py /bolsa/{id}/reactivar.
  const ok = confirm(`¿Reactivar a ${empresa || 'este lead'}? Vuelve a tus reclamos activos y arranca de nuevo el plazo de 48hs para marcarlo como contactado.`);
  if (!ok) return;
  try {
    const res = await apiFetch(`${API}/bolsa/${id}/reactivar`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      mostrarToast(data.mensaje || '¡Lead reactivado!', 'green');
      cargarBolsa();
    } else {
      mostrarToast(data.detail || 'No se pudo reactivar — puede que ya lo haya tomado otro aliado.', 'red');
      cargarBolsa();
    }
  } catch(e) { mostrarToast('Error de conexión', 'red'); }
}


async function cargarHistorialBolsa() {
  if(!aliado) return;
  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/historial-bolsa`);
    if(!res.ok) return;
    const data = await res.json();
    const s = data.stats;

    // KPIs
    const kpis = document.getElementById('historial-kpis');
    if(kpis) kpis.innerHTML = `
      <div class="metric-card"><div class="metric-label">Total reclamados</div><div class="metric-value blue">${s.total_reclamados}</div></div>
      <div class="metric-card highlight"><div class="metric-label">Exitosos</div><div class="metric-value mv-green">${s.exitosos}</div></div>
      <div class="metric-card"><div class="metric-label">No interesados</div><div class="metric-value" style="color:var(--red);">${s.no_interesados}</div></div>
      <div class="metric-card"><div class="metric-label">No contestaron</div><div class="metric-value" style="color:var(--amber);">${s.no_contestaron}</div></div>
      <div class="metric-card"><div class="metric-label">Tasa de éxito</div><div class="metric-value mv-purple">${s.tasa_exito}%</div></div>
    `;

    // Tabla
    const tbody = document.getElementById('historial-bolsa-body');
    if(!tbody) return;
    if(!data.leads.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--text-dim);">Todavía no reclamaste ningún lead.</td></tr>';
      return;
    }
    const iconRes = { exitoso:'✅', no_interesado:'❌', no_contesto:'📵' };
    const labelRes = { exitoso:'Exitoso', no_interesado:'No interesado', no_contesto:'No contestó' };
    tbody.innerHTML = data.leads.map(l => `
      <tr style="border-bottom:1px solid var(--border);">
        <td style="font-weight:700;">${l.empresa}</td>
        <td style="color:var(--text-muted);">${l.rubro}</td>
        <td style="font-size:.8rem;color:var(--text-dim);">${l.fecha_reclamo || '—'}</td>
        <td>${l.estado === 'reclamado' ? '<span class="badge badge-amber">Activo</span>' : '<span class="badge badge-blue">Cerrado</span>'}</td>
        <td>${l.resultado ? `${iconRes[l.resultado]||''} ${labelRes[l.resultado]||l.resultado}` : '<span style="color:var(--text-dim);">—</span>'}</td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

// Funciones de MI RED
// Lista de invitados de la red (para el botón de seguimiento por fila).
let _redInvitados = [];
// Lista global de módulos activos de la Academia (id/orden/título), para
// renderizar el checklist por sub-aliado sin tener que volver a pedirla.
let _redAcademiaModulos = [];

// Abre WhatsApp con un mensaje pre-armado según el estado del invitado.
// Si no hay WhatsApp en su ficha, copia el mensaje al portapapeles.
function seguirInvitadoRed(i) {
  const inv = (_redInvitados && _redInvitados[i]) ? _redInvitados[i] : null;
  if (!inv) return;
  const num = (inv.whatsapp || '').replace(/\D/g, '');
  if (num) {
    window.open(`https://wa.me/${num}?text=${encodeURIComponent(inv.msg)}`, '_blank');
    return;
  }
  // Sin número en ficha → fallback a copiar.
  const ok = () => mostrarToast('Sin WhatsApp en su ficha. Copié el mensaje para que se lo mandes.', 'amber');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(inv.msg).then(ok).catch(() => {
      const ta = document.createElement('textarea');
      ta.value = inv.msg; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (_) {}
      document.body.removeChild(ta); ok();
    });
  } else {
    const ta = document.createElement('textarea');
    ta.value = inv.msg; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (_) {}
    document.body.removeChild(ta); ok();
  }
}

// Pinta el mini gráfico de barras con la tendencia de ventas de la red
// (últimos 6 meses, viene del backend ya armado mes por mes).
function pintarHistoricoRed(historico) {
  const cont = document.getElementById('red-historico');
  if (!cont) return;
  if (!historico || !historico.length) {
    cont.innerHTML = `<div style="margin:auto;color:var(--text-dim);font-size:.8rem;">Sin datos todavía.</div>`;
    return;
  }
  const max = Math.max(1, ...historico.map(m => m.ventas || 0));
  cont.innerHTML = historico.map(m => {
    const alturaPct = Math.round(((m.ventas || 0) / max) * 100);
    const alturaPx = Math.max(4, Math.round(alturaPct * 0.9)); // mínimo visible aunque sea 0
    const activo = (m.ventas || 0) > 0;
    return `
      <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;gap:6px;" title="${m.ventas} venta(s) · USD ${(m.ganancia||0).toLocaleString()}">
        <div style="font-size:.7rem;font-weight:800;color:${activo ? 'var(--green)' : 'var(--text-dim)'};">${m.ventas || 0}</div>
        <div style="width:60%;min-width:18px;height:${alturaPx}px;border-radius:6px 6px 2px 2px;background:${activo ? 'var(--green)' : 'rgba(255,255,255,0.08)'};"></div>
        <div style="font-size:.68rem;color:var(--text-dim);text-transform:uppercase;">${m.label}</div>
      </div>`;
  }).join('');
}

// Abre el modal con el checklist módulo por módulo (01 a 07) de Academia para
// un sub-aliado puntual, leído de _redInvitados[i].academia — así el sponsor
// ve exactamente qué le falta en vez de adivinar a partir de un conteo.
function abrirModalAcademiaSub(i) {
  const inv = (_redInvitados && _redInvitados[i]) ? _redInvitados[i] : null;
  if (!inv) return;
  document.getElementById('academia-sub-nombre').textContent = inv.nombre;
  const completados = new Set(inv.academiaCompletados || []);
  const lista = document.getElementById('academia-sub-lista');
  if (!_redAcademiaModulos.length) {
    lista.innerHTML = `<div style="color:var(--text-dim);font-size:.85rem;">Todavía no hay módulos cargados en la Academia.</div>`;
  } else {
    lista.innerHTML = _redAcademiaModulos.map(mod => {
      const hecho = completados.has(mod.id);
      return `
        <div style="display:flex;align-items:center;gap:10px;background:rgba(0,0,0,0.3);border:1px solid ${hecho ? 'rgba(74,222,128,0.3)' : 'var(--border)'};border-radius:8px;padding:10px 12px;">
          <i class="fa-solid ${hecho ? 'fa-circle-check' : 'fa-circle'}" style="color:${hecho ? 'var(--green)' : 'var(--text-dim)'};font-size:1rem;"></i>
          <div style="flex:1;">
            <div style="font-size:.82rem;font-weight:700;color:${hecho ? 'var(--text)' : 'var(--text-muted)'};">Módulo ${String(mod.orden).padStart(2,'0')} — ${mod.titulo}</div>
          </div>
          <span style="font-size:.68rem;font-weight:800;color:${hecho ? 'var(--green)' : 'var(--text-dim)'};text-transform:uppercase;">${hecho ? 'Hecho' : 'Pendiente'}</span>
        </div>`;
    }).join('');
  }
  document.getElementById('modal-academia-sub').classList.add('open');
}


// Mientras el aliado está parado en la solapa Mi Red, repedimos los datos solos
// cada AUTO_REFRESH_RED_MS para que vea la activación de un invitado (ej. cuando
// un referido entra por primera vez y su contador pasa de 0 a 1) SIN apretar F5.
// Arranca al entrar a la solapa y se frena al salir (lo dispara cambiarTab). Si la
// pestaña del navegador no está visible, salteamos el pedido para no gastar
// requests al pedo; al volver, el próximo ciclo lo trae igual.
const AUTO_REFRESH_RED_MS = 45000; // 45 segundos
let _redPollTimer = null;

function iniciarAutoRefreshRed() {
  detenerAutoRefreshRed(); // nunca dejar dos timers corriendo a la vez
  _redPollTimer = setInterval(() => {
    // Si el aliado ya no está en la solapa Mi Red, frenamos por las dudas.
    const panelRed = document.getElementById('tab-red');
    if (!panelRed || !panelRed.classList.contains('active')) {
      detenerAutoRefreshRed();
      return;
    }
    // Pestaña del navegador en segundo plano → no pedimos nada todavía.
    if (document.hidden) return;
    cargarRed();
  }, AUTO_REFRESH_RED_MS);
}

function detenerAutoRefreshRed() {
  if (_redPollTimer) {
    clearInterval(_redPollTimer);
    _redPollTimer = null;
  }
}

// Tabla de niveles de override pasivo según ventas del sub-aliado.
// Sube solo, nunca baja (mismo criterio que las comisiones directas BASIC→ELITE).
// Si el backend ya manda `sub.override_pct` calculado, se respeta ese valor;
// esto es el fallback para no romper si todavía no se actualizó la API.
const RED_OVERRIDE_TIERS = [
  { min: 5, pct: 12 },
  { min: 3, pct: 10 },
  { min: 1, pct: 7 },
  { min: 0, pct: 5 }
];
function calcularOverridePct(ventasCount) {
  const v = ventasCount || 0;
  const tier = RED_OVERRIDE_TIERS.find(t => v >= t.min);
  return tier ? tier.pct : 5;
}

async function cargarRed() {
  if(!aliado) return;
  cargarInvitacionRed();

  // Poblar link de reclutamiento
  const linkRedEl = document.getElementById('red-link-reclutamiento');
  if (linkRedEl && aliado.ref_code) {
    linkRedEl.textContent = `https://avanzadigital.digital/alianzas?ref=${aliado.ref_code}`;
  }

  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/red`);
    if (res.status === 403) {
      // Defensa: Mi Red es exclusivo de Canal 1. La pestaña ya está oculta para
      // Canal 2 (ver configurarCanal), pero si se llega igual (deep link, caché
      // vieja), mostramos un mensaje en vez de dejar todo en blanco.
      const tbodyForbidden = document.getElementById('tabla-red');
      if (tbodyForbidden) {
        tbodyForbidden.innerHTML = `<div class="empty-state"><i class="fa-solid fa-lock"></i><p>Mi Red no está disponible para tu canal.</p></div>`;
      }
      return;
    }
    if(!res.ok) return;
    const data = await res.json();
    
    document.getElementById('red-equipo-count').textContent = data.total_sub_aliados;
    document.getElementById('red-ingresos-total').textContent = `USD ${data.total_ganancia_pasiva.toLocaleString()}`;

    // Override promedio: en qué tier está parada la red en general, de un vistazo.
    const overrideProm = document.getElementById('red-override-promedio');
    if (overrideProm) {
      overrideProm.textContent = (data.total_sub_aliados > 0 && data.override_promedio != null)
        ? `${data.override_promedio}%`
        : '—';
    }

    // Embudo: contamos estados directamente del detalle (no requiere backend nuevo).
    const conteoEstados = { sin_activar: 0, activado_sin_vender: 0, inactivo: 0, vendiendo: 0 };
    (data.detalle || []).forEach(sub => {
      if (conteoEstados.hasOwnProperty(sub.estado)) conteoEstados[sub.estado]++;
    });
    document.querySelectorAll('#red-embudo .red-embudo-etapa').forEach(el => {
      const estado = el.getAttribute('data-estado');
      const valorEl = el.querySelector('[data-valor]');
      if (valorEl) valorEl.textContent = conteoEstados[estado] || 0;
    });

    // Histórico mensual ─ mini gráfico de barras con la tendencia de ventas de la red.
    pintarHistoricoRed(data.historico_mensual || []);

    // Reclutamiento: usa data.reclutamiento si el backend ya lo manda; si no, deja "—"
    // en vez de mostrar un cero falso (evita que el aliado piense que el link no funciona).
    const rec = data.reclutamiento || null;
    document.getElementById('red-clics').textContent = rec ? (rec.clics ?? 0).toLocaleString() : '—';
    document.getElementById('red-registros').textContent = rec ? (rec.registros ?? 0).toLocaleString() : '—';
    document.getElementById('red-conversion').textContent = (rec && rec.clics)
      ? `${Math.round((rec.registros / rec.clics) * 100)}%`
      : '—';
    const activacionEl = document.getElementById('red-activacion');
    if (activacionEl) {
      activacionEl.textContent = (rec && rec.tasa_activacion != null) ? `${rec.tasa_activacion}%` : '—';
    }

    // Módulos de Academia (lista global ordenada), para el checklist por sub-aliado.
    _redAcademiaModulos = data.academia_modulos || [];

    const tbody = document.getElementById('tabla-red');
    if(data.total_sub_aliados === 0) {
      tbody.innerHTML = `<div class="empty-state"><i class="fa-solid fa-user-group"></i><p>Tu red está vacía.<br>Copiá tu <strong>link de reclutamiento</strong> de arriba y compartílo con vendedores, agencias o colegas que quieran sumarse como aliados.</p></div>`;
      return;
    }

    // Estado de cada invitado → presentación, acción y mensaje de seguimiento.
    const PORTAL_LOGIN = 'https://avanzadigital.digital/avanza-portal/portal.html';
    const ESTADO_RED = {
      sin_activar: {
        label: 'Sin activar', bg: 'rgba(239,68,68,0.12)', fg: '#ef4444',
        hint: 'Se registró pero nunca ingresó al portal — escribile para que haga su primer login y se te acrediten los créditos por activación.',
        cta: 'Recordarle',
        msg: n => `Hola ${n}! Te sumaste a Avanza Digital con mi link pero todavía no ingresaste al portal. Entrá para activar tu cuenta y arrancar — ya tenés leads cargados esperándote: ${PORTAL_LOGIN} . Cualquier duda, escribime.`
      },
      activado_sin_vender: {
        label: 'Activado', bg: 'rgba(245,158,11,0.14)', fg: 'var(--amber)',
        hint: 'Ya ingresó al portal pero todavía no registró ventas — dale una mano con su primer cierre.',
        cta: 'Impulsar',
        msg: n => `Hola ${n}! Vi que ya entraste al portal de Avanza. ¿Te doy una mano para arrancar con tu primer cierre? Tenés leads listos para contactar — decime y lo vemos juntos.`
      },
      inactivo: {
        label: 'Inactivo', bg: 'rgba(148,163,184,0.16)', fg: '#94a3b8',
        hint: 'Ingresó alguna vez pero hace más de 7 días que no vuelve — reactivalo antes de que se enfríe del todo.',
        cta: 'Reactivar',
        msg: n => `Hola ${n}! Hace unos días que no entrás al portal de Avanza. Tenés leads esperándote — entrá un rato y, si necesitás una mano para retomar, avisame: ${PORTAL_LOGIN}`
      },
      vendiendo: {
        label: 'Vendiendo', bg: 'rgba(34,197,94,0.14)', fg: 'var(--green)',
        hint: 'Activo y generando ventas. Tu % pasivo corre y sube con cada venta que cierre.',
        cta: 'Saludar',
        msg: n => `Hola ${n}! Vengo siguiendo tus ventas en Avanza, vas muy bien. ¿Necesitás algo para seguir cerrando? Acá estoy.`
      }
    };

    const resumen = `${data.activados || 0} de ${data.total_sub_aliados} activos (7 días) · ${data.vendiendo || 0} vendiendo`;

    _redInvitados = [];
    const filas = data.detalle.map((sub, i) => {
      const e = ESTADO_RED[sub.estado] || ESTADO_RED.sin_activar;
      const logins = sub.cantidad_logins || 0;
      const loginsTxt = logins > 0
        ? `${logins}<span style="font-size:.72rem;font-weight:400;color:var(--text-muted);"> ${logins === 1 ? 'vez' : 'veces'}</span>`
        : `<span style="color:#ef4444;font-weight:700;">0</span>`;
      const ultimo = (sub.ultimo_login && sub.ultimo_login !== 'Nunca')
        ? sub.ultimo_login
        : `<span style="color:#ef4444;">Nunca</span>`;
      _redInvitados.push({ nombre: sub.nombre, whatsapp: sub.whatsapp || '', msg: e.msg(sub.nombre), academiaCompletados: sub.academia_modulos_completados || [] });
      const ventasCount = sub.ventas_count || 0;
      const overridePct = (sub.override_pct != null) ? sub.override_pct : calcularOverridePct(ventasCount);
      const academiaTxt = (sub.academia_completados != null && sub.academia_total)
        ? `${sub.academia_completados}/${sub.academia_total}`
        : '<span style="color:var(--text-dim);">—</span>';
      const academiaCelda = (sub.academia_total)
        ? `<button onclick="abrirModalAcademiaSub(${i})" style="background:transparent;border:1px solid var(--border);border-radius:20px;padding:3px 10px;font-size:.78rem;font-weight:700;color:var(--text);cursor:pointer;">${academiaTxt} <i class="fa-solid fa-chevron-right" style="font-size:.6rem;color:var(--text-dim);"></i></button>`
        : academiaTxt;
      return `
        <tr title="${e.hint}">
          <td style="font-weight:700;">${sub.nombre}<div style="font-size:.72rem;font-weight:400;color:var(--text-muted);">${sub.ciudad}</div></td>
          <td><span style="background:${e.bg};color:${e.fg};border:1px solid ${e.fg};padding:3px 9px;border-radius:20px;font-size:.72rem;font-weight:700;white-space:nowrap;">${e.label}</span></td>
          <td style="font-size:.8rem;color:var(--text-dim);">${sub.fecha_ingreso}</td>
          <td style="font-weight:700;color:var(--text);">${loginsTxt}</td>
          <td style="font-size:.8rem;color:var(--text-dim);">${ultimo}</td>
          <td style="font-weight:700;color:var(--text);text-align:center;">${ventasCount}</td>
          <td style="font-weight:800;color:#c084fc;text-align:center;">${overridePct}%</td>
          <td style="font-size:.78rem;color:var(--text-dim);text-align:center;">${academiaCelda}</td>
          <td style="color:var(--green);font-weight:800;">USD ${(sub.ganancia_pasiva || 0).toLocaleString()}</td>
          <td><button onclick="seguirInvitadoRed(${i})" style="display:inline-flex;align-items:center;gap:6px;background:rgba(37,211,102,0.12);color:#25D366;border:1px solid rgba(37,211,102,0.4);border-radius:8px;padding:6px 12px;font-size:.76rem;font-weight:700;cursor:pointer;white-space:nowrap;"><i class="fa-brands fa-whatsapp"></i> ${e.cta}</button></td>
        </tr>`;
    }).join('');

    tbody.innerHTML = `
      <div style="display:flex;flex-wrap:wrap;justify-content:center;gap:14px;align-items:center;margin-bottom:12px;font-size:.78rem;color:var(--text-dim);">
        <span style="font-weight:800;color:var(--text);">${resumen}</span>
        <span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:9px;height:9px;border-radius:50%;background:#ef4444;display:inline-block;"></span>Sin activar = nunca entró</span>
        <span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:9px;height:9px;border-radius:50%;background:#94a3b8;display:inline-block;"></span>Inactivo = +7 días sin entrar</span>
        <span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:9px;height:9px;border-radius:50%;background:#f59e0b;display:inline-block;"></span>Activado = entró, no vendió</span>
        <span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:9px;height:9px;border-radius:50%;background:#22c55e;display:inline-block;"></span>Vendiendo</span>
      </div>
      <table><thead><tr><th>Nombre</th><th>Estado</th><th>Se sumó</th><th>Ingresos</th><th>Último ingreso</th><th>Ventas</th><th>Tu %</th><th>Academia</th><th>Ganancia</th><th>Seguimiento</th></tr></thead><tbody>${filas}</tbody></table>`;
  } catch(e) { console.error('Error cargando red', e); }
}

// ─── SIGUIENTE MEJOR ACCIÓN ───────────────────────────────────────────────────

async function cargarSiguienteAccion() {
  if(!aliado) return;
  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/siguiente-accion`);
    if(!res.ok) return;
    const data = await res.json();
    const cont = document.getElementById('nba-container');
    const bannerC2 = document.getElementById('d-canal2-banner');
    if(!data.siguiente_accion) { cont.style.display='none'; return; }
    const a = data.siguiente_accion;
    cont.style.display = 'block';
    if(bannerC2) bannerC2.style.display = 'none';

    // Badge IA cuando la descripción/mensaje vino de Groq
    const badgeIA = (a.fuente === 'ia')
      ? `<span style="margin-left:8px;font-size:.65rem;font-weight:800;background:linear-gradient(135deg,#a855f7,#6366f1);color:#fff;padding:2px 7px;border-radius:50px;letter-spacing:.5px;">✨ IA</span>`
      : '';

    // Bloque mensaje listo para copiar (solo cuando lo generó la IA)
    let mensajeBlock = '';
    if (a.mensaje_sugerido) {
      const msgEsc = escapeHtml(a.mensaje_sugerido);
      mensajeBlock = `
        <div style="margin-top:12px;padding:12px;background:rgba(168,85,247,0.06);border:1px dashed rgba(168,85,247,0.35);border-radius:10px;">
          <div style="font-size:.7rem;font-weight:800;color:#a855f7;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;">
            <i class="fa-solid fa-wand-magic-sparkles"></i> Mensaje sugerido — listo para copiar
          </div>
          <div style="white-space:pre-wrap;font-size:.86rem;line-height:1.5;color:var(--text);margin-bottom:8px;" id="nba-msg-text">${msgEsc}</div>
          <button onclick="copiarMensajeNBA()" style="background:rgba(168,85,247,0.15);color:#a855f7;border:1px solid rgba(168,85,247,0.35);border-radius:8px;padding:6px 12px;font-size:.78rem;font-weight:700;cursor:pointer;">
            <i class="fa-regular fa-copy"></i> Copiar mensaje
          </button>
        </div>
      `;
    }

    cont.innerHTML = `
      <div class="nba-card ${a.color}">
        <div class="nba-icon">${a.icono}</div>
        <div class="nba-body">
          <div class="nba-label"><i class="fa-solid fa-bolt"></i> Siguiente mejor acción ${badgeIA}</div>
          <div class="nba-title">${escapeHtml(a.titulo || '')}</div>
          <div class="nba-desc">${escapeHtml(a.descripcion || '')}</div>
          ${mensajeBlock}
          <button class="nba-btn ${a.color}" onclick="cambiarTabDesdeNBA('${a.tab}')">
            <i class="fa-solid fa-arrow-right"></i> ${escapeHtml(a.boton || 'Ver')}
          </button>
        </div>
      </div>
    `;
  } catch(e) { console.warn('Error cargando siguiente acción', e); }
}

function copiarMensajeNBA() {
  const el = document.getElementById('nba-msg-text');
  if (!el) return;
  const txt = el.textContent || '';
  navigator.clipboard.writeText(txt).then(() => {
    if (typeof showToast === 'function') showToast('Mensaje copiado', 'success');
    else alert('Mensaje copiado al portapapeles.');
  }).catch(() => {
    // Fallback para navegadores antiguos
    const ta = document.createElement('textarea');
    ta.value = txt; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch(_) {}
    document.body.removeChild(ta);
    if (typeof showToast === 'function') showToast('Mensaje copiado', 'success');
  });
}

function cambiarTabDesdeNBA(tab) {
  const btn = document.querySelector(`.tab-btn[onclick*="'${tab}'"]`);
  if(btn) cambiarTab(tab, btn);
}

// ─── CHECKLIST ONBOARDING ─────────────────────────────────────────────────────

async function cargarOnboardingChecklist() {
  if(!aliado) return;
  // El checklist ya es visible por defecto con fallback estático en el HTML.
  // Si la API responde, reemplazamos el contenido con datos reales.
  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/onboarding`);
    if(!res.ok) return; // Mantener fallback estático si falla la API
    const data = await res.json();
    const box = document.getElementById('onboarding-checklist-box');
    if(data.pct === 100) { box.style.display='none'; return; }
    box.style.display = 'block';
    document.getElementById('ob-pct-badge').textContent = data.pct + '%';
    document.getElementById('ob-progress-fill').style.width = data.pct + '%';
    document.getElementById('ob-checklist-items').innerHTML = data.pasos.map(p=>`
      <div class="ob-step ${p.completado?'done':'pending'}">
        <div class="ob-check ${p.completado?'done':'pending'}">
          ${p.completado?'<i class="fa-solid fa-check" style="font-size:.7rem;"></i>':''}
        </div>
        <span>${p.titulo}</span>
        ${!p.completado && p.id==='cbu'
          ? `<a href="#" onclick="cambiarTab('mi-cuenta', document.getElementById('btn-tab-mi-cuenta')); return false;"
               style="margin-left:auto;font-size:.72rem;font-weight:700;color:var(--amber);background:rgba(250,204,21,0.1);border:1px solid rgba(250,204,21,0.3);padding:2px 10px;border-radius:50px;text-decoration:none;white-space:nowrap;">
               Cargar →</a>`
          : !p.completado
          ? '<span style="margin-left:auto;font-size:.72rem;font-weight:700;color:var(--primary);background:rgba(59,130,246,0.1);padding:2px 8px;border-radius:50px;">Pendiente</span>'
          : ''}
      </div>
    `).join('');
  } catch(e) {
    // Silently keep the static fallback — don't show error to user
    console.warn('Checklist API no disponible, usando fallback estático', e);
  }
}

// ─── COTIZADOR CON ANÁLISIS IA ────────────────────────────────────────────────

// Cache del tipo de cambio (se recarga al entrar al cotizador)
let _tipoDeCambio = null;
let _ultimoLinkGenerado = null; // { id, url, moneda, expires_at }

async function cargarTipoDeCambio() {
  try {
    const res = await apiFetch(`${API}/tipo-de-cambio`);
    if(!res.ok) throw new Error();
    const data = await res.json();
    _tipoDeCambio = data.venta;
    const badge = document.getElementById('cot-tc-badge');
    if(badge) badge.innerHTML = `USD 1 = ARS ${Number(data.venta).toLocaleString('es-AR')} <span style="opacity:.7;">(blue · actualizado)</span>`;
    actualizarPrecioARS();
    return data.venta;
  } catch(e) {
    const badge = document.getElementById('cot-tc-badge');
    if(badge) badge.innerHTML = `<span style="color:var(--red);">Sin TC en vivo — se usa al generar el link</span>`;
    return null;
  }
}

function actualizarPrecioARS() {
  const plan = document.getElementById('cot-plan').value;
  const el   = document.getElementById('cot-precio-ars');
  if(!el) return;
  const precioUsd = PLANES[plan] ?? PLANES_CONTINUIDAD[plan];
  if(!plan || !precioUsd || !_tipoDeCambio) { el.textContent = 'ARS —'; return; }
  const arsEstimado = Math.round(precioUsd * _tipoDeCambio);
  el.textContent = 'ARS ' + arsEstimado.toLocaleString('es-AR');
}

function actualizarCotizador() {
  if(!aliado) return;
  const plan     = document.getElementById('cot-plan').value;
  const resDiv   = document.getElementById('cot-resultado');
  const aiBox    = document.getElementById('cot-ai-box');
  if(!plan) { resDiv.style.display='none'; return; }

  const esContinuidad = (plan in PLANES_CONTINUIDAD);
  const precio     = esContinuidad ? PLANES_CONTINUIDAD[plan] : PLANES[plan];
  const nivel      = aliado.nivel_calculado || aliado.nivel_actual || 'BASIC';
  const porcentaje = esContinuidad ? COMISION_RECURRENTE_PCT : pct(nivel);
  const comision   = Math.round(precio * porcentaje);
  const suf        = esContinuidad ? '/mes' : '';

  document.getElementById('cot-precio').textContent   = 'USD ' + precio.toLocaleString() + suf;
  document.getElementById('cot-comision').textContent = 'USD ' + comision.toLocaleString() + suf;
  document.getElementById('cot-pct-badge').textContent = Math.round(porcentaje * 100) + '%';

  const cliente = document.getElementById('cot-cliente').value || 'tu-cliente';
  const link    = `https://avanzadigital.digital/contratar?plan=${encodeURIComponent(plan)}&ref=${aliado.ref_code}&cliente=${encodeURIComponent(cliente)}`;
  document.getElementById('cot-link').value = link;

  // Mostrar box de checkout dual y cargar tipo de cambio si hace falta
  const checkoutBox = document.getElementById('cot-checkout-box');
  if(checkoutBox) {
    checkoutBox.style.display = 'block';
    if(!_tipoDeCambio) cargarTipoDeCambio(); else actualizarPrecioARS();
  }

  // Continuidad (mensual): el link automático credita comisión de pago único,
  // así que para mensuales ocultamos los botones de link y dejamos solo las
  // instrucciones manuales (USDT/Payoneer) + nota para registrar la continuidad.
  const autoLinks = document.getElementById('cot-auto-links');
  const contNota  = document.getElementById('cot-continuidad-nota');
  if(autoLinks) autoLinks.style.display = esContinuidad ? 'none' : 'grid';
  if(contNota)  contNota.style.display  = esContinuidad ? 'block' : 'none';

  // Ocultar link previo (si el aliado cambia de plan, queda stale)
  const lgBox = document.getElementById('cot-link-generado');
  if(lgBox) lgBox.style.display = 'none';
  _ultimoLinkGenerado = null;

  // Resetear análisis IA
  if(aiBox) { aiBox.style.display='none'; aiBox.innerHTML=''; }
  resDiv.style.display = 'block';
}

// Generador de link de pago con selección de moneda (spec §2, §3, §13)
async function generarLinkPago(moneda) {
  if(!aliado) return;
  const plan    = document.getElementById('cot-plan').value;
  const cliente = document.getElementById('cot-cliente').value || 'Cliente';
  if(!plan) { mostrarToast('Elegí un plan primero.', 'amber'); return; }

  const btnARS = document.getElementById('btn-checkout-ars');
  const btnUSD = document.getElementById('btn-checkout-usd');
  const btnActivo = (moneda === 'ars') ? btnARS : btnUSD;
  const labelOriginal = btnActivo.innerHTML;
  btnActivo.innerHTML = '<span class="spinner"></span> Generando...';
  btnActivo.disabled = true;
  if(moneda === 'ars' && btnUSD) btnUSD.disabled = true;
  if(moneda === 'usd' && btnARS) btnARS.disabled = true;

  try {
    const clienteEmail = (document.getElementById('cot-cliente-email')?.value || '').trim();
    const clienteWA    = (document.getElementById('cot-cliente-whatsapp')?.value || '').trim();
    const url = `${API}/checkout/crear?plan=${encodeURIComponent(plan)}&ref_code=${aliado.ref_code}&nombre_cliente=${encodeURIComponent(cliente)}&moneda=${moneda}&cliente_email=${encodeURIComponent(clienteEmail)}&cliente_whatsapp=${encodeURIComponent(clienteWA)}`;
    if (window._cotizadorProspectoId) { url += '&prospecto_id=' + window._cotizadorProspectoId; window._cotizadorProspectoId = null; }
    const res  = await fetch(url, { method: 'POST' });
    const data = await res.json();

    if(!res.ok || !data.checkout_url) {
      mostrarToast(data.detail || 'Error al generar el link. Intentá de nuevo.', 'red');
      return;
    }

    if(data.fallback) {
      mostrarToast('Procesador no configurado todavía. Avisale al admin.', 'amber');
      return;
    }

    // Mostrar el link en el box de resultado
    _ultimoLinkGenerado = {
      id: data.link_id,
      url: data.checkout_url,
      moneda: data.moneda,
      expires_at: data.expires_at,
      plan: data.plan,
      precio_usd: data.precio_usd,
      precio_ars: data.precio_ars,
      tipo_cambio: data.tipo_cambio,
    };

    const lgBox   = document.getElementById('cot-link-generado');
    const lgTitulo = document.getElementById('cot-lg-titulo');
    const lgVence = document.getElementById('cot-lg-vence');
    const lgUrl   = document.getElementById('cot-lg-url');
    const lgAviso = document.getElementById('cot-lg-aviso');

    if(data.moneda === 'ars') {
      lgTitulo.innerHTML = `Link en pesos — <strong>ARS ${Math.round(data.precio_ars).toLocaleString('es-AR')}</strong> (TC ${Number(data.tipo_cambio).toLocaleString('es-AR')})`;
    } else {
      lgTitulo.innerHTML = `Link en dólares — <strong>USD ${Number(data.precio_usd).toFixed(2)}</strong> (USDT TRC20)`;
    }
    lgUrl.value = data.checkout_url;
    if(lgAviso) lgAviso.textContent = `Compartí este link con tu cliente. Expira a las 48 horas por seguridad (el tipo de cambio puede variar).`;
    actualizarTextoVencimiento();
    lgBox.style.display = 'block';
    document.getElementById('btn-regenerar-link').style.display = 'none';
    mostrarToast('Link generado. Copialo y mandáselo al cliente.', 'green');

    // Reemplazar el link manual con el de pago real
    document.getElementById('cot-link').value = data.checkout_url;
  } catch(e) {
    mostrarToast('Error de conexión al generar link.', 'red');
    console.error(e);
  } finally {
    btnActivo.innerHTML = labelOriginal;
    btnActivo.disabled = false;
    if(btnARS) btnARS.disabled = false;
    if(btnUSD) btnUSD.disabled = false;
  }
}

function actualizarTextoVencimiento() {
  if(!_ultimoLinkGenerado || !_ultimoLinkGenerado.expires_at) return;
  const lgVence = document.getElementById('cot-lg-vence');
  const btnRegen = document.getElementById('btn-regenerar-link');
  const exp = new Date(_ultimoLinkGenerado.expires_at);
  const ahora = new Date();
  const diffMs = exp.getTime() - ahora.getTime();
  if(diffMs <= 0) {
    lgVence.innerHTML = '<span style="color:var(--red);"><i class="fa-solid fa-triangle-exclamation"></i> Este link venció. Generá uno nuevo.</span>';
    if(btnRegen) btnRegen.style.display = 'inline-flex';
  } else {
    const horas = Math.floor(diffMs / (1000*60*60));
    const mins  = Math.floor((diffMs % (1000*60*60)) / (1000*60));
    const txt = horas >= 1 ? `Vence en ${horas}h ${mins}m` : `Vence en ${mins} min`;
    lgVence.innerHTML = `<i class="fa-solid fa-clock"></i> ${txt}`;
    if(btnRegen) btnRegen.style.display = 'none';
  }
}
// refrescar cada 60s mientras el cotizador está abierto
setInterval(actualizarTextoVencimiento, 60000);

// Recupera el último link activo del aliado desde el backend al entrar al cotizador (spec §13)
async function recuperarUltimoLinkActivo() {
  if(!aliado) return;
  try {
    const res = await apiFetch(`${API}/checkout/ultimo-link?codigo=${encodeURIComponent(aliado.codigo)}`);
    if(!res.ok) return; // 404 = sin link previo, silencioso
    const data = await res.json();
    if(!data || !data.checkout_url) return;

    _ultimoLinkGenerado = {
      id:          data.link_id,
      url:         data.checkout_url,
      moneda:      data.moneda,
      expires_at:  data.expires_at,
      plan:        data.plan,
      precio_usd:  data.precio_usd,
      precio_ars:  data.precio_ars,
      tipo_cambio: data.tipo_cambio,
    };

    // Mostrar el box con el link recuperado
    const lgBox    = document.getElementById('cot-link-generado');
    const lgTitulo = document.getElementById('cot-lg-titulo');
    const lgUrl    = document.getElementById('cot-lg-url');
    const lgAviso  = document.getElementById('cot-lg-aviso');
    if(!lgBox) return;

    if(data.moneda === 'ars') {
      lgTitulo.innerHTML = `Link anterior en pesos — <strong>ARS ${Math.round(data.precio_ars).toLocaleString('es-AR')}</strong> (TC ${Number(data.tipo_cambio).toLocaleString('es-AR')})`;
    } else {
      lgTitulo.innerHTML = `Link anterior en dólares — <strong>USD ${Number(data.precio_usd).toFixed(2)}</strong> (USDT TRC20)`;
    }
    lgUrl.value = data.checkout_url;
    if(lgAviso) lgAviso.textContent = 'Link recuperado de tu sesión anterior.';

    // Mostrar checkout-box padre si corresponde
    const checkoutBox = document.getElementById('cot-checkout-box');
    if(checkoutBox) checkoutBox.style.display = 'block';

    lgBox.style.display = 'block';
    actualizarTextoVencimiento(); // evalúa si ya venció y muestra el botón "Regenerar"
  } catch(e) {
    console.warn('No se pudo recuperar el último link:', e);
  }
}

function copiarLinkPago() {
  if(!_ultimoLinkGenerado) return;
  navigator.clipboard.writeText(_ultimoLinkGenerado.url)
    .then(() => mostrarToast('Link de pago copiado', 'green'))
    .catch(() => mostrarToast('No se pudo copiar', 'red'));
}

// ── USDT/USDC — instrucciones para el cotizador del aliado ───────────────────
let _usdtConfig = null;

async function mostrarInstruccionesUSDT() {
  const plan = document.getElementById('cot-plan')?.value;
  if (!plan) { mostrarToast('Seleccioná un plan primero', 'red'); return; }

  const panel = document.getElementById('cot-usdt-instrucciones');
  const loading = document.getElementById('cot-usdt-loading');
  const content = document.getElementById('cot-usdt-content');

  panel.style.display = 'block';
  loading.style.display = 'block';
  content.style.display = 'none';
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  try {
    if (!_usdtConfig) {
      const res = await fetch(`${API}/config/usdt`);
      if (!res.ok) throw new Error('No se pudo obtener la configuración de pago USDT.');
      _usdtConfig = await res.json();
    }

    const { direccion, red, metodo } = _usdtConfig;
    if (!direccion) {
      loading.innerHTML = '<i class="fa-solid fa-triangle-exclamation" style="color:#ef4444;"></i> Pago en USDT no configurado. Contactá al admin de Avanza.';
      return;
    }

    // Precio USD del plan (pago único o continuidad mensual)
    const esContinuidad = (plan in PLANES_CONTINUIDAD);
    const precioUSD = (PLANES[plan] ?? PLANES_CONTINUIDAD[plan]) || '—';
    const sufMes = esContinuidad ? '/mes' : '';
    const redLabel = red || 'TRC20';
    const monedaLabel = metodo && metodo.toUpperCase().includes('USDC') ? 'USDT/USDC' : 'USDT';

    document.getElementById('cot-usdt-red').textContent = redLabel;
    document.getElementById('cot-usdt-direccion').value = direccion;

    const msg = `Hola! Te comparto los datos para el pago del ${plan} de Avanza Digital en ${monedaLabel}:\n\n` +
      `💰 Monto exacto: USD ${precioUSD}${sufMes}\n` +
      `🌐 Red: ${redLabel}\n` +
      `📋 Dirección:\n${direccion}\n\n` +
      `⚠ Importante: enviá exactamente USD ${precioUSD}${sufMes} y avisame cuando lo hagas para confirmar con Avanza. ` +
      `Una vez confirmado el pago iniciamos la implementación.`;
    document.getElementById('cot-usdt-wpp-msg').value = msg;

    loading.style.display = 'none';
    content.style.display = 'block';
    _registrarPagoManual('usdt');
  } catch (err) {
    loading.innerHTML = `<i class="fa-solid fa-triangle-exclamation" style="color:#ef4444;"></i> ${err.message}`;
  }
}

function copiarDireccionUSDT() {
  const dir = document.getElementById('cot-usdt-direccion')?.value;
  if (!dir) return;
  navigator.clipboard.writeText(dir)
    .then(() => mostrarToast('Dirección copiada al portapapeles', 'green'))
    .catch(() => mostrarToast('No se pudo copiar', 'red'));
}

function copiarMensajeUSDT() {
  const msg = document.getElementById('cot-usdt-wpp-msg')?.value;
  if (!msg) return;
  navigator.clipboard.writeText(msg)
    .then(() => mostrarToast('Mensaje copiado — pegalo en WhatsApp', 'green'))
    .catch(() => mostrarToast('No se pudo copiar', 'red'));
}

// ── Payoneer (email + transferencia bancaria USD) — instrucciones para el cliente ──
let _payoneerConfig = null;

async function mostrarInstruccionesPayoneer() {
  const plan = document.getElementById('cot-plan')?.value;
  if (!plan) { mostrarToast('Seleccioná un plan primero', 'red'); return; }

  const panel   = document.getElementById('cot-payoneer-instrucciones');
  const loading = document.getElementById('cot-payoneer-loading');
  const content = document.getElementById('cot-payoneer-content');

  panel.style.display = 'block';
  loading.style.display = 'block';
  content.style.display = 'none';
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  try {
    if (!_payoneerConfig) {
      const res = await fetch(`${API}/config/payoneer`);
      if (!res.ok) throw new Error('No se pudo obtener la configuración de Payoneer.');
      _payoneerConfig = await res.json();
    }

    const cfg = _payoneerConfig;
    if (!cfg.activo || !cfg.email) {
      loading.innerHTML = '<i class="fa-solid fa-triangle-exclamation" style="color:#ef4444;"></i> Pago por Payoneer no configurado. Contactá al admin de Avanza.';
      return;
    }

    const esContinuidad = (plan in PLANES_CONTINUIDAD);
    const precioUSD = (PLANES[plan] ?? PLANES_CONTINUIDAD[plan]) || '—';
    const sufMes = esContinuidad ? '/mes' : '';

    document.getElementById('cot-payoneer-email').value = cfg.email;

    // Construir bloque de transferencia bancaria (si hay datos)
    const b = cfg.banco || {};
    const bankRows = [
      ['Beneficiario', b.beneficiario],
      ['Banco', b.banco],
      ['Dirección del banco', b.direccion],
      ['Número de cuenta', b.cuenta],
      ['Tipo de cuenta', b.tipo_cuenta],
      ['ABA / Routing', b.aba],
      ['SWIFT / BIC', b.swift],
    ].filter(([, v]) => v);

    const bankBox = document.getElementById('cot-payoneer-bank');
    if (bankRows.length) {
      bankBox.innerHTML =
        '<div style="font-size:0.72rem; font-weight:700; color:#ff7a45; text-transform:uppercase; letter-spacing:1px; margin-bottom:8px;"><i class="fa-solid fa-building-columns"></i> Opción 2 · Transferencia bancaria en USD (desde cualquier banco)</div>' +
        '<div style="background:rgba(0,0,0,0.3); border:1px solid rgba(255,122,69,0.2); border-radius:8px; padding:12px;">' +
        bankRows.map(([label, val]) =>
          `<div style="margin-bottom:8px;"><div style="font-size:0.66rem; font-weight:700; color:var(--text-dim); text-transform:uppercase; letter-spacing:.5px; margin-bottom:2px;">${label}</div>` +
          `<div style="font-family:monospace; font-size:0.8rem; color:var(--text); word-break:break-all;">${val}</div></div>`
        ).join('') +
        '</div>';
    } else {
      bankBox.innerHTML = '';
    }

    // Mensaje para WhatsApp con ambas opciones
    let msg = `Hola! Te comparto los datos para pagar el ${plan} de Avanza Digital (USD ${precioUSD}${sufMes}) por Payoneer:\n\n` +
      `💳 Opción 1 — Email de Payoneer:\n${cfg.email}\n`;
    if (bankRows.length) {
      msg += `\n🏦 Opción 2 — Transferencia bancaria en USD (desde cualquier banco):\n` +
        bankRows.map(([label, val]) => `${label}: ${val}`).join('\n') + '\n';
    }
    msg += `\n⚠ Enviá exactamente USD ${precioUSD}${sufMes} y avisame cuando lo hagas. Tu plan se activa en cuanto Avanza confirma el pago (hasta 24hs hábiles).`;
    document.getElementById('cot-payoneer-wpp-msg').value = msg;

    loading.style.display = 'none';
    content.style.display = 'block';
    _registrarPagoManual('payoneer');
  } catch (err) {
    loading.innerHTML = `<i class="fa-solid fa-triangle-exclamation" style="color:#ef4444;"></i> ${err.message}`;
  }
}

function copiarCampoPayoneer(id) {
  const val = document.getElementById(id)?.value;
  if (!val) return;
  navigator.clipboard.writeText(val)
    .then(() => mostrarToast('Copiado al portapapeles', 'green'))
    .catch(() => mostrarToast('No se pudo copiar', 'red'));
}

function copiarMensajePayoneer() {
  const msg = document.getElementById('cot-payoneer-wpp-msg')?.value;
  if (!msg) return;
  navigator.clipboard.writeText(msg)
    .then(() => mostrarToast('Mensaje copiado — pegalo en WhatsApp', 'green'))
    .catch(() => mostrarToast('No se pudo copiar', 'red'));
}

// ── Pago manual: registra el pendiente (atribución) y permite reportar el pago ──
// USDT/Payoneer no tienen webhook. Al mostrar las instrucciones creamos un
// LinkPago "pendiente" con la atribución del aliado; cuando el cliente paga, el
// aliado avisa con "el cliente ya pagó" y el admin lo confirma → comisión grabada.
const _pagosManualIds = { usdt: null, payoneer: null };

async function _registrarPagoManual(metodo) {
  if (!aliado) return;
  const plan = document.getElementById('cot-plan')?.value;
  if (!plan) return;
  const nombre = document.getElementById('cot-cliente')?.value || 'Cliente';
  const email  = (document.getElementById('cot-cliente-email')?.value || '').trim();
  const wa     = (document.getElementById('cot-cliente-whatsapp')?.value || '').trim();
  try {
    const url = `${API}/checkout/manual?plan=${encodeURIComponent(plan)}&ref_code=${encodeURIComponent(aliado.ref_code)}` +
      `&nombre_cliente=${encodeURIComponent(nombre)}&metodo=${metodo}` +
      `&cliente_email=${encodeURIComponent(email)}&cliente_whatsapp=${encodeURIComponent(wa)}`;
    const res = await fetch(url, { method: 'POST' });
    if (!res.ok) return;
    const data = await res.json();
    _pagosManualIds[metodo] = data.link_id;
  } catch (e) { console.warn('No se pudo registrar el pago pendiente:', e); }
}

async function reportarPagoManual(metodo) {
  const id = _pagosManualIds[metodo];
  if (!id) { mostrarToast('Generá las instrucciones primero.', 'amber'); return; }
  const btn = document.getElementById(`btn-reportar-${metodo}`);
  try {
    const res = await fetch(`${API}/checkout/manual/${id}/reportar`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      mostrarToast('Aviso enviado. Avanza verifica la transferencia y acredita tu comisión.', 'green');
      if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-check"></i> Avisado a Avanza'; }
    } else {
      mostrarToast(data.detail || 'No se pudo avisar.', 'red');
    }
  } catch (e) { mostrarToast('Error de conexión.', 'red'); }
}
// ─────────────────────────────────────────────────────────────────────────────

async function regenerarLinkPago() {
  if(!_ultimoLinkGenerado || !_ultimoLinkGenerado.id) {
    // si no tenemos el id, generamos uno nuevo desde cero con la misma moneda
    if(_ultimoLinkGenerado && _ultimoLinkGenerado.moneda) {
      return generarLinkPago(_ultimoLinkGenerado.moneda);
    }
    return;
  }
  const btn = document.getElementById('btn-regenerar-link');
  const label = btn.innerHTML;
  btn.innerHTML = '<span class="spinner"></span> Regenerando...';
  btn.disabled = true;
  try {
    const res = await apiFetch(`${API}/checkout/regenerar/${_ultimoLinkGenerado.id}`, { method: 'POST' });
    const data = await res.json();
    if(res.ok && data.checkout_url) {
      _ultimoLinkGenerado = {
        id: data.link_id,
        url: data.checkout_url,
        moneda: data.moneda,
        expires_at: data.expires_at,
        plan: data.plan,
        precio_usd: data.precio_usd,
        precio_ars: data.precio_ars,
        tipo_cambio: data.tipo_cambio,
      };
      document.getElementById('cot-lg-url').value = data.checkout_url;
      document.getElementById('cot-link').value = data.checkout_url;
      const lgTitulo = document.getElementById('cot-lg-titulo');
      if(data.moneda === 'ars') {
        lgTitulo.innerHTML = `Link en pesos — <strong>ARS ${Math.round(data.precio_ars).toLocaleString('es-AR')}</strong> (TC ${Number(data.tipo_cambio).toLocaleString('es-AR')})`;
      } else {
        lgTitulo.innerHTML = `Link en dólares — <strong>USD ${Number(data.precio_usd).toFixed(2)}</strong> (USDT TRC20)`;
      }
      actualizarTextoVencimiento();
      mostrarToast('Link regenerado. El anterior quedó invalidado.', 'green');
    } else {
      mostrarToast(data.detail || 'No se pudo regenerar', 'red');
    }
  } catch(e) {
    mostrarToast('Error de conexión', 'red');
  } finally {
    btn.innerHTML = label;
    btn.disabled = false;
  }
}

// Función legacy — mantengo por compatibilidad si algún handler externo la llama
async function generarCheckoutReal() { return generarLinkPago('ars'); }

async function obtenerAnalisisIA() {
  const plan     = document.getElementById('cot-plan').value;
  const rubro    = document.getElementById('cot-rubro').value;
  const urgencia = document.getElementById('cot-urgencia').value;
  const cliente  = document.getElementById('cot-cliente').value || 'el cliente';
  const aiBox    = document.getElementById('cot-ai-box');
  const btn      = document.getElementById('btn-analisis-ia');
  if(!plan || !rubro) {
    mostrarToast('Seleccioná plan y rubro para obtener análisis IA.', 'amber'); return;
  }

  btn.innerHTML = '<span class="spinner"></span> Generando análisis...';
  btn.disabled  = true;

  // Análisis local: sin llamada API externa, basado en reglas de negocio
  await new Promise(r => setTimeout(r, 900));

  const analisis = generarAnalisisLocal(plan, rubro, urgencia, cliente);

  aiBox.style.display = 'block';
  aiBox.innerHTML = `
    <div class="ai-label"><i class="fa-solid fa-brain"></i> Análisis IA — Propuesta inteligente</div>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
      <span class="ai-prob-badge ${analisis.probClass}">
        <i class="fa-solid fa-chart-line"></i> ${analisis.probTexto}
      </span>
      <span style="font-size:.78rem;color:var(--text-dim);">${analisis.motivo}</span>
    </div>
    <div class="ai-section-title"><i class="fa-solid fa-bullseye" style="color:#a855f7;margin-right:4px;"></i> Plan recomendado para este perfil</div>
    <p style="font-size:.85rem;color:var(--text-muted);margin-bottom:4px;">${analisis.planRec}</p>
    <div class="ai-section-title" style="margin-top:14px;"><i class="fa-brands fa-whatsapp" style="color:var(--green);margin-right:4px;"></i> Mensaje de apertura sugerido</div>
    <div class="ai-pitch-box">${analisis.pitch}</div>
    <div class="ai-section-title" style="margin-top:14px;"><i class="fa-solid fa-triangle-exclamation" style="color:var(--amber);margin-right:4px;"></i> Objeciones frecuentes en este rubro</div>
    <ul style="padding-left:16px;font-size:.82rem;color:var(--text-muted);line-height:1.8;">${analisis.objeciones.map(o=>`<li>${o}</li>`).join('')}</ul>
  `;

  btn.innerHTML = '<i class="fa-solid fa-brain"></i> Regenerar análisis';
  btn.disabled  = false;
}

function generarAnalisisLocal(plan, rubro, urgencia, cliente) {
  const PROB = {
    alta:  { pct: '72–85%', cls: 'ai-prob-high',   texto: '72–85% prob. cierre' },
    media: { pct: '45–65%', cls: 'ai-prob-medium',  texto: '45–65% prob. cierre' },
    baja:  { pct: '20–40%', cls: 'ai-prob-low',     texto: '20–40% prob. cierre' },
  };

  const PITCHES = {
    'Metalúrgica / Manufactura': `Hola, vi que ${cliente} tiene presencia web pero no aparece en búsquedas clave para compradores industriales. Corrí un reporte técnico gratuito para su sitio — los resultados son bastante claros. ¿Le comparto el informe para que vean el estado real de su visibilidad online?`,
    'Agro / Maquinaria agrícola': `Hola, muchos distribuidores de maquinaria agrícola están perdiendo consultas porque Google no los encuentra en la zona. Preparé un análisis gratuito para ${cliente}. ¿Lo ven y vemos si aplica?`,
    'Logística / Transporte': `Hola, en logística el 70% de las búsquedas de nuevos clientes empiezan en Google. Hice un reporte del sitio de ${cliente} y hay algunos puntos críticos. ¿Se lo mando para que evalúen?`,
    'Servicios B2B / Consultoría': `Hola, los servicios B2B que aparecen primero en Google capturan 3x más leads que los que no. Hice un diagnóstico del sitio de ${cliente} — puedo mandárselo en 2 minutos. ¿Les sirve?`,
    default: `Hola, analicé la presencia web de ${cliente} y detecté algunas mejoras concretas que podrían generar más consultas de clientes. ¿Les interesa ver el reporte gratuito?`,
  };

  const PLAN_RECS = {
    'Plan Base':        'Ideal para validar la propuesta. Empresa pequeña/mediana que necesita visibilidad inicial rápida. Buen ticket de entrada y alta conversión.',
    'Plan Pro':         'Recomendado para empresas con web activa que ya venden pero quieren escalar. Mayor ticket = mayor comisión. Presenta el ROI en leads.',
    'Plan Industrial':  'Para fábricas o distribuidores que necesitan posicionarse en búsquedas muy específicas. Pitch: "sus compradores buscan exactamente lo que hacen".',
    'Estrategico 360':  'Para empresas con ambición de dominar su mercado. Pitch de valor total: presencia, leads, reputación. Presenta múltiples canales de impacto.',
  };

  const OBJECIONES = {
    'Metalúrgica / Manufactura': ['«Nuestros clientes nos llegan por referidos» → Respondé: ¿y si pudieran llegar también por Google?', '«Ya tenemos web» → Respondé: tenerla y aparecer en búsquedas son cosas distintas.'],
    'Agro / Maquinaria agrícola': ['«No usamos internet para cerrar ventas» → Respondé: sus competidores sí.', '«Lo decide el dueño, no yo» → Pedí el contacto del dueño directamente.'],
    default: ['«No tenemos presupuesto ahora» → Preguntá cuándo sería buen momento y agendá seguimiento.', '«Lo vemos más adelante» → Ofrecé el diagnóstico gratuito para que tengan info concreta.'],
  };

  const probData  = PROB[urgencia] || PROB.media;
  const pitch     = PITCHES[rubro] || PITCHES.default;
  const planRec   = PLAN_RECS[plan] || 'Plan adecuado para este perfil.';
  const objs      = OBJECIONES[rubro] || OBJECIONES.default;
  const motivo    = urgencia === 'alta' ? 'Alta urgencia detectada — cerrá en la próxima llamada' :
                    urgencia === 'baja' ? 'Lead frío — aportá valor primero con la auditoría' :
                    'Probabilidad media — seguimiento estructurado recomendado';

  return { probClass: probData.cls, probTexto: probData.texto, motivo, pitch, planRec, objeciones: objs };
}

function copiarCotizacion() {
  const link    = document.getElementById('cot-link').value;
  const cliente = document.getElementById('cot-cliente').value || 'tu empresa';
  const plan    = document.getElementById('cot-plan').value;
  const texto = `Hola, te dejo el link directo con la propuesta del ${plan} para ${cliente}. Podés avanzar con la contratación desde ahí de forma segura:\n\n${link}\n\nCualquier consulta, avisame.`;
  navigator.clipboard.writeText(texto).then(() => {
    mostrarToast('¡Link y mensaje listos para enviar por WhatsApp!', 'green');
  });
}

function irACotizador(id, nombre, plan) {
  const btn = document.querySelector('.tab-btn[onclick*="cotizador"]');
  if(btn) cambiarTab('cotizador', btn);
  setTimeout(()=>{
    const inp = document.getElementById('cot-cliente');
    if(inp) inp.value = nombre;
    if(plan) {
      const sel = document.getElementById('cot-plan');
      if(sel) { sel.value = plan; actualizarCotizador(); }
    }
  }, 100);
}

// ─── PILOTO AUTOMÁTICO ────────────────────────────────────────────────────────

async function togglePiloto(id, actual) {
  try {
    const nuevoEstado = !actual;
    const res = await apiFetch(`${API}/prospectos/${id}/piloto?activo=${nuevoEstado}`, {method:'PATCH'});
    if(res.ok) {
      mostrarToast(nuevoEstado ? '🤖 Piloto automático activado para este lead' : 'Piloto desactivado', nuevoEstado ? 'primary' : 'amber');
      await cargarProspectos();
    }
  } catch(e) { console.warn('Error toggle piloto', e); }
}

// ─── HISTORIAL BOLSA (fixes para los nuevos KPI IDs) ─────────────────────────

async function cargarHistorialBolsa() {
  if(!aliado) return;
  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/historial-bolsa`);
    if(!res.ok) return;
    const data = await res.json();
    const s = data.stats;

    const setKPI = (id, val) => { const el = document.getElementById(id); if(el) el.textContent = val; };
    setKPI('hk-total',    s.total_reclamados);
    setKPI('hk-exitosos', s.exitosos);
    setKPI('hk-noint',    s.no_interesados);
    setKPI('hk-nocont',   s.no_contestaron);
    setKPI('hk-tasa',     s.tasa_exito + '%');

    const tbody = document.getElementById('historial-bolsa-body');
    if(!tbody) return;
    if(!data.leads.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--text-dim);">Todavía no reclamaste ningún lead.</td></tr>';
      return;
    }
    const iconRes  = { exitoso:'✅', no_interesado:'❌', no_contesto:'📵' };
    const labelRes = { exitoso:'Exitoso', no_interesado:'No interesado', no_contesto:'No contestó' };
    tbody.innerHTML = data.leads.map(l => `
      <tr>
        <td style="font-weight:700;">${l.empresa}</td>
        <td style="color:var(--text-muted);">${l.rubro}</td>
        <td style="font-size:.8rem;color:var(--text-dim);">${l.fecha_reclamo||'—'}</td>
        <td>${l.estado==='reclamado'?'<span class="badge badge-amber">Activo</span>':'<span class="badge badge-blue">Cerrado</span>'}</td>
        <td>${l.resultado?`${iconRes[l.resultado]||''} ${labelRes[l.resultado]||l.resultado}`:'<span style="color:var(--text-dim);">—</span>'}</td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

// ─── PATCH: cargarTodo con las nuevas funciones ───────────────────────────────

const _cargarTodoOriginal = cargarTodo;
cargarTodo = async function() {
  await _cargarTodoOriginal();
  await cargarSiguienteAccion();
  await cargarOnboardingChecklist();

  // Si llega de un pago exitoso, mostrar celebración
  const urlParams = new URLSearchParams(window.location.search);
  if(urlParams.get('pago') === 'ok') {
    const plan = urlParams.get('plan') || '';
    mostrarToast(`🎉 ¡Pago confirmado! Venta de ${plan} registrada.`, 'green');
    history.replaceState(null, '', window.location.pathname);
  }
};
// ══════════════════════════════════════════════════════════════════════
// ══ v1.3 — REPUTACIÓN, CRÉDITOS, PERFILADO IA, CUOTAS, MARKETPLACE, COMUNIDAD
// ══════════════════════════════════════════════════════════════════════

// ─── REPUTACIÓN ──────────────────────────────────────────────────────
async function cargarReputacion() {
  if (!aliado) return;
  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/reputacion`);
    if (!res.ok) return;
    const data = await res.json();
    const score = data.score ?? 0;
    const f = data.factores || {};

    // Score principal
    const scoreEl = document.getElementById('d-reput-score');
    if (scoreEl) scoreEl.textContent = score;

    // Rango badge + consecuencia
    const rangoBadge = document.getElementById('d-reput-rango-badge');
    const consecuencia = document.getElementById('d-reput-consecuencia');
    let rangoLabel, rangoColor, rangoFg, consecText;
    if (score >= 85) {
      rangoLabel = 'Élite'; rangoColor = 'rgba(192,132,252,0.2)'; rangoFg = '#c084fc';
      consecText = '✅ Máxima prioridad en la Bolsa. Acceso anticipado a leads nuevos. Todos los badges desbloqueables.';
    } else if (score >= 70) {
      rangoLabel = 'Destacado'; rangoColor = 'rgba(74,222,128,0.15)'; rangoFg = 'var(--green)';
      consecText = '✅ Prioridad en leads premium. Candidato a todos los badges de calidad.';
    } else if (score >= 50) {
      rangoLabel = 'Confiable'; rangoColor = 'rgba(245,158,11,0.15)'; rangoFg = 'var(--amber)';
      consecText = '⚡ Prioridad media en la asignación. Visible en el leaderboard interno. Para subir a Destacado: mejorá tu tasa de cierre o éxito en bolsa.';
    } else {
      rangoLabel = 'Inicial'; rangoColor = 'rgba(239,68,68,0.12)'; rangoFg = '#ef4444';
      consecText = '⚠ Sin prioridad en la asignación de leads. Registrá prospectos y contactá leads para subir de rango.';
    }
    if (rangoBadge) {
      rangoBadge.textContent = rangoLabel;
      rangoBadge.style.background = rangoColor;
      rangoBadge.style.color = rangoFg;
      rangoBadge.style.border = `1px solid ${rangoFg}`;
    }
    if (consecuencia) consecuencia.textContent = consecText;

    // Badges ganados
    const box = document.getElementById('d-reput-badges');
    if (box) {
      if (data.badges && data.badges.length) {
        box.innerHTML = data.badges.map(b =>
          `<span title="${b.desc}" style="background:rgba(192,132,252,0.18); color:#c084fc; border:1px solid rgba(192,132,252,0.35); padding:4px 10px; border-radius:20px; font-size:.72rem; font-weight:700;">${b.icono} ${b.label}</span>`
        ).join('');
      } else {
        box.innerHTML = `<span style="font-size:.76rem; color:var(--text-dim);">Sin badges aún — seguí vendiendo para desbloquearlos.</span>`;
      }
    }

    // Factor bars (pintamos con timeout para que el CSS transition sea visible)
    setTimeout(() => {
      // Tasa de cierre → máx 40 pts → barra sobre 40%
      const cierrePct = Math.min(100, (f.tasa_cierre || 0) / 40 * 100);
      const cierreBar = document.getElementById('rf-cierre-bar');
      const cierreVal = document.getElementById('rf-cierre-val');
      if (cierreBar) cierreBar.style.width = cierrePct + '%';
      if (cierreVal) cierreVal.textContent = (f.tasa_cierre || 0) + '%';

      // Éxito bolsa → máx 20 pts → barra sobre 50% (50% tasa = 20 pts)
      const bolsaPct = Math.min(100, (f.tasa_bolsa || 0) / 50 * 100);
      const bolsaBar = document.getElementById('rf-bolsa-bar');
      const bolsaVal = document.getElementById('rf-bolsa-val');
      if (bolsaBar) bolsaBar.style.width = bolsaPct + '%';
      if (bolsaVal) bolsaVal.textContent = (f.tasa_bolsa || 0) + '%';

      // Actividad reciente → binario 0 o 10 pts
      const activo = f.activo_reciente;
      const activoBar = document.getElementById('rf-activo-bar');
      const activoVal = document.getElementById('rf-activo-val');
      if (activoBar) activoBar.style.width = activo ? '100%' : '0%';
      if (activoVal) { activoVal.textContent = activo ? 'Activo' : 'Inactivo'; activoVal.style.color = activo ? 'var(--green)' : '#ef4444'; }

      // Red activa → máx 3 sub-aliados = 10 pts (3 pts c/u)
      const redActiva = f.red_activa || 0;
      const redPct = Math.min(100, (redActiva / 3) * 100);
      const redBar = document.getElementById('rf-red-bar');
      const redVal = document.getElementById('rf-red-val');
      if (redBar) redBar.style.width = redPct + '%';
      if (redVal) redVal.textContent = `${redActiva} aliado${redActiva !== 1 ? 's' : ''}`;
    }, 150);

  } catch(e){ console.error('reputacion:', e); }
}

function toggleReputExplicacion() {
  const panel = document.getElementById('reput-explicacion');
  const icon  = document.getElementById('reput-toggle-icon');
  const open  = panel.style.display === 'block';
  panel.style.display = open ? 'none' : 'block';
  if (icon) icon.style.transform = open ? '' : 'rotate(180deg)';
}

// ─── CRÉDITOS ────────────────────────────────────────────────────────
async function cargarCreditos() {
  if (!aliado) return;
  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/creditos`);
    if (!res.ok) return;
    const data = await res.json();
    const saldo = data.saldo ?? 0;
    const el = document.getElementById('d-creditos');
    if (el) {
      el.textContent = saldo;
      el.style.color = saldo > 0 ? 'var(--amber)' : 'var(--text-dim)';
    }
    // Sub-texto y CTA según saldo
    const sub = document.getElementById('d-creditos-sub');
    const cta = document.getElementById('d-creditos-cta');
    if (sub) sub.textContent = saldo > 0
      ? `Crédito${saldo !== 1 ? 's' : ''} para usar Jarvis IA`
      : 'Sin créditos — recargá para usar Jarvis IA';
    if (cta) cta.style.display = 'block';  // siempre visible: es el punto único de recarga
  } catch(e){ console.error('creditos:', e); }
}

// ─── SIMULADOR DE CUOTAS — removido (sistema de pago único / mantenimiento mensual) ──

// ─── PERFILADO IA ────────────────────────────────────────────────────

// ── Perfilado client-side para leads de BOLSA ────────────────────────
// (El backend solo expone /prospectos/{id}/perfilar. Los leads de bolsa
//  viven en otra tabla, así que replicamos la heurística determinística
//  acá y persistimos por aliado en localStorage.)
const _PERF_PLANES   = { "Plan Base": 1050, "Plan Pro": 2900, "Plan Industrial": 4900, "Estrategico 360": 7500 };
const _PERF_RUBROS   = {
  "Metalúrgica / Manufactura":   ["Plan Industrial", "B2B técnico con ciclo largo de venta"],
  "Agro / Maquinaria agrícola":  ["Plan Industrial", "Sector con presupuesto pero poca presencia digital"],
  "Logística / Transporte":      ["Plan Pro",        "Necesita canales claros de contacto y cotización"],
  "Servicios B2B / Consultoría": ["Plan Pro",        "Necesita autoridad online y generación de leads"],
  "Comercio / Retail B2B":       ["Plan Pro",        "Catálogo + presencia local"],
  "Construcción / Obras":        ["Plan Industrial", "Obra pública/privada, necesita respaldo digital"],
  "Salud / Clínicas":            ["Plan Pro",        "Pacientes investigan online antes de elegir"],
  "Educación / Capacitación":    ["Plan Pro",        "Captación online es crítica"],
  "Tecnología / Software":       ["Estrategico 360", "Mercado educado, espera excelencia digital"],
  "Otro":                        ["Plan Pro",        "Plan versátil para la mayoría"],
};
const _PERF_TAMANO   = { micro: 0.6, pyme: 1.0, mediana: 1.25, grande: 1.4 };
const _PERF_URGENCIA = { baja: 10, media: 25, alta: 40 };

function _bolsaPerfiladoKey(id) {
  return `avanza:bolsa-perfilado:${aliado?.codigo || 'anon'}:${id}`;
}
function _bolsaPerfiladoGet(id) {
  try { const raw = localStorage.getItem(_bolsaPerfiladoKey(id)); return raw ? JSON.parse(raw) : null; }
  catch { return null; }
}
function _bolsaPerfiladoSet(id, data) {
  try { localStorage.setItem(_bolsaPerfiladoKey(id), JSON.stringify(data)); } catch {}
}

function _perfilarLeadBolsaLocal(empresa, rubro, tamano, urgencia) {
  let score = 20;
  let [plan, razonRubro] = _PERF_RUBROS[rubro] || ["Plan Pro", "Plan versátil"];
  if (rubro && rubro !== "Otro") score += 20;
  score += _PERF_URGENCIA[urgencia] ?? 25;
  const mult = _PERF_TAMANO[tamano] ?? 1.0;

  if (tamano === "grande" && plan === "Plan Pro") plan = "Plan Industrial";
  else if (tamano === "grande" && plan === "Plan Industrial") plan = "Estrategico 360";
  else if (tamano === "micro" && plan !== "Plan Base") {
    plan = "Plan Base";
    razonRubro = "Empresa chica — empezar con Plan Base y escalar después";
  }
  const ticket = (_PERF_PLANES[plan] || 2900) * mult;
  score = Math.max(0, Math.min(100, Math.round(score)));

  // Pitch — mismas plantillas que el backend
  const apertura = ({
    alta:  `Hola, vi que ${empresa} está creciendo rápido — les paso algo que puede ahorrarles tiempo.`,
    media: `Hola, estuve revisando empresas del rubro ${rubro || 'de ustedes'} y ${empresa} me llamó la atención.`,
    baja:  `Hola, te paso info por si a futuro les sirve. Sin apuro.`,
  })[urgencia || 'media'];
  const dolor = ({
    "Metalúrgica / Manufactura":   "Muchas fábricas pierden contactos porque su web no genera confianza técnica.",
    "Agro / Maquinaria agrícola":  "En el agro el cliente investiga mucho antes de llamar — la web define si te llaman o no.",
    "Logística / Transporte":      "Los clientes B2B esperan poder cotizar rápido, sin esperar 2 días a que les llamen.",
    "Servicios B2B / Consultoría": "Si tu web no transmite autoridad en 5 segundos, el lead se va a la competencia.",
    "Salud / Clínicas":            "El 80% de los pacientes googlean antes de sacar turno.",
    "Construcción / Obras":        "Las obras grandes se eligen por respaldo — y el respaldo hoy se mide online.",
  })[rubro] || "Las empresas que no invierten en digital pierden hasta un 30% de oportunidades por mes.";
  const cierre = ({
    "Plan Base":       `Arrancamos con el Plan Base (USD ${_PERF_PLANES["Plan Base"]}): sitio limpio + Google Business + métricas en 30 días.`,
    "Plan Pro":        `Te sugiero el Plan Pro (USD ${_PERF_PLANES["Plan Pro"]}): incluye captación activa de leads, no solo presencia.`,
    "Plan Industrial": `Por el tamaño de ${empresa} va el Plan Industrial (USD ${_PERF_PLANES["Plan Industrial"]}): sistema completo + ventas B2B.`,
    "Estrategico 360": `Lo que encaja acá es un Estratégico 360 (USD ${_PERF_PLANES["Estrategico 360"]}): canal digital entero operando como una máquina.`,
  })[plan] || "";
  const pitch = `${apertura}\n\n${dolor}\n\n${cierre}\n\n¿Te mando un diagnóstico gratis para que veas el estado actual?`;

  return { score, plan_recomendado: plan, pitch_sugerido: pitch, ticket_esperado: Math.round(ticket), razon: razonRubro };
}

// Modo del modal: 'prospecto' (default, llama al backend) o 'bolsa' (local)
let _perfModo = 'prospecto';
let _perfBolsaCtx = null; // { id, empresa, rubro }

function abrirPerfiladoBolsa(id, nombre, rubro) {
  _perfModo = 'bolsa';
  _perfBolsaCtx = { id, empresa: nombre, rubro: rubro || '' };
  document.getElementById('perf-id').value = id;
  document.getElementById('perf-nombre').textContent = nombre;
  // Pre-llenar rubro si vino del card
  if (rubro) {
    const sel = document.getElementById('perf-rubro');
    if ([...sel.options].some(o => o.value === rubro || o.text === rubro)) sel.value = rubro;
  }
  // Pre-llenar tamaño/urgencia si ya hubo perfilado previo
  const cached = _bolsaPerfiladoGet(id);
  if (cached) {
    if (cached.tamano)   document.getElementById('perf-tamano').value   = cached.tamano;
    if (cached.urgencia) document.getElementById('perf-urgencia').value = cached.urgencia;
  }
  document.getElementById('perf-resultado').style.display = 'none';
  document.getElementById('modal-perfilado').classList.add('open');
}

function abrirPerfilado(id, nombre) {
  _perfModo = 'prospecto';
  _perfBolsaCtx = null;
  document.getElementById('perf-id').value = id;
  document.getElementById('perf-nombre').textContent = nombre;
  // Pre-llenar si ya tiene datos
  const p = todosProspectos.find(x => x.id === id);
  if (p) {
    if (p.rubro) document.getElementById('perf-rubro').value = p.rubro;
    if (p.tamano) document.getElementById('perf-tamano').value = p.tamano;
    if (p.urgencia) document.getElementById('perf-urgencia').value = p.urgencia;
  }
  document.getElementById('perf-resultado').style.display = 'none';
  document.getElementById('modal-perfilado').classList.add('open');
}

async function ejecutarPerfilado() {
  const id = document.getElementById('perf-id').value;
  const rubro = document.getElementById('perf-rubro').value;
  const tamano = document.getElementById('perf-tamano').value;
  const urgencia = document.getElementById('perf-urgencia').value;
  if (!rubro) { alert('Seleccioná un rubro.'); return; }

  // ── Modo BOLSA: pasa por el backend (Groq IA) con fallback local ──
  if (_perfModo === 'bolsa' && _perfBolsaCtx) {
    const empresa = _perfBolsaCtx.empresa || 'la empresa';

    // Mostramos un loading suave mientras Groq responde
    const elScore = document.getElementById('perf-score');
    const elPlan  = document.getElementById('perf-plan');
    const elTick  = document.getElementById('perf-ticket');
    const elPitch = document.getElementById('perf-pitch');
    const elRes   = document.getElementById('perf-resultado');
    elScore.textContent = '...';
    elPlan.textContent  = 'Analizando con IA…';
    elTick.textContent  = '';
    elPitch.textContent = 'Generando pitch personalizado para ' + empresa + '…';
    elRes.style.display = 'block';

    let data = null;
    try {
      const res = await apiFetch(
        `${API}/bolsa/${id}/perfilar-ia?rubro=${encodeURIComponent(rubro)}&tamano=${tamano}&urgencia=${urgencia}`,
        { method: 'POST' }
      );
      if (res.ok) {
        data = await res.json();
      }
    } catch (_) { /* sigue al fallback */ }

    // Si el backend no respondió → usamos el cálculo local de siempre
    if (!data) {
      data = _perfilarLeadBolsaLocal(empresa, rubro, tamano, urgencia);
      data.modo = 'fallback-local';
    }

    _bolsaPerfiladoSet(id, {
      score: data.score,
      plan_recomendado: data.plan_recomendado,
      pitch_sugerido: data.pitch_sugerido,
      tamano, urgencia, rubro,
      perfilado_en: new Date().toISOString(),
      modo: data.modo || 'fallback-local',
    });
    elScore.textContent = data.score;
    elPlan.textContent  = data.plan_recomendado + (data.modo === 'ia' ? '  •  IA' : '');
    elTick.textContent  = `Ticket estimado: USD ${Number(data.ticket_esperado).toLocaleString('es-AR')}`;
    elPitch.textContent = data.pitch_sugerido;
    elRes.style.display = 'block';
    setTimeout(() => cargarBolsa(), 800);
    return;
  }

  // ── Modo PROSPECTO: llamada al backend ──
  try {
    const url = `${API}/prospectos/${id}/perfilar?rubro=${encodeURIComponent(rubro)}&tamano=${tamano}&urgencia=${urgencia}`;
    const res = await apiFetch(url, {method:'POST'});
    if (!res.ok) { const d = await res.json(); alert(d.detail || 'Error'); return; }
    const data = await res.json();
    document.getElementById('perf-score').textContent = data.score;
    document.getElementById('perf-plan').textContent = data.plan_recomendado;
    document.getElementById('perf-ticket').textContent = `Ticket estimado: USD ${data.ticket_esperado.toLocaleString('es-AR')}`;
    document.getElementById('perf-pitch').textContent = data.pitch_sugerido;
    document.getElementById('perf-resultado').style.display = 'block';
    // Recargar prospectos para que se vea en la card
    setTimeout(() => cargarProspectos(), 800);
  } catch(e) { alert('Error de conexión.'); }
}

function copiarPitch() {
  const pitch = document.getElementById('perf-pitch').textContent;
  navigator.clipboard.writeText(pitch).then(()=> {
    mostrarToast('Pitch copiado al portapapeles', 'green');
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// ASISTENTE IA — Follow-up + Objeciones (modal único reutilizable)
// ═══════════════════════════════════════════════════════════════════════════

function abrirAsistenteIA(modo, prospId, prospNombre) {
  document.getElementById('ai-prosp-id').value = prospId;
  document.getElementById('ai-modo').value = modo;
  document.getElementById('ai-resultado').style.display = 'none';
  document.getElementById('ai-extra').style.display = 'none';

  const titulo = document.getElementById('ai-modal-titulo');
  const sub    = document.getElementById('ai-modal-sub');
  const formFu = document.getElementById('ai-form-followup');
  const formObj= document.getElementById('ai-form-objecion');
  const btnLab = document.getElementById('ai-btn-label');

  formFu.style.display  = (modo === 'followup') ? 'block' : 'none';
  formObj.style.display = (modo === 'objecion') ? 'block' : 'none';

  if (modo === 'followup') {
    titulo.textContent = 'Follow-up para ' + prospNombre;
    sub.innerHTML = 'Generamos un mensaje listo para mandar por WhatsApp o email — adaptado al contexto de <strong>' + escapeHtml(prospNombre) + '</strong>.';
    btnLab.textContent = 'Generar mensaje';
  } else if (modo === 'objecion') {
    titulo.textContent = 'Responder objeción de ' + prospNombre;
    sub.innerHTML = 'Pegá lo que te dijo el prospecto y te paso una respuesta concreta.';
    btnLab.textContent = 'Generar respuesta';
    document.getElementById('ai-objecion').value = '';
  }

  document.getElementById('modal-asistente-ia').classList.add('open');
}

function cerrarAsistenteIA() {
  document.getElementById('modal-asistente-ia').classList.remove('open');
}

function aiRellenarObjecion(texto) {
  document.getElementById('ai-objecion').value = texto;
}

async function ejecutarAsistenteIA() {
  const modo = document.getElementById('ai-modo').value;
  const id   = document.getElementById('ai-prosp-id').value;
  if (!id) return;

  const btn   = document.getElementById('ai-btn-generar');
  const out   = document.getElementById('ai-output');
  const meta  = document.getElementById('ai-meta');
  const extra = document.getElementById('ai-extra');
  const resBlock = document.getElementById('ai-resultado');

  // UI loading
  btn.disabled = true;
  btn.style.opacity = '0.7';
  resBlock.style.display = 'block';
  meta.textContent = 'Generando con IA...';
  out.textContent = '';
  extra.style.display = 'none';

  try {
    let url = '';
    if (modo === 'followup') {
      const tono = document.getElementById('ai-tono').value || 'directo';
      url = `${API}/prospectos/${id}/followup-ia?tono=${encodeURIComponent(tono)}`;
    } else if (modo === 'objecion') {
      const obj = (document.getElementById('ai-objecion').value || '').trim();
      if (obj.length < 3) {
        meta.textContent = 'Falta texto';
        out.textContent = 'Escribí lo que te dijo el prospecto en el campo de arriba.';
        btn.disabled = false; btn.style.opacity = '1';
        return;
      }
      url = `${API}/prospectos/${id}/objecion-ia?objecion=${encodeURIComponent(obj)}`;
    } else {
      btn.disabled = false; btn.style.opacity = '1';
      return;
    }

    const res = await apiFetch(url, { method: 'POST' });
    if (!res.ok) {
      const d = await res.json().catch(()=>({}));
      meta.textContent = 'Error';
      out.textContent = d.detail || 'No se pudo generar. Probá de nuevo en un momento.';
      btn.disabled = false; btn.style.opacity = '1';
      return;
    }
    const data = await res.json();

    const fuenteLabel = (data.modo === 'ia') ? '✨ IA' : '📋 Plantilla';
    if (modo === 'followup') {
      meta.textContent = `Follow-up • ${fuenteLabel}` + (data.dias_sin_responder != null ? ` • ${data.dias_sin_responder} días sin respuesta` : '');
      out.textContent = data.mensaje || '';
      if (data.estrategia) {
        extra.style.display = 'block';
        extra.innerHTML = '<strong style="color:#c084fc;">Estrategia:</strong> ' + escapeHtml(data.estrategia);
      }
    } else if (modo === 'objecion') {
      meta.textContent = `Respuesta a objeción • ${fuenteLabel}`;
      out.textContent = data.respuesta || '';
      const partes = [];
      if (data.explicacion)        partes.push('<strong style="color:#c084fc;">Por qué funciona:</strong> ' + escapeHtml(data.explicacion));
      if (data.siguiente_pregunta) partes.push('<strong style="color:#c084fc;">Pregunta de seguimiento:</strong> ' + escapeHtml(data.siguiente_pregunta));
      if (partes.length) {
        extra.style.display = 'block';
        extra.innerHTML = partes.join('<br><br>');
      }
    }
  } catch(e) {
    meta.textContent = 'Error de conexión';
    out.textContent = 'Revisá tu conexión y probá de nuevo.';
  } finally {
    btn.disabled = false; btn.style.opacity = '1';
  }
}

function copiarOutputIA() {
  const txt = document.getElementById('ai-output').textContent || '';
  if (!txt) return;
  navigator.clipboard.writeText(txt).then(() => {
    mostrarToast('Copiado al portapapeles', 'green');
  }).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = txt; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch(_) {}
    document.body.removeChild(ta);
    mostrarToast('Copiado al portapapeles', 'green');
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// ASISTENTE COMUNIDAD — Generar post con IA
// ═══════════════════════════════════════════════════════════════════════════

function abrirAsistenteComunidad() {
  document.getElementById('aic-resultado').style.display = 'none';
  // Pre-rellenar con datos parciales si el usuario ya escribió algo
  const titMan = (document.getElementById('post-titulo').value || '').trim();
  const cuerpoMan = (document.getElementById('post-cuerpo').value || '').trim();
  const elDatos = document.getElementById('aic-datos');
  if (!elDatos.value) {
    if (titMan || cuerpoMan) {
      elDatos.value = (titMan ? titMan + '. ' : '') + cuerpoMan;
    }
  }
  document.getElementById('modal-asistente-comunidad').classList.add('open');
}

async function ejecutarAsistenteComunidad() {
  const tipo = document.getElementById('post-tipo').value || 'tip';
  const datos = (document.getElementById('aic-datos').value || '').trim();
  if (datos.length < 5) {
    alert('Escribí al menos unas palabras con los datos clave para que la IA pueda redactar el post.');
    return;
  }

  const btn   = document.getElementById('aic-btn-generar');
  const meta  = document.getElementById('aic-meta');
  const elTit = document.getElementById('aic-titulo');
  const elCue = document.getElementById('aic-cuerpo');
  const resBlock = document.getElementById('aic-resultado');

  btn.disabled = true; btn.style.opacity = '0.7';
  resBlock.style.display = 'block';
  meta.textContent = 'Generando post con IA...';
  elTit.textContent = '...';
  elCue.textContent = '...';

  try {
    const url = `${API}/comunidad/asistente-ia?tipo=${encodeURIComponent(tipo)}&datos=${encodeURIComponent(datos)}`;
    const res = await apiFetch(url, { method: 'POST' });
    if (!res.ok) {
      const d = await res.json().catch(()=>({}));
      meta.textContent = 'Error';
      elTit.textContent = '—';
      elCue.textContent = d.detail || 'No se pudo generar. Probá de nuevo en un momento.';
      return;
    }
    const data = await res.json();
    const fuenteLabel = (data.modo === 'ia') ? '✨ IA' : '📋 Plantilla';
    meta.textContent = `Post tipo "${tipo}" • ${fuenteLabel}`;
    elTit.textContent = data.titulo || '';
    elCue.textContent = data.cuerpo || '';
  } catch(e) {
    meta.textContent = 'Error de conexión';
    elCue.textContent = 'Revisá tu conexión y probá de nuevo.';
  } finally {
    btn.disabled = false; btn.style.opacity = '1';
  }
}

function usarPostGenerado() {
  const tit = document.getElementById('aic-titulo').textContent || '';
  const cue = document.getElementById('aic-cuerpo').textContent || '';
  if (!tit && !cue) return;
  document.getElementById('post-titulo').value = tit;
  document.getElementById('post-cuerpo').value = cue;
  document.getElementById('modal-asistente-comunidad').classList.remove('open');
  mostrarToast('Post cargado en el editor — revisá y publicá', 'green');
}

// ─── COMPONENTE COMPARTIDO: TARJETA DE LEAD ──────────────────────────────
// Renderiza una tarjeta unificada para los 3 tiers (basico/calificado/premium)
// con la misma estructura base y "skin" diferenciada por nivel. La data
// sensible (telefono/email/whatsapp/URL del web/handle de IG/nombre del
// contacto) NUNCA se muestra acá — solo se exponen booleans como teasers.
//
// Uso:
//   renderLeadCard(lead, {
//     accion: '<button onclick="...">Comprar</button>',  // HTML del CTA
//     mostrarCosto: true,                                  // false en básicos
//   })
//
// Tarifa de referencia: 1 crédito ≈ USD 0.10 (paquete Impulso 100 cr / USD 10).
function renderLeadCard(lead, opts = {}) {
  const tier = (lead.tier || 'basico').toLowerCase();
  const pi = paisInfo(lead.pais || 'AR');
  const accion = opts.accion || '';
  const mostrarCosto = opts.mostrarCosto !== false && tier !== 'basico';

  // Skin por tier — mismo layout, distinto vestido.
  let skinStyle = '';
  let skinBadge = '';
  if (tier === 'premium') {
    skinStyle = `
      background: linear-gradient(135deg, rgba(250,204,21,0.07), rgba(250,204,21,0.02) 60%, var(--bg2));
      border: 1px solid rgba(250,204,21,0.45);
      box-shadow: 0 0 0 1px rgba(250,204,21,0.08), 0 4px 18px -8px rgba(250,204,21,0.25);
    `;
    skinBadge = `<span style="background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;font-size:.65rem;font-weight:900;padding:3px 8px;border-radius:4px;letter-spacing:.04em;">💎 PREMIUM</span>`;
  } else if (tier === 'calificado') {
    skinStyle = `
      background: linear-gradient(135deg, rgba(96,165,250,0.06), var(--bg2) 70%);
      border: 1px solid rgba(96,165,250,0.4);
    `;
    skinBadge = `<span style="background:rgba(96,165,250,0.18);color:#93c5fd;font-size:.65rem;font-weight:900;padding:3px 8px;border-radius:4px;letter-spacing:.04em;border:1px solid rgba(96,165,250,0.3);">⭐ CALIFICADO</span>`;
  } else {
    skinStyle = `background: var(--bg2); border: 1px solid var(--border);`;
    skinBadge = `<span style="background:rgba(255,255,255,0.04);color:var(--text-dim);font-size:.65rem;font-weight:700;padding:3px 8px;border-radius:4px;letter-spacing:.04em;border:1px solid var(--border);">BÁSICO</span>`;
  }

  // ── PILLS DE TEASER (lo que el aliado va a desbloquear con el lead) ──
  const teaserPill = (label, color, activo) => {
    const palette = {
      green:  { bg:'rgba(74,222,128,0.15)',  fg:'#86efac', br:'rgba(74,222,128,0.3)' },
      blue:   { bg:'rgba(96,165,250,0.15)', fg:'#93c5fd', br:'rgba(96,165,250,0.3)' },
      purple: { bg:'rgba(168,85,247,0.15)', fg:'#c084fc', br:'rgba(168,85,247,0.3)' },
    }[color] || { bg:'rgba(255,255,255,0.04)', fg:'var(--text-dim)', br:'var(--border)' };
    if (!activo) {
      return `<span style="background:transparent;color:var(--text-dim);border:1px dashed var(--border);padding:2px 8px;border-radius:99px;font-size:.7rem;font-weight:600;opacity:.5;display:inline-flex;align-items:center;gap:4px;">— ${label}</span>`;
    }
    return `<span style="background:${palette.bg};color:${palette.fg};border:1px solid ${palette.br};padding:2px 8px;border-radius:99px;font-size:.7rem;font-weight:700;display:inline-flex;align-items:center;gap:4px;">✓ ${label}</span>`;
  };

  const pillsRow = `
    <div style="display:flex;flex-wrap:wrap;gap:5px;margin:10px 0 4px;">
      ${teaserPill('Web',     'blue',   lead.tiene_web)}
      ${teaserPill('Redes',   'purple', lead.tiene_redes)}
      ${teaserPill('Contacto','green',  lead.tiene_contacto)}
    </div>
  `;

  // ── SCORE DE CALIDAD ─────────────────────────────────────────────────
  const score = lead.score_calidad || 50;
  const scoreColor = score >= 85 ? '#4ade80' : (score >= 70 ? '#fbbf24' : '#fb7185');
  const scoreBlock = `
    <div style="display:flex;align-items:center;gap:10px;margin-top:8px;">
      <div style="position:relative;width:46px;height:46px;flex-shrink:0;">
        <svg viewBox="0 0 36 36" style="width:46px;height:46px;transform:rotate(-90deg);">
          <circle cx="18" cy="18" r="15.9" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="3"></circle>
          <circle cx="18" cy="18" r="15.9" fill="none" stroke="${scoreColor}" stroke-width="3"
            stroke-dasharray="${score}, 100" stroke-linecap="round"></circle>
        </svg>
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:.78rem;font-weight:900;color:${scoreColor};">${score}</div>
      </div>
      <div style="flex:1;min-width:0;">
        <div style="font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);">Calidad</div>
        <div style="font-size:.78rem;color:var(--text);font-weight:600;">${score >= 85 ? 'Alta' : score >= 70 ? 'Media' : 'Estándar'}</div>
      </div>
    </div>
  `;

  // ── COSTO — todos los leads son gratis (los créditos son sólo para Jarvis IA)
  const costoBlock = `
      <div style="margin:10px 0 8px;">
        <span style="background:rgba(74,222,128,0.15);color:#86efac;border:1px solid rgba(74,222,128,0.3);padding:3px 10px;border-radius:99px;font-size:.72rem;font-weight:800;">GRATIS</span>
      </div>
    `;

  // ── OBSERVACIÓN PREVIEW (truncada para no spoilear info clave) ──────
  const obsPreview = (lead.tiene_observacion && lead.observacion)
    ? `<div style="margin-top:8px;padding:8px 10px;background:rgba(255,255,255,0.025);border-left:2px solid var(--border);border-radius:4px;font-size:.74rem;color:var(--text-muted);font-style:italic;line-height:1.4;">"${escapeHtml(lead.observacion.slice(0, 110))}${lead.observacion.length > 110 ? '…' : ''}"</div>`
    : '';

  // ── RECICLADO: si el lead ya pasó por la bolsa, avisar al que lo va a
  //    reclamar y dejarle ver el historial para no repetir el laburo. ──
  const recicladoBlock = (lead.reciclados > 0)
    ? `<div style="margin-top:8px;display:flex;align-items:center;justify-content:space-between;gap:8px;
              background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.25);
              border-radius:8px;padding:7px 10px;">
         <span style="font-size:.73rem;color:#c084fc;font-weight:700;">♻ Reaprovechado · ${lead.intentos||0} intento(s) previo(s)</span>
         <button onclick="verHistorialLead(${lead.id})"
           style="background:none;border:none;color:#c084fc;font-size:.73rem;font-weight:700;cursor:pointer;padding:0;text-decoration:underline;">Ver historial</button>
       </div>`
    : '';

  // ── ARMADO FINAL ────────────────────────────────────────────────────
  return `
    <div style="${skinStyle} border-radius:12px; padding:16px; display:flex; flex-direction:column; gap:0; position:relative;">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px; margin-bottom:6px;">
        <div style="font-size:1rem; font-weight:800; color:var(--text); line-height:1.25; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(lead.empresa || 'Sin nombre')}</div>
        <div style="display:flex; flex-direction:column; align-items:flex-end; gap:4px; flex-shrink:0;">
          <span title="${escapeHtml(pi.nombre)}" style="font-size:1.3rem; line-height:1;">${pi.bandera}</span>
          ${skinBadge}
        </div>
      </div>
      <div style="font-size:.75rem; color:var(--text-dim); margin-bottom:6px;">
        <i class="fa-solid fa-location-dot"></i>
        ${escapeHtml(lead.ciudad || '')}${lead.ciudad ? ', ' : ''}${escapeHtml(pi.nombre)}
      </div>
      <div style="font-size:.86rem; color:var(--text); font-weight:500; margin-bottom:2px;">
        <span style="color:var(--text-dim);font-size:.75rem;">🏭</span> ${escapeHtml(lead.rubro || '—')}
      </div>
      ${scoreBlock}
      ${pillsRow}
      ${obsPreview}
      ${recicladoBlock}
      ${costoBlock}
      <div style="margin-top:auto;padding-top:8px;">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;font-size:.72rem;color:var(--text-dim);">
          <i class="fa-solid fa-lock" style="font-size:.7rem;"></i>
          <span style="font-style:italic;">Contacto y URLs visibles al reclamar</span>
        </div>
        ${accion}
      </div>
    </div>
  `;
}


// ─── MARKETPLACE DE LEADS PREMIUM ────────────────────────────────────
// ─── HELPERS NUEVOS PARA SELECTOR DE CATEGORÍAS DE BOLSA ───────────────────
// El portal ahora muestra 3 tarjetas grandes (Básicos GRATIS / Calificados /
// Premium) y abre solo la categoría que el aliado elige. "Mis Reclamos" queda
// siempre visible al tope con el badge de límite 3/3 y el contador 48hs.

function mostrarCategoriaBolsa(tier) {
  const selector = document.getElementById('bolsa-vista-selector');
  const vBas = document.getElementById('bolsa-vista-basico');
  const vCal = document.getElementById('bolsa-vista-calificado');
  const vPre = document.getElementById('bolsa-vista-premium');
  if (!selector || !vBas || !vCal || !vPre) return;
  selector.style.display = 'none';
  vBas.style.display = (tier === 'basico')     ? 'block' : 'none';
  vCal.style.display = (tier === 'calificado') ? 'block' : 'none';
  vPre.style.display = (tier === 'premium')    ? 'block' : 'none';
  // Llevar la vista al panel elegido
  const target = (tier === 'basico') ? vBas : (tier === 'calificado') ? vCal : vPre;
  setTimeout(() => target.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
}

function volverASelectorBolsa() {
  const selector = document.getElementById('bolsa-vista-selector');
  const vBas = document.getElementById('bolsa-vista-basico');
  const vCal = document.getElementById('bolsa-vista-calificado');
  const vPre = document.getElementById('bolsa-vista-premium');
  if (!selector || !vBas || !vCal || !vPre) return;
  selector.style.display = 'block';
  vBas.style.display = 'none';
  vCal.style.display = 'none';
  vPre.style.display = 'none';
  setTimeout(() => selector.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
}

// Helper: actualiza saldo en los 3 lugares donde se muestra
// ─── BÚSQUEDA EN LA BOLSA ────────────────────────────────────────────────
// Registro de funciones de re-render por vista; los inputs llaman acá.
window._repintarBolsa = window._repintarBolsa || {};

function _matchLeadTexto(l, q) {
  if (!q) return true;
  const t = q.toLowerCase();
  return [l.empresa, l.rubro, l.ciudad].some(v => (v || '').toLowerCase().includes(t));
}

function _actualizarSaldoMkt(saldo) {
  // La bolsa ya no muestra saldo de créditos (los leads son gratis).
  // El saldo vive en el dashboard ("Créditos Jarvis IA"). No-op por compatibilidad.
}

// Helper: actualiza el contador "X disponibles" en una tarjeta de categoría
function _setContadorCategoria(tier, n) {
  const el = document.getElementById(`cnt-bolsa-${tier}`);
  if (el) el.textContent = n === 1 ? '1 disponible' : `${n} disponibles`;
}

// Helper: arma el filtro de países encima del `gridAnchor` que se le pase.
// Reemplaza el filtro previo si ya existe. callback(p) recibe el código de país
// elegido o '' para "todos".
function _renderFiltroPaises(gridAnchor, idFiltro, paisesUnicos, colorActivo, onChange) {
  const prev = document.getElementById(idFiltro);
  if (prev) prev.remove();
  if (!paisesUnicos || paisesUnicos.length <= 1) return;
  const fc = document.createElement('div');
  fc.id = idFiltro;
  fc.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin:0;padding:14px 16px 6px;';
  const allBtn = document.createElement('button');
  allBtn.textContent = '🌎 Todos';
  allBtn.style.cssText = `padding:6px 14px;border-radius:20px;border:1px solid var(--border);background:${colorActivo};color:#000;font-size:.8rem;cursor:pointer;font-weight:700;`;
  allBtn.onclick = () => {
    [...fc.querySelectorAll('button')].forEach(b => { b.style.background='transparent'; b.style.color='var(--text-muted)'; });
    allBtn.style.background = colorActivo; allBtn.style.color = '#000';
    onChange('');
  };
  fc.appendChild(allBtn);
  paisesUnicos.forEach(p => {
    const pi = paisInfo(p);
    const btn = document.createElement('button');
    btn.textContent = `${pi.bandera} ${pi.nombre}`;
    btn.style.cssText = 'padding:6px 14px;border-radius:20px;border:1px solid var(--border);background:transparent;color:var(--text-muted);font-size:.8rem;cursor:pointer;';
    btn.onclick = () => {
      [...fc.querySelectorAll('button')].forEach(b => { b.style.background='transparent'; b.style.color='var(--text-muted)'; });
      btn.style.background = colorActivo; btn.style.color = '#000';
      onChange(p);
    };
    fc.appendChild(btn);
  });
  gridAnchor.before(fc);
}

async function cargarMarketplace() {
  if (!aliado) return;
  const gridPrem = document.getElementById('grid-premium');
  const gridCal  = document.getElementById('grid-calificado');
  if (!gridPrem || !gridCal) return;
  try {
    const res = await apiFetch(`${API}/bolsa/marketplace?codigo_aliado=${aliado.codigo}`);
    if (!res.ok) return;
    const data = await res.json();

    _actualizarSaldoMkt(data.saldo_creditos);

    // Separar por tier — el endpoint devuelve premium + calificado mezclados
    const leadsPremium    = data.leads.filter(l => l.tier === 'premium');
    const leadsCalificado = data.leads.filter(l => l.tier === 'calificado');

    _setContadorCategoria('premium',    leadsPremium.length);
    _setContadorCategoria('calificado', leadsCalificado.length);

    // ── Función genérica que renderiza una grilla de un tier dado ──
    function renderGrillaTier(grid, leadsDelTier, tierLabel, idFiltro, colorActivo, gridAnchor) {
      if (!leadsDelTier.length) {
        grid.innerHTML = `<div style="text-align:center; padding:32px; color:var(--text-dim); grid-column:1/-1;">
          <i class="fa-solid fa-gem" style="font-size:1.5rem;color:var(--text-dim);opacity:.4;"></i>
          <p style="margin-top:10px;">No hay leads ${tierLabel} disponibles en este momento.</p>
          <p style="font-size:.8rem;margin-top:4px;">Volvé en unas horas — cargamos leads nuevos a diario.</p>
        </div>`;
        const prev = document.getElementById(idFiltro);
        if (prev) prev.remove();
        return;
      }
      const paisesUnicos = [...new Set(leadsDelTier.map(l => l.pais || 'AR'))];
      let filtroPais = '';

      function pintar() {
        const tierKey = grid.id === 'grid-premium' ? 'premium' : 'calificado';
        const q = (document.getElementById(`bolsa-buscar-${tierKey}`)?.value || '').trim();
        let filtrados = filtroPais
          ? leadsDelTier.filter(l => (l.pais || 'AR') === filtroPais)
          : leadsDelTier;
        filtrados = filtrados.filter(l => _matchLeadTexto(l, q));
        if (!filtrados.length) {
          grid.innerHTML = `<div style="text-align:center; padding:24px; color:var(--text-dim); grid-column:1/-1;">Sin resultados para esa búsqueda o filtro.</div>`;
          return;
        }
        grid.innerHTML = filtrados.map(l => {
          const accion = `<button onclick="comprarLead(${l.id})" style="width:100%;background:var(--green);color:#000;border:none;border-radius:8px;padding:10px;font-size:.82rem;font-weight:800;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;">
                <i class="fa-solid fa-hand-sparkles"></i> Reclamar gratis
              </button>`;
          return renderLeadCard(l, { accion });
        }).join('');
      }
      _renderFiltroPaises(gridAnchor, idFiltro, paisesUnicos, colorActivo, p => { filtroPais = p; pintar(); });
      window._repintarBolsa[grid.id === 'grid-premium' ? 'premium' : 'calificado'] = pintar;
      pintar();
    }

    // Renderizar Premium dentro de su panel
    renderGrillaTier(
      gridPrem, leadsPremium, 'premium',
      'mkt-filtro-paises-prem', 'var(--amber)',
      gridPrem
    );
    // Renderizar Calificado dentro de su panel
    renderGrillaTier(
      gridCal, leadsCalificado, 'calificados',
      'mkt-filtro-paises-cal', '#60a5fa',
      gridCal
    );

  } catch(e) { console.error('marketplace:', e); }
}

async function comprarLead(id) {
  // Mantenemos el nombre por compatibilidad, pero ahora reclamar es GRATIS:
  // los créditos quedaron sólo para Jarvis IA.
  // Mismo modal de confirmación que la bolsa básica (antes era un confirm() nativo).
  const overlay = document.createElement('div');
  overlay.id = 'modal-reclamar-mkt';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:9999;display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = `
    <div style="background:#1e293b;border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:32px;max-width:420px;width:90%;text-align:center;">
      <div style="font-size:2.2rem;margin-bottom:12px;">⏰</div>
      <h3 style="margin-bottom:8px;font-size:1.15rem;">¿Reclamar este lead?</h3>
      <div style="background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:10px;padding:16px;margin:16px 0;text-align:left;">
        <p style="margin:0 0 8px;color:#f59e0b;font-weight:700;font-size:.9rem;">⚠️ Importante antes de reclamar:</p>
        <ul style="margin:0;padding-left:18px;color:#cbd5e1;font-size:.85rem;line-height:1.8;">
          <li>El contador de <strong style="color:#f59e0b;">48 horas empieza ahora</strong>, no cuando lo contactes.</li>
          <li>Si no marcás el lead como <strong>"Contactado"</strong> dentro de ese tiempo, vuelve automáticamente a la bolsa.</li>
          <li>Reclamá solo cuando estés lista para contactarlo pronto.</li>
        </ul>
      </div>
      <div style="display:flex;gap:10px;margin-top:8px;">
        <button onclick="document.getElementById('modal-reclamar-mkt').remove()" style="flex:1;padding:12px;border-radius:10px;border:1px solid rgba(255,255,255,0.1);background:transparent;color:#94a3b8;font-weight:700;cursor:pointer;">Cancelar</button>
        <button id="btn-confirmar-reclamo-mkt" style="flex:1;padding:12px;border-radius:10px;border:none;background:#f97316;color:#000;font-weight:800;cursor:pointer;">Sí, reclamar ahora</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  document.getElementById('btn-confirmar-reclamo-mkt').onclick = async () => {
    document.getElementById('modal-reclamar-mkt').remove();
    try {
      const res = await apiFetch(`${API}/bolsa/${id}/comprar?codigo_aliado=${aliado.codigo}`, {method:'POST'});
      const data = await res.json();
      if (!res.ok) {
        const detail = data.detail;
        mostrarToast(typeof detail === 'string' ? detail : (detail?.mensaje || 'Error al reclamar'), 'red');
        return;
      }
      mostrarToast('✅ Lead reclamado gratis — el contacto ya está desbloqueado', 'green');
      await cargarMarketplace();
      await cargarBolsa();
    } catch(e) { mostrarToast('Error de conexión.', 'red'); }
  };
}

// ─── COMPRA DE CRÉDITOS POR TRANSFERENCIA (v1.7) ─────────────────────
let _ultimaSolicitudCreditos = null;  // guarda la última solicitud activa
let _monedaCreditos = 'ars';
let _metodosUsdCreditos = [];        // v2.2 — métodos USD disponibles (USDT/Payoneer)
function aplicarMetodoUsdCreditos(id){
  const m = (_metodosUsdCreditos || []).find(x => x.id === id) || _metodosUsdCreditos[0];
  if(!m) return;
  document.getElementById('cred-usd-metodo').textContent       = m.metodo || '—';
  document.getElementById('cred-usd-destinatario').textContent = m.destinatario || '—';
  document.getElementById('cred-usd-etiqueta').textContent     = m.etiqueta_dest || 'Destinatario';
  document.getElementById('cred-usd-notas').textContent        = m.notas || '';
  const redRow = document.getElementById('cred-usd-red-row');
  if (m.red && m.red.trim()) { document.getElementById('cred-usd-red').textContent = m.red; redRow.style.display = 'flex'; }
  else { redRow.style.display = 'none'; }
  _renderCredBancoPayoneer(m.banco);  // v2.3 — datos bancarios Payoneer si existen
  // estado visual de los tabs
  const t1 = document.getElementById('cred-mtab-usdt'), t2 = document.getElementById('cred-mtab-payoneer');
  if(t1 && t2){
    const on = (b)=>{ b.style.background='rgba(74,222,128,0.12)'; b.style.color='#4ade80'; b.style.borderColor='rgba(74,222,128,0.4)'; };
    const off= (b)=>{ b.style.background='transparent'; b.style.color='var(--text-muted)'; b.style.borderColor='rgba(255,255,255,0.12)'; };
    (id==='payoneer') ? (on(t2),off(t1)) : (on(t1),off(t2));
  }
}
function _renderCredBancoPayoneer(b){
  const block = document.getElementById('cred-usd-banco-block');
  if(!block) return;
  if(!b || !(b.cuenta || b.aba || b.swift)){ block.style.display='none'; block.innerHTML=''; return; }
  const esc = (t)=> String(t==null?'':t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const row = (label, val, id, copiable)=>{
    if(!val) return '';
    const btn = copiable
      ? `<button onclick="copiarCredDato('${id}', this)" style="background:rgba(74,222,128,0.1); color:var(--green); border:1px solid rgba(74,222,128,0.3); padding:7px 12px; border-radius:6px; font-size:.78rem; font-weight:700; cursor:pointer; flex-shrink:0;"><i class="fa-solid fa-copy"></i> Copiar</button>`
      : '';
    return `<div class="cred-dato-row" style="display:flex; justify-content:space-between; align-items:center; gap:8px; padding:10px 12px; background:rgba(255,255,255,0.02); border-radius:8px;">`
      + `<div style="flex:1; min-width:0;"><div style="font-size:.7rem; color:var(--text-dim); margin-bottom:2px;">${label}</div>`
      + `<div id="${id}" style="font-size:.88rem; font-weight:700; color:var(--text); font-family:'Courier New',monospace; word-break:break-all;">${esc(val)}</div></div>${btn}</div>`;
  };
  block.innerHTML =
    `<div style="font-size:.7rem; color:#4ade80; font-weight:800; text-transform:uppercase; letter-spacing:.5px; margin-bottom:2px;"><i class="fa-solid fa-building-columns"></i> Transferencia bancaria en USD (desde cualquier banco)</div>`
    + row('Beneficiario', b.beneficiario, 'cred-pyb-benef', true)
    + row('Banco', b.banco, 'cred-pyb-banco', false)
    + row('Dirección del banco', b.direccion, 'cred-pyb-dir', false)
    + row('Número de cuenta', b.cuenta, 'cred-pyb-cuenta', true)
    + row('Tipo de cuenta', b.tipo_cuenta, 'cred-pyb-tipo', false)
    + row('ABA / Routing number', b.aba, 'cred-pyb-aba', true)
    + row('Código SWIFT / BIC', b.swift, 'cred-pyb-swift', true);
  block.style.display = 'flex';
}
function cambiarMetodoUsdCreditos(id){ aplicarMetodoUsdCreditos(id); }
          // 'ars' | 'usd' — moneda elegida en el toggle
let _paquetesCreditosCache = null;    // último resultado de /paquetes-creditos

async function abrirModalComprarCreditos() {
  if (!aliado) return;
  // Reset a vista 1
  document.getElementById('creditos-vista-paquetes').style.display = 'block';
  document.getElementById('creditos-vista-instrucciones').style.display = 'none';
  document.getElementById('creditos-modal-titulo').textContent = 'Comprar créditos';
  document.getElementById('creditos-paquetes-loading').style.display = 'block';
  document.getElementById('creditos-paquetes-grid').style.display = 'none';
  document.getElementById('modal-comprar-creditos').classList.add('open');

  try {
    const res = await apiFetch(`${API}/paquetes-creditos`);
    const data = await res.json();
    if (!res.ok) { alert(data.detail || 'No se pudieron cargar los paquetes'); return; }
    _paquetesCreditosCache = data;
    renderPaquetesCreditos();
  } catch(e) {
    console.error('paquetes:', e);
    alert('Error de conexión.');
  }
}

function cambiarMonedaCreditos(moneda) {
  _monedaCreditos = (moneda === 'usd') ? 'usd' : 'ars';

  // Estilos del toggle
  const tabArs = document.getElementById('cred-tab-ars');
  const tabUsd = document.getElementById('cred-tab-usd');
  const esArs = (_monedaCreditos === 'ars');
  if (tabArs) {
    tabArs.style.background = esArs ? 'var(--amber)' : 'transparent';
    tabArs.style.color      = esArs ? '#000' : 'var(--text-muted)';
    tabArs.style.fontWeight = esArs ? '800' : '700';
  }
  if (tabUsd) {
    tabUsd.style.background = !esArs ? '#4ade80' : 'transparent';
    tabUsd.style.color      = !esArs ? '#000' : 'var(--text-muted)';
    tabUsd.style.fontWeight = !esArs ? '800' : '700';
  }

  // Texto informativo
  const infoArs = document.getElementById('cred-info-ars');
  const infoUsd = document.getElementById('cred-info-usd');
  if (infoArs) infoArs.style.display = esArs ? 'block' : 'none';
  if (infoUsd) infoUsd.style.display = esArs ? 'none' : 'block';

  // Re-render con la moneda elegida
  if (_paquetesCreditosCache) renderPaquetesCreditos();
}

function renderPaquetesCreditos() {
  const data = _paquetesCreditosCache;
  if (!data) return;
  const esUsd = (_monedaCreditos === 'usd');

  const grid = document.getElementById('creditos-paquetes-grid');
  grid.innerHTML = data.paquetes.map(p => {
    const ahorroStr = p.id === 'impulso' ? '' :
      `<span style="color:var(--green); font-size:.72rem; font-weight:800; background:rgba(74,222,128,0.1); padding:2px 7px; border-radius:4px; margin-left:6px;">−${Math.round((1 - p.usd_por_credito / 0.10) * -100 * -1)}%</span>`;
    const destacadoBorder = p.destacado ? 'border:2px solid var(--amber);' : 'border:1px solid var(--border);';
    const destacadoBadge = p.destacado
      ? `<div style="position:absolute; top:-10px; right:14px; background:var(--amber); color:#000; padding:2px 10px; border-radius:50px; font-size:.7rem; font-weight:900; letter-spacing:.5px;">EL MÁS ELEGIDO</div>`
      : '';

    // Precio principal según moneda elegida
    const precioPrincipal = esUsd
      ? `USD ${p.precio_usd.toFixed(2)}`
      : `ARS ${p.precio_ars.toLocaleString('es-AR')}`;
    const precioSecundario = esUsd
      ? `≈ ARS ${p.precio_ars.toLocaleString('es-AR')} (orientativo)`
      : `USD ${p.precio_usd}`;

    return `
      <div style="position:relative; ${destacadoBorder} border-radius:12px; padding:18px; cursor:pointer; transition:all .2s; background:rgba(255,255,255,0.02);"
           onclick="seleccionarPaquete('${p.id}')"
           onmouseenter="this.style.background='rgba(250,204,21,0.06)'"
           onmouseleave="this.style.background='rgba(255,255,255,0.02)'">
        ${destacadoBadge}
        <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap;">
          <div style="flex:1; min-width:160px;">
            <div style="font-size:1.1rem; font-weight:800; color:var(--text);">${p.nombre}${ahorroStr}</div>
            <div style="font-size:.85rem; color:var(--text-muted); margin-top:4px;">${p.descripcion}</div>
            <div style="font-size:1.4rem; font-weight:900; color:var(--amber); margin-top:10px;">${p.creditos.toLocaleString('es-AR')} <span style="font-size:.8rem; font-weight:700; color:var(--text-muted);">créditos</span></div>
          </div>
          <div style="text-align:right;">
            <div style="font-size:.7rem; color:var(--text-dim); text-transform:uppercase; font-weight:700;">Precio</div>
            <div style="font-size:1.2rem; font-weight:900; color:var(--text);">${precioPrincipal}</div>
            <div style="font-size:.78rem; color:var(--text-muted);">${precioSecundario}</div>
          </div>
        </div>
      </div>
    `;
  }).join('');
  document.getElementById('creditos-paquetes-loading').style.display = 'none';
  grid.style.display = 'flex';
}

function cerrarModalCreditos() {
  document.getElementById('modal-comprar-creditos').classList.remove('open');
}

async function seleccionarPaquete(paqueteId) {
  if (!aliado) return;
  // Mostrar feedback de loading sin romper UI
  document.getElementById('creditos-paquetes-loading').style.display = 'block';
  document.getElementById('creditos-paquetes-grid').style.display = 'none';

  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/solicitar-creditos`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({paquete_id: paqueteId, moneda: _monedaCreditos}),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.detail || 'No se pudo generar la solicitud');
      // Volver a la vista de paquetes
      document.getElementById('creditos-paquetes-loading').style.display = 'none';
      document.getElementById('creditos-paquetes-grid').style.display = 'flex';
      return;
    }
    _ultimaSolicitudCreditos = data.solicitud;
    mostrarInstruccionesTransferencia(data);
  } catch(e) {
    console.error('solicitar:', e);
    alert('Error de conexión.');
    document.getElementById('creditos-paquetes-loading').style.display = 'none';
    document.getElementById('creditos-paquetes-grid').style.display = 'flex';
  }
}

function mostrarInstruccionesTransferencia(data) {
  const s = data.solicitud;
  const dp = data.datos_pago || data.datos_bancarios || {};
  const inst = data.instrucciones;
  const esUsd = (s.moneda === 'usd');

  document.getElementById('creditos-modal-titulo').textContent = `Solicitud ${s.codigo_referencia}`;
  document.getElementById('cred-pkg-nombre').textContent = s.paquete_nombre;
  document.getElementById('cred-pkg-creditos').textContent = `${s.creditos.toLocaleString('es-AR')} créditos`;

  // Banner de monto: cambia ARS/USD según moneda
  document.getElementById('cred-pkg-moneda').textContent = esUsd ? 'USD' : 'ARS';
  document.getElementById('cred-pkg-monto').textContent = esUsd
    ? s.precio_usd.toFixed(2)
    : s.precio_ars.toLocaleString('es-AR');

  // Mostrar/ocultar bloques según moneda
  document.getElementById('cred-bloque-ars').style.display = esUsd ? 'none' : 'block';
  document.getElementById('cred-bloque-usd').style.display = esUsd ? 'block' : 'none';

  if (esUsd) {
    // v2.2 — soporta múltiples métodos USD (USDT / Payoneer)
    _metodosUsdCreditos = (dp.metodos_usd && dp.metodos_usd.length)
      ? dp.metodos_usd
      : [{ id:'usdt', metodo:dp.metodo, destinatario:dp.destinatario, etiqueta_dest:dp.etiqueta_dest, red:dp.red, notas:dp.notas }];
    document.getElementById('cred-usd-titular').textContent = dp.titular || '—';
    const tabs = document.getElementById('cred-usd-metodo-tabs');
    if (tabs) tabs.style.display = (_metodosUsdCreditos.length > 1) ? 'flex' : 'none';
    aplicarMetodoUsdCreditos(_metodosUsdCreditos[0].id || 'usdt');
  } else {
    document.getElementById('cred-titular').textContent = dp.titular || '—';
    document.getElementById('cred-banco').textContent   = dp.banco   || '—';
    document.getElementById('cred-alias').textContent   = dp.alias   || '—';
    document.getElementById('cred-cbu').textContent     = dp.cbu     || '—';
  }

  document.getElementById('cred-ref').textContent = s.codigo_referencia;
  document.getElementById('cred-whatsapp-btn').href = dp.whatsapp_url || '#';
  document.getElementById('cred-comprobante-url').value = '';

  // Formatear fecha de vencimiento
  try {
    const exp = new Date(s.expires_at);
    document.getElementById('cred-vencimiento').textContent =
      exp.toLocaleDateString('es-AR', {day:'2-digit', month:'2-digit', year:'numeric'}) + ' ' +
      exp.toLocaleTimeString('es-AR', {hour:'2-digit', minute:'2-digit'}) + 'hs';
  } catch(e) {
    document.getElementById('cred-vencimiento').textContent = 'en 48hs';
  }

  document.getElementById('creditos-vista-paquetes').style.display = 'none';
  document.getElementById('creditos-vista-instrucciones').style.display = 'block';
}

function copiarCredDato(elementId, btn) {
  const texto = document.getElementById(elementId).textContent.trim();
  navigator.clipboard.writeText(texto).then(() => {
    const labelOriginal = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-check"></i> Copiado';
    setTimeout(() => { btn.innerHTML = labelOriginal; }, 1500);
  }).catch(() => {
    // Fallback para navegadores antiguos
    const ta = document.createElement('textarea');
    ta.value = texto; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
    btn.innerHTML = '<i class="fa-solid fa-check"></i> Copiado';
    setTimeout(() => { btn.innerHTML = '<i class="fa-solid fa-copy"></i> Copiar'; }, 1500);
  });
}

async function enviarComprobante() {
  if (!_ultimaSolicitudCreditos) return;
  const url = document.getElementById('cred-comprobante-url').value.trim();
  if (!url) { alert('Pegá la URL del comprobante.'); return; }
  if (!url.startsWith('http')) { alert('La URL tiene que empezar con http:// o https://'); return; }
  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/solicitudes/${_ultimaSolicitudCreditos.id}/comprobante`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({comprobante_url: url}),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.detail || 'Error'); return; }
    mostrarToast('✅ Comprobante guardado. Lo revisamos pronto.', 'green');
  } catch(e) { alert('Error de conexión.'); }
}

// ─── COMUNIDAD ──────────────────────────────────────────────────────
let _comCat = '';
async function cargarComunidad() {
  const feed = document.getElementById('comunidad-feed');
  if (!feed) return;
  feed.innerHTML = `<div style="text-align:center; padding:40px; color:var(--text-dim);">Cargando foro...</div>`;
  const orden = (document.getElementById('com-orden')||{}).value || 'recientes';
  const q = ((document.getElementById('com-buscar')||{}).value || '').trim();
  try {
    const url = `${API}/comunidad/feed?categoria=${encodeURIComponent(_comCat)}&orden=${encodeURIComponent(orden)}&q=${encodeURIComponent(q)}`;
    const res = await apiFetch(url);
    if (!res.ok) return;
    const data = await res.json();
    if (!data.posts.length) {
      feed.innerHTML = `<div class="bento-box" style="text-align:center; padding:40px; color:var(--text-muted);">
        No hay publicaciones acá todavía.<br>¡Dejá una pregunta, proponé una mejora o compartí una victoria!
      </div>`;
      return;
    }
    const catMeta = {
      pregunta:{ic:'❓',label:'Pregunta',color:'#facc15'},
      mejora:{ic:'🛠️',label:'Mejora',color:'#60a5fa'},
      charla:{ic:'💬',label:'Charla',color:'#c084fc'},
      victoria:{ic:'🎉',label:'Victoria',color:'#4ade80'},
    };
    const estadoMeta = {
      recibido:{t:'Recibida',c:'#a1a1aa'}, evaluacion:{t:'En evaluación',c:'#facc15'},
      planificado:{t:'Planificada',c:'#60a5fa'}, hecho:{t:'Hecho ✅',c:'#4ade80'},
      descartado:{t:'Descartada',c:'#ef4444'},
    };
    feed.innerHTML = data.posts.map(p => {
      const cm = catMeta[p.categoria] || catMeta.charla;
      const esAutor = p.autor_codigo && typeof aliado!=='undefined' && aliado && p.autor_codigo === aliado.codigo;
      const badgeCat = `<span style="font-size:.65rem;font-weight:800;background:${cm.color}22;color:${cm.color};padding:2px 8px;border-radius:6px;">${cm.ic} ${cm.label.toUpperCase()}</span>`;
      const badgeResuelta = (p.categoria==='pregunta' && p.resuelto) ? `<span style="font-size:.65rem;font-weight:800;background:rgba(74,222,128,0.15);color:#4ade80;padding:2px 8px;border-radius:6px;">✓ RESUELTA</span>` : '';
      const em = p.estado_mejora ? estadoMeta[p.estado_mejora] : null;
      const badgeEstado = em ? `<span style="font-size:.65rem;font-weight:800;background:${em.c}22;color:${em.c};padding:2px 8px;border-radius:6px;">${em.t}</span>` : '';
      const badgeFijado = p.fijado ? `<span style="font-size:.65rem;background:rgba(250,204,21,0.15);color:var(--amber);padding:2px 8px;border-radius:6px;font-weight:800;">📌 DESTACADO</span>` : '';
      const comentariosHtml = (p.comentarios||[]).map(c => {
        const aceptarBtn = (esAutor && p.categoria==='pregunta' && !c.aceptada) ? `<button onclick="aceptarRespuesta(${p.id},${c.id})" title="Marcar como la respuesta" style="background:none;border:1px solid rgba(74,222,128,0.4);color:#4ade80;border-radius:6px;padding:2px 8px;font-size:.7rem;font-weight:700;cursor:pointer;margin-left:6px;">✓ Aceptar</button>` : '';
        const aceptadaBox = c.aceptada ? 'background:rgba(74,222,128,0.06);border-left:3px solid #4ade80;padding-left:8px;border-radius:4px;' : '';
        const aceptadaTag = c.aceptada ? `<span style="color:#4ade80;font-weight:800;font-size:.7rem;margin-left:6px;">✓ Aceptada</span>` : '';
        return `<div style="padding:6px 0;font-size:.82rem;${aceptadaBox}"><strong style="color:var(--text-muted);">${c.autor}:</strong> <span style="color:var(--text);">${escapeHtml(c.cuerpo)}</span> <span style="font-size:.7rem;color:var(--text-dim);margin-left:6px;">${c.fecha||''}</span>${aceptadaTag}${aceptarBtn}</div>`;
      }).join('');
      const resolverCtl = (esAutor && p.categoria==='pregunta')
        ? (p.resuelto
            ? `<button onclick="resolverToggle(${p.id},false)" style="background:none;border:1px solid var(--border);color:var(--text-muted);border-radius:6px;padding:4px 10px;font-size:.72rem;font-weight:700;cursor:pointer;white-space:nowrap;">Reabrir</button>`
            : `<button onclick="resolverToggle(${p.id},true)" style="background:rgba(74,222,128,0.1);border:1px solid rgba(74,222,128,0.35);color:#4ade80;border-radius:6px;padding:4px 10px;font-size:.72rem;font-weight:700;cursor:pointer;white-space:nowrap;">✓ Resuelta</button>`)
        : '';
      return `<div class="bento-box" style="margin-bottom:16px;${p.fijado?'border-color:rgba(250,204,21,0.4);background:rgba(250,204,21,0.02);':''}">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;gap:10px;">
          <div style="flex:1;">
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:6px;">${badgeCat}${badgeResuelta}${badgeEstado}${badgeFijado}</div>
            <strong style="font-size:1rem;">${escapeHtml(p.titulo)}</strong>
            <div style="font-size:.75rem;color:var(--text-dim);margin-top:4px;">${p.autor} · ${p.autor_nivel||'BASIC'} · ${p.fecha}</div>
          </div>
          <button onclick="darLike(${p.id})" style="background:rgba(239,68,68,0.08);color:#ef4444;border:1px solid rgba(239,68,68,0.2);border-radius:20px;padding:5px 12px;font-size:.78rem;font-weight:700;cursor:pointer;white-space:nowrap;">❤ ${p.likes||0}</button>
        </div>
        <div style="white-space:pre-wrap;font-size:.88rem;line-height:1.6;color:var(--text);margin-bottom:12px;">${escapeHtml(p.cuerpo)}</div>
        <div style="border-top:1px solid var(--border);padding-top:10px;">
          ${comentariosHtml}
          <div style="display:flex;gap:6px;margin-top:8px;align-items:center;">
            <input type="text" id="com-input-${p.id}" placeholder="Escribir una respuesta..." style="flex:1;background:rgba(255,255,255,0.03);border:1px solid var(--border);color:var(--text);padding:7px 12px;border-radius:6px;font-size:.82rem;">
            <button onclick="comentarPost(${p.id})" style="background:var(--primary);color:#fff;border:none;border-radius:6px;padding:7px 14px;font-size:.78rem;font-weight:700;cursor:pointer;"><i class="fa-solid fa-paper-plane"></i></button>
            ${resolverCtl}
          </div>
        </div>
      </div>`;
    }).join('');
  } catch(e) { console.error('comunidad:', e); }
}

function escapeHtml(s) {
  return (s||'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[c]));
}

async function publicarPost() {
  const categoria = (document.getElementById('post-categoria')||{}).value || 'charla';
  const titulo = document.getElementById('post-titulo').value.trim();
  const cuerpo = document.getElementById('post-cuerpo').value.trim();
  if (titulo.length < 3 || cuerpo.length < 5) { alert('Escribí un título y un cuerpo.'); return; }
  try {
    const res = await apiFetch(`${API}/comunidad/post`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({codigo_aliado: aliado.codigo, categoria, titulo, cuerpo})
    });
    if (!res.ok) { const d = await res.json(); alert(d.detail || 'Error'); return; }
    document.getElementById('post-titulo').value = '';
    document.getElementById('post-cuerpo').value = '';
    mostrarToast('✅ Publicado', 'green');
    cargarComunidad();
  } catch(e) { alert('Error de conexión.'); }
}

function filtrarComunidadCat(cat, btn) {
  _comCat = cat || '';
  document.querySelectorAll('#com-cats .com-chip').forEach(b=>b.classList.remove('activo'));
  if (btn) btn.classList.add('activo');
  cargarComunidad();
}

async function aceptarRespuesta(postId, comId) {
  try {
    const res = await apiFetch(`${API}/comunidad/${postId}/resolver`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({resuelto:true, comentario_id:comId})
    });
    if (res.ok) { mostrarToast('Respuesta aceptada ✓','green'); cargarComunidad(); }
  } catch(e){}
}

async function resolverToggle(postId, resuelto) {
  try {
    const res = await apiFetch(`${API}/comunidad/${postId}/resolver`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({resuelto})
    });
    if (res.ok) { mostrarToast(resuelto?'Marcada como resuelta':'Pregunta reabierta','green'); cargarComunidad(); }
  } catch(e){}
}

async function darLike(id) {
  try {
    await apiFetch(`${API}/comunidad/${id}/like`, {method:'POST'});
    cargarComunidad();
  } catch(e){}
}

async function comentarPost(id) {
  const input = document.getElementById(`com-input-${id}`);
  const cuerpo = input.value.trim();
  if (cuerpo.length < 2) return;
  try {
    const res = await apiFetch(`${API}/comunidad/${id}/comentario`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({codigo_aliado: aliado.codigo, cuerpo})
    });
    if (res.ok) { input.value=''; cargarComunidad(); }
  } catch(e){}
}

document.addEventListener('DOMContentLoaded', () => {
  // Aplicar tema/canal si la URL trae ?canal=canal1 o ?canal=canal2
  if (typeof aplicarCanalDesdeURL === 'function') {
    aplicarCanalDesdeURL();
  }
  // Si no hay param de canal, poblar dropdown con opciones por defecto (canal2)
  const params = new URLSearchParams(window.location.search);
  if (!params.get('canal') && typeof seleccionarCanal === 'function') {
    seleccionarCanal('canal2');
  }

  // Auto-login con sesión guardada (30 días)
  intentarAutoLogin();


  // Preservar ref sponsor si viene en la URL (ej: alianzas?ref=xyz)
  const _initRef = new URLSearchParams(window.location.search).get('ref');
  if (_initRef) localStorage.setItem('avanza_ref', _initRef);
});

// ═══════════════════════════════════════════════════════════════════════
// ACADEMIA DEL ALIADO · 8 módulos de formación
// ═══════════════════════════════════════════════════════════════════════

// NOTA: cada módulo tiene DOS campos distintos:
//   · id  -> identificador interno ÚNICO. Se usa para guardar el progreso y para abrir el módulo correcto.
//   · num -> el número VISIBLE para el aliado (ej. 'MÓDULO 08'). Es solo texto de pantalla.
// NO unifiques los `id` aunque dos módulos compartan el mismo `num`: Canal 1 y Canal 2 tienen cada uno
// su propio "MÓDULO 08", pero con id distinto (8 y 9). Si les das el mismo id, find() devuelve siempre
// el primero y el módulo del otro canal queda inaccesible / se mezcla el progreso.
const ACADEMIA_MODULOS = [
  {
    id: 1,
    num: 'MÓDULO 01',
    title: 'Cómo funciona el programa',
    slug: 'mod-programa',
    desc: 'Comisiones, niveles, flujo de atribución de ventas y cómo te pagamos en 24hs.',
    tiempo: '5 min',
    icon: 'fa-circle-info',
    content: `
      <div class="av-explainer"><div class="av-tag"><i class="fa-solid fa-circle-play"></i> C&oacute;mo funciona el programa &middot; M&Oacute;DULO 01</div><div class="av-stage"><div class="av-slide center" data-dur="6000"><div class="av-kicker r d1">Avanza Partner Network &middot; Canal 1</div><div class="av-h r d2">Empez&aacute;s <span class="b">sin cartera</span>.<br>El sistema te trae los leads.</div><div class="av-sub r d3">Reclam&aacute;s un lead de la bolsa, la IA te dice qu&eacute; ofrecer, vos cerr&aacute;s, Avanza implementa. Ambos cobramos.</div></div><div class="av-slide" data-dur="8000"><div class="av-kicker r d1">El modelo en 3 pasos</div><div class="av-flow"><div class="av-step r d2"><div class="sn">01</div><div class="st">Reclam&aacute;s un lead</div><div class="sd">De la Bolsa. Ten&eacute;s 48hs para contactar, m&aacute;x 3 activos a la vez.</div></div><div class="av-step r d3"><div class="sn">02</div><div class="st">La IA te prepara</div><div class="sd">Score 0-100, plan recomendado y pitch sugerido. Sab&eacute;s qu&eacute; decir antes de hablar.</div></div><div class="av-step r d4"><div class="sn">03</div><div class="st">Cerr&aacute;s y cobr&aacute;s</div><div class="sd">Registr&aacute;s antes del pago. La comisi&oacute;n llega en 24hs.</div></div></div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Comisiones &mdash; mismo esquema, sin techo</div><div class="av-grid g4"><div class="av-card r d2"><div class="n">10%</div><div class="t">BASIC</div><div class="d">Arranc&aacute;s ac&aacute;</div></div><div class="av-card r d3"><div class="n">12%</div><div class="t">SILVER</div><div class="d">1 venta</div></div><div class="av-card r d4"><div class="n o">15%</div><div class="t">PREMIUM</div><div class="d">2 ventas / 6m</div></div><div class="av-card r d5"><div class="n g">20%</div><div class="t">ELITE</div><div class="d">5 ventas / 6m</div></div></div><div class="av-sub r d6" style="margin-top:2px">Tu % sube solo con cada venta y no vuelve a bajar.</div></div><div class="av-slide" data-dur="8000"><div class="av-kicker r d1">Cu&aacute;nto gan&aacute;s por venta</div><div class="av-tl"><div class="tr r d2"><div class="tm">Base</div><div class="tx">USD 1.050 &rarr; comisi&oacute;n <b>USD 105 a 210</b></div></div><div class="tr r d3"><div class="tm">Pro</div><div class="tx">USD 2.900 &rarr; comisi&oacute;n <b>USD 290 a 580</b></div></div><div class="tr r d4"><div class="tm">Indus.</div><div class="tx">USD 4.900 &rarr; comisi&oacute;n <b>USD 490 a 980</b></div></div><div class="tr r d5"><div class="tm">360</div><div class="tx">USD 7.500 &rarr; comisi&oacute;n <b>USD 750 a 1.500</b></div></div></div></div><div class="av-slide center" data-dur="6000"><div class="av-kicker r d1">Proyecci&oacute;n realista</div><div class="av-stat r d2">USD <span class="g">1.070</span><span style="font-size:.4em;color:#a1a1aa"> /mes</span></div><div class="av-sub r d3">con 2 ventas Pro + 1 Industrial al mes &mdash; desde la bolsa, sin cartera previa y sin moverte de tu casa.</div></div><div class="av-slide" data-dur="8000"><div class="av-kicker r d1">Ingreso recurrente &middot; de por vida</div><div class="av-ok r d2"><span class="lab">+10% mensual, sin importar tu nivel</span>Si adem&aacute;s cerr&aacute;s el plan de mantenimiento, cobr&aacute;s <b>10% del precio mensual</b> mientras el cliente lo mantenga activo.</div><div class="av-row r d3" style="gap:8px;margin-top:2px"><div class="av-kpi"><div class="v">USD 8</div><div class="k">Cuidado /mes</div></div><div class="av-kpi"><div class="v">USD 17</div><div class="k">Crecim. /mes</div></div><div class="av-kpi"><div class="v">USD 28</div><div class="k">Escala /mes</div></div><div class="av-kpi"><div class="v">USD 45</div><div class="k">Liderazgo /mes</div></div></div><div class="av-sub r d4" style="margin-top:2px">Ej.: 5 Crecimiento + 3 Escala = <b>USD 169/mes</b> sin volver a vender.</div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">El paso que no se negocia</div><div class="av-warn r d2"><span class="lab">Registr&aacute; ANTES del pago</span>Si el cliente paga sin estar registrado por vos antes, <b>la venta no se te atribuye</b>. Siempre: primero registr&aacute;s en el portal, despu&eacute;s el cliente transfiere.</div><div class="av-ok r d3"><span class="lab">Cu&aacute;ndo cobr&aacute;s</span>Apenas ingresa el pago, te transferimos en <b>24 horas</b>. No necesit&aacute;s factura A ni B: como aliado independiente cobr&aacute;s seg&uacute;n tu situaci&oacute;n fiscal y tu pa&iacute;s.</div></div><div class="av-slide" data-dur="8000"><div class="av-kicker r d1">Para qui&eacute;n es este programa</div><div class="av-grid g2"><div class="av-card r d2"><div class="t">Vendedores independientes</div><div class="d">Ingreso desde cero, sin empleador ni cartera.</div></div><div class="av-card r d3"><div class="t">Emprendedores digitales</div><div class="d">Sab&eacute;s vender; ac&aacute; ten&eacute;s producto listo.</div></div><div class="av-card r d4"><div class="t">Referidores de red</div><div class="d">Convert&iacute;s tus conexiones en comisiones.</div></div><div class="av-card r d5"><div class="t">Aliados en crecimiento</div><div class="d">Leaderboard, badges y Mi Red amplifican tu resultado.</div></div></div></div></div><div class="av-controls"><button class="av-btn prev" aria-label="Anterior"><i class="fa-solid fa-backward-step"></i></button><button class="av-btn play" aria-label="Pausar"><i class="fa-solid fa-pause"></i></button><button class="av-btn next" aria-label="Siguiente"><i class="fa-solid fa-forward-step"></i></button><div class="av-progress"><div class="av-progress-fill"></div></div><div class="av-dots"></div><div class="av-counter">1 / 1</div></div></div>

      <h3>Lo que vas a aprender</h3>
      <ul>
        <li>Cuánto cobrás por cada venta y cómo sube tu comisión con el tiempo</li>
        <li>Qué hay que hacer para que una venta cuente como tuya</li>
        <li>Cuándo y cómo te pagamos</li>
      </ul>

      <h3>El programa en 1 línea</h3>
      <p><strong>Vos cerrás. Avanza implementa. Ambos cobramos.</strong> Vos presentás la solución al cliente, cerrás la venta, y cobrás comisión. Nosotros nos ocupamos de implementar el sistema técnico (web, automatización, CRM, integraciones).</p>

      <h3>Estructura de comisiones</h3>
      <p>Tu comisión se calcula sobre el valor neto de cada plan que vendas. Los niveles suben automáticamente según la cantidad de ventas en ventanas de 6 meses:</p>
      <table class="aca-table">
        <thead><tr><th>Nivel</th><th>Comisión</th><th>Requisito</th></tr></thead>
        <tbody>
          <tr><td><strong>BASIC</strong></td><td><strong>10%</strong></td><td>Sin requisitos (arrancás acá)</td></tr>
          <tr><td><strong>SILVER</strong></td><td><strong>12%</strong></td><td>1 venta cerrada</td></tr>
          <tr><td><strong>PREMIUM</strong></td><td><strong>15%</strong></td><td>2 ventas en 6 meses</td></tr>
          <tr><td><strong>ELITE</strong></td><td><strong>20%</strong></td><td>5 ventas en 6 meses</td></tr>
        </tbody>
      </table>

      <h3>Cuánto ganás por venta</h3>
      <table class="aca-table">
        <thead><tr><th>Plan</th><th>Precio</th><th>BASIC 10%</th><th>SILVER 12%</th><th>PREMIUM 15%</th><th>ELITE 20%</th></tr></thead>
        <tbody>
          <tr><td>Base</td><td>USD 1.050</td><td>USD 105</td><td>USD 126</td><td>USD 157</td><td>USD 210</td></tr>
          <tr><td>Pro</td><td>USD 2.900</td><td>USD 290</td><td>USD 348</td><td>USD 435</td><td>USD 580</td></tr>
          <tr><td>Industrial</td><td>USD 4.900</td><td>USD 490</td><td>USD 588</td><td>USD 735</td><td>USD 980</td></tr>
          <tr><td>Estratégico 360</td><td>USD 7.500</td><td>USD 750</td><td>USD 900</td><td>USD 1.125</td><td>USD 1.500</td></tr>
        </tbody>
      </table>

      <div class="aca-highlight">
        <div class="label">COMISIONES RECURRENTES</div>
        <div class="text">Si el cliente contrata un plan con mantenimiento mensual, cobrás <strong>10% de ese pago mensual</strong> mientras el cliente siga activo. Es ingreso pasivo que crece con cada cliente que sumás.</div>
      </div>

      <h3>Atribución de ventas — el paso crítico</h3>
      <p>Para que una venta te pertenezca, tenés que cumplir <strong>3 condiciones</strong>:</p>
      <ol>
        <li>Registrar al prospecto <strong>en el portal ANTES del pago del cliente</strong>, indicando nombre y plan elegido.</li>
        <li>Avanza Digital confirma la recepción del registro.</li>
        <li>Usar el link con tu parámetro <code>?ref=</code> cuenta como registro automático válido.</li>
      </ol>

      <div class="aca-warn">
        <div class="label">ATENCIÓN</div>
        <div class="text">Si el cliente llega a Avanza directamente sin estar registrado por vos antes del pago, <strong>la venta no se te atribuye</strong>. Este es el único paso técnico donde podés perder tu comisión. Siempre: primero registrás, después el cliente paga.</div>
      </div>

      <h3>Cuándo cobrás</h3>
      <p>La comisión se abona sobre el valor neto de cada venta <strong>dentro de las 24 horas posteriores al ingreso del pago</strong> del cliente a la cuenta de Avanza. No necesitás factura A ni B para cobrar: como aliado independiente, cobrás según tu propia situación fiscal y la normativa de tu país.</p>

      <h3>Tus obligaciones como aliado</h3>
      <ul>
        <li>Registrar el prospecto <strong>antes</strong> de que pague</li>
        <li>Usar solo los materiales comerciales oficiales que te damos</li>
        <li>No hacer promesas técnicas ni compromisos fuera de los planes oficiales</li>
        <li>Mantener confidencialidad sobre los términos del acuerdo y la estructura de comisiones</li>
        <li>No intermediar servicios de competidores directos durante 12 meses posteriores a cada venta</li>
      </ul>
    `
  },
  {
    id: 2,
    num: 'MÓDULO 02',
    title: 'Cómo detectar al cliente ideal',
    slug: 'mod-cliente-ideal',
    desc: 'Las 4 preguntas que hacés en los primeros 30 segundos para saber si vale la pena avanzar.',
    tiempo: '6 min',
    icon: 'fa-bullseye',
    content: `
      <div class="av-explainer"><div class="av-tag"><i class="fa-solid fa-circle-play"></i> Calificaci&oacute;n &middot; M&Oacute;DULO 02</div><div class="av-stage"><div class="av-slide center" data-dur="5500"><div class="av-kicker r d1">Calificaci&oacute;n &middot; M&oacute;dulo 02</div><div class="av-h r d2">Detect&aacute; al<br><span class="b">cliente ideal</span></div><div class="av-sub r d3">En 30 segundos sab&eacute;s si un prospecto vale tu tiempo. No todos los negocios son clientes.</div></div><div class="av-slide" data-dur="6500"><div class="av-kicker r d1">El perfil que mejor convierte</div><div class="av-list"><div class="av-li r d2"><i class="fa-solid fa-check"></i><span><b>PYME B2B industrial o de servicios</b> &mdash; 5 a 200 empleados</span></div><div class="av-li r d3"><i class="fa-solid fa-check"></i><span>Le <b>venden a otras empresas</b>, no al consumidor final</span></div><div class="av-li r d4"><i class="fa-solid fa-check"></i><span>Reciben <b>consultas t&eacute;cnicas</b>: presupuestos, cotizaciones, servicios a medida</span></div><div class="av-li r d5"><i class="fa-solid fa-check"></i><span>El due&ntilde;o <b>ya siente el dolor</b> de perder consultas o tardar en responder</span></div></div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Aprovech&aacute; el perfilado de la IA</div><div class="av-split"><div class="r d2"><div class="av-mock"><div class="mbar"><span></span><span></span><span></span><div class="url"></div></div><div class="mbody"><div class="av-row"><div class="av-chip g">Score 92</div><div class="av-chip b">Plan: Industrial</div></div><div class="av-ln b w85"></div><div class="av-ln w60"></div><div class="av-row"><div class="av-chip o">Pitch sugerido listo</div></div></div></div></div><div class="r d3"><div class="av-eyebrow">El portal ya hizo la mitad</div><div class="av-sub" style="margin-top:6px">Cada lead de la bolsa viene con <b>score 0-100</b>, plan recomendado y pitch sugerido. Empez&aacute;s por los de score m&aacute;s alto.</div></div></div></div><div class="av-slide" data-dur="8000"><div class="av-kicker r d1">Las 4 preguntas que nunca fallan</div><div class="av-tl"><div class="tr r d2"><div class="tm">01</div><div class="tx"><b>Volumen.</b> &iquest;Cu&aacute;ntas consultas nuevas por mes? (busc&aacute;s 10+)</div></div><div class="tr r d3"><div class="tm">02</div><div class="tx"><b>Canales.</b> &iquest;Por d&oacute;nde entran? (2 o m&aacute;s &mdash; ah&iacute; est&aacute; el dolor)</div></div><div class="tr r d4"><div class="tm">03</div><div class="tx"><b>Tiempo.</b> &iquest;Cu&aacute;nto tardan en responder? (+2 hs = oro)</div></div><div class="tr r d5"><div class="tm">04</div><div class="tx"><b>El cierre.</b> &iquest;Un sistema que responde en 60 seg lo resolver&iacute;a?</div></div></div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Qu&eacute; plan encaja con cada rubro</div><div class="av-grid g2"><div class="av-card r d2"><div class="t">Metal&uacute;rgica &middot; F&aacute;brica &middot; Agro</div><div class="d">Industrial &middot; USD 4.900</div></div><div class="av-card r d3"><div class="t">Transporte &middot; Log&iacute;stica</div><div class="d">Pro &middot; USD 2.900</div></div><div class="av-card r d4"><div class="t">Servicios t&eacute;cnicos &middot; Construcci&oacute;n</div><div class="d">Base &middot; USD 1.050</div></div><div class="av-card r d5"><div class="t">Parque industrial &middot; +100 empleados</div><div class="d">Estrat&eacute;gico 360 &middot; USD 7.500</div></div></div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Verde vs rojo</div><div class="av-cols"><div class="av-col good r d2"><div class="hd">Cliente caliente</div><div class="it">&ldquo;Estamos creciendo y no damos abasto&rdquo;</div><div class="it">&ldquo;Perdimos clientes por no responder a tiempo&rdquo;</div><div class="it">&ldquo;La competencia aparece primero en Google&rdquo;</div></div><div class="av-col bad r d3"><div class="hd">Dej&aacute; pasar</div><div class="it">&ldquo;Ya tengo una agencia&rdquo;</div><div class="it">&ldquo;Yo mismo hago la web&rdquo;</div><div class="it">&ldquo;No tengo presupuesto ahora&rdquo;</div></div></div></div><div class="av-slide center" data-dur="6500"><div class="av-quote r d1"><span class="lab">Tip clave</span>En esta fase <b>no vend&eacute;s, solo calific&aacute;s</b>. Si las 4 respuestas dan verde, pas&aacute;s al cierre (M&oacute;dulo 5). Si alguna da rojo, no insistas: agradec&eacute; y guard&aacute; el contacto.</div></div></div><div class="av-controls"><button class="av-btn prev" aria-label="Anterior"><i class="fa-solid fa-backward-step"></i></button><button class="av-btn play" aria-label="Pausar"><i class="fa-solid fa-pause"></i></button><button class="av-btn next" aria-label="Siguiente"><i class="fa-solid fa-forward-step"></i></button><div class="av-progress"><div class="av-progress-fill"></div></div><div class="av-dots"></div><div class="av-counter">1 / 1</div></div></div>

      <h3>Lo que vas a aprender</h3>
      <ul>
        <li>Cómo saber en 30 segundos si un prospecto vale tu tiempo</li>
        <li>Las 4 preguntas de calificación que nunca fallan</li>
        <li>Qué rubros convierten mejor y cuáles dejar pasar</li>
      </ul>

      <h3>El perfil del cliente ideal de Avanza</h3>
      <p>No todos los negocios son clientes. Los que mejor convierten tienen <strong>estas 4 características</strong>:</p>
      <ul>
        <li><strong>PYME B2B industrial o de servicios</strong> — 5 a 200 empleados</li>
        <li><strong>Venden a otras empresas</strong>, no al consumidor final</li>
        <li><strong>Reciben consultas técnicas</strong> (presupuestos, cotizaciones, servicios a medida)</li>
        <li><strong>El dueño o gerente comercial ya siente el dolor</strong> de perder consultas, tardar en responder o no tener control del pipeline</li>
      </ul>

      <h3>Los rubros que mejor convierten</h3>
      <table class="aca-table">
        <thead><tr><th>Rubro</th><th>Plan típico</th><th>Ciclo de cierre</th></tr></thead>
        <tbody>
          <tr><td>Metalúrgica · Fábrica · Agroindustria</td><td>Industrial (USD 4.900)</td><td>4–8 semanas</td></tr>
          <tr><td>Transporte · Logística · Distribución</td><td>Pro (USD 2.900)</td><td>2–4 semanas</td></tr>
          <tr><td>Servicios técnicos · Mantenimiento · Construcción</td><td>Base (USD 1.050)</td><td>1–2 semanas</td></tr>
          <tr><td>Parque industrial · Desarrollo · Corporativo +100</td><td>Estratégico 360 (USD 7.500)</td><td>8–12 semanas</td></tr>
        </tbody>
      </table>

      <h3>Las 4 preguntas de calificación</h3>
      <p>Hacelas en este orden exacto. Cada una abre la siguiente:</p>

      <div class="aca-pitch-box">
        <div class="label">PREGUNTA 1</div>
        <div class="text">"¿Cuántas consultas de clientes nuevos reciben por mes, aproximadamente?"</div>
      </div>
      <p><strong>Qué buscás:</strong> que reciban al menos 10 consultas/mes. Si reciben menos, probablemente no tengan el dolor suficiente para invertir. Si reciben más de 50, son clientes PREMIUM.</p>

      <div class="aca-pitch-box">
        <div class="label">PREGUNTA 2</div>
        <div class="text">"¿Por qué canales entran esas consultas — WhatsApp, email, teléfono, web?"</div>
      </div>
      <p><strong>Qué buscás:</strong> que mencionen 2 o más canales distintos. Ahí está el dolor — consultas dispersas, perdidas entre canales. Si solo dicen "teléfono" o "me llaman", el cliente todavía no necesita digitalizar.</p>

      <div class="aca-pitch-box">
        <div class="label">PREGUNTA 3</div>
        <div class="text">"¿Cuánto tardan en responder una consulta nueva, más o menos?"</div>
      </div>
      <p><strong>Qué buscás:</strong> cualquier respuesta mayor a 2 horas. Si dice "12 horas", "un día", "a veces al día siguiente" — ahí hay oro. Si dice "respondo en minutos", el cliente probablemente no escale al sistema completo, pero puede cerrar un Plan Base.</p>

      <div class="aca-pitch-box">
        <div class="label">PREGUNTA 4</div>
        <div class="text">"Si pudieras tener un sistema que responde automáticamente en 60 segundos y te manda solo los leads calificados al vendedor, ¿eso resolvería el problema?"</div>
      </div>
      <p><strong>Qué buscás:</strong> que diga "sí" o "claro". Si duda, no está listo. Pero si vos hacés la pregunta bien, el 80% va a decir que sí — porque le estás describiendo exactamente el dolor que te acaban de contar.</p>

      <div class="aca-tip">
        <div class="label">TIP CLAVE</div>
        <div class="text">No vendés en esta fase. Solo calificás. Si las 4 respuestas te dan verde, pasás al pitch (Módulo 5). Si alguna te da rojo claro, no insistas — agradecele y guardá el contacto para otro momento.</div>
      </div>

      <h3>Señales de un cliente "caliente" para cerrar rápido</h3>
      <ul>
        <li><strong>"Estamos creciendo y no damos abasto"</strong> — dolor activo, presupuesto disponible</li>
        <li><strong>"Perdimos clientes porque no respondimos a tiempo"</strong> — dolor con número, cierre rápido</li>
        <li><strong>"Mi competencia está apareciendo primero en Google"</strong> — urgencia competitiva</li>
        <li><strong>"Queremos digitalizar el proceso pero no sabemos por dónde"</strong> — cliente listo, solo necesita guía</li>
      </ul>

      <h3>Señales de que no vale la pena avanzar</h3>
      <ul>
        <li><strong>"Ya tengo una agencia"</strong> — difícil desplazar; no imposible, pero cuesta</li>
        <li><strong>"Yo mismo hago la web"</strong> — DIY mindset, no va a pagar por servicio</li>
        <li><strong>"No tengo presupuesto ahora"</strong> — si lo dice en la primera llamada, archivalo por 3 meses</li>
        <li><strong>"Somos muy chicos todavía"</strong> — honesto, no está listo</li>
      </ul>
    `
  },
  {
    id: 3,
    num: 'MÓDULO 03',
    title: 'Cómo hacer el diagnóstico',
    slug: 'mod-diagnostico',
    desc: 'Cuando el cliente te pide "mostrame cómo funciona": guión paso a paso del diagnóstico de 15 minutos.',
    tiempo: '7 min',
    icon: 'fa-magnifying-glass-chart',
    content: `
      <div class="av-explainer"><div class="av-tag"><i class="fa-solid fa-circle-play"></i> El diagn&oacute;stico consultivo &middot; M&Oacute;DULO 03</div><div class="av-stage"><div class="av-slide center" data-dur="6000"><div class="av-kicker r d1">El cierre consultivo &middot; M&oacute;dulo 03</div><div class="av-h r d2">El <span class="b">diagn&oacute;stico</span><br>de 15 minutos</div><div class="av-sub r d3">Una llamada corta donde el cliente se convence solo al describir sus propios problemas. Lo hac&eacute;s vos, no Avanza.</div></div><div class="av-slide" data-dur="6500"><div class="av-kicker r d1">Antes de llamar, ten&eacute; listo</div><div class="av-list"><div class="av-li r d2"><i class="fa-solid fa-file-lines"></i><span><b>Brochure comercial</b> de planes y precios</span></div><div class="av-li r d3"><i class="fa-solid fa-robot"></i><span>El <b>perfil del prospecto</b> en el portal: score IA y plan recomendado</span></div><div class="av-li r d4"><i class="fa-solid fa-magnifying-glass-chart"></i><span>La <b>Auditor&iacute;a Digital</b> enviada antes de la reuni&oacute;n</span></div></div></div><div class="av-slide" data-dur="7000"><div class="av-kicker r d1">El anticipo que convence</div><div class="av-quote r d2"><span class="lab">Por WhatsApp, antes de reunirse</span>&ldquo;Antes de reunirnos te paso un an&aacute;lisis r&aacute;pido de la presencia digital de tu empresa &mdash; gratis, 30 segundos: [link]&rdquo;.</div><div class="av-sub r d3">El cliente ve su propio reporte y <b>llega convencido de que tiene un problema</b>.</div></div><div class="av-slide center" data-dur="5500"><div class="av-kicker r d1">La regla de oro</div><div class="av-stat r d2"><span class="b">30</span> / <span class="o">70</span></div><div class="av-sub r d3">Habl&aacute; el 30%, escuch&aacute; el 70%. El cliente te va a vender a vos mismo si lo dej&aacute;s hablar.</div></div><div class="av-slide" data-dur="9000"><div class="av-kicker r d1">El diagn&oacute;stico, minuto a minuto</div><div class="av-tl"><div class="tr r d2"><div class="tm">0&ndash;3</div><div class="tx"><b>Confirm&aacute; el problema.</b> &ldquo;Contame c&oacute;mo llegan hoy las consultas y qu&eacute; pasa con las que no se responden.&rdquo;</div></div><div class="tr r d3"><div class="tm">3&ndash;7</div><div class="tx"><b>Cuantific&aacute; el costo.</b> &ldquo;Si perd&eacute;s 3 consultas/mes y el ticket es USD X, son USD Y que se van.&rdquo;</div></div><div class="tr r d4"><div class="tm">7&ndash;11</div><div class="tx"><b>Present&aacute; un solo plan.</b> El del caso de su rubro. No muestres todos.</div></div><div class="tr r d5"><div class="tm">11&ndash;15</div><div class="tx"><b>Cerr&aacute; en el momento.</b> Plan, monto y pago por WhatsApp antes de colgar.</div></div></div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Devolv&eacute; el dolor con sus palabras</div><div class="av-quote r d2"><span class="lab">Cuando termin&oacute; de contarte</span>&ldquo;O sea, si entiendo bien, el problema central es <b>[dolor principal]</b>. Perd&eacute;s clientes porque <b>[consecuencia concreta]</b>. &iquest;Es as&iacute; o me pierdo algo?&rdquo;</div><div class="av-sub r d3">El cliente asiente. Ah&iacute; ya ganaste: escuch&oacute; el diagn&oacute;stico de su propia boca.</div></div><div class="av-slide" data-dur="7500"><div class="av-warn r d1"><span class="lab">Error frecuente</span>No te vayas por la t&eacute;cnica. Si hay dudas que no pod&eacute;s responder: <b>&ldquo;eso lo definimos con el equipo t&eacute;cnico en la implementaci&oacute;n&rdquo;</b>. Tu rol es cerrar, Avanza resuelve el resto.</div><div class="av-ok r d2"><span class="lab">El follow-up del viernes</span>Si no cierra hoy, no pasa nada. Mand&aacute; el caso por WhatsApp y <b>volv&eacute; a escribir el viernes</b>. La mitad cierra en el segundo contacto.</div></div></div><div class="av-controls"><button class="av-btn prev" aria-label="Anterior"><i class="fa-solid fa-backward-step"></i></button><button class="av-btn play" aria-label="Pausar"><i class="fa-solid fa-pause"></i></button><button class="av-btn next" aria-label="Siguiente"><i class="fa-solid fa-forward-step"></i></button><div class="av-progress"><div class="av-progress-fill"></div></div><div class="av-dots"></div><div class="av-counter">1 / 1</div></div></div>

      <h3>Lo que vas a aprender</h3>
      <ul>
        <li>El guión exacto del diagnóstico de 15 minutos</li>
        <li>Cómo llevar la conversación hacia el plan correcto sin parecer vendedor</li>
        <li>Qué apoyos visuales mostrar en cada etapa</li>
        <li><strong>Cómo usar la Auditoría Digital y el Cotizador antes y después del diagnóstico</strong></li>
      </ul>

      <h3>Antes del diagnóstico: la Auditoría Digital B2B</h3>
      <p>Antes de llamar al lead, mandále el link de su <strong>Auditoría Digital gratuita</strong>. Es una herramienta que analiza la presencia digital de la empresa del prospecto (web, velocidad, posicionamiento, conversión) y genera un reporte en segundos.</p>

      <div class="aca-highlight">
        <div class="label">CUÁNDO USARLA</div>
        <div class="text">Antes del primer contacto real. Mandás el link por WhatsApp o email como "adelanto" antes de la reunión. El cliente ve su propio reporte y <strong>llega al diagnóstico ya convencido de que tiene un problema</strong>. Vos solo tenés que confirmar lo que él ya sabe.</div>
      </div>

      <div class="aca-pitch-box">
        <div class="label">MENSAJE DE ENVÍO</div>
        <div class="text">\"Hola [Nombre], antes de que nos reunamos le paso un análisis rápido de la presencia digital de [empresa] — es gratuito y tarda 30 segundos. Muchas veces muestra cosas que no se ven a simple vista: [link de auditoría]. Nos vemos el [día] entonces.\"</div>
      </div>

      <p>La Auditoría está en el menú <strong>Herramientas</strong> de la barra principal. Tu link personal se genera automáticamente con tu código de referido.</p>

      <div class="aca-tip">
        <div class="label">DESPUÉS DEL DIAGNÓSTICO: EL COTIZADOR CON IA</div>
        <div class="text">Una vez que identificaste el dolor y el plan correcto, usá el <strong>Cotizador</strong> (también en Herramientas) para generar una propuesta personalizada en segundos. Elegís rubro y plan, y la IA arma el argumento de valor adaptado a ese tipo de empresa. Mandalo por WhatsApp como cierre de la conversación.</div>
      </div>

      <h3>Qué es el diagnóstico</h3>
      <p>El diagnóstico es una <strong>llamada de 15 minutos</strong> donde el cliente te cuenta su proceso comercial actual y vos le mostrás qué plan le conviene. Es el momento más importante del cierre. No es una demo ni una venta dura — es una conversación de asesor, donde el cliente se convence solo al oírse describir sus propios problemas.</p>

      <div class="aca-highlight">
        <div class="label">REGLA DE ORO</div>
        <div class="text">En el diagnóstico <strong>hablá 30%, escuchá 70%</strong>. El cliente te va a vender a vos mismo si lo dejás hablar lo suficiente.</div>
      </div>

      <h3>Estructura del diagnóstico (15 minutos)</h3>

      <h4>Minuto 0-2 · Apertura y contexto</h4>
      <div class="aca-pitch-box">
        <div class="label">LO QUE DECÍS</div>
        <div class="text">"Gracias por el tiempo, [Nombre]. Esta charla es simple: te hago 4 o 5 preguntas sobre cómo recibís y manejás consultas comerciales hoy. Con eso te armo una recomendación concreta de qué hacer. No te vendo nada acá — si el plan tiene sentido, te paso el presupuesto por WhatsApp y decidís después."</div>
      </div>
      <p><strong>Objetivo:</strong> bajarle la guardia. Si el cliente siente que no lo estás vendiendo, se abre más.</p>

      <h4>Minuto 2-6 · Las 4 preguntas de diagnóstico</h4>
      <p>Acá las hacés con el cliente en frente, para cuantificar el dolor:</p>
      <ol>
        <li><strong>Volumen:</strong> "¿Cuántas consultas reciben por mes?"</li>
        <li><strong>Canales:</strong> "¿Por dónde entran? ¿Qué porcentaje por cada uno?"</li>
        <li><strong>Tiempo de respuesta:</strong> "¿Cuánto tardan en contestar la primera vez?"</li>
        <li><strong>Seguimiento:</strong> "¿Cómo hacen el seguimiento cuando el cliente no cierra en la primera consulta? ¿Tienen un sistema o se va por WhatsApp del vendedor?"</li>
      </ol>

      <div class="aca-tip">
        <div class="label">TÉCNICA</div>
        <div class="text">Después de cada respuesta, repetí el número de vuelta con un poquito de sorpresa: "¿12 horas de respuesta? Uf, pesado eso". El cliente se escucha y la urgencia sube sola.</div>
      </div>

      <h4>Minuto 6-10 · Identificar el dolor principal</h4>
      <p>Con las respuestas en la mano, identificá cuál de estos 4 dolores es el más fuerte:</p>
      <ul>
        <li><strong>Dolor de Volumen:</strong> reciben muchas consultas pero se pierden</li>
        <li><strong>Dolor de Tiempo:</strong> tardan mucho en responder y pierden ventas</li>
        <li><strong>Dolor de Seguimiento:</strong> el vendedor se olvida, los leads se enfrían</li>
        <li><strong>Dolor de Visibilidad:</strong> el dueño no sabe qué pasa con cada consulta</li>
      </ul>
      <div class="aca-pitch-box">
        <div class="label">CÓMO DEVOLVÉS EL DOLOR</div>
        <div class="text">"O sea, si entiendo bien, el problema central es <strong>[dolor principal]</strong>. Perdés clientes porque <strong>[consecuencia concreta]</strong>. ¿Es así o me estoy perdiendo algo?"</div>
      </div>
      <p>El cliente asiente. Ahí ya ganaste — el cliente escuchó el diagnóstico de su propia boca. Ahora solo falta presentar la solución.</p>

      <h4>Minuto 10-13 · Presentás la solución con el caso de su rubro</h4>
      <p>Elegís el caso del rubro del cliente y lo contás brevemente. Por ejemplo, si es una metalúrgica:</p>
      <div class="aca-pitch-box">
        <div class="label">CONEXIÓN CON EL CASO</div>
        <div class="text">"Te cuento rápido. <strong>Aleametal en Perú</strong> tenía exactamente lo que me acabás de describir — 38 empleados, consultas por 3 canales distintos, 40% de presupuestos sin seguimiento. En 21 días les armamos un sistema. Hoy tienen +47% de conversión y cero consultas sin respuesta en 24hs. El Plan Industrial — USD 4.900, pago único."</div>
      </div>

      <h4>Minuto 13-15 · Cierre</h4>
      <p>Hacés la pregunta de cierre directa:</p>
      <div class="aca-pitch-box">
        <div class="label">PREGUNTA DE CIERRE</div>
        <div class="text">"¿Te sirve algo así para tu empresa? ¿Querés que te pase el detalle del plan por WhatsApp?"</div>
      </div>
      <p>Si dice sí, pasaste al siguiente paso: mandás el brochure + detalle del plan, y coordinás la llamada técnica con el equipo de Avanza.</p>
      <p>Acá, en el diagnóstico, solo abrís la puerta al cierre. El guión de cierre completo —paso a paso y con el pitch exacto por rubro— lo tenés en el módulo <strong>«Cómo cerrar con el guión por rubro»</strong>.</p>

      <div class="aca-warn">
        <div class="label">ERROR FRECUENTE</div>
        <div class="text">No te vayas por la técnica en el diagnóstico. No expliques "cómo funciona el sistema por dentro". El cliente no compra tecnología — compra el resultado (más leads, menos tiempo perdido, visibilidad). Deja los detalles técnicos para la llamada con el equipo de Avanza.</div>
      </div>

      <h3>Qué hacer si el cliente no cierra en el diagnóstico</h3>
      <p>No pasa nada. La mayoría necesita pensarlo. Terminá así:</p>
      <div class="aca-pitch-box">
        <div class="label">CIERRE ALTERNATIVO</div>
        <div class="text">"Entiendo. Te mando el brochure con el caso de [Aleametal/Logística Cordillera/Soluciones Técnicas Generales] por WhatsApp ahora, y te vuelvo a escribir el viernes para ver qué pensaste. Sin presión."</div>
      </div>
      <p>El follow-up del viernes es crítico. <strong>Siempre mandalo.</strong> Con la mitad de tus clientes, el cierre pasa en el segundo contacto, no en el primero.</p>
    `
  },
  {
    id: 4,
    num: 'MÓDULO 04',
    title: 'Los 4 sistemas explicados',
    slug: 'mod-sistemas',
    desc: 'Base, Pro, Industrial y Estratégico 360: qué incluye cada uno, a qué empresa le conviene, cómo lo explicás.',
    tiempo: '8 min',
    icon: 'fa-layer-group',
    content: `
      <div class="av-explainer"><div class="av-tag"><i class="fa-solid fa-circle-play"></i> El producto por dentro &middot; M&Oacute;DULO 04</div><div class="av-stage"><div class="av-slide center" data-dur="5500"><div class="av-kicker r d1">El producto &middot; M&oacute;dulo 04</div><div class="av-h r d2">Los 4 sistemas,<br><span class="o">por dentro</span></div><div class="av-sub r d3">Qu&eacute; hace cada plan, c&oacute;mo se ve, en cu&aacute;nto se implementa y a qu&eacute; cliente le conviene.</div></div><div class="av-slide" data-dur="8000"><div class="av-pill r d1">Plan Base &middot; <span class="av-price">USD 1.050</span></div><div class="av-split"><div class="r d2"><div class="av-mock"><div class="mbar"><span></span><span></span><span></span><div class="url"></div></div><div class="mbody"><div class="av-ln b w60"></div><div class="av-ln w100"></div><div class="av-ln w85"></div><div class="av-row" style="margin-top:6px"><div class="av-chip g"><i class="fa-brands fa-whatsapp"></i> Consulta &rarr; WhatsApp</div></div></div></div></div><div class="r d3"><div class="av-eyebrow">Para arrancar sin arriesgar</div><div class="av-sub" style="margin-top:6px">Landing profesional + mensaje comercial + formulario directo a WhatsApp. <b>Implementaci&oacute;n 7 d&iacute;as.</b></div><div class="av-pill o" style="margin-top:8px">ROI: se paga con tu 1&ordf; venta (B2B &gt; USD 3.000)</div></div></div></div><div class="av-slide" data-dur="8000"><div class="av-pill r d1">Plan Pro &middot; <span class="av-price">USD 2.900</span></div><div class="av-split"><div class="r d2"><div class="av-mock"><div class="mbar"><span></span><span></span><span></span><div class="url"></div></div><div class="mbody"><div class="av-row"><div class="av-chip">Lead entra</div><div class="av-chip b">&lt; 60 seg</div><div class="av-chip o">auto-respuesta</div></div><div class="av-ln w100"></div><div class="av-ln w70"></div><div class="av-row" style="margin-top:4px"><div class="av-chip g">&check; calificado &rarr; vendedor</div></div></div></div></div><div class="r d3"><div class="av-eyebrow">Canal comercial automatizado</div><div class="av-sub" style="margin-top:6px">Suma web multi-secci&oacute;n, <b>CRM + email marketing</b>, automatizaci&oacute;n de respuestas y copy de ventas. <b>14 d&iacute;as.</b></div><div class="av-pill o" style="margin-top:8px">ROI: 3 leads/mes &times; cierre USD 5.000 = ROI mes 1</div></div></div></div><div class="av-slide" data-dur="8000"><div class="av-pill r d1">Plan Industrial &middot; <span class="av-price">USD 4.900</span></div><div class="av-split"><div class="r d2"><div class="av-mock"><div class="mbar"><span></span><span></span><span></span><div class="url"></div></div><div class="mbody"><div class="av-row"><div class="av-kpi"><div class="v">+47%</div><div class="k">conversi&oacute;n</div></div><div class="av-kpi"><div class="v">0</div><div class="k">sin respuesta 24h</div></div></div><div class="av-ln b w85"></div><div class="av-ln w60"></div><div class="av-row"><div class="av-chip o">formulario segmentado</div></div></div></div></div><div class="r d3"><div class="av-eyebrow">Para ciclos de venta complejos</div><div class="av-sub" style="margin-top:6px">Embudo t&eacute;cnico completo, <b>formularios hiper-segmentados</b>, panel de m&eacute;tricas en tiempo real y capacitaci&oacute;n al equipo. <b>21 d&iacute;as.</b></div><div class="av-pill o" style="margin-top:8px">ROI: 10 hs/sem ahorradas &gt; USD 10.000/a&ntilde;o</div></div></div></div><div class="av-slide" data-dur="8000"><div class="av-pill o r d1">Estrat&eacute;gico 360 &middot; <span class="av-price">USD 7.500</span></div><div class="av-split"><div class="r d2"><div class="av-mock"><div class="mbar"><span></span><span></span><span></span><div class="url"></div></div><div class="mbody"><div class="av-row"><div class="av-chip b">ERP: Tango / SAP</div><div class="av-chip g">Lead scoring</div></div><div class="av-row"><div class="av-ln g w50"></div><div class="av-chip">92</div></div><div class="av-row"><div class="av-ln o w40"></div><div class="av-chip">71</div></div><div class="av-row"><div class="av-ln w30"></div><div class="av-chip">38</div></div></div></div></div><div class="r d3"><div class="av-eyebrow">El ecosistema a medida</div><div class="av-sub" style="margin-top:6px">Todo lo anterior + <b>desarrollo a medida</b>, integraci&oacute;n <b>ERP (Tango/SAP)</b>, lead scoring y garant&iacute;a 12 meses con SLA. <b>30 d&iacute;as.</b></div><div class="av-pill o" style="margin-top:8px">ROI: elimina doble carga, ahorra USD 600/mes</div></div></div></div><div class="av-slide" data-dur="8500"><div class="av-kicker r d1">Qu&eacute; incluye cada uno (resumen)</div><div class="av-mock r d2"><div class="mbar"><span></span><span></span><span></span><div class="url"></div></div><div class="mbody"><div class="av-row"><div class="av-chip" style="flex:2">Caracter&iacute;stica</div><div class="av-chip">Base</div><div class="av-chip b">Pro</div><div class="av-chip">Ind.</div><div class="av-chip o">360</div></div><div class="av-row"><div class="av-chip" style="flex:2">Landing + WhatsApp</div><div class="av-chip g">&check;</div><div class="av-chip g">&check;</div><div class="av-chip g">&check;</div><div class="av-chip g">&check;</div></div><div class="av-row"><div class="av-chip" style="flex:2">CRM + automatizaci&oacute;n</div><div class="av-chip">&ndash;</div><div class="av-chip g">&check;</div><div class="av-chip g">&check;</div><div class="av-chip g">&check;</div></div><div class="av-row"><div class="av-chip" style="flex:2">Panel m&eacute;tricas tiempo real</div><div class="av-chip">&ndash;</div><div class="av-chip">&ndash;</div><div class="av-chip g">&check;</div><div class="av-chip g">&check;</div></div><div class="av-row"><div class="av-chip" style="flex:2">ERP + Lead Scoring</div><div class="av-chip">&ndash;</div><div class="av-chip">&ndash;</div><div class="av-chip">&ndash;</div><div class="av-chip g">&check;</div></div></div></div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Qu&eacute; plan ofrecer</div><div class="av-list"><div class="av-li r d2"><i class="fa-solid fa-arrow-right"></i><span>&ldquo;No tengo presencia digital&rdquo; &rarr; <b>Base</b></span></div><div class="av-li r d3"><i class="fa-solid fa-arrow-right"></i><span>&ldquo;Tengo web pero no me sirve&rdquo; &rarr; <b>Pro</b></span></div><div class="av-li r d4"><i class="fa-solid fa-arrow-right"></i><span>&ldquo;Vendo productos t&eacute;cnicos complejos&rdquo; &rarr; <b>Industrial</b></span></div><div class="av-li r d5"><i class="fa-solid fa-arrow-right"></i><span>&ldquo;Uso Tango/SAP y quiero integrar todo&rdquo; &rarr; <b>Estrat&eacute;gico 360</b></span></div></div></div><div class="av-slide" data-dur="7500"><div class="av-quote r d1"><span class="lab">Continuidad (opcional) = tu MRR</span>Cada plan se aparea con un mantenimiento: <b>Base&rarr;Cuidado, Pro&rarr;Crecimiento, Industrial&rarr;Escala, 360&rarr;Liderazgo</b>. Sin permanencia. Vos cobr&aacute;s 10% mensual de ese plan.</div><div class="av-ok r d2"><span class="lab">Todos los planes</span>Pago &uacute;nico &middot; c&oacute;digo 100% del cliente &middot; sin alquiler mensual obligatorio.</div></div></div><div class="av-controls"><button class="av-btn prev" aria-label="Anterior"><i class="fa-solid fa-backward-step"></i></button><button class="av-btn play" aria-label="Pausar"><i class="fa-solid fa-pause"></i></button><button class="av-btn next" aria-label="Siguiente"><i class="fa-solid fa-forward-step"></i></button><div class="av-progress"><div class="av-progress-fill"></div></div><div class="av-dots"></div><div class="av-counter">1 / 1</div></div></div>

      <h3>Lo que vas a aprender</h3>
      <ul>
        <li>Qué incluye cada plan en palabras simples</li>
        <li>A qué cliente le recomendás cada uno</li>
        <li>Cómo explicar diferencias sin entrar en tecnicismos</li>
      </ul>

      <h3>Plan Base · USD 1.050</h3>
      <p><strong>Para:</strong> PYMEs chicas sin presencia digital activa (menos de 15 empleados)</p>
      <p><strong>Implementación:</strong> 7 días</p>
      <p><strong>Qué incluye:</strong></p>
      <ul>
        <li>Landing page profesional orientada a conversión</li>
        <li>Mensaje comercial estratégico (no "webs bonitas")</li>
        <li>Formulario de contacto integrado con WhatsApp</li>
      </ul>
      <p><strong>ROI típico:</strong> en industria, una sola venta B2B promedio supera los USD 3.000. El plan se paga con la primera venta cerrada por el canal digital.</p>
      <div class="aca-pitch-box">
        <div class="label">CÓMO LO EXPLICÁS</div>
        <div class="text">"Es el plan de arranque. Te armamos una página simple pero efectiva con un formulario que va directo a tu WhatsApp. En 7 días estás recibiendo consultas. Ideal para empezar sin arriesgar mucho."</div>
      </div>

      <h3>Plan Pro · USD 2.900</h3>
      <p><strong>Para:</strong> empresas que quieren un canal comercial automatizado de verdad (15 a 50 empleados)</p>
      <p><strong>Implementación:</strong> 14 días</p>
      <p><strong>Qué incluye:</strong> todo lo del Base, más:</p>
      <ul>
        <li>Web multi-sección (hasta 6 páginas)</li>
        <li>Lead magnet y formulario avanzado para calificar consultas</li>
        <li>Integración con CRM y email marketing</li>
        <li>Automatización de respuestas (el sistema contesta solo en menos de 60 segundos)</li>
        <li>Copywriting orientado a ventas</li>
      </ul>
      <p><strong>ROI típico:</strong> con 3 leads calificados por mes y un cierre promedio de USD 5.000, el ROI se logra en el primer mes de operación.</p>
      <div class="aca-pitch-box">
        <div class="label">CÓMO LO EXPLICÁS</div>
        <div class="text">"El Pro es para cuando ya tenés flujo constante de consultas. Incluye automatización de respuestas — el sistema contesta al cliente solo, califica si es buen lead o no, y recién ahí le pasa el contacto a tu vendedor. Tu equipo deja de perder tiempo con consultas basura."</div>
      </div>

      <h3>Plan Industrial · USD 4.900</h3>
      <p><strong>Para:</strong> fábricas y empresas con ciclos de venta complejos (30 a 200 empleados)</p>
      <p><strong>Implementación:</strong> 21 días</p>
      <p><strong>Qué incluye:</strong> todo lo del Pro, más:</p>
      <ul>
        <li>Embudo de ventas técnico completo con etapas</li>
        <li>Formularios hiper-segmentados (se adaptan al tipo de producto consultado)</li>
        <li>Panel de métricas en tiempo real (el dueño ve todo)</li>
        <li>Capacitación al equipo comercial</li>
      </ul>
      <p><strong>ROI típico:</strong> eliminar 10 horas semanales de trabajo manual de tu equipo de ventas vale más de USD 10.000 al año en productividad recuperada.</p>
      <div class="aca-pitch-box">
        <div class="label">CÓMO LO EXPLICÁS</div>
        <div class="text">"El Industrial es para cuando vendés cosas complejas — equipos, servicios técnicos, proyectos a medida. El formulario se adapta: si alguien pregunta por un silo, te pide capacidad; si pregunta por un intercambiador, te pide otros datos. Cada consulta llega calificada con info útil. El dueño ve todo en un panel."</div>
      </div>

      <h3>Estratégico 360 · USD 7.500</h3>
      <p><strong>Para:</strong> empresas con visión de liderazgo en su sector, parques industriales, desarrollos corporativos</p>
      <p><strong>Implementación:</strong> 30-60 días</p>
      <p><strong>Qué incluye:</strong> todo lo del Industrial, más:</p>
      <ul>
        <li>Desarrollo a medida / intranet corporativa</li>
        <li>Integración con ERP (Tango, SAP)</li>
        <li>Lead scoring automático (el sistema prioriza leads por probabilidad de cierre)</li>
        <li>Garantía extendida 12 meses con SLA</li>
      </ul>
      <p><strong>ROI típico:</strong> la integración con Tango o SAP elimina la doble carga de datos. Un operador administrativo ahorrado vale USD 600/mes = ROI en 13 meses.</p>
      <div class="aca-pitch-box">
        <div class="label">CÓMO LO EXPLICÁS</div>
        <div class="text">"El Estratégico 360 es para proyectos grandes donde el sistema tiene que ser único. Lo integramos con el ERP que ya usan (Tango, SAP), le sumamos inteligencia para que el sistema priorice los leads más calientes, y queda todo documentado con garantía de 12 meses. Es el plan que estamos implementando, por ejemplo, en un Parque Logístico Industrial de 120 hectáreas."</div>
      </div>

      <h3>Cómo elegir qué plan ofrecer</h3>
      <table class="aca-table">
        <thead><tr><th>Si el cliente dice...</th><th>Ofrecés</th></tr></thead>
        <tbody>
          <tr><td>"No tengo presencia digital"</td><td><strong>Base (USD 1.050)</strong></td></tr>
          <tr><td>"Tengo web pero no me sirve"</td><td><strong>Pro (USD 2.900)</strong></td></tr>
          <tr><td>"Vendo productos técnicos complejos"</td><td><strong>Industrial (USD 4.900)</strong></td></tr>
          <tr><td>"Uso Tango/SAP y quiero integrar todo"</td><td><strong>Estratégico 360 (USD 7.500)</strong></td></tr>
          <tr><td>"Soy un desarrollador inmobiliario"</td><td><strong>Estratégico 360 (USD 7.500)</strong></td></tr>
          <tr><td>"Soy corporativo, tengo +100 empleados"</td><td><strong>Estratégico 360 (USD 7.500)</strong></td></tr>
        </tbody>
      </table>

      <div class="aca-tip">
        <div class="label">TIP DE UPSELL</div>
        <div class="text">Si el cliente dudaba entre 2 planes, siempre empujá al <strong>más caro</strong>. Tu comisión sube y el cliente termina más contento (siempre es mejor empezar con más herramientas que quedarse corto). Un cliente que contrata Pro y a los 3 meses necesita Industrial termina pagando más que si hubiese arrancado con Industrial directo.</div>
      </div>

      <h3>Todos los planes incluyen</h3>
      <ul>
        <li><strong>Pago único</strong> (sin costo mensual obligatorio)</li>
        <li><strong>Código 100% del cliente</strong> (no es licencia ni alquiler)</li>
        <li><strong>Implementación en el plazo pactado</strong> (si nos atrasamos, te compensamos)</li>
        <li><strong>Factura A o B</strong></li>
      </ul>
    `
  },
  {
    id: 5,
    num: 'MÓDULO 05',
    title: 'Cómo cerrar con el guión por rubro',
    slug: 'mod-cierre',
    desc: 'El pitch exacto, la pregunta de cierre y el mensaje de WhatsApp para cada rubro — copiá y pegá.',
    tiempo: '6 min',
    icon: 'fa-handshake',
    content: `
      <div class="av-explainer"><div class="av-tag"><i class="fa-solid fa-circle-play"></i> Masterclass de cierre &middot; M&Oacute;DULO 05</div><div class="av-stage"><div class="av-slide center" data-dur="6000"><div class="av-kicker r d1">El cierre completo &middot; M&oacute;dulo 05</div><div class="av-h r d2">Cierre de ventas,<br>de principio a <span class="o">fin</span></div><div class="av-sub r d3">El proceso oficial de Avanza, paso a paso. Lo mismo que usan los aliados que cierran 3 veces m&aacute;s. Te sirve sin importar el rubro.</div></div><div class="av-slide" data-dur="6500"><div class="av-kicker r d1">El mapa: 5 pasos</div><div class="av-flow"><div class="av-step r d2"><div class="sn">01</div><div class="st">Abr&iacute;s</div><div class="sd">con el problema, nunca con la soluci&oacute;n.</div></div><div class="av-step r d3"><div class="sn">02</div><div class="st">Pregunt&aacute;s</div><div class="sd">las 3 preguntas en orden.</div></div><div class="av-step r d4"><div class="sn">03</div><div class="st">Present&aacute;s</div><div class="sd">y mand&aacute;s el brochure.</div></div><div class="av-step r d5"><div class="sn">04</div><div class="st">Manej&aacute;s</div><div class="sd">las 3 objeciones.</div></div><div class="av-step r d6"><div class="sn">05</div><div class="st">Cerr&aacute;s</div><div class="sd">con fecha concreta.</div></div></div></div><div class="av-slide" data-dur="8500"><div class="av-kicker r d1">Paso 1 &middot; Abr&iacute; con el problema</div><div class="av-grid g3"><div class="av-card r d2"><div class="t" style="color:#3b82f6">Lead de la Bolsa</div><div class="d">&ldquo;Trabajamos con empresas de tu rubro. El canal digital no genera consultas, todo entra por tel&eacute;fono o referidos. &iquest;Es as&iacute; en tu caso?&rdquo;</div></div><div class="av-card r d3"><div class="t" style="color:#3b82f6">Referido por tu link</div><div class="d">&ldquo;Entraste por nuestro sistema. Antes de contarte m&aacute;s: &iquest;todo sigue llegando por referidos y tel&eacute;fono?&rdquo;</div></div><div class="av-card r d4"><div class="t" style="color:#3b82f6">Prospecto propio</div><div class="d">&ldquo;Vengo siguiendo tu rubro en la zona y veo que casi todos consiguen clientes por boca en boca. &iquest;C&oacute;mo es en tu empresa?&rdquo;</div></div></div></div><div class="av-slide center" data-dur="6000"><div class="av-quote r d1"><span class="lab">Regla de oro</span>Nunca digas &ldquo;te traigo una propuesta&rdquo;. Arranc&aacute; siempre con una <b>pregunta sobre su problema</b>. El cliente que reconoce su propio problema es el que cierra.</div></div><div class="av-slide" data-dur="8500"><div class="av-kicker r d1">Paso 2 &middot; Las 3 preguntas (en orden)</div><div class="av-tl"><div class="tr r d2"><div class="tm">01</div><div class="tx"><b>&iquest;Cu&aacute;nto tardan en responder una consulta nueva?</b> Si dice m&aacute;s de 1 hora &rarr; ah&iacute; est&aacute; el dolor. No juzgues, escuch&aacute;.</div></div><div class="tr r d3"><div class="tm">02</div><div class="tx"><b>&iquest;Cu&aacute;ntas consultas se pierden por mes?</b> La mayor&iacute;a no sabe el n&uacute;mero. Eso ya es el diagn&oacute;stico.</div></div><div class="tr r d4"><div class="tm">03</div><div class="tx"><b>&iquest;Un sistema que responde en 60 seg resolver&iacute;a el problema?</b> Si dice s&iacute; &rarr; cerr&aacute;s. Si duda &rarr; calcul&aacute;s juntos lo que pierde.</div></div></div></div><div class="av-slide" data-dur="8000"><div class="av-kicker r d1">Paso 3 &middot; Present&aacute; la soluci&oacute;n</div><div class="av-quote r d2"><span class="lab">Decilo as&iacute;</span>&ldquo;Eso es exactamente lo que hace Avanza. No son una agencia de dise&ntilde;o &mdash; construyen <b>sistemas de ventas</b> para industrias como la tuya. El c&oacute;digo queda tuyo, pag&aacute;s una vez y no depend&eacute;s de nadie. 4 planes desde USD 1.050. Te mando el cat&aacute;logo.&rdquo;</div><div class="av-ok r d3"><span class="lab">Acci&oacute;n</span>Mand&aacute; el <b>brochure comercial por WhatsApp</b> en ese momento.</div></div><div class="av-slide" data-dur="8500"><div class="av-kicker r d1">Paso 4 &middot; Las 3 objeciones</div><div class="av-list"><div class="av-li r d2"><i class="fa-solid fa-coins"></i><span><b>&ldquo;Es caro&rdquo;</b> &rarr; ROI: &ldquo;&iquest;Cu&aacute;nto vale cerrar 1 venta m&aacute;s por mes? Una venta B2B supera USD 3.000. El Plan Base se paga con tu primera venta.&rdquo;</span></div><div class="av-li r d3"><i class="fa-solid fa-globe"></i><span><b>&ldquo;Ya tengo web&rdquo;</b> &rarr; sistema vs presencia: &ldquo;&iquest;Tu web manda los leads directo y clasificados a tu vendedor? Eso es lo que falta.&rdquo;</span></div><div class="av-li r d4"><i class="fa-solid fa-clock"></i><span><b>&ldquo;Lo veo despu&eacute;s&rdquo;</b> &rarr; costo de esperar: &ldquo;Si tard&aacute;s un mes m&aacute;s, &iquest;cu&aacute;ntas consultas se pierden? El diagn&oacute;stico es gratis y dura 15 minutos.&rdquo;</span></div></div></div><div class="av-slide" data-dur="9000"><div class="av-kicker r d1">Paso 5 &middot; Cerr&aacute; vos mismo</div><div class="av-tl"><div class="tr r d2"><div class="tm"><i class="fa-solid fa-calendar-check" style="color:#f97316"></i></div><div class="tx"><b>Fecha concreta.</b> No preguntes &ldquo;&iquest;te interesa?&rdquo; &mdash; pregunt&aacute; <b>cu&aacute;ndo</b> puede en los pr&oacute;ximos 2 d&iacute;as.</div></div><div class="tr r d3"><div class="tm"><i class="fa-solid fa-volume-xmark" style="color:#f97316"></i></div><div class="tx"><b>Silencio activo.</b> Despu&eacute;s de la pregunta de cierre, callate. El primero que habla, cede.</div></div><div class="tr r d4"><div class="tm"><i class="fa-solid fa-rotate-left" style="color:#f97316"></i></div><div class="tx"><b>Si duda, volv&eacute; al dolor.</b> Recordale cu&aacute;nto pierde por mes sin el sistema.</div></div><div class="tr r d5"><div class="tm"><i class="fa-solid fa-check" style="color:#4ade80"></i></div><div class="tx"><b>Si dice s&iacute;.</b> Acord&aacute; plan, monto y forma de pago. Confirm&aacute; por WhatsApp en el momento.</div></div></div></div><div class="av-slide" data-dur="9000"><div class="av-kicker r d1">El arma secreta: el caso de su rubro</div><div class="av-grid g2"><div class="av-card r d2"><div class="t">Metal&uacute;rgica / Agro</div><div class="d"><b>Aleametal</b> &middot; +47% conversi&oacute;n &rarr; Industrial USD 4.900</div></div><div class="av-card r d3"><div class="t">Transporte / Log&iacute;stica</div><div class="d"><b>Logística Cordillera</b> &middot; 31h &rarr; 4h &rarr; Pro USD 2.900</div></div><div class="av-card r d4"><div class="t">Servicios t&eacute;cnicos</div><div class="d"><b>Soluciones Técnicas Generales</b> &middot; USD 8.400 en 1 trim. &rarr; Base USD 1.050</div></div><div class="av-card r d5"><div class="t">Gran escala / Corporativo</div><div class="d"><b>PLI</b> &middot; 120 ha &rarr; Estrat&eacute;gico 360 USD 7.500</div></div></div><div class="av-quote r d6" style="margin-top:2px"><span class="lab">Por qu&eacute; funciona</span>Si vas con Aleametal a un transportista, el cliente no se reconoce. El caso correcto cierra <b>3 veces m&aacute;s</b>.</div></div><div class="av-slide" data-dur="9500"><div class="av-kicker r d1">El diagn&oacute;stico de 15 minutos (lo hac&eacute;s vos)</div><div class="av-tl"><div class="tr r d2"><div class="tm">0&ndash;3</div><div class="tx"><b>Confirm&aacute; el problema.</b> &ldquo;Contame c&oacute;mo llegan hoy las consultas y qu&eacute; pasa con las que no se responden.&rdquo;</div></div><div class="tr r d3"><div class="tm">3&ndash;7</div><div class="tx"><b>Cuantific&aacute; el costo.</b> &ldquo;Si perd&eacute;s 3 consultas/mes y el ticket es USD X, son USD Y que se van.&rdquo;</div></div><div class="tr r d4"><div class="tm">7&ndash;11</div><div class="tx"><b>Present&aacute; un solo plan.</b> El del caso de su rubro. No muestres todos.</div></div><div class="tr r d5"><div class="tm">11&ndash;15</div><div class="tx"><b>Cerr&aacute; en el momento.</b> Plan, monto y pago por WhatsApp antes de colgar.</div></div></div><div class="av-warn r d6"><span class="lab">El paso que no se negocia</span>Registr&aacute; al cliente en el portal <b>ANTES de que pague</b>, o la comisi&oacute;n no se te atribuye.</div></div></div><div class="av-controls"><button class="av-btn prev" aria-label="Anterior"><i class="fa-solid fa-backward-step"></i></button><button class="av-btn play" aria-label="Pausar"><i class="fa-solid fa-pause"></i></button><button class="av-btn next" aria-label="Siguiente"><i class="fa-solid fa-forward-step"></i></button><div class="av-progress"><div class="av-progress-fill"></div></div><div class="av-dots"></div><div class="av-counter">1 / 1</div></div></div>

      <h3>Lo que vas a aprender</h3>
      <ul>
        <li>Qué caso usar según el rubro del prospecto</li>
        <li>El pitch verbal listo para recitar</li>
        <li>El mensaje de WhatsApp para mandarlo tras conseguir el SÍ</li>
      </ul>

      <h3>Matriz de decisión rápida</h3>
      <table class="aca-table">
        <thead><tr><th>Rubro del cliente</th><th>Caso a usar</th><th>Plan que cerrás</th></tr></thead>
        <tbody>
          <tr><td>Metalúrgica · Fábrica · Agroindustria · Alimentos</td><td>Aleametal</td><td><strong>Industrial USD 4.900</strong></td></tr>
          <tr><td>Transporte · Logística · Distribución · Mayorista</td><td>Logística Cordillera</td><td><strong>Pro USD 2.900</strong></td></tr>
          <tr><td>Servicios técnicos · Mantenimiento · Construcción</td><td>Soluciones Técnicas Generales</td><td><strong>Base USD 1.050</strong></td></tr>
          <tr><td>Parque industrial · Desarrollo · Corporativo +100</td><td>PLI Norte Argentino</td><td><strong>Estratégico 360 USD 7.500</strong></td></tr>
        </tbody>
      </table>

      <h3>Caso 1 · Metalúrgica, Fábrica, Agroindustria</h3>
      <div class="aca-pitch-box">
        <div class="label">PITCH VERBAL</div>
        <div class="text">"Te cuento rápido: uno de los clientes que te va a sonar parecido es <strong>Aleametal, en Perú</strong>. Hacen equipos para agroindustria, 38 empleados. Tenían exactamente tu problema — consultas por WhatsApp del vendedor, email y teléfono, sin control. <strong>Les armamos un panel único en 21 días</strong>. Hoy tienen cero consultas sin respuesta en 24hs y mejoraron un 47% la conversión. Recuperaron dos clientes grandes que ya habían dado por perdidos."</div>
      </div>
      <div class="aca-highlight">
        <div class="label">▸ PREGUNTA DE CIERRE</div>
        <div class="text">"¿Te suena el problema? ¿Querés que te pase el detalle del plan para tu empresa?"</div>
      </div>
      <div class="aca-pitch-box">
        <div class="label">MENSAJE DE WHATSAPP (TRAS EL SÍ)</div>
        <div class="text">Hola [Nombre], como charlamos, te dejo el caso de Aleametal en Perú — mismo rubro que [empresa]. Tenían el mismo problema de presupuestos que se perdían. En 21 días montaron el sistema y mejoraron un 47% la conversión.<br><br>Te paso el brochure y el detalle del Plan Industrial (USD 4.900). Si querés avanzar, coordinamos una llamada de 15 minutos con el equipo técnico para arrancar. ¿Esta semana te queda bien?</div>
      </div>

      <h3>Caso 2 · Transporte, Logística, Distribución</h3>
      <div class="aca-pitch-box">
        <div class="label">PITCH VERBAL</div>
        <div class="text">"Hay un caso que te puede interesar. <strong>Logística Cordillera, en Chile</strong>. 22 empleados. Recibían cotizaciones por 4 canales distintos. Tardaban 31 horas en contestar un presupuesto. En transporte eso es muerte — el cliente cotiza con la competencia. <strong>En 14 días les montamos el sistema</strong>. Hoy responden en 4 horas automáticamente y cerraron 3 contratos nuevos el primer mes."</div>
      </div>
      <div class="aca-highlight">
        <div class="label">▸ PREGUNTA DE CIERRE</div>
        <div class="text">"¿Cuánto tardan ustedes hoy en responder una cotización? ¿Te sirve arreglar eso en 14 días?"</div>
      </div>
      <div class="aca-pitch-box">
        <div class="label">MENSAJE DE WHATSAPP (TRAS EL SÍ)</div>
        <div class="text">Hola [Nombre], como charlamos, te paso el caso de Logística Cordillera en Chile. Pasaron de tardar 31hs a 4hs en responder cotizaciones y cerraron 3 contratos nuevos el primer mes.<br><br>Te dejo el brochure y el detalle del Plan Pro (USD 2.900). Si te cierra, coordinamos llamada técnica de 15 minutos para arrancar. ¿Esta semana o la próxima?</div>
      </div>

      <h3>Caso 3 · Servicios técnicos, Mantenimiento, Construcción</h3>
      <div class="aca-pitch-box">
        <div class="label">PITCH VERBAL</div>
        <div class="text">"Te cuento un caso ideal para empresas de tu perfil. <strong>Soluciones Técnicas Generales, en Argentina</strong>. 9 empleados, 12 años en el mercado. Conseguían clientes solo por referidos. <strong>En 7 días montamos el Plan Base</strong>. A los 20 días les entró la primera consulta de una empresa con la que nunca habían tenido contacto. USD 8.400 en contratos nuevos el primer trimestre. El plan se pagó solo con la primera venta."</div>
      </div>
      <div class="aca-highlight">
        <div class="label">▸ PREGUNTA DE CIERRE</div>
        <div class="text">"Por USD 1.050 arrancás en 7 días. Con una sola consulta nueva al mes ya te conviene, ¿no?"</div>
      </div>

      <h3>Caso 4 · Proyectos de gran escala</h3>
      <div class="aca-pitch-box">
        <div class="label">PITCH DE AUTORIDAD</div>
        <div class="text">"Para que veas la escala que manejamos: hay un proyecto en implementación con un <strong>Parque Logístico Industrial de 120 hectáreas en el norte argentino</strong>, 89 lotes y empresas ancla como Andreani, Grido e YPF Gas. Se está construyendo con 4 embudos segmentados y un cotizador inteligente de lotes. Lo tuyo puede ser más chico, pero el enfoque es el mismo."</div>
      </div>
      <div class="aca-highlight">
        <div class="label">▸ PREGUNTA DE CIERRE</div>
        <div class="text">"El Estratégico 360 son USD 7.500, pago único. Una sola empresa o inversor captado por el sistema lo paga. ¿Avanzamos?"</div>
      </div>

      <div class="aca-tip">
        <div class="label">REGLA DE ORO</div>
        <div class="text">Un aliado que usa el caso correcto del rubro cierra <strong>3 veces más</strong> que uno que improvisa. Si vas con Aleametal a un transportista, el cliente no se reconoce. Si vas con Logística Cordillera a una metalúrgica, tampoco. Memoriza los 4 casos por rubro — eso solo te cambia el resultado del mes.</div>
      </div>
    `
  },
  {
    id: 6,
    num: 'MÓDULO 06',
    title: 'Manejo de las 6 objeciones más comunes',
    slug: 'mod-objeciones',
    desc: 'Qué te van a decir los clientes para no comprar, y qué responder sin pelear.',
    tiempo: '7 min',
    icon: 'fa-shield',
    content: `
      <div class="av-explainer"><div class="av-tag"><i class="fa-solid fa-circle-play"></i> Manejo de objeciones &middot; M&Oacute;DULO 06</div><div class="av-stage"><div class="av-slide center" data-dur="5500"><div class="av-kicker r d1">Manejo de objeciones &middot; M&oacute;dulo 06</div><div class="av-h r d2">Las <span class="o">6 objeciones</span><br>m&aacute;s comunes</div><div class="av-sub r d3">Qu&eacute; te van a decir para no comprar y c&oacute;mo responder sin pelear. La t&eacute;cnica: reformular en t&eacute;rminos de ROI.</div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Objeci&oacute;n 1</div><div class="av-warn r d2"><span class="lab">El cliente dice</span>&ldquo;Es caro&rdquo;</div><div class="av-ok r d3"><span class="lab">Lo que dec&iacute;s</span>&iquest;Cu&aacute;nto vale para vos cerrar una venta m&aacute;s por mes? En industria una venta B2B supera USD 3.000. El Plan Base <b>se paga con tu primera venta cerrada</b> &mdash; y no una al a&ntilde;o: todos los meses.</div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Objeci&oacute;n 2</div><div class="av-warn r d2"><span class="lab">El cliente dice</span>&ldquo;Ya tengo una web&rdquo;</div><div class="av-ok r d3"><span class="lab">Lo que dec&iacute;s</span>La diferencia no es tener web, es tener un <b>sistema que genera y clasifica leads solo</b>. &iquest;Tu web actual manda los leads directo a tu vendedor? Eso es lo que falta.</div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Objeci&oacute;n 3</div><div class="av-warn r d2"><span class="lab">El cliente dice</span>&ldquo;Lo voy a pensar / lo veo despu&eacute;s&rdquo;</div><div class="av-ok r d3"><span class="lab">Lo que dec&iacute;s</span>Entiendo. Pero si tard&aacute;s un mes m&aacute;s en resolver esto, <b>&iquest;cu&aacute;ntas consultas calcul&aacute;s que se pierden?</b> El diagn&oacute;stico es gratis y dura 15 minutos.</div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Objeci&oacute;n 4</div><div class="av-warn r d2"><span class="lab">El cliente dice</span>&ldquo;No tengo tiempo para implementar&rdquo;</div><div class="av-ok r d3"><span class="lab">Lo que dec&iacute;s</span>Esa es justo la idea: <b>lo implementamos nosotros</b>. Vos das la info inicial una vez y en el plazo pactado lo ten&eacute;s funcionando, sin mover a tu equipo.</div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Objeci&oacute;n 5</div><div class="av-warn r d2"><span class="lab">El cliente dice</span>&ldquo;Ya tenemos WhatsApp Business&rdquo;</div><div class="av-ok r d3"><span class="lab">Lo que dec&iacute;s</span>Genial para responder. Pero WhatsApp <b>no clasifica ni hace seguimiento solo</b>: cuando llegan 30 consultas juntas igual se pierden. Lo que se agrega es el panel que unifica WhatsApp, email y tel&eacute;fono, visible para el due&ntilde;o.</div></div><div class="av-slide" data-dur="8000"><div class="av-kicker r d1">Objeci&oacute;n 6</div><div class="av-warn r d2"><span class="lab">El cliente dice</span>&ldquo;D&eacute;jame hablarlo con mi socio&rdquo;</div><div class="av-ok r d3"><span class="lab">Lo que dec&iacute;s</span>L&oacute;gico. Para que se lo lleves claro, te mando el <b>brochure con el caso de tu rubro</b> y los n&uacute;meros. &iquest;Coordinamos una llamada corta con los dos as&iacute; respondo lo que surja?</div></div><div class="av-slide center" data-dur="6000"><div class="av-quote r d1"><span class="lab">La regla detr&aacute;s de todas</span>Toda objeci&oacute;n se responde <b>devolviendo el costo de no actuar</b>. No defiendas el precio: mostr&aacute; lo que pierde por mes sin el sistema.</div></div></div><div class="av-controls"><button class="av-btn prev" aria-label="Anterior"><i class="fa-solid fa-backward-step"></i></button><button class="av-btn play" aria-label="Pausar"><i class="fa-solid fa-pause"></i></button><button class="av-btn next" aria-label="Siguiente"><i class="fa-solid fa-forward-step"></i></button><div class="av-progress"><div class="av-progress-fill"></div></div><div class="av-dots"></div><div class="av-counter">1 / 1</div></div></div>

      <h3>Lo que vas a aprender</h3>
      <ul>
        <li>Las 6 objeciones que escuchás en el 90% de los cierres</li>
        <li>Cómo responder cada una con naturalidad</li>
        <li>Cuándo insistir y cuándo dejar ir</li>
      </ul>

      <h3>Objeción 1 · "Es caro"</h3>
      <div class="aca-objection">
        <div class="q">Lo que dice el cliente</div>
        <div class="a">"USD 2.900 es mucho para mí ahora." / "Está fuera de mi presupuesto."</div>
      </div>
      <p><strong>Respuesta:</strong></p>
      <div class="aca-pitch-box">
        <div class="label">LO QUE DECÍS</div>
        <div class="text">"Entiendo. Te hago una pregunta simple: <strong>¿cuánto vale para vos cerrar una venta más por mes?</strong> Si tu venta promedio está en USD 3.000 o más — y en industria lo está — el plan se paga solo con una venta cerrada de más. Y no estamos hablando de una venta al año: hablamos de <strong>todos los meses</strong>, durante años."</div>
      </div>
      <p>Si el cliente sigue dudando, agregá:</p>
      <div class="aca-pitch-box">
        <div class="label">COMPLEMENTO</div>
        <div class="text">"Además es pago único — no es alquiler ni licencia mensual. Pagás una vez y el código queda tuyo. La mayoría de webs del mercado son alquiler de USD 200/mes, lo cual en 2 años es más caro que esto."</div>
      </div>

      <h3>Objeción 2 · "Ya tengo una web"</h3>
      <div class="aca-objection">
        <div class="q">Lo que dice el cliente</div>
        <div class="a">"Ya gasté plata en una web hace 2 años, no voy a hacer otra."</div>
      </div>
      <div class="aca-pitch-box">
        <div class="label">LO QUE DECÍS</div>
        <div class="text">"Ok, dejame preguntarte: <strong>¿cuántos clientes nuevos te trae esa web por mes?</strong> Si la respuesta es cero o casi cero — que es lo más común — entonces no tenés una web, tenés una tarjeta de presentación digital. La diferencia no es tener web, es tener un sistema que <strong>genera y califica leads automáticamente</strong>. ¿Tu web hace eso hoy?"</div>
      </div>

      <h3>Objeción 3 · "Lo voy a pensar"</h3>
      <div class="aca-objection">
        <div class="q">Lo que dice el cliente</div>
        <div class="a">"Sí, me interesa, déjame pensarlo y te aviso."</div>
      </div>
      <p>"Lo voy a pensar" es la objeción más peligrosa. Casi siempre es un "no" disfrazado.</p>
      <div class="aca-pitch-box">
        <div class="label">LO QUE DECÍS</div>
        <div class="text">"Por supuesto, es una decisión importante. Para ayudarte a pensarlo mejor: <strong>¿qué es lo que te genera más dudas específicamente?</strong> ¿El precio, el tiempo que toma, o algo del sistema en sí?"</div>
      </div>
      <p>La pregunta fuerza a concretar la duda. Una vez que la dice, tenés algo para responder. Si dice "no sé, lo necesito pensar más", entonces acordá una fecha de seguimiento:</p>
      <div class="aca-pitch-box">
        <div class="label">SEGUIMIENTO</div>
        <div class="text">"Perfecto. ¿Te parece si te vuelvo a escribir el viernes? Así, si tenés alguna pregunta, me la podés hacer directamente."</div>
      </div>

      <h3>Objeción 4 · "No tengo tiempo para implementar esto"</h3>
      <div class="aca-objection">
        <div class="q">Lo que dice el cliente</div>
        <div class="a">"Estoy con mil cosas, no puedo dedicarle tiempo a esto ahora."</div>
      </div>
      <div class="aca-pitch-box">
        <div class="label">LO QUE DECÍS</div>
        <div class="text">"Por eso existe este servicio. <strong>Vos no implementás nada</strong> — Avanza hace todo. Tu única tarea es una llamada de 30 minutos al inicio para contarles cómo funciona tu negocio, y otra al final para ver la entrega. Total: 1 hora tuya en todo el proceso. El resto lo hacen ellos. Eso es exactamente el valor del servicio — te ahorra el tiempo que no tenés."</div>
      </div>

      <h3>Objeción 5 · "Ya tenemos WhatsApp Business"</h3>
      <div class="aca-objection">
        <div class="q">Lo que dice el cliente</div>
        <div class="a">"Ya usamos WhatsApp Business y funciona bien."</div>
      </div>
      <div class="aca-pitch-box">
        <div class="label">LO QUE DECÍS</div>
        <div class="text">"Aleametal también lo usaba. <strong>El problema no es tener WhatsApp, es que nadie ve lo que pasa ahí adentro.</strong> Si hoy el vendedor atiende y no cierra, ¿vos como dueño cómo te enterás? Lo que agregamos es el panel que unifica WhatsApp, email y teléfono, con etapas visibles y seguimiento automático. Por eso Aleametal recuperó dos clientes grandes que ya habían dado por perdidos."</div>
      </div>

      <h3>Objeción 6 · "Déjame hablarlo con mi socio/equipo"</h3>
      <div class="aca-objection">
        <div class="q">Lo que dice el cliente</div>
        <div class="a">"Tengo que consultarlo con mi socio antes de avanzar."</div>
      </div>
      <p>Esto puede ser real o excusa. Descubrís cuál es con una pregunta:</p>
      <div class="aca-pitch-box">
        <div class="label">LO QUE DECÍS</div>
        <div class="text">"Perfecto. <strong>Si él/ella dice que sí, vos avanzás? ¿O hay algo más que te frene a vos también?</strong>"</div>
      </div>
      <p>Si dice "sí, yo avanzo" → real. Acordá fecha de respuesta. Si dice "bueno, también tengo que ver unos temas" → el socio es excusa, el freno es otro. Volvé a preguntar cuál.</p>

      <div class="aca-warn">
        <div class="label">CUÁNDO DEJAR IR</div>
        <div class="text">Si usás estas respuestas y el cliente sigue dudando después de 2 seguimientos, <strong>dejalo ir con gracia</strong>. No insistas más. Escribile un último mensaje amable: <em>"Entiendo que no es el momento. Te dejo mis datos por si en algún momento lo retomás. Éxitos con el negocio."</em> El 20% de esos clientes vuelven solos a los 3-6 meses cuando el dolor se hace insoportable.</div>
      </div>
    `
  },
  {
    id: 8,
    canal: 'canal1',
    num: 'MÓDULO 07',
    title: 'Materiales descargables',
    slug: 'mod-materiales-c1',
    desc: 'Brochure comercial, guión por rubro, contrato y kit corto — todo lo que necesitás para vender.',
    tiempo: '2 min',
    icon: 'fa-download',
    content: `
      <div class="av-explainer"><div class="av-tag"><i class="fa-solid fa-circle-play"></i> Tu kit de ventas &middot; M&Oacute;DULO 07</div><div class="av-stage"><div class="av-slide center" data-dur="5500"><div class="av-kicker r d1">Tu kit &middot; M&oacute;dulo 07</div><div class="av-h r d2">Qu&eacute; mandar<br>en <span class="o">cada momento</span></div><div class="av-sub r d3">Todo lo que necesit&aacute;s para vender est&aacute; listo para descargar abajo. Ac&aacute; va el orden y tus herramientas.</div></div><div class="av-slide" data-dur="6500"><div class="av-kicker r d1">El flujo de una venta</div><div class="av-flow"><div class="av-step r d2"><div class="sn">01</div><div class="st">Auditor&iacute;a</div><div class="sd">Antes de la reuni&oacute;n, como anticipo.</div></div><div class="av-step r d3"><div class="sn">02</div><div class="st">Brochure</div><div class="sd">Para mostrarle en la charla.</div></div><div class="av-step r d4"><div class="sn">03</div><div class="st">Gui&oacute;n por rubro</div><div class="sd">El caso exacto de su industria.</div></div><div class="av-step r d5"><div class="sn">04</div><div class="st">Contrato</div><div class="sd">Tras el s&iacute;, para formalizar.</div></div></div></div><div class="av-slide" data-dur="8000"><div class="av-kicker r d1">Herramientas del portal</div><div class="av-grid g2"><div class="av-card r d2"><div class="t"><i class="fa-solid fa-layer-group" style="color:#3b82f6"></i> Bolsa de Leads</div><div class="d">Reclam&aacute;s, 48hs para contactar, m&aacute;x 3 activos.</div></div><div class="av-card r d3"><div class="t"><i class="fa-solid fa-robot" style="color:#4ade80"></i> Perfilado IA + NBA</div><div class="d">Score, plan y siguiente mejor acci&oacute;n.</div></div><div class="av-card r d4"><div class="t"><i class="fa-solid fa-paper-plane" style="color:#3b82f6"></i> Piloto Autom&aacute;tico</div><div class="d">Secuencia de emails por etapa.</div></div><div class="av-card r d5"><div class="t"><i class="fa-solid fa-calculator" style="color:#f97316"></i> Cotizador con IA</div><div class="d">Precio, comisi&oacute;n, link de pago y pitch.</div></div></div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Tus dos links</div><div class="av-cols"><div class="av-col good r d2"><div class="hd">Para vender</div><div class="it"><b>P&aacute;gina de ventas</b> &mdash; /p/tu-link</div><div class="it">Se la mand&aacute;s al cliente que quer&eacute;s cerrar.</div></div><div class="av-col r d3" style="border-color:rgba(59,130,246,.25);background:rgba(59,130,246,.06)"><div class="hd" style="color:#93c5fd">Para reclutar</div><div class="it"><b>Link de reclutamiento</b> &mdash; /alianzas?ref=tu-link</div><div class="it">Para sumar aliados a tu red (Mi Red, ingreso pasivo).</div></div></div></div><div class="av-slide" data-dur="6500"><div class="av-kicker r d1">Kit de marca</div><div class="av-list"><div class="av-li r d2"><i class="fa-brands fa-linkedin"></i><span><b>Banner LinkedIn</b> (1584&times;396) &mdash; sub&iacute; sin recortar</span></div><div class="av-li r d3"><i class="fa-solid fa-image"></i><span><b>Logos</b> fondo claro/oscuro + isotipo transparente</span></div><div class="av-li r d4"><i class="fa-solid fa-palette"></i><span><b>Kit de identidad</b>: colores, tipograf&iacute;a y c&oacute;mo presentarte</span></div><div class="av-li r d5"><i class="fa-solid fa-share-nodes"></i><span><b>Casos para postear</b>: Aleametal, Logística Cordillera, Soluciones Técnicas Generales, PLI</span></div></div></div><div class="av-slide center" data-dur="6000"><div class="av-ok r d1"><span class="lab">Listo para usar</span>Todos estos materiales est&aacute;n <b>m&aacute;s abajo en este mismo m&oacute;dulo</b>, listos para descargar. Guard&aacute;los en el celu y tenelos a mano en cada llamada.</div></div></div><div class="av-controls"><button class="av-btn prev" aria-label="Anterior"><i class="fa-solid fa-backward-step"></i></button><button class="av-btn play" aria-label="Pausar"><i class="fa-solid fa-pause"></i></button><button class="av-btn next" aria-label="Siguiente"><i class="fa-solid fa-forward-step"></i></button><div class="av-progress"><div class="av-progress-fill"></div></div><div class="av-dots"></div><div class="av-counter">1 / 1</div></div></div>

      <h3>Lo que vas a aprender</h3>
      <ul>
        <li>Qué material mandar en cada momento del cierre</li>
        <li>Cómo usar cada PDF correctamente</li>
      </ul>

      <h3>Los 4 materiales oficiales</h3>

      <h3>Generar contrato</h3>
      <p style="color:var(--text-dim);font-size:.85rem;margin:-4px 0 10px;">Gener&aacute; el contrato de servicio en <strong>PDF o Word</strong>: el plan y los datos del cliente se completan, el Anexo se arma solo, queda listo para firmar.</p>
      <p style="color:var(--text-dim);font-size:.82rem;line-height:1.6;margin:-2px 0 14px;padding:10px 14px;background:rgba(59,130,246,.06);border-left:3px solid #3b82f6;border-radius:4px;">
        <strong style="color:#fff;">El contrato es opcional</strong> &mdash; gener&aacute;lo solo si el cliente lo pide; no es obligaci&oacute;n hacerlo para todos los clientes.<br>
        Para avanzar al pago, el cliente <strong>siempre abona desde tu link de p&aacute;gina de ventas personal</strong>: si no quiere contrato, mandale directo tu link; si pide contrato, igual paga desde ese mismo link (el contrato no reemplaza al pago).
      </p>
      <div class="aca-downloads">
        <a class="aca-download" href="#" onclick="abrirModalContrato();return false;">
          <i class="fa-solid fa-file-signature" style="color:#1463ff;"></i>
          <div><div class="dl-title">Generar contrato (PDF o Word)</div><div class="dl-sub">Solo si el cliente lo pide &middot; se completa solo</div></div>
        </a>
      </div>

      <!-- ── CANAL 1 ── -->
      <div class="d-canal1-only">
        <h3>Materiales Canal 1</h3>
        <div class="aca-downloads">
          <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1hj4ZUv2SDL5VtlpFODMqXPsrF0Ou8Zxn','Brochure_Comercial_AvanzaDigital_v4.pdf');return false;">
            <i class="fa-solid fa-file-pdf" style="color:#ef4444;"></i>
            <div><div class="dl-title">Brochure Comercial v4</div><div class="dl-sub">Para mostrarle al cliente</div></div>
          </a>
          <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1kLslLvgP7R9QXx9s3Gw0ftjsdnWziwK2','Avanza_Partner_Intro_Canal1.pdf');return false;">
            <i class="fa-solid fa-rocket" style="color:#f97316;"></i>
            <div><div class="dl-title">Partner Intro — Canal 1</div><div class="dl-sub">Presentación del programa para tu canal</div></div>
          </a>
          <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=11WKAQPxlu3DHxG_dRN8y5WB5tdpiIFzM','Guion_Ventas_Aliados_v2.pdf');return false;">
            <i class="fa-solid fa-list-ol" style="color:#4ade80;"></i>
            <div><div class="dl-title">Guión de Ventas v2</div><div class="dl-sub">Qué decir en cada momento</div></div>
          </a>
          <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1j7VRc_DHVOaQEo9_NdT4WDI9TVXyevrR','Guion_Ventas_Aliados_Canal1.pdf');return false;">
            <i class="fa-solid fa-list-ol" style="color:#f97316;"></i>
            <div><div class="dl-title">Guión Canal 1</div><div class="dl-sub">Pitch adaptado a tu perfil de aliado</div></div>
          </a>
          <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1_JCSowJx9bH9AtLbutL052v3evuWQd0a','Guion_Ventas_Por_Rubro_v2.pdf');return false;">
            <i class="fa-solid fa-book" style="color:#3b82f6;"></i>
            <div><div class="dl-title">Guión por Rubro v2</div><div class="dl-sub">Casos por industria para cerrar</div></div>
          </a>
          <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1AbJJPQYD2js_QmrxTmpv2K73iCs6Z0hW','Manual_Producto_Aliados_AvanzaDigital.pdf');return false;">
            <i class="fa-solid fa-circle-info" style="color:#a78bfa;"></i>
            <div><div class="dl-title">Manual de Producto</div><div class="dl-sub">Qué vendés por dentro, para responder con seguridad</div></div>
          </a>
          <a class="aca-download" href="https://avanzadigital.digital/demo.html" target="_blank" rel="noopener">
            <i class="fa-solid fa-display" style="color:#22d3ee;"></i>
            <div><div class="dl-title">Lo que vendés, plan por plan</div><div class="dl-sub">La consola explicada por nivel (Base → 360). Es interna — no se la mandes al cliente</div></div>
          </a>
        </div>

      <h3>Kit de Marca · Tu presencia online</h3>
      <p style="color:var(--text-dim);font-size:.85rem;margin:-4px 0 12px;">Esto es para <strong>tu perfil</strong> (LinkedIn y redes), no para mandarle al cliente. Bajá el banner, el logo y la guía de identidad.</p>
      <div class="aca-downloads">
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1f-SgCcagc_uACtVxnoOCVD5ER4ncEnIL','Kit_Identidad_Aliados_Canal1.pdf');return false;">
          <i class="fa-solid fa-palette" style="color:#f97316;"></i>
          <div><div class="dl-title">Kit de Identidad (PDF)</div><div class="dl-sub">Colores, tipografía y cómo presentarte en LinkedIn</div></div>
        </a>
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1GJwxVGf9qDLAiCuIBTtsN7pR6Te5n319','Banner_LinkedIn_Aliado_Canal1.png');return false;">
          <i class="fa-brands fa-linkedin" style="color:#0a66c2;"></i>
          <div><div class="dl-title">Banner LinkedIn</div><div class="dl-sub">Portada lista para subir (1584×396). Subila sin recortar.</div></div>
        </a>
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1KtG1HXXLfwLUdzwXkqO7sNh4B7Gti_h0','Avanza_Logo_FondoOscuro.png');return false;">
          <i class="fa-solid fa-image" style="color:#a1a1aa;"></i>
          <div><div class="dl-title">Logo — fondo oscuro</div><div class="dl-sub">Versión clara, para usar sobre fondos oscuros</div></div>
        </a>
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1Dznp8Wo9Ujrd9MXooNe_KnbiWAAwJNy-','Avanza_Logo_FondoClaro.png');return false;">
          <i class="fa-solid fa-image" style="color:#a1a1aa;"></i>
          <div><div class="dl-title">Logo — fondo claro</div><div class="dl-sub">Versión oscura, para usar sobre fondos claros</div></div>
        </a>
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1Ue-To0velBm8YZF4SnYXDdPF275aFzmI','Avanza_Isotipo.png');return false;">
          <i class="fa-solid fa-shapes" style="color:#06b6d4;"></i>
          <div><div class="dl-title">Isotipo</div><div class="dl-sub">Solo el símbolo (las flechas), fondo transparente</div></div>
        </a>
      </div>

      </div>

      <h3>Cuándo usar cada material</h3>

      <h4>Brochure Comercial</h4>
      <p><strong>Cuándo:</strong> después del primer contacto con el cliente, cuando ya calificaste que vale la pena avanzar. Se lo mandás por WhatsApp tras el diagnóstico o tras la primera conversación donde el cliente mostró interés.</p>
      <p><strong>Mensaje para acompañarlo:</strong></p>
      <div class="aca-pitch-box">
        <div class="text">"Hola [Nombre], como charlamos, te paso el brochure con los 4 planes y los casos de éxito. En la página 5 está el caso de [Aleametal/Logística Cordillera/Soluciones Técnicas Generales] que te va a interesar. Si te cierra alguno, coordinamos llamada técnica. ¿Esta semana?"</div>
      </div>

      <h4>Guión de Ventas por Rubro</h4>
      <p><strong>Cuándo:</strong> nunca se lo mandes al cliente. Es tu herramienta personal. Tenelo abierto en el celular durante tus llamadas con clientes — especialmente las primeras 5 o 10 — para recordar el pitch exacto del rubro.</p>

      <h4>Contrato de Aliado</h4>
      <p><strong>Cuándo:</strong> antes de registrarte formalmente. Ya lo firmaste cuando te diste de alta en el portal — este es para que tengas la referencia completa de términos y condiciones, niveles de comisión y obligaciones.</p>

      <h3>Tus dos links — cuál usar en cada caso</h3>
      <p>Tenés dos links distintos en el Dashboard. Confundirlos es el error más común. Este es el mapa:</p>

      <div class="aca-highlight" style="margin-bottom:1rem;">
        <div class="label">LINK 1 — PÁGINA DE VENTAS PERSONAL <code>/p/tu-link</code></div>
        <div class="text"><strong>Para tus clientes (empresas que podrían contratar).</strong> Es una landing de ventas con tus datos, los planes, casos de éxito y botón de pago. Cuando el cliente entra y paga desde ahí, la venta te queda atribuida automáticamente.<br><br>
        Usalo en:<br>
        • Cuando mandás el brochure por WhatsApp tras el pitch<br>
        • En tu firma de email a prospectos<br>
        • En propuestas a empresas</div>
      </div>

      <div class="aca-highlight">
        <div class="label">LINK 2 — LINK DE RECLUTAMIENTO <code>/alianzas?ref=tu-link</code></div>
        <div class="text"><strong>Para otros aliados (vendedores, agencias, consultores que quieran sumarse).</strong> Cuando alguien se registra desde este link, queda bajo tu red y generás 5% pasivo de cada venta que cierre.<br><br>
        Usalo en:<br>
        • Tu bio de LinkedIn<br>
        • Conversaciones con vendedores o agencias que quieran el programa<br>
        • <strong>Nunca</strong> lo mandes a un cliente final — no es una página de compra</div>
      </div>

      <h3>Flujo recomendado para cerrar una venta</h3>
      <ol>
        <li><strong>Primera conversación</strong> (5 min) — Calificás con las 4 preguntas del Módulo 2</li>
        <li><strong>Diagnóstico</strong> (15 min) — Aplicás el guión del Módulo 3</li>
        <li><strong>Pitch del caso</strong> del rubro (Módulo 5)</li>
        <li><strong>Pregunta de cierre</strong> (Módulo 5)</li>
        <li><strong>Si dice sí</strong> → mandás el Brochure en PDF + tu <strong>página de ventas personal</strong> (<code>/p/tu-link</code>) por WhatsApp — el cliente paga directamente ahí y la venta te queda atribuida</li>
        <li><strong>Registrás al cliente en el portal</strong> con su nombre y plan elegido</li>
        <li><strong>Coordinás llamada técnica</strong> con equipo de Avanza para arrancar implementación</li>
        <li><strong>El cliente paga</strong> → tu comisión en 24hs</li>
      </ol>

      <div class="aca-tip">
        <div class="label">¡LISTO!</div>
        <div class="text">Completaste la Academia. Ya sabés todo lo necesario para cerrar tu primera venta. <strong>El próximo paso es salir a vender</strong>. Andá al Dashboard, copiá tu página de ventas personal, y empezá a escribirle a 5 prospectos hoy. La primera venta cambia todo.</div>
      </div>
    `
  },

  // ── MÓDULO 07 · CANAL 2 ──────────────────────────────────────────────────────
  {
    id: 9,
    canal: 'canal2',
    num: 'MÓDULO 07',
    title: 'Tu kit de materiales',
    slug: 'mod-materiales-c2',
    desc: 'Guiones por perfil, brochure v5 y guía por rubro — todo adaptado a tu canal.',
    tiempo: '5 min',
    icon: 'fa-download',
    content: `
      <h3>Lo que vas a aprender</h3>
      <ul>
        <li>Qué guión usar según tu perfil (agencia, consultor, contador o proveedor)</li>
        <li>Cuándo mandar el brochure y el partner intro a tu cliente</li>
        <li>Cómo usar el guión por rubro para cerrar con el caso exacto</li>
      </ul>

      <h3>Tus guiones por perfil</h3>
      <p>Como Canal 2, tu punto de entrada es distinto al de Canal 1: vos ya tenés una relación previa con el cliente. Cada guión está armado para introducir Avanza <strong>sin romper ese vínculo</strong> y sin sonar a vendedor. Descargá el de tu perfil y tenelo abierto en el celular.</p>

      <div class="aca-downloads">
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1GH0y75Db9LtTRUweAnQBm4ZKw9fHkf1H','Guion_Ventas_Canal2_Agencia_v1.pdf');return false;">
          <i class="fa-solid fa-bullhorn" style="color:#f97316;"></i>
          <div>
            <div class="dl-title">Guión — Agencia Digital</div>
            <div class="dl-sub">El cliente pide una web. Vos reencuadrás el pedido</div>
          </div>
        </a>
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1cZICwO6cfT4MmLj7gn37clCr-nN6mgcW','Guion_Ventas_Canal2_Consultor_v1.pdf');return false;">
          <i class="fa-solid fa-magnifying-glass-chart" style="color:#3b82f6;"></i>
          <div>
            <div class="dl-title">Guión — Consultor Industrial</div>
            <div class="dl-sub">Lo incorporás como hallazgo del informe de auditoría</div>
          </div>
        </a>
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1hT4iHxV-2suvC4LAlg2Y0y7gvUsM25iN','Guion_Ventas_Canal2_Contador_v1.pdf');return false;">
          <i class="fa-solid fa-calculator" style="color:#4ade80;"></i>
          <div>
            <div class="dl-title">Guión — Contador / Estudio Contable</div>
            <div class="dl-sub">Lo introducís en la revisión mensual con los números del cliente</div>
          </div>
        </a>
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1KBzZB9XMmwjDhGFOO0kuCp7yIe6eCXM7','Guion_Ventas_Canal2_Proveedor_v1.pdf');return false;">
          <i class="fa-solid fa-truck" style="color:#a78bfa;"></i>
          <div>
            <div class="dl-title">Guión — Proveedor B2B</div>
            <div class="dl-sub">Lo mencionás después de cerrar tu propia venta, nunca antes</div>
          </div>
        </a>
      </div>

      <h3>Brochure y presentación del programa</h3>
      <p>Estos dos documentos son los que <strong>le mandás al cliente</strong> una vez que mostró interés. No los mandes sin contexto — siempre acompañados con el mensaje de WhatsApp de tu guión.</p>

      <div class="aca-downloads">
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1IY7yrkzDdtkvv3U5hNk3C__6iNReN9Au','Brochure_Comercial_AvanzaDigital_v5.pdf');return false;">
          <i class="fa-solid fa-file-pdf" style="color:#ef4444;"></i>
          <div>
            <div class="dl-title">Brochure Comercial v5</div>
            <div class="dl-sub">Planes, precios, casos de éxito y ROI — para mostrarle al cliente</div>
          </div>
        </a>
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1Cc9CTpt0yVNaHfeRzxUZySB__pGf17xt','Avanza_Partner_Intro.pdf');return false;">
          <i class="fa-solid fa-rocket" style="color:#f97316;"></i>
          <div>
            <div class="dl-title">Partner Intro — Canal 2</div>
            <div class="dl-sub">Qué es el programa, cómo funciona y cuánto cobrás</div>
          </div>
        </a>
        <a class="aca-download" href="#" onclick="descargarPDF('https://drive.google.com/uc?export=download&id=1V9HIVq6UCAIu1a_UsG8_dW3msXXtvhBX','Guion_Ventas_Por_Rubro_Aliados_v2.pdf');return false;">
          <i class="fa-solid fa-book" style="color:#3b82f6;"></i>
          <div>
            <div class="dl-title">Guión por Rubro v2</div>
            <div class="dl-sub">El caso exacto para cada industria — metalúrgica, logística, servicios, parques</div>
          </div>
        </a>
      </div>

      <h3>Cuándo usar cada material</h3>

      <div class="aca-highlight" style="margin-bottom:1rem;">
        <div class="label">PASO 1 — EL MOMENTO DE INTRODUCCIÓN</div>
        <div class="text">Usás <strong>tu guión de perfil</strong> para hacer la transición natural. No menciones el brochure todavía — primero conseguís el "me interesa".</div>
      </div>

      <div class="aca-highlight" style="margin-bottom:1rem;">
        <div class="label">PASO 2 — CONSEGUÍS EL SÍ INICIAL</div>
        <div class="text">Ahí mandás el <strong>Brochure Comercial v5</strong> por WhatsApp, acompañado del mensaje que está en tu guión de perfil. No mandes solo el PDF — siempre con contexto.</div>
      </div>

      <div class="aca-highlight" style="margin-bottom:1rem;">
        <div class="label">PASO 3 — CLIENTE PREGUNTA POR EL CASO DE SU RUBRO</div>
        <div class="text">Abrís el <strong>Guión por Rubro</strong>, encontrás el caso que más se parece a su empresa (Aleametal = metalúrgica/fábrica; Logística Cordillera = transporte/logística; Sol. Técnicas = servicios/mantenimiento; PLI = proyectos de escala) y contás ese caso con el pitch verbal que está en el guión.</div>
      </div>

      <div class="aca-highlight">
        <div class="label">REGLA DE ORO DE CANAL 2</div>
        <div class="text"><strong>Registrá al cliente en el portal ANTES de que pague.</strong> Eso es lo único técnico que tenés que hacer. Si lo registrás después, corrés el riesgo de perder la comisión porque el sistema ya no puede atribuirte la venta.</div>
      </div>

      <h3>Tu flujo de cierre según perfil</h3>
      <table class="aca-table">
        <thead>
          <tr><th>Perfil</th><th>Momento de entrada</th><th>Regla de oro</th></tr>
        </thead>
        <tbody>
          <tr><td><strong>Agencia</strong></td><td>El cliente pide una web / rediseño / SEO</td><td>Reencuadrá ANTES de cotizar — si cotizás primero, Avanza parece caro</td></tr>
          <tr><td><strong>Consultor</strong></td><td>Entrega del informe / "optimizamos todo menos ventas"</td><td>Incluilo como hallazgo del informe, no como sugerencia suelta</td></tr>
          <tr><td><strong>Contador</strong></td><td>Revisión mensual / facturación estancada</td><td>Usá los números del cliente, no los de un caso ajeno</td></tr>
          <tr><td><strong>Proveedor B2B</strong></td><td>Después de cerrar tu pedido, nunca antes</td><td>Primero cerrás lo tuyo — tu venta principal no puede sufrir</td></tr>
        </tbody>
      </table>

      <div class="aca-tip">
        <div class="label">¡LISTO!</div>
        <div class="text">Completaste la Academia. Tenés el guión de tu perfil, el brochure y el caso de rubro. <strong>El próximo paso es identificar 3 clientes de tu cartera actual que podrían necesitar un sistema comercial</strong> y aplicar el momento de entrada de tu guión en la próxima conversación que ya tengas con ellos.</div>
      </div>
    `
  },
  {
    id: 10,
    num: 'MÓDULO 08',
    title: 'LinkedIn B2B: el canal con más potencial a mediano plazo',
    slug: 'mod-linkedin',
    desc: 'Cómo usar LinkedIn para prospectar gerentes industriales, publicar contenido que genera autoridad y cerrar reuniones sin parecer vendedor.',
    tiempo: '12 min',
    icon: 'fa-linkedin',
    content: `
      <div class="av-explainer"><div class="av-tag"><i class="fa-solid fa-circle-play"></i> Prospecci&oacute;n LinkedIn &middot; M&Oacute;DULO 08</div><div class="av-stage"><div class="av-slide center" data-dur="5500"><div class="av-kicker r d1">Prospecci&oacute;n &middot; M&oacute;dulo 08</div><div class="av-h r d2"><span class="b">LinkedIn</span> B2B</div><div class="av-sub r d3">El canal con m&aacute;s potencial a mediano plazo para llegar a due&ntilde;os y gerentes industriales.</div></div><div class="av-slide center" data-dur="6500"><div class="av-kicker r d1">Por qu&eacute; es distinto</div><div class="av-sub r d2" style="max-width:48ch">WhatsApp sirve para cerrar. Instagram para awareness. <b>LinkedIn es el &uacute;nico canal</b> donde un gerente de compras busca proveedores con intenci&oacute;n declarada, en horario laboral.</div></div><div class="av-slide center" data-dur="6500"><div class="av-kicker r d1">La regla de oro</div><div class="av-h r d2" style="font-size:clamp(1.3rem,3.6vw,2.1rem)">LinkedIn <span class="o">calienta</span>,<br>WhatsApp <span class="g">cierra</span></div><div class="av-sub r d3">No es para vender en el primer mensaje. Es para que cuando el prospecto tenga el problema, vos seas el primero que recuerda.</div></div><div class="av-slide" data-dur="7500"><div class="av-kicker r d1">Tu perfil = landing, no CV</div><div class="av-cols"><div class="av-col bad r d2"><div class="hd">Titular que NO</div><div class="it">&ldquo;Consultor de Marketing | Especialista en Redes | Freelance&rdquo;</div></div><div class="av-col good r d3"><div class="hd">Titular que convierte</div><div class="it">&ldquo;Sistemas de ventas B2B para empresas industriales en LATAM &middot; Canal certificado Avanza&rdquo;</div></div></div></div><div class="av-slide center" data-dur="6500"><div class="av-kicker r d1">A qui&eacute;n conectar</div><div class="av-stat r d2"><span class="b">30&ndash;50</span></div><div class="av-sub r d3">prospectos calificados por semana con la cuenta gratis. Filtr&aacute; por Cargo &rarr; Industria &rarr; Ubicaci&oacute;n. Busc&aacute; due&ntilde;os y gerentes comerciales de PYMEs industriales.</div></div><div class="av-slide" data-dur="8000"><div class="av-kicker r d1">El mensaje de conexi&oacute;n &middot; 3 pasos</div><div class="av-tl"><div class="tr r d2"><div class="tm">01</div><div class="tx"><b>Person&aacute;.</b> Mencion&aacute; algo concreto de su empresa o rubro.</div></div><div class="tr r d3"><div class="tm">02</div><div class="tx"><b>Aport&aacute; valor.</b> Un dato o caso de su sector (Aleametal, Logística Cordillera&hellip;).</div></div><div class="tr r d4"><div class="tm">03</div><div class="tx"><b>No vendas.</b> En el primer mensaje solo abr&iacute;s la puerta. M&aacute;ximo 280 caracteres.</div></div></div></div><div class="av-slide center" data-dur="6000"><div class="av-quote r d1"><span class="lab">El puente a WhatsApp</span>Cuando responde y hay inter&eacute;s, llev&aacute; la charla a WhatsApp: ah&iacute; aplic&aacute;s el cierre del M&oacute;dulo 5. LinkedIn abre, WhatsApp cierra.</div></div></div><div class="av-controls"><button class="av-btn prev" aria-label="Anterior"><i class="fa-solid fa-backward-step"></i></button><button class="av-btn play" aria-label="Pausar"><i class="fa-solid fa-pause"></i></button><button class="av-btn next" aria-label="Siguiente"><i class="fa-solid fa-forward-step"></i></button><div class="av-progress"><div class="av-progress-fill"></div></div><div class="av-dots"></div><div class="av-counter">1 / 1</div></div></div>

      <h3>Por qué LinkedIn es diferente a todos los otros canales</h3>
      <p>WhatsApp sirve para cerrar. Instagram sirve para awareness. LinkedIn es el único canal donde un <strong>gerente de compras industrial</strong> está activamente buscando proveedores y soluciones, con intención declarada, dentro de su horario laboral.</p>
      <p>En LATAM, la penetración de LinkedIn entre decisores industriales B2B (gerentes de compra, directores de operaciones, dueños de PYMEs exportadoras) creció más del 40% desde 2022. El problema: casi nadie de tu competencia lo usa bien, lo que te deja un canal con muy poca fricción.</p>

      <div class="aca-highlight">
        <div class="label">REGLA DE ORO LINKEDIN</div>
        <div class="text">LinkedIn no es para vender. Es para que cuando el prospecto tenga el problema, <strong>vos seas la primera persona que recuerda</strong>. El cierre pasa por WhatsApp o llamada — LinkedIn es el calentador.</div>
      </div>

      <h3>Paso 1 — Tu perfil tiene que ser una landing page, no un CV</h3>
      <p>El error más común: el perfil parece un currículum de empleado en vez de la página de un especialista que resuelve un problema específico. Cuando un gerente industrial hace click en tu perfil, tiene que entender en 5 segundos qué problema resolvés y para quién.</p>

      <h4>CHECKLIST DE PERFIL</h4>
      <ul>
        <li><strong>Foto:</strong> fondo liso, ropa de trabajo (no casual), mirada a la cámara. Sin filtros.</li>
        <li><strong>Banner:</strong> no dejes el azul default. Usá una imagen con el texto "Sistemas de ventas B2B para PYMEs industriales de LATAM" o pedile a Avanza el banner de aliado.</li>
        <li><strong>Titular (headline):</strong> no pongas tu cargo. Poné el resultado que generás. Ejemplo: <em>"Ayudo a PYMEs metalmecánicas a conseguir clientes industriales sin depender del boca a boca · Aliado Avanza Digital"</em></li>
        <li><strong>Sección Acerca de:</strong> abrí con el problema del cliente, no con tu historia. Estructura: 1) problema, 2) cómo lo resolvés, 3) para quién, 4) call to action (link a tu página de ventas del portal).</li>
        <li><strong>Experiencia:</strong> describí resultados, no tareas. "Implementé sistemas de captación de leads para 12 metalúrgicas en Santa Fe, Córdoba y Buenos Aires" es infinitamente mejor que "consultor de marketing digital".</li>
        <li><strong>URL personalizada:</strong> linkedin.com/in/tu-nombre — sin números ni letras random.</li>
      </ul>

      <div class="aca-pitch-box">
        <div class="label">EJEMPLO — TITULAR QUE CONVIERTE</div>
        <div class="text">❌ "Consultor de Marketing | Especialista en Redes Sociales | Freelance"<br><br>✅ <strong>"Sistemas de ventas B2B para empresas industriales en LATAM · Metalúrgicas, Agro, Logística · Canal certificado Avanza Digital"</strong></div>
      </div>

      <h3>Paso 2 — A quién conectar (y a quién NO)</h3>
      <p>La calidad de tu red importa más que el número de conexiones. LinkedIn empieza a limitar el alcance orgánico cuando tu tasa de aceptación baja del 20%. Mandá solicitudes solo a personas con probabilidad real de aceptar y de ser prospectos.</p>

      <h4>PERFIL IDEAL DEL PROSPECTO EN LINKEDIN</h4>
      <ul>
        <li>Cargo: Gerente Comercial, Director de Operaciones, Dueño / Socio, Gerente de Ventas, Gerente de Compras</li>
        <li>Industria: Metalúrgica, Agro, Logística, Construcción, Frigorífico, Servicios Técnicos</li>
        <li>Tamaño de empresa: 10–250 empleados (PYME con escala)</li>
        <li>Ubicación: Argentina, México, Colombia, Chile, Perú</li>
        <li>Señal de actividad: publicó algo en los últimos 30 días O comentó en posts de industria</li>
      </ul>

      <h4>CÓMO ENCONTRARLOS</h4>
      <p>Usá el buscador de LinkedIn con filtros: <strong>Personas → Cargo → Industria → Ubicación</strong>. Sin Sales Navigator, tenés ~100 búsquedas gratis por mes. Con la cuenta gratuita bien usada, podés identificar 30–50 prospectos calificados por semana.</p>

      <div class="aca-tip">
        <div class="label">TRUCO — EVENTOS DE INDUSTRIA</div>
        <div class="text">Buscá eventos de industria en LinkedIn (ferias metalmecánicas, congresos agro, cámaras industriales). Los asistentes son prospectos perfectos. Podés filtrar "asistentes" y conectar con todos con una nota personalizada.</div>
      </div>

      <h3>Paso 3 — El mensaje de conexión que abre la puerta</h3>
      <p>El 90% de los mensajes de conexión en LinkedIn son spam genérico. Un mensaje personalizado que muestra que investigaste al prospecto tiene una tasa de apertura 4–6x mayor.</p>

      <h4>ESTRUCTURA DEL MENSAJE DE CONEXIÓN (280 caracteres máx)</h4>
      <p>Hola [nombre], vi que estás en [industria/empresa]. Trabajo con PYMEs [sector] en LATAM en sistemas de captación de clientes B2B. ¿Te parece conectar? Sin compromiso.</p>

      <div class="aca-pitch-box">
        <div class="label">PLANTILLA 1 — CONEXIÓN FRÍA (metalúrgica)</div>
        <div class="text">Hola [Nombre], vi que están en fabricación de [producto] en [ciudad]. Trabajo con metalúrgicas B2B en sistemas de generación de leads industriales. ¿Conectamos? Sin compromiso.</div>
      </div>

      <div class="aca-pitch-box">
        <div class="label">PLANTILLA 2 — CONEXIÓN FRÍA (agro/logística)</div>
        <div class="text">Hola [Nombre], trabajo con empresas [agro/logísticas] en LATAM en digitalización comercial B2B. Vi que [empresa] opera en [zona]. ¿Conectamos para compartir contenido del sector?</div>
      </div>

      <div class="aca-warn">
        <div class="label">LO QUE NO HAY QUE HACER</div>
        <div class="text">No mandes el pitch ni el link en el mensaje de conexión. No menciones precios ni propuestas en los primeros 2 mensajes. No uses mensajes copiados idénticos — LinkedIn los detecta y sombrea tu cuenta.</div>
      </div>

      <h3>Paso 4 — La conversación que lleva a la reunión</h3>
      <p>Una vez aceptada la conexión, esperá 48–72 horas antes de escribir. El primer mensaje post-conexión <strong>no vende nada</strong> — abre conversación.</p>

      <h4>SECUENCIA DE 3 MENSAJES</h4>

      <div class="aca-pitch-box">
        <div class="label">MENSAJE 1 — Post-conexión (día 2-3)</div>
        <div class="text">Hola [Nombre], gracias por conectar. Vi que publicaste sobre [tema reciente o industria]. Curioso: ¿cómo están manejando hoy la captación de nuevos clientes B2B? ¿Referidos, visita en frío, ferias?</div>
      </div>

      <div class="aca-pitch-box">
        <div class="label">MENSAJE 2 — Si responden (día 4-7)</div>
        <div class="text">Tiene sentido. Trabajamos con varias [metalúrgicas/empresas de logística/etc.] en Argentina y México que tenían ese mismo problema. Implementamos un sistema de generación de consultas por Google + automatización de seguimiento. En [Empresa X] bajaron el tiempo de respuesta de 48hs a 4hs y duplicaron las cotizaciones mensuales. ¿Vale la pena que hablemos 15 minutos para ver si aplica a su caso?</div>
      </div>

      <div class="aca-pitch-box">
        <div class="label">MENSAJE 3 — Cierre de reunión</div>
        <div class="text">¿Cuándo tenés 15 minutos esta semana o la que viene? Puedo mostrarte el sistema funcionando en vivo con un caso de [rubro similar]. Sin compromiso, sin presentación de ventas — solo te muestro qué está haciendo la competencia.</div>
      </div>

      <h3>Paso 5 — Contenido que genera autoridad (sin ser influencer)</h3>
      <p>No necesitás publicar todos los días ni tener miles de seguidores. Con <strong>2 posts por semana</strong> durante 3 meses, un perfil B2B bien posicionado empieza a recibir solicitudes entrantes de prospectos. El algoritmo de LinkedIn prioriza consistencia sobre viralidad.</p>

      <h4>CALENDARIO DE CONTENIDO — 4 TIPOS DE POST</h4>

      <table class="aca-table">
        <thead><tr><th>Tipo</th><th>Frecuencia</th><th>Estructura</th><th>Ejemplo</th></tr></thead>
        <tbody>
          <tr>
            <td><strong>Caso real</strong></td><td>1 vez/semana</td>
            <td>Problema → Sistema → Resultado (con número)</td>
            <td>"Una metalúrgica de Perú tenía el 40% de sus presupuestos sin seguimiento. Implementamos un sistema de alertas automáticas. Resultado: +62% de cotizaciones respondidas en menos de 4hs."</td>
          </tr>
          <tr>
            <td><strong>Error común</strong></td><td>1 vez/semana</td>
            <td>"El error más común de las PYMEs [sector] en [tema]"</td>
            <td>"El error más común de las empresas de logística al digitalizar: poner el cotizador online sin entrenar al equipo comercial para cerrar por teléfono."</td>
          </tr>
          <tr>
            <td><strong>Dato del sector</strong></td><td>2 veces/mes</td>
            <td>Estadística + tu lectura</td>
            <td>"El 68% de los compradores B2B industriales en LATAM consulta mínimo 3 proveedores online antes de contactar. Si tu web no aparece en esa búsqueda, no existís en ese proceso de compra."</td>
          </tr>
          <tr>
            <td><strong>Pregunta al sector</strong></td><td>2 veces/mes</td>
            <td>Pregunta abierta que genera comentarios</td>
            <td>"¿Cómo llegan los nuevos clientes a tu empresa hoy? Recomendaciones, ferias, Google, frío. Curioso qué funciona en [sector]."</td>
          </tr>
        </tbody>
      </table>

      <div class="aca-tip">
        <div class="label">FORMATO QUE MÁS FUNCIONA EN 2025</div>
        <div class="text">Posts de texto de 5–8 líneas con saltos de párrafo, sin links en el cuerpo (el algoritmo penaliza los links — ponelos en el primer comentario). Una pregunta al final. Sin hashtags genéricos — si usás hashtags, que sean de nicho: #metalurgiaargentina #logisticaB2B.</div>
      </div>

      <h3>Paso 6 — Métricas para saber si está funcionando</h3>
      <table class="aca-table">
        <thead><tr><th>Métrica</th><th>Benchmark mes 1</th><th>Benchmark mes 3</th></tr></thead>
        <tbody>
          <tr><td>Conexiones nuevas/semana</td><td>15–20</td><td>25–40</td></tr>
          <tr><td>Tasa de aceptación de solicitudes</td><td>&gt; 25%</td><td>&gt; 30%</td></tr>
          <tr><td>Respuestas a mensaje 1</td><td>&gt; 10%</td><td>&gt; 15%</td></tr>
          <tr><td>Conversaciones que llegan a reunión</td><td>1 de cada 10</td><td>2 de cada 10</td></tr>
          <tr><td>Reuniones/mes desde LinkedIn</td><td>1–2</td><td>3–5</td></tr>
          <tr><td>Impresiones por post</td><td>300–800</td><td>1.000–3.000</td></tr>
        </tbody>
      </table>

      <div class="aca-highlight">
        <div class="label">TIEMPO DE INVERSIÓN</div>
        <div class="text">30 min/día bien invertidos en LinkedIn generan más pipeline B2B que 4 horas de llamadas en frío. Dividí así: 10 min conectar + mensajes, 10 min comentar posts de prospectos, 10 min escribir o planificar contenido.</div>
      </div>

      <div class="aca-tip">
        <div class="label">ACCIÓN INMEDIATA</div>
        <div class="text">Antes de cerrar este módulo: (1) actualizá tu titular de LinkedIn con la estructura que aprendiste, (2) identificá 10 prospectos con el filtro de búsqueda, (3) mandá 5 solicitudes de conexión personalizadas hoy. El momentum empieza con las primeras 5.</div>
      </div>
    `
  },
  {
    id: 11,
    canal: 'canal1',
    num: 'MÓDULO 09',
    title: 'Setting B2B: cómo llegar al que decide',
    slug: 'mod-setting',
    desc: 'Identificá al que firma, pasá la recepción sin que te filtren y ganate los primeros 30 segundos con el dueño.',
    tiempo: '9 min',
    icon: 'fa-key',
    content: `
    <div class="av-explainer"><div class="av-tag"><i class="fa-solid fa-circle-play"></i> Setting B2B &middot; M&Oacute;DULO 09</div><div class="av-stage">

      <div class="av-slide center" data-dur="6000">
        <div class="av-kicker r d1">Avanza Partner Network &middot; Canal 1</div>
        <div class="av-h r d2">No le vend&eacute;s a una empresa.<br><span class="b">Lleg&aacute;s a una persona.</span></div>
        <div class="av-sub r d3">La mayor&iacute;a de los closers se queda trabado en la recepci&oacute;n. Este m&oacute;dulo es para pasar ese filtro y hablar con el que firma.</div>
      </div>

      <div class="av-slide" data-dur="8000">
        <div class="av-kicker r d1">Qui&eacute;n decide en una PYME industrial</div>
        <div class="av-grid g2">
          <div class="av-card r d2"><div class="t">Due&ntilde;o / Socio gerente</div><div class="d">En empresas de 10 a 50 personas, casi siempre firma &eacute;l. <b>Tu objetivo #1.</b></div></div>
          <div class="av-card r d3"><div class="t">Gerente comercial</div><div class="d">Decide o influye fuerte. V&aacute;lido si no lleg&aacute;s al due&ntilde;o.</div></div>
          <div class="av-card r d4"><div class="t">Encargado de compras</div><div class="d">Compra insumos, <b>no</b> decide invertir en un sistema. No pierdas tiempo ac&aacute;.</div></div>
          <div class="av-card r d5"><div class="t">Hijo/a o sucesi&oacute;n</div><div class="d">Suele empujar lo digital. Aliado interno valioso.</div></div>
        </div>
      </div>

      <div class="av-slide" data-dur="7500">
        <div class="av-kicker r d1">La recepci&oacute;n te filtra si son&aacute;s a vendedor</div>
        <div class="av-warn r d2"><span class="lab">Frases que te queman</span>&ldquo;&iquest;C&oacute;mo est&aacute; usted hoy?&rdquo; &middot; &ldquo;Le hablo para ofrecerle&hellip;&rdquo; &middot; &ldquo;&iquest;Es el encargado de compras?&rdquo; &middot; cualquier pitch dicho a la recepci&oacute;n.</div>
        <div class="av-ok r d3"><span class="lab">Lo que s&iacute; pasa el filtro</span>Nombre de pila del que decide + tono de quien ya lo conoce + un motivo que la recepci&oacute;n <b>no puede evaluar</b>.</div>
      </div>

      <div class="av-slide" data-dur="8000">
        <div class="av-kicker r d1">Los primeros 10 segundos con el due&ntilde;o</div>
        <div class="av-flow">
          <div class="av-step r d2"><div class="sn">01</div><div class="st">Nombre + micro-permiso</div><div class="sd">&ldquo;Santiago, te robo 30 segundos y vos me dec&iacute;s si sigo o corto.&rdquo;</div></div>
          <div class="av-step r d3"><div class="sn">02</div><div class="st">Problema, no producto</div><div class="sd">Nombr&aacute;s el dolor (no el sistema): presupuestos sin seguimiento, invisibles en Google.</div></div>
          <div class="av-step r d4"><div class="sn">03</div><div class="st">Pregunta que abre</div><div class="sd">&ldquo;&iquest;Te suena el problema o ya lo tienen resuelto?&rdquo;</div></div>
        </div>
      </div>

      <div class="av-slide" data-dur="7500">
        <div class="av-kicker r d1">Si te bloquean, no cortes: sac&aacute; informaci&oacute;n</div>
        <div class="av-row r d2" style="gap:8px">
          <div class="av-kpi"><div class="v">Nombre</div><div class="k">del que decide</div></div>
          <div class="av-kpi"><div class="v">Mail</div><div class="k">directo, no info@</div></div>
          <div class="av-kpi"><div class="v">Horario</div><div class="k">en que lo encontr&aacute;s</div></div>
          <div class="av-kpi"><div class="v">WhatsApp</div><div class="k">para el toque 2</div></div>
        </div>
        <div class="av-sub r d3" style="margin-top:2px">Una llamada &ldquo;fallida&rdquo; que te deja el nombre y el horario es una llamada ganada.</div>
      </div>

      <div class="av-slide center" data-dur="6000">
        <div class="av-kicker r d1">Regla de oro del setting</div>
        <div class="av-h r d2">Nunca llames <span class="b">sin el nombre</span> del que decide.</div>
        <div class="av-sub r d3">Saber el nombre de pila antes de marcar es lo que m&aacute;s sube tu tasa de &ldquo;pasame con&hellip;&rdquo;. Se consigue en 3 minutos.</div>
      </div>

    </div><div class="av-controls"><button class="av-btn prev" aria-label="Anterior"><i class="fa-solid fa-backward-step"></i></button><button class="av-btn play" aria-label="Pausar"><i class="fa-solid fa-pause"></i></button><button class="av-btn next" aria-label="Siguiente"><i class="fa-solid fa-forward-step"></i></button><div class="av-progress"><div class="av-progress-fill"></div></div><div class="av-dots"></div><div class="av-counter">1 / 1</div></div></div>

    <h3>Lo que vas a aprender</h3>
    <ul>
      <li>Quién decide de verdad en una PYME industrial (y a quién NO le pierdas tiempo)</li>
      <li>Cómo conseguir el nombre del que firma en 3 minutos, antes de llamar</li>
      <li>Cómo pasar la recepción sin que te corten con "mandá un mail"</li>
      <li>Qué decir en los primeros 10 segundos con el dueño para ganarte 30 más</li>
      <li>El plan B multicanal cuando no llegás por teléfono</li>
    </ul>

    <p><strong>Por qué este módulo:</strong> en B2B no contactás a una persona, contactás a una empresa. Y entre vos y el que firma casi siempre hay un filtro humano —recepción, secretaría— cuyo trabajo, literal, es no pasarte. El setting es el arte de llegar al que decide. Es el paso donde se cae la mayoría de los closers, y por eso es donde más rápido vas a notar la diferencia en tu cierre.</p>

    <h3>Paso 1 — Identificá al que decide (antes de marcar)</h3>
    <p>En una PYME industrial de 10 a 50 personas, el que tiene la lapicera para invertir en un sistema casi siempre es el <strong>dueño o socio gerente</strong>. El encargado de compras compra chapa o repuestos: no decide una inversión digital. Apuntá arriba.</p>

    <table class="aca-table">
      <thead><tr><th>Rol</th><th>¿Decide la compra?</th><th>Qué hacés</th></tr></thead>
      <tbody>
        <tr><td><strong>Dueño / Socio gerente</strong></td><td>Sí (objetivo #1)</td><td>Apuntás directo a él</td></tr>
        <tr><td>Gerente comercial</td><td>Decide o influye fuerte</td><td>Válido si no llegás al dueño</td></tr>
        <tr><td>Hijo/a en la sucesión</td><td>Empuja lo digital</td><td>Aliado interno: te abre la puerta</td></tr>
        <tr><td>Encargado de compras / administración</td><td>No</td><td>Lo usás solo para conseguir datos del que decide</td></tr>
      </tbody>
    </table>

    <p><strong>Conseguí el nombre en 3 minutos</strong> (esto es lo que más sube tu tasa de "pasame con…"):</p>
    <ol>
      <li><strong>LinkedIn:</strong> buscá la empresa &rarr; "Personas" &rarr; filtrá por "Propietario", "Socio Gerente", "Director Comercial".</li>
      <li><strong>Web de la empresa:</strong> la sección "Quiénes somos" / "Nosotros" suele tener nombre y a veces foto.</li>
      <li><strong>Google:</strong> <code>"nombre de la empresa" dueño OR gerente OR socio</code> — aparecen notas, cámaras industriales, premios.</li>
      <li><strong>Instagram / Facebook de la empresa:</strong> en PYMEs industriales el dueño suele aparecer firmando posts o en fotos de planta.</li>
    </ol>

    <div class="aca-highlight">
      <div class="label">REGLA DE ORO</div>
      <div class="text">Nunca llames sin el <strong>nombre de pila</strong> del que decide. Pedir "con el gerente" te delata como vendedor frío. Pedir "con Santiago" suena a que ya lo conocés.</div>
    </div>

    <h3>Paso 2 — Pasá la recepción sin que te filtren</h3>
    <p>La persona que atiende está entrenada para filtrar vendedores. Si le vendés a ella, perdiste: su trabajo es cortarte. El truco no es "engañarla", es <strong>no darle motivos para filtrarte</strong> y, cuando se puede, hacerla tu aliada.</p>

    <p><strong>Las 5 técnicas que funcionan:</strong></p>
    <ol>
      <li><strong>Nombre de pila + tono de familiaridad.</strong> "Hola, ¿me pasás con Santiago?" — calmado, sin apuro, como quien llama todos los días. El apuro y la formalidad excesiva gritan "vendedor".</li>
      <li><strong>Pedí ayuda en vez de pedir permiso.</strong> A los vendedores los cortan; a quien pide ayuda lo ayudan: "Hola, capaz me podés ayudar vos… estoy buscando a la persona que ve el tema comercial / digital de la empresa, ¿con quién sería?"</li>
      <li><strong>Un motivo que no pueda evaluar.</strong> Si te pregunta "¿de qué se trata?", no pitchees. "Es por un tema puntual de la parte comercial, ¿está Santiago?" Lo que ella no puede juzgar, lo pasa.</li>
      <li><strong>Llamá en horario sin filtro.</strong> Antes de las 9, después de las 18, o en el horario de almuerzo. En PYMEs industriales el dueño suele atender el teléfono directo cuando la recepción no está.</li>
      <li><strong>Evitá las frases-bandera.</strong> Nada de "¿cómo está usted hoy?", "le hablo de [empresa] para ofrecerle…", "¿es el encargado de compras?". Cada una de esas te manda directo al filtro.</li>
    </ol>

    <p><strong>Banco de respuestas (cuando te bloquean):</strong></p>
    <table class="aca-table">
      <thead><tr><th>Te dicen</th><th>Respondés</th></tr></thead>
      <tbody>
        <tr><td>"¿De parte de quién?"</td><td>Tu nombre, tranquilo, y seguís: "…¿está Santiago?" (sin agregar empresa ni motivo).</td></tr>
        <tr><td>"¿De qué se trata?"</td><td>"Un tema puntual de la parte comercial / digital. ¿Lo tenés ahí?" No abras el pitch.</td></tr>
        <tr><td>"Mandá un mail a info@…"</td><td>"Dale, lo mando. ¿Cuál es el mail directo de Santiago así no se pierde entre todo lo que les llega?" (conseguís el mail real).</td></tr>
        <tr><td>"Está en reunión / no está"</td><td>"Perfecto, no lo molesto. ¿En qué horario lo encuentro mejor? Y de paso, ¿con quién hablo yo así te lo nombro cuando vuelva a llamar?" (horario + nombre de la recepción para el toque 2).</td></tr>
        <tr><td>"Ya trabajamos con alguien"</td><td>"Buenísimo, no vengo a reemplazar a nadie. Justo por eso quería hablar 2 minutos con Santiago. ¿Me lo pasás o le dejo un mensaje?"</td></tr>
      </tbody>
    </table>

    <h3>Paso 3 — Ganate los primeros 30 segundos con el que decide</h3>
    <p>Cuando te pasan, tenés ~10 segundos antes de que decida si te escucha o te corta. La estructura ganadora: <strong>nombre + micro-permiso + problema (no producto) + pregunta que abre</strong>. Hablás del dolor, nunca del sistema.</p>

    <p><strong>Guion — Metalúrgica:</strong><br>
    "Santiago, te robo 30 segundos y vos me decís si sigo o corto. Trabajo con metalúrgicas de la zona que estaban perdiendo presupuestos porque las consultas llegaban por WhatsApp y mail y nadie les hacía seguimiento. ¿Te suena ese problema o ya lo tienen resuelto?"</p>

    <p><strong>Guion — Logística / Agro:</strong><br>
    "Hola Martín, 30 segundos y me decís si te interesa o lo dejamos. Ayudo a empresas como la tuya a que cuando un cliente las busca en Google aparezcan primero y la consulta entre ordenada, en vez de perderse. ¿Hoy cómo les llegan los pedidos nuevos?"</p>

    <p><strong>Guion — Genérico:</strong><br>
    "Hola [nombre], te robo medio minuto. Trabajo con PYMEs que reciben consultas por varios canales pero no tienen forma de saber cuántas se cierran ni cuántas se pierden. ¿Eso lo tienen medido o es más a ojo?"</p>

    <div class="aca-warn">
      <div class="label">QUÉ NO HACER EN ESOS 30 SEGUNDOS</div>
      <div class="text">No largues el pitch completo, no expliques los planes, no preguntes "¿cómo está hoy?". El objetivo del opener NO es vender: es ganarte permiso para los próximos 2 minutos. Si te dice "contame", ganaste — y ahí pasás al guion de cierre por rubro y al manejo de objeciones que ya viste en la Academia.</div>
    </div>

    <h3>Plan B — Si no llegás por teléfono</h3>
    <p>El teléfono es el canal más rápido, pero no el único. Los dueños de PYMEs industriales están en WhatsApp y muchos en LinkedIn (donde tenés el módulo dedicado). Secuencia de toques recomendada:</p>
    <ol>
      <li><strong>Toque 1 — Llamada.</strong> Intentás pasar la recepción. Si no, conseguís nombre + horario + el mail directo.</li>
      <li><strong>Toque 2 — WhatsApp directo</strong> (si conseguiste el número): mensaje corto, mismo enfoque de problema. "Hola Santiago, soy [nombre]. Trabajo con metalúrgicas que estaban perdiendo presupuestos por falta de seguimiento. ¿Te muestro en 2 min cómo lo resolvieron? Si no es para vos, sin drama."</li>
      <li><strong>Toque 3 — LinkedIn:</strong> conectás y mandás una nota breve con el mismo ángulo. Sirve además para que te vea como profesional, no como spam.</li>
      <li><strong>Toque 4 — Volvés a llamar</strong> en el horario que te dieron, nombrando a la recepción ("Hola, me dijo [recepción] que a esta hora lo encontraba a Santiago").</li>
    </ol>

    <h3>Ficha de setting (completala antes de cada llamada)</h3>
    <ul>
      <li><strong>Empresa:</strong> _______ &nbsp;&middot;&nbsp; <strong>Rubro:</strong> _______</li>
      <li><strong>Nombre del que decide:</strong> _______ &nbsp;&middot;&nbsp; <strong>Rol:</strong> _______</li>
      <li><strong>Dolor más probable</strong> (según rubro): _______</li>
      <li><strong>Frase de apertura</strong> elegida: _______</li>
      <li><strong>Si me filtran:</strong> objetivo = nombre + horario + mail directo</li>
    </ul>

    <h3>Errores que te queman</h3>
    <ul>
      <li>Llamar sin el nombre del que decide.</li>
      <li>Pitchear a la recepción.</li>
      <li>Hablar de "un sistema / una web" en vez del problema del cliente.</li>
      <li>Pedir "con el gerente / el encargado" en lugar del nombre de pila.</li>
      <li>Cortar cuando te dicen "no está", sin sacar nombre ni horario.</li>
      <li>Soltar el pitch completo en los primeros 10 segundos.</li>
    </ul>

    <div class="aca-highlight">
      <div class="label">MEDÍ TU SETTING</div>
      <div class="text">Llevá tres números por semana: <strong>llamadas hechas &rarr; "pasame con…" logrados &rarr; decisores con los que hablaste</strong>. Cuando suba el del medio, sabés que tu manejo de la recepción está mejorando. Es el embudo antes del embudo.</div>
    </div>
  `
  }
];

function inicializarAcademia() {
  renderAcademiaIndex();         // Renderiza rápido con caché local
  sincronizarProgresoDesdeBackend(); // Luego sincroniza con backend y re-renderiza
}

function renderAcademiaIndex() {
  document.getElementById('aca-index').style.display = 'block';
  document.getElementById('aca-lessons-container').innerHTML = '';

  const esCanal2 = aliado?.tipo_aliado === 'canal2';
  const modulosVisibles = ACADEMIA_MODULOS.filter(m => {
    if (m.canal === 'canal1' && esCanal2) return false;
    if (m.canal === 'canal2' && !esCanal2) return false;
    return true;
  });
  const total = modulosVisibles.length;

  const progreso = getAcademiaProgreso();
  const completados = progreso.filter(id => modulosVisibles.some(m => m.id === id)).length;
  const pct = Math.round((completados / total) * 100);
  
  document.getElementById('aca-progress-fill').style.width = pct + '%';
  document.getElementById('aca-progress-text').textContent = `${completados} de ${total} módulos completados (${pct}%)`;
  
  const grid = document.getElementById('aca-modules-grid');
  grid.innerHTML = modulosVisibles.map(m => `
    <div class="aca-module ${progreso.includes(m.id) ? 'completed' : ''}" onclick="abrirModulo(${m.id})">
      <div class="num">${m.num}</div>
      <h3>${m.title}</h3>
      <p>${m.desc}</p>
      <div class="meta">
        <span><i class="fa-solid ${m.icon}"></i></span>
        <span><i class="fa-solid fa-clock"></i> ${m.tiempo}</span>
        ${progreso.includes(m.id) ? '<span style="color:var(--green);"><i class="fa-solid fa-check"></i> Completado</span>' : ''}
      </div>
    </div>
  `).join('');
}

// Mapea id de módulo actual -> slug estable (según ACADEMIA_MODULOS).
function _slugDeModulo(id) {
  const m = (typeof ACADEMIA_MODULOS !== 'undefined') ? ACADEMIA_MODULOS.find(x => x.id === id) : null;
  return m ? m.slug : null;
}
// Mapea slug estable -> id de módulo actual.
function _idDeSlug(slug) {
  const m = (typeof ACADEMIA_MODULOS !== 'undefined') ? ACADEMIA_MODULOS.find(x => x.slug === slug) : null;
  return m ? m.id : null;
}

// Progreso resiliente: se guarda por SLUG estable, no por id numérico.
// Aunque se renumeren o agreguen módulos, lo ya completado nunca se "descompleta".
function getAcademiaProgresoSlugs() {
  if (!aliado) return [];
  const keySlugs = `academia_progress_slugs_${aliado.codigo}`;
  let slugs = [];
  try { slugs = JSON.parse(localStorage.getItem(keySlugs) || '[]'); } catch { slugs = []; }
  // Migración única desde el progreso viejo (basado en id) si todavía no migramos.
  if (localStorage.getItem(keySlugs) === null) {
    const keyOld = `academia_progress_${aliado.codigo}`;
    let ids = [];
    try { ids = JSON.parse(localStorage.getItem(keyOld) || '[]'); } catch { ids = []; }
    if (ids.length) {
      // El id 8 legacy en canal2 era el módulo de materiales (hoy id 9).
      const esCanal2 = aliado && aliado.tipo_aliado === 'canal2';
      const migrados = ids
        .map(id => (esCanal2 && id === 8) ? 9 : id)
        .map(id => _slugDeModulo(id))
        .filter(Boolean);
      slugs = [...new Set([...slugs, ...migrados])];
      localStorage.setItem(keySlugs, JSON.stringify(slugs));
    }
  }
  return slugs;
}

// Compat: el resto del código razona por id. Convertimos slugs -> ids actuales.
function getAcademiaProgreso() {
  return getAcademiaProgresoSlugs().map(s => _idDeSlug(s)).filter(v => v != null);
}

// Carga el progreso desde el backend y sincroniza al localStorage.
// Llamada al inicializar la academia para que el progreso sobreviva
// cambios de dispositivo o limpieza de caché.
async function sincronizarProgresoDesdeBackend() {
  if (!aliado) return;
  try {
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/academia`);
    if (!res.ok) return;
    const data = await res.json();
    const completadosIds = (data.modulos || [])
      .filter(m => m.completado)
      .map(m => m.id);
    if (completadosIds.length > 0) {
      const keySlugs = `academia_progress_slugs_${aliado.codigo}`;
      // Merge por slug estable: mapeamos los ids del backend a slugs actuales.
      const local = getAcademiaProgresoSlugs();
      const desdeBackend = completadosIds.map(id => _slugDeModulo(id)).filter(Boolean);
      const merged = [...new Set([...desdeBackend, ...local])];
      localStorage.setItem(keySlugs, JSON.stringify(merged));
    }
    renderAcademiaIndex();
  } catch (e) {
    // Fallo silencioso: seguimos con lo que hay en localStorage
  }
}

async function marcarModuloCompleto(id) {
  if (!aliado) return;
  const slug = _slugDeModulo(id);
  const keySlugs = `academia_progress_slugs_${aliado.codigo}`;
  const slugs = getAcademiaProgresoSlugs();
  if (slug && !slugs.includes(slug)) {
    // Optimistic update: guardamos por slug estable
    slugs.push(slug);
    localStorage.setItem(keySlugs, JSON.stringify(slugs));
    actualizarBadgeAcademia();
  }
  // Persistir en backend (idempotente: si ya estaba, no duplica créditos)
  try {
    const res = await apiFetch(
      `${API}/aliados/${aliado.codigo}/academia/${id}/completar`,
      { method: 'POST' }
    );
    if (res.ok) {
      const data = await res.json();
      if (!data.ya_completado && data.creditos_ganados > 0) {
        mostrarToast(`✓ Módulo completado · +${data.creditos_ganados} créditos`, 'green');
        // Refrescar saldo de créditos en el UI si existe el elemento
        const saldoEl = document.getElementById('aliado-creditos');
        if (saldoEl) saldoEl.textContent = data.saldo;
      }
    } else {
      mostrarToast('✓ Módulo completado', 'green');
    }
  } catch (e) {
    mostrarToast('✓ Módulo guardado localmente', 'green');
  }
}

function abrirModulo(id) {
  const esCanal2 = aliado?.tipo_aliado === 'canal2';
  // Verificar que el módulo sea visible para este canal
  const modCheck = ACADEMIA_MODULOS.find(x => x.id === id);
  if (!modCheck) return;
  if (modCheck.canal === 'canal1' && esCanal2) return;
  if (modCheck.canal === 'canal2' && !esCanal2) return;

  const m = ACADEMIA_MODULOS.find(x => x.id === id);
  if (!m) return;

  document.getElementById('aca-index').style.display = 'none';
  const progreso = getAcademiaProgreso();
  const completo = progreso.includes(id);

  const modulosVisibles = ACADEMIA_MODULOS.filter(m => {
    if (m.canal === 'canal1' && esCanal2) return false;
    if (m.canal === 'canal2' && !esCanal2) return false;
    return true;
  });
  const idx = modulosVisibles.findIndex(x => x.id === id);
  const prevModulo = idx > 0 ? modulosVisibles[idx - 1] : null;
  const nextModulo = idx < modulosVisibles.length - 1 ? modulosVisibles[idx + 1] : null;
  
  const html = `
    <div class="aca-lesson active">
      <button class="back-btn" onclick="volverAIndice()"><i class="fa-solid fa-arrow-left"></i> Volver al índice</button>
      <div class="lesson-head">
        <div class="lesson-num">${m.num} · ${m.tiempo} de lectura</div>
        <h2>${m.title}</h2>
        <div class="lesson-sub">${m.desc}</div>
      </div>
      ${m.content}
      <div class="aca-nav">
        <button onclick="${prevModulo ? `abrirModulo(${prevModulo.id})` : ''}" ${!prevModulo ? 'disabled' : ''}>
          <i class="fa-solid fa-arrow-left"></i> Módulo anterior
        </button>
        <button class="complete-btn ${completo ? 'done' : ''}" onclick="completarYAvanzar(${id})">
          ${completo ? '<i class="fa-solid fa-check"></i> Completado' : '<i class="fa-solid fa-check"></i> Marcar como completado'}
        </button>
        <button onclick="${nextModulo ? `abrirModulo(${nextModulo.id})` : 'volverAIndice()'}">
          ${nextModulo ? 'Siguiente módulo' : 'Volver al índice'} <i class="fa-solid fa-arrow-right"></i>
        </button>
      </div>
    </div>
  `;
  
  document.getElementById('aca-lessons-container').innerHTML = html;
  if (window.initAvExplainers) initAvExplainers(document.getElementById('aca-lessons-container'));
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function volverAIndice() {
  renderAcademiaIndex();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function mostrarCelebracionAcademia() {
  document.getElementById('aca-index').style.display = 'none';
  const container = document.getElementById('aca-lessons-container');
  container.innerHTML = `
    <div style="
      display:flex;flex-direction:column;align-items:center;justify-content:center;
      gap:28px;padding:60px 24px;text-align:center;animation:fadeIn .5s ease;
    ">
      <div style="font-size:4rem;line-height:1;">&#127942;</div>
      <div>
        <h2 style="font-size:1.6rem;font-weight:800;color:var(--accent);margin:0 0 8px;">
          &#161;Completaste la formación!
        </h2>
        <p style="color:var(--text-secondary);max-width:380px;margin:0 auto;line-height:1.5;">
          Recorriste los 8 módulos de la Academia Avanza. Ya tenés todo lo que necesitás para cerrar tu primera venta.
        </p>
      </div>
      <div style="display:flex;flex-direction:column;gap:12px;width:100%;max-width:340px;">
        <button
          onclick="irABolsaDesdeAcademia()"
          style="
            padding:14px 24px;border-radius:10px;border:none;cursor:pointer;
            background:linear-gradient(135deg,#f97316,#fb923c);
            color:#000;font-weight:800;font-size:1rem;
            box-shadow:0 4px 20px rgba(249,115,22,.35);
            display:flex;align-items:center;justify-content:center;gap:8px;
          ">
          <i class="fa-solid fa-bolt"></i>
          &#161;Ir a reclamar mi primer lead!
        </button>
        <button
          onclick="volverAIndice()"
          style="
            padding:12px 24px;border-radius:10px;border:1px solid var(--border);
            background:transparent;color:var(--text-secondary);cursor:pointer;font-size:.9rem;
          ">
          Ver resumen de la Academia
        </button>
      </div>
    </div>
  `;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function irABolsaDesdeAcademia() {
  // Navegar a la tab de Bolsa (leads gratuitos)
  const btnBolsa = document.querySelector('.tab-btn[onclick*="bolsa"]') ||
                   document.querySelector('[onclick*="bolsa"]');
  if (btnBolsa) {
    btnBolsa.click();
  } else {
    // Fallback: buscar por texto
    const tabs = document.querySelectorAll('.tab-btn');
    const bolsaTab = Array.from(tabs).find(t => t.textContent.toLowerCase().includes('bolsa'));
    if (bolsaTab) bolsaTab.click();
  }
}

async function completarYAvanzar(id) {
  await marcarModuloCompleto(id);

  const esCanal2 = aliado?.tipo_aliado === 'canal2';
  const modulosVisibles = ACADEMIA_MODULOS.filter(m => {
    if (m.canal === 'canal1' && esCanal2) return false;
    if (m.canal === 'canal2' && !esCanal2) return false;
    return true;
  });

  const idx = modulosVisibles.findIndex(x => x.id === id);
  const esUltimo = idx === modulosVisibles.length - 1;

  if (esUltimo) {
    // Verificar que todos los módulos estén realmente completados
    const progreso = getAcademiaProgreso();
    const todosCompletos = modulosVisibles.every(m => progreso.includes(m.id));
    if (todosCompletos) {
      setTimeout(() => mostrarCelebracionAcademia(), 600);
      return;
    }
  }

  const nextModulo = idx >= 0 && idx < modulosVisibles.length - 1
    ? modulosVisibles[idx + 1]
    : null;

  if (nextModulo) {
    setTimeout(() => abrirModulo(nextModulo.id), 600);
  } else {
    setTimeout(() => volverAIndice(), 600);
  }
}


// ── INVITACIÓN / RECLUTAMIENTO (Hueco 2): enriquece la solapa Red ────────────
// Consume GET /aliados/{codigo}/invitacion para sumar el bono de activación,
// el desglose activados/invitados, los créditos ganados y el botón de compartir.
let _redMensajeCompartir = '';
let _redInviteLink = '';
async function cargarInvitacionRed() {
  if (!aliado) return;
  try {
    const r = await apiFetch(`${API}/aliados/${aliado.codigo}/invitacion`);
    if (!r.ok) return;
    const d = await r.json();
    _redMensajeCompartir = d.mensaje_para_compartir || '';
    _redInviteLink = d.invite_link || '';
    // Usar el link autoritativo del backend (respeta PORTAL_URL).
    const linkEl = document.getElementById('red-link-reclutamiento');
    if (linkEl && _redInviteLink) linkEl.textContent = _redInviteLink;
    const bono = document.getElementById('red-bono-activacion');
    if (bono && d.bono_por_activacion != null) bono.textContent = d.bono_por_activacion;
    const st = d.stats || {};
    const act = document.getElementById('red-activados');
    if (act) act.textContent = `${st.activados || 0} / ${st.invitados || 0}`;
    const cred = document.getElementById('red-creditos-ref');
    if (cred) cred.textContent = (st.creditos_por_referidos || 0).toLocaleString();
  } catch (e) { /* best-effort: la solapa Red sigue andando igual */ }
}

function compartirRed() {
  // Mensaje: primero el del backend; si /invitacion no cargó (ej. backend aún
  // sin deployar), lo armamos desde el ref_code, que SIEMPRE está en el cliente.
  let msg = _redMensajeCompartir;
  if (!msg) {
    const link = _redInviteLink
      || (aliado && aliado.ref_code ? ('https://avanzadigital.digital/alianzas?ref=' + aliado.ref_code) : '');
    if (link) {
      msg = 'Te comparto el programa de aliados de Avanza Digital, para closers/setters '
          + 'que quieran cerrar sistemas comerciales para PyMEs industriales, con leads ya '
          + 'cargados y comisión por venta. Si te interesa, registrate con mi link: ' + link;
    }
  }
  if (!msg) {
    if (typeof mostrarToast === 'function') mostrarToast('Esperá a que cargue tu link…', 'amber');
    return;
  }

  // Móvil / PWA: hoja de compartir nativa (deja elegir WhatsApp u otra app).
  // Se llama de forma síncrona desde el click para no perder el gesto del usuario.
  if (navigator.share) {
    navigator.share({ text: msg }).catch(() => {});
    return;
  }

  // Desktop / sin Web Share API: abrir WhatsApp con el texto pre-cargado.
  // OJO: usamos api.whatsapp.com/send?text= (no wa.me/?text=, que sin número
  // no resuelve y deja la pestaña en blanco).
  window.open('https://api.whatsapp.com/send?text=' + encodeURIComponent(msg), '_blank');
}


// ============================================================
//  Aviso de navegador in-app (LinkedIn, Instagram, Facebook, TikTok,
//  WebView genérico de Android)
//
//  Problema que resuelve: dentro de estos navegadores embebidos las
//  descargas de PDF y los links hacia otras apps (wa.me, etc.) fallan
//  SIN mostrar ningún error — el aliado hace click y no pasa nada.
//  Este bloque detecta esos casos por user-agent y muestra un banner
//  fijo arriba de la página avisando y ofreciendo copiar el link para
//  pegarlo en Safari/Chrome.
//
//  Es detección por user-agent (heurística), no 100% infalible — las
//  apps cambian sus firmas de UA de tanto en tanto. Si en el futuro
//  algún caso similar no se detecta, agregar su firma acá abajo.
// ============================================================
(function () {
  const ua = navigator.userAgent || '';

  const FIRMAS_WEBVIEW = [
    { nombre: 'LinkedIn',  test: /LinkedInApp/i },
    { nombre: 'Instagram', test: /Instagram/i },
    { nombre: 'Facebook',  test: /FBAN|FBAV|FB_IAB|MessengerLite/i },
    { nombre: 'TikTok',    test: /BytedanceWebview|musical_ly|TikTok/i },
    // WebView genérico de Android: Chrome mobile normal NO tiene "wv)"
    // en el user-agent, los WebView embebidos sí.
    { nombre: 'esta app',  test: /Android.*\swv\)/i },
  ];

  const match = FIRMAS_WEBVIEW.find(f => f.test.test(ua));
  if (!match) return; // navegador normal → no hacer nada

  const AVZ_WEBVIEW_KEY = 'avz_webview_banner_oculto';
  if (sessionStorage.getItem(AVZ_WEBVIEW_KEY)) return; // ya lo cerró en esta sesión

  function avzCopiarLinkWebview() {
    const url = location.href;
    const avisar = () => {
      if (typeof mostrarToast === 'function') {
        mostrarToast('Link copiado — pegalo en Safari o Chrome ✓', 'green');
      } else {
        alert('Link copiado. Pegalo en Safari o Chrome.');
      }
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(avisar).catch(() => avzPromptFallbackWebview(url));
    } else {
      avzPromptFallbackWebview(url);
    }
  }

  function avzPromptFallbackWebview(url) {
    window.prompt('Copiá este link y abrilo en Safari o Chrome:', url);
  }

  function avzCerrarBannerWebview() {
    const b = document.getElementById('avz-webview-banner');
    if (b) b.remove();
    document.body.style.paddingTop = '';
    sessionStorage.setItem(AVZ_WEBVIEW_KEY, '1');
  }

  function avzRenderBannerWebview() {
    const b = document.createElement('div');
    b.id = 'avz-webview-banner';
    b.style.cssText = `
      position:fixed; top:0; left:0; right:0; z-index:100000;
      background:#1a1400; border-bottom:1px solid rgba(250,204,21,0.35);
      color:#facc15; padding:10px 14px; font-size:.8rem; line-height:1.4;
      display:flex; align-items:center; gap:10px; flex-wrap:wrap;
      font-family:inherit;
    `;
    b.innerHTML = `
      <i class="fa-solid fa-triangle-exclamation" style="flex-shrink:0;"></i>
      <span style="flex:1;min-width:220px;">
        Estás viendo esto desde <strong>${match.nombre}</strong> — las descargas de PDF y los links a WhatsApp no funcionan bien acá adentro.
        Copiá el link y abrilo en tu navegador (Safari/Chrome).
      </span>
      <button id="avz-webview-copiar" style="flex-shrink:0;background:#facc15;color:#000;border:none;border-radius:6px;padding:6px 12px;font-weight:700;font-size:.78rem;cursor:pointer;">Copiar link</button>
      <button id="avz-webview-cerrar" aria-label="Cerrar aviso" style="flex-shrink:0;background:transparent;border:none;color:#facc15;font-size:1rem;cursor:pointer;padding:0 4px;">✕</button>
    `;
    document.body.prepend(b);
    document.getElementById('avz-webview-copiar').onclick = avzCopiarLinkWebview;
    document.getElementById('avz-webview-cerrar').onclick = avzCerrarBannerWebview;

    // Empujar el contenido para que el banner no tape el header del portal
    requestAnimationFrame(() => {
      document.body.style.paddingTop = b.offsetHeight + 'px';
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', avzRenderBannerWebview);
  } else {
    avzRenderBannerWebview();
  }
})();