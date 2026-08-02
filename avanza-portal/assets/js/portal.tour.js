/* ─────────────────────────────────────────────────────────────────────────────
 * portal.tour.js — Tour guiado de bienvenida (coach-marks / spotlight)
 *
 * QUÉ HACE:
 *   Resalta 6 botones clave del sidebar con un globo explicativo ("esto sirve
 *   para tal cosa"). Se dispara solo en el primer ingreso (encadenado al cierre
 *   del modal de bienvenida) y se puede volver a ver desde "¿Cómo funciona?".
 *
 *   Recortado deliberadamente a lo esencial (antes: 17 pasos, todo el sidebar).
 *   El resto de las secciones (Capturas, CRM, Kit de Ventas, Red, Equipo,
 *   Comunidad, JARVIS...) las va descubriendo el aliado solo, y el checklist
 *   de progreso del Dashboard le va marcando el próximo paso a medida que
 *   avanza — no hace falta explicarlo todo de una sola vez acá.
 *
 * SIN DEPENDENCIAS: vanilla JS + CSS inyectado. Usa las variables de tema del
 *   portal (--primary, --orange, --green, …) con fallbacks.
 *
 * RESPETA EL CANAL: cada paso apunta a un botón real; si ese botón está oculto
 *   (Canal 1 vs Canal 2 → display:none), el paso se saltea automáticamente.
 *
 * API GLOBAL:
 *   AvanzaTour.start()       → fuerza el tour desde el principio (botón de ayuda)
 *   AvanzaTour.maybeStart()  → lo lanza solo si el aliado no lo vio todavía
 * ───────────────────────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  // ── Pasos del tour ──────────────────────────────────────────────────────────
  // sel: selector del elemento a resaltar (null = tarjeta centrada, sin target)
  // Orden deliberado: Academia va primero porque desbloquea la Bolsa de Leads
  // (antes estaba en el paso 10 de 17, casi al final del recorrido).
  const STEPS = [
    { sel: '[data-tab="academia"]', icon: 'fa-graduation-cap', title: 'Academia 🎓',
      text: 'Empezá por acá: 3 módulos cortos (15 min) que desbloquean la Bolsa de Leads y te enseñan a vender.' },

    { sel: '[data-tab="bolsa"]', icon: 'fa-bullhorn', title: 'Bolsa de Leads 🔥',
      text: 'Empresas reales ya cargadas y listas para contactar. Las trabajás, las cerrás y cada venta te deja una comisión que sube con tu nivel. Acá empieza la plata.' },

    { sel: '[data-tab="ventas"]', icon: 'fa-handshake', title: 'Ventas',
      text: 'Registrá tu venta ANTES del pago para que la comisión se te atribuya. Este paso no se saltea.' },

    { sel: '[data-tab="comisiones"]', icon: 'fa-dollar-sign', title: 'Comisiones 💰',
      text: 'Mirá lo que ganaste y lo que está por cobrarse. Apenas entra el pago, te transferimos en 24 hs.' },

    { sel: '#btn-tab-mi-cuenta', icon: 'fa-gear', title: 'Mi Cuenta',
      text: 'Cargá tu método de cobro acá. Sin esto no podemos pagarte tus comisiones, así que no lo dejes para después.' },

    { sel: null, icon: 'fa-rocket', title: '¡Listo, con esto arrancás! 🚀',
      text: 'El resto del portal (Capturas, CRM, Kit de Ventas, Comunidad, JARVIS...) lo vas descubriendo solo. El checklist de tu Dashboard te va marcando el próximo paso.',
      cta: { label: 'Ir a la Academia', tab: 'academia' },
      ctaAlt: { label: 'Ir a la Bolsa de Leads', tab: 'bolsa' } },
  ];

  const MOBILE_BP = 860;   // coincide con el breakpoint del drawer del portal
  let idx = 0;
  let order = [];          // índices de STEPS visibles para este aliado
  let active = false;
  let _tourOpenedDrawer = false;   // ¿el tour abrió el menú en mobile?
  let _drawerAnimandoHasta = 0;    // timestamp hasta el que el drawer está deslizándose
  let _paintT = null;              // timer del pintado diferido

  // ── Helpers ──────────────────────────────────────────────────────────────────
  function codigoAliado() {
    try { return (window.aliado && window.aliado.codigo) || ''; } catch (e) { return ''; }
  }
  function tourKey() { return 'avanza_tour_visto_' + (codigoAliado() || 'anon'); }
  function yaVisto() { try { return !!localStorage.getItem(tourKey()); } catch (e) { return false; } }
  function marcarVisto() { try { localStorage.setItem(tourKey(), '1'); } catch (e) {} }

  function esMobile() { return window.innerWidth <= MOBILE_BP; }

  // Abre el menú lateral en mobile. Marca el momento hasta el que el drawer
  // sigue deslizándose (~0.28s) para no medir posiciones a mitad de la transición.
  function abrirDrawerSiHaceFalta() {
    if (esMobile() && typeof window.avzDrawer === 'function' &&
        !document.documentElement.classList.contains('avz-drawer')) {
      try {
        window.avzDrawer(true);
        _tourOpenedDrawer = true;
        _drawerAnimandoHasta = Date.now() + 360;
        return true;
      } catch (e) {}
    }
    return false;
  }

  function visible(el) {
    return !!(el && el.offsetParent !== null && el.getClientRects().length);
  }

  // ── Inyección de estilos (una sola vez) ──────────────────────────────────────
  function inyectarCSS() {
    if (document.getElementById('avz-tour-css')) return;
    const css = `
    .avz-tour-overlay{position:fixed;inset:0;z-index:100000;pointer-events:auto;}
    .avz-tour-hl{position:fixed;z-index:100001;border-radius:12px;
      box-shadow:0 0 0 4px var(--orange,#f97316), 0 0 0 9999px rgba(2,4,10,.78);
      transition:all .28s cubic-bezier(.4,0,.2,1);pointer-events:none;}
    .avz-tour-hl.pulse{animation:avzTourPulse 1.6s ease-in-out infinite;}
    @keyframes avzTourPulse{
      0%,100%{box-shadow:0 0 0 4px var(--orange,#f97316),0 0 0 9999px rgba(2,4,10,.78);}
      50%{box-shadow:0 0 0 7px rgba(249,115,22,.55),0 0 0 9999px rgba(2,4,10,.78);}}
    .avz-tour-pop{position:fixed;z-index:100002;width:min(330px,calc(100vw - 32px));
      background:#0c0f17;border:1px solid var(--border-h,rgba(255,255,255,.14));
      border-radius:16px;padding:20px 20px 16px;color:var(--text,#f5f5f7);
      box-shadow:0 24px 60px -12px rgba(0,0,0,.7);
      font-family:'Inter',system-ui,sans-serif;opacity:0;transform:translateY(6px) scale(.98);
      transition:opacity .22s ease,transform .22s ease;}
    .avz-tour-pop.show{opacity:1;transform:none;}
    .avz-tour-caret{position:absolute;width:14px;height:14px;background:#0c0f17;
      border-left:1px solid var(--border-h,rgba(255,255,255,.14));
      border-top:1px solid var(--border-h,rgba(255,255,255,.14));transform:rotate(45deg);}
    .avz-tour-ico{width:38px;height:38px;border-radius:10px;display:flex;align-items:center;
      justify-content:center;background:rgba(249,115,22,.14);color:var(--orange,#f97316);
      font-size:1.05rem;margin-bottom:12px;}
    .avz-tour-pop h4{margin:0 0 6px;font-size:1.02rem;font-weight:900;letter-spacing:-.3px;line-height:1.25;}
    .avz-tour-pop p{margin:0;font-size:.86rem;line-height:1.5;color:var(--text-muted,#a1a1aa);}
    .avz-tour-foot{display:flex;align-items:center;gap:10px;margin-top:16px;}
    .avz-tour-prog{font-size:.72rem;font-weight:700;color:var(--text-dim,#71717a);letter-spacing:.5px;}
    .avz-tour-spacer{flex:1;}
    .avz-tour-skip{background:none;border:none;color:var(--text-dim,#71717a);font-size:.76rem;
      font-weight:700;cursor:pointer;padding:6px 4px;font-family:inherit;}
    .avz-tour-skip:hover{color:var(--text,#f5f5f7);}
    .avz-tour-btn{border:1px solid var(--border-h,rgba(255,255,255,.14));background:rgba(255,255,255,.04);
      color:var(--text,#f5f5f7);font-size:.8rem;font-weight:700;cursor:pointer;
      padding:8px 12px;border-radius:9px;font-family:inherit;transition:all .18s;}
    .avz-tour-btn:hover{background:rgba(255,255,255,.09);}
    .avz-tour-btn.primary{background:var(--orange,#f97316);border-color:var(--orange,#f97316);color:#0a0a0a;}
    .avz-tour-btn.primary:hover{filter:brightness(1.08);}
    .avz-tour-cta{display:flex;flex-direction:column;gap:8px;margin-top:16px;}
    .avz-tour-cta .avz-tour-btn{width:100%;text-align:center;padding:11px;}
    .avz-tour-dots{display:flex;gap:5px;margin-top:14px;flex-wrap:wrap;}
    .avz-tour-dots i{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.16);transition:all .2s;}
    .avz-tour-dots i.on{background:var(--orange,#f97316);width:18px;border-radius:3px;}
    @media (max-width:560px){.avz-tour-pop{padding:16px;}}
    `;
    const tag = document.createElement('style');
    tag.id = 'avz-tour-css';
    tag.textContent = css;
    document.head.appendChild(tag);
  }

  // ── Construcción del DOM del tour ─────────────────────────────────────────────
  let elOverlay, elHl, elPop;
  function construir() {
    if (elOverlay) return;
    elOverlay = document.createElement('div');
    elOverlay.className = 'avz-tour-overlay';
    elOverlay.addEventListener('click', function (e) {
      // click fuera del globo = no hace nada (evita cierres accidentales)
      e.stopPropagation();
    });

    elHl = document.createElement('div');
    elHl.className = 'avz-tour-hl pulse';

    elPop = document.createElement('div');
    elPop.className = 'avz-tour-pop';

    document.body.appendChild(elOverlay);
    document.body.appendChild(elHl);
    document.body.appendChild(elPop);
  }

  // ── Posicionamiento del globo respecto al target ─────────────────────────────
  function posicionar(rect) {
    const margin = 14, vw = window.innerWidth, vh = window.innerHeight;
    const pw = elPop.offsetWidth, ph = elPop.offsetHeight;
    let top, left, caretSide;

    // Si el target quedó fuera de la pantalla (ej. drawer aún sin abrir), centramos.
    if (rect && (rect.right < 8 || rect.left > vw - 8 || rect.bottom < 8 || rect.top > vh - 8)) rect = null;

    if (!rect) { // tarjeta centrada
      elHl.style.display = 'none';
      top = (vh - ph) / 2; left = (vw - pw) / 2; caretSide = 'none';
    } else {
      elHl.style.display = 'block';
      elHl.style.top = rect.top + 'px';
      elHl.style.left = rect.left + 'px';
      elHl.style.width = rect.width + 'px';
      elHl.style.height = rect.height + 'px';

      const espacioDer = vw - rect.right;
      if (espacioDer >= pw + margin + 12 && !esMobile()) {
        // a la derecha del item (sidebar)
        left = rect.right + margin;
        top = rect.top + rect.height / 2 - ph / 2;
        caretSide = 'left';
      } else if (rect.bottom + ph + margin <= vh) {
        // debajo
        top = rect.bottom + margin;
        left = rect.left + rect.width / 2 - pw / 2;
        caretSide = 'top';
      } else {
        // arriba
        top = rect.top - ph - margin;
        left = rect.left + rect.width / 2 - pw / 2;
        caretSide = 'bottom';
      }
    }

    // Clamp al viewport
    left = Math.max(margin, Math.min(left, vw - pw - margin));
    top = Math.max(margin, Math.min(top, vh - ph - margin));
    elPop.style.left = left + 'px';
    elPop.style.top = top + 'px';

    // Caret
    const caret = elPop.querySelector('.avz-tour-caret');
    if (caret) {
      caret.style.display = (caretSide === 'none') ? 'none' : 'block';
      if (caretSide === 'left') { caret.style.left = '-8px'; caret.style.top = '50%'; caret.style.marginTop = '-7px'; caret.style.transform = 'rotate(-45deg)'; }
      else if (caretSide === 'top') { caret.style.top = '-8px'; caret.style.left = '50%'; caret.style.marginLeft = '-7px'; caret.style.transform = 'rotate(45deg)'; }
      else if (caretSide === 'bottom') { caret.style.top = '100%'; caret.style.marginTop = '-6px'; caret.style.left = '50%'; caret.style.marginLeft = '-7px'; caret.style.transform = 'rotate(-135deg)'; }
    }
  }

  // ── Render del paso actual ───────────────────────────────────────────────────
  function pintar() {
    const stepIndex = order[idx];
    const step = STEPS[stepIndex];
    let target = step.sel ? document.querySelector(step.sel) : null;

    // Asegurar que el item sea alcanzable (abre el drawer en mobile si hace falta)
    if (step.sel && !visible(target)) {
      abrirDrawerSiHaceFalta();
      target = document.querySelector(step.sel);
      if (!visible(target)) { // sigue oculto (otro canal) → saltar
        return idx < order.length - 1 ? siguiente() : finalizar(true);
      }
    }

    const esUltimo = idx === order.length - 1;
    const dots = order.map((_, i) => `<i class="${i === idx ? 'on' : ''}"></i>`).join('');

    let footHTML;
    if (step.cta) {
      const btns = [];
      btns.push(`<button class="avz-tour-btn primary" data-cta="${step.cta.tab}"><i class="fa-solid ${step.icon}"></i> ${step.cta.label}</button>`);
      if (step.ctaAlt) btns.push(`<button class="avz-tour-btn" data-cta="${step.ctaAlt.tab}">${step.ctaAlt.label}</button>`);
      btns.push(`<button class="avz-tour-btn" data-fin="1">Explorar por mi cuenta</button>`);
      footHTML = `<div class="avz-tour-cta">${btns.join('')}</div>`;
    } else {
      footHTML = `<div class="avz-tour-foot">
        <span class="avz-tour-prog">${idx + 1} / ${order.length}</span>
        <span class="avz-tour-spacer"></span>
        ${idx > 0 ? '<button class="avz-tour-btn" data-prev="1">‹ Atrás</button>' : ''}
        <button class="avz-tour-btn primary" data-next="1">${esUltimo ? 'Listo 🚀' : 'Siguiente ›'}</button>
      </div>`;
    }

    elPop.classList.remove('show');
    elPop.innerHTML = `
      <span class="avz-tour-caret"></span>
      <div class="avz-tour-ico"><i class="fa-solid ${step.icon}"></i></div>
      <h4>${step.title}</h4>
      <p>${step.text}</p>
      ${footHTML}
      ${!step.cta ? `<div class="avz-tour-dots">${dots}</div>` : ''}
      ${!step.cta ? '<button class="avz-tour-skip" data-skip="1" style="position:absolute;top:12px;right:14px;">Saltar tour</button>' : ''}
    `;

    // Listeners de los botones
    elPop.querySelectorAll('[data-next]').forEach(b => b.onclick = siguiente);
    elPop.querySelectorAll('[data-prev]').forEach(b => b.onclick = anterior);
    elPop.querySelectorAll('[data-skip]').forEach(b => b.onclick = () => finalizar(true));
    elPop.querySelectorAll('[data-fin]').forEach(b => b.onclick = () => finalizar(true));
    elPop.querySelectorAll('[data-cta]').forEach(b => b.onclick = function () {
      const tab = this.getAttribute('data-cta');
      finalizar(true);
      try {
        const btn = document.querySelector(`[data-tab="${tab}"]`);
        if (btn && typeof window.cambiarTab === 'function') window.cambiarTab(tab, btn);
      } catch (e) {}
    });

    // El sidebar scrollea: traemos el item al centro antes de medir.
    if (target && target.scrollIntoView) {
      try { target.scrollIntoView({ block: 'center', inline: 'nearest' }); }
      catch (e) { target.scrollIntoView(); }
    }

    // Esperar a que el drawer termine de deslizarse (y el scroll se asiente)
    // antes de medir, si no el globo aparece corrido.
    const animPend = Math.max(0, _drawerAnimandoHasta - Date.now());
    const delay = Math.max(animPend, target ? 60 : 0);

    clearTimeout(_paintT);
    _paintT = setTimeout(() => {
      const t = step.sel ? document.querySelector(step.sel) : null;
      if (step.sel && !visible(t)) {
        return idx < order.length - 1 ? siguiente() : finalizar(true);
      }
      posicionar(t ? t.getBoundingClientRect() : null);
      requestAnimationFrame(() => elPop.classList.add('show'));
    }, delay);
  }

  function siguiente() {
    elPop.classList.remove('show');
    if (idx >= order.length - 1) return finalizar(true);
    idx++; pintar();
  }
  function anterior() {
    if (idx === 0) return;
    elPop.classList.remove('show');
    idx--; pintar();
  }

  // ── Reposición en scroll/resize ──────────────────────────────────────────────
  function onReposicionar() {
    if (!active) return;
    const step = STEPS[order[idx]];
    const target = step.sel ? document.querySelector(step.sel) : null;
    posicionar(target ? target.getBoundingClientRect() : null);
  }
  function onKey(e) {
    if (!active) return;
    if (e.key === 'Escape') finalizar(true);
    else if (e.key === 'ArrowRight') siguiente();
    else if (e.key === 'ArrowLeft') anterior();
  }

  // ── Ciclo de vida ────────────────────────────────────────────────────────────
  function finalizar(marcar) {
    active = false;
    clearTimeout(_paintT);
    window.removeEventListener('resize', onReposicionar);
    window.removeEventListener('scroll', onReposicionar, true);
    window.removeEventListener('keydown', onKey);
    [elOverlay, elHl, elPop].forEach(el => el && el.remove());
    elOverlay = elHl = elPop = null;
    // Si el tour abrió el menú en mobile, lo dejamos como estaba (cerrado)
    if (_tourOpenedDrawer) {
      try { window.avzDrawer && window.avzDrawer(false); } catch (e) {}
      _tourOpenedDrawer = false;
    }
    if (marcar) marcarVisto();
  }

  function start() {
    if (active) return;
    inyectarCSS();
    abrirDrawerSiHaceFalta();
    // Calcular pasos visibles para este aliado (saltea ítems del otro canal)
    order = STEPS.map((s, i) => i).filter(i => {
      const s = STEPS[i];
      if (!s.sel) return true;                 // tarjeta final: siempre
      const el = document.querySelector(s.sel);
      return !!(el && visible(el));            // off-channel = display:none → se excluye solo
    });
    if (!order.length) order = STEPS.map((s, i) => i);
    idx = 0;
    active = true;
    construir();
    window.addEventListener('resize', onReposicionar);
    window.addEventListener('scroll', onReposicionar, true);
    window.addEventListener('keydown', onKey);
    pintar();
  }

  function maybeStart() {
    if (yaVisto()) return;
    // pequeño respiro para que el portal termine de pintar el sidebar
    setTimeout(start, 250);
  }

  window.AvanzaTour = { start, maybeStart, finalizar };
})();