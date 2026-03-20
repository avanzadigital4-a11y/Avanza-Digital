// ============================================================
// HELPER: Función central para disparar eventos a GA4 via GTM
// ============================================================
function trackEvent(eventName, params) {
    params = params || {};
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push(Object.assign({ event: eventName }, params));
}

// ============================================================
// Todo el código que toca el DOM va dentro de DOMContentLoaded
// para asegurarse que los elementos ya existen en la página
// ============================================================
document.addEventListener('DOMContentLoaded', function() {

    // 1. MENU MOVIL
    var menuToggle = document.getElementById('mobile-menu');
    var navList = document.getElementById('navbar-nav');

    if (menuToggle && navList) {
        menuToggle.addEventListener('click', function() {
            navList.classList.toggle('active');
        });
        document.querySelectorAll('.navbar ul li a').forEach(function(link) {
            link.addEventListener('click', function() {
                navList.classList.remove('active');
            });
        });
    }

    // 2. TRACKING DE CLICKS EN BOTONES DE WHATSAPP
    document.querySelectorAll('a[href*="wa.me"]').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var parent = this.closest('[class*="plan"], [class*="card"], section');
            var titleEl = parent ? parent.querySelector('h2, h3, h4') : null;
            var planTitle = titleEl ? titleEl.innerText.trim().substring(0, 50) : 'sin_identificar';
            trackEvent('qualify_lead', {
                method: 'whatsapp',
                plan: planTitle
            });
        });
    });

    // 3. FORMULARIO DE CONTACTO
    var contactForm = document.getElementById('contactForm');
    var submitBtn = document.getElementById('submitBtn');
    var formStatus = document.getElementById('formStatus');

    if (contactForm) {
        contactForm.addEventListener('submit', async function(e) {
            e.preventDefault();

            var originalBtnText = submitBtn.innerHTML;
            submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Enviando...';
            submitBtn.disabled = true;
            submitBtn.style.opacity = '0.7';

            var formData = new FormData(contactForm);

            try {
                var response = await fetch("https://formsubmit.co/ajax/avanzadigital4@gmail.com", {
                    method: "POST",
                    body: formData
                });

                var result = await response.json();

                if (result.success === "true" || response.ok) {
                    // Conversión: formulario enviado
                    trackEvent('qualify_lead', { method: 'formulario' });

                    formStatus.style.display = 'block';
                    formStatus.style.color = '#2ecc71';
                    formStatus.innerHTML = '<i class="fa-solid fa-check-circle"></i> ¡Mensaje enviado! Te responderemos pronto.';
                    contactForm.reset();

                    setTimeout(function() { formStatus.style.display = 'none'; }, 5000);
                } else {
                    throw new Error('Error en el servicio');
                }

            } catch (error) {
                console.error(error);
                formStatus.style.display = 'block';
                formStatus.style.color = '#e74c3c';
                formStatus.innerHTML = '<i class="fa-solid fa-circle-exclamation"></i> Hubo un error. Escríbenos por WhatsApp.';
            } finally {
                submitBtn.innerHTML = originalBtnText;
                submitBtn.disabled = false;
                submitBtn.style.opacity = '1';
            }
        });
    }

    // 4. TOGGLE DE PRECIOS
    var pricingToggle = document.getElementById('pricing-toggle');
    var labelSub = document.getElementById('label-sub');
    var labelUnique = document.getElementById('label-unique');
    var monthlyElements = document.querySelectorAll('.show-monthly');
    var uniqueElements = document.querySelectorAll('.show-unique');

    if (pricingToggle) {
        pricingToggle.addEventListener('change', function() {
            if (this.checked) {
                labelSub.classList.remove('active');
                labelUnique.classList.add('active');
                monthlyElements.forEach(function(el) { el.style.display = 'none'; });
                uniqueElements.forEach(function(el) { el.style.display = 'block'; });
                trackEvent('view_pricing_mode', { mode: 'pago_unico' });
            } else {
                labelSub.classList.add('active');
                labelUnique.classList.remove('active');
                monthlyElements.forEach(function(el) { el.style.display = 'block'; });
                uniqueElements.forEach(function(el) { el.style.display = 'none'; });
                trackEvent('view_pricing_mode', { mode: 'suscripcion' });
            }
        });
    }

    // 5. MENU MOBILE nav links
    document.querySelectorAll('#nav-links a').forEach(function(a) {
        a.addEventListener('click', function() {
            var links = document.getElementById('nav-links');
            var btn = document.getElementById('nav-hamburger');
            if (links) links.classList.remove('open');
            if (btn) btn.classList.remove('open');
            document.body.style.overflow = '';
        });
    });

}); // fin DOMContentLoaded

// ============================================================
// Funciones globales — fuera del DOMContentLoaded porque se
// llaman desde onclick en el HTML
// ============================================================

function toggleNav() {
    var links = document.getElementById('nav-links');
    var btn = document.getElementById('nav-hamburger');
    links.classList.toggle('open');
    btn.classList.toggle('open');
    document.body.style.overflow = links.classList.contains('open') ? 'hidden' : '';
}

function openRoiModal(e) {
    if (e) e.preventDefault();
    document.getElementById('roiModal').style.display = 'flex';
    trackEvent('open_roi_calculator');
}

function closeRoiModal() {
    document.getElementById('roiModal').style.display = 'none';
    document.getElementById('roiResult').style.display = 'none';
}

function calculateLoss() {
    var reps = document.getElementById('salesReps').value || 0;
    var hours = document.getElementById('hoursWasted').value || 0;
    var rate = document.getElementById('hourlyRate').value || 0;

    var weeklyLoss = reps * hours * rate;
    var annualLoss = weeklyLoss * 50;

    var resultBox = document.getElementById('roiResult');
    var amountText = document.getElementById('lossAmount');

    var formatter = new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 0
    });

    amountText.innerText = formatter.format(annualLoss);
    resultBox.style.display = 'block';

    // Conversión: calculadora ROI usada
    trackEvent('qualify_lead', {
        method: 'calculadora_roi',
        annual_loss: Math.round(annualLoss)
    });
}

// Cerrar modal ROI si se hace clic fuera
window.onclick = function(event) {
    var modal = document.getElementById('roiModal');
    if (event.target == modal) {
        closeRoiModal();
    }
}