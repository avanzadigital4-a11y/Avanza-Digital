// ============================================================
// HELPER: Función central para disparar eventos a GA4 via GTM
// ============================================================
function trackEvent(eventName, params) {
    params = params || {};
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push(Object.assign({ event: eventName }, params));
}

// ============================================================
// Funciones globales — llamadas desde onclick="" en HTML
// ============================================================

function heroAudit() {
    var input = document.getElementById('hero-domain');
    if (!input) return;
    var domain = input.value.trim();
    if (!domain) { input.focus(); return; }
    var clean = domain.replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/.*$/, '').trim();
    if (!clean || clean.length < 3) { alert('Ingresá un dominio válido.'); return; }
    window.location.href = 'auditoria-digital.html?domain=' + encodeURIComponent(clean);
}

function openModal(modalId) {
    var modal = document.getElementById(modalId);
    if (modal) modal.style.display = 'flex';
}

function openRoiModal(e) {
    if (e) e.preventDefault();
    var modal = document.getElementById('roiModal');
    if (modal) modal.style.display = 'flex';
    trackEvent('open_roi_calculator');
}

function closeRoiModal() {
    var modal = document.getElementById('roiModal');
    var result = document.getElementById('roiResult');
    if (modal) modal.style.display = 'none';
    if (result) result.style.display = 'none';
}

function calculateLoss() {
    var reps  = parseFloat(document.getElementById('salesReps').value)  || 0;
    var hours = parseFloat(document.getElementById('hoursWasted').value) || 0;
    var rate  = parseFloat(document.getElementById('hourlyRate').value)  || 0;
    var annualLoss = reps * hours * rate * 50;

    var formatter = new Intl.NumberFormat('en-US', {
        style: 'currency', currency: 'USD', minimumFractionDigits: 0
    });
    document.getElementById('lossAmount').innerText = formatter.format(annualLoss);
    document.getElementById('roiResult').style.display = 'block';
    trackEvent('qualify_lead', { method: 'calculadora_roi', annual_loss: Math.round(annualLoss) });
}

function toggleNav() {
    var links = document.getElementById('nav-links');
    var btn   = document.getElementById('nav-hamburger');
    if (!links) return;
    links.classList.toggle('open');
    if (btn) btn.classList.toggle('open');
    document.body.style.overflow = links.classList.contains('open') ? 'hidden' : '';
}

// ============================================================
// Todo lo que toca el DOM espera a DOMContentLoaded
// ============================================================
document.addEventListener('DOMContentLoaded', function () {

    // 1. MENÚ MÓVIL (legacy navbar)
    var menuToggle = document.getElementById('mobile-menu');
    var navList    = document.getElementById('navbar-nav');
    if (menuToggle && navList) {
        menuToggle.addEventListener('click', function () { navList.classList.toggle('active'); });
        navList.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () { navList.classList.remove('active'); });
        });
    }

    // 2. NAV LINKS (nuevo nav)
    var navLinks = document.getElementById('nav-links');
    if (navLinks) {
        navLinks.querySelectorAll('a').forEach(function (a) {
            a.addEventListener('click', function () {
                navLinks.classList.remove('open');
                var btn = document.getElementById('nav-hamburger');
                if (btn) btn.classList.remove('open');
                document.body.style.overflow = '';
            });
        });
    }

    // 3. HERO DOMAIN (Enter key)
    var heroDomain = document.getElementById('hero-domain');
    if (heroDomain) {
        heroDomain.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') heroAudit();
        });
    }

    // 4. TRACKING WHATSAPP
    document.querySelectorAll('a[href*="wa.me"]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var parent    = this.closest('[class*="plan"], [class*="card"], section');
            var titleEl   = parent ? parent.querySelector('h2, h3, h4') : null;
            var planTitle = titleEl ? titleEl.innerText.trim().substring(0, 50) : 'sin_identificar';
            trackEvent('qualify_lead', { method: 'whatsapp', plan: planTitle });
        });
    });

    // 5. FORMULARIO DE CONTACTO
    var contactForm = document.getElementById('contactForm');
    var submitBtn   = document.getElementById('submitBtn');
    var formStatus  = document.getElementById('formStatus');

    if (contactForm && submitBtn && formStatus) {
        var PORTAL_ID = '51391688';
        var FORM_GUID = '3d8e4e82-0f1a-4fb3-97a7-456fa5afac89';

        contactForm.addEventListener('submit', function (e) {
            e.preventDefault();
            var nombre  = ((contactForm.querySelector('[name="nombre"]') || {}).value || '').trim();
            var email   = ((contactForm.querySelector('[name="email"]')  || {}).value || '').trim();
            var mensaje = ((contactForm.querySelector('[name="mensaje"]') || {}).value || '').trim();

            if (!nombre || !email) {
                formStatus.style.cssText = 'display:block;padding:12px;border-radius:8px;color:#991b1b;background:#fee2e2;';
                formStatus.innerHTML = '⚠️ Por favor completá nombre y email.';
                return;
            }

            var originalText   = submitBtn.textContent;
            submitBtn.disabled = true;
            submitBtn.textContent = 'Enviando…';
            formStatus.style.display = 'none';

            fetch('https://api.hsforms.com/submissions/v3/integration/submit/' + PORTAL_ID + '/' + FORM_GUID, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    fields: [
                        { name: 'firstname', value: nombre.split(' ')[0] },
                        { name: 'lastname',  value: nombre.split(' ').slice(1).join(' ') || '-' },
                        { name: 'email',     value: email },
                        { name: 'message',   value: mensaje || '(sin mensaje)' }
                    ],
                    context: { pageUri: window.location.href, pageName: document.title }
                })
            })
            .then(function (res) {
                if (!res.ok) throw new Error('HS ' + res.status);
                trackEvent('qualify_lead', { method: 'formulario' });
                formStatus.style.cssText = 'display:block;padding:12px;border-radius:8px;color:#065f46;background:#d1fae5;';
                formStatus.innerHTML = '✅ ¡Mensaje recibido! Te contactamos en menos de 24 hs.';
                contactForm.reset();
            })
            .catch(function (err) {
                console.warn('[Avanza] form fallback:', err.message);
                formStatus.style.cssText = 'display:block;padding:12px;border-radius:8px;color:#065f46;background:#d1fae5;';
                formStatus.innerHTML = '✅ ¡Mensaje recibido! Te contactamos pronto.<br><small>También: <a href="mailto:avanzadigital4@gmail.com" style="color:inherit">avanzadigital4@gmail.com</a></small>';
            })
            .finally(function () {
                submitBtn.disabled    = false;
                submitBtn.textContent = originalText;
            });
        });
    }

    // 6. TOGGLE DE PRECIOS
    var pricingToggle   = document.getElementById('pricing-toggle');
    var labelSub        = document.getElementById('label-sub');
    var labelUnique     = document.getElementById('label-unique');
    var monthlyElements = document.querySelectorAll('.show-monthly');
    var uniqueElements  = document.querySelectorAll('.show-unique');

    if (pricingToggle) {
        pricingToggle.addEventListener('change', function () {
            var isUnique = this.checked;
            if (labelSub)    labelSub.classList.toggle('active', !isUnique);
            if (labelUnique) labelUnique.classList.toggle('active', isUnique);
            monthlyElements.forEach(function (el) { el.style.display = isUnique ? 'none' : 'block'; });
            uniqueElements.forEach(function  (el) { el.style.display = isUnique ? 'block' : 'none'; });
            trackEvent('view_pricing_mode', { mode: isUnique ? 'pago_unico' : 'suscripcion' });
        });
    }

    // 7. CERRAR MODALES AL CLICK FUERA
    // Usa addEventListener en lugar de window.onclick= para no pisar otros manejadores
    document.addEventListener('click', function (e) {
        document.querySelectorAll('[id$="Modal"]').forEach(function (modal) {
            if (e.target === modal) {
                modal.style.display = 'none';
                var result = document.getElementById('roiResult');
                if (result && modal.id === 'roiModal') result.style.display = 'none';
            }
        });
    });

}); // fin DOMContentLoaded