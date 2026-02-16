// 1. MENU MOVIL (Protegido para que no rompa el sitio si no existe)
const menuToggle = document.getElementById('mobile-menu');
const navList = document.getElementById('navbar-nav');

if (menuToggle && navList) {
    menuToggle.addEventListener('click', () => {
        navList.classList.toggle('active');
    });

    document.querySelectorAll('.navbar ul li a').forEach(link => {
        link.addEventListener('click', () => {
            navList.classList.remove('active');
        });
    });
}

// 4. MANEJO DEL FORMULARIO (AJAX)
const contactForm = document.getElementById('contactForm');
const submitBtn = document.getElementById('submitBtn');
const formStatus = document.getElementById('formStatus');

if (contactForm) {
    contactForm.addEventListener('submit', async function(e) {
        e.preventDefault(); 

        // UI: Estado de Carga
        const originalBtnText = submitBtn.innerHTML;
        submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Enviando...';
        submitBtn.disabled = true;
        submitBtn.style.opacity = '0.7';

        const formData = new FormData(contactForm);

        try {
            // ⚠️ ASEGÚRATE DE QUE ESTE EMAIL SEA EL CORRECTO
            const response = await fetch("https://formsubmit.co/ajax/avanzadigital4@gmail.com", {
                method: "POST",
                body: formData
            });

            const result = await response.json();

            if (result.success === "true" || response.ok) {
                formStatus.style.display = 'block';
                formStatus.style.color = '#2ecc71'; // Verde
                formStatus.innerHTML = '<i class="fa-solid fa-check-circle"></i> ¡Mensaje enviado! Te responderemos pronto.';
                contactForm.reset();
                
                setTimeout(() => {
                    formStatus.style.display = 'none';
                }, 5000);
            } else {
                throw new Error('Error en el servicio');
            }

        } catch (error) {
            console.error(error);
            formStatus.style.display = 'block';
            formStatus.style.color = '#e74c3c'; // Rojo
            formStatus.innerHTML = '<i class="fa-solid fa-circle-exclamation"></i> Hubo un error. Escríbenos por WhatsApp.';
        } finally {
            submitBtn.innerHTML = originalBtnText;
            submitBtn.disabled = false;
            submitBtn.style.opacity = '1';
        }
    });
}

// 5. LOGICA DEL TOGGLE DE PRECIOS
const pricingToggle = document.getElementById('pricing-toggle');
const labelSub = document.getElementById('label-sub');
const labelUnique = document.getElementById('label-unique');

const monthlyElements = document.querySelectorAll('.show-monthly');
const uniqueElements = document.querySelectorAll('.show-unique');

// Verificamos si el toggle existe antes de agregar el evento
if (pricingToggle) {
    pricingToggle.addEventListener('change', function() {
        if(this.checked) {
            // MODO PAGO ÚNICO ACTIVADO
            labelSub.classList.remove('active');
            labelUnique.classList.add('active');
            
            // Ocultar Mensual / Mostrar Único
            monthlyElements.forEach(el => el.style.display = 'none');
            uniqueElements.forEach(el => el.style.display = 'block');
        } else {
            // MODO SUSCRIPCIÓN ACTIVADO
            labelSub.classList.add('active');
            labelUnique.classList.remove('active');
            
            // Mostrar Mensual / Ocultar Único
            monthlyElements.forEach(el => el.style.display = 'block');
            uniqueElements.forEach(el => el.style.display = 'none');
        }
    });
}
// === LÓGICA DE LA CALCULADORA ROI ===

function openRoiModal(e) {
    if(e) e.preventDefault();
    document.getElementById('roiModal').style.display = 'flex';
}

function closeRoiModal() {
    document.getElementById('roiModal').style.display = 'none';
    document.getElementById('roiResult').style.display = 'none';
}

function calculateLoss() {
    // 1. Obtener valores
    const reps = document.getElementById('salesReps').value || 0;
    const hours = document.getElementById('hoursWasted').value || 0;
    const rate = document.getElementById('hourlyRate').value || 0;

    // 2. Calcular (Semanas laborales al año: aprox 50)
    const weeklyLoss = reps * hours * rate;
    const annualLoss = weeklyLoss * 50;

    // 3. Mostrar Resultado con animación simple
    const resultBox = document.getElementById('roiResult');
    const amountText = document.getElementById('lossAmount');
    
    // Formato de moneda
    const formatter = new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 0
    });

    amountText.innerText = formatter.format(annualLoss);
    resultBox.style.display = 'block';
}

// Cerrar modal si se hace clic fuera del contenido
window.onclick = function(event) {
    const modal = document.getElementById('roiModal');
    if (event.target == modal) {
        closeRoiModal();
    }
}