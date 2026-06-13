

// ═══════════════════════════════════════════════════════════
// JARVIS v2 — Centro de Comando
// ═══════════════════════════════════════════════════════════

const J = {
  historial: [],
  esperando: false,
  estadoEmocional: 'neutro',
  briefingTimer: null,
  briefingCount: 30,
  recognition: null,
};

// ── Init (se llama cuando el tab JARVIS se abre) ──────────
function inicializarJarvisV2() {
  jCargarAliado();
  jMostrarBienvenida();
  jVerificarIntegraciones();
  jVerificarEstado();
  jChequearAcceso();
  const hoy = new Date().toDateString();
  if (localStorage.getItem('j_briefing_fecha') !== hoy) {
    setTimeout(jAbrirBriefing, 500);
  }
}

// ── Paywall: acceso por créditos (pagar para usar) ────────
function jMostrarPaywall(msg) {
  const pw = document.getElementById('j-paywall');
  if (!pw) return;
  if (msg) { const m = document.getElementById('j-paywall-msg'); if (m) m.textContent = msg; }
  pw.classList.add('show');
}
function jOcultarPaywall() {
  const pw = document.getElementById('j-paywall');
  if (pw) pw.classList.remove('show');
}
// Acceso: 7 días gratis para todos; después, lo habilita tener créditos.
async function jChequearAcceso() {
  try {
    if (!aliado || !aliado.codigo) return;
    const res = await apiFetch(`${API}/aliados/${aliado.codigo}/creditos`);
    if (res.ok) {
      const data = await res.json();
      const saldo = data.saldo ?? 0;
      J.saldo = saldo;
      J.enTrial = data.jarvis_trial_activo === true;
      J.diasTrial = data.jarvis_trial_dias_restantes ?? 0;

      if (J.enTrial) {
        // Prueba gratis activa: JARVIS es gratis, no se muestra el paywall y
        // no se tocan los créditos (quedan para la bolsa de leads).
        jOcultarPaywall();
        const d = J.diasTrial;
        const txt = d === 1 ? 'Último día de prueba gratis de JARVIS'
                            : `Prueba gratis de JARVIS: te quedan ${d} días`;
        if (typeof mostrarToast === 'function') mostrarToast(txt, 'blue');
      } else if (saldo < 1) {
        jMostrarPaywall('Tu prueba gratis terminó. JARVIS funciona con créditos y tu saldo es 0. Cargá créditos para seguir usando tu asistente comercial.');
      } else {
        jOcultarPaywall();
      }
    }
  } catch {}
}

// ── Datos del aliado ──────────────────────────────────────
async function jCargarAliado() {
  try {
    const r = await fetch('/aliados/me', { credentials: 'include' });
    if (r.ok) {
      const d = await r.json();
      const nombre = d.nombre || 'Aliado';
      const score  = d.jarvis_score || 87;
      document.getElementById('j-brief-name').textContent = nombre.split(' ')[0].toUpperCase();
      document.getElementById('j-score-top').textContent  = score;
      document.getElementById('jbm-score').textContent    = score;
    }
  } catch {}
  const fechaOpts = { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' };
  document.getElementById('j-brief-date').textContent =
    new Date().toLocaleDateString('es-AR', fechaOpts).toUpperCase();
}

async function jVerificarEstado() {
  try {
    const r = await apiFetch('/jarvis/estado');
    if (r.ok) {
      const d = await r.json();
      const activo = d.activo !== false;
      document.getElementById('j-status-dot').style.background = activo ? 'var(--j-green)' : 'var(--j-red)';
      document.getElementById('j-status-txt').textContent = activo
        ? 'JARVIS operativo · Claude Sonnet' : 'JARVIS no configurado — contactá soporte';
    }
  } catch {}
}

// ── Bienvenida ────────────────────────────────────────────
function jMostrarBienvenida() {
  const hora = new Date().getHours();
  const saludo = hora < 12 ? 'Buenos días' : hora < 18 ? 'Buenas tardes' : 'Buenas noches';
  const texto = `**${saludo}.** Soy JARVIS, tu sistema de inteligencia comercial.\n\nTengo **MetalPro SRL** activo como lead principal — score 82/100. ¿Arrancamos con el seguimiento de hoy?`;
  jAgregarBot(texto, 'propio');
}

// ── Iron Man Protocol ─────────────────────────────────────
function jAbrirBriefing() {
  document.getElementById('j-briefing').classList.add('active');
  J.briefingCount = 30;
  document.getElementById('j-bp-timer').textContent = 30;
  const fill = document.getElementById('j-bp-fill');
  fill.style.transition = 'none'; fill.style.width = '0%';
  setTimeout(() => {
    fill.style.transition = 'width 30s linear';
    fill.style.width = '100%';
  }, 50);
  J.briefingTimer = setInterval(() => {
    J.briefingCount--;
    const el = document.getElementById('j-bp-timer');
    if (el) el.textContent = J.briefingCount;
    if (J.briefingCount <= 0) jCerrarBriefing();
  }, 1000);
}

function jCerrarBriefing() {
  if (J.briefingTimer) clearInterval(J.briefingTimer);
  document.getElementById('j-briefing').classList.remove('active');
  localStorage.setItem('j_briefing_fecha', new Date().toDateString());
}

function jLeerBriefing() {
  if (!window.speechSynthesis) return jToast('TTS no disponible en este browser', 'err');
  const nombre = document.getElementById('j-brief-name').textContent;
  const p1 = document.getElementById('jbp-1').textContent;
  const p2 = document.getElementById('jbp-2').textContent;
  const u = new SpeechSynthesisUtterance(
    `Buenos días ${nombre}. Soy JARVIS. Tus prioridades de hoy: ${p1}. Segunda: ${p2}. Revisá las alertas. Buen día.`
  );
  u.lang = 'es-AR'; u.rate = 0.93;
  speechSynthesis.speak(u);
  const btn = document.getElementById('j-btn-listen');
  btn.innerHTML = '<i class="fa-solid fa-stop"></i> Detener';
  btn.onclick = () => { speechSynthesis.cancel(); btn.innerHTML = '<i class="fa-solid fa-volume-high"></i> Escuchar'; btn.onclick = jLeerBriefing; };
}

// ── Chat ──────────────────────────────────────────────────
async function jEnviar() {
  const input = document.getElementById('j-input');
  const texto = input.value.trim();
  if (!texto || J.esperando) return;
  input.value = ''; jResize(input);
  jAgregarUser(texto);
  J.historial.push({ role: 'user', content: texto });
  J.esperando = true;
  document.getElementById('j-send').disabled = true;
  jDetectarEstado(texto);
  const typingId = jMostrarTyping();
  try {
    const r = await apiFetch('/jarvis/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        mensaje: texto,
        historial: J.historial.slice(-8),
        estado_emocional: J.estadoEmocional,
      }),
    });
    jQuitarTyping(typingId);
    if (r.ok) {
      const d = await r.json();
      const resp = d.respuesta || d.response || '';
      J.historial.push({ role: 'assistant', content: resp });
      jAgregarBot(resp, d.contexto || 'general', d.tiempo_ms);
    } else if (r.status === 402) {
      let msg = 'Te quedaste sin créditos. Cargá créditos para seguir usando JARVIS.';
      try { const e = await r.json(); if (e && e.detail) msg = e.detail; } catch {}
      jMostrarPaywall(msg);
    } else {
      jAgregarBot('Hubo un error al conectar con JARVIS. Revisá tu conexión.', 'general');
    }
  } catch {
    jQuitarTyping(typingId);
    jAgregarBot('Error de conexión. Verificá que el servidor esté activo.', 'general');
  }
  J.esperando = false;
  document.getElementById('j-send').disabled = false;
}

function jAgregarUser(texto) {
  const c = document.getElementById('j-chat');
  const d = document.createElement('div');
  d.className = 'j-msg user';
  d.innerHTML = `<div class="j-avatar"><i class="fa-solid fa-user"></i></div>
    <div class="j-msg-body"><div class="j-bubble">${jMd(texto)}</div></div>`;
  c.appendChild(d); c.scrollTop = c.scrollHeight;
}

function jAgregarBot(texto, ctx = 'general', ms = null) {
  const c = document.getElementById('j-chat');
  const ctxMap = {
    propio:  ['j-ctx-propio',  '🟢 contexto propio'],
    sector:  ['j-ctx-sector',  '🟡 sectorial'],
    red:     ['j-ctx-red',     '🔵 red'],
    general: ['j-ctx-general', '🔴 estimación'],
  };
  const [cls, lbl] = ctxMap[ctx] || ctxMap.general;
  const msStr = ms ? `<div class="j-ctx-tag" style="background:var(--j-bg3);border:1px solid var(--j-border);color:rgba(100,130,180,0.5)">⚡ ${(ms/1000).toFixed(1)}s</div>` : '';
  const acciones = jDetectarAcciones(texto);
  const acHTML = acciones.length ? `<div class="j-msg-actions">${acciones.map(a=>`<button class="j-act-btn" onclick="${a.fn}"><i class="${a.icon}"></i> ${a.label}</button>`).join('')}</div>` : '';
  const hora = new Date().toLocaleTimeString('es-AR',{hour:'2-digit',minute:'2-digit'});
  const d = document.createElement('div');
  d.className = 'j-msg bot';
  d.innerHTML = `<div class="j-avatar"><i class="fa-solid fa-bolt"></i></div>
    <div class="j-msg-body">
      <div class="j-bubble">${jMd(texto)}</div>
      ${acHTML}
      <div class="j-ctx">
        <div class="j-ctx-tag ${cls}">${lbl}</div>
        ${msStr}
        <div class="j-ctx-time">${hora}</div>
      </div>
    </div>`;
  c.appendChild(d); c.scrollTop = c.scrollHeight;
}

function jMostrarTyping() {
  const c = document.getElementById('j-chat');
  const id = 'jtyp-' + Date.now();
  const d = document.createElement('div');
  d.className = 'j-msg bot'; d.id = id;
  d.innerHTML = `<div class="j-avatar"><i class="fa-solid fa-bolt"></i></div>
    <div class="j-msg-body">
      <div class="j-typing">
        <div class="j-dots"><span></span><span></span><span></span></div>
        <div class="j-typing-txt">JARVIS procesando...</div>
      </div>
    </div>`;
  c.appendChild(d); c.scrollTop = c.scrollHeight;
  return id;
}

function jQuitarTyping(id) { const el = document.getElementById(id); if (el) el.remove(); }

// ── Acciones embebidas ────────────────────────────────────
function jDetectarAcciones(texto) {
  const t = texto.toLowerCase(); const acc = [];
  if (t.includes('email') || t.includes('mensaje'))  {
    acc.push({ icon:'fa-brands fa-google', label:'Abrir en Gmail',   fn:'jAccionCRM("gmail")' });
    acc.push({ icon:'fa-brands fa-whatsapp', label:'Para WhatsApp', fn:'jCopiarMensaje(this)' });
  }
  if (t.includes('propuesta') || t.includes('pdf'))
    acc.push({ icon:'fa-regular fa-file-pdf', label:'Generar PDF', fn:'jToast("PDF generado","ok")' });
  if (t.includes('lead') || t.includes('score'))
    acc.push({ icon:'fa-solid fa-cloud-arrow-up', label:'Guardar en CRM', fn:'jAccionCRM("hubspot")' });
  if (t.includes('reuni') || t.includes('mañana'))
    acc.push({ icon:'fa-regular fa-calendar-plus', label:'Agendar', fn:'jAccionCRM("calendar")' });
  acc.push({ icon:'fa-regular fa-copy', label:'Copiar', fn:'jCopiarMensaje(this)' });
  return acc.slice(0, 4);
}

function jCopiarMensaje(btn) {
  const b = btn?.closest?.('.j-msg-body')?.querySelector?.('.j-bubble');
  if (!b) return;
  navigator.clipboard.writeText(b.innerText).then(() => jToast('Texto copiado', 'ok'));
}

// ── Estado emocional ──────────────────────────────────────
const J_ESTADOS = {
  frustracion:    { p:[/no s[eé] c[oó]mo/i,/bloqueado/i,/qué hago/i],    emoji:'😤', tipo:'Frustración',    desc:'Modo coaching. Paso a paso.' },
  energia_alta:   { p:[/cerr[eé]/i,/firmé/i,/lo logré/i,/genial/i],       emoji:'🔥', tipo:'Energía alta',   desc:'Momentum. Acción audaz.' },
  decepcion:      { p:[/perdí/i,/rechazaron/i,/eligieron a otro/i],        emoji:'😔', tipo:'Decepción',      desc:'Validar primero, aprender después.' },
  urgencia:       { p:[/urgente/i,/ya$/i,/ahora/i,/rápido/i],              emoji:'⚡', tipo:'Urgencia',       desc:'Respuesta ultra-corta.' },
  coaching_needed:{ p:[/cómo le digo/i,/qué harías/i,/cómo arrancar/i],   emoji:'🎓', tipo:'Modo coaching',  desc:'JARVIS explica el razonamiento.' },
};

function jDetectarEstado(texto) {
  for (const [nombre, data] of Object.entries(J_ESTADOS)) {
    if (data.p.some(p => p.test(texto))) {
      J.estadoEmocional = nombre;
      document.getElementById('j-estado-emoji').textContent = data.emoji;
      document.getElementById('j-estado-tipo').textContent  = data.tipo;
      document.getElementById('j-estado-desc').textContent  = data.desc;
      return;
    }
  }
  J.estadoEmocional = 'neutro';
  document.getElementById('j-estado-emoji').textContent = '😐';
  document.getElementById('j-estado-tipo').textContent  = 'Neutro';
  document.getElementById('j-estado-desc').textContent  = 'Sin señal emocional. Modo estándar.';
}

// ── Navegación ────────────────────────────────────────────
const J_SECCION_MSGS = {
  leads:        'Mostrá los leads activos con sus scores',
  pipeline:     'Dame un resumen de mi pipeline comercial',
  propuestas:   'Listá mis propuestas abiertas y su estado',
  comunicador:  'Quiero redactar una comunicación. ¿Por dónde arrancamos?',
  mercado:      'Dame el radar de mercado de esta semana para mi sector',
  reuniones:    'Qué reuniones tengo próximas y cómo prepararme',
  alertas:      'Mostrá todas mis alertas activas con prioridad',
  academia:     'Qué módulo de capacitación me recomendás hoy',
  integraciones:'Mostrá el estado de mis integraciones y cómo configurar las que faltan',
};

function jSetSection(s, el) {
  document.querySelectorAll('.j-nav').forEach(n => n.classList.remove('active'));
  if (el) el.classList.add('active');
  if (s !== 'chat' && J_SECCION_MSGS[s]) jDispararAccion(J_SECCION_MSGS[s]);
}

function jDispararAccion(texto) {
  document.getElementById('j-input').value = texto;
  jEnviar();
}

// ── CRM ───────────────────────────────────────────────────
async function jAccionCRM(tipo) {
  const msgs = {
    hubspot:  ['✅ Contacto guardado en HubSpot',          'HubSpot no configurado. Activalo en Integraciones.'],
    pipedrive:['✅ Deal sincronizado en Pipedrive',         'Pipedrive no configurado.'],
    gmail:    ['✅ Borrador creado en Gmail',               'Gmail no configurado. Conectá tu cuenta.'],
    calendar: ['✅ Recordatorio creado en Calendar',        'Google Calendar no configurado.'],
    slack:    ['✅ Alerta enviada a Slack',                  'Slack no configurado.'],
  };
  const dot = document.getElementById(`jd-${tipo}`);
  if (dot?.classList.contains('on')) {
    jToast(msgs[tipo]?.[0] || '✅ Acción completada', 'ok');
  } else {
    jToast(msgs[tipo]?.[1] || 'Integración no configurada. Activá en el panel.', 'err');
  }
}

// ── Integraciones ─────────────────────────────────────────
async function jVerificarIntegraciones() {
  try {
    const r = await apiFetch('/jarvis/integraciones/estado');
    if (!r.ok) return;
    const d = await r.json();
    for (const [nombre, estado] of Object.entries(d.integraciones || {})) {
      const dot = document.getElementById(`jd-${nombre.replace('_','-')}`);
      if (dot) dot.className = 'j-dot ' + (estado.configurado ? 'on' : 'off');
    }
  } catch { /* LinkedIn siempre on */ }
}

// ── Voz ───────────────────────────────────────────────────
function jIniciarVoz() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return jToast('Voz no disponible en este browser', 'err');
  if (J.recognition) { J.recognition.stop(); J.recognition = null; document.getElementById('j-voice-btn').style.color = ''; return; }
  J.recognition = new SR();
  J.recognition.lang = 'es-AR'; J.recognition.continuous = false;
  document.getElementById('j-voice-btn').style.color = 'var(--j-red)';
  J.recognition.onresult = (e) => {
    document.getElementById('j-input').value = e.results[0][0].transcript;
    jEnviar();
  };
  J.recognition.onend = () => { J.recognition = null; document.getElementById('j-voice-btn').style.color = ''; };
  J.recognition.start();
}

// ── Utilidades ────────────────────────────────────────────
function jHandleKey(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); jEnviar(); } }
function jResize(el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 110) + 'px'; }

function jToast(msg, tipo = 'ok') {
  const c = document.getElementById('j-toast-wrap');
  const t = document.createElement('div');
  t.className = `j-toast ${tipo}`;
  t.innerHTML = `<i class="fa-solid ${tipo === 'ok' ? 'fa-check-circle' : 'fa-circle-xmark'}"></i>${msg}`;
  c.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

function jMd(text) {
  if (!text) return '';
  return text
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>')
    .replace(/\*(.*?)\*/g,'<em>$1</em>')
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/^### (.*)/gm,'<strong style="font-size:11px;color:var(--j-cyan);letter-spacing:1px">$1</strong>')
    .replace(/^- (.*)/gm,'• $1')
    .replace(/^(\d+)\. (.*)/gm,'<span style="color:var(--j-cyan)">$1.</span> $2')
    .replace(/---/g,'<hr>')
    .replace(/\n\n/g,'</p><p>')
    .replace(/\n/g,'<br>');
}

